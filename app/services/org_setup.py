"""Departments and roles, and how HR's employee sheet maps onto them.

One place for the mapping from the sheet's Department and Designation
columns to the CRM's departments and roles. Used by the user bulk upload
(Users > Bulk Upload), by seed_departments_and_roles.py and by the release
setup, so all three agree.

A role is what someone may open and do, not their job title: the sheet
has some ninety designations, and each maps onto one role (ROLE_FOR). A
designation the map does not know is an Employee.
"""

from app.core.procurement import RAISE_REQUISITION
from app.models.permission import Permission
from app.models.procurement import Department
from app.models.role import Role
from app.models.role_permission import RolePermission

#: The sheet's department, as written, to the department in the CRM.
DEPARTMENT_FOR = {
    "management": "Management",
    "sales": "Sales",
    "business": "Business",
    "accounts and finance": "Accounts and Finance",
    "purchase": "Purchase",
    "logistics": "Logistics and Warehouse",
    "logistics and warehouse": "Logistics and Warehouse",
    "logistics and accounts": "Logistics and Warehouse",
    "operations": "Operations",
    "admin and operations": "Admin and Operations",
    "hr": "Human Resources",
    "human resources": "Human Resources",
    "engineering": "Engineering",
    "software developer": "Software Development",
    "it": "IT",
    "curriculuam": "Curriculum",
    "curriculum": "Curriculum",
    "marketing": "Marketing",
    "graphic designing": "Design",
    "designing": "Design",
    "project management": "Project Management",
}

#: Codes shown beside the name on Masters > Departments.
DEPARTMENT_CODES = {
    "Management": "MGMT", "Sales": "SALES", "Business": "BIZ", "Accounts and Finance": "FIN",
    "Purchase": "PUR", "Logistics and Warehouse": "LOG", "Operations": "OPS",
    "Admin and Operations": "ADMIN", "Human Resources": "HR", "Engineering": "ENG",
    "Software Development": "SOFT", "IT": "IT", "Curriculum": "CUR", "Marketing": "MKT",
    "Design": "DES", "Project Management": "PM",
}

#: New roles: description, and the menu permissions they start with.
NEW_ROLES = {
    "Employee": (
        "Everyday user: raises purchase requisitions and approves the ones that reach them.",
        [],
    ),
    "Procurement": (
        "Works the procurement desk: quotations, vendors and purchase orders.",
        ["procurement.read", "procurement.manage", "vendor.read", "vendor.manage",
         "purchase_order.read", "purchase_order.manage"],
    ),
    "Procurement Head": (
        "Runs procurement: everything Procurement does, approves vendors, keeps departments and the PR approval matrix.",
        ["procurement.read", "procurement.manage", "vendor.read", "vendor.manage", "vendor.approve",
         "purchase_order.read", "purchase_order.manage", "masters.menu", "procurement_masters.manage"],
    ),
    "HR & Admin": (
        "People and lists: users, the reporting chart and the Masters lists.",
        ["user.read", "masters.menu", "company.read", "location.read", "customer_type.read",
         "product_type.read", "category_group.read", "unit.read", "procurement_masters.manage"],
    ),
    "Marketing": (
        "Marketing and brand: leads from campaigns, and purchase requisitions.",
        ["lead.read"],
    ),
}

#: The sheet's designation, lower-cased and spaced out, to the CRM role.
#: Existing roles are used where they fit; the rest go to the new ones.
ROLE_FOR = {
    # The sales chart, as import_users_from_sheet.py maps it.
    "founder": "Founder",
    "group ceo": "CEO",
    "avp": "AVP",
    "avp business": "AVP",
    "executive vp": "AVP",
    "zonal head": "Zonal Head",
    "regional manager": "Zonal Head",
    "area manager": "Area Manager",
    "area manager business": "Area Manager",
    "sr. area sales manager": "Area Manager",
    "cbo": "AVP",
    "cbo-display panels": "AVP",
    # Sales support and coordination.
    "business support executive": "Business Coordinator",
    "executive, business operations": "Business Coordinator",
    "manager-mis": "Business Coordinator",
    "manager - product & business excellence": "Business Coordinator",
    "ea to founder": "Business Coordinator",
    "manager at founders office": "Business Coordinator",
    "founder office and strategic": "Business Coordinator",
    "assistant manager – gem & tendering": "Business Coordinator",
    "assistant manager- project & business analyst": "Business Coordinator",
    "am-business ops": "Business Coordinator",
    # Money.
    "director": "Director",
    "senior manager- accounts and finance": "Finance",
    "am-accounts and finance": "Finance",
    "account executive": "Accounts",
    "accounts & finance executive": "Accounts",
    "executive - accounts & finance": "Accounts",
    "accounts and operations": "Accounts",
    "sr.account ex.": "Accounts",
    # Buying.
    "purchase executive": "Procurement",
    "executive - supply chain management": "Procurement",
    "manager - procurement & brand association": "Procurement Head",
    # Stock, production and delivery.
    "head - production and technology": "Production Tech Head",
    "assistant manager- production & warehouse": "Inventory",
    "stores operations lead": "Inventory",
    "manager operation": "Inventory",
    "scm & logistics lead": "Logistics",
    "scm -manager": "Logistics",
    # People and the office.
    "hr manager": "HR & Admin",
    "hr executive": "HR & Admin",
    "hr exceutive": "HR & Admin",
    "executive – human resources": "HR & Admin",
    "manager- hr & business operations": "HR & Admin",
    "front office exceutive": "HR & Admin",
    # Marketing.
    "head -marketing and branding": "Marketing",
    "senior manager -marketing": "Marketing",
    "social media and brand manager": "Marketing",
    "social media intern": "Marketing",
    "seo": "Marketing",
    "video editor": "Marketing",
    "product manager": "Marketing",
}

#: Everyone else - curriculum, engineering, software, design, projects,
#: operations staff - is an Employee.
DEFAULT_ROLE = "Employee"


def tidy(value) -> str:
    return " ".join(str(value or "").split())


def role_for(designation: str) -> str:
    return ROLE_FOR.get(tidy(designation).lower(), DEFAULT_ROLE)


def department_for(name: str) -> str | None:
    return DEPARTMENT_FOR.get(tidy(name).lower())


def ensure_departments(db) -> list[str]:
    added = []
    have = {d.name.lower() for d in db.query(Department).all()}
    for name in sorted(set(DEPARTMENT_FOR.values())):
        if name.lower() in have:
            continue
        code = DEPARTMENT_CODES.get(name)
        if code and db.query(Department).filter(Department.code == code).first():
            code = None
        db.add(Department(name=name, code=code, is_active=True))
        added.append(name)
    db.commit()
    return added


def permission(db, key: str) -> Permission:
    row = db.query(Permission).filter(Permission.permission_name == key).first()
    if row is None:
        row = Permission(permission_name=key, module=key.split(".")[0], description=None)
        db.add(row)
        db.flush()
    return row


def ensure_roles(db) -> list[str]:
    """Each new role, with its starting permissions and the requisition tick.

    A role that already exists is left exactly as it is - its permissions
    may have been changed on purpose since."""

    added = []
    for name, (description, keys) in NEW_ROLES.items():
        if db.query(Role).filter(Role.role_name.ilike(name)).first():
            continue
        role = Role(role_name=name, description=description)
        db.add(role)
        db.flush()
        for key in [RAISE_REQUISITION, *keys]:
            db.add(RolePermission(role_id=role.id, permission_id=permission(db, key).id))
        added.append(name)
    db.commit()
    return added


#: The sales chart. Whatever the sheet says, these people work in Sales.
SALES_ROLES = {"AVP", "Zonal Head", "Area Manager"}


def department_for_person(department: str, role: str) -> str | None:
    """The CRM department for a row: Sales for the sales chart, otherwise
    the sheet's department as mapped."""
    if role in SALES_ROLES:
        return "Sales"
    return department_for(department)
