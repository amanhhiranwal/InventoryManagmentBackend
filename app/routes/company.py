import os
import shutil
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException
from sqlalchemy.orm import Session

from app.controllers.company_controller import CompanyController
from app.database.dependencies import get_db
from app.middleware.permission_middleware import require_permission, require_super_admin

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
    current_user=Depends(require_super_admin),
):

    return CompanyController.create(
        request,
        db,
    )


@router.get("/")
def get_companies(
    page: int = 1,
    size: int = 20,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("company.view")),
):

    skip = (page - 1) * size
    return CompanyController.get_all(db, skip=skip, limit=size)


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


@router.get("/{company_id}/exists-internal")
def check_company_exists_internal(
    company_id: UUID,
    db: Session = Depends(get_db)
):
    try:
        company = CompanyController.get_by_id(company_id, db)
        return {"exists": company is not None}
    except Exception:
        return {"exists": False}


UPLOAD_DIR = "uploads/company_logos"
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/{company_id}/logo")
def upload_company_logo(
    company_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin),
):
    company = CompanyController.get_by_id(company_id, db)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    ext = os.path.splitext(file.filename)[1].lower() if file.filename else ".png"
    if ext not in [".jpg", ".jpeg", ".png", ".webp", ".svg"]:
        raise HTTPException(status_code=400, detail="Invalid image format. Use PNG, JPG, WEBP or SVG.")

    filename = f"logo_{company_id}{ext}"
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    logo_url = f"http://127.0.0.1:8000/uploads/company_logos/{filename}"
    update_req = UpdateCompanyRequest(logo_url=logo_url)
    updated = CompanyController.update(company_id, update_req, db)

    return {
        "success": True,
        "message": "Company logo uploaded successfully.",
        "logo_url": logo_url,
        "data": updated
    }