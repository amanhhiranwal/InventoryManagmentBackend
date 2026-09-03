from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.utils.exception_handler import register_exception_handlers

from app.routes.order import router as order_router
from app.routes.customer import router as customer_router
from app.routes.customer_type import router as customer_type_router
from app.routes.location import router as location_router
from app.routes.state import router as state_router

app = FastAPI(
    title=f"{settings.APP_NAME} - Sales Service",
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

# Include Sales & Customer Routes under /api/v1
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(order_router)
api_router.include_router(customer_router)
api_router.include_router(customer_type_router)
api_router.include_router(location_router)
api_router.include_router(state_router)

app.include_router(api_router)

@app.get("/", tags=["Root"])
async def root():
    return {
        "success": True,
        "service": "Sales & Customer Service",
        "version": settings.APP_VERSION,
    }
