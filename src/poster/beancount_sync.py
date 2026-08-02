from __future__ import annotations
from datetime import date, datetime
from typing import TYPE_CHECKING
import uuid
from pydantic import BaseModel

from model import Config
from decimal import Decimal




class BadTransactionError(Exception):
    pass


class BeancountSync:

    def __init__(self, config: Config):
        from poster.accrual_poster import AccrualsPoster
        from poster.energy_sync import EnergyConsumptionPoster

        self.config = config
        self.accrual = AccrualsPoster(config)
        self.energy_sync = EnergyConsumptionPoster(self.config)

    def sync(self):
        """"
        Sync the given transactions with the ledger
        For any updates, publish these to the appropiate topics
        """
        self._update_ledger()

    def _update_ledger(self) -> None:

        # TODO move elsewhere
        # Compute any new accrual transactions & energy
        self.accrual.run_accruals()
        self.energy_sync.run_energy_sync()

