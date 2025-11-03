import datetime
from decimal import Decimal

from minio import Minio

from gocardless.gocardless import GoCardlessClient, GcTranscation
from gocardless.gc_connection import MissingTransactionId
from model import Config, GcStore, SantanderTransactions, GcSantanderTransaction
from storage import GC_STORE_FILE, SANTANDER_TX_FILE, ObjectNotFound, Store
import logging

class SantanderImporter:

    def __init__(self, config: Config, gc_secret_id: str, gc_secret_key: str, minio_client: Minio):
        self.config = config
        self.client = GoCardlessClient(gc_secret_id, gc_secret_key, self.config.gocardless.insitutionId,
                                          self.config.gocardless.redirectUri)
        self.store = Store(minio_client)

    def import_transactions(self):
        self.client.get_new_tokens()
        store = self.store.load(GC_STORE_FILE, GcStore)
        transactions = self.client.get_account_transactions(store.account_id)
        logging.info("Got transactions from Santander (%s booked, %s pending)", len(transactions.transactions.booked), len(transactions.transactions.pending))

        try:
            existing_transactions = self.store.load(SANTANDER_TX_FILE, SantanderTransactions).transactions
        except ObjectNotFound:
            logging.info("No existing transactions found... creating new list")
            existing_transactions = []
        txs_by_id = {tx.transaction_id: tx for tx in existing_transactions}

        booked_transactions = [SantanderImporter._gc_to_model(tx, True) for tx in transactions.transactions.booked if tx.transactionId]
        pending_transactions = [SantanderImporter._gc_to_model(tx, True) for tx in transactions.transactions.pending if tx.transactionId]

        booked_no_ids= [tx for tx in transactions.transactions.booked if not tx.transactionId]
        pending_no_ids= [tx for tx in transactions.transactions.pending if not tx.transactionId]

        if booked_no_ids:
            logging.error("Found booked transactions with no id %s", booked_no_ids)
        if booked_no_ids:
            logging.info("Found pending transactions with no id %s", pending_no_ids)

        for tx in booked_transactions + pending_transactions:
            txs_by_id[tx.transaction_id] = tx
        new_transactions = sorted(list(txs_by_id.values()), key=lambda x: (x.date is not None, x.date), reverse=True)
        logging.info("Added %s new transactions to total %s", len(new_transactions) - len(existing_transactions), len(new_transactions))
        self.store.write(SANTANDER_TX_FILE, SantanderTransactions(transactions=new_transactions))

    @staticmethod
    def _gc_to_model(tx: GcTranscation, booked: bool) -> GcSantanderTransaction:
        if tx.transactionAmount.currency != "GBP":
            logging.error("Failed to process transaction %s", tx)
            raise ValueError("Unsupported currency " + tx.transactionAmount.currency)
        if not tx.transactionId:
            logging.error("Failed to process transaction %s", tx)
            raise MissingTransactionId()    # sometimes no tx id until booking?

        return GcSantanderTransaction(
            transaction_id=tx.transactionId,
            description=tx.remittanceInformationUnstructured,
            # NB: debtorName is confusing - really it is the counterparty name as debtorName is
            # used regardless of whether the money is flowing out of the acount (debtorName is correct)
            # or whether it is flowing into the account (where it is really the creditor name)
            counterparty_name=tx.debtorName,
            booked=booked,
            amount=Decimal(tx.transactionAmount.amount),
            date=datetime.datetime.strptime(tx.bookingDate, "%Y-%m-%d") if tx.bookingDate else None,
            transaction_code=tx.proprietaryBankTransactionCode
        )


    def days_requisitions_expiring_in(self) -> int | None:
        # TODO: this doesn't really belong here
        """
        The number of days the soonest requisition will expire, or None if there are no active requisitions
        """
        self.client.get_new_tokens()
        requisitions = self.client.get_requisitions()
        inst_id = self.config.gocardless.insitutionId
        durations_days = []
        for req in [r for r in requisitions if r.institution_id == inst_id and r.created]:
            req_date = datetime.datetime.fromisoformat(req.created.replace('Z', '+00:00'))
            delta = datetime.datetime.now(datetime.timezone.utc) - req_date
            logging.info("Req %s (%s) has existed for %s days", req.id, req.institution_id, delta.days)
            durations_days.append(delta.days)
        return min(durations_days) if durations_days else None
