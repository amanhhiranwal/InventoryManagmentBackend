"""Stock transfers between two warehouses of the same company.

  DRAFT      the requester lists what goes from where.
  SUBMITTED  sent for approval. No stock moves.
  APPROVED   by someone other than the requester. No stock moves.
  SHIPPED    one posting: TRANSFER_OUT takes the goods out of the sending
             bins, and the same quantity appears IN_TRANSIT at the
             receiving warehouse. Reservations are respected: reserved
             stock cannot be shipped.
  RECEIVED   one posting at the receiving warehouse: in transit goes down
             by everything shipped; what arrived good is put in a store
             bin, what arrived damaged is BLOCKED in the rejected-goods bin,
             and the shortfall is written off with the reason given.
  CANCELLED  any time before it ships.

A transfer between two companies is refused: that is a sale and a
purchase, never a transfer. Each step locks the transfer row and checks
its status, and every posting goes through posting.post, so a retried
ship or receive moves nothing twice.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.stock import (
    APPROVE_TRANSFER,
    BLOCKED,
    CREATE_TRANSFER,
    IN_TRANSIT,
    SERIAL_IN_TRANSIT,
    STOCK_ADJUSTMENT_OUT,
    TRANSFER_IN,
    TRANSFER_OUT,
    TRF_APPROVED,
    TRF_CANCELLED,
    TRF_DRAFT,
    TRF_RECEIVED,
    TRF_SHIPPED,
    TRF_SUBMITTED,
    UNRESTRICTED,
)
from app.core.timezone import now_ist
from app.core.warehouse import WAREHOUSE, location_type
from app.models.company import Company
from app.models.location import Location
from app.models.stock import StockBalance, StockLedger
from app.models.stock_transfer import StockTransfer, StockTransferLine
from app.models.warehouse import WarehouseBin
from app.services.company_scope_service import CompanyScopeService
from app.services.procurement.common import actor, has_permission, next_reference
from app.services.stock import posting
from app.services.stock.documents import catalogue_product, serial_units_on_shelf
from app.services.stock.queries import lookups

SOURCE = "STOCK_TRANSFER"


def _dec(value, label: str, allow_zero: bool = False) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise HTTPException(status_code=400, detail=f"{label}: enter a number.")
    if number < 0 or (number == 0 and not allow_zero):
        raise HTTPException(status_code=400, detail=f"{label}: enter a quantity above zero.")
    return number


def _serials(raw) -> list[str]:
    return [s.strip().upper() for s in (raw or []) if s and s.strip()]


def transfer_dict(t: StockTransfer, names=None) -> dict:
    companies, warehouses, bins = names or ({}, {}, {})
    src = warehouses.get(str(t.from_warehouse_id), (None, None))
    dst = warehouses.get(str(t.to_warehouse_id), (None, None))
    lines = [
        {
            "id": line.id,
            "line_no": line.line_no,
            "sku": line.sku,
            "product_name": line.product_name,
            "from_bin_id": str(line.from_bin_id),
            "from_bin_code": bins.get(str(line.from_bin_id)),
            "to_bin_id": str(line.to_bin_id) if line.to_bin_id else None,
            "to_bin_code": bins.get(str(line.to_bin_id)) if line.to_bin_id else None,
            "qty": float(line.qty),
            "qty_received": float(line.qty_received) if line.qty_received is not None else None,
            "qty_damaged": float(line.qty_damaged) if line.qty_damaged is not None else None,
            "qty_short": (
                float(Decimal(line.qty) - Decimal(line.qty_received) - Decimal(line.qty_damaged or 0))
                if line.qty_received is not None else None
            ),
            "unit_cost": float(line.unit_cost) if line.unit_cost is not None else None,
            "serials": line.serials or [],
            "serials_received": line.serials_received or [],
            "serials_damaged": line.serials_damaged or [],
            "remarks": line.remarks,
        }
        for line in t.lines
    ]
    return {
        "id": t.id,
        "transfer_number": t.transfer_number,
        "company_id": str(t.company_id),
        "company_name": companies.get(str(t.company_id)),
        "from_warehouse_id": str(t.from_warehouse_id),
        "from_warehouse_name": src[0],
        "from_warehouse_code": src[1],
        "to_warehouse_id": str(t.to_warehouse_id),
        "to_warehouse_name": dst[0],
        "to_warehouse_code": dst[1],
        "status": t.status,
        "transfer_date": t.transfer_date.isoformat() if t.transfer_date else None,
        "reason": t.reason,
        "remarks": t.remarks,
        "vehicle_number": t.vehicle_number,
        "docket_no": t.docket_no,
        "challan_no": t.challan_no,
        "created_by": str(t.created_by) if t.created_by else None,
        "created_by_name": t.created_by_name,
        "submitted_at": t.submitted_at.isoformat() if t.submitted_at else None,
        "approved_by_name": t.approved_by_name,
        "approved_at": t.approved_at.isoformat() if t.approved_at else None,
        "shipped_by_name": t.shipped_by_name,
        "shipped_at": t.shipped_at.isoformat() if t.shipped_at else None,
        "received_by_name": t.received_by_name,
        "received_at": t.received_at.isoformat() if t.received_at else None,
        "receipt_remarks": t.receipt_remarks,
        "cancelled_reason": t.cancelled_reason,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "lines": lines,
        "total_quantity": sum(line["qty"] for line in lines),
    }


class StockTransferService:

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    @staticmethod
    def _visible(t: StockTransfer, current_user: dict, db: Session) -> bool:
        allowed = CompanyScopeService.visible_warehouse_ids(current_user, db)
        return allowed is None or str(t.from_warehouse_id) in allowed or str(t.to_warehouse_id) in allowed

    @staticmethod
    def _get(transfer_id: int, current_user: dict, db: Session, lock: bool = False) -> StockTransfer:
        query = db.query(StockTransfer).filter(StockTransfer.id == transfer_id)
        if lock:
            query = query.with_for_update()
        t = query.first()
        # Seen from neither end reads as not found, so ids cannot be probed.
        if t is None or not StockTransferService._visible(t, current_user, db):
            raise HTTPException(status_code=404, detail="Transfer not found.")
        return t

    @staticmethod
    def get(transfer_id: int, current_user: dict, db: Session) -> dict:
        return transfer_dict(StockTransferService._get(transfer_id, current_user, db), lookups(db))

    @staticmethod
    def list_transfers(current_user: dict, db: Session, *, company_id=None, warehouse_id=None, status=None,
                       search=None, page: int = 1, size: int = 20) -> dict:
        query = db.query(StockTransfer)
        if company_id:
            CompanyScopeService.assert_company_allowed(current_user, company_id, db)
            query = query.filter(StockTransfer.company_id == UUID(str(company_id)))
        if warehouse_id:
            CompanyScopeService.assert_warehouse_allowed(current_user, warehouse_id, db)
            wid = UUID(str(warehouse_id))
            query = query.filter(or_(StockTransfer.from_warehouse_id == wid, StockTransfer.to_warehouse_id == wid))
        allowed = CompanyScopeService.visible_warehouse_ids(current_user, db)
        if allowed is not None:
            if not allowed:
                return {"data": [], "total": 0, "counts": {}}
            ids = [UUID(a) for a in allowed]
            query = query.filter(or_(StockTransfer.from_warehouse_id.in_(ids), StockTransfer.to_warehouse_id.in_(ids)))
        if search and search.strip():
            query = query.filter(StockTransfer.transfer_number.ilike(f"%{search.strip()}%"))
        counts = {s: n for s, n in query.with_entities(StockTransfer.status, func.count()).group_by(StockTransfer.status).all()}
        if status:
            query = query.filter(StockTransfer.status == status.upper())

        total = query.count()
        rows = query.order_by(StockTransfer.id.desc()).offset((max(page, 1) - 1) * size).limit(size).all()
        names = lookups(db)
        return {"data": [transfer_dict(r, names) for r in rows], "total": total, "counts": counts}

    @staticmethod
    def destinations(from_warehouse_id: str, current_user: dict, db: Session) -> list[dict]:
        """The other active warehouses of the sending warehouse's company.

        The requester may not work in them, so this names them only."""

        source = CompanyScopeService.assert_warehouse_allowed(current_user, from_warehouse_id, db)
        rows = db.query(Location).filter(Location.company_id == source.company_id, Location.id != source.id).all()
        return [
            {"id": str(w.id), "location_name": w.location_name, "location_code": w.location_code}
            for w in sorted(rows, key=lambda w: w.location_name or "")
            if location_type(w) == WAREHOUSE and getattr(w, "is_active", True) is not False
        ]

    # ------------------------------------------------------------------
    # Drafting
    # ------------------------------------------------------------------
    @staticmethod
    def _destination(source: Location, to_warehouse_id, db: Session) -> Location:
        try:
            wid = UUID(str(to_warehouse_id))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Choose the warehouse the goods go to.")
        dest = db.query(Location).filter(Location.id == wid).first()
        if dest is None or location_type(dest) != WAREHOUSE:
            raise HTTPException(status_code=400, detail="Choose the warehouse the goods go to.")
        if dest.id == source.id:
            raise HTTPException(status_code=400, detail="The goods have to go to a different warehouse.")
        if dest.company_id != source.company_id:
            raise HTTPException(
                status_code=400,
                detail="That warehouse belongs to another company. Moving goods between companies is a sale and a "
                       "purchase, not a transfer.",
            )
        if getattr(dest, "is_active", True) is False:
            raise HTTPException(status_code=400, detail=f"{dest.location_name} is switched off.")
        return dest

    @staticmethod
    def _fill(t: StockTransfer, payload, source: Location, db: Session) -> None:
        if not payload.lines:
            raise HTTPException(status_code=400, detail="Add at least one product.")
        bins = {str(b.id): b for b in db.query(WarehouseBin).filter(WarehouseBin.warehouse_id == source.id).all()}
        store = next((b for b in bins.values() if b.code == "STORE"), None) or next(
            (b for b in bins.values() if b.bin_type == "STORE"), None)

        lines, seen, seen_serials = [], set(), set()
        for index, raw in enumerate(payload.lines, start=1):
            label = f"Line {index}"
            sku = (raw.sku or "").strip().upper()
            if not sku:
                raise HTTPException(status_code=400, detail=f"{label}: choose a product.")
            product = catalogue_product(sku, str(source.company_id))
            if product is None:
                raise HTTPException(status_code=400, detail=f"{label}: {sku} is not in this company's product catalogue.")
            bin_row = bins.get(str(raw.bin_id)) if raw.bin_id else store
            if bin_row is None or bin_row.bin_type != "STORE":
                raise HTTPException(status_code=400, detail=f"{label}: take the goods from a store bin of {source.location_name}.")
            if (sku, str(bin_row.id)) in seen:
                raise HTTPException(status_code=400, detail=f"{label}: {sku} from bin {bin_row.code} is already on this transfer.")
            seen.add((sku, str(bin_row.id)))
            qty = _dec(raw.quantity, f"{label} quantity")
            serials = _serials(raw.serials)
            if not serials and serial_units_on_shelf(db, source.company_id, source.id, bin_row.id, sku):
                # Otherwise the units would stay "in stock" here after the
                # quantity has left.
                raise HTTPException(status_code=400, detail=f"{label}: {sku} is serial-numbered. Enter the serial of each unit.")
            if serials and len(serials) != qty:
                raise HTTPException(status_code=400, detail=f"{label}: {len(serials)} serial(s) for a quantity of {qty.normalize()}.")
            for s in serials:
                if s in seen_serials:
                    raise HTTPException(status_code=400, detail=f"Serial {s} is entered twice.")
                seen_serials.add(s)
            lines.append(StockTransferLine(
                line_no=index, sku=sku, product_name=product.get("name"), from_bin_id=bin_row.id, qty=qty,
                serials=serials or None, remarks=(raw.remarks or "").strip() or None,
            ))

        t.lines.clear()
        db.flush()
        t.lines.extend(lines)
        if payload.transfer_date:
            t.transfer_date = payload.transfer_date
        if payload.reason is not None:
            t.reason = payload.reason.strip() or None
        if payload.remarks is not None:
            t.remarks = payload.remarks.strip() or None

    @staticmethod
    def create(payload, current_user: dict, db: Session) -> dict:
        source = CompanyScopeService.assert_warehouse_allowed(current_user, payload.from_warehouse_id, db)
        dest = StockTransferService._destination(source, payload.to_warehouse_id, db)
        company = db.query(Company).filter(Company.id == source.company_id).first()
        user_id, name = actor(current_user, db)
        t = StockTransfer(
            transfer_number=next_reference(db, StockTransfer, "transfer_number", f"{(company.company_code or 'CO').upper()}-TRF"),
            company_id=source.company_id,
            from_warehouse_id=source.id,
            to_warehouse_id=dest.id,
            status=TRF_DRAFT,
            transfer_date=payload.transfer_date or now_ist().date(),
            created_by=user_id,
            created_by_name=name,
        )
        db.add(t)
        try:
            StockTransferService._fill(t, payload, source, db)
        except HTTPException:
            db.rollback()
            raise
        db.commit()
        db.refresh(t)
        return transfer_dict(t, lookups(db))

    @staticmethod
    def update(transfer_id: int, payload, current_user: dict, db: Session) -> dict:
        t = StockTransferService._get(transfer_id, current_user, db, lock=True)
        source = CompanyScopeService.assert_warehouse_allowed(current_user, t.from_warehouse_id, db)
        if t.status != TRF_DRAFT:
            raise HTTPException(status_code=409, detail=f"{t.transfer_number} is {t.status.lower()} and can no longer be changed.")
        if payload.to_warehouse_id:
            t.to_warehouse_id = StockTransferService._destination(source, payload.to_warehouse_id, db).id
        try:
            StockTransferService._fill(t, payload, source, db)
        except HTTPException:
            db.rollback()
            raise
        db.commit()
        db.refresh(t)
        return transfer_dict(t, lookups(db))

    @staticmethod
    def delete(transfer_id: int, current_user: dict, db: Session) -> None:
        t = StockTransferService._get(transfer_id, current_user, db, lock=True)
        CompanyScopeService.assert_warehouse_allowed(current_user, t.from_warehouse_id, db)
        if t.status != TRF_DRAFT:
            raise HTTPException(status_code=409, detail="Only a draft can be deleted.")
        db.delete(t)
        db.commit()

    # ------------------------------------------------------------------
    # Moving it on
    # ------------------------------------------------------------------
    @staticmethod
    def _done(t: StockTransfer, db: Session) -> dict:
        db.commit()
        db.refresh(t)
        return transfer_dict(t, lookups(db))

    @staticmethod
    def submit(transfer_id: int, current_user: dict, db: Session) -> dict:
        t = StockTransferService._get(transfer_id, current_user, db, lock=True)
        CompanyScopeService.assert_warehouse_allowed(current_user, t.from_warehouse_id, db)
        if t.status != TRF_DRAFT:
            raise HTTPException(status_code=409, detail=f"{t.transfer_number} has already been submitted.")
        t.status = TRF_SUBMITTED
        t.submitted_at = datetime.utcnow()
        t.cancelled_reason = None
        return StockTransferService._done(t, db)

    @staticmethod
    def approve(transfer_id: int, current_user: dict, db: Session) -> dict:
        t = StockTransferService._get(transfer_id, current_user, db, lock=True)
        CompanyScopeService.assert_warehouse_allowed(current_user, t.from_warehouse_id, db)
        if t.status != TRF_SUBMITTED:
            raise HTTPException(status_code=409, detail="Only a submitted transfer can be approved.")
        user_id, name = actor(current_user, db)
        if t.created_by and user_id and str(t.created_by) == str(user_id):
            raise HTTPException(status_code=403, detail="Someone other than the person who requested it has to approve it.")
        t.status = TRF_APPROVED
        t.approved_by = user_id
        t.approved_by_name = name
        t.approved_at = datetime.utcnow()
        return StockTransferService._done(t, db)

    @staticmethod
    def cancel(transfer_id: int, reason: str, current_user: dict, db: Session) -> dict:
        t = StockTransferService._get(transfer_id, current_user, db, lock=True)
        CompanyScopeService.assert_warehouse_allowed(current_user, t.from_warehouse_id, db)
        if t.status in (TRF_SHIPPED, TRF_RECEIVED):
            raise HTTPException(status_code=409, detail="It has shipped; receive it at the other warehouse instead.")
        if t.status == TRF_CANCELLED:
            raise HTTPException(status_code=409, detail="It is already cancelled.")
        if not (reason or "").strip():
            raise HTTPException(status_code=400, detail="Say why it is cancelled.")
        # The requester's side may cancel until it ships; an approver may
        # decline one that is waiting for them.
        may = has_permission(current_user, CREATE_TRANSFER, db) or (
            t.status == TRF_SUBMITTED and has_permission(current_user, APPROVE_TRANSFER, db)
        )
        if not may:
            raise HTTPException(status_code=403, detail="Permission denied.")
        t.status = TRF_CANCELLED
        t.cancelled_reason = reason.strip()
        return StockTransferService._done(t, db)

    @staticmethod
    def _bin(warehouse_id, code: str, db: Session) -> WarehouseBin | None:
        rows = db.query(WarehouseBin).filter(WarehouseBin.warehouse_id == warehouse_id, WarehouseBin.is_active.is_(True)).all()
        return next((b for b in rows if b.code == code), None) or next((b for b in rows if b.bin_type == code), None)

    @staticmethod
    def ship(transfer_id: int, payload, current_user: dict, db: Session) -> dict:
        t = StockTransferService._get(transfer_id, current_user, db, lock=True)
        CompanyScopeService.assert_warehouse_allowed(current_user, t.from_warehouse_id, db)
        if t.status == TRF_SHIPPED:
            raise HTTPException(status_code=409, detail=f"{t.transfer_number} has already shipped.")
        if t.status != TRF_APPROVED:
            raise HTTPException(status_code=409, detail="It has to be approved before it ships.")
        transit_bin = StockTransferService._bin(t.to_warehouse_id, "STORE", db)
        if transit_bin is None:
            raise HTTPException(status_code=400, detail="The receiving warehouse has no active store bin.")

        t.vehicle_number = (payload.vehicle_number or "").strip() or None
        t.docket_no = (payload.docket_no or "").strip() or None
        t.challan_no = (payload.challan_no or "").strip() or None
        if not (t.vehicle_number or t.docket_no):
            raise HTTPException(status_code=400, detail="Enter the vehicle number or the courier docket number.")

        movements = []
        for line in t.lines:
            cost = (
                db.query(StockBalance.avg_cost)
                .filter(StockBalance.company_id == t.company_id, StockBalance.warehouse_id == t.from_warehouse_id,
                        StockBalance.bin_id == line.from_bin_id, StockBalance.sku == line.sku,
                        StockBalance.stock_status == UNRESTRICTED)
                .scalar()
            )
            line.unit_cost = cost
            qty = Decimal(line.qty)
            common = dict(company_id=t.company_id, sku=line.sku, product_name=line.product_name,
                          movement_type=TRANSFER_OUT, source_line_id=str(line.id),
                          unit_cost=Decimal(cost) if cost is not None else None)
            serials = list(line.serials or [])
            movements.append(posting.Movement(
                warehouse_id=t.from_warehouse_id, bin_id=line.from_bin_id, stock_status=UNRESTRICTED, quantity=-qty,
                serials=serials, serial_status_after=SERIAL_IN_TRANSIT, **common))
            movements.append(posting.Movement(
                warehouse_id=t.to_warehouse_id, bin_id=transit_bin.id, stock_status=IN_TRANSIT, quantity=qty,
                serials=serials, serial_status_before=SERIAL_IN_TRANSIT, **common))

        user_id, name = actor(current_user, db)
        try:
            posting.post(db, posting.Posting(
                source_doc_type=SOURCE, source_doc_id=str(t.id), source_doc_number=t.transfer_number,
                movement_date=now_ist().date(), movements=movements,
                created_by=str(t.created_by) if t.created_by else None, created_by_name=t.created_by_name,
                approved_by=str(user_id) if user_id else None, approved_by_name=name,
                remarks=f"Transfer {t.transfer_number} shipped",
            ))
        except HTTPException:
            db.rollback()
            raise

        t.status = TRF_SHIPPED
        t.shipped_by = user_id
        t.shipped_by_name = name
        t.shipped_at = datetime.utcnow()
        return StockTransferService._done(t, db)

    @staticmethod
    def receive(transfer_id: int, payload, current_user: dict, db: Session) -> dict:
        """Close the transfer at the receiving warehouse.

        Every line must be accounted for: received good + damaged <= shipped,
        and any shortfall or damage needs a remark. In transit falls by the
        full shipped quantity; the shortfall leaves stock as a write-off."""

        t = StockTransferService._get(transfer_id, current_user, db, lock=True)
        CompanyScopeService.assert_warehouse_allowed(current_user, t.to_warehouse_id, db)
        if t.status == TRF_RECEIVED:
            raise HTTPException(status_code=409, detail=f"{t.transfer_number} has already been received.")
        if t.status != TRF_SHIPPED:
            raise HTTPException(status_code=409, detail="Only a shipped transfer can be received.")

        decisions = {d.line_id: d for d in payload.lines}
        missing = [line.line_no for line in t.lines if line.id not in decisions]
        if missing:
            raise HTTPException(status_code=400, detail=f"Record what arrived for line(s) {', '.join(map(str, missing))}.")

        bins = {str(b.id): b for b in db.query(WarehouseBin).filter(WarehouseBin.warehouse_id == t.to_warehouse_id).all()}
        transit_bin = StockTransferService._bin(t.to_warehouse_id, "STORE", db)
        reject_bin = StockTransferService._bin(t.to_warehouse_id, "REJECT", db)
        # In transit sits in the bin the shipment put it in, read back from
        # the ledger, even if that bin has been switched off since.
        transit_bins = {
            row.source_line_id: row.bin_id
            for row in db.query(StockLedger.source_line_id, StockLedger.bin_id).filter(
                StockLedger.source_doc_type == SOURCE, StockLedger.source_doc_id == str(t.id),
                StockLedger.stock_status == IN_TRANSIT, StockLedger.quantity > 0)
        }

        movements, discrepancy = [], False
        for line in t.lines:
            label = f"Line {line.line_no}"
            d = decisions[line.id]
            shipped = Decimal(line.qty)
            good = _dec(d.qty_received, f"{label} received", allow_zero=True)
            damaged = _dec(d.qty_damaged or 0, f"{label} damaged", allow_zero=True)
            short = shipped - good - damaged
            if short < 0:
                raise HTTPException(status_code=400, detail=f"{label}: {(good + damaged).normalize()} recorded but only {shipped.normalize()} shipped.")
            if (short > 0 or damaged > 0) and not (d.remarks or "").strip():
                raise HTTPException(status_code=400, detail=f"{label}: say what happened to the short or damaged goods.")
            put_into = bins.get(str(d.bin_id)) if d.bin_id else transit_bin
            if put_into is None or not put_into.is_active or put_into.bin_type != "STORE":
                raise HTTPException(status_code=400, detail=f"{label}: choose an active store bin to put the goods in.")
            if damaged > 0 and reject_bin is None:
                raise HTTPException(status_code=400, detail="This warehouse has no active rejected-goods bin.")

            shipped_serials = list(line.serials or [])
            good_serials = _serials(d.serials_received)
            damaged_serials = _serials(d.serials_damaged)
            if shipped_serials:
                unknown = [s for s in good_serials + damaged_serials if s not in shipped_serials]
                if unknown:
                    raise HTTPException(status_code=400, detail=f"{label}: serial {unknown[0]} was not on this transfer.")
                if len(set(good_serials) | set(damaged_serials)) != len(good_serials) + len(damaged_serials):
                    raise HTTPException(status_code=400, detail=f"{label}: a serial is listed as both good and damaged.")
                if len(good_serials) != good or len(damaged_serials) != damaged:
                    raise HTTPException(status_code=400, detail=f"{label}: list the serial of every unit received and damaged.")
            short_serials = [s for s in shipped_serials if s not in good_serials and s not in damaged_serials]

            in_transit_bin = transit_bins.get(str(line.id))
            if in_transit_bin is None:
                raise HTTPException(status_code=409, detail=f"{label}: no shipment of it is in transit.")
            cost = Decimal(line.unit_cost) if line.unit_cost is not None else None
            common = dict(company_id=t.company_id, warehouse_id=t.to_warehouse_id, sku=line.sku,
                          product_name=line.product_name, unit_cost=cost)

            def out_of_transit(qty, serials, tag, movement_type=TRANSFER_IN, after=SERIAL_IN_TRANSIT):
                movements.append(posting.Movement(
                    bin_id=in_transit_bin, stock_status=IN_TRANSIT, quantity=-qty, movement_type=movement_type,
                    source_line_id=f"{line.id}:{tag}", serials=serials, serial_status_after=after, **common))

            if good > 0:
                out_of_transit(good, good_serials, "ok")
                movements.append(posting.Movement(
                    bin_id=put_into.id, stock_status=UNRESTRICTED, quantity=good, movement_type=TRANSFER_IN,
                    source_line_id=f"{line.id}:ok", serials=good_serials, serial_status_before=SERIAL_IN_TRANSIT, **common))
            if damaged > 0:
                out_of_transit(damaged, damaged_serials, "damaged")
                movements.append(posting.Movement(
                    bin_id=reject_bin.id, stock_status=BLOCKED, quantity=damaged, movement_type=TRANSFER_IN,
                    source_line_id=f"{line.id}:damaged", serials=damaged_serials, serial_status_before=SERIAL_IN_TRANSIT,
                    **common))
            if short > 0:
                out_of_transit(short, short_serials, "short", movement_type=STOCK_ADJUSTMENT_OUT, after="LOST")

            line.to_bin_id = put_into.id
            line.qty_received = good
            line.qty_damaged = damaged
            line.serials_received = good_serials or None
            line.serials_damaged = damaged_serials or None
            if d.remarks is not None:
                line.remarks = d.remarks.strip() or line.remarks
            discrepancy = discrepancy or short > 0 or damaged > 0

        user_id, name = actor(current_user, db)
        try:
            posting.post(db, posting.Posting(
                # Its own type, so receipt keys never meet the shipment's.
                source_doc_type=f"{SOURCE}_IN", source_doc_id=str(t.id), source_doc_number=t.transfer_number,
                movement_date=now_ist().date(), movements=movements,
                created_by=str(t.created_by) if t.created_by else None, created_by_name=t.created_by_name,
                approved_by=str(user_id) if user_id else None, approved_by_name=name,
                remarks=f"Transfer {t.transfer_number} received" + (" with shortage or damage" if discrepancy else ""),
            ))
        except HTTPException:
            db.rollback()
            raise

        t.status = TRF_RECEIVED
        t.received_by = user_id
        t.received_by_name = name
        t.received_at = datetime.utcnow()
        t.receipt_remarks = (payload.remarks or "").strip() or None
        return StockTransferService._done(t, db)
