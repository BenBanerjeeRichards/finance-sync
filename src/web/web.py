import os
import uuid
from pathlib import Path

import pika
from fastapi import FastAPI, Request, Depends, HTTPException
from pika.adapters.blocking_connection import BlockingChannel
from starlette.responses import RedirectResponse
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

import dependencies
from importer.import_service import ImportService, MonzoImportRuleDto, GcImportRuleDto, UnknownAccountError
from ledger.repo import TransactionFilters
from model import MonzoSyncMessage
from poster.poster_config_service import (
    PosterConfigService,
    DuplicatePosterConfigError,
    InvalidPosterConfigError,
    PosterConfigNotFoundError,
    UnknownPosterTypeError,
)
from web.model import *
import logging

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

def create_fastapi() -> FastAPI:
    app = FastAPI(root_path=os.environ.get("make"), redirect_slashes=False)
    app.mount("/finance/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    import_service = dependencies.get_import_service()
    ledger_service = dependencies.get_ledger_service()
    def _get_channel() -> BlockingChannel:
        # just make new connection so we don't have to worry about maintaining
        # heartbeats
        return pika.BlockingConnection(pika.URLParameters(dependencies.get_settings().rabbitmq_connection_string)).channel()

    @app.get("/finance/")
    async def index(request: Request):
        return templates.TemplateResponse("index.html",
                                          {"request": request, "start_monzo_url": dependencies.get_monzo_client().get_start_ouath_url()})

    @app.get("/finance/success")
    async def success(request: Request):
        return templates.TemplateResponse("success.html", {"request": request})

    @app.get("/finance/monzo_redirect")
    async def monzo_redirect(request: Request):
        params = dict(request.query_params)
        access, refresh = dependencies.get_monzo_client().exchange_code(params["code"])
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
        link_url = dependencies.get_gc_connection().start_requisition()
        return RedirectResponse(link_url)

    @app.get("/finance/complete-requisition")
    async def start_gc_req(request: Request, ref: str):
        dependencies.get_gc_connection().complete_requisition(ref)
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
    async def get_accounts():
        return {
            "accounts": ledger_service.get_accounts()
        }

    @app.get("/finance/tag")
    async def get_tags():
        return {
            "tags": ledger_service.get_tags()
        }

    @app.get("/finance/balance")
    async def get_balance(params: GetBalanceParams = Depends()):
        filters = TransactionFilters(**params.model_dump())
        balances = ledger_service.get_balance(filters, params.account_types)
        return balances.model_dump()


    @app.get("/finance/balance_history")
    async def get_balance_history(params: GetBalanceHistoryParams = Depends()):
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

    @app.put("/finance/import_configuration/{import_id}")
    async def update_import_configuration(import_id: uuid.UUID, update: ImportConfigUpdateRequest):
        try:
            kind, config = ImportService.update_import_config(
                import_id, update.cash_account_id, update.default_income_account_id, update.default_expense_account_id)
        except UnknownAccountError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except ValueError:
            raise HTTPException(status_code=404, detail="Import configuration not found")

        response_cls = MonzoImportConfigResponse if kind == "monzo" else GcImportConfigResponse
        return response_cls(**config.model_dump())

    @app.get("/finance/import_configuration/{import_id}/rule")
    async def get_import_rules(import_id: uuid.UUID):
        try:
            kind, rules = import_service.get_import_rules(import_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="Rule not found")

        response_cls = MonzoImportRuleResponse if kind == "monzo" else GcImportRuleResponse
        return {
            "rules": [response_cls(**r.model_dump()) for r in rules]
        }

    @app.put("/finance/import_configuration/{import_id}/rule")
    async def update_import_rules(import_id: uuid.UUID, update: dict):
        # creates and updates rules, never deletes - use the delete endpoint for that
        # priority is determined by list order, first item is highest priority
        kind = ImportService.get_import_rule_type(import_id)
        try:
            if kind == "monzo":
                logging.info("updating monzo rules %s", import_id)
                update = MonzoImportRuleUpdateRequest(**update)
                rules = [MonzoImportRuleDto(**r.model_dump(), priority=0) for r in update.rules]
                ImportService.upsert_monzo_import_rules(import_id, rules)
                rules = [MonzoImportRuleResponse(**r.model_dump()) for r in import_service.get_monzo_import_rules(import_id)]
            else:
                logging.info("updating gc monzo rules %s", import_id)
                update = GcImportRuleUpdateRequest(**update)
                rules = [GcImportRuleDto(**r.model_dump(), priority=0) for r in update.rules]
                ImportService.upsert_gc_import_rules(import_id, rules)
                rules = [GcImportRuleResponse(**r.model_dump()) for r in ImportService.get_gc_import_rules(import_id)]
        except UnknownAccountError as e:
            raise HTTPException(status_code=400, detail=str(e))

        return {
            "rules": rules
        }

    @app.delete("/finance/import_configuration/{import_id}/rule/{rule_id}")
    async def delete_import_rule(import_id: uuid.UUID, rule_id: uuid.UUID):
        ImportService.delete_import_rule(import_id, rule_id)


    @app.get("/finance/poster_config")
    async def list_poster_configs(type: str | None = None):
        configs = PosterConfigService.list_configs(type)
        return {
            "poster_configs": [PosterConfigResponse(**c.model_dump()) for c in configs]
        }

    @app.get("/finance/poster_config/{config_id}")
    async def get_poster_config(config_id: uuid.UUID):
        try:
            config = PosterConfigService.get_config(config_id)
        except PosterConfigNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return PosterConfigResponse(**config.model_dump())

    @app.post("/finance/poster_config")
    async def create_poster_config(create: PosterConfigCreateRequest):
        try:
            config = PosterConfigService.create_config(
                create.type, create.name, create.config, create.enabled)
        except UnknownPosterTypeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except InvalidPosterConfigError as e:
            raise HTTPException(status_code=422, detail=e.errors)
        except DuplicatePosterConfigError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return PosterConfigResponse(**config.model_dump())

    @app.put("/finance/poster_config/{config_id}")
    async def update_poster_config(config_id: uuid.UUID, update: PosterConfigUpdateRequest):
        try:
            config = PosterConfigService.update_config(
                config_id, update.name, update.config, update.enabled)
        except PosterConfigNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except InvalidPosterConfigError as e:
            raise HTTPException(status_code=422, detail=e.errors)
        except DuplicatePosterConfigError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return PosterConfigResponse(**config.model_dump())

    @app.delete("/finance/poster_config/{config_id}")
    async def delete_poster_config(config_id: uuid.UUID):
        PosterConfigService.delete_config(config_id)


    return app

