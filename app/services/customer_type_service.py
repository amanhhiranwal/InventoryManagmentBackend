from sqlalchemy.orm import Session
from app.models.customer_type import CustomerType
from app.schemas.customer_type import CreateCustomerTypeRequest
from fastapi import HTTPException
from uuid import UUID

class CustomerTypeService:
    @staticmethod
    def create(request: CreateCustomerTypeRequest, db: Session) -> CustomerType:
        code_upper = request.code.upper().strip()
        
        # Check duplicate code
        exists = db.query(CustomerType).filter(CustomerType.code == code_upper).first()
        if exists:
            raise HTTPException(status_code=400, detail=f"Customer type code '{request.code}' already exists.")

        ct = CustomerType(
            name=request.name.strip(),
            code=code_upper,
            description=request.description.strip() if request.description else None
        )
        db.add(ct)
        db.commit()
        db.refresh(ct)
        return ct

    @staticmethod
    def get_all(db: Session) -> list[CustomerType]:
        types = db.query(CustomerType).order_by(CustomerType.created_at.desc()).all()
        if not types:
            initial = [
                {"name": "Distributor", "code": "DIST", "description": "B2B bulk trade channels"},
                {"name": "Retailer", "code": "RET", "description": "Direct consumer trade POS outlets"},
                {"name": "Enterprise Customer", "code": "ENT", "description": "Large corporate entities and conglomerates"},
                {"name": "Individual Consumer", "code": "IND", "description": "Retail individual purchases"}
            ]
            for it in initial:
                ct = CustomerType(
                    name=it["name"],
                    code=it["code"],
                    description=it["description"]
                )
                db.add(ct)
            db.commit()
            types = db.query(CustomerType).order_by(CustomerType.created_at.desc()).all()
        return types

    @staticmethod
    def delete(ct_id: str, db: Session) -> None:
        try:
            uuid_obj = UUID(ct_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Customer Type UUID format.")

        ct = db.query(CustomerType).filter(CustomerType.id == uuid_obj).first()
        if not ct:
            raise HTTPException(status_code=404, detail="Customer Type not found.")
        
        db.delete(ct)
        db.commit()
