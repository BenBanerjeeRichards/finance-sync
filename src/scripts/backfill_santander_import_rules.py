import logging
import uuid
from decimal import Decimal

from sqlalchemy import select, delete

from ledger.dto import AccountDto
from ledger.ledger_service import LedgerService
from ledger.model import GoCardlessImportRule, GoCardlessImportIntegration
from main import Session
from model import Config


def _account_id(accounts: list[AccountDto], full_name: str) -> uuid.UUID:
    parsed = LedgerService._from_beancount_account_name(full_name)
    matches = [a for a in accounts if a.name == parsed.name and a.type == parsed.type]
    assert matches, f"no account found for {full_name}"
    return matches[0].id


def backfill_santander_import_rules(config: Config, secret_id: str) -> None:
    ledger_service = LedgerService(config)
    ledger_service.sync_ledger()
    accounts = LedgerService.get_accounts()

    with Session.begin() as session:
        integration_id = session.execute(
            select(GoCardlessImportIntegration.id).where(GoCardlessImportIntegration.secret_id == secret_id)
        ).scalar_one()

        session.execute(delete(GoCardlessImportRule).where(GoCardlessImportRule.import_integration_id == integration_id))

        rules = []
        priority = 0
        for rule in config.santanderAccountRules:
            assert not rule.accountMatches or len(rule.accountMatches) == 1, \
                f"rule {rule.name}: accountMatches has more than one entry, model only supports a single value"
            assert not rule.referenceMatches or len(rule.referenceMatches) == 1, \
                f"rule {rule.name}: referenceMatches has more than one entry, model only supports a single value"

            rules.append(GoCardlessImportRule(
                id=uuid.uuid4(),
                import_integration_id=integration_id,
                name=rule.name,
                priority=priority,
                new_metadata=rule.metadata,
                account_id=_account_id(accounts, rule.accountName),
                payee=rule.payee,
                credit_only=rule.creditOnly or False,
                debit_only=rule.debitOnly or False,
                ignore=rule.ignore or False,
                transaction_type=rule.type,
                account_name_matches=rule.accountMatches[0] if rule.accountMatches else None,
                reference_name_matches=rule.referenceMatches[0] if rule.referenceMatches else None,
                amount_equals=Decimal(str(rule.amount)) if rule.amount is not None else None,
            ))
            priority += 10

        session.add_all(rules)
        logging.info("Backfilled %s santander import rules", len(rules))
