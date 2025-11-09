import asyncio
import datetime
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from minio import Minio
from pika.adapters.blocking_connection import BlockingConnection
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from model import MonzoStore, MonzoSyncMessage
from monzo import MonzoClient
from storage import write_monzo_store


# I have wasted too much of my life trying to get k8s, nginx, fastapi to work and they don't
# so instead of trying to debug this impossible mess I am hardcoding this
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

def create_fastapi(monzo_client: MonzoClient, minio_client: Minio, rmq: BlockingConnection) -> FastAPI:
    app = FastAPI(root_path=os.environ.get("make"),redirect_slashes=False)
    app.mount("/finance/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    @app.get("/finance/")
    async def index(request: Request):
        return templates.TemplateResponse("index.html", {"request": request, "start_monzo_url": monzo_client.get_start_ouath_url()})

    @app.get("/finance/success")
    async def success(request: Request):
        return templates.TemplateResponse("success.html", {"request": request})

    @app.get("/finance/monzo_redirect")
    async def monzo_redirect(request: Request):
        params = dict(request.query_params)
        access, refresh = monzo_client.exchange_code(params["code"])
        store = MonzoStore(access_token=access, refresh_token=refresh)
        write_monzo_store(minio_client, store)
        return templates.TemplateResponse("success.html", {"request": request})

    @app.post("/finance/monzo_partial_sync")
    async def monzo_partial_sync():
        ch = rmq.channel()
        ch.basic_publish("", 	"monzo-sync-transactions", body=MonzoSyncMessage(past_days=89).model_dump_json())

    @app.post("/finance/monzo_full_sync")
    async def monzo_partial_sync():
        ch = rmq.channel()
        start = datetime.datetime(year=2018, month=1, day=1)
        now = datetime.datetime.now()
        days = (now - start).days
        ch.basic_publish("", 	"monzo-sync-transactions", body=MonzoSyncMessage(past_days=days).model_dump_json())

    return app

async def main():
    app = create_fastapi(monzo_client=None, minio_client=None)
    config = uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="info", forwarded_allow_ips="*", proxy_headers=True)
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__" and os.getenv("LOCAL") == "true":
    asyncio.run(main())

