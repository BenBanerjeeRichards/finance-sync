import datetime

import pika
from fastapi import APIRouter
from pika.adapters.blocking_connection import BlockingChannel

import dependencies
from model import MonzoSyncMessage

router = APIRouter(prefix="/finance", tags=["sync"])


def _get_channel() -> BlockingChannel:
    # just make new connection so we don't have to worry about maintaining
    # heartbeats
    return pika.BlockingConnection(pika.URLParameters(dependencies.get_settings().rabbitmq_connection_string)).channel()


@router.post("/monzo_partial_sync")
async def monzo_partial_sync():
    _get_channel().basic_publish("", "monzo-sync-transactions",
                                 body=MonzoSyncMessage(past_days=89).model_dump_json())


@router.post("/update_ledger")
async def update_ledger():
    _get_channel().basic_publish("", "update-ledger", body=MonzoSyncMessage(past_days=89).model_dump_json())


@router.post("/monzo_full_sync")
async def monzo_full_sync():
    start = datetime.datetime(year=2018, month=1, day=1)
    now = datetime.datetime.now()
    days = (now - start).days
    _get_channel().basic_publish("", "monzo-sync-transactions",
                                 body=MonzoSyncMessage(past_days=days).model_dump_json())
