import datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import urljoin

import requests
from pydantic import BaseModel
import logging

import dependencies
from model import SimpleLedgerTransaction
from poster.base_poster import BasePoster
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

    def __init__(self):
        self.config = dependencies.get_config()

        # TODO support configuring this in the frontend

    def run(self):
        if not self.config.energy:
            logging.info("Energy Sync not configured, skipping")
            return

        with Session.begin() as session:
            electric_prepay_account = LedgerService.get_account_by_full_name(session,
                                                                             self.config.energy.electricityPrepayAccount).id
            electric_expense_account = LedgerService.get_account_by_full_name(session,
                                                                              self.config.energy.electricityExpenseAccount).id
            gas_prepay_account = LedgerService.get_account_by_full_name(session,
                                                                        self.config.energy.gasPrepayAccount).id
            gas_expense_account = LedgerService.get_account_by_full_name(session,
                                                                         self.config.energy.gasExpenseAccount).id

            energy_client = EnergyClient(self.config.energy.energySyncBaseUrl)
            try:
                self._create_energy_transactions(energy_client, "electricity", electric_prepay_account,
                                                 electric_expense_account)
                self._create_energy_transactions(energy_client, "gas", gas_prepay_account,
                                                 gas_expense_account)
            except Exception as e:
                logging.exception("failed to sync energy")

    def _create_energy_transactions(self, client: EnergyClient, meter_type: Literal["gas", "electricity"],
                                    asset_account: uuid.UUID, expense_account: uuid.UUID):
        readings = client.get_monthly_readings(self.config.energy.startMonth, meter_type)
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
                                                        ledger_metadata={},
                                                        source="energy",
                                                        amount=amount,
                                                        metadata={}))
        LedgerService(self.config).create_or_update_transactions(self.config.accrualBeanFileName, transactions)
