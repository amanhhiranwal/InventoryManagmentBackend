from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.middleware.permission_middleware import require_super_admin
from app.middleware.auth_middleware import get_current_user
from app.schemas.workflow import CreateWorkflowRequest
from app.services.workflow_service import WorkflowService

router = APIRouter(
    prefix="/workflows",
    tags=["Workflows"],
)

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
        "data": {
            "id": str(wf.id),
            "name": wf.name,
            "description": wf.description,
            "nodes": wf.nodes,
            "edges": wf.edges,
            "created_at": wf.created_at.isoformat(),
        }
    }

@router.get("/")
def get_workflows(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    workflows = WorkflowService.get_all(db)
    return {
        "success": True,
        "data": [
            {
                "id": str(wf.id),
                "name": wf.name,
                "description": wf.description,
                "nodes": wf.nodes,
                "edges": wf.edges,
                "created_at": wf.created_at.isoformat(),
            }
            for wf in workflows
        ]
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
        "data": {
            "id": str(wf.id),
            "name": wf.name,
            "description": wf.description,
            "nodes": wf.nodes,
            "edges": wf.edges,
            "created_at": wf.created_at.isoformat(),
        }
    }
