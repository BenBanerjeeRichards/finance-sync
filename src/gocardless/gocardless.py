import requests
import os
from pydantic import BaseModel
import logging
import json


GC_BASE = "https://bankaccountdata.gocardless.com/api/v2/"

class GcRequisitionResponse(BaseModel):
    link: str
    id: str
    reference: str

class GcAccountResponse(BaseModel):
    resourceId: str
    iban: str
    currency: str
    ownerName: str
    name: str
    product: str
    cashAccountType: str

class GcGetRequisitionsResponse(BaseModel):
    id: str
    status: str
    institution_id: str
    created: str
    link: str
    accounts: list[str]
    reference: str


class GcAmount(BaseModel):
    amount: str
    currency: str

class GcDebtorAccount(BaseModel):
    iban: str

class GcTranscation(BaseModel):
    transactionId: str | None = None
    internalTransactionId: str | None = None
    entryReference: str | None = None
    bookingDate: str | None = None
    entryDate: str | None = None
    transactionAmount: GcAmount
    debtorName: str | None = None
    debtorAccount: GcDebtorAccount | None = None
    remittanceInformationUnstructured: str
    bankTransactionCode: str | None = None
    proprietaryBankTransactionCode: str | None = None


class GcAccountsBase(BaseModel):
    # Below headers are retrieved from headers, they are often very restrictive
    account_success_limit: int
    account_success_remaining: int
    account_success_reset_seconds: int


class GcTransactionsPendingBooked(BaseModel):
    booked: list[GcTranscation]
    pending: list[GcTranscation]

class GcTransactionsResponse(GcAccountsBase):
    transactions: GcTransactionsPendingBooked


class EUAExpiredError(Exception):
    pass

class AccountNotFoundError(Exception):
    pass

class AccessExpiredError(Exception):
    pass

class RateLimitError(Exception):
    pass


class GoCardlessClient:

    def __init__(self, secret_id: str, secret_key: str, insit_id: str, redirect_uri: str):
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.insit_id = insit_id
        self.access = None
        self.redirect_uri = redirect_uri

    def get_new_tokens(self) -> tuple[str, str]:
        logging.info("Obtaining new tokens")
        resp = requests.post(GC_BASE + "token/new/", json={
            "secret_id": self.secret_id,
            "secret_key": self.secret_key
        })
        self._check_resp(resp)
        resp_json = resp.json()
        self.access = resp_json["access"]
        return resp_json["access"], resp_json["refresh"]

    def get_institutions(self) -> dict:
        logging.info("Gettting all insutitions")
        resp = requests.get(GC_BASE + "institutions/?country=gb", headers={
            "Authorization": f"Bearer {self.access}"
        })
        self._check_resp(resp)
        resp_json = resp.json()
        return resp_json

    def get_requisitions(self) -> list[GcGetRequisitionsResponse]:
        logging.info("Gettting all requisitions")
        url = GC_BASE + "requisitions/"
        reqs = []
        while url is not None:
            resp = requests.get(url, headers={
                "Authorization": f"Bearer {self.access}"
            })
            self._check_resp(resp)
            resp_json = resp.json()
            url = resp_json["next"]
            reqs += [GcGetRequisitionsResponse(**r) for r in resp_json["results"]]
        return reqs

    def get_requisition(self, req_id: str) -> GcGetRequisitionsResponse:
        url = f"{GC_BASE}requisitions/{req_id}/"
        resp = requests.get(url, headers={
            "Authorization": f"Bearer {self.access}"
        })
        self._check_resp(resp)
        return GcGetRequisitionsResponse(**resp.json())

    def delete_requisition(self, req_id: str) -> dict:
        logging.info("Deleting requisiton %s", req_id)
        resp = requests.delete(f"{GC_BASE}requisitions/{req_id}", headers={
            "Authorization": f"Bearer {self.access}"
        })
        self._check_resp(resp)
        resp_json = resp.json()
        return resp_json

    def create_requisition(self) -> GcRequisitionResponse:
        logging.info("Creating requisition")
        resp = requests.post(GC_BASE + "requisitions/", json={
            "institution_id": self.insit_id,
            "redirect": self.redirect_uri,
        }, headers={
            "Authorization": f"Bearer {self.access}"
        })
        self._check_resp(resp)
        return GcRequisitionResponse(**resp.json())

    def get_accounts(self, req_id: str) -> list[str]:
        logging.info("Getting accounts for req %s", req_id)
        resp = requests.get(f"{GC_BASE}requisitions/{req_id}/", headers={
            "Authorization": f"Bearer {self.access}"
        })
        self._check_resp(resp)
        return resp.json()["accounts"]

    def get_account_details(self, account_id: str) -> GcAccountResponse:
        logging.info("Getting account details for account %s", account_id)
        resp = requests.get(f"{GC_BASE}accounts/{account_id}/details/", headers={
            "Authorization": f"Bearer {self.access}"
        })
        self._check_resp(resp)
        return GcAccountResponse(**resp.json())

    def get_account_transactions(self, account_id: str) -> GcTransactionsResponse:
        logging.info("Getting account transcations %s", account_id)
        resp = requests.get(f"{GC_BASE}accounts/{account_id}/transactions/", headers={
            "Authorization": f"Bearer {self.access}"
        })
        self._check_resp(resp)
        accounts_response = resp.json()
        return GcTransactionsResponse(**{
            "account_success_limit": int(resp.headers["http_x_ratelimit_account_success_limit"]),
            "account_success_remaining": int(resp.headers["http_x_ratelimit_account_success_remaining"]),
            "account_success_reset_seconds": int(resp.headers["http_x_ratelimit_account_success_reset"]),
            **accounts_response
        })

    def _check_resp(self, resp: requests.Response):
        if resp.status_code < 200 or resp.status_code > 299:
            logging.error("Failed to make gc request %s", resp.json())
            detail = resp.json().get("detail")
            if not detail:
                resp.raise_for_status()
            try:
                resp.raise_for_status()
            except Exception as e:
                if "Please check whether you specified a valid Account ID" in detail:
                    raise AccountNotFoundError() from e
                if "The end user must connect the account" in detail:
                    raise EUAExpiredError() from e
                if "Access has expired or it has been revoked" in detail:
                    raise AccessExpiredError() from e
                if resp.status_code == 429:
                    raise RateLimitError() from e
                raise e

