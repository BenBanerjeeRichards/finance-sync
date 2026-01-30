from minio import Minio
from pydantic import BaseModel
import logging
from model import Config, Transaction, Merchant, Counterparty, Tab, Attachment, TransactionUpdate
import re
import datetime

from monzo import MonzoClient
from storage import Store, MONZO_TX_FILE


class MonzoConfig(BaseModel):
    access_token: str
    refresh_token: str
    client_id: str
    client_secret: str
    account_id: str


class MonzoImporter:

    def __init__(self, config: Config, monzo_client: MonzoClient, minio_client: Minio):
        self.monzo_client = monzo_client
        self.minio_client = minio_client
        self.store = Store(minio_client)
        self.config = config

    def import_transactions(self, since: datetime.datetime):
        logging.info("Syncing transactions since=%s", since)
        synced_monzo_transactions = []
        for batch in self.monzo_client.get_transactions_since(since):
            [MonzoImporter.augment_monzo_transaction(t) for t in batch]
            synced_monzo_transactions.extend(batch)
        logging.info("Got %s transactions from monzo", len(synced_monzo_transactions))
        synced_transactions = [self.monzo_to_transaction(tx) for tx in synced_monzo_transactions]
        created, updated = self._write_transactions_to_storage(synced_transactions)
        logging.info("Updated monzo transactions: %s created, %s updated", len(updated), len(created))

    def update_notes(self, updates: list[TransactionUpdate]):
        # Why not write to monzo and then re-sync?
        # Because we can only get last 90 days from monzo. Therefore, this would limit us to the past few months
        # Instead we can update notes and assume monzo sync will work
        logging.info("Updating %s transactions with notes", len(updates))
        transactions = self.store.load_list(MONZO_TX_FILE, Transaction)

        update_transaction_ids = []
        for update in updates:
            found_txs = [tx for tx in transactions if tx.id == update.transactionId]
            tx = None if not found_txs else found_txs[0]
            if not tx:
                logging.warning("Failed to find transaction for note update %s", update)
                continue
            tx.notes = update.note
            tx.tags = MonzoImporter.get_tags_from_string(update.note)
            update_transaction_ids.append(tx.id)

        self.store.write(MONZO_TX_FILE, transactions)

        for update in updates:
            if update.transactionId in update_transaction_ids:
                self.monzo_client.set_transaction_notes(update.transactionId, update.note)

    def _write_transactions_to_storage(self, synced_transactions: list[Transaction]) -> tuple[
        list[Transaction], list[Transaction]]:
        synced_id_to_tx = {t.id: t for t in synced_transactions}
        new_stored_transactions = []
        updated = []
        created = []
        total_unchanged = 0

        stored_transactions = self.store.load_list(MONZO_TX_FILE, Transaction)

        for stored_tx in stored_transactions:
            if stored_tx.id in synced_id_to_tx:
                new_tx = synced_id_to_tx[stored_tx.id]
                if new_tx != stored_tx:
                    # Only return updated if something has changed
                    updated.append(synced_id_to_tx[stored_tx.id])
                new_stored_transactions.append(synced_id_to_tx[stored_tx.id])
                del synced_id_to_tx[stored_tx.id]
            else:
                new_stored_transactions.append(stored_tx)
                total_unchanged += 1
        for synced_tx_id in synced_id_to_tx:
            new_stored_transactions.append(synced_id_to_tx[synced_tx_id])
            created.append(synced_id_to_tx[synced_tx_id])

        logging.info("Syncing transactions to minio. updated=%s created=%s unchanged=%s", len(updated), len(created),
                     total_unchanged)
        self.store.write(MONZO_TX_FILE, new_stored_transactions)
        return created, updated

    @staticmethod
    def get_tags_from_string(notes: str) -> list[str]:
        tags = re.findall(r"#[^\s]+", notes)
        return [t[1:] for t in tags if len(t) > 1]

    @staticmethod
    def augment_monzo_transaction(tx):
        if tx.get("notes"):
            tx["tags"] = MonzoImporter.get_tags_from_string(tx["notes"])

    def monzo_to_transaction(self, tx) -> Transaction:
        merchant = None
        counterparty = None
        tab = None
        if tx.get("merchant"):
            m = tx["merchant"]
            merchant = Merchant(name=m["name"], logo_url=m["logo"], lat=m["address"].get("latitude"),
                                long=m["address"].get("longitude"), country=m["address"].get("country"),
                                id=m["id"], group_id=m.get("group_id"), online=m.get("online"),
                                approximate=m["address"].get("approximate", False))
        if tx.get("counterparty"):
            counterparty = Counterparty(id=tx["counterparty"].get("user_id"), name=tx["counterparty"].get("name"),
                                        is_monzo=tx["counterparty"].get("preferred_name") is not None,
                                        account_number=tx["counterparty"].get("account_number"))
        tags = [] if not tx.get("tags") else tx["tags"]

        if tx.get("tab"):
            tx_tab = tx["tab"]
            tab_names = [p.get("name", "") for p in tx_tab.get("participants", [])]
            tab = Tab(name=tx_tab.get("name", ""), id=tx_tab.get("id", ""), participant_names=tab_names)
        is_split = tx.get("metadata", {}).get("bill_splitting_id", False) != False

        tx_attachments = tx.get("attachments", [])
        if tx_attachments is None:
            tx_attachments = []
        attachments = [Attachment(url=a.get("url"), file_type=a.get("file_type")) for a in tx_attachments]
        original_tx_id = tx.get("metadata", {}).get("original_transaction_id")
        pot_id = tx.get("metadata", {}).get("pot_id")
        # We map custom categories straight away to a readable string to hide the implementation detail
        category = self.config.monzoCategoryMappings.get(tx["category"], tx["category"])

        return Transaction(id=tx["id"], created=tx["created"], settled=tx.get("settled"), merchant=merchant,
                           amount=tx["amount"],
                           category=category, tags=tags, notes=tx["notes"], local_currency=tx["local_currency"],
                           local_amount=tx["local_amount"], include_in_spending=tx["include_in_spending"],
                           counterparty=counterparty,
                           attachments=attachments, tab=tab, is_split=is_split, original_transaction_id=original_tx_id,
                           pot_id=pot_id)
