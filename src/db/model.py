from datetime import datetime
from decimal import Decimal
from enum import Enum as PythonEnum
from uuid import UUID

from sqlalchemy import Enum as SQLEnum
from sqlalchemy import ForeignKey, Numeric, String, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
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
    name: Mapped[str] = mapped_column(unique=True)
    type: Mapped[AccountType] = mapped_column(SQLEnum(AccountType), name="account_type")

    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        server_default=text("'{}'::text[]"),
        default=list
    )

    # Relationships
    entries: Mapped[list["Entry"]] = relationship(back_populates="account")


class Transaction(Base):
    """A financial transaction holding metadata and linking multiple entries."""

    __tablename__ = "transaction"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    ledger_id: Mapped[UUID] = mapped_column(ForeignKey("ledger.id", ondelete="CASCADE"), index=True)

    transaction_datetime: Mapped[datetime] = mapped_column(index=True)
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