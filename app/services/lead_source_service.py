from sqlalchemy.orm import Session
from sqlalchemy import asc
from app.models.lead_source import LeadSource
from app.schemas.lead_source import CreateLeadSourceRequest

DEFAULT_LEAD_SOURCES = [
    {"name": "Marketing", "code": "MARKETING", "description": "Leads generated through marketing activities"},
    {"name": "Cold Calling", "code": "COLD_CALLING", "description": "Leads generated through cold calling"},
    {"name": "In-bound", "code": "INBOUND", "description": "Leads generated through inbound enquiries"},
]

class LeadSourceService:

    @staticmethod
    def seed_default_lead_sources(db: Session):
        count = db.query(LeadSource).count()
        if count > 0:
            return
        
        for item in DEFAULT_LEAD_SOURCES:
            src = LeadSource(
                name=item["name"],
                code=item["code"],
                description=item["description"],
                is_active=True
            )
            db.add(src)
        db.commit()

    @staticmethod
    def get_lead_sources(db: Session) -> list[LeadSource]:
        LeadSourceService.seed_default_lead_sources(db)
        return db.query(LeadSource).filter(LeadSource.is_active == True).order_by(asc(LeadSource.name)).all()

    @staticmethod
    def create_lead_source(request: CreateLeadSourceRequest, db: Session) -> LeadSource:
        src = LeadSource(
            name=request.name,
            code=request.code or request.name.lower().replace(" ", "_"),
            description=request.description,
            is_active=True
        )
        db.add(src)
        db.commit()
        db.refresh(src)
        return src
