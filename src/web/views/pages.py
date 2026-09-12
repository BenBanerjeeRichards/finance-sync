from fastapi import APIRouter, Request, Depends
from starlette.responses import RedirectResponse

import dependencies
from importer.import_service import ImportService
from db import get_db_session
from web.templating import templates

router = APIRouter(prefix="/finance", tags=["pages"])


@router.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html",
                                      {"request": request, "start_monzo_url": dependencies.get_monzo_client().get_start_ouath_url()})


@router.get("/success")
async def success(request: Request):
    return templates.TemplateResponse("success.html", {"request": request})


@router.get("/monzo_redirect")
async def monzo_redirect(request: Request, session=Depends(get_db_session)):
    params = dict(request.query_params)
    access, refresh = dependencies.get_monzo_client().exchange_code(params["code"])
    ImportService.update_monzo_tokens(session, params["state"], access, refresh)
    return templates.TemplateResponse("success.html", {"request": request})


@router.get("/start-requisition")
async def start_gc_req():
    link_url = dependencies.get_gc_connection().start_requisition()
    return RedirectResponse(link_url)


@router.get("/complete-requisition")
async def complete_gc_req(request: Request, ref: str):
    dependencies.get_gc_connection().complete_requisition(ref)
    return templates.TemplateResponse("success.html", {"request": request})
