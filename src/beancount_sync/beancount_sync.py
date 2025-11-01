from datetime import date
from pydantic import BaseModel
import logging

from main import EXCHANGE_TX_CREATED, EXCHANGE_TX_UPDATED
from model import Config
from src.beancount_sync.beancount_util import *
from storage import write_file
import minio
from beancount.core.data import Transaction
from decimal import Decimal
from beancount.parser import printer
from beancount.loader import load_string
from typing import Literal
import pika


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


class BadTransactionError(Exception):
    pass


class BeancountSync:

    def __init__(self, config: Config, minio_client: minio.Minio, rmq_connection: pika.BlockingConnection):
        self.config = config
        self.minio_client = minio_client
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
        response = self.minio_client.get_object(bucket_name=self.config.bucket, object_name=ledger_name)
        tx_contents = response.read().decode("utf-8")
        f = BeancountFile(tx_contents)
        updated = []
        created = []

        for tx in transactions:
            status = f.add_or_update(tx)
            if status == 'new':
                created.append(tx)
            elif status == 'updated':
                updated.append(tx)
        write_file(self.minio_client, self.config.bucket, ledger_name, f.export_as_string())
        logging.info("Updated ledger: new=%s updated=%s", len(created), len(updated))
        return created, updated

    def _publish_event(self, transaction: BeancountTransaction, exchange: str):
        logging.info("Sending beancount for external_id %s to exchange %s", transaction.external_id, exchange)
        self.channel.basic_publish(exchange, "", body=transaction.model_dump_json())


class BeancountFile:

    def __init__(self, filename: str):
        entries, errors, options = load_string(filename)
        self.entries_by_id: dict[str, Transaction] = {}
        for e in entries:
            ext_id = e.meta.get("external_id")
            if not ext_id:
                logging.warning("No external id found on transaction %s", e)
                continue
            self.entries_by_id[ext_id] = e
        logging.info("Loaded %s entries from file", len(self.entries_by_id.keys()))

    def add_or_update(self, tx: BeancountTransaction) -> Literal['new', 'updated', 'none']:
        existing = self.entries_by_id.get(tx.external_id)
        status = 'new'
        if existing:
            # This is an update: we can only update payee or description
            if len(existing.postings) != 2:
                raise BadTransactionError(
                    f"Only transactions with two postings supported, id {tx.external_id} has {len(existing.postings)}: {existing}")
            if abs(existing.postings[0].units.number) != abs(tx.amount):
                raise BadTransactionError(
                    f"Can not update amount on transaction: existing is {existing.postings[0].units} and new is {tx.amount}. TX {tx.external_id} {existing}")
            if tx.tx_date != existing.date:
                raise BadTransactionError(
                    f"Can not update transaction date to {tx.tx_date}: {tx.external_id} {existing}")

        # Amount always > 0 -we use the credit/debit accounts to determine movement direction and add sign appropiatly
        credit_posting = new_posting(account=tx.credit_account, units=create_amount_from_decimal(-1 * tx.amount))
        debit_posting = new_posting(account=tx.debit_account, units=create_amount_from_decimal(tx.amount))
        new_tx = new_transaction(date=tx.tx_date, flag="!" if tx.flagged else "*",
                                 postings=[credit_posting, debit_posting], payee=tx.payee, narration=tx.description,
                                 meta={"external_id": tx.external_id})
        status = 'none' if transactions_equal(new_tx, existing) else ('updated' if existing is not None else 'new')

        if status == 'updated':
            logging.info("Updated: old=%s new=%s", existing, new_tx)
        self.entries_by_id[tx.external_id] = new_tx
        return status

    def export_as_string(self) -> str:
        s = ""
        for entry in self.entries_by_id.values():
            s += printer.format_entry(entry) + "\n"
        return s
