"""Which companies' records a user may see.

Every user is assigned companies on their profile (``user_companies``), and
a product belongs to one company. So the same product can be stocked by two
companies and each side only ever sees its own copy - which is what lets one
installation run several companies side by side.

A product with no company is shared: it shows for everyone. Products created
before companies were tracked have none, so nothing disappears, and a super
admin can keep listing genuinely shared items that way.

A super admin sees every company.

Stores (warehouses) follow companies: a person works in the stores of the
companies on their profile. A store given to particular roles (Roles &
Access) is kept for those roles; a store given to no role is open to
everyone of its company. Users > Warehouse Access gives a person a kept
store as well, and takes nothing from anyone else.
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

    # ------------------------------------------------------------------
    # Products shown on the Inventory list (Roles & Access, per role)
    # ------------------------------------------------------------------
    OWN_COMPANIES = "OWN_COMPANIES"
    ALL_COMPANIES = "ALL_COMPANIES"
    STORE_STOCK = "STORE_STOCK"
    PRODUCT_SCOPES = (OWN_COMPANIES, ALL_COMPANIES, STORE_STOCK)

    @staticmethod
    def product_scope(current_user: dict, db: Session) -> str:
        """The widest "Products shown" among the caller's roles.

        A role with no setting counts as OWN_COMPANIES, so STORE_STOCK
        applies only when every role of the person is set to it.
        """

        if current_user.get("is_super_admin"):
            return CompanyScopeService.ALL_COMPANIES

        from app.models.user_role import UserRole
        from app.models.warehouse import RoleProductScope

        user_id = current_user.get("user_id")
        if not user_id:
            return CompanyScopeService.OWN_COMPANIES

        roles = [r for (r,) in db.query(UserRole.role_id).filter(UserRole.user_id == _as_uuid(user_id))]
        if not roles:
            return CompanyScopeService.OWN_COMPANIES

        set_to = {
            str(role): scope
            for role, scope in db.query(RoleProductScope.role_id, RoleProductScope.scope).filter(
                RoleProductScope.role_id.in_(roles)
            )
        }
        scopes = {set_to.get(str(role), CompanyScopeService.OWN_COMPANIES) for role in roles}

        if CompanyScopeService.ALL_COMPANIES in scopes:
            return CompanyScopeService.ALL_COMPANIES
        if CompanyScopeService.OWN_COMPANIES in scopes:
            return CompanyScopeService.OWN_COMPANIES
        return CompanyScopeService.STORE_STOCK

    @staticmethod
    def product_list_filter(current_user: dict, db: Session, company_id: str | None = None) -> dict:
        """The Mongo filter for the Inventory list, by the caller's "Products shown".

        Only for reading the list: anything that changes a product still
        goes through mongo_filter / assert_company_allowed.
        """

        scope = CompanyScopeService.product_scope(current_user, db)

        if scope == CompanyScopeService.ALL_COMPANIES:
            if company_id:
                _as_uuid(company_id)
                return {"company_id": str(company_id)}
            return {}

        base = CompanyScopeService.mongo_filter(current_user, db, company_id)

        if scope == CompanyScopeService.OWN_COMPANIES:
            return base

        # STORE_STOCK: the products with stock in the stores the person
        # works in - a serialised unit by its serial, the rest by model.
        import re

        from app.core.stock import SERIAL_IN_STOCK
        from app.models.stock import StockBalance, StockSerial

        stores = CompanyScopeService.visible_warehouse_ids(current_user, db) or []
        store_ids = [_as_uuid(s) for s in stores]

        serials: list[str] = []
        skus: list[str] = []
        tracked: list[str] = []

        if store_ids:
            serials = [
                s for (s,) in db.query(StockSerial.serial_no)
                .filter(StockSerial.warehouse_id.in_(store_ids), StockSerial.status == SERIAL_IN_STOCK)
            ]
            skus = [
                s for (s,) in db.query(StockBalance.sku).filter(
                    StockBalance.warehouse_id.in_(store_ids), StockBalance.qty_on_hand > 0
                ).distinct()
            ]
            # Serials the ledger tracks for those models, wherever they are:
            # a record carrying one of them is shown only by its own serial.
            if skus:
                tracked = [s for (s,) in db.query(StockSerial.serial_no).filter(StockSerial.sku.in_(skus))]

        held = {
            "$or": [
                {"serial_number": {"$in": serials}},
                {
                    "serial_number": {"$nin": tracked},
                    "attributes.model_number": {
                        "$in": [re.compile(f"^{re.escape(s)}$", re.IGNORECASE) for s in skus]
                    },
                },
            ]
        }

        return {"$and": [base, held]} if base else held

    # ------------------------------------------------------------------
    # Warehouses
    # ------------------------------------------------------------------
    @staticmethod
    def visible_warehouse_ids(current_user: dict, db: Session) -> list[str] | None:
        """Warehouses the caller may work in. ``None`` means every one.

        A grant counts only while the warehouse's company is still on the
        caller's profile, so taking a company away takes its warehouses
        with it even before anybody tidies the grants up.
        """

        if current_user.get("is_super_admin"):
            return None

        from app.core.warehouse import OFFICE
        from app.models.location import Location
        from app.models.warehouse import UserWarehouse

        user_id = current_user.get("user_id")
        if not user_id:
            return []

        companies = CompanyScopeService.assigned_company_ids(user_id, db)
        if not companies:
            return []

        company_ids = [_as_uuid(c) for c in companies]
        is_warehouse = (Location.location_type.is_(None)) | (Location.location_type != OFFICE)
        stores = [str(r.id) for r in db.query(Location.id).filter(Location.company_id.in_(company_ids), is_warehouse).all()]
        if not stores:
            return []

        # A store given to some roles (Roles & Access) is kept for them; a
        # store given to no role is open to its company. A store given to a
        # person (Users > Warehouse Access) adds it for them and takes
        # nothing from anyone else.
        from app.models.user_role import UserRole
        from app.models.warehouse import RoleWarehouse

        store_ids = [_as_uuid(s) for s in stores]
        kept = {
            str(w) for (w,) in db.query(RoleWarehouse.warehouse_id).filter(RoleWarehouse.warehouse_id.in_(store_ids))
        }
        roles = [r for (r,) in db.query(UserRole.role_id).filter(UserRole.user_id == _as_uuid(user_id))]
        given = {
            str(w) for (w,) in db.query(UserWarehouse.warehouse_id).filter(UserWarehouse.user_id == _as_uuid(user_id))
        } | ({
            str(w) for (w,) in db.query(RoleWarehouse.warehouse_id).filter(RoleWarehouse.role_id.in_(roles))
        } if roles else set())
        return [s for s in stores if s not in kept or s in given]

    @staticmethod
    def assert_warehouse_allowed(current_user: dict, warehouse_id, db: Session):
        """The warehouse, if the caller may work in it.

        A warehouse the caller may not see is reported as not found, the
        same as one that does not exist, so ids cannot be probed.
        """

        from app.core.warehouse import WAREHOUSE, location_type
        from app.models.location import Location

        try:
            key = UUID(str(warehouse_id))
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(status_code=404, detail="Warehouse not found.")

        warehouse = db.query(Location).filter(Location.id == key).first()

        if warehouse is None or location_type(warehouse) != WAREHOUSE:
            raise HTTPException(status_code=404, detail="Warehouse not found.")

        allowed = CompanyScopeService.visible_warehouse_ids(current_user, db)

        if allowed is not None and str(warehouse.id) not in allowed:
            raise HTTPException(status_code=404, detail="Warehouse not found.")

        return warehouse
