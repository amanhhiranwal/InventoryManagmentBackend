from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
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

    #: Set when an account is created, and when somebody resets it for
    #: them. While it stands the password they hold is a temporary one
    #: that was emailed to them, so the account can do exactly two
    #: things: sign in, and choose a new password. Everything else is
    #: refused - an emailed password has been through a mail server and
    #: somebody's inbox, and is not a credential to work under.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        server_default="false",
    )

    #: Goes into every token at sign-in and is checked on every request,
    #: so raising it signs the account out everywhere at once.
    session_version: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        server_default="0",
    )

    #: When a super admin sent the welcome email from User Management.
    #: It goes once per person: set, the button is spent.
    welcome_email_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    phone_number: Mapped[str] = mapped_column(
        String(20),
        nullable=True,
    )

    employee_id: Mapped[str] = mapped_column(
        String(50),
        unique=True,
    )

    #: Where they work, as the staffing sheet records it - a city for most
    #: people, a state for those covering one. Free text rather than a key
    #: into the Locations master, because the sheet mixes the two and a
    #: salesperson's base is a fact about them rather than a place we trade
    #: in.
    location: Mapped[str] = mapped_column(
        String(100),
        nullable=True,
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