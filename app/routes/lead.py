from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.controllers.opportunity_controller import OpportunityController
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.core.field_checks import (
    check_email,
    check_gstin,
    check_mobile,
    check_pan,
    check_pin,
)
from app.middleware.permission_middleware import require_granted
from app.schemas.lead import (
    AssignLeadRequest,
    CreateLeadRequest,
    LogLeadActivityRequest,
    ProgressLeadRequest,
    UpdateLeadRequest,
)
from app.schemas.opportunity import ConvertLeadRequest
from app.services.lead_service import LeadService
from app.utils.user_names import get_user_names_helper

router = APIRouter(
    prefix="/leads",
    tags=["Leads"],
)

def serialize_activity(activity, names_map: dict[str, str] | None = None) -> dict:
    """Shape one Activity History entry for the Lead Details drawer."""

    names_map = names_map or {}
    created_by = str(activity.created_by) if activity.created_by else None

    return {
        "id": str(activity.id),
        "action": activity.action,
        "description": activity.description,
        "from_status": activity.from_status,
        "to_status": activity.to_status,
        "created_by": created_by,
        "created_by_name": names_map.get(created_by) if created_by else None,
        "created_at": activity.created_at.isoformat(),
    }


@router.post("/")
def create_lead(
    request: CreateLeadRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user_id = current_user["user_id"]
    first_name = current_user.get("first_name", "")
    last_name = current_user.get("last_name", "")
    creator_name = f"{first_name} {last_name}".strip() or "User"

    # Shape-checked here as well as in the browser. This endpoint is
    # reachable without the form, and an enquiry recorded with an address
    # that has no "@" in it is an enquiry nobody can answer.
    check_email(request.email)
    check_mobile(request.mobile_number)
    check_gstin(request.gst_number)
    check_pan(request.pan_number)
    check_pin(request.zip_code)

    lead = LeadService.create_lead(request, UUID(user_id), db)
    return {
        "success": True,
        "message": "Lead created successfully.",
        "data": {
            "id": lead.id,
            "title": lead.title,
            "description": lead.description,
            "status": lead.status,
            "contact_name": lead.contact_name,
            "organization_name": lead.organization_name,
            "email": lead.email,
            "mobile_number": lead.mobile_number,
            "website": lead.website,
            "office_address": lead.office_address,
            "city": lead.city,
            "zip_code": lead.zip_code,
            "country": lead.country,
            "gst_number": lead.gst_number,
            "pan_number": lead.pan_number,
            "coi_number": lead.coi_number,
            "designation": lead.designation,
            "remarks": lead.remarks,
            "customer_type_id": lead.customer_type_id,
            "customer_type_name": lead.customer_type.name if lead.customer_type else None,
            "state_id": lead.state_id,
            "state_name": lead.state.name if lead.state else None,
            "lead_source_id": lead.lead_source_id,
            "lead_source_name": lead.lead_source.name if lead.lead_source else None,
            "creator_id": str(lead.creator_id),
            "creator_name": creator_name,
            "assigned_to_id": str(lead.assigned_to_id) if lead.assigned_to_id else None,
            "created_at": lead.created_at.isoformat(),
        }
    }

@router.get("/")
def get_leads(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user_id = current_user["user_id"]
    is_super_admin = current_user.get("is_super_admin", False)
    role_id = current_user.get("role_id")
    role_ids = {role_id} if role_id else set()
    
    leads = LeadService.get_visible_leads(user_id, is_super_admin, role_ids, db)
    
    # Resolve names of creators & assigned users in bulk via HTTP
    all_user_ids = set()
    for lead_row in leads:
        if lead_row.creator_id:
            all_user_ids.add(str(lead_row.creator_id))
        if lead_row.assigned_to_id:
            all_user_ids.add(str(lead_row.assigned_to_id))
        if lead_row.assigned_by_id:
            all_user_ids.add(str(lead_row.assigned_by_id))
            
    names_map = get_user_names_helper(list(all_user_ids), db)
    
    return {
        "success": True,
        "data": [
            {
                "id": lead_row.id,
                "title": lead_row.title,
                "description": lead_row.description,
                "status": lead_row.status,
                "stage": lead_row.stage,
                "demo_status": lead_row.demo_status,
                "requirements": lead_row.requirements,
                "quotation_type": lead_row.quotation_type,
                "quotation_items": lead_row.quotation_items,
                "contact_name": lead_row.contact_name,
                "organization_name": lead_row.organization_name,
                "email": lead_row.email,
                "mobile_number": lead_row.mobile_number,
                "website": lead_row.website,
                "office_address": lead_row.office_address,
                "city": lead_row.city,
                "zip_code": lead_row.zip_code,
                "country": lead_row.country,
                "gst_number": lead_row.gst_number,
                "pan_number": lead_row.pan_number,
                "coi_number": lead_row.coi_number,
                "designation": lead_row.designation,
                "remarks": lead_row.remarks,
                "customer_type_id": lead_row.customer_type_id,
                "customer_type_name": lead_row.customer_type.name if lead_row.customer_type else None,
                "state_id": lead_row.state_id,
                "state_name": lead_row.state.name if lead_row.state else None,
                "lead_source_id": lead_row.lead_source_id,
                "lead_source_name": lead_row.lead_source.name if lead_row.lead_source else None,
                "creator_id": str(lead_row.creator_id),
                "creator_name": names_map.get(str(lead_row.creator_id), "Unknown"),
                "assigned_to_id": str(lead_row.assigned_to_id) if lead_row.assigned_to_id else None,
                "assigned_to_name": names_map.get(str(lead_row.assigned_to_id), None) if lead_row.assigned_to_id else None,
                "assigned_by_id": str(lead_row.assigned_by_id) if lead_row.assigned_by_id else None,
                "assigned_by_name": names_map.get(str(lead_row.assigned_by_id), None) if lead_row.assigned_by_id else None,
                "created_at": lead_row.created_at.isoformat(),
            }
            for lead_row in leads
        ]
    }

@router.get("/{lead_id}")
def get_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Fetch a single lead. The Opportunity and Lead detail views both call
    this; previously it did not exist and callers silently fell back to
    whatever was already in memory."""

    from app.models.lead import Lead as LeadModel

    lead = db.query(LeadModel).filter(LeadModel.id == lead_id).first()

    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    LeadService.assert_can_modify_lead(lead, current_user, db)

    user_ids = [
        str(uid)
        for uid in (lead.creator_id, lead.assigned_to_id, lead.assigned_by_id)
        if uid
    ]
    names_map = get_user_names_helper(user_ids, db)

    return {
        "success": True,
        "data": {
            "id": lead.id,
            "title": lead.title,
            "description": lead.description,
            "status": lead.status,
            "stage": lead.stage,
            "demo_status": lead.demo_status,
            "requirements": lead.requirements,
            "quotation_type": lead.quotation_type,
            "quotation_items": lead.quotation_items,
            "contact_name": lead.contact_name,
            "organization_name": lead.organization_name,
            "email": lead.email,
            "mobile_number": lead.mobile_number,
            "website": lead.website,
            "office_address": lead.office_address,
            "city": lead.city,
            "zip_code": lead.zip_code,
            "country": lead.country,
            "gst_number": lead.gst_number,
            "pan_number": lead.pan_number,
            "coi_number": lead.coi_number,
            "designation": lead.designation,
            "remarks": lead.remarks,
            "customer_type_id": lead.customer_type_id,
            "customer_type_name": (
                lead.customer_type.name if lead.customer_type else None
            ),
            "state_id": lead.state_id,
            "state_name": lead.state.name if lead.state else None,
            "lead_source_id": lead.lead_source_id,
            "lead_source_name": (
                lead.lead_source.name if lead.lead_source else None
            ),
            "creator_id": str(lead.creator_id),
            "creator_name": names_map.get(str(lead.creator_id), "Unknown"),
            "assigned_to_id": (
                str(lead.assigned_to_id) if lead.assigned_to_id else None
            ),
            "assigned_to_name": (
                names_map.get(str(lead.assigned_to_id))
                if lead.assigned_to_id
                else None
            ),
            "created_at": lead.created_at.isoformat(),
        },
    }


@router.get("/{lead_id}/activities")
def get_lead_activities(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Activity History for one lead, newest first.

    Deliberately its own endpoint rather than a field on the list response:
    the list renders a table that never shows history, so loading every
    lead's timeline to draw it would be wasted work.
    """

    activities = LeadService.get_activities(lead_id, current_user, db)

    names_map = get_user_names_helper(
        list({str(a.created_by) for a in activities if a.created_by}),
        db,
    )

    return {
        "success": True,
        "data": [serialize_activity(a, names_map) for a in activities],
    }


@router.post("/{lead_id}/activities")
def log_lead_activity(
    lead_id: str,
    request: LogLeadActivityRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Log an activity against a lead, moving its status when one was chosen."""

    lead, activity = LeadService.log_activity(lead_id, request, current_user, db)

    names_map = get_user_names_helper(
        [str(activity.created_by)] if activity.created_by else [],
        db,
    )

    return {
        "success": True,
        "message": "Activity logged successfully.",
        "data": {
            "activity": serialize_activity(activity, names_map),
            "lead": {
                "id": lead.id,
                "status": lead.status,
                "stage": lead.stage,
            },
        },
    }


@router.put("/{lead_id}/assign")
def assign_lead(
    lead_id: str,
    request: AssignLeadRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user_id = current_user["user_id"]
    is_super_admin = current_user.get("is_super_admin", False)
    role_id = current_user.get("role_id")
    role_ids = {role_id} if role_id else set()
    
    lead = LeadService.assign_lead(lead_id, request.assigned_to_id, user_id, is_super_admin, role_ids, db)
    # Was get_user_names_http, which is not defined anywhere - assigning a
    # lead raised NameError instead of returning the assignee's name.
    names_map = get_user_names_helper(
        [str(lead.creator_id), str(lead.assigned_to_id)],
        db,
    )
    
    return {
        "success": True,
        "message": "Lead assigned successfully.",
        "data": {
            "id": str(lead.id),
            "title": lead.title,
            "status": lead.status,
            "stage": lead.stage,
            "creator_id": str(lead.creator_id),
            "creator_name": names_map.get(str(lead.creator_id), "Unknown"),
            "assigned_to_id": str(lead.assigned_to_id),
            "assigned_to_name": names_map.get(str(lead.assigned_to_id), "Unknown"),
        }
    }

@router.put("/{lead_id}/progress")
def progress_lead(
    lead_id: str,
    request: ProgressLeadRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user_id = current_user["user_id"]
    is_super_admin = current_user.get("is_super_admin", False)
    role_id = current_user.get("role_id")
    role_ids = {role_id} if role_id else set()
    
    lead = LeadService.progress_lead(lead_id, request, user_id, is_super_admin, role_ids, db)
    return {
        "success": True,
        "message": f"Lead progressed to {lead.stage} successfully.",
        "data": {
            "id": str(lead.id),
            "title": lead.title,
            "description": lead.description,
            "status": lead.status,
            "stage": lead.stage,
            "demo_status": lead.demo_status,
            "requirements": lead.requirements,
            "quotation_type": lead.quotation_type,
            "quotation_items": lead.quotation_items,
            "creator_id": str(lead.creator_id),
            "created_at": lead.created_at.isoformat(),
        }
    }

@router.put("/{lead_id}")
def update_lead(
    lead_id: str,
    request: UpdateLeadRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user_id = current_user["user_id"]
    is_super_admin = current_user.get("is_super_admin", False)
    role_id = current_user.get("role_id")
    role_ids = {role_id} if role_id else set()

    lead = LeadService.update_lead(lead_id, request, user_id, is_super_admin, role_ids, db)
    return {
        "success": True,
        "message": "Lead updated successfully.",
        "data": {
            "id": lead.id,
            "title": lead.title,
            "description": lead.description,
            "status": lead.status,
            "stage": lead.stage,
            "contact_name": lead.contact_name,
            "organization_name": lead.organization_name,
            "email": lead.email,
            "mobile_number": lead.mobile_number,
            "website": lead.website,
            "office_address": lead.office_address,
            "city": lead.city,
            "zip_code": lead.zip_code,
            "country": lead.country,
            "gst_number": lead.gst_number,
            "pan_number": lead.pan_number,
            "coi_number": lead.coi_number,
            "designation": lead.designation,
            "remarks": lead.remarks,
            "customer_type_id": lead.customer_type_id,
            "customer_type_name": lead.customer_type.name if lead.customer_type else None,
            "state_id": lead.state_id,
            "state_name": lead.state.name if lead.state else None,
            "lead_source_id": lead.lead_source_id,
            "lead_source_name": lead.lead_source.name if lead.lead_source else None,
            "creator_id": str(lead.creator_id),
            "assigned_to_id": str(lead.assigned_to_id) if lead.assigned_to_id else None,
            "created_at": lead.created_at.isoformat(),
        }
    }


@router.post("/{lead_id}/convert")
def convert_lead_to_opportunity(
    lead_id: int,
    request: ConvertLeadRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Promote a QUALIFIED lead into an Opportunity and mark it CONVERTED."""

    return OpportunityController.convert_lead(
        lead_id,
        request,
        current_user,
        db,
    )


@router.delete("/{lead_id}")
def delete_lead(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Remove a lead, once it has not become an opportunity."""

    LeadService.delete_lead(lead_id, current_user, db)

    return {
        "success": True,
        "message": "Lead deleted successfully.",
    }


# ---------------------------------------------------------------------------
# Bulk import (Leads -> Add Lead -> Add From Excel)
# ---------------------------------------------------------------------------

#: One upload at a time. A sheet larger than this is a data migration, not
#: somebody adding the enquiries they came back from a trade show with.
MAX_IMPORT_ROWS = 2000


class LeadImportRow(BaseModel):
    """One spreadsheet row, already mapped from its column headers.

    The master lists are named rather than numbered. A salesperson's sheet
    says "Dealer" and "Maharashtra"; asking them for the row ids behind
    those is asking them to do a join by hand, and a wrong number is a
    silent mistake where a wrong name is one we can report.
    """

    contact_name: Optional[str] = None
    organization_name: Optional[str] = None
    designation: Optional[str] = None
    email: Optional[str] = None
    mobile_number: Optional[str] = None
    website: Optional[str] = None
    office_address: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    country: Optional[str] = None
    gst_number: Optional[str] = None
    pan_number: Optional[str] = None
    coi_number: Optional[str] = None
    remarks: Optional[str] = None

    customer_type: Optional[str] = None
    state: Optional[str] = None
    lead_source: Optional[str] = None
    assigned_to: Optional[str] = None


class LeadImportRequest(BaseModel):
    leads: list[LeadImportRow]


def _clean_cell(value) -> str:
    return str(value or "").strip()


@router.post("/bulk")
def import_leads(
    request: LeadImportRequest,
    db: Session = Depends(get_db),
    # Its own tick in Roles & Access: seeing Leads is not enough. The
    # button was gated on the client, which only hid it - anybody could
    # still post the rows one at a time. This is where it is decided.
    current_user=Depends(require_granted("lead.bulk_upload")),
):
    """Create leads from an uploaded sheet.

    Every row is checked on its own: good rows are created together, and
    each rejected one comes back with its sheet row number and the reason,
    so the user can fix just those and upload them again. One bad cell in
    two hundred rows should not cost the other hundred and ninety-nine.
    """

    rows = request.leads

    if not rows:
        raise HTTPException(status_code=400, detail="The file has no lead rows.")

    if len(rows) > MAX_IMPORT_ROWS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Upload at most {MAX_IMPORT_ROWS} leads at a time; "
                f"this file has {len(rows)}."
            ),
        )

    from app.models.customer_type import CustomerType
    from app.models.lead_source import LeadSource
    from app.models.state import State
    from app.models.user import User

    def by_name(model, attr: str = "name") -> dict:
        return {
            str(getattr(row, attr) or "").strip().lower(): row.id
            for row in db.query(model).all()
        }

    customer_types = by_name(CustomerType)
    states = by_name(State)
    lead_sources = by_name(LeadSource)

    users = {
        f"{u.first_name or ''} {u.last_name or ''}".strip().lower(): u.id
        for u in db.query(User).all()
    }

    creator_id = UUID(str(current_user["user_id"]))

    created: list[int] = []
    skipped: list[dict] = []
    seen: set[str] = set()

    for index, row in enumerate(rows):
        # Row 1 of the sheet is the header, so data starts on row 2.
        sheet_row = index + 2

        contact = _clean_cell(row.contact_name)
        organisation = _clean_cell(row.organization_name)
        label = organisation or contact or "-"

        def skip(reason: str) -> None:
            skipped.append({"row": sheet_row, "name": label, "reason": reason})

        if not contact and not organisation:
            skip("Both the contact name and the organisation are missing.")
            continue

        key = (organisation or contact).lower()

        if key in seen:
            skip("Listed more than once in this file.")
            continue

        # The same checks the single-create route applies, reported per
        # row rather than raised, so one bad cell costs its own row only.
        try:
            email = check_email(row.email)
            check_mobile(row.mobile_number)
            check_gstin(row.gst_number)
            check_pan(row.pan_number)
            check_pin(row.zip_code)
        except HTTPException as exc:
            skip(str(exc.detail))
            continue

        # A master list the sheet names but we do not hold is reported
        # rather than dropped: a lead filed under no customer type is
        # quietly wrong, and the whole point of these columns is that the
        # pricing and the pipeline read them later.
        resolved: dict[str, int | None] = {}
        failed = False

        for field, given, table, what in (
            ("customer_type_id", row.customer_type, customer_types, "customer type"),
            ("state_id", row.state, states, "state"),
            ("lead_source_id", row.lead_source, lead_sources, "lead source"),
        ):
            name = _clean_cell(given)

            if not name:
                resolved[field] = None
                continue

            found = table.get(name.lower())

            if found is None:
                skip(f"{what.capitalize()} '{name}' is not one we hold.")
                failed = True
                break

            resolved[field] = found

        if failed:
            continue

        assigned_name = _clean_cell(row.assigned_to)
        assignee = users.get(assigned_name.lower()) if assigned_name else None

        if assigned_name and assignee is None:
            skip(f"Assigned To '{assigned_name}' is not a user.")
            continue

        payload = CreateLeadRequest(
            title=organisation or contact,
            status="new",
            contact_name=contact or None,
            organization_name=organisation or None,
            designation=_clean_cell(row.designation) or None,
            email=email or None,
            mobile_number=_clean_cell(row.mobile_number) or None,
            website=_clean_cell(row.website) or None,
            office_address=_clean_cell(row.office_address) or None,
            city=_clean_cell(row.city) or None,
            zip_code=_clean_cell(row.zip_code) or None,
            country=_clean_cell(row.country) or "India",
            gst_number=_clean_cell(row.gst_number).upper() or None,
            pan_number=_clean_cell(row.pan_number).upper() or None,
            coi_number=_clean_cell(row.coi_number) or None,
            remarks=_clean_cell(row.remarks) or None,
            customer_type_id=resolved.get("customer_type_id"),
            state_id=resolved.get("state_id"),
            lead_source_id=resolved.get("lead_source_id"),
            assigned_to_id=str(assignee) if assignee else None,
        )

        try:
            lead = LeadService.create_lead(payload, creator_id, db)
        except Exception as exc:  # noqa: BLE001 - one bad row, not the file
            db.rollback()
            skip(str(getattr(exc, "detail", exc))[:140])
            continue

        created.append(lead.id)
        seen.add(key)

    return {
        "success": True,
        "message": f"{len(created)} lead(s) imported, {len(skipped)} skipped.",
        "data": {
            "created": len(created),
            "ids": created,
            "skipped": skipped,
            "total": len(rows),
        },
    }
