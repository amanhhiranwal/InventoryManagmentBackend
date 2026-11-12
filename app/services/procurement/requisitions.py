"""The purchase requisition: what an employee needs, confirmed by their manager.

The requester says what is needed, how many and by when - not the price;
pricing is procurement's, from the vendors' quotations. So a requisition
is approved by the requester's reporting manager alone, and the amount
bands of the approval matrix are applied to the purchase order instead
(see purchase_orders.py), once the price is known.

Returning a PR for correction ends that round; the next submission starts
another, and both stay on record. Where the requester has no manager the
super admins decide. Nobody ever decides a step on their own PR.

Procurement may also raise a requisition directly (create_direct): it is
their own need-to-buy, so it goes straight to the desk without a manager's
approval.
"""

from datetime import date, datetime

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.procurement import (
    PRIORITIES,
    PURCHASE_TYPES,
    ApprovalStep,
    PRStatus,
    ProcurementStatus,
)
from app.core.timezone import iso
from app.models.procurement import (
    CostCenter,
    Department,
    PurchaseRequisition,
    PurchaseRequisitionApproval,
    PurchaseRequisitionItem,
    Vendor,
)
from app.services.procurement import approval_chain, masters
from app.services.procurement.common import (
    activity_rows,
    actor,
    as_uuid,
    attachments,
    can_link_sales_order,
    full_name,
    has_permission,
    holders_of,
    is_super_admin,
    log,
    money,
    names_for,
    notify,
    next_reference,
    text,
    today,
    user_row,
)

MODULE = "purchase_requisition"


def link(pr: PurchaseRequisition) -> str:
    return f"/procurement/requisitions/{pr.id}"


# ----------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------
def get(pr_id: int, db: Session, *, lock: bool = False) -> PurchaseRequisition:
    query = db.query(PurchaseRequisition).filter(PurchaseRequisition.id == pr_id)
    if lock:
        query = query.with_for_update()
    pr = query.first()
    if pr is None:
        raise HTTPException(status_code=404, detail="Purchase requisition not found.")
    return pr


def approvals(pr: PurchaseRequisition, db: Session) -> list[PurchaseRequisitionApproval]:
    return (
        db.query(PurchaseRequisitionApproval)
        .filter(PurchaseRequisitionApproval.requisition_id == pr.id)
        .order_by(PurchaseRequisitionApproval.round, PurchaseRequisitionApproval.level)
        .all()
    )


def pending_step(pr: PurchaseRequisition, db: Session) -> PurchaseRequisitionApproval | None:
    if pr.status != PRStatus.PENDING_APPROVAL:
        return None
    return (
        db.query(PurchaseRequisitionApproval)
        .filter(
            PurchaseRequisitionApproval.requisition_id == pr.id,
            PurchaseRequisitionApproval.round == pr.approval_round,
            PurchaseRequisitionApproval.decision == "PENDING",
        )
        .first()
    )


def may_decide(step: PurchaseRequisitionApproval | None, pr: PurchaseRequisition, current_user: dict) -> bool:
    """Whether this person may decide the step the PR is waiting on: someone
    named on it, or a super admin standing in - never the requester."""

    if step is None:
        return False
    return approval_chain.may_decide(step.approver_ids, current_user, exclude={str(pr.requested_by)})


def assert_can_view(pr: PurchaseRequisition, current_user: dict, db: Session) -> None:
    """The requester, their managers, everyone on the chain, and procurement
    once it is approved."""

    if is_super_admin(current_user):
        return

    me = str(current_user.get("user_id") or "")

    if me == str(pr.requested_by):
        return

    for step in approvals(pr, db):
        if me in [str(i) for i in step.approver_ids or []] or me == str(step.decided_by or ""):
            return

    if pr.status == PRStatus.APPROVED and has_permission(current_user, "procurement.read", db):
        return

    from app.services.hierarchy_service import HierarchyService

    if HierarchyService.can_see_user(current_user, pr.requested_by, db):
        return

    raise HTTPException(status_code=403, detail="This requisition is not yours to see.")


def serialize_item(item: PurchaseRequisitionItem) -> dict:
    return {
        "id": item.id,
        "line_no": item.line_no,
        "product_name": item.product_name,
        "sku": item.sku,
        "category": item.category,
        "brand": item.brand,
        "model": item.model,
        "description": item.description,
        "specification": item.specification,
        "uom": item.uom,
        "quantity": item.quantity,
        "approved_quantity": item.approved_quantity,
        "effective_quantity": item.approved_quantity if item.approved_quantity is not None else item.quantity,
        "quantity_change_reason": item.quantity_change_reason,
        "est_unit_price": item.est_unit_price,
        "tax_percent": item.tax_percent,
        "est_total": item.est_total,
        "required_date": item.required_date.isoformat() if item.required_date else None,
        "preferred_vendor_id": item.preferred_vendor_id,
        "rfq_vendor_ids": item.rfq_vendor_ids or [],
        "remarks": item.remarks,
        "attachments": item.attachments or [],
    }


def serialize_step(step: PurchaseRequisitionApproval, db: Session) -> dict:
    # The label is written when the chain is built; names are looked up
    # only for a row that somehow has none.
    names = [] if step.approver_label else names_for(step.approver_ids, db)
    return {
        "id": step.id,
        "round": step.round,
        "level": step.level,
        "step": step.step,
        "step_label": ApprovalStep.LABELS.get(step.step, step.step),
        "approver_ids": [str(i) for i in step.approver_ids or []],
        "approver_label": step.approver_label or (", ".join(names) if names else "Super admins"),
        "decision": step.decision,
        "decided_by": str(step.decided_by) if step.decided_by else None,
        "decided_by_name": step.decided_by_name,
        "decided_at": iso(step.decided_at),
        "remarks": step.remarks,
    }


def _pending_days(pr: PurchaseRequisition) -> int:
    since = pr.submitted_at or pr.created_at
    return max(0, (datetime.utcnow() - since).days) if since else 0


_UNSET = object()


def serialize(pr: PurchaseRequisition, db: Session, current_user: dict | None = None, *, full: bool = True,
              step=_UNSET, item_count: int | None = None) -> dict:
    """One requisition. A list passes ``step`` and ``item_count`` already
    looked up for the whole page (serialize_many), so a row costs no
    queries of its own."""

    if step is _UNSET:
        step = pending_step(pr, db)
    data = {
        "id": pr.id,
        "pr_number": pr.pr_number,
        "pr_date": iso(pr.pr_date),
        "requested_by": str(pr.requested_by),
        "requester_name": pr.requester_name,
        "employee_id": pr.employee_id,
        "designation": pr.designation,
        "department_id": pr.department_id,
        "department_name": pr.department_name,
        "cost_center_id": pr.cost_center_id,
        "cost_center_code": pr.cost_center_code,
        "reporting_manager_id": str(pr.reporting_manager_id) if pr.reporting_manager_id else None,
        "reporting_manager_name": pr.reporting_manager_name,
        "purchase_type": pr.purchase_type,
        "purchase_type_label": PURCHASE_TYPES.get(pr.purchase_type, pr.purchase_type),
        "priority": pr.priority,
        "required_by": pr.required_by.isoformat() if pr.required_by else None,
        "currency": pr.currency,
        "sales_order_id": pr.sales_order_id,
        "sales_order_number": pr.sales_order_number,
        "customer_name": pr.customer_name,
        "project_code": pr.project_code,
        "linked_record": pr.linked_record,
        "delivery_location": pr.delivery_location,
        "purpose": pr.purpose,
        "remarks": pr.remarks,
        "budget_code": pr.budget_code,
        "budget_available": pr.budget_available,
        "budgeted": pr.budgeted,
        "preferred_vendor_id": pr.preferred_vendor_id,
        "preferred_vendor_name": pr.preferred_vendor_name,
        "preferred_vendor_reason": pr.preferred_vendor_reason,
        "alternatives_considered": pr.alternatives_considered,
        "special_requirements": pr.special_requirements,
        "attachments": pr.attachments or [],
        "estimated_total": pr.estimated_total,
        "status": pr.status,
        "approval_round": pr.approval_round,
        "current_level": pr.current_level,
        "submitted_at": iso(pr.submitted_at),
        "approved_at": iso(pr.approved_at),
        "resubmitted": pr.resubmitted,
        "procurement_status": pr.procurement_status,
        "procurement_since": iso(pr.procurement_since),
        "assigned_to": str(pr.assigned_to) if pr.assigned_to else None,
        "assigned_name": pr.assigned_name,
        "item_count": item_count if item_count is not None else len(pr.items or []),
        "pending_days": _pending_days(pr),
        "waiting_on": serialize_step(step, db) if step else None,
        "created_at": iso(pr.created_at),
    }

    if current_user is not None:
        mine = str(current_user.get("user_id") or "") == str(pr.requested_by)
        data["can_edit"] = mine and pr.status in PRStatus.EDITABLE
        data["can_cancel"] = mine and pr.status in (*PRStatus.EDITABLE, PRStatus.PENDING_APPROVAL)
        data["can_decide"] = may_decide(step, pr, current_user)

    if full:
        data["items"] = [serialize_item(i) for i in pr.items or []]
        # The vendors asked to quote, by name, in one lookup for all lines.
        asked = {v for i in pr.items or [] for v in (i.rfq_vendor_ids or [])}
        if asked:
            names = dict(db.query(Vendor.id, Vendor.name).filter(Vendor.id.in_(asked)).all())
            for row in data["items"]:
                row["rfq_vendors"] = [{"id": v, "name": names.get(v, f"#{v}")} for v in row["rfq_vendor_ids"]]
        data["approvals"] = [serialize_step(s, db) for s in approvals(pr, db)]
        data["procurement_remarks"] = pr.procurement_remarks
        data["internal_notes"] = pr.internal_notes or []
        data["activity"] = activity_rows(db, requisition_id=pr.id)

    return data


def serialize_many(prs: list[PurchaseRequisition], db: Session, current_user: dict | None = None) -> list[dict]:
    """A page of requisitions in three queries rather than three per row:
    the pending steps and the line counts are read for all of them at once."""

    from sqlalchemy import func

    ids = [pr.id for pr in prs]
    if not ids:
        return []
    rounds = {pr.id: pr.approval_round for pr in prs if pr.status == PRStatus.PENDING_APPROVAL}
    steps = {}
    if rounds:
        for row in db.query(PurchaseRequisitionApproval).filter(
            PurchaseRequisitionApproval.requisition_id.in_(list(rounds)),
            PurchaseRequisitionApproval.decision == "PENDING",
        ).all():
            if rounds.get(row.requisition_id) == row.round:
                steps[row.requisition_id] = row
    counts = dict(
        db.query(PurchaseRequisitionItem.requisition_id, func.count(PurchaseRequisitionItem.id))
        .filter(PurchaseRequisitionItem.requisition_id.in_(ids))
        .group_by(PurchaseRequisitionItem.requisition_id).all()
    )
    return [
        serialize(pr, db, current_user, full=False, step=steps.get(pr.id), item_count=counts.get(pr.id, 0))
        for pr in prs
    ]


# ----------------------------------------------------------------------
# Writing a draft
# ----------------------------------------------------------------------
def _date(value, field: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field} is not a date.")


def _number(value, field: str, *, positive: bool = False, allow_none: bool = True) -> float | None:
    if value in (None, ""):
        if allow_none:
            return None
        raise HTTPException(status_code=400, detail=f"{field} is required.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{field} must be a number.")
    if number < 0 or (positive and number <= 0):
        raise HTTPException(status_code=400, detail=f"{field} must be greater than zero.")
    return number


def _apply(pr: PurchaseRequisition, payload: dict, current_user: dict, db: Session) -> None:
    """The requester's fields and lines, written onto the PR."""

    purchase_type = (text(payload.get("purchase_type")) or "").upper() or pr.purchase_type or "OTHER"
    if purchase_type not in PURCHASE_TYPES:
        raise HTTPException(status_code=400, detail="Choose a purchase type from the list.")

    priority = (text(payload.get("priority")) or "NORMAL").upper()
    if priority not in PRIORITIES:
        raise HTTPException(status_code=400, detail="Priority is Low, Normal, High or Urgent.")

    pr.purchase_type = purchase_type
    pr.priority = priority
    pr.required_by = _date(payload.get("required_by"), "Required by date")
    pr.currency = (text(payload.get("currency"), 10) or "INR").upper()

    # The department is the requester's own when they have one; otherwise
    # the form's choice.
    requester = user_row(pr.requested_by, db)
    department = masters.department_of(requester, db)
    if department is None and payload.get("department_id"):
        department = db.query(Department).filter(Department.id == int(payload["department_id"])).first()
        if department is None:
            raise HTTPException(status_code=400, detail="That department does not exist.")
    pr.department_id = department.id if department else None
    pr.department_name = department.name if department else text(payload.get("department_name"), 150)

    cost_center = None
    if payload.get("cost_center_id"):
        cost_center = db.query(CostCenter).filter(CostCenter.id == int(payload["cost_center_id"])).first()
        if cost_center is None or not cost_center.is_active:
            raise HTTPException(status_code=400, detail="Choose an active cost centre.")
    pr.cost_center_id = cost_center.id if cost_center else None
    pr.cost_center_code = f"{cost_center.code} - {cost_center.name}" if cost_center else None

    if purchase_type == "SALES_ORDER" and not can_link_sales_order(current_user, db):
        raise HTTPException(
            status_code=403,
            detail="Only the sales team raises a requisition against a sales order.",
        )

    # A sales order reference fills the customer from the order.
    previous_order = pr.sales_order_id
    pr.sales_order_id = None
    pr.sales_order_number = None
    if payload.get("sales_order_id") and purchase_type == "SALES_ORDER":
        from app.models.sales_order import SalesOrder

        order = db.query(SalesOrder).filter(SalesOrder.id == int(payload["sales_order_id"])).first()
        if order is None:
            raise HTTPException(status_code=400, detail="That sales order does not exist.")
        # Only an order this person may open - the same rule as the Sales
        # Order list - so a PR cannot be used to read another region's
        # customer. An order already on the PR is kept as it is.
        if order.id != previous_order:
            from app.services.sales_order_service import SalesOrderService

            SalesOrderService.assert_can_view(order, current_user, db)
        pr.sales_order_id = order.id
        pr.sales_order_number = order.order_number
        pr.customer_name = order.company_name or order.customer_name
    else:
        pr.customer_name = text(payload.get("customer_name"), 200)

    pr.project_code = text(payload.get("project_code"), 100)
    pr.linked_record = text(payload.get("linked_record"), 200)
    pr.delivery_location = text(payload.get("delivery_location"), 300)
    pr.purpose = text(payload.get("purpose"))
    pr.remarks = text(payload.get("remarks"))
    # The budget is the manager's to record when approving, and vendors are
    # the purchase team's: the requester's form sends neither, so they are
    # only changed when a caller does send them.
    if "budget_code" in payload:
        pr.budget_code = text(payload.get("budget_code"), 100)
    if "budget_available" in payload:
        pr.budget_available = _number(payload.get("budget_available"), "Budget available")
    if "budgeted" in payload:
        pr.budgeted = payload.get("budgeted") if payload.get("budgeted") in (True, False) else None

    if "preferred_vendor_id" in payload or "preferred_vendor_name" in payload:
        pr.preferred_vendor_id = None
        pr.preferred_vendor_name = text(payload.get("preferred_vendor_name"), 200)
        if payload.get("preferred_vendor_id"):
            vendor = db.query(Vendor).filter(Vendor.id == int(payload["preferred_vendor_id"])).first()
            if vendor is None:
                raise HTTPException(status_code=400, detail="That vendor does not exist.")
            pr.preferred_vendor_id = vendor.id
            pr.preferred_vendor_name = vendor.name
    if "preferred_vendor_reason" in payload:
        pr.preferred_vendor_reason = text(payload.get("preferred_vendor_reason"), 500)
    if "alternatives_considered" in payload:
        pr.alternatives_considered = text(payload.get("alternatives_considered"), 1000)
    pr.special_requirements = text(payload.get("special_requirements"), 1000)
    pr.attachments = attachments(payload.get("attachments"))

    # The lines are replaced whole: a draft is the requester's to rework.
    lines = payload.get("items") or []
    pr.items = []
    total = 0.0

    for number, raw in enumerate(lines, start=1):
        name = text(raw.get("product_name"), 300)
        quantity = _number(raw.get("quantity"), f"Line {number} quantity", positive=True, allow_none=True)
        price = _number(raw.get("est_unit_price"), f"Line {number} estimated unit price")
        tax = _number(raw.get("tax_percent"), f"Line {number} tax %")
        if tax is not None and tax > 100:
            raise HTTPException(status_code=400, detail=f"Line {number} tax % cannot be over 100.")

        line_total = money((quantity or 0) * (price or 0) * (1 + (tax or 0) / 100))
        total += line_total

        pr.items.append(PurchaseRequisitionItem(
            line_no=number,
            product_name=name or "",
            sku=text(raw.get("sku"), 100),
            category=text(raw.get("category"), 150),
            brand=text(raw.get("brand"), 150),
            model=text(raw.get("model"), 150),
            description=text(raw.get("description")),
            specification=text(raw.get("specification")),
            uom=text(raw.get("uom"), 30) or "Nos",
            quantity=quantity or 0,
            est_unit_price=price,
            tax_percent=tax,
            est_total=line_total,
            required_date=_date(raw.get("required_date"), f"Line {number} required date"),
            preferred_vendor_id=int(raw["preferred_vendor_id"]) if raw.get("preferred_vendor_id") else None,
            remarks=text(raw.get("remarks"), 1000),
            attachments=attachments(raw.get("attachments")),
        ))

    pr.estimated_total = money(total)


def _assert_complete(pr: PurchaseRequisition, db: Session) -> None:
    """Everything the form marks required, checked again on the server."""

    problems = []

    if not pr.items:
        problems.append("Add at least one item.")
    for item in pr.items:
        if not item.product_name:
            problems.append(f"Line {item.line_no} needs a product name.")
        if not item.quantity or item.quantity <= 0:
            problems.append(f"Line {item.line_no} needs a quantity above nothing.")
        if not item.uom:
            problems.append(f"Line {item.line_no} needs a unit of measure.")

    if not pr.required_by:
        problems.append("Enter the required by date.")
    elif pr.required_by < (pr.pr_date.date() if pr.pr_date else today()):
        problems.append("The required by date cannot be before the date the PR was raised.")

    if not pr.purpose:
        problems.append("Say why this is needed - the business justification.")
    if not pr.delivery_location:
        problems.append("Enter the delivery location.")
    if pr.purchase_type == "SALES_ORDER" and not pr.sales_order_id:
        problems.append("Pick the sales order this purchase is for.")

    # Only asked for once the masters exist, so a fresh install can raise PRs.
    if not pr.department_id and db.query(Department.id).filter(Department.is_active.is_(True)).first():
        problems.append("Choose the department.")
    if not pr.cost_center_id and db.query(CostCenter.id).filter(CostCenter.is_active.is_(True)).first():
        problems.append("Choose the cost centre.")

    if pr.preferred_vendor_name and not pr.preferred_vendor_reason:
        problems.append("Say why the preferred vendor is preferred.")

    if problems:
        raise HTTPException(status_code=400, detail=" ".join(problems))


def create(payload: dict, current_user: dict, db: Session) -> PurchaseRequisition:
    """A new PR as a draft, owned by whoever is signed in."""

    me = user_row(current_user.get("user_id"), db)
    if me is None:
        raise HTTPException(status_code=401, detail="Sign in again.")

    manager = user_row(me.reports_to_id, db) if me.reports_to_id else None

    pr = PurchaseRequisition(
        pr_number=next_reference(db, PurchaseRequisition, "pr_number", "PR"),
        requested_by=me.id,
        requester_name=full_name(me),
        employee_id=me.employee_id,
        designation=me.roles[0].role_name if me.roles else None,
        reporting_manager_id=manager.id if manager else None,
        reporting_manager_name=full_name(manager) or None,
        status=PRStatus.DRAFT,
    )
    db.add(pr)

    _apply(pr, payload, current_user, db)
    # Submitting straight away: check it first, so a refusal saves nothing.
    # Otherwise the draft stays behind and the corrected retry makes a second.
    if payload.get("submit"):
        _assert_complete(pr, db)
    db.flush()

    log(db, current_user, entity_type="requisition", entity_id=pr.id, requisition_id=pr.id,
        action="PR Created", new={"status": pr.status, "estimated_total": pr.estimated_total})
    db.commit()
    db.refresh(pr)

    if payload.get("submit"):
        return submit(pr.id, current_user, db)
    return pr


def create_direct(payload: dict, current_user: dict, db: Session) -> PurchaseRequisition:
    """Procurement raising a requisition straight onto the desk.

    The manager's approval confirms an employee's need; when the purchase
    team raises one - for itself or on someone's behalf (``requested_for``)
    - the need is already confirmed, so it skips that step and is assigned
    to whoever raised it. Each line may carry its GST % and the vendors to
    ask for a quotation (``vendor_ids``); those are recorded as the first
    quotation request, so the desk starts at collecting quotes.
    """

    me = user_row(current_user.get("user_id"), db)
    if me is None:
        raise HTTPException(status_code=401, detail="Sign in again.")

    requester = me
    if payload.get("requested_for"):
        requester = user_row(payload["requested_for"], db)
        if requester is None or not requester.is_active:
            raise HTTPException(status_code=400, detail="Choose an active person the requisition is for.")

    # The vendors are checked before anything is saved, so a vendor that
    # cannot be asked fails the whole request rather than leaving a PR
    # behind that a retry would duplicate.
    asked = set()
    for number, raw in enumerate(payload.get("items") or [], start=1):
        try:
            asked |= {int(v) for v in (raw.get("vendor_ids") or [])}
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"Line {number}: vendors are ids.")
    if asked:
        from app.core.procurement import VendorStatus

        found = {v.id: v for v in db.query(Vendor).filter(Vendor.id.in_(asked)).all()}
        if set(found) != asked:
            raise HTTPException(status_code=400, detail="Some vendors do not exist.")
        unusable = [v.name for v in found.values() if v.status not in VendorStatus.USABLE]
        if unusable:
            raise HTTPException(status_code=409, detail=f"Not approved or active: {', '.join(sorted(unusable))}.")

    manager = user_row(requester.reports_to_id, db) if requester.reports_to_id else None
    now = datetime.utcnow()
    who, name = actor(current_user, db)
    pr = PurchaseRequisition(
        pr_number=next_reference(db, PurchaseRequisition, "pr_number", "PR"),
        requested_by=requester.id,
        requester_name=full_name(requester),
        employee_id=requester.employee_id,
        designation=requester.roles[0].role_name if requester.roles else None,
        reporting_manager_id=manager.id if manager else None,
        reporting_manager_name=full_name(manager) or None,
        status=PRStatus.APPROVED,
        submitted_at=now,
        approved_at=now,
        procurement_status=ProcurementStatus.ASSIGNED,
        procurement_since=now,
        assigned_to=who,
        assigned_name=name,
    )
    db.add(pr)
    _apply(pr, payload, current_user, db)
    _assert_complete(pr, db)
    db.flush()

    on_behalf = requester.id != me.id
    log(db, current_user, entity_type="requisition", entity_id=pr.id, requisition_id=pr.id,
        action="PR Raised By Procurement",
        new={"status": pr.status, "procurement_status": pr.procurement_status, "assigned_to": name,
             "for": pr.requester_name if on_behalf else None})
    if on_behalf:
        notify(db, [str(requester.id)], module=MODULE, entity_id=pr.id, action="PR Raised For You",
               message=f"{name} raised {pr.pr_number} for you; it is with the procurement team.",
               link=link(pr), current_user=current_user)
    db.commit()
    db.refresh(pr)

    # The vendors to ask, line by line, in the order the lines were given.
    plan = {}
    for item, raw in zip(pr.items, payload.get("items") or []):
        vendors = raw.get("vendor_ids") or []
        if vendors:
            plan[item.id] = vendors
    if plan:
        from app.services.procurement import desk

        desk.request_quotations(pr.id, plan, None, "Asked when the requisition was raised.", current_user, db)
        db.refresh(pr)
    return pr


def update(pr_id: int, payload: dict, current_user: dict, db: Session) -> PurchaseRequisition:
    pr = get(pr_id, db, lock=True)

    if str(current_user.get("user_id") or "") != str(pr.requested_by):
        raise HTTPException(status_code=403, detail="Only the person who raised this requisition can change it.")
    if pr.status not in PRStatus.EDITABLE:
        raise HTTPException(
            status_code=409,
            detail="A submitted requisition cannot be changed. Ask for it to be returned for correction.",
        )

    before = {"estimated_total": pr.estimated_total, "items": len(pr.items or [])}
    _apply(pr, payload, current_user, db)
    log(db, current_user, entity_type="requisition", entity_id=pr.id, requisition_id=pr.id,
        action="PR Updated", previous=before,
        new={"estimated_total": pr.estimated_total, "items": len(pr.items or [])})
    db.commit()
    db.refresh(pr)

    if payload.get("submit"):
        return submit(pr.id, current_user, db)
    return pr


# ----------------------------------------------------------------------
# The approval chain
# ----------------------------------------------------------------------
def _build_chain(pr: PurchaseRequisition, db: Session) -> list[PurchaseRequisitionApproval]:
    """One step: the requester's reporting manager confirms the need."""

    rows = approval_chain.build(
        [ApprovalStep.REPORTING_MANAGER],
        requester_id=pr.requested_by, department_id=pr.department_id,
        exclude={str(pr.requested_by)}, db=db,
    )
    approval_chain.assert_decidable(rows, {str(pr.requested_by)}, db)
    return [
        PurchaseRequisitionApproval(requisition_id=pr.id, round=pr.approval_round, **row)
        for row in rows
    ]


def _step_people(step: PurchaseRequisitionApproval, pr: PurchaseRequisition, db: Session) -> list[str]:
    return approval_chain.people(step.approver_ids, db, exclude={str(pr.requested_by)})


def submit(pr_id: int, current_user: dict, db: Session) -> PurchaseRequisition:
    pr = get(pr_id, db, lock=True)

    if str(current_user.get("user_id") or "") != str(pr.requested_by):
        raise HTTPException(status_code=403, detail="Only the person who raised this requisition can submit it.")
    if pr.status not in PRStatus.EDITABLE:
        raise HTTPException(status_code=409, detail="This requisition has already been submitted.")

    _assert_complete(pr, db)

    resubmission = pr.status == PRStatus.RETURNED
    pr.approval_round = (pr.approval_round or 0) + 1
    pr.status = PRStatus.PENDING_APPROVAL
    pr.submitted_at = datetime.utcnow()
    pr.resubmitted = resubmission

    # The manager as they are today, in case it changed since the draft.
    requester = user_row(pr.requested_by, db)
    manager = user_row(requester.reports_to_id, db) if requester and requester.reports_to_id else None
    pr.reporting_manager_id = manager.id if manager else None
    pr.reporting_manager_name = full_name(manager) or None

    chain = _build_chain(pr, db)
    for row in chain:
        db.add(row)
    pr.current_level = chain[0].level

    action = "PR Resubmitted" if resubmission else "PR Submitted"
    log(db, current_user, entity_type="requisition", entity_id=pr.id, requisition_id=pr.id,
        action=action, new={"status": pr.status, "round": pr.approval_round,
                            "chain": [ApprovalStep.LABELS.get(r.step, r.step) for r in chain]})

    notify(db, _step_people(chain[0], pr, db), module=MODULE, entity_id=pr.id,
           action=f"{action} For Your Approval",
           message=f"{pr.pr_number} from {pr.requester_name}: {len(pr.items)} item(s), "
                   f"needed by {pr.required_by.isoformat() if pr.required_by else '-'}.",
           link=link(pr), current_user=current_user)

    db.commit()
    db.refresh(pr)
    return pr


def decide(pr_id: int, decision: str, remarks: str | None, current_user: dict, db: Session,
           budget: dict | None = None) -> PurchaseRequisition:
    """Approve, reject or return the step the PR is waiting on.

    Approving, the manager may record the budget the purchase is charged
    to - the code, what is available, and whether it was budgeted. That is
    the manager's to know rather than the requester's; it is optional.
    """

    decision = (decision or "").upper()
    if decision not in ("APPROVE", "REJECT", "RETURN"):
        raise HTTPException(status_code=400, detail="Approve, reject or return for correction.")

    remarks = text(remarks, 2000)
    if decision in ("REJECT", "RETURN") and not remarks:
        raise HTTPException(
            status_code=400,
            detail="Say why - a rejection or a return for correction needs remarks.",
        )

    pr = get(pr_id, db, lock=True)
    step = pending_step(pr, db)

    if step is None:
        raise HTTPException(status_code=409, detail="This requisition is not waiting on an approval.")

    if str(current_user.get("user_id") or "") == str(pr.requested_by):
        raise HTTPException(status_code=403, detail="You cannot approve your own requisition.")
    if not may_decide(step, pr, current_user):
        raise HTTPException(status_code=403, detail="This requisition is not waiting on you.")

    actor_id, actor_name = actor(current_user, db)
    step.decided_by = actor_id
    step.decided_by_name = actor_name
    step.decided_at = datetime.utcnow()
    step.remarks = remarks
    label = ApprovalStep.LABELS.get(step.step, step.step)

    if decision == "APPROVE":
        step.decision = "APPROVED"
        if budget:
            if "budget_code" in budget:
                pr.budget_code = text(budget.get("budget_code"), 100)
            if "budget_available" in budget:
                pr.budget_available = _number(budget.get("budget_available"), "Budget available")
            if "budgeted" in budget:
                pr.budgeted = budget.get("budgeted") if budget.get("budgeted") in (True, False) else None
        following = (
            db.query(PurchaseRequisitionApproval)
            .filter(
                PurchaseRequisitionApproval.requisition_id == pr.id,
                PurchaseRequisitionApproval.round == pr.approval_round,
                PurchaseRequisitionApproval.level > step.level,
            )
            .order_by(PurchaseRequisitionApproval.level)
            .first()
        )

        if following is not None:
            following.decision = "PENDING"
            pr.current_level = following.level
            log(db, current_user, entity_type="requisition", entity_id=pr.id, requisition_id=pr.id,
                action=f"Approved By {label}", remarks=remarks,
                new={"next": ApprovalStep.LABELS.get(following.step, following.step)})
            notify(db, _step_people(following, pr, db), module=MODULE, entity_id=pr.id,
                   action="PR Waiting For Your Approval",
                   message=f"{pr.pr_number} from {pr.requester_name} was approved by {actor_name} "
                           f"({label}) and now needs your approval.",
                   link=link(pr), current_user=current_user)
        else:
            pr.status = PRStatus.APPROVED
            pr.current_level = None
            pr.approved_at = datetime.utcnow()
            pr.procurement_status = ProcurementStatus.AWAITING
            pr.procurement_since = datetime.utcnow()
            log(db, current_user, entity_type="requisition", entity_id=pr.id, requisition_id=pr.id,
                action="PR Approved", remarks=remarks,
                previous={"status": PRStatus.PENDING_APPROVAL},
                new={"status": pr.status, "procurement_status": pr.procurement_status})
            notify(db, [str(pr.requested_by)], module=MODULE, entity_id=pr.id, action="PR Approved",
                   message=f"{pr.pr_number} is approved and has gone to the procurement team.",
                   link=link(pr), current_user=current_user)
            notify(db, holders_of("procurement.read", db), module=MODULE, entity_id=pr.id,
                   action="Approved PR Received",
                   message=f"{pr.pr_number} from {pr.requester_name} ({pr.department_name or '-'}) "
                           f"is approved and waiting for procurement.",
                   link=f"/procurement/desk/{pr.id}", current_user=current_user)
    else:
        step.decision = "REJECTED" if decision == "REJECT" else "RETURNED"
        pr.status = PRStatus.REJECTED if decision == "REJECT" else PRStatus.RETURNED
        pr.current_level = None

        # Steps after this one in the round never came up.
        for later in db.query(PurchaseRequisitionApproval).filter(
            PurchaseRequisitionApproval.requisition_id == pr.id,
            PurchaseRequisitionApproval.round == pr.approval_round,
            PurchaseRequisitionApproval.decision == "WAITING",
        ):
            later.decision = "SKIPPED"

        action = "PR Rejected" if decision == "REJECT" else "PR Returned For Correction"
        log(db, current_user, entity_type="requisition", entity_id=pr.id, requisition_id=pr.id,
            action=action, remarks=remarks,
            previous={"status": PRStatus.PENDING_APPROVAL}, new={"status": pr.status})
        notify(db, [str(pr.requested_by)], module=MODULE, entity_id=pr.id, action=action,
               message=f"{pr.pr_number}: {remarks}", link=link(pr), current_user=current_user)

    db.commit()
    db.refresh(pr)
    return pr


def cancel(pr_id: int, remarks: str | None, current_user: dict, db: Session) -> PurchaseRequisition:
    pr = get(pr_id, db, lock=True)

    if str(current_user.get("user_id") or "") != str(pr.requested_by):
        raise HTTPException(status_code=403, detail="Only the person who raised this requisition can cancel it.")
    if pr.status not in (*PRStatus.EDITABLE, PRStatus.PENDING_APPROVAL):
        raise HTTPException(status_code=409, detail="An approved, rejected or cancelled requisition cannot be cancelled.")

    previous = pr.status
    for step in db.query(PurchaseRequisitionApproval).filter(
        PurchaseRequisitionApproval.requisition_id == pr.id,
        PurchaseRequisitionApproval.decision.in_(("PENDING", "WAITING")),
    ):
        step.decision = "SKIPPED"

    pr.status = PRStatus.CANCELLED
    pr.current_level = None
    log(db, current_user, entity_type="requisition", entity_id=pr.id, requisition_id=pr.id,
        action="PR Cancelled", remarks=text(remarks, 2000),
        previous={"status": previous}, new={"status": pr.status})
    db.commit()
    db.refresh(pr)
    return pr


# ----------------------------------------------------------------------
# Lists
# ----------------------------------------------------------------------
def list_mine(current_user: dict, db: Session, status: str | None = None, search: str | None = None,
              page: int = 1, size: int = 20) -> dict:
    query = db.query(PurchaseRequisition).filter(
        PurchaseRequisition.requested_by == as_uuid(current_user.get("user_id"))
    )
    counts = {}
    for row_status, in query.with_entities(PurchaseRequisition.status).all():
        counts[row_status] = counts.get(row_status, 0) + 1

    if status:
        query = query.filter(PurchaseRequisition.status == status.upper())
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(PurchaseRequisition.pr_number.ilike(like) | PurchaseRequisition.purpose.ilike(like))

    total = query.count()
    rows = (
        query.order_by(PurchaseRequisition.id.desc())
        .offset(max(0, (page - 1) * size)).limit(min(max(size, 1), 100)).all()
    )
    # The KPIs for the page: what this person raised and where it stands.
    open_statuses = set(ProcurementStatus.OPEN)
    ordered = {ProcurementStatus.PO_GENERATED, ProcurementStatus.COMPLETED}
    stages = dict(
        db.query(PurchaseRequisition.procurement_status, func.count(PurchaseRequisition.id))
        .filter(PurchaseRequisition.requested_by == as_uuid(current_user.get("user_id")),
                PurchaseRequisition.status == PRStatus.APPROVED)
        .group_by(PurchaseRequisition.procurement_status).all()
    )
    summary = {
        "raised": sum(n for s, n in counts.items() if s != PRStatus.CANCELLED),
        "awaiting_approval": counts.get(PRStatus.PENDING_APPROVAL, 0),
        "returned": counts.get(PRStatus.RETURNED, 0),
        "with_procurement": sum(n for s, n in stages.items() if s in open_statuses),
        "ordered": sum(n for s, n in stages.items() if s in ordered),
    }
    return {
        "data": serialize_many(rows, db, current_user),
        "total": total,
        "counts": counts,
        "summary": summary,
    }


def list_for_approval(current_user: dict, db: Session) -> dict:
    """PRs waiting on this person's decision now, and those they decided.

    Only the pending steps that name this person are read (all of them
    for a super admin, who stands in for anyone), rather than every
    pending PR in the business.
    """

    from sqlalchemy import String, cast

    me = str(current_user.get("user_id") or "")

    steps = db.query(PurchaseRequisitionApproval.requisition_id).join(
        PurchaseRequisition,
        (PurchaseRequisition.id == PurchaseRequisitionApproval.requisition_id)
        & (PurchaseRequisition.approval_round == PurchaseRequisitionApproval.round),
    ).filter(
        PurchaseRequisition.status == PRStatus.PENDING_APPROVAL,
        PurchaseRequisitionApproval.decision == "PENDING",
        PurchaseRequisition.requested_by != as_uuid(me),
    )
    if not is_super_admin(current_user):
        steps = steps.filter(cast(PurchaseRequisitionApproval.approver_ids, String).like(f'%"{me}"%'))

    pending_ids = [row[0] for row in steps.distinct().all()]
    waiting = serialize_many(
        db.query(PurchaseRequisition)
        .filter(PurchaseRequisition.id.in_(pending_ids or [-1]))
        .order_by(PurchaseRequisition.submitted_at).all(),
        db, current_user,
    )

    # This person's latest decision on each PR they decided, so the history
    # can be split into what they approved and what they sent back or rejected.
    mine = {}
    for step in (
        db.query(PurchaseRequisitionApproval)
        .filter(PurchaseRequisitionApproval.decided_by == as_uuid(me))
        .order_by(PurchaseRequisitionApproval.decided_at)
        .all()
    ):
        mine[step.requisition_id] = step

    decided = []
    if mine:
        decided_prs = (
            db.query(PurchaseRequisition)
            .filter(PurchaseRequisition.id.in_(list(mine)))
            .order_by(PurchaseRequisition.id.desc()).limit(100).all()
        )
        for pr, row in zip(decided_prs, serialize_many(decided_prs, db, current_user)):
            step = mine[pr.id]
            row["my_decision"] = step.decision
            row["my_step_label"] = ApprovalStep.LABELS.get(step.step, step.step)
            row["my_decided_at"] = iso(step.decided_at)
            row["my_remarks"] = step.remarks
            decided.append(row)

    return {"pending": waiting, "decided": decided}
