"""Departments, the companies purchases are charged to, and the requisition
approval matrix."""

import json

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.procurement import APPROVAL_SETTING, DEFAULT_APPROVAL_MATRIX, ApprovalStep
from app.models.app_setting import AppSetting
from app.models.company import Company
from app.models.procurement import Department, PurchaseRequisition
from app.models.user import User
from app.services.company_scope_service import CompanyScopeService
from app.services.procurement.common import full_name, text, user_row


# ----------------------------------------------------------------------
# Departments
# ----------------------------------------------------------------------
def serialize_department(row: Department, db: Session, members: dict | None = None) -> dict:
    head = user_row(row.head_user_id, db) if row.head_user_id else None
    return {
        "id": row.id,
        "name": row.name,
        "code": row.code,
        "head_user_id": str(row.head_user_id) if row.head_user_id else None,
        "head_name": full_name(head) or None,
        "is_active": row.is_active,
        "members": (members or {}).get(row.id, 0),
    }


def list_departments(db: Session, include_inactive: bool = True) -> list[dict]:
    query = db.query(Department)
    if not include_inactive:
        query = query.filter(Department.is_active.is_(True))

    members: dict[int, int] = {}
    if hasattr(User, "department_id"):
        members = dict(
            db.query(User.department_id, func.count(User.id))
            .filter(User.department_id.isnot(None))
            .group_by(User.department_id)
            .all()
        )

    return [serialize_department(r, db, members) for r in query.order_by(Department.name).all()]


def _department_fields(payload: dict, db: Session) -> dict:
    name = text(payload.get("name"), 150)
    if not name:
        raise HTTPException(status_code=400, detail="A department needs a name.")

    head = None
    if payload.get("head_user_id"):
        head = user_row(payload["head_user_id"], db)
        if head is None or not head.is_active:
            raise HTTPException(status_code=400, detail="Choose an active user as the department head.")

    return {
        "name": name,
        "code": (text(payload.get("code"), 30) or "").upper() or None,
        "head_user_id": head.id if head else None,
        "is_active": bool(payload.get("is_active", True)),
    }


def save_department(payload: dict, db: Session, department_id: int | None = None) -> dict:
    fields = _department_fields(payload, db)

    clash = db.query(Department).filter(func.lower(Department.name) == fields["name"].lower())
    if department_id:
        clash = clash.filter(Department.id != department_id)
    if clash.first():
        raise HTTPException(status_code=409, detail=f"A department called {fields['name']} already exists.")

    if fields["code"]:
        clash = db.query(Department).filter(Department.code == fields["code"])
        if department_id:
            clash = clash.filter(Department.id != department_id)
        if clash.first():
            raise HTTPException(status_code=409, detail=f"The code {fields['code']} is already used.")

    if department_id:
        row = db.query(Department).filter(Department.id == department_id).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Department not found.")
        for key, value in fields.items():
            setattr(row, key, value)
    else:
        row = Department(**fields)
        db.add(row)

    db.commit()
    db.refresh(row)
    return serialize_department(row, db)


def delete_department(department_id: int, db: Session) -> None:
    row = db.query(Department).filter(Department.id == department_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Department not found.")

    in_use = db.query(PurchaseRequisition.id).filter(PurchaseRequisition.department_id == department_id).count()
    people = (
        db.query(User.id).filter(User.department_id == department_id).count()
        if hasattr(User, "department_id") else 0
    )
    if in_use or people:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{row.name} is on {in_use} requisition(s) and {people} user(s). "
                "Mark it inactive instead."
            ),
        )

    db.delete(row)
    db.commit()


# ----------------------------------------------------------------------
# Cost centre: the company a purchase is for
# ----------------------------------------------------------------------
def company_options(current_user: dict, db: Session) -> list[dict]:
    """The active companies this person may charge a purchase to: those on
    their profile, or every company for a super admin."""

    allowed = CompanyScopeService.visible_company_ids(current_user, db)
    query = db.query(Company).filter(Company.is_active.is_(True))
    if allowed is not None:
        if not allowed:
            return []
        query = query.filter(Company.id.in_(allowed))
    return [{"id": str(c.id), "name": c.company_name} for c in query.order_by(Company.company_name).all()]


# ----------------------------------------------------------------------
# The approval matrix
# ----------------------------------------------------------------------
def approval_matrix(db: Session) -> list[dict]:
    """The bands, from the saved setting or as the form sets them out."""

    row = db.query(AppSetting).filter(AppSetting.key == APPROVAL_SETTING).first()

    if row and row.value:
        try:
            bands = json.loads(row.value)
            if isinstance(bands, list) and bands:
                return bands
        except ValueError:
            pass

    return DEFAULT_APPROVAL_MATRIX


def step_options(db: Session) -> list[dict]:
    """What a band's approvers can be: the four steps of the company's PR
    form, then any role by name (decided by whoever holds it)."""

    from app.models.role import Role

    fixed = [{"value": step, "label": label} for step, label in ApprovalStep.LABELS.items()]
    covered = {name.lower() for name in ApprovalStep.ROLE_NAMES.values()}
    roles = [
        {"value": f"{ApprovalStep.ROLE_PREFIX}{r.role_name}", "label": r.role_name}
        for r in db.query(Role).order_by(Role.role_name).all()
        if r.role_name.lower() not in covered
        and r.role_name.lower() != "super admin"
        and len(r.role_name) <= ApprovalStep.MAX_ROLE_NAME
    ]
    return fixed + roles


def describe_matrix(db: Session) -> dict:
    bands = approval_matrix(db)
    return {
        "bands": [
            {
                "upto": band.get("upto"),
                "steps": band.get("steps") or [],
                "labels": [ApprovalStep.label(s) for s in band.get("steps") or []],
            }
            for band in bands
        ],
        "limits": [band.get("upto") for band in bands if band.get("upto") is not None],
        "options": step_options(db),
    }


#: More bands than this is a mistake rather than a policy.
MAX_BANDS = 8


def save_matrix(bands: list, db: Session) -> dict:
    """Replace the whole matrix: each band's ceiling and its approvers in order.

    Every band but the last needs a ceiling above the one before; the last
    has none, so every amount lands somewhere. Each band needs at least one
    approver, each a known step or an existing role, none twice.
    """

    if not isinstance(bands, list) or not 1 <= len(bands) <= MAX_BANDS:
        raise HTTPException(status_code=400, detail=f"Give between 1 and {MAX_BANDS} bands.")

    allowed = {option["value"] for option in step_options(db)}
    cleaned: list[dict] = []
    previous = 0.0

    for index, band in enumerate(bands):
        last = index == len(bands) - 1
        where = f"Band {index + 1}"
        if not isinstance(band, dict):
            raise HTTPException(status_code=400, detail=f"{where} is not a band.")

        upto = band.get("upto")
        if last:
            if upto not in (None, ""):
                raise HTTPException(status_code=400, detail="The last band has no upper amount - it covers everything above.")
            upto = None
        else:
            try:
                upto = float(upto)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"{where} needs an upper amount.")
            if upto <= previous:
                raise HTTPException(
                    status_code=400,
                    detail=f"{where}'s upper amount must be more than {previous:,.0f}.",
                )
            previous = upto

        steps = band.get("steps")
        if not isinstance(steps, list) or not steps:
            raise HTTPException(status_code=400, detail=f"{where} needs at least one approver.")
        unknown = [s for s in steps if s not in allowed]
        if unknown:
            raise HTTPException(status_code=400, detail=f"{where}: unknown approver {unknown[0]!r}.")
        if len(set(steps)) != len(steps):
            raise HTTPException(status_code=400, detail=f"{where} lists the same approver twice.")

        cleaned.append({"upto": upto, "steps": list(steps)})

    row = db.query(AppSetting).filter(AppSetting.key == APPROVAL_SETTING).first()
    if row is None:
        db.add(AppSetting(key=APPROVAL_SETTING, value=json.dumps(cleaned)))
    else:
        row.value = json.dumps(cleaned)
    db.commit()

    return describe_matrix(db)


def save_matrix_limits(limits: list, db: Session) -> dict:
    """Move the two thresholds; the steps in each band stay as the form has them."""

    try:
        values = [float(v) for v in limits]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Enter the thresholds as amounts.")

    if len(values) != 2 or values[0] <= 0 or values[1] <= values[0]:
        raise HTTPException(
            status_code=400,
            detail="Give two thresholds, the second higher than the first, both above nothing.",
        )

    bands = [
        {**DEFAULT_APPROVAL_MATRIX[0], "upto": values[0]},
        {**DEFAULT_APPROVAL_MATRIX[1], "upto": values[1]},
        {**DEFAULT_APPROVAL_MATRIX[2], "upto": None},
    ]

    row = db.query(AppSetting).filter(AppSetting.key == APPROVAL_SETTING).first()
    if row is None:
        db.add(AppSetting(key=APPROVAL_SETTING, value=json.dumps(bands)))
    else:
        row.value = json.dumps(bands)
    db.commit()

    return describe_matrix(db)


def steps_for_amount(amount: float, db: Session) -> list[str]:
    for band in approval_matrix(db):
        ceiling = band.get("upto")
        if ceiling is None or amount <= float(ceiling):
            return list(band.get("steps") or [])
    return [ApprovalStep.REPORTING_MANAGER]


def department_of(user: User | None, db: Session) -> Department | None:
    department_id = getattr(user, "department_id", None) if user else None
    if not department_id:
        return None
    return db.query(Department).filter(Department.id == department_id).first()


def head_of(department: Department | None) -> str | None:
    return str(department.head_user_id) if department and department.head_user_id else None
