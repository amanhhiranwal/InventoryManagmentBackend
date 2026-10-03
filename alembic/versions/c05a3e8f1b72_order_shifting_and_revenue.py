"""What an order costs to shift, and what it leaves us.

Revision ID: c05a3e8f1b72
Revises: b92e5d1a7c46
"""

import sqlalchemy as sa
from alembic import op

revision = "c05a3e8f1b72"
down_revision = "b92e5d1a7c46"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sales_order",
        sa.Column("shifting_charges", sa.Float(), nullable=True, server_default="0"),
    )
    op.add_column(
        "sales_order",
        sa.Column("total_revenue", sa.Float(), nullable=True, server_default="0"),
    )

    # Existing orders carry no shifting cost, so their revenue is the grand
    # total less what was only ever passing through it. Backfilled rather
    # than left at zero: a report summing revenue should not read every
    # order raised before today as worth nothing.
    op.execute(
        """
        UPDATE sales_order
        SET total_revenue = coalesce(grand_total, 0)
                          - coalesce(freight_charges, 0)
                          - coalesce(installation_lumpsum, 0)
                          - coalesce(gst_amount, 0)
        """
    )


def downgrade() -> None:
    op.drop_column("sales_order", "total_revenue")
    op.drop_column("sales_order", "shifting_charges")
