"""add_service_assignment_table

Revision ID: f1c2a3d4e5f6
Revises: e0ee54589690
Create Date: 2026-04-22 12:55:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f1c2a3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "e0ee54589690"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "service_assignment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("updated_on", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_on", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["operator_id"], ["operator.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_id"], ["service.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_id", "operator_id"),
    )
    op.create_index(
        op.f("ix_service_assignment_operator_id"),
        "service_assignment",
        ["operator_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_service_assignment_service_id"),
        "service_assignment",
        ["service_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_service_assignment_service_id"), table_name="service_assignment"
    )
    op.drop_index(
        op.f("ix_service_assignment_operator_id"), table_name="service_assignment"
    )
    op.drop_table("service_assignment")
