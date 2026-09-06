import abc
import datetime
import uuid
from abc import ABC
from decimal import Decimal

from pydantic import BaseModel


class BaseNotificationContext(BaseModel, ABC):
    kind: str
    @abc.abstractmethod
    def idempotency_key(self) -> str:
        pass


class NewTransactionNotification(BaseNotificationContext):
    kind: str = "NewTransaction"
    transaction_id: str
    amount: str     # > 0 => money IN, < => money OUT. has to be str due to limitations of json serialization
    counterparty_name: str

    def idempotency_key(self) -> str:
        # Only send a single notification
        return f"Type#NewTransaction#TranscationId#{self.transaction_id}"


class ExpiringConnectionNotification(BaseNotificationContext):
    kind: str = "ExpiringConnection"
    name: str
    connection_id: str
    expires_in_days: int | None = None
    reauth_link: str | None = None


    def idempotency_key(self) -> str:
        # Notify each day
        now = datetime.datetime.now()
        return f"Type#ExpiringConnection#ConnectionId#{self.connection_id}#Date#{now.date().isoformat()}"
