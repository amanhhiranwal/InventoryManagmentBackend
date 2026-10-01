from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.middleware.permission_middleware import require_permission
from app.services.company_scope_service import CompanyScopeService
from app.services.inventory_service import InventoryService

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
    # The company that stocks this product. Left out, the product is shared
    # with every company.
    company_id: Optional[str] = None

@router.post("/templates")
def save_template(
    request: SaveTemplateRequest,
    current_user=Depends(require_permission("inventory.create"))
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
    current_user=Depends(require_permission("inventory.read"))
):
    template = InventoryService.get_template(product_type_code)
    return {
        "success": True,
        "data": template
    }

@router.post("/items")
def create_item(
    request: CreateItemRequest,
    current_user=Depends(require_permission("inventory.create")),
    db: Session = Depends(get_db),
):
    doc = InventoryService.create_item(
        name=request.name,
        serial_number=request.serial_number,
        product_type_code=request.product_type_code,
        category=request.category,
        attributes=request.attributes,
        image_base64=request.image_base64,
        company_id=request.company_id,
        current_user=current_user,
        db=db,
    )
    return {
        "success": True,
        "message": "Inventory item created successfully.",
        "data": doc
    }

@router.get("/companies")
def get_scope_companies(
    current_user=Depends(require_permission("inventory.read")),
    db: Session = Depends(get_db),
):
    """The companies this user may file products under.

    A super admin gets every company; everyone else gets the ones on their
    profile, which is what the Company picker on the product form offers.
    """

    allowed = CompanyScopeService.visible_company_ids(current_user, db)
    names = CompanyScopeService.company_names(db)

    ids = sorted(names) if allowed is None else [i for i in allowed if i in names]

    return {
        "success": True,
        "data": [{"id": i, "company_name": names[i]} for i in sorted(ids, key=lambda i: names[i].lower())],
        "unrestricted": allowed is None,
    }


@router.get("/items")
def get_items(
    product_type_code: Optional[str] = None,
    search: Optional[str] = None,
    company_id: Optional[str] = None,
    current_user=Depends(require_permission("inventory.read")),
    db: Session = Depends(get_db),
):
    items = InventoryService.get_items(
        product_type_code=product_type_code,
        search=search,
        company_id=company_id,
        current_user=current_user,
        db=db,
    )
    return {
        "success": True,
        "data": items,
    }

@router.get("/items/{item_id}/label")
def item_label(
    item_id: str,
    current_user=Depends(require_permission("inventory.read")),
    db: Session = Depends(get_db),
):
    """The sticker that goes on the box: a barcode, a QR and what they say.

    Served as SVG so it prints crisp at whatever size the sticker is and
    the screen can show the same markup it prints.
    """

    from fastapi import HTTPException
    from fastapi.responses import Response

    from app.services.product_label_service import label_svg

    # Read through the same list the screen reads, so a label can only be
    # printed for a product the caller is allowed to see at all.
    item = next(
        (
            row for row in InventoryService.get_items(
                current_user=current_user, db=db
            )
            if str(row.get("_id")) == str(item_id)
        ),
        None,
    )

    if item is None:
        raise HTTPException(status_code=404, detail="Product not found")

    attributes = item.get("attributes") or {}

    svg = label_svg(
        sku=item.get("serial_number") or "",
        name=item.get("name") or "",
        hsn=str(attributes.get("hsn_code") or ""),
    )

    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={
            "Content-Disposition":
                f'inline; filename="{item.get("serial_number") or "label"}.svg"'
        },
    )


@router.put("/items/{item_id}")
def update_item(
    item_id: str,
    request: CreateItemRequest,
    current_user=Depends(require_permission("inventory.update")),
    db: Session = Depends(get_db),
):
    doc = InventoryService.update_item(
        item_id=item_id,
        name=request.name,
        serial_number=request.serial_number,
        product_type_code=request.product_type_code,
        category=request.category,
        attributes=request.attributes,
        image_base64=request.image_base64,
        company_id=request.company_id,
        # Whether the form sent the field at all, so "move to All companies"
        # reads differently from a form that never showed the picker.
        company_set="company_id" in request.model_fields_set,
        current_user=current_user,
        db=db,
    )
    return {
        "success": True,
        "message": "Inventory item updated successfully.",
        "data": doc
    }

@router.delete("/items/{item_id}")
def delete_item(
    item_id: str,
    current_user=Depends(require_permission("inventory.delete")),
    db: Session = Depends(get_db),
):
    InventoryService.delete_item(item_id, current_user=current_user, db=db)
    return {
        "success": True,
        "message": "Inventory item deleted successfully."
    }
