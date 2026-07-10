from sqlalchemy.orm import Session

from app.schemas.location import (
    LocationCreate,
    LocationUpdate,
)
from app.services.location_service import LocationService


class LocationController:

    @staticmethod
    def create(
        request: LocationCreate,
        db: Session,
    ):

        location = LocationService.create(
            request,
            db,
        )

        return {
            "success": True,
            "message": "Location created successfully.",
            "data": location,
        }

    @staticmethod
    def get_all(
        db: Session,
    ):

        locations = LocationService.get_all(
            db,
        )

        return {
            "success": True,
            "data": locations,
        }

    @staticmethod
    def get_by_id(
        location_id,
        db: Session,
    ):

        location = LocationService.get_by_id(
            location_id,
            db,
        )

        return {
            "success": True,
            "data": location,
        }

    @staticmethod
    def get_by_company(
        company_id,
        db: Session,
    ):

        locations = LocationService.get_by_company(
            company_id,
            db,
        )

        return {
            "success": True,
            "data": locations,
        }

    @staticmethod
    def update(
        location_id,
        request: LocationUpdate,
        db: Session,
    ):

        location = LocationService.update(
            location_id,
            request,
            db,
        )

        return {
            "success": True,
            "message": "Location updated successfully.",
            "data": location,
        }

    @staticmethod
    def delete(
        location_id,
        db: Session,
    ):

        return LocationService.delete(
            location_id,
            db,
        )