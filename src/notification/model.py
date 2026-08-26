import abc
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
