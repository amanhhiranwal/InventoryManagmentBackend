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

from app.core.workflow_status import OpportunityStatus
from app.database.base import Base


class Opportunity(Base):
    """A qualified Lead promoted into the sales pipeline.

    The originating Lead is kept as a foreign key so the Lead UI and the
    Opportunity UI stay linked; the Lead row itself moves to CONVERTED.
    """

    __tablename__ = "sales_opportunity"

    id = Column(Integer, primary_key=True, autoincrement=True)

    lead_id = Column(
        Integer,
        ForeignKey("sales_lead.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    title = Column(String(150), nullable=False)
    description = Column(String(2000), nullable=True)

    status = Column(
        String(50),
        default=OpportunityStatus.QUALIFICATION,
        nullable=False,
        index=True,
    )

    # Pipeline attributes the previous Lead-only implementation could not store.
    deal_value = Column(Float, default=0.0, nullable=True)
    priority = Column(String(20), default="Medium", nullable=True)
    expected_closing_date = Column(DateTime, nullable=True)

    # Customer snapshot, carried over at conversion time.
    contact_name = Column(String(100), nullable=True)
    organization_name = Column(String(150), nullable=True)
    email = Column(String(255), nullable=True)
    mobile_number = Column(String(20), nullable=True)
    website = Column(String(255), nullable=True)
    designation = Column(String(100), nullable=True)
    office_address = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    zip_code = Column(String(20), nullable=True)
    country = Column(String(100), default="India", nullable=True)
    gst_number = Column(String(50), nullable=True)
    pan_number = Column(String(50), nullable=True)
    coi_number = Column(String(50), nullable=True)

    # Shipping address; the columns above are the billing address.
    shipping_address = Column(String(255), nullable=True)
    shipping_city = Column(String(100), nullable=True)
    shipping_state = Column(String(100), nullable=True)
    shipping_zip_code = Column(String(20), nullable=True)
    shipping_country = Column(String(100), nullable=True)

    requirements = Column(String(1000), nullable=True)
    remarks = Column(String(1000), nullable=True)
    demo_status = Column(String(50), default="none", nullable=True)

    product_items = Column(JSON, nullable=True)

    customer_type_id = Column(
        Integer,
        ForeignKey("sales_customer_type.id"),
        nullable=True,
    )
    state_id = Column(Integer, ForeignKey("sales_state.id"), nullable=True)

    creator_id = Column(UUID(as_uuid=True), nullable=False)
    assigned_to_id = Column(UUID(as_uuid=True), nullable=True)

    # Deal Won outcome. "Deal Won" is status == WON, not a separate entity.
    won_at = Column(DateTime, nullable=True)
    won_by = Column(UUID(as_uuid=True), nullable=True)
    won_reason = Column(String(1000), nullable=True)
    lost_reason = Column(String(1000), nullable=True)

    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    lead = relationship("Lead", foreign_keys=[lead_id])
    customer_type = relationship("CustomerType", foreign_keys=[customer_type_id])
    state = relationship("State", foreign_keys=[state_id])
