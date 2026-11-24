"""Cycle counts: count what is on the shelf and post the differences.

  OPEN       started: every available (UNRESTRICTED) balance in the
             warehouse's store bins, or one bin, is copied as the system
             quantity. The counter enters what is really there, and can
             add products found that the system did not list.
  SUBMITTED  every line counted. No stock has moved.
  POSTED     approved by someone other than the counter: each difference
             is posted to the ledger as STOCK_ADJUSTMENT_IN or _OUT with
             reason COUNT_VARIANCE, in the same transaction.
  CANCELLED  before posting.

Approval is refused if stock on a counted line has moved since the count
started - the count no longer describes the shelf - and for differences
on serial-numbered products, which have to be adjusted naming the serials.
Above the finance value limit Finance must approve, as for adjustments.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.stock import (
    COUNT_CANCELLED,
    COUNT_OPEN,
    COUNT_POSTED,
    COUNT_SUBMITTED,
    FINANCE_ADJUSTMENT,
    STOCK_ADJUSTMENT_IN,
    STOCK_ADJUSTMENT_OUT,
    UNRESTRICTED,
)
from app.core.timezone import now_ist
from app.models.company import Company
from app.models.stock import StockBalance, StockSerial
from app.models.stock_transfer import StockCount, StockCountLine
from app.models.warehouse import WarehouseBin
from app.services.company_scope_service import CompanyScopeService
from app.services.procurement.common import actor, has_permission, next_reference
from app.services.stock import posting
from app.services.stock.documents import catalogue_product, finance_threshold
from app.services.stock.queries import lookups

SOURCE = "STOCK_COUNT"


def count_dict(c: StockCount, names=None) -> dict:
    companies, warehouses, bins = names or ({}, {}, {})
    warehouse = warehouses.get(str(c.warehouse_id), (None, None))
    lines = []
    for line in c.lines:
        system = Decimal(line.qty_system)
        counted = Decimal(line.qty_counted) if line.qty_counted is not None else None
        variance = counted - system if counted is not None else None
        lines.append({
            "id": line.id,
            "line_no": line.line_no,
            "bin_id": str(line.bin_id),
            "bin_code": bins.get(str(line.bin_id)),
            "sku": line.sku,
            "product_name": line.product_name,
            "qty_system": float(system),
            "qty_counted": float(counted) if counted is not None else None,
            "variance": float(variance) if variance is not None else None,
            "unit_cost": float(line.unit_cost) if line.unit_cost is not None else None,
            "variance_value": float(variance * Decimal(line.unit_cost or 0)) if variance is not None else None,
            "remarks": line.remarks,
        })
    return {
        "id": c.id,
        "count_number": c.count_number,
        "company_id": str(c.company_id),
        "company_name": companies.get(str(c.company_id)),
        "warehouse_id": str(c.warehouse_id),
        "warehouse_name": warehouse[0],
        "warehouse_code": warehouse[1],
        "bin_id": str(c.bin_id) if c.bin_id else None,
        "bin_code": bins.get(str(c.bin_id)) if c.bin_id else None,
        "status": c.status,
        "count_date": c.count_date.isoformat() if c.count_date else None,
        "remarks": c.remarks,
        "created_by": str(c.created_by) if c.created_by else None,
        "created_by_name": c.created_by_name,
        "submitted_by": str(c.submitted_by) if c.submitted_by else None,
        "submitted_by_name": c.submitted_by_name,
        "submitted_at": c.submitted_at.isoformat() if c.submitted_at else None,
        "approved_by_name": c.approved_by_name,
        "posted_at": c.posted_at.isoformat() if c.posted_at else None,
        "cancelled_reason": c.cancelled_reason,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "lines": lines,
        "lines_counted": sum(1 for line in lines if line["qty_counted"] is not None),
        "lines_with_variance": sum(1 for line in lines if line["variance"]),
        "variance_value": sum(line["variance_value"] or 0 for line in lines),
    }


class StockCountService:

    @staticmethod
    def _get(count_id: int, current_user: dict, db: Session, lock: bool = False) -> StockCount:
        query = db.query(StockCount).filter(StockCount.id == count_id)
        if lock:
            query = query.with_for_update()
        c = query.first()
        if c is None:
            raise HTTPException(status_code=404, detail="Stock count not found.")
        CompanyScopeService.assert_warehouse_allowed(current_user, c.warehouse_id, db)
        return c

    @staticmethod
    def get(count_id: int, current_user: dict, db: Session) -> dict:
        return count_dict(StockCountService._get(count_id, current_user, db), lookups(db))

    @staticmethod
    def list_counts(current_user: dict, db: Session, *, company_id=None, warehouse_id=None, status=None,
                    search=None, page: int = 1, size: int = 20) -> dict:
        from app.services.stock.queries import scope

        conditions = scope(StockCount, current_user, db, company_id, warehouse_id)
        if conditions is None:
            return {"data": [], "total": 0, "counts": {}}
        query = db.query(StockCount).filter(*conditions)
        if search and search.strip():
            query = query.filter(StockCount.count_number.ilike(f"%{search.strip()}%"))
        counts = {s: n for s, n in query.with_entities(StockCount.status, func.count()).group_by(StockCount.status).all()}
        if status:
            query = query.filter(StockCount.status == status.upper())

        total = query.count()
        rows = query.order_by(StockCount.id.desc()).offset((max(page, 1) - 1) * size).limit(size).all()
        names = lookups(db)
        return {"data": [count_dict(r, names) for r in rows], "total": total, "counts": counts}

    @staticmethod
    def create(payload, current_user: dict, db: Session) -> dict:
        warehouse = CompanyScopeService.assert_warehouse_allowed(current_user, payload.warehouse_id, db)
        bins = {str(b.id): b for b in db.query(WarehouseBin).filter(WarehouseBin.warehouse_id == warehouse.id).all()}
        only_bin = None
        if payload.bin_id:
            only_bin = bins.get(str(payload.bin_id))
            if only_bin is None or only_bin.bin_type != "STORE":
                raise HTTPException(status_code=400, detail="Choose a store bin of this warehouse.")
        store_ids = [b.id for b in bins.values() if b.bin_type == "STORE"]

        query = db.query(StockBalance).filter(
            StockBalance.warehouse_id == warehouse.id,
            StockBalance.stock_status == UNRESTRICTED,
            StockBalance.qty_on_hand != 0,
            StockBalance.bin_id.in_([only_bin.id] if only_bin else store_ids),
        )
        balances = query.order_by(StockBalance.bin_id, StockBalance.sku).all()

        company = db.query(Company).filter(Company.id == warehouse.company_id).first()
        user_id, name = actor(current_user, db)
        c = StockCount(
            count_number=next_reference(db, StockCount, "count_number", f"{(company.company_code or 'CO').upper()}-CNT"),
            company_id=warehouse.company_id,
            warehouse_id=warehouse.id,
            bin_id=only_bin.id if only_bin else None,
            status=COUNT_OPEN,
            count_date=payload.count_date or now_ist().date(),
            remarks=(payload.remarks or "").strip() or None,
            created_by=user_id,
            created_by_name=name,
        )
        c.lines.extend(
            StockCountLine(line_no=i, bin_id=b.bin_id, sku=b.sku, product_name=b.product_name,
                           qty_system=b.qty_on_hand, unit_cost=b.avg_cost)
            for i, b in enumerate(balances, start=1)
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        return count_dict(c, lookups(db))

    @staticmethod
    def record(count_id: int, payload, current_user: dict, db: Session) -> dict:
        """Enter counted quantities, and add products found that the system did not list."""

        c = StockCountService._get(count_id, current_user, db, lock=True)
        if c.status != COUNT_OPEN:
            raise HTTPException(status_code=409, detail=f"{c.count_number} is {c.status.lower()}; counts can no longer be entered.")
        by_id = {line.id: line for line in c.lines}
        for entry in payload.lines:
            line = by_id.get(entry.line_id)
            if line is None:
                raise HTTPException(status_code=400, detail="A line is not on this count.")
            line.qty_counted = StockCountService._qty(entry.qty_counted, f"Line {line.line_no}")
            if entry.remarks is not None:
                line.remarks = entry.remarks.strip() or None

        if payload.found:
            bins = {str(b.id): b for b in db.query(WarehouseBin).filter(WarehouseBin.warehouse_id == c.warehouse_id).all()}
            have = {(str(line.bin_id), line.sku) for line in c.lines}
            next_no = max((line.line_no for line in c.lines), default=0)
            for entry in payload.found:
                sku = (entry.sku or "").strip().upper()
                product = catalogue_product(sku, str(c.company_id)) if sku else None
                if product is None:
                    raise HTTPException(status_code=400, detail=f"{sku or 'A found product'} is not in this company's product catalogue.")
                bin_row = bins.get(str(entry.bin_id)) if entry.bin_id else (bins.get(str(c.bin_id)) if c.bin_id else None)
                if bin_row is None or bin_row.bin_type != "STORE" or (c.bin_id and bin_row.id != c.bin_id):
                    raise HTTPException(status_code=400, detail=f"{sku}: choose the store bin it was found in.")
                if (str(bin_row.id), sku) in have:
                    raise HTTPException(status_code=400, detail=f"{sku} in bin {bin_row.code} is already on this count.")
                have.add((str(bin_row.id), sku))
                current = db.query(StockBalance).filter(
                    StockBalance.warehouse_id == c.warehouse_id, StockBalance.bin_id == bin_row.id,
                    StockBalance.sku == sku, StockBalance.stock_status == UNRESTRICTED).first()
                next_no += 1
                c.lines.append(StockCountLine(
                    line_no=next_no, bin_id=bin_row.id, sku=sku, product_name=product.get("name"),
                    qty_system=current.qty_on_hand if current else 0,
                    qty_counted=StockCountService._qty(entry.qty_counted, sku),
                    unit_cost=current.avg_cost if current else None,
                    remarks=(entry.remarks or "").strip() or None,
                ))
        db.commit()
        db.refresh(c)
        return count_dict(c, lookups(db))

    @staticmethod
    def _qty(value, label: str) -> Decimal:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError):
            raise HTTPException(status_code=400, detail=f"{label}: enter the counted quantity.")
        if number < 0:
            raise HTTPException(status_code=400, detail=f"{label}: a count cannot be negative.")
        return number

    @staticmethod
    def submit(count_id: int, current_user: dict, db: Session) -> dict:
        c = StockCountService._get(count_id, current_user, db, lock=True)
        if c.status != COUNT_OPEN:
            raise HTTPException(status_code=409, detail=f"{c.count_number} has already been submitted.")
        if not c.lines:
            raise HTTPException(status_code=400, detail="There is nothing on this count.")
        missing = [str(line.line_no) for line in c.lines if line.qty_counted is None]
        if missing:
            raise HTTPException(status_code=400, detail=f"Count line(s) {', '.join(missing[:10])} first.")
        user_id, name = actor(current_user, db)
        c.status = COUNT_SUBMITTED
        c.submitted_by = user_id
        c.submitted_by_name = name
        c.submitted_at = datetime.utcnow()
        db.commit()
        db.refresh(c)
        return count_dict(c, lookups(db))

    @staticmethod
    def cancel(count_id: int, reason: str, current_user: dict, db: Session) -> dict:
        c = StockCountService._get(count_id, current_user, db, lock=True)
        if c.status not in (COUNT_OPEN, COUNT_SUBMITTED):
            raise HTTPException(status_code=409, detail=f"{c.count_number} is {c.status.lower()} and cannot be cancelled.")
        if not (reason or "").strip():
            raise HTTPException(status_code=400, detail="Say why it is cancelled.")
        c.status = COUNT_CANCELLED
        c.cancelled_reason = reason.strip()
        db.commit()
        db.refresh(c)
        return count_dict(c, lookups(db))

    @staticmethod
    def approve(count_id: int, current_user: dict, db: Session) -> dict:
        c = StockCountService._get(count_id, current_user, db, lock=True)
        if c.status == COUNT_POSTED:
            raise HTTPException(status_code=409, detail=f"{c.count_number} has already been posted.")
        if c.status != COUNT_SUBMITTED:
            raise HTTPException(status_code=409, detail="Submit the count before approving it.")
        user_id, name = actor(current_user, db)
        if user_id and str(user_id) in {str(c.submitted_by or ""), str(c.created_by or "")}:
            raise HTTPException(status_code=403, detail="Someone other than the person who counted has to approve it.")

        changed = [line for line in c.lines if Decimal(line.qty_counted) != Decimal(line.qty_system)]

        # The count describes the shelf only if nothing has moved since. The
        # rows are locked, so nothing can move between this check and posting.
        moved = []
        for line in sorted(c.lines, key=lambda x: (str(x.bin_id), x.sku)):
            row = db.query(StockBalance).filter(
                StockBalance.company_id == c.company_id, StockBalance.warehouse_id == c.warehouse_id,
                StockBalance.bin_id == line.bin_id, StockBalance.sku == line.sku,
                StockBalance.stock_status == UNRESTRICTED).with_for_update().populate_existing().first()
            if Decimal(row.qty_on_hand if row else 0) != Decimal(line.qty_system):
                moved.append(line.sku)
        if moved:
            raise HTTPException(
                status_code=409,
                detail=f"Stock of {', '.join(sorted(set(moved))[:5])} has moved since the count started. Cancel it and count again.",
            )

        serialised = {
            sku for (sku,) in db.query(StockSerial.sku).filter(
                StockSerial.company_id == c.company_id, StockSerial.sku.in_([line.sku for line in changed])).distinct()
        } if changed else set()
        if serialised:
            raise HTTPException(
                status_code=409,
                detail=f"{', '.join(sorted(serialised))} is serial-numbered; adjust it with a stock adjustment naming the serials.",
            )

        value = sum((abs(Decimal(line.qty_counted) - Decimal(line.qty_system)) * Decimal(line.unit_cost or 0) for line in changed), Decimal(0))
        if value >= finance_threshold(db) and not has_permission(current_user, FINANCE_ADJUSTMENT, db):
            raise HTTPException(
                status_code=403,
                detail=f"The differences are worth Rs. {value.normalize()}, at or above the finance limit; Finance must approve.",
            )

        if changed:
            movements = []
            for line in changed:
                diff = Decimal(line.qty_counted) - Decimal(line.qty_system)
                movements.append(posting.Movement(
                    company_id=c.company_id, warehouse_id=c.warehouse_id, bin_id=line.bin_id, sku=line.sku,
                    product_name=line.product_name, stock_status=UNRESTRICTED, quantity=diff,
                    movement_type=STOCK_ADJUSTMENT_IN if diff > 0 else STOCK_ADJUSTMENT_OUT,
                    source_line_id=str(line.id), unit_cost=Decimal(line.unit_cost) if line.unit_cost is not None else None,
                    serial_status_after="WRITTEN_OFF",
                ))
            try:
                posting.post(db, posting.Posting(
                    source_doc_type=SOURCE, source_doc_id=str(c.id), source_doc_number=c.count_number,
                    movement_date=now_ist().date(), movements=movements,
                    created_by=str(c.submitted_by or c.created_by) if (c.submitted_by or c.created_by) else None,
                    created_by_name=c.submitted_by_name or c.created_by_name,
                    approved_by=str(user_id) if user_id else None, approved_by_name=name,
                    remarks=f"Stock count {c.count_number} (COUNT_VARIANCE)",
                ))
            except HTTPException:
                db.rollback()
                raise

        c.status = COUNT_POSTED
        c.approved_by = user_id
        c.approved_by_name = name
        c.posted_at = datetime.utcnow()
        db.commit()
        db.refresh(c)
        return count_dict(c, lookups(db))
