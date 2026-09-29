from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.services.notification_service import (
    NotificationService,
    serialize_notification,
)

router = APIRouter(
    prefix="/notifications",
    tags=["Notifications"],
)


@router.get("")
def get_notifications(
    limit: int = 30,
    unread_only: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Notifications, newest first, with the unread count.

    Everyone sees their own work: an AVP gets the approvals they are
    holding up, an accounts clerk gets their own queue. A super admin
    oversees the lot, so their feed is everybody's, each row labelled with
    whose it is. The badge stays their own unread count either way - a
    number counting the whole company's unread would mean nothing.
    """

    user_id = current_user.get("user_id")

    if current_user.get("is_super_admin"):
        rows = [
            serialize_notification(notification, for_user=name)
            for notification, name in NotificationService.list_everyone(
                db, limit, unread_only
            )
        ]
    else:
        rows = [
            serialize_notification(row)
            for row in NotificationService.list_for_user(
                db, user_id, limit, unread_only
            )
        ]

    return {
        "success": True,
        "data": rows,
        "unread_count": NotificationService.unread_count(db, user_id),
    }


@router.get("/unread-count")
def get_unread_count(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return {
        "success": True,
        "data": {"unread_count": NotificationService.unread_count(db, current_user.get("user_id"))},
    }


@router.put("/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    updated = NotificationService.mark_all_read(db, current_user.get("user_id"))

    return {"success": True, "data": {"updated": updated}}


@router.put("/{notification_id}/read")
def mark_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    notification = NotificationService.mark_read(
        db,
        current_user.get("user_id"),
        notification_id,
        any_user=bool(current_user.get("is_super_admin")),
    )

    return {"success": True, "data": serialize_notification(notification)}
