import datetime
import uuid
from decimal import Decimal
from zoneinfo import ZoneInfo

import minio
from beancount.core.data import Transaction as BcTransaction
from beancount.loader import load_string

from ledger.ledger_service import LedgerService
from ledger.repo import LedgerRepo
from main import Session
from ledger.model import Transaction as DbTransaction, Entry
import logging


def backfill_ledger_from_beancount(minio_client: minio.Minio, bucket: str, file: str) -> None:
    response = minio_client.get_object(bucket_name=bucket, object_name=file)
    tx_contents = response.read().decode("utf-8")
    items, errors, opt = load_string(tx_contents)
    if errors:
        logging.warning("Found errors in beancount file %s", errors)
    bc_account_names = set()
    bc_transactions = [item for item in items if isinstance(item, BcTransaction)]
    for item in bc_transactions:
        for posting in item.postings:
            bc_account_names.add(posting.account)
    account_dtos = [LedgerService._from_beancount_account_name(bc_acc) for bc_acc in bc_account_names]
    with Session.begin() as session:
        [LedgerRepo.ensure_account(session, a) for a in account_dtos]
        ledger_name_to_id = {l.name: l.id for l in LedgerRepo.get_ledgers(session)}
        ledger_name = file.split(".")[0]
        ledger = ledger_name_to_id[ledger_name]

    accounts = LedgerService.get_accounts()
    txs = []
    all_entries = []
    for item in bc_transactions:
        dt = datetime.datetime.combine(item.date, datetime.time.min, tzinfo=ZoneInfo("UTC"))
        key_amount = sum([a.units.number for a in item.postings if a.units.number >= 0])
        key = LedgerService.compute_key(dt, item.payee, item.narration, Decimal(key_amount))
        tx = DbTransaction(id=uuid.uuid4(), ledger_id=ledger, transaction_datetime=dt, key=key, payee=item.payee,
                            narration=item.narration, tags=list(set(item.tags)))
        entries = []
        for posting in item.postings:
            acc_dto = LedgerService._from_beancount_account_name(posting.account)
            account = [a for a in accounts if a.name == acc_dto.name and a.type == acc_dto.type][0]
            e = Entry(id=uuid.uuid4(), transaction_id=tx.id, amount=posting.units.number,
                             local_amount=posting.units.number, local_currency="GBP", account_id=account.id)
            entries.append(e)
            all_entries.append(e)
        tx.entries = entries
        txs.append(tx)

    with Session.begin() as session:
        LedgerRepo.bulk_upsert_transactions(session, txs)
        LedgerRepo.bulk_upsert_entries(session, all_entries)

