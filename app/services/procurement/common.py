"""What every part of procurement leans on: who someone is, what they may
do, the audit trail, notifications and document numbers."""

import logging
from datetime import date, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.references import next_number
from app.core.timezone import iso, now_ist
from app.models.procurement import ProcurementActivity
from app.models.user import User

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# People
# ----------------------------------------------------------------------
def as_uuid(value) -> UUID | None:
    if value in (None, ""):
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def user_row(user_id, db: Session) -> User | None:
    uid = as_uuid(user_id)
    return db.query(User).filter(User.id == uid).first() if uid else None


def full_name(user: User | None) -> str:
    if user is None:
        return ""
    return f"{user.first_name or ''} {user.last_name or ''}".strip() or (user.email or "")


def actor(current_user: dict, db: Session) -> tuple[UUID | None, str]:
    """The person doing this, as an id and a name for the record."""

    user = user_row(current_user.get("user_id"), db)
    name = full_name(user) or (
        f"{current_user.get('first_name') or ''} {current_user.get('last_name') or ''}".strip()
        or current_user.get("email")
        or ""
    )
    return as_uuid(current_user.get("user_id")), name


def is_super_admin(current_user: dict) -> bool:
    return bool(current_user.get("is_super_admin"))


def permissions_of(current_user: dict, db: Session) -> set[str]:
    """What this person's roles grant, read fresh, as require_permission does."""

    held = set(current_user.get("permissions") or [])
    user = user_row(current_user.get("user_id"), db)

    if user is not None:
        from app.repositories.rbac_repository import RBACRepository

        for role in user.roles or []:
            for permission in RBACRepository.get_role_permissions(db, role.id):
                held.add(permission.permission_name)

    return held


def has_permission(current_user: dict, key: str, db: Session) -> bool:
    return is_super_admin(current_user) or key in permissions_of(current_user, db)


def require(current_user: dict, key: str, db: Session) -> None:
    if not has_permission(current_user, key, db):
        raise HTTPException(status_code=403, detail=f"Permission denied. Required: {key}")


def holders_of(key: str, db: Session) -> list[str]:
    """Active users a permission reaches - through a role, or super admin."""

    from app.models.permission import Permission
    from app.models.role_permission import RolePermission
    from app.models.user_role import UserRole

    via_roles = (
        db.query(UserRole.user_id)
        .join(RolePermission, RolePermission.role_id == UserRole.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .filter(Permission.permission_name == key)
        .all()
    )
    ids = {str(row[0]) for row in via_roles}

    admins = db.query(User.id).filter(User.is_super_admin.is_(True)).all()
    ids |= {str(row[0]) for row in admins}

    if not ids:
        return []

    active = db.query(User.id).filter(
        User.id.in_([UUID(i) for i in ids]), User.is_active.is_(True)
    ).all()
    return [str(row[0]) for row in active]


def super_admin_ids(db: Session) -> list[str]:
    rows = db.query(User.id).filter(User.is_super_admin.is_(True), User.is_active.is_(True)).all()
    return [str(row[0]) for row in rows]


def role_holders(role_name: str, db: Session) -> list[str]:
    """Active users who hold a role of this name."""

    from app.models.role import Role
    from app.models.user_role import UserRole

    rows = (
        db.query(UserRole.user_id)
        .join(Role, Role.id == UserRole.role_id)
        .join(User, User.id == UserRole.user_id)
        .filter(func.lower(Role.role_name) == role_name.lower(), User.is_active.is_(True))
        .all()
    )
    return [str(row[0]) for row in rows]


def names_for(ids, db: Session) -> list[str]:
    uids = [u for u in (as_uuid(i) for i in ids or []) if u]
    if not uids:
        return []
    return [full_name(u) for u in db.query(User).filter(User.id.in_(uids)).all()]


# ----------------------------------------------------------------------
# The audit trail and the bell
# ----------------------------------------------------------------------
def log_by_hand(db: Session, current_user: dict, *, entity_type: str, entity_id: int,
                requisition_id: int | None, activity: str | None, remarks: str | None) -> None:
    """An entry someone adds to a record's activity history (+ Log Activity)."""

    from fastapi import HTTPException

    from app.core.procurement import ACTIVITY_TYPES

    kind = text(activity, 40) or "Note"
    if kind not in ACTIVITY_TYPES:
        raise HTTPException(status_code=400, detail=f"Activity is one of: {', '.join(ACTIVITY_TYPES)}.")
    remarks = text(remarks, 2000)
    if not remarks:
        raise HTTPException(status_code=400, detail="Write what happened.")
    log(db, current_user, entity_type=entity_type, entity_id=entity_id, requisition_id=requisition_id,
        action=kind, remarks=remarks, new={"logged_by_hand": True})
    db.commit()


def can_link_sales_order(current_user: dict, db: Session) -> bool:
    """Whether this person may raise a requisition against a sales order:
    someone in the sales chart, or a super admin. They see the orders their
    place in the chart lets them see; nobody else is offered sales orders."""

    from app.core.procurement import SALES_CHART_ROLES

    if is_super_admin(current_user):
        return True
    user = user_row(current_user.get("user_id"), db)
    return bool(user) and any(role.role_name in SALES_CHART_ROLES for role in user.roles or [])


def log(
    db: Session,
    current_user: dict | None,
    *,
    entity_type: str,
    entity_id: int,
    action: str,
    requisition_id: int | None = None,
    previous=None,
    new=None,
    remarks: str | None = None,
) -> None:
    """One line of the procurement audit trail, in the caller's transaction."""

    actor_id, actor_name = actor(current_user, db) if current_user else (None, "System")

    db.add(ProcurementActivity(
        entity_type=entity_type,
        entity_id=entity_id,
        requisition_id=requisition_id,
        action=action,
        previous_value=jsonable(previous),
        new_value=jsonable(new),
        remarks=(remarks or None),
        actor_id=actor_id,
        actor_name=actor_name,
    ))


def notify(
    db: Session,
    user_ids,
    *,
    module: str,
    entity_id: int,
    action: str,
    message: str,
    link: str,
    current_user: dict | None = None,
) -> None:
    """The bell, never anyone outside: vendors are not messaged."""

    from app.services.notification_service import notify_users

    actor_id, actor_name = actor(current_user, db) if current_user else (None, None)
    # Nobody is told about their own action.
    recipients = [u for u in user_ids or [] if str(u) != str(actor_id or "")]

    notify_users(
        db,
        recipients,
        module=module,
        entity_id=entity_id,
        action=action,
        message=message,
        link=link,
        actor_id=actor_id,
        actor_name=actor_name,
        commit=False,
    )


def jsonable(value):
    """A value as the audit trail can store it."""

    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return iso(value) if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


# ----------------------------------------------------------------------
# Numbers
# ----------------------------------------------------------------------
def next_reference(db: Session, model, column: str, prefix: str, *, yearly: bool = True, width: int = 5) -> str:
    """The next reference in a series - PR-2026-00001, VEN-00001 - never reused."""

    year = now_ist().year
    lead = f"{prefix}-{year}-" if yearly else f"{prefix}-"
    series = f"{prefix.lower()}_{year}" if yearly else prefix.lower()
    field = getattr(model, column)

    def issued_so_far() -> int:
        highest = 0
        for (value,) in db.query(field).filter(field.like(f"{lead}%")).all():
            tail = str(value)[len(lead):]
            if tail.isdigit():
                highest = max(highest, int(tail))
        return highest

    def taken(number: int) -> bool:
        return db.query(model.id).filter(field == f"{lead}{number:0{width}d}").first() is not None

    return f"{lead}{next_number(db, series, issued_so_far, taken):0{width}d}"


# ----------------------------------------------------------------------
# Small shared helpers
# ----------------------------------------------------------------------
def money(value) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def text(value, limit: int | None = None) -> str | None:
    cleaned = str(value).strip() if value is not None else ""
    if not cleaned:
        return None
    return cleaned[:limit] if limit else cleaned


def today() -> date:
    return now_ist().date()


def attachments(value) -> list[dict]:
    """Uploaded files as the records keep them: [{key, name, size, type}]."""

    kept = []
    for raw in value or []:
        if isinstance(raw, dict) and str(raw.get("key") or "").strip():
            kept.append({
                "key": str(raw["key"]).strip(),
                "name": str(raw.get("name") or raw["key"])[:300],
                "size": raw.get("size"),
                "type": raw.get("type"),
            })
    return kept


def activity_rows(db: Session, *, entity_type: str | None = None, entity_id: int | None = None,
                  requisition_id: int | None = None) -> list[dict]:
    query = db.query(ProcurementActivity)
    if requisition_id is not None:
        query = query.filter(ProcurementActivity.requisition_id == requisition_id)
    else:
        query = query.filter(
            ProcurementActivity.entity_type == entity_type,
            ProcurementActivity.entity_id == entity_id,
        )
    return [
        {
            "id": row.id,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "action": row.action,
            "previous_value": row.previous_value,
            "new_value": row.new_value,
            "remarks": row.remarks,
            "actor_id": str(row.actor_id) if row.actor_id else None,
            "actor_name": row.actor_name,
            "created_at": iso(row.created_at),
        }
        for row in query.order_by(ProcurementActivity.created_at.desc(), ProcurementActivity.id.desc()).all()
    ]
