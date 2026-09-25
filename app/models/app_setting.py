from sqlalchemy import Column, DateTime, String, Text, func

from app.database.base import Base


class AppSetting(Base):
    """One configurable value, set from the UI rather than the environment.

    Everything here has an environment variable behind it as a fallback, so
    an installation that has never opened the settings screen still prints
    and emails sensibly. What is stored here wins.
    """

    __tablename__ = "app_setting"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=True)

    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=True
    )
