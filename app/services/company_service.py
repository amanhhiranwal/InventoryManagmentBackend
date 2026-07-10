from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.company import Company
from app.repositories.company_repository import CompanyRepository


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

        return CompanyRepository.create(
            db,
            company,
        )

    @staticmethod
    def get_all(db: Session):

        return CompanyRepository.get_all(db)

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

        CompanyRepository.delete(
            db,
            company,
        )

        return {
            "success": True,
            "message": "Company deleted successfully.",
        }