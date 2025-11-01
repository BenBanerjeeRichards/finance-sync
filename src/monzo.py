import requests
import logging
import json
from datetime import datetime, timezone, timedelta
from typing import Tuple
import time
import os


MONZO_BASE = "https://api.monzo.com"


def _check_response(res):
    try:
        res.raise_for_status()
    except Exception as e:
        logging.info("Invalid response: %s", json.dumps(res.json(), indent=2))
        raise e


class MonzoClient:

    def __init__(self, access_token: str, refresh_token: str, client_id: str, client_secret: str, account_id: str):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.account_id = account_id


    def get_access_token(self) -> Tuple[str, str]:
        res = requests.post(MONZO_BASE + "/oauth2/token", {
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token
        })
        _check_response(res)
        j = res.json()
        return j["access_token"], j["refresh_token"]


    def get_transactions(self, since=None, before=None, limit=100):
        start = time.time() * 1000
        since_query = "" if since is None else f"&since={since}"
        before_query = "" if before is None else f"&before={before}"
        url = MONZO_BASE + f"/transactions?expand[]=merchant&account_id={self.account_id}&limit={limit}" + since_query + before_query
        res = requests.get(url,
                        headers={"Authorization": f"Bearer {self.access_token}"})
        _check_response(res)
        j = res.json()
        logging.info("Got transactions from monzo num_transactions=%s time_ms=%s url=%s",
                    len(j["transactions"]), round((1000 * time.time()) - start), url)
        return [t for t in j["transactions"] if "decline_reason" not in t]


    def get_transactions_since(self, since: datetime):
        # See: https://community.monzo.com/t/changes-when-listing-with-our-api/158676
        # Pagination of monzo is horrible! No nice way of doing it, especially for old transactions
        # Monzo limits the diff between since and before
        # So we just use the since to paginate from the given start date, and then just keep
        # before 365 (max range) days ahead of the since date
        before = since + timedelta(days=364)
        while since <= datetime.now(tz=timezone.utc):
            final_call = False
            if (before - since).total_seconds() <= 60 and (datetime.now(tz=timezone.utc) - before).total_seconds() <= 20:
                since = since - timedelta(seconds=60)
                final_call = True
            res = self.get_transactions(limit = 100, since=since.strftime('%Y-%m-%dT%H:%M:%SZ'),
                                                before=before.strftime('%Y-%m-%dT%H:%M:%SZ'))
            yield res
            # hack, can't be bothered to figure out this part of the logic
            if final_call:
                return
            if len(res) == 0:
                # If no transactions in the period, then just move along and check the next year
                since = before
                before = since +timedelta(days=364)
            else:
                latest_dt = datetime.fromisoformat(res[-1]["created"])
                diff_days = (latest_dt - since).days
                assert diff_days >= 0
                before = before + timedelta(days=diff_days)
                if before >= datetime.now(tz=timezone.utc):
                    before = datetime.now(tz=timezone.utc) - timedelta(seconds=10)  # Monzo doesn't like it if you query close to now
                since = latest_dt + timedelta(seconds=1)


    def set_transaction_notes(self, tx_id: str, notes: str):
        # No point making this more generic as annotating doesn't work - https://community.monzo.com/t/annotate-transaction-endpoint-not-working-for-custom-key/121203
        # Seems that only notes are supported, which is fine for this application
        logging.info("Setting transaction note %s=%s", tx_id, notes)
        url = MONZO_BASE + f"/transactions/{tx_id}"
        update = {"metadata[notes]": notes}
        res = requests.patch(url,
                        headers={"Authorization": f"Bearer {self.access_token}"}, data=update)
        _check_response(res)


    def register_webhook(self, endpoint: str):
        url = MONZO_BASE + "/webhooks"
        data = {
            "account_id": self.account_id,
            "url": endpoint
        }
        res = requests.post(url,
                        headers={"Authorization": f"Bearer {self.access_token}"}, data=data)
        _check_response(res)
