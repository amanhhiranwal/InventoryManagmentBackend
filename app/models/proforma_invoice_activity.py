from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.database.base import Base


class ProformaInvoiceActivity(Base):
    """One entry in a proforma invoice's Activity History.

    Only the invoice's own events live here. The detail page also shows the
    sales order's history (and, through it, the opportunity's), which the
    service merges in on read rather than copying, so it cannot go stale.
    """

    __tablename__ = "sales_proforma_invoice_activity"

    id = Column(Integer, primary_key=True, autoincrement=True)

    proforma_invoice_id = Column(
        Integer,
        ForeignKey("sales_proforma_invoice.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Short headline, e.g. "Proforma Invoice Generated".
    action = Column(String(150), nullable=False)

    description = Column(String(2000), nullable=True)

    from_status = Column(String(50), nullable=True)
    to_status = Column(String(50), nullable=True)

    created_by = Column(UUID(as_uuid=True), nullable=True)

    created_at = Column(DateTime, default=func.now(), nullable=False)
