from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.database.base import Base


class LeadActivity(Base):
    """One entry in a lead's Activity History.

    The Lead Details drawer has always shown a timeline, but nothing was ever
    written to it - the cards were placeholders rebuilt from the lead's own
    created_at. This table is what makes that timeline real: every status move
    and every note logged against a lead lands here, in the order it happened.

    The status either side of the move is kept alongside the note so the
    history stays readable after the lead has moved on - "Marked as Qualified"
    on its own loses the fact that it came from CONTACTED.
    """

    __tablename__ = "sales_lead_activity"

    id = Column(Integer, primary_key=True, autoincrement=True)

    lead_id = Column(
        Integer,
        ForeignKey("sales_lead.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Short headline, e.g. "Marked as Qualified" or "Note Logged".
    action = Column(String(150), nullable=False)

    #: The remarks typed by the user when logging the activity.
    description = Column(String(2000), nullable=True)

    from_status = Column(String(50), nullable=True)
    to_status = Column(String(50), nullable=True)

    #: Auth user who logged it. Nullable so a system-generated entry can be
    #: written without inventing a user.
    created_by = Column(UUID(as_uuid=True), nullable=True)

    created_at = Column(DateTime, default=func.now(), nullable=False)
