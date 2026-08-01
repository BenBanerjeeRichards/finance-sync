import logging

from beancount_sync.beancount_sync import SimpleLedgerTransaction
from model import *
from model import Transaction as MonzoTransaction

def create_amount(pence: int) -> Decimal:
    return Decimal(pence) / Decimal("100")


class MonzoTranslater:
    from importer.import_service import  MonzoImportRuleDto, MonzoImportIntegrationDto

    """
    Translates monzo translations to Beancount ledger postings
    Uses the rules in the provided config to determine the account
    Most just get the accounts from the monzo categories: e.g. Groceries -> Expenses::Groceries
    """

    def __init__(self, import_config: MonzoImportIntegrationDto) -> None:
        from importer.import_service import ImportService

        self.import_config = import_config
        self.import_rules = ImportService.get_monzo_import_rules(import_config.id)

    def translate_to_ledger(self, tx: MonzoTransaction) -> SimpleLedgerTransaction:
        cash_account = self.import_config.cash_account_id
        assert cash_account is not None
        rule = self._get_transaction_rule(tx)

        flagged = False
        if not rule:
            logging.warning(
                "Failed to find account for transaction %s(created=%s)",
                tx.id,
                tx.created,
            )
            flagged = True
            other_account = (
                self.import_config.default_income_account_id
                if tx.amount >= 0
                else self.import_config.default_expense_account_id
            )
        else:
            other_account = rule.account_id

        if tx.amount >= 0:
            # This is money into the monzo cash, hence we are debiting the cash account
            # Remember: credit = money source, debit = money destination
            amount = create_amount(tx.amount)
            debit_account = cash_account
            credit_account = other_account
        else:
            # Spending money from the cash account
            amount = create_amount(tx.amount * -1)
            debit_account = other_account
            credit_account = cash_account

        tx_date = datetime.datetime.fromisoformat(tx.created).date()
        tx_datetime = datetime.datetime.fromisoformat(tx.created)

        merchant_name = None if not tx.merchant else tx.merchant.name
        counterparty_name = None if not tx.counterparty else tx.counterparty.name
        payee = merchant_name or counterparty_name or ""
        if payee is None and rule:
            payee = rule.name

        description = "" if not tx.notes else tx.notes
        return SimpleLedgerTransaction(
            external_id=tx.id,
            tx_date=tx_date,
            tx_datetime=tx_datetime,
            amount=amount,
            credit_account_id=credit_account,
            debit_account_id=debit_account,
            payee=rule.payee if rule and rule.payee else payee,
            description=description,
            flagged=flagged,
            metadata=tx.model_dump(),
            source="monzo",
            ledger_metadata=rule.metadata if rule else {},
            tags=tx.tags,
            local_amount=create_amount(tx.local_amount),
            local_currency=tx.local_currency
        )

    def _get_transaction_rule(
            self, monzo_tx: MonzoTransaction
    ) -> MonzoImportRuleDto | None:
        for rule in self.import_rules:
            if self._match_account_rule(rule, monzo_tx):
                return rule
        return None

    @staticmethod
    def _match_account_rule(rule: MonzoImportRuleDto, tx: MonzoTransaction) -> bool:
        """
        Core logic for matching rules against transactions
        If a rule item is provided and fails to match, the entire rule fails
        """
        if rule.created_at:
            if tx.created <= rule.created_at.isoformat():
                return False
        tags = rule.tags
        if tags:
            return len(set(tags) - set(tx.tags)) < len(set(tags))
        account_number = rule.account_number
        if account_number and tx.counterparty is not None:
            return tx.counterparty.account_number == account_number
        pot_id = rule.pot_id
        if pot_id:
            return tx.pot_id == pot_id
        group_id = rule.merchant_group_id
        if group_id:
            if tx.merchant and tx.merchant.group_id == group_id:
                return True
        if rule.counterparty_name and tx.counterparty and tx.counterparty.name:
            if rule.counterparty_name.lower() in tx.counterparty.name.lower():
                return True

        if rule.category and rule.category.lower() == tx.category.lower():
            return True
        return False
