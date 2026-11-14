"""Who decides each approval step, shared by requisitions and purchase orders.

A requisition needs only the requester's reporting manager, who confirms
the need. A purchase order carries the price, so it runs the amount bands
of the approval matrix (Masters > PR Approval Matrix): the reporting
manager, then the department head and finance, then the director.

Nobody ever decides a step on a record they raised; where nobody else
holds a step, the super admins stand in, and the step says so.
"""

from sqlalchemy.orm import Session

from app.core.procurement import ApprovalStep
from app.models.procurement import Department
from app.services.procurement import masters
from app.services.procurement.common import names_for, role_holders, super_admin_ids, user_row


def approvers_for(step: str, *, requester_id, department_id, exclude: set[str], db: Session) -> tuple[list[str], str | None]:
    """The people who may decide ``step``, and a label when it falls back."""

    requester = user_row(requester_id, db) if requester_id else None
    ids: list[str] = []
    note = None

    if step == ApprovalStep.REPORTING_MANAGER:
        manager = user_row(requester.reports_to_id, db) if requester and requester.reports_to_id else None
        if manager and manager.is_active:
            ids = [str(manager.id)]
        else:
            note = "Super admins (no reporting manager set)"
    elif step == ApprovalStep.DEPARTMENT_HEAD:
        department = db.query(Department).filter(Department.id == department_id).first() if department_id else None
        head_id = masters.head_of(department)
        head = user_row(head_id, db) if head_id else None
        if head is not None and head.is_active:
            ids = [str(head.id)]
        else:
            note = "Super admins (no department head set)"
    else:
        # Finance, Director, or any role the matrix names ("ROLE:CEO").
        role = ApprovalStep.role_of(step)
        ids = role_holders(role, db) if role else []
        if not ids:
            note = f"Super admins (nobody holds the {role} role)"

    ids = [i for i in ids if i not in exclude]
    if not ids and note is None:
        note = "Super admins (the only approver raised this)"

    return ids, note


def is_approver(user_id, db: Session) -> bool:
    """True for anyone a requisition or PO step can name, or has named.

    My Requisitions holds the "Waiting on me" tab, so a person whose role
    has lost the right to raise requisitions still needs the page to decide
    the ones that reach them: someone with people reporting to them, a
    department head, a Finance or Director role holder, or anyone a step
    already names.
    """

    from sqlalchemy import String, cast

    from app.models.procurement import PurchaseOrderApproval, PurchaseRequisitionApproval
    from app.models.user import User

    me = str(user_id or "")
    if not me:
        return False

    if db.query(User.id).filter(User.reports_to_id == me, User.is_active.is_(True)).first():
        return True
    if db.query(Department.id).filter(Department.head_user_id == me).first():
        return True
    roles = set(ApprovalStep.ROLE_NAMES.values()) | {
        ApprovalStep.role_of(step)
        for band in masters.approval_matrix(db) for step in band.get("steps") or []
        if ApprovalStep.role_of(step)
    }
    if any(me in role_holders(role, db) for role in roles):
        return True

    named = f'%"{me}"%'
    return any(
        db.query(model.id).filter(cast(model.approver_ids, String).like(named)).first()
        for model in (PurchaseRequisitionApproval, PurchaseOrderApproval)
    )


def build(steps: list[str], *, requester_id, department_id, exclude: set[str], db: Session) -> list[dict]:
    """The chain for ``steps``, one dict per step that needs a decision.

    The same person twice in a row - a manager who is also the department
    head - signs once.
    """

    rows: list[dict] = []
    last: list[str] | None = None

    for step in steps:
        ids, note = approvers_for(step, requester_id=requester_id, department_id=department_id,
                                  exclude=exclude, db=db)
        if ids and last is not None and set(ids) == set(last):
            continue
        last = ids
        rows.append({
            "level": len(rows) + 1,
            "step": step,
            "approver_ids": ids,
            "approver_label": note or ", ".join(names_for(ids, db)),
            "decision": "PENDING" if not rows else "WAITING",
        })

    return rows


#: What to set up so a step has someone to decide it.
_SETUP = {
    ApprovalStep.REPORTING_MANAGER: "set the requester's reporting manager on Users",
    ApprovalStep.DEPARTMENT_HEAD: "set a head for the department in Masters > Departments",
    ApprovalStep.FINANCE: "give someone the Finance role",
    ApprovalStep.DIRECTOR: "give someone the Director role",
}


def assert_decidable(rows: list[dict], exclude: set[str], db: Session) -> None:
    """Refuse a chain with a step nobody may decide.

    A step with no named approver falls to the super admins - but never to
    whoever raised the record. If that person is the only super admin, the
    step could never be decided and the record would sit pending for good,
    so it is refused up front with what to set up instead.
    """

    from fastapi import HTTPException

    for row in rows:
        if people(row["approver_ids"], db, exclude):
            continue
        label = ApprovalStep.label(row["step"])
        raise HTTPException(
            status_code=409,
            detail=(
                f"Nobody can approve the {label} step: "
                f"{_SETUP.get(row['step']) or f'give someone the {label} role'}, "
                "or have another super admin raise it - nobody approves what they raised themselves."
            ),
        )


def people(approver_ids, db: Session, exclude: set[str] = frozenset()) -> list[str]:
    """Whoever is named on the step, or the super admins when nobody is -
    never anyone in ``exclude`` (the person who raised the record)."""

    named = [str(i) for i in approver_ids or []]
    return named or [i for i in super_admin_ids(db) if i not in exclude]


def may_decide(approver_ids, current_user: dict, exclude: set[str]) -> bool:
    me = str(current_user.get("user_id") or "")
    if not me or me in exclude:
        return False
    named = [str(i) for i in approver_ids or []]
    if me in named:
        return True
    from app.services.procurement.common import is_super_admin

    return is_super_admin(current_user)
