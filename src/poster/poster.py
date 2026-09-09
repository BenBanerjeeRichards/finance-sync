import datetime
import logging
import operator
import time

import dependencies
from db import DBSession
from importer.import_service import MonzoImportIntegrationDto, GcImportIntegrationDto
from ledger.account_alert_service import AccountAlertService
from ledger.ledger_service import LedgerService
from ledger.repo import TransactionFilters
from poster.accrual_poster import AccrualsPoster
from poster.base_poster import BasePoster
from poster.energy_sync import EnergyConsumptionPoster
from poster.monzo_poster import MonzoPoster
from poster.mortgage_poster import MortgagePoster
from poster.poster_config_service import PosterConfigService
from poster.santander_poster import SantanderPoster


def _get_monzo_config(session) -> MonzoImportIntegrationDto | None:
    monzo_configs = dependencies.get_import_service().get_monzo_configs(session)
    if len(monzo_configs) != 1:
        logging.error("Expected exactly one monzo config, got %s", len(monzo_configs))
        return None
    monzo_config = monzo_configs[0]
    if None in [monzo_config.default_expense_account_id, monzo_config.default_income_account_id,
                monzo_config.cash_account_id]:
        logging.error("Monzo config not yet configured, skipping...")
        return None
    return monzo_config


def _get_santander_config(session) -> GcImportIntegrationDto | None:
    santander_configs = [c for c in dependencies.get_import_service().get_gc_configs(session) if c.kind == "santander"]
    if len(santander_configs) != 1:
        logging.error("Expected exactly one santader config, got %s", len(santander_configs))
        return None
    santander_config = santander_configs[0]
    if None in [santander_config.default_expense_account_id, santander_config.default_income_account_id,
                santander_config.cash_account_id]:
        logging.error("Santander config not yet configured, skipping...")
        return None
    return santander_config


def run_posters() -> None:
    with DBSession.begin() as session:
        monzo_config = _get_monzo_config(session)
        santander_config = _get_santander_config(session)
        energy_config = PosterConfigService.get_energy_config(session)
        accrual_rules = PosterConfigService.get_accrual_configs(session)
        mortgage_rules = PosterConfigService.get_mortgage_configs(session)

    posters: list[BasePoster] = []
    if monzo_config is not None:
        posters.append(MonzoPoster(monzo_config))
    if santander_config is not None:
        posters.append(SantanderPoster(santander_config))
    for rule in accrual_rules:
        posters += [AccrualsPoster(rule)]
    for rule in mortgage_rules:
        posters += [MortgagePoster(rule)]
    if energy_config is not None:
        posters.append(EnergyConsumptionPoster(energy_config))

    for poster in posters:
        start = time.time()
        try:
            poster.run()
        except Exception:
            logging.exception("Failed to run poster %s", poster.__class__.__name__)
        end = time.time()
        logging.info("Poster %s ran for %s ms", poster.__class__.__name__, int((end - start) * 1000))

    if santander_config:
        check_santander_notify(santander_config)
    check_balances()


def check_santander_notify(santander_config: GcImportIntegrationDto):
    cfg = dependencies.get_config()
    now = datetime.datetime.now()
    diff = (santander_config.requisition_expires_at - now).days
    diff = 0 if diff < 0 else int(diff)
    if diff <= 7:
        notifier = dependencies.get_notification_service()
        with DBSession.begin() as session:
            notifier.register_santander_expiring(session, santander_config.id, cfg.gocardless.startUri, diff)


def check_balances():
    ops = {
       "above": operator.gt,
        "below": operator.lt,
    }
    not_service = dependencies.get_notification_service()
    with DBSession.begin() as session:
        rules = AccountAlertService.list_alerts(session)
        if not rules:
            return
        accounts = LedgerService.get_accounts(session)
        balances = LedgerService.get_balance(session, TransactionFilters(), account_types=[]).balances
        for rule in rules:
            bal = [b for b in balances if b.account_id == rule.account_id]
            if not bal:
                logging.error("Failed to get balance for %s", rule.account_id)
                continue
            acc_balance = bal[0].amount
            if ops[rule.condition](acc_balance, rule.amount):
                matching_account = [a for a in accounts if a.id == rule.account_id][0].name
                not_service.register_account_balance_notification(session, rule.id, matching_account, rule.amount,
                                                                  rule.condition, acc_balance)
