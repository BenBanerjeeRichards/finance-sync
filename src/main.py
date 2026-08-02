import asyncio

import uvicorn
from pika.adapters.blocking_connection import BlockingConnection
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from constants import EXCHANGE_TX_CREATED, EXCHANGE_TX_UPDATED, EXCHANGE_LEDGER_UPDATED
from model import Settings
import os
import logging
import dependencies

import multiprocessing


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
# logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

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


def listen_for_updates(pika_connection: BlockingConnection, handler: "Handler"):
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
    from web.web import create_fastapi

    def start_pika():
        from handler import Handler
        message_handler = Handler()
        listen_for_updates(dependencies.get_rabbitmq_connection(), message_handler)

    def start_gc_sync():
        async def start_async():
            config = uvicorn.Config(
                create_fastapi(),
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
