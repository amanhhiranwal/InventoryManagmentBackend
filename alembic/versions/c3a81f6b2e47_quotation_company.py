"""Which of our companies is selling on a quotation.

Revision ID: c3a81f6b2e47
Revises: b5e21c7a4d08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c3a81f6b2e47"
down_revision = "b5e21c7a4d08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sales_quotation",
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_sales_quotation_company_id", "sales_quotation", ["company_id"]
    )
    op.create_foreign_key(
        "fk_sales_quotation_company",
        "sales_quotation",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_sales_quotation_company", "sales_quotation", type_="foreignkey")
    op.drop_index("ix_sales_quotation_company_id", table_name="sales_quotation")
    op.drop_column("sales_quotation", "company_id")
