"""add shipping address to sales_opportunity

The New Opportunity form captures a billing address and a separate shipping
address. The existing address columns are the billing address; these add
somewhere for the shipping address to live so the form does not silently
discard it. Leads capture a single address, so they are not touched.

Revision ID: c4e81f2a6b93
Revises: b7c3d91e5a20
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4e81f2a6b93"
down_revision: Union[str, Sequence[str], None] = "b7c3d91e5a20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COLUMNS = [
    ("shipping_address", sa.String(length=255)),
    ("shipping_city", sa.String(length=100)),
    ("shipping_state", sa.String(length=100)),
    ("shipping_zip_code", sa.String(length=20)),
    ("shipping_country", sa.String(length=100)),
]


def upgrade() -> None:
    for name, column_type in COLUMNS:
        op.add_column(
            "sales_opportunity", sa.Column(name, column_type, nullable=True)
        )


def downgrade() -> None:
    for name, _ in COLUMNS:
        op.drop_column("sales_opportunity", name)
