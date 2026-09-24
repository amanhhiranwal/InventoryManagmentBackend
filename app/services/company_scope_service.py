"""Which companies' records a user may see.

Every user is assigned companies on their profile (``user_companies``), and
a product belongs to one company. So the same product can be stocked by two
companies and each side only ever sees its own copy - which is what lets one
installation run several companies side by side.

A product with no company is shared: it shows for everyone. Products created
before companies were tracked have none, so nothing disappears, and a super
admin can keep listing genuinely shared items that way.

A super admin sees every company.
"""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.user_company import UserCompany


def _as_uuid(company_id) -> UUID:
    try:
        return UUID(str(company_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid company.")


class CompanyScopeService:

    @staticmethod
    def assigned_company_ids(user_id: str | None, db: Session) -> list[str]:
        """The companies on a user's profile, whoever they are."""

        if not user_id:
            return []

        rows = (
            db.query(UserCompany.company_id)
            .filter(UserCompany.user_id == _as_uuid(user_id))
            .all()
        )
        return [str(r.company_id) for r in rows]

    @staticmethod
    def visible_company_ids(current_user: dict, db: Session) -> list[str] | None:
        """Companies the caller works for. ``None`` means every company."""

        if current_user.get("is_super_admin"):
            return None

        return CompanyScopeService.assigned_company_ids(
            current_user.get("user_id"), db
        )

    @staticmethod
    def company_names(db: Session) -> dict[str, str]:
        return {
            str(c.id): c.company_name
            for c in db.query(Company.id, Company.company_name).all()
        }

    @staticmethod
    def resolve_owner(
        current_user: dict,
        company_id: str | None,
        db: Session,
    ) -> str | None:
        """The company a product being saved belongs to.

        A super admin may leave it out, which shares the product with every
        company. Everyone else has to own the choice: with one company on
        their profile it is filled in for them, with several they must say
        which, and with none there is nothing they could file it under.
        """

        if company_id:
            return CompanyScopeService.assert_company_allowed(current_user, company_id, db)

        if current_user.get("is_super_admin"):
            return None

        mine = CompanyScopeService.assigned_company_ids(current_user.get("user_id"), db)

        if len(mine) == 1:
            return mine[0]

        if not mine:
            raise HTTPException(
                status_code=400,
                detail="Your profile is not assigned to any company, so this product has nowhere to go. Ask a super admin to assign one.",
            )

        raise HTTPException(
            status_code=400,
            detail="Choose which company stocks this product.",
        )

    @staticmethod
    def assert_company_allowed(
        current_user: dict,
        company_id: str | None,
        db: Session,
    ) -> str | None:
        """Check a chosen company and return it as a string.

        ``None`` is allowed and means "shared with every company". Only a
        super admin may file a product under a company they are not in.
        """

        if not company_id:
            return None

        company = db.query(Company).filter(Company.id == _as_uuid(company_id)).first()
        if company is None:
            raise HTTPException(status_code=404, detail="Company not found.")

        allowed = CompanyScopeService.visible_company_ids(current_user, db)

        if allowed is not None and str(company.id) not in allowed:
            raise HTTPException(
                status_code=403,
                detail="You can only work with companies assigned to your profile.",
            )

        return str(company.id)

    @staticmethod
    def mongo_filter(current_user: dict, db: Session, company_id: str | None = None) -> dict:
        """The company half of a Mongo query for products.

        Callers merge it into their own query. An empty dict means no
        restriction at all.
        """

        allowed = CompanyScopeService.visible_company_ids(current_user, db)

        if company_id:
            # Asking for one company still has to be a company they are in.
            CompanyScopeService.assert_company_allowed(current_user, company_id, db)
            return {"company_id": str(company_id)}

        if allowed is None:
            return {}

        # Shared products (no company) stay visible alongside their own.
        return {"company_id": {"$in": [*allowed, None]}}
