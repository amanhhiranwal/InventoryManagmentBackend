import uuid
from sqlalchemy import Boolean, Column, String, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.base import Base

class MenuItem(Base):
    __tablename__ = "menu_items"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    title = Column(
        String(100),
        nullable=False,
    )

    icon = Column(
        String(50),
        nullable=True,
    )

    path = Column(
        String(200),
        nullable=True,
    )

    permission_key = Column(
        String(100),
        nullable=True,
    )

    parent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("menu_items.id"),
        nullable=True,
    )

    order_index = Column(
        Integer,
        default=0,
        nullable=False,
    )

    is_active = Column(
        Boolean,
        default=True,
        nullable=False,
    )

    parent = relationship("MenuItem", remote_side=[id], backref="children")
