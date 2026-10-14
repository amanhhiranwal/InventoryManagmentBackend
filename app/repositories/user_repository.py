from uuid import UUID

from sqlalchemy import func, or_
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
    def get_all(
        db: Session,
        skip: int = 0,
        limit: int = 100,
        visible_ids: set[str] | None = None,
        search: str | None = None,
    ) -> dict:
        """None for visible_ids means every user (super admin).

        A search narrows the whole list before it is cut into pages, so
        typing a name finds the person wherever they are - searching the
        ten rows that happen to be on screen is not searching.
        """

        query = db.query(User)
        if visible_ids is not None:
            query = query.filter(User.id.in_([UUID(uid) for uid in visible_ids]))

        term = (search or "").strip()

        if term:
            # % and _ are wildcards to LIKE and ordinary characters to the
            # person typing them.
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"

            query = query.filter(
                or_(
                    User.first_name.ilike(like, escape="\\"),
                    User.last_name.ilike(like, escape="\\"),
                    User.email.ilike(like, escape="\\"),
                    User.employee_id.ilike(like, escape="\\"),
                    # "Neha Bansal" as it reads on screen. concat() treats a
                    # missing surname as empty rather than as null.
                    func.concat(User.first_name, " ", User.last_name).ilike(
                        like, escape="\\"
                    ),
                )
            )

        total = query.count()
        data = query.order_by(User.email.asc()).offset(skip).limit(limit).all()
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
