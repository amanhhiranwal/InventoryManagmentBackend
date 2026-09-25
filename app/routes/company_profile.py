import os
import shutil
from pathlib import Path

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


def _store_image(file: UploadFile, name: str) -> str:
    ext = os.path.splitext(file.filename or "")[1].lower() or ".png"

    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(
            status_code=400,
            detail="Use a PNG, JPG or WEBP image.",
        )

    os.makedirs(LOGO_DIR, exist_ok=True)
    path = os.path.join(LOGO_DIR, f"{name}{ext}")

    with open(path, "wb") as target:
        shutil.copyfileobj(file.file, target)

    return path


@router.post("/logo")
def upload_logo(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin),
):
    """Replace the mark printed on every proposal."""

    CompanyProfileService.save(
        {"company_logo_path": _store_image(file, "brand")}, db
    )

    return {
        "success": True,
        "message": "Logo uploaded.",
        "data": CompanyProfileService.as_lists(db),
    }


@router.post("/cover")
def upload_cover(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin),
):
    """The picture on the proposal cover, under the addresses.

    Optional - without one the cover simply runs without a picture.
    """

    CompanyProfileService.save(
        {"company_cover_image": _store_image(file, "cover")}, db
    )

    return {
        "success": True,
        "message": "Cover image uploaded.",
        "data": CompanyProfileService.as_lists(db),
    }


@router.delete("/cover")
def remove_cover(
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin),
):
    CompanyProfileService.save({"company_cover_image": ""}, db)

    return {
        "success": True,
        "message": "Cover image removed.",
        "data": CompanyProfileService.as_lists(db),
    }


@router.get("/cover/image")
def get_cover_image(db: Session = Depends(get_db)):
    """The cover picture, for the preview to show.

    Open like the logo: an <img> tag cannot carry an Authorization header.
    """

    from fastapi.responses import FileResponse

    configured = CompanyProfileService.raw(db).get("company_cover_image") or ""
    path = Path(configured) if configured else None

    if path and not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / configured

    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="No cover image set.")

    return FileResponse(str(path))
