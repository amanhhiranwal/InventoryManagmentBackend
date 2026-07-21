from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.models.lead import Lead
from app.models.user import User
from app.models.user_role import UserRole
from app.models.workflow import Workflow
from uuid import UUID
from fastapi import HTTPException

class LeadService:
    @staticmethod
    def get_junior_roles_for_user(user: User, db: Session) -> set[str]:
        user_role_ids = {str(role.id) for role in user.roles}
        workflows = db.query(Workflow).all()
        
        junior_role_ids = set()
        for wf in workflows:
            # handle nodes and edges format
            nodes_list = wf.nodes if isinstance(wf.nodes, list) else []
            edges_list = wf.edges if isinstance(wf.edges, list) else []
            
            # build adjacency list
            adj = {}
            for edge in edges_list:
                src = edge.get("source")
                tgt = edge.get("target")
                if src and tgt:
                    adj.setdefault(src, []).append(tgt)
            
            # find start nodes corresponding to user roles
            start_nodes = []
            for n in nodes_list:
                role_id = n.get("data", {}).get("role_id")
                if role_id in user_role_ids:
                    start_nodes.append(n.get("id"))
            
            # BFS traverse downwards
            visited = set()
            queue = list(start_nodes)
            while queue:
                curr = queue.pop(0)
                if curr not in visited:
                    visited.add(curr)
                    for neighbor in adj.get(curr, []):
                        if neighbor not in visited:
                            queue.append(neighbor)
            
            # extract junior roles
            for n in nodes_list:
                if n.get("id") in visited:
                    role_id = n.get("data", {}).get("role_id")
                    if role_id and role_id not in user_role_ids:
                        junior_role_ids.add(role_id)
                        
        return junior_role_ids

    @staticmethod
    def get_visible_leads(user: User, db: Session) -> list[Lead]:
        if user.is_super_admin:
            return db.query(Lead).order_by(Lead.created_at.desc()).all()
            
        junior_role_ids = LeadService.get_junior_roles_for_user(user, db)
        
        # Find all users who have these junior roles
        junior_user_ids = []
        if junior_role_ids:
            junior_user_roles = db.query(UserRole.user_id).filter(
                UserRole.role_id.in_([UUID(rid) for rid in junior_role_ids])
            ).all()
            junior_user_ids = [ur.user_id for ur in junior_user_roles]
            
        # Visible leads are created by user OR created by juniors
        query = db.query(Lead).filter(
            or_(
                Lead.creator_id == user.id,
                Lead.creator_id.in_(junior_user_ids)
            )
        )
        return query.order_by(Lead.created_at.desc()).all()

    @staticmethod
    def create_lead(request, creator_id: UUID, db: Session) -> Lead:
        lead = Lead(
            title=request.title,
            description=request.description,
            status=request.status or "new",
            creator_id=creator_id,
        )
        db.add(lead)
        db.commit()
        db.refresh(lead)
        return lead

    @staticmethod
    def progress_lead(lead_id: str, request, user: User, db: Session) -> Lead:
        lead = db.query(Lead).filter(Lead.id == UUID(lead_id)).first()
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
            
        is_authorized = False
        if user.is_super_admin:
            is_authorized = True
        elif lead.creator_id == user.id:
            is_authorized = True
        else:
            junior_role_ids = LeadService.get_junior_roles_for_user(user, db)
            if junior_role_ids:
                creator = db.query(User).filter(User.id == lead.creator_id).first()
                if creator:
                    creator_role_ids = {str(r.id) for r in creator.roles}
                    if creator_role_ids.intersection(junior_role_ids):
                        is_authorized = True
                        
        if not is_authorized:
            raise HTTPException(status_code=403, detail="Only the lead creator and their reporting superiors can progress this lead")
            
        lead.stage = request.stage
        if request.status is not None:
            lead.status = request.status
        if request.demo_status is not None:
            lead.demo_status = request.demo_status
        if request.requirements is not None:
            lead.requirements = request.requirements
        if request.quotation_type is not None:
            lead.quotation_type = request.quotation_type
        if request.quotation_items is not None:
            lead.quotation_items = request.quotation_items
            
        db.commit()
        db.refresh(lead)
        return lead
