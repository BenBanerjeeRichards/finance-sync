import datetime
from typing import Annotated, Union, Literal

from pydantic import BaseModel, Discriminator



class NewTransactionNotification(BaseModel):
    kind: Literal["NewTransaction"] = "NewTransaction"
    transaction_key: str
    amount: str     # > 0 => money IN, < => money OUT. has to be str due to limitations of json serialization
    counterparty_name: str

    def idempotency_key(self) -> str:
        # Only send a single notification
        return f"Type#NewTransaction#TransactionKey#{self.transaction_key}"


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


class AccountBalanceNotification(BaseModel):
    kind: Literal["AccountBalance"] = "AccountBalance"
    alert_id: str
    account_name: str
    condition: Literal["above", "below"]
    threshold: str
    balance: str

    def idempotency_key(self) -> str:
        # Notify once per day
        now = datetime.datetime.now()
        return f"Type#AccountBalance#RuleId#{self.alert_id}#Date#{now.date().isoformat()}"


NotificationContext = Annotated[Union[NewTransactionNotification, ExpiringConnectionNotification, AccountBalanceNotification], Discriminator("kind")]