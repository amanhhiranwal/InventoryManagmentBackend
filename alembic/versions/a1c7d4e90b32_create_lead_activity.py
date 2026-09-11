"""create sales_lead_activity

The Lead Details drawer has always rendered an Activity History, but there was
nothing behind it: the cards were placeholders rebuilt from the lead's own
created_at, and every status change - from the row menu, the drawer menu or a
conversion - left no record at all. This table gives that timeline something
real to read, and the Log Activity form something to write to.

Revision ID: a1c7d4e90b32
Revises: f2b6d84a19c7
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "a1c7d4e90b32"
down_revision: Union[str, Sequence[str], None] = "f2b6d84a19c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sales_lead_activity",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "lead_id",
            sa.Integer(),
            sa.ForeignKey("sales_lead.id", ondelete="CASCADE"),
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
        "ix_sales_lead_activity_lead_id",
        "sales_lead_activity",
        ["lead_id"],
    )

    # Every existing lead opens its timeline with the event that created it,
    # so leads raised before this table existed are not left with an empty
    # history panel.
    op.execute(
        "INSERT INTO sales_lead_activity "
        "(lead_id, action, description, to_status, created_by, created_at) "
        "SELECT id, 'Lead Created', remarks, status, creator_id, created_at "
        "FROM sales_lead"
    )


def downgrade() -> None:
    op.drop_index("ix_sales_lead_activity_lead_id", table_name="sales_lead_activity")
    op.drop_table("sales_lead_activity")
