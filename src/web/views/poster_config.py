import uuid

from fastapi import APIRouter, Depends, HTTPException

from db import get_db_session
from poster.poster_config_service import (
    PosterConfigService,
    DuplicatePosterConfigError,
    InvalidPosterConfigError,
    PosterConfigNotFoundError,
    UnknownPosterTypeError,
)
from web.model import PosterConfigResponse, PosterConfigCreateRequest, PosterConfigUpdateRequest

router = APIRouter(prefix="/finance", tags=["poster_config"])


@router.get("/poster_config")
async def list_poster_configs(type: str | None = None, session=Depends(get_db_session)):
    configs = PosterConfigService.list_configs(session, type)
    return {
        "poster_configs": [PosterConfigResponse(**c.model_dump()) for c in configs]
    }


@router.get("/poster_config/{config_id}")
async def get_poster_config(config_id: uuid.UUID, session=Depends(get_db_session)):
    try:
        config = PosterConfigService.get_config(session, config_id)
    except PosterConfigNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return PosterConfigResponse(**config.model_dump())


@router.post("/poster_config")
async def create_poster_config(create: PosterConfigCreateRequest, session=Depends(get_db_session)):
    try:
        config = PosterConfigService.create_config(
            session, create.type, create.name, create.config, create.enabled)
    except UnknownPosterTypeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except InvalidPosterConfigError as e:
        raise HTTPException(status_code=422, detail=e.errors)
    except DuplicatePosterConfigError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return PosterConfigResponse(**config.model_dump())


@router.put("/poster_config/{config_id}")
async def update_poster_config(config_id: uuid.UUID, update: PosterConfigUpdateRequest,
                               session=Depends(get_db_session)):
    try:
        config = PosterConfigService.update_config(
            session, config_id, update.name, update.config, update.enabled)
    except PosterConfigNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidPosterConfigError as e:
        raise HTTPException(status_code=422, detail=e.errors)
    except DuplicatePosterConfigError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return PosterConfigResponse(**config.model_dump())


@router.delete("/poster_config/{config_id}")
async def delete_poster_config(config_id: uuid.UUID, session=Depends(get_db_session)):
    PosterConfigService.delete_config(session, config_id)
