from model import Transaction as MonzoTransaction
from beancount_sync.beancount_sync import BeancountTransaction
import logging
from beancount_sync.beancount_util import create_amount
from model import *
import datetime


class MonzoTranslater:
    """
    Translates monzo translations to Beancount ledger postings
    Uses the rules in the provided config to determine the account
    Most just get the accounts from the monzo categories: e.g. Grocieres -> Expenses::Groceries
    """

    def __init__(self, config: Config) -> None:
        self.config = config

    def translate_to_beancount(self, tx: MonzoTransaction) -> BeancountTransaction:
        cash_account = self.config.monzoCashAccount
        other_account, rule = self._get_transaction_accounts(tx)
        flagged = False
        if not other_account:
            logging.warning("Failed to find account for transaction %s(created=%s)", tx.id, tx.created)
            flagged = True
            other_account = self.config.defaultIncomeAccount if tx.amount >= 0 else self.config.defaultExpenseAccount

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

        merchant_name = None if not tx.merchant else tx.merchant.name
        counterparty_name = None if not tx.counterparty else tx.counterparty.name
        payee = merchant_name or counterparty_name or ""
        if payee is None and rule:
            payee = rule.name

        description = '' if not tx.notes else tx.notes
        return BeancountTransaction(external_id=tx.id, tx_date=tx_date, amount=amount.number,
                                    credit_account=credit_account,
                                    debit_account=debit_account, payee=payee, description=description, flagged=flagged,
                                    metadata=tx.model_dump(), source="monzo")

    def _get_transaction_accounts(self, monzo_tx: MonzoTransaction) -> tuple[str | None, MonzoAccountRule | None]:
        for rule in self.config.accountRules:
            if MonzoTranslater._match_account_rule(rule, monzo_tx):
                return rule.account, rule

        account_maybe = self.config.monzoCategoryMappings.get(monzo_tx.category)

        if not account_maybe:
            return None, None
        return account_maybe, None

    @staticmethod
    def _match_account_rule(rule: MonzoAccountRule, tx: MonzoTransaction) -> bool:
        """
        Core logic for matching rules against transactions 
        If a rule item is provided and fails to match, the entire rule fails
        """
        tags = rule.tags
        if tags:
            return len(set(tags) - set(tx.tags)) < len(set(tags))
        account_number = rule.accountNumber
        if account_number and tx.counterparty is not None:
            return tx.counterparty.account_number == account_number
        pot_id = rule.potId
        if pot_id:
            return tx.pot_id == pot_id
        group_id = rule.merhantGroupId or rule.merchantGroupId
        if group_id:
            if tx.merchant and tx.merchant.group_id == group_id:
                return True
        return False
