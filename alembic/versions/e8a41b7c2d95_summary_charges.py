"""add summary charges to sales_order and entry modes to sales_quotation

The New Sales Order summary showed inputs for ORC, freight and installation
and a line for advance received, but none of them were bound to anything, so
whatever was entered was discarded and Outstanding Balance always equalled
the grand total. These add somewhere for those figures to live, along with
the taxable amount and GST rate that were only ever computed on the client.

Both screens also let a discount or ORC be entered either as a flat amount or
as a percentage; the mode and the raw value as typed are stored so the figure
round-trips into the same field rather than being silently converted.

Revision ID: e8a41b7c2d95
Revises: d5f92a3c8e41
Create Date: 2026-09-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e8a41b7c2d95"
down_revision: Union[str, Sequence[str], None] = "d5f92a3c8e41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ORDER_COLUMNS = [
    ("orc_amount", sa.Float(), "0"),
    ("orc_percent", sa.Float(), "0"),
    ("freight_charges", sa.Float(), "0"),
    ("installation_lumpsum", sa.Float(), "0"),
    ("taxable_amount", sa.Float(), "0"),
    ("gst_percent", sa.Float(), "18"),
    ("advance_received", sa.Float(), "0"),
    ("outstanding_balance", sa.Float(), "0"),
    ("discount_mode", sa.String(length=10), "'AMOUNT'"),
    ("orc_mode", sa.String(length=10), "'AMOUNT'"),
    ("discount_input", sa.Float(), None),
    ("orc_input", sa.Float(), None),
]

QUOTATION_COLUMNS = [
    ("discount_mode", sa.String(length=10), "'AMOUNT'"),
    ("orc_mode", sa.String(length=10), "'AMOUNT'"),
    ("discount_input", sa.Float(), None),
    ("orc_input", sa.Float(), None),
]


def upgrade() -> None:
    for name, column_type, default in ORDER_COLUMNS:
        op.add_column(
            "sales_order",
            sa.Column(
                name,
                column_type,
                nullable=True,
                server_default=sa.text(default) if default else None,
            ),
        )

    for name, column_type, default in QUOTATION_COLUMNS:
        op.add_column(
            "sales_quotation",
            sa.Column(
                name,
                column_type,
                nullable=True,
                server_default=sa.text(default) if default else None,
            ),
        )

    # Existing orders have nothing outstanding recorded; seed the balance
    # from the total they were saved with so the column is never misleading.
    op.execute(
        "UPDATE sales_order "
        "SET outstanding_balance = COALESCE(grand_total, 0), "
        "    taxable_amount = COALESCE(grand_total, 0) - COALESCE(gst_amount, 0) "
        "WHERE outstanding_balance IS NULL OR outstanding_balance = 0"
    )


def downgrade() -> None:
    for name, _, _ in QUOTATION_COLUMNS:
        op.drop_column("sales_quotation", name)

    for name, _, _ in ORDER_COLUMNS:
        op.drop_column("sales_order", name)
