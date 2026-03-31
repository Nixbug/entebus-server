"""add_vehicle_image_table

Revision ID: f3a1b2c4d5e6
Revises: e0ee54589690
Create Date: 2026-03-31 05:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a1b2c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'e0ee54589690'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('vehicle_image',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('vehicle_id', sa.Integer(), nullable=False),
    sa.Column('file_name', sa.String(length=128), nullable=False),
    sa.Column('file_size', sa.Integer(), nullable=False),
    sa.Column('file_type', sa.String(length=128), nullable=False),
    sa.Column('created_on', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['company.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicle.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_vehicle_image_company_id'), 'vehicle_image', ['company_id'], unique=False)
    op.create_index(op.f('ix_vehicle_image_vehicle_id'), 'vehicle_image', ['vehicle_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_vehicle_image_vehicle_id'), table_name='vehicle_image')
    op.drop_index(op.f('ix_vehicle_image_company_id'), table_name='vehicle_image')
    op.drop_table('vehicle_image')
