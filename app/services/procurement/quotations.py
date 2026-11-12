"""Vendor quotations, typed in by procurement from what vendors sent offline.

Nothing goes to a vendor and nothing comes in from one: the team records a
quotation against an approved requisition and attaches the vendor's own
document. A revised offer is a new version beside the old, never written
over it.
"""

from datetime import date, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.procurement import (
    COMPLIANCE,
    PRStatus,
    ProcurementStatus,
    QuotationStatus,
)
from app.core.timezone import iso
from app.models.procurement import (
    PurchaseRequisition,
    PurchaseRequisitionItem,
    VendorQuotation,
    VendorQuotationItem,
)
from app.services.procurement import vendors as vendor_service
from app.services.procurement.common import (
    activity_rows,
    actor,
    attachments,
    log,
    money,
    next_reference,
    text,
    today,
    user_row,
    full_name,
)

#: Procurement statuses a recorded quotation moves the PR on from.
BEFORE_QUOTES = (
    ProcurementStatus.AWAITING,
    ProcurementStatus.ASSIGNED,
    ProcurementStatus.IN_PROGRESS,
    ProcurementStatus.QUOTATION_COLLECTION,
)


def get(quotation_id: int, db: Session) -> VendorQuotation:
    quotation = db.query(VendorQuotation).filter(VendorQuotation.id == quotation_id).first()
    if quotation is None:
        raise HTTPException(status_code=404, detail="Vendor quotation not found.")
    return quotation


def is_expired(quotation: VendorQuotation) -> bool:
    return bool(quotation.valid_until and quotation.valid_until < today())


def effective_status(quotation: VendorQuotation) -> str:
    """Expired is read from the date, so nobody has to remember to set it."""

    if quotation.status not in (QuotationStatus.SELECTED, QuotationStatus.REJECTED, QuotationStatus.REVISED) and is_expired(quotation):
        return QuotationStatus.EXPIRED
    return quotation.status


# ----------------------------------------------------------------------
# The arithmetic
# ----------------------------------------------------------------------
def _num(value, field: str, *, allow_negative: bool = False) -> float:
    if value in (None, ""):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{field} must be a number.")
    if number < 0 and not allow_negative:
        raise HTTPException(status_code=400, detail=f"{field} cannot be negative.")
    return number


def price_line(raw: dict, number: int) -> dict:
    """One line's figures, worked out on the server whatever the form sent.

    taxable = quantity x rate - discount; GST on the taxable amount; the
    line total adds freight, packing and other charges, which are taken as
    already inclusive of any tax on them.
    """

    quantity = _num(raw.get("quantity"), f"Line {number} quantity")
    if quantity <= 0:
        raise HTTPException(status_code=400, detail=f"Line {number} needs an offered quantity above nothing.")

    rate = _num(raw.get("unit_rate"), f"Line {number} unit rate")
    gross = quantity * rate

    discount_type = (str(raw.get("discount_type") or "PERCENT")).upper()
    if discount_type not in ("PERCENT", "AMOUNT"):
        raise HTTPException(status_code=400, detail=f"Line {number} discount is a percentage or an amount.")

    discount_value = _num(raw.get("discount_value"), f"Line {number} discount")
    if discount_type == "PERCENT":
        if discount_value > 100:
            raise HTTPException(status_code=400, detail=f"Line {number} discount cannot be over 100%.")
        discount = gross * discount_value / 100
    else:
        if discount_value > gross:
            raise HTTPException(status_code=400, detail=f"Line {number} discount is more than the line is worth.")
        discount = discount_value

    gst_rate = _num(raw.get("gst_rate"), f"Line {number} GST rate")
    if gst_rate > 100:
        raise HTTPException(status_code=400, detail=f"Line {number} GST rate cannot be over 100%.")

    taxable = gross - discount
    gst = taxable * gst_rate / 100
    freight = _num(raw.get("freight"), f"Line {number} freight")
    packing = _num(raw.get("packing"), f"Line {number} packing")
    other = _num(raw.get("other_charges"), f"Line {number} other charges")

    compliance = (str(raw.get("compliance") or "COMPLIANT")).upper()
    if compliance not in COMPLIANCE:
        raise HTTPException(status_code=400, detail=f"Line {number} compliance is Compliant, Partial or Non-compliant.")

    delivery = raw.get("delivery_days")
    if delivery not in (None, ""):
        try:
            delivery = int(delivery)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"Line {number} delivery time is a whole number of days.")
    else:
        delivery = None

    return {
        "quantity": quantity,
        "unit_rate": money(rate),
        "discount_type": discount_type,
        "discount_value": discount_value,
        "discount_amount": money(discount),
        "taxable_amount": money(taxable),
        "gst_rate": gst_rate,
        "gst_amount": money(gst),
        "freight": money(freight),
        "packing": money(packing),
        "other_charges": money(other),
        "total": money(taxable + gst + freight + packing + other),
        "delivery_days": delivery,
        "compliance": compliance,
    }


def _apply(quotation: VendorQuotation, pr: PurchaseRequisition, payload: dict, db: Session) -> None:
    pr_items = {item.id: item for item in pr.items}

    def day(value, field):
        if value in (None, ""):
            return None
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            raise HTTPException(status_code=400, detail=f"{field} is not a date.")

    quotation.vendor_reference = text(payload.get("vendor_reference"), 100)
    quotation.quotation_date = day(payload.get("quotation_date"), "Quotation date")
    quotation.valid_until = day(payload.get("valid_until"), "Valid until")
    quotation.received_date = day(payload.get("received_date"), "Received date") or today()
    if quotation.quotation_date and quotation.valid_until and quotation.valid_until < quotation.quotation_date:
        raise HTTPException(status_code=400, detail="Valid until cannot be before the quotation date.")

    quotation.currency = (text(payload.get("currency"), 10) or "INR").upper()
    quotation.payment_terms = text(payload.get("payment_terms"), 300)
    quotation.delivery_terms = text(payload.get("delivery_terms"), 300)
    quotation.freight_terms = text(payload.get("freight_terms"), 300)
    quotation.warranty_terms = text(payload.get("warranty_terms"), 300)
    quotation.remarks = text(payload.get("remarks"))
    quotation.attachments = attachments(payload.get("attachments"))

    days = payload.get("delivery_days")
    try:
        quotation.delivery_days = int(days) if days not in (None, "") else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Expected delivery time is a whole number of days.")
    if quotation.delivery_days is not None and quotation.delivery_days < 0:
        raise HTTPException(status_code=400, detail="Expected delivery time cannot be negative.")

    if payload.get("received_by"):
        receiver = user_row(payload["received_by"], db)
        if receiver is None:
            raise HTTPException(status_code=400, detail="That person is not a user.")
        quotation.received_by = receiver.id
        quotation.received_by_name = full_name(receiver)

    lines = payload.get("items") or []
    if not lines:
        raise HTTPException(status_code=400, detail="Enter at least one quoted line.")

    quotation.items = []
    seen = set()
    sums = {"gross": 0.0, "discount": 0.0, "taxable": 0.0, "gst": 0.0, "charges": 0.0, "total": 0.0}

    for number, raw in enumerate(lines, start=1):
        pr_item_id = raw.get("requisition_item_id")
        pr_item = pr_items.get(int(pr_item_id)) if pr_item_id else None
        if pr_item_id and pr_item is None:
            raise HTTPException(status_code=400, detail=f"Line {number} is not a line of {pr.pr_number}.")
        if pr_item is not None:
            if pr_item.id in seen:
                raise HTTPException(status_code=400, detail=f"{pr_item.product_name} is quoted twice.")
            seen.add(pr_item.id)

        figures = price_line(raw, number)
        sums["gross"] += figures["quantity"] * figures["unit_rate"]
        sums["discount"] += figures["discount_amount"]
        sums["taxable"] += figures["taxable_amount"]
        sums["gst"] += figures["gst_amount"]
        sums["charges"] += figures["freight"] + figures["packing"] + figures["other_charges"]
        sums["total"] += figures["total"]

        quotation.items.append(VendorQuotationItem(
            requisition_item_id=pr_item.id if pr_item else None,
            product_name=(pr_item.product_name if pr_item else text(raw.get("product_name"), 300)),
            sku=(pr_item.sku if pr_item else text(raw.get("sku"), 100)),
            vendor_product_name=text(raw.get("vendor_product_name"), 300),
            vendor_model=text(raw.get("vendor_model"), 150),
            brand=text(raw.get("brand"), 150),
            hsn=text(raw.get("hsn"), 20),
            uom=text(raw.get("uom"), 30) or (pr_item.uom if pr_item else None),
            warranty=text(raw.get("warranty"), 200),
            deviation=text(raw.get("deviation"), 1000),
            **figures,
        ))

    quotation.subtotal = money(sums["gross"])
    quotation.discount_total = money(sums["discount"])
    quotation.taxable_total = money(sums["taxable"])
    quotation.gst_total = money(sums["gst"])
    quotation.charges_total = money(sums["charges"])
    quotation.grand_total = money(sums["total"])


def _assert_open(pr: PurchaseRequisition) -> None:
    if pr.status != PRStatus.APPROVED:
        raise HTTPException(status_code=409, detail="Quotations are recorded against an approved requisition only.")
    if pr.procurement_status in (ProcurementStatus.CANCELLED, ProcurementStatus.COMPLETED):
        raise HTTPException(status_code=409, detail=f"{pr.pr_number} is {pr.procurement_status.lower()}.")


def create(payload: dict, current_user: dict, db: Session) -> VendorQuotation:
    from app.services.procurement import requisitions

    pr = requisitions.get(int(payload.get("requisition_id") or 0), db, lock=True)
    _assert_open(pr)

    vendor = vendor_service.get(int(payload.get("vendor_id") or 0), db)
    vendor_service.assert_usable(vendor)

    status = (str(payload.get("status") or QuotationStatus.RECEIVED)).upper()
    if status not in (QuotationStatus.DRAFT, QuotationStatus.RECEIVED):
        raise HTTPException(status_code=400, detail="A new quotation is saved as Draft or Received.")

    existing = db.query(VendorQuotation).filter(
        VendorQuotation.requisition_id == pr.id,
        VendorQuotation.vendor_id == vendor.id,
        VendorQuotation.is_latest.is_(True),
        VendorQuotation.status != QuotationStatus.REJECTED,
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"{vendor.name} already has quotation {existing.quotation_number} on {pr.pr_number}. "
                   "Record a revision of it instead.",
        )

    entered_by, entered_name = actor(current_user, db)
    quotation = VendorQuotation(
        quotation_number=next_reference(db, VendorQuotation, "quotation_number", "VQ"),
        requisition_id=pr.id,
        vendor_id=vendor.id,
        vendor_name=vendor.name,
        version=0,
        version_label="Original",
        is_latest=True,
        status=status,
        entered_by=entered_by,
        entered_by_name=entered_name,
        received_by=entered_by,
        received_by_name=entered_name,
    )
    _apply(quotation, pr, payload, db)

    if status != QuotationStatus.DRAFT and not quotation.attachments:
        raise HTTPException(status_code=400, detail="Attach the vendor's quotation document.")

    db.add(quotation)
    db.flush()

    previous = pr.procurement_status
    if pr.procurement_status in BEFORE_QUOTES and status != QuotationStatus.DRAFT:
        pr.procurement_status = ProcurementStatus.QUOTATIONS_RECEIVED

    log(db, current_user, entity_type="quotation", entity_id=quotation.id, requisition_id=pr.id,
        action="Vendor Quotation Recorded",
        previous={"procurement_status": previous},
        new={"quotation": quotation.quotation_number, "vendor": vendor.name,
             "total": quotation.grand_total, "procurement_status": pr.procurement_status})
    db.commit()
    db.refresh(quotation)
    return quotation


def revise(quotation_id: int, payload: dict, current_user: dict, db: Session) -> VendorQuotation:
    """A new version of a vendor's offer; the old one stays as it was."""

    from app.services.procurement import requisitions

    source = get(quotation_id, db)
    pr = requisitions.get(source.requisition_id, db, lock=True)
    _assert_open(pr)

    if not source.is_latest:
        raise HTTPException(status_code=409, detail="Revise the latest version of this quotation.")
    if source.status in (QuotationStatus.SELECTED, QuotationStatus.REJECTED):
        raise HTTPException(status_code=409, detail=f"A {source.status.lower()} quotation cannot be revised.")

    vendor = vendor_service.get(source.vendor_id, db)
    vendor_service.assert_usable(vendor)

    final = bool(payload.get("final"))
    version = (source.version or 0) + 1
    entered_by, entered_name = actor(current_user, db)

    revision = VendorQuotation(
        quotation_number=next_reference(db, VendorQuotation, "quotation_number", "VQ"),
        requisition_id=pr.id,
        vendor_id=vendor.id,
        vendor_name=vendor.name,
        root_id=source.root_id or source.id,
        version=version,
        version_label="Final Negotiated" if final else f"Revision {version}",
        is_latest=True,
        status=QuotationStatus.FINAL if final else QuotationStatus.RECEIVED,
        entered_by=entered_by,
        entered_by_name=entered_name,
        received_by=entered_by,
        received_by_name=entered_name,
    )
    _apply(revision, pr, payload, db)

    if not revision.attachments:
        raise HTTPException(status_code=400, detail="Attach the vendor's revised quotation document.")

    source.is_latest = False
    source.status = QuotationStatus.REVISED

    db.add(revision)
    db.flush()

    log(db, current_user, entity_type="quotation", entity_id=revision.id, requisition_id=pr.id,
        action=f"Quotation {revision.version_label} Recorded",
        previous={"quotation": source.quotation_number, "total": source.grand_total},
        new={"quotation": revision.quotation_number, "total": revision.grand_total},
        remarks=text(payload.get("remarks")))
    db.commit()
    db.refresh(revision)
    return revision


def set_status(quotation_id: int, status: str, remarks: str | None, current_user: dict, db: Session) -> VendorQuotation:
    quotation = get(quotation_id, db)
    status = (status or "").upper()

    if status not in QuotationStatus.MANUAL:
        raise HTTPException(status_code=400, detail="That status is set by the system, not by hand.")
    if not quotation.is_latest:
        raise HTTPException(status_code=409, detail="Only the latest version's status can change.")
    if quotation.status == QuotationStatus.SELECTED:
        raise HTTPException(status_code=409, detail="A selected quotation's status cannot be changed.")
    if status == QuotationStatus.REJECTED and not text(remarks):
        raise HTTPException(status_code=400, detail="Say why the quotation is rejected.")

    previous = quotation.status
    quotation.status = status
    log(db, current_user, entity_type="quotation", entity_id=quotation.id,
        requisition_id=quotation.requisition_id, action="Quotation Status Changed",
        previous={"status": previous}, new={"status": status}, remarks=text(remarks))
    db.commit()
    db.refresh(quotation)
    return quotation


# ----------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------
def serialize_item(item: VendorQuotationItem) -> dict:
    return {
        "id": item.id,
        "requisition_item_id": item.requisition_item_id,
        "product_name": item.product_name,
        "sku": item.sku,
        "vendor_product_name": item.vendor_product_name,
        "vendor_model": item.vendor_model,
        "brand": item.brand,
        "hsn": item.hsn,
        "quantity": item.quantity,
        "uom": item.uom,
        "unit_rate": item.unit_rate,
        "discount_type": item.discount_type,
        "discount_value": item.discount_value,
        "discount_amount": item.discount_amount,
        "taxable_amount": item.taxable_amount,
        "gst_rate": item.gst_rate,
        "gst_amount": item.gst_amount,
        "freight": item.freight,
        "packing": item.packing,
        "other_charges": item.other_charges,
        "total": item.total,
        "delivery_days": item.delivery_days,
        "warranty": item.warranty,
        "compliance": item.compliance,
        "deviation": item.deviation,
    }


def serialize(quotation: VendorQuotation, db: Session, *, full: bool = True) -> dict:
    data = {
        "id": quotation.id,
        "quotation_number": quotation.quotation_number,
        "requisition_id": quotation.requisition_id,
        "pr_number": db.query(PurchaseRequisition.pr_number).filter(
            PurchaseRequisition.id == quotation.requisition_id).scalar(),
        "vendor_id": quotation.vendor_id,
        "vendor_name": quotation.vendor_name,
        "root_id": quotation.root_id or quotation.id,
        "version": quotation.version,
        "version_label": quotation.version_label,
        "is_latest": quotation.is_latest,
        "vendor_reference": quotation.vendor_reference,
        "quotation_date": quotation.quotation_date.isoformat() if quotation.quotation_date else None,
        "valid_until": quotation.valid_until.isoformat() if quotation.valid_until else None,
        "received_date": quotation.received_date.isoformat() if quotation.received_date else None,
        "received_by_name": quotation.received_by_name,
        "currency": quotation.currency,
        "payment_terms": quotation.payment_terms,
        "delivery_terms": quotation.delivery_terms,
        "freight_terms": quotation.freight_terms,
        "delivery_days": quotation.delivery_days,
        "warranty_terms": quotation.warranty_terms,
        "remarks": quotation.remarks,
        "attachments": quotation.attachments or [],
        "status": effective_status(quotation),
        "expired": is_expired(quotation),
        "subtotal": quotation.subtotal,
        "discount_total": quotation.discount_total,
        "taxable_total": quotation.taxable_total,
        "gst_total": quotation.gst_total,
        "charges_total": quotation.charges_total,
        "grand_total": quotation.grand_total,
        "entered_by_name": quotation.entered_by_name,
        "created_at": iso(quotation.created_at),
        "item_count": len(quotation.items or []),
    }

    if full:
        data["items"] = [serialize_item(i) for i in quotation.items]
        root = quotation.root_id or quotation.id
        data["versions"] = [
            serialize(v, db, full=False)
            for v in db.query(VendorQuotation)
            .filter((VendorQuotation.id == root) | (VendorQuotation.root_id == root))
            .order_by(VendorQuotation.version).all()
        ]
        data["activity"] = activity_rows(db, entity_type="quotation", entity_id=quotation.id)

    return data


def for_requisition(pr: PurchaseRequisition, db: Session, *, latest_only: bool = False) -> list[dict]:
    query = db.query(VendorQuotation).filter(VendorQuotation.requisition_id == pr.id)
    if latest_only:
        query = query.filter(VendorQuotation.is_latest.is_(True))
    return [serialize(q, db, full=False) for q in query.order_by(VendorQuotation.vendor_name, VendorQuotation.version).all()]


# ----------------------------------------------------------------------
# The comparative statement
# ----------------------------------------------------------------------
def compare(pr: PurchaseRequisition, quotation_ids: list[int] | None, db: Session) -> dict:
    """Quotations side by side, line by line, with the stand-outs flagged.

    Flags only - lowest price, fastest delivery, deviations, expiry. The
    choice is procurement's, with a reason, never made here.
    """

    query = db.query(VendorQuotation).filter(VendorQuotation.requisition_id == pr.id)
    if quotation_ids:
        query = query.filter(VendorQuotation.id.in_(quotation_ids))
    else:
        query = query.filter(
            VendorQuotation.is_latest.is_(True),
            VendorQuotation.status.notin_((QuotationStatus.REJECTED, QuotationStatus.DRAFT)),
        )
    quotes = query.order_by(VendorQuotation.grand_total).all()

    if quotation_ids and len(quotes) != len(set(quotation_ids)):
        raise HTTPException(status_code=400, detail=f"Every quotation compared must be on {pr.pr_number}.")

    performance = {q.vendor_id: vendor_service.performance(vendor_service.get(q.vendor_id, db), db) for q in quotes}

    columns = [
        {
            "quotation_id": q.id,
            "quotation_number": q.quotation_number,
            "vendor_id": q.vendor_id,
            "vendor_name": q.vendor_name,
            "vendor_reference": q.vendor_reference,
            "version_label": q.version_label,
            "status": effective_status(q),
            "expired": is_expired(q),
            "valid_until": q.valid_until.isoformat() if q.valid_until else None,
            "payment_terms": q.payment_terms,
            "delivery_terms": q.delivery_terms,
            "warranty_terms": q.warranty_terms,
            "delivery_days": q.delivery_days,
            "taxable_total": q.taxable_total,
            "gst_total": q.gst_total,
            "charges_total": q.charges_total,
            "grand_total": q.grand_total,
            "performance": performance.get(q.vendor_id),
            "deviations": sum(1 for i in q.items if i.compliance != "COMPLIANT" or i.deviation),
        }
        for q in quotes
    ]

    if columns:
        lowest_total = min(c["grand_total"] for c in columns)
        days = [c["delivery_days"] for c in columns if c["delivery_days"] is not None]
        fastest = min(days) if days else None
        for column in columns:
            column["lowest_total"] = column["grand_total"] == lowest_total
            column["fastest_delivery"] = fastest is not None and column["delivery_days"] == fastest
            column["difference"] = money(column["grand_total"] - lowest_total)
            column["variance_percent"] = (
                round((column["grand_total"] - lowest_total) / lowest_total * 100, 2) if lowest_total else None
            )

    rows = []
    for item in pr.items:
        requested = item.approved_quantity if item.approved_quantity is not None else item.quantity
        cells = {}
        for q in quotes:
            line = next((i for i in q.items if i.requisition_item_id == item.id), None)
            if line is None:
                continue
            charges = (line.freight or 0) + (line.packing or 0) + (line.other_charges or 0)
            cells[q.id] = {
                **serialize_item(line),
                "charges": money(charges),
                "landed_unit_cost": money(line.total / line.quantity) if line.quantity else None,
                "short_quantity": line.quantity < requested,
            }

        quoted = [c for c in cells.values()]
        if quoted:
            low_rate = min(c["unit_rate"] for c in quoted)
            landed = [c["landed_unit_cost"] for c in quoted if c["landed_unit_cost"] is not None]
            low_landed = min(landed) if landed else None
            line_days = [c["delivery_days"] for c in quoted if c["delivery_days"] is not None]
            line_fastest = min(line_days) if line_days else None
            for cell in quoted:
                cell["lowest_rate"] = cell["unit_rate"] == low_rate
                cell["lowest_landed"] = low_landed is not None and cell["landed_unit_cost"] == low_landed
                cell["fastest_delivery"] = line_fastest is not None and cell["delivery_days"] == line_fastest
                cell["deviation_flag"] = cell["compliance"] != "COMPLIANT" or bool(cell["deviation"])
                cell["variance_percent"] = (
                    round((cell["landed_unit_cost"] - low_landed) / low_landed * 100, 2)
                    if low_landed and cell["landed_unit_cost"] is not None else None
                )

        rows.append({
            "requisition_item_id": item.id,
            "product_name": item.product_name,
            "sku": item.sku,
            "uom": item.uom,
            "requested_quantity": item.quantity,
            "approved_quantity": item.approved_quantity,
            "quantity": requested,
            "quotes": {str(k): v for k, v in cells.items()},
        })

    return {
        "requisition_id": pr.id,
        "pr_number": pr.pr_number,
        "columns": columns,
        "rows": rows,
        "generated_at": iso(datetime.utcnow()),
    }
