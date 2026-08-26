import dependencies
from ledger.dto import TransactionDto, AccountType
from ledger.ledger_service import LedgerService
from main import Session
from notification.discord import DiscordClient
from notification.model import NewTransactionNotification
import logging
import uuid


class NotificationService:

    def __init__(self, discord_client: DiscordClient, ledger_service: LedgerService):
        self.notification_repo = dependencies.get_notification_repo()
        self.discord_client = discord_client
        self.ledger_service = ledger_service

    def register_new_transaction(self, transaction: TransactionDto):
        with Session.begin() as session:
            asset_amount = sum([e.amount for e in transaction.entries if e.account.type == AccountType.ASSET])
            if abs(asset_amount) != transaction.absolute_amount():
                logging.info("skipping registration of new notification for transaction %s as asset_amount is %s",
                             transaction.id, asset_amount)
                return
            if not asset_amount:
                logging.info("skipping registration of new notification for transaction %s as asset_amount is %s",
                             transaction.id, asset_amount)
                return
            context = NewTransactionNotification(transaction_id=str(transaction.id), amount=str(asset_amount),
                                                 counterparty_name=transaction.payee or transaction.narrative)
            i = self.notification_repo.register_notification(session, context.idempotency_key(),
                                                             "NewTransactionNotification", context.model_dump())
            logging.info("Registered notification with id %s key %s", i, context.idempotency_key())

    def send_notifications(self):
        for i in range(10):  # limit per batch
            with Session.begin() as session:
                claim = self.notification_repo.claim_next_notification(session)
                if not claim:
                    return
                if claim.type == "NewTransactionNotification":
                    ctx = NewTransactionNotification(**claim.context)
                    self.send_new_transaction_notification(ctx)
                else:
                    logging.error("Unknown notification type %s", claim.type)

    def send_new_transaction_notification(self, context: NewTransactionNotification):
        # make sure we are up to date
        tx = self.ledger_service.get_transaction(uuid.UUID(context.transaction_id))
        if not tx:
            logging.error("Failed to get transaction with id %s", context.transaction_id)
            return
        asset_amount = sum([e.amount for e in tx.entries if e.account.type == AccountType.ASSET])
        if asset_amount > 0:
            self.discord_client.send_message(f"💸 Received {tx.absolute_amount()} from {tx.payee or tx.description}")
        else:
            self.discord_client.send_message(f"💵 Spent {tx.absolute_amount()} at {tx.payee or tx.description}")
