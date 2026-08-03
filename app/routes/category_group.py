from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.services.category_group_service import CategoryGroupService
from app.schemas.category_group import CreateCategoryGroupRequest
from app.middleware.permission_middleware import require_super_admin

router = APIRouter(
    prefix="/category-groups",
    tags=["Category Groups"],
)

@router.post("/")
def create_category_group(
    request: CreateCategoryGroupRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin)
):
    cg = CategoryGroupService.create(request, db)
    return {
        "success": True,
        "message": "Category Group created successfully.",
        "data": {
            "id": str(cg.id),
            "name": cg.name,
            "code": cg.code
        }
    }

@router.get("/")
def get_category_groups(
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin)
):
    cgs = CategoryGroupService.get_all(db)
    return {
        "success": True,
        "data": [
            {
                "id": str(cg.id),
                "name": cg.name,
                "code": cg.code
            }
            for cg in cgs
        ]
    }

@router.delete("/{cg_id}")
def delete_category_group(
    cg_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin)
):
    CategoryGroupService.delete(cg_id, db)
    return {
        "success": True,
        "message": "Category Group deleted successfully."
    }
