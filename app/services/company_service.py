import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.company import Company
from app.repositories.company_repository import CompanyRepository

logger = logging.getLogger(__name__)


class CompanyService:

    @staticmethod
    def create(request, db: Session):

        email_exists = CompanyRepository.get_by_email(
            db,
            request.email,
        )

        if email_exists:
            raise HTTPException(
                status_code=400,
                detail="Company email already exists.",
            )

        code_exists = CompanyRepository.get_by_company_code(
            db,
            request.company_code,
        )

        if code_exists:
            raise HTTPException(
                status_code=400,
                detail="Company code already exists.",
            )

        company = Company(
            company_name=request.company_name,
            company_code=request.company_code,
            email=request.email,
            phone_number=request.phone_number,
            website=request.website,
            gst_number=request.gst_number,
            address_line_1=request.address_line_1,
            address_line_2=request.address_line_2,
            city=request.city,
            state=request.state,
            country=request.country,
            postal_code=request.postal_code,
            is_active=request.is_active,
        )

        company = CompanyRepository.create(
            db,
            company,
        )

        # Each company is one place: its store is set up with it, so stock
        # can be received and sent from day one without choosing a warehouse.
        from app.services.warehouse_service import WarehouseService

        # The company is already saved; a store that cannot be set up is
        # reported, not allowed to fail the company it belongs to.
        try:
            WarehouseService.ensure_company_store(db, company)
            db.commit()
        except Exception:  # noqa: BLE001 - logged below, and the store can be added under Locations
            db.rollback()
            logger.exception(
                "Company %s was created but its store could not be set up; add one under Masters > Locations.",
                company.company_code,
            )
        db.refresh(company)

        if getattr(request, "user_id", None):
            from uuid import UUID

            from app.repositories.user_repository import UserRepository
            from app.utils.validators import validate_uuid
            validate_uuid(request.user_id, "user_id")
            user = UserRepository.get_by_id(db, UUID(request.user_id))
            if user:
                user.companies.append(company)
                db.commit()

        return company

    @staticmethod
    def get_all(db: Session, skip: int = 0, limit: int = 100):

        return CompanyRepository.get_all(db, skip, limit)

    @staticmethod
    def get_by_id(company_id, db: Session):

        company = CompanyRepository.get_by_id(
            db,
            company_id,
        )

        if company is None:
            raise HTTPException(
                status_code=404,
                detail="Company not found.",
            )

        return company

    @staticmethod
    def update(company_id, request, db: Session):

        company = CompanyRepository.get_by_id(
            db,
            company_id,
        )

        if company is None:
            raise HTTPException(
                status_code=404,
                detail="Company not found.",
            )

        update_data = request.model_dump(exclude_unset=True)

        for key, value in update_data.items():
            setattr(company, key, value)

        return CompanyRepository.update(
            db,
            company,
        )

    @staticmethod
    def delete(company_id, db: Session):

        company = CompanyRepository.get_by_id(
            db,
            company_id,
        )

        if company is None:
            raise HTTPException(
                status_code=404,
                detail="Company not found.",
            )

        # A company that has held stock keeps its ledger; deleting it would
        # leave stock history pointing at nothing.
        from app.models.stock import StockDocument, StockLedger

        if (
            db.query(StockLedger.id).filter(StockLedger.company_id == company.id).first()
            or db.query(StockDocument.id).filter(StockDocument.company_id == company.id).first()
        ):
            raise HTTPException(
                status_code=400,
                detail=f"{company.company_name} has stock history, so it cannot be deleted.",
            )

        CompanyRepository.delete(
            db,
            company,
        )

        return {
            "success": True,
            "message": "Company deleted successfully.",
        }