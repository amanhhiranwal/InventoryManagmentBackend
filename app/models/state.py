from sqlalchemy import Column, DateTime, Integer, String, func

from app.database.base import Base


class State(Base):
    __tablename__ = "sales_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    #: The postal abbreviation - UP, MP - which is what the dropdowns
    #: show. It is not the tax code and never decides a split.
    code = Column(String(50), unique=True, nullable=False, index=True)

    #: The two-digit GST state code, set on Masters > States. Blank
    #: falls back to the statutory table in app/core/gst.py; a value
    #: here overrides it, so a state the table has never heard of can
    #: be taxed correctly without a deployment.
    gst_code = Column(String(2), nullable=True)
    country = Column(String(100), default="India", nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
