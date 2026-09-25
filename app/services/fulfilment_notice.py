"""Telling the reporting line where an order has got to.

The discount chain writes to everyone while an order is being approved,
and then went quiet - once confirmed, an order could travel all the way
from payment to installation without anybody outside the CRM hearing about
it. This carries the same letter through the rest of the journey: every
stage move emails the salesperson and every manager above them, and rings
the bell for the same people.

Never allowed to break the move it is reporting. A mail server that is
down must not stop an order being dispatched.
"""

import logging

from sqlalchemy.orm import Session

from app.core.fulfilment import ACCOUNTS, INVENTORY, desk_for
from app.core.workflow_status import SalesOrderStatus
from app.models.user import User
from app.services.email_service import EmailService
from app.services.email_templates import EmailLetter, inr
from app.services.hierarchy_service import HierarchyService
from app.services.notification_service import notify_users

logger = logging.getLogger(__name__)

#: Where a desk works, so its notification opens the queue rather than one
#: order in isolation.
DESK_LINKS = {
    ACCOUNTS: "/fulfilment/accounts",
    INVENTORY: "/fulfilment/procurement",
}

#: What each stage means to the people reading about it, as a sentence
#: rather than a status code.
STAGE_NOTE: dict[str, str] = {
    SalesOrderStatus.CONFIRMED: (
        "The order is approved and confirmed. Accounts can collect the "
        "advance against the proforma invoice."
    ),
    SalesOrderStatus.PAYMENT_VERIFIED: (
        "Accounts have verified the payment. The order can go to inventory "
        "for procurement."
    ),
    SalesOrderStatus.PROCUREMENT: (
        "Inventory have taken the order: stock is being picked or placed on "
        "order."
    ),
    SalesOrderStatus.READY: "Stock is in hand and the order is ready to dispatch.",
    SalesOrderStatus.DISPATCHED: "The order has left the warehouse.",
    SalesOrderStatus.DELIVERED: (
        "The order has reached the client. Installation can be scheduled."
    ),
    SalesOrderStatus.INSTALLED: (
        "Installation is complete. Any balance payment can now be collected."
    ),
    SalesOrderStatus.COMPLETED: (
        "The order is closed - delivered, installed and paid in full."
    ),
    SalesOrderStatus.ON_HOLD: (
        "The order has been put on hold and needs a correction before it can "
        "go any further."
    ),
    SalesOrderStatus.CANCELLED: "The order has been cancelled.",
}


def _name(user: User | None) -> str:
    if user is None:
        return "Someone"

    full = f"{user.first_name or ''} {user.last_name or ''}".strip()

    return full or (user.email or "Someone")


def desk_holders(role: str, db: Session) -> list[User]:
    """Everyone staffing a desk, so its queue is never a surprise.

    Falls back to the super admins where nobody holds the role, because an
    order nobody has been told about is an order that stops.
    """

    holders = [
        user
        for user in db.query(User).filter(User.is_active.is_(True)).all()
        if role in {r.role_name for r in (user.roles or [])}
    ]

    if holders:
        return holders

    return (
        db.query(User)
        .filter(User.is_super_admin.is_(True), User.is_active.is_(True))
        .all()
    )


def _line(owner_id: str | None, db: Session) -> tuple[User | None, list[User]]:
    """The order's owner and the managers above them, nearest first."""

    if not owner_id:
        return None, []

    users = db.query(User).filter(User.is_active.is_(True)).all()
    by_id = {str(u.id): u for u in users}

    managers = {
        str(u.id): (str(u.reports_to_id) if u.reports_to_id else None)
        for u in users
    }

    chain = HierarchyService.manager_chain(str(owner_id), managers)

    return by_id.get(str(owner_id)), [by_id[uid] for uid in chain if uid in by_id]


def record_stage_change(
    order,
    db: Session,
    *,
    previous: str | None,
    actor_name: str | None = None,
    actor_id=None,
    remarks: str | None = None,
) -> list[dict]:
    """Everything that follows an order reaching a new stage.

    The stock moves and the people are told. Kept together because the two
    must not drift apart - an order that went out without the shelf being
    counted down is exactly the bug this whole thing exists to stop - and
    because every path that moves an order should do both.
    """

    from app.services.stock_movement_service import apply_for_stage

    moved = apply_for_stage(order, previous or "", db, actor=actor_name)

    announce_stage(
        order,
        db,
        previous=previous,
        actor_name=actor_name,
        actor_id=actor_id,
        remarks=remarks,
        stock_moved=moved,
    )

    return moved


def announce_stage(
    order,
    db: Session,
    *,
    previous: str | None,
    actor_name: str | None = None,
    actor_id=None,
    remarks: str | None = None,
    stock_moved: list[dict] | None = None,
) -> None:
    """Write to the reporting line about a sales order's new stage.

    Only the fulfilment stages are announced. Pending Approval is left to
    the approval chain, which already writes a better letter about it,
    and a draft is nobody's news yet.
    """

    stage = str(getattr(order, "status", "") or "")

    if stage not in STAGE_NOTE or stage == previous:
        return

    try:
        from app.services.approval_service import app_url

        owner, managers = _line(
            str(order.creator_id) if order.creator_id else None, db
        )

        reference = order.order_number or f"#{order.id}"
        heading = f"Sales order {reference}: {_label(stage)}"
        link = f"/sales/orders/{order.id}"

        facts: list[tuple[str, str]] = [
            ("Order No.", reference),
            ("Customer", order.customer_name or "-"),
            ("Stage", _label(stage)),
        ]

        if order.grand_total:
            facts.append(("Order value", inr(order.grand_total)))

        if getattr(order, "outstanding_balance", None):
            facts.append(("Balance due", inr(order.outstanding_balance)))

        if remarks:
            facts.append(("Remarks", remarks))

        # What left the shelf, and what is left on it. Inventory care, and
        # so does anyone wondering whether the next order can be filled.
        for line in stock_moved or []:
            facts.append((
                ("Issued" if line["direction"] == "OUT" else "Returned")
                + f" - {line['product']}",
                f"{line['quantity']:g} of {line['stock_before']:g}, "
                f"{line['stock_after']:g} left in stock",
            ))

        # The trail so far, so nobody has to open the CRM to see how far
        # along the order is.
        facts.append(("Fulfilment", _strip(stage)))

        letter = EmailLetter(
            db,
            heading=heading,
            greeting=f"Dear {_name(owner)}," if owner else None,
            paragraphs=[
                STAGE_NOTE[stage],
                (
                    f"{actor_name} moved it there."
                    if actor_name
                    else "The order has moved there."
                ),
            ],
            facts=facts,
            action=(f"Open sales order {reference}", app_url(link)),
            sign_off_name=actor_name or _name(owner),
        )

        recipients = [u for u in [owner, *managers] if u is not None]

        notify_users(
            db,
            [u.id for u in recipients],
            module="sales_order",
            entity_id=order.id,
            action=_label(stage),
            message=f"{reference} - {STAGE_NOTE[stage]}",
            link=link,
            actor_id=actor_id,
            actor_name=actor_name,
            commit=True,
        )

        # And whoever the order has just landed on, with a link to their own
        # desk rather than to the order's page: it is a job, not news.
        next_desk = desk_for(stage)

        if next_desk is not None:
            holders = desk_holders(next_desk.role, db)

            notify_users(
                db,
                [u.id for u in holders],
                module="sales_order",
                entity_id=order.id,
                action=f"With {next_desk.role}",
                message=f"{reference} - {next_desk.asks}",
                link=DESK_LINKS.get(next_desk.role, link),
                actor_id=actor_id,
                actor_name=actor_name,
                commit=True,
            )

            recipients += [u for u in holders if u not in recipients]

        addresses = [u.email for u in recipients if u.email]

        if addresses:
            EmailService.send(
                to=addresses[:1],
                subject=f"{_label(stage)} - sales order {reference}",
                text_body=letter.text(),
                html_body=letter.html(),
                cc=addresses[1:],
            )
    except Exception:  # noqa: BLE001 - never block the move being reported
        logger.exception(
            "Could not announce sales order %s reaching %s",
            getattr(order, "id", "?"),
            stage,
        )


def _label(stage: str) -> str:
    """The stage as a person would say it."""

    from app.services.sales_order_service import SALES_ORDER_STATUS_ACTIONS

    return SALES_ORDER_STATUS_ACTIONS.get(stage, stage.replace("_", " ").title())


def _strip(stage: str) -> str:
    """The fulfilment chain with the current stage marked."""

    if stage not in SalesOrderStatus.PIPELINE:
        return _label(stage)

    reached = SalesOrderStatus.PIPELINE.index(stage)

    return " -> ".join(
        f"[{step.replace('_', ' ').title()}]"
        if index == reached
        else step.replace("_", " ").title()
        for index, step in enumerate(SalesOrderStatus.PIPELINE)
    )
