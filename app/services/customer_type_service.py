from sqlalchemy.orm import Session
from app.models.customer_type import CustomerType
from app.schemas.customer_type import CreateCustomerTypeRequest
from fastapi import HTTPException

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
        defaults = [
            {"id": 1, "name": "Distributor", "code": "DISTRIBUTOR", "description": "Wholesale distribution partner"},
            {"id": 2, "name": "OEM", "code": "OEM", "description": "Original equipment manufacturer"},
            {"id": 3, "name": "End Customer", "code": "END_CUSTOMER", "description": "Direct end user or consumer"},
            {"id": 4, "name": "Institution", "code": "INSTITUTION", "description": "Institutional organization or government client"},
            {"id": 5, "name": "Corporate", "code": "CORPORATE", "description": "Corporate enterprise client"},
        ]
        for d in defaults:
            exists = db.query(CustomerType).filter(CustomerType.code == d["code"]).first()
            if not exists:
                db.add(CustomerType(id=d["id"], name=d["name"], code=d["code"], description=d["description"]))
            else:
                exists.name = d["name"]
        db.commit()

    @staticmethod
    def get_all(db: Session) -> list[CustomerType]:
        CustomerTypeService.seed_default_customer_types(db)
        return db.query(CustomerType).order_by(CustomerType.id.asc()).all()

    @staticmethod
    def delete(ct_id: int, db: Session) -> None:
        ct = db.query(CustomerType).filter(CustomerType.id == ct_id).first()
        if not ct:
            raise HTTPException(status_code=404, detail="Customer Type not found.")
        
        db.delete(ct)
        db.commit()
