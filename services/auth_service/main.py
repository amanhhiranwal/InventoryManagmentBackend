from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from app.core.config import settings
from app.utils.exception_handler import register_exception_handlers

from app.routes.role import router as role_router
from app.routes.auth import router as auth_router
from app.routes.user import router as user_router
from app.routes.rbac import router as rbac_router
from app.routes.profile import router as profile_router
from app.routes.company import router as company_router

app = FastAPI(
    title=f"{settings.APP_NAME} - Auth Service",
    version=settings.APP_VERSION,
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register Custom Exception Handlers
register_exception_handlers(app)

# Static Media Mount for Profile Avatars
os.makedirs("uploads/avatars", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Include Auth & User Routes under /api/v1
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(role_router)
api_router.include_router(auth_router)
api_router.include_router(user_router)
api_router.include_router(rbac_router)
api_router.include_router(profile_router)
api_router.include_router(company_router)

app.include_router(api_router)

@app.get("/", tags=["Root"])
async def root():
    return {
        "success": True,
        "service": "Auth & User Service",
        "version": settings.APP_VERSION,
    }
