# from fastapi import FastAPI

# from app.core.config import settings
# from app.routes.api import api_router
# from app.utils.exception_handler import register_exception_handlers

# app = FastAPI(
#     title=settings.APP_NAME,
#     version=settings.APP_VERSION,
# )

# register_exception_handlers(app)
# app.include_router(api_router)


# @app.get("/", tags=["Root"])
# async def root():
#     return {
#         "success": True,
#         "message": "Enterprise SaaS Backend",
#         "version": settings.APP_VERSION,
#     }


from fastapi import FastAPI

from fastapi.middleware.cors import CORSMiddleware

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

origins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
]


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------
# Exception Handlers
# -----------------------------

register_exception_handlers(app)


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
