from uuid import UUID
from fastapi import HTTPException


def validate_uuid(value: str, field_name: str = "id") -> str:
    try:
        UUID(value)
        return value
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {field_name}. Must be a valid UUID."
        )