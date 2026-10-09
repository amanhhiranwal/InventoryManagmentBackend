"""Files attached to a record: the purchase order, the signed copy, the spec.

Until now a form took the file, showed it in a list, and sent only its
name, size and type up with the record. The bytes stayed in the browser
and went when the tab closed, so a sales order said it had the customer's
PO attached and nobody could ever open it.

So the file is stored here and the record keeps the key it came back
with. Three things worth knowing about how:

  * The stored name is a fresh uuid, never the one the browser supplied.
    An uploaded filename is attacker-controlled text, and ".." in it is
    how a file ends up somewhere it was never meant to be written.
  * The original name is kept in the record, not on disk, so the list can
    still read "Purchase Order.pdf".
  * Reading one needs a signed-in user, like any other record. That means
    a preview cannot be an <img src> pointing here - the browser sends no
    Authorization header on those - so the screen fetches it and shows
    what comes back. The alternative, an open endpoint guarded only by an
    unguessable name, makes a leaked link a permanent one.
"""

import os
import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database.postgres import get_db
from app.middleware.auth_middleware import get_current_user
from app.models.attachment import Attachment
from app.services.attachment_access import may_read
from app.services.attachment_store import (
    ALLOWED,
    ATTACHMENT_DIR,
    KEY,
    MAX_BYTES,
    media_type,
)

router = APIRouter(prefix="/attachments", tags=["Attachments"])


@router.post("")
@router.post("/")
def upload_attachment(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Store one file and hand back the key the record should keep."""

    original = os.path.basename(file.filename or "").strip()
    ext = os.path.splitext(original)[1].lower()

    if ext not in ALLOWED:
        raise HTTPException(
            status_code=400,
            detail="Attach a PDF, Word, Excel or image file.",
        )

    ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)

    key = f"{uuid.uuid4().hex}{ext}"
    path = ATTACHMENT_DIR / key
    written = 0

    try:
        with open(path, "wb") as target:
            # In pieces, and counted as it goes: a size read from the
            # request headers is the uploader's word for it, and a file
            # already on disk is too late to refuse.
            while chunk := file.file.read(1024 * 1024):
                written += len(chunk)

                if written > MAX_BYTES:
                    raise HTTPException(
                        status_code=400,
                        detail="Each file must be 10MB or less.",
                    )

                target.write(chunk)
    except HTTPException:
        path.unlink(missing_ok=True)
        raise
    except Exception:  # noqa: BLE001
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Could not store the file.")

    # Who uploaded it is the only thing that can answer "may this person
    # read it" until the record it belongs to has been saved.
    try:
        db.add(
            Attachment(
                key=key,
                name=original or key,
                size=written,
                content_type=ALLOWED[ext],
                uploaded_by=UUID(str(current_user["user_id"])),
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Could not store the file.")

    return {
        "success": True,
        "message": "Attachment uploaded.",
        "data": {
            "key": key,
            "name": original or key,
            "size": written,
            "type": ALLOWED[ext],
        },
    }


@router.get("/{key}")
def read_attachment(
    key: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Hand the file back, for the preview to show or the browser to save.

    Signed in is not enough. A file follows the record it hangs off, so
    another zone's purchase order is no more readable here than the order
    itself is - see attachment_access, which asks each record the
    question rather than keeping a second rulebook.
    """

    if not KEY.match(key or ""):
        raise HTTPException(status_code=404, detail="No such attachment.")

    path = ATTACHMENT_DIR / key

    if not path.exists():
        raise HTTPException(status_code=404, detail="No such attachment.")

    if not may_read(key, current_user, db):
        # Not 403: whether a file exists is itself worth not confirming
        # to somebody with no business reading it.
        raise HTTPException(status_code=404, detail="No such attachment.")

    ext = os.path.splitext(key)[1].lower()

    return FileResponse(
        str(path),
        media_type=media_type(key),
        # inline, so a PDF opens in the viewer rather than downloading.
        headers={"Content-Disposition": f'inline; filename="{key}"'},
    )
