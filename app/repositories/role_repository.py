from sqlalchemy.orm import Session

from app.models.role import Role


class RoleRepository:

    @staticmethod
    def create(db: Session, role: Role):

        db.add(role)
        db.commit()
        db.refresh(role)

        return role

    @staticmethod
    def get_all(db: Session):

        return db.query(Role).all()
