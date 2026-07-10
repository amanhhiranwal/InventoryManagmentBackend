from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
)
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.controllers.location_controller import LocationController
from app.middleware.permission_middleware import require_permission
from app.schemas.location import (
    LocationCreate,
    LocationUpdate,
)

router = APIRouter(
    prefix="/locations",
    tags=["Locations"],
)


@router.post(
    "/",
    dependencies=[
        Depends(require_permission("location.create"))
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
        Depends(require_permission("location.view"))
    ],
)
def get_locations(
    db: Session = Depends(get_db),
):

    return LocationController.get_all(
        db,
    )


@router.get(
    "/{location_id}",
    dependencies=[
        Depends(require_permission("location.view"))
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
        Depends(require_permission("location.view"))
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
        Depends(require_permission("location.update"))
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
        Depends(require_permission("location.delete"))
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