import minio
from beancount.core.data import Transaction as BcTransaction
from beancount.loader import load_string
from pika.adapters.blocking_connection import BlockingChannel

from beancount_sync.beancount_sync import BeancountTransaction
from beancount_sync.monzo_translater import MonzoTranslater
from beancount_sync.santander_translater import SantanderTranslater
from model import Config, SantanderTransactions, Transaction
from santander import from_gc
from storage import Store, SANTANDER_TX_FILE, MONZO_TX_FILE
import logging
import hashlib


def backfill_monzo(config: Config, ch: BlockingChannel, minio_client: minio.Minio, routing_key: str, in_only=False):
    monzo_transactions = Store(minio_client, "transactions").load_list(MONZO_TX_FILE, Transaction)
    translater = MonzoTranslater(config)
    ledger_transactions = [translater.translate_to_beancount(tx) for tx in monzo_transactions if tx.created > "2024-04"]
    if in_only:
        ledger_transactions = [tx for tx in  ledger_transactions if tx.debit_account == "Assets:Cash:Monzo"]
    backfill_transactions(ch, routing_key, ledger_transactions)


def backfill_santander_gc(config: Config, ch: BlockingChannel, minio_client: minio.Minio, routing_key: str) -> None:
    santander_transactions = Store(minio_client, "transactions").load(SANTANDER_TX_FILE,
                                                                      SantanderTransactions).transactions
    translater = SantanderTranslater(config)
    logging.info("Backfilling %s santander transactions", len(santander_transactions))
    ledger_transactions = [translater.translate_to_beancount(from_gc(tx)) for tx in santander_transactions]
    backfill_transactions(ch, routing_key, [tx for tx in ledger_transactions if tx])


def backfill_from_beancount(config: Config, ch: BlockingChannel, minio_client: minio.Minio, route: str, bucket: str,
                            file: str) -> None:
    response = minio_client.get_object(bucket_name=bucket, object_name=file)
    tx_contents = response.read().decode("utf-8")
    items, errors, opt = load_string(tx_contents)
    if errors:
        logging.warning("Found errors in beancount file %s", errors)
    bc_txs = []
    for item in items:
        if not isinstance(item, BcTransaction):
            logging.info("Skipping non-transaction item %s", item)
            continue
        if len(item.postings) != 2:
            logging.warning("Skipping transaction with postings not equal to 2 %s", item)
            continue
        amount = abs(item.postings[0].units.number)
        # generate an external id that must remain the same: this allows the actual id to be set and
        # prevents duplicates from being created
        external_id_items = f"{item.date}-{item.payee}-{item.narration}-{amount}"
        external_id = hashlib.sha1(external_id_items.encode()).hexdigest()[20:]
        if amount == 0:
            # if no money, does not matter
            credit_posting = item.postings[0]
            debit_posting = item.postings[1]
        else:
            credit_posting = [posting for posting in item.postings if posting.units.number < 0]
            debit_posting = [posting for posting in item.postings if posting.units.number > 0]
            if not credit_posting:
                logging.error("Found no credit posting for transaction %s", item)
            if not debit_posting:
                logging.error("Found no debit posting for transaction %s", item)
            credit_posting = credit_posting[0]
            debit_posting = debit_posting[0]
        bc = BeancountTransaction(external_id=external_id, tx_date=item.date, amount=amount,
                                  credit_account=credit_posting.account, debit_account=debit_posting.account,
                                  payee=item.payee, description=item.narration, flagged=item.flag != "*", metadata={},
                                  source="beancount_file")
        bc_txs.append(bc)
    backfill_transactions(ch, route, bc_txs)

def backfill_transactions(ch: BlockingChannel, routing: str, transactions: list[BeancountTransaction]) -> None:
    logging.info("Submitting %s transactions to routing key %s for backfill purposes", len(transactions), routing)
    for tx in transactions:
        ch.basic_publish(exchange="", routing_key=routing, body=tx.model_dump_json())
