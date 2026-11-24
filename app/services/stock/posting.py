"""The only code that writes the stock ledger and the balances.

Every flow that moves stock - opening stock now; goods receipts,
dispatches, transfers, returns and adjustments as they are built - hands
its movements here. In one database transaction, which the caller
commits together with its own document's status:

  1. each movement is checked: the warehouse belongs to the company, the
     bin belongs to the warehouse, serials are where they should be;
  2. the balance rows it touches are locked (SELECT ... FOR UPDATE), in
     one fixed order so two postings cannot deadlock;
  3. stock may not go below nothing, or below what is reserved;
  4. ledger rows are inserted, each with an idempotency key that is
     unique in the database, and the balances are moved by the same
     amounts.

A second posting of the same document line fails on that unique key, so
however often a request is retried, stock moves once.

Nothing here commits. If any check fails the caller rolls the whole
transaction back and the document stays where it was.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.stock import (
    SERIAL_DISPATCHED,
    SERIAL_IN_STOCK,
    SERIAL_IN_TRANSIT,
    SERIAL_QUARANTINE,
    SERIAL_REJECTED,
    STOCK_STATUSES,
)
from app.core.warehouse import WAREHOUSE, location_type
from app.models.location import Location
from app.models.stock import StockBalance, StockLedger, StockSerial
from app.models.warehouse import WarehouseBin

#: Which serial state a unit is in while it sits in each stock status.
SERIAL_STATE_FOR = {
    "UNRESTRICTED": SERIAL_IN_STOCK,
    "QUARANTINE": SERIAL_QUARANTINE,
    "REJECTED": SERIAL_REJECTED,
    "BLOCKED": SERIAL_REJECTED,
    "IN_TRANSIT": SERIAL_IN_TRANSIT,
}


class StockPostingError(HTTPException):
    """A posting that cannot go ahead. Reported to the user as 409."""

    def __init__(self, detail: str):
        super().__init__(status_code=409, detail=detail)


@dataclass
class Movement:
    company_id: UUID
    warehouse_id: UUID
    bin_id: UUID
    sku: str
    stock_status: str
    #: Signed: positive in, negative out.
    quantity: Decimal
    movement_type: str
    source_line_id: str
    product_name: str | None = None
    unit_cost: Decimal | None = None
    uom: str | None = None
    #: For serial-numbered products, one per unit; len == abs(quantity).
    serials: list[str] = field(default_factory=list)
    #: What an outgoing serial becomes (DISPATCHED for a sale).
    serial_status_after: str = SERIAL_DISPATCHED
    #: Reserved quantity this movement uses up (a dispatch consuming its
    #: sales order's reservation). Taken off qty_reserved before checking.
    consume_reserved: Decimal = Decimal(0)
    #: Status a serial is expected to have now: PICKED for a dispatch going
    #: out; IN_TRANSIT for a transfer arriving. Empty means "on the shelf
    #: for its stock status" going out, and "new or dispatched" coming in.
    serial_status_before: str | None = None


@dataclass
class Posting:
    source_doc_type: str
    source_doc_id: str
    source_doc_number: str | None
    movement_date: date
    movements: list[Movement]
    created_by: str | None = None
    created_by_name: str | None = None
    approved_by: str | None = None
    approved_by_name: str | None = None
    remarks: str | None = None


def _check_places(db: Session, movements: list[Movement]) -> None:
    """Every warehouse is a warehouse of that company; every bin is in it."""

    for m in movements:
        if m.stock_status not in STOCK_STATUSES:
            raise StockPostingError(f"Unknown stock status {m.stock_status}.")
        if not m.sku:
            raise StockPostingError("A movement has no product.")
        if m.quantity == 0:
            raise StockPostingError(f"{m.sku}: quantity cannot be zero.")

        warehouse = db.query(Location).filter(Location.id == m.warehouse_id).first()
        if warehouse is None or location_type(warehouse) != WAREHOUSE:
            raise StockPostingError("The warehouse was not found.")
        if warehouse.company_id != m.company_id:
            # The rule that keeps two companies' stock apart.
            raise StockPostingError(f"{warehouse.location_name} does not belong to this company.")

        bin_row = db.query(WarehouseBin).filter(WarehouseBin.id == m.bin_id).first()
        if bin_row is None or bin_row.warehouse_id != m.warehouse_id:
            raise StockPostingError(f"The bin for {m.sku} is not in {warehouse.location_name}.")
        if m.quantity > 0 and not bin_row.is_active:
            raise StockPostingError(f"Bin {bin_row.code} is switched off; stock cannot be put into it.")

        if m.serials and len(m.serials) != abs(m.quantity):
            raise StockPostingError(
                f"{m.sku}: {abs(m.quantity)} unit(s) but {len(m.serials)} serial number(s)."
            )
        if len({s.upper() for s in m.serials}) != len(m.serials):
            raise StockPostingError(f"{m.sku}: the same serial number is entered twice.")


def _key(m: Movement) -> tuple:
    return (str(m.company_id), str(m.warehouse_id), str(m.bin_id), m.sku, m.stock_status)


def _lock_balances(db: Session, movements: list[Movement]) -> dict[tuple, StockBalance]:
    """Lock (creating if need be) every balance row this posting touches."""

    keys = sorted({_key(m) for m in movements})
    held: dict[tuple, StockBalance] = {}

    for company_id, warehouse_id, bin_id, sku, status in keys:
        # Created empty if it does not exist yet; two postings creating the
        # same row at once both land on the unique key and one does nothing.
        db.execute(
            pg_insert(StockBalance)
            .values(
                company_id=UUID(company_id),
                warehouse_id=UUID(warehouse_id),
                bin_id=UUID(bin_id),
                sku=sku,
                stock_status=status,
                qty_on_hand=0,
                qty_reserved=0,
                updated_at=datetime.utcnow(),
            )
            .on_conflict_do_nothing(constraint="uq_stock_balance_key")
        )
        row = (
            db.query(StockBalance)
            .filter(
                StockBalance.company_id == UUID(company_id),
                StockBalance.warehouse_id == UUID(warehouse_id),
                StockBalance.bin_id == UUID(bin_id),
                StockBalance.sku == sku,
                StockBalance.stock_status == status,
            )
            .with_for_update()
            .populate_existing()
            .one()
        )
        held[(company_id, warehouse_id, bin_id, sku, status)] = row

    return held


def _move_serials(db: Session, m: Movement, ledger_ids: list[int]) -> None:
    for serial, ledger_id in zip(m.serials, ledger_ids):
        text_serial = serial.strip().upper()
        unit = (
            db.query(StockSerial)
            .filter(StockSerial.company_id == m.company_id, StockSerial.serial_no == text_serial)
            .with_for_update()
            .first()
        )

        if m.quantity > 0:
            # In: a new unit, one that left before (a return) coming back, or
            # one this same posting or transfer has in hand (serial_status_before).
            expected = (SERIAL_DISPATCHED, m.serial_status_before) if m.serial_status_before else (SERIAL_DISPATCHED,)
            if unit is not None and unit.status not in expected:
                raise StockPostingError(f"Serial {text_serial} is already in stock for this company.")
            if unit is None:
                unit = StockSerial(company_id=m.company_id, serial_no=text_serial)
                db.add(unit)
            unit.sku = m.sku
            unit.warehouse_id = m.warehouse_id
            unit.bin_id = m.bin_id
            unit.status = SERIAL_STATE_FOR.get(m.stock_status, SERIAL_IN_STOCK)
            unit.origin_movement_id = ledger_id
        else:
            # Out: it has to be this product, here, and on the shelf.
            if (
                unit is None
                or unit.sku != m.sku
                or unit.warehouse_id != m.warehouse_id
                or unit.bin_id != m.bin_id
                or unit.status != (m.serial_status_before or SERIAL_STATE_FOR.get(m.stock_status, SERIAL_IN_STOCK))
            ):
                raise StockPostingError(f"Serial {text_serial} is not on the shelf here for {m.sku}.")
            unit.status = m.serial_status_after
            unit.warehouse_id = None if m.serial_status_after == SERIAL_DISPATCHED else unit.warehouse_id


def post(db: Session, posting: Posting) -> list[StockLedger]:
    """Write a document's movements. Does not commit; see the module notes."""

    if not posting.movements:
        raise StockPostingError("There is nothing to post.")

    _check_places(db, posting.movements)
    balances = _lock_balances(db, posting.movements)

    created_by = UUID(posting.created_by) if posting.created_by else None
    approved_by = UUID(posting.approved_by) if posting.approved_by else None
    now = datetime.utcnow()
    rows: list[StockLedger] = []

    for m in posting.movements:
        balance = balances[_key(m)]
        on_hand = Decimal(balance.qty_on_hand or 0)
        reserved = Decimal(balance.qty_reserved or 0)
        if m.consume_reserved:
            if m.consume_reserved > reserved:
                raise StockPostingError(f"{m.sku}: only {reserved.normalize()} is reserved here, not {m.consume_reserved.normalize()}.")
            reserved -= m.consume_reserved
            balance.qty_reserved = reserved
        after = on_hand + m.quantity

        if after < 0:
            raise StockPostingError(
                f"Not enough stock of {m.sku}: {on_hand.normalize()} on hand, {abs(m.quantity).normalize()} needed."
            )
        if after < reserved:
            raise StockPostingError(
                f"{m.sku}: {reserved.normalize()} of the stock is reserved for orders and cannot go out this way."
            )

        if m.quantity > 0 and m.unit_cost is not None:
            held_value = on_hand * Decimal(balance.avg_cost or m.unit_cost)
            balance.avg_cost = (held_value + m.quantity * m.unit_cost) / after

        balance.qty_on_hand = after
        balance.updated_at = now
        if m.product_name:
            balance.product_name = m.product_name

        # A serial-numbered line is one ledger row per unit, so each unit's
        # history can be followed on its own; otherwise one row.
        pieces = (
            [(s.strip().upper(), Decimal(1) if m.quantity > 0 else Decimal(-1)) for s in m.serials]
            if m.serials
            else [(None, m.quantity)]
        )

        line_rows = []
        for index, (serial, quantity) in enumerate(pieces):
            row = StockLedger(
                company_id=m.company_id,
                warehouse_id=m.warehouse_id,
                bin_id=m.bin_id,
                sku=m.sku,
                product_name=m.product_name,
                serial_no=serial,
                movement_type=m.movement_type,
                stock_status=m.stock_status,
                quantity=quantity,
                uom=m.uom,
                unit_cost=m.unit_cost,
                source_doc_type=posting.source_doc_type,
                source_doc_id=str(posting.source_doc_id),
                source_doc_number=posting.source_doc_number,
                source_line_id=m.source_line_id,
                movement_date=posting.movement_date,
                posted_at=now,
                created_by=created_by,
                created_by_name=posting.created_by_name,
                approved_by=approved_by,
                approved_by_name=posting.approved_by_name,
                idempotency_key=(
                    f"{posting.source_doc_type}:{posting.source_doc_id}:{m.source_line_id}:"
                    f"{m.stock_status}:{serial or index}"
                ),
                remarks=posting.remarks,
            )
            db.add(row)
            line_rows.append(row)

        try:
            db.flush()
        except IntegrityError as error:
            if "idempotency_key" in str(error.orig):
                raise StockPostingError("This document has already been posted.") from error
            raise

        if m.serials:
            _move_serials(db, m, [r.id for r in line_rows])
            try:
                db.flush()
            except IntegrityError as error:
                if "uq_stock_serial_company_serial" in str(error.orig):
                    raise StockPostingError(f"A serial on {m.sku} is already in stock for this company.") from error
                raise

        rows.extend(line_rows)

    return rows


# ----------------------------------------------------------------------
# Reservations: held for an order, still on hand
# ----------------------------------------------------------------------
def reserve(db: Session, company_id: UUID, warehouse_id: UUID, sku: str, wanted: Decimal) -> list[tuple[StockBalance, Decimal]]:
    """Hold ``wanted`` units of available stock in a warehouse's store bins.

    Locks the warehouse's UNRESTRICTED balance rows for the SKU, takes from
    them in bin order, and raises qty_reserved; on hand is untouched.
    Refuses, changing nothing, if not enough is available. Returns the
    (balance, quantity) taken from each bin. Does not commit.
    """

    if wanted <= 0:
        raise StockPostingError(f"{sku}: reserve a quantity above zero.")

    rows = (
        db.query(StockBalance)
        .join(WarehouseBin, WarehouseBin.id == StockBalance.bin_id)
        .filter(
            StockBalance.company_id == company_id,
            StockBalance.warehouse_id == warehouse_id,
            StockBalance.sku == sku,
            StockBalance.stock_status == "UNRESTRICTED",
            WarehouseBin.bin_type == "STORE",
        )
        .order_by(WarehouseBin.code.asc(), StockBalance.id.asc())
        .with_for_update(of=StockBalance)
        .populate_existing()
        .all()
    )
    available = sum((Decimal(r.qty_on_hand) - Decimal(r.qty_reserved) for r in rows), Decimal(0))
    if available < wanted:
        raise StockPostingError(
            f"Only {available.normalize()} of {sku} is available in this warehouse; {wanted.normalize()} was asked for."
        )

    taken = []
    left = wanted
    for row in rows:
        free = Decimal(row.qty_on_hand) - Decimal(row.qty_reserved)
        if free <= 0:
            continue
        take = min(free, left)
        row.qty_reserved = Decimal(row.qty_reserved) + take
        row.updated_at = datetime.utcnow()
        taken.append((row, take))
        left -= take
        if left == 0:
            break
    return taken


def release(db: Session, company_id: UUID, warehouse_id: UUID, bin_id: UUID, sku: str, quantity: Decimal) -> None:
    """Give back reserved stock in one bin. Does not commit."""

    row = (
        db.query(StockBalance)
        .filter(
            StockBalance.company_id == company_id,
            StockBalance.warehouse_id == warehouse_id,
            StockBalance.bin_id == bin_id,
            StockBalance.sku == sku,
            StockBalance.stock_status == "UNRESTRICTED",
        )
        .with_for_update()
        .populate_existing()
        .one()
    )
    if Decimal(row.qty_reserved) < quantity:
        raise StockPostingError(f"{sku}: cannot release {quantity.normalize()}; only {Decimal(row.qty_reserved).normalize()} is reserved.")
    row.qty_reserved = Decimal(row.qty_reserved) - quantity
    row.updated_at = datetime.utcnow()
