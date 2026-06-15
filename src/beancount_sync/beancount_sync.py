from __future__ import annotations
from datetime import date
from typing import TYPE_CHECKING

from pydantic import BaseModel
import logging

from constants import EXCHANGE_TX_CREATED, EXCHANGE_TX_UPDATED
from model import Config
from decimal import Decimal
import pika

if TYPE_CHECKING:
    from beancount_sync.beancount import Beancount
    from beancount_sync.accrual import BeancountAccruals


# Only consider transactions with two legs: credit and debit
class BeancountTransaction(BaseModel):
    external_id: str  # the banks record of this transaction
    tx_date: date
    amount: Decimal
    credit_account: str  # where money comes from
    debit_account: str  # where money goes to
    payee: str  # summary of who is being paid
    description: str  # aka narration - more detail about transaction
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

    def __init__(self, config: Config, beancount: Beancount, rmq_connection: pika.BlockingConnection):
        self.config = config
        self.beancount = beancount
        self.accrual = BeancountAccruals(self.beancount, config)
        self.rmq_connection = rmq_connection
        self.channel = self.rmq_connection.channel()

    def sync(self, ledger_name: str, transactions: list[BeancountTransaction]):
        """"
        Sync the given transactions with the ledger
        For any updates, publish these to the appropiate topics
        """
        created, updated = self._update_ledger(ledger_name, transactions)
        for created_tx in created:
            self._publish_event(created_tx, EXCHANGE_TX_CREATED)
        for created_tx in updated:
            self._publish_event(created_tx, EXCHANGE_TX_UPDATED)

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

        # Compute any new accrual transactions
        self.accrual.run_accruals()
        return created, updated

    def _publish_event(self, transaction: BeancountTransaction, exchange: str):
        logging.info("Sending beancount for external_id %s to exchange %s", transaction.external_id, exchange)
        self.channel.basic_publish(exchange, "", body=transaction.model_dump_json())


