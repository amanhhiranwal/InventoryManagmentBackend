from typing import List

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.controllers.user_controller import UserController
from app.database.dependencies import get_db
from app.middleware.permission_middleware import require_granted, require_permission, require_super_admin
from app.schemas.user import (
    CreateUserRequest,
    SetUserActiveRequest,
    UpdateUserRequest,
    UpdateUserRoleRequest,
)

router = APIRouter(
    prefix="/users",
    tags=["Users"],
)


@router.get("/import/template")
def user_import_template(current_user=Depends(require_granted("user.bulk_upload"))):
    """The columns Users > Bulk Upload reads, with one example row."""

    from fastapi.responses import Response

    from app.services.user_import_service import template_csv

    return Response(
        content=template_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="User_Upload_Template.csv"'},
    )


@router.post("/import")
async def import_users(
    file: UploadFile = File(...),
    commit: bool = Form(False),
    test_emails: bool = Form(False),
    send_welcome: bool = Form(False),
    start_password: str | None = Form(None),
    db: Session = Depends(get_db),
    # Its own tick on Roles & Access, beside Users: adding one person and
    # adding a hundred from a file are different trusts.
    current_user=Depends(require_granted("user.bulk_upload")),
):
    """Create and update people from an Excel or CSV sheet.

    Called twice by the screen: without ``commit`` to show what each row
    would do, then with it to do it. See app/services/user_import_service.
    """

    from fastapi import HTTPException

    from app.services.user_import_service import run

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="That file is empty.")
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="That file is larger than 5 MB.")

    result = run(content, file.filename or "", db, current_user, commit=commit, test_emails=test_emails,
                 send_welcome=send_welcome, start_password=(start_password or "").strip() or None)
    done = result["counts"]
    message = (
        f"{done.get('create', 0)} added, {done.get('update', 0)} updated, "
        f"{done.get('deactivate', 0)} switched off, {done.get('error', 0)} refused."
        if commit else "Here is what the upload would do."
    )
    return {"success": True, "message": message, "data": result}


@router.post("/")
def create_user(
    request: CreateUserRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user.create")),
):
    return UserController.create(request, db, current_user)


@router.get("/")
def get_users(
    page: int = 1,
    size: int = 20,
    search: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user.read")),
):
    """One page of the people this user can see.

    A search is applied to everyone they can see, not to the page they are
    looking at, so a name is found wherever it falls in the list.
    """

    skip = (page - 1) * size
    return UserController.get_all(
        db, skip=skip, limit=size, current_user=current_user, search=search
    )


@router.put("/{user_id}/role")
def update_user_role(
    user_id: str,
    request: UpdateUserRoleRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user.update")),
):
    return UserController.update_role(user_id, request.role_ids, request.company_ids, db, current_user)


@router.put("/{user_id}/status")
def set_user_active(
    user_id: str,
    request: SetUserActiveRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user.update")),
):
    """Switch an account on or off without deleting the person's work."""

    from app.services.user_service import UserService

    user = UserService.set_active(user_id, request.is_active, db, current_user)

    return {
        "success": True,
        "message": (
            "Account switched on."
            if user.is_active
            else "Account switched off. They are signed out."
        ),
        "data": UserController._to_response_dict(user),
    }


@router.delete("/{user_id}")
def delete_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user.delete")),
):
    return UserController.delete(user_id, db, current_user)


@router.post("/{user_id}/reset-password")
def reset_user_password(
    user_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user.update")),
):
    """Email this person a new temporary password.

    The password itself is never in the response - the message says who
    it went to, and the inbox is where it is read.
    """

    return UserController.reset_password(user_id, db, current_user)


@router.post("/{user_id}/welcome-email")
def send_welcome_email(
    user_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin),
):
    """Super admin only, once per person: email a temporary password and
    sign them out of every earlier session."""

    return UserController.send_welcome_email(user_id, db, current_user)


@router.put("/{user_id}")
def update_user(
    user_id: str,
    request: UpdateUserRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user.update")),
):
    return UserController.update(user_id, request, db, current_user)


@router.get("/by-roles", tags=["Microservice Internal"])
def get_users_by_roles(
    role_ids: List[str] = Query(...),
    db: Session = Depends(get_db)
):
    from uuid import UUID

    from app.models.user_role import UserRole
    role_uuids = [UUID(rid) for rid in role_ids]
    user_roles = db.query(UserRole.user_id).filter(UserRole.role_id.in_(role_uuids)).all()
    user_ids = [str(ur.user_id) for ur in user_roles]
    return {"success": True, "user_ids": user_ids}


@router.get("/{user_id}/role-ids", tags=["Microservice Internal"])
def get_user_role_ids(
    user_id: str,
    db: Session = Depends(get_db)
):
    from app.repositories.rbac_repository import RBACRepository
    user = RBACRepository.get_user(db, user_id)
    if not user:
        return {"success": False, "role_ids": []}
    role_ids = [str(r.id) for r in user.roles]
    return {"success": True, "role_ids": role_ids}


@router.get("/names", tags=["Microservice Internal"])
def get_user_names(
    user_ids: List[str] = Query(...),
    db: Session = Depends(get_db)
):
    from uuid import UUID

    from app.models.user import User
    user_uuids = [UUID(uid) for uid in user_ids]
    users = db.query(User).filter(User.id.in_(user_uuids)).all()
    names = {str(u.id): f"{u.first_name} {u.last_name}" for u in users}
    return {"success": True, "names": names}