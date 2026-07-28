import datetime
from typing import Literal
from uuid import UUID

from fastapi import Query
from pydantic import Field, BaseModel


class GetTransactionsParams(BaseModel):
    created_gt: datetime.datetime | None = None
    created_lt: datetime.datetime | None = None
    account_id: UUID | None = None
    key: str | None = None
    tags: list[str] = Field(Query(default=[]))
    text_filter: str | None = None
    payee: str | None = None
    count: int = 100
    cursor: str | None = None


class GetBalanceParams(BaseModel):
    created_gt: datetime.datetime | None = None
    created_lt: datetime.datetime | None = None
    account_id: UUID | None = None
    key: str | None = None
    tags: list[str] = Field(Query(default=[]))
    text_filter: str | None = None
    payee: str | None = None
    account_types: list[str] = Field(Query(default=[]))


class GetBalanceHistoryParams(BaseModel):
    created_gt: datetime.datetime | None = None
    created_lt: datetime.datetime | None = None
    account_id: UUID | None = None
    key: str | None = None
    tags: list[str] = Field(Query(default=[]))
    text_filter: str | None = None
    payee: str | None = None
    account_types: list[str] = Field(Query(default=[]))
    period: Literal["day", "month", "week"] = "month"


class GetPayeeParams(BaseModel):
    filter: str | None = None


class MonzoImportConfigResponse(BaseModel):
    id: UUID
    client_id: str
    active_at: datetime.datetime | None = None

class GcImportConfigResponse(BaseModel):
    id: UUID
    secret_id: str
    requisition_expires_at: datetime.datetime | None = None
    kind: str


class MonzoImportRuleResponse(BaseModel):
    id: UUID
    import_integration_id: UUID
    name: str
    priority: int
    account_id: UUID
    payee: str | None = None
    narration: str | None = None
    created_at: datetime.datetime | None = None
    category: str | None = None
    account_number: str | None = None
    tags: list[str] = []
    pot_id: str | None = None
    merchant_group_id: str | None = None
    counterparty_name: str | None = None
    transaction_id: UUID | None = None
    metadata: dict[str, str] = {}