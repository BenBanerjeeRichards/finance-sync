import datetime
import uuid
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ledger.model import Notification

import logging

class NotificationRepo:

    def __init__(self):
        pass

    def register_notification(self, session: Session, key: str,
                              type: Literal["NewTransactionNotification", "ExpiringConnection"],
                              context: dict[str, str]) -> uuid.UUID:
        # Silently does nothing if key already exists
        stmt = insert(Notification).values(id=uuid.uuid4(), key=key, context=context, type=type)
        stmt = stmt.on_conflict_do_nothing(index_elements=["key"])
        session.execute(stmt)
        q = select(Notification).where(Notification.key == key)
        item = session.execute(q).scalar_one_or_none()
        assert item
        logging.info("Registered notification key %s", key)
        return item.id

    def claim_next_notification(self, session: Session) -> Notification | None:
        q = select(Notification).where(Notification.sent_at == None).limit(1).with_for_update(skip_locked=True)
        claimed_notification = session.scalars(q).first()
        if claimed_notification:
            claimed_notification.sent_at = datetime.datetime.now()
            return claimed_notification
        return None
