import datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import urljoin

import requests
from pydantic import BaseModel
import logging

from beancount_sync.beancount import Beancount
from beancount_sync.beancount_sync import BeancountTransaction
from model import Config


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
class EnergySync:

    def __init__(self, config: Config, beancount: Beancount):
        self.config = config
        self.beancount = beancount

    def run_energy_sync(self):
        if not self.config.energy:
            logging.info("Energy Sync not configured, skipping")
            return

        energy_client = EnergyClient(self.config.energy.energySyncBaseUrl)

        try:
            self._create_energy_transactions(energy_client, "electricity", self.config.energy.electricityPrepayAccount,
                                             self.config.energy.electricityExpenseAccount)
            self._create_energy_transactions(energy_client, "gas", self.config.energy.gasPrepayAccount,
                                             self.config.energy.gasExpenseAccount)
        except Exception as e:
            logging.exception("failed to sync energy")

    def _create_energy_transactions(self, client: EnergyClient, meter_type: Literal["gas", "electricity"],
                                    asset_account: str, expense_account: str):
        readings = client.get_monthly_readings(self.config.energy.startMonth, meter_type)

        with self.beancount.transaction() as beancount_tx:
            for month, reading_amount in readings.items():
                external_id = f"energy_{meter_type}_{month}"  # idempotency key, one reading we update per month
                reading_date = datetime.datetime.strptime(month, "%Y-%m").date()
                amount = Decimal(reading_amount) / Decimal("100")
                tx = BeancountTransaction(external_id=external_id,
                                          tx_date=reading_date,
                                          credit_account=asset_account,
                                          debit_account=expense_account,
                                          payee=f"Energy consumption ({meter_type})",
                                          description="",
                                          flagged=False,
                                          ledger_metadata={},
                                          source="energy",
                                          amount=amount,
                                          metadata={})
                beancount_tx.create_or_update_transaction(self.config.accrualBeanFileName, tx)
