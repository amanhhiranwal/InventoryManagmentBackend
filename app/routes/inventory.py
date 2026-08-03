from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Any
from app.services.inventory_service import InventoryService
from app.middleware.permission_middleware import require_super_admin

router = APIRouter(
    prefix="/inventory",
    tags=["Inventory"],
)

class SaveTemplateRequest(BaseModel):
    product_type_code: str
    fields: list[dict]

class CreateItemRequest(BaseModel):
    name: str
    serial_number: str
    product_type_code: str
    category: str
    attributes: dict = {}
    image_base64: Optional[str] = None

@router.post("/templates")
def save_template(
    request: SaveTemplateRequest,
    current_user=Depends(require_super_admin)
):
    doc = InventoryService.save_template(request.product_type_code, request.fields)
    return {
        "success": True,
        "message": "Dynamic field templates saved successfully.",
        "data": doc
    }

@router.get("/templates/{product_type_code}")
def get_template(
    product_type_code: str,
    current_user=Depends(require_super_admin)
):
    template = InventoryService.get_template(product_type_code)
    return {
        "success": True,
        "data": template
    }

@router.post("/items")
def create_item(
    request: CreateItemRequest,
    current_user=Depends(require_super_admin)
):
    doc = InventoryService.create_item(
        name=request.name,
        serial_number=request.serial_number,
        product_type_code=request.product_type_code,
        category=request.category,
        attributes=request.attributes,
        image_base64=request.image_base64
    )
    return {
        "success": True,
        "message": "Inventory item created successfully.",
        "data": doc
    }

@router.get("/items")
def get_items(
    product_type_code: Optional[str] = None,
    search: Optional[str] = None,
    current_user=Depends(require_super_admin)
):
    items = InventoryService.get_items(product_type_code=product_type_code, search=search)
    return {
        "success": True,
        "data": items
    }

@router.put("/items/{item_id}")
def update_item(
    item_id: str,
    request: CreateItemRequest,
    current_user=Depends(require_super_admin)
):
    doc = InventoryService.update_item(
        item_id=item_id,
        name=request.name,
        serial_number=request.serial_number,
        product_type_code=request.product_type_code,
        category=request.category,
        attributes=request.attributes,
        image_base64=request.image_base64
    )
    return {
        "success": True,
        "message": "Inventory item updated successfully.",
        "data": doc
    }

@router.delete("/items/{item_id}")
def delete_item(
    item_id: str,
    current_user=Depends(require_super_admin)
):
    InventoryService.delete_item(item_id)
    return {
        "success": True,
        "message": "Inventory item deleted successfully."
    }
