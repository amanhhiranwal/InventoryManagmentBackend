from typing import Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
)
from sqlalchemy.orm import Session

from app.controllers.location_controller import LocationController
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.middleware.permission_middleware import require_permission, require_super_admin
from app.schemas.location import (
    BinCreate,
    BinUpdate,
    LocationCreate,
    LocationUpdate,
)
from app.services.warehouse_service import WarehouseService

# Masters are maintained by the super admin; reads stay open to the
# permissions below because the sales forms use them for their dropdowns.
router = APIRouter(
    prefix="/locations",
    tags=["Locations"],
)


@router.post(
    "/",
    dependencies=[
        Depends(require_super_admin)
    ],
)
def create_location(
    request: LocationCreate,
    db: Session = Depends(get_db),
):

    return LocationController.create(
        request,
        db,
    )


@router.get(
    "/",
    dependencies=[
        Depends(require_permission("location.read"))
    ],
)
def get_locations(
    page: int = 1,
    size: int = 20,
    db: Session = Depends(get_db),
):
    skip = (page - 1) * size
    return LocationController.get_all(
        db,
        skip=skip,
        limit=size,
    )


@router.get("/warehouses/mine")
def get_my_warehouses(
    company_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Warehouses the caller may work in: every one for a super admin,
    otherwise those granted on their profile, within their companies."""

    return {
        "success": True,
        "data": WarehouseService.my_warehouses(current_user, db, company_id),
    }


@router.get(
    "/{location_id}/bins",
    dependencies=[
        Depends(require_permission("location.read"))
    ],
)
def get_bins(
    location_id: UUID,
    db: Session = Depends(get_db),
):
    return {"success": True, "data": WarehouseService.list_bins(location_id, db)}


@router.post(
    "/{location_id}/bins",
    dependencies=[
        Depends(require_super_admin)
    ],
)
def create_bin(
    location_id: UUID,
    request: BinCreate,
    db: Session = Depends(get_db),
):
    return {
        "success": True,
        "message": "Bin created.",
        "data": WarehouseService.create_bin(location_id, request, db),
    }


@router.put(
    "/{location_id}/bins/{bin_id}",
    dependencies=[
        Depends(require_super_admin)
    ],
)
def update_bin(
    location_id: UUID,
    bin_id: UUID,
    request: BinUpdate,
    db: Session = Depends(get_db),
):
    return {
        "success": True,
        "message": "Bin updated.",
        "data": WarehouseService.update_bin(location_id, bin_id, request, db),
    }


@router.get(
    "/{location_id}",
    dependencies=[
        Depends(require_permission("location.read"))
    ],
)
def get_location(
    location_id: UUID,
    db: Session = Depends(get_db),
):

    return LocationController.get_by_id(
        location_id,
        db,
    )


@router.get(
    "/company/{company_id}",
    dependencies=[
        Depends(require_permission("location.read"))
    ],
)
def get_company_locations(
    company_id: UUID,
    db: Session = Depends(get_db),
):

    return LocationController.get_by_company(
        company_id,
        db,
    )


@router.put(
    "/{location_id}",
    dependencies=[
        Depends(require_super_admin)
    ],
)
def update_location(
    location_id: UUID,
    request: LocationUpdate,
    db: Session = Depends(get_db),
):

    return LocationController.update(
        location_id,
        request,
        db,
    )


@router.delete(
    "/{location_id}",
    dependencies=[
        Depends(require_super_admin)
    ],
)
def delete_location(
    location_id: UUID,
    db: Session = Depends(get_db),
):

    return LocationController.delete(
        location_id,
        db,
    )