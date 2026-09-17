from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.workflow_status import ProformaInvoiceStatus
from app.database.base import Base


class ProformaInvoice(Base):
    """Proforma invoice, raised against a confirmed sales order.

    The customer, addresses, lines and charges are copied from the order when
    the invoice is created rather than read through the link, because an
    invoice is a document: once it has gone to the customer it must keep
    saying what it said, even if the order is edited afterwards.
    """

    __tablename__ = "sales_proforma_invoice"

    id = Column(Integer, primary_key=True, autoincrement=True)

    #: Human-facing reference shown in the UI (e.g. "PI-00012").
    pi_number = Column(String(50), unique=True, nullable=True, index=True)

    sales_order_id = Column(
        Integer,
        ForeignKey("sales_order.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    status = Column(
        String(50),
        default=ProformaInvoiceStatus.DRAFT,
        nullable=False,
        index=True,
    )

    #: PI Date (Issue) and PI Date (Due). The gap between them is the
    #: validity period printed on the document.
    issue_date = Column(DateTime, nullable=True)
    due_date = Column(DateTime, nullable=True)

    assigned_to = Column(String(200), nullable=True)

    customer_name = Column(String(200), nullable=False)
    company_name = Column(String(200), nullable=True)
    customer_type = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)

    customer_information = Column(JSON, nullable=True)
    billing_address = Column(JSON, nullable=True)
    shipping_address = Column(JSON, nullable=True)
    items = Column(JSON, nullable=True)

    # Money. Always re-derived from the lines and charges on write, with the
    # same arithmetic as the sales order, so the two documents agree.
    total_amount = Column(Float, default=0.0, nullable=True)
    discount_amount = Column(Float, default=0.0, nullable=True)
    discount_mode = Column(String(10), default="AMOUNT", nullable=True)
    discount_input = Column(Float, nullable=True)
    orc_amount = Column(Float, default=0.0, nullable=True)
    orc_percent = Column(Float, default=0.0, nullable=True)
    orc_mode = Column(String(10), default="AMOUNT", nullable=True)
    orc_input = Column(Float, nullable=True)
    freight_charges = Column(Float, default=0.0, nullable=True)
    installation_lumpsum = Column(Float, default=0.0, nullable=True)
    taxable_amount = Column(Float, default=0.0, nullable=True)
    gst_percent = Column(Float, default=18.0, nullable=True)
    gst_amount = Column(Float, default=0.0, nullable=True)
    grand_total = Column(Float, default=0.0, nullable=True)

    #: Amount Paid against this invoice, and what is still owed.
    amount_paid = Column(Float, default=0.0, nullable=True)
    balance_due = Column(Float, default=0.0, nullable=True)

    #: Share of the total expected up front; drives the Payment Terms split.
    advance_percent = Column(Float, default=30.0, nullable=True)

    commercial_terms = Column(JSON, nullable=True)
    technical_notes = Column(String(2000), nullable=True)

    #: Annexures: [{name, size, type}].
    attachments = Column(JSON, nullable=True)

    generated_at = Column(DateTime, nullable=True)

    #: Delivery record, written only once the email actually went out.
    sent_at = Column(DateTime, nullable=True)
    sent_to = Column(String(500), nullable=True)
    sent_subject = Column(String(500), nullable=True)

    #: Statutory & Operational Clauses ticked in the Send dialog.
    send_options = Column(JSON, nullable=True)

    creator_id = Column(UUID(as_uuid=True), nullable=True)
    creator_name = Column(String(200), nullable=True)

    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    sales_order = relationship("SalesOrder", foreign_keys=[sales_order_id])
