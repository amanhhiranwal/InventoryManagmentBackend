"""Bulk resolution of auth user ids to display names.

Lives here rather than on a route module because both the Lead routes and the
Opportunity controller need it, and importing it from the routes would close a
cycle: app/routes/lead.py already imports the opportunity controller.

Reads the local users table first and only falls back to the auth service over
HTTP, so the monolith does not pay for a network round trip it does not need.
"""

import os
from uuid import UUID

import requests
from sqlalchemy.orm import Session


def get_user_names_helper(
    user_ids: list[str],
    db: Session = None,
) -> dict[str, str]:
    if not user_ids:
        return {}

    if db is not None:
        try:
            from app.models.user import User

            user_uuids = [UUID(uid) for uid in user_ids if uid]
            users = db.query(User).filter(User.id.in_(user_uuids)).all()

            if users:
                return {
                    str(u.id): f"{u.first_name} {u.last_name}".strip()
                    for u in users
                }
        except Exception:
            pass

    try:
        auth_host = os.getenv("AUTH_SERVICE_HOST", "auth_service")
        auth_port = os.getenv("AUTH_SERVICE_PORT", "8001")

        response = requests.get(
            f"http://{auth_host}:{auth_port}/api/v1/users/names",
            params={"user_ids": user_ids},
            timeout=1,
        )

        if response.status_code == 200:
            return response.json().get("names", {})
    except Exception:
        pass

    return {}
