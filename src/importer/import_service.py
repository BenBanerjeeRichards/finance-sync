import datetime
import uuid

from pydantic import BaseModel
from sqlalchemy import update, select
from sqlalchemy.dialects.postgresql import insert

from ledger.model import MonzoImportIntegration, GoCardlessImportIntegration, MonzoImportRule
from main import Session


class MonzoImportIntegrationDto(BaseModel):
    id: uuid.UUID
    access_token: str | None
    refresh_token: str | None
    client_id: str
    client_secret: str
    active_at: datetime.datetime | None = None


class GcImportIntegrationDto(BaseModel):
    id: uuid.UUID
    secret_id: str | None
    secret_key: str | None
    requisition_expires_at: datetime.datetime | None = None
    # GC is now closed to new bank accounts so we don't need to support any more than santander
    kind: str = "santander"


class MonzoImportRuleDto(BaseModel):
    id: uuid.UUID
    import_integration_id: uuid.UUID
    name: str
    priority: int
    account_id: uuid.UUID
    payee: str | None = None
    narration: str | None = None
    created_at: datetime.datetime | None = None
    category: str | None = None
    account_number: str | None = None
    tags: list[str] = []
    pot_id: str | None = None
    merchant_group_id: str | None = None
    counterparty_name: str | None = None
    transaction_id: uuid.UUID | None = None
    metadata: dict[str, str] = {}


# don't bother with repo for now as this is so simple
class ImportService:

    @staticmethod
    def create_monzo_import_if_not_exists(client_id: str, client_secret: str, monzo_account_id: str):
        with Session.begin() as session:
            stmt = insert(MonzoImportIntegration).values(id=uuid.uuid4(), client_id=client_id,
                                                         client_secret=client_secret,
                                                         account_id=monzo_account_id)
            stmt = stmt.on_conflict_do_nothing(index_elements=["client_id"])
            session.execute(stmt)

    @staticmethod
    def create_gocardless_import_if_not_exists(secret_id: str, secret_key: str):
        with Session.begin() as session:
            stmt = insert(GoCardlessImportIntegration).values(id=uuid.uuid4(), secret_id=secret_id,
                                                              secret_key=secret_key)
            stmt = stmt.on_conflict_do_nothing(index_elements=["secret_id"])
            session.execute(stmt)

    @staticmethod
    def update_monzo_tokens(client_id: str, access_token: str, refresh_token: str):
        with Session.begin() as session:
            stmt = update(MonzoImportIntegration).where(MonzoImportIntegration.client_id == client_id).values(
                access_token=access_token, refresh_token=refresh_token,
                active_at=datetime.datetime.now(tz=datetime.timezone.utc))
            session.execute(stmt)

    @staticmethod
    def get_monzo_config(client_id: str) -> MonzoImportIntegrationDto:
        with Session.begin() as session:
            q = select(MonzoImportIntegration).where(
                MonzoImportIntegration.client_id == client_id
            )
            res = session.execute(q).scalar_one_or_none()

            if not res:
                raise ValueError(f"No monzo config found for client {client_id}")

            return MonzoImportIntegrationDto(
                id=res.id,
                client_id=res.client_id,
                client_secret=res.client_secret,
                access_token=res.access_token,
                refresh_token=res.refresh_token,
                active_at=res.active_at.isoformat() if res.active_at else None,
            )

    @staticmethod
    def get_monzo_configs() -> list[MonzoImportIntegrationDto]:
        with Session.begin() as session:
            q = select(MonzoImportIntegration)
            results = session.execute(q).scalars()

            return [MonzoImportIntegrationDto(
                id=res.id,
                client_id=res.client_id,
                client_secret=res.client_secret,
                access_token=res.access_token,
                refresh_token=res.refresh_token,
                active_at=res.active_at.isoformat() if res.active_at else None,
            ) for res in results]


    @staticmethod
    def get_gc_configs() -> list[GcImportIntegrationDto]:
        with Session.begin() as session:
            q = select(GoCardlessImportIntegration)
            results = session.execute(q).scalars()

            return [GcImportIntegrationDto(
                id=res.id,
                secret_id=res.secret_id,
                secret_key=res.secret_key,
                requisition_expires_at=res.requisition_expires_at,
            ) for res in results]


    @staticmethod
    def get_santander_config(secret_id: str) -> GcImportIntegrationDto:
        with Session.begin() as session:
            q = select(GoCardlessImportIntegration).where(
                GoCardlessImportIntegration.secret_id == secret_id
            )
            res = session.execute(q).scalar_one_or_none()

            if not res:
                raise ValueError(f"No santander config found for client {secret_id}")

            return GcImportIntegrationDto(
                id=res.id,
                secret_id=res.secret_id,
                secret_key=res.secret_key,
                requisition_expires_at=res.requisition_expires_at,
            )

    @staticmethod
    def update_santander_req_date(secret_id: str, dt: datetime.datetime):
        with Session.begin() as session:
            stmt = update(GoCardlessImportIntegration).where(GoCardlessImportIntegration.secret_id == secret_id).values(
                requisition_expires_at=dt)
            session.execute(stmt)

    @staticmethod
    def get_monzo_import_rules(import_integration_id: uuid.UUID) -> list[MonzoImportRuleDto]:
        with Session.begin() as session:
            q = select(MonzoImportRule).where(
                MonzoImportRule.import_integration_id == import_integration_id
            ).order_by(MonzoImportRule.priority)
            results = session.execute(q).scalars()

            return [MonzoImportRuleDto(
                id=r.id,
                import_integration_id=r.import_integration_id,
                name=r.name,
                priority=r.priority,
                account_id=r.account_id,
                payee=r.payee,
                narration=r.narration,
                created_at=r.created_at,
                category=r.category,
                account_number=r.account_number,
                tags=r.tags,
                pot_id=r.pot_id,
                merchant_group_id=r.merchant_group_id,
                counterparty_name=r.counterparty_name,
                transaction_id=r.transaction_id,
                metadata=r.new_metadata,
            ) for r in results]
