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
    def seed_default_customer_types(db: Session):
        if db.query(CustomerType).count() > 0:
            return
        defaults = [
            {"name": "Distributor", "code": "DISTRIBUTOR", "description": "Wholesale distribution partner"},
            {"name": "Retailer", "code": "RETAILER", "description": "Retail outlet / shop owner"},
            {"name": "Enterprise", "code": "ENTERPRISE", "description": "Large corporate enterprise client"},
            {"name": "OEM Client", "code": "OEM", "description": "Original equipment manufacturer"},
            {"name": "Direct Customer", "code": "DIRECT", "description": "End consumer / direct customer"},
        ]
        for d in defaults:
            db.add(CustomerType(name=d["name"], code=d["code"], description=d["description"]))
        db.commit()

    @staticmethod
    def get_all(db: Session) -> list[CustomerType]:
        CustomerTypeService.seed_default_customer_types(db)
        return db.query(CustomerType).order_by(CustomerType.created_at.desc()).all()

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
