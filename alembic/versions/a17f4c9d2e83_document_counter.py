"""One forward-only counter per document series, so a reference is never reused.

Revision ID: a17f4c9d2e83
Revises: c3a81f6b2e47
"""

import sqlalchemy as sa
from alembic import op

revision = "a17f4c9d2e83"
down_revision = "c3a81f6b2e47"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_counter",
        sa.Column("name", sa.String(length=50), primary_key=True),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
    )

    # Start each counter above the highest reference already printed, so
    # the first number issued after this runs is a new one. A series with
    # no documents yet is left out and seeds itself on first use.
    op.execute(
        """
        INSERT INTO document_counter (name, seq)
        SELECT 'sales_order',
               max(
                   nullif(regexp_replace(order_number, '\\D', '', 'g'), '')::int
               )
        FROM sales_order
        WHERE order_number IS NOT NULL
        HAVING max(nullif(regexp_replace(order_number, '\\D', '', 'g'), '')::int)
               IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT INTO document_counter (name, seq)
        SELECT 'quotation',
               max(
                   nullif(regexp_replace(quote_number, '\\D', '', 'g'), '')::int
               ) - 3000
        FROM sales_quotation
        WHERE quote_number IS NOT NULL
        HAVING max(nullif(regexp_replace(quote_number, '\\D', '', 'g'), '')::int)
               IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT INTO document_counter (name, seq)
        SELECT 'proforma_invoice',
               max(nullif(regexp_replace(pi_number, '\\D', '', 'g'), '')::int)
        FROM sales_proforma_invoice
        WHERE pi_number IS NOT NULL
        HAVING max(nullif(regexp_replace(pi_number, '\\D', '', 'g'), '')::int)
               IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_table("document_counter")
