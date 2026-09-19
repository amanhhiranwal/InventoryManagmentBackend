from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.middleware.permission_middleware import require_granted, require_super_admin
from app.schemas.workflow import CreateWorkflowRequest
from app.services.workflow_service import WorkflowService

router = APIRouter(
    prefix="/workflows",
    tags=["Workflows"],
)

# The charts decide who sees whose records, so only a super admin changes
# them. A role given the Workflows page sees its own part read-only.


def _serialize(wf) -> dict:
    get = wf.get if isinstance(wf, dict) else lambda key: getattr(wf, key)
    created = get("created_at")

    return {
        "id": str(get("id")),
        "name": get("name"),
        "description": get("description"),
        "nodes": get("nodes"),
        "edges": get("edges"),
        "created_at": created.isoformat() if created else None,
    }


@router.post("/")
def create_workflow(
    request: CreateWorkflowRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin),
):
    wf = WorkflowService.create(request, db)
    return {
        "success": True,
        "message": "Workflow created successfully.",
        "data": _serialize(wf),
    }


@router.get("/")
def get_workflows(
    db: Session = Depends(get_db),
    current_user=Depends(require_granted("workflow.read")),
):
    return {
        "success": True,
        "data": [_serialize(wf) for wf in WorkflowService.get_visible(current_user, db)],
    }


@router.delete("/{workflow_id}")
def delete_workflow(
    workflow_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin),
):
    WorkflowService.delete(workflow_id, db)
    return {
        "success": True,
        "message": "Workflow deleted successfully."
    }


@router.put("/{workflow_id}")
def update_workflow(
    workflow_id: str,
    request: CreateWorkflowRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin),
):
    wf = WorkflowService.update(workflow_id, request, db)
    return {
        "success": True,
        "message": "Workflow updated successfully.",
        "data": _serialize(wf),
    }
