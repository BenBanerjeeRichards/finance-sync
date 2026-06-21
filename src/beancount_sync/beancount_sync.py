from __future__ import annotations
from datetime import date
from typing import TYPE_CHECKING

from pydantic import BaseModel
import logging

from model import Config
from decimal import Decimal

if TYPE_CHECKING:
    from beancount_sync.beancount import Beancount


# Only consider transactions with two legs: credit and debit
class BeancountTransaction(BaseModel):
    external_id: str  # the banks record of this transaction
    tx_date: date
    amount: Decimal
    credit_account: str  # where money comes from
    debit_account: str  # where money goes to
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

    def sync(self, ledger_name: str, transactions: list[BeancountTransaction]):
        """"
        Sync the given transactions with the ledger
        For any updates, publish these to the appropiate topics
        """
        self._update_ledger(ledger_name, transactions)

    def _update_ledger(self, ledger_name: str, transactions: list[BeancountTransaction]) -> tuple[
        list[BeancountTransaction], list[BeancountTransaction]]:
        updated = []
        created = []

        with self.beancount.transaction() as beancount_tx:
            for tx in transactions:
                try:
                    status = beancount_tx.create_or_update_transaction(ledger_name, tx)
                    if status == 'new':
                        created.append(tx)
                    elif status == 'updated':
                        updated.append(tx)
                except Exception as e:
                    raise RuntimeError(f"Failed to add or update transaction {tx.external_id}") from e
            logging.info("Updated ledger: new=%s updated=%s", len(created), len(updated))

        # Compute any new accrual transactions & energy
        self.accrual.run_accruals()
        self.energy_sync.run_energy_sync()
        return created, updated

