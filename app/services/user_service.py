from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.user import User
from app.repositories.rbac_repository import RBACRepository
from app.core.field_checks import check_email
from app.repositories.user_repository import UserRepository
from app.services.hierarchy_service import HierarchyService
from app.services import account_mail
from app.services.password_service import PasswordService
from app.utils.validators import validate_uuid


class UserService:

    @staticmethod
    def _assert_can_assign_roles(current_user: dict | None, role_ids: list[str], db: Session) -> None:
        """A non-admin may only hand out roles below their own."""

        if current_user is None or current_user.get("is_super_admin"):
            return

        below = HierarchyService.junior_role_ids(
            HierarchyService.user_role_ids(current_user.get("user_id"), db), db
        )
        outside = [r for r in role_ids if str(r) not in below]

        if outside:
            raise HTTPException(
                status_code=403,
                detail="You can only assign roles that are below your own in the hierarchy.",
            )

    @staticmethod
    def _assert_can_manage_user(current_user: dict | None, user: User, db: Session) -> None:
        if current_user is None or current_user.get("is_super_admin"):
            return

        if str(user.id) == str(current_user.get("user_id")):
            raise HTTPException(status_code=403, detail="You cannot change your own access.")

        if user.is_super_admin or not HierarchyService.can_see_user(current_user, user.id, db):
            raise HTTPException(
                status_code=403,
                detail="You can only manage users who are below you in the hierarchy.",
            )

    @staticmethod
    def reset_password(user_id: str, db: Session, current_user: dict) -> User:
        """Issue a fresh temporary password and email it to the person.

        The password is not returned. An administrator who can read it
        off their own screen will write it somewhere, or say it down a
        phone; the point of mailing it is that the account's owner is
        the one who learns it.
        """

        validate_uuid(user_id, "user_id")

        user = db.query(User).filter(User.id == UUID(user_id)).first()

        if user is None:
            raise HTTPException(status_code=404, detail="User not found.")

        if not HierarchyService.can_see_user(current_user, user.id, db):
            raise HTTPException(
                status_code=403,
                detail="You can only reset the password of somebody in your team.",
            )

        temporary = PasswordService.temporary_password()

        user.password = PasswordService.hash_password(temporary)
        user.must_change_password = True

        db.commit()
        db.refresh(user)

        if not account_mail.send_reset(user, temporary, db):
            # The password has already changed, so saying it went when it
            # did not would lock somebody out with nothing to sign in
            # with and no way to know why.
            raise HTTPException(
                status_code=502,
                detail=(
                    f"The password was reset but the email to {user.email} "
                    "could not be sent. Check the mail settings and reset "
                    "it again."
                ),
            )

        return user

    @staticmethod
    def send_welcome_email(user_id: str, db: Session, current_user: dict) -> User:
        """The super admin's one-time welcome email, with a fresh password.

        Once per person. The new temporary password replaces whatever
        they had and every earlier sign-in is ended, so the emailed one is
        the only way in. The claim is a conditional UPDATE, so two clicks
        at once send one email; and it is committed only once the email
        has gone, so a failed send changes nothing and can be retried.
        """

        from datetime import datetime

        from sqlalchemy import update

        validate_uuid(user_id, "user_id")

        user = db.query(User).filter(User.id == UUID(user_id)).first()

        if user is None:
            raise HTTPException(status_code=404, detail="User not found.")

        if str(user.id) == str(current_user.get("user_id")):
            raise HTTPException(
                status_code=400,
                detail="You cannot send the welcome email to yourself - it would sign you out.",
            )

        if not user.is_active:
            raise HTTPException(
                status_code=400,
                detail=f"{user.email} is switched off. Switch the account on before welcoming them.",
            )

        temporary = PasswordService.temporary_password()

        claimed = db.execute(
            update(User)
            .where(User.id == user.id, User.welcome_email_sent_at.is_(None))
            .values(
                password=PasswordService.hash_password(temporary),
                must_change_password=True,
                session_version=User.session_version + 1,
                welcome_email_sent_at=datetime.utcnow(),
            )
            .execution_options(synchronize_session=False)
        ).rowcount

        if not claimed:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"The welcome email has already been sent to {user.email}.",
            )

        db.refresh(user)

        if not account_mail.send_welcome(user, temporary, db):
            # Nothing is kept: their old password still works and the
            # button is still there to try again.
            db.rollback()
            raise HTTPException(
                status_code=502,
                detail=(
                    f"The welcome email to {user.email} could not be sent, so "
                    "nothing was changed. Check the mail settings and try again."
                ),
            )

        db.commit()
        db.refresh(user)

        UserService._copy_session_version(user)

        return user

    @staticmethod
    def _copy_session_version(user: User) -> None:
        """Carry the account's session number to every service's database.

        Each service checks a token against its own copy of the users
        table, so a sign-out has to reach all of them. A copy that cannot
        be reached is logged; the account is still signed out of this one.
        """

        import logging
        from urllib.parse import quote_plus

        from sqlalchemy import create_engine, text

        from app.core.config import settings

        logger = logging.getLogger(__name__)
        password = quote_plus(settings.POSTGRES_PASSWORD)

        for name in ("solutions", "auth_db", "crm_db", "inventory_db", "sales_db"):
            if name == settings.POSTGRES_DB:
                continue

            engine = create_engine(
                f"postgresql+psycopg2://{settings.POSTGRES_USER}:{password}"
                f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{name}"
            )

            try:
                with engine.begin() as conn:
                    conn.execute(
                        text("UPDATE users SET session_version = :v WHERE id = :id"),
                        {"v": user.session_version, "id": str(user.id)},
                    )
            except Exception:  # noqa: BLE001 - the others still need it
                logger.exception("Could not sign %s out of %s", user.email, name)
            finally:
                engine.dispose()

    @staticmethod
    def create(request, db: Session, current_user: dict | None = None) -> User:
        # Check if email is already taken
        email_exists = UserRepository.get_by_email(db, request.email)
        if email_exists:
            raise HTTPException(
                status_code=400,
                detail="User email already exists.",
            )

        # Check if employee_id is already taken
        employee_exists = UserRepository.get_by_employee_id(db, request.employee_id)
        if employee_exists:
            raise HTTPException(
                status_code=400,
                detail="Employee ID already exists.",
            )

        # Validate roles
        roles_list = []
        for r_id in request.role_ids:
            validate_uuid(r_id, "role_id")
            role = RBACRepository.get_role_by_id(db, r_id)
            if role is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Role '{r_id}' not found.",
                )
            roles_list.append(role)

        # Validate companies
        companies_list = []
        for c_id in request.company_ids:
            validate_uuid(c_id, "company_id")
            company = db.query(Company).filter(Company.id == UUID(c_id)).first()
            if company is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Company '{c_id}' not found.",
                )
            companies_list.append(company)

        UserService._assert_can_assign_roles(current_user, request.role_ids, db)

        reports_to = HierarchyService.validate_reports_to(
            None, getattr(request, "reports_to_id", None), request.role_ids, db
        )

        # A manager creating a user puts them in their own team unless they
        # chose someone else in it.
        if (
            reports_to is None
            and current_user is not None
            and not current_user.get("is_super_admin")
        ):
            reports_to = UUID(current_user["user_id"])

        # Whatever it starts as, the account cannot be worked under
        # until the person has chosen their own - so a blank field is not
        # an omission to refuse, it is the ordinary case.
        temporary = getattr(request, "password", None) or ""

        if temporary.strip():
            # One typed here is emailed and then signed in with, so it is
            # held to the same rules as one chosen on the way in.
            PasswordService.assert_strong(temporary)
        else:
            temporary = PasswordService.temporary_password()

        hashed_password = PasswordService.hash_password(temporary)

        user = User(
            first_name=request.first_name,
            last_name=request.last_name,
            email=request.email,
            password=hashed_password,
            phone_number=request.phone_number,
            employee_id=request.employee_id,
            location=getattr(request, "location", None),
            is_super_admin=False,
            is_active=True,
            must_change_password=True,
            reports_to_id=reports_to,
        )

        user.roles = roles_list
        user.companies = companies_list

        created_user = UserRepository.create(db, user)

        # The only place that password is ever written down. It is not in
        # the response and not on the screen that asked for the account,
        # so the person it belongs to is the one who learns it.
        account_mail.send_welcome(created_user, temporary, db)
        
        # Mirror user record across all microservice databases
        try:
            from urllib.parse import quote_plus

            from sqlalchemy import create_engine, text

            from app.core.config import settings
            db_names = ["solutions", "crm_db", "inventory_db", "sales_db"]
            encoded_pw = quote_plus(settings.POSTGRES_PASSWORD)
            for target_db in db_names:
                try:
                    db_url = f"postgresql+psycopg2://{settings.POSTGRES_USER}:{encoded_pw}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{target_db}"
                    t_engine = create_engine(db_url)
                    with t_engine.connect() as conn:
                        exists = conn.execute(text(f"SELECT id FROM users WHERE id = '{created_user.id}';")).fetchone()
                        if not exists:
                            conn.execute(text("""
                                INSERT INTO users (id, first_name, last_name, email, password, phone_number, employee_id, is_super_admin, is_active, created_at, updated_at)
                                VALUES (:id, :first_name, :last_name, :email, :password, :phone_number, :employee_id, :is_super_admin, :is_active, NOW(), NOW())
                            """), {
                                "id": str(created_user.id),
                                "first_name": created_user.first_name,
                                "last_name": created_user.last_name,
                                "email": created_user.email,
                                "password": created_user.password,
                                "phone_number": created_user.phone_number,
                                "employee_id": created_user.employee_id,
                                "is_super_admin": created_user.is_super_admin,
                                "is_active": created_user.is_active,
                            })
                            conn.commit()
                except Exception as inner_e:
                    print(f"User sync error to {target_db}:", inner_e)
        except Exception as e:
            print("User replication error:", e)

        return created_user

    @staticmethod
    def get_all(
        db: Session,
        skip: int = 0,
        limit: int = 100,
        current_user: dict | None = None,
        search: str | None = None,
    ) -> dict:
        visible = (
            HierarchyService.visible_user_ids(current_user, db)
            if current_user is not None
            else None
        )
        return UserRepository.get_all(db, skip, limit, visible, search)

    @staticmethod
    def update_role(user_id: str, role_ids: list[str], company_ids: list[str], db: Session, current_user: dict | None = None) -> User:
        validate_uuid(user_id, "user_id")

        user = UserRepository.get_by_id(db, UUID(user_id))
        if user is None:
            raise HTTPException(
                status_code=404,
                detail="User not found.",
            )

        UserService._assert_can_manage_user(current_user, user, db)
        UserService._assert_can_assign_roles(current_user, role_ids, db)

        # Validate roles
        roles_list = []
        for r_id in role_ids:
            validate_uuid(r_id, "role_id")
            role = RBACRepository.get_role_by_id(db, r_id)
            if role is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Role '{r_id}' not found.",
                )
            roles_list.append(role)

        # Validate companies
        companies_list = []
        for c_id in company_ids:
            validate_uuid(c_id, "company_id")
            company = db.query(Company).filter(Company.id == UUID(c_id)).first()
            if company is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Company '{c_id}' not found.",
                )
            companies_list.append(company)

        user.roles = roles_list
        user.companies = companies_list
        return UserRepository.update(db, user)

    @staticmethod
    def set_active(user_id: str, is_active: bool, db: Session, current_user=None):
        """Switch an account on or off.

        Off means off: the password stops working and any token already
        issued stops working with it, so somebody who has left cannot
        carry on until their session happens to expire.

        Kept separate from delete, because a person who has left still
        owns the leads they raised and the approvals they granted, and
        those have to keep pointing at somebody.
        """

        from app.models.user import User

        user = db.query(User).filter(User.id == UUID(str(user_id))).first()

        if user is None:
            raise HTTPException(status_code=404, detail="User not found.")

        # Locking out the last way in is not a mistake worth allowing.
        if not is_active and user.is_super_admin:
            others = (
                db.query(User)
                .filter(
                    User.is_super_admin.is_(True),
                    User.is_active.is_(True),
                    User.id != user.id,
                )
                .count()
            )

            if others == 0:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "This is the last active super admin. Give somebody "
                        "else that role before switching this one off."
                    ),
                )

        if current_user and str(current_user.get("user_id")) == str(user.id) and not is_active:
            raise HTTPException(
                status_code=400,
                detail="You cannot switch off your own account.",
            )

        user.is_active = is_active
        db.commit()
        db.refresh(user)

        return user

    @staticmethod
    def delete(user_id: str, db: Session, current_user: dict | None = None) -> None:
        validate_uuid(user_id, "user_id")
        user = UserRepository.get_by_id(db, UUID(user_id))
        if user is None:
            raise HTTPException(
                status_code=404,
                detail="User not found.",
            )
        if user.is_super_admin:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete Super Admin.",
            )
        UserService._assert_can_manage_user(current_user, user, db)

        # Their team moves up to their own manager rather than being left
        # without one.
        for report in db.query(User).filter(User.reports_to_id == user.id).all():
            report.reports_to_id = user.reports_to_id

        UserRepository.delete(db, user)

    @staticmethod
    def update(user_id: str, request, db: Session, current_user: dict | None = None) -> User:
        validate_uuid(user_id, "user_id")
        user = UserRepository.get_by_id(db, UUID(user_id))
        if user is None:
            raise HTTPException(
                status_code=404,
                detail="User not found.",
            )
        UserService._assert_can_manage_user(current_user, user, db)
        UserService._assert_can_assign_roles(current_user, request.role_ids, db)

        if request.employee_id != user.employee_id:
            emp_exists = UserRepository.get_by_employee_id(db, request.employee_id)
            if emp_exists:
                raise HTTPException(
                    status_code=400,
                    detail="Employee ID already exists.",
                )

        # The address somebody signs in with. Held to the same shape as
        # on the way in, and refused if it already belongs to another
        # account - two people cannot share a login.
        new_email = (request.email or "").strip()

        if new_email and new_email.lower() != (user.email or "").lower():
            check_email(new_email, required=True)

            taken = UserRepository.get_by_email(db, new_email)

            if taken and str(taken.id) != str(user.id):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{new_email} is already the login for "
                        f"{taken.first_name} {taken.last_name}."
                    ),
                )

            user.email = new_email
        roles_list = []
        for r_id in request.role_ids:
            validate_uuid(r_id, "role_id")
            role = RBACRepository.get_role_by_id(db, r_id)
            if role is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Role '{r_id}' not found.",
                )
            roles_list.append(role)
        companies_list = []
        for c_id in request.company_ids:
            validate_uuid(c_id, "company_id")
            company = db.query(Company).filter(Company.id == UUID(c_id)).first()
            if company is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Company '{c_id}' not found.",
                )
            companies_list.append(company)
        user.first_name = request.first_name
        user.last_name = request.last_name
        user.phone_number = request.phone_number
        user.employee_id = request.employee_id

        # Left alone when the form does not send it, so a caller updating
        # only roles cannot blank where somebody works.
        if getattr(request, "location", None) is not None:
            user.location = request.location
        # Only touched when the form sends it, so an edit from a screen that
        # does not show Reports To leaves the manager as it was.
        if "reports_to_id" in request.model_fields_set:
            user.reports_to_id = HierarchyService.validate_reports_to(
                str(user.id), request.reports_to_id, request.role_ids, db
            )

        user.roles = roles_list
        user.companies = companies_list
        return UserRepository.update(db, user)
