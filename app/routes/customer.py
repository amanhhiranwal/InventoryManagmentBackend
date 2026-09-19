"""Customers: list, detail, create / edit, stage and status, Activity History,
conversion to a lead, and bulk import from a spreadsheet.

Customers live in MongoDB. Who sees which customer follows the role
hierarchy, exactly as for leads: your own, your team's, and older records
that were saved before a creator was recorded.
"""

import re
from datetime import datetime
from typing import Optional
from uuid import UUID

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from pymongo import ReturnDocument
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.database.mongodb import sync_mongo_db
from app.middleware.permission_middleware import require_granted, require_permission
from app.services.lead_service import get_visible_creator_user_ids

router = APIRouter(prefix="/customers", tags=["Customers Master"])

#: Pipeline shown as the stepper on Contact Details, in order.
CUSTOMER_STAGES = ["NEW", "CONTACTED", "OPEN_DEAL", "CLOSED"]
STAGE_LABELS = {
    "NEW": "New",
    "CONTACTED": "Contacted",
    "OPEN_DEAL": "Open Deal",
    "CLOSED": "Closed",
}

#: Kinds of entry a user can log from "Log Activity".
ACTIVITY_TYPES = ["Call", "Email", "Meeting", "Note", "Follow Up"]

#: Rows accepted in one upload; larger sheets should be split.
MAX_IMPORT_ROWS = 2000

#: The customer number sequence starts here, so the first is CUS-1042 as in
#: the design.
FIRST_CUSTOMER_NUMBER = 1042

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _customers():
    return sync_mongo_db["customers"]


def _activities():
    return sync_mongo_db["customer_activities"]


def _now() -> str:
    return datetime.utcnow().isoformat()


def _clean(value) -> str:
    return str(value or "").strip()


def _user_name(current_user: dict) -> str:
    return (
        f"{current_user.get('first_name', '')} {current_user.get('last_name', '')}".strip()
        or "User"
    )


# ---------------------------------------------------------------------------
# Customer numbers
# ---------------------------------------------------------------------------

def _reserve_codes(count: int) -> list[str]:
    """Take the next `count` customer numbers from a shared counter, so two
    uploads at once can never hand out the same number."""

    counter = sync_mongo_db["counters"].find_one_and_update(
        {"_id": "customer_code"},
        {"$inc": {"seq": count}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    last = FIRST_CUSTOMER_NUMBER - 1 + counter["seq"]
    return [f"CUS-{n}" for n in range(last - count + 1, last + 1)]


def _backfill_codes() -> None:
    """Give older customers a number, oldest first."""

    missing = list(_customers().find({"customer_code": {"$exists": False}}, {"_id": 1}).sort("_id", 1))
    if not missing:
        return

    for doc, code in zip(missing, _reserve_codes(len(missing))):
        _customers().update_one({"_id": doc["_id"]}, {"$set": {"customer_code": code}})


# ---------------------------------------------------------------------------
# Visibility and lookups
# ---------------------------------------------------------------------------

def _visibility_query(current_user: dict, db: Session) -> dict:
    visible = get_visible_creator_user_ids(current_user, db)
    if not visible:
        return {}

    return {
        "$or": [
            {"creator_id": {"$in": visible}},
            {"assigned_to_id": {"$in": visible}},
            {"creator_id": {"$exists": False}},
            {"creator_id": None},
        ]
    }


def _get_visible_customer(customer_id: str, current_user: dict, db: Session) -> dict:
    try:
        object_id = ObjectId(customer_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid customer ID.")

    doc = _customers().find_one({"_id": object_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Customer not found.")

    visible = get_visible_creator_user_ids(current_user, db)
    if visible:
        owners = {doc.get("creator_id"), doc.get("assigned_to_id")} - {None}
        if owners and not owners & set(visible):
            raise HTTPException(
                status_code=403,
                detail="You can only open customers owned by yourself or your team.",
            )

    return doc


def _assignable_users(current_user: dict, db: Session) -> dict:
    """Users this person may assign customers to - themselves and their
    team - keyed by id, lower-cased full name and email."""

    from app.models.user import User

    query = db.query(User)
    visible = get_visible_creator_user_ids(current_user, db)
    if visible:
        query = query.filter(User.id.in_([UUID(uid) for uid in visible]))

    lookup: dict = {}
    for user in query.all():
        lookup[str(user.id)] = user
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip().lower()
        if full_name:
            lookup.setdefault(full_name, user)
        if user.email:
            lookup[user.email.lower()] = user

    return lookup


def _full_name(user) -> str:
    return f"{user.first_name or ''} {user.last_name or ''}".strip()


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    doc["_id"] = doc["id"]
    doc.setdefault("stage", "NEW")
    doc.setdefault("status", "Active")
    return doc


def _record_activity(
    customer_id: str,
    action: str,
    current_user: dict,
    description: str = "",
    activity_type: str = "System",
    from_stage: str | None = None,
    to_stage: str | None = None,
) -> dict:
    entry = {
        "customer_id": customer_id,
        "type": activity_type,
        "action": action,
        "description": description or "",
        "from_stage": from_stage,
        "to_stage": to_stage,
        "created_by": current_user.get("user_id"),
        "created_by_name": _user_name(current_user),
        "created_at": _now(),
    }
    _activities().insert_one(entry)
    _customers().update_one(
        {"_id": ObjectId(customer_id)},
        {"$set": {"last_activity_at": entry["created_at"], "updated_at": entry["created_at"]}},
    )
    entry["id"] = str(entry.pop("_id"))
    return entry


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CustomerFields(BaseModel):
    name: Optional[str] = None
    customer_name: Optional[str] = None
    contact_name: Optional[str] = None
    designation: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pin_code: Optional[str] = None
    country: Optional[str] = None
    gst: Optional[str] = None
    pan: Optional[str] = None
    coi: Optional[str] = None
    customer_type: Optional[str] = None
    category: Optional[str] = None
    remarks: Optional[str] = None
    lead_source: Optional[str] = None
    assigned_to_id: Optional[str] = None
    isRegistered: Optional[bool] = None
    kycDocs: Optional[list] = None
    attachments: Optional[list] = None
    #: "Create Lead" on the form: save the customer and open its lead.
    create_lead: bool = False
    #: "Save as Draft": keep it without a lead.
    draft: bool = False


class StageRequest(BaseModel):
    stage: str
    remarks: Optional[str] = None


class StatusRequest(BaseModel):
    status: str
    remarks: Optional[str] = None


class ActivityRequest(BaseModel):
    type: str = "Note"
    description: str


class ConvertRequest(BaseModel):
    title: Optional[str] = None
    remarks: Optional[str] = None
    assigned_to_id: Optional[str] = None


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


def _validated_fields(request: CustomerFields, current_user: dict, db: Session, partial: bool) -> dict:
    """The fields to store, checked. On an edit only what was sent changes."""

    sent = request.model_fields_set if partial else set(CustomerFields.model_fields)
    fields: dict = {}

    def take(key: str, value):
        if key in sent or (key == "name" and "customer_name" in sent):
            fields[key] = value

    name = _clean(request.name) or _clean(request.customer_name)
    if not partial or "name" in sent or "customer_name" in sent:
        if not name:
            raise HTTPException(status_code=400, detail="Organization name is required.")
        fields["name"] = name
        fields["customer_name"] = name

    email = _clean(request.email)
    if email and not _EMAIL.match(email):
        raise HTTPException(status_code=400, detail=f"'{email}' is not a valid email address.")

    for key in [
        "contact_name", "designation", "phone", "website", "address", "city",
        "state", "pin_code", "customer_type", "category", "remarks",
        "lead_source",
    ]:
        take(key, _clean(getattr(request, key)))

    take("email", email)
    take("country", _clean(request.country) or "India")
    take("gst", _clean(request.gst).upper())
    take("pan", _clean(request.pan).upper())
    take("coi", _clean(request.coi))

    if "gst" in fields:
        fields["isRegistered"] = bool(fields["gst"]) if request.isRegistered is None else bool(request.isRegistered)

    if request.kycDocs is not None:
        fields["kycDocs"] = request.kycDocs
    if request.attachments is not None:
        fields["attachments"] = request.attachments

    if "assigned_to_id" in sent:
        assignee_id = _clean(request.assigned_to_id)
        if assignee_id:
            user = _assignable_users(current_user, db).get(assignee_id)
            if user is None:
                raise HTTPException(status_code=403, detail="You can only assign customers to yourself or your team.")
            fields["assigned_to_id"] = str(user.id)
            fields["assigned_to_name"] = _full_name(user)
        else:
            fields["assigned_to_id"] = None
            fields["assigned_to_name"] = None

    return fields


def _create_lead_for(
    doc: dict,
    current_user: dict,
    db: Session,
    title: str = "",
    remarks: str = "",
    assigned_to_id: str | None = None,
):
    """Open a lead carrying the customer's details, record it on the
    customer and in its Activity History, and return the lead."""

    from app.models.customer_type import CustomerType
    from app.models.lead_source import LeadSource
    from app.models.state import State
    from app.schemas.lead import CreateLeadRequest
    from app.services.lead_service import LeadService

    if doc.get("converted_lead_id"):
        raise HTTPException(status_code=400, detail=f"This customer is already Lead #{doc['converted_lead_id']}.")

    def lookup(model, value):
        value = _clean(value)
        if not value:
            return None
        row = db.query(model).filter(model.name.ilike(value)).first()
        return row.id if row else None

    assigned = _clean(assigned_to_id) or doc.get("assigned_to_id") or None
    if assigned and assigned not in _assignable_users(current_user, db):
        raise HTTPException(status_code=403, detail="You can only assign the lead to yourself or your team.")

    lead = LeadService.create_lead(
        CreateLeadRequest(
            title=_clean(title) or doc.get("name"),
            description=_clean(remarks),
            contact_name=doc.get("contact_name") or doc.get("name"),
            organization_name=doc.get("name"),
            email=doc.get("email") or None,
            mobile_number=doc.get("phone") or None,
            website=doc.get("website") or None,
            office_address=doc.get("address") or None,
            city=doc.get("city") or None,
            zip_code=doc.get("pin_code") or None,
            country=doc.get("country") or "India",
            gst_number=doc.get("gst") or None,
            pan_number=doc.get("pan") or None,
            coi_number=doc.get("coi") or None,
            designation=doc.get("designation") or None,
            remarks=_clean(remarks) or doc.get("remarks") or None,
            customer_type_id=lookup(CustomerType, doc.get("customer_type")),
            state_id=lookup(State, doc.get("state")),
            lead_source_id=lookup(LeadSource, doc.get("lead_source")),
            assigned_to_id=assigned,
        ),
        UUID(current_user["user_id"]),
        db,
    )

    _customers().update_one(
        {"_id": doc["_id"]},
        {"$set": {"converted_lead_id": lead.id, "is_draft": False, "updated_at": _now()}},
    )
    _record_activity(
        str(doc["_id"]),
        "Converted to Lead",
        current_user,
        f"Lead #{lead.id} created"
        + (f" from {doc['lead_source']}" if doc.get("lead_source") else "")
        + "."
        + (f" {_clean(remarks)}" if _clean(remarks) else ""),
        activity_type="Conversion",
    )

    return lead


def _assert_lead_source(value: str | None, db: Session) -> None:
    from app.models.lead_source import LeadSource

    if not _clean(value):
        raise HTTPException(status_code=400, detail="Choose a Lead Source to create the lead.")

    if not db.query(LeadSource).filter(LeadSource.name.ilike(_clean(value))).first():
        raise HTTPException(status_code=400, detail=f"'{value}' is not a lead source.")


# ---------------------------------------------------------------------------
# List / detail
# ---------------------------------------------------------------------------

@router.get("")
def get_customers(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("customer.read")),
):
    _backfill_codes()
    cursor = _customers().find(_visibility_query(current_user, db)).sort("_id", -1)
    return {"success": True, "data": [_serialize(doc) for doc in cursor]}


@router.get("/meta")
def get_customer_meta(current_user=Depends(require_permission("customer.read"))):
    return {
        "success": True,
        "data": {
            "stages": [{"value": s, "label": STAGE_LABELS[s]} for s in CUSTOMER_STAGES],
            "activity_types": ACTIVITY_TYPES,
        },
    }


@router.get("/{customer_id}")
def get_customer(
    customer_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("customer.read")),
):
    _backfill_codes()
    return {"success": True, "data": _serialize(_get_visible_customer(customer_id, current_user, db))}


# ---------------------------------------------------------------------------
# Create / edit
# ---------------------------------------------------------------------------

@router.post("")
def create_customer(
    request: CustomerFields,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("customer.read")),
):
    fields = _validated_fields(request, current_user, db, partial=False)

    if request.create_lead:
        _assert_lead_source(fields.get("lead_source"), db)

    if _customers().find_one({"name": {"$regex": f"^{re.escape(fields['name'])}$", "$options": "i"}}):
        raise HTTPException(status_code=400, detail=f"Customer with name '{fields['name']}' already exists.")

    now = _now()
    doc = {
        **fields,
        "customer_code": _reserve_codes(1)[0],
        "stage": "NEW",
        "status": "Active",
        "kycDocs": fields.get("kycDocs", []),
        "creator_id": current_user.get("user_id"),
        "creator_name": _user_name(current_user),
        "source": "manual",
        "is_draft": bool(request.draft) and not request.create_lead,
        "created_at": now,
        "updated_at": now,
        "last_activity_at": now,
    }
    # With no one chosen, the customer is the creator's to follow up.
    if not doc.get("assigned_to_id"):
        doc["assigned_to_id"] = current_user.get("user_id")
        doc["assigned_to_name"] = doc["creator_name"]

    _customers().insert_one(doc)
    customer_id = str(doc["_id"])
    _record_activity(customer_id, "Customer Created", current_user, fields.get("remarks", ""), to_stage="NEW")

    lead_id = None
    if request.create_lead:
        lead_id = _create_lead_for(doc, current_user, db, remarks=fields.get("remarks", "")).id

    saved = _serialize(_customers().find_one({"_id": doc["_id"]}))
    saved["lead_id"] = lead_id

    return {
        "success": True,
        "message": f"Customer created and Lead #{lead_id} opened." if lead_id else "Customer created successfully.",
        "data": saved,
    }


@router.put("/{customer_id}")
def update_customer(
    customer_id: str,
    request: CustomerFields,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("customer.read")),
):
    doc = _get_visible_customer(customer_id, current_user, db)
    fields = _validated_fields(request, current_user, db, partial=True)

    if "name" in fields:
        clash = _customers().find_one({
            "name": {"$regex": f"^{re.escape(fields['name'])}$", "$options": "i"},
            "_id": {"$ne": doc["_id"]},
        })
        if clash:
            raise HTTPException(status_code=400, detail=f"Customer with name '{fields['name']}' already exists.")

    if request.create_lead:
        _assert_lead_source(fields.get("lead_source", doc.get("lead_source")), db)

    if fields:
        fields["updated_at"] = _now()
        if not request.draft:
            fields["is_draft"] = False
        _customers().update_one({"_id": doc["_id"]}, {"$set": fields})
        _record_activity(customer_id, "Details Updated", current_user)

    lead_id = None
    if request.create_lead:
        current = _customers().find_one({"_id": doc["_id"]})
        lead_id = _create_lead_for(current, current_user, db, remarks=fields.get("remarks", "")).id

    saved = _serialize(_customers().find_one({"_id": doc["_id"]}))
    saved["lead_id"] = lead_id

    return {
        "success": True,
        "message": f"Customer saved and Lead #{lead_id} opened." if lead_id else "Customer updated.",
        "data": saved,
    }


# ---------------------------------------------------------------------------
# Stage, status, activity, conversion
# ---------------------------------------------------------------------------

@router.put("/{customer_id}/stage")
def update_stage(
    customer_id: str,
    request: StageRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("customer.read")),
):
    doc = _get_visible_customer(customer_id, current_user, db)
    target = _clean(request.stage).upper().replace(" ", "_")

    if target not in CUSTOMER_STAGES:
        raise HTTPException(status_code=400, detail="Stage must be New, Contacted, Open Deal or Closed.")

    if doc.get("status") == "Inactive":
        raise HTTPException(status_code=400, detail="Reactivate this customer before moving its stage.")

    current = doc.get("stage") or "NEW"
    if target == current:
        return {"success": True, "data": _serialize(doc)}

    _customers().update_one({"_id": doc["_id"]}, {"$set": {"stage": target, "updated_at": _now()}})
    activity = _record_activity(
        customer_id,
        f"Moved to {STAGE_LABELS[target]}",
        current_user,
        _clean(request.remarks),
        activity_type="Stage",
        from_stage=current,
        to_stage=target,
    )

    return {"success": True, "data": _serialize(_customers().find_one({"_id": doc["_id"]})), "activity": activity}


@router.put("/{customer_id}/status")
def update_status(
    customer_id: str,
    request: StatusRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("customer.read")),
):
    """Mark as Dead (Inactive) or bring a customer back (Active)."""

    doc = _get_visible_customer(customer_id, current_user, db)
    target = _clean(request.status).capitalize()

    if target not in ("Active", "Inactive"):
        raise HTTPException(status_code=400, detail="Status must be Active or Inactive.")

    if target == "Inactive" and not _clean(request.remarks):
        raise HTTPException(status_code=400, detail="Give a reason for marking this customer as dead.")

    if doc.get("status", "Active") == target:
        return {"success": True, "data": _serialize(doc)}

    _customers().update_one({"_id": doc["_id"]}, {"$set": {"status": target, "updated_at": _now()}})
    _record_activity(
        customer_id,
        "Marked as Dead" if target == "Inactive" else "Reactivated",
        current_user,
        _clean(request.remarks),
        activity_type="Status",
    )

    return {"success": True, "data": _serialize(_customers().find_one({"_id": doc["_id"]}))}


@router.get("/{customer_id}/activities")
def get_activities(
    customer_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("customer.read")),
):
    _get_visible_customer(customer_id, current_user, db)
    rows = []
    for entry in _activities().find({"customer_id": customer_id}).sort("created_at", -1):
        entry["id"] = str(entry.pop("_id"))
        rows.append(entry)
    return {"success": True, "data": rows}


@router.post("/{customer_id}/activities")
def log_activity(
    customer_id: str,
    request: ActivityRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("customer.read")),
):
    _get_visible_customer(customer_id, current_user, db)

    kind = _clean(request.type).title() or "Note"
    if kind not in ACTIVITY_TYPES:
        raise HTTPException(status_code=400, detail=f"Type must be one of: {', '.join(ACTIVITY_TYPES)}.")

    description = _clean(request.description)
    if not description:
        raise HTTPException(status_code=400, detail="Describe what happened.")

    headline = {
        "Call": "Outgoing Call",
        "Email": "Email Sent",
        "Meeting": "Meeting Held",
        "Note": "Note Added",
        "Follow Up": "Follow Up Scheduled",
    }[kind]

    entry = _record_activity(customer_id, headline, current_user, description, activity_type=kind)
    return {"success": True, "data": entry}


@router.post("/{customer_id}/convert-to-lead")
def convert_to_lead(
    customer_id: str,
    request: ConvertRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("customer.read")),
):
    """Open a lead for this customer, carrying its details across."""

    doc = _get_visible_customer(customer_id, current_user, db)
    lead = _create_lead_for(
        doc,
        current_user,
        db,
        title=request.title or "",
        remarks=request.remarks or "",
        assigned_to_id=request.assigned_to_id,
    )

    return {
        "success": True,
        "message": "Lead created from this customer.",
        "data": {"lead_id": lead.id, "customer": _serialize(_customers().find_one({"_id": doc["_id"]}))},
    }


# ---------------------------------------------------------------------------
# Bulk import (Customers -> New Customer -> Add From Excel)
# ---------------------------------------------------------------------------

@router.post("/bulk")
def import_customers(
    request: CustomerImportRequest,
    db: Session = Depends(get_db),
    # Its own tick in Roles & Access: seeing Customers is not enough.
    current_user=Depends(require_granted("customer.bulk_upload")),
):
    """Create customers from an uploaded sheet.

    Every row is checked on its own: good rows are created together, and each
    rejected one comes back with its sheet row number and the reason, so the
    user can fix just those and upload them again.
    """

    rows = request.customers

    if not rows:
        raise HTTPException(status_code=400, detail="The file has no customer rows.")

    if len(rows) > MAX_IMPORT_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"Upload at most {MAX_IMPORT_ROWS} customers at a time; this file has {len(rows)}.",
        )

    existing = {
        _clean(doc.get("name")).lower()
        for doc in _customers().find({}, {"name": 1})
        if doc.get("name")
    }
    assignees = _assignable_users(current_user, db)
    creator_name = _user_name(current_user)
    now = _now()

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
            "stage": "NEW",
            "status": status,
            "isRegistered": bool(gst),
            "kycDocs": [],
            "creator_id": current_user.get("user_id"),
            "creator_name": creator_name,
            "assigned_to_id": str(assignee.id) if assignee else current_user.get("user_id"),
            "assigned_to_name": _full_name(assignee) if assignee else creator_name,
            "source": "excel_import",
            "created_at": now,
            "updated_at": now,
            "last_activity_at": now,
        }

        to_insert.append(doc)
        seen_in_file.add(key)

    if to_insert:
        for doc, code in zip(to_insert, _reserve_codes(len(to_insert))):
            doc["customer_code"] = code

        result = _customers().insert_many(to_insert)

        _activities().insert_many([
            {
                "customer_id": str(inserted_id),
                "type": "System",
                "action": "Customer Created",
                "description": "Imported from Excel.",
                "from_stage": None,
                "to_stage": "NEW",
                "created_by": current_user.get("user_id"),
                "created_by_name": creator_name,
                "created_at": now,
            }
            for inserted_id in result.inserted_ids
        ])

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
    current_user=Depends(require_permission("customer.read")),
):
    doc = _get_visible_customer(customer_id, current_user, db)
    _customers().delete_one({"_id": doc["_id"]})
    _activities().delete_many({"customer_id": customer_id})
    return {"success": True, "message": "Customer deleted successfully."}
