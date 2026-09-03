from sqlalchemy.orm import Session
from app.models.state import State
from app.schemas.state import CreateStateRequest
from fastapi import HTTPException

class StateService:
    @staticmethod
    def create(request: CreateStateRequest, db: Session) -> State:
        code_upper = request.code.upper().strip()
        
        exists = db.query(State).filter(State.code == code_upper).first()
        if exists:
            raise HTTPException(status_code=400, detail=f"State code '{request.code}' already exists.")

        st = State(
            name=request.name.strip(),
            code=code_upper,
            country=request.country.strip() if request.country else "India"
        )
        db.add(st)
        db.commit()
        db.refresh(st)
        return st

    @staticmethod
    def seed_default_states(db: Session):
        defaults = [
            {"id": 1, "name": "Andhra Pradesh", "code": "AP"},
            {"id": 2, "name": "Delhi", "code": "DL"},
            {"id": 3, "name": "Gujarat", "code": "GJ"},
            {"id": 4, "name": "Haryana", "code": "HR"},
            {"id": 5, "name": "Karnataka", "code": "KA"},
            {"id": 6, "name": "Kerala", "code": "KL"},
            {"id": 7, "name": "Maharashtra", "code": "MH"},
            {"id": 8, "name": "Punjab", "code": "PB"},
            {"id": 9, "name": "Rajasthan", "code": "RJ"},
            {"id": 10, "name": "Tamil Nadu", "code": "TN"},
            {"id": 11, "name": "Telangana", "code": "TG"},
            {"id": 12, "name": "Uttar Pradesh", "code": "UP"},
            {"id": 13, "name": "West Bengal", "code": "WB"},
        ]
        for d in defaults:
            exists = db.query(State).filter(State.code == d["code"]).first()
            if not exists:
                db.add(State(id=d["id"], name=d["name"], code=d["code"], country="India"))
            else:
                exists.name = d["name"]
        db.commit()

    @staticmethod
    def get_all(db: Session) -> list[State]:
        StateService.seed_default_states(db)
        return db.query(State).order_by(State.id.asc()).all()

    @staticmethod
    def delete(st_id: int, db: Session) -> None:
        st = db.query(State).filter(State.id == st_id).first()
        if not st:
            raise HTTPException(status_code=404, detail="State not found.")
        
        db.delete(st)
        db.commit()
