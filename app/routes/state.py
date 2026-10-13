from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.middleware.permission_middleware import require_super_admin
from app.schemas.state import CreateStateRequest, UpdateStateRequest
from app.services.state_service import StateService

router = APIRouter(
    prefix="/states",
    tags=["States Master"],
)


def _serialise(st) -> dict:
    return {
        "id": str(st.id),
        "name": st.name,
        "code": st.code,
        #: Blank means the statutory table decides it.
        "gst_code": st.gst_code or "",
        "country": st.country,
    }

@router.post("/", dependencies=[Depends(require_super_admin)])
def create_state(
    request: CreateStateRequest,
    db: Session = Depends(get_db),
):
    st = StateService.create(request, db)
    return {
        "success": True,
        "message": "State created successfully.",
        "data": _serialise(st),
    }

@router.get("/")
def get_states(
    db: Session = Depends(get_db),
):
    sts = StateService.get_all(db)
    return {
        "success": True,
        "data": [_serialise(st) for st in sts],
    }

@router.put("/{st_id}", dependencies=[Depends(require_super_admin)])
def update_state(
    st_id: str,
    request: UpdateStateRequest,
    db: Session = Depends(get_db),
):
    """Correct a state - its name, its abbreviation, or its GST code."""

    st = StateService.update(int(st_id), request, db)

    return {
        "success": True,
        "message": "State updated successfully.",
        "data": _serialise(st),
    }


@router.delete("/{st_id}", dependencies=[Depends(require_super_admin)])
def delete_state(
    st_id: str,
    db: Session = Depends(get_db),
):
    StateService.delete(st_id, db)
    return {
        "success": True,
        "message": "State deleted successfully."
    }
