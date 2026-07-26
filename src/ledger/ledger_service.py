import hashlib
import uuid
from datetime import datetime, time
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from beancount_sync.beancount_sync import SimpleLedgerTransaction
from ledger.dto import TransactionDto, TransactionListDto, TransactionListResultDto, AccountDto, BalancesDto, \
    PeriodicBalancesDto
from ledger.model import Account, Transaction, Entry, AccountType
from ledger.repo import LedgerRepo, TransactionFilters, ListTransactionCursor
from main import Session
from model import Config
import logging


class LedgerService:

    def __init__(self, config: Config):
        self.config = config

    @staticmethod
    def get_transactions(filters: TransactionFilters, cursor_str: str | None,
                         count: int = 100) -> TransactionListResultDto:
        with Session.begin() as session:
            if cursor_str:
                cursor = ListTransactionCursor.from_b64(cursor_str)
            else:
                cursor = None
            transactions, next_cursor = LedgerRepo.get_transactions(session, filters, cursor=cursor, count=count)
            tx_dtos = [TransactionListDto.model_validate(x) for x in transactions]
            next_cursor_str = next_cursor.to_b64() if next_cursor else None
            return TransactionListResultDto(transactions=tx_dtos, next_cursor=next_cursor_str)

    @staticmethod
    def get_transaction(tx_id: uuid.UUID) -> TransactionDto | None:
        with Session.begin() as session:
            tx = LedgerRepo.get_transaction_by_id(session, tx_id)
            if not tx:
                return None
            return TransactionDto.model_validate(tx)


    @staticmethod
    def get_payees(term: str | None) -> list[str]:
        with Session.begin() as session:
            return LedgerRepo.get_payees(session, term)

    @staticmethod
    def get_accounts() -> list[AccountDto]:
        with Session.begin() as session:
            return LedgerRepo.get_accounts(session)

    @staticmethod
    def get_tags() -> list[str]:
        with Session.begin() as session:
            return LedgerRepo.get_tags(session)


    @staticmethod
    def get_balance(filters: TransactionFilters, account_types: list[str]) -> BalancesDto:
        with Session.begin() as session:
            return LedgerRepo.get_balances(session, filters, account_types)

    @staticmethod
    def get_balance_history(filters: TransactionFilters, account_types: list[str], period: Literal["day", "month", "week"]) -> PeriodicBalancesDto:
        with Session.begin() as session:
            return LedgerRepo.get_balances_over_time(session, filters, account_types, granularity=period)


    def sync_ledger(self):
        accruals = [a.liability_account for a in self.config.accruals] + [a.expense_account for a in
                                                                          self.config.accruals]
        accounts = ([r.account for r in self.config.accountRules] +
                    [r.accountName for r in self.config.santanderAccountRules] +
                    list(self.config.monzoCategoryMappings.values())
                    + [self.config.defaultIncomeAccount,
                       self.config.defaultExpenseAccount, self.config.energy.gasExpenseAccount,
                       self.config.energy.gasPrepayAccount, self.config.energy.electricityPrepayAccount,
                       self.config.energy.electricityExpenseAccount
                       ] + accruals)

        logging.info("ensuring accounts and ledgers")
        with Session.begin() as session:
            for acc in accounts:
                acc = LedgerService._from_beancount_account_name(acc)
                LedgerRepo.ensure_account(session, acc)

            ledger_names = ["main", "accrual", "FY24", "monzo", "santander"]
            for l in ledger_names:
                LedgerRepo.ensure_ledger(session, l)

    def write_beancount_transactions(self, ledger_bean_name: str, bc_transactions: list[SimpleLedgerTransaction]):
        self.sync_ledger()
        import time as t_time
        start = t_time.time()
        # 1. Create transactions
        # 2. Create entries, linking to transactions using key -> id
        # 3. Remove any unused legs (as we allow updating items as this isn't a proper ledger)
        with Session.begin() as session:
            all_accounts = LedgerRepo.get_accounts(session) # very small list
            ledger_name_to_id = {l.name: l.id for l in LedgerRepo.get_ledgers(session)}
            ledger_name = ledger_bean_name.split(".")[0]
            transactions = []
            dt: datetime

            for tx in bc_transactions:
                # fallback to midnight if time not available
                if not tx.tx_datetime:
                    dt = datetime.combine(tx.tx_date, time.min, tzinfo=ZoneInfo("UTC"))
                else:
                    dt = tx.tx_datetime

                transaction = Transaction(id=uuid.uuid4(), ledger_id=ledger_name_to_id[ledger_name],
                                          transaction_datetime=dt,
                                          key=tx.external_id, payee=tx.payee, narration=tx.description,
                                          external_metadata=tx.metadata, tx_metadata=tx.ledger_metadata,
                                          flagged=tx.flagged, tags=tx.tags)
                transactions.append(transaction)

            transaction_key_to_id = LedgerRepo.bulk_upsert_transactions(session, transactions)
            entries = []
            active_legs = []

            for tx in bc_transactions:
                local_amount = tx.local_amount
                local_currency = tx.local_currency
                if not local_amount:
                    local_amount = tx.amount
                    local_currency = "GBP"

                credit_account = LedgerService._from_beancount_account_name(tx.credit_account)
                debit_account = LedgerService._from_beancount_account_name(tx.debit_account)
                tx_id = transaction_key_to_id[tx.external_id]
                db_credit_account = \
                [a for a in all_accounts if a.name == credit_account.name and a.type == credit_account.type][0]
                db_debit_account = \
                [a for a in all_accounts if a.name == debit_account.name and a.type == debit_account.type][0]
                credit_entry = Entry(id=uuid.uuid4(), account_id=db_credit_account.id,
                                     amount=abs(tx.amount) * -1, local_amount=abs(local_amount) * -1,
                                     local_currency=local_currency, transaction_id=tx_id)
                debit_entry = Entry(id=uuid.uuid4(), account_id=db_debit_account.id,
                                    amount=abs(tx.amount), local_amount=abs(local_amount),
                                    local_currency=local_currency, transaction_id=tx_id)
                entries.append(credit_entry)
                entries.append(debit_entry)
                active_legs.append((tx_id, credit_entry.account_id))
                active_legs.append((tx_id, debit_entry.account_id))
            LedgerRepo.delete_entries_in_transactions_not_in(session, list(transaction_key_to_id.values()), active_legs)
            LedgerRepo.bulk_upsert_entries(session, entries)
        duration = int((t_time.time() - start) * 1000)
        logging.info("synced transactions to ledger {} in {}ms".format(ledger_name, duration))

    @staticmethod
    def compute_key(dt: datetime, payee: str, narration: str, amount: Decimal) -> str:
        # Compute a unique key for items
        # This is only used for manually added items. If a key can come from an external system or computed naturally
        # from the operation creating it, that should be used instead
        payee = str(payee).strip().lower()
        narration = str(narration).strip().lower()
        external_id_items = f"{dt.isoformat(timespec="seconds")}-{payee}-{narration}-{amount}"
        return hashlib.md5(external_id_items.encode()).hexdigest()

    @staticmethod
    def _from_beancount_account_name(name: str) -> Account:
        parts = name.split(":")
        assert len(parts) >= 2
        acc_type = {
            "Expenses": AccountType.EXPENSE,
            "Assets": AccountType.ASSET,
            "Liabilities": AccountType.LIABILITY,
            "Equity": AccountType.EQUITY,
            "Income": AccountType.INCOME,
        }.get(parts[0])
        return Account(name=parts[-1], tags=parts[1:-1], type=acc_type, id=uuid.uuid4())
