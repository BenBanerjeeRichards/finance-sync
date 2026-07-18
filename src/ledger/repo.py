import uuid
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select, delete, tuple_, or_, func, desc
from sqlalchemy.dialects.postgresql import insert  # need postgres version for on_conflict
from sqlalchemy.orm import Session, defer
import logging

from ledger.model import Account, Ledger, Transaction, Entry
from ledger.dto import AccountDto, LedgerDto
import base64


class TransactionFilters(BaseModel):
    created_gt: datetime | None = None
    created_lt: datetime | None = None
    account_name: str | None = None
    key: str | None = None
    tags: list[str] | None = None
    payee: str | None = None
    text_filter: str | None = None  # search over trigrams {payee, tags, narration}


class ListTransactionCursor(BaseModel):
    created: datetime
    id: UUID

    def to_b64(self) -> str:
        return base64.b64encode(self.model_dump_json().encode("utf8")).decode("utf8")

    @staticmethod
    def from_b64(cursor: str) -> "ListTransactionCursor":
        return ListTransactionCursor.model_validate_json(base64.b64decode(cursor))


class LedgerRepo:

    def __init__(self):
        pass

    @staticmethod
    def get_accounts(session) -> list[AccountDto]:
        return [AccountDto.model_validate(a) for a in session.scalars(select(Account))]

    @staticmethod
    def get_ledgers(session) -> list[LedgerDto]:
        return [LedgerDto.model_validate(l) for l in session.scalars(select(Ledger))]

    @staticmethod
    def get_transactions(session: Session, filters: TransactionFilters,
                         count: int = 100, cursor: ListTransactionCursor | None = None) -> tuple[
        list[Transaction], ListTransactionCursor | None]:
        if filters.text_filter:
            q = select(Transaction)
        else:
            q = select(Transaction)

        q = q.options(defer(Transaction.tx_metadata))
        q = q.options(defer(Transaction.external_metadata))

        q = q.join(Transaction.entries).join(Entry.account)
        if filters.created_gt:
            q = q.where(Transaction.transaction_datetime >= filters.created_gt)
        if filters.created_lt:
            q = q.where(Transaction.transaction_datetime <= filters.created_lt)
        if filters.key:
            q = q.where(Transaction.key == filters.key)
        if filters.tags:
            q = q.where(Transaction.tags.contains(filters.tags))
        if filters.payee:
            q = q.where(Transaction.payee.ilike(f"%{filters.payee}%", escape="/"))
        if filters.account_name:
            account = session.scalar(select(Account).where(Account.name.ilike(filters.account_name)))
            if not account:
                return [], None
            q = q.where(Account.id == account.id)
        if filters.text_filter:
            q = q.where(or_(
                Transaction.search_vector.op("%>")(filters.text_filter),
                Transaction.key == filters.text_filter,
                Account.name == filters.account_name
            ))

        q = q.order_by(Transaction.transaction_datetime.desc(), Transaction.id.desc())
        q = q.distinct().limit(count + 1)  # get next one to determine next cursor
        if cursor:
            # remember we go from most to least recent hence <=
            q = q.where(tuple_(Transaction.transaction_datetime, Transaction.id) <= (cursor.created, cursor.id))
        result = list(session.scalars(q))
        if len(result) == count + 1:
            next_cursor = ListTransactionCursor(created=result[-1].transaction_datetime, id=result[-1].id)
            return result[:count], next_cursor
        return result[:count], None

    @staticmethod
    def get_payees(session: Session, filter: str | None = None) -> list[str]:
        q = select(Transaction.payee).distinct()
        if filter:
             q = q.where(Transaction.payee.ilike(f"%{filter}%"))
        q = q.limit(100)
        return list(session.scalars(q).all())

    @staticmethod
    def get_tags(session: Session) -> list[str]:
        stmt = select(func.distinct(func.unnest(Transaction.tags)))
        return list(session.scalars(stmt).all())

    @staticmethod
    def ensure_account(session: Session, acc: Account):
        st = insert(Account).values(name=acc.name, type=acc.type, tags=acc.tags, id=acc.id).on_conflict_do_nothing(
            index_elements=["name"])
        session.execute(st)

    @staticmethod
    def ensure_ledger(session: Session, name: str):
        st = insert(Ledger).values(name=name, id=uuid.uuid4()).on_conflict_do_nothing(index_elements=["name"])
        session.execute(st)

    @staticmethod
    def bulk_upsert_transactions(session: Session, transactions: list[Transaction]) -> dict[str, UUID]:
        if not transactions:
            return {}
        logging.info("upserting %s transactions", len(transactions))
        data_to_upsert = [
            {k: v for k, v in tx.__dict__.items() if k != '_sa_instance_state'}
            for tx in transactions
        ]
        stmt = insert(Transaction.__table__)
        update_values = {
            c.name: stmt.excluded[c.name]
            for c in Transaction.__table__.c
            if c.name not in ["id", "key"]
        }
        upsert_stmt = (
            stmt.on_conflict_do_update(
                index_elements=["key"],
                set_=update_values
            )
            .returning(Transaction.id, Transaction.key)
        )
        res = session.execute(upsert_stmt, data_to_upsert)
        return {row.key: row.id for row in res.all()}

    @staticmethod
    def bulk_upsert_entries(session: Session, entries: list[Entry]):
        if not entries:
            return

        logging.info("upserting %s entries", len(entries))
        data_to_upsert = [
            {k: v for k, v in entry.__dict__.items() if k != '_sa_instance_state'}
            for entry in entries
        ]
        stmt = insert(Entry.__table__)
        update_values = {
            c.name: stmt.excluded[c.name]
            for c in Entry.__table__.c
            if c.name not in ["id", "transaction_id", "account_id"]
        }
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=["transaction_id", "account_id"],
            set_=update_values
        )
        session.execute(upsert_stmt, data_to_upsert)

    @staticmethod
    def delete_entries_in_transactions_not_in(session: Session, transaction_ids: list[UUID],
                                              tx_acc_ids: list[tuple[UUID, UUID]]):
        # quick check to make sure that we have got params right way round
        diff = {x[0] for x in tx_acc_ids} - set(transaction_ids)
        if diff:
            logging.error("invalid args: %s", diff)
            assert len(diff) == 0
        cleanup = delete(Entry).where(Entry.transaction_id.in_(transaction_ids)).where(
            tuple_(Entry.transaction_id, Entry.account_id).not_in(tx_acc_ids))
        res = session.execute(cleanup)
        logging.info("cleanup cleaned %s ledger entries", res.rowcount)
