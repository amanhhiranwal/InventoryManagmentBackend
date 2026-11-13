"""Every PO instalment in one place, by week, and the daily reminder.

The payment schedule of a single PO lives on that PO. Accounts need the
other view: what falls due this week across every vendor, by which bank
or cheque, and what is still unpaid. Only POs that have been approved are
included - before that the schedule can still change and nothing may be
paid against it.

The reminder is run once a day by cron (send_payment_reminders.py). It
tells the people who record payments, and whoever raised the PO, about
each unpaid instalment from a few days before it falls due, and every day
after, until someone marks it paid.
"""

from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.procurement import PurchaseOrder, PurchaseOrderPayment
from app.services.procurement.common import has_permission, holders_of, is_super_admin
from app.services.procurement.purchase_orders import PAYABLE, link

#: Reminders start this many days before an instalment falls due.
REMIND_DAYS_BEFORE = 3

#: Who records payments; reminded alongside whoever raised the PO.
PAY_PERMISSION = "purchase_order.pay"


def assert_can_see(current_user: dict, db: Session) -> None:
    """The purchase team and accounts: whoever may open a PO or pay one."""

    if is_super_admin(current_user) or any(
        has_permission(current_user, key, db) for key in ("purchase_order.read", PAY_PERMISSION)
    ):
        return
    raise HTTPException(status_code=403, detail="Permission denied. Required: purchase_order.read")


def _parse(value: str | None, name: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{name} must be a date like 2026-11-30.")


def calendar(current_user: dict, db: Session, *, start: str | None = None, end: str | None = None,
             status: str | None = None) -> dict:
    """Instalments of approved POs, grouped by ISO week, oldest first.

    ``start``/``end`` bound the due date (inclusive). Unpaid instalments
    from before ``start`` are always included, marked overdue: a payment
    missed last month still has to be made. ``status`` is PENDING, PAID or
    empty for both.
    """

    assert_can_see(current_user, db)

    today = date.today()
    first = _parse(start, "From")
    last = _parse(end, "To")
    if first and last and last < first:
        raise HTTPException(status_code=400, detail="'To' must be on or after 'From'.")

    wanted = (status or "").upper()
    if wanted and wanted not in ("PENDING", "PAID"):
        raise HTTPException(status_code=400, detail="Status must be PENDING or PAID.")

    query = (
        db.query(PurchaseOrderPayment, PurchaseOrder)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderPayment.purchase_order_id)
        .filter(PurchaseOrder.status.in_(PAYABLE))
    )
    if wanted:
        query = query.filter(PurchaseOrderPayment.status == wanted)
    if last:
        query = query.filter(PurchaseOrderPayment.due_date <= last)

    rows = []
    for payment, po in query.order_by(PurchaseOrderPayment.due_date, PurchaseOrderPayment.id).all():
        due = payment.due_date
        overdue = payment.status != "PAID" and due is not None and due < today
        # Before the window: only what is still owed.
        if first and due and due < first and not overdue:
            continue
        rows.append({
            "id": payment.id,
            "purchase_order_id": po.id,
            "po_number": po.po_number,
            "vendor_name": po.vendor_name,
            "link": link(po),
            "label": payment.label,
            "percent": payment.percent,
            "amount": payment.amount,
            "due_date": due.isoformat() if due else None,
            "week": due.isocalendar()[1] if due else None,
            "week_start": (due - timedelta(days=due.weekday())).isoformat() if due else None,
            "mode": payment.mode,
            "bank_name": payment.bank_name,
            "cheque_number": payment.cheque_number,
            "cheque_date": payment.cheque_date.isoformat() if payment.cheque_date else None,
            "reference": payment.reference,
            "status": payment.status,
            "overdue": overdue,
            "paid_by_name": payment.paid_by_name,
        })

    weeks: dict[str, dict] = {}
    for row in rows:
        key = row["week_start"] or "undated"
        week = weeks.setdefault(key, {
            "week_start": row["week_start"],
            "week": row["week"],
            "rows": [],
            "total": 0.0,
            "pending": 0.0,
        })
        week["rows"].append(row)
        week["total"] += row["amount"] or 0
        if row["status"] != "PAID":
            week["pending"] += row["amount"] or 0

    ordered = sorted(weeks.values(), key=lambda w: (w["week_start"] is None, w["week_start"] or ""))
    for week in ordered:
        week["total"] = round(week["total"], 2)
        week["pending"] = round(week["pending"], 2)

    return {
        "weeks": ordered,
        "summary": {
            "pending": round(sum(r["amount"] or 0 for r in rows if r["status"] != "PAID"), 2),
            "overdue": round(sum(r["amount"] or 0 for r in rows if r["overdue"]), 2),
            "paid": round(sum(r["amount"] or 0 for r in rows if r["status"] == "PAID"), 2),
            "count": len(rows),
        },
    }


def due_for_reminder(db: Session, today: date) -> list[tuple[PurchaseOrderPayment, PurchaseOrder]]:
    """Unpaid instalments of approved POs falling due within the reminder
    window or already overdue, not yet reminded today."""

    return (
        db.query(PurchaseOrderPayment, PurchaseOrder)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderPayment.purchase_order_id)
        .filter(
            PurchaseOrder.status.in_(PAYABLE),
            PurchaseOrderPayment.status != "PAID",
            PurchaseOrderPayment.due_date.isnot(None),
            PurchaseOrderPayment.due_date <= today + timedelta(days=REMIND_DAYS_BEFORE),
            (PurchaseOrderPayment.last_reminded_on.is_(None))
            | (PurchaseOrderPayment.last_reminded_on < today),
        )
        .order_by(PurchaseOrderPayment.due_date, PurchaseOrderPayment.id)
        .all()
    )


def _message(payment: PurchaseOrderPayment, po: PurchaseOrder, today: date) -> str:
    amount = f"Rs {payment.amount:,.2f}"
    how = "post-dated cheque" if payment.mode == "PDC" else "bank transfer"
    days = (payment.due_date - today).days
    when = (
        f"was due {payment.due_date:%d %b %Y} ({-days} day(s) overdue)" if days < 0
        else "is due today" if days == 0
        else f"is due {payment.due_date:%d %b %Y} (in {days} day(s))"
    )
    return f"{po.po_number} to {po.vendor_name or 'the vendor'}: {payment.label} of {amount} by {how} {when}."


def send_reminders(db: Session, today: date | None = None) -> int:
    """One bell notification per unpaid instalment to the people who pay,
    and to whoever raised the PO. Returns how many instalments were
    reminded. Safe to run more than once a day: each instalment is
    reminded at most once per day."""

    from app.services.notification_service import notify_users

    today = today or date.today()
    due = due_for_reminder(db, today)
    if not due:
        return 0

    payers = holders_of(PAY_PERMISSION, db)

    for payment, po in due:
        recipients = list(payers)
        if po.created_by:
            recipients.append(str(po.created_by))

        # Marked first, so the notification's own commit saves both. If it
        # fails, notify_users logs it and rolls both back, and the
        # instalment is tried again on the next run rather than counted.
        payment.last_reminded_on = today
        notify_users(
            db,
            recipients,
            module="purchase_order",
            entity_id=po.id,
            action="Payment Due",
            message=_message(payment, po, today),
            link=link(po),
            actor_name="Payment reminder",
        )

    return len(due)
