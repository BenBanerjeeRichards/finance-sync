import uuid
from uuid import UUID

from sqlalchemy import select, delete, tuple_
from sqlalchemy.dialects.postgresql import insert  # need postgres version for on_conflict
from sqlalchemy.orm import Session
import logging

from ledger.model import Account, Ledger, Transaction, Entry
from ledger.dto import AccountDto, LedgerDto


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
        diff =  {x[0] for x in tx_acc_ids} - set(transaction_ids)
        if diff:
            logging.error("invalid args: %s", diff)
            assert len(diff) == 0
        cleanup = delete(Entry).where(Entry.transaction_id.in_(transaction_ids)).where(
            tuple_(Entry.transaction_id, Entry.account_id).not_in(tx_acc_ids))
        res = session.execute(cleanup)
        logging.info("cleanup cleaned %s ledger entries", res.rowcount)
