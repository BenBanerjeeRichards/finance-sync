import hashlib
import uuid
from datetime import datetime, time
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import delete

from ledger.dto import TransactionDto, TransactionListDto, TransactionListResultDto, AccountDto, BalancesDto, \
    PeriodicBalancesDto, EntryDto
from ledger.model import Account, Transaction, Entry, AccountType
from ledger.repo import LedgerRepo, TransactionFilters, ListTransactionCursor
from main import Session
from model import Config, SimpleLedgerTransaction
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
    def get_balance_history(filters: TransactionFilters, account_types: list[str],
                            period: Literal["day", "month", "week"]) -> PeriodicBalancesDto:
        with Session.begin() as session:
            return LedgerRepo.get_balances_over_time(session, filters, account_types, granularity=period)

    def create_or_update_transactions(self, txs: list[TransactionDto]):
        # 1. Create transactions
        # 2. Create entries, linking to transactions using key -> id
        # 3. Remove any unused legs (as we allow updating items as this isn't a proper ledger)
        with Session.begin() as session:
            transactions = []
            dt: datetime
            for tx in txs:
                transaction = Transaction(id=uuid.uuid4(),
                                          transaction_datetime=tx.transaction_datetime,
                                          key=tx.key, payee=tx.payee, narration=tx.narration,
                                          external_metadata=tx.external_metadata, tx_metadata=tx.tx_metadata,
                                          flagged=tx.flagged, tags=tx.tags)
                transactions.append(transaction)

            transaction_key_to_id = LedgerRepo.bulk_upsert_transactions(session, transactions)
            entries = []
            active_legs = []

            for tx in txs:
                tx_id = transaction_key_to_id[tx.key]
                for entry in tx.entries:
                    db_entry = Entry(id=entry.id, account_id=entry.account.id, amount=entry.amount, local_amount=entry.local_amount,
                                     local_currency=entry.local_currency, transaction_id=tx_id)

                    entries.append(db_entry)
                    active_legs.append((tx_id, db_entry.account_id))
            LedgerRepo.delete_entries_in_transactions_not_in(session, list(transaction_key_to_id.values()), active_legs)
            LedgerRepo.bulk_upsert_entries(session, entries)

    def create_or_update_simple_transactions(self, ledger_txs: list[SimpleLedgerTransaction]):
        transactions = [tx.to_dto() for tx in ledger_txs]
        self.create_or_update_transactions(transactions)


    @staticmethod
    def create_or_update_transaction(session, update_dto: TransactionDto) -> None:
        """
        Frontend update, so considers source of existing tx to determine how the transaction can be updated
        In general - we only allow updating manually added transactions, imported transactions would be overwritten
        Exception is monzo which provides an api to directly update the transaction in Monzo
        """
        existing = LedgerRepo.get_transaction_by_id(session, update_dto.id)
        source = None if not existing else existing.tx_metadata.get("source")
        # We limit what we can update depending on the source
        if source in ["santander", "accrual", "energy"]:
            return
        if source == "monzo":
            # TODO support updating tags and narration
            pass

        # those without a poster source are manually added and can safely be fully upserted
        update = Transaction()
        LedgerRepo.bulk_upsert_transactions(session, [update_dto])


    @staticmethod
    def find_all_by_metadata_by_date_desc(session, key: str, value: str) -> list[TransactionDto]:
        res = LedgerRepo.find_all_by_metadata_by_date_desc(session, key, value)
        return [TransactionDto.model_validate(x) for x in res]

    @staticmethod
    def delete_transactions(session, ids: list[uuid.UUID]):
        q = delete(Transaction).where(Transaction.id.in_(ids))
        session.execute(q)

    @staticmethod
    def delete_transactions_by_key(session, ids: list[str]):
        q = delete(Transaction).where(Transaction.key.in_(ids))
        session.execute(q)

    @staticmethod
    def compute_key(dt: datetime, payee: str, narration: str, amount: Decimal) -> str:
        # Compute a unique key for items
        # This is only used for manually added items. If a key can come from an external system or computed naturally
        # from the operation creating it, that should be used instead
        payee = str(payee).strip().lower()
        narration = str(narration).strip().lower()
        date_str = dt.strftime("%Y-%m-%d")
        amount_str = f"{amount:.2f}"
        external_id_items = f"{date_str}-{payee}-{narration}-{amount_str}"
        return hashlib.md5(external_id_items.encode("utf-8")).hexdigest()