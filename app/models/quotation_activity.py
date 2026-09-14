from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.database.base import Base


class QuotationActivity(Base):
    """One entry in a quotation's Activity History.

    The last of the four records to get one, so the detail page can show how
    a quotation got where it is - drafted, sent, accepted - rather than only
    its current status. Like the sales order's, the originating opportunity's
    history is merged in on read rather than copied here.
    """

    __tablename__ = "sales_quotation_activity"

    id = Column(Integer, primary_key=True, autoincrement=True)

    quotation_id = Column(
        Integer,
        ForeignKey("sales_quotation.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Short headline, e.g. "Sent To Client" or "Note Logged".
    action = Column(String(150), nullable=False)

    description = Column(String(2000), nullable=True)

    from_status = Column(String(50), nullable=True)
    to_status = Column(String(50), nullable=True)

    created_by = Column(UUID(as_uuid=True), nullable=True)

    created_at = Column(DateTime, default=func.now(), nullable=False)
