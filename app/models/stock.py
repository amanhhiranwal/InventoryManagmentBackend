"""Stock: the ledger, the balances it keeps, serials, and stock documents.

The ledger (``inventory_ledger``) is the record of truth. Rows are only
ever inserted, by the posting service, never edited; a mistake is put
right with a reversal or an adjustment. ``stock_balance`` is what the
ledger adds up to, maintained in the same transaction so a screen does
not have to sum the ledger, and rebuilt from it by the reconciliation.

Every row carries its company and warehouse. A warehouse belongs to one
company, so stock of two companies can never meet in one balance.

Products themselves stay in MongoDB; they are referred to by SKU (the
catalogue's model number), with the product name kept on each row as it
was when the stock moved.

People are held as user ids without a foreign key, as the procurement and
sales tables do.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
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


class StockBalance(Base):
    __tablename__ = "stock_balance"
    __table_args__ = (
        UniqueConstraint("company_id", "warehouse_id", "bin_id", "sku", "stock_status", name="uq_stock_balance_key"),
        CheckConstraint("qty_on_hand >= 0", name="ck_stock_balance_on_hand"),
        CheckConstraint("qty_reserved >= 0", name="ck_stock_balance_reserved"),
        CheckConstraint("qty_reserved <= qty_on_hand", name="ck_stock_balance_reserved_le_on_hand"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    warehouse_id = Column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False, index=True)
    bin_id = Column(UUID(as_uuid=True), ForeignKey("warehouse_bins.id", ondelete="RESTRICT"), nullable=False)
    sku = Column(String(100), nullable=False, index=True)
    product_name = Column(String(300), nullable=True)
    stock_status = Column(String(20), nullable=False, default="UNRESTRICTED")
    qty_on_hand = Column(QTY, nullable=False, default=0)
    qty_reserved = Column(QTY, nullable=False, default=0)
    #: Weighted average cost of what is on hand, per unit.
    avg_cost = Column(MONEY, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class StockLedger(Base):
    __tablename__ = "inventory_ledger"
    __table_args__ = (
        CheckConstraint("quantity <> 0", name="ck_inventory_ledger_quantity"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    warehouse_id = Column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False, index=True)
    bin_id = Column(UUID(as_uuid=True), ForeignKey("warehouse_bins.id", ondelete="RESTRICT"), nullable=False)
    sku = Column(String(100), nullable=False, index=True)
    product_name = Column(String(300), nullable=True)
    serial_no = Column(String(120), nullable=True, index=True)
    movement_type = Column(String(30), nullable=False, index=True)
    stock_status = Column(String(20), nullable=False)
    #: Signed: positive in, negative out.
    quantity = Column(QTY, nullable=False)
    uom = Column(String(30), nullable=True)
    unit_cost = Column(MONEY, nullable=True)
    source_doc_type = Column(String(40), nullable=False)
    source_doc_id = Column(String(64), nullable=False, index=True)
    source_doc_number = Column(String(60), nullable=True)
    source_line_id = Column(String(64), nullable=True)
    movement_date = Column(Date, nullable=False)
    posted_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_by = Column(UUID(as_uuid=True), nullable=True)
    created_by_name = Column(String(200), nullable=True)
    approved_by = Column(UUID(as_uuid=True), nullable=True)
    approved_by_name = Column(String(200), nullable=True)
    #: One posting of one line, once. A retry or a second click hits this.
    idempotency_key = Column(String(160), nullable=False, unique=True)
    reversal_of_movement_id = Column(BigInteger, ForeignKey("inventory_ledger.id", ondelete="RESTRICT"), nullable=True)
    remarks = Column(Text, nullable=True)


class StockSerial(Base):
    """A serial-numbered unit and where it is now."""

    __tablename__ = "stock_serial"
    __table_args__ = (
        UniqueConstraint("company_id", "serial_no", name="uq_stock_serial_company_serial"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    sku = Column(String(100), nullable=False, index=True)
    serial_no = Column(String(120), nullable=False)
    warehouse_id = Column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=True, index=True)
    bin_id = Column(UUID(as_uuid=True), ForeignKey("warehouse_bins.id", ondelete="RESTRICT"), nullable=True)
    status = Column(String(20), nullable=False, default="IN_STOCK")
    #: The ledger row that brought it in.
    origin_movement_id = Column(BigInteger, ForeignKey("inventory_ledger.id", ondelete="RESTRICT"), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class StockDocument(Base):
    """Opening stock, stock adjustments in and out, and vendor returns.

    Drafted and submitted by one person, approved and posted by another.
    Nothing reaches the ledger before it is POSTED.
    """

    __tablename__ = "stock_document"

    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_number = Column(String(60), nullable=False, unique=True)
    doc_type = Column(String(20), nullable=False, index=True)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    warehouse_id = Column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="DRAFT", index=True)
    movement_date = Column(Date, nullable=False)
    reason = Column(String(300), nullable=True)
    #: Adjustments: DAMAGE, LOSS, FOUND ... (app/core/stock.py).
    reason_code = Column(String(30), nullable=True)
    #: Vendor returns: the GRN the goods came in on.
    grn_id = Column(Integer, ForeignKey("goods_receipt.id", ondelete="RESTRICT"), nullable=True, index=True)
    remarks = Column(Text, nullable=True)
    created_by = Column(UUID(as_uuid=True), nullable=True)
    created_by_name = Column(String(200), nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    approved_by = Column(UUID(as_uuid=True), nullable=True)
    approved_by_name = Column(String(200), nullable=True)
    posted_at = Column(DateTime, nullable=True)
    rejected_by_name = Column(String(200), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    lines = relationship(
        "StockDocumentLine",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="StockDocumentLine.line_no",
    )


class StockDocumentLine(Base):
    __tablename__ = "stock_document_line"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_stock_document_line_quantity"),
        UniqueConstraint("document_id", "line_no", name="uq_stock_document_line_no"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("stock_document.id", ondelete="CASCADE"), nullable=False, index=True)
    line_no = Column(Integer, nullable=False)
    sku = Column(String(100), nullable=False)
    product_name = Column(String(300), nullable=True)
    bin_id = Column(UUID(as_uuid=True), ForeignKey("warehouse_bins.id", ondelete="RESTRICT"), nullable=False)
    quantity = Column(QTY, nullable=False)
    unit_cost = Column(MONEY, nullable=True)
    #: Serial numbers on this line; empty for products counted by quantity.
    serials = Column(JSON, nullable=True)
    #: The stock status the line moves: UNRESTRICTED, or for a vendor
    #: return the REJECTED / BLOCKED goods the GRN set aside.
    stock_status = Column(String(20), nullable=True)
    #: Vendor returns: the GRN line the goods came in on.
    grn_line_id = Column(Integer, ForeignKey("goods_receipt_line.id", ondelete="RESTRICT"), nullable=True)

    document = relationship("StockDocument", back_populates="lines")
