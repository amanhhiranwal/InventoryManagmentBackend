import uuid

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.base import Base


class Location(Base):
    __tablename__ = "locations"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    location_name = Column(
        String(255),
        nullable=False,
    )

    location_code = Column(
        String(50),
        nullable=False,
        unique=True,
    )

    email = Column(
        String(255),
        nullable=True,
        unique=True,
        index=True,
    )

    phone_number = Column(
        String(20),
        nullable=True,
    )

    address_line_1 = Column(
        String(255),
        nullable=True,
    )

    address_line_2 = Column(
        String(255),
        nullable=True,
    )

    city = Column(
        String(100),
        nullable=True,
    )

    state = Column(
        String(100),
        nullable=True,
    )

    country = Column(
        String(100),
        nullable=True,
    )

    postal_code = Column(
        String(20),
        nullable=True,
    )

    is_default = Column(
        Boolean,
        default=False,
        nullable=False,
    )

    is_active = Column(
        Boolean,
        default=True,
        nullable=False,
    )

    company = relationship(
        "Company",
        backref="locations",
    )