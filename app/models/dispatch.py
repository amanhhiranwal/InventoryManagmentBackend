"""Stock out: sales order lines, reservations and dispatch orders.

A sales order keeps its lines as JSON for the screens that already read
them; ``sales_order_line`` gives each goods line a stable identity so
stock can be reserved and dispatched against it, partly and more than once.

A reservation holds available stock for one line in one warehouse bin. It
raises ``stock_balance.qty_reserved`` and leaves on hand alone.

A dispatch order sends some or all of a sales order out of one warehouse.
Picking and packing allocate serials but move no stock; committing it is
the stock-out (SALES_DISPATCH), exactly once, and consumes the
reservation. Proof of delivery moves nothing.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.base import Base

QTY = Numeric(14, 3)


class SalesOrderLine(Base):
    __tablename__ = "sales_order_line"
    __table_args__ = (
        UniqueConstraint("sales_order_id", "line_index", name="uq_sales_order_line_index"),
        CheckConstraint("qty_ordered >= 0", name="ck_sales_order_line_ordered"),
        CheckConstraint("qty_dispatched <= qty_ordered", name="ck_sales_order_line_dispatched"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    sales_order_id = Column(Integer, ForeignKey("sales_order.id", ondelete="CASCADE"), nullable=False, index=True)
    #: Position of the line in sales_order.items, which screens still read.
    line_index = Column(Integer, nullable=False)
    sku = Column(String(100), nullable=False, index=True)
    product_name = Column(String(300), nullable=True)
    uom = Column(String(30), nullable=True)
    qty_ordered = Column(QTY, nullable=False)
    qty_dispatched = Column(QTY, nullable=False, default=0)
    qty_delivered = Column(QTY, nullable=False, default=0)


class StockReservation(Base):
    __tablename__ = "stock_reservation"
    __table_args__ = (
        CheckConstraint("qty > 0", name="ck_stock_reservation_qty"),
        CheckConstraint("qty_consumed >= 0 AND qty_consumed <= qty", name="ck_stock_reservation_consumed"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    warehouse_id = Column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False, index=True)
    bin_id = Column(UUID(as_uuid=True), ForeignKey("warehouse_bins.id", ondelete="RESTRICT"), nullable=False)
    sku = Column(String(100), nullable=False, index=True)
    so_line_id = Column(Integer, ForeignKey("sales_order_line.id", ondelete="RESTRICT"), nullable=False, index=True)
    qty = Column(QTY, nullable=False)
    qty_consumed = Column(QTY, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="ACTIVE", index=True)
    created_by = Column(UUID(as_uuid=True), nullable=True)
    created_by_name = Column(String(200), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    released_at = Column(DateTime, nullable=True)
    released_reason = Column(String(300), nullable=True)


class DispatchOrder(Base):
    __tablename__ = "dispatch_order"
    __table_args__ = (
        UniqueConstraint("company_id", "do_number", name="uq_dispatch_order_company_number"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    do_number = Column(String(40), nullable=False)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    warehouse_id = Column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False, index=True)
    sales_order_id = Column(Integer, ForeignKey("sales_order.id", ondelete="RESTRICT"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="DRAFT", index=True)
    dispatch_date = Column(Date, nullable=True)
    ship_to = Column(Text, nullable=True)
    # Transport and documents.
    transporter = Column(String(200), nullable=True)
    vehicle_number = Column(String(30), nullable=True)
    driver_name = Column(String(120), nullable=True)
    docket_no = Column(String(80), nullable=True)
    challan_no = Column(String(80), nullable=True)
    challan_date = Column(Date, nullable=True)
    invoice_no = Column(String(80), nullable=True)
    eway_bill_no = Column(String(40), nullable=True)
    eway_bill_date = Column(Date, nullable=True)
    eway_valid_until = Column(Date, nullable=True)
    gate_pass_no = Column(String(80), nullable=True)
    packages = Column(Integer, nullable=True)
    gross_weight = Column(String(40), nullable=True)
    consignment_value = Column(Numeric(14, 2), nullable=True)
    remarks = Column(Text, nullable=True)
    # Who and when.
    created_by = Column(UUID(as_uuid=True), nullable=True)
    created_by_name = Column(String(200), nullable=True)
    committed_by = Column(UUID(as_uuid=True), nullable=True)
    committed_by_name = Column(String(200), nullable=True)
    committed_at = Column(DateTime, nullable=True)
    # Proof of delivery.
    delivered_at = Column(DateTime, nullable=True)
    pod_receiver = Column(String(200), nullable=True)
    pod_remarks = Column(Text, nullable=True)
    pod_recorded_by_name = Column(String(200), nullable=True)
    cancelled_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    lines = relationship("DispatchLine", back_populates="dispatch", cascade="all, delete-orphan", order_by="DispatchLine.id")


class DispatchLine(Base):
    __tablename__ = "dispatch_line"
    __table_args__ = (
        CheckConstraint("qty > 0", name="ck_dispatch_line_qty"),
        CheckConstraint("qty_picked >= 0 AND qty_picked <= qty", name="ck_dispatch_line_picked"),
        UniqueConstraint("dispatch_id", "so_line_id", name="uq_dispatch_line_so_line"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    dispatch_id = Column(Integer, ForeignKey("dispatch_order.id", ondelete="CASCADE"), nullable=False, index=True)
    so_line_id = Column(Integer, ForeignKey("sales_order_line.id", ondelete="RESTRICT"), nullable=False, index=True)
    sku = Column(String(100), nullable=False)
    product_name = Column(String(300), nullable=True)
    qty = Column(QTY, nullable=False)
    qty_picked = Column(QTY, nullable=False, default=0)
    #: Serials picked for this line, one per unit, for serial-numbered goods.
    serials = Column(JSON, nullable=True)

    dispatch = relationship("DispatchOrder", back_populates="lines")
