from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.middleware.permission_middleware import require_permission, require_super_admin
from app.schemas.customer_type import CreateCustomerTypeRequest
from app.services.customer_type_service import CustomerTypeService

# Masters are maintained by the super admin; reads stay open to the
# permissions below because the sales forms use them for their dropdowns.
router = APIRouter(
    prefix="/customer-types",
    tags=["Customer Types"],
)

@router.post("/")
def create_customer_type(
    request: CreateCustomerTypeRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin)
):
    ct = CustomerTypeService.create(request, db)
    return {
        "success": True,
        "message": "Customer Type created successfully.",
        "data": {
            "id": str(ct.id),
            "name": ct.name,
            "code": ct.code,
            "description": ct.description
        }
    }

@router.get("/")
def get_customer_types(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("customer_type.read"))
):
    cts = CustomerTypeService.get_all(db)
    return {
        "success": True,
        "data": [
            {
                "id": str(ct.id),
                "name": ct.name,
                "code": ct.code,
                "description": ct.description
            }
            for ct in cts
        ]
    }

@router.delete("/{ct_id}")
def delete_customer_type(
    ct_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin)
):
    CustomerTypeService.delete(ct_id, db)
    return {
        "success": True,
        "message": "Customer Type deleted successfully."
    }
