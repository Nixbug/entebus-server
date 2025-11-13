"""merge heads

Revision ID: f0c598116948
Revises: 0d0d1c677a5d, dfc1d7b10d1b
Create Date: 2025-11-13 10:41:22.798936

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f0c598116948'
down_revision: Union[str, Sequence[str], None] = ('0d0d1c677a5d', 'dfc1d7b10d1b')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
