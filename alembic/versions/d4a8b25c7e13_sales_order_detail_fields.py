"""add order header, terms and activity history to sales_order

The redesigned Sales Order screens collect a quotation reference, the
customer's own PO number and date, the advance/balance split, the commercial
conditions and technical scope, and the annexures attached to the order - none
of which had anywhere to live. The detail page also shows an Activity History
that nothing was ever written to.

Revision ID: d4a8b25c7e13
Revises: c9e2f1a7b408
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "d4a8b25c7e13"
down_revision: Union[str, Sequence[str], None] = "c9e2f1a7b408"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COLUMNS = [
    ("quotation_id", sa.String(length=50)),
    ("po_number", sa.String(length=100)),
    ("po_date", sa.DateTime()),
    ("advance_percent", sa.Float()),
    ("commercial_terms", sa.JSON()),
    ("technical_notes", sa.String(length=2000)),
    ("attachments", sa.JSON()),
]


def upgrade() -> None:
    for name, column_type in COLUMNS:
        op.add_column("sales_order", sa.Column(name, column_type, nullable=True))

    # Existing orders keep the 30/70 split the summary used to print into
    # the markup, so none of them start showing a zero advance.
    op.execute(
        "UPDATE sales_order SET advance_percent = 30 WHERE advance_percent IS NULL"
    )

    op.create_table(
        "sales_order_activity",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "sales_order_id",
            sa.Integer(),
            sa.ForeignKey("sales_order.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=150), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("from_status", sa.String(length=50), nullable=True),
        sa.Column("to_status", sa.String(length=50), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_index(
        "ix_sales_order_activity_sales_order_id",
        "sales_order_activity",
        ["sales_order_id"],
    )

    # Every existing order opens its timeline with the event that created it.
    op.execute(
        "INSERT INTO sales_order_activity "
        "(sales_order_id, action, description, to_status, created_by, created_at) "
        "SELECT id, 'Sales Order Created', "
        "       'Sales order ' || COALESCE(order_number, id::text) || ' created.', "
        "       status, creator_id, created_at "
        "FROM sales_order"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sales_order_activity_sales_order_id",
        table_name="sales_order_activity",
    )
    op.drop_table("sales_order_activity")

    for name, _ in COLUMNS:
        op.drop_column("sales_order", name)
