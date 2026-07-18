"""search_index

Revision ID: 9bf1f84498c9
Revises: 5f8004f024eb
Create Date: 2026-07-13 21:26:25.033636

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9bf1f84498c9'
down_revision: Union[str, Sequence[str], None] = '5f8004f024eb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("""
            CREATE OR REPLACE FUNCTION immutable_transaction_search_vector(
                payee TEXT, 
                narration TEXT, 
                tags TEXT[]
            ) RETURNS TEXT AS $$
            BEGIN
                RETURN coalesce(payee, '') || ' ' || 
                       coalesce(narration, '') || ' ' || 
                       coalesce(array_to_string(tags, ' '), '');
            END;
            $$ LANGUAGE plpgsql IMMUTABLE;
        """)

    op.execute("""
            CREATE INDEX idx_transactions_search_trgm ON transaction 
            USING gin (
              immutable_transaction_search_vector(payee, narration, tags) 
              gin_trgm_ops
            );
        """)
def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS idx_transactions_search_trgm;")