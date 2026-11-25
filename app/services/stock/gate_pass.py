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


def generate_for_order(order_id: int, current_user: dict, db: Session) -> dict:
    """Logistics making the pass for an order already out for delivery
    (sent out before passes were made, or when it could not be made then)."""

    from app.core.fulfilment import LOGISTICS, holds_desk
    from app.core.workflow_status import SalesOrderStatus
    from app.services.fulfilment_desk_service import _user

    order = db.query(SalesOrder).filter(SalesOrder.id == order_id).with_for_update().first()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found.")
    if not holds_desk(_user(current_user, db), LOGISTICS, is_super_admin=bool(current_user.get("is_super_admin"))):
        raise HTTPException(status_code=403, detail="Gate passes are made by logistics.")
    if order.status not in (SalesOrderStatus.OUT_FOR_DELIVERY, SalesOrderStatus.DELIVERED, SalesOrderStatus.INSTALLED):
        raise HTTPException(status_code=409, detail=f"Order {order.order_number} has not gone out for delivery yet.")
    gp = for_out_for_delivery(order, current_user, db)
    db.commit()
    db.refresh(gp)
    return gate_pass_dict(gp)


# ----------------------------------------------------------------------
# PDF, in the gate pass sheet layout
# ----------------------------------------------------------------------
def render(data: dict, head: dict) -> bytes:
    # S.No | Description | Serial | Qty | Purpose | Returnable
    sheet = sheet_pdf.Sheet([7, 36, 25, 8, 11, 17])
    sheet.full(sheet_pdf.letterhead_markup(head), align="center", markup=True)
    sheet.full("Gate Pass", bold=True, align="center")

    for (left_label, left_value), right in (
        (("Gate Pass No. :-", f"G.P. No- {data['gate_pass_no']}"), f"Transport Mode: {data.get('transporter') or ''}"),
        (("Gate Pass Date :-", sheet_pdf.sheet_date(data.get("gate_pass_date"))),
         f"Transport ID / Docket- {data.get('docket_no') or ''}"),
        (("DC  /  Invoice No :-", data.get("dc_invoice_no") or ""), f"E-way bill No- {data.get('eway_bill_no') or ''}"),
        (("DC  /  Invoice Date:", sheet_pdf.sheet_date(data.get("dc_invoice_date"))),
         f"Vehicle No : {data.get('vehicle_number') or ''}"
         + (f"  |  Driver: {data['driver_name']}" if data.get("driver_name") else "")),
    ):
        sheet.row([left_label, left_value, "", "", right, ""], spans=[(1, 3), (4, 5)], bold={0})

    bill, ship = data.get("bill_to") or {}, data.get("ship_to") or {}
    sheet.row(["Bill To", "", "", "Ship To", "", ""], spans=[(0, 2), (3, 5)], bold=True, shade=True)
    for label, key in (("Name", "name"), ("Address", "address"), ("GSTIN/UIN", "gstin")):
        sheet.row([f"{label} : {bill.get(key) or ''}", "", "", f"{label} : {ship.get(key) or ''}", "", ""],
                  spans=[(0, 2), (3, 5)])

    sheet.row(["S. No.", "Description of Materials", "Serial No.", "QTY", "Purpose", "Returnable / Non Returnable"],
              bold=True, shade=True, align=["left", "left", "left", "right", "left", "left"])
    for number, line in enumerate(data.get("lines") or [], start=1):
        description = line.get("description") or ""
        if line.get("detail") and line.get("detail") != description:
            description += f"\n{line['detail']}"
        sheet.row([number, description, line.get("serial") or "", sheet_pdf.sheet_qty(line.get("qty")),
                   line.get("purpose") or "", line.get("returnable") or ""],
                  align=["left", "left", "left", "right", "left", "left"])
    sheet.row(["Total", "", "", sheet_pdf.sheet_qty(data.get("total_qty")), "", ""], spans=[(0, 2)], bold=True,
              align=["left", "left", "left", "right", "left", "left"])

    for department in ("Warehouse Department", "Operation Department", "Accounts Department"):
        sheet.full(department, bold=True, height=34)

    return sheet.render(f"Gate Pass {data['gate_pass_no']}")


def filename(data: dict) -> str:
    return f"Gate_Pass_{sheet_pdf.safe_filename(data['gate_pass_no'])}.pdf"
