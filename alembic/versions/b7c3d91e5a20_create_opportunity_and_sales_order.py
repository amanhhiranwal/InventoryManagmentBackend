"""create sales_opportunity and sales_order, normalise lead statuses

Promotes Opportunity from a Lead.stage string to its own table, moves Sales
Orders out of MongoDB into Postgres, and rewrites legacy lowercase lead
statuses to the canonical vocabulary.

Existing rows are preserved: leads already sitting at stage='opportunity' are
backfilled into sales_opportunity, and the Mongo sales_orders documents are
copied across by the accompanying data migration script.

Revision ID: b7c3d91e5a20
Revises: 9f2c41ab77e0
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7c3d91e5a20"
down_revision: Union[str, Sequence[str], None] = "9f2c41ab77e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # sales_opportunity
    # ------------------------------------------------------------------
    op.create_table(
        "sales_opportunity",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=150), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("deal_value", sa.Float(), nullable=True),
        sa.Column("priority", sa.String(length=20), nullable=True),
        sa.Column("expected_closing_date", sa.DateTime(), nullable=True),
        sa.Column("contact_name", sa.String(length=100), nullable=True),
        sa.Column("organization_name", sa.String(length=150), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("mobile_number", sa.String(length=20), nullable=True),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("designation", sa.String(length=100), nullable=True),
        sa.Column("office_address", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("zip_code", sa.String(length=20), nullable=True),
        sa.Column("country", sa.String(length=100), nullable=True),
        sa.Column("gst_number", sa.String(length=50), nullable=True),
        sa.Column("pan_number", sa.String(length=50), nullable=True),
        sa.Column("coi_number", sa.String(length=50), nullable=True),
        sa.Column("requirements", sa.String(length=1000), nullable=True),
        sa.Column("remarks", sa.String(length=1000), nullable=True),
        sa.Column("demo_status", sa.String(length=50), nullable=True),
        sa.Column("product_items", sa.JSON(), nullable=True),
        sa.Column("customer_type_id", sa.Integer(), nullable=True),
        sa.Column("state_id", sa.Integer(), nullable=True),
        sa.Column("creator_id", sa.UUID(), nullable=False),
        sa.Column("assigned_to_id", sa.UUID(), nullable=True),
        sa.Column("won_at", sa.DateTime(), nullable=True),
        sa.Column("won_by", sa.UUID(), nullable=True),
        sa.Column("won_reason", sa.String(length=1000), nullable=True),
        sa.Column("lost_reason", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["sales_lead.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["customer_type_id"], ["sales_customer_type.id"]
        ),
        sa.ForeignKeyConstraint(["state_id"], ["sales_state.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_sales_opportunity_lead_id"),
        "sales_opportunity",
        ["lead_id"],
    )
    op.create_index(
        op.f("ix_sales_opportunity_status"),
        "sales_opportunity",
        ["status"],
    )

    # ------------------------------------------------------------------
    # sales_order
    # ------------------------------------------------------------------
    op.create_table(
        "sales_order",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_number", sa.String(length=50), nullable=True),
        sa.Column("legacy_mongo_id", sa.String(length=50), nullable=True),
        sa.Column("opportunity_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("customer_name", sa.String(length=200), nullable=False),
        sa.Column("company_name", sa.String(length=200), nullable=True),
        sa.Column("customer_type", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=100), nullable=True),
        sa.Column("order_date", sa.DateTime(), nullable=True),
        sa.Column("assigned_to", sa.String(length=200), nullable=True),
        sa.Column("sales_executive", sa.String(length=200), nullable=True),
        sa.Column("customer_information", sa.JSON(), nullable=True),
        sa.Column("billing_address", sa.JSON(), nullable=True),
        sa.Column("shipping_address", sa.JSON(), nullable=True),
        sa.Column("items", sa.JSON(), nullable=True),
        sa.Column("total_amount", sa.Float(), nullable=True),
        sa.Column("discount_amount", sa.Float(), nullable=True),
        sa.Column("gst_amount", sa.Float(), nullable=True),
        sa.Column("grand_total", sa.Float(), nullable=True),
        sa.Column("aging_0_30", sa.Float(), nullable=True),
        sa.Column("aging_31_60", sa.Float(), nullable=True),
        sa.Column("aging_61_90", sa.Float(), nullable=True),
        sa.Column("aging_91_120", sa.Float(), nullable=True),
        sa.Column("aging_121_180", sa.Float(), nullable=True),
        sa.Column("aging_above_180", sa.Float(), nullable=True),
        sa.Column("remarks", sa.String(length=2000), nullable=True),
        sa.Column("creator_id", sa.UUID(), nullable=True),
        sa.Column("creator_name", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["sales_opportunity.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_sales_order_order_number"),
        "sales_order",
        ["order_number"],
        unique=True,
    )
    op.create_index(
        op.f("ix_sales_order_opportunity_id"),
        "sales_order",
        ["opportunity_id"],
    )
    op.create_index(op.f("ix_sales_order_status"), "sales_order", ["status"])
    op.create_index(
        op.f("ix_sales_order_legacy_mongo_id"),
        "sales_order",
        ["legacy_mongo_id"],
        unique=True,
    )

    # ------------------------------------------------------------------
    # Backfill: leads already in the opportunity/quotation stage become
    # real opportunity rows, preserving their ids and ownership.
    # ------------------------------------------------------------------
    op.execute(
        """
        INSERT INTO sales_opportunity (
            lead_id, title, description, status,
            deal_value, priority,
            contact_name, organization_name, email, mobile_number, website,
            designation, office_address, city, zip_code, country,
            gst_number, pan_number, coi_number,
            requirements, remarks, demo_status, product_items,
            customer_type_id, state_id,
            creator_id, assigned_to_id,
            created_at, updated_at
        )
        SELECT
            l.id,
            l.title,
            l.description,
            CASE
                WHEN lower(l.stage) = 'quotation' THEN 'PROPOSAL'
                WHEN lower(l.stage) = 'dead' THEN 'LOST'
                ELSE 'QUALIFICATION'
            END,
            0.0,
            'Medium',
            l.contact_name, l.organization_name, l.email, l.mobile_number,
            l.website, l.designation, l.office_address, l.city, l.zip_code,
            l.country, l.gst_number, l.pan_number, l.coi_number,
            l.requirements, l.remarks, l.demo_status, l.quotation_items,
            l.customer_type_id, l.state_id,
            l.creator_id, l.assigned_to_id,
            l.created_at, l.updated_at
        FROM sales_lead l
        WHERE lower(l.stage) IN ('opportunity', 'quotation')
        """
    )

    # Leads that produced an opportunity are CONVERTED.
    op.execute(
        """
        UPDATE sales_lead
        SET status = 'CONVERTED'
        WHERE lower(stage) IN ('opportunity', 'quotation')
        """
    )

    # ------------------------------------------------------------------
    # Normalise remaining legacy lead statuses to canonical values.
    # ------------------------------------------------------------------
    op.execute(
        """
        UPDATE sales_lead SET status = CASE
            WHEN lower(status) = 'new' THEN 'NEW'
            WHEN lower(status) = 'contacted' THEN 'CONTACTED'
            WHEN lower(status) = 'qualified' THEN 'QUALIFIED'
            WHEN lower(status) = 'converted' THEN 'CONVERTED'
            WHEN lower(status) IN ('dead', 'lost', 'inactive') THEN 'LOST'
            WHEN lower(status) = 'active' THEN 'NEW'
            ELSE upper(status)
        END
        WHERE status IS NOT NULL
          AND status <> upper(status)
        """
    )

    # A lead sitting at stage='dead' is LOST regardless of its old status.
    op.execute(
        """
        UPDATE sales_lead
        SET status = 'LOST'
        WHERE lower(stage) = 'dead'
        """
    )


def downgrade() -> None:
    # Restore the legacy lowercase lead vocabulary.
    op.execute(
        """
        UPDATE sales_lead SET status = CASE
            WHEN status = 'LOST' THEN 'dead'
            WHEN status = 'CONVERTED' THEN 'qualified'
            ELSE lower(status)
        END
        """
    )

    op.drop_index(
        op.f("ix_sales_order_legacy_mongo_id"), table_name="sales_order"
    )
    op.drop_index(op.f("ix_sales_order_status"), table_name="sales_order")
    op.drop_index(
        op.f("ix_sales_order_opportunity_id"), table_name="sales_order"
    )
    op.drop_index(
        op.f("ix_sales_order_order_number"), table_name="sales_order"
    )
    op.drop_table("sales_order")

    op.drop_index(
        op.f("ix_sales_opportunity_status"), table_name="sales_opportunity"
    )
    op.drop_index(
        op.f("ix_sales_opportunity_lead_id"), table_name="sales_opportunity"
    )
    op.drop_table("sales_opportunity")
