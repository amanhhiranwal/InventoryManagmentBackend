"""The vendor master, kept by the procurement team.

Vendors never sign in, register or receive anything from the system: the
team records who they deal with, verifies the documents, and approves a
vendor before quotations are recorded against it or orders placed with it.
"""

import re
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.field_checks import check_email, check_gstin, check_mobile, check_pan, check_pin
from app.core.procurement import VENDOR_TYPES, VendorStatus
from app.core.timezone import iso
from app.models.procurement import PurchaseOrder, Vendor, VendorQuotation
from app.services.procurement.common import (
    activity_rows,
    actor,
    attachments,
    log,
    money,
    next_reference,
    text,
)

IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


def get(vendor_id: int, db: Session) -> Vendor:
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found.")
    return vendor


def _documents(value) -> dict:
    docs = value if isinstance(value, dict) else {}
    kept = {}
    for slot in ("gst", "pan", "bank", "msme"):
        files = attachments(docs.get(slot) if isinstance(docs.get(slot), list) else [docs.get(slot)] if docs.get(slot) else [])
        if files:
            kept[slot] = files[0]
    others = attachments(docs.get("other"))
    if others:
        kept["other"] = others
    return kept


def _list_of(value) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip() for v in value or [] if str(v).strip()]


def _apply(vendor: Vendor, payload: dict) -> None:
    name = text(payload.get("name"), 200)
    if not name:
        raise HTTPException(status_code=400, detail="A vendor needs a name.")

    vendor_type = text(payload.get("vendor_type"), 50)
    if vendor_type and vendor_type not in VENDOR_TYPES:
        raise HTTPException(status_code=400, detail=f"Vendor type is one of: {', '.join(VENDOR_TYPES)}.")

    ifsc = (text(payload.get("ifsc"), 15) or "").upper() or None
    if ifsc and not IFSC.match(ifsc):
        raise HTTPException(status_code=400, detail=f"{ifsc} is not an IFSC - four letters, 0, then six characters.")

    def whole(value, field):
        if value in (None, ""):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"{field} must be a whole number of days.")
        if number < 0:
            raise HTTPException(status_code=400, detail=f"{field} cannot be negative.")
        return number

    vendor.name = name
    vendor.vendor_type = vendor_type
    vendor.contact_person = text(payload.get("contact_person"), 150)
    vendor.designation = text(payload.get("designation"), 100)
    vendor.mobile = check_mobile(payload.get("mobile")) or None
    vendor.email = check_email(payload.get("email")) or None
    vendor.address = text(payload.get("address"), 500)
    vendor.city = text(payload.get("city"), 100)
    vendor.state = text(payload.get("state"), 100)
    vendor.pin_code = check_pin(payload.get("pin_code")) or None
    vendor.gstin = check_gstin(payload.get("gstin")) or None
    vendor.pan = check_pan(payload.get("pan")) or None
    vendor.bank_name = text(payload.get("bank_name"), 150)
    vendor.account_holder = text(payload.get("account_holder"), 150)
    vendor.account_number = text(payload.get("account_number"), 40)
    vendor.ifsc = ifsc
    vendor.branch = text(payload.get("branch"), 150)
    vendor.product_categories = _list_of(payload.get("product_categories"))
    vendor.brands = _list_of(payload.get("brands"))
    vendor.payment_terms = text(payload.get("payment_terms"), 300)
    vendor.credit_days = whole(payload.get("credit_days"), "Credit days")
    vendor.freight_terms = text(payload.get("freight_terms"), 300)
    vendor.lead_time_days = whole(payload.get("lead_time_days"), "Delivery lead time")
    vendor.warranty_terms = text(payload.get("warranty_terms"), 300)
    vendor.documents = _documents(payload.get("documents"))
    vendor.remarks = text(payload.get("remarks"))


def create(payload: dict, current_user: dict, db: Session) -> Vendor:
    vendor = Vendor(vendor_code=next_reference(db, Vendor, "vendor_code", "VEN", yearly=False))
    _apply(vendor, payload)

    duplicate = None
    if vendor.gstin:
        duplicate = db.query(Vendor).filter(Vendor.gstin == vendor.gstin).first()
    if duplicate:
        raise HTTPException(status_code=409, detail=f"{duplicate.name} ({duplicate.vendor_code}) already has that GSTIN.")

    vendor.status = (
        VendorStatus.PENDING_VERIFICATION
        if str(payload.get("status") or "").upper() == VendorStatus.PENDING_VERIFICATION
        else VendorStatus.DRAFT
    )
    vendor.created_by, _ = actor(current_user, db)
    db.add(vendor)
    db.flush()

    log(db, current_user, entity_type="vendor", entity_id=vendor.id, action="Vendor Created",
        new={"vendor_code": vendor.vendor_code, "name": vendor.name, "status": vendor.status})
    db.commit()
    db.refresh(vendor)
    return vendor


def update(vendor_id: int, payload: dict, current_user: dict, db: Session) -> Vendor:
    vendor = get(vendor_id, db)
    before = {"name": vendor.name, "gstin": vendor.gstin, "account_number": vendor.account_number}

    _apply(vendor, payload)

    if vendor.gstin:
        duplicate = db.query(Vendor).filter(Vendor.gstin == vendor.gstin, Vendor.id != vendor.id).first()
        if duplicate:
            raise HTTPException(status_code=409, detail=f"{duplicate.name} ({duplicate.vendor_code}) already has that GSTIN.")

    # Changed bank details or GSTIN have to be checked again.
    after = {"name": vendor.name, "gstin": vendor.gstin, "account_number": vendor.account_number}
    if vendor.documents_verified and (before["gstin"] != after["gstin"] or before["account_number"] != after["account_number"]):
        vendor.documents_verified = False
        vendor.verified_by = None
        vendor.verified_at = None

    log(db, current_user, entity_type="vendor", entity_id=vendor.id, action="Vendor Updated",
        previous=before, new=after)
    db.commit()
    db.refresh(vendor)
    return vendor


#: action -> (status it sets, who may - "manage" or "approve")
ACTIONS = {
    "SUBMIT": (VendorStatus.PENDING_VERIFICATION, "manage"),
    "VERIFY": (None, "approve"),
    "APPROVE": (VendorStatus.APPROVED, "approve"),
    "ACTIVATE": (VendorStatus.ACTIVE, "approve"),
    "HOLD": (VendorStatus.ON_HOLD, "approve"),
    "BLOCK": (VendorStatus.BLOCKED, "approve"),
    "DEACTIVATE": (VendorStatus.INACTIVE, "approve"),
}


def act(vendor_id: int, action: str, remarks: str | None, current_user: dict, db: Session) -> Vendor:
    from app.services.procurement.common import require

    action = (action or "").upper()
    if action not in ACTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown vendor action {action}.")

    status, needs = ACTIONS[action]
    require(current_user, f"vendor.{needs}", db)

    vendor = get(vendor_id, db)
    previous = {"status": vendor.status, "documents_verified": vendor.documents_verified}
    remarks = text(remarks, 2000)
    who, _ = actor(current_user, db)

    if action == "VERIFY":
        docs = vendor.documents or {}
        missing = [label for slot, label in (("gst", "GST certificate"), ("pan", "PAN"), ("bank", "bank proof")) if not docs.get(slot)]
        if missing:
            raise HTTPException(status_code=400, detail=f"Upload the {', '.join(missing)} before verifying.")
        vendor.documents_verified = True
        vendor.verified_by = who
        vendor.verified_at = datetime.utcnow()
        if vendor.status in (VendorStatus.DRAFT, VendorStatus.PENDING_VERIFICATION):
            vendor.status = VendorStatus.PENDING_VERIFICATION
    elif action in ("APPROVE", "ACTIVATE"):
        if not vendor.documents_verified:
            raise HTTPException(status_code=409, detail="Verify the vendor's documents before approving it.")
        if action == "APPROVE" and vendor.status not in (VendorStatus.DRAFT, VendorStatus.PENDING_VERIFICATION, VendorStatus.ON_HOLD):
            raise HTTPException(status_code=409, detail=f"A vendor that is {vendor.status.replace('_', ' ').lower()} cannot be approved.")
        vendor.status = status
        if action == "APPROVE" or not vendor.approved_by:
            vendor.approved_by = who
            vendor.approved_at = datetime.utcnow()
    else:
        if action in ("HOLD", "BLOCK", "DEACTIVATE") and not remarks:
            raise HTTPException(status_code=400, detail="Say why - putting a vendor on hold, blocking or deactivating needs remarks.")
        vendor.status = status

    log(db, current_user, entity_type="vendor", entity_id=vendor.id, action=f"Vendor {action.title()}",
        previous=previous, new={"status": vendor.status, "documents_verified": vendor.documents_verified},
        remarks=remarks)
    db.commit()
    db.refresh(vendor)
    return vendor


def assert_usable(vendor: Vendor) -> None:
    if vendor.status not in VendorStatus.USABLE:
        raise HTTPException(
            status_code=409,
            detail=f"{vendor.name} is {vendor.status.replace('_', ' ').lower()}. "
                   "Only approved and active vendors can quote or be ordered from.",
        )


# ----------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------
def performance(vendor: Vendor, db: Session) -> dict:
    """What the record says about how this vendor has done.

    Delivery and quality need goods receipts, which this module does not
    record, so those are reported as not yet measured rather than as zero.
    """

    quotes = db.query(func.count(VendorQuotation.id)).filter(
        VendorQuotation.vendor_id == vendor.id, VendorQuotation.is_latest.is_(True)
    ).scalar() or 0
    selected = db.query(func.count(VendorQuotation.id)).filter(
        VendorQuotation.vendor_id == vendor.id, VendorQuotation.status == "SELECTED"
    ).scalar() or 0
    orders = db.query(PurchaseOrder).filter(
        PurchaseOrder.vendor_id == vendor.id, PurchaseOrder.status != "CANCELLED"
    ).all()
    return {
        "quotations": quotes,
        "selected": selected,
        "win_rate": round(selected / quotes * 100, 1) if quotes else None,
        "purchase_orders": len(orders),
        "po_value": money(sum(o.grand_total or 0 for o in orders)),
        "on_time_delivery": None,
        "quality_acceptance": None,
        "note": "Delivery and quality are measured from goods receipts, which are not recorded yet.",
    }


def serialize(vendor: Vendor, db: Session, *, full: bool = False) -> dict:
    data = {
        "id": vendor.id,
        "vendor_code": vendor.vendor_code,
        "name": vendor.name,
        "vendor_type": vendor.vendor_type,
        "contact_person": vendor.contact_person,
        "designation": vendor.designation,
        "mobile": vendor.mobile,
        "email": vendor.email,
        "address": vendor.address,
        "city": vendor.city,
        "state": vendor.state,
        "pin_code": vendor.pin_code,
        "gstin": vendor.gstin,
        "pan": vendor.pan,
        "bank_name": vendor.bank_name,
        "account_holder": vendor.account_holder,
        "account_number": vendor.account_number,
        "ifsc": vendor.ifsc,
        "branch": vendor.branch,
        "product_categories": vendor.product_categories or [],
        "brands": vendor.brands or [],
        "payment_terms": vendor.payment_terms,
        "credit_days": vendor.credit_days,
        "freight_terms": vendor.freight_terms,
        "lead_time_days": vendor.lead_time_days,
        "warranty_terms": vendor.warranty_terms,
        "documents": vendor.documents or {},
        "status": vendor.status,
        "usable": vendor.status in VendorStatus.USABLE,
        "documents_verified": vendor.documents_verified,
        "verified_at": iso(vendor.verified_at),
        "approved_at": iso(vendor.approved_at),
        "remarks": vendor.remarks,
        "created_at": iso(vendor.created_at),
    }

    if full:
        from app.services.procurement import purchase_orders, quotations

        data["performance"] = performance(vendor, db)
        data["quotations"] = [
            quotations.serialize(q, db, full=False)
            for q in db.query(VendorQuotation)
            .filter(VendorQuotation.vendor_id == vendor.id)
            .order_by(VendorQuotation.id.desc()).limit(100).all()
        ]
        data["purchase_orders"] = [
            purchase_orders.serialize(po, db, full=False)
            for po in db.query(PurchaseOrder)
            .filter(PurchaseOrder.vendor_id == vendor.id)
            .order_by(PurchaseOrder.id.desc()).limit(100).all()
        ]
        data["activity"] = activity_rows(db, entity_type="vendor", entity_id=vendor.id)

    return data


def list_vendors(db: Session, *, search: str | None = None, status: str | None = None,
                 usable: bool = False, category: str | None = None, page: int = 1, size: int = 20) -> dict:
    query = db.query(Vendor)

    counts = dict(db.query(Vendor.status, func.count(Vendor.id)).group_by(Vendor.status).all())

    if usable:
        query = query.filter(Vendor.status.in_(VendorStatus.USABLE))
    elif status:
        query = query.filter(Vendor.status == status.upper())

    if search:
        like = f"%{search.strip()}%"
        query = query.filter(or_(
            Vendor.name.ilike(like), Vendor.vendor_code.ilike(like), Vendor.gstin.ilike(like),
            Vendor.contact_person.ilike(like), Vendor.city.ilike(like),
        ))

    rows = query.order_by(Vendor.name).all()
    if category:
        wanted = category.strip().lower()
        rows = [v for v in rows if any(wanted == str(c).lower() for c in v.product_categories or [])]

    total = len(rows)
    size = min(max(size, 1), 500)
    start = max(0, (page - 1) * size)

    return {
        "data": [serialize(v, db) for v in rows[start:start + size]],
        "total": total,
        "counts": counts,
    }
