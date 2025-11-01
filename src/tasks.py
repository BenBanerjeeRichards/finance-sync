import datetime
import monzo
from beancount_sync.beancount_sync import BeancountSync
from beancount_sync.monzo_converter import MonzoTranslater
from model import TransactionUpdate, Transaction, Settings, MonzoStore, Merchant, Counterparty, Tab, Attachment, SantanderTransaction as GcSantanderTransaction, Config
from storage import load_monzo_store, write_monzo_store, load_transactions as load_monzo_transactions, write_transactions, SANTANDER_TX_FILE
import minio
from santander import SantanderTransaction, from_gc 
import logging
import re
from discord import DiscordClient
from processor import monzo_to_beancount_tx, santander_to_beancount_tx
from src.beancount_sync.beancount_sync import update_ledger
from pika.adapters.blocking_connection import BlockingChannel



class TaskProcessor:

    def __init__(self, settings: Settings, config: Config, minio_client: minio.Minio, discord_client: DiscordClient, pika_channel: BlockingChannel):
        monzo_store = load_monzo_store(minio_client)
        self.settings = settings
        self.config = config
        self.minio_client = minio_client
        self.monzo_client = TaskProcessor._create_monzo_client(monzo_store, settings)
        self.discord_client = discord_client
        self.channel = pika_channel


    def sync_monzo_transactions(self, since: datetime.datetime) -> tuple[list[Transaction], list[Transaction]]:
        logging.info("Syncing transactions since=%s", since)
        synced_monzo_transactions = []
        for batch in self.monzo_client.get_transactions_since(since):
            [augment_monzo_transaction(t) for t in batch]
            synced_monzo_transactions.extend(batch)
        logging.info("Got %s transactions from monzo", len(synced_monzo_transactions))
        synced_transactions =  [monzo_to_transaction(tx) for tx in synced_monzo_transactions]
        created, updated = self._write_transactions_to_storage(synced_transactions)
        
        ledger_created, ledger_updated = self.update_monzo_ledger()
        
        for updated_tx in set(updated+ledger_updated):
            self.monzo_to_beancount_tx("transaction.updated", updated_tx, monzo_to_beancount_tx(updated_tx, self.config))
        for creatd_tx in set(created + ledger_created):
            self.monzo_to_beancount_tx("transaction.created", creatd_tx, monzo_to_beancount_tx(creatd_tx, self.config))


    def _write_transactions_to_storage(self, synced_transactions: list[Transaction]) -> tuple[list[Transaction], list[Transaction]]:
        synced_id_to_tx = {t.id: t for t in synced_transactions}
        new_stored_transactions = []
        updated = []
        created =[]
        total_unchanged = 0

        stored_transactions = load_monzo_transactions(self.minio_client)

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

        logging.info("Syncing transactions to minio. updated=%s created=%s unchanged=%s", len(updated), len(created), total_unchanged)
        write_transactions(self.minio_client, new_stored_transactions)
        return (created, updated)


    def update_notes(self, updates: list[TransactionUpdate]):
        logging.info("Updating %s transactions with notes", len(updates))
        transactions = load_monzo_transactions(self.minio_client)
        update_transaction_ids = []
        for update in updates:
            tx = [tx for tx in transactions if tx.id == update.transactionId]
            tx = None if not tx else tx[0]
            if not tx:
                logging.warning("Failed to find transaction for note update %s", update)
                continue
            tx.notes = update.note
            tx.tags = get_tags_from_string(update.note)
            update_transaction_ids.append(tx.id)
            
        # Why not write to monzo and then re-sync?
        # Because we can only get last 90 days from monzo. Therefore this would limit us to the past few months
        # Instead we can update notes and assume monzo sync will work 
        write_transactions(self.minio_client, transactions)
        
        for update in updates:
            if update.transactionId in update_transaction_ids:
                self.monzo_client.set_transaction_notes(update.transactionId, update.note)


    def refresh_token(self):
        logging.info("Refreshing monzo token")
        access, refresh = self.monzo_client.get_access_token()
        new_store = MonzoStore(access_token=access, refresh_token=refresh)
        write_monzo_store(self.minio_client, new_store)
        self.monzo_client = TaskProcessor._create_monzo_client(new_store, self.settings)


    def update_monzo_ledger(self) -> tuple[list[Transaction], list[Transaction]]:
        monzo_transactions = load_monzo_transactions(self.minio_client)
        monzo_id_to_tx = {tx.id : tx for tx in monzo_transactions}
        # We limit start from FY25 as that is only as far back as I have Santander and can be bothered to do the manual postings for 
        ledger_txs = [monzo_to_beancount_tx(tx, self.config) for tx in monzo_transactions if tx.created > "2024-04"]
        created, updated = update_ledger(self.minio_client, self.config.bucket, self.config.beanFileName, ledger_txs)
        monzo_updated = [monzo_id_to_tx.get(tx.external_id) for tx in updated]
        monzo_created = [monzo_id_to_tx.get(tx.external_id) for tx in created]
        
        if None in monzo_created:
            raise ValueError("Failed to find all created ledger ids in monzo Transaction")
        if None in monzo_updated:
            raise ValueError("Failed to find all created ledger ids in monzo Transaction")

        return monzo_created, monzo_updated
    
    def update_santander_ledger(self) -> tuple[list[SantanderTransaction], list[SantanderTransaction]]:
        txs = self.load_santander_transactions()
        id_to_txs = {tx.id : tx for tx in txs}
        ledger_txs =[santander_to_beancount_tx(tx, self.config) for tx in txs]
        created, updated = update_ledger(self.minio_client, self.config.bucket, self.config.santanderBeanFileName, ledger_txs)
        
        s_updated = [id_to_txs.get(tx.external_id) for tx in updated]
        s_created = [id_to_txs.get(tx.external_id) for tx in created]
        
        if None in s_updated:
            raise ValueError("Failed to find all updated ledger ids in santander Transaction")
        if None in s_created:
            raise ValueError("Failed to find all created ledger ids in santander Transaction")
        return s_created, s_updated

    def update_ledgers(self):
        # TODO pika
        syncer = BeancountSync(self.config, self.minio_client, self.channel)
        syncer.sync(monzo_ledger_name, monzo_beancount_transactions)
        syncer.sync(santander_ledger_name, santander_beancount_transactions)

    
    def load_santander_transactions(self) -> list[SantanderTransaction]:
        gc_transactions = self.store.load(SANTANDER_TX_FILE, GcSantanderTransaction).transactions
        return [from_gc(tx) for tx in gc_transactions]
    
    def send_santander_discord_notification(self, tx: SantanderTransaction) -> None:
        formatted_amount = f"£{abs(tx.amount):.2f}"

        if tx.amount > 0:
            message = f"💸 Recieved {formatted_amount} from {tx.counterparty_name or tx.description}"
        else:
            message = f"💵 Spent {formatted_amount} at {tx.counterparty_name or tx.description}"
        self.discord_client.send_message(message)

    def notify_expiring(self, name: str, url: str | None, days: int | None) -> None:
        message = f"⚠️ {name} connection expires {"soon" if not days else f"in {days} days"}"
        if url:
            message += f". Re-connect: {url}"
        self.discord_client.send_message(message)

    @staticmethod
    def _create_monzo_client(monzo_store: MonzoStore, settings: Settings) -> monzo.MonzoClient:
        return monzo.MonzoClient(monzo_store.access_token, monzo_store.refresh_token,
            settings.monzo_client_id, settings.monzo_client_secret, settings.monzo_account_id)
