from pydantic import BaseModel
import logging
from model import Config, Transaction, Merchant, Counterparty, Tab, Attachment
import monzo
import re
import datetime


class MonzoConfig(BaseModel):
    access_token: str
    refresh_token: str
    client_id: str
    client_secret: str
    account_id: str


class MonzoImporter:

    def __init__(self, config: Config, monzo_config: MonzoConfig):
        self.config = config
        self.monzo_client = monzo.MonzoClient(monzo_config.access_token, monzo_config.refresh_token,
                                              monzo_config.monzo_client_id, monzo_config.monzo_client_secret,
                                              monzo_config.monzo_account_id)

    def import_transactions(self, since: datetime.datetime):
        logging.info("Syncing transactions since=%s", since)
        synced_monzo_transactions = []
        for batch in self.monzo_client.get_transactions_since(since):
            [MonzoImporter.augment_monzo_transaction(t) for t in batch]
            synced_monzo_transactions.extend(batch)
        logging.info("Got %s transactions from monzo", len(synced_monzo_transactions))
        synced_transactions = [MonzoImporter.monzo_to_transaction(tx) for tx in synced_monzo_transactions]
        created, updated = self._write_transactions_to_storage(synced_transactions)

    @staticmethod
    def get_tags_from_string(notes: str) -> list[str]:
        tags = re.findall(r"#[^\s]+", notes)
        return [t[1:] for t in tags if len(t) > 1]

    @staticmethod
    def augment_monzo_transaction(tx):
        if tx.get("notes"):
            tx["tags"] = MonzoImporter.get_tags_from_string(tx["notes"])

    @staticmethod
    def monzo_to_transaction(tx) -> Transaction:
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
        return Transaction(id=tx["id"], created=tx["created"], settled=tx.get("settled"), merchant=merchant,
                           amount=tx["amount"],
                           category=tx["category"], tags=tags, notes=tx["notes"], local_currency=tx["local_currency"],
                           local_amount=tx["local_amount"], include_in_spending=tx["include_in_spending"],
                           counterparty=counterparty,
                           attachments=attachments, tab=tab, is_split=is_split, original_transaction_id=original_tx_id,
                           pot_id=pot_id)
