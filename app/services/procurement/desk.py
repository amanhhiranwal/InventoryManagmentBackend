"""The procurement team's desk: approved requisitions, and working them."""

from datetime import date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.procurement import PRStatus, ProcurementStatus
from app.core.timezone import iso
from app.models.procurement import (
    PurchaseRequisition,
    PurchaseRequisitionApproval,
    PurchaseRequisitionItem,
    QuotationCollection,
    Vendor,
)
from app.services.procurement import requisitions
from app.services.procurement.common import (
    actor,
    as_uuid,
    full_name,
    has_permission,
    holders_of,
    log,
    money,
    next_reference,
    notify,
    text,
    today,
    user_row,
)

KPI_GROUPS = {
    "total": None,
    "new": (ProcurementStatus.AWAITING,),
    "under_procurement": (ProcurementStatus.ASSIGNED, ProcurementStatus.IN_PROGRESS),
    "collecting": (ProcurementStatus.QUOTATION_COLLECTION,),
    "awaiting_comparison": (ProcurementStatus.QUOTATIONS_RECEIVED, ProcurementStatus.COMPARISON),
    "po_approval": (ProcurementStatus.PO_APPROVAL,),
    "po_generated": (ProcurementStatus.PO_GENERATED, ProcurementStatus.COMPLETED),
}


def queue(db: Session, filters: dict, page: int = 1, size: int = 20) -> dict:
    from sqlalchemy import func

    base = db.query(PurchaseRequisition).filter(PurchaseRequisition.status == PRStatus.APPROVED)

    # Counted by the database rather than by loading every approved PR.
    by_status = dict(
        db.query(PurchaseRequisition.procurement_status, func.count(PurchaseRequisition.id))
        .filter(PurchaseRequisition.status == PRStatus.APPROVED)
        .group_by(PurchaseRequisition.procurement_status).all()
    )
    overdue_filter = (
        (PurchaseRequisition.required_by < today())
        & PurchaseRequisition.procurement_status.in_(ProcurementStatus.OPEN)
    )
    kpis = {
        key: sum(by_status.values()) if group is None else sum(by_status.get(s, 0) for s in group)
        for key, group in KPI_GROUPS.items()
    }
    kpis["overdue"] = base.filter(overdue_filter).count()

    query = base
    if filters.get("search"):
        like = f"%{filters['search'].strip()}%"
        query = query.filter(or_(
            PurchaseRequisition.pr_number.ilike(like),
            PurchaseRequisition.requester_name.ilike(like),
            PurchaseRequisition.sales_order_number.ilike(like),
            PurchaseRequisition.customer_name.ilike(like),
        ))
    if filters.get("department_id"):
        query = query.filter(PurchaseRequisition.department_id == int(filters["department_id"]))
    if filters.get("purchase_type"):
        query = query.filter(PurchaseRequisition.purchase_type == filters["purchase_type"].upper())
    if filters.get("priority"):
        query = query.filter(PurchaseRequisition.priority == filters["priority"].upper())
    if filters.get("procurement_status"):
        wanted = filters["procurement_status"].strip()
        group = KPI_GROUPS.get(wanted.lower())
        wanted = wanted.upper()
        if wanted == "OVERDUE":
            query = query.filter(overdue_filter)
        elif group:
            query = query.filter(PurchaseRequisition.procurement_status.in_(group))
        else:
            query = query.filter(PurchaseRequisition.procurement_status == wanted)
    if filters.get("assigned_to"):
        if filters["assigned_to"] == "unassigned":
            query = query.filter(PurchaseRequisition.assigned_to.is_(None))
        else:
            query = query.filter(PurchaseRequisition.assigned_to == as_uuid(filters["assigned_to"]))
    if filters.get("sales_order_number"):
        query = query.filter(PurchaseRequisition.sales_order_number.ilike(f"%{filters['sales_order_number'].strip()}%"))
    for key, compare in (("required_from", ">="), ("required_to", "<=")):
        if filters.get(key):
            try:
                day = date.fromisoformat(filters[key][:10])
            except ValueError:
                raise HTTPException(status_code=400, detail=f"{key.replace('_', ' ')} is not a date.")
            query = query.filter(
                PurchaseRequisition.required_by >= day if compare == ">=" else PurchaseRequisition.required_by <= day
            )

    total = query.count()
    size = min(max(size, 1), 100)
    rows = (
        query.order_by(PurchaseRequisition.approved_at.desc().nullslast(), PurchaseRequisition.id.desc())
        .offset(max(0, (page - 1) * size)).limit(size).all()
    )

    data = []
    for pr, row in zip(rows, requisitions.serialize_many(rows, db)):
        since = pr.procurement_since or pr.approved_at
        row["pending_since_days"] = max(0, (datetime.utcnow() - since).days) if since else 0
        row["overdue"] = bool(
            pr.required_by and pr.required_by < today() and pr.procurement_status in ProcurementStatus.OPEN
        )
        data.append(row)

    return {"data": data, "total": total, "kpis": kpis}


def executives(db: Session) -> list[dict]:
    """Who a requisition can be assigned to: those who work procurement."""

    ids = holders_of("procurement.manage", db)
    people = []
    for user_id in ids:
        user = user_row(user_id, db)
        if user is not None:
            people.append({"id": str(user.id), "name": full_name(user), "email": user.email})
    return sorted(people, key=lambda p: p["name"].lower())


def _approved_pr(pr_id: int, db: Session) -> PurchaseRequisition:
    pr = requisitions.get(pr_id, db, lock=True)
    if pr.status != PRStatus.APPROVED:
        raise HTTPException(status_code=409, detail="Only an approved requisition is with procurement.")
    return pr


def assign(pr_id: int, user_id: str, remarks: str | None, current_user: dict, db: Session) -> PurchaseRequisition:
    pr = _approved_pr(pr_id, db)
    assignee = user_row(user_id, db)

    if assignee is None or not assignee.is_active:
        raise HTTPException(status_code=400, detail="Assign to an active user.")
    if not has_permission({"user_id": str(assignee.id), "is_super_admin": assignee.is_super_admin}, "procurement.manage", db):
        raise HTTPException(status_code=400, detail=f"{full_name(assignee)} does not work procurement.")

    previous = {"assigned_to": pr.assigned_name, "procurement_status": pr.procurement_status}
    pr.assigned_to = assignee.id
    pr.assigned_name = full_name(assignee)
    if pr.procurement_status == ProcurementStatus.AWAITING:
        pr.procurement_status = ProcurementStatus.ASSIGNED

    action = "Procurement Assignment Updated" if previous["assigned_to"] else "PR Assigned To Procurement"
    log(db, current_user, entity_type="requisition", entity_id=pr.id, requisition_id=pr.id,
        action=action, previous=previous,
        new={"assigned_to": pr.assigned_name, "procurement_status": pr.procurement_status},
        remarks=text(remarks))
    notify(db, [str(assignee.id)], module="purchase_requisition", entity_id=pr.id, action=action,
           message=f"{pr.pr_number} from {pr.requester_name} is assigned to you.",
           link=f"/procurement/desk/{pr.id}", current_user=current_user)
    db.commit()
    db.refresh(pr)
    return pr


def add_note(pr_id: int, note: str, current_user: dict, db: Session) -> PurchaseRequisition:
    pr = _approved_pr(pr_id, db)
    note = text(note, 2000)
    if not note:
        raise HTTPException(status_code=400, detail="Write the note first.")

    _, name = actor(current_user, db)
    pr.internal_notes = [*(pr.internal_notes or []), {"text": note, "by": name, "at": iso(datetime.utcnow())}]
    log(db, current_user, entity_type="requisition", entity_id=pr.id, requisition_id=pr.id,
        action="Internal Note Added", new={"note": note})
    db.commit()
    db.refresh(pr)
    return pr


def set_remarks(pr_id: int, remarks: str | None, current_user: dict, db: Session) -> PurchaseRequisition:
    pr = _approved_pr(pr_id, db)
    previous = pr.procurement_remarks
    pr.procurement_remarks = text(remarks)
    log(db, current_user, entity_type="requisition", entity_id=pr.id, requisition_id=pr.id,
        action="Procurement Remarks Updated", previous={"remarks": previous}, new={"remarks": pr.procurement_remarks})
    db.commit()
    db.refresh(pr)
    return pr


def set_status(pr_id: int, status: str, remarks: str | None, current_user: dict, db: Session) -> PurchaseRequisition:
    pr = _approved_pr(pr_id, db)
    status = (status or "").upper()

    if status not in ProcurementStatus.MANUAL:
        raise HTTPException(status_code=400, detail="That status follows from an action, not set by hand.")
    if status in (ProcurementStatus.ON_HOLD, ProcurementStatus.CANCELLED) and not text(remarks):
        raise HTTPException(status_code=400, detail="Say why - holding or cancelling needs remarks.")
    if pr.procurement_status in (ProcurementStatus.CANCELLED, ProcurementStatus.COMPLETED):
        raise HTTPException(status_code=409, detail=f"{pr.pr_number} is already {pr.procurement_status.lower()}.")

    previous = pr.procurement_status
    pr.procurement_status = status
    log(db, current_user, entity_type="requisition", entity_id=pr.id, requisition_id=pr.id,
        action="Procurement Status Changed", previous={"procurement_status": previous},
        new={"procurement_status": status}, remarks=text(remarks))
    db.commit()
    db.refresh(pr)
    return pr


def request_quotations(pr_id: int, item_vendors: dict | None, vendor_ids: list | None, notes: str | None,
                       current_user: dict, db: Session) -> dict:
    """Record which vendors are being asked to quote for which lines.

    Several vendors per line; ``item_vendors`` maps a line's id to its
    vendors. ``vendor_ids`` alone asks the same vendors for every line.
    Asking is done offline by the team - nothing is sent from here.
    """

    from app.core.procurement import VendorStatus

    pr = _approved_pr(pr_id, db)
    if pr.procurement_status in (ProcurementStatus.CANCELLED, ProcurementStatus.COMPLETED):
        raise HTTPException(status_code=409, detail=f"{pr.pr_number} is {pr.procurement_status.lower()}.")

    lines = {item.id: item for item in pr.items}
    if item_vendors:
        try:
            plan = {int(k): [int(v) for v in (vs or [])] for k, vs in item_vendors.items()}
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Vendors per line are ids.")
    else:
        try:
            chosen = [int(v) for v in (vendor_ids or [])]
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Vendors are ids.")
        plan = {item_id: chosen for item_id in lines}

    unknown = [k for k in plan if k not in lines]
    if unknown:
        raise HTTPException(status_code=400, detail="Some lines are not on this requisition.")
    plan = {k: sorted(set(v)) for k, v in plan.items() if v}
    if not plan:
        raise HTTPException(status_code=400, detail="Choose at least one vendor for at least one line.")

    every = sorted({v for vs in plan.values() for v in vs})
    found = {v.id: v for v in db.query(Vendor).filter(Vendor.id.in_(every)).all()}
    missing = [v for v in every if v not in found]
    if missing:
        raise HTTPException(status_code=400, detail="Some vendors do not exist.")
    unusable = [found[v].name for v in every if found[v].status not in VendorStatus.USABLE]
    if unusable:
        raise HTTPException(status_code=409, detail=f"Not approved or active: {', '.join(unusable)}.")

    # Only what is newly asked is recorded: re-saving the same vendors is
    # not a new request.
    plan = {k: [v for v in vs if v not in (lines[k].rfq_vendor_ids or [])] for k, vs in plan.items()}
    plan = {k: vs for k, vs in plan.items() if vs}
    if not plan:
        raise HTTPException(status_code=400, detail="Those vendors were already asked. Tick a new vendor to request more.")
    every = sorted({v for vs in plan.values() for v in vs})

    for item_id, vendors in plan.items():
        item = lines[item_id]
        item.rfq_vendor_ids = sorted(set(item.rfq_vendor_ids or []) | set(vendors))

    who, name = actor(current_user, db)
    collection = QuotationCollection(
        reference=next_reference(db, QuotationCollection, "reference", "RFQ"),
        requisition_id=pr.id,
        vendor_ids=every,
        item_vendor_ids={str(k): v for k, v in plan.items()},
        notes=text(notes),
        started_by=who,
        started_by_name=name,
    )
    db.add(collection)

    previous = pr.procurement_status
    if pr.procurement_status in (ProcurementStatus.AWAITING, ProcurementStatus.ASSIGNED, ProcurementStatus.IN_PROGRESS):
        pr.procurement_status = ProcurementStatus.QUOTATION_COLLECTION
    db.flush()

    log(db, current_user, entity_type="requisition", entity_id=pr.id, requisition_id=pr.id,
        action="Quotations Requested", previous={"procurement_status": previous},
        new={"reference": collection.reference, "vendors": [found[v].name for v in every],
             "lines": len(plan), "procurement_status": pr.procurement_status},
        remarks=collection.notes)
    db.commit()
    return {"reference": collection.reference, "requisition_id": pr.id}


def collections(pr: PurchaseRequisition, db: Session) -> list[dict]:
    rows = db.query(QuotationCollection).filter(QuotationCollection.requisition_id == pr.id).order_by(QuotationCollection.id).all()
    wanted = {v for r in rows for v in (r.vendor_ids or [])}
    names = dict(db.query(Vendor.id, Vendor.name).filter(Vendor.id.in_(wanted or [-1])).all())
    lines = {item.id: item.product_name for item in pr.items}
    return [
        {
            "reference": r.reference,
            "vendors": [names.get(v, f"#{v}") for v in r.vendor_ids or []],
            "lines": [
                {"item": lines.get(int(k), f"Line {k}"), "vendors": [names.get(v, f"#{v}") for v in vs]}
                for k, vs in (r.item_vendor_ids or {}).items()
            ],
            "notes": r.notes,
            "started_by_name": r.started_by_name,
            "created_at": iso(r.created_at),
        }
        for r in rows
    ]


def change_quantity(pr_id: int, item_id: int, quantity, reason: str | None, current_user: dict, db: Session) -> PurchaseRequisition:
    """A procurement figure beside the employee's, never over it."""

    pr = _approved_pr(pr_id, db)
    item = db.query(PurchaseRequisitionItem).filter(
        PurchaseRequisitionItem.id == item_id, PurchaseRequisitionItem.requisition_id == pr.id
    ).first()
    if item is None:
        raise HTTPException(status_code=404, detail="That line is not on this requisition.")

    reason = text(reason, 500)
    if not reason:
        raise HTTPException(status_code=400, detail="Say why the quantity is different from what was asked for.")

    if quantity in (None, ""):
        new_quantity = None
    else:
        try:
            new_quantity = float(quantity)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="The quantity must be a number.")
        if new_quantity <= 0:
            raise HTTPException(status_code=400, detail="The quantity must be above nothing.")

    from app.services.procurement.purchase_orders import ordered_items

    if item.id in ordered_items(pr.id, db):
        raise HTTPException(status_code=409, detail="This line is already on a purchase order.")

    previous = {"requested": item.quantity, "approved_quantity": item.approved_quantity}
    item.approved_quantity = new_quantity
    item.quantity_change_reason = reason if new_quantity is not None else None

    log(db, current_user, entity_type="requisition", entity_id=pr.id, requisition_id=pr.id,
        action="Procurement Quantity Changed",
        previous=previous,
        new={"line": item.line_no, "product": item.product_name, "approved_quantity": new_quantity},
        remarks=reason)
    db.commit()
    db.refresh(pr)
    return pr


# ----------------------------------------------------------------------
# Analytics: the procurement KPIs, worked out from the records
# ----------------------------------------------------------------------
ANALYTICS_PERIODS = (30, 90, 365)


def _average(values: list[float]) -> float | None:
    """The mean to one place, or None when there is nothing to average -
    "no data" and "zero days" are different answers."""

    return round(sum(values) / len(values), 1) if values else None


def _days_between(start: datetime | None, end: datetime | None) -> float | None:
    if not start or not end or end < start:
        return None
    return (end - start).total_seconds() / 86400


def analytics(db: Session, days: int = 90) -> dict:
    """The desk's KPIs over the last ``days`` days.

    Each figure states its basis (how many records it was worked out from),
    so a card can show "no data" rather than a misleading zero:

    * open_pipeline      approved PRs still being worked (count)
    * overdue            open PRs past their required-by date, and their share
    * approval_cycle     mean days from PR submission to the manager's approval
    * sourcing_cycle     mean days from PR approval to its first purchase order
    * po_spend           value of purchase orders raised, cancelled ones excluded
    * po_approval_cycle  mean days from PO submission to its final approval
    * competitive_rate   share of POs raised where three or more vendors quoted
    * quotes_per_pr      mean latest quotations per requisition that has any
    """

    from sqlalchemy import func

    from app.core.procurement import POStatus
    from app.models.procurement import PurchaseOrder, VendorQuotation

    if days not in ANALYTICS_PERIODS:
        raise HTTPException(status_code=400, detail=f"Choose a period of {', '.join(map(str, ANALYTICS_PERIODS))} days.")

    now = datetime.utcnow()
    since = now - timedelta(days=days)

    open_query = db.query(PurchaseRequisition).filter(
        PurchaseRequisition.status == PRStatus.APPROVED,
        PurchaseRequisition.procurement_status.in_(ProcurementStatus.OPEN),
    )
    open_count = open_query.count()
    overdue_count = open_query.filter(PurchaseRequisition.required_by < today()).count()

    approval_days = [
        d for d in (
            _days_between(submitted, approved)
            for submitted, approved in db.query(PurchaseRequisition.submitted_at, PurchaseRequisition.approved_at)
            .filter(
                PurchaseRequisition.approved_at >= since,
                # Only those a manager actually approved: one raised directly
                # by procurement has no approval and would count as 0 days.
                PurchaseRequisition.id.in_(db.query(PurchaseRequisitionApproval.requisition_id)),
            ).all()
        ) if d is not None
    ]

    # Each requisition's first live PO, for those first ordered in the period.
    first_po = (
        db.query(PurchaseOrder.requisition_id, func.min(PurchaseOrder.po_date))
        .filter(PurchaseOrder.status != POStatus.CANCELLED)
        .group_by(PurchaseOrder.requisition_id)
        .having(func.min(PurchaseOrder.po_date) >= since)
        .all()
    )
    approved_at = dict(
        db.query(PurchaseRequisition.id, PurchaseRequisition.approved_at)
        .filter(PurchaseRequisition.id.in_([pr_id for pr_id, _ in first_po] or [-1])).all()
    )
    sourcing_days = [
        d for d in (_days_between(approved_at.get(pr_id), when) for pr_id, when in first_po) if d is not None
    ]

    period_pos = (
        db.query(PurchaseOrder.requisition_id, PurchaseOrder.grand_total,
                 PurchaseOrder.submitted_at, PurchaseOrder.approved_at)
        .filter(PurchaseOrder.po_date >= since, PurchaseOrder.status != POStatus.CANCELLED)
        .all()
    )
    po_approval_days = [
        d for d in (_days_between(sub, app) for _, _, sub, app in period_pos) if d is not None
    ]

    vendors_quoting = dict(
        db.query(VendorQuotation.requisition_id, func.count(func.distinct(VendorQuotation.vendor_id)))
        .filter(VendorQuotation.is_latest.is_(True),
                VendorQuotation.requisition_id.in_({r for r, *_ in period_pos} or {-1}))
        .group_by(VendorQuotation.requisition_id).all()
    )
    competitive = sum(1 for pr_id, *_ in period_pos if vendors_quoting.get(pr_id, 0) >= 3)

    quotes = [
        float(n) for _, n in
        db.query(VendorQuotation.requisition_id, func.count(VendorQuotation.id))
        .filter(VendorQuotation.is_latest.is_(True), VendorQuotation.created_at >= since)
        .group_by(VendorQuotation.requisition_id).all()
    ]

    open_value = db.query(func.coalesce(func.sum(PurchaseRequisition.estimated_total), 0)).filter(
        PurchaseRequisition.status == PRStatus.APPROVED,
        PurchaseRequisition.procurement_status.in_(ProcurementStatus.OPEN),
    ).scalar()

    return {
        "days": days,
        "since": iso(since),
        "open_pipeline": {"count": open_count, "value": money(open_value)},
        "overdue": {
            "count": overdue_count,
            "percent": round(overdue_count * 100 / open_count, 1) if open_count else None,
        },
        "approval_cycle": {"days": _average(approval_days), "basis": len(approval_days)},
        "sourcing_cycle": {"days": _average(sourcing_days), "basis": len(sourcing_days)},
        "po_spend": {"value": money(sum(t or 0 for _, t, _, _ in period_pos)), "count": len(period_pos)},
        "po_approval_cycle": {"days": _average(po_approval_days), "basis": len(po_approval_days)},
        "competitive_rate": {
            "percent": round(competitive * 100 / len(period_pos), 1) if period_pos else None,
            "basis": len(period_pos),
        },
        "quotes_per_pr": {"average": _average(quotes), "basis": len(quotes)},
    }
