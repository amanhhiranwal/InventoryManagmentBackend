"""Purchase orders, created from a vendor's quotation.

Procurement asks several vendors to quote for each line, compares what
comes back and creates the PO by picking a vendor's quotation and the
lines it will supply. Nothing is typed in again: the lines, rates, taxes
and charges come from that quotation.

The PO carries the price, so it is the PO that runs the amount bands of
the approval matrix (Masters > PR Approval Matrix): the requester's
reporting manager, then the department head and finance, then the
director. Whoever created the PO never approves it.

Payment is agreed on the PO: the terms (advance, on delivery, after credit
days), the mode (bank transfer or post-dated cheque) and the schedule of
instalments those terms produce, each marked paid as it is paid.

A PO touches no stock - goods are received against it by a GRN, which is
not part of this module. The PO is not sent anywhere by the system:
procurement downloads the PDF, shares it and marks it sent.
"""

from datetime import date, datetime, timedelta
from io import BytesIO

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.procurement import (
    CREDIT_DAY_OPTIONS,
    PAYMENT_MODES,
    PAYMENT_TERMS,
    ApprovalStep,
    POStatus,
    PRStatus,
    ProcurementStatus,
    QuotationStatus,
)
from app.core.timezone import iso
from app.models.procurement import (
    PurchaseOrder,
    PurchaseOrderApproval,
    PurchaseOrderItem,
    PurchaseOrderPayment,
    PurchaseRequisition,
    VendorQuotation,
)
from app.services.procurement import approval_chain, masters, requisitions
from app.services.procurement import vendors as vendor_service
from app.services.procurement.common import (
    activity_rows,
    actor,
    has_permission,
    holders_of,
    is_super_admin,
    log,
    money,
    next_reference,
    notify,
    require,
    text,
    today,
)

MODULE = "purchase_order"

DEFAULT_TERMS = [
    "Goods must match the specifications and quantities on this order; deviations need written approval.",
    "Each delivery must carry the invoice, delivery challan and this PO number.",
    "Goods are accepted only after inspection; rejected goods are returned at the vendor's cost.",
    "Payment as per the payment terms above, against an invoice quoting this PO number.",
]

#: Statuses a PO is still live in: its lines are taken.
LIVE = (POStatus.DRAFT, POStatus.PENDING_APPROVAL, POStatus.APPROVED, POStatus.SENT,
        POStatus.ACKNOWLEDGED, POStatus.PARTIALLY_RECEIVED, POStatus.FULLY_RECEIVED, POStatus.CLOSED)

#: From approval on, money may be paid against the PO.
PAYABLE = (POStatus.APPROVED, POStatus.SENT, POStatus.ACKNOWLEDGED, POStatus.PARTIALLY_RECEIVED,
           POStatus.FULLY_RECEIVED, POStatus.CLOSED)


def link(po: PurchaseOrder) -> str:
    return f"/procurement/purchase-orders/{po.id}"


def get(po_id: int, db: Session, *, lock: bool = False) -> PurchaseOrder:
    query = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id)
    if lock:
        query = query.with_for_update()
    po = query.first()
    if po is None:
        raise HTTPException(status_code=404, detail="Purchase order not found.")
    return po


def _company(db: Session) -> dict:
    from app.services.quotation_pdf_service import QuotationPDFService

    return QuotationPDFService.company(db)


def _bill_to(db: Session) -> str:
    company = _company(db)
    lines = [company.get("name"), *(company.get("address") or [])]
    if company.get("gstin"):
        lines.append(f"GSTIN: {company['gstin']}")
    return "\n".join(line for line in lines if line)


def ordered_items(requisition_id: int, db: Session) -> set[int]:
    """The requisition lines already on a live (not cancelled) PO."""

    rows = (
        db.query(PurchaseOrderItem.requisition_item_id)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
        .filter(PurchaseOrder.requisition_id == requisition_id, PurchaseOrder.status.in_(LIVE))
        .all()
    )
    return {row[0] for row in rows if row[0]}


def _sync_requisition(pr: PurchaseRequisition, db: Session) -> None:
    """The PR's procurement status from its lines and orders."""

    # The session does not autoflush: write the PO change being made (a
    # cancel, an approval) first, or the queries below read the old status
    # from the table while the object in hand already has the new one.
    db.flush()

    live = db.query(PurchaseOrder).filter(
        PurchaseOrder.requisition_id == pr.id, PurchaseOrder.status.in_(LIVE)
    ).all()
    if not live:
        # Every PO was cancelled: the lines are back to being sourced.
        if pr.procurement_status in (ProcurementStatus.PO_APPROVAL, ProcurementStatus.PO_GENERATED):
            pr.procurement_status = ProcurementStatus.COMPARISON
        return

    awaiting = any(po.status in (POStatus.DRAFT, POStatus.PENDING_APPROVAL) for po in live)
    every_line = {item.id for item in pr.items}
    ordered = ordered_items(pr.id, db)

    if awaiting:
        pr.procurement_status = ProcurementStatus.PO_APPROVAL
    elif every_line and every_line <= ordered:
        closed = all(po.status == POStatus.CLOSED for po in live)
        pr.procurement_status = ProcurementStatus.COMPLETED if closed else ProcurementStatus.PO_GENERATED
    else:
        # Some lines are ordered and approved; the rest are still being sourced.
        pr.procurement_status = ProcurementStatus.COMPARISON


# ----------------------------------------------------------------------
# Payment terms and the schedule they produce
# ----------------------------------------------------------------------
def _percent(value, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Enter the {field} as a percentage.")
    if not 0 < number < 100:
        raise HTTPException(status_code=400, detail=f"The {field} must be between 1 and 99%.")
    return number


def _payment_terms(payload: dict) -> dict:
    """The payment terms from a form, checked, with their wording."""

    kind = (text(payload.get("payment_term_type")) or "").upper()
    if kind not in PAYMENT_TERMS:
        raise HTTPException(status_code=400, detail="Choose the payment terms.")

    advance = None
    days = None
    if kind in ("ADVANCE_DELIVERY", "ADVANCE_CREDIT"):
        advance = _percent(payload.get("advance_percent") or 50, "advance")
    if kind in CREDIT_DAY_OPTIONS:
        try:
            days = int(payload.get("credit_days"))
        except (TypeError, ValueError):
            days = None
        if days not in CREDIT_DAY_OPTIONS[kind]:
            choices = ", ".join(str(d) for d in CREDIT_DAY_OPTIONS[kind])
            raise HTTPException(status_code=400, detail=f"Choose the credit days: {choices}.")

    mode = (text(payload.get("payment_mode")) or "").upper() or None
    if mode is not None and mode not in PAYMENT_MODES:
        raise HTTPException(status_code=400, detail="Payment is by bank transfer or post-dated cheque.")

    def pct(value: float) -> str:
        return f"{value:g}%"

    wording = {
        "ADVANCE_100": "100% advance",
        "ADVANCE_DELIVERY": f"{pct(advance or 0)} advance, {pct(100 - (advance or 0))} on delivery",
        "ADVANCE_CREDIT": f"{pct(advance or 0)} advance, {pct(100 - (advance or 0))} after {days} days",
        "CREDIT_100": f"100% after {days} days",
    }[kind]
    if mode:
        wording += f", by {PAYMENT_MODES[mode].lower()}"

    return {"payment_term_type": kind, "advance_percent": advance, "credit_days": days,
            "payment_mode": mode, "payment_terms": wording}


def _instalments(po: PurchaseOrder) -> list[tuple[str, float, date | None]]:
    """(label, percent, due date) for each instalment the terms produce.

    Advances fall due on the PO date, delivery payments on the expected
    delivery date, and credit days run from delivery.
    """

    placed = (po.po_date or datetime.utcnow()).date()
    delivery = po.expected_delivery or placed
    kind, advance, days = po.payment_term_type, po.advance_percent or 0, po.credit_days or 0

    if kind == "ADVANCE_100":
        return [("Advance", 100.0, placed)]
    if kind == "ADVANCE_DELIVERY":
        return [("Advance", advance, placed), ("On delivery", 100 - advance, delivery)]
    if kind == "ADVANCE_CREDIT":
        return [("Advance", advance, placed),
                (f"After {days} days", 100 - advance, delivery + timedelta(days=days))]
    if kind == "CREDIT_100":
        return [(f"After {days} days", 100.0, delivery + timedelta(days=days))]
    return []


def _rebuild_schedule(po: PurchaseOrder, db: Session) -> None:
    """The instalments, laid out afresh from the terms and the total.

    Only while every instalment is still untouched: once one has been
    submitted to accounts, approved or paid, the schedule is a record and
    is kept as it is.
    """

    existing = db.query(PurchaseOrderPayment).filter(PurchaseOrderPayment.purchase_order_id == po.id).all()
    if any(row.status != "PENDING" for row in existing):
        return
    for row in existing:
        db.delete(row)
    db.flush()

    plan = _instalments(po)
    remaining = money(po.grand_total)
    for number, (label, percent, due) in enumerate(plan, start=1):
        # The last instalment takes what is left, so the rows add up to the total.
        amount = remaining if number == len(plan) else money(po.grand_total * percent / 100)
        remaining = money(remaining - amount)
        db.add(PurchaseOrderPayment(
            purchase_order_id=po.id, line_no=number, label=label, percent=round(percent, 2),
            amount=amount, due_date=due, mode=po.payment_mode,
        ))


# ----------------------------------------------------------------------
# Creating a PO from a quotation
# ----------------------------------------------------------------------
def create(payload: dict, current_user: dict, db: Session) -> PurchaseOrder:
    """A draft PO to the vendor of ``quotation_id``, for the chosen lines.

    The requisition row is locked first, so two people creating POs for
    the same lines at once cannot both succeed.
    """

    try:
        quotation_id = int(payload.get("quotation_id") or 0)
    except (TypeError, ValueError):
        quotation_id = 0
    quotation = db.query(VendorQuotation).filter(VendorQuotation.id == quotation_id).first()
    if quotation is None:
        raise HTTPException(status_code=404, detail="Quotation not found.")

    pr = requisitions.get(quotation.requisition_id, db, lock=True)
    if pr.status != PRStatus.APPROVED:
        raise HTTPException(status_code=409, detail="A PO is raised only for an approved requisition.")
    if pr.procurement_status in (ProcurementStatus.CANCELLED, ProcurementStatus.ON_HOLD):
        raise HTTPException(status_code=409, detail="This requisition is on hold or cancelled.")

    if not quotation.is_latest:
        raise HTTPException(status_code=409, detail="Raise the PO from the latest version of the quotation.")
    if quotation.status in (QuotationStatus.DRAFT, QuotationStatus.REJECTED):
        raise HTTPException(status_code=409, detail="A draft or rejected quotation cannot become a PO.")
    if quotation.valid_until and quotation.valid_until < today():
        raise HTTPException(status_code=409, detail="This quotation has expired. Record a revised one first.")

    vendor = vendor_service.get(quotation.vendor_id, db)
    vendor_service.assert_usable(vendor)

    taken = ordered_items(pr.id, db)
    wanted = {int(i) for i in (payload.get("requisition_item_ids") or [])}
    quoted = [line for line in quotation.items if line.requisition_item_id]
    lines = [line for line in quoted if (not wanted or line.requisition_item_id in wanted)]
    if wanted - {line.requisition_item_id for line in quoted}:
        raise HTTPException(status_code=400, detail="Some chosen lines are not on this vendor's quotation.")
    clash = [line.product_name for line in lines if line.requisition_item_id in taken]
    if clash:
        raise HTTPException(status_code=409, detail=f"Already on another PO: {', '.join(clash)}.")
    if not lines:
        raise HTTPException(status_code=400, detail="Choose at least one line for this PO.")

    terms = _payment_terms(payload) if payload.get("payment_term_type") else None

    from app.services.sales_order_service import _hsn_for_sku

    who, name = actor(current_user, db)
    expected = None
    if quotation.delivery_days is not None:
        expected = today() + timedelta(days=quotation.delivery_days)
    elif pr.required_by:
        expected = pr.required_by

    vendor_address = ", ".join(p for p in (vendor.address, vendor.city, vendor.state, vendor.pin_code) if p)

    po = PurchaseOrder(
        po_number=next_reference(db, PurchaseOrder, "po_number", "PO"),
        requisition_id=pr.id,
        sales_order_id=pr.sales_order_id,
        vendor_id=vendor.id,
        quotation_id=quotation.id,
        vendor_name=vendor.name,
        vendor_address=vendor_address or None,
        vendor_gstin=vendor.gstin,
        vendor_reference=quotation.vendor_reference,
        quotation_version=quotation.version_label,
        bill_to=text(payload.get("bill_to"), 600) or _bill_to(db),
        ship_to=text(payload.get("ship_to"), 600) or pr.delivery_location or _bill_to(db),
        payment_terms=(terms or {}).get("payment_terms") or quotation.payment_terms or vendor.payment_terms,
        delivery_terms=quotation.delivery_terms,
        freight_terms=quotation.freight_terms or vendor.freight_terms,
        warranty_terms=quotation.warranty_terms or vendor.warranty_terms,
        terms=[t for t in (payload.get("terms") or DEFAULT_TERMS) if str(t).strip()],
        expected_delivery=expected,
        currency=quotation.currency,
        status=POStatus.DRAFT,
        remarks=text(payload.get("remarks")),
        created_by=who,
        created_by_name=name,
    )
    if terms:
        for key in ("payment_term_type", "advance_percent", "credit_days", "payment_mode"):
            setattr(po, key, terms[key])

    sums = {"gross": 0.0, "discount": 0.0, "taxable": 0.0, "gst": 0.0, "charges": 0.0, "total": 0.0}
    for line in lines:
        charges = (line.freight or 0) + (line.packing or 0) + (line.other_charges or 0)
        po.items.append(PurchaseOrderItem(
            requisition_item_id=line.requisition_item_id,
            quotation_item_id=line.id,
            product_name=line.vendor_product_name or line.product_name,
            sku=line.sku,
            hsn=line.hsn or (_hsn_for_sku(line.sku) if line.sku else None),
            description=", ".join(p for p in (line.brand, line.vendor_model) if p) or None,
            uom=line.uom,
            quantity=line.quantity,
            unit_rate=line.unit_rate,
            discount_amount=line.discount_amount,
            taxable_amount=line.taxable_amount,
            gst_rate=line.gst_rate,
            gst_amount=line.gst_amount,
            freight=line.freight or 0,
            other_charges=money((line.packing or 0) + (line.other_charges or 0)),
            total=line.total,
        ))
        sums["gross"] += line.quantity * line.unit_rate
        sums["discount"] += line.discount_amount
        sums["taxable"] += line.taxable_amount
        sums["gst"] += line.gst_amount
        sums["charges"] += charges
        sums["total"] += line.total

    po.subtotal = money(sums["gross"])
    po.discount_total = money(sums["discount"])
    po.taxable_total = money(sums["taxable"])
    po.gst_total = money(sums["gst"])
    po.charges_total = money(sums["charges"])
    po.grand_total = money(sums["total"])

    db.add(po)
    db.flush()
    if terms:
        _rebuild_schedule(po, db)

    quotation.status = QuotationStatus.SELECTED
    previous = pr.procurement_status
    _sync_requisition(pr, db)

    log(db, current_user, entity_type="purchase_order", entity_id=po.id, requisition_id=pr.id,
        action="Purchase Order Created", previous={"procurement_status": previous},
        new={"po": po.po_number, "vendor": vendor.name, "total": po.grand_total,
             "quotation": quotation.quotation_number, "lines": len(lines),
             "procurement_status": pr.procurement_status})
    db.commit()
    db.refresh(po)
    return po


def update(po_id: int, payload: dict, current_user: dict, db: Session) -> PurchaseOrder:
    """What may change on a draft: where it goes, when, and the payment
    terms - not the prices, which are the quotation's."""

    po = get(po_id, db, lock=True)
    if po.status != POStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Only a draft PO can be changed.")

    before = {"ship_to": po.ship_to, "expected_delivery": iso(po.expected_delivery),
              "payment_terms": po.payment_terms}

    if "ship_to" in payload:
        po.ship_to = text(payload.get("ship_to"), 600)
    if "bill_to" in payload:
        po.bill_to = text(payload.get("bill_to"), 600)
    if "expected_delivery" in payload:
        value = payload.get("expected_delivery")
        try:
            po.expected_delivery = datetime.fromisoformat(str(value)[:10]).date() if value else None
        except ValueError:
            raise HTTPException(status_code=400, detail="Expected delivery is not a date.")
    if "terms" in payload:
        po.terms = [str(t).strip() for t in payload.get("terms") or [] if str(t).strip()]
    if "remarks" in payload:
        po.remarks = text(payload.get("remarks"))

    reschedule = "expected_delivery" in payload
    if payload.get("payment_term_type"):
        terms = _payment_terms(payload)
        for key, value in terms.items():
            setattr(po, key, value)
        reschedule = True
    if reschedule and po.payment_term_type:
        _rebuild_schedule(po, db)

    log(db, current_user, entity_type="purchase_order", entity_id=po.id, requisition_id=po.requisition_id,
        action="Purchase Order Updated", previous=before,
        new={"ship_to": po.ship_to, "expected_delivery": iso(po.expected_delivery),
             "payment_terms": po.payment_terms})
    db.commit()
    db.refresh(po)
    return po


# ----------------------------------------------------------------------
# Approval: the amount bands, on the PO
# ----------------------------------------------------------------------
def approvals(po: PurchaseOrder, db: Session) -> list[PurchaseOrderApproval]:
    return (
        db.query(PurchaseOrderApproval)
        .filter(PurchaseOrderApproval.purchase_order_id == po.id)
        .order_by(PurchaseOrderApproval.round, PurchaseOrderApproval.level)
        .all()
    )


def pending_step(po: PurchaseOrder, db: Session) -> PurchaseOrderApproval | None:
    if po.status != POStatus.PENDING_APPROVAL:
        return None
    return (
        db.query(PurchaseOrderApproval)
        .filter(PurchaseOrderApproval.purchase_order_id == po.id,
                PurchaseOrderApproval.round == po.approval_round,
                PurchaseOrderApproval.decision == "PENDING")
        .first()
    )


def _creator(po: PurchaseOrder) -> set[str]:
    return {str(po.created_by)} if po.created_by else set()


def may_decide(step: PurchaseOrderApproval | None, po: PurchaseOrder, current_user: dict) -> bool:
    if step is None:
        return False
    return approval_chain.may_decide(step.approver_ids, current_user, exclude=_creator(po))


def assert_can_view(po: PurchaseOrder, current_user: dict, db: Session) -> None:
    """The purchase team, and anyone on the PO's approval chain - an approver
    may hold no procurement permission at all."""

    if is_super_admin(current_user) or any(
        has_permission(current_user, key, db) for key in ("purchase_order.read", "purchase_order.pay")
    ):
        return
    me = str(current_user.get("user_id") or "")
    for step in approvals(po, db):
        if me in [str(i) for i in step.approver_ids or []] or me == str(step.decided_by or ""):
            return
    raise HTTPException(status_code=403, detail="This purchase order is not yours to see.")


def _submit(po: PurchaseOrder, current_user: dict, db: Session) -> None:
    if not po.payment_term_type:
        raise HTTPException(status_code=400, detail="Set the payment terms before submitting the PO.")

    pr = db.query(PurchaseRequisition).filter(PurchaseRequisition.id == po.requisition_id).first()
    po.approval_round = (po.approval_round or 0) + 1
    rows = approval_chain.build(
        masters.steps_for_amount(po.grand_total, db),
        requester_id=pr.requested_by if pr else None,
        department_id=pr.department_id if pr else None,
        exclude=_creator(po), db=db,
    )
    approval_chain.assert_decidable(rows, _creator(po), db)
    chain = [PurchaseOrderApproval(purchase_order_id=po.id, round=po.approval_round, **row) for row in rows]
    for row in chain:
        db.add(row)
    po.current_level = chain[0].level
    po.submitted_at = datetime.utcnow()

    notify(db, approval_chain.people(chain[0].approver_ids, db, exclude=_creator(po)), module=MODULE,
           entity_id=po.id, action="Purchase Order Waiting For Your Approval",
           message=f"{po.po_number} to {po.vendor_name} - ₹{po.grand_total:,.2f}"
                   f"{f' for {pr.pr_number}' if pr else ''}.",
           link=link(po), current_user=current_user)


def decide(po_id: int, decision: str, remarks: str | None, current_user: dict, db: Session) -> PurchaseOrder:
    """Approve or reject the step the PO is waiting on. A rejection sends
    it back to draft for procurement to change and resubmit."""

    decision = (decision or "").upper()
    if decision not in ("APPROVE", "REJECT"):
        raise HTTPException(status_code=400, detail="Approve or reject.")
    remarks = text(remarks, 2000)
    if decision == "REJECT" and not remarks:
        raise HTTPException(status_code=400, detail="Say why - a rejection needs remarks.")

    po = get(po_id, db, lock=True)
    step = pending_step(po, db)
    if step is None:
        raise HTTPException(status_code=409, detail="This purchase order is not waiting on an approval.")
    if str(current_user.get("user_id") or "") in _creator(po):
        raise HTTPException(status_code=403, detail="Someone other than the person who created the PO must approve it.")
    if not may_decide(step, po, current_user):
        raise HTTPException(status_code=403, detail="This purchase order is not waiting on you.")

    who, name = actor(current_user, db)
    now = datetime.utcnow()
    step.decided_by, step.decided_by_name, step.decided_at, step.remarks = who, name, now, remarks
    label = ApprovalStep.label(step.step)

    if decision == "APPROVE":
        step.decision = "APPROVED"
        following = (
            db.query(PurchaseOrderApproval)
            .filter(PurchaseOrderApproval.purchase_order_id == po.id,
                    PurchaseOrderApproval.round == po.approval_round,
                    PurchaseOrderApproval.level > step.level)
            .order_by(PurchaseOrderApproval.level)
            .first()
        )
        if following is not None:
            following.decision = "PENDING"
            po.current_level = following.level
            log(db, current_user, entity_type="purchase_order", entity_id=po.id,
                requisition_id=po.requisition_id, action=f"PO Approved By {label}", remarks=remarks,
                new={"next": ApprovalStep.label(following.step)})
            notify(db, approval_chain.people(following.approver_ids, db, exclude=_creator(po)), module=MODULE,
                   entity_id=po.id, action="Purchase Order Waiting For Your Approval",
                   message=f"{po.po_number} to {po.vendor_name} - ₹{po.grand_total:,.2f} was approved by "
                           f"{name} ({label}) and now needs your approval.",
                   link=link(po), current_user=current_user)
        else:
            po.status = POStatus.APPROVED
            po.current_level = None
            po.approved_by, po.approved_by_name, po.approved_at = who, name, now
            log(db, current_user, entity_type="purchase_order", entity_id=po.id,
                requisition_id=po.requisition_id, action="Purchase Order Approved", remarks=remarks,
                previous={"status": POStatus.PENDING_APPROVAL}, new={"status": po.status})
            notify(db, [str(po.created_by)] if po.created_by else [], module=MODULE, entity_id=po.id,
                   action="Purchase Order Approved", message=f"{po.po_number} is approved and can be sent.",
                   link=link(po), current_user=current_user)
            # Approved, it goes to accounts for payment as the schedule falls due.
            from app.services.procurement.common import holders_of

            notify(db, holders_of("purchase_order.pay", db), module=MODULE, entity_id=po.id,
                   action="Purchase Order Approved For Payment",
                   message=f"{po.po_number} to {po.vendor_name} - ₹{po.grand_total:,.2f}, "
                           f"{po.payment_terms or 'terms on the PO'}.",
                   link=link(po), current_user=current_user)
    else:
        step.decision = "REJECTED"
        po.status = POStatus.DRAFT
        po.current_level = None
        for later in db.query(PurchaseOrderApproval).filter(
            PurchaseOrderApproval.purchase_order_id == po.id,
            PurchaseOrderApproval.round == po.approval_round,
            PurchaseOrderApproval.decision == "WAITING",
        ):
            later.decision = "SKIPPED"
        log(db, current_user, entity_type="purchase_order", entity_id=po.id, requisition_id=po.requisition_id,
            action="Purchase Order Rejected", remarks=remarks,
            previous={"status": POStatus.PENDING_APPROVAL}, new={"status": po.status})
        notify(db, [str(po.created_by)] if po.created_by else [], module=MODULE, entity_id=po.id,
               action="Purchase Order Rejected", message=f"{po.po_number}: {remarks}",
               link=link(po), current_user=current_user)

    pr = requisitions.get(po.requisition_id, db, lock=True)
    _sync_requisition(pr, db)
    db.commit()
    db.refresh(po)
    return po


#: The moves the purchase team makes by hand. Approval is decide() above.
ACTIONS = {
    "SUBMIT": POStatus.PENDING_APPROVAL,
    "SEND": POStatus.SENT,
    "ACKNOWLEDGE": POStatus.ACKNOWLEDGED,
    "CLOSE": POStatus.CLOSED,
    "CANCEL": POStatus.CANCELLED,
}

LABELS = {
    "SUBMIT": "Purchase Order Submitted For Approval",
    "SEND": "Purchase Order Sent To Vendor",
    "ACKNOWLEDGE": "Purchase Order Acknowledged By Vendor",
    "CLOSE": "Purchase Order Closed",
    "CANCEL": "Purchase Order Cancelled",
}


def act(po_id: int, action: str, remarks: str | None, current_user: dict, db: Session) -> PurchaseOrder:
    from app.services.procurement.common import require

    action = (action or "").upper()
    if action not in ACTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown purchase order action {action}.")
    require(current_user, "purchase_order.manage", db)

    target = ACTIONS[action]
    po = get(po_id, db, lock=True)
    remarks = text(remarks, 2000)

    if target not in POStatus.MOVES.get(po.status, ()):
        raise HTTPException(
            status_code=409,
            detail=f"A {po.status.replace('_', ' ').lower()} PO cannot move to {target.replace('_', ' ').lower()}.",
        )
    if action == "CANCEL" and not remarks:
        raise HTTPException(status_code=400, detail="Say why - cancelling a PO needs remarks.")
    if action == "CANCEL" and db.query(PurchaseOrderPayment.id).filter(
        PurchaseOrderPayment.purchase_order_id == po.id, PurchaseOrderPayment.status == "PAID"
    ).first():
        raise HTTPException(status_code=409, detail="Money has been paid against this PO; it cannot be cancelled.")

    previous = po.status
    now = datetime.utcnow()

    if action == "SUBMIT":
        _submit(po, current_user, db)
    elif action == "SEND":
        po.sent_at = now
    elif action == "ACKNOWLEDGE":
        po.acknowledged_at = now
    elif action == "CLOSE":
        po.closed_at = now
    elif action == "CANCEL":
        for step in db.query(PurchaseOrderApproval).filter(
            PurchaseOrderApproval.purchase_order_id == po.id,
            PurchaseOrderApproval.decision.in_(("PENDING", "WAITING")),
        ):
            step.decision = "SKIPPED"
        po.current_level = None
    po.status = target

    if action == "CANCEL":
        # The quotation is free again unless another live PO still uses it:
        # it can be revised, or a new PO raised from it.
        db.flush()
        still_used = db.query(PurchaseOrder.id).filter(
            PurchaseOrder.quotation_id == po.quotation_id, PurchaseOrder.status.in_(LIVE)
        ).first()
        quotation = db.query(VendorQuotation).filter(VendorQuotation.id == po.quotation_id).first()
        if quotation is not None and not still_used and quotation.status == QuotationStatus.SELECTED:
            quotation.status = (
                QuotationStatus.FINAL if quotation.version_label == "Final Negotiated" else QuotationStatus.RECEIVED
            )

    pr = requisitions.get(po.requisition_id, db, lock=True)
    _sync_requisition(pr, db)
    log(db, current_user, entity_type="purchase_order", entity_id=po.id, requisition_id=po.requisition_id,
        action=LABELS[action], previous={"status": previous}, new={"status": po.status}, remarks=remarks)
    db.commit()
    db.refresh(po)
    return po


# ----------------------------------------------------------------------
# Payments
# ----------------------------------------------------------------------
def _payment(po: PurchaseOrder, payment_id: int, db: Session) -> PurchaseOrderPayment:
    row = db.query(PurchaseOrderPayment).filter(
        PurchaseOrderPayment.id == payment_id, PurchaseOrderPayment.purchase_order_id == po.id
    ).with_for_update().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Instalment not found on this PO.")
    return row


def _day(value, field: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field} is not a date.")


#: Who does what to an instalment. Procurement fills in the cheque or
#: transfer and submits it; accounts approve it (or send it back) and
#: then mark it paid.
DETAILS_PERMISSION = "purchase_order.manage"
ACCOUNTS_PERMISSION = "purchase_order.pay"

#: Statuses in which procurement may still change the details.
EDITABLE_PAYMENT = ("PENDING", "RETURNED")


def _details_missing(row: PurchaseOrderPayment) -> str | None:
    if row.mode == "PDC":
        if not (row.cheque_number and row.bank_name and row.cheque_date):
            return "Enter the cheque number, bank and cheque date first."
    elif row.mode == "BANK_TRANSFER":
        if not row.bank_name:
            return "Enter the bank the transfer is made from first."
    else:
        return "Choose how it is paid: bank transfer or post-dated cheque."
    return None


def update_payment(po_id: int, payment_id: int, payload: dict, current_user: dict, db: Session) -> PurchaseOrder:
    """Procurement filling in one instalment: how it is paid, the bank, the
    cheque. Only while it is still with procurement - once submitted,
    accounts have it, and a rejection hands it back.

    Approving and paying are separate steps (payment_action), done by
    accounts.
    """

    require(current_user, DETAILS_PERMISSION, db)
    if "paid" in payload:
        raise HTTPException(
            status_code=400,
            detail="Submit the instalment to accounts; they approve it and mark it paid.",
        )

    po = get(po_id, db, lock=True)
    if po.status == POStatus.CANCELLED:
        raise HTTPException(status_code=409, detail="This PO is cancelled.")
    row = _payment(po, payment_id, db)
    if row.status not in EDITABLE_PAYMENT:
        raise HTTPException(
            status_code=409,
            detail=f"This instalment is {row.status.lower()}; ask accounts to send it back to change it.",
        )
    before = {"status": row.status, "mode": row.mode, "amount": row.amount}

    if "mode" in payload:
        mode = (text(payload.get("mode")) or "").upper() or None
        if mode is not None and mode not in PAYMENT_MODES:
            raise HTTPException(status_code=400, detail="Payment is by bank transfer or post-dated cheque.")
        row.mode = mode
    for key, limit in (("bank_name", 150), ("cheque_number", 50), ("reference", 150), ("remarks", 500)):
        if key in payload:
            setattr(row, key, text(payload.get(key), limit))
    if "cheque_date" in payload:
        row.cheque_date = _day(payload.get("cheque_date"), "Cheque date")
    if "due_date" in payload:
        row.due_date = _day(payload.get("due_date"), "Due date")

    log(db, current_user, entity_type="purchase_order", entity_id=po.id, requisition_id=po.requisition_id,
        action="Payment Updated", previous=before,
        new={"instalment": row.label, "status": row.status, "mode": row.mode, "amount": row.amount})
    db.commit()
    db.refresh(po)
    return po


#: action -> (permission, statuses it may come from, status it goes to, log entry)
PAYMENT_ACTIONS = {
    "SUBMIT": (DETAILS_PERMISSION, EDITABLE_PAYMENT, "SUBMITTED", "Payment Submitted To Accounts"),
    "APPROVE": (ACCOUNTS_PERMISSION, ("SUBMITTED",), "APPROVED", "Payment Approved By Accounts"),
    "REJECT": (ACCOUNTS_PERMISSION, ("SUBMITTED",), "RETURNED", "Payment Returned By Accounts"),
    "MARK_PAID": (ACCOUNTS_PERMISSION, ("APPROVED",), "PAID", "Payment Marked Paid"),
}


def payment_action(po_id: int, payment_id: int, action: str | None, remarks: str | None,
                   current_user: dict, db: Session) -> PurchaseOrder:
    """Move one instalment along: procurement submits it to accounts with
    its details; accounts approve it or return it with a reason, then
    mark it paid once the money has gone."""

    key = (action or "").strip().upper()
    if key not in PAYMENT_ACTIONS:
        raise HTTPException(status_code=400, detail="Action must be SUBMIT, APPROVE, REJECT or MARK_PAID.")
    permission, sources, target, entry = PAYMENT_ACTIONS[key]
    require(current_user, permission, db)

    po = get(po_id, db, lock=True)
    if po.status not in PAYABLE:
        raise HTTPException(status_code=409, detail="Payments are made once the PO is approved.")
    row = _payment(po, payment_id, db)
    if row.status not in sources:
        raise HTTPException(
            status_code=409,
            detail=f"This instalment is {row.status.lower()}; it cannot be {entry.split(' ')[1].lower()} now.",
        )

    note = text(remarks, 500)
    if key == "REJECT" and not note:
        raise HTTPException(status_code=400, detail="Say why it is being returned - procurement sees the reason.")
    if key == "SUBMIT":
        missing = _details_missing(row)
        if missing:
            raise HTTPException(status_code=400, detail=missing)

    who, name = actor(current_user, db)
    now = datetime.utcnow()
    previous = row.status
    row.status = target

    if key == "SUBMIT":
        row.submitted_at, row.submitted_by, row.submitted_by_name = now, who, name
        row.returned_reason = None
    elif key == "APPROVE":
        row.approved_at, row.approved_by_name = now, name
    elif key == "REJECT":
        row.returned_reason, row.returned_at, row.returned_by_name = note, now, name
    elif key == "MARK_PAID":
        row.paid_at, row.paid_by, row.paid_by_name = now, who, name
    if note and key != "REJECT":
        row.remarks = note

    log(db, current_user, entity_type="purchase_order", entity_id=po.id, requisition_id=po.requisition_id,
        action=entry, remarks=note or None,
        previous={"instalment": row.label, "status": previous},
        new={"instalment": row.label, "status": target, "mode": row.mode, "amount": row.amount})

    # Whoever acts next is told: accounts on a submission, procurement when
    # accounts send it back. The daily reminder covers the rest. Added in
    # this transaction (notify does not commit), so step and notice are
    # saved together.
    amount = f"Rs {row.amount:,.2f}"
    if key == "SUBMIT":
        notify(db, holders_of(ACCOUNTS_PERMISSION, db), module=MODULE, entity_id=po.id,
               action="Payment To Approve", link=link(po), current_user=current_user,
               message=f"{po.po_number}: {row.label} of {amount} to {po.vendor_name or 'the vendor'} is waiting for your approval.")
    elif key == "REJECT":
        notify(db, [row.submitted_by, po.created_by], module=MODULE, entity_id=po.id,
               action="Payment Returned", link=link(po), current_user=current_user,
               message=f"{po.po_number}: accounts returned {row.label} of {amount} - {note}")

    db.commit()
    db.refresh(po)
    return po


def payments(po: PurchaseOrder, db: Session) -> list[dict]:
    rows = (
        db.query(PurchaseOrderPayment)
        .filter(PurchaseOrderPayment.purchase_order_id == po.id)
        .order_by(PurchaseOrderPayment.line_no)
        .all()
    )
    return [
        {
            "id": r.id,
            "line_no": r.line_no,
            "label": r.label,
            "percent": r.percent,
            "amount": r.amount,
            "due_date": r.due_date.isoformat() if r.due_date else None,
            "week": r.due_date.isocalendar()[1] if r.due_date else None,
            "mode": r.mode,
            "bank_name": r.bank_name,
            "cheque_number": r.cheque_number,
            "cheque_date": r.cheque_date.isoformat() if r.cheque_date else None,
            "reference": r.reference,
            "status": r.status,
            "submitted_at": iso(r.submitted_at),
            "submitted_by_name": r.submitted_by_name,
            "approved_at": iso(r.approved_at),
            "approved_by_name": r.approved_by_name,
            "returned_reason": r.returned_reason,
            "returned_at": iso(r.returned_at),
            "returned_by_name": r.returned_by_name,
            "paid_at": iso(r.paid_at),
            "paid_by_name": r.paid_by_name,
            "remarks": r.remarks,
        }
        for r in rows
    ]


# ----------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------
def serialize_step(step: PurchaseOrderApproval, db: Session) -> dict:
    from app.services.procurement.common import names_for

    return {
        "id": step.id,
        "round": step.round,
        "level": step.level,
        "step": step.step,
        "step_label": ApprovalStep.label(step.step),
        "approver_label": step.approver_label or ", ".join(names_for(step.approver_ids, db)) or "Super admins",
        "decision": step.decision,
        "decided_by_name": step.decided_by_name,
        "decided_at": iso(step.decided_at),
        "remarks": step.remarks,
    }


_UNSET = object()


def serialize(po: PurchaseOrder, db: Session, *, full: bool = True, current_user: dict | None = None,
              pr=_UNSET, quote=_UNSET, step=_UNSET, item_count: int | None = None) -> dict:
    """One PO. A list passes what it already looked up for the whole page
    (serialize_many), so a row costs no queries of its own."""

    if pr is _UNSET:
        pr = db.query(PurchaseRequisition).filter(PurchaseRequisition.id == po.requisition_id).first()
    if quote is _UNSET:
        quote = db.query(VendorQuotation.quotation_number).filter(VendorQuotation.id == po.quotation_id).scalar()
    if step is _UNSET:
        step = pending_step(po, db)

    data = {
        "id": po.id,
        "po_number": po.po_number,
        "po_date": iso(po.po_date),
        "requisition_id": po.requisition_id,
        "pr_number": pr.pr_number if pr else None,
        "sales_order_id": po.sales_order_id,
        "sales_order_number": pr.sales_order_number if pr else None,
        "vendor_id": po.vendor_id,
        "vendor_name": po.vendor_name,
        "vendor_address": po.vendor_address,
        "vendor_gstin": po.vendor_gstin,
        "vendor_reference": po.vendor_reference,
        "quotation_id": po.quotation_id,
        "quotation_number": quote,
        "quotation_version": po.quotation_version,
        "bill_to": po.bill_to,
        "ship_to": po.ship_to,
        "payment_terms": po.payment_terms,
        "payment_term_type": po.payment_term_type,
        "advance_percent": po.advance_percent,
        "credit_days": po.credit_days,
        "payment_mode": po.payment_mode,
        "delivery_terms": po.delivery_terms,
        "freight_terms": po.freight_terms,
        "warranty_terms": po.warranty_terms,
        "terms": po.terms or [],
        "expected_delivery": po.expected_delivery.isoformat() if po.expected_delivery else None,
        "currency": po.currency,
        "subtotal": po.subtotal,
        "discount_total": po.discount_total,
        "taxable_total": po.taxable_total,
        "gst_total": po.gst_total,
        "charges_total": po.charges_total,
        "grand_total": po.grand_total,
        "status": po.status,
        "submitted_at": iso(po.submitted_at),
        "approved_by_name": po.approved_by_name,
        "approved_at": iso(po.approved_at),
        "sent_at": iso(po.sent_at),
        "acknowledged_at": iso(po.acknowledged_at),
        "closed_at": iso(po.closed_at),
        "remarks": po.remarks,
        "created_by": str(po.created_by) if po.created_by else None,
        "created_by_name": po.created_by_name,
        "created_at": iso(po.created_at),
        "item_count": item_count if item_count is not None else len(po.items or []),
        "next_actions": [a for a, target in ACTIONS.items() if target in POStatus.MOVES.get(po.status, ())],
        "waiting_on": serialize_step(step, db) if step else None,
        "requester_name": pr.requester_name if pr else None,
    }
    if current_user is not None:
        data["can_decide"] = may_decide(step, po, current_user)
        # Which payment steps this person may take; the server checks
        # again on every action.
        data["can_submit_payments"] = has_permission(current_user, DETAILS_PERMISSION, db)
        data["can_approve_payments"] = has_permission(current_user, ACCOUNTS_PERMISSION, db)

    if full:
        data["items"] = [
            {
                "id": i.id,
                "requisition_item_id": i.requisition_item_id,
                "quotation_item_id": i.quotation_item_id,
                "product_name": i.product_name,
                "sku": i.sku,
                "hsn": i.hsn,
                "description": i.description,
                "uom": i.uom,
                "quantity": i.quantity,
                "received_quantity": i.received_quantity,
                "unit_rate": i.unit_rate,
                "discount_amount": i.discount_amount,
                "taxable_amount": i.taxable_amount,
                "gst_rate": i.gst_rate,
                "gst_amount": i.gst_amount,
                "freight": i.freight,
                "other_charges": i.other_charges,
                "total": i.total,
            }
            for i in po.items
        ]
        data["approvals"] = [serialize_step(a, db) for a in approvals(po, db)]
        data["payments"] = payments(po, db)
        data["activity"] = activity_rows(db, entity_type="purchase_order", entity_id=po.id)

    return data


def serialize_many(pos: list[PurchaseOrder], db: Session, current_user: dict | None = None) -> list[dict]:
    """A page of POs with their requisitions, quotation numbers, pending
    steps and line counts read once for the page."""

    from sqlalchemy import func

    if not pos:
        return []
    ids = [po.id for po in pos]
    prs = {pr.id: pr for pr in db.query(PurchaseRequisition).filter(
        PurchaseRequisition.id.in_({po.requisition_id for po in pos})).all()}
    quotes = dict(db.query(VendorQuotation.id, VendorQuotation.quotation_number).filter(
        VendorQuotation.id.in_({po.quotation_id for po in pos})).all())
    rounds = {po.id: po.approval_round for po in pos if po.status == POStatus.PENDING_APPROVAL}
    steps = {}
    if rounds:
        for row in db.query(PurchaseOrderApproval).filter(
            PurchaseOrderApproval.purchase_order_id.in_(list(rounds)),
            PurchaseOrderApproval.decision == "PENDING",
        ).all():
            if rounds.get(row.purchase_order_id) == row.round:
                steps[row.purchase_order_id] = row
    counts = dict(
        db.query(PurchaseOrderItem.purchase_order_id, func.count(PurchaseOrderItem.id))
        .filter(PurchaseOrderItem.purchase_order_id.in_(ids))
        .group_by(PurchaseOrderItem.purchase_order_id).all()
    )
    return [
        serialize(po, db, full=False, current_user=current_user, pr=prs.get(po.requisition_id),
                  quote=quotes.get(po.quotation_id), step=steps.get(po.id), item_count=counts.get(po.id, 0))
        for po in pos
    ]


def list_orders(db: Session, *, search: str | None = None, status: str | None = None,
                vendor_id: int | None = None, requisition_id: int | None = None,
                page: int = 1, size: int = 20) -> dict:
    query = db.query(PurchaseOrder)

    from sqlalchemy import func

    counts = dict(db.query(PurchaseOrder.status, func.count(PurchaseOrder.id)).group_by(PurchaseOrder.status).all())

    if status:
        query = query.filter(PurchaseOrder.status == status.upper())
    if vendor_id:
        query = query.filter(PurchaseOrder.vendor_id == vendor_id)
    if requisition_id:
        query = query.filter(PurchaseOrder.requisition_id == requisition_id)
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(or_(PurchaseOrder.po_number.ilike(like), PurchaseOrder.vendor_name.ilike(like)))

    total = query.count()
    size = min(max(size, 1), 100)
    rows = query.order_by(PurchaseOrder.id.desc()).offset(max(0, (page - 1) * size)).limit(size).all()
    return {"data": serialize_many(rows, db), "total": total, "counts": counts}


# ----------------------------------------------------------------------
# The PDF
# ----------------------------------------------------------------------
def filename(po: PurchaseOrder) -> str:
    return f"Purchase_Order_{po.po_number}.pdf"


def render_pdf(po: PurchaseOrder, db: Session) -> bytes:
    """The order as it goes to the vendor, laid out like the sales order PDF."""

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

    from app.services.quotation_pdf_service import _words
    from app.services.sales_order_pdf_service import (
        NAVY_DARK,
        SLATE_200,
        SLATE_50,
        SLATE_500,
        SLATE_700,
        SLATE_900,
        _date,
        _p,
        _rs,
        _style,
    )

    company = _company(db)
    pr = db.query(PurchaseRequisition).filter(PurchaseRequisition.id == po.requisition_id).first()

    label = _style("label", 8, SLATE_500)
    value = _style("value", 8.5, SLATE_900)
    bold = _style("bold", 8.5, SLATE_900, bold=True)
    head = _style("head", 8, "#ffffff", bold=True)
    cell = _style("cell", 8, SLATE_900)
    cell_r = _style("cell_r", 8, SLATE_900, align="right")
    section = _style("section", 10.5, SLATE_700, bold=True)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm,
        bottomMargin=12 * mm, title=f"Purchase Order {po.po_number}",
    )
    width = A4[0] - 24 * mm
    story: list = []

    story.append(Table(
        [[
            [_p(company.get("name") or "", _style("co", 13, NAVY_DARK, bold=True)),
             _p("\n".join(company.get("address") or []), label),
             _p(f"GSTIN: {company.get('gstin')}" if company.get("gstin") else "", label)],
            [_p("PURCHASE ORDER", _style("title", 16, SLATE_900, bold=True, align="right")),
             _p(po.po_number, _style("no", 10, NAVY_DARK, bold=True, align="right")),
             _p(f"Date: {_date(po.po_date)}", _style("dt", 8.5, SLATE_500, align="right"))],
        ]],
        colWidths=[width * 0.6, width * 0.4],
        style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                          ("LINEBELOW", (0, 0), (-1, -1), 1, colors.HexColor(NAVY_DARK)),
                          ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]),
    ))
    story.append(Spacer(1, 4 * mm))

    def block(title, lines):
        return [_p(title, label)] + [_p(line, value) for line in lines if line]

    story.append(Table(
        [[
            block("VENDOR", [po.vendor_name, po.vendor_address, f"GSTIN: {po.vendor_gstin}" if po.vendor_gstin else None]),
            block("BILL TO", (po.bill_to or "").split("\n")),
            block("SHIP TO", (po.ship_to or "").split("\n")),
        ]],
        colWidths=[width / 3] * 3,
        style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 4),
                          ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(SLATE_200)),
                          ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor(SLATE_200)),
                          ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]),
    ))
    story.append(Spacer(1, 3 * mm))

    refs = [
        ("PR Number", pr.pr_number if pr else "-"), ("Sales Order", (pr.sales_order_number if pr else None) or "-"),
        ("Vendor Quotation", po.vendor_reference or "-"), ("Quotation Version", po.quotation_version or "-"),
        ("Expected Delivery", po.expected_delivery.strftime("%d/%m/%Y") if po.expected_delivery else "-"),
        ("Currency", po.currency or "INR"),
    ]
    story.append(Table(
        [[_p(k, label), _p(v, bold)] for k, v in refs[:3]],
        colWidths=[30 * mm, width / 2 - 30 * mm], hAlign="LEFT",
        style=TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                          ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]),
    ))
    story.append(Table(
        [[_p(k, label), _p(v, bold)] for k, v in refs[3:]],
        colWidths=[30 * mm, width / 2 - 30 * mm], hAlign="LEFT",
        style=TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                          ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]),
    ))
    story.append(Spacer(1, 4 * mm))

    rows = [[_p(h, head) for h in ("#", "Item / SKU", "HSN", "Qty", "Rate", "Discount", "Taxable", "GST", "Charges", "Total")]]
    for number, item in enumerate(po.items, start=1):
        rows.append([
            _p(str(number), cell),
            _p(f"{item.product_name or '-'}" + (f"\n{item.sku}" if item.sku else ""), cell),
            _p(item.hsn or "-", cell),
            _p(f"{item.quantity:g} {item.uom or ''}".strip(), cell_r),
            _p(_rs(item.unit_rate), cell_r),
            _p(_rs(item.discount_amount), cell_r),
            _p(_rs(item.taxable_amount), cell_r),
            _p(f"{item.gst_rate:g}%\n{_rs(item.gst_amount)}", cell_r),
            _p(_rs((item.freight or 0) + (item.other_charges or 0)), cell_r),
            _p(_rs(item.total), cell_r),
        ])
    lines = Table(rows, colWidths=[7 * mm, 45 * mm, 15 * mm, 14 * mm, 18 * mm, 16 * mm, 19 * mm, 17 * mm, 16 * mm, width - 167 * mm],
                  repeatRows=1)
    lines.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(NAVY_DARK)),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor(SLATE_200)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(SLATE_50)]),
    ]))
    story.append(lines)
    story.append(Spacer(1, 3 * mm))

    totals = [
        ("Subtotal", po.subtotal), ("Discount", -(po.discount_total or 0)), ("Taxable Value", po.taxable_total),
        ("GST", po.gst_total), ("Freight & Other Charges", po.charges_total),
    ]
    total_rows = [[_p(k, label), _p(_rs(v), cell_r)] for k, v in totals]
    total_rows.append([_p("Total PO Value", bold), _p(_rs(po.grand_total), _style("gt", 9.5, NAVY_DARK, bold=True, align="right"))])
    story.append(Table(total_rows, colWidths=[45 * mm, 35 * mm], hAlign="RIGHT",
                       style=TableStyle([("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.HexColor(NAVY_DARK)),
                                         ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)])))
    story.append(Spacer(1, 2 * mm))
    story.append(_p(f"Amount in words: {_words(po.grand_total)}", _style("words", 8.5, SLATE_700, bold=True)))
    story.append(Spacer(1, 4 * mm))

    story.append(_p("Commercial Terms", section))
    story.append(Spacer(1, 1.5 * mm))
    for k, v in (("Payment", po.payment_terms), ("Delivery", po.delivery_terms), ("Freight", po.freight_terms), ("Warranty", po.warranty_terms)):
        if v:
            story.append(_p(f"{k}: {v}", value))
    if po.terms:
        story.append(Spacer(1, 3 * mm))
        story.append(_p("Terms and Conditions", section))
        story.append(Spacer(1, 1.5 * mm))
        for number, term in enumerate(po.terms, start=1):
            story.append(_p(f"{number}. {term}", value))

    story.append(Spacer(1, 12 * mm))
    story.append(Table(
        [[_p(f"Prepared by: {po.created_by_name or '-'}", label),
          _p(f"Authorised Signatory\n{company.get('name') or ''}", _style("sig", 8.5, SLATE_700, bold=True, align="right"))]],
        colWidths=[width / 2, width / 2],
        style=TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0)]),
    ))
    if po.status in (POStatus.DRAFT, POStatus.PENDING_APPROVAL):
        story.append(Spacer(1, 4 * mm))
        story.append(_p(f"{po.status.replace('_', ' ').title()} - not valid for supply until approved.",
                        _style("draft", 8.5, "#e11d48", bold=True)))

    doc.build(story)
    return buffer.getvalue()


def list_waiting(current_user: dict, db: Session) -> list[dict]:
    """POs waiting on this person's decision now (all pending steps for a
    super admin, who stands in for anyone) - never their own POs."""

    from sqlalchemy import String, cast

    from app.services.procurement.common import as_uuid

    me = str(current_user.get("user_id") or "")
    steps = db.query(PurchaseOrderApproval.purchase_order_id).join(
        PurchaseOrder,
        (PurchaseOrder.id == PurchaseOrderApproval.purchase_order_id)
        & (PurchaseOrder.approval_round == PurchaseOrderApproval.round),
    ).filter(
        PurchaseOrder.status == POStatus.PENDING_APPROVAL,
        PurchaseOrderApproval.decision == "PENDING",
        or_(PurchaseOrder.created_by.is_(None), PurchaseOrder.created_by != as_uuid(me)),
    )
    if not is_super_admin(current_user):
        steps = steps.filter(cast(PurchaseOrderApproval.approver_ids, String).like(f'%"{me}"%'))

    ids = [row[0] for row in steps.distinct().all()]
    return serialize_many(
        db.query(PurchaseOrder).filter(PurchaseOrder.id.in_(ids or [-1])).order_by(PurchaseOrder.submitted_at).all(),
        db, current_user,
    )
