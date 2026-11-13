"""Procurement: from a requisition an employee raises to the purchase order.

    Employee -> Purchase Requisition -> approval chain (reporting manager,
    and above it by amount) -> Procurement desk -> vendor quotations entered
    by hand -> comparison -> vendor selection approval -> Purchase Order.

There is no vendor portal: vendors are a master the procurement team keeps,
and every quotation is typed in from what the vendor sent offline, with the
vendor's own document attached.

Nothing here touches stock. A PO records what was ordered; goods are
received against it by a GRN process that is not part of this module, so
the received quantities on PO lines start at nought and stay there until
one exists.

People are held as user ids (UUID) without a foreign key, as the sales
approvals do: the users table is mirrored across the services' databases.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.base import Base


# ----------------------------------------------------------------------
# Masters
# ----------------------------------------------------------------------
class Department(Base):
    """A department people belong to, and who heads it.

    The head is the Department Head step of a requisition's approval chain
    for anyone in the department.
    """

    __tablename__ = "department"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(150), nullable=False, unique=True)
    code = Column(String(30), nullable=True, unique=True)
    head_user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)


class CostCenter(Base):
    """What a purchase is charged to, optionally under a department."""

    __tablename__ = "cost_center"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(30), nullable=False, unique=True)
    name = Column(String(150), nullable=False)
    department_id = Column(Integer, ForeignKey("department.id", ondelete="SET NULL"), nullable=True, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)


class Vendor(Base):
    """A supplier, kept by the procurement team. Vendors never sign in."""

    __tablename__ = "vendor"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vendor_code = Column(String(30), nullable=False, unique=True, index=True)
    name = Column(String(200), nullable=False, index=True)
    vendor_type = Column(String(50), nullable=True)

    contact_person = Column(String(150), nullable=True)
    designation = Column(String(100), nullable=True)
    mobile = Column(String(20), nullable=True)
    email = Column(String(200), nullable=True)

    address = Column(String(500), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    pin_code = Column(String(10), nullable=True)

    gstin = Column(String(20), nullable=True)
    pan = Column(String(15), nullable=True)

    bank_name = Column(String(150), nullable=True)
    account_holder = Column(String(150), nullable=True)
    account_number = Column(String(40), nullable=True)
    ifsc = Column(String(15), nullable=True)
    branch = Column(String(150), nullable=True)

    product_categories = Column(JSON, nullable=True)
    brands = Column(JSON, nullable=True)

    payment_terms = Column(String(300), nullable=True)
    credit_days = Column(Integer, nullable=True)
    freight_terms = Column(String(300), nullable=True)
    lead_time_days = Column(Integer, nullable=True)
    warranty_terms = Column(String(300), nullable=True)

    #: {"gst": key, "pan": key, "bank": key, "msme": key, "other": [ ... ]},
    #: each an uploaded attachment {key, name, size, type}.
    documents = Column(JSON, nullable=True)

    status = Column(String(30), nullable=False, default="DRAFT", index=True)
    documents_verified = Column(Boolean, nullable=False, default=False)
    verified_by = Column(UUID(as_uuid=True), nullable=True)
    verified_at = Column(DateTime, nullable=True)
    approved_by = Column(UUID(as_uuid=True), nullable=True)
    approved_at = Column(DateTime, nullable=True)

    remarks = Column(Text, nullable=True)

    created_by = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)


# ----------------------------------------------------------------------
# The requisition
# ----------------------------------------------------------------------
class PurchaseRequisition(Base):
    """One employee's request to buy.

    Two statuses, kept apart so neither is overloaded: ``status`` is the
    requisition's own life (draft, approval, approved, rejected), and
    ``procurement_status`` is where the procurement team has got to with it
    once it is approved.
    """

    __tablename__ = "purchase_requisition"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pr_number = Column(String(30), nullable=False, unique=True, index=True)
    pr_date = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Who asked - copied at the time so the PR still reads after a move.
    requested_by = Column(UUID(as_uuid=True), nullable=False, index=True)
    requester_name = Column(String(200), nullable=True)
    employee_id = Column(String(50), nullable=True)
    designation = Column(String(150), nullable=True)
    department_id = Column(Integer, ForeignKey("department.id", ondelete="SET NULL"), nullable=True, index=True)
    department_name = Column(String(150), nullable=True)
    # Kept for requisitions raised before the company became the cost
    # centre; nothing writes them now.
    cost_center_id = Column(Integer, ForeignKey("cost_center.id", ondelete="SET NULL"), nullable=True)
    cost_center_code = Column(String(60), nullable=True)
    # The cost centre: which of the group's companies the purchase is for.
    # Its name is copied so the PR still reads if the company is renamed.
    company_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    company_name = Column(String(255), nullable=True)
    reporting_manager_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    reporting_manager_name = Column(String(200), nullable=True)

    purchase_type = Column(String(40), nullable=False)
    priority = Column(String(20), nullable=False, default="NORMAL")
    required_by = Column(Date, nullable=True)
    currency = Column(String(10), nullable=False, default="INR")

    # What it is for.
    sales_order_id = Column(Integer, ForeignKey("sales_order.id", ondelete="SET NULL"), nullable=True, index=True)
    sales_order_number = Column(String(50), nullable=True)
    customer_name = Column(String(200), nullable=True)
    project_code = Column(String(100), nullable=True)
    linked_record = Column(String(200), nullable=True)
    delivery_location = Column(String(300), nullable=True)
    purpose = Column(Text, nullable=True)
    remarks = Column(Text, nullable=True)

    # Budget, as the requisition form asks for it.
    budget_code = Column(String(100), nullable=True)
    budget_available = Column(Float, nullable=True)
    budgeted = Column(Boolean, nullable=True)
    preferred_vendor_id = Column(Integer, ForeignKey("vendor.id", ondelete="SET NULL"), nullable=True)
    preferred_vendor_name = Column(String(200), nullable=True)
    preferred_vendor_reason = Column(String(500), nullable=True)
    alternatives_considered = Column(String(1000), nullable=True)
    special_requirements = Column(String(1000), nullable=True)

    attachments = Column(JSON, nullable=True)

    estimated_total = Column(Float, nullable=False, default=0.0)

    # The approval.
    status = Column(String(30), nullable=False, default="DRAFT", index=True)
    approval_round = Column(Integer, nullable=False, default=0)
    current_level = Column(Integer, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    resubmitted = Column(Boolean, nullable=False, default=False)

    # The procurement team's side.
    procurement_status = Column(String(40), nullable=True, index=True)
    procurement_since = Column(DateTime, nullable=True)
    assigned_to = Column(UUID(as_uuid=True), nullable=True, index=True)
    assigned_name = Column(String(200), nullable=True)
    procurement_remarks = Column(Text, nullable=True)
    internal_notes = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)

    items = relationship(
        "PurchaseRequisitionItem",
        back_populates="requisition",
        order_by="PurchaseRequisitionItem.line_no",
        cascade="all, delete-orphan",
    )


class PurchaseRequisitionItem(Base):
    """One line of a requisition.

    ``quantity`` is what the employee asked for and is never changed by
    procurement; a different figure goes in ``approved_quantity`` with the
    reason beside it.
    """

    __tablename__ = "purchase_requisition_item"

    id = Column(Integer, primary_key=True, autoincrement=True)
    requisition_id = Column(Integer, ForeignKey("purchase_requisition.id", ondelete="CASCADE"), nullable=False, index=True)
    line_no = Column(Integer, nullable=False, default=1)

    product_name = Column(String(300), nullable=False)
    sku = Column(String(100), nullable=True)
    category = Column(String(150), nullable=True)
    brand = Column(String(150), nullable=True)
    model = Column(String(150), nullable=True)
    description = Column(Text, nullable=True)
    specification = Column(Text, nullable=True)
    uom = Column(String(30), nullable=False, default="Nos")

    quantity = Column(Float, nullable=False)
    approved_quantity = Column(Float, nullable=True)
    quantity_change_reason = Column(String(500), nullable=True)

    est_unit_price = Column(Float, nullable=True)
    tax_percent = Column(Float, nullable=True)
    est_total = Column(Float, nullable=False, default=0.0)

    required_date = Column(Date, nullable=True)
    preferred_vendor_id = Column(Integer, ForeignKey("vendor.id", ondelete="SET NULL"), nullable=True)
    #: The vendors procurement is asking to quote for this line (ids). Set
    #: when quotations are requested; several vendors per line.
    rfq_vendor_ids = Column(JSON, nullable=True)
    remarks = Column(String(1000), nullable=True)
    attachments = Column(JSON, nullable=True)

    requisition = relationship("PurchaseRequisition", back_populates="items")


class PurchaseRequisitionApproval(Base):
    """One step of a requisition's approval, in one round.

    A round starts at each submission; returning a PR for correction ends
    it, and the next submission starts the next round with a fresh chain,
    so every earlier decision stays on record.
    """

    __tablename__ = "purchase_requisition_approval"
    __table_args__ = (
        UniqueConstraint("requisition_id", "round", "level", name="uq_pr_approval_step"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    requisition_id = Column(Integer, ForeignKey("purchase_requisition.id", ondelete="CASCADE"), nullable=False, index=True)
    round = Column(Integer, nullable=False)
    level = Column(Integer, nullable=False)

    #: REPORTING_MANAGER / DEPARTMENT_HEAD / FINANCE / DIRECTOR
    step = Column(String(40), nullable=False)
    #: Who may decide this step. Empty means the super admins, who stand
    #: in where nobody holds the step.
    approver_ids = Column(JSON, nullable=False, default=list)
    approver_label = Column(String(300), nullable=True)

    #: PENDING / WAITING / APPROVED / REJECTED / RETURNED / SKIPPED
    decision = Column(String(20), nullable=False, default="WAITING")
    decided_by = Column(UUID(as_uuid=True), nullable=True)
    decided_by_name = Column(String(200), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    remarks = Column(String(2000), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)


# ----------------------------------------------------------------------
# Quotations, collected offline and entered by hand
# ----------------------------------------------------------------------
class QuotationCollection(Base):
    """The procurement team noting that it has started asking vendors.

    Internal tracking only - nothing is sent to anyone.
    """

    __tablename__ = "quotation_collection"

    id = Column(Integer, primary_key=True, autoincrement=True)
    reference = Column(String(30), nullable=False, unique=True)
    requisition_id = Column(Integer, ForeignKey("purchase_requisition.id", ondelete="CASCADE"), nullable=False, index=True)
    vendor_ids = Column(JSON, nullable=True)
    #: {requisition item id: [vendor ids]} - which vendors were asked for which line.
    item_vendor_ids = Column(JSON, nullable=True)
    notes = Column(Text, nullable=True)
    started_by = Column(UUID(as_uuid=True), nullable=True)
    started_by_name = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)


class VendorQuotation(Base):
    """A quotation a vendor gave, as procurement typed it in.

    A revision is a new row pointing at the original (``root_id``), so the
    figures, attachment and date of every version are kept. Only the
    newest of a chain has ``is_latest``.
    """

    __tablename__ = "vendor_quotation"

    id = Column(Integer, primary_key=True, autoincrement=True)
    quotation_number = Column(String(30), nullable=False, unique=True, index=True)
    requisition_id = Column(Integer, ForeignKey("purchase_requisition.id", ondelete="CASCADE"), nullable=False, index=True)
    vendor_id = Column(Integer, ForeignKey("vendor.id", ondelete="RESTRICT"), nullable=False, index=True)
    vendor_name = Column(String(200), nullable=True)

    root_id = Column(Integer, ForeignKey("vendor_quotation.id", ondelete="CASCADE"), nullable=True, index=True)
    version = Column(Integer, nullable=False, default=0)
    version_label = Column(String(40), nullable=False, default="Original")
    is_latest = Column(Boolean, nullable=False, default=True, index=True)

    vendor_reference = Column(String(100), nullable=True)
    quotation_date = Column(Date, nullable=True)
    valid_until = Column(Date, nullable=True)
    received_date = Column(Date, nullable=True)
    received_by = Column(UUID(as_uuid=True), nullable=True)
    received_by_name = Column(String(200), nullable=True)

    currency = Column(String(10), nullable=False, default="INR")
    payment_terms = Column(String(300), nullable=True)
    delivery_terms = Column(String(300), nullable=True)
    freight_terms = Column(String(300), nullable=True)
    delivery_days = Column(Integer, nullable=True)
    warranty_terms = Column(String(300), nullable=True)
    remarks = Column(Text, nullable=True)
    attachments = Column(JSON, nullable=True)

    status = Column(String(20), nullable=False, default="RECEIVED", index=True)

    subtotal = Column(Float, nullable=False, default=0.0)
    discount_total = Column(Float, nullable=False, default=0.0)
    taxable_total = Column(Float, nullable=False, default=0.0)
    gst_total = Column(Float, nullable=False, default=0.0)
    charges_total = Column(Float, nullable=False, default=0.0)
    grand_total = Column(Float, nullable=False, default=0.0)

    entered_by = Column(UUID(as_uuid=True), nullable=True)
    entered_by_name = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)

    items = relationship(
        "VendorQuotationItem",
        back_populates="quotation",
        order_by="VendorQuotationItem.id",
        cascade="all, delete-orphan",
    )


class VendorQuotationItem(Base):
    __tablename__ = "vendor_quotation_item"

    id = Column(Integer, primary_key=True, autoincrement=True)
    quotation_id = Column(Integer, ForeignKey("vendor_quotation.id", ondelete="CASCADE"), nullable=False, index=True)
    requisition_item_id = Column(Integer, ForeignKey("purchase_requisition_item.id", ondelete="SET NULL"), nullable=True, index=True)

    product_name = Column(String(300), nullable=True)
    sku = Column(String(100), nullable=True)
    vendor_product_name = Column(String(300), nullable=True)
    vendor_model = Column(String(150), nullable=True)
    brand = Column(String(150), nullable=True)
    hsn = Column(String(20), nullable=True)

    quantity = Column(Float, nullable=False, default=0.0)
    uom = Column(String(30), nullable=True)
    unit_rate = Column(Float, nullable=False, default=0.0)
    discount_type = Column(String(10), nullable=False, default="PERCENT")
    discount_value = Column(Float, nullable=False, default=0.0)
    discount_amount = Column(Float, nullable=False, default=0.0)
    taxable_amount = Column(Float, nullable=False, default=0.0)
    gst_rate = Column(Float, nullable=False, default=0.0)
    gst_amount = Column(Float, nullable=False, default=0.0)
    freight = Column(Float, nullable=False, default=0.0)
    packing = Column(Float, nullable=False, default=0.0)
    other_charges = Column(Float, nullable=False, default=0.0)
    total = Column(Float, nullable=False, default=0.0)

    delivery_days = Column(Integer, nullable=True)
    warranty = Column(String(200), nullable=True)
    #: COMPLIANT / PARTIAL / NON_COMPLIANT
    compliance = Column(String(20), nullable=False, default="COMPLIANT")
    deviation = Column(String(1000), nullable=True)

    quotation = relationship("VendorQuotation", back_populates="items")


# ----------------------------------------------------------------------
# The purchase order
# ----------------------------------------------------------------------
class PurchaseOrder(Base):
    """An order placed with a vendor, built from the approved quotation."""

    __tablename__ = "purchase_order"

    id = Column(Integer, primary_key=True, autoincrement=True)
    po_number = Column(String(30), nullable=False, unique=True, index=True)
    po_date = Column(DateTime, default=datetime.utcnow, nullable=False)

    requisition_id = Column(Integer, ForeignKey("purchase_requisition.id", ondelete="RESTRICT"), nullable=False, index=True)
    sales_order_id = Column(Integer, ForeignKey("sales_order.id", ondelete="SET NULL"), nullable=True, index=True)
    vendor_id = Column(Integer, ForeignKey("vendor.id", ondelete="RESTRICT"), nullable=False, index=True)
    quotation_id = Column(Integer, ForeignKey("vendor_quotation.id", ondelete="RESTRICT"), nullable=False)

    # The vendor as they were when the order went out.
    vendor_name = Column(String(200), nullable=True)
    vendor_address = Column(String(600), nullable=True)
    vendor_gstin = Column(String(20), nullable=True)
    vendor_reference = Column(String(100), nullable=True)
    quotation_version = Column(String(40), nullable=True)

    bill_to = Column(String(600), nullable=True)
    ship_to = Column(String(600), nullable=True)
    payment_terms = Column(String(300), nullable=True)
    delivery_terms = Column(String(300), nullable=True)
    freight_terms = Column(String(300), nullable=True)
    warranty_terms = Column(String(300), nullable=True)
    terms = Column(JSON, nullable=True)
    expected_delivery = Column(Date, nullable=True)
    currency = Column(String(10), nullable=False, default="INR")

    #: ADVANCE_100 / ADVANCE_DELIVERY / ADVANCE_CREDIT / CREDIT_100
    #: (app/core/procurement.py PAYMENT_TERMS), with the figures they need.
    payment_term_type = Column(String(30), nullable=True)
    advance_percent = Column(Float, nullable=True)
    credit_days = Column(Integer, nullable=True)
    #: BANK_TRANSFER / PDC (post-dated cheque)
    payment_mode = Column(String(20), nullable=True)

    #: The amount-based approval runs on the PO: each submission is a round.
    approval_round = Column(Integer, nullable=False, default=0)
    current_level = Column(Integer, nullable=True)

    subtotal = Column(Float, nullable=False, default=0.0)
    discount_total = Column(Float, nullable=False, default=0.0)
    taxable_total = Column(Float, nullable=False, default=0.0)
    gst_total = Column(Float, nullable=False, default=0.0)
    charges_total = Column(Float, nullable=False, default=0.0)
    grand_total = Column(Float, nullable=False, default=0.0)

    status = Column(String(30), nullable=False, default="DRAFT", index=True)
    submitted_at = Column(DateTime, nullable=True)
    approved_by = Column(UUID(as_uuid=True), nullable=True)
    approved_by_name = Column(String(200), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    remarks = Column(Text, nullable=True)

    created_by = Column(UUID(as_uuid=True), nullable=True)
    created_by_name = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)

    items = relationship(
        "PurchaseOrderItem",
        back_populates="order",
        order_by="PurchaseOrderItem.id",
        cascade="all, delete-orphan",
    )


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_item"

    id = Column(Integer, primary_key=True, autoincrement=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_order.id", ondelete="CASCADE"), nullable=False, index=True)
    requisition_item_id = Column(Integer, ForeignKey("purchase_requisition_item.id", ondelete="SET NULL"), nullable=True, index=True)
    quotation_item_id = Column(Integer, ForeignKey("vendor_quotation_item.id", ondelete="SET NULL"), nullable=True)

    product_name = Column(String(300), nullable=True)
    sku = Column(String(100), nullable=True)
    hsn = Column(String(20), nullable=True)
    description = Column(String(500), nullable=True)
    uom = Column(String(30), nullable=True)

    quantity = Column(Float, nullable=False, default=0.0)
    #: Filled by goods receipt, which is not part of this module.
    received_quantity = Column(Float, nullable=False, default=0.0)

    unit_rate = Column(Float, nullable=False, default=0.0)
    discount_amount = Column(Float, nullable=False, default=0.0)
    taxable_amount = Column(Float, nullable=False, default=0.0)
    gst_rate = Column(Float, nullable=False, default=0.0)
    gst_amount = Column(Float, nullable=False, default=0.0)
    freight = Column(Float, nullable=False, default=0.0)
    other_charges = Column(Float, nullable=False, default=0.0)
    total = Column(Float, nullable=False, default=0.0)

    order = relationship("PurchaseOrder", back_populates="items")


class PurchaseOrderApproval(Base):
    """One step of a PO's amount-based approval, in one round."""

    __tablename__ = "purchase_order_approval"
    __table_args__ = (
        UniqueConstraint("purchase_order_id", "round", "level", name="uq_po_approval_step"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_order.id", ondelete="CASCADE"), nullable=False, index=True)
    round = Column(Integer, nullable=False)
    level = Column(Integer, nullable=False)
    step = Column(String(40), nullable=False)
    approver_ids = Column(JSON, nullable=False, default=list)
    approver_label = Column(String(300), nullable=True)
    #: PENDING / WAITING / APPROVED / REJECTED / SKIPPED
    decision = Column(String(20), nullable=False, default="WAITING")
    decided_by = Column(UUID(as_uuid=True), nullable=True)
    decided_by_name = Column(String(200), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    remarks = Column(String(2000), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)


class PurchaseOrderPayment(Base):
    """One instalment of a PO's payment schedule.

    Generated from the payment terms (advance, on delivery, after N days)
    and then kept by hand: the mode, the bank, the cheque for a post-dated
    cheque, and whether it has been paid.
    """

    __tablename__ = "purchase_order_payment"

    id = Column(Integer, primary_key=True, autoincrement=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_order.id", ondelete="CASCADE"), nullable=False, index=True)
    line_no = Column(Integer, nullable=False, default=1)
    #: Advance / On delivery / After 30 days ...
    label = Column(String(100), nullable=False)
    percent = Column(Float, nullable=False, default=0.0)
    amount = Column(Float, nullable=False, default=0.0)
    due_date = Column(Date, nullable=True)

    #: BANK_TRANSFER / PDC
    mode = Column(String(20), nullable=True)
    bank_name = Column(String(150), nullable=True)
    cheque_number = Column(String(50), nullable=True)
    cheque_date = Column(Date, nullable=True)
    reference = Column(String(150), nullable=True)

    #: PENDING (procurement to fill in and submit) -> SUBMITTED (with
    #: accounts) -> APPROVED (accounts agreed the details) -> PAID.
    #: Accounts rejecting a submission sends it to RETURNED, which
    #: procurement corrects and submits again.
    status = Column(String(20), nullable=False, default="PENDING")
    submitted_at = Column(DateTime, nullable=True)
    submitted_by = Column(UUID(as_uuid=True), nullable=True)
    submitted_by_name = Column(String(200), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approved_by_name = Column(String(200), nullable=True)
    #: Why accounts sent it back, while it is RETURNED.
    returned_reason = Column(String(500), nullable=True)
    returned_at = Column(DateTime, nullable=True)
    returned_by_name = Column(String(200), nullable=True)
    #: The day the daily reminder last went out for it, so a second run
    #: on the same day sends nothing (see payment_calendar.send_reminders).
    last_reminded_on = Column(Date, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    paid_by = Column(UUID(as_uuid=True), nullable=True)
    paid_by_name = Column(String(200), nullable=True)
    remarks = Column(String(500), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)


class ProcurementActivity(Base):
    """The procurement audit trail: every action on every procurement record."""

    __tablename__ = "procurement_activity"

    id = Column(Integer, primary_key=True, autoincrement=True)
    #: requisition / vendor / quotation / selection / purchase_order
    entity_type = Column(String(40), nullable=False, index=True)
    entity_id = Column(Integer, nullable=False, index=True)
    #: The requisition it belongs to, so a PR's history reads in one place.
    requisition_id = Column(Integer, nullable=True, index=True)

    action = Column(String(150), nullable=False)
    previous_value = Column(JSON, nullable=True)
    new_value = Column(JSON, nullable=True)
    remarks = Column(Text, nullable=True)

    actor_id = Column(UUID(as_uuid=True), nullable=True)
    actor_name = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
