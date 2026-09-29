from sqlalchemy import Column, Integer, String

from app.database.base import Base


class DocumentCounter(Base):
    """The last reference number handed out for one kind of document.

    Numbers used to be worked out as "the highest row plus one", which
    hands a deleted order's number straight to the next one raised. One
    reference then meant several different orders, and a stock movement
    or an email naming it could not be tied back to the one that caused
    it - which is most of the point of keeping a trail.

    A counter only ever moves forward, so a number is never issued twice.
    It is incremented inside the caller's transaction: an insert that
    rolls back gives the number back, which keeps invoice numbers
    gapless, while a delete does not, which keeps them unique.
    """

    __tablename__ = "document_counter"

    #: Which series this counts - "sales_order", "quotation", and so on.
    name = Column(String(50), primary_key=True)
    #: The last number issued. The next one is this plus one.
    seq = Column(Integer, nullable=False, default=0)
