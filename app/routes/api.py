from fastapi import APIRouter

from app.routes.auth import router as auth_router
from app.routes.user import router as user_router
from app.routes.role import router as role_router
from app.routes.rbac import router as rbac_router

api_router = APIRouter(
    prefix="/api/v1",
)


@api_router.get("/health")
async def health():

    return {
        "success": True,
        "message": "Running",
    }


api_router.include_router(role_router)
api_router.include_router(auth_router)
api_router.include_router(user_router)
api_router.include_router(rbac_router)