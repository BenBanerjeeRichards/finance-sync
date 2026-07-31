from __future__ import annotations
from datetime import date, datetime
from typing import TYPE_CHECKING
import uuid
from pydantic import BaseModel
import logging

from model import Config
from decimal import Decimal

if TYPE_CHECKING:
    from beancount_sync.beancount import Beancount


# Simple transaction with just two legs
# This is 99.9% of our transactions and currently 100% of automatically generated ones
class SimpleLedgerTransaction(BaseModel):
    external_id: str  # the banks record of this transaction
    tx_date: date
    tx_datetime: datetime | None = None
    amount: Decimal
    credit_account_id: uuid.UUID  # where money comes from
    debit_account_id: uuid.UUID  # where money goes to
    payee: str  # summary of who is being paid
    description: str  # aka narration - more detail about transaction
    tags: list[str] = []  # tags added to further categorise e.g. #travel
    flagged: bool = False  # needs attention
    # the full data from the source of this transaction: e.g. monzo api data
    # can be used for more granular information
    metadata: dict = {}
    # Metadata to add directly into the ledger
    ledger_metadata: dict = {}
    source: str = ""
    local_amount: Decimal | None = None
    local_currency: str | None = None


class BadTransactionError(Exception):
    pass


class BeancountSync:

    def __init__(self, config: Config, beancount: Beancount):
        from beancount_sync.accrual import BeancountAccruals
        from beancount_sync.energy_sync import EnergySync

        self.config = config
        self.beancount = beancount
        self.accrual = BeancountAccruals(self.beancount, config)
        self.energy_sync = EnergySync(self.config, self.beancount)

    def sync(self):
        """"
        Sync the given transactions with the ledger
        For any updates, publish these to the appropiate topics
        """
        self._update_ledger()

    def _update_ledger(self) -> None:

        # TODO move elsewhere
        from ledger.ledger_service import LedgerService
        LedgerService(self.config).sync_ledger()

        # Compute any new accrual transactions & energy
        self.accrual.run_accruals()
        self.energy_sync.run_energy_sync()

