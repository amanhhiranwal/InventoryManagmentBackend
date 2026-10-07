from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.middleware.permission_middleware import require_super_admin
from app.schemas.warranty_term import (
    CreateWarrantyTermRequest,
    UpdateWarrantyTermRequest,
)
from app.services.warranty_term_service import WarrantyTermService

router = APIRouter(
    prefix="/warranty-terms",
    tags=["Warranty Terms"],
)


def _serialise(term) -> dict:
    return {
        "id": str(term.id),
        "name": term.name,
        "years": term.years,
        "is_default": term.is_default,
        "description": term.description,
        "is_active": term.is_active,
    }


@router.get("")
@router.get("/")
def get_warranty_terms(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Every term a proposal may quote. Readable by anyone who can sell."""

    terms = WarrantyTermService.get_all(db, include_inactive=include_inactive)

    return {"success": True, "data": [_serialise(t) for t in terms]}


@router.get("/rates")
def get_warranty_rates(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """What each product charges for each term, keyed by SKU.

    The sales screens work the running total out in the browser so a
    salesperson sees the figure move as they pick. They cannot do that
    without the rates, and the rates live on the product - so this hands
    over the small map of them rather than the whole catalogue.

    It is a statement of the price list, not a way to set it: the server
    prices every saved document from the same source regardless of what
    the browser believed.
    """

    try:
        from app.database.mongodb import sync_mongo_db

        rows = sync_mongo_db["inventory_items"].find(
            {"attributes.warranty_rates": {"$exists": True, "$ne": {}}},
            {"serial_number": 1, "attributes.warranty_rates": 1},
        )

        rates = {
            str(row.get("serial_number") or "").upper(): (
                row.get("attributes") or {}
            ).get("warranty_rates")
            or {}
            for row in rows
        }
    except Exception:  # noqa: BLE001 - the screen works without it
        rates = {}

    return {"success": True, "data": {k: v for k, v in rates.items() if k and v}}


@router.post("/")
def create_warranty_term(
    request: CreateWarrantyTermRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin),
):
    """The lengths are company-wide; their price is set on each product."""

    term = WarrantyTermService.create(request, db)

    return {
        "success": True,
        "message": "Warranty term created successfully.",
        "data": _serialise(term),
    }


@router.put("/{term_id}")
def update_warranty_term(
    term_id: str,
    request: UpdateWarrantyTermRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin),
):
    term = WarrantyTermService.update(term_id, request, db)

    return {
        "success": True,
        "message": "Warranty term updated successfully.",
        "data": _serialise(term),
    }


@router.delete("/{term_id}")
def delete_warranty_term(
    term_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin),
):
    WarrantyTermService.delete(term_id, db)

    return {"success": True, "message": "Warranty term removed."}
