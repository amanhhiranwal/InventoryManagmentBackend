"""Warehouses, their bins, and who may work in which warehouse.

A warehouse is a location of type WAREHOUSE. It belongs to one company,
which is what keeps two companies' stock apart even when both call a
warehouse "Noida". Every warehouse always has a store bin, a quality
check bin and a rejected-goods bin, because receiving, inspecting and
rejecting goods each need somewhere to put them; more bins (rack, shelf)
can be added.

Bins are switched off rather than deleted: once stock has been posted to
a bin, its history has to keep pointing at it.
"""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.warehouse import DEFAULT_BINS, WAREHOUSE, location_type
from app.models.location import Location
from app.models.user import User
from app.models.warehouse import UserWarehouse, WarehouseBin
from app.services.company_scope_service import CompanyScopeService


def _uuid(value, what: str) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid {what}.")


def bin_dict(row: WarehouseBin) -> dict:
    return {
        "id": str(row.id),
        "warehouse_id": str(row.warehouse_id),
        "code": row.code,
        "name": row.name,
        "bin_type": row.bin_type,
        "zone": row.zone,
        "rack": row.rack,
        "shelf": row.shelf,
        "is_active": row.is_active,
    }


def warehouse_dict(row: Location, company_names: dict[str, str]) -> dict:
    return {
        "id": str(row.id),
        "location_name": row.location_name,
        "location_code": row.location_code,
        "company_id": str(row.company_id),
        "company_name": company_names.get(str(row.company_id)),
        "city": row.city,
        "state": row.state,
        "is_default": row.is_default,
        "is_active": row.is_active,
    }


class WarehouseService:

    # ------------------------------------------------------------------
    @staticmethod
    def ensure_company_store(db: Session, company) -> Location:
        """The company's store: its one warehouse, created if it has none.

        Each company is one place (Synergy Global - Noida, ...), so stock
        needs no choosing of warehouses: it is the company's store. A
        company that already has a warehouse keeps it; the default one, or
        the first, is its store. Does not commit.
        """

        held = (
            db.query(Location)
            .filter(Location.company_id == company.id)
            .order_by(Location.is_default.desc(), Location.location_name.asc())
            .all()
        )
        store = next((w for w in held if location_type(w) == WAREHOUSE and w.is_active is not False), None)
        if store is not None:
            WarehouseService.ensure_default_bins(db, store)
            return store

        base = f"{(company.company_code or 'CO').upper()}-STORE"
        code, n = base, 1
        while db.query(Location.id).filter(Location.location_code == code).first():
            n += 1
            code = f"{base}-{n}"
        store = Location(
            company_id=company.id,
            location_name=f"{company.city or company.company_name} Store",
            location_code=code,
            address_line_1=getattr(company, "address_line_1", None),
            city=company.city,
            state=getattr(company, "state", None),
            country=getattr(company, "country", None),
            postal_code=getattr(company, "postal_code", None),
            is_default=not any(w.is_default for w in held),
            location_type=WAREHOUSE,
            is_active=True,
        )
        db.add(store)
        db.flush()
        WarehouseService.ensure_default_bins(db, store)
        return store

    # ------------------------------------------------------------------
    # Bins
    # ------------------------------------------------------------------
    @staticmethod
    def ensure_default_bins(db: Session, warehouse: Location) -> int:
        """Create whichever of the three standard bins the warehouse lacks.

        Does not commit; the caller's transaction carries it. Returns how
        many were added.
        """

        if location_type(warehouse) != WAREHOUSE:
            return 0

        held = {
            code
            for (code,) in db.query(WarehouseBin.code)
            .filter(WarehouseBin.warehouse_id == warehouse.id)
            .all()
        }

        added = 0
        for code, name, kind in DEFAULT_BINS:
            if code not in held:
                db.add(WarehouseBin(warehouse_id=warehouse.id, code=code, name=name, bin_type=kind))
                added += 1

        return added

    @staticmethod
    def _warehouse(location_id, db: Session) -> Location:
        warehouse = db.query(Location).filter(Location.id == _uuid(location_id, "warehouse")).first()

        if warehouse is None:
            raise HTTPException(status_code=404, detail="Location not found.")

        if location_type(warehouse) != WAREHOUSE:
            raise HTTPException(status_code=400, detail="Only a warehouse has bins. This location is an office.")

        return warehouse

    @staticmethod
    def list_bins(location_id, db: Session) -> list[dict]:
        warehouse = WarehouseService._warehouse(location_id, db)

        rows = (
            db.query(WarehouseBin)
            .filter(WarehouseBin.warehouse_id == warehouse.id)
            .order_by(WarehouseBin.code.asc())
            .all()
        )
        return [bin_dict(r) for r in rows]

    @staticmethod
    def create_bin(location_id, payload, db: Session) -> dict:
        warehouse = WarehouseService._warehouse(location_id, db)

        row = WarehouseBin(
            warehouse_id=warehouse.id,
            code=payload.code.upper(),
            name=payload.name,
            bin_type=payload.bin_type,
            zone=payload.zone,
            rack=payload.rack,
            shelf=payload.shelf,
            is_active=True,
        )
        db.add(row)

        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(
                status_code=400,
                detail=f"Bin code {payload.code.upper()} already exists in this warehouse.",
            )

        db.refresh(row)
        return bin_dict(row)

    @staticmethod
    def update_bin(location_id, bin_id, payload, db: Session) -> dict:
        warehouse = WarehouseService._warehouse(location_id, db)

        row = (
            db.query(WarehouseBin)
            .filter(
                WarehouseBin.id == _uuid(bin_id, "bin"),
                WarehouseBin.warehouse_id == warehouse.id,
            )
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Bin not found.")

        changes = payload.model_dump(exclude_unset=True)

        if changes.get("code"):
            changes["code"] = changes["code"].upper()

        kind_after = changes.get("bin_type", row.bin_type)
        active_after = changes.get("is_active", row.is_active)

        # Receiving, inspecting and rejecting each need a bin to put goods
        # in, so the last active bin of a kind cannot be switched off or
        # turned into another kind.
        if row.is_active and (not active_after or kind_after != row.bin_type):
            others = (
                db.query(WarehouseBin)
                .filter(
                    WarehouseBin.warehouse_id == warehouse.id,
                    WarehouseBin.bin_type == row.bin_type,
                    WarehouseBin.is_active.is_(True),
                    WarehouseBin.id != row.id,
                )
                .count()
            )
            if others == 0:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{row.code} is the only active {row.bin_type} bin in this warehouse. "
                        "Add another one first."
                    ),
                )

        for key, value in changes.items():
            setattr(row, key, value)

        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(
                status_code=400,
                detail=f"Bin code {changes.get('code')} already exists in this warehouse.",
            )

        db.refresh(row)
        return bin_dict(row)

    # ------------------------------------------------------------------
    # The caller's own warehouses
    # ------------------------------------------------------------------
    @staticmethod
    def my_warehouses(current_user: dict, db: Session, company_id: str | None = None) -> list[dict]:
        """Warehouses the caller may work in, for their pickers."""

        query = db.query(Location).filter(
            (Location.location_type.is_(None)) | (Location.location_type == WAREHOUSE),
            Location.is_active.is_(True),
        )

        if company_id:
            CompanyScopeService.assert_company_allowed(current_user, company_id, db)
            query = query.filter(Location.company_id == _uuid(company_id, "company"))

        allowed = CompanyScopeService.visible_warehouse_ids(current_user, db)
        if allowed is not None:
            if not allowed:
                return []
            query = query.filter(Location.id.in_([UUID(a) for a in allowed]))

        names = CompanyScopeService.company_names(db)
        return [
            warehouse_dict(row, names)
            for row in query.order_by(Location.location_name.asc()).all()
        ]

    # ------------------------------------------------------------------
    # Who works where
    # ------------------------------------------------------------------
    @staticmethod
    def warehouse_ids_of(user: User, db: Session) -> list[str]:
        return [
            str(w)
            for (w,) in db.query(UserWarehouse.warehouse_id)
            .filter(UserWarehouse.user_id == user.id)
            .all()
        ]

    @staticmethod
    def set_user_warehouses(
        user: User,
        warehouse_ids: list[str] | None,
        company_ids: list[str],
        current_user: dict,
        db: Session,
    ) -> None:
        """Replace a user's warehouse grants. Does not commit.

        ``warehouse_ids`` None keeps what they have, apart from warehouses
        of companies no longer on their profile, which always go. Each
        warehouse asked for must be a warehouse (not an office), belong to
        one of ``company_ids``, and be one the caller may work in.
        """

        companies = {str(_uuid(c, "company")) for c in company_ids}

        existing = {
            str(row.warehouse_id): row
            for row in db.query(UserWarehouse).filter(UserWarehouse.user_id == user.id).all()
        }

        if warehouse_ids is None:
            wanted = set(existing)
            # Grants whose company has just been taken away are dropped.
            for warehouse_id in list(wanted):
                location = db.query(Location).filter(Location.id == UUID(warehouse_id)).first()
                if location is None or str(location.company_id) not in companies:
                    wanted.discard(warehouse_id)
        else:
            wanted = set()
            caller_allowed = CompanyScopeService.visible_warehouse_ids(current_user, db)

            for raw in warehouse_ids:
                key = _uuid(raw, "warehouse")
                location = db.query(Location).filter(Location.id == key).first()

                if location is None or location_type(location) != WAREHOUSE:
                    raise HTTPException(status_code=404, detail="Warehouse not found.")

                # Only a new grant needs the caller to work there; keeping a
                # grant someone else gave is not granting anything.
                if (
                    caller_allowed is not None
                    and str(key) not in caller_allowed
                    and str(key) not in existing
                ):
                    raise HTTPException(
                        status_code=403,
                        detail="You can only grant warehouses you work in yourself.",
                    )

                if str(location.company_id) not in companies:
                    # A grant they already had goes with its company, as it
                    # does when warehouse_ids is left out; a new one is a
                    # mistake worth saying.
                    if str(key) in existing:
                        continue
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"{location.location_name} belongs to a company that is not on "
                            "this person's profile. Add the company first."
                        ),
                    )

                wanted.add(str(key))

        for warehouse_id, row in existing.items():
            if warehouse_id not in wanted:
                db.delete(row)

        for warehouse_id in wanted - set(existing):
            db.add(UserWarehouse(user_id=user.id, warehouse_id=UUID(warehouse_id)))


class RoleStoreService:
    """Which stores a role is given, from Roles & Access."""

    @staticmethod
    def _role(role_id, db: Session):
        from app.models.role import Role

        role = db.query(Role).filter(Role.id == _uuid(role_id, "role")).first()
        if role is None:
            raise HTTPException(status_code=404, detail="Role not found.")
        return role

    @staticmethod
    def list_for_role(role_id, db: Session) -> list[dict]:
        """Every store, saying whether this role is given it and whether it
        is kept for particular roles at all."""

        from app.models.warehouse import RoleWarehouse

        role = RoleStoreService._role(role_id, db)
        given = {str(w) for (w,) in db.query(RoleWarehouse.warehouse_id).filter(RoleWarehouse.role_id == role.id)}
        kept = {str(w) for (w,) in db.query(RoleWarehouse.warehouse_id)}
        names = CompanyScopeService.company_names(db)
        stores = [w for w in db.query(Location).all() if location_type(w) == WAREHOUSE]
        stores.sort(key=lambda w: (names.get(str(w.company_id), ""), w.location_name or ""))
        return [
            {
                "id": str(w.id),
                "location_name": w.location_name,
                "location_code": w.location_code,
                "company_id": str(w.company_id),
                "company_name": names.get(str(w.company_id)),
                "is_active": w.is_active,
                "given": str(w.id) in given,
                "kept": str(w.id) in kept,
            }
            for w in stores
        ]

    @staticmethod
    def set_for_role(role_id, warehouse_ids: list[str], db: Session) -> list[dict]:
        from app.models.warehouse import RoleWarehouse

        role = RoleStoreService._role(role_id, db)
        wanted = set()
        for raw in warehouse_ids:
            key = _uuid(raw, "store")
            store = db.query(Location).filter(Location.id == key).first()
            if store is None or location_type(store) != WAREHOUSE:
                raise HTTPException(status_code=404, detail="Store not found.")
            wanted.add(key)
        dropped = db.query(RoleWarehouse).filter(RoleWarehouse.role_id == role.id)
        if wanted:
            dropped = dropped.filter(RoleWarehouse.warehouse_id.notin_(wanted))
        dropped.delete(synchronize_session=False)
        have = {w for (w,) in db.query(RoleWarehouse.warehouse_id).filter(RoleWarehouse.role_id == role.id)}
        for key in wanted - have:
            db.add(RoleWarehouse(role_id=role.id, warehouse_id=key))
        db.commit()
        return RoleStoreService.list_for_role(role_id, db)
