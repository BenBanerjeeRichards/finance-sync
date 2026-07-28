import logging
import uuid

from sqlalchemy import select, delete

from ledger.dto import AccountDto
from ledger.ledger_service import LedgerService
from ledger.model import MonzoImportRule, MonzoImportIntegration
from main import Session
from model import Config


def _account_id(accounts: list[AccountDto], full_name: str) -> uuid.UUID:
    parsed = LedgerService._from_beancount_account_name(full_name)
    matches = [a for a in accounts if a.name == parsed.name and a.type == parsed.type]
    assert matches, f"no account found for {full_name}"
    return matches[0].id


# One-off backfill: convert config.yml's accountRules + monzoCategoryMappings into MonzoImportRule rows.
# accountRules keep their relative order as priority (lower = evaluated first, first match wins).
# monzoCategoryMappings become lowest-priority rules matching only on category, so accountRules always
# take precedence - matching the "accountRules below always take priority" comment in config.yml.
def backfill_monzo_import_rules(config: Config, client_id: str) -> None:
    ledger_service = LedgerService(config)
    ledger_service.sync_ledger()
    accounts = LedgerService.get_accounts()

    with Session.begin() as session:
        integration_id = session.execute(
            select(MonzoImportIntegration.id).where(MonzoImportIntegration.client_id == client_id)
        ).scalar_one()

        session.execute(delete(MonzoImportRule).where(MonzoImportRule.import_integration_id == integration_id))

        rules = []
        priority = 0
        for rule in config.accountRules:
            rules.append(MonzoImportRule(
                id=uuid.uuid4(),
                import_integration_id=integration_id,
                name=rule.name,
                priority=priority,
                new_metadata=rule.metadata,
                account_id=_account_id(accounts, rule.account),
                payee=rule.payee,
                narration=rule.narrative,
                created_at=rule.createdGt,
                account_number=rule.accountNumber,
                tags=rule.tags or [],
                pot_id=rule.potId,
                merchant_group_id=rule.merchantGroupId,
                counterparty_name=rule.counterpartyName,
            ))
            priority += 10

        for category, account in config.monzoCategoryMappings.items():
            rules.append(MonzoImportRule(
                id=uuid.uuid4(),
                import_integration_id=integration_id,
                name=f"Category: {category}",
                priority=priority,
                new_metadata={},
                account_id=_account_id(accounts, account),
                category=category,
                tags=[],
            ))
            priority += 10

        session.add_all(rules)
        logging.info("Backfilled %s monzo import rules", len(rules))
