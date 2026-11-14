"""Procurement: requisitions, approvals, the procurement desk, vendors,
quotations, vendor selection and purchase orders.

Raising and approving a requisition needs only a sign-in: every employee
may raise one, and the service decides whose turn it is to approve. The
procurement team's work needs its own permissions (app/core/procurement),
checked here on every call - never only by hiding a button.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.procurement import (
    COMPLIANCE,
    PRIORITIES,
    PURCHASE_TYPES,
    CREDIT_DAY_OPTIONS,
    PAYMENT_MODES,
    PAYMENT_TERMS,
    VENDOR_TYPES,
    ApprovalStep,
    ProcurementStatus,
    QuotationStatus,
    VendorStatus,
)
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.middleware.permission_middleware import require_permission
from app.models.procurement import PurchaseRequisition, Vendor
from app.services.procurement import (
    desk,
    masters,
    payment_calendar,
    purchase_orders,
    quotations,
    requisitions,
    vendors,
)
from app.services.procurement.common import (
    can_link_sales_order,
    full_name,
    has_permission,
    may_raise_requisition,
    user_row,
)

router = APIRouter(tags=["Procurement"])

Body = dict[str, Any]


def _ok(data, message: str | None = None, **extra) -> dict:
    body = {"success": True, "data": data, **extra}
    if message:
        body["message"] = message
    return body


# ======================================================================
# Masters
# ======================================================================
@router.get("/departments")
def list_departments(active: bool = False, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return _ok(masters.list_departments(db, include_inactive=not active))


@router.post("/departments")
def create_department(payload: Body, db: Session = Depends(get_db),
                      current_user=Depends(require_permission("procurement_masters.manage"))):
    return _ok(masters.save_department(payload, db), "Department added.")


@router.put("/departments/{department_id}")
def update_department(department_id: int, payload: Body, db: Session = Depends(get_db),
                      current_user=Depends(require_permission("procurement_masters.manage"))):
    return _ok(masters.save_department(payload, db, department_id), "Department updated.")


@router.delete("/departments/{department_id}")
def delete_department(department_id: int, db: Session = Depends(get_db),
                      current_user=Depends(require_permission("procurement_masters.manage"))):
    masters.delete_department(department_id, db)
    return _ok(None, "Department removed.")


@router.get("/procurement/approval-matrix")
def get_matrix(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return _ok(masters.describe_matrix(db))


@router.put("/procurement/approval-matrix")
def save_matrix(payload: Body, db: Session = Depends(get_db),
                current_user=Depends(require_permission("procurement_masters.manage"))):
    # Whole bands from the matrix page; two thresholds alone still work for
    # older callers and keep the form's default approvers.
    if "bands" in payload:
        return _ok(masters.save_matrix(payload.get("bands"), db), "Approval matrix saved.")
    return _ok(masters.save_matrix_limits(payload.get("limits") or [], db), "Approval thresholds saved.")


# ======================================================================
# Purchase requisitions - any signed-in employee
# ======================================================================
@router.get("/purchase-requisitions/meta")
def requisition_meta(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """What the form needs: the lists, and who the requester is."""

    me = user_row(current_user.get("user_id"), db)
    manager = user_row(me.reports_to_id, db) if me and me.reports_to_id else None
    department = masters.department_of(me, db)

    return _ok({
        # The sales-order type is offered only to those who may use it.
        "purchase_types": [
            {"value": k, "label": v} for k, v in PURCHASE_TYPES.items()
            if k != "SALES_ORDER" or can_link_sales_order(current_user, db)
        ],
        "priorities": list(PRIORITIES),
        "departments": masters.list_departments(db, include_inactive=False),
        # The cost centre is the company the purchase is for.
        "companies": masters.company_options(current_user, db),
        "matrix": masters.describe_matrix(db),
        "me": {
            "id": str(me.id) if me else None,
            "name": full_name(me),
            "employee_id": me.employee_id if me else None,
            "designation": me.roles[0].role_name if me and me.roles else None,
            "department_id": department.id if department else None,
            "department_name": department.name if department else None,
            "reporting_manager_id": str(manager.id) if manager else None,
            "reporting_manager_name": full_name(manager) or None,
            # The My Requisitions tick: without it the page still shows
            # what waits on them, but they cannot raise one.
            "can_raise": may_raise_requisition(current_user, db),
        },
    })


@router.get("/purchase-requisitions/sales-orders")
def sales_order_options(search: str | None = None, db: Session = Depends(get_db),
                        current_user=Depends(get_current_user)):
    """Orders a requisition can be raised against: the number and customer only.

    Only for the sales chart, and only the orders the Sales Order list would
    show this person.
    """

    if not can_link_sales_order(current_user, db):
        raise HTTPException(status_code=403, detail="Only the sales team raises requisitions against sales orders.")

    from app.models.sales_order import SalesOrder
    from app.services.sales_order_service import SalesOrderService

    # Only the orders this person could open on the Sales Order list.
    query = SalesOrderService.visible_query(current_user, db, search=search)
    rows = query.order_by(SalesOrder.id.desc()).limit(20).all()
    return _ok([
        {"id": r.id, "order_number": r.order_number, "customer_name": r.company_name or r.customer_name}
        for r in rows
    ])


@router.get("/purchase-requisitions/vendor-options")
def vendor_options(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Approved vendors by name, for a requester's optional preferred vendor."""

    rows = db.query(Vendor.id, Vendor.name, Vendor.vendor_code).filter(
        Vendor.status.in_(VendorStatus.USABLE)).order_by(Vendor.name).all()
    return _ok([{"id": r.id, "name": r.name, "vendor_code": r.vendor_code} for r in rows])


@router.get("/purchase-requisitions/mine")
def my_requisitions(status: str | None = None, search: str | None = None, page: int = 1, size: int = 20,
                    db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    result = requisitions.list_mine(current_user, db, status=status, search=search, page=page, size=size)
    return {"success": True, **result}


@router.get("/purchase-requisitions/approvals")
def requisitions_for_approval(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """What waits on this person: requisitions (a manager confirming a need)
    and purchase orders (the amount-based approval), plus PRs they decided."""
    data = requisitions.list_for_approval(current_user, db)
    data["purchase_orders"] = purchase_orders.list_waiting(current_user, db)
    return _ok(data)


@router.post("/purchase-requisitions")
def create_requisition(payload: Body, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    pr = requisitions.create(payload, current_user, db)
    message = "Requisition submitted for approval." if payload.get("submit") else "Requisition saved as draft."
    return _ok(requisitions.serialize(pr, db, current_user), message)


@router.get("/purchase-requisitions/{pr_id}")
def get_requisition(pr_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    pr = requisitions.get(pr_id, db)
    requisitions.assert_can_view(pr, current_user, db)
    return _ok(requisitions.serialize(pr, db, current_user))


@router.put("/purchase-requisitions/{pr_id}")
def update_requisition(pr_id: int, payload: Body, db: Session = Depends(get_db),
                       current_user=Depends(get_current_user)):
    pr = requisitions.update(pr_id, payload, current_user, db)
    message = "Requisition submitted for approval." if payload.get("submit") else "Requisition saved."
    return _ok(requisitions.serialize(pr, db, current_user), message)


@router.post("/purchase-requisitions/{pr_id}/submit")
def submit_requisition(pr_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    pr = requisitions.submit(pr_id, current_user, db)
    return _ok(requisitions.serialize(pr, db, current_user), "Requisition submitted for approval.")


@router.post("/purchase-requisitions/{pr_id}/decision")
def decide_requisition(pr_id: int, payload: Body, db: Session = Depends(get_db),
                       current_user=Depends(get_current_user)):
    budget = {k: payload[k] for k in ("budget_code", "budget_available", "budgeted") if k in payload}
    pr = requisitions.decide(pr_id, payload.get("decision"), payload.get("remarks"), current_user, db, budget=budget)
    messages = {"APPROVE": "Requisition approved.", "REJECT": "Requisition rejected.",
                "RETURN": "Requisition returned for correction."}
    return _ok(requisitions.serialize(pr, db, current_user), messages.get(str(payload.get("decision")).upper()))


@router.post("/purchase-requisitions/{pr_id}/activity")
def log_requisition_activity(pr_id: int, payload: Body, db: Session = Depends(get_db),
                             current_user=Depends(get_current_user)):
    """+ Log Activity: anyone who may open the requisition adds a dated entry."""
    from app.services.procurement.common import log_by_hand

    pr = requisitions.get(pr_id, db)
    requisitions.assert_can_view(pr, current_user, db)
    log_by_hand(db, current_user, entity_type="requisition", entity_id=pr.id, requisition_id=pr.id,
                activity=payload.get("activity"), remarks=payload.get("remarks"))
    return _ok(requisitions.serialize(pr, db, current_user), "Activity logged.")


@router.post("/purchase-requisitions/{pr_id}/cancel")
def cancel_requisition(pr_id: int, payload: Body | None = None, db: Session = Depends(get_db),
                       current_user=Depends(get_current_user)):
    pr = requisitions.cancel(pr_id, (payload or {}).get("remarks"), current_user, db)
    return _ok(requisitions.serialize(pr, db, current_user), "Requisition cancelled.")


# ======================================================================
# The procurement desk
# ======================================================================
@router.get("/procurement/meta")
def procurement_meta(db: Session = Depends(get_db), current_user=Depends(require_permission("procurement.read"))):
    return _ok({
        "procurement_statuses": list(ProcurementStatus.ALL),
        "manual_statuses": list(ProcurementStatus.MANUAL),
        "purchase_types": [{"value": k, "label": v} for k, v in PURCHASE_TYPES.items()],
        "priorities": list(PRIORITIES),
        "quotation_statuses": list(QuotationStatus.ALL),
        "manual_quotation_statuses": list(QuotationStatus.MANUAL),
        "compliance": list(COMPLIANCE),
        "approval_steps": ApprovalStep.LABELS,
        "payment_terms": [{"value": k, "label": v} for k, v in PAYMENT_TERMS.items()],
        "credit_day_options": {k: list(v) for k, v in CREDIT_DAY_OPTIONS.items()},
        "payment_modes": [{"value": k, "label": v} for k, v in PAYMENT_MODES.items()],
        "permissions": {
            key: has_permission(current_user, key, db)
            for key in ("procurement.manage", "vendor.read", "vendor.manage",
                        "vendor.approve", "purchase_order.read", "purchase_order.manage",
                        "procurement_masters.manage")
        },
    })


@router.get("/procurement/requisitions")
def procurement_queue(
    search: str | None = None, department_id: int | None = None, purchase_type: str | None = None,
    priority: str | None = None, procurement_status: str | None = None, assigned_to: str | None = None,
    sales_order_number: str | None = None, required_from: str | None = None, required_to: str | None = None,
    page: int = 1, size: int = 20,
    db: Session = Depends(get_db), current_user=Depends(require_permission("procurement.read")),
):
    filters = {
        "search": search, "department_id": department_id, "purchase_type": purchase_type,
        "priority": priority, "procurement_status": procurement_status, "assigned_to": assigned_to,
        "sales_order_number": sales_order_number, "required_from": required_from, "required_to": required_to,
    }
    return {"success": True, **desk.queue(db, filters, page=page, size=size)}


@router.get("/procurement/analytics")
def procurement_analytics(days: int = 90, db: Session = Depends(get_db),
                          current_user=Depends(require_permission("procurement.read"))):
    """The desk's KPIs over the last 30, 90 or 365 days."""
    return _ok(desk.analytics(db, days))


@router.get("/procurement/executives")
def procurement_executives(db: Session = Depends(get_db), current_user=Depends(require_permission("procurement.read"))):
    return _ok(desk.executives(db))


def _workspace(pr: PurchaseRequisition, current_user: dict, db: Session) -> dict:
    data = requisitions.serialize(pr, db, current_user)
    data["quotations"] = quotations.for_requisition(pr, db)
    data["collections"] = desk.collections(pr, db)
    data["purchase_orders"] = purchase_orders.list_orders(db, requisition_id=pr.id, size=100)["data"]
    data["ordered_item_ids"] = sorted(purchase_orders.ordered_items(pr.id, db))
    return data


@router.get("/procurement/requisitions/{pr_id}")
def procurement_requisition(pr_id: int, db: Session = Depends(get_db),
                            current_user=Depends(require_permission("procurement.read"))):
    pr = requisitions.get(pr_id, db)
    if pr.status != "APPROVED":
        raise HTTPException(status_code=404, detail="That requisition is not with procurement.")
    return _ok(_workspace(pr, current_user, db))


@router.post("/procurement/requisitions/{pr_id}/assign")
def assign_requisition(pr_id: int, payload: Body, db: Session = Depends(get_db),
                       current_user=Depends(require_permission("procurement.manage"))):
    pr = desk.assign(pr_id, payload.get("user_id"), payload.get("remarks"), current_user, db)
    return _ok(_workspace(pr, current_user, db), f"Assigned to {pr.assigned_name}.")


@router.post("/procurement/requisitions/{pr_id}/notes")
def add_note(pr_id: int, payload: Body, db: Session = Depends(get_db),
             current_user=Depends(require_permission("procurement.manage"))):
    pr = desk.add_note(pr_id, payload.get("note"), current_user, db)
    return _ok(_workspace(pr, current_user, db), "Note added.")


@router.put("/procurement/requisitions/{pr_id}/remarks")
def set_remarks(pr_id: int, payload: Body, db: Session = Depends(get_db),
                current_user=Depends(require_permission("procurement.manage"))):
    pr = desk.set_remarks(pr_id, payload.get("remarks"), current_user, db)
    return _ok(_workspace(pr, current_user, db), "Procurement remarks saved.")


@router.post("/procurement/requisitions/{pr_id}/status")
def set_procurement_status(pr_id: int, payload: Body, db: Session = Depends(get_db),
                           current_user=Depends(require_permission("procurement.manage"))):
    pr = desk.set_status(pr_id, payload.get("status"), payload.get("remarks"), current_user, db)
    return _ok(_workspace(pr, current_user, db), "Procurement status updated.")


@router.get("/procurement/requesters")
def requester_options(search: str | None = None, db: Session = Depends(get_db),
                      current_user=Depends(require_permission("procurement.manage"))):
    """Active people a requisition can be raised for, with their department.

    The whole active staff list (up to 1,000), so the form's searchable
    picker can find anyone; ``search`` narrows it on the server too."""
    from app.models.user import User

    query = db.query(User).filter(User.is_active.is_(True))
    if search:
        like = f"%{search.strip()}%"
        query = query.filter((User.first_name.ilike(like)) | (User.last_name.ilike(like)) | (User.employee_id.ilike(like)))
    people = query.order_by(User.first_name, User.last_name).limit(1000).all()
    departments = {d["id"]: d["name"] for d in masters.list_departments(db)}
    return _ok([
        {"id": str(u.id), "name": full_name(u), "employee_id": u.employee_id,
         "department_id": u.department_id, "department_name": departments.get(u.department_id)}
        for u in people
    ])


@router.post("/procurement/requisitions")
def raise_requisition_directly(payload: Body, db: Session = Depends(get_db),
                               current_user=Depends(require_permission("procurement.manage"))):
    """Procurement raising a requisition itself; it lands on the desk directly."""
    pr = requisitions.create_direct(payload, current_user, db)
    return _ok(_workspace(pr, current_user, db), f"{pr.pr_number} raised and on the procurement desk.")


@router.post("/procurement/requisitions/{pr_id}/collection")
def request_quotations(pr_id: int, payload: Body, db: Session = Depends(get_db),
                       current_user=Depends(require_permission("procurement.manage"))):
    """Which vendors are asked to quote for which lines: {item_vendors: {line id: [vendor ids]}}."""
    result = desk.request_quotations(pr_id, payload.get("item_vendors"), payload.get("vendor_ids"),
                                     payload.get("notes"), current_user, db)
    pr = requisitions.get(pr_id, db)
    return _ok(_workspace(pr, current_user, db), f"Quotations requested ({result['reference']}).")


@router.put("/procurement/requisitions/{pr_id}/items/{item_id}/quantity")
def change_quantity(pr_id: int, item_id: int, payload: Body, db: Session = Depends(get_db),
                    current_user=Depends(require_permission("procurement.manage"))):
    pr = desk.change_quantity(pr_id, item_id, payload.get("quantity"), payload.get("reason"), current_user, db)
    return _ok(_workspace(pr, current_user, db), "Procurement quantity recorded.")


@router.get("/procurement/requisitions/{pr_id}/comparison")
def comparison(pr_id: int, quotation_ids: str | None = Query(None), db: Session = Depends(get_db),
               current_user=Depends(require_permission("procurement.read"))):
    pr = requisitions.get(pr_id, db)
    ids = [int(i) for i in quotation_ids.split(",") if i.strip().isdigit()] if quotation_ids else None
    return _ok(quotations.compare(pr, ids, db))


# ======================================================================
# Vendors
# ======================================================================
@router.get("/vendors")
def list_vendors(search: str | None = None, status: str | None = None, usable: bool = False,
                 category: str | None = None, page: int = 1, size: int = 20,
                 db: Session = Depends(get_db), current_user=Depends(require_permission("vendor.read"))):
    return {"success": True, **vendors.list_vendors(db, search=search, status=status, usable=usable,
                                                    category=category, page=page, size=size),
            "vendor_types": list(VENDOR_TYPES), "statuses": list(VendorStatus.ALL)}


@router.post("/vendors")
def create_vendor(payload: Body, db: Session = Depends(get_db),
                  current_user=Depends(require_permission("vendor.manage"))):
    return _ok(vendors.serialize(vendors.create(payload, current_user, db), db, full=True), "Vendor added.")


@router.get("/vendors/{vendor_id}")
def get_vendor(vendor_id: int, db: Session = Depends(get_db), current_user=Depends(require_permission("vendor.read"))):
    return _ok(vendors.serialize(vendors.get(vendor_id, db), db, full=True))


@router.put("/vendors/{vendor_id}")
def update_vendor(vendor_id: int, payload: Body, db: Session = Depends(get_db),
                  current_user=Depends(require_permission("vendor.manage"))):
    return _ok(vendors.serialize(vendors.update(vendor_id, payload, current_user, db), db, full=True), "Vendor updated.")


@router.post("/vendors/{vendor_id}/action")
def vendor_action(vendor_id: int, payload: Body, db: Session = Depends(get_db),
                  current_user=Depends(require_permission("vendor.read"))):
    vendor = vendors.act(vendor_id, payload.get("action"), payload.get("remarks"), current_user, db)
    return _ok(vendors.serialize(vendor, db, full=True), "Vendor updated.")


# ======================================================================
# Vendor quotations - entered by hand
# ======================================================================
@router.get("/vendor-quotations")
def list_quotations(requisition_id: int, db: Session = Depends(get_db),
                    current_user=Depends(require_permission("procurement.read"))):
    return _ok(quotations.for_requisition(requisitions.get(requisition_id, db), db))


@router.post("/vendor-quotations")
def create_quotation(payload: Body, db: Session = Depends(get_db),
                     current_user=Depends(require_permission("procurement.manage"))):
    return _ok(quotations.serialize(quotations.create(payload, current_user, db), db), "Vendor quotation recorded.")


@router.get("/vendor-quotations/{quotation_id}")
def get_quotation(quotation_id: int, db: Session = Depends(get_db),
                  current_user=Depends(require_permission("procurement.read"))):
    return _ok(quotations.serialize(quotations.get(quotation_id, db), db))


@router.post("/vendor-quotations/{quotation_id}/revise")
def revise_quotation(quotation_id: int, payload: Body, db: Session = Depends(get_db),
                     current_user=Depends(require_permission("procurement.manage"))):
    revision = quotations.revise(quotation_id, payload, current_user, db)
    return _ok(quotations.serialize(revision, db), f"{revision.version_label} recorded.")


@router.post("/vendor-quotations/{quotation_id}/status")
def quotation_status(quotation_id: int, payload: Body, db: Session = Depends(get_db),
                     current_user=Depends(require_permission("procurement.manage"))):
    quotation = quotations.set_status(quotation_id, payload.get("status"), payload.get("remarks"), current_user, db)
    return _ok(quotations.serialize(quotation, db), "Quotation status updated.")


# ======================================================================
# Purchase orders
# ======================================================================
@router.get("/purchase-orders/payment-calendar")
def get_payment_calendar(start: str | None = None, end: str | None = None, status: str | None = None,
                         db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Every approved PO's instalments by week: due date, bank or cheque,
    amount, paid. For the purchase team and accounts (checked in the
    service: either purchase_order.read or purchase_order.pay)."""
    return _ok(payment_calendar.calendar(current_user, db, start=start, end=end, status=status))


@router.get("/purchase-orders")
def list_purchase_orders(search: str | None = None, status: str | None = None, vendor_id: int | None = None,
                         requisition_id: int | None = None, page: int = 1, size: int = 20,
                         db: Session = Depends(get_db), current_user=Depends(require_permission("purchase_order.read"))):
    return {"success": True, **purchase_orders.list_orders(db, search=search, status=status, vendor_id=vendor_id,
                                                           requisition_id=requisition_id, page=page, size=size)}


@router.post("/purchase-orders")
def create_purchase_order(payload: Body, db: Session = Depends(get_db),
                          current_user=Depends(require_permission("purchase_order.manage"))):
    """A draft PO from a vendor's quotation: {quotation_id, requisition_item_ids, payment terms...}."""
    po = purchase_orders.create(payload, current_user, db)
    return _ok(purchase_orders.serialize(po, db, current_user=current_user), f"{po.po_number} created as a draft.")


@router.get("/purchase-orders/{po_id}")
def get_purchase_order(po_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    po = purchase_orders.get(po_id, db)
    purchase_orders.assert_can_view(po, current_user, db)
    return _ok(purchase_orders.serialize(po, db, current_user=current_user))


@router.put("/purchase-orders/{po_id}")
def update_purchase_order(po_id: int, payload: Body, db: Session = Depends(get_db),
                          current_user=Depends(require_permission("purchase_order.manage"))):
    po = purchase_orders.update(po_id, payload, current_user, db)
    return _ok(purchase_orders.serialize(po, db, current_user=current_user), "Purchase order saved.")


@router.post("/purchase-orders/{po_id}/action")
def purchase_order_action(po_id: int, payload: Body, db: Session = Depends(get_db),
                          current_user=Depends(require_permission("purchase_order.manage"))):
    po = purchase_orders.act(po_id, payload.get("action"), payload.get("remarks"), current_user, db)
    return _ok(purchase_orders.serialize(po, db, current_user=current_user),
               f"{po.po_number} is now {po.status.replace('_', ' ').lower()}.")


@router.post("/purchase-orders/{po_id}/decision")
def decide_purchase_order(po_id: int, payload: Body, db: Session = Depends(get_db),
                          current_user=Depends(get_current_user)):
    """The approver whose turn it is approves or rejects (rejecting returns it to draft)."""
    po = purchase_orders.decide(po_id, payload.get("decision"), payload.get("remarks"), current_user, db)
    message = "Purchase order approved." if payload.get("decision", "").upper() == "APPROVE" else "Purchase order rejected."
    return _ok(purchase_orders.serialize(po, db, current_user=current_user), message)


@router.post("/purchase-orders/{po_id}/activity")
def log_purchase_order_activity(po_id: int, payload: Body, db: Session = Depends(get_db),
                                current_user=Depends(get_current_user)):
    """+ Log Activity on a PO: anyone who may open it adds a dated entry."""
    from app.services.procurement.common import log_by_hand

    po = purchase_orders.get(po_id, db)
    purchase_orders.assert_can_view(po, current_user, db)
    log_by_hand(db, current_user, entity_type="purchase_order", entity_id=po.id, requisition_id=po.requisition_id,
                activity=payload.get("activity"), remarks=payload.get("remarks"))
    return _ok(purchase_orders.serialize(po, db, current_user=current_user), "Activity logged.")


@router.put("/purchase-orders/{po_id}/payments/{payment_id}")
def update_payment(po_id: int, payment_id: int, payload: Body, db: Session = Depends(get_db),
                   current_user=Depends(require_permission("purchase_order.manage"))):
    """Procurement filling in an instalment: its cheque or transfer details."""
    po = purchase_orders.update_payment(po_id, payment_id, payload, current_user, db)
    return _ok(purchase_orders.serialize(po, db, current_user=current_user), "Payment details saved.")


@router.post("/purchase-orders/{po_id}/payments/{payment_id}/action")
def payment_action(po_id: int, payment_id: int, payload: Body, db: Session = Depends(get_db),
                   current_user=Depends(get_current_user)):
    """{action: SUBMIT | APPROVE | REJECT | MARK_PAID, remarks}. Procurement
    submits (purchase_order.manage); accounts approve, return or mark paid
    (purchase_order.pay). Checked in the service, per action."""
    po = purchase_orders.payment_action(po_id, payment_id, payload.get("action"), payload.get("remarks"),
                                        current_user, db)
    messages = {"SUBMIT": "Submitted to accounts.", "APPROVE": "Payment approved.",
                "REJECT": "Returned to procurement.", "MARK_PAID": "Marked paid."}
    return _ok(purchase_orders.serialize(po, db, current_user=current_user),
               messages.get(str(payload.get("action") or "").upper(), "Payment updated."))


@router.get("/purchase-orders/{po_id}/pdf")
def purchase_order_pdf(po_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    po = purchase_orders.get(po_id, db)
    purchase_orders.assert_can_view(po, current_user, db)
    return Response(
        content=purchase_orders.render_pdf(po, db),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{purchase_orders.filename(po)}"'},
    )

