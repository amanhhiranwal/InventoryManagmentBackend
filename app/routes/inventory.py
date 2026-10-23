from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
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
    serial_number: Optional[str] = ""
    product_type_code: str
    category: str
    attributes: dict = {}
    image_base64: Optional[str] = None
    # The company that stocks this product. Left out, the product is shared
    # with every company.
    company_id: Optional[str] = None
    # On an edit, the product's whole list of serial numbers: any left out
    # are removed and any new ones added. Left out, serial_number names the
    # one record being edited, as before.
    serial_numbers: Optional[List[str]] = None

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

@router.get("/items/bulk/template")
def bulk_upload_template(
    current_user=Depends(require_permission("inventory.bulk_upload")),
    db: Session = Depends(get_db),
):
    """The blank sheet to fill in, with today's masters listed on it."""

    from fastapi.responses import Response

    from app.services import inventory_import_service

    workbook = inventory_import_service.template_workbook(db, current_user)

    return Response(
        content=workbook,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition":
                'attachment; filename="Inventory_Upload_Template.xlsx"'
        },
    )


@router.post("/items/bulk")
async def bulk_upload(
    file: UploadFile = File(...),
    commit: bool = False,
    current_user=Depends(require_permission("inventory.bulk_upload")),
    db: Session = Depends(get_db),
):
    """Load a sheet of products.

    Called twice by the screen: once to show what the file would do, and
    again with ``commit`` to do it. Both read the file the same way, so
    what was shown is what is saved - and a sheet with anything wrong in
    it saves nothing at all.
    """

    from app.services import inventory_import_service

    content = await file.read()

    if not content:
        raise HTTPException(status_code=400, detail="That file is empty.")

    # Big enough for a catalogue, small enough that nobody uploads a
    # database by accident.
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail="That file is larger than 5 MB. Split it into two sheets.",
        )

    result = inventory_import_service.load(
        content=content,
        filename=file.filename or "",
        current_user=current_user,
        db=db,
        commit=commit,
    )

    if result["errors"]:
        message = (
            f"{len(result['errors'])} problem"
            f"{'' if len(result['errors']) == 1 else 's'} in that sheet. "
            "Nothing has been loaded."
        )
    elif result["created"]:
        message = f"{result['created']} products loaded from that sheet."
    else:
        message = (
            f"{result['products']} products ready to load from "
            f"{result['rows']} rows."
        )

    return {
        "success": not result["errors"],
        "message": message,
        "data": result,
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
    page: Optional[int] = None,
    size: Optional[int] = None,
    grouped: Optional[bool] = True,
    current_user=Depends(require_permission("inventory.read")),
    db: Session = Depends(get_db),
):
    """The catalogue, a page at a time when a page is asked for.

    The figures across the top come back with it, worked out by the
    database over the whole catalogue - a total that only adds up the
    hundred rows on screen is not a total.

    When grouped is True (default), items with multiple serial numbers
    are represented as a single product row with aggregate stock.
    """

    items = InventoryService.get_items(
        product_type_code=product_type_code,
        search=search,
        company_id=company_id,
        current_user=current_user,
        db=db,
        page=page,
        size=size,
        grouped=grouped if grouped is not None else True,
    )

    summary = InventoryService.summarise(
        search=search,
        company_id=company_id,
        current_user=current_user,
        db=db,
        grouped=grouped if grouped is not None else True,
    )

    counts = summary.pop("counts", {})

    # With a category chosen, the page is that category; the total it is
    # paged against has to be that category's count, not the catalogue's.
    total = (
        counts.get(str(product_type_code or "").upper().strip(), 0)
        if product_type_code
        else summary["products"]
    )

    return {
        "success": True,
        "data": items,
        "total": total,
        "summary": summary,
        "counts": counts,
    }


@router.get("/items/{item_id}/image")
def get_item_image(
    item_id: str,
    current_user=Depends(require_permission("inventory.read")),
    db: Session = Depends(get_db),
):
    """One product's picture.

    Kept off the list: a data: URI of a couple of megabytes on every
    record is a hundred megabytes of wire to draw thumbnails the size of
    a thumbnail.
    """

    return {
        "success": True,
        "data": InventoryService.image_for(item_id, current_user=current_user, db=db),
    }

@router.get("/items/{item_id}/label")
def item_label(
    item_id: str,
    serial: Optional[str] = None,
    current_user=Depends(require_permission("inventory.read")),
    db: Session = Depends(get_db),
):
    """The sticker that goes on the box: a barcode, a QR and what they say.

    Served as SVG so it prints crisp at whatever size the sticker is and
    the screen can show the same markup it prints.
    """

    from fastapi.responses import Response

    from app.services.product_label_service import label_svg

    obj_id, item = InventoryService._get_in_scope(
        item_id, current_user=current_user, db=db
    )

    attributes = item.get("attributes") or {}

    from urllib.parse import quote
    from app.services.company_profile_service import app_url

    sku = (serial or item.get("serial_number") or "").strip()

    try:
        link = app_url(f"/inventory?product={quote(sku)}", db)
    except Exception:
        link = ""

    svg = label_svg(
        sku=sku,
        name=item.get("name") or "",
        hsn=str(attributes.get("hsn_code") or ""),
        model=str(attributes.get("model_number") or ""),
        specification=str(attributes.get("specification") or ""),
        unit=str(attributes.get("unit") or ""),
        url=link,
    )

    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={
            "Content-Disposition":
                f'inline; filename="{sku or "label"}.svg"'
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
        # Same question about the picture: a form that never had it must
        # not clear it. The list does not carry images any more.
        image_set="image_base64" in request.model_fields_set,
        current_user=current_user,
        db=db,
        serial_numbers=request.serial_numbers,
    )
    return {
        "success": True,
        "message": "Inventory item updated successfully.",
        "data": doc
    }

@router.delete("/items/{item_id}")
def delete_item(
    item_id: str,
    all_units: bool = True,
    current_user=Depends(require_permission("inventory.delete")),
    db: Session = Depends(get_db),
):
    InventoryService.delete_item(item_id, all_units=all_units, current_user=current_user, db=db)
    return {
        "success": True,
        "message": "Inventory item deleted successfully."
    }
