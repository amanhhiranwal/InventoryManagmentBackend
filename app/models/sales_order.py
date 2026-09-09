from sqlalchemy import (
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

from app.core.workflow_status import SalesOrderStatus
from app.database.base import Base


class SalesOrder(Base):
    """Sales order, linked back to the Opportunity it was raised from.

    Previously stored in MongoDB, where the create schema silently discarded
    customer information, addresses, order date and status. Those fields are
    persisted here as real columns.
    """

    __tablename__ = "sales_order"

    id = Column(Integer, primary_key=True, autoincrement=True)

    #: Human-facing order reference shown in the UI (e.g. "SO-00012").
    order_number = Column(String(50), unique=True, nullable=True, index=True)

    #: Original MongoDB ObjectId for rows copied over from the previous
    #: implementation. Keeps the one-off data migration re-runnable.
    legacy_mongo_id = Column(String(50), unique=True, nullable=True, index=True)

    opportunity_id = Column(
        Integer,
        ForeignKey("sales_opportunity.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    status = Column(
        String(50),
        default=SalesOrderStatus.DRAFT,
        nullable=False,
        index=True,
    )

    customer_name = Column(String(200), nullable=False)
    company_name = Column(String(200), nullable=True)
    customer_type = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)

    order_date = Column(DateTime, default=func.now(), nullable=True)

    assigned_to = Column(String(200), nullable=True)
    sales_executive = Column(String(200), nullable=True)

    # Structured payload the Mongo implementation dropped on the floor.
    customer_information = Column(JSON, nullable=True)
    billing_address = Column(JSON, nullable=True)
    shipping_address = Column(JSON, nullable=True)
    items = Column(JSON, nullable=True)

    total_amount = Column(Float, default=0.0, nullable=True)
    discount_amount = Column(Float, default=0.0, nullable=True)
    gst_amount = Column(Float, default=0.0, nullable=True)
    grand_total = Column(Float, default=0.0, nullable=True)

    # Summary charges. The New Sales Order form showed inputs for these but
    # they were never bound to anything, so nothing entered was kept or
    # included in the total.
    orc_amount = Column(Float, default=0.0, nullable=True)
    orc_percent = Column(Float, default=0.0, nullable=True)
    freight_charges = Column(Float, default=0.0, nullable=True)
    installation_lumpsum = Column(Float, default=0.0, nullable=True)
    taxable_amount = Column(Float, default=0.0, nullable=True)
    gst_percent = Column(Float, default=18.0, nullable=True)

    #: How discount and ORC were entered: "PERCENT" or "AMOUNT".
    discount_mode = Column(String(10), default="AMOUNT", nullable=True)
    orc_mode = Column(String(10), default="AMOUNT", nullable=True)
    discount_input = Column(Float, nullable=True)
    orc_input = Column(Float, nullable=True)

    #: Payment tracking. Outstanding is stored rather than derived on read so
    #: an order always reports the balance it was saved with.
    advance_received = Column(Float, default=0.0, nullable=True)
    outstanding_balance = Column(Float, default=0.0, nullable=True)

    aging_0_30 = Column(Float, default=0.0, nullable=True)
    aging_31_60 = Column(Float, default=0.0, nullable=True)
    aging_61_90 = Column(Float, default=0.0, nullable=True)
    aging_91_120 = Column(Float, default=0.0, nullable=True)
    aging_121_180 = Column(Float, default=0.0, nullable=True)
    aging_above_180 = Column(Float, default=0.0, nullable=True)

    remarks = Column(String(2000), nullable=True)

    creator_id = Column(UUID(as_uuid=True), nullable=True)
    creator_name = Column(String(200), nullable=True)

    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    opportunity = relationship("Opportunity", foreign_keys=[opportunity_id])
