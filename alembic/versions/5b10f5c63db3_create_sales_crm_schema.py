"""create sales crm schema

Revision ID: 5b10f5c63db3
Revises: bf51e5527bff
Create Date: 2026-09-03

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "5b10f5c63db3"
down_revision: Union[str, Sequence[str], None] = "bf51e5527bff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create sales_customer_type
    op.create_table(
        "sales_customer_type",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_sales_customer_type_code",
        "sales_customer_type",
        ["code"],
        unique=True,
    )

    # Create sales_lead_source
    op.create_table(
        "sales_lead_source",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("code"),
    )

    # Create sales_state
    op.create_table(
        "sales_state",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("country", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_sales_state_code",
        "sales_state",
        ["code"],
        unique=True,
    )

    # Create sales_lead
    op.create_table(
        "sales_lead",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=150), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("stage", sa.String(length=50), nullable=False),
        sa.Column("demo_status", sa.String(length=50), nullable=True),

        sa.Column("contact_name", sa.String(length=100), nullable=True),
        sa.Column("organization_name", sa.String(length=150), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("mobile_number", sa.String(length=20), nullable=True),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("office_address", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("zip_code", sa.String(length=20), nullable=True),
        sa.Column("country", sa.String(length=100), nullable=True),
        sa.Column("gst_number", sa.String(length=50), nullable=True),
        sa.Column("pan_number", sa.String(length=50), nullable=True),
        sa.Column("coi_number", sa.String(length=50), nullable=True),
        sa.Column("designation", sa.String(length=100), nullable=True),
        sa.Column("remarks", sa.String(length=1000), nullable=True),

        sa.Column("requirements", sa.String(length=1000), nullable=True),
        sa.Column("quotation_type", sa.String(length=50), nullable=True),
        sa.Column("quotation_items", sa.JSON(), nullable=True),

        sa.Column("customer_type_id", sa.Integer(), nullable=True),
        sa.Column("state_id", sa.Integer(), nullable=True),
        sa.Column("lead_source_id", sa.Integer(), nullable=True),

        sa.Column("creator_id", sa.UUID(), nullable=False),
        sa.Column("assigned_to_id", sa.UUID(), nullable=True),
        sa.Column("assigned_by_id", sa.UUID(), nullable=True),

        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),

        sa.ForeignKeyConstraint(
            ["customer_type_id"],
            ["sales_customer_type.id"],
        ),
        sa.ForeignKeyConstraint(
            ["state_id"],
            ["sales_state.id"],
        ),
        sa.ForeignKeyConstraint(
            ["lead_source_id"],
            ["sales_lead_source.id"],
        ),
        sa.ForeignKeyConstraint(
            ["creator_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Preserve the existing 3 lead-source records.
    op.execute(
        """
        INSERT INTO sales_lead_source
            (name, code, description, is_active, created_at, updated_at)
        SELECT
            name, code, description, is_active, created_at, updated_at
        FROM lead_source
        """
    )


def downgrade() -> None:
    op.drop_table("sales_lead")

    op.drop_index(
        "ix_sales_state_code",
        table_name="sales_state",
    )
    op.drop_table("sales_state")

    op.drop_table("sales_lead_source")

    op.drop_index(
        "ix_sales_customer_type_code",
        table_name="sales_customer_type",
    )
    op.drop_table("sales_customer_type")
