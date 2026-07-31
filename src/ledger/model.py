from datetime import datetime
from decimal import Decimal
from enum import Enum as PythonEnum
from uuid import UUID

from sqlalchemy import Enum as SQLEnum, UniqueConstraint, func, DateTime
from sqlalchemy import ForeignKey, Numeric, String, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AccountType(str, PythonEnum):
    ASSET = "asset"
    EXPENSE = "expense"  # Normally positive
    LIABILITY = "liability"  # Normally negative
    EQUITY = "equity"
    INCOME = "income"


class Ledger(Base):
    """Allows separating transactions (e.g., for financial years or Beancount files)."""

    __tablename__ = "ledger"

    # Switched to server-generated UUIDv4
    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column(unique=True)

    # Relationships
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="ledger",
        cascade="all, delete-orphan"
    )


class Account(Base):
    """Represents a Beancount-style chart of accounts."""

    __tablename__ = "account"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column()
    type: Mapped[AccountType] = mapped_column(SQLEnum(AccountType), name="account_type")

    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        server_default=text("'{}'::text[]"),
        default=list
    )

    # Relationships
    entries: Mapped[list["Entry"]] = relationship(back_populates="account")
    # allows for us to have e.g. a Factor liability account and a Factor expense account
    __table_args__ = (
        UniqueConstraint("name", "account_type", name="uq_name_and_type"),
    )

class Transaction(Base):
    """A financial transaction holding metadata and linking multiple entries."""

    __tablename__ = "transaction"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    ledger_id: Mapped[UUID] = mapped_column(ForeignKey("ledger.id", ondelete="CASCADE"), index=True)

    transaction_datetime: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True
    )
    key: Mapped[str] = mapped_column(index=True, unique=True)

    payee: Mapped[str | None] = mapped_column()
    narration: Mapped[str | None] = mapped_column()

    external_metadata: Mapped[dict] = mapped_column(
        JSONB,
        name="external_metadata",
        server_default=text("'{}'::jsonb")
    )
    tx_metadata: Mapped[dict] = mapped_column(
        JSONB,
        name="metadata",
        server_default=text("'{}'::jsonb")
    )

    flagged: Mapped[bool] = mapped_column(default=False)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        server_default=text("'{}'::text[]"),
        default=list
    )

    # Relationships
    ledger: Mapped["Ledger"] = relationship(back_populates="transactions")
    entries: Mapped[list["Entry"]] = relationship(
        back_populates="transaction",
        cascade="all, delete-orphan"
    )

    @hybrid_property
    def search_vector(self):
        return func.immutable_transaction_search_vector(
            self.payee,
            self.narration,
            self.tags
        )


class Entry(Base):
    """An individual posting/leg of a transaction."""

    __tablename__ = "entry"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    transaction_id: Mapped[UUID] = mapped_column(
        ForeignKey("transaction.id", ondelete="CASCADE"),
        index=True
    )
    account_id: Mapped[UUID] = mapped_column(ForeignKey("account.id"), index=True)

    amount: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=4))
    local_amount: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=4))
    local_currency: Mapped[str] = mapped_column()

    # Relationships
    transaction: Mapped["Transaction"] = relationship(back_populates="entries")
    account: Mapped["Account"] = relationship(back_populates="entries")

    # Limit to a single leg per account to keep things easy when preventing duplicates
    __table_args__ = (
        UniqueConstraint("transaction_id", "account_id", name="uq_entries_tx_account"),
    )


class MonzoImportIntegration(Base):
    __tablename__ = "monzo_importer"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    # use monzo client id as unique constraint as it does not make sense to have duplicate importers
    client_id: Mapped[str] = mapped_column(unique=True)
    client_secret: Mapped[str] = mapped_column()
    account_id: Mapped[str]= mapped_column()
    access_token: Mapped[str | None] = mapped_column()
    refresh_token: Mapped[str | None] = mapped_column()
    # Datetime representing latest refresh success, if failure this becomes None
    active_at: Mapped[datetime | None] = mapped_column()


class MonzoImportRule(Base):
    __tablename__ = "monzo_import_rule"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    import_integration_id: Mapped[UUID] = mapped_column(ForeignKey("monzo_importer.id"))
    name: Mapped[str] = mapped_column()
    priority: Mapped[int] = mapped_column()
    # Add this to tx
    new_metadata: Mapped[dict] = mapped_column(
        JSONB,
        name="metadata",
        server_default=text("'{}'::jsonb")
    )

    # No FK as Ledger is a different domain
    # This is the account the transaction is assigned to (the other side to the Monzo)
    account_id: Mapped[UUID] = mapped_column()
    # What payee & narration to set on the tx
    payee: Mapped[str | None] = mapped_column()
    narration: Mapped[str | None] = mapped_column()
    # Pre-filter that can be combined with anything: date
    created_at: Mapped[datetime | None] = mapped_column()
    # Monzo category, e.g. for defining holiday -> Expenses:Holiday
    category: Mapped[str | None] = mapped_column()
    # From monzo counterparty.account_name
    account_number: Mapped[str | None] = mapped_column()
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        server_default=text("'{}'::text[]"),
        default=list
    )
    # Monzo pot id
    pot_id: Mapped[str | None] = mapped_column()
    # Defines the Merchant (group means all branches, e.g. all Sainburys have same group id)
    merchant_group_id: Mapped[str | None] = mapped_column()
    # counterparty.name
    counterparty_name: Mapped[str | None] = mapped_column()
    # match for a single transaction, useful for specific overrides
    transaction_id: Mapped[UUID | None] = mapped_column()


class GoCardlessImportIntegration(Base):
    __tablename__ = "go_cardless_importer"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    secret_id: Mapped[str] = mapped_column(unique=True)
    secret_key: Mapped[str] = mapped_column()
    requisition_expires_at: Mapped[datetime | None] = mapped_column()


class GoCardlessImportRule(Base):
    __tablename__ = "gocardless_import_rule"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    import_integration_id: Mapped[UUID] = mapped_column(ForeignKey("go_cardless_importer.id"))
    name: Mapped[str] = mapped_column()
    priority: Mapped[int] = mapped_column()
    new_metadata: Mapped[dict] = mapped_column(
        JSONB,
        name="metadata",
        server_default=text("'{}'::jsonb")
    )
    account_id: Mapped[UUID] = mapped_column()
    payee: Mapped[str | None] = mapped_column()
    narration: Mapped[str | None] = mapped_column()


    credit_only: Mapped[bool] = mapped_column(default=False)
    debit_only: Mapped[bool] = mapped_column(default=False)
    ignore: Mapped[bool] = mapped_column(default=False)
    transaction_type: Mapped[str | None] = mapped_column()
    account_name_matches: Mapped[str | None] = mapped_column()
    reference_name_matches: Mapped[str | None] = mapped_column()
    amount_equals: Mapped[Decimal | None] = mapped_column()
