from operator import abs

import dependencies
from importer.import_service import GcImportIntegrationDto, ImportService, GcImportRuleDto
from model import *

import logging

from poster.base_poster import BasePoster
from santander import SantanderTransaction, from_gc
from storage import SANTANDER_TX_FILE


class SantanderPoster(BasePoster):

    def __init__(self, config: GcImportIntegrationDto):
        self.import_config = config
        self.import_rules = ImportService.get_gc_import_rules(config.id)

    def run(self):
        santander_transactions = dependencies.get_transactions_store().load(SANTANDER_TX_FILE, SantanderTransactions).transactions
        mapped_transactions = [self.translate_to_ledger(from_gc(tx)) for tx in santander_transactions]
        ledger_transactions = [tx for tx in mapped_transactions if tx]
        logging.info("writing santander to db")
        dependencies.get_ledger_service().create_or_update_transactions("santander.bean", ledger_transactions)

    def translate_to_ledger(self, tx: SantanderTransaction) -> SimpleLedgerTransaction | None:
        cash_account = self.import_config.cash_account_id
        flagged = False

        account_rule = self._get_santander_account(tx)
        if not account_rule:
            logging.warning("Failed to find santander account for transaction %s (created=%s, amount=%s)", tx, tx.date,
                            tx.amount)
            flagged = True
            other_account = self.import_config.default_income_account_id if tx.amount >= 0 else self.import_config.default_expense_account_id
            ledger_metadata = {}
        else:
            other_account = account_rule.account_id
            if account_rule.ignore or False:
                # We specify rule.ignore for postings created on monzo side to prevent duplicates when transferring
                # Proper accounting rules would be to post both transfers, but we are lazy and only do one
                return None
            ledger_metadata = account_rule.new_metadata

        if tx.amount >= 0:
            # Positive is money coming into the santander account, so we debit the cash account
            credit_amount = Decimal(tx.amount)
            debit_account = cash_account
            credit_account = other_account
        else:
            # Spending money from the cash account
            credit_amount = Decimal(tx.amount * -1)
            debit_account = other_account
            credit_account = cash_account

        payee = tx.account_name
        description = tx.description
        if account_rule:
            if account_rule.payee:
                payee = account_rule.payee
            if account_rule.narration:
                description = account_rule.narration

        # santander only provides date, currency always GBP (local currency not provided)
        return SimpleLedgerTransaction(external_id=tx.id, tx_date=tx.date, amount=credit_amount,
                                       credit_account_id=credit_account,
                                       debit_account_id=debit_account,
                                       payee=payee or "",
                                       description=description or "", flagged=flagged,
                                       metadata=tx.model_dump(mode="json"), source="santander", ledger_metadata=ledger_metadata,
                                       local_amount=abs(credit_amount), local_currency="GBP")

    def _get_santander_account(self, tx: SantanderTransaction) -> GcImportRuleDto | None:
        for rule in self.import_rules:
            if SantanderPoster._santander_rule_matches(rule, tx):
                return rule
        return None

    @staticmethod
    def _santander_rule_matches(rule: GcImportRuleDto, tx: SantanderTransaction) -> bool:
        # credit_only means we only care about money out, so ignore money in
        if rule.credit_only and tx.amount >= 0:
            return False
        if rule.debit_only and tx.amount < 0:
            return False

        if rule.transaction_type and rule.transaction_type != tx.type:
            return False

        if rule.account_name_matches:
            if not tx.account_name:
                return False
            account_lower = tx.account_name.lower()
            found = False
            if rule.account_name_matches.lower() in account_lower:
                found = True
            if not found:
                return False

        found = False
        if rule.reference_name_matches:
            if not tx.reference:
                return False
            reference_lower = tx.reference.lower()
            if rule.reference_name_matches.lower() in reference_lower:
                found = True
            if not found:
                return False

        # useful for strange things as we get relativly little info compared to Monzo
        amount_check = abs(rule.amount_equals or 0)
        if amount_check > 0:
            if Decimal(amount_check) != abs(tx.amount):
                return False
        return True  # innocent until proven guilty
