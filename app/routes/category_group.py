from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.middleware.permission_middleware import require_permission, require_super_admin
from app.schemas.category_group import CreateCategoryGroupRequest
from app.services.category_group_service import CategoryGroupService

# Masters are maintained by the super admin; reads stay open to the
# permissions below because the sales forms use them for their dropdowns.
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
    current_user=Depends(require_permission("category_group.read"))
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
