from sqlalchemy.orm import Session
from app.models.workflow import Workflow
from uuid import UUID
from fastapi import HTTPException

class WorkflowService:
    @staticmethod
    def create(request, db: Session) -> Workflow:
        workflow = Workflow(
            name=request.name,
            description=request.description,
            nodes=request.nodes,
            edges=request.edges,
        )
        db.add(workflow)
        db.commit()
        db.refresh(workflow)
        return workflow

    @staticmethod
    def get_all(db: Session) -> list[Workflow]:
        return db.query(Workflow).order_by(Workflow.created_at.desc()).all()

    @staticmethod
    def delete(workflow_id: str, db: Session) -> None:
        wf = db.query(Workflow).filter(Workflow.id == UUID(workflow_id)).first()
        if not wf:
            raise HTTPException(status_code=404, detail="Workflow not found")
        db.delete(wf)
        db.commit()

    @staticmethod
    def update(workflow_id: str, request, db: Session) -> Workflow:
        wf = db.query(Workflow).filter(Workflow.id == UUID(workflow_id)).first()
        if not wf:
            raise HTTPException(status_code=404, detail="Workflow not found")
        wf.name = request.name
        if request.description is not None:
            wf.description = request.description
        wf.nodes = request.nodes
        wf.edges = request.edges
        db.commit()
        db.refresh(wf)
        return wf
