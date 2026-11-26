"""Goods receipts (GRN): from goods at the gate to stock on the shelf.

    DRAFT        the storekeeper records what arrived: against which PO (or
                 a return / goods with no documents), from whom, the
                 delivery note, and the quantity received per product.
    SUBMITTED    sent in by the storekeeper. Still no stock.
    QC_PENDING   the receipt is posted (an authorized person): everything
                 received is in stock in QUARANTINE, in the QC bin, and the
                 PO's received quantities and status have moved.
    POSTED       quality check: each line is split into accepted (to
                 available stock, STORE by default), rejected (REJECTED) and
                 damaged (BLOCKED, both in the REJECT bin). "GRN Generated".
    CANCELLED    a draft or submitted GRN that will not be received.

Over-receiving a PO line is refused when the GRN is saved and again, under
a lock on the PO, when it is posted - so two GRNs against the same PO line
cannot both take its last units.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.procurement import POStatus
from app.core.references import next_number
from app.core.stock import (
    BLOCKED,
    CUSTOMER_RETURN,
    DO_DELIVERED,
    DO_DISPATCHED,
    GRN_CANCELLED,
    GRN_DRAFT,
    GRN_POSTED,
    GRN_PURCHASE,
    GRN_QC_PENDING,
    GRN_RETURN,
    GRN_SUBMITTED,
    PURCHASE_RECEIPT,
    QC_STATUS_TRANSFER,
    QUARANTINE,
    REJECTED,
    UNRESTRICTED,
)
from app.core.timezone import now_ist
from app.models.company import Company
from app.models.dispatch import DispatchLine, DispatchOrder
from app.models.goods_receipt import GoodsReceipt, GoodsReceiptLine
from app.models.location import Location
from app.models.procurement import PurchaseOrder, PurchaseOrderItem, PurchaseRequisition
from app.models.sales_order import SalesOrder
from app.models.warehouse import WarehouseBin
from app.services.company_scope_service import CompanyScopeService
from app.services.procurement.common import actor
from app.services.stock import posting
from app.services.stock.documents import catalogue_product
from app.services.stock.queries import lookups, scope

#: A PO goods can be received against: approved and not yet complete.
RECEIVABLE = (POStatus.APPROVED, POStatus.SENT, POStatus.ACKNOWLEDGED, POStatus.PARTIALLY_RECEIVED)


def _dec(value, label: str, allow_zero: bool = False) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{label}: enter a number.")
    if number < 0:
        raise HTTPException(status_code=400, detail=f"{label}: cannot be negative.")
    if number == 0 and not allow_zero:
        raise HTTPException(status_code=400, detail=f"{label}: must be more than zero.")
    return number


def financial_year(day) -> str:
    """2026-27 for any date from April 2026 to March 2027."""

    start = day.year if day.month >= 4 else day.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def next_grn_number(db: Session, company_id, day) -> str:
    """The next number in this company's series for the year: 2026-27/001."""

    fy = financial_year(day)
    lead = f"{fy}/"

    def issued_so_far() -> int:
        highest = 0
        for (value,) in db.query(GoodsReceipt.grn_number).filter(
            GoodsReceipt.company_id == company_id, GoodsReceipt.grn_number.like(f"{lead}%")
        ):
            tail = value[len(lead):]
            if tail.isdigit():
                highest = max(highest, int(tail))
        return highest

    def taken(number: int) -> bool:
        return (
            db.query(GoodsReceipt.id)
            .filter(GoodsReceipt.company_id == company_id, GoodsReceipt.grn_number == f"{lead}{number:03d}")
            .first()
            is not None
        )

    return f"{lead}{next_number(db, f'grn:{company_id}:{fy}', issued_so_far, taken):03d}"


def _po_company(po: PurchaseOrder, db: Session):
    requisition = db.query(PurchaseRequisition).filter(PurchaseRequisition.id == po.requisition_id).first()
    return requisition.company_id if requisition else None


def _open_quantity(item: PurchaseOrderItem, db: Session, exclude_grn: int | None = None) -> Decimal:
    """What is still to come on a PO line: ordered, less received, less what
    GRNs not yet posted are already holding."""

    held = (
        db.query(func.coalesce(func.sum(GoodsReceiptLine.qty_received), 0))
        .join(GoodsReceipt, GoodsReceipt.id == GoodsReceiptLine.grn_id)
        .filter(
            GoodsReceiptLine.po_item_id == item.id,
            GoodsReceipt.status.in_((GRN_DRAFT, GRN_SUBMITTED)),
            *( [GoodsReceipt.id != exclude_grn] if exclude_grn else [] ),
        )
        .scalar()
    )
    return Decimal(str(item.quantity or 0)) - Decimal(str(item.received_quantity or 0)) - Decimal(str(held or 0))


def _returnable(dline: DispatchLine, db: Session, exclude_grn: int | None, statuses: tuple) -> Decimal:
    """What can still come back on a dispatch line: what went out, less
    what other customer-return GRNs in ``statuses`` already bring back."""

    back = (
        db.query(func.coalesce(func.sum(GoodsReceiptLine.qty_received), 0))
        .join(GoodsReceipt, GoodsReceipt.id == GoodsReceiptLine.grn_id)
        .filter(
            GoodsReceiptLine.dispatch_line_id == dline.id,
            GoodsReceipt.status.in_(statuses),
            *([GoodsReceipt.id != exclude_grn] if exclude_grn else []),
        )
        .scalar()
    )
    return Decimal(str(dline.qty)) - Decimal(str(back or 0))


def grn_dict(grn: GoodsReceipt, db: Session, names=None) -> dict:
    companies, warehouses, bins = names or lookups(db)
    warehouse = warehouses.get(str(grn.warehouse_id), (None, None))
    po = db.query(PurchaseOrder.po_number, PurchaseOrder.po_date).filter(PurchaseOrder.id == grn.purchase_order_id).first() if grn.purchase_order_id else None
    lines = [
        {
            "id": line.id,
            "line_no": line.line_no,
            "po_item_id": line.po_item_id,
            "dispatch_line_id": line.dispatch_line_id,
            "sku": line.sku,
            "description": line.description,
            "uom": line.uom,
            "qty_ordered": float(line.qty_ordered) if line.qty_ordered is not None else None,
            "qty_received": float(line.qty_received),
            "qty_accepted": float(line.qty_accepted) if line.qty_accepted is not None else None,
            "qty_rejected": float(line.qty_rejected) if line.qty_rejected is not None else None,
            "qty_damaged": float(line.qty_damaged) if line.qty_damaged is not None else None,
            "bin_id": str(line.bin_id) if line.bin_id else None,
            "bin_code": bins.get(str(line.bin_id)) if line.bin_id else None,
            "unit_cost": float(line.unit_cost) if line.unit_cost is not None else None,
            "serials": line.serials or [],
            "remarks": line.remarks,
        }
        for line in grn.lines
    ]
    return {
        "id": grn.id,
        "grn_number": grn.grn_number,
        "company_id": str(grn.company_id),
        "company_name": companies.get(str(grn.company_id)),
        "warehouse_id": str(grn.warehouse_id),
        "warehouse_name": warehouse[0],
        "warehouse_code": warehouse[1],
        "receipt_type": grn.receipt_type,
        "purchase_order_id": grn.purchase_order_id,
        "dispatch_order_id": grn.dispatch_order_id,
        "do_number": (
            db.query(DispatchOrder.do_number).filter(DispatchOrder.id == grn.dispatch_order_id).scalar()
            if grn.dispatch_order_id else None
        ),
        "po_number": po.po_number if po else None,
        "po_date": po.po_date.date().isoformat() if po and po.po_date else None,
        "vendor_id": grn.vendor_id,
        "supplier_name": grn.supplier_name,
        "supplier_address": grn.supplier_address,
        "delivery_note_no": grn.delivery_note_no,
        "delivery_note_date": grn.delivery_note_date.isoformat() if grn.delivery_note_date else None,
        "receipt_date": grn.receipt_date.isoformat() if grn.receipt_date else None,
        "transport_remarks": grn.transport_remarks,
        "remarks": grn.remarks,
        "status": grn.status,
        "received_by": str(grn.received_by) if grn.received_by else None,
        "received_by_name": grn.received_by_name,
        "submitted_at": grn.submitted_at.isoformat() if grn.submitted_at else None,
        "receipt_posted_by_name": grn.receipt_posted_by_name,
        "receipt_posted_at": grn.receipt_posted_at.isoformat() if grn.receipt_posted_at else None,
        "inspected_by_name": grn.inspected_by_name,
        "posted_at": grn.posted_at.isoformat() if grn.posted_at else None,
        "cancelled_reason": grn.cancelled_reason,
        "created_at": grn.created_at.isoformat() if grn.created_at else None,
        "lines": lines,
        "totals": {
            "ordered": sum(x["qty_ordered"] or 0 for x in lines),
            "received": sum(x["qty_received"] for x in lines),
            "accepted": sum(x["qty_accepted"] or 0 for x in lines),
            "rejected": sum(x["qty_rejected"] or 0 for x in lines),
            "damaged": sum(x["qty_damaged"] or 0 for x in lines),
        },
    }


class GoodsReceiptService:

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    @staticmethod
    def _get(grn_id: int, current_user: dict, db: Session, lock: bool = False) -> GoodsReceipt:
        query = db.query(GoodsReceipt).filter(GoodsReceipt.id == grn_id)
        if lock:
            query = query.with_for_update()
        grn = query.first()
        if grn is None:
            raise HTTPException(status_code=404, detail="Goods receipt not found.")
        CompanyScopeService.assert_warehouse_allowed(current_user, grn.warehouse_id, db)
        return grn

    @staticmethod
    def get(grn_id: int, current_user: dict, db: Session) -> dict:
        return grn_dict(GoodsReceiptService._get(grn_id, current_user, db), db)

    @staticmethod
    def list_receipts(current_user: dict, db: Session, *, company_id=None, warehouse_id=None, status=None,
                      search=None, page: int = 1, size: int = 20) -> dict:
        conditions = scope(GoodsReceipt, current_user, db, company_id, warehouse_id)
        if conditions is None:
            return {"data": [], "total": 0, "counts": {}}

        base = db.query(GoodsReceipt).filter(*conditions)
        if search and search.strip():
            term = f"%{search.strip()}%"
            po_ids = [r[0] for r in db.query(PurchaseOrder.id).filter(PurchaseOrder.po_number.ilike(term)).all()]
            base = base.filter(
                GoodsReceipt.grn_number.ilike(term)
                | GoodsReceipt.supplier_name.ilike(term)
                | GoodsReceipt.delivery_note_no.ilike(term)
                | GoodsReceipt.purchase_order_id.in_(po_ids or [-1])
            )

        counts = {s: n for s, n in base.with_entities(GoodsReceipt.status, func.count()).group_by(GoodsReceipt.status).all()}
        query = base.filter(GoodsReceipt.status == status.upper()) if status else base
        total = query.count()
        rows = query.order_by(GoodsReceipt.id.desc()).offset((max(page, 1) - 1) * size).limit(size).all()
        names = lookups(db)
        return {"data": [grn_dict(r, db, names) for r in rows], "total": total, "counts": counts}

    @staticmethod
    def returnable_dispatches(warehouse_id: str, current_user: dict, db: Session, search: str | None = None) -> list[dict]:
        """Dispatches of the warehouse's company that goods can come back on,
        with what can still be returned on each line."""

        warehouse = CompanyScopeService.assert_warehouse_allowed(current_user, warehouse_id, db)
        query = db.query(DispatchOrder).filter(
            DispatchOrder.company_id == warehouse.company_id,
            DispatchOrder.status.in_((DO_DISPATCHED, DO_DELIVERED)),
        )
        if search and search.strip():
            query = query.filter(DispatchOrder.do_number.ilike(f"%{search.strip()}%"))
        result = []
        for dispatch in query.order_by(DispatchOrder.id.desc()).limit(50).all():
            order = db.query(SalesOrder.order_number, SalesOrder.customer_name, SalesOrder.company_name).filter(
                SalesOrder.id == dispatch.sales_order_id).first()
            lines = []
            for line in dispatch.lines:
                room = _returnable(line, db, None, (GRN_DRAFT, GRN_SUBMITTED, GRN_QC_PENDING, GRN_POSTED))
                if room > 0:
                    lines.append({"id": line.id, "sku": line.sku, "product_name": line.product_name,
                                  "qty_dispatched": float(line.qty), "qty_returnable": float(room),
                                  "serials": line.serials or []})
            if lines:
                result.append({
                    "id": dispatch.id, "do_number": dispatch.do_number, "status": dispatch.status,
                    "order_number": order.order_number if order else None,
                    "customer_name": (order.company_name or order.customer_name) if order else None,
                    "dispatch_date": dispatch.dispatch_date.isoformat() if dispatch.dispatch_date else None,
                    "lines": lines,
                })
        return result

    @staticmethod
    def receivable_orders(warehouse_id: str, current_user: dict, db: Session, search: str | None = None) -> list[dict]:
        """POs of the warehouse's company with something still to receive."""

        warehouse = CompanyScopeService.assert_warehouse_allowed(current_user, warehouse_id, db)
        query = (
            db.query(PurchaseOrder)
            .join(PurchaseRequisition, PurchaseRequisition.id == PurchaseOrder.requisition_id)
            .filter(PurchaseRequisition.company_id == warehouse.company_id, PurchaseOrder.status.in_(RECEIVABLE))
        )
        if search and search.strip():
            term = f"%{search.strip()}%"
            query = query.filter(PurchaseOrder.po_number.ilike(term) | PurchaseOrder.vendor_name.ilike(term))

        result = []
        for po in query.order_by(PurchaseOrder.id.desc()).limit(50).all():
            items = [
                {
                    "po_item_id": item.id,
                    "sku": (item.sku or "").strip().upper() or None,
                    "product_name": item.product_name,
                    "description": item.description,
                    "uom": item.uom,
                    "qty_ordered": float(item.quantity or 0),
                    "qty_received": float(item.received_quantity or 0),
                    "qty_open": float(_open_quantity(item, db)),
                    "unit_rate": float(item.unit_rate or 0),
                }
                for item in po.items
            ]
            if any(i["qty_open"] > 0 for i in items):
                result.append({
                    "id": po.id,
                    "po_number": po.po_number,
                    "po_date": po.po_date.date().isoformat() if po.po_date else None,
                    "status": po.status,
                    "vendor_id": po.vendor_id,
                    "vendor_name": po.vendor_name,
                    "vendor_address": po.vendor_address,
                    "created_by_name": po.created_by_name,
                    "items": items,
                })
        return result

    # ------------------------------------------------------------------
    # Drafting
    # ------------------------------------------------------------------
    @staticmethod
    def _build(grn: GoodsReceipt, payload, warehouse: Location, db: Session) -> None:
        """Fill a draft from the form, checking every line."""

        grn.receipt_type = payload.receipt_type
        grn.receipt_date = payload.receipt_date or now_ist().date()
        grn.delivery_note_no = (payload.delivery_note_no or "").strip() or None
        grn.delivery_note_date = payload.delivery_note_date
        grn.transport_remarks = (payload.transport_remarks or "").strip() or None
        grn.remarks = (payload.remarks or "").strip() or None

        po = None
        po_items: dict[int, PurchaseOrderItem] = {}
        dispatch = None
        dispatch_lines: dict[int, DispatchLine] = {}
        grn.dispatch_order_id = None
        if payload.receipt_type == GRN_RETURN and getattr(payload, "dispatch_order_id", None):
            dispatch = db.query(DispatchOrder).filter(DispatchOrder.id == payload.dispatch_order_id).first()
            if dispatch is None:
                raise HTTPException(status_code=404, detail="Dispatch order not found.")
            if dispatch.company_id != warehouse.company_id:
                # A company takes back only what it sold itself.
                raise HTTPException(status_code=400, detail=f"{dispatch.do_number} went out from another company than this warehouse's.")
            if dispatch.status not in (DO_DISPATCHED, DO_DELIVERED):
                raise HTTPException(status_code=409, detail=f"{dispatch.do_number} has not been dispatched, so nothing can come back on it.")
            dispatch_lines = {line.id: line for line in dispatch.lines}
            grn.dispatch_order_id = dispatch.id

        if payload.receipt_type == GRN_PURCHASE:
            if not payload.purchase_order_id:
                raise HTTPException(status_code=400, detail="Choose the purchase order these goods are against.")
            po = db.query(PurchaseOrder).filter(PurchaseOrder.id == payload.purchase_order_id).first()
            if po is None:
                raise HTTPException(status_code=404, detail="Purchase order not found.")
            if _po_company(po, db) != warehouse.company_id:
                # Goods bought by one company never land in another's warehouse.
                raise HTTPException(status_code=400, detail=f"{po.po_number} was raised for another company than this warehouse's.")
            if po.status not in RECEIVABLE:
                raise HTTPException(status_code=409, detail=f"{po.po_number} is {po.status.replace('_', ' ').lower()} and cannot receive goods.")
            po_items = {item.id: item for item in po.items}
            grn.purchase_order_id = po.id
            grn.vendor_id = po.vendor_id
            grn.supplier_name = po.vendor_name or "—"
            grn.supplier_address = po.vendor_address
        else:
            supplier = (payload.supplier_name or "").strip()
            if not supplier and dispatch is not None:
                order = db.query(SalesOrder).filter(SalesOrder.id == dispatch.sales_order_id).first()
                supplier = ((order.company_name or order.customer_name) if order else "") or ""
            if not supplier:
                raise HTTPException(status_code=400, detail="Enter who the goods came from.")
            grn.purchase_order_id = None
            grn.vendor_id = None
            grn.supplier_name = supplier
            grn.supplier_address = (payload.supplier_address or "").strip() or None

        if not payload.lines:
            raise HTTPException(status_code=400, detail="Add at least one product.")

        lines = []
        seen_items = set()
        for index, raw in enumerate(payload.lines, start=1):
            label = f"Line {index}"
            sku = (raw.sku or "").strip().upper()
            if not sku:
                raise HTTPException(status_code=400, detail=f"{label}: choose the product from the catalogue.")
            product = catalogue_product(sku, str(warehouse.company_id))
            if product is None:
                raise HTTPException(status_code=400, detail=f"{label}: {sku} is not in this company's product catalogue.")

            received = _dec(raw.qty_received, f"{label} quantity received")
            ordered = None
            unit_cost = Decimal(str(raw.unit_cost)) if raw.unit_cost is not None else None

            if po is not None:
                item = po_items.get(raw.po_item_id) if raw.po_item_id else None
                if item is None:
                    raise HTTPException(status_code=400, detail=f"{label}: pick the PO line it is against.")
                if raw.po_item_id in seen_items:
                    raise HTTPException(status_code=400, detail=f"{label}: that PO line is already on this GRN.")
                seen_items.add(raw.po_item_id)
                open_qty = _open_quantity(item, db, exclude_grn=grn.id)
                if received > open_qty:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{label}: {received.normalize()} received but only {open_qty.normalize()} is still to come on {po.po_number}.",
                    )
                ordered = Decimal(str(item.quantity or 0))
                if unit_cost is None:
                    unit_cost = Decimal(str(item.unit_rate or 0))
            elif dispatch is not None:
                dline = dispatch_lines.get(raw.dispatch_line_id) if raw.dispatch_line_id else None
                if dline is None:
                    raise HTTPException(status_code=400, detail=f"{label}: pick the line of {dispatch.do_number} that is coming back.")
                if dline.sku.upper() != sku:
                    raise HTTPException(status_code=400, detail=f"{label}: {dispatch.do_number}'s line is {dline.sku}, not {sku}.")
                room = _returnable(dline, db, grn.id, (GRN_DRAFT, GRN_SUBMITTED, GRN_QC_PENDING, GRN_POSTED))
                if received > room:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{label}: only {room.normalize()} of {sku} on {dispatch.do_number} can still come back.",
                    )
                ordered = Decimal(str(dline.qty))
            elif raw.qty_ordered is not None:
                ordered = _dec(raw.qty_ordered, f"{label} quantity ordered", allow_zero=True)

            lines.append(GoodsReceiptLine(
                line_no=index,
                po_item_id=raw.po_item_id if po is not None else None,
                dispatch_line_id=raw.dispatch_line_id if dispatch is not None else None,
                sku=sku,
                description=(raw.description or product.get("name") or "").strip()[:300] or None,
                uom=(raw.uom or (product.get("attributes") or {}).get("unit") or None),
                qty_ordered=ordered,
                qty_received=received,
                unit_cost=unit_cost,
                remarks=(raw.remarks or "").strip() or None,
            ))

        grn.lines.clear()
        db.flush()
        grn.lines.extend(lines)

    @staticmethod
    def create(payload, current_user: dict, db: Session) -> dict:
        warehouse = CompanyScopeService.assert_warehouse_allowed(current_user, payload.warehouse_id, db)
        user_id, name = actor(current_user, db)
        receipt_date = payload.receipt_date or now_ist().date()
        # Allocated inside this transaction: if anything below fails, the
        # rollback gives the number back.
        grn = GoodsReceipt(
            grn_number=next_grn_number(db, warehouse.company_id, receipt_date),
            company_id=warehouse.company_id,
            warehouse_id=warehouse.id,
            status=GRN_DRAFT,
            supplier_name="",
            receipt_date=receipt_date,
            received_by=user_id,
            received_by_name=name,
        )
        db.add(grn)
        GoodsReceiptService._build(grn, payload, warehouse, db)
        db.commit()
        db.refresh(grn)
        return grn_dict(grn, db)

    @staticmethod
    def update(grn_id: int, payload, current_user: dict, db: Session) -> dict:
        grn = GoodsReceiptService._get(grn_id, current_user, db, lock=True)
        if grn.status != GRN_DRAFT:
            raise HTTPException(status_code=409, detail=f"GRN {grn.grn_number} has been sent for QC and can no longer be changed.")
        warehouse = db.query(Location).filter(Location.id == grn.warehouse_id).first()
        GoodsReceiptService._build(grn, payload, warehouse, db)
        db.commit()
        db.refresh(grn)
        return grn_dict(grn, db)

    @staticmethod
    def submit(grn_id: int, current_user: dict, db: Session) -> dict:
        """The storekeeper sends the GRN in. No stock moves yet."""

        grn = GoodsReceiptService._get(grn_id, current_user, db, lock=True)
        if grn.status != GRN_DRAFT:
            raise HTTPException(status_code=409, detail=f"GRN {grn.grn_number} has already been sent in.")
        grn.status = GRN_SUBMITTED
        grn.submitted_at = datetime.utcnow()
        db.commit()
        db.refresh(grn)
        return grn_dict(grn, db)

    @staticmethod
    def cancel(grn_id: int, reason: str, current_user: dict, db: Session) -> dict:
        grn = GoodsReceiptService._get(grn_id, current_user, db, lock=True)
        if grn.status not in (GRN_DRAFT, GRN_SUBMITTED):
            raise HTTPException(
                status_code=409,
                detail="Its receipt has been posted, so stock has moved; it cannot be cancelled. Reject the goods at QC instead.",
            )
        if not (reason or "").strip():
            raise HTTPException(status_code=400, detail="Say why it is cancelled.")
        grn.status = GRN_CANCELLED
        grn.cancelled_reason = reason.strip()
        db.commit()
        db.refresh(grn)
        return grn_dict(grn, db)

    @staticmethod
    def _bins(grn: GoodsReceipt, db: Session) -> dict[str, WarehouseBin]:
        bins = {str(b.id): b for b in db.query(WarehouseBin).filter(WarehouseBin.warehouse_id == grn.warehouse_id).all()}

        def first(code: str):
            return next((b for b in bins.values() if b.code == code and b.is_active), None) or next(
                (b for b in bins.values() if b.bin_type == code and b.is_active), None)

        return {"all": bins, "STORE": first("STORE"), "QC": first("QC"), "REJECT": first("REJECT")}

    @staticmethod
    def post_receipt(grn_id: int, current_user: dict, db: Session) -> dict:
        """Post the physical receipt: everything received enters stock in
        QUARANTINE, in the QC bin, and the PO's received quantities move.

        One transaction, with the GRN and the PO locked, so the PO's open
        quantity is checked against what other GRNs have posted meanwhile.
        """

        grn = GoodsReceiptService._get(grn_id, current_user, db, lock=True)
        if grn.status in (GRN_QC_PENDING, GRN_POSTED):
            raise HTTPException(status_code=409, detail=f"GRN {grn.grn_number}'s receipt has already been posted.")
        if grn.status != GRN_SUBMITTED:
            raise HTTPException(status_code=409, detail="Send the GRN in before posting its receipt.")

        bins = GoodsReceiptService._bins(grn, db)
        if bins["QC"] is None:
            raise HTTPException(status_code=400, detail="This warehouse has no active quality-check bin.")

        po = None
        po_items: dict[int, PurchaseOrderItem] = {}
        if grn.purchase_order_id:
            po = db.query(PurchaseOrder).filter(PurchaseOrder.id == grn.purchase_order_id).with_for_update().one()
            if po.status not in RECEIVABLE:
                raise HTTPException(status_code=409, detail=f"{po.po_number} is {po.status.replace('_', ' ').lower()} and cannot receive goods.")
            po_items = {
                item.id: item
                for item in db.query(PurchaseOrderItem)
                .filter(PurchaseOrderItem.purchase_order_id == po.id)
                .with_for_update()
                .all()
            }

        if grn.dispatch_order_id:
            # Locked, so two returns against one dispatch are checked in turn.
            db.query(DispatchOrder).filter(DispatchOrder.id == grn.dispatch_order_id).with_for_update().one()
            for line in grn.lines:
                dline = db.query(DispatchLine).filter(DispatchLine.id == line.dispatch_line_id).first()
                room = _returnable(dline, db, grn.id, (GRN_QC_PENDING, GRN_POSTED)) if dline else Decimal(0)
                if Decimal(line.qty_received) > room:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Line {line.line_no}: only {room.normalize()} of {line.sku} can still come back on that dispatch.",
                    )

        movement_type = CUSTOMER_RETURN if grn.receipt_type == GRN_RETURN else PURCHASE_RECEIPT
        movements = []
        for line in grn.lines:
            received = Decimal(line.qty_received)
            if po is not None:
                item = po_items.get(line.po_item_id)
                if item is None:
                    raise HTTPException(status_code=409, detail=f"Line {line.line_no}: its PO line no longer exists.")
                still = Decimal(str(item.quantity or 0)) - Decimal(str(item.received_quantity or 0))
                if received > still:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Line {line.line_no}: only {still.normalize()} is still to come on {po.po_number}; another GRN has taken the rest.",
                    )
                item.received_quantity = float(Decimal(str(item.received_quantity or 0)) + received)

            movements.append(posting.Movement(
                company_id=grn.company_id, warehouse_id=grn.warehouse_id, bin_id=bins["QC"].id,
                sku=line.sku, product_name=line.description, stock_status=QUARANTINE,
                quantity=received, movement_type=movement_type, source_line_id=str(line.id),
                unit_cost=Decimal(line.unit_cost) if line.unit_cost is not None else None, uom=line.uom,
            ))

        user_id, name = actor(current_user, db)
        try:
            posting.post(db, posting.Posting(
                source_doc_type="GRN",
                source_doc_id=str(grn.id),
                source_doc_number=grn.grn_number,
                movement_date=grn.receipt_date,
                movements=movements,
                created_by=str(grn.received_by) if grn.received_by else None,
                created_by_name=grn.received_by_name,
                approved_by=str(user_id) if user_id else None,
                approved_by_name=name,
                remarks=f"GRN {grn.grn_number} from {grn.supplier_name}: received into quarantine",
            ))
        except HTTPException:
            db.rollback()
            raise

        if po is not None:
            done = all(Decimal(str(i.received_quantity or 0)) >= Decimal(str(i.quantity or 0)) for i in po_items.values())
            po.status = POStatus.FULLY_RECEIVED if done else POStatus.PARTIALLY_RECEIVED

        grn.status = GRN_QC_PENDING
        grn.receipt_posted_by = user_id
        grn.receipt_posted_by_name = name
        grn.receipt_posted_at = datetime.utcnow()
        db.commit()
        db.refresh(grn)
        return grn_dict(grn, db)

    @staticmethod
    def inspect_and_post(grn_id: int, payload, current_user: dict, db: Session) -> dict:
        """Quality check: move each line out of quarantine.

        Accepted goes to available stock (the bin chosen, STORE by default),
        rejected to REJECTED and damaged to BLOCKED in the REJECT bin. One
        transaction; a second or simultaneous post moves nothing more.
        """

        grn = GoodsReceiptService._get(grn_id, current_user, db, lock=True)
        if grn.status == GRN_POSTED:
            raise HTTPException(status_code=409, detail=f"GRN {grn.grn_number} has already been posted.")
        if grn.status != GRN_QC_PENDING:
            raise HTTPException(status_code=409, detail="Post the receipt into quarantine before the quality check.")

        decisions = {d.line_id: d for d in payload.lines}
        missing = [line.line_no for line in grn.lines if line.id not in decisions]
        if missing:
            raise HTTPException(status_code=400, detail=f"Record the quality check for line(s) {', '.join(map(str, missing))}.")

        bins = GoodsReceiptService._bins(grn, db)
        movements = []

        for line in grn.lines:
            label = f"Line {line.line_no}"
            decision = decisions[line.id]
            received = Decimal(line.qty_received)
            accepted = _dec(decision.qty_accepted, f"{label} accepted", allow_zero=True)
            rejected = _dec(decision.qty_rejected, f"{label} rejected", allow_zero=True)
            damaged = _dec(decision.qty_damaged or 0, f"{label} damaged", allow_zero=True)
            if accepted + rejected + damaged != received:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{label}: accepted {accepted.normalize()} + rejected {rejected.normalize()} + damaged "
                        f"{damaged.normalize()} must equal the {received.normalize()} received."
                    ),
                )
            if (rejected > 0 or damaged > 0) and not (decision.remarks or line.remarks or "").strip():
                raise HTTPException(status_code=400, detail=f"{label}: give the reason for rejection or damage.")

            put_into = bins["all"].get(str(decision.bin_id)) if decision.bin_id else bins["STORE"]
            if put_into is None or not put_into.is_active or put_into.bin_type != "STORE":
                raise HTTPException(status_code=400, detail=f"{label}: choose an active store bin in this warehouse for the accepted goods.")
            if (rejected > 0 or damaged > 0) and bins["REJECT"] is None:
                raise HTTPException(status_code=400, detail="This warehouse has no active rejected-goods bin.")

            serials = [s.strip().upper() for s in (decision.serials or []) if s and s.strip()]
            if serials and len(serials) != accepted:
                raise HTTPException(
                    status_code=400,
                    detail=f"{label}: {len(serials)} serial(s) for {accepted.normalize()} accepted. Enter one per accepted unit.",
                )

            line.qty_accepted = accepted
            line.qty_rejected = rejected
            line.qty_damaged = damaged
            line.bin_id = put_into.id
            line.serials = serials or None
            if decision.remarks is not None:
                line.remarks = decision.remarks.strip() or None

            cost = Decimal(line.unit_cost) if line.unit_cost is not None else None
            common = dict(
                company_id=grn.company_id, warehouse_id=grn.warehouse_id, sku=line.sku,
                product_name=line.description, movement_type=QC_STATUS_TRANSFER, source_line_id=str(line.id),
                unit_cost=cost, uom=line.uom,
            )
            movements.append(posting.Movement(bin_id=bins["QC"].id, stock_status=QUARANTINE, quantity=-received, **common))
            if accepted > 0:
                movements.append(posting.Movement(bin_id=put_into.id, stock_status=UNRESTRICTED, quantity=accepted,
                                                  serials=serials, **common))
            if rejected > 0:
                movements.append(posting.Movement(bin_id=bins["REJECT"].id, stock_status=REJECTED, quantity=rejected, **common))
            if damaged > 0:
                movements.append(posting.Movement(bin_id=bins["REJECT"].id, stock_status=BLOCKED, quantity=damaged, **common))

        user_id, name = actor(current_user, db)
        try:
            posting.post(db, posting.Posting(
                # Its own document type, so its keys never meet the receipt's.
                source_doc_type="GRN_QC",
                source_doc_id=str(grn.id),
                source_doc_number=grn.grn_number,
                movement_date=now_ist().date(),
                movements=movements,
                created_by=str(grn.received_by) if grn.received_by else None,
                created_by_name=grn.received_by_name,
                approved_by=str(user_id) if user_id else None,
                approved_by_name=name,
                remarks=f"Quality check of GRN {grn.grn_number}",
            ))
        except HTTPException:
            db.rollback()
            raise

        grn.status = GRN_POSTED
        grn.inspected_by = user_id
        grn.inspected_by_name = name
        grn.posted_at = datetime.utcnow()

        # Until the ledger is live the desks sell from the Inventory list:
        # accepted goods go onto it too, and come off again if this fails.
        from app.services.stock.catalogue_sync import add_accepted_to_catalogue, ledger_off, undo_catalogue

        undo: list = []
        try:
            if ledger_off(db):
                add_accepted_to_catalogue(grn, db, undo)
            db.commit()
        except Exception:
            undo_catalogue(undo)
            db.rollback()
            raise
        db.refresh(grn)
        return grn_dict(grn, db)

    @staticmethod
    def letterhead(grn_id: int, current_user: dict, db: Session) -> dict:
        """The company block printed at the top of the GRN."""

        grn = GoodsReceiptService._get(grn_id, current_user, db)
        company = db.query(Company).filter(Company.id == grn.company_id).first()
        address = ", ".join(
            p for p in (company.address_line_1, company.address_line_2, company.city, company.state, company.postal_code) if p
        )
        return {
            "company_name": company.company_name,
            "address": address,
            "gst_number": company.gst_number,
            "cin_number": company.cin_number,
            "phone_number": company.phone_number,
            "email": company.email,
        }
