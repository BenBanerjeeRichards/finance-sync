import datetime
import uuid
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, field_validator

from ledger.dto import TransactionDto, EntryDto, AccountDto


class Merchant(BaseModel):
    name: Optional[str] = None
    logo_url: Optional[str] = None
    lat: Optional[float] = None
    long: Optional[float] = None
    approximate: bool = False  # Is the location approx (e.g generic country/city location or specifc lat/lon)
    country: Optional[str] = None
    id: Optional[str]
    group_id: Optional[str] = None
    online: Optional[bool] = None


# Transfers to other people
class Counterparty(BaseModel):
    name: Optional[str] = None
    id: str
    is_monzo: bool
    account_number: Optional[str] = (
        None  # Helpful for reconcilling with the account transfer came from/to
    )


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
    is_split: bool = False  # Has this been split - i.e. payment requested to others
    original_transaction_id: Optional[str] = (
        None  # If this is an incoming transaction to pay a split, this is the original tx that has been split
    )
    pot_id: Optional[str] = None  # If we are transferring from/to a pot, this is the id


class TransactionUpdate(BaseModel):
    transactionId: str
    note: str



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


class GcSantanderTransaction(BaseModel):
    transaction_id: str
    description: str
    counterparty_name: Optional[str] = None
    amount: Decimal
    transaction_code: str | None = None
    date: datetime.date | None = None  # Will be None if not booked
    booked: bool


class SantanderTransactions(BaseModel):
    transactions: list[GcSantanderTransaction]


class GoCardlessConfig(BaseModel):
    insitutionId: str
    redirectUri: str
    startUri: str
    notifyOlderThan: int

    model_config = {"extra": "forbid"}


# Support things that are billed periodically - e.g. factor billed every Q
# Allow us to accrue a liability every month and update these once settled each Q
# Only supports monthly basis for now (i.e. build liability each month)
class AccrualConfig(BaseModel):
    name: str
    metadata_key: str   # metadata key that identifies transactions for this
    settlement_months: int  # how many months does a settlement cover. e.g. quarterly bill = 3
    liability_account: uuid.UUID
    expense_account: uuid.UUID

    model_config = {"extra": "forbid"}

class EnergyConfig(BaseModel):
    electricityPrepayAccount: uuid.UUID
    gasPrepayAccount: uuid.UUID
    electricityExpenseAccount: uuid.UUID
    gasExpenseAccount: uuid.UUID
    startMonth: str

    @field_validator("startMonth")
    @classmethod
    def validate_start_month(cls, v: str) -> str:
        try:
            datetime.datetime.strptime(v, "%Y-%m")
        except:
            raise ValueError("Start month must be in YYYY-MM")
        return v

    model_config = {"extra": "forbid"}

class Config(BaseModel):
    gocardless: GoCardlessConfig
    monzoCustomCategories: dict[str, str] = {}
    energySyncBaseUrl: str

    model_config = {"extra": "forbid"}



class MonzoSyncMessage(BaseModel):
    past_days: int


class MonzoUpdateNotesMessage(BaseModel):
    transactionId: str
    note: str


class NotifyExpiringMessage(BaseModel):
    name: str
    url: Optional[str] = None
    days: int | None = None

# Simple transaction with just two legs
# This is 99.9% of our transactions and currently 100% of automatically generated ones
class SimpleLedgerTransaction(BaseModel):
    external_id: str  # the banks record of this transaction
    tx_date: datetime.date
    tx_datetime: datetime.datetime | None = None
    amount: Decimal
    credit_account_id: uuid.UUID  # where money comes from
    debit_account_id: uuid.UUID  # where money goes to
    payee: str  # summary of who is being paid
    description: str  # aka narration - more detail about transaction
    tags: list[str] = []  # tags added to further categorise e.g. #travel
    flagged: bool = False  # needs attention
    # the full data from the source of this transaction: e.g. monzo api data
    # can be used for more granular information
    metadata: dict = {}
    # Metadata to add directly into the ledger
    ledger_metadata: dict = {}
    source: str = ""
    local_amount: Decimal | None = None
    local_currency: str | None = None

    def to_dto(self) -> TransactionDto:
        if not self.tx_datetime:
            dt = datetime.datetime.combine(self.tx_date, datetime.time.min, tzinfo=ZoneInfo("UTC"))
        else:
            dt = self.tx_datetime

        transaction = TransactionDto(id=uuid.uuid4(),
                                     transaction_datetime=dt,
                                     key=self.external_id, payee=self.payee, narration=self.description,
                                     external_metadata=self.metadata, tx_metadata=self.ledger_metadata,
                                     flagged=self.flagged, tags=self.tags, entries=[])

        local_amount = self.local_amount
        local_currency = self.local_currency
        if not local_amount:
            local_amount = self.amount
            local_currency = "GBP"

        credit_entry = EntryDto(id=uuid.uuid4(), account=AccountDto(id=self.credit_account_id),
                                amount=abs(self.amount) * -1, local_amount=abs(local_amount) * -1,
                                local_currency=local_currency, transaction_id=transaction.id)
        debit_entry = EntryDto(id=uuid.uuid4(), account=AccountDto(id=self.debit_account_id),
                               amount=abs(self.amount), local_amount=abs(local_amount),
                               local_currency=local_currency, transaction_id=transaction.id)

        transaction.entries = [credit_entry, debit_entry]
        return transaction
