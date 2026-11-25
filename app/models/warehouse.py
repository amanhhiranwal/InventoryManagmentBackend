"""Bins inside a warehouse, and which warehouses a person may work in.

A warehouse is a location (app/models/location.py) of type WAREHOUSE; it
belongs to exactly one company, so two companies that both have a "Noida"
warehouse have two different rows and can never share stock.

Company access (user_companies) decides which companies a person sees.
Warehouse access, here, narrows that to the warehouses they may work in:
receive goods, pick, dispatch, count. A grant is only valid for a
warehouse of a company already on the person's profile.
"""

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_model import BaseModel


class WarehouseBin(BaseModel):
    __tablename__ = "warehouse_bins"
    __table_args__ = (
        UniqueConstraint("warehouse_id", "code", name="uq_warehouse_bin_code"),
    )

    warehouse_id: Mapped[UUID] = mapped_column(
        ForeignKey("locations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Short and unique within its warehouse, e.g. STORE or A-01-03.
    code: Mapped[str] = mapped_column(String(40), nullable=False)

    name: Mapped[str] = mapped_column(String(120), nullable=False)

    #: STORE, QC or REJECT (app/core/warehouse.py).
    bin_type: Mapped[str] = mapped_column(String(20), nullable=False, default="STORE")

    zone: Mapped[str | None] = mapped_column(String(60), nullable=True)
    rack: Mapped[str | None] = mapped_column(String(60), nullable=True)
    shelf: Mapped[str | None] = mapped_column(String(60), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class UserWarehouse(BaseModel):
    __tablename__ = "user_warehouses"
    __table_args__ = (
        UniqueConstraint("user_id", "warehouse_id", name="uq_user_warehouse"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    warehouse_id: Mapped[UUID] = mapped_column(
        ForeignKey("locations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class RoleWarehouse(BaseModel):
    """A store given to a role in Roles & Access.

    A store given to any role is kept for those roles: only people of those
    roles, or given it themselves under Users, work in it. A store given to
    no role is open to everyone of its company.
    """

    __tablename__ = "role_warehouses"
    __table_args__ = (
        UniqueConstraint("role_id", "warehouse_id", name="uq_role_warehouse"),
    )

    role_id: Mapped[UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    warehouse_id: Mapped[UUID] = mapped_column(
        ForeignKey("locations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
