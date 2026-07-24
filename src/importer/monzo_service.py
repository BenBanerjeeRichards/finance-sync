import datetime
import uuid

from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from ledger.model import MonzoImportIntegration
from main import Session


class MonzoImporterDto(BaseModel):
    access_token: str | None
    refresh_token: str | None
    client_id: str
    client_secret: str
    active_at: str | None = None


# don't bother with repo for now as this is so simple
class MonzoService:

    @staticmethod
    def create_monzo_import_if_not_exists(client_id: str, client_secret: str, monzo_account_id: str):
        with Session.begin() as session:
            stmt = insert(MonzoImportIntegration).values(id=uuid.uuid4(), client_id=client_id, client_secret=client_secret,
                                                         account_id=monzo_account_id)
            stmt = stmt.on_conflict_do_nothing(index_elements=["client_id"])
            session.execute(stmt)

    @staticmethod
    def update_tokens(client_id: str, access_token: str, refresh_token: str):
        with Session.begin() as session:
            stmt = update(MonzoImportIntegration).where(MonzoImportIntegration.client_id == client_id).values(
                access_token=access_token, refresh_token=refresh_token, active_at=datetime.datetime.now(tz=datetime.timezone.utc))
            session.execute(stmt)