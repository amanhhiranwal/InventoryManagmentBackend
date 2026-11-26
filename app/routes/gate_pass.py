"""Gate passes: read, download as PDF, and made by logistics for an order.

Passes are made when goods leave (dispatch commit, out for delivery); the
POST here is only for an order that went out before a pass could be made.
Who may read one is decided per pass in app/services/stock/gate_pass.py.
"""

from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.services.stock import gate_pass as service

router = APIRouter(prefix="/gate-passes", tags=["Gate Passes"])


@router.get("/")
def list_gate_passes(
    sales_order_id: Optional[int] = None,
    dispatch_order_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return {"success": True, "data": service.list_for(current_user, db, sales_order_id=sales_order_id,
                                                       dispatch_order_id=dispatch_order_id)}


@router.get("/{gate_pass_id}")
def get_gate_pass(gate_pass_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _, body = service.get(gate_pass_id, current_user, db)
    return {"success": True, "data": body["gate_pass"], "letterhead": body["letterhead"]}


@router.get("/{gate_pass_id}/pdf")
def download_gate_pass(gate_pass_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _, body = service.get(gate_pass_id, current_user, db)
    return Response(
        content=service.render(body["gate_pass"], body["letterhead"]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{service.filename(body["gate_pass"])}"'},
    )


class GatePassGenerate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vehicle_number: Optional[str] = Field(default=None, max_length=30)
    driver_name: Optional[str] = Field(default=None, max_length=120)


@router.post("/orders/{order_id}")
def generate_for_order(
    order_id: int,
    request: Optional[GatePassGenerate] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Generate the order's gate pass (Logistics). At Dispatched it needs
    the vehicle number and driver name; generating again returns the same pass."""

    body = request or GatePassGenerate()
    data = service.generate_for_order(order_id, current_user, db, body.vehicle_number, body.driver_name)
    return {"success": True, "message": f"Gate pass {data['gate_pass_no']} generated.", "data": data}
