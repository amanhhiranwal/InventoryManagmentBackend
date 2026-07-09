from uuid import UUID

from sqlalchemy.orm import Session

from app.models.company import Company


class CompanyRepository:

    @staticmethod
    def create(
        db: Session,
        company: Company,
    ) -> Company:
        db.add(company)
        db.commit()
        db.refresh(company)
        return company

    @staticmethod
    def get_by_id(
        db: Session,
        company_id: UUID,
    ) -> Company | None:
        return (
            db.query(Company)
            .filter(Company.id == company_id)
            .first()
        )

    @staticmethod
    def get_by_email(
        db: Session,
        email: str,
    ) -> Company | None:
        return (
            db.query(Company)
            .filter(Company.email == email)
            .first()
        )

    @staticmethod
    def get_by_company_code(
        db: Session,
        company_code: str,
    ) -> Company | None:
        return (
            db.query(Company)
            .filter(Company.company_code == company_code)
            .first()
        )

    @staticmethod
    def get_all(
        db: Session,
    ) -> list[Company]:
        return (
            db.query(Company)
            .order_by(Company.company_name.asc())
            .all()
        )

    @staticmethod
    def update(
        db: Session,
        company: Company,
    ) -> Company:
        db.commit()
        db.refresh(company)
        return company

    @staticmethod
    def delete(
        db: Session,
        company: Company,
    ) -> None:
        db.delete(company)
        db.commit()