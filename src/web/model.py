import datetime
from decimal import Decimal
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
    cash_account_id: UUID | None = None
    default_income_account_id: UUID | None = None
    default_expense_account_id: UUID | None = None

class GcImportConfigResponse(BaseModel):
    id: UUID
    secret_id: str
    requisition_expires_at: datetime.datetime | None = None
    kind: str
    cash_account_id: UUID | None = None
    default_income_account_id: UUID | None = None
    default_expense_account_id: UUID | None = None


class ImportConfigUpdateRequest(BaseModel):
    cash_account_id: UUID | None = None
    default_income_account_id: UUID | None = None
    default_expense_account_id: UUID | None = None


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


class GcImportRuleResponse(BaseModel):
    id: UUID
    import_integration_id: UUID
    name: str # name of this rule
    priority: int
    payee: str | None
    new_metadata: dict[str, str] = {}
    narration: str | None
    account_id: UUID

    credit_only: bool = False       # only apply when this account on credit side
    debit_only: bool = False # only apply when this account is on debit side (money moving in)
    ignore: bool = False  # don't create a transaction (would duplicate a different importer)
    transaction_type: str | None # GIRA, CARD etc
    account_name_matches: str | None # account name match, if this string is a substring (case insensitive)
    reference_name_matches: str | None # reference match, again case insensitive substring
    amount_equals: Decimal | None # exact transaction amount match

class MonzoImportRuleUpdateRequest(BaseModel):
    rules: list["MonzoImportRuleUpdate"]


class GcImportRuleUpdateRequest(BaseModel):
    rules: list["GcImportRuleUpdate"]


class MonzoImportRuleUpdate(BaseModel):
    id: UUID
    import_integration_id: UUID
    name: str
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


class GcImportRuleUpdate(BaseModel):
    id: UUID
    import_integration_id: UUID
    name: str
    payee: str | None
    new_metadata: dict[str, str] = {}
    narration: str | None
    account_id: UUID
    credit_only: bool = False
    debit_only: bool = False
    ignore: bool = False
    transaction_type: str | None
    account_name_matches: str | None
    reference_name_matches: str | None
    amount_equals: Decimal | None
