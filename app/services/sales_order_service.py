import time
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.timezone import iso
from app.core.fulfilment import desk_for, holds_desk
from app.core.approvals import price_type_for
from app.core.listing import page_of, search_clause
from app.core.workflow_status import (
    SALES_ORDER_TRANSITIONS,
    ProformaInvoiceStatus,
    SalesOrderStatus,
    assert_transition,
    normalize_sales_order_status,
)
from app.models.approval import ApprovalDocument, ApprovalStatus, SalesApproval
from app.models.opportunity_activity import OpportunityActivity
from app.models.quotation import Quotation
from app.models.sales_order import SalesOrder
from app.models.sales_order_activity import SalesOrderActivity
from app.models.user import User
from app.repositories.opportunity_repository import OpportunityRepository
from app.repositories.sales_order_repository import SalesOrderRepository
from app.services.fulfilment_notice import record_stage_change
from app.services.lead_service import get_visible_creator_user_ids
from app.services.notification_service import NotificationService

#: Headline written onto the activity entry when an order reaches a status.
SALES_ORDER_STATUS_ACTIONS: dict[str, str] = {
    SalesOrderStatus.DRAFT: "Sales Order Created",
    SalesOrderStatus.PENDING_APPROVAL: "Sent For Approval",
    SalesOrderStatus.CONFIRMED: "Order Approved",
    SalesOrderStatus.ADVANCE_ACCEPTED: "Advance Accepted",
    SalesOrderStatus.ORDER_PROCESSED: "Order Processed",
    SalesOrderStatus.PAYMENT_VERIFIED: "Payment Verified",
    SalesOrderStatus.PROCUREMENT: "In Production",
    SalesOrderStatus.READY: "Ready To Dispatch",
    SalesOrderStatus.DISPATCHED: "Order Dispatched",
    SalesOrderStatus.OUT_FOR_DELIVERY: "Out For Delivery",
    SalesOrderStatus.DELIVERED: "Order Delivered",
    SalesOrderStatus.INSTALLED: "Installation Completed",
    SalesOrderStatus.ON_HOLD: "Order Put On Hold",
    SalesOrderStatus.RELEASED: "Order Released",
    SalesOrderStatus.COMPLETED: "Order Completed",
    SalesOrderStatus.CANCELLED: "Order Cancelled",
}


def check_order_details(
    customer_information: dict | None,
    billing_address: dict | None,
    shipping_address: dict | None,
) -> None:
    """Refuse an order whose numbers are the wrong shape.

    The same rules the form checks as it is typed (app/core/field_checks),
    so nothing that skips the form - an import, a script, an old screen -
    can store a nine-character PAN or a five-digit PIN. Only what was
    typed is checked: an empty field is a draft's business. An
    unregistered customer has no GST or PAN to check. A PIN is held to six
    digits only in India.
    """

    from app.core.field_checks import check_email, check_gstin, check_mobile, check_pan, check_pin

    info = customer_information or {}

    if str(info.get("registration") or "").strip() != "Unregistered":
        check_gstin(info.get("gst"), field="GST")
        check_pan(info.get("pan"))

    contact = info.get("primary_contact") or {}

    if isinstance(contact, dict):
        check_mobile(contact.get("phone"), field="Phone")
        check_email(contact.get("email"), field="Email")

    for label, address in (("Billing", billing_address), ("Shipping", shipping_address)):
        if not isinstance(address, dict):
            continue

        country = str(address.get("country") or "").strip().lower()

        if country in ("", "india"):
            check_pin(address.get("pin") or address.get("zip_code"), field=f"{label} PIN code")


def assert_ready_to_send(order: SalesOrder) -> None:
    """What an order must carry before it leaves draft.

    A registered organisation has a PAN, and an order committing the
    business to one is not sent up or confirmed without it. A draft may
    still be saved without; it is work in progress.
    """

    info = order.customer_information or {}

    if (
        str(info.get("registration") or "").strip() == "Registered"
        and not str(info.get("pan") or "").strip()
    ):
        raise HTTPException(
            status_code=400,
            detail="PAN is required for a registered organisation.",
        )


#: What the business coordinator does not see: orders the business has
#: not committed to yet, and ones cancelled before they were.
NOT_FOR_COORDINATOR = (
    SalesOrderStatus.DRAFT,
    SalesOrderStatus.PENDING_APPROVAL,
    SalesOrderStatus.CANCELLED,
)


def coordinates_orders(current_user: dict, db: Session) -> bool:
    """Whether this reader is the business coordinator.

    The coordinator follows every approved order across the business -
    they check its advance and process it, then keep track of it to
    delivery - so they read outside the sales reporting line. Reading
    only: nothing here lets them change an order.
    """

    from app.core.fulfilment import BUSINESS_COORDINATOR

    if current_user.get("is_super_admin"):
        return False

    user = db.query(User).filter(User.id == current_user.get("user_id")).first()

    return holds_desk(user, BUSINESS_COORDINATOR)


def _staffs_the_desk(order: SalesOrder, current_user: dict, db: Session) -> bool:
    """Whether this user works the desk the order is sitting at."""

    desk = desk_for(order.status)

    if desk is None:
        return False

    user = db.query(User).filter(User.id == current_user.get("user_id")).first()

    return holds_desk(user, desk.role)


#: Stages a desk can only move on from its own screen, because the move
#: carries something that has to be checked: the coordinator's advance and
#: Process Order, production's serials, logistics' dispatch documents.
DESK_ONLY_STAGES = {
    SalesOrderStatus.CONFIRMED,
    SalesOrderStatus.ADVANCE_ACCEPTED,
    SalesOrderStatus.PROCUREMENT,
    SalesOrderStatus.READY,
}


def assert_desk_allows(
    order: SalesOrder,
    target: str,
    current_user: dict,
    db: Session,
) -> None:
    """Keep a stage move with the desk that owns it.

    Once an order is approved it stops being the salesperson's to push:
    accounts say the money arrived, inventory say the stock is there. Any
    role can still put an order on hold or cancel it - that is a business
    decision, not a desk's confirmation - and a super admin can do
    anything, so a company with nobody in a role is never stuck.
    """

    if current_user.get("is_super_admin"):
        return

    desk = desk_for(order.status)

    if desk is None or target == order.status:
        return

    if target in (SalesOrderStatus.ON_HOLD, SalesOrderStatus.CANCELLED):
        return

    user = db.query(User).filter(User.id == current_user.get("user_id")).first()

    if holds_desk(user, desk.role):
        if order.status in DESK_ONLY_STAGES:
            # These stages ask for something with the move - the advance,
            # the serials, the dispatch documents - which only the desk's
            # own screen collects and checks.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Move order {order.order_number or order.id} on from the "
                    f"{desk.role} desk. {desk.asks}"
                ),
            )

        return

    raise HTTPException(
        status_code=403,
        detail=(
            f"Order {order.order_number or order.id} is with the {desk.role} "
            f"desk. {desk.asks} Only {desk.role} can move it on."
        ),
    )


def _to_uuid(value) -> UUID | None:
    if not value:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


#: SKU -> {"hsn": ..., "ecp": ..., "dtp": ...}, read from inventory and held
#: for a minute. The pickers offer whatever inventory holds now, so a product
#: uploaded after start-up has to be priced here too, not only after a
#: restart.
_CATALOGUE_BY_SKU: dict[str, dict] | None = None
_CATALOGUE_READ_AT = 0.0
CATALOGUE_TTL_SECONDS = 60


def _catalogue() -> dict[str, dict]:
    global _CATALOGUE_BY_SKU, _CATALOGUE_READ_AT

    if _CATALOGUE_BY_SKU is None or time.monotonic() - _CATALOGUE_READ_AT > CATALOGUE_TTL_SECONDS:
        _CATALOGUE_READ_AT = time.monotonic()

        try:
            from app.database.mongodb import sync_mongo_db

            rows = list(
                sync_mongo_db["inventory_items"].find(
                    {},
                    {
                        "serial_number": 1,
                        "attributes.model_number": 1,
                        "attributes.hsn_code": 1,
                        "attributes.dtp_rate": 1,
                        "attributes.rate": 1,
                        "attributes.rate_per_unit": 1,
                        "attributes.warranty_rates": 1,
                    },
                )
            )

            def entry(row: dict) -> dict:
                attributes = row.get("attributes") or {}

                return {
                    "hsn": str(attributes.get("hsn_code") or "").strip(),
                    "dtp": _as_float(attributes.get("dtp_rate")),
                    "ecp": _as_float(
                        attributes.get("rate") or attributes.get("rate_per_unit")
                    ),
                    "warranty_rates": attributes.get("warranty_rates") or {},
                }

            _CATALOGUE_BY_SKU = {
                str(row.get("serial_number") or "").upper(): entry(row)
                for row in rows
            }

            # The Add Product pickers read inventory, where units of one
            # model are one product and the model number is the line's
            # SKU. A serial keeps its own entry if the two ever collide.
            for row in rows:
                model = str(
                    (row.get("attributes") or {}).get("model_number") or ""
                ).strip().upper()

                if model:
                    _CATALOGUE_BY_SKU.setdefault(model, entry(row))
        except Exception:  # noqa: BLE001 - a lookup must never fail a save
            _CATALOGUE_BY_SKU = {}

    return _CATALOGUE_BY_SKU


def _hsn_for_sku(sku: str) -> str:
    """The HSN the catalogue holds against a SKU, or "" if it holds none."""

    return _catalogue().get(str(sku or "").upper(), {}).get("hsn", "")


def dtp_rate_for_sku(sku: str) -> float:
    """The fixed transfer price for a SKU, or 0 when none is set.

    Zero means the product has no dealer price yet, not that it is free.
    A document written at DTP keeps the end customer rate in that case and
    says so, rather than quietly quoting nothing.
    """

    return _catalogue().get(str(sku or "").upper(), {}).get("dtp", 0.0)


def rate_for_sku(sku: str, price_type: str | None = None) -> float | None:
    """What the price list says this SKU costs, or None if it is not on it.

    The price a document is written at is the company's to decide, not the
    browser's. A form that posts its own unit price can post any unit
    price - a panel at one rupee passes every other check, because the
    totals are recomputed faithfully from a figure that was already a lie.

    None means the SKU is not in the catalogue at all, which is a line
    somebody typed by hand rather than picked, and those keep what they
    were sent. Zero means the list genuinely holds no rate.
    """

    entry = _catalogue().get(str(sku or "").upper())

    if entry is None:
        return None

    if str(price_type or "").upper() in {"DP", "DTP"}:
        # A product with no dealer rate yet falls back to the end customer
        # rate rather than quoting nothing, the same way dtp_rate_for_sku
        # is read everywhere else.
        return entry.get("dtp") or entry.get("ecp", 0.0)

    return entry.get("ecp", 0.0)


def warranty_uplift_for_sku(
    sku: str, term_name: str | None, unit_price: float, quantity: float
) -> float:
    """What extending to this term costs on this product.

    The lengths are a master, so every document offers the same three. What
    they cost is not: five years on a panel and five years on a camera are
    different undertakings, so the rate is held against the product and
    read from the catalogue the same way the price is.

    A product with no rate for the term adds nothing, which covers both a
    term that is simply not offered on it and the standard cover that is
    included in the price. It fails towards not charging rather than
    charging at a figure nobody set.
    """

    entry = (
        _catalogue()
        .get(str(sku or "").upper(), {})
        .get("warranty_rates", {})
        .get(str(term_name or "").strip())
    )

    if not entry:
        return 0.0

    rate = _as_float(entry.get("rate"))

    if rate <= 0:
        return 0.0

    if str(entry.get("mode") or "").upper() == "AMOUNT":
        return rate * float(quantity or 0.0)

    return float(unit_price or 0.0) * float(quantity or 0.0) * rate / 100.0


def _items_to_json(items) -> list | None:
    """Lines as stored, with the two things every document needs settled.

    The quantity is written into both ``qty`` and ``quantity_case``. The
    older screens write one and the documents read the other, so a line
    saved from the quotation form printed a quantity of zero on the
    invoice - and an amount of zero beside it - while the totals, computed
    from the other key, were right. One line disagreeing with the total
    under it is worse than either being wrong on its own.

    The HSN is filled from the catalogue when the line arrives without
    one. It is a property of the product, so a line that omits it has not
    been classified differently - it has just lost the code on the way,
    and without it the invoice groups the tax under a dash.
    """

    if items is None:
        return None

    rows = [
        item.dict() if hasattr(item, "dict") else dict(item)
        for item in items
    ]

    for row in rows:
        quantity = _as_float(
            row.get("qty") or row.get("quantity_case") or row.get("quantity")
        )
        row["qty"] = quantity
        row["quantity_case"] = quantity

        if not str(row.get("hsn") or "").strip():
            found = _hsn_for_sku(row.get("sku"))
            if found:
                row["hsn"] = found

    return rows


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default



def _opportunity_from_quotation(quotation_id: str | None, db: Session) -> int | None:
    """Resolve the opportunity behind a quotation reference.

    The New Sales Order screen links an order to the quotation it was raised
    against, not to an opportunity - so without this the chain stops there and
    the order's Activity History loses everything that happened before it was
    raised. The quotation already knows its opportunity; this follows that
    link rather than asking the user for an id they have no way of knowing.
    """

    reference = (quotation_id or "").strip()

    if not reference:
        return None

    quotation = (
        db.query(Quotation)
        .filter(Quotation.quote_number == reference)
        .first()
    )

    return quotation.opportunity_id if quotation else None


def _confirmed_onwards(target: str) -> bool:
    """Whether this status is past the point of being a draft.

    Everything from Confirmed on commits the company to the price, so the
    discount has to have been signed off before any of it.
    """

    return target not in (
        SalesOrderStatus.DRAFT,
        SalesOrderStatus.PENDING_APPROVAL,
        SalesOrderStatus.CANCELLED,
    )


def compute_order_totals(
    items,
    price_type: str | None = None,
    discount_mode: str | None = None,
    discount_input: float | None = None,
    orc_mode: str | None = None,
    orc_input: float | None = None,
    freight_charges: float = 0.0,
    installation_lumpsum: float = 0.0,
    shifting_charges: float = 0.0,
    gst_percent: float = 18.0,
    advance_received: float = 0.0,
) -> dict:
    """Derive every money figure on the order from its line items.

    Mirrors compute_totals in quotation_service so both screens agree, and
    is always recomputed on write so the stored figures cannot drift from
    the lines - or be forged by a client posting its own totals.

        subtotal     = SUM(qty * unit price)
        discount     = SUM(line * discount%), or the summary override
        taxable      = subtotal - discount + freight + installation
        gst          = taxable * gst%
        grand total  = taxable + gst
        outstanding  = grand total - advance received

    And what the order is actually worth to us, which is a different
    question from what the customer pays:

        revenue      = grand total - ORC - freight - installation
                                   - shifting - gst

    Delivery and installation are passed straight through, the GST goes to
    the government, and shifting is a cost we carry.

    The ORC and the shifting charge are recorded but do not move what the
    order is worth. Neither is something the customer is billed for - the
    ORC is a commission we pay out and shifting is a cost we absorb - so
    they belong against the margin rather than against the order value.
    The ORC used to come off the taxable amount, which quietly reduced the
    GST and the grand total, meaning the customer's own invoice moved when
    somebody recorded a commission.

    The unit price is read from the catalogue by SKU rather than taken from
    the request, for the reason set out in compute_totals: recomputing
    faithfully from a figure the browser chose protects nothing.
    """

    # Settled values are written back into the list the caller holds, not
    # into a copy: that list is what gets stored, and a line reading
    # "rate 0" under a total of 147,000 is a document arguing with itself.
    rows = _items_to_json(items) or []

    subtotal = 0.0
    discount_amount = 0.0

    for row in rows:
        quantity = _as_float(
            row.get("quantity_case")
            or row.get("qty")
            or row.get("quantity")
            or 1,
            1.0,
        )
        listed = rate_for_sku(row.get("sku"), price_type)
        price = (
            listed
            if listed is not None
            else _as_float(row.get("rate") or row.get("price") or 0)
        )

        # Store the line at the price it was costed at, so the document and
        # the totals cannot disagree later.
        row["rate"] = price
        row["price"] = price

        # Extended cover is sold on top of the line, at the rate the
        # product holds for the chosen term - never at a figure the form
        # sends.
        #
        # The proforma invoice passes neither a price type nor rates: it
        # bills what the order agreed and must not re-price itself because
        # somebody edited the product afterwards, so it keeps the figure
        # already on the line. That figure was worked out by the order,
        # server side, so it is ours rather than the browser's.
        if price_type is None:
            warranty_uplift = _as_float(row.get("warranty_uplift"))
        else:
            warranty_uplift = warranty_uplift_for_sku(
                row.get("sku"), row.get("warranty_term"), price, quantity
            )

        row["warranty_uplift"] = round(warranty_uplift, 2)

        line = quantity * price + warranty_uplift

        subtotal += line
        discount_amount += line * _as_float(row.get("discount")) / 100.0

    if isinstance(items, list):
        items[:] = rows

    discount_mode = (discount_mode or "AMOUNT").upper()

    if discount_input is not None:
        typed = _as_float(discount_input)
        discount_amount = (
            subtotal * typed / 100.0 if discount_mode == "PERCENT" else typed
        )

    discount_amount = max(0.0, min(discount_amount, subtotal))

    orc_mode = (orc_mode or "AMOUNT").upper()
    orc_amount = 0.0
    orc_percent = 0.0

    if orc_input is not None:
        typed = _as_float(orc_input)

        if orc_mode == "PERCENT":
            orc_percent = typed
            orc_amount = subtotal * typed / 100.0
        else:
            orc_amount = typed
            orc_percent = orc_amount / subtotal * 100.0 if subtotal else 0.0

    freight_charges = _as_float(freight_charges)
    installation_lumpsum = _as_float(installation_lumpsum)
    shifting_charges = _as_float(shifting_charges)

    # What the customer is billed: the discount comes off, then delivery
    # and installation are added back. The ORC and the shifting charge are
    # deliberately absent - see above - so recording either one cannot move
    # the customer's invoice.
    taxable_amount = (
        subtotal
        - discount_amount
        + freight_charges
        + installation_lumpsum
    )

    gst_percent = _as_float(gst_percent, 18.0)
    gst_amount = taxable_amount * gst_percent / 100.0
    grand_total = taxable_amount + gst_amount

    # What the order leaves us once everything that was never ours has
    # been taken back out.
    total_revenue = (
        grand_total
        - orc_amount
        - freight_charges
        - installation_lumpsum
        - shifting_charges
        - gst_amount
    )

    # An advance cannot exceed the order, and the balance never goes negative.
    advance_received = max(0.0, min(_as_float(advance_received), grand_total))

    return {
        "total_amount": round(subtotal, 2),
        "discount_amount": round(discount_amount, 2),
        "discount_mode": discount_mode,
        "discount_input": discount_input,
        "orc_amount": round(orc_amount, 2),
        "orc_percent": round(orc_percent, 4),
        "orc_mode": orc_mode,
        "orc_input": orc_input,
        "freight_charges": round(freight_charges, 2),
        "installation_lumpsum": round(installation_lumpsum, 2),
        "shifting_charges": round(shifting_charges, 2),
        "taxable_amount": round(taxable_amount, 2),
        "gst_percent": round(gst_percent, 2),
        "gst_amount": round(gst_amount, 2),
        "grand_total": round(grand_total, 2),
        "total_revenue": round(total_revenue, 2),
        "advance_received": round(advance_received, 2),
        "outstanding_balance": round(grand_total - advance_received, 2),
    }


def _assert_po_not_after_order(po_date, order_date) -> None:
    """A purchase order cannot be dated after the order raised against it.

    The two dates were taken on trust, so an order created on the 16th
    could carry a PO dated the 18th - and the order's own process
    timeline then said "Customer PO Received" two days into the future,
    on a step that had supposedly already happened.
    """

    if not po_date or not order_date:
        return

    def day(value):
        return value.date() if hasattr(value, "date") else value

    if day(po_date) > day(order_date):
        raise HTTPException(
            status_code=400,
            detail=(
                "The customer's PO is dated after the sales order raised "
                "against it. A PO comes first, so the order date cannot "
                "be earlier than the PO date."
            ),
        )


class SalesOrderService:

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    #: What a search box looks through on the Sales Order screen.
    SEARCHABLE = [
        "order_number", "customer_name", "company_name", "customer_type",
        "state", "po_number", "assigned_to", "sales_executive", "status",
    ]

    @staticmethod
    def visible_query(
        current_user: dict,
        db: Session,
        search: str | None = None,
        status: str | None = None,
    ):
        """The orders this person may see, narrowed by a search."""

        if coordinates_orders(current_user, db):
            # Every approved order in the business, whoever raised it.
            query = SalesOrderRepository.visible_query(db, []).filter(
                SalesOrder.status.notin_(NOT_FOR_COORDINATOR)
            )
        else:
            query = SalesOrderRepository.visible_query(
                db, get_visible_creator_user_ids(current_user, db)
            )

        clause = search_clause(
            search,
            [getattr(SalesOrder, name) for name in SalesOrderService.SEARCHABLE],
        )

        if clause is not None:
            query = query.filter(clause)

        # The status tabs. Filtered here rather than over the page that
        # came back, so "Draft 12" means twelve drafts rather than however
        # many of them happened to land on this page.
        if status:
            query = query.filter(SalesOrder.status == status)

        return query

    @staticmethod
    def get_visible(current_user: dict, db: Session) -> list[SalesOrder]:
        return SalesOrderService.visible_query(current_user, db).all()

    @staticmethod
    def summarise(
        current_user: dict,
        db: Session,
        search: str | None = None,
    ) -> dict:
        """The four figures across the top of the Sales Order screen.

        The same definitions the screen used when it counted them in the
        browser: how many orders, what they are worth, how many are
        waiting (draft or on hold) and how many are finished.
        """

        from sqlalchemy import or_

        from app.core.listing import measure

        query = SalesOrderService.visible_query(current_user, db, search)

        figures = measure(
            query,
            SalesOrder.created_at,
            {
                "total": {},
                "order_value": {"sum": SalesOrder.grand_total},
                "pending": {
                    "where": or_(
                        SalesOrder.status == "DRAFT",
                        SalesOrder.status == "ON_HOLD",
                    )
                },
                "completed": {"where": SalesOrder.status == "COMPLETED"},
            },
        )

        # One count per status, for the tabs. Raw statuses: the screen
        # knows which label each one is shown under, and two of them share
        # a label.
        from sqlalchemy import func

        figures["counts"] = {
            str(status or ""): int(n)
            for status, n in (
                query.order_by(None)
                .with_entities(SalesOrder.status, func.count())
                .group_by(SalesOrder.status)
                .all()
            )
        }

        return figures

    @staticmethod
    def get_visible_page(
        current_user: dict,
        db: Session,
        search: str | None = None,
        page: int | None = None,
        size: int | None = None,
        status: str | None = None,
    ) -> tuple[list[SalesOrder], int]:
        """One page of them, and how many there are in all."""

        return page_of(
            SalesOrderService.visible_query(current_user, db, search, status), page, size
        )

    @staticmethod
    def get_by_id(order_id: int, db: Session) -> SalesOrder:
        order = SalesOrderRepository.get_by_id(db, order_id)

        if order is None:
            raise HTTPException(status_code=404, detail="Sales order not found")

        return order

    # ------------------------------------------------------------------
    # Authorisation
    # ------------------------------------------------------------------
    @staticmethod
    def assert_can_view(
        order: SalesOrder,
        current_user: dict,
        db: Session,
    ) -> None:
        """Whether this person may open the order.

        Everyone who may change it, and also whoever has been asked to
        decide on it. An approver sits outside the raiser's reporting
        line by design - that is the point of sending it upwards - so the
        rule that governs editing refused the one person the order was
        waiting on, and the founder got it in their pending list and a
        permission error when they clicked it.

        Reading is not editing: this grants no more than opening it.
        """

        from app.services.approval_service import ApprovalService

        if ApprovalService.is_approver_of(
            ApprovalDocument.SALES_ORDER, order.id, current_user, db
        ):
            return

        # The coordinator tracks every approved order to delivery.
        if order.status not in NOT_FOR_COORDINATOR and coordinates_orders(
            current_user, db
        ):
            return

        SalesOrderService.assert_can_edit(order, current_user, db)

    @staticmethod
    def assert_can_edit(
        order: SalesOrder,
        current_user: dict,
        db: Session,
    ) -> None:
        if current_user.get("is_super_admin", False):
            return

        user_id = current_user.get("user_id")

        # Orders migrated from Mongo may have no creator recorded.
        if order.creator_id is None:
            return

        if str(order.creator_id) == user_id:
            return

        visible_ids = get_visible_creator_user_ids(current_user, db)

        if visible_ids and str(order.creator_id) in visible_ids:
            return

        # A desk holding the order stands outside the sales reporting line:
        # accounts verify the whole company's payments, and inventory ship
        # for everybody. Refusing them here would leave orders stuck.
        if _staffs_the_desk(order, current_user, db):
            return

        raise HTTPException(
            status_code=403,
            detail=(
                "Permission denied. You can only modify orders created by "
                "yourself or your subordinates."
            ),
        )

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_opportunity(opportunity_id, db: Session) -> None:
        """Reject a link to an opportunity that does not exist."""

        if opportunity_id is None:
            return

        if OpportunityRepository.get_by_id(db, opportunity_id) is None:
            raise HTTPException(
                status_code=400,
                detail=f"Opportunity {opportunity_id} does not exist.",
            )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    @staticmethod
    def send_to_desks(order_id: int, current_user: dict, db: Session) -> SalesOrder:
        """Process an approved order on to accounts.

        The business coordinator's button, and nobody else's: they check
        the advance against the payment terms first, and only once they
        have accepted it can the order be processed. Processing moves it to
        accounts, who verify the payment as they always have, and tells
        them it is there.
        """

        from app.core.fulfilment import is_coordinator
        from app.models.proforma_invoice import ProformaInvoice
        from app.models.user import User
        from app.services.fulfilment_desk_service import FulfilmentDeskService
        from app.services.fulfilment_notice import announce_processing

        order = SalesOrderService.get_by_id(order_id, db)

        user = db.query(User).filter(User.id == current_user.get("user_id")).first()

        # The coordinator's button, and the super admin's, who stands in.
        if not current_user.get("is_super_admin") and not is_coordinator(user):
            raise HTTPException(
                status_code=403,
                detail="Only the business coordinator can process an order.",
            )

        if order.status == SalesOrderStatus.CONFIRMED:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{order.order_number or order.id} is waiting on its advance. "
                    "Record and accept the advance before processing it."
                ),
            )

        if order.status != SalesOrderStatus.ADVANCE_ACCEPTED:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{order.order_number or order.id} cannot be processed from "
                    f"{order.status.replace('_', ' ').title()}."
                ),
            )

        order = FulfilmentDeskService.decide(order.id, True, None, current_user, db)

        invoice = (
            db.query(ProformaInvoice)
            .filter(ProformaInvoice.sales_order_id == order.id)
            .order_by(ProformaInvoice.id.desc())
            .first()
        )

        if invoice is not None:
            name = f"{current_user.get('first_name', '')} {current_user.get('last_name', '')}".strip()
            announce_processing(order, invoice, db, name or None)

        return order

    @staticmethod
    def create(request, current_user: dict, db: Session) -> SalesOrder:
        SalesOrderService._validate_opportunity(request.opportunity_id, db)

        check_order_details(
            request.customer_information,
            request.billing_address,
            request.shipping_address,
        )

        opportunity_id = request.opportunity_id or _opportunity_from_quotation(
            request.quotation_id,
            db,
        )

        status = normalize_sales_order_status(request.status)

        # An order is born a draft. Everything from Confirmed on commits
        # the company to the price, and that is the approval chain's to
        # grant - so it cannot be claimed on the way in. The New Sales
        # Order screen used to post CONFIRMED straight from its "Send For
        # Approval" button, which is how an order came into being already
        # approved, with no chain, no signature and nothing to show on
        # the approvals screen.
        if _confirmed_onwards(status):
            raise HTTPException(
                status_code=400,
                detail=(
                    "A sales order starts as a draft. Send it for approval "
                    "and it is confirmed once the chain has cleared it."
                ),
            )

        _assert_po_not_after_order(request.po_date, request.order_date)

        first_name = current_user.get("first_name", "")
        last_name = current_user.get("last_name", "")
        creator_name = f"{first_name} {last_name}".strip() or "User"

        order = SalesOrder(
            order_number=(
                request.order_number
                or SalesOrderRepository.next_order_number(db)
            ),
            opportunity_id=opportunity_id,
            status=status,
            customer_name=request.customer_name,
            company_name=request.company_name,
            customer_type=request.customer_type,
            state=request.state,
            order_date=request.order_date,
            quotation_id=request.quotation_id,
            po_number=request.po_number,
            po_date=request.po_date,
            assigned_to=request.assigned_to,
            sales_executive=request.sales_executive,
            customer_information=request.customer_information,
            billing_address=request.billing_address,
            shipping_address=request.shipping_address,
            items=_items_to_json(request.items),
            aging_0_30=request.aging_0_30 or 0.0,
            aging_31_60=request.aging_31_60 or 0.0,
            aging_61_90=request.aging_61_90 or 0.0,
            aging_91_120=request.aging_91_120 or 0.0,
            aging_121_180=request.aging_121_180 or 0.0,
            aging_above_180=request.aging_above_180 or 0.0,
            remarks=request.remarks,
            advance_percent=(
                request.advance_percent
                if request.advance_percent is not None
                else 30.0
            ),
            commercial_terms=request.commercial_terms,
            payment_terms=request.payment_terms,
            technical_notes=request.technical_notes,
            attachments=request.attachments,
            creator_id=_to_uuid(current_user.get("user_id")),
            creator_name=creator_name,
            **compute_order_totals(
                request.items,
                price_type=price_type_for(request.customer_type),
                discount_mode=getattr(request, "discount_mode", None),
                discount_input=getattr(request, "discount_input", None),
                orc_mode=getattr(request, "orc_mode", None),
                orc_input=getattr(request, "orc_input", None),
                freight_charges=getattr(request, "freight_charges", 0.0) or 0.0,
                installation_lumpsum=(
                    getattr(request, "installation_lumpsum", 0.0) or 0.0
                ),
                shifting_charges=(
                    getattr(request, "shifting_charges", 0.0) or 0.0
                ),
                gst_percent=(
                    getattr(request, "gst_percent", None)
                    if getattr(request, "gst_percent", None) is not None
                    else 18.0
                ),
                advance_received=(
                    getattr(request, "advance_received", 0.0) or 0.0
                ),
            ),
        )

        order = SalesOrderRepository.create(db, order)

        # Opens the timeline with the event that started it, so the detail
        # page shows real history rather than an empty panel.
        SalesOrderService.record_activity(
            db,
            order,
            action=SALES_ORDER_STATUS_ACTIONS.get(status, "Sales Order Created"),
            description=(
                f"Sales order {order.order_number} created"
                + (" for the opportunity." if order.opportunity_id else ".")
            ),
            to_status=status,
            user_id=current_user.get("user_id"),
        )

        return order

    @staticmethod
    def update(
        order_id: int,
        request,
        current_user: dict,
        db: Session,
    ) -> SalesOrder:
        order = SalesOrderService.get_by_id(order_id, db)

        SalesOrderService.assert_can_edit(order, current_user, db)

        check_order_details(
            getattr(request, "customer_information", None),
            getattr(request, "billing_address", None),
            getattr(request, "shipping_address", None),
        )

        if getattr(request, "opportunity_id", None) is not None:
            SalesOrderService._validate_opportunity(request.opportunity_id, db)

        simple_fields = [
            "customer_name",
            "opportunity_id",
            "company_name",
            "customer_type",
            "state",
            "order_date",
            "quotation_id",
            "po_number",
            "po_date",
            "assigned_to",
            "sales_executive",
            "customer_information",
            "billing_address",
            "shipping_address",
            "aging_0_30",
            "aging_31_60",
            "aging_61_90",
            "aging_91_120",
            "aging_121_180",
            "aging_above_180",
            "remarks",
            "advance_percent",
            "commercial_terms",
            "payment_terms",
            "technical_notes",
            "attachments",
        ]

        for field in simple_fields:
            value = getattr(request, field, None)
            if value is not None:
                setattr(order, field, value)

        # Checked after the fields are applied, so a change to either
        # date is measured against the other as it now stands.
        _assert_po_not_after_order(order.po_date, order.order_date)

        if (
            getattr(request, "quotation_id", None) is not None
            and not order.opportunity_id
        ):
            order.opportunity_id = _opportunity_from_quotation(
                request.quotation_id,
                db,
            )

        if getattr(request, "items", None) is not None:
            order.items = _items_to_json(request.items)

        # Any change to lines or charges re-derives every figure, so the
        # stored totals always match the lines they came from.
        money_fields = (
            "items", "discount_mode", "discount_input", "orc_mode",
            "orc_input", "freight_charges", "installation_lumpsum",
            "shifting_charges", "gst_percent", "advance_received",
        )

        if any(getattr(request, field, None) is not None for field in money_fields):
            def pick(field, fallback):
                value = getattr(request, field, None)
                return fallback if value is None else value

            totals = compute_order_totals(
                order.items,
                price_type=price_type_for(order.customer_type),
                discount_mode=pick("discount_mode", order.discount_mode),
                discount_input=pick("discount_input", order.discount_input),
                orc_mode=pick("orc_mode", order.orc_mode),
                orc_input=pick("orc_input", order.orc_input),
                freight_charges=pick("freight_charges", order.freight_charges),
                installation_lumpsum=pick(
                    "installation_lumpsum", order.installation_lumpsum
                ),
                shifting_charges=pick("shifting_charges", order.shifting_charges),
                gst_percent=pick("gst_percent", order.gst_percent or 18.0),
                advance_received=pick(
                    "advance_received", order.advance_received
                ),
            )

            for key, value in totals.items():
                setattr(order, key, value)

            # The figures have changed, so the thing that was approved is
            # not the thing we now hold. An order past Draft goes back to
            # it and climbs the chain again - otherwise a signed-off 10%
            # could be edited to 40% afterwards and stay approved, and
            # the invoice already raised would be asking for the wrong
            # money.
            SalesOrderService._reopen_for_approval(order, current_user, db)

        return SalesOrderRepository.save(db, order)

    @staticmethod
    def _reopen_for_approval(order, current_user: dict, db: Session) -> None:
        """Send a priced-up order back to Draft after its figures change.

        Nothing happens to an order that is still a draft - it has not
        been approved, so there is nothing to withdraw and nothing to
        invalidate.
        """

        from app.models.proforma_invoice import ProformaInvoice
        from app.services.approval_service import ApprovalService

        if order.status == SalesOrderStatus.DRAFT:
            return

        was = order.status

        # Every approval that still stands, not just one waiting to be
        # decided. A signature already given was given to figures that no
        # longer exist, and leaving it in place let a changed order be
        # confirmed straight back on the strength of it - which is the
        # whole thing this is here to stop.
        from app.models.approval import SalesApproval

        standing = (
            db.query(SalesApproval)
            .filter(
                SalesApproval.document_type == ApprovalDocument.SALES_ORDER,
                SalesApproval.document_id == order.id,
                SalesApproval.status.in_(
                    [ApprovalStatus.PENDING, ApprovalStatus.APPROVED]
                ),
            )
            .all()
        )

        for approval in standing:
            approval.status = ApprovalStatus.WITHDRAWN
            approval.remarks = (
                "Superseded - the order was changed after this was decided."
            )

        # An invoice raised against the old figures asks for the wrong
        # money. Cancelled rather than deleted: it was a real document,
        # and it may already have been sent.
        stale = (
            db.query(ProformaInvoice)
            .filter(
                ProformaInvoice.sales_order_id == order.id,
                ProformaInvoice.status != ProformaInvoiceStatus.CANCELLED,
            )
            .all()
        )

        for invoice in stale:
            invoice.status = ProformaInvoiceStatus.CANCELLED

        order.status = SalesOrderStatus.DRAFT

        name = f"{current_user.get('first_name', '')} {current_user.get('last_name', '')}".strip()

        SalesOrderService.record_activity(
            db,
            order,
            action="Reopened For Approval",
            description=(
                "The order was changed after approval, so it goes back up "
                "the chain."
                + (
                    f" {len(stale)} proforma invoice(s) cancelled."
                    if stale
                    else ""
                )
            ),
            from_status=was,
            to_status=SalesOrderStatus.DRAFT,
            user_id=current_user.get("user_id"),
            commit=False,
        )

    # ------------------------------------------------------------------
    # Activity History
    # ------------------------------------------------------------------
    @staticmethod
    def record_activity(
        db: Session,
        order: SalesOrder,
        action: str,
        description: str | None = None,
        from_status: str | None = None,
        to_status: str | None = None,
        user_id: str | None = None,
        commit: bool = True,
    ) -> SalesOrderActivity:
        """Append one entry to a sales order's Activity History."""

        activity = SalesOrderActivity(
            sales_order_id=order.id,
            action=action,
            description=(description or None),
            from_status=from_status,
            to_status=to_status,
            created_by=_to_uuid(user_id) if user_id else None,
        )

        db.add(activity)

        # Everyone who owns or oversees the record hears about it.
        NotificationService.notify_activity(
            db, "sales_order", order, action, description, user_id
        )

        if commit:
            db.commit()
            db.refresh(activity)

        return activity

    @staticmethod
    def get_activities(
        order_id: int,
        current_user: dict,
        db: Session,
        *,
        access_checked: bool = False,
    ) -> list[dict]:
        """The order's history, newest first, with the originating
        opportunity's entries merged in.

        The detail page reads as one story - the opportunity was raised, a
        demo happened, a proposal went out, the order was created - so the
        opportunity's half is merged on read rather than copied at creation,
        which would go stale the moment the opportunity moved on.

        ``access_checked`` is for a caller that has already decided the
        reader may see this order on its own terms - the desk screens, where
        an accounts or inventory person may read an order the reporting line
        would refuse them. Everyone else falls through to the reporting rule.
        """

        order = SalesOrderService.get_by_id(order_id, db)

        if not access_checked:
            # Reading the history is reading, so an approver gets it too -
            # the panel sits on the same page as the Approve button.
            SalesOrderService.assert_can_view(order, current_user, db)

        rows: list[dict] = [
            {
                "id": f"so-{a.id}",
                "source": "order",
                "action": a.action,
                "description": a.description,
                "from_status": a.from_status,
                "to_status": a.to_status,
                "created_by": str(a.created_by) if a.created_by else None,
                "created_at": a.created_at,
            }
            for a in db.query(SalesOrderActivity)
            .filter(SalesOrderActivity.sales_order_id == order.id)
            .all()
        ]

        if order.opportunity_id:
            rows.extend(
                {
                    "id": f"opp-{a.id}",
                    "source": "opportunity",
                    "action": a.action,
                    "description": a.description,
                    "from_status": a.from_status,
                    "to_status": a.to_status,
                    "created_by": str(a.created_by) if a.created_by else None,
                    "created_at": a.created_at,
                }
                for a in db.query(OpportunityActivity)
                .filter(OpportunityActivity.opportunity_id == order.opportunity_id)
                .all()
            )

        rows.sort(key=lambda row: row["created_at"], reverse=True)

        return rows

    @staticmethod
    def log_activity(
        order_id: int,
        request,
        current_user: dict,
        db: Session,
    ) -> tuple[SalesOrder, SalesOrderActivity]:
        """Record an activity, moving the order's status when one was chosen."""

        order = SalesOrderService.get_by_id(order_id, db)

        SalesOrderService.assert_can_edit(order, current_user, db)

        remarks = (getattr(request, "remarks", None) or "").strip()
        raw_status = getattr(request, "status", None)

        if not raw_status and not remarks:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Choose a status or write remarks before logging the "
                    "activity."
                ),
            )

        current_status = order.status
        action = (getattr(request, "action", None) or "").strip()
        target = None

        if raw_status:
            target = str(raw_status).upper()

            if target not in SalesOrderStatus.ALL:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Invalid sales order status '{raw_status}'. "
                        f"Expected one of: {', '.join(SalesOrderStatus.ALL)}."
                    ),
                )

            assert_transition(
                "sales order",
                SALES_ORDER_TRANSITIONS,
                current_status,
                target,
            )

            assert_desk_allows(order, target, current_user, db)

            order.status = target

            if not action:
                action = SALES_ORDER_STATUS_ACTIONS.get(target, "Status Updated")
        elif not action:
            action = "Note Logged"

        activity = SalesOrderService.record_activity(
            db,
            order,
            action=action,
            description=remarks,
            from_status=current_status,
            to_status=target,
            user_id=current_user.get("user_id"),
            commit=False,
        )

        db.add(order)
        db.commit()
        db.refresh(order)
        db.refresh(activity)

        if target and target != current_status:
            record_stage_change(
                order,
                db,
                previous=current_status,
                actor_name=current_user.get("name") or current_user.get("email"),
                actor_id=current_user.get("user_id"),
                remarks=remarks or None,
            )

        return order, activity

    @staticmethod
    def _assert_discount_signed_off(order, target: str, db) -> None:
        """An order cannot be committed to before the discount is signed.

        This is the point the approval moved to. A proposal is a price put
        in front of a customer to see what they say; the order is the
        commitment, so the chain runs here and nothing past Draft happens
        until it has cleared.

        Draft, Pending Approval and Cancelled are all reachable without
        it: the first two are where an order sits while it is being
        decided, and an order nobody wants should not need three
        signatures before it can be called off.
        """

        if not _confirmed_onwards(target):
            return

        from app.core.approvals import price_type_for
        from app.models.approval import ApprovalDocument
        from app.models.user import User
        from app.services.approval_service import ApprovalService

        # Whether this order needs signing depends on who raised it: a
        # discount inside their own authority is theirs to give. Asked
        # about the creator, which is the same question raising the
        # approval asks - otherwise an order could be refused for wanting
        # an approval nobody was allowed to raise.
        raiser = (
            db.query(User).filter(User.id == order.creator_id).first()
            if order.creator_id
            else None
        )

        chain = ApprovalService.chain_for(
            price_type_for(order.customer_type),
            ApprovalService._deepest_discount(
                ApprovalDocument.SALES_ORDER, order.id, db
            )
            or 0.0,
            raiser,
            db,
            ApprovalDocument.SALES_ORDER,
        )

        if not chain:
            return

        if ApprovalService.is_approved(
            ApprovalDocument.SALES_ORDER, order.id, db
        ):
            return

        waiting = ApprovalService.open_for(
            ApprovalDocument.SALES_ORDER, order.id, db
        )

        if waiting is not None:
            role = waiting.steps[waiting.current_step]["role"]

            raise HTTPException(
                status_code=409,
                detail=(
                    f"{order.order_number} is waiting on the {role} to "
                    "approve the discount. It cannot move on until that is "
                    "cleared."
                ),
            )

        raise HTTPException(
            status_code=409,
            detail=(
                f"{order.order_number} has not been approved yet. The "
                f"discount needs {' then the '.join(chain)} - use Send For "
                "Approval first."
            ),
        )

    @staticmethod
    def update_status(
        order_id: int,
        request,
        current_user: dict,
        db: Session,
    ) -> SalesOrder:
        order = SalesOrderService.get_by_id(order_id, db)

        SalesOrderService.assert_can_edit(order, current_user, db)

        target = str(request.status or "").upper()

        if target not in SalesOrderStatus.ALL:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid sales order status '{request.status}'. "
                    f"Expected one of: {', '.join(SalesOrderStatus.ALL)}."
                ),
            )

        previous_status = order.status

        assert_transition(
            "sales order",
            SALES_ORDER_TRANSITIONS,
            previous_status,
            target,
        )

        assert_desk_allows(order, target, current_user, db)

        SalesOrderService._assert_discount_signed_off(order, target, db)

        if previous_status == SalesOrderStatus.DRAFT and target in (
            SalesOrderStatus.PENDING_APPROVAL,
            SalesOrderStatus.CONFIRMED,
        ):
            assert_ready_to_send(order)

        order.status = target

        # The list's row menu still moves orders through this endpoint, so it
        # has to leave the same trail the detail page's Log Activity does.
        if target != previous_status:
            SalesOrderService.record_activity(
                db,
                order,
                action=SALES_ORDER_STATUS_ACTIONS.get(target, "Status Updated"),
                description=getattr(request, "remarks", None),
                from_status=previous_status,
                to_status=target,
                user_id=current_user.get("user_id"),
                commit=False,
            )

        saved = SalesOrderRepository.save(db, order)

        if target != previous_status:
            record_stage_change(
                saved,
                db,
                previous=previous_status,
                actor_name=current_user.get("name") or current_user.get("email"),
                actor_id=current_user.get("user_id"),
                remarks=getattr(request, "remarks", None),
            )

        return saved

    @staticmethod
    def delete(order_id: int, current_user: dict, db: Session) -> None:
        order = SalesOrderService.get_by_id(order_id, db)

        SalesOrderService.assert_can_edit(order, current_user, db)

        # The approvals go with it. They were outliving the order and the
        # ids are handed out by a sequence, so an approval left behind is
        # one waiting to be read as the history of whatever order gets
        # that id next - which is how an order nobody had decided came up
        # already approved.
        db.query(SalesApproval).filter(
            SalesApproval.document_type == ApprovalDocument.SALES_ORDER,
            SalesApproval.document_id == order.id,
        ).delete(synchronize_session=False)

        # And its bell entries, which otherwise outlive it and open a page
        # for an order that is gone - as proposals and opportunities do.
        NotificationService.delete_for_entity(db, "sales_order", order.id)

        SalesOrderRepository.delete(db, order)


def serialize_sales_order(order: SalesOrder) -> dict:
    """Shape a SalesOrder for the API.

    ``_id`` is emitted alongside ``id`` because the existing Sales Order UI
    keys rows off ``_id`` from the previous Mongo implementation.
    """

    return {
        "id": order.id,
        "_id": str(order.id),
        "order_number": order.order_number,
        "sales_order_id": order.order_number,
        "opportunity_id": order.opportunity_id,
        "status": order.status,
        "customer_name": order.customer_name,
        "company_name": order.company_name,
        "customer_type": order.customer_type,
        "state": order.state,
        "order_date": iso(order.order_date) if order.order_date else None,
        "quotation_id": order.quotation_id,
        "po_number": order.po_number,
        "po_date": iso(order.po_date) if order.po_date else None,
        "assigned_to": order.assigned_to,
        "sales_executive": order.sales_executive,
        "customer_information": order.customer_information,
        "billing_address": order.billing_address,
        "shipping_address": order.shipping_address,
        "items": order.items or [],
        "total_amount": order.total_amount,
        "discount_amount": order.discount_amount,
        "gst_amount": order.gst_amount,
        "grand_total": order.grand_total,
        "taxable_amount": order.taxable_amount or 0.0,
        "orc_amount": order.orc_amount or 0.0,
        "orc_percent": order.orc_percent or 0.0,
        "orc_mode": order.orc_mode or "AMOUNT",
        "orc_input": order.orc_input,
        "discount_mode": order.discount_mode or "AMOUNT",
        "discount_input": order.discount_input,
        "freight_charges": order.freight_charges or 0.0,
        "installation_lumpsum": order.installation_lumpsum or 0.0,
        "shifting_charges": order.shifting_charges or 0.0,
        "total_revenue": order.total_revenue or 0.0,
        "gst_percent": order.gst_percent if order.gst_percent is not None else 18.0,
        "advance_received": order.advance_received or 0.0,
        "outstanding_balance": order.outstanding_balance or 0.0,
        "aging_0_30": order.aging_0_30,
        "aging_31_60": order.aging_31_60,
        "aging_61_90": order.aging_61_90,
        "aging_91_120": order.aging_91_120,
        "aging_121_180": order.aging_121_180,
        "aging_above_180": order.aging_above_180,
        "remarks": order.remarks,
        "advance_percent": (
            order.advance_percent if order.advance_percent is not None else 30.0
        ),
        # Derived here rather than in the UI so the Advance / Balance split
        # cannot drift from the total it is a share of.
        "advance_expected": round(
            (order.grand_total or 0.0)
            * (
                (
                    order.advance_percent
                    if order.advance_percent is not None
                    else 30.0
                )
                / 100.0
            ),
            2,
        ),
        "balance_expected": round(
            (order.grand_total or 0.0)
            - (order.grand_total or 0.0)
            * (
                (
                    order.advance_percent
                    if order.advance_percent is not None
                    else 30.0
                )
                / 100.0
            ),
            2,
        ),
        "commercial_terms": order.commercial_terms or [],
        "payment_terms": order.payment_terms,
        "technical_notes": order.technical_notes,
        "attachments": order.attachments or [],
        "creator_id": str(order.creator_id) if order.creator_id else None,
        "creator_name": order.creator_name,
        "created_at": iso(order.created_at) if order.created_at else None,
        "updated_at": iso(order.updated_at) if order.updated_at else None,
    }
