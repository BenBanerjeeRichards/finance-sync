import uuid

from fastapi import APIRouter, Depends, HTTPException

import dependencies
from db import get_db_session
from ledger.dto import TransactionDto, CreateTransactionDto
from ledger.ledger_service import (
    ImmutableTransactionException,
    TransactionNotFoundException,
    TransactionDoesNotBalanceException,
    LedgerService,
)
from ledger.repo import TransactionFilters
from web.model import GetTransactionsParams, GetPayeeParams, GetBalanceParams, GetBalanceHistoryParams

router = APIRouter(prefix="/finance", tags=["transactions"])
ledger_service = dependencies.get_ledger_service()


@router.get("/transactions")
async def get_transactions(params: GetTransactionsParams = Depends(), session=Depends(get_db_session)):
    filters = TransactionFilters(**params.model_dump())
    txs = ledger_service.get_transactions(session, filters, params.cursor, params.count)
    return txs.model_dump()


@router.get("/transactions/{transaction_id}")
async def get_transaction(transaction_id: uuid.UUID, session=Depends(get_db_session)):
    tx = ledger_service.get_transaction(session, transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return tx.model_dump()


@router.put("/transactions/{transaction_id}")
async def update_transaction(transaction_id: uuid.UUID, update: TransactionDto, session=Depends(get_db_session)):
    try:
        tx = ledger_service.update_transaction(session, update)
    except ImmutableTransactionException:
        raise HTTPException(status_code=404, detail="Can not update transaction")
    except TransactionNotFoundException:
        raise HTTPException(status_code=404, detail="Transaction not found")
    except TransactionDoesNotBalanceException:
        raise HTTPException(status_code=404, detail="Transaction does not balance")

    return tx.model_dump()


@router.post("/transactions/{transaction_id}")
async def create_transaction(transaction_id: uuid.UUID, create: CreateTransactionDto, session=Depends(get_db_session)):
    try:
        ledger_service.create_transaction(session, create)
    except TransactionDoesNotBalanceException:
        raise HTTPException(status_code=404, detail="Transaction does not balance")


@router.delete("/transactions/{transaction_id}")
async def delete_transaction(transaction_id: uuid.UUID, session=Depends(get_db_session)):
    try:
        LedgerService.safe_delete_transaction(session, transaction_id)
    except ImmutableTransactionException:
        raise HTTPException(status_code=404, detail="Can not update transaction")
    except TransactionNotFoundException:
        raise HTTPException(status_code=404, detail="Transaction not found")


@router.get("/payee")
async def get_payee(params: GetPayeeParams = Depends(), session=Depends(get_db_session)):
    filters = GetPayeeParams(**params.model_dump())
    payees = ledger_service.get_payees(session, filters.filter)
    return {
        "payees": payees
    }


@router.get("/tag")
async def get_tags(session=Depends(get_db_session)):
    return {
        "tags": ledger_service.get_tags(session)
    }


@router.get("/balance")
async def get_balance(params: GetBalanceParams = Depends(), session=Depends(get_db_session)):
    filters = TransactionFilters(**params.model_dump())
    balances = ledger_service.get_balance(session, filters, params.account_types)
    return balances.model_dump()


@router.get("/balance_history")
async def get_balance_history(params: GetBalanceHistoryParams = Depends(), session=Depends(get_db_session)):
    filters = TransactionFilters(**params.model_dump())
    balances = ledger_service.get_balance_history(session, filters, params.account_types, params.period or "month")
    return balances.model_dump()
