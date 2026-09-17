"""create sales_proforma_invoice and its activity table

A proforma invoice is raised against a confirmed sales order and carries its
own copy of the customer, addresses, lines and charges, so it keeps saying
what it said once it has gone to the customer.

Revision ID: b3f9e2c71a58
Revises: e7c1a93f2b64
Create Date: 2026-09-17 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "b3f9e2c71a58"
down_revision: Union[str, Sequence[str], None] = "e7c1a93f2b64"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sales_proforma_invoice",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("pi_number", sa.String(length=50), nullable=True),
        sa.Column(
            "sales_order_id",
            sa.Integer(),
            sa.ForeignKey("sales_order.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="DRAFT"),
        sa.Column("issue_date", sa.DateTime(), nullable=True),
        sa.Column("due_date", sa.DateTime(), nullable=True),
        sa.Column("assigned_to", sa.String(length=200), nullable=True),
        sa.Column("customer_name", sa.String(length=200), nullable=False),
        sa.Column("company_name", sa.String(length=200), nullable=True),
        sa.Column("customer_type", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=100), nullable=True),
        sa.Column("customer_information", sa.JSON(), nullable=True),
        sa.Column("billing_address", sa.JSON(), nullable=True),
        sa.Column("shipping_address", sa.JSON(), nullable=True),
        sa.Column("items", sa.JSON(), nullable=True),
        sa.Column("total_amount", sa.Float(), nullable=True),
        sa.Column("discount_amount", sa.Float(), nullable=True),
        sa.Column("discount_mode", sa.String(length=10), nullable=True),
        sa.Column("discount_input", sa.Float(), nullable=True),
        sa.Column("orc_amount", sa.Float(), nullable=True),
        sa.Column("orc_percent", sa.Float(), nullable=True),
        sa.Column("orc_mode", sa.String(length=10), nullable=True),
        sa.Column("orc_input", sa.Float(), nullable=True),
        sa.Column("freight_charges", sa.Float(), nullable=True),
        sa.Column("installation_lumpsum", sa.Float(), nullable=True),
        sa.Column("taxable_amount", sa.Float(), nullable=True),
        sa.Column("gst_percent", sa.Float(), nullable=True),
        sa.Column("gst_amount", sa.Float(), nullable=True),
        sa.Column("grand_total", sa.Float(), nullable=True),
        sa.Column("amount_paid", sa.Float(), nullable=True),
        sa.Column("balance_due", sa.Float(), nullable=True),
        sa.Column("advance_percent", sa.Float(), nullable=True),
        sa.Column("commercial_terms", sa.JSON(), nullable=True),
        sa.Column("technical_notes", sa.String(length=2000), nullable=True),
        sa.Column("attachments", sa.JSON(), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("sent_to", sa.String(length=500), nullable=True),
        sa.Column("sent_subject", sa.String(length=500), nullable=True),
        sa.Column("send_options", sa.JSON(), nullable=True),
        sa.Column("creator_id", UUID(as_uuid=True), nullable=True),
        sa.Column("creator_name", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_index(
        "ix_sales_proforma_invoice_pi_number",
        "sales_proforma_invoice",
        ["pi_number"],
        unique=True,
    )
    op.create_index(
        "ix_sales_proforma_invoice_sales_order_id",
        "sales_proforma_invoice",
        ["sales_order_id"],
    )
    op.create_index(
        "ix_sales_proforma_invoice_status",
        "sales_proforma_invoice",
        ["status"],
    )

    op.create_table(
        "sales_proforma_invoice_activity",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "proforma_invoice_id",
            sa.Integer(),
            sa.ForeignKey("sales_proforma_invoice.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=150), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("from_status", sa.String(length=50), nullable=True),
        sa.Column("to_status", sa.String(length=50), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_index(
        "ix_sales_proforma_invoice_activity_proforma_invoice_id",
        "sales_proforma_invoice_activity",
        ["proforma_invoice_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sales_proforma_invoice_activity_proforma_invoice_id",
        table_name="sales_proforma_invoice_activity",
    )
    op.drop_table("sales_proforma_invoice_activity")

    op.drop_index("ix_sales_proforma_invoice_status", table_name="sales_proforma_invoice")
    op.drop_index("ix_sales_proforma_invoice_sales_order_id", table_name="sales_proforma_invoice")
    op.drop_index("ix_sales_proforma_invoice_pi_number", table_name="sales_proforma_invoice")
    op.drop_table("sales_proforma_invoice")
