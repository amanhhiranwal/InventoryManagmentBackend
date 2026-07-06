from sqlalchemy.orm import Session

from app.models.user import User
from app.models.role import Role


class AuthRepository:

    @staticmethod
    def get_super_admin(db: Session):

        return (
            db.query(User)
            .filter(User.is_super_admin == True)
            .first()
        )

    @staticmethod
    def get_role_by_name(db: Session, role_name: str):

        
        return (
            db.query(Role)
            .filter(Role.role_name == role_name)
            .first()
        )

    @staticmethod
    def create_role(db: Session, role_name: str):

        role = Role(
            role_name=role_name,
            description="System Super Admin",
        )

        db.add(role)
        db.commit()
        db.refresh(role)

        return role

    @staticmethod
    def create_super_admin(db: Session, user: User):
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    
    @staticmethod
    def get_user_by_email(db, email: str):

        return (
        db.query(User)
        .filter(User.email == email)
        .first()
        )
    