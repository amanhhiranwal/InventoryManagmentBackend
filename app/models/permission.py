from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_model import BaseModel


class Permission(BaseModel):
    __tablename__ = "permissions"

    permission_name: Mapped[str] = mapped_column(
        String(150),
        unique=True,
        nullable=False,
    )

    module: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )