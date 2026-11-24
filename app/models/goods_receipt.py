"""Goods received at a warehouse: the GRN.

Laid out as the GRN sheet the warehouse already keeps: a number per
financial year (2026-27/083), the date of receipt, the PO it is against
(or none, for goods coming back or arriving without papers), the
supplier's name and address, their delivery note or invoice number and
date, the warehouse and how the goods came in, and one line per product
with the quantity ordered, received, accepted and rejected and a remark.

A GRN changes no stock while it is a draft or submitted. Posting the
receipt puts everything received into stock in QUARANTINE (the QC bin) and
moves the PO's received quantities. Quality check then moves accepted goods
to available stock and rejected or damaged goods to the rejected-goods bin.
Both postings go through the stock ledger.
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


class GoodsReceipt(Base):
    __tablename__ = "goods_receipt"
    __table_args__ = (
        UniqueConstraint("company_id", "grn_number", name="uq_goods_receipt_company_number"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    #: 2026-27/083 - per company and financial year, as on the sheet.
    grn_number = Column(String(30), nullable=False)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    warehouse_id = Column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False, index=True)
    receipt_type = Column(String(20), nullable=False, default="PURCHASE")
    purchase_order_id = Column(Integer, ForeignKey("purchase_order.id", ondelete="RESTRICT"), nullable=True, index=True)
    #: A customer return: the dispatch the goods went out on.
    dispatch_order_id = Column(Integer, ForeignKey("dispatch_order.id", ondelete="RESTRICT"), nullable=True, index=True)
    vendor_id = Column(Integer, ForeignKey("vendor.id", ondelete="SET NULL"), nullable=True)
    supplier_name = Column(String(200), nullable=False)
    supplier_address = Column(String(600), nullable=True)
    #: "Delivery Note / Invoice Number & Date" on the sheet.
    delivery_note_no = Column(String(120), nullable=True)
    delivery_note_date = Column(Date, nullable=True)
    receipt_date = Column(Date, nullable=False)
    #: How it came in - "Received from PORTER", a vehicle number.
    transport_remarks = Column(String(300), nullable=True)
    remarks = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="DRAFT", index=True)

    received_by = Column(UUID(as_uuid=True), nullable=True)
    received_by_name = Column(String(200), nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    #: Who posted the physical receipt into quarantine, and when.
    receipt_posted_by = Column(UUID(as_uuid=True), nullable=True)
    receipt_posted_by_name = Column(String(200), nullable=True)
    receipt_posted_at = Column(DateTime, nullable=True)
    inspected_by = Column(UUID(as_uuid=True), nullable=True)
    inspected_by_name = Column(String(200), nullable=True)
    posted_at = Column(DateTime, nullable=True)
    cancelled_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    lines = relationship(
        "GoodsReceiptLine",
        back_populates="receipt",
        cascade="all, delete-orphan",
        order_by="GoodsReceiptLine.line_no",
    )


class GoodsReceiptLine(Base):
    __tablename__ = "goods_receipt_line"
    __table_args__ = (
        UniqueConstraint("grn_id", "line_no", name="uq_goods_receipt_line_no"),
        CheckConstraint("qty_received > 0", name="ck_goods_receipt_line_received"),
        CheckConstraint(
            "qty_accepted IS NULL OR (qty_accepted >= 0 AND qty_rejected >= 0 AND COALESCE(qty_damaged, 0) >= 0 "
            "AND qty_accepted + qty_rejected + COALESCE(qty_damaged, 0) = qty_received)",
            name="ck_goods_receipt_line_split",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    grn_id = Column(Integer, ForeignKey("goods_receipt.id", ondelete="CASCADE"), nullable=False, index=True)
    line_no = Column(Integer, nullable=False)
    po_item_id = Column(Integer, ForeignKey("purchase_order_item.id", ondelete="RESTRICT"), nullable=True, index=True)
    #: A customer return: the dispatch line the goods went out on.
    dispatch_line_id = Column(Integer, ForeignKey("dispatch_line.id", ondelete="RESTRICT"), nullable=True, index=True)
    sku = Column(String(100), nullable=False)
    description = Column(String(300), nullable=True)
    uom = Column(String(30), nullable=True)
    qty_ordered = Column(QTY, nullable=True)
    qty_received = Column(QTY, nullable=False)
    #: Filled by the inspector; accepted + rejected + damaged = received.
    qty_accepted = Column(QTY, nullable=True)
    qty_rejected = Column(QTY, nullable=True)
    qty_damaged = Column(QTY, nullable=True)
    #: Where accepted goods are put; rejected go to the REJECT bin.
    bin_id = Column(UUID(as_uuid=True), ForeignKey("warehouse_bins.id", ondelete="RESTRICT"), nullable=True)
    unit_cost = Column(Numeric(14, 2), nullable=True)
    #: Serials of the accepted units, for serial-numbered products.
    serials = Column(JSON, nullable=True)
    remarks = Column(String(500), nullable=True)

    receipt = relationship("GoodsReceipt", back_populates="lines")
