import uuid
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select, delete

from ledger.model import AccountAlerts, Account

AccountAlertCondition = Literal["above", "below"]
ACCOUNT_ALERT_CONDITIONS: set[str] = {"above", "below"}


class AccountNotFoundException(Exception):
    pass


class AccountAlertNotFoundException(Exception):
    pass


class InvalidAccountAlertConditionException(ValueError):
    pass


class AccountAlertDto(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    condition: AccountAlertCondition
    amount: Decimal


def _validate_condition(condition: str) -> None:
    if condition not in ACCOUNT_ALERT_CONDITIONS:
        raise InvalidAccountAlertConditionException(
            f"Unknown condition '{condition}', expected one of {sorted(ACCOUNT_ALERT_CONDITIONS)}")


def _to_dto(row: AccountAlerts) -> AccountAlertDto:
    return AccountAlertDto(
        id=row.id,
        account_id=row.account_id,
        condition=row.condition,
        amount=row.amount,
    )


class AccountAlertService:

    @staticmethod
    def list_alerts(session, account_id: uuid.UUID | None = None) -> list[AccountAlertDto]:
        q = select(AccountAlerts)
        if account_id is not None:
            q = q.where(AccountAlerts.account_id == account_id)
        return [_to_dto(row) for row in session.execute(q).scalars()]

    @staticmethod
    def get_alert(session, alert_id: uuid.UUID) -> AccountAlertDto:
        row = session.get(AccountAlerts, alert_id)
        if row is None:
            raise AccountAlertNotFoundException(f"No account alert found for id {alert_id}")
        return _to_dto(row)

    @staticmethod
    def create_alert(session, account_id: uuid.UUID, condition: str, amount: Decimal) -> AccountAlertDto:
        _validate_condition(condition)
        if session.get(Account, account_id) is None:
            raise AccountNotFoundException(f"No account found for id {account_id}")

        row = AccountAlerts(
            id=uuid.uuid4(),
            account_id=account_id,
            condition=condition,
            amount=amount,
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        return _to_dto(row)

    @staticmethod
    def update_alert(session, alert_id: uuid.UUID, condition: str | None = None,
                      amount: Decimal | None = None) -> AccountAlertDto:
        row = session.get(AccountAlerts, alert_id)
        if row is None:
            raise AccountAlertNotFoundException(f"No account alert found for id {alert_id}")

        if condition is not None:
            _validate_condition(condition)
            row.condition = condition
        if amount is not None:
            row.amount = amount

        session.flush()
        session.refresh(row)
        return _to_dto(row)

    @staticmethod
    def delete_alert(session, alert_id: uuid.UUID) -> None:
        session.execute(delete(AccountAlerts).where(AccountAlerts.id == alert_id))
