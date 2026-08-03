from app.services.company_service import CompanyService


class CompanyController:

    @staticmethod
    def create(request, db):

        company = CompanyService.create(
            request,
            db,
        )

        return {
            "success": True,
            "message": "Company created successfully.",
            "data": company,
        }

    @staticmethod
    def get_all(db, skip: int = 0, limit: int = 100):

        result = CompanyService.get_all(db, skip, limit)

        return {
            "success": True,
            "data": result["data"],
            "total": result["total"],
        }

    @staticmethod
    def get_by_id(company_id, db):

        company = CompanyService.get_by_id(
            company_id,
            db,
        )

        return {
            "success": True,
            "data": company,
        }

    @staticmethod
    def update(company_id, request, db):

        company = CompanyService.update(
            company_id,
            request,
            db,
        )

        return {
            "success": True,
            "message": "Company updated successfully.",
            "data": company,
        }

    @staticmethod
    def delete(company_id, db):

        return CompanyService.delete(
            company_id,
            db,
        )