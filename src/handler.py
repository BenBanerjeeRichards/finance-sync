import datetime
import json
from functools import wraps

from minio import Minio
from pydantic import BaseModel
import logging

from beancount_sync.accrual import BeancountAccruals
from beancount_sync.beancount import Beancount
from beancount_sync.beancount_sync import BeancountTransaction, BeancountSync
from beancount_sync.monzo_translater import MonzoTranslater
from beancount_sync.santander_translater import SantanderTranslater
from importer.monzo_import import MonzoImporter
from importer.santander_import import SantanderImporter
from model import Transaction, MonzoStore, Config, MonzoSyncMessage, TransactionUpdate, SantanderTransactions
from monzo import MonzoClient
from notification.notifier import Notifier
from santander import from_gc
from storage import SANTANDER_TX_FILE, write_monzo_store, MONZO_TX_FILE, Store
from notification.discord import DiscordClient
from pika.adapters.blocking_connection import BlockingConnection


def rmq_handler(body_type: type[BaseModel] | None = None):
    def do_decorate(message_handler):
        @wraps(message_handler)
        def handle(*args):
            if len(args) == 4:
                ch, method, properties, body = args
                self = None
            elif len(args) == 5:
                self, ch, method, properties, body = args
            else:
                raise RuntimeError(
                    f"Invalid number of arguments to rmq_handler, expected 4 or 5, got {len(args)}: {args}")

            def self_aware_handler(f, *inner_args):
                if self:
                    return f(self, *inner_args)
                else:
                    return f(*inner_args)

            try:
                logging.info("Got message for handler %s", message_handler.__name__)
                if body_type is None:
                    return self_aware_handler(message_handler)
                parsed_json = json.loads(body.decode())
                if isinstance(parsed_json, list):
                    return self_aware_handler(message_handler, [body_type(**item) for item in parsed_json])
                else:
                    return self_aware_handler(message_handler, body_type(**parsed_json))
            except:
                logging.exception("Failed to handle %s message", message_handler.__name__)

        return handle

    return do_decorate


class Handler:
    """
    Main entry point for all tasks triggered from RMQ
    """

    def __init__(self, config: Config, minio_client: Minio, discord_client: DiscordClient, monzo_client: MonzoClient,
                 santander_import: SantanderImporter, pika_connection: BlockingConnection, notifier: Notifier,
                 beancount: Beancount):
        self.config = config
        self.minio_client = minio_client
        self.discord_client = discord_client
        self.monzo_client = monzo_client
        self.santander_import = santander_import
        self.pika_connection = pika_connection
        self.monzo_importer = MonzoImporter(config, monzo_client, self.minio_client)
        self.notifier = notifier
        self.store = Store(self.minio_client)
        self.beancount = beancount
        self.beancount_sync = BeancountSync(config, beancount, pika_connection)

    @rmq_handler(MonzoSyncMessage)
    def on_monzo_sync_transactions(self, sync_message: MonzoSyncMessage):
        sync_since = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=sync_message.past_days)
        self.monzo_importer.import_transactions(sync_since)
        sync_monzo_ledger(self.config, self.store, self.beancount_sync)

    @rmq_handler(TransactionUpdate)
    def on_monzo_update_notes(self, updates: list[TransactionUpdate]):
        self.monzo_importer.update_notes(updates)
        sync_monzo_ledger(self.config, self.store, self.beancount_sync)

    @rmq_handler()
    def on_monzo_refresh_token(self):
        logging.info("Refreshing monzo token")
        access, refresh = self.monzo_client.get_access_token()
        new_store = MonzoStore(access_token=access, refresh_token=refresh)
        write_monzo_store(self.minio_client, new_store)

    @rmq_handler()
    def on_update_ledger(self):
        sync_monzo_ledger(self.config, self.store, self.beancount_sync)
        sync_santander_ledger(self.config, self.store, self.beancount_sync)

    @rmq_handler()
    def on_santander_sync_transactions(self):
        self.santander_import.import_transactions()
        age_days = self.santander_import.requisition_oldest_days()
        if age_days >= self.config.gocardless.notifyOlderThan:
            self.notifier.notify_expiring("GoCardless", self.config.gocardless.startUri, 90 - age_days)
        sync_santander_ledger(self.config, self.store, self.beancount_sync)

    @rmq_handler(BeancountTransaction)
    def notify_new_transaction(self, tx: BeancountTransaction):
        if tx.credit_account == self.config.santanderCashAccount or tx.debit_account == self.config.santanderCashAccount:
            self.notifier.send_santander_discord_notification(self.config.santanderCashAccount, tx)


def sync_santander_ledger(config: Config, store: Store, beancount_sync: BeancountSync):
    santander_transactions = store.load(SANTANDER_TX_FILE, SantanderTransactions).transactions
    translater = SantanderTranslater(config)
    mapped_transactions = [translater.translate_to_beancount(from_gc(tx)) for tx in santander_transactions]
    ledger_transactions = [tx for tx in mapped_transactions if tx]
    beancount_sync.sync(config.santanderBeanFileName, ledger_transactions)


def sync_monzo_ledger(config: Config, store: Store, beancount_sync: BeancountSync):
    monzo_transactions = store.load_list(MONZO_TX_FILE, Transaction)
    translater = MonzoTranslater(config)
    # We limit start from FY25 as that is only as far back as I have Santander and can be bothered to do the manual postings for
    ledger_transactions = [translater.translate_to_beancount(tx) for tx in monzo_transactions if tx.created > "2024-04"]
    beancount_sync.sync(config.beanFileName, ledger_transactions)
