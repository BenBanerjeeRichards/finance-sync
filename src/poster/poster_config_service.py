import datetime
import uuid

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError

from ledger.model import PosterConfig
from main import Session
from model import AccrualConfig, EnergyConfig

POSTER_TYPES: dict[str, type[BaseModel]] = {
    "energy": EnergyConfig,
    "accrual": AccrualConfig,
}

SINGLETON_TYPES = {"energy"}


class UnknownPosterTypeError(ValueError):
    pass


class InvalidPosterConfigError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__(str(errors))


class PosterConfigNotFoundError(ValueError):
    pass


class DuplicatePosterConfigError(ValueError):
    pass


class PosterConfigDto(BaseModel):
    id: uuid.UUID
    type: str
    name: str
    enabled: bool
    config: dict
    created_at: datetime.datetime
    updated_at: datetime.datetime


def _validate_config(poster_type: str, config: dict) -> dict:
    model_cls = POSTER_TYPES.get(poster_type)
    if model_cls is None:
        raise UnknownPosterTypeError(
            f"Unknown poster type '{poster_type}', expected one of {sorted(POSTER_TYPES)}")
    try:
        validated = model_cls(**config)
    except ValidationError as e:
        raise InvalidPosterConfigError(e.errors())
    return validated.model_dump(mode="json")


def _to_dto(row: PosterConfig) -> PosterConfigDto:
    return PosterConfigDto(
        id=row.id,
        type=row.type,
        name=row.name,
        enabled=row.enabled,
        config=row.config,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PosterConfigService:

    @staticmethod
    def list_configs(poster_type: str | None = None) -> list[PosterConfigDto]:
        with Session.begin() as session:
            q = select(PosterConfig)
            if poster_type is not None:
                q = q.where(PosterConfig.type == poster_type)
            q = q.order_by(PosterConfig.type, PosterConfig.name)
            return [_to_dto(row) for row in session.execute(q).scalars()]

    @staticmethod
    def get_config(config_id: uuid.UUID) -> PosterConfigDto:
        with Session.begin() as session:
            row = session.get(PosterConfig, config_id)
            if row is None:
                raise PosterConfigNotFoundError(f"No poster config found for id {config_id}")
            return _to_dto(row)

    @staticmethod
    def create_config(poster_type: str, name: str, config: dict, enabled: bool = True) -> PosterConfigDto:
        validated_config = _validate_config(poster_type, config)

        if poster_type in SINGLETON_TYPES:
            name = poster_type

        with Session.begin() as session:
            row = PosterConfig(
                id=uuid.uuid4(),
                type=poster_type,
                name=name,
                enabled=enabled,
                config=validated_config,
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                raise DuplicatePosterConfigError(
                    f"A '{poster_type}' poster config named '{name}' already exists")
            session.refresh(row)
            return _to_dto(row)

    @staticmethod
    def update_config(config_id: uuid.UUID, name: str | None = None, config: dict | None = None,
                      enabled: bool | None = None) -> PosterConfigDto:
        with Session.begin() as session:
            row = session.get(PosterConfig, config_id)
            if row is None:
                raise PosterConfigNotFoundError(f"No poster config found for id {config_id}")

            if config is not None:
                row.config = _validate_config(row.type, config)
            if name is not None:
                row.name = row.type if row.type in SINGLETON_TYPES else name
            if enabled is not None:
                row.enabled = enabled

            try:
                session.flush()
            except IntegrityError:
                raise DuplicatePosterConfigError(
                    f"A '{row.type}' poster config named '{row.name}' already exists")
            session.refresh(row)
            return _to_dto(row)

    @staticmethod
    def delete_config(config_id: uuid.UUID) -> None:
        with Session.begin() as session:
            session.execute(delete(PosterConfig).where(PosterConfig.id == config_id))

    @staticmethod
    def get_energy_config() -> EnergyConfig | None:
        configs = PosterConfigService.list_configs("energy")
        enabled = [c for c in configs if c.enabled]
        if not enabled:
            return None
        return EnergyConfig(**enabled[0].config)

    @staticmethod
    def get_accrual_configs() -> list[AccrualConfig]:
        configs = PosterConfigService.list_configs("accrual")
        return [AccrualConfig(**c.config) for c in configs if c.enabled]
