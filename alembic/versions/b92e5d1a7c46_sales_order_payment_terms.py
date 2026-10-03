"""Payment terms in their own field on the sales order.

Revision ID: b92e5d1a7c46
Revises: a17f4c9d2e83
"""

import sqlalchemy as sa
from alembic import op

revision = "b92e5d1a7c46"
down_revision = "a17f4c9d2e83"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sales_order",
        sa.Column("payment_terms", sa.String(length=500), nullable=True),
    )

    # The clause used to be the first bullet of commercial_terms. Lift it
    # out of the list and into its own field, so an order raised before
    # this reads the same as one raised after.
    op.execute(
        """
        UPDATE sales_order
        SET payment_terms = trim(both '"' from (commercial_terms->>0)),
            commercial_terms = (commercial_terms - 0)
        WHERE commercial_terms IS NOT NULL
          AND jsonb_typeof(commercial_terms::jsonb) = 'array'
          AND commercial_terms->>0 ILIKE 'Payment Terms:%'
        """
    )


def downgrade() -> None:
    op.drop_column("sales_order", "payment_terms")
