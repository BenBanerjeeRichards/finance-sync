import datetime
import json
from functools import wraps

from pydantic import BaseModel
import logging

import dependencies
from poster.poster import run_posters
from importer.monzo_import import MonzoImporter
from model import MonzoSyncMessage, TransactionUpdate, SimpleLedgerTransaction
from storage import Store


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

    def __init__(self):
        self.monzo_importer = MonzoImporter(dependencies.get_config(), dependencies.get_monzo_client(), dependencies.get_minio_client())
        self.store = Store(dependencies.get_minio_client())

    @rmq_handler(MonzoSyncMessage)
    def on_monzo_sync_transactions(self, sync_message: MonzoSyncMessage):
        sync_since = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=sync_message.past_days)
        self.monzo_importer.import_transactions(sync_since)
        run_posters()

    @rmq_handler(TransactionUpdate)
    def on_monzo_update_notes(self, updates: list[TransactionUpdate]):
        self.monzo_importer.update_notes(updates)
        run_posters()

    @rmq_handler()
    def on_monzo_refresh_token(self):
        from importer.import_service import ImportService

        logging.info("Refreshing monzo token")
        access, refresh = dependencies.get_monzo_client().get_access_token()
        ImportService.update_monzo_tokens(dependencies.get_settings().settings.monzo_client_id, access, refresh)

    @rmq_handler()
    def on_update_ledger(self):
        run_posters()

    @rmq_handler()
    def on_santander_sync_transactions(self):
        config = dependencies.get_config()
        santander_importer = dependencies.get_santander_importer()
        santander_importer.import_transactions()
        age_days = santander_importer.update_expires_dates()
        if age_days >= config.config.gocardless.notifyOlderThan:
            dependencies.get_notifier().notify_expiring("GoCardless", config.gocardless.startUri, 90 - age_days)
        run_posters()

    @rmq_handler(SimpleLedgerTransaction)
    def notify_new_transaction(self, tx: SimpleLedgerTransaction):
        config = dependencies.get_config()
        if tx.credit_account == config.santanderCashAccount or tx.debit_account == config.santanderCashAccount:
            dependencies.get_notifier().send_santander_discord_notification(config.santanderCashAccount, tx)
