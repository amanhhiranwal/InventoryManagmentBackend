from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.controllers.company_controller import CompanyController
from app.database.dependencies import get_db
from app.middleware.permission_middleware import require_permission

from app.schemas.company import (
    CreateCompanyRequest,
    UpdateCompanyRequest,
)

router = APIRouter(
    prefix="/companies",
    tags=["Companies"],
)


@router.post("/")
def create_company(
    request: CreateCompanyRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("company.create")),
):

    return CompanyController.create(
        request,
        db,
    )


@router.get("/")
def get_companies(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("company.view")),
):

    return CompanyController.get_all(db)


@router.get("/{company_id}")
def get_company(
    company_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("company.read")),
):

    return CompanyController.get_by_id(
        company_id,
        db,
    )


@router.put("/{company_id}")
def update_company(
    company_id: UUID,
    request: UpdateCompanyRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("company.update")),
):

    return CompanyController.update(
        company_id,
        request,
        db,
    )


@router.delete("/{company_id}")
def delete_company(
    company_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("company.delete")),
):

    return CompanyController.delete(
        company_id,
        db,
    )