import os
import shutil
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.repositories.rbac_repository import RBACRepository
from pydantic import BaseModel
from typing import Optional

router = APIRouter(
    prefix="/profile",
    tags=["Profile"],
)

UPLOAD_DIR = "uploads/avatars"
os.makedirs(UPLOAD_DIR, exist_ok=True)

class ProfileUpdateRequest(BaseModel):
    first_name: str
    last_name: str
    phone_number: Optional[str] = None

@router.get("/")
def get_profile(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user = RBACRepository.get_user(db, current_user["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
        
    avatar_filename = f"avatar_{user.id}.jpg"
    avatar_path = os.path.join(UPLOAD_DIR, avatar_filename)
    has_avatar = os.path.exists(avatar_path)
    
    return {
        "success": True,
        "data": {
            "id": str(user.id),
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "phone_number": user.phone_number,
            "employee_id": user.employee_id,
            "is_super_admin": user.is_super_admin,
            "avatar_url": f"http://127.0.0.1:8000/uploads/avatars/{avatar_filename}" if has_avatar else None,
            "roles": [r.role_name for r in user.roles],
        }
    }

@router.put("/")
def update_profile(
    request: ProfileUpdateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user = RBACRepository.get_user(db, current_user["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
        
    user.first_name = request.first_name
    user.last_name = request.last_name
    if request.phone_number is not None:
        user.phone_number = request.phone_number
        
    db.commit()
    db.refresh(user)
    
    return {
        "success": True,
        "message": "Profile updated successfully.",
        "data": {
            "first_name": user.first_name,
            "last_name": user.last_name,
            "phone_number": user.phone_number,
        }
    }

@router.post("/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user = RBACRepository.get_user(db, current_user["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
        
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are allowed")
        
    avatar_filename = f"avatar_{user.id}.jpg"
    dest_path = os.path.join(UPLOAD_DIR, avatar_filename)
    
    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    return {
        "success": True,
        "message": "Avatar uploaded successfully.",
        "avatar_url": f"http://127.0.0.1:8000/uploads/avatars/{avatar_filename}"
    }

@router.get("/avatar")
def get_avatar(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user = RBACRepository.get_user(db, current_user["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
        
    avatar_filename = f"avatar_{user.id}.jpg"
    avatar_path = os.path.join(UPLOAD_DIR, avatar_filename)
    if os.path.exists(avatar_path):
        return FileResponse(avatar_path)
    else:
        raise HTTPException(status_code=404, detail="Avatar not found")
