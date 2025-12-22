from beancount_sync.beancount_sync import BeancountTransaction
from notification.discord import DiscordClient


class Notifier:

    def __init__(self, discord_client: DiscordClient):
        self.discord_client = discord_client

    def send_santander_discord_notification(self, account_name: str, tx: BeancountTransaction) -> None:
        formatted_amount = f"£{abs(tx.amount):.2f}"
        if tx.debit_account == account_name:
            message = f"💸 Received {formatted_amount} from {tx.payee or tx.description}"
        else:
            message = f"💵 Spent {formatted_amount} at {tx.payee or tx.description}"
        self.discord_client.send_message(message)

    def notify_expiring(self, name: str, url: str | None, days: int | None) -> None:
        message = f"⚠️ {name} connection expires {"soon" if not days else f"in {days} days"}"
        if url:
            message += f". Re-connect: {url}"
        self.discord_client.send_message(message)

