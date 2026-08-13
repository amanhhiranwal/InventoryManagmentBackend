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
        print("Access token retrieved successfully.")

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

    print("\nCreating customer-type...")
    create_ct_data = json.dumps({
        "name": "Integration Test Customer Type",
        "code": "TEST-CT",
        "description": "Created during integration tests"
    }).encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:8000/api/v1/customer-types/",
        data=create_ct_data,
        headers={"Content-Type": "application/json"}
    )
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as response:
        print("Status:", response.status)
        ct_payload = response.read().decode("utf-8")
        print("Payload:", ct_payload)
        ct_id = json.loads(ct_payload).get("data", {}).get("id")

    print(f"\nDeleting customer-type: {ct_id}...")
    req = urllib.request.Request(
        f"http://localhost:8000/api/v1/customer-types/{ct_id}",
        method="DELETE"
    )
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as response:
        print("Status:", response.status)
        print("Payload:", response.read().decode("utf-8"))

    print("\nFetching companies...")
    req = urllib.request.Request("http://localhost:8000/api/v1/companies/")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as response:
        print("Status:", response.status)
        print("Payload:", response.read().decode("utf-8"))

    print("\nCreating new company...")
    create_company_data = json.dumps({
        "company_name": "Integration Test Company",
        "company_code": "TEST-COMP",
        "email": "test-comp@example.com",
        "phone_number": "1234567890",
        "address_line_1": "123 Test St",
        "city": "Testville",
        "state": "Test State",
        "country": "Test Country",
        "postal_code": "12345"
    }).encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:8000/api/v1/companies/",
        data=create_company_data,
        headers={"Content-Type": "application/json"}
    )
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as response:
        print("Status:", response.status)
        comp_payload = response.read().decode("utf-8")
        print("Payload:", comp_payload)
        company_id = json.loads(comp_payload).get("data", {}).get("id")

    print("\nCreating new location...")
    create_loc_data = json.dumps({
        "company_id": company_id,
        "location_name": "Integration Test Location",
        "location_code": "TEST-LOC",
        "email": "test-loc@example.com",
        "phone_number": "0987654321",
        "address_line_1": "456 Test Rd",
        "city": "Locality",
        "state": "Loc State",
        "country": "Loc Country",
        "postal_code": "54321",
        "is_default": True
    }).encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:8000/api/v1/locations/",
        data=create_loc_data,
        headers={"Content-Type": "application/json"}
    )
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as response:
        print("Status:", response.status)
        print("Payload:", response.read().decode("utf-8"))

    print("\nFetching CRM leads...")
    req = urllib.request.Request("http://localhost:8000/api/v1/leads/")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as response:
        print("Status:", response.status)
        leads_payload = response.read().decode("utf-8")
        print("Payload:", leads_payload)
        leads = json.loads(leads_payload).get("data", [])

    print("\nCreating new CRM lead...")
    create_data = json.dumps({
        "title": "Decoupled Integration Test Lead",
        "description": "Created during decoupled microservices integration tests.",
        "status": "new"
    }).encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:8000/api/v1/leads/",
        data=create_data,
        headers={"Content-Type": "application/json"}
    )
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as response:
        print("Status:", response.status)
        created_lead_payload = response.read().decode("utf-8")
        print("Payload:", created_lead_payload)
        new_lead = json.loads(created_lead_payload).get("data", {})
        new_lead_id = new_lead.get("id")

    if new_lead_id:
        print(f"\nProgressing new CRM lead: {new_lead_id}...")
        progress_data = json.dumps({
            "stage": "opportunity",
            "status": "active"
        }).encode("utf-8")
        req = urllib.request.Request(
            f"http://localhost:8000/api/v1/leads/{new_lead_id}/progress",
            data=progress_data,
            headers={"Content-Type": "application/json"},
            method="PUT"
        )
        req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req) as response:
            print("Status:", response.status)
            print("Payload:", response.read().decode("utf-8"))

except Exception as e:
    print("Error:", e)
