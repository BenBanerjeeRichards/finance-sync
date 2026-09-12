import uuid

from fastapi import APIRouter, Depends, HTTPException

import dependencies
from db import get_db_session
from ledger.ledger_service import AccountNotFoundException, DuplicateAccountException, LedgerService
from web.model import AccountCreateRequest, AccountUpdateRequest

router = APIRouter(prefix="/finance", tags=["accounts"])
ledger_service = dependencies.get_ledger_service()


@router.get("/account")
async def get_accounts(session=Depends(get_db_session)):
    return {
        "accounts": ledger_service.get_accounts(session)
    }


@router.post("/account")
async def create_account(create: AccountCreateRequest, session=Depends(get_db_session)):
    try:
        account = LedgerService.create_account(session, create.name, create.type, create.tags)
    except DuplicateAccountException as e:
        raise HTTPException(status_code=409, detail=str(e))
    return account.model_dump()


@router.put("/account/{account_id}")
async def update_account(account_id: uuid.UUID, update: AccountUpdateRequest, session=Depends(get_db_session)):
    try:
        account = LedgerService.update_account(session, account_id, update.name, update.tags)
    except AccountNotFoundException:
        raise HTTPException(status_code=404, detail="Account not found")
    except DuplicateAccountException as e:
        raise HTTPException(status_code=409, detail=str(e))
    return account.model_dump()
