import os
from functools import lru_cache
import minio
import pika
import yaml

from gocardless.gc_connection import GcConnection
from gocardless.gocardless import GoCardlessClient
from importer.import_service import ImportService
from importer.santander_import import SantanderImporter
from ledger.ledger_service import LedgerService
from model import Config, Settings
from monzo import MonzoClient
from notification.discord import DiscordClient
from notification.notifier import Notifier
from storage import Store


def get_settings() -> Settings:
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

@lru_cache
def get_config() -> Config:
    settings = get_settings()
    return Config(**yaml.safe_load(open(settings.config_path)))


@lru_cache
def get_minio_client() -> minio.Minio:
    settings = get_settings()
    return minio.Minio(
        endpoint=settings.minio_endpoint,
        secure=settings.minio_secure,
        access_key=settings.minio_access,
        secret_key=settings.minio_secret,
    )


@lru_cache
def get_gc_client() -> GoCardlessClient:
    config = get_config()
    settings = get_settings()
    return GoCardlessClient(
        settings.gc_secret_id,
        settings.gc_secret_key,
        config.gocardless.insitutionId,
        config.gocardless.redirectUri,
    )


def _get_monzo_tokens() -> tuple[str, str]:
    settings = get_settings()
    cfg = ImportService.get_monzo_config(settings.monzo_client_id)
    return cfg.access_token, cfg.refresh_token


@lru_cache
def get_monzo_client() -> MonzoClient:
    settings = get_settings()
    return MonzoClient(
        settings.monzo_client_id,
        settings.monzo_client_secret,
        settings.monzo_account_id,
        _get_monzo_tokens,
    )


@lru_cache
def get_discord_client() -> DiscordClient:
    settings = get_settings()
    return DiscordClient(settings.santander_discord_webhook)


def get_rabbitmq_connection() -> pika.BlockingConnection:
    settings = get_settings()
    return pika.BlockingConnection(
        pika.URLParameters(settings.rabbitmq_connection_string)
    )


def get_transactions_store() -> Store:
    return Store(get_minio_client(), "transactions")


def get_gc_connection() -> GcConnection:
    return GcConnection(
        client=get_gc_client(),
        store=get_transactions_store(),
        config=get_config(),
    )


def get_notifier() -> Notifier:
    return Notifier(get_discord_client())


def get_santander_importer() -> SantanderImporter:
    settings = get_settings()
    return SantanderImporter(
        get_config(),
        settings.gc_secret_id,
        settings.gc_secret_key,
        get_minio_client(),
    )


def get_ledger_service() -> LedgerService:
    return LedgerService(get_config())


def get_import_service() -> ImportService:
    return ImportService()