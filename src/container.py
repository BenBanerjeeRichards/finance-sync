import minio
import pika

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


class Container:

    def __init__(self, config: Config, settings: Settings):
        self.config = config
        self.settings = settings
        self.minio_client = minio.Minio(endpoint=settings.minio_endpoint, secure=settings.minio_secure,
                                        access_key=settings.minio_access,
                                        secret_key=settings.minio_secret)
        self.gc_client = GoCardlessClient(settings.gc_secret_id, settings.gc_secret_key, config.gocardless.insitutionId,
                                          config.gocardless.redirectUri)

        def get_monzo_tokens() -> tuple[str, str]:
            cfg = ImportService.get_monzo_config(settings.monzo_client_id)
            return cfg.access_token, cfg.refresh_token

        self.monzo_client = MonzoClient(settings.monzo_client_id, settings.monzo_client_secret,
                                        settings.monzo_account_id,
                                        get_monzo_tokens)
        self.gc_connection = GcConnection(self.gc_client, Store(self.minio_client, "transactions"), config)
        self.discord_client = DiscordClient(settings.santander_discord_webhook)
        self.notifier = Notifier(self.discord_client)
        self.santander_importer = SantanderImporter(config, settings.gc_secret_id, settings.gc_secret_key, self.minio_client)
        self.pika_connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_connection_string))
        self.ledger_service = LedgerService(config)
