import logging
import time

import dependencies
from importer.import_service import MonzoImportIntegrationDto, GcImportIntegrationDto
from poster.accrual_poster import AccrualsPoster
from poster.base_poster import BasePoster
from poster.energy_sync import EnergyConsumptionPoster
from poster.monzo_poster import MonzoPoster
from poster.mortgage_poster import MortgagePoster
from poster.poster_config_service import PosterConfigService
from poster.santander_poster import SantanderPoster


def _get_monzo_config() -> MonzoImportIntegrationDto | None:
    monzo_configs = dependencies.get_import_service().get_monzo_configs()
    if len(monzo_configs) != 1:
        logging.error("Expected exactly one monzo config, got %s", len(monzo_configs))
        return None
    monzo_config = monzo_configs[0]
    if None in [monzo_config.default_expense_account_id, monzo_config.default_income_account_id, monzo_config.cash_account_id]:
        logging.error("Monzo config not yet configured, skipping...")
        return None
    return monzo_config

def _get_santander_config() -> GcImportIntegrationDto | None:
    santander_configs = [c for c in dependencies.get_import_service().get_gc_configs() if c.kind == "santander"]
    if len(santander_configs) != 1:
        logging.error("Expected exactly one santader config, got %s", len(santander_configs))
        return None
    santander_config = santander_configs[0]
    if None in [santander_config.default_expense_account_id, santander_config.default_income_account_id, santander_config.cash_account_id]:
        logging.error("Santander config not yet configured, skipping...")
        return None
    return santander_config


def run_posters() -> None:
    monzo_config = _get_monzo_config()
    santander_config = _get_santander_config()
    energy_config =  PosterConfigService.get_energy_config()
    accrual_rules = PosterConfigService.get_accrual_configs()
    mortgage_rules = PosterConfigService.get_mortgage_configs()

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
