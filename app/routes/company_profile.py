import os
import shutil

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.middleware.permission_middleware import require_super_admin
from app.services.company_profile_service import CompanyProfileService

router = APIRouter(
    prefix="/company-profile",
    tags=["Company Profile"],
)

LOGO_DIR = "uploads/company_logos"


class CompanyProfileRequest(BaseModel):
    company_legal_name: str | None = None
    company_address: str | None = None
    company_gstin: str | None = None
    company_website: str | None = None
    company_email: str | None = None
    company_phone: str | None = None
    company_about: str | None = None
    company_offerings: str | None = None
    company_logo_path: str | None = None
    company_cover_image: str | None = None
    signatory_name: str | None = None
    signatory_title: str | None = None


@router.get("")
def get_profile(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Who the proposals and emails come from.

    Readable by anyone signed in - the quotation preview needs it - but
    only a super admin may change it.
    """

    return {"success": True, "data": CompanyProfileService.as_lists(db)}


@router.put("")
def save_profile(
    request: CompanyProfileRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin),
):
    saved = CompanyProfileService.save(
        request.model_dump(exclude_unset=True), db
    )

    return {
        "success": True,
        "message": "Company profile saved.",
        "data": {**saved, **CompanyProfileService.as_lists(db)},
    }


@router.post("/logo")
def upload_logo(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin),
):
    """Replace the mark printed on every proposal."""

    ext = os.path.splitext(file.filename or "")[1].lower() or ".png"

    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(
            status_code=400,
            detail="Use a PNG, JPG or WEBP image for the logo.",
        )

    os.makedirs(LOGO_DIR, exist_ok=True)
    path = os.path.join(LOGO_DIR, f"brand{ext}")

    with open(path, "wb") as target:
        shutil.copyfileobj(file.file, target)

    CompanyProfileService.save({"company_logo_path": path}, db)

    return {
        "success": True,
        "message": "Logo uploaded.",
        "data": CompanyProfileService.as_lists(db),
    }
