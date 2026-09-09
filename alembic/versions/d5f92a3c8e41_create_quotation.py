"""create sales_quotation

Adds the Quotation entity, which sits between Opportunity and Sales Order in
the workflow: an opportunity reaching PROPOSAL produces a quotation, and an
ACCEPTED quotation is what a sales order is raised from. Totals are stored
rather than recomputed on read so an issued quotation always shows the
figures it was sent with.

Revision ID: d5f92a3c8e41
Revises: c4e81f2a6b93
Create Date: 2026-09-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "d5f92a3c8e41"
down_revision: Union[str, Sequence[str], None] = "c4e81f2a6b93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sales_quotation",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("quote_number", sa.String(length=50), nullable=True),
        sa.Column("opportunity_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=50),
            server_default="DRAFT",
            nullable=False,
        ),

        sa.Column("opportunity_name", sa.String(length=200), nullable=True),
        sa.Column("organization_name", sa.String(length=200), nullable=True),
        sa.Column("contact_name", sa.String(length=200), nullable=True),
        sa.Column("designation", sa.String(length=100), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("mobile_number", sa.String(length=20), nullable=True),
        sa.Column("customer_type", sa.String(length=100), nullable=True),

        sa.Column("quotation_date", sa.DateTime(), nullable=True),
        sa.Column("validation_date", sa.DateTime(), nullable=True),

        sa.Column("billing_address", sa.JSON(), nullable=True),
        sa.Column("shipping_address", sa.JSON(), nullable=True),
        sa.Column("shipping_same_as_billing", sa.Boolean(), nullable=True),

        sa.Column("items", sa.JSON(), nullable=True),

        sa.Column("subtotal", sa.Float(), nullable=True),
        sa.Column("discount_amount", sa.Float(), nullable=True),
        sa.Column("orc_amount", sa.Float(), nullable=True),
        sa.Column("orc_percent", sa.Float(), nullable=True),
        sa.Column("freight_charges", sa.Float(), nullable=True),
        sa.Column("installation_lumpsum", sa.Float(), nullable=True),
        sa.Column("taxable_amount", sa.Float(), nullable=True),
        sa.Column("gst_percent", sa.Float(), nullable=True),
        sa.Column("gst_amount", sa.Float(), nullable=True),
        sa.Column("total_payable", sa.Float(), nullable=True),
        sa.Column("advance_percent", sa.Float(), nullable=True),
        sa.Column("advance_amount", sa.Float(), nullable=True),
        sa.Column("on_delivery_amount", sa.Float(), nullable=True),

        sa.Column("attachments", sa.JSON(), nullable=True),
        sa.Column("terms", sa.JSON(), nullable=True),
        sa.Column("remarks", sa.String(length=4000), nullable=True),

        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("sent_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sent_to", sa.String(length=500), nullable=True),
        sa.Column("sent_cc", sa.String(length=500), nullable=True),
        sa.Column("sent_bcc", sa.String(length=500), nullable=True),
        sa.Column("sent_subject", sa.String(length=500), nullable=True),
        sa.Column("sent_body", sa.String(length=8000), nullable=True),
        sa.Column("send_options", sa.JSON(), nullable=True),

        sa.Column("rejected_reason", sa.String(length=1000), nullable=True),

        sa.Column("customer_type_id", sa.Integer(), nullable=True),
        sa.Column("state_id", sa.Integer(), nullable=True),

        sa.Column("creator_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "assigned_to_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["sales_opportunity.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["customer_type_id"],
            ["sales_customer_type.id"],
        ),
        sa.ForeignKeyConstraint(["state_id"], ["sales_state.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quote_number"),
    )

    op.create_index(
        "ix_sales_quotation_quote_number",
        "sales_quotation",
        ["quote_number"],
    )
    op.create_index(
        "ix_sales_quotation_opportunity_id",
        "sales_quotation",
        ["opportunity_id"],
    )
    op.create_index(
        "ix_sales_quotation_status",
        "sales_quotation",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_sales_quotation_status", table_name="sales_quotation")
    op.drop_index(
        "ix_sales_quotation_opportunity_id",
        table_name="sales_quotation",
    )
    op.drop_index(
        "ix_sales_quotation_quote_number",
        table_name="sales_quotation",
    )
    op.drop_table("sales_quotation")
