from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.utils.exception_handler import register_exception_handlers

from app.routes.lead import router as lead_router
from app.routes.workflow import router as workflow_router
from app.routes.lead_source import router as lead_source_router

app = FastAPI(
    title=f"{settings.APP_NAME} - CRM Service",
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

# Include CRM & Lead Routes under /api/v1
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(lead_router)
api_router.include_router(workflow_router)
api_router.include_router(lead_source_router)

app.include_router(api_router)

@app.get("/", tags=["Root"])
async def root():
    return {
        "success": True,
        "service": "CRM & Lead Service",
        "version": settings.APP_VERSION,
    }
