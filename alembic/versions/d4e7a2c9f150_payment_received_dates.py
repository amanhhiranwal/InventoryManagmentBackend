"""The day accounts say the advance and the balance were received.

Revision ID: d4e7a2c9f150
Revises: c05a3e8f1b72
"""

import sqlalchemy as sa
from alembic import op

revision = "d4e7a2c9f150"
down_revision = "c05a3e8f1b72"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sales_order", sa.Column("advance_received_date", sa.Date(), nullable=True))
    op.add_column("sales_order", sa.Column("balance_received_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("sales_order", "balance_received_date")
    op.drop_column("sales_order", "advance_received_date")
