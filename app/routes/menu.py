from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.schemas.menu import CreateMenuItemRequest, UpdateMenuItemRequest
from app.services.menu_service import MenuService
from app.repositories.rbac_repository import RBACRepository

router = APIRouter(
    prefix="/menus",
    tags=["Menus"],
)

@router.get("/")
def get_menu_tree(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    tree = MenuService.get_menu_tree(db)
    return {
        "success": True,
        "data": tree
    }

@router.get("/sidebar")
def get_user_sidebar(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user_id = current_user["user_id"]
    is_super_admin = current_user.get("is_super_admin", False)
    
    user = RBACRepository.get_user(db, user_id)
    user_perms = set()
    if user:
        for role in user.roles:
            role_perms = RBACRepository.get_role_permissions(db, role.id)
            for p in role_perms:
                user_perms.add(p.permission_name)

    sidebar_tree = MenuService.get_user_sidebar(user_perms, is_super_admin, db)
    return {
        "success": True,
        "data": sidebar_tree
    }

@router.post("/")
def create_menu_item(
    request: CreateMenuItemRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    item = MenuService.create_menu_item(request, db)
    return {
        "success": True,
        "data": {
            "id": str(item.id),
            "title": item.title,
            "path": item.path,
            "permission_key": item.permission_key,
        }
    }

@router.put("/{menu_id}")
def update_menu_item(
    menu_id: str,
    request: UpdateMenuItemRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    item = MenuService.update_menu_item(menu_id, request, db)
    if not item:
        raise HTTPException(status_code=404, detail="Menu item not found")
    return {
        "success": True,
        "data": {
            "id": str(item.id),
            "title": item.title,
            "path": item.path,
            "is_active": item.is_active,
        }
    }

@router.delete("/{menu_id}")
def delete_menu_item(
    menu_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    success = MenuService.delete_menu_item(menu_id, db)
    if not success:
        raise HTTPException(status_code=404, detail="Menu item not found")
    return {
        "success": True,
        "message": "Menu item deleted successfully."
    }
