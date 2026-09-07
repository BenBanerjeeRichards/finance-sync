import uuid
from decimal import Decimal

from pydantic import TypeAdapter

import dependencies
from ledger.dto import TransactionDto, AccountType, EntryDto
from ledger.ledger_service import LedgerService
from main import Session
from notification.discord import DiscordClient
from notification.model import NewTransactionNotification, ExpiringConnectionNotification, NotificationContext
import logging


class NotificationService:

    def __init__(self, discord_client: DiscordClient, ledger_service: LedgerService):
        self.notification_repo = dependencies.get_notification_repo()
        self.discord_client = discord_client
        self.ledger_service = ledger_service

    def register_new_transaction(self, transaction: TransactionDto):
        with Session.begin() as session:
            # Only care about card transactions which will have two legs
            if len(transaction.entries) != 2:
                logging.info("skipping notifying %s: >2 legs "), transaction.id
                return
            asset_entries: list[EntryDto] = [e for e in transaction.entries if e.account.type == AccountType.ASSET]
            santander_amount = sum([x.amount for x in asset_entries if x.account.name == "Santander"])
            if not santander_amount:
                logging.info("skipping notifying %s: no santander amount "), transaction.id
                return
            if transaction.ledger_metadata.get("source") not in ["monzo", "santander"]:
                logging.info("skipping non-monzo or santander notification: %s", transaction.id)
                return

            context = NewTransactionNotification(transaction_key=str(transaction.key), amount=str(santander_amount),
                                                 counterparty_name=transaction.payee or transaction.narrative)
            self.notification_repo.register_notification(session, context.idempotency_key(),
                                                         "NewTransactionNotification", context.model_dump())

    def register_santander_expiring(self, connection_id: uuid.UUID, reauth_link: str, expires_in_days: int) -> None:
        with Session.begin() as session:
            context = ExpiringConnectionNotification(name="Santander", reauth_link=reauth_link,
                                                     connection_id=str(connection_id), expires_in_days=expires_in_days)
            self.notification_repo.register_notification(session, context.idempotency_key(),
                                                         "ExpiringConnection", context.model_dump())

    def send_notifications(self):
        adapter = TypeAdapter(NotificationContext)
        for i in range(10):  # limit per batch
            with Session.begin() as session:
                claim = self.notification_repo.claim_next_notification(session)
                if not claim:
                    return
                context = adapter.validate_python(claim.context)
                if isinstance(context, NewTransactionNotification):
                    self.send_new_transaction_notification(context)
                if isinstance(context, ExpiringConnectionNotification):
                    self.send_expiring_connection_notification(context)
                else:
                    logging.error("Unknown notification type %s", context.kind)

    def send_new_transaction_notification(self, context: NewTransactionNotification):
        asset_amount = Decimal(context.amount).quantize(Decimal(".01"))
        if asset_amount > 0:
            self.discord_client.send_message(f"💸 Received £{asset_amount} from {context.counterparty_name}")
        else:
            self.discord_client.send_message(f"💵 Spent £{abs(asset_amount)} at {context.counterparty_name}")


    def send_expiring_connection_notification(self, context: ExpiringConnectionNotification):
        message = f"⚠️ {context.name} connection expires {"soon" if not context.expires_in_days else f"in {context.expires_in_days} days"}"
        if context.reauth_link:
            message += ". Reauth: " + context.reauth_link
        self.discord_client.send_message(message)
