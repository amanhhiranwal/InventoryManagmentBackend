import logging
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.user import User

logger = logging.getLogger(__name__)

#: Where the bell opens each module's record.
MODULE_LINKS = {
    "lead": "/leads?open={id}",
    "opportunity": "/sales/opportunities?open={id}",
    "quotation": "/sales/quotations/{id}",
    "sales_order": "/sales/orders/{id}",
    "proforma_invoice": "/sales/proforma-invoices/{id}",
}


def notify_users(
    db: Session,
    user_ids,
    *,
    module: str,
    entity_id: int,
    action: str,
    message: str,
    link: str | None = None,
    actor_id=None,
    actor_name: str | None = None,
    commit: bool = True,
) -> None:
    """Put one notification in front of specific people.

    Used where the recipients are not the record's own creator and
    assignee - an approval goes to whoever is holding it up, which is
    decided by the hierarchy rather than by the document.

    A failure here is logged and swallowed: a notification must never stop
    the action it describes.
    """

    try:
        seen: set[str] = set()

        for raw in user_ids or []:
            user_id = _to_uuid(raw)

            if user_id is None or str(user_id) in seen:
                continue

            seen.add(str(user_id))

            db.add(
                Notification(
                    user_id=user_id,
                    module=module,
                    entity_id=entity_id,
                    action=action,
                    title=action,
                    message=message[:1000],
                    link=link,
                    actor_id=_to_uuid(actor_id),
                    actor_name=actor_name,
                )
            )

        if commit:
            db.commit()
    except Exception:  # noqa: BLE001 - never block the underlying action
        db.rollback()
        logger.exception("Could not notify users about %s %s", module, action)


def _to_uuid(value) -> UUID | None:
    if value is None or value == "":
        return None

    if isinstance(value, UUID):
        return value

    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _full_name(user: User) -> str:
    return f"{user.first_name or ''} {user.last_name or ''}".strip() or user.email


def _subject(module: str, record) -> str:
    """The record's name as a person would say it, e.g. "#SO-00003 · BrightEdge"."""

    def ref(number, fallback):
        return f"#{number}" if number else fallback

    if module == "lead":
        return record.title or record.organization_name or f"Lead #{record.id}"

    if module == "opportunity":
        return record.title or record.organization_name or f"Opportunity #{record.id}"

    if module == "quotation":
        name = record.organization_name or record.contact_name or ""
        return " · ".join(
            part for part in (ref(record.quote_number, f"Quotation #{record.id}"), name) if part
        )

    if module == "sales_order":
        name = record.company_name or record.customer_name or ""
        return " · ".join(
            part for part in (ref(record.order_number, f"SO #{record.id}"), name) if part
        )

    if module == "proforma_invoice":
        name = record.company_name or record.customer_name or ""
        return " · ".join(
            part for part in (ref(record.pi_number, f"PI #{record.id}"), name) if part
        )

    return f"#{record.id}"


class NotificationService:
    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------
    @staticmethod
    def _recipients(db: Session, record) -> set[UUID]:
        """The record's assignee and creator, plus every super admin."""

        recipients: set[UUID] = set()

        for attr in ("assigned_to_id", "creator_id"):
            user_id = _to_uuid(getattr(record, attr, None))
            if user_id:
                recipients.add(user_id)

        # Sales orders and invoices store the assignee as a display name.
        assignee_name = (getattr(record, "assigned_to", None) or "").strip().lower()

        if assignee_name:
            for user in db.query(User).filter(User.is_active.is_(True)).all():
                if _full_name(user).lower() == assignee_name or (
                    user.first_name or ""
                ).strip().lower() == assignee_name:
                    recipients.add(user.id)

        for (admin_id,) in (
            db.query(User.id)
            .filter(User.is_super_admin.is_(True), User.is_active.is_(True))
            .all()
        ):
            recipients.add(admin_id)

        return recipients

    @staticmethod
    def notify_activity(
        db: Session,
        module: str,
        record,
        action: str,
        description: str | None = None,
        actor_id: str | None = None,
    ) -> None:
        """Turn one Activity History entry into a notification per recipient.

        Rows are only added to the session; they are committed together with
        the activity. A failure here is logged and swallowed, so a notification
        can never stop the action it describes.
        """

        try:
            if getattr(record, "id", None) is None:
                return

            actor_uuid = _to_uuid(actor_id)
            actor = db.query(User).filter(User.id == actor_uuid).first() if actor_uuid else None

            subject = _subject(module, record)
            detail = (description or "").strip()
            message = f"{subject} — {detail}" if detail else subject

            link_template = MODULE_LINKS.get(module)

            for user_id in NotificationService._recipients(db, record):
                db.add(
                    Notification(
                        user_id=user_id,
                        module=module,
                        entity_id=record.id,
                        action=action,
                        title=action,
                        message=message[:1000],
                        link=link_template.format(id=record.id) if link_template else None,
                        actor_id=actor_uuid,
                        actor_name=_full_name(actor) if actor else None,
                    )
                )
        except Exception:  # noqa: BLE001 - never block the underlying action
            logger.exception("Could not create notifications for %s %s", module, action)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    @staticmethod
    def list_for_user(
        db: Session,
        user_id: str,
        limit: int = 30,
        unread_only: bool = False,
    ) -> list[Notification]:
        query = db.query(Notification).filter(Notification.user_id == _to_uuid(user_id))

        if unread_only:
            query = query.filter(Notification.is_read.is_(False))

        return (
            query.order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(max(1, min(limit, 100)))
            .all()
        )

    @staticmethod
    def unread_count(db: Session, user_id: str) -> int:
        return (
            db.query(func.count(Notification.id))
            .filter(
                Notification.user_id == _to_uuid(user_id),
                Notification.is_read.is_(False),
            )
            .scalar()
            or 0
        )

    @staticmethod
    def mark_read(db: Session, user_id: str, notification_id: int) -> Notification:
        notification = (
            db.query(Notification)
            .filter(
                Notification.id == notification_id,
                Notification.user_id == _to_uuid(user_id),
            )
            .first()
        )

        if notification is None:
            raise HTTPException(status_code=404, detail="Notification not found")

        if not notification.is_read:
            notification.is_read = True
            notification.read_at = datetime.utcnow()
            db.commit()
            db.refresh(notification)

        return notification

    @staticmethod
    def mark_all_read(db: Session, user_id: str) -> int:
        updated = (
            db.query(Notification)
            .filter(
                Notification.user_id == _to_uuid(user_id),
                Notification.is_read.is_(False),
            )
            .update(
                {Notification.is_read: True, Notification.read_at: datetime.utcnow()},
                synchronize_session=False,
            )
        )

        db.commit()

        return updated


def serialize_notification(notification: Notification) -> dict:
    return {
        "id": notification.id,
        "module": notification.module,
        "entity_id": notification.entity_id,
        "action": notification.action,
        "title": notification.title,
        "message": notification.message,
        "link": notification.link,
        "actor_name": notification.actor_name,
        "is_read": notification.is_read,
        "created_at": notification.created_at.isoformat() if notification.created_at else None,
    }
