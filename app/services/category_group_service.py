from sqlalchemy.orm import Session
from app.models.category_group import CategoryGroup
from app.schemas.category_group import CreateCategoryGroupRequest
from fastapi import HTTPException
from uuid import UUID

class CategoryGroupService:
    @staticmethod
    def create(request: CreateCategoryGroupRequest, db: Session) -> CategoryGroup:
        code_upper = request.code.upper().strip()
        
        # Check duplicate code
        exists = db.query(CategoryGroup).filter(CategoryGroup.code == code_upper).first()
        if exists:
            raise HTTPException(status_code=400, detail=f"Category group code '{request.code}' already exists.")

        cg = CategoryGroup(
            name=request.name.strip(),
            code=code_upper
        )
        db.add(cg)
        db.commit()
        db.refresh(cg)
        return cg

    @staticmethod
    def get_all(db: Session) -> list[CategoryGroup]:
        groups = db.query(CategoryGroup).order_by(CategoryGroup.created_at.desc()).all()
        if not groups:
            initial = [
                {"name": "Software", "code": "SOFTWARE"},
                {"name": "Hardware", "code": "HARDWARE"},
                {"name": "Services", "code": "SERVICES"},
                {"name": "Support", "code": "SUPPORT"}
            ]
            for it in initial:
                cg = CategoryGroup(
                    name=it["name"],
                    code=it["code"]
                )
                db.add(cg)
            db.commit()
            groups = db.query(CategoryGroup).order_by(CategoryGroup.created_at.desc()).all()
        return groups

    @staticmethod
    def delete(cg_id: str, db: Session) -> None:
        try:
            uuid_obj = UUID(cg_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Category Group UUID format.")

        cg = db.query(CategoryGroup).filter(CategoryGroup.id == uuid_obj).first()
        if not cg:
            raise HTTPException(status_code=404, detail="Category Group not found.")
        
        db.delete(cg)
        db.commit()
