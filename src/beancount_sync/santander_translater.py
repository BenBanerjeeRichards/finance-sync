from operator import abs
from model import *
from beancount_sync.beancount_util import create_amount_from_decimal

import logging

from santander import SantanderTransaction
from beancount_sync.beancount_sync import BeancountTransaction


class SantanderTranslater:

    def __init__(self, config: Config):
        self.config = config

    def translate_to_beancount(self, tx: SantanderTransaction) -> BeancountTransaction | None:
        cash_account = self.config.santanderCashAccount
        flagged = False

        account_rule = self._get_santander_account(tx)
        if not account_rule:
            logging.warning("Failed to find santander account for transaction %s (created=%s, amount=%s)", tx, tx.date,
                            tx.amount)
            flagged = True
            other_account = self.config.defaultIncomeAccount if tx.amount >= 0 else self.config.defaultExpenseAccount
            ledger_metadata = {}
        else:
            other_account = account_rule.accountName
            if account_rule.ignore or False:
                # We specify rule.ignore for postings created on monzo side to prevent duplicates when transferring
                # Proper accounting rules would be to post both transfers, but we are lazy and only do one
                logging.info("Skipping transaction as ignored %s", tx)
                return None
            ledger_metadata = account_rule.metadata

        if tx.amount >= 0:
            # Positive is money coming into the santander account, so we debit the cash account
            credit_amount = create_amount_from_decimal(tx.amount)
            debit_account = cash_account
            credit_account = other_account
        else:
            # Spending money from the cash account
            credit_amount = create_amount_from_decimal(tx.amount * -1)
            debit_account = other_account
            credit_account = cash_account

        payee = account_rule.payee if account_rule.payee else tx.account_name

        return BeancountTransaction(external_id=tx.id, tx_date=tx.date, amount=credit_amount.number,
                                    credit_account=credit_account,
                                    debit_account=debit_account,
                                    payee=payee or "",
                                    description=tx.description, flagged=flagged,
                                    metadata=tx.model_dump(), source="santander", ledger_metadata=ledger_metadata)

    def _get_santander_account(self, tx: SantanderTransaction) -> SantanderAccountRule | None:
        for rule in self.config.santanderAccountRules:
            if SantanderTranslater._santander_rule_matches(rule, tx):
                return rule
        return None

    @staticmethod
    def _santander_rule_matches(rule: SantanderAccountRule, tx: SantanderTransaction) -> bool:
        # creditOnly means we only care about money out, so ignore money in
        if rule.creditOnly and tx.amount >= 0:
            return False
        if rule.debitOnly and tx.amount < 0:
            return False

        if rule.type and rule.type != tx.type:
            return False

        if rule.accountMatches:
            if not tx.account_name:
                return False
            account_lower = tx.account_name.lower()
            found = False
            for match in rule.accountMatches:
                if match.lower() in account_lower:
                    found = True
            if not found:
                return False

        found = False
        if rule.referenceMatches:
            if not tx.reference:
                return False
            reference_lower = tx.reference.lower()
            for match in rule.referenceMatches:
                if match.lower() in reference_lower:
                    found = True
            if not found:
                return False

        # useful for strange things as we get relativly little info compared to Monzo
        amount_check = abs(rule.amount or 0)
        if amount_check > 0:
            if Decimal(amount_check) != abs(tx.amount):
                return False
        return True  # innocent until proven guilty
