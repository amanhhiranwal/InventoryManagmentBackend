from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.models.lead import Lead
from app.models.workflow import Workflow
from uuid import UUID
from fastapi import HTTPException
import requests
import os

def get_users_by_roles_helper(role_ids: list[str], db: Session = None) -> list[str]:
    if not role_ids:
        return []
    if db is not None:
        try:
            from app.models.user_role import UserRole
            role_uuids = [UUID(rid) for rid in role_ids if rid]
            user_roles = db.query(UserRole.user_id).filter(UserRole.role_id.in_(role_uuids)).all()
            if user_roles:
                return [str(ur.user_id) for ur in user_roles]
        except Exception:
            pass
    try:
        auth_host = os.getenv("AUTH_SERVICE_HOST", "auth_service")
        auth_port = os.getenv("AUTH_SERVICE_PORT", "8001")
        response = requests.get(
            f"http://{auth_host}:{auth_port}/api/v1/users/by-roles",
            params={"role_ids": role_ids},
            timeout=1
        )
        if response.status_code == 200:
            return response.json().get("user_ids", [])
    except Exception:
        pass
    return []

def get_user_roles_helper(user_id: str, db: Session = None) -> list[str]:
    if not user_id:
        return []
    if db is not None:
        try:
            from app.models.user_role import UserRole
            user_roles = db.query(UserRole.role_id).filter(UserRole.user_id == UUID(user_id)).all()
            if user_roles:
                return [str(ur.role_id) for ur in user_roles]
        except Exception:
            pass
    try:
        auth_host = os.getenv("AUTH_SERVICE_HOST", "auth_service")
        auth_port = os.getenv("AUTH_SERVICE_PORT", "8001")
        response = requests.get(
            f"http://{auth_host}:{auth_port}/api/v1/users/{user_id}/role-ids",
            timeout=1
        )
        if response.status_code == 200:
            return response.json().get("role_ids", [])
    except Exception:
        pass
    return []

def get_visible_creator_user_ids(current_user: dict, db: Session) -> list[str]:
    user_id = current_user.get("user_id")
    if not user_id:
        return []
    is_super_admin = current_user.get("is_super_admin", False)
    if is_super_admin:
        return []  # Empty list signifies unrestricted Super Admin access

    role_id = current_user.get("role_id")
    user_role_ids = {role_id} if role_id else set()

    junior_role_ids = LeadService.get_junior_roles_for_user(user_role_ids, db)
    junior_user_ids = []
    if junior_role_ids:
        junior_user_ids = get_users_by_roles_helper(list(junior_role_ids), db)

    return list(set([user_id] + junior_user_ids))


class LeadService:
    @staticmethod
    def get_junior_roles_for_user(user_role_ids: set[str], db: Session) -> set[str]:
        workflows = db.query(Workflow).all()
        
        junior_role_ids = set()
        for wf in workflows:
            nodes_list = wf.nodes if isinstance(wf.nodes, list) else []
            edges_list = wf.edges if isinstance(wf.edges, list) else []
            
            adj = {}
            for edge in edges_list:
                src = edge.get("source")
                tgt = edge.get("target")
                if src and tgt:
                    adj.setdefault(src, []).append(tgt)
            
            start_nodes = []
            for n in nodes_list:
                role_id = n.get("data", {}).get("role_id")
                if role_id in user_role_ids:
                    start_nodes.append(n.get("id"))
            
            visited = set()
            queue = list(start_nodes)
            while queue:
                curr = queue.pop(0)
                if curr not in visited:
                    visited.add(curr)
                    for neighbor in adj.get(curr, []):
                        if neighbor not in visited:
                            queue.append(neighbor)
            
            for n in nodes_list:
                if n.get("id") in visited:
                    role_id = n.get("data", {}).get("role_id")
                    if role_id and role_id not in user_role_ids:
                        junior_role_ids.add(role_id)
                        
        return junior_role_ids

    @staticmethod
    def get_visible_leads(user_id: str, is_super_admin: bool, user_role_ids: set[str], db: Session) -> list[Lead]:
        if is_super_admin:
            return db.query(Lead).order_by(Lead.created_at.desc()).all()
            
        junior_role_ids = LeadService.get_junior_roles_for_user(user_role_ids, db)
        
        junior_user_ids = []
        if junior_role_ids:
            junior_user_ids = get_users_by_roles_helper(list(junior_role_ids), db)
            
        query = db.query(Lead).filter(
            or_(
                Lead.creator_id == UUID(user_id),
                Lead.assigned_to_id == UUID(user_id),
                Lead.creator_id.in_([UUID(uid) for uid in junior_user_ids])
            )
        )
        return query.order_by(Lead.created_at.desc()).all()

    @staticmethod
    def create_lead(request, creator_id: UUID, db: Session) -> Lead:
        assigned_to_uuid = UUID(request.assigned_to_id) if getattr(request, "assigned_to_id", None) else None
        lead = Lead(
            title=request.title,
            description=request.description,
            status=request.status or "new",
            creator_id=creator_id,
            assigned_to_id=assigned_to_uuid
        )
        db.add(lead)
        db.commit()
        db.refresh(lead)
        return lead

    @staticmethod
    def assign_lead(lead_id: str, target_user_id: str, assigner_id: str, is_super_admin: bool, user_role_ids: set[str], db: Session) -> Lead:
        lead = db.query(Lead).filter(Lead.id == UUID(lead_id)).first()
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")

        is_authorized = False
        if is_super_admin:
            is_authorized = True
        elif str(lead.creator_id) == assigner_id or (lead.assigned_to_id and str(lead.assigned_to_id) == assigner_id):
            is_authorized = True
        else:
            junior_role_ids = LeadService.get_junior_roles_for_user(user_role_ids, db)
            if junior_role_ids:
                creator_role_ids = set(get_user_roles_http(str(lead.creator_id)))
                if creator_role_ids.intersection(junior_role_ids):
                    is_authorized = True

        if not is_authorized:
            raise HTTPException(status_code=403, detail="Only superiors within authority scope or lead owners can reassign this lead")

        lead.assigned_to_id = UUID(target_user_id)
        lead.assigned_by_id = UUID(assigner_id)
        db.commit()
        db.refresh(lead)
        return lead

    @staticmethod
    def progress_lead(lead_id: str, request, user_id: str, is_super_admin: bool, user_role_ids: set[str], db: Session) -> Lead:
        lead = db.query(Lead).filter(Lead.id == UUID(lead_id)).first()
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
            
        is_authorized = False
        if is_super_admin:
            is_authorized = True
        elif str(lead.creator_id) == user_id or (lead.assigned_to_id and str(lead.assigned_to_id) == user_id):
            is_authorized = True
        else:
            junior_role_ids = LeadService.get_junior_roles_for_user(user_role_ids, db)
            if junior_role_ids:
                creator_role_ids = set(get_user_roles_http(str(lead.creator_id)))
                if creator_role_ids.intersection(junior_role_ids):
                    is_authorized = True
                        
        if not is_authorized:
            raise HTTPException(status_code=403, detail="Only the lead creator, assigned user, and their reporting superiors can progress this lead")
            
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
