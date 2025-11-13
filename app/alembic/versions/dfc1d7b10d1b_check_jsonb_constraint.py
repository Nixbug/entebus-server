"""Add JSONB not-null constraint to executive_role.permissions

Revision ID: dfc1d7b10d1b
Revises: 29344cfda993
Create Date: 2025-11-13 09:36:37.775953
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# Revision identifiers, used by Alembic.
revision: str = "dfc1d7b10d1b"
down_revision: Union[str, Sequence[str], None] = "29344cfda993"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: add check constraint on executive_role.permissions."""
    # Add constraint to ensure permissions is not 'null'::jsonb
    op.create_check_constraint(
        constraint_name="executive_permissions_null",
        table_name="executive_role",
        condition="permissions <> 'null'::jsonb",
    )


def downgrade() -> None:
    """Downgrade schema: remove the check constraint."""
    op.drop_constraint(
        constraint_name="executive_permissions_null",
        table_name="executive_role",
        type_="check",
    )
