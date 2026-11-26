"""Gate passes: made when goods leave, numbered per company per year.

Two places send goods out of the gate:
  * Stock Out: committing a dispatch order. If the dispatch was given a
    gate pass number by hand that number is kept; otherwise one is
    generated and written onto the dispatch.
  * Logistics: marking a sales order out for delivery. When the order
    already left on a dispatch with its own pass, that pass is the one;
    otherwise a pass is made from what was issued to the order.

The number is "{financial year}/{running number}", e.g. 2026-27/001, kept
per company: the company row is locked while the next number is taken, so
two passes made at once cannot get the same one.

Reading a pass follows what it came from: a dispatch's pass needs
dispatch.read and the dispatch's store; an order's pass follows who may
read the order on the desks (FulfilmentDeskService.assert_can_view).
"""

from datetime import date

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.stock import DO_DELIVERED, DO_DISPATCHED, VIEW_DISPATCH
from app.core.timezone import now_ist
from app.models.company import Company
from app.models.dispatch import DispatchOrder
from app.models.gate_pass import GatePass
from app.models.sales_order import SalesOrder
from app.services.company_scope_service import CompanyScopeService
from app.services.procurement.common import actor
from app.services.stock import sheet_pdf

SOURCE_DISPATCH = "DISPATCH"
SOURCE_OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"

PURPOSE = "Sale"
RETURNABLE = "Non-Returnable"


def financial_year(day: date) -> str:
    """April to March: 25 Nov 2026 is in 2026-27, 10 Feb 2027 too."""

    start = day.year if day.month >= 4 else day.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def _next_number(company_id, day: date, db: Session) -> tuple[str, int, str]:
    year = financial_year(day)
    # Held until the caller commits: the next pass of this company waits.
    if db.query(Company.id).filter(Company.id == company_id).with_for_update().first() is None:
        raise HTTPException(status_code=400, detail="The company to number this gate pass under was not found.")
    last = (
        db.query(func.max(GatePass.seq))
        .filter(GatePass.company_id == company_id, GatePass.financial_year == year)
        .scalar()
    )
    seq = int(last or 0) + 1
    return year, seq, f"{year}/{seq:03d}"


def _party(order: SalesOrder | None, address: dict | None) -> dict:
    info = (order.customer_information or {}) if order else {}
    name = (order.company_name if order else None) or info.get("organization_name") or (order.customer_name if order else "")
    parts = []
    if isinstance(address, dict):
        parts = [
            str(address.get(key)).strip()
            for key in ("street", "city", "state", "pin", "zipCode", "country")
            if address.get(key) and str(address.get(key)).strip()
        ]
    elif address:
        parts = [str(address)]
    return {"name": name or "", "address": ", ".join(parts), "gstin": info.get("gst") or ""}


def _rows_from_dispatches(dispatches: list[DispatchOrder]) -> list[dict]:
    rows = []
    for do in dispatches:
        for line in do.lines:
            serials = line.serials or []
            if serials:
                rows += [
                    {"description": line.product_name or line.sku, "detail": line.sku, "serial": s, "qty": 1,
                     "purpose": PURPOSE, "returnable": RETURNABLE}
                    for s in serials
                ]
            else:
                rows.append({"description": line.product_name or line.sku, "detail": line.sku, "serial": "",
                             "qty": float(line.qty), "purpose": PURPOSE, "returnable": RETURNABLE})
    return rows


def _rows_from_order(order: SalesOrder) -> tuple[list[dict], str | None]:
    """What was issued to the order (by serial), or its goods lines.

    Also the company the issued units belong to, for an order with no
    selling company on it.
    """

    from app.services.stock.dispatch import is_stock_line
    from app.services.stock_movement_service import OUT, movements_col

    issued: dict[int, list[str]] = {}
    company = None
    for row in movements_col.find({"order_id": int(order.id), "direction": OUT}):
        serial = str(row.get("serial_number") or "")
        line_serials = issued.setdefault(int(row.get("line") or 0), [])
        # An order held and issued again logs the unit twice; it is one unit.
        if serial and serial not in line_serials:
            line_serials.append(serial)
        company = company or row.get("company_id")

    rows = []
    for index, item in enumerate(order.items or []):
        sku = str(item.get("sku") or item.get("model") or "").strip()
        description = item.get("product") or item.get("name") or item.get("description") or sku
        serials = issued.get(index, [])
        if serials:
            rows += [{"description": description, "detail": sku, "serial": s, "qty": 1,
                      "purpose": PURPOSE, "returnable": RETURNABLE} for s in serials]
        elif is_stock_line(sku):
            quantity = float(item.get("qty") or item.get("quantity") or 0)
            if quantity > 0:
                rows.append({"description": description, "detail": sku, "serial": "", "qty": quantity,
                             "purpose": PURPOSE, "returnable": RETURNABLE})
    return rows, (str(company) if company else None)


def for_dispatch(do: DispatchOrder, order: SalesOrder, current_user: dict, db: Session) -> GatePass:
    """The pass for a dispatch being committed. Does not commit."""

    existing = db.query(GatePass).filter(GatePass.dispatch_order_id == do.id).first()
    if existing is not None:
        return existing

    today = now_ist().date()
    typed = (do.gate_pass_no or "").strip()
    if typed:
        year, seq, number = financial_year(today), None, typed
    else:
        year, seq, number = _next_number(do.company_id, today, db)
        do.gate_pass_no = number

    user_id, name = actor(current_user, db)
    gp = GatePass(
        company_id=do.company_id, warehouse_id=do.warehouse_id, financial_year=year, seq=seq, gate_pass_no=number,
        gate_pass_date=today, source=SOURCE_DISPATCH, sales_order_id=order.id, dispatch_order_id=do.id,
        order_number=order.order_number,
        dc_invoice_no=do.invoice_no or do.challan_no, dc_invoice_date=do.challan_date,
        transporter=do.transporter, docket_no=do.docket_no, eway_bill_no=do.eway_bill_no,
        vehicle_number=do.vehicle_number, driver_name=do.driver_name,
        bill_to=_party(order, order.billing_address),
        ship_to=_party(order, order.shipping_address or order.billing_address) if not do.ship_to
        else {**_party(order, None), "address": do.ship_to},
        lines=_rows_from_dispatches([do]), created_by=user_id, created_by_name=name,
    )
    db.add(gp)
    db.flush()
    return gp


def for_out_for_delivery(order: SalesOrder, current_user: dict, db: Session) -> GatePass:
    """The pass for an order Logistics sends out. Does not commit.

    Refused (400) when the order's company cannot be told - nothing to
    number the pass under.
    """

    existing = (
        db.query(GatePass)
        .filter(GatePass.sales_order_id == order.id)
        .order_by(GatePass.id.asc())
        .first()
    )
    if existing is not None:
        # Made at Dispatched and now going out: the vehicle and driver are
        # the ones on the order now, if they were changed in between.
        if existing.source == SOURCE_OUT_FOR_DELIVERY and existing.dispatch_order_id is None:
            existing.vehicle_number = order.delivery_vehicle_number or existing.vehicle_number
            existing.driver_name = order.delivery_driver_name or existing.driver_name
        return existing

    dispatches = (
        db.query(DispatchOrder)
        .filter(DispatchOrder.sales_order_id == order.id, DispatchOrder.status.in_((DO_DISPATCHED, DO_DELIVERED)))
        .order_by(DispatchOrder.id.asc())
        .all()
    )
    if dispatches:
        rows, from_units = _rows_from_dispatches(dispatches), None
        company_id = dispatches[0].company_id
    else:
        rows, from_units = _rows_from_order(order)
        company_id = order.selling_company_id or from_units

    if not company_id:
        raise HTTPException(
            status_code=400,
            detail=f"Order {order.order_number} has no selling company, so its gate pass cannot be numbered. "
                   "Set the company on the order, then generate the gate pass.",
        )
    if not rows:
        raise HTTPException(status_code=400, detail=f"Order {order.order_number} has no goods to put on a gate pass.")

    from app.services.fulfilment_desk_service import _latest_invoice

    invoice = _latest_invoice(order, db)
    today = now_ist().date()
    year, seq, number = _next_number(company_id, today, db)
    user_id, name = actor(current_user, db)
    first = dispatches[0] if dispatches else None
    gp = GatePass(
        company_id=company_id, warehouse_id=(first.warehouse_id if first else order.dispatch_warehouse_id),
        financial_year=year, seq=seq, gate_pass_no=number, gate_pass_date=today, source=SOURCE_OUT_FOR_DELIVERY,
        sales_order_id=order.id, order_number=order.order_number,
        dc_invoice_no=(first.invoice_no or first.challan_no) if first else (invoice.pi_number if invoice else None),
        dc_invoice_date=(first.challan_date if first else None),
        transporter=first.transporter if first else None, docket_no=first.docket_no if first else None,
        eway_bill_no=first.eway_bill_no if first else None,
        vehicle_number=order.delivery_vehicle_number or (first.vehicle_number if first else None),
        driver_name=order.delivery_driver_name or (first.driver_name if first else None),
        bill_to=_party(order, order.billing_address),
        ship_to=_party(order, order.shipping_address or order.billing_address),
        lines=rows, created_by=user_id, created_by_name=name,
    )
    db.add(gp)
    db.flush()
    return gp


# ----------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------
def _assert_can_view(gp: GatePass, current_user: dict, db: Session) -> None:
    if current_user.get("is_super_admin"):
        return

    if gp.dispatch_order_id:
        from app.middleware.permission_middleware import require_permission

        require_permission(VIEW_DISPATCH)(current_user=current_user, db=db)
        if gp.warehouse_id:
            CompanyScopeService.assert_warehouse_allowed(current_user, gp.warehouse_id, db)
        return

    from app.services.fulfilment_desk_service import FulfilmentDeskService

    order = db.query(SalesOrder).filter(SalesOrder.id == gp.sales_order_id).first()
    if order is None:
        raise HTTPException(status_code=404, detail="Gate pass not found.")
    try:
        FulfilmentDeskService.assert_can_view(order, current_user, db)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Gate pass not found.")


def letterhead(company_id, db: Session) -> dict:
    company = db.query(Company).filter(Company.id == company_id).first()
    if company is None:
        return {"company_name": ""}
    address = ", ".join(
        p for p in (company.address_line_1, company.address_line_2, company.city, company.state, company.postal_code) if p
    )
    return {"company_name": company.company_name, "address": address, "gst_number": company.gst_number,
            "cin_number": company.cin_number,
            "phone_number": company.phone_number, "email": company.email}


def gate_pass_dict(gp: GatePass) -> dict:
    return {
        "id": gp.id,
        "gate_pass_no": gp.gate_pass_no,
        "gate_pass_date": gp.gate_pass_date.isoformat() if gp.gate_pass_date else None,
        "financial_year": gp.financial_year,
        "company_id": str(gp.company_id),
        "source": gp.source,
        "sales_order_id": gp.sales_order_id,
        "dispatch_order_id": gp.dispatch_order_id,
        "order_number": gp.order_number,
        "dc_invoice_no": gp.dc_invoice_no,
        "dc_invoice_date": gp.dc_invoice_date.isoformat() if gp.dc_invoice_date else None,
        "transporter": gp.transporter,
        "docket_no": gp.docket_no,
        "eway_bill_no": gp.eway_bill_no,
        "vehicle_number": gp.vehicle_number,
        "driver_name": gp.driver_name,
        "bill_to": gp.bill_to or {},
        "ship_to": gp.ship_to or {},
        "lines": gp.lines or [],
        "total_qty": sum(float(x.get("qty") or 0) for x in gp.lines or []),
        "created_by_name": gp.created_by_name,
        "created_at": gp.created_at.isoformat() if gp.created_at else None,
    }


def get(gate_pass_id: int, current_user: dict, db: Session) -> tuple[GatePass, dict]:
    gp = db.query(GatePass).filter(GatePass.id == gate_pass_id).first()
    if gp is None:
        raise HTTPException(status_code=404, detail="Gate pass not found.")
    _assert_can_view(gp, current_user, db)
    return gp, {"gate_pass": gate_pass_dict(gp), "letterhead": letterhead(gp.company_id, db)}


def list_for(current_user: dict, db: Session, *, sales_order_id: int | None = None,
             dispatch_order_id: int | None = None) -> list[dict]:
    if sales_order_id is None and dispatch_order_id is None:
        raise HTTPException(status_code=400, detail="Name the order or the dispatch.")
    query = db.query(GatePass)
    if sales_order_id is not None:
        query = query.filter(GatePass.sales_order_id == sales_order_id)
    if dispatch_order_id is not None:
        query = query.filter(GatePass.dispatch_order_id == dispatch_order_id)
    found = []
    for gp in query.order_by(GatePass.id.asc()).all():
        try:
            _assert_can_view(gp, current_user, db)
        except HTTPException as refused:
            if refused.status_code not in (403, 404):
                raise
            continue
        found.append(gate_pass_dict(gp))
    return found


def generate_for_order(order_id: int, current_user: dict, db: Session, vehicle_number: str | None = None,
                       driver_name: str | None = None) -> dict:
    """Logistics generating the gate pass from the order.

    At Dispatched - before it goes out - with the vehicle and driver taking
    it, which are kept on the order as marking it out for delivery keeps
    them; marking it out then uses this same pass. Or for an order already
    out (sent before passes were made, or when one could not be made then).
    """

    from app.core.fulfilment import LOGISTICS, holds_desk
    from app.core.workflow_status import SalesOrderStatus
    from app.services.fulfilment_desk_service import _user

    order = db.query(SalesOrder).filter(SalesOrder.id == order_id).with_for_update().first()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found.")
    if not holds_desk(_user(current_user, db), LOGISTICS, is_super_admin=bool(current_user.get("is_super_admin"))):
        raise HTTPException(status_code=403, detail="Gate passes are made by logistics.")
    if order.status == SalesOrderStatus.DISPATCHED:
        vehicle = " ".join((vehicle_number or "").split()).upper()
        driver = " ".join((driver_name or "").split())
        missing = [label for label, value in (("vehicle number", vehicle), ("driver name", driver)) if not value]
        if missing:
            raise HTTPException(status_code=400, detail=f"Enter the {' and '.join(missing)} for the gate pass.")
        order.delivery_vehicle_number = vehicle
        order.delivery_driver_name = driver
    elif order.status not in (SalesOrderStatus.OUT_FOR_DELIVERY, SalesOrderStatus.DELIVERED, SalesOrderStatus.INSTALLED):
        raise HTTPException(
            status_code=409,
            detail=f"Order {order.order_number} is not dispatched yet; the gate pass is made when it leaves.",
        )
    gp = for_out_for_delivery(order, current_user, db)
    db.commit()
    db.refresh(gp)
    return gate_pass_dict(gp)


# ----------------------------------------------------------------------
# PDF, in the gate pass sheet layout
# ----------------------------------------------------------------------
#: Column widths in Excel units, from the Gate Pass workbook (A..G).
GP_WIDTHS = [6.7, 36.6, 33.4, 6.7, 13.3, 22.3, 20.0]
GP_TITLE_FILL = "#DEEAF6"
GP_TOTAL_FILL = "#BCD6ED"
#: Item rows on the sheet (rows 12..34), filled out with empty rows.
GP_ITEM_ROWS = 23


def render(data: dict, head: dict) -> bytes:
    """The gate pass as the warehouse's Gate Pass workbook lays it out."""

    g = sheet_pdf.ExcelGrid(GP_WIDTHS)
    letter = "<br/>".join(sheet_pdf.text(x) for x in sheet_pdf.letterhead_lines(head))
    g.row([g.letterhead(letter, size=11, family="serif", color="#1F3864", height=110.25)] + [None] * 6,
          height=110.25, spans=[(0, 6)])
    g.row([{"value": "Gate Pass", "size": 18, "bold": True, "align": "center"}] + [None] * 6,
          height=23.25, spans=[(0, 6)], fills={(0, 6): GP_TITLE_FILL}, valign="top")

    small = {"size": 9, "bold": True}
    vehicle = f"Vehicle No : {data.get('vehicle_number') or ''}"
    if data.get("driver_name"):
        vehicle += f"  |  Driver : {data['driver_name']}"
    for (label, value, right), height in zip((
        ("Gate Pass No. :-", f"G.P. No- {data['gate_pass_no']}", f"Transport Mode: {data.get('transporter') or ''}"),
        ("Gate Pass Date :-", sheet_pdf.sheet_date(data.get("gate_pass_date")), f"Transport ID- {data.get('docket_no') or ''}"),
        ("DC  /  Invoice No :-", data.get("dc_invoice_no") or "", f"E-way bill No- {data.get('eway_bill_no') or ''}"),
        ("DC  /  Invoice Date:", sheet_pdf.sheet_date(data.get("dc_invoice_date")), vehicle),
    ), (24.75, 21.75, 21.75, 21.75)):
        g.row([{"value": label, **small}, None, {"value": value, **small}, None, None, {"value": right, **small}, None],
              height=height, spans=[(0, 1), (2, 4), (5, 6)], valign="top")

    bill, ship = data.get("bill_to") or {}, data.get("ship_to") or {}
    g.row([{"value": "Bill To", **small}, None, None, {"value": "Ship To", **small}, None, None, None],
          height=25.5, spans=[(0, 2), (3, 6)], fills={(0, 6): GP_TITLE_FILL}, valign="top")
    for label, key, size, height in (("Name : ", "name", 10, 21), ("Address : ", "address", 10, 36.75),
                                     ("GSTIN/UIN: ", "gstin", 9, 27)):
        g.row([{"value": label + (bill.get(key) or ""), "size": size, "bold": True}, None, None,
               {"value": label + (ship.get(key) or ""), "size": size, "bold": True}, None, None, None],
              height=height, spans=[(0, 2), (3, 6)], valign="top")

    header = {"size": 12, "bold": True}
    g.row([{"value": "S. No.", **header, "align": "center"}, {"value": "Description of Materials", **header},
           {"value": "Serial No.", **header}, {"value": "QTY", **header}, {"value": "Purpose", **header},
           {"value": "Returnable / Non Returnable", **header, "align": "center"}, None],
          height=28.5, spans=[(5, 6)])

    used = 0
    for number, line in enumerate(data.get("lines") or [], start=1):
        g.row([{"value": number, "size": 12, "align": "center"}, {"value": line.get("description") or "", "size": 11},
               {"value": line.get("serial") or "", "size": 11}, {"value": sheet_pdf.sheet_qty(line.get("qty")), "size": 11,
                                                                 "align": "center"},
               {"value": line.get("purpose") or "", "size": 12, "align": "center"},
               {"value": line.get("returnable") or "", "size": 12, "align": "center"}, None],
              height=15.75, spans=[(5, 6)])
        used += 1
        # The model on its own row under the product, as the sheet writes it.
        if line.get("detail") and line.get("detail") != line.get("description"):
            g.row([None, {"value": line["detail"], "size": 11}, None, None, None, None, None], height=15.75, spans=[(5, 6)])
            used += 1
    for _ in range(max(0, GP_ITEM_ROWS - used)):
        g.row([None] * 7, height=15.75, spans=[(5, 6)])

    g.row([{"value": "Total", "size": 12, "bold": True, "align": "center"}, None, None,
           {"value": sheet_pdf.sheet_qty(data.get("total_qty")), "size": 12, "bold": True, "align": "center"}, None, None, None],
          height=15.75, spans=[(0, 2), (5, 6)], fills={(0, 2): GP_TOTAL_FILL})

    for department in ("Warehouse Department", "Operation Department", "Accounts Department"):
        top = g.row([{"value": department, "size": 12, "bold": True}] + [None] * 6, height=30, valign="top")
        g.row([None] * 7, height=30)
        g.span_rows(0, top, 6, top + 1)

    return g.render(f"Gate Pass {data['gate_pass_no']}")


def filename(data: dict) -> str:
    return f"Gate_Pass_{sheet_pdf.safe_filename(data['gate_pass_no'])}.pdf"
