import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException

import dependencies
from db import get_db_session
from importer.import_service import ImportService, MonzoImportRuleDto, GcImportRuleDto, UnknownAccountError
from web.model import (
    MonzoImportConfigResponse,
    GcImportConfigResponse,
    ImportConfigUpdateRequest,
    MonzoImportRuleResponse,
    GcImportRuleResponse,
    MonzoImportRuleUpdateRequest,
    GcImportRuleUpdateRequest,
)

router = APIRouter(prefix="/finance", tags=["import_configuration"])
import_service = dependencies.get_import_service()


@router.get("/import_configuration")
async def get_import_configurations(session=Depends(get_db_session)):
    monzo_configs = [MonzoImportConfigResponse(**i.model_dump()) for i in ImportService.get_monzo_configs(session)]
    gc_configs = [GcImportConfigResponse(**i.model_dump()) for i in ImportService.get_gc_configs(session)]
    return {
        "monzo_configs": monzo_configs,
        "gocardless_configs": gc_configs
    }


@router.put("/import_configuration/{import_id}")
async def update_import_configuration(import_id: uuid.UUID, update: ImportConfigUpdateRequest,
                                      session=Depends(get_db_session)):
    try:
        kind, config = ImportService.update_import_config(
            session, import_id, update.cash_account_id, update.default_income_account_id,
            update.default_expense_account_id)
    except UnknownAccountError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError:
        raise HTTPException(status_code=404, detail="Import configuration not found")

    response_cls = MonzoImportConfigResponse if kind == "monzo" else GcImportConfigResponse
    return response_cls(**config.model_dump())


@router.get("/import_configuration/{import_id}/rule")
async def get_import_rules(import_id: uuid.UUID, session=Depends(get_db_session)):
    try:
        kind, rules = import_service.get_import_rules(session, import_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Rule not found")

    response_cls = MonzoImportRuleResponse if kind == "monzo" else GcImportRuleResponse
    return {
        "rules": [response_cls(**r.model_dump()) for r in rules]
    }


@router.put("/import_configuration/{import_id}/rule")
async def update_import_rules(import_id: uuid.UUID, update: dict, session=Depends(get_db_session)):
    # creates and updates rules, never deletes - use the delete endpoint for that
    # priority is determined by list order, first item is highest priority
    kind = ImportService.get_import_rule_type(session, import_id)
    try:
        if kind == "monzo":
            logging.info("updating monzo rules %s", import_id)
            update = MonzoImportRuleUpdateRequest(**update)
            rules = [MonzoImportRuleDto(**r.model_dump(), priority=0) for r in update.rules]
            ImportService.upsert_monzo_import_rules(session, import_id, rules)
            rules = [MonzoImportRuleResponse(**r.model_dump()) for r in
                    import_service.get_monzo_import_rules(session, import_id)]
        else:
            logging.info("updating gc monzo rules %s", import_id)
            update = GcImportRuleUpdateRequest(**update)
            rules = [GcImportRuleDto(**r.model_dump(), priority=0) for r in update.rules]
            ImportService.upsert_gc_import_rules(session, import_id, rules)
            rules = [GcImportRuleResponse(**r.model_dump()) for r in
                    ImportService.get_gc_import_rules(session, import_id)]
    except UnknownAccountError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "rules": rules
    }


@router.delete("/import_configuration/{import_id}/rule/{rule_id}")
async def delete_import_rule(import_id: uuid.UUID, rule_id: uuid.UUID, session=Depends(get_db_session)):
    ImportService.delete_import_rule(session, import_id, rule_id)
