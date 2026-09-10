"""add sourcing, timeline and document fields to sales_opportunity

The New Opportunity form collects a lead source, a purchase timeline, the
Requirements & Files uploads and the GST/PAN/COI certificates, but there was
nowhere to store any of them: lead_source and purchase_timeline were gathered
by the form and dropped on save, and the uploads never left the browser.

Revision ID: f2b6d84a19c7
Revises: e8a41b7c2d95
Create Date: 2026-09-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f2b6d84a19c7"
down_revision: Union[str, Sequence[str], None] = "e8a41b7c2d95"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COLUMNS = [
    ("lead_source", sa.String(length=100)),
    ("purchase_timeline", sa.String(length=50)),
    ("attachments", sa.JSON()),
    ("compliance_documents", sa.JSON()),
]


def upgrade() -> None:
    for name, column_type in COLUMNS:
        op.add_column(
            "sales_opportunity",
            sa.Column(name, column_type, nullable=True),
        )

    # Opportunities converted from a lead can have their source recovered.
    op.execute(
        "UPDATE sales_opportunity o "
        "SET lead_source = s.name "
        "FROM sales_lead l "
        "JOIN sales_lead_source s ON s.id = l.lead_source_id "
        "WHERE o.lead_id = l.id AND o.lead_source IS NULL"
    )


def downgrade() -> None:
    for name, _ in COLUMNS:
        op.drop_column("sales_opportunity", name)
