from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_model import BaseModel


class User(BaseModel):

    __tablename__ = "users"

    first_name: Mapped[str] = mapped_column(String(100))

    last_name: Mapped[str] = mapped_column(String(100))

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
    )

    password: Mapped[str] = mapped_column(String(255))

    phone_number: Mapped[str] = mapped_column(
        String(20),
        nullable=True,
    )

    employee_id: Mapped[str] = mapped_column(
        String(50),
        unique=True,
    )

    from sqlalchemy.orm import relationship

    roles = relationship(
        "Role",
        secondary="user_roles",
        backref="users",
    )

    companies = relationship(
        "Company",
        secondary="user_companies",
        backref="users",
    )

    # The manager this user reports to. Together with the role hierarchy it
    # decides whose records a user can see: see app/services/hierarchy_service.
    reports_to_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    is_super_admin: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )