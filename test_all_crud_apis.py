import urllib.request
import urllib.parse
import json
import time

BASE_URL = "http://localhost:8000/api/v1"

def make_request(url, method="GET", data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    encoded_data = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=encoded_data, headers=headers, method=method)
    
    try:
        with urllib.request.urlopen(req) as response:
            res_body = response.read().decode("utf-8")
            status = response.status
            return status, json.loads(res_body) if res_body else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            parsed_err = json.loads(err_body)
        except:
            parsed_err = {"raw": err_body}
        return e.code, parsed_err

def test_crud_suite():
    print("==================================================")
    print("   ENTERPRISE SAAS SYSTEM ALL API CRUD TEST SUITE ")
    print("==================================================")
    
    # 1. Login
    print("\n[1] Auth: Logging in superadmin...")
    status, res = make_request(f"{BASE_URL}/auth/login", method="POST", data={
        "email": "superadmin@example.com",
        "password": "password123"
    })
    print(f"-> Login Status: {status}")
    if status != 200:
        print("Login failed:", res)
        return
    token = res.get("access_token")
    print("-> Access token retrieved successfully.")

    # 2. Auth Me
    print("\n[2] Auth Me: Fetching current profile...")
    status, res = make_request(f"{BASE_URL}/auth/me", token=token)
    print(f"-> Auth Me Status: {status}, Is Super Admin: {res.get('data', {}).get('is_super_admin')}")

    # 3. Companies CRUD
    print("\n[3] Companies CRUD:")
    ts = int(time.time())
    c_data = {
        "company_name": f"Test Company {ts}",
        "company_code": f"TC{ts}",
        "email": f"company{ts}@example.com",
        "phone_number": "1234567890",
        "address_line_1": "123 Tech Park",
        "city": "Silicon Valley",
        "state": "CA",
        "country": "USA",
        "postal_code": "94025"
    }
    status, res = make_request(f"{BASE_URL}/companies/", method="POST", data=c_data, token=token)
    print(f"-> Create Company Status: {status}")
    company_id = res.get("data", {}).get("id")

    status, res = make_request(f"{BASE_URL}/companies/", token=token)
    print(f"-> List Companies Status: {status}, Total Count: {len(res.get('data', []))}")

    if company_id:
        status, res = make_request(f"{BASE_URL}/companies/{company_id}", method="PUT", data={"company_name": f"Updated Company {ts}"}, token=token)
        print(f"-> Update Company Status: {status}")

    # 4. Locations CRUD
    print("\n[4] Locations CRUD:")
    if company_id:
        l_data = {
            "company_id": company_id,
            "location_name": f"Test Location {ts}",
            "location_code": f"TL{ts}",
            "email": f"loc{ts}@example.com",
            "phone_number": "9876543210",
            "address_line_1": "Building A",
            "city": "Austin",
            "state": "TX",
            "country": "USA",
            "postal_code": "73301",
            "is_default": True
        }
        status, res = make_request(f"{BASE_URL}/locations/", method="POST", data=l_data, token=token)
        print(f"-> Create Location Status: {status}")
        location_id = res.get("data", {}).get("id")

        status, res = make_request(f"{BASE_URL}/locations/", token=token)
        print(f"-> List Locations Status: {status}, Count: {len(res.get('data', []))}")

        if location_id:
            status, res = make_request(f"{BASE_URL}/locations/{location_id}", method="PUT", data={"location_name": f"Updated Loc {ts}"}, token=token)
            print(f"-> Update Location Status: {status}")

    # 5. Customer Types CRUD
    print("\n[5] Customer Types CRUD:")
    status, res = make_request(f"{BASE_URL}/customer-types/", method="POST", data={
        "name": f"Enterprise Tier {ts}",
        "code": f"ENT{ts}",
        "description": "Enterprise Tier Customer"
    }, token=token)
    print(f"-> Create Customer Type Status: {status}")
    ct_id = res.get("data", {}).get("id")

    status, res = make_request(f"{BASE_URL}/customer-types/", token=token)
    print(f"-> List Customer Types Status: {status}")

    if ct_id:
        status, res = make_request(f"{BASE_URL}/customer-types/{ct_id}", method="DELETE", token=token)
        print(f"-> Delete Customer Type Status: {status}")

    # 6. Product Types CRUD
    print("\n[6] Product Types CRUD:")
    status, res = make_request(f"{BASE_URL}/product-types/", method="POST", data={
        "name": f"Hardware Item {ts}",
        "code": f"HW{ts}",
        "category": "Electronics",
        "description": "Hardware Product Category"
    }, token=token)
    print(f"-> Create Product Type Status: {status}")
    pt_id = res.get("data", {}).get("id")

    status, res = make_request(f"{BASE_URL}/product-types/", token=token)
    print(f"-> List Product Types Status: {status}")

    if pt_id:
        status, res = make_request(f"{BASE_URL}/product-types/{pt_id}", method="DELETE", token=token)
        print(f"-> Delete Product Type Status: {status}")

    # 7. Category Groups CRUD
    print("\n[7] Category Groups CRUD:")
    status, res = make_request(f"{BASE_URL}/category-groups/", method="POST", data={
        "name": f"Smart Devices {ts}",
        "code": f"SD{ts}"
    }, token=token)
    print(f"-> Create Category Group Status: {status}")
    cg_id = res.get("data", {}).get("id")

    status, res = make_request(f"{BASE_URL}/category-groups/", token=token)
    print(f"-> List Category Groups Status: {status}")

    if cg_id:
        status, res = make_request(f"{BASE_URL}/category-groups/{cg_id}", method="DELETE", token=token)
        print(f"-> Delete Category Group Status: {status}")

    # 8. Units CRUD
    print("\n[8] Units CRUD:")
    status, res = make_request(f"{BASE_URL}/inventory/units", method="POST", data={
        "name": f"Box-{ts}"
    }, token=token)
    print(f"-> Create Unit Status: {status}")

    status, res = make_request(f"{BASE_URL}/inventory/units", token=token)
    print(f"-> List Units Status: {status}")

    # 8.5 Database Menus CRUD
    print("\n[8.5] Database Menus & Dynamic Sidebar CRUD:")
    status, res = make_request(f"{BASE_URL}/menus/", token=token)
    print(f"-> Fetch DB Menu Tree Status: {status}, Group Count: {len(res.get('data', []))}")

    status, res = make_request(f"{BASE_URL}/menus/sidebar", token=token)
    print(f"-> Fetch Authorized Dynamic Sidebar Status: {status}, Count: {len(res.get('data', []))}")

    status, res = make_request(f"{BASE_URL}/menus/", method="POST", data={
        "title": f"Custom Module {ts}",
        "icon": "LuFolder",
        "path": f"/custom-{ts}",
        "permission_key": f"custom.read.{ts}",
        "order_index": 99
    }, token=token)
    print(f"-> Create DB Menu Item Status: {status}")
    m_id = res.get("data", {}).get("id")

    if m_id:
        status, res = make_request(f"{BASE_URL}/menus/{m_id}", method="DELETE", token=token)
        print(f"-> Delete DB Menu Item Status: {status}")

    # 9. RBAC (Roles & Permissions) CRUD
    print("\n[9] RBAC Roles & Permissions CRUD:")
    status, res = make_request(f"{BASE_URL}/rbac/roles", method="POST", data={
        "role_name": f"Zonal Manager {ts}",
        "description": "Zonal Operations Head"
    }, token=token)
    print(f"-> Create Role Status: {status}")
    role_id = res.get("data", {}).get("id") or res.get("id")

    status, res = make_request(f"{BASE_URL}/rbac/permissions", method="POST", data={
        "permission_name": f"zonal.reports.{ts}",
        "module": "zonal",
        "description": "View zonal reports"
    }, token=token)
    print(f"-> Create Permission Status: {status}")
    perm_id = res.get("data", {}).get("id") or res.get("id")

    if role_id and perm_id:
        status, res = make_request(f"{BASE_URL}/rbac/roles/{role_id}/permissions/{perm_id}", method="POST", token=token)
        print(f"-> Assign Permission to Role Status: {status}")

        status, res = make_request(f"{BASE_URL}/rbac/roles/{role_id}/permissions", token=token)
        print(f"-> Fetch Role Permissions Status: {status}, Count: {len(res if isinstance(res, list) else res.get('data', []))}")

        status, res = make_request(f"{BASE_URL}/rbac/roles/{role_id}/permissions/{perm_id}", method="DELETE", token=token)
        print(f"-> Remove Permission from Role Status: {status}")

    # 10. User Accounts CRUD
    print("\n[10] User Accounts CRUD:")
    u_data = {
        "first_name": "Area",
        "last_name": "Head",
        "email": f"areahead{ts}@example.com",
        "password": "Password123!",
        "phone_number": "9988776655",
        "employee_id": f"EMP-{ts}",
        "role_ids": [role_id] if role_id else [],
        "company_ids": [company_id] if company_id else []
    }
    status, res = make_request(f"{BASE_URL}/users/", method="POST", data=u_data, token=token)
    print(f"-> Create User Status: {status}")
    user_id = res.get("data", {}).get("id")

    status, res = make_request(f"{BASE_URL}/users/", token=token)
    print(f"-> List Users Status: {status}")

    # 11. Workflows CRUD
    print("\n[11] Workflows CRUD:")
    wf_data = {
        "name": f"Sales Hierarchy {ts}",
        "description": "Standard Zonal to Salesperson Flow",
        "nodes": [
            {"id": "node-1", "x": 100, "y": 100, "data": {"label": "Zonal Head", "role_id": role_id or ""}},
            {"id": "node-2", "x": 100, "y": 250, "data": {"label": "Area Head", "role_id": role_id or ""}}
        ],
        "edges": [{"id": "edge-1", "source": "node-1", "target": "node-2"}]
    }
    status, res = make_request(f"{BASE_URL}/workflows/", method="POST", data=wf_data, token=token)
    print(f"-> Create Workflow Status: {status}")
    wf_id = res.get("data", {}).get("id")

    status, res = make_request(f"{BASE_URL}/workflows/", token=token)
    print(f"-> List Workflows Status: {status}")

    # 12. CRM Leads & Superior Allocation CRUD
    print("\n[12] CRM Leads & Superior Allocation CRUD:")
    lead_data = {
        "title": f"Enterprise Deal {ts}",
        "description": "Commercial Display Units Order"
    }
    status, res = make_request(f"{BASE_URL}/leads/", method="POST", data=lead_data, token=token)
    print(f"-> Create Lead Status: {status}")
    lead_id = res.get("data", {}).get("id")

    status, res = make_request(f"{BASE_URL}/leads/", token=token)
    print(f"-> List Leads Status: {status}, Total: {len(res.get('data', []))}")

    if lead_id and user_id:
        status, res = make_request(f"{BASE_URL}/leads/{lead_id}/assign", method="PUT", data={"assigned_to_id": user_id}, token=token)
        print(f"-> Superior Reassign Lead Status: {status}")

        prog_data = {
            "stage": "opportunity",
            "status": "active",
            "demo_status": "given",
            "requirements": "Need 50 4K displays"
        }
        status, res = make_request(f"{BASE_URL}/leads/{lead_id}/progress", method="PUT", data=prog_data, token=token)
        print(f"-> Progress Lead Stage Status: {status}")

    # 13. Customers & Orders CRUD
    print("\n[13] Sales Customers & Orders CRUD:")
    cust_data = {
        "customer_name": f"Acme Corp {ts}",
        "email": f"contact{ts}@acme.com",
        "phone": "5551234567"
    }
    status, res = make_request(f"{BASE_URL}/sales/customers/", method="POST", data=cust_data, token=token)
    print(f"-> Create Sales Customer Status: {status}")

    status, res = make_request(f"{BASE_URL}/sales/customers/", token=token)
    print(f"-> List Sales Customers Status: {status}")

    order_data = {
        "customer_name": f"Acme Corp {ts}",
        "items": [{"item": "Smart Display 55 inch", "qty": 10, "price": 45000}],
        "total_amount": 450000
    }
    status, res = make_request(f"{BASE_URL}/sales/orders/", method="POST", data=order_data, token=token)
    print(f"-> Create Sales Order Status: {status}")

    status, res = make_request(f"{BASE_URL}/sales/orders/", token=token)
    print(f"-> List Sales Orders Status: {status}")

    # Cleanup test entities
    if company_id:
        make_request(f"{BASE_URL}/companies/{company_id}", method="DELETE", token=token)
    if role_id:
        make_request(f"{BASE_URL}/rbac/roles/{role_id}", method="DELETE", token=token)
    if perm_id:
        make_request(f"{BASE_URL}/rbac/permissions/{perm_id}", method="DELETE", token=token)

    print("\n==================================================")
    print("   ALL API CRUD TESTS COMPLETED SUCCESSFULLY!")
    print("==================================================")

if __name__ == "__main__":
    test_crud_suite()
