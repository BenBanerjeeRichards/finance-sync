import os
from pathlib import Path

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from web.views import (
    pages,
    sync,
    transactions,
    accounts,
    import_configuration,
    account_alert,
    poster_config,
)

BASE_DIR = Path(__file__).resolve().parent


def create_fastapi() -> FastAPI:
    app = FastAPI(root_path=os.environ.get("make"), redirect_slashes=False)
    app.mount("/finance/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    app.include_router(pages.router)
    app.include_router(sync.router)
    app.include_router(transactions.router)
    app.include_router(accounts.router)
    app.include_router(import_configuration.router)
    app.include_router(account_alert.router)
    app.include_router(poster_config.router)

    return app
