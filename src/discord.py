import requests
from pydantic import BaseModel
import logging


class DiscordMessage(BaseModel):
    content: str
    flags: int


class DiscordClient:

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send_message(self, message: str) -> None:
        logging.info("Sending message to discord webhook: %s", message)
        discord_message = DiscordMessage(content=message, flags=4) # 4 = SUPPRESS_EMBEDS
        res = requests.post(self.webhook_url, json=discord_message.dict())
        res.raise_for_status()
