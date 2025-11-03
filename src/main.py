import yaml
from pika.adapters.blocking_connection import BlockingConnection

from importer.santander_import import SantanderImporter
from model import Settings
import os
import minio
import pika
import logging

from monzo import MonzoClient
from notification.notifier import Notifier
from handler import Handler
from gocardless.gc_connection import GcConnection
from gocardless.gocardless import GoCardlessClient
import multiprocessing
from storage import Store, load_monzo_store
from notification.discord import DiscordClient
from model import Config

EXCHANGE_TX_UPDATED = "transaction.updated"
EXCHANGE_TX_CREATED = "transaction.created"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


def load_settings() -> Settings:
    return Settings(
        monzo_account_id=os.environ["MONZO_ACCOUNT_ID"],
        monzo_client_id=os.environ["MONZO_CLIENT_ID"],
        monzo_client_secret=os.environ["MONZO_CLIENT_SECRET"],
        rabbitmq_connection_string=os.environ["RABBITMQ_CONNECTION_STRING"],
        minio_endpoint=os.environ["MINIO_ENDPOINT"],
        minio_access=os.environ["MINIO_ACCESS"],
        minio_secret=os.environ["MINIO_SECRET"],
        minio_secure=os.environ["MINIO_SECURE"] != "false",
        config_path=os.environ["CONFIG_PATH"],
        gc_secret_id=os.environ["GC_SECRET_ID"],
        gc_secret_key=os.environ["GC_SECRET_KEY"],
        santander_discord_webhook=os.environ["SANTANDER_DISCORD_WEBHOOK"]
    )


def listen_for_updates(pika_connection: BlockingConnection, handler: Handler):
    # Wire everything up...not massivly sustainable but ok for something small
    channel = pika_connection.channel()

    # Command queues
    channel.queue_declare(queue="monzo-sync-transactions", durable=True)
    channel.queue_declare(queue="santander-sync-transactions", durable=True)
    channel.queue_declare(queue="monzo-update-notes", durable=True)
    channel.queue_declare(queue="monzo-refresh-token", durable=True)
    channel.queue_declare(queue="update-ledger", durable=True)

    # pub/sub for transaction events
    channel.exchange_declare(exchange=EXCHANGE_TX_CREATED, exchange_type="fanout")
    channel.exchange_declare(exchange=EXCHANGE_TX_UPDATED, exchange_type="fanout")

    # For santander discord notifications
    channel.queue_declare(queue='transaction.notification', durable=True)
    channel.queue_bind(exchange=EXCHANGE_TX_CREATED, queue='transaction.notification')

    channel.basic_consume(queue="monzo-sync-transactions", on_message_callback=handler.on_monzo_sync_transactions,
                          auto_ack=True)
    channel.basic_consume(queue="monzo-update-notes", on_message_callback=handler.on_monzo_update_notes, auto_ack=True)
    channel.basic_consume(queue="monzo-refresh-token", on_message_callback=handler.on_monzo_refresh_token,
                          auto_ack=True)
    channel.basic_consume(queue="update-ledger", on_message_callback=handler.on_update_ledger, auto_ack=True)
    channel.basic_consume(queue="transaction.notification", on_message_callback=handler.notify_new_transaction,
                          auto_ack=True)
    channel.basic_consume(queue="santander-sync-transactions",
                          on_message_callback=handler.on_santander_sync_transactions,
                          auto_ack=True)

    logging.info("Listening for messages")
    channel.start_consuming()


def main():
    # settings = env variables (mostly secrets)
    # config = non-secret config from yaml file
    settings = load_settings()
    config = Config(**yaml.safe_load(open(settings.config_path)))

    # Actually miss DI a bit here...
    minio_client = minio.Minio(endpoint=settings.minio_endpoint, secure=settings.minio_secure,
                               access_key=settings.minio_access,
                               secret_key=settings.minio_secret)

    gc_client = GoCardlessClient(settings.gc_secret_id, settings.gc_secret_key, config.gocardless.insitutionId,
                                 config.gocardless.redirectUri)

    monzo_store = load_monzo_store(minio_client)
    monzo_client = MonzoClient(monzo_store.access_token, monzo_store.refresh_token,
                               settings.monzo_client_id, settings.monzo_client_secret, settings.monzo_account_id)

    # Connection used to manage reqs, importer for importing santander
    gc_connection = GcConnection(gc_client, Store(minio_client, "transactions"), config)
    discord_client = DiscordClient(settings.santander_discord_webhook)
    notifier = Notifier(discord_client)
    santander_importer = SantanderImporter(config, settings.gc_secret_id, settings.gc_secret_key, minio_client)

    def start_pika():
        pika_connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_connection_string))
        handler = Handler(config, minio_client, discord_client, monzo_client, santander_importer, pika_connection,
                          notifier)
        listen_for_updates(pika_connection, handler)

    def start_gc_sync():
        gc_connection.serve()

    p1 = multiprocessing.Process(target=start_pika)
    p2 = multiprocessing.Process(target=start_gc_sync)
    p1.start()
    p2.start()
    p1.join()
    p2.join()


if __name__ == "__main__":
    main()
