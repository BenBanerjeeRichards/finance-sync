from typing import Literal

import pika
from beancount.core.data import Transaction
from beancount.loader import load_string
from beancount.parser import printer
from minio import Minio

import shutil
from pathlib import Path
import io
import logging

from mypy.checker_state import contextmanager
from pydantic import BaseModel

from beancount_sync.beancount_sync import SimpleLedgerTransaction, BadTransactionError
from beancount_sync.beancount_util import new_posting, create_amount_from_decimal, new_transaction, transactions_equal
from beancount import loader
from beanquery import query

from constants import EXCHANGE_TX_CREATED, EXCHANGE_TX_UPDATED, EXCHANGE_LEDGER_UPDATED
from model import Config
import hashlib

TMP_DIR = Path("/tmp/beancount")


class BeancountExport(BaseModel):
    contents: str
    has_changed: bool


class LedgerUpdatedEvent(BaseModel):
    ledger_name: str


# Encapsulates all interactions with beancount files
class Beancount:

    def __init__(self, minio_client: Minio, rmq_connection: pika.BlockingConnection, config: Config):
        self.minio_client = minio_client
        self.ledger_bucket = config.bucket
        self.config = config
        self.editable_bean_files = [config.beanFileName, config.santanderBeanFileName, config.accrualBeanFileName]
        self.beancount_files: dict[str, BeancountFile] = {}
        self.main_ledger_file = config.mainLedgerFile
        self.channel = rmq_connection.channel()

        self._cleanup()
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        self._load_beancount_state()

    def create_or_update_transaction(self, file_name: str, transaction: SimpleLedgerTransaction) -> Literal[
        'new', 'updated', 'none']:
        if file_name not in self.beancount_files:
            logging.info("attempt to write to file %s not in editable files (%s)", file_name, self.editable_bean_files)
            raise Exception(f"File {file_name} does not exist in editable files")
        # update_type = self.beancount_files[file_name].add_or_update(transaction)
        # if update_type == 'new':
        #     self._publish_event(transaction, EXCHANGE_TX_CREATED)
        # if update_type == 'updated':
        #     self._publish_event(transaction, EXCHANGE_TX_UPDATED)

        from ledger.ledger_service import LedgerService
        # for now just skip monzo + santander as we do all those at the same time during sync
        if file_name not in [self.config.beanFileName, self.config.santanderBeanFileName]:
            LedgerService(self.config).write_beancount_transactions(file_name, [transaction])

        return 'none'

    def delete_transaction(self, file_name: str, external_id: str):
        self.beancount_files[file_name].delete(external_id)

    def find_all_by_metadata_by_date_desc(self, key: str, value: str) -> list[Transaction]:
        metadata_query = """
            SELECT entry 
            WHERE any_meta('{}') = '{}'
            ORDER BY date DESC
        """.format(key, value)
        return self.find_all_by_query(metadata_query)

    def find_all_by_query(self, q: str) -> list[Transaction]:
        result_types, result_rows = query.run_query(self.entries, self.options_map, q)
        transactions = [
            row[0] for row in result_rows
            if isinstance(row[0], Transaction)
        ]
        return transactions

    @contextmanager
    def transaction(self):
        # If an exception occurs, then we don't write any changes to s3
        try:
            yield self
        except Exception as e:
            logging.exception("beancount transaction failed")
            raise e
        else:
            self.sync_to_minio()
        finally:
            # always retrieve state again from s3
            self._load_beancount_state()

    def sync_to_minio(self):
        # as we only allow editing of editable files, we only need to sync those files
        logging.info("writing %s files to s3 %s", len(self.beancount_files), self.ledger_bucket)
        for file_name in self.beancount_files:
            new_contents = self.beancount_files[file_name].export_as_string()
            file_bytes = new_contents.contents.encode("utf-8")
            file_stream = io.BytesIO(file_bytes)
            file_size = len(file_bytes)
            self.minio_client.put_object(
                bucket_name=self.ledger_bucket,
                object_name=file_name,
                data=file_stream,
                length=file_size,
                content_type="text/plain"
            )
            if new_contents.has_changed:
                self._publish_ledger_changed_event(file_name, EXCHANGE_LEDGER_UPDATED)

    def _load_beancount_state(self):
        # We need to download all files to we can query our full beancount state
        # We will only ever write to specific files though (managed through in BeancountFile)
        # Copy all files from s3 to our local state
        objects = list(self.minio_client.list_objects(self.ledger_bucket))
        logging.info("building state from %s objects", len(objects))
        for obj in objects:
            local_file_path = TMP_DIR / obj.object_name
            local_file_path.parent.mkdir(parents=True, exist_ok=True)
            self.minio_client.fget_object(
                bucket_name=self.ledger_bucket,
                object_name=obj.object_name,
                file_path=str(local_file_path)
            )
            if obj.object_name in self.editable_bean_files:
                self.beancount_files[obj.object_name] = BeancountFile(local_file_path.read_text(encoding="utf-8"))

        self.entries, errors, self.options_map = loader.load_file(TMP_DIR / self.main_ledger_file)
        logging.info("loaded %s entries from %s", len(self.entries), self.main_ledger_file)

    def _cleanup(self):
        if TMP_DIR.exists():
            shutil.rmtree(TMP_DIR)

    def _publish_event(self, transaction: SimpleLedgerTransaction, exchange: str):
        logging.info("Sending beancount for external_id %s to exchange %s", transaction.external_id, exchange)
        self.channel.basic_publish(exchange, "", body=transaction.model_dump_json())

    def _publish_ledger_changed_event(self, ledger: str, exchange: str):
        logging.info("Sending ledger changed event for ledger %s to exchange %s", ledger, exchange)
        self.channel.basic_publish(exchange, "", body=LedgerUpdatedEvent(ledger_name=ledger).model_dump_json())


class BeancountFile:

    def __init__(self, beancount_contents: str):
        entries, errors, options = load_string(beancount_contents)
        # track if changes have been made
        # less error-prone than having to track some dirty flag through every change
        self.initial_hash = self._file_hash(beancount_contents)
        self.entries_by_id: dict[str, Transaction] = {}
        for e in entries:
            ext_id = e.meta.get("external_id")
            if not ext_id:
                logging.warning("No external id found on transaction %s", e)
                continue
            if not isinstance(e, Transaction):
                logging.info("Invalid transaction %s", e)
                continue
            self.entries_by_id[ext_id] = e
        logging.info("Loaded %s entries from file", len(self.entries_by_id.keys()))

    def add_or_update(self, tx: SimpleLedgerTransaction) -> Literal['new', 'updated', 'none']:
        existing = self.entries_by_id.get(tx.external_id)
        auth_amount = None
        if existing:
            # This is an update: we can only update payee or description
            if len(existing.postings) != 2:
                raise BadTransactionError(
                    f"Only transactions with two postings supported, id {tx.external_id} has {len(existing.postings)}: {existing}")
            tx_units = existing.postings[0].units
            existing_tx_amount = 0 if not tx_units or not tx_units.number else tx_units.number
            if tx_units is None or abs(existing_tx_amount) != abs(tx.amount):
                # we do not model authorisations properly
                # therefore, if more than is authorised is captured, we need to update the ledger
                # to at least keep some record, note this change on the metadata and only allow a single change
                # in practise, few MCCs allow this - e.g. public transport (Lothian buses for example)
                logging.info(
                    f"Changing amount on entry {tx.external_id} ({tx.payee} - {tx.description or "n/a"}) from {abs(existing_tx_amount)} to {abs(tx.amount)}")
                if existing.meta.get("authorisation_amount") is not None:
                    raise BadTransactionError(
                        f"{tx.external_id}: Change of transaction amount from {existing_tx_amount} to {tx.amount}, however authorisation_amount was is already set")
                auth_amount = existing_tx_amount
            if tx.tx_date != existing.date:
                raise BadTransactionError(
                    f"Can not update transaction date to {tx.tx_date}: {tx.external_id} {existing}")

        # Amount always > 0 -we use the credit/debit accounts to determine movement direction and add sign appropiatly
        credit_posting = new_posting(account=tx.credit_account, units=create_amount_from_decimal(-1 * tx.amount))
        debit_posting = new_posting(account=tx.debit_account, units=create_amount_from_decimal(tx.amount))
        meta = {**tx.ledger_metadata, "external_id": tx.external_id}
        if auth_amount is not None:
            meta["authorisation_amount"] = auth_amount
        new_tx = new_transaction(date=tx.tx_date, flag="!" if tx.flagged else "*",
                                 postings=[credit_posting, debit_posting], payee=tx.payee, narration=tx.description,
                                 meta=meta, tags=tx.tags)
        diff = transactions_equal(existing, new_tx)
        status: Literal['none', 'updated', 'new'] = 'none' if not diff else (
            'updated' if existing is not None else 'new')

        if status == 'updated':
            logging.info("Updated %s: diff=%s", tx.external_id, diff)
        self.entries_by_id[tx.external_id] = new_tx
        return status

    def delete(self, external_id: str):
        if external_id not in self.entries_by_id:
            logging.warning("failed to find transaction to delete: %s", external_id)
            return
        del self.entries_by_id[external_id]

    def export_as_string(self) -> BeancountExport:
        s = ""
        for entry in self.entries_by_id.values():
            s += printer.format_entry(entry) + "\n"
        new_hash = BeancountFile._file_hash(s)
        changed = not (new_hash == self.initial_hash)
        # not sure if this class is the best place for this state
        self.initial_hash = new_hash
        return BeancountExport(contents=s, has_changed=changed)

    @staticmethod
    def _file_hash(contents: str) -> str:
        return hashlib.sha256(contents.encode('utf-8')).hexdigest()
