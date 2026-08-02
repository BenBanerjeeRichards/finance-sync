import datetime
import uuid
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import update, select, delete, type_coerce
from sqlalchemy.dialects.postgresql import insert, JSONB

from ledger.model import MonzoImportIntegration, GoCardlessImportIntegration, MonzoImportRule, GoCardlessImportRule, \
    Account
from main import Session


class MonzoImportIntegrationDto(BaseModel):
    id: uuid.UUID
    access_token: str | None
    refresh_token: str | None
    client_id: str
    client_secret: str
    active_at: datetime.datetime | None = None
    cash_account_id: uuid.UUID | None = None
    default_income_account_id: uuid.UUID | None = None
    default_expense_account_id: uuid.UUID | None = None

class GcImportIntegrationDto(BaseModel):
    id: uuid.UUID
    secret_id: str | None
    secret_key: str | None
    requisition_expires_at: datetime.datetime | None = None
    # GC is now closed to new bank accounts so we don't need to support any more than santander
    kind: str = "santander"
    cash_account_id: uuid.UUID | None = None
    default_income_account_id: uuid.UUID | None = None
    default_expense_account_id: uuid.UUID | None = None


class MonzoImportRuleDto(BaseModel):
    id: uuid.UUID
    import_integration_id: uuid.UUID
    name: str
    priority: int
    account_id: uuid.UUID
    payee: str | None = None
    narration: str | None = None
    created_at: datetime.datetime | None = None
    category: str | None = None
    account_number: str | None = None
    tags: list[str] = []
    pot_id: str | None = None
    merchant_group_id: str | None = None
    counterparty_name: str | None = None
    transaction_id: uuid.UUID | None = None
    metadata: dict[str, str] = {}


class GcImportRuleDto(BaseModel):
    id: uuid.UUID
    import_integration_id: uuid.UUID
    name: str
    priority: int
    payee: str | None
    new_metadata: dict[str, str] = {}
    narration: str | None
    account_id: uuid.UUID
    credit_only: bool = False
    debit_only: bool = False
    ignore: bool = False
    transaction_type: str | None
    account_name_matches: str | None
    reference_name_matches: str | None
    amount_equals: Decimal | None


class UnknownAccountError(ValueError):
    pass


# don't bother with repo for now as this is so simple
class ImportService:

    @staticmethod
    def _validate_accounts_exist(session, account_ids: list[uuid.UUID | None]):
        # Accounts live in the ledger domain and there's no FK to enforce this, so check manually
        ids = {a for a in account_ids if a is not None}
        if not ids:
            return
        found = set(session.execute(select(Account.id).where(Account.id.in_(ids))).scalars())
        missing = ids - found
        if missing:
            raise UnknownAccountError(f"Unknown account id(s): {', '.join(str(m) for m in missing)}")

    @staticmethod
    def create_monzo_import_if_not_exists(client_id: str, client_secret: str, monzo_account_id: str):
        with Session.begin() as session:
            stmt = insert(MonzoImportIntegration).values(id=uuid.uuid4(), client_id=client_id,
                                                         client_secret=client_secret,
                                                         account_id=monzo_account_id)
            stmt = stmt.on_conflict_do_nothing(index_elements=["client_id"])
            session.execute(stmt)

    @staticmethod
    def create_gocardless_import_if_not_exists(secret_id: str, secret_key: str):
        with Session.begin() as session:
            stmt = insert(GoCardlessImportIntegration).values(id=uuid.uuid4(), secret_id=secret_id,
                                                              secret_key=secret_key)
            stmt = stmt.on_conflict_do_nothing(index_elements=["secret_id"])
            session.execute(stmt)

    @staticmethod
    def update_monzo_tokens(client_id: str, access_token: str, refresh_token: str):
        with Session.begin() as session:
            stmt = update(MonzoImportIntegration).where(MonzoImportIntegration.client_id == client_id).values(
                access_token=access_token, refresh_token=refresh_token,
                active_at=datetime.datetime.now(tz=datetime.timezone.utc))
            session.execute(stmt)

    @staticmethod
    def get_monzo_config(client_id: str) -> MonzoImportIntegrationDto:
        with Session.begin() as session:
            q = select(MonzoImportIntegration).where(
                MonzoImportIntegration.client_id == client_id
            )
            res = session.execute(q).scalar_one_or_none()

            if not res:
                raise ValueError(f"No monzo config found for client {client_id}")

            return MonzoImportIntegrationDto(
                id=res.id,
                client_id=res.client_id,
                client_secret=res.client_secret,
                access_token=res.access_token,
                refresh_token=res.refresh_token,
                active_at=res.active_at.isoformat() if res.active_at else None,
                cash_account_id=res.cash_account_id,
                default_income_account_id=res.default_income_account_id,
                default_expense_account_id=res.default_expense_account_id,
            )

    @staticmethod
    def get_monzo_configs() -> list[MonzoImportIntegrationDto]:
        with Session.begin() as session:
            q = select(MonzoImportIntegration)
            results = session.execute(q).scalars()

            return [MonzoImportIntegrationDto(
                id=res.id,
                client_id=res.client_id,
                client_secret=res.client_secret,
                access_token=res.access_token,
                refresh_token=res.refresh_token,
                active_at=res.active_at.isoformat() if res.active_at else None,
                cash_account_id=res.cash_account_id,
                default_income_account_id=res.default_income_account_id,
                default_expense_account_id=res.default_expense_account_id,
            ) for res in results]


    @staticmethod
    def get_gc_configs() -> list[GcImportIntegrationDto]:
        with Session.begin() as session:
            q = select(GoCardlessImportIntegration)
            results = session.execute(q).scalars()

            return [GcImportIntegrationDto(
                id=res.id,
                secret_id=res.secret_id,
                secret_key=res.secret_key,
                requisition_expires_at=res.requisition_expires_at,
                cash_account_id=res.cash_account_id,
                default_income_account_id=res.default_income_account_id,
                default_expense_account_id=res.default_expense_account_id,
            ) for res in results]


    @staticmethod
    def get_santander_config(secret_id: str) -> GcImportIntegrationDto:
        with Session.begin() as session:
            q = select(GoCardlessImportIntegration).where(
                GoCardlessImportIntegration.secret_id == secret_id
            )
            res = session.execute(q).scalar_one_or_none()

            if not res:
                raise ValueError(f"No santander config found for client {secret_id}")

            return GcImportIntegrationDto(
                id=res.id,
                secret_id=res.secret_id,
                secret_key=res.secret_key,
                requisition_expires_at=res.requisition_expires_at,
                cash_account_id=res.cash_account_id,
                default_income_account_id=res.default_income_account_id,
                default_expense_account_id=res.default_expense_account_id,
            )

    @staticmethod
    def update_santander_req_date(secret_id: str, dt: datetime.datetime):
        with Session.begin() as session:
            stmt = update(GoCardlessImportIntegration).where(GoCardlessImportIntegration.secret_id == secret_id).values(
                requisition_expires_at=dt)
            session.execute(stmt)

    @staticmethod
    def get_monzo_config_by_id(import_id: uuid.UUID) -> MonzoImportIntegrationDto:
        with Session.begin() as session:
            q = select(MonzoImportIntegration).where(MonzoImportIntegration.id == import_id)
            res = session.execute(q).scalar_one_or_none()

            if not res:
                raise ValueError(f"No monzo config found for id {import_id}")

            return MonzoImportIntegrationDto(
                id=res.id,
                client_id=res.client_id,
                client_secret=res.client_secret,
                access_token=res.access_token,
                refresh_token=res.refresh_token,
                active_at=res.active_at,
                cash_account_id=res.cash_account_id,
                default_income_account_id=res.default_income_account_id,
                default_expense_account_id=res.default_expense_account_id,
            )

    @staticmethod
    def get_gc_config_by_id(import_id: uuid.UUID) -> GcImportIntegrationDto:
        with Session.begin() as session:
            q = select(GoCardlessImportIntegration).where(GoCardlessImportIntegration.id == import_id)
            res = session.execute(q).scalar_one_or_none()

            if not res:
                raise ValueError(f"No gc config found for id {import_id}")

            return GcImportIntegrationDto(
                id=res.id,
                secret_id=res.secret_id,
                secret_key=res.secret_key,
                requisition_expires_at=res.requisition_expires_at,
                cash_account_id=res.cash_account_id,
                default_income_account_id=res.default_income_account_id,
                default_expense_account_id=res.default_expense_account_id,
            )

    @staticmethod
    def update_monzo_config(import_id: uuid.UUID, cash_account_id: uuid.UUID | None,
                            default_income_account_id: uuid.UUID | None,
                            default_expense_account_id: uuid.UUID | None):
        with Session.begin() as session:
            ImportService._validate_accounts_exist(
                session, [cash_account_id, default_income_account_id, default_expense_account_id])
            stmt = update(MonzoImportIntegration).where(MonzoImportIntegration.id == import_id).values(
                cash_account_id=cash_account_id,
                default_income_account_id=default_income_account_id,
                default_expense_account_id=default_expense_account_id,
            )
            session.execute(stmt)

    @staticmethod
    def update_gc_config(import_id: uuid.UUID, cash_account_id: uuid.UUID | None,
                         default_income_account_id: uuid.UUID | None,
                         default_expense_account_id: uuid.UUID | None):
        with Session.begin() as session:
            ImportService._validate_accounts_exist(
                session, [cash_account_id, default_income_account_id, default_expense_account_id])
            stmt = update(GoCardlessImportIntegration).where(GoCardlessImportIntegration.id == import_id).values(
                cash_account_id=cash_account_id,
                default_income_account_id=default_income_account_id,
                default_expense_account_id=default_expense_account_id,
            )
            session.execute(stmt)

    @staticmethod
    def get_import_config(import_id: uuid.UUID) -> tuple[Literal["monzo", "gc"], MonzoImportIntegrationDto | GcImportIntegrationDto]:
        kind = ImportService.get_import_rule_type(import_id)
        if kind == "monzo":
            return kind, ImportService.get_monzo_config_by_id(import_id)
        if kind == "gc":
            return kind, ImportService.get_gc_config_by_id(import_id)
        raise ValueError(f"No import configuration found for id {import_id}")

    @staticmethod
    def update_import_config(import_id: uuid.UUID, cash_account_id: uuid.UUID | None,
                             default_income_account_id: uuid.UUID | None,
                             default_expense_account_id: uuid.UUID | None
                             ) -> tuple[Literal["monzo", "gc"], MonzoImportIntegrationDto | GcImportIntegrationDto]:
        kind = ImportService.get_import_rule_type(import_id)
        if kind == "monzo":
            ImportService.update_monzo_config(import_id, cash_account_id, default_income_account_id,
                                              default_expense_account_id)
            return kind, ImportService.get_monzo_config_by_id(import_id)
        if kind == "gc":
            ImportService.update_gc_config(import_id, cash_account_id, default_income_account_id,
                                           default_expense_account_id)
            return kind, ImportService.get_gc_config_by_id(import_id)
        raise ValueError(f"No import configuration found for id {import_id}")

    def get_monzo_import_rules(self, import_integration_id: uuid.UUID) -> list[MonzoImportRuleDto]:
        with Session.begin() as session:
            q = select(MonzoImportRule).where(
                MonzoImportRule.import_integration_id == import_integration_id
            ).order_by(MonzoImportRule.priority)
            results = session.execute(q).scalars()

            return [MonzoImportRuleDto(
                id=r.id,
                import_integration_id=r.import_integration_id,
                name=r.name,
                priority=r.priority,
                account_id=r.account_id,
                payee=r.payee,
                narration=r.narration,
                created_at=r.created_at,
                category=r.category,
                account_number=r.account_number,
                tags=r.tags,
                pot_id=r.pot_id,
                merchant_group_id=r.merchant_group_id,
                counterparty_name=r.counterparty_name,
                transaction_id=r.transaction_id,
                metadata=r.new_metadata,
            ) for r in results]

    @staticmethod
    def get_gc_import_rules(import_integration_id: uuid.UUID) -> list[GcImportRuleDto]:
        with Session.begin() as session:
            q = select(GoCardlessImportRule).where(
                GoCardlessImportRule.import_integration_id == import_integration_id
            ).order_by(GoCardlessImportRule.priority)
            results = session.execute(q).scalars()

            return [GcImportRuleDto(
                id=r.id,
                import_integration_id=r.import_integration_id,
                name=r.name,
                priority=r.priority,
                new_metadata=r.new_metadata,
                payee=r.payee,
                narration=r.narration,
                account_id=r.account_id,
                debit_only=r.debit_only,
                credit_only=r.credit_only,
                ignore=r.ignore,
                transaction_type=r.transaction_type,
                account_name_matches=r.account_name_matches,
                reference_name_matches=r.reference_name_matches,
                amount_equals=r.amount_equals,
            ) for r in results]

    @staticmethod
    def upsert_gc_import_rules(import_integration_id: uuid.UUID, rules: list[GcImportRuleDto]):
        with Session.begin() as session:
            ImportService._validate_accounts_exist(session, [rule.account_id for rule in rules])
            for priority, rule in enumerate(rules):
                stmt = insert(GoCardlessImportRule).values(
                    id=rule.id,
                    import_integration_id=rule.import_integration_id,
                    name=rule.name,
                    priority=priority,
                    new_metadata=rule.new_metadata,
                    payee=rule.payee,
                    narration=rule.narration,
                    account_id=rule.account_id,
                    debit_only=rule.debit_only,
                    credit_only=rule.credit_only,
                    ignore=rule.ignore,
                    transaction_type=rule.transaction_type,
                    account_name_matches=rule.account_name_matches,
                    reference_name_matches=rule.reference_name_matches,
                    amount_equals=rule.amount_equals,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_=dict(
                        import_integration_id=import_integration_id,
                        name=rule.name,
                        priority=priority,
                        metadata=type_coerce(rule.new_metadata, JSONB),
                        payee=rule.payee,
                        narration=rule.narration,
                        account_id=rule.account_id,
                        debit_only=rule.debit_only,
                        credit_only=rule.credit_only,
                        ignore=rule.ignore,
                        transaction_type=rule.transaction_type,
                        account_name_matches=rule.account_name_matches,
                        reference_name_matches=rule.reference_name_matches,
                        amount_equals=rule.amount_equals,
                    )
                )
                session.execute(stmt)

    @staticmethod
    def upsert_monzo_import_rules(import_integration_id: uuid.UUID, rules: list[MonzoImportRuleDto]):
        # priority is determined by list order, first item is highest priority
        # this creates and updates rules, but never deletes - use delete_monzo_import_rule for that
        with Session.begin() as session:
            ImportService._validate_accounts_exist(session, [rule.account_id for rule in rules])
            for priority, rule in enumerate(rules):
                stmt = insert(MonzoImportRule).values(
                    id=rule.id,
                    import_integration_id=import_integration_id,
                    name=rule.name,
                    priority=priority,
                    account_id=rule.account_id,
                    payee=rule.payee,
                    narration=rule.narration,
                    created_at=rule.created_at,
                    category=rule.category,
                    account_number=rule.account_number,
                    tags=rule.tags,
                    pot_id=rule.pot_id,
                    merchant_group_id=rule.merchant_group_id,
                    counterparty_name=rule.counterparty_name,
                    transaction_id=rule.transaction_id,
                    new_metadata=type_coerce(rule.metadata, JSONB),
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_=dict(
                        import_integration_id=import_integration_id,
                        name=rule.name,
                        priority=priority,
                        account_id=rule.account_id,
                        payee=rule.payee,
                        narration=rule.narration,
                        created_at=rule.created_at,
                        category=rule.category,
                        account_number=rule.account_number,
                        tags=rule.tags,
                        pot_id=rule.pot_id,
                        merchant_group_id=rule.merchant_group_id,
                        counterparty_name=rule.counterparty_name,
                        transaction_id=rule.transaction_id,
                        metadata=type_coerce(rule.metadata, JSONB),
                    )
                )
                session.execute(stmt)

    @staticmethod
    def delete_monzo_import_rule(rule_id: uuid.UUID):
        with Session.begin() as session:
            stmt = delete(MonzoImportRule).where(MonzoImportRule.id == rule_id)
            session.execute(stmt)

    @staticmethod
    def delete_gc_import_rule(rule_id: uuid.UUID):
        with Session.begin() as session:
            stmt = delete(GoCardlessImportRule).where(GoCardlessImportRule.id == rule_id)
            session.execute(stmt)

    def get_import_rules(self, import_id: uuid.UUID) -> tuple[Literal["monzo", "gc"], list[MonzoImportRuleDto] | list[GcImportRuleDto]]:
        kind = ImportService.get_import_rule_type(import_id)
        if kind == "monzo":
            return kind, self.get_monzo_import_rules(import_id)
        if kind == "gc":
            return kind, self.get_gc_import_rules(import_id)
        raise ValueError(f"No import rules found for id {import_id}")

    @staticmethod
    def delete_import_rule(import_id: uuid.UUID, rule_id: uuid.UUID):
        kind = ImportService.get_import_rule_type(import_id)
        if kind == "monzo":
            ImportService.delete_monzo_import_rule(rule_id)
        elif kind == "gc":
            ImportService.delete_gc_import_rule(rule_id)

    @staticmethod
    def get_import_rule_type(import_id: uuid.UUID) -> Literal["gc", "monzo"] | None:
        with Session.begin() as session:
            q = select(MonzoImportIntegration).where(MonzoImportIntegration.id == import_id)
            res = session.execute(q).scalar_one_or_none()
            if res:
                return "monzo"
            q2 = select(GoCardlessImportIntegration).where(GoCardlessImportIntegration.id == import_id)
            res2 = session.execute(q2).scalar_one_or_none()
            if res2:
                return "gc"
        return None