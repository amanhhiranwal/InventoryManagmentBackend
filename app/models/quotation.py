from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.workflow_status import QuotationStatus
from app.database.base import Base


class Quotation(Base):
    """A priced proposal raised against an Opportunity.

    Sits between Opportunity and Sales Order in the workflow: an opportunity
    reaching PROPOSAL produces a quotation, and an ACCEPTED quotation is what
    a sales order is raised from.
    """

    __tablename__ = "sales_quotation"

    id = Column(Integer, primary_key=True, autoincrement=True)

    #: Human-facing reference shown throughout the UI, e.g. "QT-3021".
    quote_number = Column(String(50), unique=True, nullable=True, index=True)

    opportunity_id = Column(
        Integer,
        ForeignKey("sales_opportunity.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    status = Column(
        String(50),
        default=QuotationStatus.DRAFT,
        nullable=False,
        index=True,
    )

    # ------------------------------------------------------------------
    # Customer snapshot, taken when the quotation is raised so later edits
    # to the opportunity do not silently rewrite an issued quotation.
    # ------------------------------------------------------------------
    opportunity_name = Column(String(200), nullable=True)
    organization_name = Column(String(200), nullable=True)
    contact_name = Column(String(200), nullable=True)
    designation = Column(String(100), nullable=True)
    email = Column(String(255), nullable=True)
    mobile_number = Column(String(20), nullable=True)
    customer_type = Column(String(100), nullable=True)

    quotation_date = Column(DateTime, default=func.now(), nullable=True)
    #: Offer validity, 30 days from issue by default.
    validation_date = Column(DateTime, nullable=True)

    billing_address = Column(JSON, nullable=True)
    shipping_address = Column(JSON, nullable=True)
    #: True when the form's "Same as Billing" toggle was used.
    shipping_same_as_billing = Column(Boolean, default=False, nullable=True)

    #: Line items: product, model, sku, qty, discount, tax, unit_price.
    items = Column(JSON, nullable=True)

    # ------------------------------------------------------------------
    # Commercials. Stored rather than recomputed on read so an issued
    # quotation always shows the figures it was sent with.
    # ------------------------------------------------------------------
    subtotal = Column(Float, default=0.0, nullable=True)
    discount_amount = Column(Float, default=0.0, nullable=True)
    orc_amount = Column(Float, default=0.0, nullable=True)
    orc_percent = Column(Float, default=0.0, nullable=True)
    freight_charges = Column(Float, default=0.0, nullable=True)
    installation_lumpsum = Column(Float, default=0.0, nullable=True)
    taxable_amount = Column(Float, default=0.0, nullable=True)
    gst_percent = Column(Float, default=18.0, nullable=True)
    gst_amount = Column(Float, default=0.0, nullable=True)
    total_payable = Column(Float, default=0.0, nullable=True)

    #: Payment split shown in the Order Summary panel.
    advance_percent = Column(Float, default=30.0, nullable=True)
    advance_amount = Column(Float, default=0.0, nullable=True)
    on_delivery_amount = Column(Float, default=0.0, nullable=True)

    #: Uploaded annexures: [{name, size, type}]. The files themselves are
    #: held by the frontend upload flow; this records what was attached.
    attachments = Column(JSON, nullable=True)

    #: Statutory & operational clause toggles from the form.
    terms = Column(JSON, nullable=True)
    remarks = Column(String(4000), nullable=True)

    # ------------------------------------------------------------------
    # Send tracking, written by the Send Quotation Email action.
    # ------------------------------------------------------------------
    sent_at = Column(DateTime, nullable=True)
    sent_by = Column(UUID(as_uuid=True), nullable=True)
    sent_to = Column(String(500), nullable=True)
    sent_cc = Column(String(500), nullable=True)
    sent_bcc = Column(String(500), nullable=True)
    sent_subject = Column(String(500), nullable=True)
    sent_body = Column(String(8000), nullable=True)
    #: Read receipt / download alert / audit trail / owner notify toggles.
    send_options = Column(JSON, nullable=True)

    rejected_reason = Column(String(1000), nullable=True)

    customer_type_id = Column(
        Integer,
        ForeignKey("sales_customer_type.id"),
        nullable=True,
    )
    state_id = Column(Integer, ForeignKey("sales_state.id"), nullable=True)

    creator_id = Column(UUID(as_uuid=True), nullable=False)
    assigned_to_id = Column(UUID(as_uuid=True), nullable=True)

    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    opportunity = relationship("Opportunity", foreign_keys=[opportunity_id])
    customer_type_ref = relationship(
        "CustomerType",
        foreign_keys=[customer_type_id],
    )
    state = relationship("State", foreign_keys=[state_id])
