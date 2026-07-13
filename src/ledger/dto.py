from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AccountType(str, Enum):
    ASSET = "asset"
    EXPENSE = "expense"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"


class LedgerDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str


class AccountDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    type: AccountType
    tags: list[str] = Field(default_factory=list)


class EntryDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    account_id: UUID
    transaction_id: UUID
    amount: Decimal
    local_amount: Decimal
    local_currency: str


class TransactionDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ledger_id: UUID
    transaction_datetime: datetime
    key: str
    payee: str | None = None
    narration: str | None = None
    external_metadata: dict = Field(default_factory=dict)
    tx_metadata: dict = Field(default_factory=dict, serialization_alias="metadata")
    flagged: bool = False
    tags: list[str] = Field(default_factory=list)
