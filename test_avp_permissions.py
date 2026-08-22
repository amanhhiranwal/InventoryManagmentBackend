import urllib.request
import json
import time

BASE_URL = "http://localhost:8000/api/v1"

def make_request(url, method="GET", data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    encoded = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=encoded, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        return e.code, {"error": body}

def test_avp():
    ts = int(time.time())
    # 1. Login Superadmin to setup AVP user with AVP role & permissions
    status, res = make_request(f"{BASE_URL}/auth/login", method="POST", data={"email": "superadmin@example.com", "password": "password123"})
    admin_token = res["access_token"]

    # Get AVP role
    status, roles = make_request(f"{BASE_URL}/rbac/roles", token=admin_token)
    avp_role = next((r for r in roles if r.get("role_name") == "AVP" or r.get("name") == "AVP"), None)
    if not avp_role:
        print("AVP role not found")
        return
    avp_role_id = avp_role["id"]

    # Assign all standard permissions to AVP role
    status, perms = make_request(f"{BASE_URL}/rbac/permissions", token=admin_token)
    for p in perms:
        make_request(f"{BASE_URL}/rbac/roles/{avp_role_id}/permissions/{p['id']}", method="POST", token=admin_token)
    print(f"Assigned all permissions to AVP role ({avp_role_id}).")

    # Create AVP user account
    avp_email = f"avp{ts}@example.com"
    avp_pass = "Password123!"
    status, user_res = make_request(f"{BASE_URL}/users/", method="POST", data={
        "first_name": "Test",
        "last_name": "AVP",
        "email": avp_email,
        "password": avp_pass,
        "employee_id": f"EMP-AVP-{ts}",
        "role_ids": [avp_role_id]
    }, token=admin_token)
    print(f"Created AVP user account: {avp_email}")

    # 2. Login as AVP user!
    status, avp_login = make_request(f"{BASE_URL}/auth/login", method="POST", data={"email": avp_email, "password": avp_pass})
    print(f"AVP Login Status: {status}")
    if status != 200:
        print("AVP Login failed:", avp_login)
        return
    avp_token = avp_login["access_token"]

    # 3. Test endpoints with AVP token
    print("\n--- Testing Endpoints with AVP Token ---")
    endpoints = [
        ("GET", "/companies/"),
        ("GET", "/locations/"),
        ("GET", "/customer-types/"),
        ("GET", "/product-types/"),
        ("GET", "/category-groups/"),
        ("GET", "/workflows/"),
        ("GET", "/leads/"),
        ("GET", "/menus/sidebar"),
        ("GET", "/sales/customers/"),
        ("GET", "/sales/orders/"),
    ]

    all_passed = True
    for method, ep in endpoints:
        st, body = make_request(f"{BASE_URL}{ep}", method=method, token=avp_token)
        print(f"AVP -> {method} {ep}: Status {st}")
        if st != 200:
            print("  Error payload:", body)
            all_passed = False

    if all_passed:
        print("\n==================================================")
        print(" SUCCESS! AVP ACCESSES ALL MODULES WITH STATUS 200")
        print("==================================================")

if __name__ == "__main__":
    test_avp()
