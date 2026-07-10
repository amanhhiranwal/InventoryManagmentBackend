from uuid import UUID

from sqlalchemy.orm import Session

from app.models.location import Location


class LocationRepository:

    @staticmethod
    def create(
        db: Session,
        location: Location,
    ) -> Location:
        db.add(location)
        db.commit()
        db.refresh(location)
        return location

    @staticmethod
    def get_by_id(
        db: Session,
        location_id: UUID,
    ) -> Location | None:
        return (
            db.query(Location)
            .filter(Location.id == location_id)
            .first()
        )

    @staticmethod
    def get_by_company(
        db: Session,
        company_id: UUID,
    ) -> list[Location]:
        return (
            db.query(Location)
            .filter(Location.company_id == company_id)
            .order_by(Location.location_name.asc())
            .all()
        )

    @staticmethod
    def get_by_email(
        db: Session,
        email: str,
    ) -> Location | None:
        return (
            db.query(Location)
            .filter(Location.email == email)
            .first()
        )

    @staticmethod
    def get_by_location_code(
        db: Session,
        location_code: str,
    ) -> Location | None:
        return (
            db.query(Location)
            .filter(Location.location_code == location_code)
            .first()
        )

    @staticmethod
    def get_default_location(
        db: Session,
        company_id: UUID,
    ) -> Location | None:
        return (
            db.query(Location)
            .filter(
                Location.company_id == company_id,
                Location.is_default == True,
            )
            .first()
        )

    @staticmethod
    def get_all(
        db: Session,
    ) -> list[Location]:
        return (
            db.query(Location)
            .order_by(Location.location_name.asc())
            .all()
        )

    @staticmethod
    def update(
        db: Session,
        location: Location,
    ) -> Location:
        db.commit()
        db.refresh(location)
        return location

    @staticmethod
    def delete(
        db: Session,
        location: Location,
    ) -> None:
        db.delete(location)
        db.commit()