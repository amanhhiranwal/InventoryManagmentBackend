"""create sales_quotation_activity

The last of the four sales records to get an Activity History. Without it the
quotation detail page can only show where a quotation is, never how it got
there - drafted, emailed to the client, accepted or rejected.

Revision ID: e7c1a93f2b64
Revises: d4a8b25c7e13
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "e7c1a93f2b64"
down_revision: Union[str, Sequence[str], None] = "d4a8b25c7e13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sales_quotation_activity",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "quotation_id",
            sa.Integer(),
            sa.ForeignKey("sales_quotation.id", ondelete="CASCADE"),
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
        "ix_sales_quotation_activity_quotation_id",
        "sales_quotation_activity",
        ["quotation_id"],
    )

    # Every existing quotation opens its timeline with the event that
    # created it, so none of them start on an empty panel.
    op.execute(
        "INSERT INTO sales_quotation_activity "
        "(quotation_id, action, description, to_status, created_by, created_at) "
        "SELECT id, 'Quotation Drafted', "
        "       'Quotation ' || COALESCE(quote_number, id::text) || ' created.', "
        "       status, creator_id, created_at "
        "FROM sales_quotation"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sales_quotation_activity_quotation_id",
        table_name="sales_quotation_activity",
    )
    op.drop_table("sales_quotation_activity")
