from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_model import BaseModel


class UserCoManager(BaseModel):
    """Someone a user also reports to, beside their main manager.

    Two Directors over the Founder, say: the Founder reports to one of
    them (reports_to_id on users) and also to the other, here. Both see
    the Founder's records, and the Reporting Chart draws them side by
    side. Approval chains still follow the main manager only, so a
    discount climbs one line rather than splitting.
    """

    __tablename__ = "user_co_managers"
    __table_args__ = (UniqueConstraint("user_id", "manager_id", name="uq_user_co_manager"),)

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    manager_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
