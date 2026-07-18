"""timezone-aware

Revision ID: 33753725db89
Revises: 9bf1f84498c9
Create Date: 2026-07-18 01:53:45.337223

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '33753725db89'
down_revision: Union[str, Sequence[str], None] = '9bf1f84498c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Alter the column type using the raw SQL helper to ensure UTC interpretation
    op.execute(
        "ALTER TABLE transaction "
        "ALTER COLUMN transaction_datetime TYPE TIMESTAMPTZ "
        "USING transaction_datetime AT TIME ZONE 'UTC';"
    )


def downgrade() -> None:
    # 2. To revert, convert it back to a naive timestamp
    op.execute(
        "ALTER TABLE your_table_name "
        "ALTER COLUMN transaction_datetime TYPE TIMESTAMP WITHOUT TIME ZONE;"
    )