import uuid

from fastapi import APIRouter, Depends, HTTPException

from db import get_db_session
from ledger.account_alert_service import (
    AccountAlertService,
    AccountNotFoundException as AccountAlertAccountNotFoundException,
    AccountAlertNotFoundException,
    InvalidAccountAlertConditionException,
)
from web.model import AccountAlertResponse, AccountAlertCreateRequest, AccountAlertUpdateRequest

router = APIRouter(prefix="/finance", tags=["account_alert"])


@router.get("/account_alert")
async def list_account_alerts(account_id: uuid.UUID | None = None, session=Depends(get_db_session)):
    alerts = AccountAlertService.list_alerts(session, account_id)
    return {
        "account_alerts": [AccountAlertResponse(**a.model_dump()) for a in alerts]
    }


@router.get("/account_alert/{alert_id}")
async def get_account_alert(alert_id: uuid.UUID, session=Depends(get_db_session)):
    try:
        alert = AccountAlertService.get_alert(session, alert_id)
    except AccountAlertNotFoundException as e:
        raise HTTPException(status_code=404, detail=str(e))
    return AccountAlertResponse(**alert.model_dump())


@router.post("/account_alert")
async def create_account_alert(create: AccountAlertCreateRequest, session=Depends(get_db_session)):
    try:
        alert = AccountAlertService.create_alert(session, create.account_id, create.condition, create.amount)
    except AccountAlertAccountNotFoundException as e:
        raise HTTPException(status_code=400, detail=str(e))
    except InvalidAccountAlertConditionException as e:
        raise HTTPException(status_code=422, detail=str(e))
    return AccountAlertResponse(**alert.model_dump())


@router.put("/account_alert/{alert_id}")
async def update_account_alert(alert_id: uuid.UUID, update: AccountAlertUpdateRequest,
                               session=Depends(get_db_session)):
    try:
        alert = AccountAlertService.update_alert(session, alert_id, update.condition, update.amount)
    except AccountAlertNotFoundException as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidAccountAlertConditionException as e:
        raise HTTPException(status_code=422, detail=str(e))
    return AccountAlertResponse(**alert.model_dump())


@router.delete("/account_alert/{alert_id}")
async def delete_account_alert(alert_id: uuid.UUID, session=Depends(get_db_session)):
    AccountAlertService.delete_alert(session, alert_id)
