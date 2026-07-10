from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.location import Location
from app.repositories.company_repository import CompanyRepository
from app.repositories.location_repository import LocationRepository


class LocationService:

    @staticmethod
    def create(request, db: Session):

        company = CompanyRepository.get_by_id(
            db,
            request.company_id,
        )

        if company is None:
            raise HTTPException(
                status_code=404,
                detail="Company not found.",
            )

        if request.email:

            email_exists = LocationRepository.get_by_email(
                db,
                request.email,
            )

            if email_exists:
                raise HTTPException(
                    status_code=400,
                    detail="Location email already exists.",
                )

        code_exists = LocationRepository.get_by_location_code(
            db,
            request.location_code,
        )

        if code_exists:
            raise HTTPException(
                status_code=400,
                detail="Location code already exists.",
            )

        if request.is_default:

            default_location = LocationRepository.get_default_location(
                db,
                request.company_id,
            )

            if default_location:
                default_location.is_default = False
                LocationRepository.update(
                    db,
                    default_location,
                )

        location = Location(
            company_id=request.company_id,
            location_name=request.location_name,
            location_code=request.location_code,
            email=request.email,
            phone_number=request.phone_number,
            address_line_1=request.address_line_1,
            address_line_2=request.address_line_2,
            city=request.city,
            state=request.state,
            country=request.country,
            postal_code=request.postal_code,
            is_default=request.is_default,
        )

        return LocationRepository.create(
            db,
            location,
        )

    @staticmethod
    def get_all(db: Session):

        return LocationRepository.get_all(db)

    @staticmethod
    def get_by_id(location_id, db: Session):

        location = LocationRepository.get_by_id(
            db,
            location_id,
        )

        if location is None:
            raise HTTPException(
                status_code=404,
                detail="Location not found.",
            )

        return location

    @staticmethod
    def get_by_company(company_id, db: Session):

        return LocationRepository.get_by_company(
            db,
            company_id,
        )

    @staticmethod
    def update(location_id, request, db: Session):

        location = LocationRepository.get_by_id(
            db,
            location_id,
        )

        if location is None:
            raise HTTPException(
                status_code=404,
                detail="Location not found.",
            )

        update_data = request.model_dump(
            exclude_unset=True,
        )

        if (
            "location_code" in update_data
            and update_data["location_code"] != location.location_code
        ):

            exists = LocationRepository.get_by_location_code(
                db,
                update_data["location_code"],
            )

            if exists:
                raise HTTPException(
                    status_code=400,
                    detail="Location code already exists.",
                )

        if (
            "email" in update_data
            and update_data["email"] != location.email
        ):

            exists = LocationRepository.get_by_email(
                db,
                update_data["email"],
            )

            if exists:
                raise HTTPException(
                    status_code=400,
                    detail="Location email already exists.",
                )

        if update_data.get("is_default"):

            default_location = LocationRepository.get_default_location(
                db,
                location.company_id,
            )

            if (
                default_location
                and default_location.id != location.id
            ):
                default_location.is_default = False
                LocationRepository.update(
                    db,
                    default_location,
                )

        for key, value in update_data.items():
            setattr(
                location,
                key,
                value,
            )

        return LocationRepository.update(
            db,
            location,
        )

    @staticmethod
    def delete(location_id, db: Session):

        location = LocationRepository.get_by_id(
            db,
            location_id,
        )

        if location is None:
            raise HTTPException(
                status_code=404,
                detail="Location not found.",
            )

        LocationRepository.delete(
            db,
            location,
        )

        return {
            "success": True,
            "message": "Location deleted successfully.",
        }