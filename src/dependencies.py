from functools import lru_cache
import minio
import pika
import yaml

from db import DBSession
from gocardless.gc_connection import GcConnection
from gocardless.gocardless import GoCardlessClient
from importer.import_service import ImportService
from importer.santander_import import SantanderImporter
from ledger.ledger_service import LedgerService
from model import Config, Settings
from importer.monzo import MonzoClient
from notification.discord import DiscordClient
from notification.notification_repo import NotificationRepo
from notification.notification_service import NotificationService
from storage import Store


def get_settings() -> Settings:
    return Settings()


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
    with DBSession.begin() as session:
        cfg = ImportService.get_monzo_config(session, settings.monzo_client_id)
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


@lru_cache
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


def get_notification_repo() -> NotificationRepo:
    return NotificationRepo()


def get_notification_service() -> NotificationService:
    return NotificationService(get_discord_client(), get_ledger_service())
