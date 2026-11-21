"""Live end-to-end API test for the sequential sales approval flow.

Tests against http://localhost:8000/api/v1:
1. Logins for ZH, AVP, CEO, Founder succeed with Synergy@123.
2. Step 0 (AVP):
   - Only AVP has approval in GET /approvals/pending.
   - CEO and Founder are blocked (403/400) if they try to decide step 0.
   - AVP successfully approves step 0.
3. Step 1 (CEO):
   - Approval moves to CEO (in CEO's pending queue, not Founder's).
   - Founder is blocked (403/400) if they try to decide step 1.
   - CEO successfully approves step 1.
4. Step 2 (Founder):
   - Approval moves to Founder.
   - Founder approves step 2.
   - Flow completes as APPROVED.
5. Unauthenticated security:
   - POST /decide-by-link returns 401.
   - GET /decide-by-link redirects to CRM /login.
"""

import sys
import requests

BASE = "http://localhost:8000/api/v1"
PASSWORD = "Synergy@123"


def login(email):
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, f"Login failed for {email}: {r.status_code} {r.text}"
    body = r.json()
    return body.get("access_token") or body.get("data", {}).get("access_token")


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def run_live_test():
    print("=== Testing Live Approval Flow on http://localhost:8000/api/v1 ===")

    # 1. Sign in all users
    print("\n1. Signing in demo users...")
    zh_token = login("zh.north@mailinator.com")
    avp_token = login("avp@mailinator.com")
    ceo_token = login("ceo@mailinator.com")
    founder_token = login("founder@mailinator.com")
    print("  [PASS] All 4 users logged in successfully.")

    # 2. Inspect Sample Order SO-NORTH-001
    r_orders = requests.get(f"{BASE}/orders", headers=auth_header(zh_token))
    assert r_orders.status_code == 200, f"Could not list orders: {r_orders.status_code}"
    target = next((o for o in r_orders.json().get("data", []) if o.get("order_number") == "SO-NORTH-001"), None)
    assert target is not None, "Sample order SO-NORTH-001 not found!"
    order_id = target["id"]
    order_data = target
    print(f"\n2. Testing Order: {order_data.get('order_number')} (ID: {order_id}, Customer: {order_data.get('customer_name')})")

    # Fetch approval
    r_appr = requests.get(f"{BASE}/approvals/document/SALES_ORDER/{order_id}", headers=auth_header(zh_token))
    assert r_appr.status_code == 200, f"Could not fetch approval: {r_appr.status_code} {r_appr.text}"
    approval = r_appr.json()["data"]
    approval_id = approval["id"]
    print(f"  Approval ID: {approval_id}, Current step: {approval['current_step']}, Waiting on: {approval.get('waiting_on')}")
    assert approval["waiting_on"] == "AVP", f"Expected waiting on AVP, got {approval.get('waiting_on')}"

    # 3. Check queues for Step 0 (AVP step)
    print("\n3. Verifying pending queues for Step 0 (AVP)...")
    avp_pending = [x["id"] for x in requests.get(f"{BASE}/approvals/pending", headers=auth_header(avp_token)).json().get("data", [])]
    ceo_pending = [x["id"] for x in requests.get(f"{BASE}/approvals/pending", headers=auth_header(ceo_token)).json().get("data", [])]
    founder_pending = [x["id"] for x in requests.get(f"{BASE}/approvals/pending", headers=auth_header(founder_token)).json().get("data", [])]

    assert approval_id in avp_pending, f"Approval {approval_id} should be in AVP's pending queue!"
    assert approval_id not in ceo_pending, f"Approval {approval_id} must NOT be in CEO's pending queue yet!"
    assert approval_id not in founder_pending, f"Approval {approval_id} must NOT be in Founder's pending queue yet!"
    print("  [PASS] Only AVP has the approval in their pending queue. CEO and Founder queues are clear.")

    # 4. Attempt premature decisions by Founder and CEO on Step 0
    print("\n4. Verifying higher roles cannot bypass Step 0...")
    r_founder_premature = requests.put(
        f"{BASE}/approvals/{approval_id}/decide",
        headers=auth_header(founder_token),
        json={"approve": True, "remarks": "Founder unauthorized bypass attempt"},
    )
    assert r_founder_premature.status_code in (400, 403), f"Founder must be refused! Got {r_founder_premature.status_code}"
    print(f"  [PASS] Founder decision refused with HTTP {r_founder_premature.status_code}: {r_founder_premature.json().get('detail')}")

    r_ceo_premature = requests.put(
        f"{BASE}/approvals/{approval_id}/decide",
        headers=auth_header(ceo_token),
        json={"approve": True, "remarks": "CEO unauthorized bypass attempt"},
    )
    assert r_ceo_premature.status_code in (400, 403), f"CEO must be refused! Got {r_ceo_premature.status_code}"
    print(f"  [PASS] CEO decision refused with HTTP {r_ceo_premature.status_code}: {r_ceo_premature.json().get('detail')}")

    # 5. AVP approves Step 0
    print("\n5. AVP approving Step 0...")
    r_avp_decide = requests.put(
        f"{BASE}/approvals/{approval_id}/decide",
        headers=auth_header(avp_token),
        json={"approve": True, "remarks": "Approved by AVP Rohit Malhotra"},
    )
    assert r_avp_decide.status_code == 200, f"AVP decision failed: {r_avp_decide.status_code} {r_avp_decide.text}"
    appr_after_step0 = r_avp_decide.json()["data"]
    print(f"  [PASS] AVP approved. Current step: {appr_after_step0['current_step']}, Waiting on: {appr_after_step0['waiting_on']}")
    assert appr_after_step0["waiting_on"] == "CEO"

    # 6. Check queues for Step 1 (CEO step)
    print("\n6. Verifying pending queues for Step 1 (CEO)...")
    avp_pending = [x["id"] for x in requests.get(f"{BASE}/approvals/pending", headers=auth_header(avp_token)).json().get("data", [])]
    ceo_pending = [x["id"] for x in requests.get(f"{BASE}/approvals/pending", headers=auth_header(ceo_token)).json().get("data", [])]
    founder_pending = [x["id"] for x in requests.get(f"{BASE}/approvals/pending", headers=auth_header(founder_token)).json().get("data", [])]

    assert approval_id not in avp_pending, "Approval should no longer be in AVP pending queue"
    assert approval_id in ceo_pending, "Approval MUST now be in CEO pending queue"
    assert approval_id not in founder_pending, "Approval must NOT be in Founder queue yet"
    print("  [PASS] Approval successfully transferred strictly to CEO queue.")

    # 7. Founder attempts to decide Step 1 (CEO step)
    print("\n7. Verifying Founder cannot decide CEO step...")
    r_founder_premature_step1 = requests.put(
        f"{BASE}/approvals/{approval_id}/decide",
        headers=auth_header(founder_token),
        json={"approve": True, "remarks": "Founder bypassing CEO"},
    )
    assert r_founder_premature_step1.status_code in (400, 403), f"Founder must be refused! Got {r_founder_premature_step1.status_code}"
    print(f"  [PASS] Founder decision refused on CEO step with HTTP {r_founder_premature_step1.status_code}.")

    # 8. CEO approves Step 1
    print("\n8. CEO approving Step 1...")
    r_ceo_decide = requests.put(
        f"{BASE}/approvals/{approval_id}/decide",
        headers=auth_header(ceo_token),
        json={"approve": True, "remarks": "Approved by CEO Arjun Sethi"},
    )
    assert r_ceo_decide.status_code == 200, f"CEO decision failed: {r_ceo_decide.status_code} {r_ceo_decide.text}"
    appr_after_step1 = r_ceo_decide.json()["data"]
    print(f"  [PASS] CEO approved. Current step: {appr_after_step1['current_step']}, Waiting on: {appr_after_step1['waiting_on']}")
    assert appr_after_step1["waiting_on"] == "Founder"

    # 9. Founder approves Step 2
    print("\n9. Founder approving Step 2...")
    r_founder_decide = requests.put(
        f"{BASE}/approvals/{approval_id}/decide",
        headers=auth_header(founder_token),
        json={"approve": True, "remarks": "Approved by Founder Nina Kapoor"},
    )
    assert r_founder_decide.status_code == 200, f"Founder decision failed: {r_founder_decide.status_code} {r_founder_decide.text}"
    appr_final = r_founder_decide.json()["data"]
    print(f"  [PASS] Founder approved. Status: {appr_final['status']}, Waiting on: {appr_final['waiting_on']}")
    assert appr_final["status"] == "APPROVED"
    assert appr_final["waiting_on"] is None

    # 10. Security link checks
    print("\n10. Testing unauthenticated link endpoints...")
    unauth_post = requests.post(f"{BASE}/approvals/decide-by-link")
    assert unauth_post.status_code == 401, f"Expected 401, got {unauth_post.status_code}"
    print("  [PASS] POST /decide-by-link rejected with HTTP 401 Unauthorized.")

    unauth_get = requests.get(f"{BASE}/approvals/decide-by-link?token=invalid", allow_redirects=False)
    assert unauth_get.status_code == 303, f"Expected 303 redirect, got {unauth_get.status_code}"
    assert unauth_get.headers.get("location", "").endswith("/login"), f"Expected redirect to /login, got {unauth_get.headers.get('location')}"
    print("  [PASS] GET /decide-by-link safely redirects to CRM /login.")

    print("\n========================================================")
    print("ALL LIVE TESTS PASSED! The complete sales approval flow is")
    print("strictly sequential, secure, and ready for user testing.")
    print("========================================================")
    return 0


if __name__ == "__main__":
    sys.exit(run_live_test())
