"""Stock reports and the inventory overview.

Every report is limited to the warehouses the caller works in (the same
scope as the Warehouse page) and returns its columns with its rows, so the
screen and the CSV export are drawn from one definition and cannot drift
apart. Money columns appear only for people with ``stock.valuation.view``;
without it they are left out of the rows and the CSV alike.

  stock        on hand in each bucket, by company, warehouse or product
  valuation    quantity x weighted average cost, per company and product
  ledger       every movement in a date range
  slow_moving  stock with no movement out for N days (default 90)
  pending_qc   GRNs received into quarantine and not yet inspected, by age
  in_transit   transfers shipped and not received, by age; overdue after N days
  variances    adjustments, count differences and transfer shortages posted
"""

import csv
import io
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.stock import (
    BLOCKED,
    GRN_QC_PENDING,
    GRN_SUBMITTED,
    IN_TRANSIT,
    QUARANTINE,
    REJECTED,
    SALES_DISPATCH,
    STOCK_ADJUSTMENT_IN,
    STOCK_ADJUSTMENT_OUT,
    TRANSFER_OUT,
    TRF_SHIPPED,
    TRF_SUBMITTED,
    UNRESTRICTED,
    VALUATION_VIEW,
    VENDOR_RETURN,
)
from app.models.stock import StockBalance, StockLedger
from app.services.company_scope_service import CompanyScopeService
from app.services.procurement.common import has_permission
from app.services.stock.queries import ledger_dict, lookups, scope, summary

#: A CSV larger than this is refused with a request to narrow the filters,
#: rather than tying up the server or quietly cutting rows off.
CSV_ROW_LIMIT = 50000

#: Movements that take stock out: what "slow moving" looks for.
OUT_MOVEMENTS = (SALES_DISPATCH, TRANSFER_OUT, STOCK_ADJUSTMENT_OUT, VENDOR_RETURN)

REPORTS = {
    "stock": "Stock by company, warehouse or product",
    "valuation": "Stock valuation (weighted average cost)",
    "ledger": "Stock ledger",
    "slow_moving": "Slow-moving and dead stock",
    "pending_qc": "Goods awaiting quality check",
    "in_transit": "Transfers in transit",
    "variances": "Adjustments, count differences and shortages",
}


def _col(key: str, label: str, kind: str = "text", money: bool = False) -> dict:
    return {"key": key, "label": label, "type": kind, "money": money}


def _num(value) -> float:
    return float(value or 0)


def _ids(current_user: dict, db: Session, company_id, warehouse_id) -> list[UUID] | None:
    """The warehouses a report covers, or None for every one (super admin, no filter)."""

    conditions = scope(StockBalance, current_user, db, company_id, warehouse_id)
    if conditions is None:
        return []
    if warehouse_id:
        return [UUID(str(warehouse_id))]
    allowed = CompanyScopeService.visible_warehouse_ids(current_user, db)
    if company_id:
        from app.models.location import Location

        rows = db.query(Location.id).filter(Location.company_id == UUID(str(company_id))).all()
        ids = [r.id for r in rows]
        return [i for i in ids if allowed is None or str(i) in allowed]
    return None if allowed is None else [UUID(a) for a in allowed]


class StockReportService:

    @staticmethod
    def run(kind: str, current_user: dict, db: Session, *, company_id=None, warehouse_id=None, group_by="product",
            date_from: date | None = None, date_to: date | None = None, days: int | None = None) -> dict:
        if kind not in REPORTS:
            raise HTTPException(status_code=404, detail="Report not found.")
        money = has_permission(current_user, VALUATION_VIEW, db)
        if kind == "valuation" and not money:
            raise HTTPException(status_code=403, detail="Stock valuation needs its own permission.")
        if date_from and date_to and date_from > date_to:
            raise HTTPException(status_code=400, detail="The start date is after the end date.")

        warehouses = _ids(current_user, db, company_id, warehouse_id)
        handler = getattr(StockReportService, f"_{kind}")
        columns, rows = handler(db, warehouses, money=money, group_by=group_by, date_from=date_from,
                                date_to=date_to, days=days)
        if not money:
            hidden = {c["key"] for c in columns if c["money"]}
            columns = [c for c in columns if not c["money"]]
            rows = [{k: v for k, v in row.items() if k not in hidden} for row in rows]
        return {"report": kind, "title": REPORTS[kind], "columns": columns, "rows": rows}

    # ------------------------------------------------------------------
    @staticmethod
    def _balances(db: Session, warehouses):
        query = db.query(StockBalance).filter(StockBalance.qty_on_hand != 0)
        if warehouses is not None:
            if not warehouses:
                return []
            query = query.filter(StockBalance.warehouse_id.in_(warehouses))
        return query.all()

    @staticmethod
    def _stock(db, warehouses, *, money, group_by, **_):
        companies, names, _bins = lookups(db)
        if group_by not in ("company", "warehouse", "product"):
            raise HTTPException(status_code=400, detail="Group by company, warehouse or product.")

        groups: dict[tuple, dict] = {}
        for b in StockReportService._balances(db, warehouses):
            if group_by == "company":
                key = (str(b.company_id),)
                label = {"company": companies.get(str(b.company_id))}
            elif group_by == "warehouse":
                key = (str(b.warehouse_id),)
                wh = names.get(str(b.warehouse_id), (None, None))
                label = {"company": companies.get(str(b.company_id)), "warehouse": wh[0], "warehouse_code": wh[1]}
            else:
                key = (str(b.company_id), b.sku)
                label = {"company": companies.get(str(b.company_id)), "sku": b.sku, "product": b.product_name}
            row = groups.setdefault(key, {**label, "available": 0.0, "reserved": 0.0, "quarantine": 0.0,
                                          "rejected": 0.0, "blocked": 0.0, "in_transit": 0.0, "on_hand": 0.0, "value": 0.0})
            qty = _num(b.qty_on_hand)
            if b.stock_status == UNRESTRICTED:
                row["reserved"] += _num(b.qty_reserved)
                row["available"] += qty - _num(b.qty_reserved)
            else:
                row[b.stock_status.lower()] += qty
            row["on_hand"] += qty
            row["value"] += qty * _num(b.avg_cost)
            if group_by == "product" and b.product_name and not row.get("product"):
                row["product"] = b.product_name

        head = {
            "company": [_col("company", "Company")],
            "warehouse": [_col("company", "Company"), _col("warehouse", "Warehouse"), _col("warehouse_code", "Code")],
            "product": [_col("company", "Company"), _col("sku", "SKU"), _col("product", "Product")],
        }[group_by]
        columns = head + [
            _col("available", "Available", "number"), _col("reserved", "Reserved", "number"),
            _col("quarantine", "Quarantine", "number"), _col("rejected", "Rejected", "number"),
            _col("blocked", "Blocked", "number"), _col("in_transit", "In Transit", "number"),
            _col("on_hand", "Total On Hand", "number"), _col("value", "Value (Rs.)", "money", money=True),
        ]
        rows = sorted(groups.values(), key=lambda r: tuple(str(r.get(c["key"]) or "") for c in head))
        for r in rows:
            r["value"] = round(r["value"], 2)
        return columns, rows

    @staticmethod
    def _valuation(db, warehouses, **_):
        companies, _names, _bins = lookups(db)
        groups: dict[tuple, dict] = {}
        for b in StockReportService._balances(db, warehouses):
            row = groups.setdefault((str(b.company_id), b.sku), {
                "company": companies.get(str(b.company_id)), "sku": b.sku, "product": b.product_name,
                "quantity": 0.0, "value": 0.0, "uncosted": 0.0,
            })
            qty = _num(b.qty_on_hand)
            row["quantity"] += qty
            if b.avg_cost is None:
                row["uncosted"] += qty
            else:
                row["value"] += qty * _num(b.avg_cost)
        rows = []
        for r in sorted(groups.values(), key=lambda r: (r["company"] or "", r["sku"])):
            costed = r["quantity"] - r["uncosted"]
            r["avg_cost"] = round(r["value"] / costed, 2) if costed else None
            r["value"] = round(r["value"], 2)
            rows.append(r)
        columns = [
            _col("company", "Company"), _col("sku", "SKU"), _col("product", "Product"),
            _col("quantity", "Quantity", "number"), _col("avg_cost", "Avg Cost (Rs.)", "money", money=True),
            _col("value", "Value (Rs.)", "money", money=True),
            _col("uncosted", "Qty Without Cost", "number"),
        ]
        return columns, rows

    @staticmethod
    def _ledger(db, warehouses, *, money, date_from, date_to, **_):
        query = db.query(StockLedger)
        if warehouses is not None:
            if not warehouses:
                return StockReportService._ledger_columns(), []
            query = query.filter(StockLedger.warehouse_id.in_(warehouses))
        if date_from:
            query = query.filter(StockLedger.movement_date >= date_from)
        if date_to:
            query = query.filter(StockLedger.movement_date <= date_to)
        if query.count() > CSV_ROW_LIMIT:
            raise HTTPException(status_code=400, detail=f"More than {CSV_ROW_LIMIT:,} movements; narrow the dates or pick a warehouse.")
        names = lookups(db)
        rows = []
        for row in query.order_by(StockLedger.id.asc()).all():
            d = ledger_dict(row, names)
            d["value"] = round(d["quantity"] * (d["unit_cost"] or 0), 2)
            rows.append(d)
        return StockReportService._ledger_columns(), rows

    @staticmethod
    def _ledger_columns():
        return [
            _col("movement_date", "Date", "date"), _col("posted_at", "Posted At", "datetime"),
            _col("company_name", "Company"), _col("warehouse_name", "Warehouse"), _col("bin_code", "Bin"),
            _col("sku", "SKU"), _col("product_name", "Product"), _col("serial_no", "Serial"),
            _col("movement_type", "Movement"), _col("stock_status", "Status"), _col("quantity", "Qty", "number"),
            _col("unit_cost", "Unit Cost (Rs.)", "money", money=True), _col("value", "Value (Rs.)", "money", money=True),
            _col("source_doc_number", "Document"), _col("created_by_name", "Prepared By"),
            _col("approved_by_name", "Posted By"), _col("remarks", "Remarks"),
        ]

    @staticmethod
    def _slow_moving(db, warehouses, *, days, **_):
        days = days or 90
        if days < 1 or days > 3650:
            raise HTTPException(status_code=400, detail="Choose between 1 and 3650 days.")
        cutoff = date.today() - timedelta(days=days)
        companies, names, _bins = lookups(db)

        last_out = db.query(
            StockLedger.warehouse_id, StockLedger.sku, func.max(StockLedger.movement_date)
        ).filter(StockLedger.movement_type.in_(OUT_MOVEMENTS), StockLedger.quantity < 0)
        last_in = db.query(
            StockLedger.warehouse_id, StockLedger.sku, func.min(StockLedger.movement_date)
        ).filter(StockLedger.quantity > 0)
        if warehouses is not None:
            last_out = last_out.filter(StockLedger.warehouse_id.in_(warehouses or [UUID(int=0)]))
            last_in = last_in.filter(StockLedger.warehouse_id.in_(warehouses or [UUID(int=0)]))
        outs = {(str(w), s): d for w, s, d in last_out.group_by(StockLedger.warehouse_id, StockLedger.sku).all()}
        ins = {(str(w), s): d for w, s, d in last_in.group_by(StockLedger.warehouse_id, StockLedger.sku).all()}

        held: dict[tuple, dict] = {}
        for b in StockReportService._balances(db, warehouses):
            if b.stock_status not in (UNRESTRICTED, BLOCKED, REJECTED, QUARANTINE):
                continue
            key = (str(b.warehouse_id), b.sku)
            wh = names.get(str(b.warehouse_id), (None, None))
            row = held.setdefault(key, {"company": companies.get(str(b.company_id)), "warehouse": wh[0], "sku": b.sku,
                                        "product": b.product_name, "on_hand": 0.0, "value": 0.0})
            row["on_hand"] += _num(b.qty_on_hand)
            row["value"] += _num(b.qty_on_hand) * _num(b.avg_cost)

        rows = []
        for key, row in held.items():
            last = outs.get(key)
            if last and last > cutoff:
                continue
            since = last or ins.get(key)
            row["last_out"] = last.isoformat() if last else None
            row["days_idle"] = (date.today() - since).days if since else None
            row["state"] = "Dead (never went out)" if not last else "Slow moving"
            row["value"] = round(row["value"], 2)
            rows.append(row)
        rows.sort(key=lambda r: -(r["days_idle"] or 0))
        columns = [
            _col("company", "Company"), _col("warehouse", "Warehouse"), _col("sku", "SKU"), _col("product", "Product"),
            _col("on_hand", "On Hand", "number"), _col("value", "Value (Rs.)", "money", money=True),
            _col("last_out", "Last Out", "date"), _col("days_idle", "Days Idle", "number"), _col("state", "State"),
        ]
        return columns, rows

    @staticmethod
    def _pending_qc(db, warehouses, **_):
        from app.models.goods_receipt import GoodsReceipt

        companies, names, _bins = lookups(db)
        query = db.query(GoodsReceipt).filter(GoodsReceipt.status.in_((GRN_SUBMITTED, GRN_QC_PENDING)))
        if warehouses is not None:
            query = query.filter(GoodsReceipt.warehouse_id.in_(warehouses or [UUID(int=0)]))
        now = datetime.utcnow()
        rows = []
        for g in query.order_by(GoodsReceipt.id.asc()).all():
            since = g.receipt_posted_at or g.submitted_at or g.created_at
            rows.append({
                "grn_number": g.grn_number, "id": g.id, "company": companies.get(str(g.company_id)),
                "warehouse": names.get(str(g.warehouse_id), (None, None))[0], "supplier": g.supplier_name,
                "type": g.receipt_type, "stage": "In quarantine, awaiting QC" if g.status == GRN_QC_PENDING else "Awaiting receipt posting",
                "units": sum(_num(line.qty_received) for line in g.lines),
                "waiting_days": (now - since).days if since else None,
            })
        rows.sort(key=lambda r: -(r["waiting_days"] or 0))
        columns = [
            _col("grn_number", "GRN"), _col("company", "Company"), _col("warehouse", "Warehouse"),
            _col("supplier", "Received From"), _col("type", "Type"), _col("stage", "Stage"),
            _col("units", "Units", "number"), _col("waiting_days", "Days Waiting", "number"),
        ]
        return columns, rows

    @staticmethod
    def _in_transit(db, warehouses, *, days, **_):
        from app.models.stock_transfer import StockTransfer

        overdue_after = days or 7
        companies, names, _bins = lookups(db)
        query = db.query(StockTransfer).filter(StockTransfer.status.in_((TRF_SUBMITTED, TRF_SHIPPED)))
        if warehouses is not None:
            ids = warehouses or [UUID(int=0)]
            query = query.filter(or_(StockTransfer.from_warehouse_id.in_(ids), StockTransfer.to_warehouse_id.in_(ids)))
        now = datetime.utcnow()
        rows = []
        for t in query.order_by(StockTransfer.id.asc()).all():
            since = t.shipped_at if t.status == TRF_SHIPPED else t.submitted_at
            age = (now - since).days if since else None
            rows.append({
                "transfer_number": t.transfer_number, "id": t.id, "company": companies.get(str(t.company_id)),
                "from": names.get(str(t.from_warehouse_id), (None, None))[0],
                "to": names.get(str(t.to_warehouse_id), (None, None))[0],
                "stage": "In transit" if t.status == TRF_SHIPPED else "Awaiting approval",
                "units": sum(_num(line.qty) for line in t.lines),
                "days": age,
                "overdue": "Yes" if t.status == TRF_SHIPPED and age is not None and age > overdue_after else "",
                "vehicle": t.vehicle_number or t.docket_no,
            })
        rows.sort(key=lambda r: -(r["days"] or 0))
        columns = [
            _col("transfer_number", "Transfer"), _col("company", "Company"), _col("from", "From"), _col("to", "To"),
            _col("stage", "Stage"), _col("units", "Units", "number"), _col("days", "Days", "number"),
            _col("overdue", f"Overdue (> {overdue_after} days)"), _col("vehicle", "Vehicle / Docket"),
        ]
        return columns, rows

    @staticmethod
    def _variances(db, warehouses, *, date_from, date_to, **_):
        names = lookups(db)
        query = db.query(StockLedger).filter(StockLedger.movement_type.in_((STOCK_ADJUSTMENT_IN, STOCK_ADJUSTMENT_OUT)))
        if warehouses is not None:
            query = query.filter(StockLedger.warehouse_id.in_(warehouses or [UUID(int=0)]))
        if date_from:
            query = query.filter(StockLedger.movement_date >= date_from)
        if date_to:
            query = query.filter(StockLedger.movement_date <= date_to)
        source = {"STOCK_DOCUMENT": "Stock adjustment", "STOCK_COUNT": "Stock count",
                  "STOCK_TRANSFER_IN": "Transfer shortage"}
        rows = []
        for row in query.order_by(StockLedger.movement_date.desc(), StockLedger.id.desc()).limit(CSV_ROW_LIMIT).all():
            d = ledger_dict(row, names)
            rows.append({
                "movement_date": d["movement_date"], "company": d["company_name"], "warehouse": d["warehouse_name"],
                "source": source.get(d["source_doc_type"], d["source_doc_type"]), "document": d["source_doc_number"],
                "sku": d["sku"], "product": d["product_name"], "quantity": d["quantity"],
                "value": round(d["quantity"] * (d["unit_cost"] or 0), 2),
                "approved_by": d["approved_by_name"], "remarks": d["remarks"],
            })
        columns = [
            _col("movement_date", "Date", "date"), _col("company", "Company"), _col("warehouse", "Warehouse"),
            _col("source", "From"), _col("document", "Document"), _col("sku", "SKU"), _col("product", "Product"),
            _col("quantity", "Qty (+ in / - out)", "number"), _col("value", "Value (Rs.)", "money", money=True),
            _col("approved_by", "Approved By"), _col("remarks", "Reason"),
        ]
        return columns, rows

    # ------------------------------------------------------------------
    @staticmethod
    def to_csv(result: dict) -> str:
        if len(result["rows"]) > CSV_ROW_LIMIT:
            raise HTTPException(status_code=400, detail=f"More than {CSV_ROW_LIMIT:,} rows; narrow the filters.")
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow([c["label"] for c in result["columns"]])
        for row in result["rows"]:
            writer.writerow([_cell(row.get(c["key"])) for c in result["columns"]])
        # A byte-order mark so Excel opens rupee signs and Hindi names correctly.
        return "﻿" + out.getvalue()

    # ------------------------------------------------------------------
    @staticmethod
    def overview(current_user: dict, db: Session, *, company_id=None, warehouse_id=None) -> dict:
        """The Inventory Overview: buckets, value, work waiting, recent moves."""

        from app.models.dispatch import DispatchOrder
        from app.models.goods_receipt import GoodsReceipt
        from app.models.stock import StockDocument
        from app.models.stock_transfer import StockCount, StockTransfer

        buckets = summary(current_user, db, company_id=company_id, warehouse_id=warehouse_id)
        warehouses = _ids(current_user, db, company_id, warehouse_id)

        def within(query, column, *more):
            if warehouses is None:
                return query
            ids = warehouses or [UUID(int=0)]
            return query.filter(or_(column.in_(ids), *[m.in_(ids) for m in more]))

        def count(model, column, statuses, *more):
            return within(db.query(func.count(model.id)).filter(model.status.in_(statuses)), column, *more).scalar() or 0

        money = has_permission(current_user, VALUATION_VIEW, db)
        value = None
        if money:
            value_query = db.query(func.coalesce(func.sum(StockBalance.qty_on_hand * func.coalesce(StockBalance.avg_cost, 0)), 0))
            value = round(_num(within(value_query, StockBalance.warehouse_id).scalar()), 2)

        pending = {
            "grn_to_post": count(GoodsReceipt, GoodsReceipt.warehouse_id, (GRN_SUBMITTED,)),
            "qc_pending": count(GoodsReceipt, GoodsReceipt.warehouse_id, (GRN_QC_PENDING,)),
            "dispatch_open": count(DispatchOrder, DispatchOrder.warehouse_id, ("DRAFT", "PICKED", "PACKED")),
            "pod_pending": count(DispatchOrder, DispatchOrder.warehouse_id, ("DISPATCHED",)),
            "transfers_to_approve": count(StockTransfer, StockTransfer.from_warehouse_id, (TRF_SUBMITTED,)),
            "transfers_in_transit": count(StockTransfer, StockTransfer.from_warehouse_id, (TRF_SHIPPED,), StockTransfer.to_warehouse_id),
            "counts_to_approve": count(StockCount, StockCount.warehouse_id, ("SUBMITTED",)),
            "documents_to_approve": count(StockDocument, StockDocument.warehouse_id, ("SUBMITTED",)),
        }

        recent_query = within(db.query(StockLedger), StockLedger.warehouse_id).order_by(StockLedger.id.desc()).limit(8)
        names = lookups(db)
        recent = [ledger_dict(r, names) for r in recent_query.all()]
        if not money:
            for r in recent:
                r.pop("unit_cost", None)

        return {"buckets": buckets, "value": value, "pending": pending, "recent": recent,
                "in_transit_units": buckets.get(IN_TRANSIT.lower(), 0)}


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))
    if isinstance(value, Decimal):
        return str(value.normalize())
    return value
