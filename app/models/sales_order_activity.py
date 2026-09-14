from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.database.base import Base


class SalesOrderActivity(Base):
    """One entry in a sales order's Activity History.

    The order detail panel reads as a single story - the opportunity was
    raised, a demo happened, a proposal went out, the order was created - so
    what is stored here is only the order's own half of it. The service
    merges in the originating opportunity's history on read rather than
    copying those rows, which would go stale the moment the opportunity
    moved on.
    """

    __tablename__ = "sales_order_activity"

    id = Column(Integer, primary_key=True, autoincrement=True)

    sales_order_id = Column(
        Integer,
        ForeignKey("sales_order.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Short headline, e.g. "Sent For Approval" or "Note Logged".
    action = Column(String(150), nullable=False)

    description = Column(String(2000), nullable=True)

    from_status = Column(String(50), nullable=True)
    to_status = Column(String(50), nullable=True)

    created_by = Column(UUID(as_uuid=True), nullable=True)

    created_at = Column(DateTime, default=func.now(), nullable=False)
