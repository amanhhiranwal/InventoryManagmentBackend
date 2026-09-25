from uuid import UUID

from sqlalchemy import asc
from sqlalchemy.orm import Session

from app.models.menu import MenuItem
from app.schemas.menu import CreateMenuItemRequest, UpdateMenuItemRequest

# Default titles that were corrected to match the design. A row still carrying
# the old default is renamed on seed; a title the user chose is left alone.
RENAMED_DEFAULT_TITLES = {
  "Oppurtunity": "Opportunity",
  "Sales Orders": "Sales Order",
}

#: Top-level menus the design moved: title -> (old default, new default).
#: A row still at its old default is moved; one the user reordered is not.
MOVED_DEFAULT_ORDER = {
  "Customers": (2, 6),
  "Reports": (6, 7),
}

#: Set once the default menus have been checked in this process.
_DEFAULTS_ENSURED = False

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
    # After Inventory, as in the design.
    "order_index": 6,
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
      {"title": "Opportunity", "icon": "LuStar", "path": "/sales/opportunities", "permission_key": "opportunity.read", "order_index": 2},
      {"title": "Quotation", "icon": "LuQuoteOpen", "path": "/sales/quotations", "permission_key": "quotation.read", "order_index": 3},
      {"title": "Sales Order", "icon": "LuFileText", "path": "/sales/orders", "permission_key": "order.read", "order_index": 4},
      # Raised against a confirmed sales order, so whoever can see orders can
      # see their invoices - no separate permission to grant.
      {"title": "Proforma Invoice", "icon": "LuReceiptText", "path": "/sales/proforma-invoices", "permission_key": "order.read", "order_index": 5},
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
    "order_index": 7,
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
      # Who the proposals and emails come from. Super admin only, like the
      # rest of Masters - the page refuses anyone else.
      {"title": "Company Profile", "icon": "LuBuilding2", "path": "/company-profile", "permission_key": "company.read", "order_index": 8},
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
        """Ensure every default menu item exists.

        Backfills anything missing, matched on title within its parent, and
        leaves existing rows - including any the user has renamed or
        reordered - untouched. Reads every menu row in one query and commits
        only when something was actually added or renamed, so it costs a
        single query when the menus are already in place.
        """

        rows = db.query(MenuItem).all()
        changed = False

        # Old default titles corrected to match the design.
        for old_title, new_title in RENAMED_DEFAULT_TITLES.items():
            for row in [r for r in rows if r.title == old_title]:
                taken = any(
                    other.title == new_title and other.parent_id == row.parent_id
                    for other in rows
                )

                if not taken:
                    row.title = new_title
                    changed = True

        # Default positions the design changed (Customers now sits after
        # Inventory). Checked against the old defaults first, so moving one
        # row cannot be mistaken for another row's old position.
        moves = [
            (row, new)
            for row in rows
            if row.parent_id is None
            for title, (old, new) in MOVED_DEFAULT_ORDER.items()
            if row.title == title and row.order_index == old
        ]
        for row, new in moves:
            row.order_index = new
            changed = True

        parents_by_title = {r.title: r for r in rows if r.parent_id is None}
        children_keys = {(r.parent_id, r.title) for r in rows if r.parent_id is not None}

        for g_item in DEFAULT_MENUS_DATA:
            parent_menu = parents_by_title.get(g_item["title"])

            if parent_menu is None:
                parent_menu = MenuItem(
                    title=g_item["title"],
                    icon=g_item["icon"],
                    path=g_item.get("path"),
                    permission_key=g_item["permission_key"],
                    order_index=g_item["order_index"],
                    is_active=True,
                )
                db.add(parent_menu)
                db.flush()
                parents_by_title[parent_menu.title] = parent_menu
                changed = True

            for c_item in g_item.get("children", []):
                if (parent_menu.id, c_item["title"]) in children_keys:
                    continue

                db.add(
                    MenuItem(
                        title=c_item["title"],
                        icon=c_item["icon"],
                        path=c_item.get("path"),
                        permission_key=c_item["permission_key"],
                        parent_id=parent_menu.id,
                        order_index=c_item["order_index"],
                        is_active=True,
                    )
                )
                children_keys.add((parent_menu.id, c_item["title"]))
                changed = True

        if changed:
            db.commit()

    @staticmethod
    def ensure_default_menus(db: Session):
        """Run the default-menu check once per server process.

        The sidebar is fetched on every page load; the defaults only need
        checking once after start-up, not on each request.
        """

        global _DEFAULTS_ENSURED

        if _DEFAULTS_ENSURED:
            return

        MenuService.seed_default_menus(db)
        _DEFAULTS_ENSURED = True

    @staticmethod
    def get_menu_tree(db: Session):
        MenuService.ensure_default_menus(db)

        # Every menu in one query, grouped into parents and children here.
        rows = db.query(MenuItem).order_by(asc(MenuItem.order_index)).all()

        children_by_parent: dict = {}
        for row in rows:
            if row.parent_id is not None:
                children_by_parent.setdefault(row.parent_id, []).append(row)

        result = []
        for p in (r for r in rows if r.parent_id is None):
            children = children_by_parent.get(p.id, [])

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

        # Hidden menus stay hidden for everyone, super admins included; a
        # super admin only skips the permission check.
        if is_super_admin:
            return [item for item in tree if item["is_active"]]

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
        """Remove a menu item.

        A default item is hidden (is_active = False), with its sub-menus,
        rather than deleted: the seed recreates any default that is missing,
        so a real delete would bring it back. Hidden, it stays gone and can be
        switched back on with is_active = True. Custom items are deleted.
        """

        item = db.query(MenuItem).filter(MenuItem.id == UUID(menu_id)).first()

        if not item:
            return False

        if MenuService.is_default_menu(item, db):
            item.is_active = False

            for child in db.query(MenuItem).filter(MenuItem.parent_id == item.id).all():
                child.is_active = False
        else:
            db.delete(item)

        db.commit()
        return True

    @staticmethod
    def is_default_menu(item: MenuItem, db: Session) -> bool:
        """Whether the item is one the seed would recreate."""

        titles = set(RENAMED_DEFAULT_TITLES) | set(RENAMED_DEFAULT_TITLES.values())

        if item.parent_id is None:
            return item.title in titles or any(
                g["title"] == item.title for g in DEFAULT_MENUS_DATA
            )

        parent = db.query(MenuItem).filter(MenuItem.id == item.parent_id).first()

        for g_item in DEFAULT_MENUS_DATA:
            if parent is not None and g_item["title"] == parent.title:
                return item.title in titles or any(
                    c["title"] == item.title for c in g_item.get("children", [])
                )

        return False
