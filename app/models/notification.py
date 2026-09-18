from sqlalchemy import Boolean, Column, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.database.base import Base


class Notification(Base):
    """One entry in a user's notification bell.

    Written from each module's Activity History, so every step of the sales
    flow - a lead created or moved, an opportunity staged, a quotation sent,
    an order confirmed, an invoice generated or paid - reaches the people who
    own or oversee the record. One row per recipient keeps read state per user.
    """

    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)

    #: The recipient.
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)

    #: lead / opportunity / quotation / sales_order / proforma_invoice
    module = Column(String(50), nullable=False)
    entity_id = Column(Integer, nullable=False)

    #: The Activity History headline, e.g. "Proforma Invoice Generated".
    action = Column(String(150), nullable=False)

    title = Column(String(200), nullable=False)
    message = Column(String(1000), nullable=True)

    #: Where the bell sends the user, e.g. "/sales/orders/3".
    link = Column(String(300), nullable=True)

    actor_id = Column(UUID(as_uuid=True), nullable=True)
    actor_name = Column(String(200), nullable=True)

    is_read = Column(Boolean, nullable=False, default=False, index=True)
    read_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=func.now(), nullable=False, index=True)
