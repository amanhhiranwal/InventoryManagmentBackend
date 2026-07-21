from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.services.product_type_service import ProductTypeService
from app.schemas.product_type import CreateProductTypeRequest
from app.middleware.permission_middleware import require_super_admin

router = APIRouter(
    prefix="/product-types",
    tags=["Product Types"],
)

@router.post("/")
def create_product_type(
    request: CreateProductTypeRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin)
):
    pt = ProductTypeService.create(request, db)
    return {
        "success": True,
        "message": "Product Type created successfully.",
        "data": {
            "id": str(pt.id),
            "name": pt.name,
            "code": pt.code,
            "category": pt.category,
            "description": pt.description
        }
    }

@router.get("/")
def get_product_types(
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin)
):
    pts = ProductTypeService.get_all(db)
    return {
        "success": True,
        "data": [
            {
                "id": str(pt.id),
                "name": pt.name,
                "code": pt.code,
                "category": pt.category,
                "description": pt.description
            }
            for pt in pts
        ]
    }

@router.put("/{pt_id}")
def update_product_type(
    pt_id: str,
    request: CreateProductTypeRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin)
):
    pt = ProductTypeService.update(pt_id, request, db)
    return {
        "success": True,
        "message": "Product Type updated successfully.",
        "data": {
            "id": str(pt.id),
            "name": pt.name,
            "code": pt.code,
            "category": pt.category,
            "description": pt.description
        }
    }

@router.delete("/{pt_id}")
def delete_product_type(
    pt_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin)
):
    ProductTypeService.delete(pt_id, db)
    return {
        "success": True,
        "message": "Product Type deleted successfully."
    }
