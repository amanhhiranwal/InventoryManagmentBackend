from sqlalchemy import Column, Integer, String, DateTime, func
from app.database.base import Base

class State(Base):
    __tablename__ = "sales_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    code = Column(String(50), unique=True, nullable=False, index=True)
    country = Column(String(100), default="India", nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
