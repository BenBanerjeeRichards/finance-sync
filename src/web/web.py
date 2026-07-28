import asyncio
import datetime
import os
import uuid
from pathlib import Path

import pika
import uvicorn
from fastapi import FastAPI, Request, Depends, HTTPException
from minio import Minio
from pika.adapters.blocking_connection import BlockingChannel
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import RedirectResponse
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from gocardless.gc_connection import GcConnection
from importer.import_service import ImportService, MonzoImportRuleDto
from ledger.ledger_service import LedgerService
from ledger.repo import TransactionFilters
from model import MonzoStore, MonzoSyncMessage
from monzo import MonzoClient
from web.model import GetTransactionsParams, GetPayeeParams, GetBalanceHistoryParams, GetBalanceParams, \
    MonzoImportConfigResponse, GcImportConfigResponse, MonzoImportRuleResponse, MonzoImportRuleUpdateRequest


# TODO setup routes properly
def hardcoded_url_for(name: str, **kwargs):
    if name == "static" and not os.environ.get("LOCAL") == "true":
        # kwargs should contain 'path'
        path = kwargs.get("path", "")
        return f"https://benbanerjeerichards.com/finance/static{path}"
    # fallback for other endpoints if needed
    return f"/{name}/{kwargs.get("path", "")}"


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals['hardcoded_url_for'] = hardcoded_url_for

def create_fastapi(monzo_client: MonzoClient, minio_client: Minio, rmq_connection_string: str,
                   gc_connection: GcConnection, ledger_service: LedgerService) -> FastAPI:
    app = FastAPI(root_path=os.environ.get("make"), redirect_slashes=False)
    app.mount("/finance/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    def _get_channel() -> BlockingChannel:
        # just make new connection so we don't have to worry about maintaining
        # heartbeats
        return pika.BlockingConnection(pika.URLParameters(rmq_connection_string)).channel()

    @app.get("/finance/")
    async def index(request: Request):
        return templates.TemplateResponse("index.html",
                                          {"request": request, "start_monzo_url": monzo_client.get_start_ouath_url()})

    @app.get("/finance/success")
    async def success(request: Request):
        return templates.TemplateResponse("success.html", {"request": request})

    @app.get("/finance/monzo_redirect")
    async def monzo_redirect(request: Request):
        params = dict(request.query_params)
        access, refresh = monzo_client.exchange_code(params["code"])
        ImportService.update_monzo_tokens(params["state"], access, refresh)
        return templates.TemplateResponse("success.html", {"request": request})

    @app.post("/finance/monzo_partial_sync")
    async def monzo_partial_sync():
        _get_channel().basic_publish("", "monzo-sync-transactions",
                                     body=MonzoSyncMessage(past_days=89).model_dump_json())

    @app.post("/finance/update_ledger")
    async def update_ledger():
        _get_channel().basic_publish("", "update-ledger", body=MonzoSyncMessage(past_days=89).model_dump_json())

    @app.post("/finance/monzo_full_sync")
    async def monzo_full_sync():
        start = datetime.datetime(year=2018, month=1, day=1)
        now = datetime.datetime.now()
        days = (now - start).days
        _get_channel().basic_publish("", "monzo-sync-transactions",
                                     body=MonzoSyncMessage(past_days=days).model_dump_json())

    @app.get("/finance/start-requisition")
    async def start_gc_req():
        link_url = gc_connection.start_requisition()
        return RedirectResponse(link_url)

    @app.get("/finance/complete-requisition")
    async def start_gc_req(request: Request, ref: str):
        gc_connection.complete_requisition(ref)
        return templates.TemplateResponse("success.html", {"request": request})


    @app.get("/finance/transactions")
    async def get_transactions(params: GetTransactionsParams = Depends()):
        filters = TransactionFilters(**params.model_dump())
        txs = ledger_service.get_transactions(filters, params.cursor, params.count)
        return txs.model_dump()

    @app.get("/finance/transactions/{transaction_id}")
    async def get_transactions(transaction_id: uuid.UUID):
        tx = ledger_service.get_transaction(transaction_id)
        if not tx:
            raise HTTPException(status_code=404, detail="Transaction not found")
        return tx.model_dump()


    @app.get("/finance/payee")
    async def get_payee(params: GetPayeeParams = Depends()):
        filters = GetPayeeParams(**params.model_dump())
        payees =  ledger_service.get_payees(filters.filter)
        return {
            "payees": payees
        }

    @app.get("/finance/account")
    async def get_payee():
        return {
            "accounts": ledger_service.get_accounts()
        }

    @app.get("/finance/tag")
    async def get_payee():
        return {
            "tags": ledger_service.get_tags()
        }

    @app.get("/finance/balance")
    async def get_payee(params: GetBalanceParams = Depends()):
        filters = TransactionFilters(**params.model_dump())
        balances = ledger_service.get_balance(filters, params.account_types)
        return balances.model_dump()


    @app.get("/finance/balance_history")
    async def get_payee(params: GetBalanceHistoryParams = Depends()):
        filters = TransactionFilters(**params.model_dump())
        balances = ledger_service.get_balance_history(filters, params.account_types, params.period or "month")
        return balances.model_dump()

    @app.get("/finance/import_configuration")
    async def get_import_configurations():
        monzo_configs = [MonzoImportConfigResponse(**i.model_dump()) for i in ImportService.get_monzo_configs()]
        gc_configs = [GcImportConfigResponse(**i.model_dump()) for i in ImportService.get_gc_configs()]
        return {
            "monzo_configs": monzo_configs,
            "gocardless_configs": gc_configs
        }

    @app.get("/finance/import_configuration/{import_id}/rule")
    async def get_import_rules(import_id: uuid.UUID):
        rules = [MonzoImportRuleResponse(**r.model_dump()) for r in ImportService.get_monzo_import_rules(import_id)]
        return {
            "rules": rules
        }

    @app.put("/finance/import_configuration/{import_id}/rule")
    async def update_import_rules(import_id: uuid.UUID, update: MonzoImportRuleUpdateRequest):
        # creates and updates rules, never deletes - use the delete endpoint for that
        # priority is determined by list order, first item is highest priority
        rules = [MonzoImportRuleDto(**r.model_dump(), priority=0) for r in update.rules]
        ImportService.upsert_monzo_import_rules(import_id, rules)
        rules = [MonzoImportRuleResponse(**r.model_dump()) for r in ImportService.get_monzo_import_rules(import_id)]
        return {
            "rules": rules
        }

    @app.delete("/finance/import_configuration/{import_id}/rule/{rule_id}")
    async def delete_import_rule(import_id: uuid.UUID, rule_id: uuid.UUID):
        ImportService.delete_monzo_import_rule(rule_id)

    return app


async def main():
    app = create_fastapi(monzo_client=None, minio_client=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    config = uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="info", forwarded_allow_ips="*",
                            proxy_headers=True)
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__" and os.getenv("LOCAL") == "true":
    asyncio.run(main())
