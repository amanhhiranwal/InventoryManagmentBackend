"""Moving stock between two warehouses of the same company, and counting it.

A transfer never crosses companies: goods going to another company are a
sale by one and a purchase by the other, with their own documents. It is
requested, approved by someone else, shipped (stock leaves the sending
warehouse and waits IN_TRANSIT at the receiving one) and received (in
transit becomes available stock, with any shortage or damage recorded).

A cycle count snapshots what the ledger says a warehouse holds, takes the
counted quantities, and on approval posts each difference as an
adjustment, so the ledger stays the only record of stock.
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
MONEY = Numeric(14, 2)


class StockTransfer(Base):
    __tablename__ = "stock_transfer"
    __table_args__ = (
        CheckConstraint("from_warehouse_id <> to_warehouse_id", name="ck_stock_transfer_two_warehouses"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    transfer_number = Column(String(60), nullable=False, unique=True)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    from_warehouse_id = Column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False, index=True)
    to_warehouse_id = Column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="DRAFT", index=True)
    transfer_date = Column(Date, nullable=False)
    reason = Column(String(300), nullable=True)
    remarks = Column(Text, nullable=True)
    #: How it travels: vehicle, docket, challan.
    vehicle_number = Column(String(30), nullable=True)
    docket_no = Column(String(80), nullable=True)
    challan_no = Column(String(80), nullable=True)

    created_by = Column(UUID(as_uuid=True), nullable=True)
    created_by_name = Column(String(200), nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    approved_by = Column(UUID(as_uuid=True), nullable=True)
    approved_by_name = Column(String(200), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    shipped_by = Column(UUID(as_uuid=True), nullable=True)
    shipped_by_name = Column(String(200), nullable=True)
    shipped_at = Column(DateTime, nullable=True)
    received_by = Column(UUID(as_uuid=True), nullable=True)
    received_by_name = Column(String(200), nullable=True)
    received_at = Column(DateTime, nullable=True)
    receipt_remarks = Column(Text, nullable=True)
    cancelled_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    lines = relationship("StockTransferLine", back_populates="transfer", cascade="all, delete-orphan",
                         order_by="StockTransferLine.line_no")


class StockTransferLine(Base):
    __tablename__ = "stock_transfer_line"
    __table_args__ = (
        UniqueConstraint("transfer_id", "line_no", name="uq_stock_transfer_line_no"),
        CheckConstraint("qty > 0", name="ck_stock_transfer_line_qty"),
        CheckConstraint(
            "qty_received IS NULL OR (qty_received >= 0 AND COALESCE(qty_damaged, 0) >= 0 "
            "AND qty_received + COALESCE(qty_damaged, 0) <= qty)",
            name="ck_stock_transfer_line_received",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    transfer_id = Column(Integer, ForeignKey("stock_transfer.id", ondelete="CASCADE"), nullable=False, index=True)
    line_no = Column(Integer, nullable=False)
    sku = Column(String(100), nullable=False)
    product_name = Column(String(300), nullable=True)
    #: Taken from here in the sending warehouse.
    from_bin_id = Column(UUID(as_uuid=True), ForeignKey("warehouse_bins.id", ondelete="RESTRICT"), nullable=False)
    #: Put here in the receiving warehouse, chosen on receipt.
    to_bin_id = Column(UUID(as_uuid=True), ForeignKey("warehouse_bins.id", ondelete="RESTRICT"), nullable=True)
    qty = Column(QTY, nullable=False)
    #: On receipt: good and damaged; the rest of qty is short.
    qty_received = Column(QTY, nullable=True)
    qty_damaged = Column(QTY, nullable=True)
    #: The sending bin's average cost when it shipped.
    unit_cost = Column(MONEY, nullable=True)
    serials = Column(JSON, nullable=True)
    #: Serials that arrived damaged; the ones in neither list are short.
    serials_damaged = Column(JSON, nullable=True)
    serials_received = Column(JSON, nullable=True)
    remarks = Column(String(500), nullable=True)

    transfer = relationship("StockTransfer", back_populates="lines")


class StockCount(Base):
    __tablename__ = "stock_count"

    id = Column(Integer, primary_key=True, autoincrement=True)
    count_number = Column(String(60), nullable=False, unique=True)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    warehouse_id = Column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False, index=True)
    #: Counting one bin, or (empty) every store bin of the warehouse.
    bin_id = Column(UUID(as_uuid=True), ForeignKey("warehouse_bins.id", ondelete="RESTRICT"), nullable=True)
    status = Column(String(20), nullable=False, default="OPEN", index=True)
    count_date = Column(Date, nullable=False)
    remarks = Column(Text, nullable=True)
    created_by = Column(UUID(as_uuid=True), nullable=True)
    created_by_name = Column(String(200), nullable=True)
    submitted_by = Column(UUID(as_uuid=True), nullable=True)
    submitted_by_name = Column(String(200), nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    approved_by = Column(UUID(as_uuid=True), nullable=True)
    approved_by_name = Column(String(200), nullable=True)
    posted_at = Column(DateTime, nullable=True)
    cancelled_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    lines = relationship("StockCountLine", back_populates="count", cascade="all, delete-orphan",
                         order_by="StockCountLine.line_no")


class StockCountLine(Base):
    __tablename__ = "stock_count_line"
    __table_args__ = (
        UniqueConstraint("count_id", "line_no", name="uq_stock_count_line_no"),
        CheckConstraint("qty_counted IS NULL OR qty_counted >= 0", name="ck_stock_count_line_counted"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    count_id = Column(Integer, ForeignKey("stock_count.id", ondelete="CASCADE"), nullable=False, index=True)
    line_no = Column(Integer, nullable=False)
    bin_id = Column(UUID(as_uuid=True), ForeignKey("warehouse_bins.id", ondelete="RESTRICT"), nullable=False)
    sku = Column(String(100), nullable=False)
    product_name = Column(String(300), nullable=True)
    #: What the ledger said when the count started.
    qty_system = Column(QTY, nullable=False)
    qty_counted = Column(QTY, nullable=True)
    unit_cost = Column(MONEY, nullable=True)
    remarks = Column(String(500), nullable=True)

    count = relationship("StockCount", back_populates="lines")
