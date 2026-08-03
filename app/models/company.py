import uuid

from sqlalchemy import Boolean, Column, String
from sqlalchemy.dialects.postgresql import UUID

from app.database.base import Base


class Company(Base):
    __tablename__ = "companies"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    company_name = Column(
        String(255),
        nullable=False,
    )

    company_code = Column(
        String(50),
        unique=True,
        nullable=False,
    )

    email = Column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )

    phone_number = Column(
        String(20),
        nullable=False,
    )

    website = Column(
        String(255),
        nullable=True,
    )

    gst_number = Column(
        String(50),
        nullable=True,
    )

    address_line_1 = Column(
        String(255),
        nullable=False,
    )

    address_line_2 = Column(
        String(255),
        nullable=True,
    )

    city = Column(
        String(100),
        nullable=False,
    )

    state = Column(
        String(100),
        nullable=False,
    )

    country = Column(
        String(100),
        nullable=False,
    )

    postal_code = Column(
        String(20),
        nullable=False,
    )

    is_active = Column(
        Boolean,
        default=True,
        nullable=False,
    )