"""create sales_opportunity_activity

The Opportunity drawer had the same gap the Lead drawer did: an Activity
History rendered from three hardcoded sample cards, with every real stage move
- from the row menu, the board menu or the drawer - leaving no record at all.
This table gives that timeline something real to read, and the Log Activity
form something to write to.

Revision ID: c9e2f1a7b408
Revises: a1c7d4e90b32
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "c9e2f1a7b408"
down_revision: Union[str, Sequence[str], None] = "a1c7d4e90b32"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sales_opportunity_activity",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "opportunity_id",
            sa.Integer(),
            sa.ForeignKey("sales_opportunity.id", ondelete="CASCADE"),
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
        "ix_sales_opportunity_activity_opportunity_id",
        "sales_opportunity_activity",
        ["opportunity_id"],
    )

    # Every existing opportunity opens its timeline with the event that
    # created it. Those raised from a lead say so, since that is what actually
    # happened and the distinction is visible in the drawer.
    op.execute(
        "INSERT INTO sales_opportunity_activity "
        "(opportunity_id, action, description, to_status, created_by, created_at) "
        "SELECT o.id, "
        "       CASE WHEN o.lead_id IS NULL "
        "            THEN 'Opportunity Created' ELSE 'Converted From Lead' END, "
        "       o.remarks, o.status, o.creator_id, o.created_at "
        "FROM sales_opportunity o"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sales_opportunity_activity_opportunity_id",
        table_name="sales_opportunity_activity",
    )
    op.drop_table("sales_opportunity_activity")
