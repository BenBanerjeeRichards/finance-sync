import datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import urljoin

import requests
from pydantic import BaseModel
import logging

import dependencies
from model import SimpleLedgerTransaction, EnergyConfig
from poster.base_poster import BasePoster
from poster.poster_config_service import PosterConfigService
from ledger.ledger_service import LedgerService
from main import Session
import uuid


class Reading(BaseModel):
    amount_pence: int


class ReadingsResponse(BaseModel):
    readings: dict[str, Reading]


class EnergyClient:

    def __init__(self, base_url: str):
        self.base_url = base_url

    def get_monthly_readings(self, since: str, meter_type: Literal["gas", "electricity"]) -> dict[str, int]:
        url = urljoin(self.base_url, f"api/{meter_type}/readings?since={since}")
        res = requests.get(url)
        res.raise_for_status()
        readings = ReadingsResponse(**res.json())
        return {k: v.amount_pence for (k, v) in readings.readings.items()}


# Tracks energy usage by crediting prepay asset with monthly usage
class EnergyConsumptionPoster(BasePoster):

    def __init__(self, energy_config: EnergyConfig):
        self.config = dependencies.get_config()
        self.energy_config = energy_config

    def run(self):
        if not self.energy_config:
            logging.info("Energy Sync not configured, skipping")
            return

        energy_client = EnergyClient(self.config.energySyncBaseUrl)
        self._create_energy_transactions(energy_client, "electricity",
                                         self.energy_config.electricityPrepayAccount,
                                         self.energy_config.electricityExpenseAccount)
        self._create_energy_transactions(energy_client, "gas", self.energy_config.gasPrepayAccount,
                                         self.energy_config.gasExpenseAccount)

    def _create_energy_transactions(self, client: EnergyClient, meter_type: Literal["gas", "electricity"],
                                    asset_account: uuid.UUID, expense_account: uuid.UUID):
        readings = client.get_monthly_readings(self.energy_config.startMonth, meter_type)
        transactions = []
        for month, reading_amount in readings.items():
            external_id = f"energy_{meter_type}_{month}"  # idempotency key, one reading we update per month
            reading_date = datetime.datetime.strptime(month, "%Y-%m").date()
            amount = Decimal(reading_amount) / Decimal("100")
            transactions.append(SimpleLedgerTransaction(external_id=external_id,
                                                        tx_date=reading_date,
                                                        credit_account_id=asset_account,
                                                        debit_account_id=expense_account,
                                                        payee=f"Energy consumption ({meter_type})",
                                                        description="",
                                                        flagged=False,
                                                        ledger_metadata={"source": "energy"},
                                                        source="energy",
                                                        amount=amount,
                                                        metadata={}))
        LedgerService(self.config).create_or_update_simple_transactions(transactions)
