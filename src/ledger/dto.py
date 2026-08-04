import uuid
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
    name: str | None = None
    type: AccountType | None = None
    tags: list[str] | None = Field(default_factory=list)


class EntryDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    transaction_id: UUID
    amount: Decimal
    local_amount: Decimal
    local_currency: str
    account: AccountDto


class TransactionDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    transaction_datetime: datetime
    key: str
    payee: str | None = None
    narration: str | None = None
    external_metadata: dict = Field(default_factory=dict)
    tx_metadata: dict = Field(default_factory=dict, serialization_alias="metadata")
    flagged: bool = False
    tags: list[str] = Field(default_factory=list)
    entries: list[EntryDto] = Field(default_factory=list)

class CreateTransactionDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    transaction_datetime: datetime
    payee: str | None = None
    narration: str | None = None
    external_metadata: dict = Field(default_factory=dict)
    tx_metadata: dict = Field(default_factory=dict, serialization_alias="metadata")
    flagged: bool = False
    tags: list[str] = Field(default_factory=list)
    entries: list[EntryDto] = Field(default_factory=list)


class TransactionListAccountDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str


class TransactionListEntryDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    amount: Decimal
    local_amount: Decimal
    local_currency: str
    account: TransactionListAccountDto


class TransactionListDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    transaction_datetime: datetime
    key: str
    payee: str | None = None
    narration: str | None = None
    flagged: bool = False
    tags: list[str] = Field(default_factory=list)
    entries: list[TransactionListEntryDto] = Field(default_factory=list)


class TransactionListResultDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    transactions: list[TransactionListDto] = Field(default_factory=list)
    next_cursor: str | None = None


class BalanceEntryDto(BaseModel):
    account_id: uuid.UUID
    amount: Decimal


class BalancesDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    balances: list[BalanceEntryDto]


class PeriodicBalanceEntryDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    account_id: uuid.UUID
    period: datetime
    amount: Decimal

class PeriodicBalancesDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    balances: list[PeriodicBalanceEntryDto]