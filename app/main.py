from fastapi import FastAPI

from app.core.config import settings
from app.routes.api import api_router

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
)

app.include_router(api_router)


@app.get("/", tags=["Root"])
async def root():
    return {
        "success": True,
        "message": "Enterprise SaaS Backend",
        "version": settings.APP_VERSION,
    }