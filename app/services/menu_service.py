from sqlalchemy.orm import Session
from sqlalchemy import asc
from uuid import UUID
from app.models.menu import MenuItem
from app.schemas.menu import CreateMenuItemRequest, UpdateMenuItemRequest

DEFAULT_MENUS_DATA = [
  {
    "title": "Dashboard",
    "icon": "LuLayoutGrid",
    "path": "/dashboard",
    "permission_key": "dashboard.read",
    "order_index": 1,
    "children": []
  },
  {
    "title": "Customers",
    "icon": "LuContact",
    "path": "/sales/customers",
    "permission_key": "customer.read",
    "order_index": 2,
    "children": []
  },
  {
    "title": "Sales",
    "icon": "LuMegaphone",
    "path": None,
    "permission_key": "sales.menu",
    "order_index": 3,
    "children": [
      {"title": "Leads", "icon": "LuUser", "path": "/leads", "permission_key": "lead.read", "order_index": 1},
      {"title": "Oppurtunity", "icon": "LuStar", "path": "/sales/opportunities", "permission_key": "opportunity.read", "order_index": 2},
      {"title": "Sales Orders", "icon": "LuFileText", "path": "/sales/orders", "permission_key": "order.read", "order_index": 3},
    ]
  },
  {
    "title": "Accounts",
    "icon": "LuUsers",
    "path": "/users",
    "permission_key": "user.read",
    "order_index": 4,
    "children": []
  },
  {
    "title": "Inventory",
    "icon": "LuPackage",
    "path": "/inventory",
    "permission_key": "inventory.read",
    "order_index": 5,
    "children": []
  },
  {
    "title": "Reports",
    "icon": "LuTrendingUp",
    "path": "/reports",
    "permission_key": "reports.read",
    "order_index": 6,
    "children": []
  },
  {
    "title": "Masters",
    "icon": "LuDatabase",
    "path": None,
    "permission_key": "masters.menu",
    "order_index": 8,
    "children": [
      {"title": "Companies", "icon": "LuBuilding", "path": "/companies", "permission_key": "company.read", "order_index": 1},
      {"title": "Locations", "icon": "LuMapPin", "path": "/locations", "permission_key": "location.read", "order_index": 2},
      {"title": "Customer Type", "icon": "LuTag", "path": "/customer-types", "permission_key": "customer_type.read", "order_index": 3},
      {"title": "Product Type", "icon": "LuBoxes", "path": "/product-types", "permission_key": "product_type.read", "order_index": 4},
      {"title": "Category Group", "icon": "LuLayers", "path": "/category-groups", "permission_key": "category_group.read", "order_index": 5},
      {"title": "Units", "icon": "LuList", "path": "/units", "permission_key": "unit.read", "order_index": 6},
      {"title": "Roles & Access", "icon": "LuShieldCheck", "path": "/rbac", "permission_key": "role.read", "order_index": 7},
    ]
  },
  {
    "title": "Workflows",
    "icon": "LuGitBranch",
    "path": "/workflows",
    "permission_key": "workflow.read",
    "order_index": 9,
    "children": []
  }
]

class MenuService:

    @staticmethod
    def seed_default_menus(db: Session):
        count = db.query(MenuItem).count()
        if count > 0:
            return
        
        for parent_idx, g_item in enumerate(DEFAULT_MENUS_DATA):
            parent_menu = MenuItem(
                title=g_item["title"],
                icon=g_item["icon"],
                path=g_item.get("path"),
                permission_key=g_item["permission_key"],
                order_index=g_item["order_index"],
                is_active=True,
            )
            db.add(parent_menu)
            db.commit()
            db.refresh(parent_menu)

            for c_item in g_item.get("children", []):
                child_menu = MenuItem(
                    title=c_item["title"],
                    icon=c_item["icon"],
                    path=c_item.get("path"),
                    permission_key=c_item["permission_key"],
                    parent_id=parent_menu.id,
                    order_index=c_item["order_index"],
                    is_active=True,
                )
                db.add(child_menu)
            db.commit()

    @staticmethod
    def get_menu_tree(db: Session):
        MenuService.seed_default_menus(db)
        parents = (
            db.query(MenuItem)
            .filter(MenuItem.parent_id.is_(None))
            .order_by(asc(MenuItem.order_index))
            .all()
        )
        
        result = []
        for p in parents:
            children = (
                db.query(MenuItem)
                .filter(MenuItem.parent_id == p.id)
                .order_by(asc(MenuItem.order_index))
                .all()
            )
            
            result.append({
                "id": str(p.id),
                "title": p.title,
                "icon": p.icon,
                "path": p.path,
                "permission_key": p.permission_key,
                "parent_id": None,
                "order_index": p.order_index,
                "is_active": p.is_active,
                "children": [
                    {
                        "id": str(c.id),
                        "title": c.title,
                        "icon": c.icon,
                        "path": c.path,
                        "permission_key": c.permission_key,
                        "parent_id": str(p.id),
                        "order_index": c.order_index,
                        "is_active": c.is_active,
                    }
                    for c in children if c.is_active
                ]
            })
        return result

    @staticmethod
    def get_user_sidebar(user_permissions: set[str], is_super_admin: bool, db: Session):
        tree = MenuService.get_menu_tree(db)
        if is_super_admin:
            return tree

        filtered = []
        for item in tree:
            if not item["is_active"]:
                continue
            
            # If item has submenus
            if item["children"]:
                allowed_children = [
                    ch for ch in item["children"]
                    if ch["is_active"] and (not ch["permission_key"] or ch["permission_key"] in user_permissions)
                ]
                if allowed_children:
                    item_copy = dict(item)
                    item_copy["children"] = allowed_children
                    filtered.append(item_copy)
            else:
                if not item["permission_key"] or item["permission_key"] in user_permissions:
                    filtered.append(item)
                    
        return filtered

    @staticmethod
    def create_menu_item(request: CreateMenuItemRequest, db: Session):
        parent_id = UUID(request.parent_id) if request.parent_id else None
        item = MenuItem(
            title=request.title,
            icon=request.icon,
            path=request.path,
            permission_key=request.permission_key,
            parent_id=parent_id,
            order_index=request.order_index or 0,
            is_active=request.is_active if request.is_active is not None else True,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    @staticmethod
    def update_menu_item(menu_id: str, request: UpdateMenuItemRequest, db: Session):
        item = db.query(MenuItem).filter(MenuItem.id == UUID(menu_id)).first()
        if not item:
            return None
        if request.title is not None:
            item.title = request.title
        if request.icon is not None:
            item.icon = request.icon
        if request.path is not None:
            item.path = request.path
        if request.permission_key is not None:
            item.permission_key = request.permission_key
        if request.parent_id is not None:
            item.parent_id = UUID(request.parent_id) if request.parent_id else None
        if request.order_index is not None:
            item.order_index = request.order_index
        if request.is_active is not None:
            item.is_active = request.is_active
        db.commit()
        db.refresh(item)
        return item

    @staticmethod
    def delete_menu_item(menu_id: str, db: Session):
        item = db.query(MenuItem).filter(MenuItem.id == UUID(menu_id)).first()
        if item:
            db.delete(item)
            db.commit()
            return True
        return False
