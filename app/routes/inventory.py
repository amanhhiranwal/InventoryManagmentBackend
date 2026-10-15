from typing import Optional

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
    current_user=Depends(require_permission("inventory.read")),
    db: Session = Depends(get_db),
):
    """The catalogue, a page at a time when a page is asked for.

    The figures across the top come back with it, worked out by the
    database over the whole catalogue - a total that only adds up the
    hundred rows on screen is not a total.

    Without page and size this returns everything, which is what the
    screens that read the catalogue into a picker still expect.
    """

    items = InventoryService.get_items(
        product_type_code=product_type_code,
        search=search,
        company_id=company_id,
        current_user=current_user,
        db=db,
        page=page,
        size=size,
    )

    summary = InventoryService.summarise(
        search=search,
        company_id=company_id,
        current_user=current_user,
        db=db,
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

    # Where the QR sends a phone: this product, on the Inventory screen,
    # opened on its details. Scanning the sticker on the box and pressing
    # the eye on the row land in the same place.
    from urllib.parse import quote

    from app.services.company_profile_service import app_url

    try:
        link = app_url(
            f"/inventory?product={quote(str(item.get('serial_number') or ''))}", db
        )
    except Exception:
        # No address configured is a reason for the QR to carry plain
        # text, not a reason to fail printing a label.
        link = ""

    svg = label_svg(
        sku=item.get("serial_number") or "",
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
        # Same question about the picture: a form that never had it must
        # not clear it. The list does not carry images any more.
        image_set="image_base64" in request.model_fields_set,
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
