from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.database.base import Base


class OpportunityActivity(Base):
    """One entry in an opportunity's Activity History.

    The Opportunity drawer had the same gap the Lead drawer did: a timeline
    rendered from three hardcoded sample cards, with every real stage move
    leaving no trace. Kept as its own table rather than sharing the lead's so
    each row can cascade with the record it belongs to.
    """

    __tablename__ = "sales_opportunity_activity"

    id = Column(Integer, primary_key=True, autoincrement=True)

    opportunity_id = Column(
        Integer,
        ForeignKey("sales_opportunity.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Short headline, e.g. "Moved to Negotiation" or "Note Logged".
    action = Column(String(150), nullable=False)

    #: The remarks typed by the user when logging the activity.
    description = Column(String(2000), nullable=True)

    from_status = Column(String(50), nullable=True)
    to_status = Column(String(50), nullable=True)

    created_by = Column(UUID(as_uuid=True), nullable=True)

    created_at = Column(DateTime, default=func.now(), nullable=False)
