from fastapi import APIRouter, Depends

from app.middleware.permission_middleware import require_permission

router = APIRouter(
    prefix="/users",
    tags=["Users"],
)


@router.get("/")
def get_users(

    current_user=Depends(
        require_permission("user.read")
    ),
):
    return {
        "success": True,
        "message": "Permission Verified",
        "loggedInUser": current_user.email,

    }