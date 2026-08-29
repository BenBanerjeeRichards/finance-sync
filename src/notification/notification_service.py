from decimal import Decimal

import dependencies
from ledger.dto import TransactionDto, AccountType, EntryDto
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
            # Only care about card transactions which will have two legs
            if len(transaction.entries) != 2:
                logging.info("skipping notifying %s: > "), transaction.id
                return
            asset_entries: list[EntryDto] = [e for e in transaction.entries if e.account.type == AccountType.ASSET]
            santander_amount = sum([x.amount for x in asset_entries if x.account.name == "Santander"])
            if not santander_amount:
                logging.info("skipping notifying %s: no santander amount "), transaction.id
                return
            if transaction.ledger_metadata.get("source") not in ["monzo", "santander"]:
                logging.info("skipping non-monzo or santander notification: %s", transaction.id)
                return

            context = NewTransactionNotification(transaction_id=str(transaction.id), amount=str(santander_amount),
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
        asset_amount = Decimal(context.amount)
        if asset_amount > 0:
            self.discord_client.send_message(f"💸 Received {asset_amount =} from {context.counterparty_name}")
        else:
            self.discord_client.send_message(f"💵 Spent {asset_amount} at {context.counterparty_name}")
