"""Gate passes: the document goods leave the gate on.

Generated when goods leave - when Logistics marks an order out for
delivery, and when a dispatch is committed on Stock Out - and numbered per
company per financial year (2026-27/001). What it lists is copied in when
it is made, so the pass reads the same later however the order changes.
"""

from datetime import datetime

from sqlalchemy import JSON, Column, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID

from app.database.base import Base


class GatePass(Base):
    __tablename__ = "gate_pass"
    __table_args__ = (
        # Generated numbers never repeat; a number typed on a dispatch by
        # hand (seq null) may, as it always could.
        UniqueConstraint("company_id", "financial_year", "seq", name="uq_gate_pass_company_year_seq"),
        # One pass per dispatch, and one per order sent out by Logistics.
        UniqueConstraint("dispatch_order_id", name="uq_gate_pass_dispatch"),
        # An order can leave on several dispatches, each with its own pass.
        Index("uq_gate_pass_out_for_delivery_order", "sales_order_id", unique=True,
              postgresql_where=text("source = 'OUT_FOR_DELIVERY'")),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    warehouse_id = Column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=True)
    #: "2026-27"; seq is the running number within it, null for a number
    #: typed on the dispatch by hand.
    financial_year = Column(String(9), nullable=False)
    seq = Column(Integer, nullable=True)
    gate_pass_no = Column(String(80), nullable=False)
    gate_pass_date = Column(Date, nullable=False)
    #: OUT_FOR_DELIVERY (Logistics) or DISPATCH (Stock Out).
    source = Column(String(20), nullable=False)
    sales_order_id = Column(Integer, ForeignKey("sales_order.id", ondelete="RESTRICT"), nullable=True, index=True)
    dispatch_order_id = Column(Integer, ForeignKey("dispatch_order.id", ondelete="RESTRICT"), nullable=True)
    order_number = Column(String(60), nullable=True)

    dc_invoice_no = Column(String(80), nullable=True)
    dc_invoice_date = Column(Date, nullable=True)
    transporter = Column(String(200), nullable=True)
    docket_no = Column(String(80), nullable=True)
    eway_bill_no = Column(String(40), nullable=True)
    vehicle_number = Column(String(30), nullable=True)
    driver_name = Column(String(120), nullable=True)

    bill_to = Column(JSON, nullable=True)
    ship_to = Column(JSON, nullable=True)
    #: [{description, detail, serial, qty, purpose, returnable}]
    lines = Column(JSON, nullable=False, default=list)
    remarks = Column(Text, nullable=True)

    created_by = Column(UUID(as_uuid=True), nullable=True)
    created_by_name = Column(String(200), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
