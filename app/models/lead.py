from sqlalchemy import Column, Integer, String, ForeignKey, JSON, DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database.base import Base

class Lead(Base):
    __tablename__ = "sales_lead"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(150), nullable=False)
    description = Column(String(2000), nullable=True)
    status = Column(String(50), default="new", nullable=False)
    stage = Column(String(50), default="lead", nullable=False) # lead | opportunity | quotation | dead
    demo_status = Column(String(50), default="none", nullable=True)

    # Dedicated Structured Fields
    contact_name = Column(String(100), nullable=True)
    organization_name = Column(String(150), nullable=True)
    email = Column(String(255), nullable=True)
    mobile_number = Column(String(20), nullable=True)
    website = Column(String(255), nullable=True)
    office_address = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    zip_code = Column(String(20), nullable=True)
    country = Column(String(100), default="India", nullable=True)
    gst_number = Column(String(50), nullable=True)
    pan_number = Column(String(50), nullable=True)
    coi_number = Column(String(50), nullable=True)
    designation = Column(String(100), nullable=True)
    remarks = Column(String(1000), nullable=True)

    requirements = Column(String(1000), nullable=True)
    quotation_type = Column(String(50), nullable=True)
    quotation_items = Column(JSON, nullable=True)

    # Integer Business Foreign Keys
    customer_type_id = Column(Integer, ForeignKey("sales_customer_type.id"), nullable=True)
    state_id = Column(Integer, ForeignKey("sales_state.id"), nullable=True)
    lead_source_id = Column(Integer, ForeignKey("sales_lead_source.id"), nullable=True)

    # Auth User UUID Foreign Keys
    creator_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    assigned_to_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    assigned_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    customer_type = relationship("CustomerType", foreign_keys=[customer_type_id])
    state = relationship("State", foreign_keys=[state_id])
    lead_source = relationship("LeadSource", foreign_keys=[lead_source_id])

    creator = relationship("User", foreign_keys=[creator_id], backref="created_leads")
    assigned_to = relationship("User", foreign_keys=[assigned_to_id], backref="assigned_leads")
    assigned_by = relationship("User", foreign_keys=[assigned_by_id], backref="assigned_by_leads")
