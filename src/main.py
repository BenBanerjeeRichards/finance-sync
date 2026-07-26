import asyncio

import uvicorn
import yaml
from pika.adapters.blocking_connection import BlockingConnection
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker

from beancount_sync.beancount import Beancount
from notification.notifier import Notifier
from constants import EXCHANGE_TX_CREATED, EXCHANGE_TX_UPDATED, EXCHANGE_LEDGER_UPDATED
from importer.santander_import import SantanderImporter
from model import Settings
import os
import minio
import pika
import logging

from monzo import MonzoClient
from handler import Handler
from gocardless.gc_connection import GcConnection
from gocardless.gocardless import GoCardlessClient
import multiprocessing

from storage import Store, load_monzo_store
from notification.discord import DiscordClient
from model import Config

# Don't include timestamp, we will just use loki ingestion timestamp
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# sqlA module level constants

engine = create_engine(
    os.environ["PSQL_CONNECTION_STRING"],
    executemany_mode="values_plus_batch",
    executemany_batch_page_size=1000,
)
Session = sessionmaker(engine)


def load_settings() -> Settings:
    rmq_connection_string = os.environ["RABBITMQ_CONNECTION_STRING"]
    # HACK: default rmq operator secret uses 3 dots, (rabbitmq.default.svc), but this fails to resolve with low values of
    # ndots (required for resolution of over domains...). So repace with fully higher number of dots rabbitmq.default.svc.cluster.local
    if ".svc" in rmq_connection_string and ".cluster.local" not in rmq_connection_string:
        rmq_connection_string = rmq_connection_string.replace(".svc", ".svc.cluster.local")

    return Settings(
        monzo_account_id=os.environ["MONZO_ACCOUNT_ID"],
        monzo_client_id=os.environ["MONZO_CLIENT_ID"],
        monzo_client_secret=os.environ["MONZO_CLIENT_SECRET"],
        rabbitmq_connection_string=rmq_connection_string,
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

    # bit lazy... just subscribe from energy sync straight to the update-ledger to force energy to be updated
    channel.queue_bind("update-ledger", "energy.synced", routing_key="")

    # pub/sub for transaction events
    channel.exchange_declare(exchange=EXCHANGE_TX_CREATED, exchange_type="fanout")
    channel.exchange_declare(exchange=EXCHANGE_TX_UPDATED, exchange_type="fanout")
    channel.exchange_declare(exchange=EXCHANGE_LEDGER_UPDATED, exchange_type="fanout")

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
    from importer.import_service import ImportService

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

    def get_monzo_tokens() -> tuple[str, str]:
        cfg = ImportService.get_monzo_config(settings.monzo_client_id)
        return cfg.access_token, cfg.refresh_token

    monzo_client = MonzoClient(settings.monzo_client_id, settings.monzo_client_secret, settings.monzo_account_id,
                               get_monzo_tokens)

    # Connection used to manage reqs, importer for importing santander
    gc_connection = GcConnection(gc_client, Store(minio_client, "transactions"), config)
    discord_client = DiscordClient(settings.santander_discord_webhook)
    notifier = Notifier(discord_client)
    santander_importer = SantanderImporter(config, settings.gc_secret_id, settings.gc_secret_key, minio_client)
    pika_connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_connection_string))
    beancount = Beancount(minio_client, pika_connection, config)

    from ledger.ledger_service import LedgerService
    from web.web import create_fastapi

    ledger_service = LedgerService(config)

    def start_pika():
        message_handler = Handler(config, settings, minio_client, discord_client, monzo_client, santander_importer,
                                  pika_connection,
                                  notifier, beancount)
        # backfill_monzo(config, pika_connection.channel(), minio_client, "actual-sync.transactions", in_only=True)
        # backfill_from_beancount(config, pika_connection.channel(), minio_client, "actual-sync.transactions", "ledger", "FY24.bean")
        # backfill_santander_gc(config, pika_connection.channel(), minio_client, "actual-sync.transactions")
        listen_for_updates(pika_connection, message_handler)

    def start_gc_sync():
        async def start_async():
            config = uvicorn.Config(
                create_fastapi(monzo_client, minio_client, settings.rabbitmq_connection_string, gc_connection,
                               ledger_service),
                host="0.0.0.0", port=8080, log_level="info")
            server = uvicorn.Server(config)
            await server.serve()

        logging.info("Starting finance sync server on 0.0.0.0:8080")
        asyncio.run(start_async())

    p1 = multiprocessing.Process(target=start_pika)
    p2 = multiprocessing.Process(target=start_gc_sync)
    p1.start()
    p2.start()
    p1.join()
    p2.join()


if __name__ == "__main__":
    main()
