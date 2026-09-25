"""Discount approvals travelling up the sales hierarchy.

Revision ID: a7f42c1d90b3
Revises: d8b3f6a2c914
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7f42c1d90b3"
down_revision = "d8b3f6a2c914"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sales_approval",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_type", sa.String(length=20), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("document_number", sa.String(length=50), nullable=True),
        sa.Column("price_type", sa.String(length=10), nullable=False, server_default="ECP"),
        sa.Column("discount_percent", sa.Float(), nullable=False, server_default="0"),
        sa.Column("discount_amount", sa.Float(), nullable=True),
        sa.Column("orc_percent", sa.Float(), nullable=True),
        sa.Column("orc_amount", sa.Float(), nullable=True),
        sa.Column("document_value", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("current_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requested_by_name", sa.String(length=200), nullable=True),
        sa.Column("requested_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("remarks", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_sales_approval_document", "sales_approval", ["document_type", "document_id"])
    op.create_index("ix_sales_approval_status", "sales_approval", ["status"])
    op.create_index("ix_sales_approval_requested_by", "sales_approval", ["requested_by"])


def downgrade() -> None:
    op.drop_index("ix_sales_approval_requested_by", table_name="sales_approval")
    op.drop_index("ix_sales_approval_status", table_name="sales_approval")
    op.drop_index("ix_sales_approval_document", table_name="sales_approval")
    op.drop_table("sales_approval")
