from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.role import Role
from app.models.workflow import Workflow
from app.services.hierarchy_service import (
    HierarchyService,
    _descendants,
    _role_edges,
    find_cycle,
)


class WorkflowService:
    """Hierarchy charts: role nodes joined senior -> junior.

    Every saved chart feeds HierarchyService, which decides whose records
    each user can see, so a chart is checked before it is stored.
    """

    @staticmethod
    def _validate(request, db: Session, workflow_id: UUID | None = None) -> None:
        name = (request.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Workflow name is required.")

        nodes = request.nodes if isinstance(request.nodes, list) else []
        edges = request.edges if isinstance(request.edges, list) else []

        if len(nodes) < 2:
            raise HTTPException(
                status_code=400,
                detail="Add at least two roles to build a hierarchy.",
            )

        role_ids = {str(r.id) for r in db.query(Role.id).all()}
        node_ids: set[str] = set()
        seen_roles: set[str] = set()

        for node in nodes:
            role_id = str((node.get("data") or {}).get("role_id") or "")

            if role_id not in role_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"'{(node.get('data') or {}).get('label') or role_id}' is not an existing role.",
                )

            if role_id in seen_roles:
                raise HTTPException(
                    status_code=400,
                    detail=f"'{(node.get('data') or {}).get('label')}' appears more than once in this workflow.",
                )

            seen_roles.add(role_id)
            node_ids.add(node.get("id"))

        for edge in edges:
            if edge.get("source") not in node_ids or edge.get("target") not in node_ids:
                raise HTTPException(status_code=400, detail="A connection points at a role that is not on the chart.")

            if edge.get("source") == edge.get("target"):
                raise HTTPException(status_code=400, detail="A role cannot report to itself.")

        # Every chart is read as one hierarchy, so a loop across two charts
        # (A above B in one, B above A in another) is as broken as one within.
        others = db.query(Workflow)
        if workflow_id is not None:
            others = others.filter(Workflow.id != workflow_id)

        pairs = _role_edges(others.all()) + _role_edges([request])

        if find_cycle(pairs):
            raise HTTPException(
                status_code=400,
                detail="This would put a role above itself in the hierarchy. Check the direction of the connections.",
            )

    @staticmethod
    def create(request, db: Session) -> Workflow:
        WorkflowService._validate(request, db)

        workflow = Workflow(
            name=request.name.strip(),
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
    def get_visible(current_user: dict, db: Session) -> list[Workflow | dict]:
        """Charts as the caller may see them.

        A super admin sees every chart whole. Anyone else sees only charts
        their role is on, cut down to their own node and what sits below it,
        so peers and seniors are left off.
        """

        workflows = WorkflowService.get_all(db)

        if current_user.get("is_super_admin"):
            return workflows

        mine_roles = HierarchyService.user_role_ids(current_user.get("user_id"), db)
        result = []

        for wf in workflows:
            nodes = wf.nodes if isinstance(wf.nodes, list) else []
            edges = wf.edges if isinstance(wf.edges, list) else []

            mine = {
                n.get("id")
                for n in nodes
                if str((n.get("data") or {}).get("role_id")) in mine_roles
            }
            if not mine:
                continue

            children: dict[str, set[str]] = {}
            for e in edges:
                children.setdefault(e.get("source"), set()).add(e.get("target"))

            keep = mine | _descendants(mine, children)

            result.append({
                "id": wf.id,
                "name": wf.name,
                "description": wf.description,
                "nodes": [n for n in nodes if n.get("id") in keep],
                "edges": [e for e in edges if e.get("source") in keep and e.get("target") in keep],
                "created_at": wf.created_at,
            })

        return result

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

        WorkflowService._validate(request, db, wf.id)

        wf.name = request.name.strip()
        if request.description is not None:
            wf.description = request.description
        wf.nodes = request.nodes
        wf.edges = request.edges
        db.commit()
        db.refresh(wf)
        return wf
