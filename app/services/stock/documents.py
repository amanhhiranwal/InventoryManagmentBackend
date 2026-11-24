"""Stock documents: opening stock, adjustments in and out, vendor returns.

All four share one life: drafted by one person, submitted, then approved
by someone else - which posts it to the stock ledger in the same
transaction as the status change, so a document never says Posted without
its stock having moved. Each belongs to one company and one warehouse and
carries one line per product (SKU) with the bin, quantity, unit cost and,
for serial-numbered goods, the serials.

  OPENING         stock a warehouse already holds on its first day (in).
  ADJUSTMENT_IN   found, data correction... (in). Reason code required.
  ADJUSTMENT_OUT  damage, loss, expiry... (out). Reason code required.
                  Above a value limit Finance must approve as well.
  VENDOR_RETURN   goods going back to the supplier, against the GRN they
                  came in on: only what that GRN received, and only once.

Products are looked up in the MongoDB catalogue by SKU (the model number),
among the company's own products and the shared ones.
"""

import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.stock import (
    ADJUSTMENT_FINANCE_DEFAULT,
    ADJUSTMENT_FINANCE_SETTING,
    ADJUSTMENT_IN,
    ADJUSTMENT_OUT,
    ADJUSTMENT_REASONS,
    BLOCKED,
    DOCUMENT_PERMISSIONS,
    DOCUMENT_PREFIX,
    DOCUMENT_SIGN,
    DRAFT,
    FINANCE_ADJUSTMENT,
    OPENING,
    OPENING_BALANCE,
    POSTED,
    REJECTED,
    REJECTED_DOC,
    STOCK_ADJUSTMENT_IN,
    STOCK_ADJUSTMENT_OUT,
    SUBMITTED,
    UNRESTRICTED,
    VENDOR_RETURN,
    VENDOR_RETURN_DOC,
)
from app.core.timezone import now_ist
from app.models.app_setting import AppSetting
from app.models.company import Company
from app.models.location import Location
from app.models.stock import StockDocument, StockDocumentLine
from app.models.warehouse import WarehouseBin
from app.services.company_scope_service import CompanyScopeService
from app.services.procurement.common import actor, has_permission, next_reference
from app.services.stock import posting
from app.services.stock.queries import lookups

#: The ledger movement each document type writes.
MOVEMENT_FOR = {
    OPENING: OPENING_BALANCE,
    ADJUSTMENT_IN: STOCK_ADJUSTMENT_IN,
    ADJUSTMENT_OUT: STOCK_ADJUSTMENT_OUT,
    VENDOR_RETURN_DOC: VENDOR_RETURN,
}

LABEL = {
    OPENING: "Opening stock",
    ADJUSTMENT_IN: "Stock adjustment (in)",
    ADJUSTMENT_OUT: "Stock adjustment (out)",
    VENDOR_RETURN_DOC: "Vendor return",
}


def catalogue_product(sku: str, company_id: str) -> dict | None:
    """The catalogue record for a SKU that this company may stock."""

    from app.services.inventory_service import InventoryService

    pattern = {"$regex": f"^{re.escape(sku)}$", "$options": "i"}
    return InventoryService.items_col.find_one(
        {"attributes.model_number": pattern, "company_id": {"$in": [company_id, None]}},
        {"image_base64": 0},
    )


logger = logging.getLogger(__name__)


def finance_threshold(db: Session) -> Decimal:
    """The value at or above which Finance must approve. A setting that is
    not a number is logged and the documented default used, so the limit
    never silently disappears."""

    row = db.query(AppSetting).filter(AppSetting.key == ADJUSTMENT_FINANCE_SETTING).first()
    if not row or row.value in (None, ""):
        return Decimal(ADJUSTMENT_FINANCE_DEFAULT)
    try:
        return Decimal(str(row.value).replace(",", "").strip())
    except InvalidOperation:
        logger.warning(
            "AppSetting %s is %r, not a number; using the default %s.",
            ADJUSTMENT_FINANCE_SETTING, row.value, ADJUSTMENT_FINANCE_DEFAULT,
        )
        return Decimal(ADJUSTMENT_FINANCE_DEFAULT)


def serial_units_on_shelf(db: Session, company_id, warehouse_id, bin_id, sku: str) -> int:
    """How many serial-numbered units of a SKU are on the shelf in a bin.
    Above zero, stock of it can only leave that bin naming its serials."""

    from app.core.stock import SERIAL_IN_STOCK
    from app.models.stock import StockSerial

    return (
        db.query(func.count(StockSerial.id))
        .filter(
            StockSerial.company_id == company_id,
            StockSerial.warehouse_id == warehouse_id,
            StockSerial.bin_id == bin_id,
            StockSerial.sku == sku,
            StockSerial.status == SERIAL_IN_STOCK,
        )
        .scalar()
        or 0
    )


def document_dict(doc: StockDocument, names=None) -> dict:
    companies, warehouses, bins = names or ({}, {}, {})
    warehouse = warehouses.get(str(doc.warehouse_id), (None, None))
    lines = [
        {
            "id": line.id,
            "line_no": line.line_no,
            "sku": line.sku,
            "product_name": line.product_name,
            "bin_id": str(line.bin_id),
            "bin_code": bins.get(str(line.bin_id)),
            "quantity": float(line.quantity),
            "unit_cost": float(line.unit_cost) if line.unit_cost is not None else None,
            "serials": line.serials or [],
            "stock_status": line.stock_status or UNRESTRICTED,
            "grn_line_id": line.grn_line_id,
        }
        for line in doc.lines
    ]
    return {
        "id": doc.id,
        "doc_number": doc.doc_number,
        "doc_type": doc.doc_type,
        "company_id": str(doc.company_id),
        "company_name": companies.get(str(doc.company_id)),
        "warehouse_id": str(doc.warehouse_id),
        "warehouse_name": warehouse[0],
        "warehouse_code": warehouse[1],
        "status": doc.status,
        "movement_date": doc.movement_date.isoformat() if doc.movement_date else None,
        "reason": doc.reason,
        "reason_code": doc.reason_code,
        "grn_id": doc.grn_id,
        "remarks": doc.remarks,
        "created_by": str(doc.created_by) if doc.created_by else None,
        "created_by_name": doc.created_by_name,
        "submitted_at": doc.submitted_at.isoformat() if doc.submitted_at else None,
        "approved_by_name": doc.approved_by_name,
        "posted_at": doc.posted_at.isoformat() if doc.posted_at else None,
        "rejected_by_name": doc.rejected_by_name,
        "rejection_reason": doc.rejection_reason,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "lines": lines,
        "total_quantity": sum(line["quantity"] for line in lines),
        "total_value": sum(line["quantity"] * (line["unit_cost"] or 0) for line in lines),
    }


class StockDocumentService:

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    @staticmethod
    def _get(doc_type: str, doc_id: int, current_user: dict, db: Session, lock: bool = False) -> StockDocument:
        query = db.query(StockDocument).filter(StockDocument.id == doc_id, StockDocument.doc_type.in_(StockDocumentService._family(doc_type)))
        if lock:
            query = query.with_for_update()
        doc = query.first()
        if doc is None:
            raise HTTPException(status_code=404, detail=f"{LABEL[doc_type]} document not found.")
        # Someone else's warehouse reads as not found, so ids cannot be probed.
        CompanyScopeService.assert_warehouse_allowed(current_user, doc.warehouse_id, db)
        return doc

    @staticmethod
    def _family(doc_type: str) -> tuple[str, ...]:
        """Adjustments in and out are one list and one set of routes."""

        return (ADJUSTMENT_IN, ADJUSTMENT_OUT) if doc_type in (ADJUSTMENT_IN, ADJUSTMENT_OUT) else (doc_type,)

    @staticmethod
    def get(doc_type: str, doc_id: int, current_user: dict, db: Session) -> dict:
        return document_dict(StockDocumentService._get(doc_type, doc_id, current_user, db), lookups(db))

    @staticmethod
    def list_documents(doc_type: str, current_user: dict, db: Session, *, company_id=None, warehouse_id=None, status=None,
                       search=None, page: int = 1, size: int = 20) -> dict:
        from app.services.stock.queries import scope

        conditions = scope(StockDocument, current_user, db, company_id, warehouse_id)
        if conditions is None:
            return {"data": [], "total": 0}

        query = db.query(StockDocument).filter(*conditions, StockDocument.doc_type.in_(StockDocumentService._family(doc_type)))
        if status:
            query = query.filter(StockDocument.status == status.upper())
        if search and search.strip():
            query = query.filter(StockDocument.doc_number.ilike(f"%{search.strip()}%"))

        total = query.count()
        rows = query.order_by(StockDocument.id.desc()).offset((max(page, 1) - 1) * size).limit(size).all()
        names = lookups(db)
        return {"data": [document_dict(r, names) for r in rows], "total": total}

    # ------------------------------------------------------------------
    # Writing a draft
    # ------------------------------------------------------------------
    @staticmethod
    def _vendor_return_lines(doc: StockDocument, payload_lines, warehouse: Location, db: Session) -> list[StockDocumentLine]:
        """Lines of a vendor return: each against a line of its GRN, never
        more than that GRN received in that state, less what other returns
        (not rejected) already take."""

        from app.models.goods_receipt import GoodsReceipt

        grn = db.query(GoodsReceipt).filter(GoodsReceipt.id == doc.grn_id).first()
        if grn is None or grn.warehouse_id != warehouse.id:
            raise HTTPException(status_code=400, detail="Choose a GRN of this warehouse.")
        if grn.status != "POSTED":
            raise HTTPException(status_code=400, detail=f"GRN {grn.grn_number} has not been through quality check yet.")

        grn_lines = {line.id: line for line in grn.lines}
        bins = {b.code: b for b in db.query(WarehouseBin).filter(WarehouseBin.warehouse_id == warehouse.id).all()}
        lines = []
        for index, raw in enumerate(payload_lines, start=1):
            source = grn_lines.get(raw.grn_line_id)
            if source is None:
                raise HTTPException(status_code=400, detail=f"Line {index}: pick a line of GRN {grn.grn_number}.")
            status = (raw.stock_status or REJECTED).upper()
            received = {REJECTED: source.qty_rejected, BLOCKED: source.qty_damaged, UNRESTRICTED: source.qty_accepted}.get(status)
            if received is None:
                raise HTTPException(status_code=400, detail=f"Line {index}: return rejected, damaged or accepted goods.")
            taken = (
                db.query(func.coalesce(func.sum(StockDocumentLine.quantity), 0))
                .join(StockDocument, StockDocument.id == StockDocumentLine.document_id)
                .filter(
                    StockDocumentLine.grn_line_id == source.id,
                    func.coalesce(StockDocumentLine.stock_status, UNRESTRICTED) == status,
                    StockDocument.doc_type == VENDOR_RETURN_DOC,
                    StockDocument.status.in_((DRAFT, SUBMITTED, POSTED)),
                    StockDocument.id != (doc.id or -1),
                )
                .scalar()
            )
            room = Decimal(received or 0) - Decimal(taken or 0)
            quantity = Decimal(str(raw.quantity))
            if quantity > room:
                raise HTTPException(
                    status_code=400,
                    detail=f"Line {index}: only {room.normalize()} of {source.sku} ({status.lower()}) from GRN {grn.grn_number} can still go back.",
                )
            bin_row = bins.get("STORE") if status == UNRESTRICTED else bins.get("REJECT")
            if status == UNRESTRICTED and source.bin_id:
                bin_row = db.query(WarehouseBin).filter(WarehouseBin.id == source.bin_id).first()
            # Only accepted goods carry serials in stock; rejected and damaged
            # ones were never put on the shelf, so they go back by quantity.
            serials = [s.strip().upper() for s in (raw.serials or []) if s and s.strip()] if status == UNRESTRICTED else []
            if (
                status == UNRESTRICTED
                and not serials
                and serial_units_on_shelf(db, warehouse.company_id, warehouse.id, bin_row.id, source.sku)
            ):
                raise HTTPException(status_code=400, detail=f"Line {index}: {source.sku} is serial-numbered. Enter the serial of each unit going back.")
            if serials and len(serials) != quantity:
                raise HTTPException(status_code=400, detail=f"Line {index}: {len(serials)} serial(s) for {quantity.normalize()} going back.")
            lines.append(StockDocumentLine(
                line_no=index, sku=source.sku, product_name=source.description, bin_id=bin_row.id, quantity=quantity,
                unit_cost=source.unit_cost, serials=serials or None, stock_status=status, grn_line_id=source.id,
            ))
        if not lines:
            raise HTTPException(status_code=400, detail="Add at least one product.")
        return lines

    @staticmethod
    def _lines(doc_type: str, payload_lines, warehouse: Location, db: Session) -> list[StockDocumentLine]:
        if not payload_lines:
            raise HTTPException(status_code=400, detail="Add at least one product.")

        bins = {
            str(b.id): b
            for b in db.query(WarehouseBin).filter(WarehouseBin.warehouse_id == warehouse.id).all()
        }
        default_bin = next(
            (b for b in bins.values() if b.code == "STORE" and b.is_active),
            next((b for b in bins.values() if b.bin_type == "STORE" and b.is_active), None),
        )

        lines: list[StockDocumentLine] = []
        seen_serials: set[str] = set()
        seen_skus: set[tuple] = set()

        for index, raw in enumerate(payload_lines, start=1):
            sku = (raw.sku or "").strip().upper()
            if not sku:
                raise HTTPException(status_code=400, detail=f"Line {index}: choose a product.")

            product = catalogue_product(sku, str(warehouse.company_id))
            if product is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Line {index}: {sku} is not in this company's product catalogue.",
                )

            bin_row = bins.get(str(raw.bin_id)) if raw.bin_id else default_bin
            if bin_row is None:
                raise HTTPException(status_code=400, detail=f"Line {index}: the bin is not in this warehouse.")
            if not bin_row.is_active and DOCUMENT_SIGN[doc_type] > 0:
                raise HTTPException(status_code=400, detail=f"Line {index}: bin {bin_row.code} is switched off.")

            if (sku, str(bin_row.id)) in seen_skus:
                raise HTTPException(
                    status_code=400,
                    detail=f"Line {index}: {sku} is already on this document for bin {bin_row.code}. Add the quantity there.",
                )
            seen_skus.add((sku, str(bin_row.id)))

            try:
                quantity = Decimal(str(raw.quantity))
            except (InvalidOperation, TypeError):
                raise HTTPException(status_code=400, detail=f"Line {index}: enter a quantity.")
            if quantity <= 0:
                raise HTTPException(status_code=400, detail=f"Line {index}: the quantity must be more than zero.")

            unit_cost = None
            if raw.unit_cost is not None:
                unit_cost = Decimal(str(raw.unit_cost))
                if unit_cost < 0:
                    raise HTTPException(status_code=400, detail=f"Line {index}: the unit cost cannot be negative.")

            serials = [s.strip().upper() for s in (raw.serials or []) if s and s.strip()]
            if (
                not serials
                and DOCUMENT_SIGN[doc_type] < 0
                and serial_units_on_shelf(db, warehouse.company_id, warehouse.id, bin_row.id, sku)
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"Line {index}: {sku} is serial-numbered. Enter the serial of each unit going out.",
                )
            if serials:
                if quantity != int(quantity) or len(serials) != int(quantity):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Line {index}: {len(serials)} serial number(s) for a quantity of {quantity.normalize()}. Enter one per unit.",
                    )
                for serial in serials:
                    if serial in seen_serials:
                        raise HTTPException(status_code=400, detail=f"Serial {serial} is entered twice.")
                    seen_serials.add(serial)

            lines.append(
                StockDocumentLine(
                    line_no=index,
                    sku=sku,
                    product_name=product.get("name"),
                    bin_id=bin_row.id,
                    quantity=quantity,
                    unit_cost=unit_cost,
                    serials=serials or None,
                    stock_status=UNRESTRICTED,
                )
            )

        return lines

    @staticmethod
    def _fill(doc: StockDocument, payload, warehouse: Location, db: Session) -> None:
        doc_type = doc.doc_type
        if doc_type in (ADJUSTMENT_IN, ADJUSTMENT_OUT):
            code = (getattr(payload, "reason_code", None) or "").upper()
            if code not in ADJUSTMENT_REASONS:
                raise HTTPException(status_code=400, detail="Choose why stock is being adjusted.")
            doc.reason_code = code
        if doc_type == VENDOR_RETURN_DOC:
            grn_id = getattr(payload, "grn_id", None)
            if grn_id:
                doc.grn_id = grn_id
            if not doc.grn_id:
                raise HTTPException(status_code=400, detail="Choose the GRN the goods came in on.")
            lines = StockDocumentService._vendor_return_lines(doc, payload.lines, warehouse, db)
        else:
            lines = StockDocumentService._lines(doc_type, payload.lines, warehouse, db)

        doc.lines.clear()
        db.flush()
        doc.lines.extend(lines)
        if payload.movement_date:
            doc.movement_date = payload.movement_date
        if payload.reason is not None:
            doc.reason = payload.reason.strip() or LABEL[doc_type]
        if payload.remarks is not None:
            doc.remarks = payload.remarks.strip() or None

    @staticmethod
    def create(doc_type: str, payload, current_user: dict, db: Session) -> dict:
        if doc_type in (ADJUSTMENT_IN, ADJUSTMENT_OUT):
            doc_type = ADJUSTMENT_OUT if (getattr(payload, "direction", None) or "").upper() == "OUT" else ADJUSTMENT_IN
        warehouse = CompanyScopeService.assert_warehouse_allowed(current_user, payload.warehouse_id, db)
        company = db.query(Company).filter(Company.id == warehouse.company_id).first()
        user_id, name = actor(current_user, db)

        prefix = f"{(company.company_code or 'CO').upper()}-{DOCUMENT_PREFIX[doc_type]}"
        doc = StockDocument(
            doc_number=next_reference(db, StockDocument, "doc_number", prefix),
            doc_type=doc_type,
            company_id=warehouse.company_id,
            warehouse_id=warehouse.id,
            status=DRAFT,
            movement_date=payload.movement_date or now_ist().date(),
            reason=(payload.reason or LABEL[doc_type]).strip(),
            created_by=user_id,
            created_by_name=name,
        )
        db.add(doc)
        try:
            StockDocumentService._fill(doc, payload, warehouse, db)
        except HTTPException:
            db.rollback()
            raise
        db.commit()
        db.refresh(doc)
        return document_dict(doc, lookups(db))

    @staticmethod
    def update(doc_type: str, doc_id: int, payload, current_user: dict, db: Session) -> dict:
        doc = StockDocumentService._get(doc_type, doc_id, current_user, db, lock=True)
        if doc.status != DRAFT:
            raise HTTPException(status_code=409, detail=f"{doc.doc_number} is {doc.status.lower()} and can no longer be changed.")
        warehouse = db.query(Location).filter(Location.id == doc.warehouse_id).first()
        try:
            StockDocumentService._fill(doc, payload, warehouse, db)
        except HTTPException:
            db.rollback()
            raise
        db.commit()
        db.refresh(doc)
        return document_dict(doc, lookups(db))

    @staticmethod
    def delete(doc_type: str, doc_id: int, current_user: dict, db: Session) -> None:
        doc = StockDocumentService._get(doc_type, doc_id, current_user, db, lock=True)
        if doc.status != DRAFT:
            raise HTTPException(status_code=409, detail="Only a draft can be deleted.")
        db.delete(doc)
        db.commit()

    # ------------------------------------------------------------------
    # Moving it on
    # ------------------------------------------------------------------
    @staticmethod
    def submit(doc_type: str, doc_id: int, current_user: dict, db: Session) -> dict:
        doc = StockDocumentService._get(doc_type, doc_id, current_user, db, lock=True)
        if doc.status != DRAFT:
            raise HTTPException(status_code=409, detail=f"{doc.doc_number} has already been submitted.")
        doc.status = SUBMITTED
        doc.submitted_at = datetime.utcnow()
        doc.rejection_reason = None
        doc.rejected_by_name = None
        db.commit()
        db.refresh(doc)
        return document_dict(doc, lookups(db))

    @staticmethod
    def reject(doc_type: str, doc_id: int, reason: str, current_user: dict, db: Session) -> dict:
        doc = StockDocumentService._get(doc_type, doc_id, current_user, db, lock=True)
        if doc.status != SUBMITTED:
            raise HTTPException(status_code=409, detail="Only a submitted document can be rejected.")
        if not (reason or "").strip():
            raise HTTPException(status_code=400, detail="Say why it is rejected.")
        doc.status = REJECTED_DOC
        doc.rejection_reason = reason.strip()
        doc.rejected_by_name = actor(current_user, db)[1]
        db.commit()
        db.refresh(doc)
        return document_dict(doc, lookups(db))

    @staticmethod
    def approve(doc_type: str, doc_id: int, current_user: dict, db: Session) -> dict:
        """Approve and post, in one transaction.

        The row is locked and must still be SUBMITTED, so two approvers
        clicking at once post it once; the ledger's unique keys stop it
        again even if that check were somehow passed. The person who
        prepared it never approves it, and a large adjustment also needs
        Finance's permission.
        """

        doc = StockDocumentService._get(doc_type, doc_id, current_user, db, lock=True)
        if doc.status == POSTED:
            raise HTTPException(status_code=409, detail=f"{doc.doc_number} has already been posted.")
        if doc.status != SUBMITTED:
            raise HTTPException(status_code=409, detail="Submit it for approval first.")

        user_id, name = actor(current_user, db)
        if doc.created_by and user_id and str(doc.created_by) == str(user_id):
            raise HTTPException(status_code=403, detail="Someone other than the person who prepared it has to approve it.")

        if doc.doc_type in (ADJUSTMENT_IN, ADJUSTMENT_OUT):
            value = sum((Decimal(line.quantity) * Decimal(line.unit_cost or 0) for line in doc.lines), Decimal(0))
            if value >= finance_threshold(db) and not has_permission(current_user, FINANCE_ADJUSTMENT, db):
                raise HTTPException(
                    status_code=403,
                    detail=f"This adjustment is worth Rs. {value.normalize()}, at or above the finance limit; Finance must approve it.",
                )

        sign = DOCUMENT_SIGN[doc.doc_type]
        try:
            posting.post(
                db,
                posting.Posting(
                    source_doc_type="STOCK_DOCUMENT",
                    source_doc_id=str(doc.id),
                    source_doc_number=doc.doc_number,
                    movement_date=doc.movement_date,
                    created_by=str(doc.created_by) if doc.created_by else None,
                    created_by_name=doc.created_by_name,
                    approved_by=str(user_id) if user_id else None,
                    approved_by_name=name,
                    remarks=f"{doc.reason}" + (f" ({doc.reason_code})" if doc.reason_code else ""),
                    movements=[
                        posting.Movement(
                            company_id=doc.company_id,
                            warehouse_id=doc.warehouse_id,
                            bin_id=line.bin_id,
                            sku=line.sku,
                            product_name=line.product_name,
                            stock_status=line.stock_status or UNRESTRICTED,
                            quantity=Decimal(line.quantity) * sign,
                            movement_type=MOVEMENT_FOR[doc.doc_type],
                            source_line_id=str(line.id),
                            unit_cost=Decimal(line.unit_cost) if line.unit_cost is not None else None,
                            serials=list(line.serials or []),
                            serial_status_after="RETURNED_TO_VENDOR" if doc.doc_type == VENDOR_RETURN_DOC else "WRITTEN_OFF",
                        )
                        for line in doc.lines
                    ],
                ),
            )
        except HTTPException:
            db.rollback()
            raise

        doc.status = POSTED
        doc.approved_by = user_id
        doc.approved_by_name = name
        doc.posted_at = datetime.utcnow()
        db.commit()
        db.refresh(doc)
        return document_dict(doc, lookups(db))

    @staticmethod
    def can_draft(doc_type: str, current_user: dict, db: Session) -> bool:
        return has_permission(current_user, DOCUMENT_PERMISSIONS[doc_type][0], db)


def company_catalogue(company_id: str, search: str | None = None, limit: int = 50) -> list[dict]:
    """Products this company may hold, one per SKU, for the line picker.

    Read from the MongoDB catalogue: the company's own products and the
    shared ones, grouped by model number.
    """

    from app.services.inventory_service import InventoryService

    match: dict = {
        "company_id": {"$in": [company_id, None]},
        "attributes.model_number": {"$nin": [None, ""]},
    }
    if search and search.strip():
        term = {"$regex": re.escape(search.strip()), "$options": "i"}
        match["$or"] = [{"name": term}, {"attributes.model_number": term}]

    rows = InventoryService.items_col.aggregate([
        {"$match": match},
        {"$group": {
            "_id": {"$toUpper": "$attributes.model_number"},
            "name": {"$first": "$name"},
            "unit": {"$first": "$attributes.unit"},
            "serialised": {"$max": {"$cond": [{"$gt": [{"$strLenCP": {"$ifNull": ["$serial_number", ""]}}, 0]}, 1, 0]}},
        }},
        {"$sort": {"name": 1, "_id": 1}},
        {"$limit": max(1, min(limit, 200))},
    ])
    return [
        {"sku": r["_id"], "name": r.get("name"), "unit": r.get("unit"), "has_serials": bool(r.get("serialised"))}
        for r in rows
    ]
