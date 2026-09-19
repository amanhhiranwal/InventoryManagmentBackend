import re
from datetime import datetime
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.database.mongodb import sync_mongo_db
from app.middleware.auth_middleware import get_current_user
from app.services.lead_service import get_visible_creator_user_ids

router = APIRouter(prefix="/customers", tags=["Customers Master"])


class CustomerCreate(BaseModel):
    name: Optional[str] = None
    customer_name: Optional[str] = None
    email: Optional[str] = ""
    phone: Optional[str] = ""
    address: Optional[str] = ""
    gst: Optional[str] = ""
    pan: Optional[str] = ""
    category: Optional[str] = "Agriculture"
    isRegistered: Optional[bool] = True
    kycDocs: Optional[list] = []


@router.get("")
def get_customers(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    col = sync_mongo_db["customers"]
    visible_user_ids = get_visible_creator_user_ids(current_user, db)

    query = {}
    if visible_user_ids:
        query = {
            "$or": [
                {"creator_id": {"$in": visible_user_ids}},
                {"creator_id": {"$exists": False}},
                {"creator_id": None}
            ]
        }

    cursor = col.find(query).sort("_id", -1)
    data = []
    for doc in cursor:
        doc["id"] = str(doc["_id"])
        doc["_id"] = str(doc["_id"])
        data.append(doc)
    return {"success": True, "data": data}


@router.post("")
def create_customer(
    request: CustomerCreate,
    current_user=Depends(get_current_user),
):
    col = sync_mongo_db["customers"]
    doc = request.dict()
    cust_name = request.name or request.customer_name or "Unnamed Customer"
    doc["name"] = cust_name
    doc["customer_name"] = cust_name

    if col.find_one({"name": cust_name}):
        raise HTTPException(status_code=400, detail=f"Customer with name '{cust_name}' already exists.")

    first_name = current_user.get("first_name", "")
    last_name = current_user.get("last_name", "")
    creator_name = f"{first_name} {last_name}".strip() or "User"

    doc["creator_id"] = current_user.get("user_id")
    doc["creator_name"] = creator_name

    col.insert_one(doc)
    doc["id"] = str(doc["_id"])
    doc["_id"] = str(doc["_id"])
    return {"success": True, "message": "Customer created successfully.", "data": doc}


# ---------------------------------------------------------------------------
# Bulk import (Customers -> New Customer -> Add From Excel)
# ---------------------------------------------------------------------------

#: Rows accepted in one upload; larger sheets should be split.
MAX_IMPORT_ROWS = 2000

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CustomerImportRow(BaseModel):
    """One spreadsheet row, already mapped from its column headers."""

    name: Optional[str] = None
    contact_name: Optional[str] = None
    designation: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pin_code: Optional[str] = None
    country: Optional[str] = None
    gst: Optional[str] = None
    pan: Optional[str] = None
    customer_type: Optional[str] = None
    category: Optional[str] = None
    status: Optional[str] = None
    assigned_to: Optional[str] = None


class CustomerImportRequest(BaseModel):
    customers: list[CustomerImportRow]


def _clean(value) -> str:
    return str(value or "").strip()


@router.post("/bulk")
def import_customers(
    request: CustomerImportRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Create customers from an uploaded sheet.

    Every row is checked on its own: good rows are created together, and each
    rejected one comes back with its sheet row number and the reason, so the
    user can fix just those and upload them again.
    """

    from app.models.user import User

    rows = request.customers

    if not rows:
        raise HTTPException(status_code=400, detail="The file has no customer rows.")

    if len(rows) > MAX_IMPORT_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"Upload at most {MAX_IMPORT_ROWS} customers at a time; this file has {len(rows)}.",
        )

    col = sync_mongo_db["customers"]

    existing = {
        _clean(doc.get("name")).lower()
        for doc in col.find({}, {"name": 1})
        if doc.get("name")
    }

    # "Assigned To" is matched by name or email against the users this
    # person may assign to - themselves and their team, per the hierarchy.
    visible = get_visible_creator_user_ids(current_user, db)
    users_query = db.query(User)
    if visible:
        from uuid import UUID

        users_query = users_query.filter(User.id.in_([UUID(uid) for uid in visible]))

    assignees: dict[str, User] = {}
    for user in users_query.all():
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip().lower()
        if full_name:
            assignees.setdefault(full_name, user)
        if user.email:
            assignees[user.email.lower()] = user

    creator_name = (
        f"{current_user.get('first_name', '')} {current_user.get('last_name', '')}".strip()
        or "User"
    )
    now = datetime.utcnow().isoformat()

    to_insert: list[dict] = []
    skipped: list[dict] = []
    seen_in_file: set[str] = set()

    for index, row in enumerate(rows):
        # Row 1 of the sheet is the header, so data starts on row 2.
        sheet_row = index + 2
        name = _clean(row.name) or _clean(row.contact_name)

        def skip(reason: str):
            skipped.append({"row": sheet_row, "name": name or "-", "reason": reason})

        if not name:
            skip("Customer / company name is missing.")
            continue

        key = name.lower()

        if key in existing:
            skip("A customer with this name already exists.")
            continue

        if key in seen_in_file:
            skip("Listed more than once in this file.")
            continue

        email = _clean(row.email)
        if email and not _EMAIL.match(email):
            skip(f"'{email}' is not a valid email address.")
            continue

        status = _clean(row.status).capitalize() or "Active"
        if status not in ("Active", "Inactive"):
            skip("Status must be Active or Inactive.")
            continue

        assigned_name = _clean(row.assigned_to)
        assignee = assignees.get(assigned_name.lower()) if assigned_name else None
        if assigned_name and assignee is None:
            skip(f"Assigned To '{assigned_name}' is not a user you can assign to.")
            continue

        gst = _clean(row.gst).upper()

        doc = {
            "name": name,
            "customer_name": name,
            "contact_name": _clean(row.contact_name) or name,
            "designation": _clean(row.designation),
            "email": email,
            "phone": _clean(row.phone),
            "address": _clean(row.address),
            "city": _clean(row.city),
            "state": _clean(row.state),
            "pin_code": _clean(row.pin_code),
            "country": _clean(row.country) or "India",
            "gst": gst,
            "pan": _clean(row.pan).upper(),
            "customer_type": _clean(row.customer_type),
            "category": _clean(row.category) or "General",
            "status": status,
            "isRegistered": bool(gst),
            "kycDocs": [],
            "creator_id": current_user.get("user_id"),
            "creator_name": creator_name,
            "source": "excel_import",
            "created_at": now,
            "updated_at": now,
        }

        if assignee is not None:
            doc["assigned_to_id"] = str(assignee.id)
            doc["assigned_to_name"] = f"{assignee.first_name or ''} {assignee.last_name or ''}".strip()

        to_insert.append(doc)
        seen_in_file.add(key)

    if to_insert:
        col.insert_many(to_insert)

    created = len(to_insert)

    return {
        "success": True,
        "message": f"{created} customer(s) imported, {len(skipped)} skipped.",
        "data": {
            "created": created,
            "skipped": skipped,
            "total": len(rows),
        },
    }


@router.delete("/{customer_id}")
def delete_customer(
    customer_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    col = sync_mongo_db["customers"]
    try:
        obj_id = ObjectId(customer_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid customer ID format.")

    doc = col.find_one({"_id": obj_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Customer not found.")

    visible_user_ids = get_visible_creator_user_ids(current_user, db)
    if visible_user_ids:
        target_creator = doc.get("creator_id")
        if target_creator and target_creator not in visible_user_ids:
            raise HTTPException(status_code=403, detail="Permission denied. You can only delete customers created by yourself or your subordinates.")

    col.delete_one({"_id": obj_id})
    return {"success": True, "message": "Customer deleted successfully."}
