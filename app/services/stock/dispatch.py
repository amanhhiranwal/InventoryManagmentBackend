"""Stock out: availability, reservations and dispatch orders.

    Sales order          creates demand; moves no stock.
    Reserve              holds available stock in one warehouse for its
                         lines (qty_reserved up, on hand unchanged). Fixes
                         the order's selling company and dispatch warehouse.
    Dispatch order       part or all of the order from that warehouse.
      DRAFT              lines and quantities, within what is reserved.
      PICKED             serials allocated (status PICKED); no stock moves.
      PACKED             packages and weight; no stock moves.
      DISPATCHED         commit: SALES_DISPATCH out of the ledger, once,
                         consuming the reservation; serials DISPATCHED.
      DELIVERED          proof of delivery; no stock moves.
      CANCELLED          before commit only; picked serials go back.

Everything is limited to the warehouses the caller works in. A dispatch
order cannot take more than is reserved for a line, and a serial cannot
be picked twice.
"""

from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.stock import (
    DO_CANCELLED,
    DO_DELIVERED,
    DO_DISPATCHED,
    DO_DRAFT,
    DO_PACKED,
    DO_PICKED,
    EWAY_BILL_DEFAULT,
    EWAY_BILL_SETTING,
    NON_STOCK_SKU_PREFIXES,
    RES_ACTIVE,
    RES_CONSUMED,
    RES_RELEASED,
    SALES_DISPATCH,
    SERIAL_DISPATCHED,
    SERIAL_IN_STOCK,
    SERIAL_PICKED,
    UNRESTRICTED,
)
from app.core.timezone import now_ist
from app.models.app_setting import AppSetting
from app.models.company import Company
from app.models.dispatch import DispatchLine, DispatchOrder, SalesOrderLine, StockReservation
from app.models.procurement import PurchaseOrder, PurchaseOrderItem, PurchaseRequisition
from app.models.sales_order import SalesOrder
from app.models.stock import StockBalance, StockSerial
from app.models.warehouse import WarehouseBin
from app.services.company_scope_service import CompanyScopeService
from app.services.procurement.common import actor, next_reference
from app.services.stock import posting
from app.services.stock.queries import lookups

OPEN_DO = (DO_DRAFT, DO_PICKED, DO_PACKED)
#: POs whose undelivered quantity counts as "on order".
OPEN_PO = ("APPROVED", "SENT", "ACKNOWLEDGED", "PARTIALLY_RECEIVED")


def _d(value) -> Decimal:
    return Decimal(str(value or 0))


def is_stock_line(sku: str) -> bool:
    return bool(sku) and not sku.upper().startswith(NON_STOCK_SKU_PREFIXES)


def _line_qty(item: dict) -> Decimal:
    for key in ("quantity_case", "qty", "quantity"):
        if item.get(key) is not None:
            try:
                return Decimal(str(item[key]))
            except Exception:  # noqa: BLE001 - a bad figure is skipped below
                continue
    return Decimal(0)


def ensure_lines(order: SalesOrder, db: Session) -> list[SalesOrderLine]:
    """A stable row for every goods line in the order's JSON items.

    Idempotent. A line's ordered quantity follows the JSON until something
    has been dispatched against it; after that it is left alone. Does not
    commit.
    """

    held = {
        line.line_index: line
        for line in db.query(SalesOrderLine).filter(SalesOrderLine.sales_order_id == order.id).all()
    }
    for index, item in enumerate(order.items or []):
        sku = str(item.get("sku") or "").strip().upper()
        if not is_stock_line(sku):
            continue
        quantity = _line_qty(item)
        line = held.get(index)
        if line is None:
            line = SalesOrderLine(
                sales_order_id=order.id, line_index=index, sku=sku,
                product_name=item.get("product") or item.get("name"), uom=item.get("unit"),
                qty_ordered=quantity, qty_dispatched=0, qty_delivered=0,
            )
            db.add(line)
            held[index] = line
        elif _d(line.qty_dispatched) == 0:
            line.sku = sku
            line.qty_ordered = quantity
    db.flush()
    return sorted(held.values(), key=lambda line: line.line_index)


def _order(order_id: int, db: Session, lock: bool = False) -> SalesOrder:
    query = db.query(SalesOrder).filter(SalesOrder.id == order_id)
    if lock:
        query = query.with_for_update()
    order = query.first()
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found.")
    return order


def _reserved_open(line_id: int, db: Session, warehouse_id=None) -> Decimal:
    query = db.query(func.coalesce(func.sum(StockReservation.qty - StockReservation.qty_consumed), 0)).filter(
        StockReservation.so_line_id == line_id, StockReservation.status == RES_ACTIVE,
    )
    if warehouse_id is not None:
        query = query.filter(StockReservation.warehouse_id == warehouse_id)
    return _d(query.scalar())


def _on_open_dispatches(line_id: int, db: Session, exclude: int | None = None) -> Decimal:
    query = (
        db.query(func.coalesce(func.sum(DispatchLine.qty), 0))
        .join(DispatchOrder, DispatchOrder.id == DispatchLine.dispatch_id)
        .filter(DispatchLine.so_line_id == line_id, DispatchOrder.status.in_(OPEN_DO))
    )
    if exclude:
        query = query.filter(DispatchOrder.id != exclude)
    return _d(query.scalar())


def _available(company_id, warehouse_id, sku: str, db: Session) -> Decimal:
    row = (
        db.query(func.coalesce(func.sum(StockBalance.qty_on_hand - StockBalance.qty_reserved), 0))
        .join(WarehouseBin, WarehouseBin.id == StockBalance.bin_id)
        .filter(
            StockBalance.company_id == company_id, StockBalance.sku == sku,
            StockBalance.stock_status == UNRESTRICTED, WarehouseBin.bin_type == "STORE",
            *([StockBalance.warehouse_id == warehouse_id] if warehouse_id else []),
        )
        .scalar()
    )
    return _d(row)


def _on_order(company_id, sku: str, db: Session) -> Decimal:
    rows = (
        db.query(PurchaseOrderItem.quantity, PurchaseOrderItem.received_quantity)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
        .join(PurchaseRequisition, PurchaseRequisition.id == PurchaseOrder.requisition_id)
        .filter(
            PurchaseRequisition.company_id == company_id,
            PurchaseOrder.status.in_(OPEN_PO),
            func.upper(PurchaseOrderItem.sku) == sku,
        )
        .all()
    )
    return sum((max(_d(q) - _d(r), Decimal(0)) for q, r in rows), Decimal(0))


def eway_threshold(db: Session) -> Decimal:
    row = db.query(AppSetting).filter(AppSetting.key == EWAY_BILL_SETTING).first()
    try:
        return Decimal(str(row.value)) if row and row.value else Decimal(EWAY_BILL_DEFAULT)
    except Exception:  # noqa: BLE001 - a bad setting falls back to the default, said in the docs
        return Decimal(EWAY_BILL_DEFAULT)


# ----------------------------------------------------------------------
# Availability and reservations
# ----------------------------------------------------------------------
class ReservationService:

    @staticmethod
    def availability(order_id: int, warehouse_id: str | None, current_user: dict, db: Session) -> dict:
        order = _order(order_id, db)
        chosen = warehouse_id or order.dispatch_warehouse_id
        if not chosen:
            raise HTTPException(status_code=400, detail="Choose the warehouse this order will ship from.")
        warehouse = CompanyScopeService.assert_warehouse_allowed(current_user, chosen, db)
        if order.selling_company_id and order.selling_company_id != warehouse.company_id:
            raise HTTPException(status_code=400, detail="This order is being fulfilled by another company's warehouse.")

        lines = ensure_lines(order, db)
        db.commit()
        result = []
        for line in lines:
            reserved = _reserved_open(line.id, db)
            remaining = _d(line.qty_ordered) - _d(line.qty_dispatched)
            here = _available(warehouse.company_id, warehouse.id, line.sku, db)
            company_wide = _available(warehouse.company_id, None, line.sku, db)
            on_order = _on_order(warehouse.company_id, line.sku, db)
            to_reserve = max(remaining - reserved, Decimal(0))
            result.append({
                "so_line_id": line.id,
                "line_index": line.line_index,
                "sku": line.sku,
                "product_name": line.product_name,
                "qty_ordered": float(line.qty_ordered),
                "qty_reserved": float(reserved),
                "qty_on_dispatch": float(_on_open_dispatches(line.id, db)),
                "qty_dispatched": float(line.qty_dispatched),
                "qty_delivered": float(line.qty_delivered),
                "qty_remaining": float(remaining),
                "available_here": float(here),
                "available_other_warehouses": float(company_wide - here),
                "on_order": float(on_order),
                "shortage": float(max(to_reserve - here, Decimal(0))),
            })
        return {
            "sales_order_id": order.id,
            "order_number": order.order_number,
            "customer_name": order.customer_name,
            "status": order.status,
            "warehouse_id": str(warehouse.id),
            "warehouse_name": warehouse.location_name,
            "company_id": str(warehouse.company_id),
            "fixed_warehouse_id": str(order.dispatch_warehouse_id) if order.dispatch_warehouse_id else None,
            "lines": result,
        }

    @staticmethod
    def reserve(order_id: int, payload, current_user: dict, db: Session) -> dict:
        """Hold stock for the order's lines in one warehouse.

        The first reservation fixes the order's selling company and dispatch
        warehouse; later ones must use the same warehouse. A line cannot be
        reserved beyond what is still to dispatch, nor beyond what is
        available - both refused, nothing changed.
        """

        order = _order(order_id, db, lock=True)
        warehouse = CompanyScopeService.assert_warehouse_allowed(current_user, payload.warehouse_id, db)
        if order.dispatch_warehouse_id and order.dispatch_warehouse_id != warehouse.id:
            active = (
                db.query(StockReservation.id)
                .join(SalesOrderLine, SalesOrderLine.id == StockReservation.so_line_id)
                .filter(SalesOrderLine.sales_order_id == order.id, StockReservation.status == RES_ACTIVE)
                .first()
            )
            if active:
                raise HTTPException(status_code=409, detail="This order already has stock reserved in another warehouse. Release it first.")

        lines = {line.id: line for line in ensure_lines(order, db)}
        user_id, name = actor(current_user, db)
        made = []
        try:
            for request in payload.lines:
                line = lines.get(request.so_line_id)
                if line is None:
                    raise HTTPException(status_code=404, detail="That line is not on this order.")
                wanted = _d(request.qty)
                if wanted <= 0:
                    raise HTTPException(status_code=400, detail=f"{line.sku}: reserve a quantity above zero.")
                room = _d(line.qty_ordered) - _d(line.qty_dispatched) - _reserved_open(line.id, db)
                if wanted > room:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{line.sku}: only {room.normalize()} is still to be reserved on this order.",
                    )
                for balance, taken in posting.reserve(db, warehouse.company_id, warehouse.id, line.sku, wanted):
                    reservation = StockReservation(
                        company_id=warehouse.company_id, warehouse_id=warehouse.id, bin_id=balance.bin_id,
                        sku=line.sku, so_line_id=line.id, qty=taken, qty_consumed=0, status=RES_ACTIVE,
                        created_by=user_id, created_by_name=name,
                    )
                    db.add(reservation)
                    made.append(reservation)
        except HTTPException:
            db.rollback()
            raise

        order.selling_company_id = warehouse.company_id
        order.dispatch_warehouse_id = warehouse.id
        db.commit()
        return ReservationService.availability(order.id, str(warehouse.id), current_user, db)

    @staticmethod
    def release(order_id: int, so_line_id: int | None, reason: str, current_user: dict, db: Session) -> dict:
        """Give back what is reserved and not yet on an open dispatch order."""

        order = _order(order_id, db, lock=True)
        if not order.dispatch_warehouse_id:
            raise HTTPException(status_code=400, detail="Nothing is reserved for this order.")
        CompanyScopeService.assert_warehouse_allowed(current_user, order.dispatch_warehouse_id, db)
        released = ReservationService.release_open(order, so_line_id, reason or "Released", db)
        if not released:
            raise HTTPException(status_code=400, detail="Nothing can be released: it is all on dispatch orders or already gone.")
        db.commit()
        return ReservationService.availability(order.id, str(order.dispatch_warehouse_id), current_user, db)

    @staticmethod
    def release_open(order: SalesOrder, so_line_id: int | None, reason: str, db: Session, include_dispatch: bool = False) -> Decimal:
        """Release a line's (or every line's) free reservation. Does not commit.

        Quantity already on an open dispatch order stays reserved for it
        unless ``include_dispatch`` (the order itself is being cancelled).
        """

        released = Decimal(0)
        line_ids = [
            line.id for line in db.query(SalesOrderLine).filter(SalesOrderLine.sales_order_id == order.id).all()
            if so_line_id is None or line.id == so_line_id
        ]
        for line_id in line_ids:
            keep = Decimal(0) if include_dispatch else _on_open_dispatches(line_id, db)
            rows = (
                db.query(StockReservation)
                .filter(StockReservation.so_line_id == line_id, StockReservation.status == RES_ACTIVE)
                .order_by(StockReservation.id.desc())
                .with_for_update()
                .all()
            )
            for row in rows:
                free = _d(row.qty) - _d(row.qty_consumed)
                give = free - min(free, keep)
                keep -= min(free, keep)
                if give <= 0:
                    continue
                posting.release(db, row.company_id, row.warehouse_id, row.bin_id, row.sku, give)
                released += give
                if give < free:
                    row.qty = _d(row.qty) - give
                    continue
                # Nothing of it left free: a row never used keeps its quantity
                # as history and is marked released; one partly dispatched
                # shrinks to what it covered.
                if _d(row.qty_consumed) == 0:
                    row.status = RES_RELEASED
                else:
                    row.qty = _d(row.qty_consumed)
                    row.status = RES_CONSUMED
                row.released_at = datetime.utcnow()
                row.released_reason = reason
        return released


# ----------------------------------------------------------------------
# Dispatch orders
# ----------------------------------------------------------------------
def dispatch_dict(do: DispatchOrder, db: Session, names=None) -> dict:
    companies, warehouses, bins = names or lookups(db)
    order = db.query(SalesOrder.order_number, SalesOrder.customer_name, SalesOrder.status).filter(SalesOrder.id == do.sales_order_id).first()
    warehouse = warehouses.get(str(do.warehouse_id), (None, None))
    lines = [
        {
            "id": line.id,
            "so_line_id": line.so_line_id,
            "sku": line.sku,
            "product_name": line.product_name,
            "qty": float(line.qty),
            "qty_picked": float(line.qty_picked),
            "serials": line.serials or [],
        }
        for line in do.lines
    ]
    value = do.consignment_value
    return {
        "id": do.id,
        "do_number": do.do_number,
        "company_id": str(do.company_id),
        "company_name": companies.get(str(do.company_id)),
        "warehouse_id": str(do.warehouse_id),
        "warehouse_name": warehouse[0],
        "warehouse_code": warehouse[1],
        "sales_order_id": do.sales_order_id,
        "order_number": order.order_number if order else None,
        "customer_name": order.customer_name if order else None,
        "order_status": order.status if order else None,
        "status": do.status,
        "dispatch_date": do.dispatch_date.isoformat() if do.dispatch_date else None,
        "ship_to": do.ship_to,
        "transporter": do.transporter,
        "vehicle_number": do.vehicle_number,
        "driver_name": do.driver_name,
        "docket_no": do.docket_no,
        "challan_no": do.challan_no,
        "challan_date": do.challan_date.isoformat() if do.challan_date else None,
        "invoice_no": do.invoice_no,
        "eway_bill_no": do.eway_bill_no,
        "eway_bill_date": do.eway_bill_date.isoformat() if do.eway_bill_date else None,
        "eway_valid_until": do.eway_valid_until.isoformat() if do.eway_valid_until else None,
        "gate_pass_no": do.gate_pass_no,
        "packages": do.packages,
        "gross_weight": do.gross_weight,
        "consignment_value": float(value) if value is not None else None,
        "eway_bill_threshold": float(eway_threshold(db)),
        "remarks": do.remarks,
        "created_by_name": do.created_by_name,
        "committed_by_name": do.committed_by_name,
        "committed_at": do.committed_at.isoformat() if do.committed_at else None,
        "delivered_at": do.delivered_at.isoformat() if do.delivered_at else None,
        "pod_receiver": do.pod_receiver,
        "pod_remarks": do.pod_remarks,
        "pod_recorded_by_name": do.pod_recorded_by_name,
        "cancelled_reason": do.cancelled_reason,
        "created_at": do.created_at.isoformat() if do.created_at else None,
        "lines": lines,
        "total_qty": sum(x["qty"] for x in lines),
    }


class DispatchService:

    @staticmethod
    def _get(do_id: int, current_user: dict, db: Session, lock: bool = False) -> DispatchOrder:
        query = db.query(DispatchOrder).filter(DispatchOrder.id == do_id)
        if lock:
            query = query.with_for_update()
        do = query.first()
        if do is None:
            raise HTTPException(status_code=404, detail="Dispatch order not found.")
        CompanyScopeService.assert_warehouse_allowed(current_user, do.warehouse_id, db)
        return do

    @staticmethod
    def get(do_id: int, current_user: dict, db: Session) -> dict:
        return dispatch_dict(DispatchService._get(do_id, current_user, db), db)

    @staticmethod
    def list_orders(current_user: dict, db: Session, *, warehouse_id=None, status=None, search=None, sales_order_id=None,
                    page: int = 1, size: int = 20) -> dict:
        from app.services.stock.queries import scope

        conditions = scope(DispatchOrder, current_user, db, None, warehouse_id)
        if conditions is None:
            return {"data": [], "total": 0, "counts": {}}
        base = db.query(DispatchOrder).filter(*conditions)
        if sales_order_id:
            base = base.filter(DispatchOrder.sales_order_id == sales_order_id)
        if search and search.strip():
            term = f"%{search.strip()}%"
            order_ids = [r[0] for r in db.query(SalesOrder.id).filter(
                SalesOrder.order_number.ilike(term) | SalesOrder.customer_name.ilike(term)).limit(500).all()]
            base = base.filter(DispatchOrder.do_number.ilike(term) | DispatchOrder.sales_order_id.in_(order_ids or [-1]))
        counts = {s: n for s, n in base.with_entities(DispatchOrder.status, func.count()).group_by(DispatchOrder.status).all()}
        query = base.filter(DispatchOrder.status == status.upper()) if status else base
        total = query.count()
        rows = query.order_by(DispatchOrder.id.desc()).offset((max(page, 1) - 1) * size).limit(size).all()
        names = lookups(db)
        return {"data": [dispatch_dict(r, db, names) for r in rows], "total": total, "counts": counts}

    @staticmethod
    def create(payload, current_user: dict, db: Session) -> dict:
        order = _order(payload.sales_order_id, db, lock=True)
        if not order.dispatch_warehouse_id:
            raise HTTPException(status_code=400, detail="Reserve stock for this order first; that fixes the warehouse it ships from.")
        warehouse = CompanyScopeService.assert_warehouse_allowed(current_user, order.dispatch_warehouse_id, db)
        lines = {line.id: line for line in ensure_lines(order, db)}

        do_lines = []
        seen = set()
        for request in payload.lines:
            line = lines.get(request.so_line_id)
            if line is None:
                raise HTTPException(status_code=404, detail="That line is not on this order.")
            if line.id in seen:
                raise HTTPException(status_code=400, detail=f"{line.sku} is on this dispatch twice.")
            seen.add(line.id)
            qty = _d(request.qty)
            if qty <= 0:
                raise HTTPException(status_code=400, detail=f"{line.sku}: dispatch a quantity above zero.")
            free = _reserved_open(line.id, db, warehouse.id) - _on_open_dispatches(line.id, db)
            if qty > free:
                raise HTTPException(
                    status_code=400,
                    detail=f"{line.sku}: only {max(free, Decimal(0)).normalize()} is reserved and not already on a dispatch order.",
                )
            do_lines.append(DispatchLine(so_line_id=line.id, sku=line.sku, product_name=line.product_name, qty=qty, qty_picked=0))

        if not do_lines:
            raise HTTPException(status_code=400, detail="Add at least one line to dispatch.")

        company = db.query(Company).filter(Company.id == warehouse.company_id).first()
        user_id, name = actor(current_user, db)
        address = order.shipping_address or order.billing_address or {}
        do = DispatchOrder(
            do_number=next_reference(db, DispatchOrder, "do_number", f"{(company.company_code or 'CO').upper()}-DO"),
            company_id=warehouse.company_id, warehouse_id=warehouse.id, sales_order_id=order.id, status=DO_DRAFT,
            dispatch_date=payload.dispatch_date or now_ist().date(),
            ship_to=payload.ship_to or (
                ", ".join(str(v) for v in address.values() if isinstance(v, (str, int)) and str(v).strip())
                if isinstance(address, dict) else str(address)
            ) or None,
            remarks=payload.remarks, created_by=user_id, created_by_name=name, lines=do_lines,
        )
        db.add(do)
        db.commit()
        db.refresh(do)
        return dispatch_dict(do, db)

    @staticmethod
    def pick(do_id: int, payload, current_user: dict, db: Session) -> dict:
        """Allocate what goes in the box. Serials become PICKED; no stock moves."""

        do = DispatchService._get(do_id, current_user, db, lock=True)
        if do.status not in (DO_DRAFT, DO_PICKED):
            raise HTTPException(status_code=409, detail=f"{do.do_number} is {do.status.lower()}; picking is done.")
        picks = {p.line_id: p for p in payload.lines}
        missing = [line.sku for line in do.lines if line.id not in picks]
        if missing:
            raise HTTPException(status_code=400, detail=f"Pick every line: {', '.join(missing)}.")

        for line in do.lines:
            pick = picks[line.id]
            serials = [s.strip().upper() for s in (pick.serials or []) if s and s.strip()]
            if len(set(serials)) != len(serials):
                raise HTTPException(status_code=400, detail=f"{line.sku}: a serial is entered twice.")
            if serials and len(serials) != _d(line.qty):
                raise HTTPException(status_code=400, detail=f"{line.sku}: {len(serials)} serial(s) for {_d(line.qty).normalize()} to dispatch.")

            # Put back what this line had picked before, then take the new ones.
            for old in line.serials or []:
                unit = db.query(StockSerial).filter(StockSerial.company_id == do.company_id, StockSerial.serial_no == old).with_for_update().first()
                if unit is not None and unit.status == SERIAL_PICKED:
                    unit.status = SERIAL_IN_STOCK

            reserved_bins = {
                str(r.bin_id): _d(r.qty) - _d(r.qty_consumed)
                for r in db.query(StockReservation).filter(
                    StockReservation.so_line_id == line.so_line_id, StockReservation.status == RES_ACTIVE,
                    StockReservation.warehouse_id == do.warehouse_id).all()
            }
            per_bin: dict[str, int] = {}
            for serial in serials:
                unit = (
                    db.query(StockSerial)
                    .filter(StockSerial.company_id == do.company_id, StockSerial.serial_no == serial)
                    .with_for_update()
                    .first()
                )
                if unit is None or unit.sku != line.sku or unit.warehouse_id != do.warehouse_id:
                    raise HTTPException(status_code=400, detail=f"Serial {serial} is not a {line.sku} in this warehouse.")
                if unit.status != SERIAL_IN_STOCK:
                    raise HTTPException(status_code=409, detail=f"Serial {serial} is {unit.status.lower()}, not on the shelf.")
                per_bin[str(unit.bin_id)] = per_bin.get(str(unit.bin_id), 0) + 1
                unit.status = SERIAL_PICKED
            for bin_id, count in per_bin.items():
                if count > reserved_bins.get(bin_id, Decimal(0)):
                    raise HTTPException(status_code=400, detail=f"{line.sku}: more serials from one bin than are reserved there.")

            line.serials = serials or None
            line.qty_picked = line.qty

        do.status = DO_PICKED
        db.commit()
        db.refresh(do)
        return dispatch_dict(do, db)

    @staticmethod
    def pack(do_id: int, payload, current_user: dict, db: Session) -> dict:
        do = DispatchService._get(do_id, current_user, db, lock=True)
        if do.status != DO_PICKED:
            raise HTTPException(status_code=409, detail="Pick it before packing.")
        if not payload.packages or payload.packages < 1:
            raise HTTPException(status_code=400, detail="Enter how many packages.")
        do.packages = payload.packages
        do.gross_weight = (payload.gross_weight or "").strip() or None
        do.status = DO_PACKED
        db.commit()
        db.refresh(do)
        return dispatch_dict(do, db)

    @staticmethod
    def documents(do_id: int, payload, current_user: dict, db: Session) -> dict:
        do = DispatchService._get(do_id, current_user, db, lock=True)
        if do.status not in OPEN_DO:
            raise HTTPException(status_code=409, detail="Documents are fixed once the goods have left.")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(do, key, value.strip() if isinstance(value, str) else value)
        db.commit()
        db.refresh(do)
        return dispatch_dict(do, db)

    @staticmethod
    def commit(do_id: int, current_user: dict, db: Session) -> dict:
        """The goods have left: stock out, once, consuming the reservation."""

        do = DispatchService._get(do_id, current_user, db, lock=True)
        if do.status in (DO_DISPATCHED, DO_DELIVERED):
            raise HTTPException(status_code=409, detail=f"{do.do_number} has already been dispatched.")
        if do.status != DO_PACKED:
            raise HTTPException(status_code=409, detail="Pick and pack it before dispatching.")

        missing = [label for label, value in (("delivery challan number", do.challan_no), ("gate pass number", do.gate_pass_no),
                                              ("vehicle number or docket", do.vehicle_number or do.docket_no)) if not value]
        if do.consignment_value is None:
            missing.append("consignment value")
        elif _d(do.consignment_value) >= eway_threshold(db) and not (do.eway_bill_no and do.eway_valid_until):
            missing.append(f"e-way bill number and validity (consignment of Rs. {_d(do.consignment_value).normalize()} is at or above the limit)")
        if missing:
            raise HTTPException(status_code=400, detail="Before dispatch, enter the " + ", ".join(missing) + ".")

        movements = []
        consumed: list[tuple[StockReservation, Decimal]] = []
        for line in do.lines:
            reservations = (
                db.query(StockReservation)
                .filter(StockReservation.so_line_id == line.so_line_id, StockReservation.status == RES_ACTIVE,
                        StockReservation.warehouse_id == do.warehouse_id)
                .order_by(StockReservation.id.asc())
                .with_for_update()
                .all()
            )
            serial_bin = {}
            for serial in line.serials or []:
                unit = db.query(StockSerial).filter(StockSerial.company_id == do.company_id, StockSerial.serial_no == serial).first()
                serial_bin.setdefault(str(unit.bin_id), []).append(serial)

            need = _d(line.qty)
            if serial_bin:
                plan = [(bin_id, Decimal(len(found)), found) for bin_id, found in serial_bin.items()]
            else:
                plan = []
                left = need
                for r in reservations:
                    free = _d(r.qty) - _d(r.qty_consumed)
                    take = min(free, left)
                    if take > 0:
                        plan.append((str(r.bin_id), take, []))
                        left -= take
                if left > 0:
                    raise HTTPException(status_code=409, detail=f"{line.sku}: the reservation no longer covers {need.normalize()}.")

            for bin_id, qty, serials in plan:
                left = qty
                for r in reservations:
                    if str(r.bin_id) != bin_id or left == 0:
                        continue
                    free = _d(r.qty) - _d(r.qty_consumed) - sum((q for x, q in consumed if x is r), Decimal(0))
                    take = min(free, left)
                    if take > 0:
                        consumed.append((r, take))
                        left -= take
                if left > 0:
                    raise HTTPException(status_code=409, detail=f"{line.sku}: not enough reserved in that bin.")
                movements.append(posting.Movement(
                    company_id=do.company_id, warehouse_id=do.warehouse_id, bin_id=_uuid(bin_id), sku=line.sku,
                    product_name=line.product_name, stock_status=UNRESTRICTED, quantity=-qty,
                    movement_type=SALES_DISPATCH, source_line_id=f"{line.id}:{bin_id}", consume_reserved=qty,
                    serials=serials, serial_status_before=SERIAL_PICKED if serials else None,
                    serial_status_after=SERIAL_DISPATCHED,
                ))

        user_id, name = actor(current_user, db)
        order = _order(do.sales_order_id, db, lock=True)
        try:
            posting.post(db, posting.Posting(
                source_doc_type="DISPATCH", source_doc_id=str(do.id), source_doc_number=do.do_number,
                movement_date=now_ist().date(), movements=movements,
                created_by=str(do.created_by) if do.created_by else None, created_by_name=do.created_by_name,
                approved_by=str(user_id) if user_id else None, approved_by_name=name,
                remarks=f"Dispatch {do.do_number} for {order.order_number or order.id}",
            ))
        except HTTPException:
            db.rollback()
            raise

        for reservation, take in consumed:
            reservation.qty_consumed = _d(reservation.qty_consumed) + take
            if _d(reservation.qty_consumed) >= _d(reservation.qty):
                reservation.status = RES_CONSUMED
        for line in do.lines:
            so_line = db.query(SalesOrderLine).filter(SalesOrderLine.id == line.so_line_id).with_for_update().one()
            so_line.qty_dispatched = _d(so_line.qty_dispatched) + _d(line.qty)

        do.status = DO_DISPATCHED
        do.committed_by = user_id
        do.committed_by_name = name
        do.committed_at = datetime.utcnow()
        db.commit()
        db.refresh(do)
        return dispatch_dict(do, db)

    @staticmethod
    def pod(do_id: int, payload, current_user: dict, db: Session) -> dict:
        """Proof of delivery. Moves no stock."""

        do = DispatchService._get(do_id, current_user, db, lock=True)
        if do.status == DO_DELIVERED:
            raise HTTPException(status_code=409, detail="Delivery has already been recorded.")
        if do.status != DO_DISPATCHED:
            raise HTTPException(status_code=409, detail="It has not been dispatched yet.")
        if not (payload.receiver or "").strip():
            raise HTTPException(status_code=400, detail="Enter who received the goods.")
        _, name = actor(current_user, db)
        do.delivered_at = payload.delivered_at or datetime.utcnow()
        do.pod_receiver = payload.receiver.strip()
        do.pod_remarks = (payload.remarks or "").strip() or None
        do.pod_recorded_by_name = name
        do.status = DO_DELIVERED
        for line in do.lines:
            so_line = db.query(SalesOrderLine).filter(SalesOrderLine.id == line.so_line_id).with_for_update().one()
            so_line.qty_delivered = _d(so_line.qty_delivered) + _d(line.qty)
        db.commit()
        db.refresh(do)
        return dispatch_dict(do, db)

    @staticmethod
    def cancel(do_id: int, reason: str, current_user: dict, db: Session) -> dict:
        do = DispatchService._get(do_id, current_user, db, lock=True)
        if do.status not in OPEN_DO:
            raise HTTPException(status_code=409, detail="The goods have left; a dispatched order is not cancelled. Bring them back with a customer return.")
        if not (reason or "").strip():
            raise HTTPException(status_code=400, detail="Say why it is cancelled.")
        DispatchService.unpick(do, db)
        do.status = DO_CANCELLED
        do.cancelled_reason = reason.strip()
        db.commit()
        db.refresh(do)
        return dispatch_dict(do, db)

    @staticmethod
    def unpick(do: DispatchOrder, db: Session) -> None:
        """Put a dispatch order's picked serials back on the shelf. Does not commit."""

        for line in do.lines:
            for serial in line.serials or []:
                unit = db.query(StockSerial).filter(StockSerial.company_id == do.company_id, StockSerial.serial_no == serial).with_for_update().first()
                if unit is not None and unit.status == SERIAL_PICKED:
                    unit.status = SERIAL_IN_STOCK
            line.qty_picked = 0


def _uuid(value):
    from uuid import UUID

    return UUID(str(value))


# ----------------------------------------------------------------------
# The existing order desks, once the ledger is the stock record
# ----------------------------------------------------------------------
def ledger_live(db: Session) -> bool:
    """Whether dispatch orders, not the MongoDB counts, move stock.

    Off until a super admin turns it on after migrate_stock_to_ledger.py,
    so the two can never both count the same goods out.
    """

    from app.core.stock import LEDGER_LIVE_SETTING

    row = db.query(AppSetting).filter(AppSetting.key == LEDGER_LIVE_SETTING).first()
    return bool(row and str(row.value).strip().lower() in ("1", "true", "yes", "on"))


def _goods_lines(order: SalesOrder, db: Session) -> list[SalesOrderLine]:
    return [line for line in ensure_lines(order, db) if _d(line.qty_ordered) > 0]


def assert_stock_ready(order: SalesOrder, db: Session) -> None:
    """Production marks an order ready only when its goods are held:
    reserved or already dispatched, line by line."""

    short = []
    for line in _goods_lines(order, db):
        held = _reserved_open(line.id, db) + _d(line.qty_dispatched)
        if held < _d(line.qty_ordered):
            short.append(f"{line.sku} ({(_d(line.qty_ordered) - held).normalize()} short)")
    if short:
        raise HTTPException(
            status_code=400,
            detail="Reserve stock for every line before marking it ready: " + ", ".join(short) + ". Use SCM > Dispatch.",
        )


def assert_dispatched(order: SalesOrder, db: Session) -> None:
    """Logistics marks an order dispatched only when its dispatch orders
    have been committed for everything on it."""

    left = [
        f"{line.sku} ({(_d(line.qty_ordered) - _d(line.qty_dispatched)).normalize()} to go)"
        for line in _goods_lines(order, db)
        if _d(line.qty_dispatched) < _d(line.qty_ordered)
    ]
    if left:
        raise HTTPException(
            status_code=400,
            detail="Commit the dispatch orders first - not everything has left the warehouse: " + ", ".join(left) + ".",
        )


def on_order_stage(order: SalesOrder, db: Session) -> None:
    """A cancelled order gives back what was held for it and its dispatch
    orders that have not left. Goods already dispatched stay out: they
    come back only through a customer return. Does not commit."""

    if str(order.status) != "CANCELLED":
        return
    for do in (
        db.query(DispatchOrder)
        .filter(DispatchOrder.sales_order_id == order.id, DispatchOrder.status.in_(OPEN_DO))
        .with_for_update()
        .all()
    ):
        DispatchService.unpick(do, db)
        do.status = DO_CANCELLED
        do.cancelled_reason = "The sales order was cancelled."
    ReservationService.release_open(order, None, "The sales order was cancelled.", db, include_dispatch=True)
