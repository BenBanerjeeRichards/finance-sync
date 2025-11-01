import yaml
import json

from importer.santander_import import SantanderImporter
from model import Settings, TransactionUpdate, SantanderTransaction
import os
import minio
import pika
import logging
from tasks import TaskProcessor
from pydantic import BaseModel
import datetime
from gocardless.gc_connection import GcConnection
from gocardless.gocardless import GoCardlessClient
import multiprocessing
from storage import Store
from discord import DiscordClient
from typing import Optional
from event import TransactionEvent
from model import Config

from functools import wraps

EXCHANGE_TX_UPDATED = "transaction.updated"
EXCHANGE_TX_CREATED = "transaction.created"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

def load_settings() -> Settings:
    return Settings(
        monzo_account_id = os.environ["MONZO_ACCOUNT_ID"],
        monzo_client_id = os.environ["MONZO_CLIENT_ID"],
        monzo_client_secret = os.environ["MONZO_CLIENT_SECRET"],
        rabbitmq_connection_string = os.environ["RABBITMQ_CONNECTION_STRING"],
        minio_endpoint = os.environ["MINIO_ENDPOINT"],
        minio_access = os.environ["MINIO_ACCESS"],
        minio_secret = os.environ["MINIO_SECRET"],
        minio_secure = os.environ["MINIO_SECURE"] != "false",
        config_path = os.environ["CONFIG_PATH"],
        gc_secret_id = os.environ["GC_SECRET_ID"],
        gc_secret_key = os.environ["GC_SECRET_KEY"],
        santander_discord_webhook=os.environ["SANTANDER_DISCORD_WEBHOOK"]
    )


def save_result(config: dict, minio_client: minio.Minio | None, bean_file: str) -> None:
    name = config["beanFileName"]
    open(name, "w+").write(bean_file)

    if minio_client:
        minio_client.fput_object(config["bucket"], name, name)

class MonzoSyncMessage(BaseModel):
    past_days: int

class MonzoUpdateNotesMessage(BaseModel):
    transactionId: str
    note: str

class NotifyExpiringMessage(BaseModel):
    name: str
    url: Optional[str] = None
    days: int | None = None

def rmq_handler(body_type: type[BaseModel] | None = None):
    def do_decorate(message_handler):
        @wraps(message_handler)
        def handle(ch, method, properties, body):
            try:
                logging.info("Got message for handler %s",  message_handler.__name__)
                if body_type is None:
                    return message_handler()
                parsed_json = json.loads(body.decode())
                if isinstance(parsed_json, list):
                    return message_handler([body_type(**item) for item in parsed_json])
                else:
                    return message_handler(body_type(**parsed_json))
            except:
                logging.exception("Failed to handle %s message", message_handler.__name__)
        return handle
    return do_decorate

def listen_for_updates(config: Config, pika_connection: pika.BlockingConnection, minio_client: minio.Minio, discord_client: DiscordClient, gc_sync: SantanderImporter):
    # Wire everything up...not massivly sustainable but ok for something small
    channel = pika_connection.channel()

    #Command queues
    channel.queue_declare(queue="monzo-sync-transactions", durable=True)
    channel.queue_declare(queue="santander-sync-transactions", durable=True)
    channel.queue_declare(queue="monzo-update-notes", durable=True)
    channel.queue_declare(queue="monzo-refresh-token", durable=True)
    channel.queue_declare(queue="monzo-update-ledger", durable=True)
    channel.queue_declare(queue="send-santander-notification", durable=True)
    channel.queue_declare(queue="notify-connection-expiring", durable=True)
    
    # pub/sub for transaction events
    channel.exchange_declare(exchange=EXCHANGE_TX_CREATED, exchange_type="fanout")
    channel.exchange_declare(exchange=EXCHANGE_TX_UPDATED, exchange_type="fanout")

    # For santander discord notifications
    channel.queue_declare(queue='transaction.notification', durable=True)
    channel.queue_bind(exchange=EXCHANGE_TX_CREATED, queue='transaction.notification')

    @rmq_handler(MonzoSyncMessage)
    def on_monzo_sync_transactions(sync_message: MonzoSyncMessage):
        sync_since = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=sync_message.past_days)
        task_processor.sync_monzo_transactions(sync_since)

    @rmq_handler(TransactionUpdate)
    def on_monzo_update_notes(updates: list[TransactionUpdate]):
        task_processor.update_notes(updates)
        task_processor.update_monzo_ledger()

    @rmq_handler
    def on_monzo_refresh_token():
        task_processor.refresh_token()

    @rmq_handler
    def on_monzo_update_ledger():
        task_processor.update_monzo_ledger()

    @rmq_handler
    def on_santander_sync_transactions():
        gc_sync.import_transactions()
        expires_in = gc_sync.days_requisitions_expiring_in()
        if expires_in >= config.gocardless.notifyOlderThan:
            message = NotifyExpiringMessage(name="GoCardless", url=config.gocardless.startUri, days=expires_in)
            channel.basic_publish(exchange="", routing_key="notify-connection-expiring", body=message.model_dump_json())

    @rmq_handler(SantanderTransaction)
    def send_santander_notification(tx: SantanderTransaction):
        task_processor.send_santander_discord_notification(tx)

    @rmq_handler(NotifyExpiringMessage)
    def notify_connection_expiring(expiring: NotifyExpiringMessage):
        task_processor.notify_expiring(expiring.name, expiring.url, expiring.days)

    @rmq_handler(TransactionEvent)
    def test_notify(tx: TransactionEvent):
        logging.info("Got transaction %s", tx)

    channel.basic_consume(queue="monzo-sync-transactions", on_message_callback=on_monzo_sync_transactions, auto_ack=True)
    channel.basic_consume(queue="monzo-update-notes", on_message_callback=on_monzo_update_notes, auto_ack=True)
    channel.basic_consume(queue="monzo-refresh-token", on_message_callback=on_monzo_refresh_token, auto_ack=True)
    channel.basic_consume(queue="monzo-update-ledger", on_message_callback=on_monzo_update_ledger, auto_ack=True)
    channel.basic_consume(queue="send-santander-notification", on_message_callback=send_santander_notification, auto_ack=True)
    channel.basic_consume(queue="notify-connection-expiring", on_message_callback=notify_connection_expiring, auto_ack=True)
    channel.basic_consume(queue="transaction.notification", on_message_callback=test_notify, auto_ack=True)
    channel.basic_consume(queue="santander-sync-transactions", on_message_callback=on_santander_sync_transactions, auto_ack=True)

    task_processor = TaskProcessor(load_settings(), config, minio_client, discord_client, pika_connection.channel())

    logging.info("Listening for messages")
    channel.start_consuming()


def main():
    # settings = env variables (mostly secrets)
    # config = non-secret config from yaml file
    settings = load_settings()
    config = Config(**yaml.safe_load(open(settings.config_path)))

    # Actually miss DI a bit here...
    minio_client =  minio.Minio(endpoint=settings.minio_endpoint, secure=settings.minio_secure, access_key=settings.minio_access,
                                secret_key=settings.minio_secret)

    gc_client = GoCardlessClient(settings.gc_secret_id, settings.gc_secret_key, config.gocardless.insitutionId, config.gocardless.redirectUri)
    store = Store(minio_client, "transactions")

    # Connection used to manage reqs, importer for importing santander
    gc_connection = GcConnection(gc_client, store, config)
    gc_importer = SantanderImporter(config, settings.gc_secret_id, settings.gc_secret_key, minio_client)

    discord_client = DiscordClient(settings.santander_discord_webhook)

    def start_pika():
        pika_connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_connection_string))
        listen_for_updates(config, pika_connection, minio_client, discord_client, gc_importer)

    def start_gc_sync():
        # TODO what is this?
        def on_req_expiring_soon():
            # quick fix. can't keep connection in above scope as serve() will block heartbeats
            pika_connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_connection_string))
            ch = pika_connection.channel()
            message = NotifyExpiringMessage(name="GoCardless", url=config["gocardless"]["startUri"])
            ch.basic_publish(exchange="", routing_key="notify-connection-expiring", body=message.model_dump_json())

        gc_connection.serve()

    p1 = multiprocessing.Process(target=start_pika)
    p2 = multiprocessing.Process(target=start_gc_sync)
    p1.start()
    p2.start()
    p1.join()
    p2.join()


if __name__ == "__main__":
    main()
