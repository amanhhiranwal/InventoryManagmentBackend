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
