from sqlalchemy.orm import Session
from app.models.product_type import ProductType
from app.schemas.product_type import CreateProductTypeRequest
from fastapi import HTTPException
from uuid import UUID

class ProductTypeService:
    @staticmethod
    def create(request: CreateProductTypeRequest, db: Session) -> ProductType:
        code_upper = request.code.upper().strip()
        
        # Check duplicate code
        exists = db.query(ProductType).filter(ProductType.code == code_upper).first()
        if exists:
            raise HTTPException(status_code=400, detail=f"Product type code '{request.code}' already exists.")

        pt = ProductType(
            name=request.name.strip(),
            code=code_upper,
            category=request.category.strip(),
            description=request.description.strip() if request.description else None
        )
        db.add(pt)
        db.commit()
        db.refresh(pt)
        return pt

    @staticmethod
    def get_all(db: Session) -> list[ProductType]:
        types = db.query(ProductType).order_by(ProductType.created_at.desc()).all()
        if not types:
            initial = [
                {"name": "SaaS Subscription", "code": "SAAS", "category": "Software", "description": "Monthly recurring cloud subscriptions"},
                {"name": "Hardware Terminals", "code": "HW", "category": "Hardware", "description": "Physical scanner and POS registers"},
                {"name": "On-site Deployment", "code": "SRV", "category": "Services", "description": "System setups and routing configurations"},
                {"name": "Technical Support SLAs", "code": "SLA", "category": "Support", "description": "Premium ticket priority assistance packages"},
                {"name": "Interactive Flat Panel", "code": "IFP", "category": "Hardware", "description": "Interactive classroom smartboards"},
                {"name": "Open Pluggable Spec", "code": "OPS", "category": "Hardware", "description": "Detachable media player slot modules"},
            ]
            for it in initial:
                pt = ProductType(
                    name=it["name"],
                    code=it["code"],
                    category=it["category"],
                    description=it["description"]
                )
                db.add(pt)
            db.commit()
            types = db.query(ProductType).order_by(ProductType.created_at.desc()).all()
        return types

    @staticmethod
    def update(pt_id: str, request: CreateProductTypeRequest, db: Session) -> ProductType:
        try:
            uuid_obj = UUID(pt_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Product Type UUID format.")

        pt = db.query(ProductType).filter(ProductType.id == uuid_obj).first()
        if not pt:
            raise HTTPException(status_code=404, detail="Product Type not found.")

        code_upper = request.code.upper().strip()
        
        # Check duplicate code excluding this product type
        exists = db.query(ProductType).filter(ProductType.code == code_upper, ProductType.id != uuid_obj).first()
        if exists:
            raise HTTPException(status_code=400, detail=f"Product type code '{request.code}' already exists.")

        pt.name = request.name.strip()
        pt.code = code_upper
        pt.category = request.category.strip()
        pt.description = request.description.strip() if request.description else None
        
        db.commit()
        db.refresh(pt)
        return pt

    @staticmethod
    def delete(pt_id: str, db: Session) -> None:
        try:
            uuid_obj = UUID(pt_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Product Type UUID format.")

        pt = db.query(ProductType).filter(ProductType.id == uuid_obj).first()
        if not pt:
            raise HTTPException(status_code=404, detail="Product Type not found.")
        
        db.delete(pt)
        db.commit()
