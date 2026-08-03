from sqlalchemy import String, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base_model import BaseModel

class Lead(BaseModel):
    __tablename__ = "leads"

    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="new")
    creator_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    
    stage: Mapped[str] = mapped_column(String(50), default="lead") # lead | opportunity | quotation | dead
    demo_status: Mapped[str] = mapped_column(String(50), default="none") # none | pending | given | skipped
    requirements: Mapped[str] = mapped_column(String(1000), nullable=True)
    quotation_type: Mapped[str] = mapped_column(String(50), nullable=True) # quotation | purchase_indent
    quotation_items: Mapped[list] = mapped_column(JSON, nullable=True)

    creator = relationship("User", backref="leads")
