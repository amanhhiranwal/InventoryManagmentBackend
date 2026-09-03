from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.services.state_service import StateService
from app.schemas.state import CreateStateRequest

router = APIRouter(
    prefix="/states",
    tags=["States Master"],
)

@router.post("/")
def create_state(
    request: CreateStateRequest,
    db: Session = Depends(get_db),
):
    st = StateService.create(request, db)
    return {
        "success": True,
        "message": "State created successfully.",
        "data": {
            "id": str(st.id),
            "name": st.name,
            "code": st.code,
            "country": st.country
        }
    }

@router.get("/")
def get_states(
    db: Session = Depends(get_db),
):
    sts = StateService.get_all(db)
    return {
        "success": True,
        "data": [
            {
                "id": str(st.id),
                "name": st.name,
                "code": st.code,
                "country": st.country
            }
            for st in sts
        ]
    }

@router.delete("/{st_id}")
def delete_state(
    st_id: str,
    db: Session = Depends(get_db),
):
    StateService.delete(st_id, db)
    return {
        "success": True,
        "message": "State deleted successfully."
    }
