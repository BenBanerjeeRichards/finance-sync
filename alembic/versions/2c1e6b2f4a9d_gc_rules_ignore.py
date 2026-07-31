"""gc_rules_ignore

Revision ID: 2c1e6b2f4a9d
Revises: 10eeeec539cc
Create Date: 2026-07-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2c1e6b2f4a9d'
down_revision: Union[str, Sequence[str], None] = '10eeeec539cc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('gocardless_import_rule', sa.Column('ignore', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('gocardless_import_rule', 'ignore')
