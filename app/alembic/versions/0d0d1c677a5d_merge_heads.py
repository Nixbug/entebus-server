"""merge heads

Revision ID: 0d0d1c677a5d
Revises: 29344cfda993, 4cd24675b1e9
Create Date: 2025-10-29 06:22:08.706585

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0d0d1c677a5d'
down_revision: Union[str, Sequence[str], None] = ('29344cfda993', '4cd24675b1e9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
