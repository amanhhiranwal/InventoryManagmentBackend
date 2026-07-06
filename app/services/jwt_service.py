# from datetime import datetime, timedelta, timezone

# from jose import jwt

# from app.core.config import settings


# class JWTService:

#     @staticmethod
#     def create_access_token(data: dict):

#         payload = data.copy()

#         expire = datetime.now(timezone.utc) + timedelta(
#             minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
#         )

#         payload.update(
#             {
#                 "exp": expire
#             }
#         )

#         return jwt.encode(
#             payload,
#             settings.JWT_SECRET_KEY,
#             algorithm=settings.JWT_ALGORITHM,
#         )


from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from app.core.config import settings


class JWTService:

    @staticmethod
    def create_access_token(data: dict):

        payload = data.copy()

        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

        payload["exp"] = expire

        return jwt.encode(
            payload,
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

    @staticmethod
    def verify_token(token: str):

        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
            )

            return payload

        except JWTError:
            return None