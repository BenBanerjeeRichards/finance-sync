from pydantic import BaseModel
from typing import Optional
import datetime
from decimal import Decimal


class Merchant(BaseModel):
    name: Optional[str] = None
    logo_url: Optional[str] = None
    lat: Optional[float] = None
    long: Optional[float] = None
    approximate: bool = False       # Is the location approx (e.g generic country/city location or specifc lat/lon)
    country: Optional[str] = None
    id: Optional[str]
    group_id: Optional[str] = None
    online: Optional[bool] = None


# Transfers to other people
class Counterparty(BaseModel):
    name: Optional[str] = None
    id: str
    is_monzo: bool
    account_number: Optional[str] = None       # Helpful for reconcilling with the account transfer came from/to

class Tab(BaseModel):
    id: str
    name: str
    participant_names: list[str]

class Attachment(BaseModel):
    file_type: str
    url: str

class Transaction(BaseModel):
    id: str
    created: str
    settled: Optional[str] = None
    merchant: Optional[Merchant] = None
    amount: int
    category: str
    tags: list[str] = []
    notes: str
    local_currency: str
    local_amount: int
    include_in_spending: bool
    counterparty: Optional[Counterparty] = None
    tab: Optional[Tab] = None
    attachments: Optional[list[Attachment]] = None
    is_split: bool = False    # Has this been split - i.e. payment requested to others
    original_transaction_id: Optional[str] = None   # If this is an incoming transaction to pay a split, this is the original tx that has been split
    pot_id: Optional[str] = None        # If we are transferring from/to a pot, this is the id


class TransactionUpdate(BaseModel):
    transactionId: str
    note: str

class MonzoStore(BaseModel):
    access_token: str
    refresh_token: str

class GcStore(BaseModel):
    requisition_id: str
    account_id: str


class Settings(BaseModel):
    monzo_client_id: str
    monzo_client_secret: str
    monzo_account_id: str
    rabbitmq_connection_string: str
    minio_endpoint: str
    minio_access: str
    minio_secret: str
    minio_secure: bool
    config_path: str
    gc_secret_id: str
    gc_secret_key: str
    santander_discord_webhook: str


class SantanderTransaction(BaseModel):
    transaction_id: str
    description: str
    counterparty_name: Optional[str]  = None
    amount: Decimal
    transaction_code: str | None = None
    date: datetime.date | None = None       # Will be None if not booked
    booked: bool


class SantanderTransactions(BaseModel):
    transactions: list[SantanderTransaction]


class MonzoAccountRule(BaseModel):
    name: str
    account: str
    tags: list[str] | None = None
    potId: str | None = None
    accountNumber: str | None = None
    narrative: str | None = None
    groupId: str | None = None
    merhantGroupId: str | None = None

    model_config = {"extra": "forbid"}


class SantanderAccountRule(BaseModel):
    name: str
    accountName: str
    accountMatches: list[str] | None = None
    referenceMatches: list[str] | None = None
    creditOnly: bool | None = None
    debitOnly: bool | None = None
    type: str | None = None
    ignore: bool | None = None
    amount: float | None = None

    model_config = {"extra": "forbid"}


class GoCardlessConfig(BaseModel):
    insitutionId: str
    redirectUri: str
    startUri: str
    notifyOlderThan: int

    model_config = {"extra": "forbid"}


class Config(BaseModel):
    monzoCategoryMappings: dict[str, str]
    accountRules: list[MonzoAccountRule]
    monzoCashAccount: str
    santanderCashAccount: str
    startDate: str   # keep as raw string
    defaultIncomeAccount: str
    defaultExpenseAccount: str
    santanderAccountRules: list[SantanderAccountRule]
    bucket: str
    monzoTransactionName: str
    beanFileName: str
    santanderBeanFileName: str
    gocardless: GoCardlessConfig

    model_config = {"extra": "forbid"}
