from sqlalchemy import Column, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.database.base import Base


class Attachment(Base):
    """One stored file, and who put it there.

    The file on disk is named by a uuid and says nothing about itself.
    This is the record that does: what it was called, how big it is, and
    whose upload it was.

    The uploader matters for more than an audit line. A file is stored
    the moment it is chosen, which is before the lead or order it belongs
    to exists - so for that window there is no record to ask about who
    may read it, and the only honest answer is "the person who uploaded
    it, and whoever may see that person's work".
    """

    __tablename__ = "sales_attachment"

    id = Column(Integer, primary_key=True, autoincrement=True)

    #: The name on disk: uuid4 hex plus the original extension.
    key = Column(String(80), unique=True, nullable=False, index=True)

    #: What the person called it, which is what every list shows.
    name = Column(String(255), nullable=False)

    size = Column(Integer, nullable=False, default=0)
    content_type = Column(String(120), nullable=True)

    uploaded_by = Column(UUID(as_uuid=True), nullable=True, index=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
