import datetime
from typing import Annotated, Union, Literal

from pydantic import BaseModel, Discriminator



class NewTransactionNotification(BaseModel):
    kind: Literal["NewTransaction"] = "NewTransaction"
    transaction_id: str
    amount: str     # > 0 => money IN, < => money OUT. has to be str due to limitations of json serialization
    counterparty_name: str

    def idempotency_key(self) -> str:
        # Only send a single notification
        return f"Type#NewTransaction#TranscationId#{self.transaction_id}"


class ExpiringConnectionNotification(BaseModel):
    kind: Literal["ExpiringConnection"] = "ExpiringConnection"
    name: str
    connection_id: str
    expires_in_days: int | None = None
    reauth_link: str | None = None


    def idempotency_key(self) -> str:
        # Notify each day
        now = datetime.datetime.now()
        return f"Type#ExpiringConnection#ConnectionId#{self.connection_id}#Date#{now.date().isoformat()}"

NotificationContext = Annotated[Union[NewTransactionNotification, ExpiringConnectionNotification], Discriminator("kind")]