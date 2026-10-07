from fastapi import HTTPException
from sqlalchemy import asc
from sqlalchemy.orm import Session

from app.models.warranty_term import WarrantyTerm
from app.schemas.warranty_term import (
    CreateWarrantyTermRequest,
    UpdateWarrantyTermRequest,
)

#: Three years is the cover included in the price, so it costs nothing to
#: have. The two longer terms are priced as a percentage of the line by
#: default; whoever owns the commercials sets the real figures in Masters,
#: and may switch either to a flat amount per unit instead.
DEFAULT_WARRANTY_TERMS = [
    {
        "name": "3 Years",
        "years": 3,
        "rate_mode": "PERCENT",
        "rate": 0.0,
        "is_default": True,
        "description": "Standard cover, included in the price.",
    },
    {
        "name": "4 Years",
        "years": 4,
        "rate_mode": "PERCENT",
        "rate": 0.0,
        "is_default": False,
        "description": "One extra year. Set the rate in Masters.",
    },
    {
        "name": "5 Years",
        "years": 5,
        "rate_mode": "PERCENT",
        "rate": 0.0,
        "is_default": False,
        "description": "Two extra years. Set the rate in Masters.",
    },
]


def uplift_for(term: WarrantyTerm | None, unit_price: float, quantity: float) -> float:
    """What extending to this term adds to a line.

    A percentage applies to the line - price times quantity - because that
    is how cover is priced against what is being covered. A flat amount is
    per unit, for the same reason: two panels under extended cover cost
    twice what one does.
    """

    if term is None:
        return 0.0

    rate = float(term.rate or 0.0)

    if rate <= 0:
        return 0.0

    if str(term.rate_mode or "").upper() == "AMOUNT":
        return rate * float(quantity or 0.0)

    return float(unit_price or 0.0) * float(quantity or 0.0) * rate / 100.0


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

        if request.rate < 0:
            raise HTTPException(status_code=400, detail="A warranty rate cannot be negative.")

        term = WarrantyTerm(
            name=name,
            years=request.years,
            rate_mode=request.rate_mode,
            rate=request.rate,
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

        if request.rate is not None:
            if request.rate < 0:
                raise HTTPException(
                    status_code=400, detail="A warranty rate cannot be negative."
                )
            term.rate = request.rate

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

        for field in ("years", "rate_mode", "description", "is_active"):
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


def warranty_rates(db: Session) -> dict[str, dict]:
    """Every term by name, for costing lines without a query each.

    Read from the database on each save rather than cached for the life of
    the process: a rate somebody has just set in Masters must apply to the
    next proposal, not to the one after the next restart.
    """

    WarrantyTermService.seed_defaults(db)

    return {
        term.name: {
            "rate_mode": term.rate_mode,
            "rate": float(term.rate or 0.0),
            "is_default": term.is_default,
        }
        for term in db.query(WarrantyTerm).all()
    }


def uplift_from_rates(
    rates: dict[str, dict] | None,
    term_name: str | None,
    unit_price: float,
    quantity: float,
) -> float:
    """What the chosen term adds to a line, given the rates in force.

    A term the master does not hold adds nothing. That covers a document
    quoting a term somebody has since renamed, and it fails towards not
    charging for cover rather than towards charging for cover at a rate
    nobody can point at.
    """

    entry = (rates or {}).get(str(term_name or "").strip())

    if not entry:
        return 0.0

    rate = float(entry.get("rate") or 0.0)

    if rate <= 0:
        return 0.0

    if str(entry.get("rate_mode") or "").upper() == "AMOUNT":
        return rate * float(quantity or 0.0)

    return float(unit_price or 0.0) * float(quantity or 0.0) * rate / 100.0
