import urllib.request
import urllib.parse
import json

try:
    print("Logging in...")
    login_data = json.dumps({
        "email": "superadmin@example.com",
        "password": "password123"
    }).encode("utf-8")
    
    req = urllib.request.Request(
        "http://localhost:8000/api/v1/auth/login",
        data=login_data,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as response:
        res_body = response.read().decode("utf-8")
        token = json.loads(res_body).get("access_token")
        print("Access token retrieved.")

    print("\nFetching product-types...")
    req = urllib.request.Request("http://localhost:8000/api/v1/product-types/")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as response:
        print("Status:", response.status)
        print("Payload:", response.read().decode("utf-8"))

    print("\nFetching category-groups...")
    req = urllib.request.Request("http://localhost:8000/api/v1/category-groups/")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as response:
        print("Status:", response.status)
        print("Payload:", response.read().decode("utf-8"))

except Exception as e:
    print("Error:", e)
