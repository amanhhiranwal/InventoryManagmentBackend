from fastapi import APIRouter

from app.routes.auth import router as auth_router
from app.routes.user import router as user_router
from app.routes.role import router as role_router
from app.routes.rbac import router as rbac_router
from app.routes.company import router as company_router
from app.routes.location import router as location_router
from app.routes.workflow import router as workflow_router
from app.routes.lead import router as lead_router
from app.routes.inventory import router as inventory_router
from app.routes.product_type import router as product_type_router
from app.routes.category_group import router as category_group_router
from app.routes.customer_type import router as customer_type_router
from app.routes.profile import router as profile_router


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
api_router.include_router(company_router)
api_router.include_router(location_router)
api_router.include_router(workflow_router)
api_router.include_router(lead_router)
api_router.include_router(inventory_router)
api_router.include_router(product_type_router)
api_router.include_router(category_group_router)
api_router.include_router(customer_type_router)
api_router.include_router(profile_router)
