from uuid import UUID
from sqlalchemy.orm import Session
from app.models.user import User

class UserRepository:

    @staticmethod
    def get_by_id(db: Session, user_id: UUID) -> User | None:
        return db.query(User).filter(User.id == user_id).first()

    @staticmethod
    def get_by_email(db: Session, email: str) -> User | None:
        return db.query(User).filter(User.email == email).first()

    @staticmethod
    def get_by_employee_id(db: Session, employee_id: str) -> User | None:
        return db.query(User).filter(User.employee_id == employee_id).first()

    @staticmethod
    def get_all(db: Session, skip: int = 0, limit: int = 100) -> dict:
        total = db.query(User).count()
        data = db.query(User).order_by(User.email.asc()).offset(skip).limit(limit).all()
        return {"data": data, "total": total}

    @staticmethod
    def create(db: Session, user: User) -> User:
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def update(db: Session, user: User) -> User:
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def delete(db: Session, user: User) -> None:
        db.delete(user)
        db.commit()
