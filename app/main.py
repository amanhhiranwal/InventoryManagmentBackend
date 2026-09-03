from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from app.core.config import settings
from app.routes.api import api_router
from app.utils.exception_handler import register_exception_handlers

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
)

# -----------------------------
# CORS Configuration
# -----------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# Exception Handlers
# -----------------------------
register_exception_handlers(app)

# -----------------------------
# Static Media Mount
# -----------------------------
os.makedirs("uploads/avatars", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# -----------------------------
# API Routes
# -----------------------------
app.include_router(api_router)

# -----------------------------
# Root Health Check
# -----------------------------
@app.get("/", tags=["Root"])
async def root():
    return {
        "success": True,
        "message": "Enterprise SaaS Backend",
        "version": settings.APP_VERSION,
    }
