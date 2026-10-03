from fastapi import HTTPException
from sqlalchemy import asc
from sqlalchemy.orm import Session

from app.models.lead import Lead
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
        return db.query(LeadSource).filter(LeadSource.is_active.is_(True)).order_by(asc(LeadSource.name)).all()

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

    @staticmethod
    def update_lead_source(
        source_id: str,
        request: CreateLeadSourceRequest,
        db: Session,
    ) -> LeadSource:
        src = db.query(LeadSource).filter(LeadSource.id == source_id).first()

        if src is None:
            raise HTTPException(status_code=404, detail="Lead source not found.")

        src.name = request.name
        src.code = request.code or request.name.lower().replace(" ", "_")
        src.description = request.description

        db.commit()
        db.refresh(src)
        return src

    @staticmethod
    def delete_lead_source(source_id: str, db: Session) -> None:
        """Retire a source, unless leads were taken through it.

        Deleting one a lead points at would leave that lead unable to say
        where it came from, which is the only thing the source is for. The
        refusal names the count so it is clear what is in the way.
        """

        src = db.query(LeadSource).filter(LeadSource.id == source_id).first()

        if src is None:
            raise HTTPException(status_code=404, detail="Lead source not found.")

        in_use = db.query(Lead).filter(Lead.lead_source_id == src.id).count()

        if in_use:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{in_use} lead{'s' if in_use > 1 else ''} came in through "
                    f"\u201c{src.name}\u201d, so it cannot be removed."
                ),
            )

        db.delete(src)
        db.commit()
