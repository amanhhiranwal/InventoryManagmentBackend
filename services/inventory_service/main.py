from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.utils.exception_handler import register_exception_handlers

from app.routes.inventory import router as inventory_router
from app.routes.product_type import router as product_type_router
from app.routes.category_group import router as category_group_router
from app.routes.unit import router as unit_router

app = FastAPI(
    title=f"{settings.APP_NAME} - Inventory Service",
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

# Include Inventory Routes under /api/v1
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(inventory_router)
api_router.include_router(product_type_router)
api_router.include_router(category_group_router)
api_router.include_router(unit_router)

app.include_router(api_router)

@app.get("/", tags=["Root"])
async def root():
    return {
        "success": True,
        "service": "Inventory Service",
        "version": settings.APP_VERSION,
    }
