"""update_ex_role

Revision ID: 29344cfda993
Revises: b9592064514c
Create Date: 2025-10-28 04:12:29.990350

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "29344cfda993"
down_revision: Union[str, Sequence[str], None] = "b9592064514c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: safely convert ARRAY(String) -> JSONB."""
    # Convert permissions array to JSONB
    op.execute(
        """
    	ALTER TABLE executive_role
    	ALTER COLUMN permissions TYPE JSONB
    	USING to_jsonb(permissions);
    """
    )


def downgrade() -> None:
    """Downgrade schema: revert JSONB -> ARRAY(String)."""
    op.execute(
        """
    	ALTER TABLE executive_role
    	ALTER COLUMN permissions TYPE VARCHAR[]
    	USING array(
    		SELECT jsonb_array_elements_text(permissions)
    	);
    """
    )
