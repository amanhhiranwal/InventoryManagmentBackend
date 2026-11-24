"""Reading stock: balances, the ledger, totals, and reconciliation.

Everything here is limited to the warehouses the caller may work in
(CompanyScopeService.visible_warehouse_ids); a super admin sees all.
Asking for a company or warehouse outside that is refused, not quietly
emptied.
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.stock import UNRESTRICTED
from app.models.location import Location
from app.models.stock import StockBalance, StockLedger
from app.models.warehouse import WarehouseBin
from app.services.company_scope_service import CompanyScopeService


def _num(value) -> float:
    return float(value or 0)


def scope(model, current_user: dict, db: Session, company_id: str | None, warehouse_id: str | None):
    """The filters that keep a query inside what the caller may see.

    Returns a list of SQLAlchemy conditions, or None when the caller can
    see nothing at all.
    """

    conditions = []

    if company_id:
        CompanyScopeService.assert_company_allowed(current_user, company_id, db)
        conditions.append(model.company_id == UUID(str(company_id)))

    if warehouse_id:
        CompanyScopeService.assert_warehouse_allowed(current_user, warehouse_id, db)
        conditions.append(model.warehouse_id == UUID(str(warehouse_id)))

    allowed = CompanyScopeService.visible_warehouse_ids(current_user, db)
    if allowed is not None:
        if not allowed:
            return None
        conditions.append(model.warehouse_id.in_([UUID(a) for a in allowed]))

    return conditions


def lookups(db: Session) -> tuple[dict, dict, dict]:
    companies = CompanyScopeService.company_names(db)
    warehouses = {str(w.id): (w.location_name, w.location_code) for w in db.query(Location).all()}
    bins = {str(b.id): b.code for b in db.query(WarehouseBin.id, WarehouseBin.code).all()}
    return companies, warehouses, bins


def balance_dict(row: StockBalance, names) -> dict:
    companies, warehouses, bins = names
    on_hand = Decimal(row.qty_on_hand or 0)
    reserved = Decimal(row.qty_reserved or 0)
    warehouse = warehouses.get(str(row.warehouse_id), (None, None))
    return {
        "id": row.id,
        "company_id": str(row.company_id),
        "company_name": companies.get(str(row.company_id)),
        "warehouse_id": str(row.warehouse_id),
        "warehouse_name": warehouse[0],
        "warehouse_code": warehouse[1],
        "bin_id": str(row.bin_id),
        "bin_code": bins.get(str(row.bin_id)),
        "sku": row.sku,
        "product_name": row.product_name,
        "stock_status": row.stock_status,
        "qty_on_hand": float(on_hand),
        "qty_reserved": float(reserved),
        # Only unrestricted stock can be promised; the rest is held back.
        "qty_available": float(on_hand - reserved) if row.stock_status == UNRESTRICTED else 0.0,
        "avg_cost": float(row.avg_cost) if row.avg_cost is not None else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_balances(
    current_user: dict,
    db: Session,
    *,
    company_id: str | None = None,
    warehouse_id: str | None = None,
    status: str | None = None,
    search: str | None = None,
    include_zero: bool = False,
    page: int = 1,
    size: int = 20,
) -> dict:
    conditions = scope(StockBalance, current_user, db, company_id, warehouse_id)
    if conditions is None:
        return {"data": [], "total": 0}

    query = db.query(StockBalance).filter(*conditions)
    if status:
        query = query.filter(StockBalance.stock_status == status.upper())
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(or_(StockBalance.sku.ilike(term), StockBalance.product_name.ilike(term)))
    if not include_zero:
        query = query.filter(StockBalance.qty_on_hand > 0)

    total = query.count()
    rows = (
        query.order_by(StockBalance.sku.asc(), StockBalance.warehouse_id, StockBalance.stock_status)
        .offset((max(page, 1) - 1) * size)
        .limit(size)
        .all()
    )
    names = lookups(db)
    return {"data": [balance_dict(r, names) for r in rows], "total": total}


def summary(current_user: dict, db: Session, *, company_id: str | None = None, warehouse_id: str | None = None) -> dict:
    """On hand, reserved, available, quarantine, rejected and in transit."""

    empty = {
        "skus": 0, "on_hand": 0.0, "reserved": 0.0, "available": 0.0,
        "quarantine": 0.0, "rejected": 0.0, "blocked": 0.0, "in_transit": 0.0,
    }
    conditions = scope(StockBalance, current_user, db, company_id, warehouse_id)
    if conditions is None:
        return empty

    rows = (
        db.query(
            StockBalance.stock_status,
            func.coalesce(func.sum(StockBalance.qty_on_hand), 0),
            func.coalesce(func.sum(StockBalance.qty_reserved), 0),
        )
        .filter(*conditions)
        .group_by(StockBalance.stock_status)
        .all()
    )

    result = dict(empty)
    for status, on_hand, reserved in rows:
        if status == UNRESTRICTED:
            result["on_hand"] = _num(on_hand)
            result["reserved"] = _num(reserved)
            result["available"] = _num(on_hand) - _num(reserved)
        else:
            result[status.lower()] = _num(on_hand)

    result["skus"] = (
        db.query(func.count(func.distinct(StockBalance.sku)))
        .filter(*conditions, StockBalance.qty_on_hand > 0)
        .scalar()
        or 0
    )
    return result


def ledger_dict(row: StockLedger, names) -> dict:
    companies, warehouses, bins = names
    warehouse = warehouses.get(str(row.warehouse_id), (None, None))
    return {
        "id": row.id,
        "company_id": str(row.company_id),
        "company_name": companies.get(str(row.company_id)),
        "warehouse_id": str(row.warehouse_id),
        "warehouse_name": warehouse[0],
        "bin_code": bins.get(str(row.bin_id)),
        "sku": row.sku,
        "product_name": row.product_name,
        "serial_no": row.serial_no,
        "movement_type": row.movement_type,
        "stock_status": row.stock_status,
        "quantity": float(row.quantity),
        "unit_cost": float(row.unit_cost) if row.unit_cost is not None else None,
        "source_doc_type": row.source_doc_type,
        "source_doc_id": row.source_doc_id,
        "source_doc_number": row.source_doc_number,
        "movement_date": row.movement_date.isoformat() if row.movement_date else None,
        "posted_at": row.posted_at.isoformat() if row.posted_at else None,
        "created_by_name": row.created_by_name,
        "approved_by_name": row.approved_by_name,
        "reversal_of_movement_id": row.reversal_of_movement_id,
        "remarks": row.remarks,
    }


def list_ledger(
    current_user: dict,
    db: Session,
    *,
    company_id: str | None = None,
    warehouse_id: str | None = None,
    movement_type: str | None = None,
    search: str | None = None,
    date_from=None,
    date_to=None,
    page: int = 1,
    size: int = 20,
) -> dict:
    conditions = scope(StockLedger, current_user, db, company_id, warehouse_id)
    if conditions is None:
        return {"data": [], "total": 0}

    query = db.query(StockLedger).filter(*conditions)
    if movement_type:
        query = query.filter(StockLedger.movement_type == movement_type.upper())
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                StockLedger.sku.ilike(term),
                StockLedger.product_name.ilike(term),
                StockLedger.serial_no.ilike(term),
                StockLedger.source_doc_number.ilike(term),
            )
        )
    if date_from:
        query = query.filter(StockLedger.movement_date >= date_from)
    if date_to:
        query = query.filter(StockLedger.movement_date <= date_to)

    total = query.count()
    rows = (
        query.order_by(StockLedger.id.desc())
        .offset((max(page, 1) - 1) * size)
        .limit(size)
        .all()
    )
    names = lookups(db)
    return {"data": [ledger_dict(r, names) for r in rows], "total": total}


def reconcile(db: Session) -> list[dict]:
    """Every balance that differs from what its ledger adds up to.

    The ledger is the record of truth, so an empty list means the balances
    can be trusted. Anything listed is a bug or a hand edit to look into;
    this reports it and changes nothing.
    """

    key = (
        StockLedger.company_id,
        StockLedger.warehouse_id,
        StockLedger.bin_id,
        StockLedger.sku,
        StockLedger.stock_status,
    )
    from_ledger = {
        tuple(str(v) for v in row[:5]): Decimal(row[5])
        for row in db.query(*key, func.sum(StockLedger.quantity)).group_by(*key).all()
    }
    held = {
        (str(b.company_id), str(b.warehouse_id), str(b.bin_id), b.sku, b.stock_status): Decimal(b.qty_on_hand or 0)
        for b in db.query(StockBalance).all()
    }

    mismatches = []
    for k in sorted(set(from_ledger) | set(held)):
        ledger_total = from_ledger.get(k, Decimal(0))
        balance = held.get(k, Decimal(0))
        if ledger_total != balance:
            mismatches.append({
                "company_id": k[0],
                "warehouse_id": k[1],
                "bin_id": k[2],
                "sku": k[3],
                "stock_status": k[4],
                "ledger_total": float(ledger_total),
                "balance_on_hand": float(balance),
            })
    return mismatches
