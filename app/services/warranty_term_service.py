from fastapi import HTTPException
from sqlalchemy import asc
from sqlalchemy.orm import Session

from app.models.warranty_term import WarrantyTerm
from app.schemas.warranty_term import (
    CreateWarrantyTermRequest,
    UpdateWarrantyTermRequest,
)

#: The lengths every document offers. What each costs is set per product,
#: on the product record, because five years on a panel and five years on
#: a camera are different undertakings.
DEFAULT_WARRANTY_TERMS = [
    {
        "name": "3 Years",
        "years": 3,
        "is_default": True,
        "description": "Standard cover, included in the price.",
    },
    {
        "name": "4 Years",
        "years": 4,
        "is_default": False,
        "description": "One extra year. Priced on each product.",
    },
    {
        "name": "5 Years",
        "years": 5,
        "is_default": False,
        "description": "Two extra years. Priced on each product.",
    },
]


class WarrantyTermService:

    @staticmethod
    def seed_defaults(db: Session) -> None:
        if db.query(WarrantyTerm).count() > 0:
            return

        for item in DEFAULT_WARRANTY_TERMS:
            db.add(WarrantyTerm(**item, is_active=True))

        db.commit()

    @staticmethod
    def get_all(db: Session, include_inactive: bool = False) -> list[WarrantyTerm]:
        WarrantyTermService.seed_defaults(db)

        query = db.query(WarrantyTerm)

        if not include_inactive:
            query = query.filter(WarrantyTerm.is_active.is_(True))

        return query.order_by(asc(WarrantyTerm.years), asc(WarrantyTerm.name)).all()

    @staticmethod
    def get_by_name(db: Session, name: str | None) -> WarrantyTerm | None:
        if not name:
            return None

        return (
            db.query(WarrantyTerm)
            .filter(WarrantyTerm.name == str(name).strip())
            .first()
        )

    @staticmethod
    def default_term(db: Session) -> WarrantyTerm | None:
        WarrantyTermService.seed_defaults(db)

        return (
            db.query(WarrantyTerm)
            .filter(WarrantyTerm.is_default.is_(True))
            .order_by(asc(WarrantyTerm.years))
            .first()
        )

    @staticmethod
    def _clear_other_defaults(db: Session, keep_id: int | None) -> None:
        """Only one term is the standard one, so setting it unsets the rest."""

        query = db.query(WarrantyTerm).filter(WarrantyTerm.is_default.is_(True))

        if keep_id is not None:
            query = query.filter(WarrantyTerm.id != keep_id)

        for other in query.all():
            other.is_default = False

    @staticmethod
    def create(request: CreateWarrantyTermRequest, db: Session) -> WarrantyTerm:
        name = (request.name or "").strip()

        if not name:
            raise HTTPException(status_code=400, detail="Warranty term name is required.")

        if db.query(WarrantyTerm).filter(WarrantyTerm.name == name).first():
            raise HTTPException(
                status_code=400, detail=f"A warranty term called “{name}” already exists."
            )

        term = WarrantyTerm(
            name=name,
            years=request.years,
            is_default=request.is_default,
            description=request.description,
            is_active=True,
        )

        db.add(term)
        db.flush()

        if request.is_default:
            WarrantyTermService._clear_other_defaults(db, term.id)

        db.commit()
        db.refresh(term)

        return term

    @staticmethod
    def update(term_id: str, request: UpdateWarrantyTermRequest, db: Session) -> WarrantyTerm:
        term = db.query(WarrantyTerm).filter(WarrantyTerm.id == int(term_id)).first()

        if not term:
            raise HTTPException(status_code=404, detail="Warranty term not found.")

        if request.name is not None:
            name = request.name.strip()

            clash = (
                db.query(WarrantyTerm)
                .filter(WarrantyTerm.name == name, WarrantyTerm.id != term.id)
                .first()
            )

            if clash:
                raise HTTPException(
                    status_code=400,
                    detail=f"A warranty term called “{name}” already exists.",
                )

            term.name = name

        for field in ("years", "description", "is_active"):
            value = getattr(request, field, None)
            if value is not None:
                setattr(term, field, value)

        if request.is_default is not None:
            term.is_default = request.is_default

            if request.is_default:
                WarrantyTermService._clear_other_defaults(db, term.id)

        db.commit()
        db.refresh(term)

        return term

    @staticmethod
    def delete(term_id: str, db: Session) -> None:
        term = db.query(WarrantyTerm).filter(WarrantyTerm.id == int(term_id)).first()

        if not term:
            raise HTTPException(status_code=404, detail="Warranty term not found.")

        if term.is_default:
            raise HTTPException(
                status_code=400,
                detail=(
                    "The standard warranty term cannot be removed. Make another "
                    "term the standard one first."
                ),
            )

        # Kept rather than deleted: documents already quote it by name, and
        # a term that vanishes leaves those lines describing cover nobody
        # can look up.
        term.is_active = False
        db.commit()


