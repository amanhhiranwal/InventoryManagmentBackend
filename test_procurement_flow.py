"""Procurement, end to end: a requisition to a paid purchase order.

Driven through the API as the people who would do it:

  * Anil (Area Manager) raises PRs - what is needed, not the price;
  * Priya (Zonal Head) is his manager: she confirms the need, and approves
    the POs in the first amount band;
  * the super admin runs the procurement desk and creates the POs, and so
    never approves them.

Covers: submit -> the manager only; outsiders and the requester refused;
return -> resubmit; reject; the desk; vendors; quotations requested per
line from several vendors; hand-entered quotations and a revision; the
comparison; POs created from chosen quotations with payment terms; the
amount bands applied to the PO (manager, department head, finance,
director); PO rejection and resubmission; approvers seeing the PO without
procurement access; the payment schedule, a post-dated cheque and payment
recorded; a requisition raised directly by procurement; the sales order
picker limited to orders the person may see; the KPIs; inventory
untouched; and the audit trail.

Everything it makes is removed at the end. Run seed_sales_team.py first.

    docker exec -w /app backend_app python test_procurement_flow.py
"""

import os
import sys
import time
import uuid
from datetime import date, timedelta
from io import BytesIO

import requests

from seed_sales_team import ADMIN_EMAIL, ADMIN_PASSWORD, BASE, TEAM_PASSWORD, api, email_for, login

TAG = uuid.uuid4().hex[:5].upper()
passed, failed = [], []
made = {"prs": [], "vendors": [], "users": []}


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


def banner(text):
    print(f"\n{text}")


def ok(response):
    return response.status_code == 200


def data(response):
    return response.json().get("data")


def upload(token, name="doc.pdf"):
    content = b"%PDF-1.4\n% test document\n%%EOF\n"
    r = requests.post(f"{BASE}/attachments", headers={"Authorization": f"Bearer {token}"},
                      files={"file": (name, BytesIO(content), "application/pdf")}, timeout=30)
    body = r.json().get("data", r.json())
    return {"key": body["key"], "name": name, "size": len(content), "type": "application/pdf"}


def stock_counts():
    from app.database.mongodb import sync_mongo_db
    return (sync_mongo_db["inventory_items"].count_documents({}),
            sync_mongo_db["inventory_movements"].count_documents({}))


IN_A_WEEK = (date.today() + timedelta(days=7)).isoformat()


def pr_payload(lines, **extra):
    return {
        "purchase_type": "OFFICE", "priority": "NORMAL", "required_by": IN_A_WEEK,
        "delivery_location": "Noida office, B-66 Sector 65", "purpose": f"Procurement flow test {TAG}",
        "items": lines, **extra,
    }


# What an employee asks for: no price - pricing is procurement's.
LINE_A = {"product_name": f"Test Laptop {TAG}", "sku": f"TL-{TAG}", "category": "IT", "brand": "Dell",
          "uom": "Nos", "quantity": 2}
LINE_B = {"product_name": f"Test Monitor {TAG}", "sku": f"TM-{TAG}", "category": "IT", "uom": "Nos", "quantity": 3}

try:
    before_stock = stock_counts()

    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    # The KPIs before this run, so the checks below measure what it adds.
    kpi_before = data(api("get", "/procurement/analytics?days=30", admin))
    anil = login(email_for("am_north_1"), TEAM_PASSWORD)
    deepa = login(email_for("am_north_2"), TEAM_PASSWORD)
    priya = login(email_for("zh_north"), TEAM_PASSWORD)

    # ------------------------------------------------------------------
    banner("1. An employee raises a requisition - what is needed, not the price")
    meta = data(api("get", "/purchase-requisitions/meta", anil))
    check("the form knows who is asking and their manager",
          meta["me"]["name"] and meta["me"]["reporting_manager_name"], str(meta["me"]))

    r = api("post", "/purchase-requisitions", anil, json=pr_payload([LINE_A, LINE_B]))
    check("saved as a draft", ok(r) and data(r)["status"] == "DRAFT", r.text[:200])
    pr = data(r)
    made["prs"].append(pr["id"])
    check("numbered PR-<year>-", pr["pr_number"].startswith(f"PR-{date.today().year}-"), pr["pr_number"])
    check("it carries no price", pr["estimated_total"] == 0, str(pr["estimated_total"]))
    r = api("post", "/purchase-requisitions", anil, json=pr_payload([{**LINE_A, "est_unit_price": 50000}]))
    check("a price an employee sends is ignored", ok(r) and data(r)["estimated_total"] == 0
          and data(r)["items"][0]["est_unit_price"] is None, r.text[:200])
    unpriced_id = data(r)["id"] if ok(r) else None
    if ok(r):
        made["prs"].append(data(r)["id"])

    r = api("post", "/purchase-requisitions", anil, json=pr_payload([LINE_A], required_by="2020-01-01", submit=True))
    check("a past required-by date is refused", r.status_code == 400, r.text[:150])
    left = [x for x in api("get", "/purchase-requisitions/mine", anil, params={"search": TAG, "size": 100}).json()["data"]
            if x["id"] not in (pr["id"], unpriced_id)]
    check("and nothing is saved by the refused submit", not left, str([x["pr_number"] for x in left]))
    made["prs"].extend(x["id"] for x in left)

    r = api("post", f"/purchase-requisitions/{pr['id']}/submit", anil)
    check("submitted", ok(r) and data(r)["status"] == "PENDING_APPROVAL", r.text[:200])
    pr = data(r)
    check("it waits on his reporting manager", pr["waiting_on"]["step"] == "REPORTING_MANAGER"
          and "Priya" in (pr["waiting_on"]["approver_label"] or ""), str(pr["waiting_on"]))
    steps = data(api("get", f"/purchase-requisitions/{pr['id']}", anil))["approvals"]
    check("and on nobody else - the amount bands are for the PO", len(steps) == 1, str(steps))

    queue = data(api("get", "/purchase-requisitions/approvals", priya))
    check("it is in the manager's Waiting on me", any(p["id"] == pr["id"] for p in queue["pending"]))
    queue = data(api("get", "/purchase-requisitions/approvals", deepa))
    check("and not in a colleague's", not any(p["id"] == pr["id"] for p in queue["pending"]))

    r = api("put", f"/purchase-requisitions/{pr['id']}", anil, json=pr_payload([LINE_A]))
    check("the employee cannot edit it once submitted", r.status_code == 409, r.text[:150])

    # ------------------------------------------------------------------
    banner("2. Only the right person approves")
    r = api("post", f"/purchase-requisitions/{pr['id']}/decision", anil, json={"decision": "APPROVE"})
    check("the requester cannot approve their own", r.status_code == 403, f"{r.status_code}")
    r = api("post", f"/purchase-requisitions/{pr['id']}/decision", deepa, json={"decision": "APPROVE"})
    check("a colleague cannot approve it", r.status_code == 403, f"{r.status_code}")
    r = api("post", f"/purchase-requisitions/{pr['id']}/decision", priya, json={"decision": "RETURN"})
    check("a return needs remarks", r.status_code == 400, f"{r.status_code}")

    # ------------------------------------------------------------------
    banner("3. Returned for correction, corrected, resubmitted")
    r = api("post", f"/purchase-requisitions/{pr['id']}/decision", priya,
            json={"decision": "RETURN", "remarks": "Add the brand on the monitors."})
    check("the manager returns it", ok(r) and data(r)["status"] == "RETURNED", r.text[:150])
    r = api("put", f"/purchase-requisitions/{pr['id']}", anil,
            json=pr_payload([LINE_A, {**LINE_B, "brand": "LG"}], submit=True))
    check("the employee edits and resubmits", ok(r) and data(r)["status"] == "PENDING_APPROVAL"
          and data(r)["resubmitted"] is True, r.text[:150])
    detail = data(api("get", f"/purchase-requisitions/{pr['id']}", anil))
    rounds = {s["round"] for s in detail["approvals"]}
    check("the first round's decision is kept", rounds == {1, 2}
          and any(s["decision"] == "RETURNED" for s in detail["approvals"]), str(detail["approvals"]))

    # ------------------------------------------------------------------
    banner("4. Approved, and on the procurement desk")
    r = api("post", f"/purchase-requisitions/{pr['id']}/decision", priya,
            json={"decision": "APPROVE", "remarks": "Go ahead.", "budget_code": f"OPEX-{TAG}",
                  "budget_available": 150000, "budgeted": True})
    check("the manager approves", ok(r) and data(r)["status"] == "APPROVED"
          and data(r)["procurement_status"] == "AWAITING_PROCUREMENT", r.text[:150])
    check("and records the budget it is charged to", ok(r) and data(r)["budget_code"] == f"OPEX-{TAG}"
          and data(r)["budget_available"] == 150000 and data(r)["budgeted"] is True, r.text[:200])
    mine = api("get", "/purchase-requisitions/mine", anil).json()
    check("the requester's page counts it as with procurement", mine["summary"]["with_procurement"] >= 1, str(mine.get("summary")))

    r = api("get", "/procurement/requisitions", priya)
    check("a manager without procurement access cannot open the desk", r.status_code == 403, f"{r.status_code}")
    desk = api("get", "/procurement/requisitions", admin, params={"search": pr["pr_number"]}).json()
    check("it is on the procurement desk", any(p["id"] == pr["id"] for p in desk["data"]), str(desk)[:200])
    check("the desk counts it as new", desk["kpis"]["new"] >= 1, str(desk["kpis"]))

    admin_id = data(api("get", "/auth/me", admin))["user_id"]
    r = api("post", f"/procurement/requisitions/{pr['id']}/assign", admin, json={"user_id": admin_id})
    check("procurement assigns it", ok(r) and data(r)["procurement_status"] == "ASSIGNED", r.text[:150])
    r = api("post", f"/procurement/requisitions/{pr['id']}/notes", admin, json={"note": "Ask three vendors."})
    check("an internal note is kept", ok(r) and data(r)["internal_notes"][-1]["text"] == "Ask three vendors.")

    line_a = next(i for i in data(r)["items"] if i["sku"] == f"TL-{TAG}")
    line_b = next(i for i in data(r)["items"] if i["sku"] == f"TM-{TAG}")
    r = api("put", f"/procurement/requisitions/{pr['id']}/items/{line_b['id']}/quantity", admin, json={"quantity": 4})
    check("a quantity change needs a reason", r.status_code == 400, f"{r.status_code}")
    r = api("put", f"/procurement/requisitions/{pr['id']}/items/{line_b['id']}/quantity", admin,
            json={"quantity": 4, "reason": "Pack of four is cheaper."})
    changed = next(i for i in data(r)["items"] if i["id"] == line_b["id"])
    check("procurement's quantity sits beside the employee's", changed["quantity"] == 3
          and changed["approved_quantity"] == 4, str(changed))

    # ------------------------------------------------------------------
    banner("5. Three vendors in the master")
    vendor_ids = []
    for n in range(3):
        docs = {"gst": upload(admin, "gst.pdf"), "pan": upload(admin, "pan.pdf"), "bank": upload(admin, "bank.pdf")}
        r = api("post", "/vendors", admin, json={
            "name": f"Test Vendor {n + 1} {TAG}", "vendor_type": "Distributor", "contact_person": "Ravi",
            "mobile": "9876543210", "email": f"vendor{n}.{TAG.lower()}@mailinator.com", "city": "Delhi",
            "state": "Delhi", "pin_code": "110001", "payment_terms": "30 days", "documents": docs,
        })
        check(f"vendor {n + 1} added", ok(r) and data(r)["vendor_code"].startswith("VEN-"), r.text[:150])
        vendor_ids.append(data(r)["id"])
        made["vendors"].append(data(r)["id"])

    r = api("post", f"/procurement/requisitions/{pr['id']}/collection", admin,
            json={"item_vendors": {str(line_a["id"]): [vendor_ids[0]]}})
    check("an unapproved vendor cannot be asked to quote", r.status_code == 409, r.text[:150])

    for vid in vendor_ids:
        r1 = api("post", f"/vendors/{vid}/action", admin, json={"action": "VERIFY"})
        r2 = api("post", f"/vendors/{vid}/action", admin, json={"action": "APPROVE"})
        check(f"vendor {vid} verified and approved", ok(r1) and ok(r2) and data(r2)["status"] == "APPROVED", r2.text[:150])

    # ------------------------------------------------------------------
    banner("6. Quotations requested per line, from several vendors")
    r = api("post", f"/procurement/requisitions/{pr['id']}/collection", admin,
            json={"item_vendors": {"999999999": [vendor_ids[0]]}})
    check("a line from another requisition is refused", r.status_code == 400, r.text[:150])
    r = api("post", f"/procurement/requisitions/{pr['id']}/collection", admin, json={
        "item_vendors": {str(line_a["id"]): vendor_ids, str(line_b["id"]): vendor_ids[:2]},
        "notes": "Called all three for laptops, two for monitors.",
    })
    check("quotations requested, line by line", ok(r) and data(r)["procurement_status"] == "QUOTATION_COLLECTION",
          r.text[:150])
    items = {i["id"]: i for i in data(r)["items"]}
    check("each line keeps the vendors asked", sorted(items[line_a["id"]]["rfq_vendor_ids"]) == sorted(vendor_ids)
          and sorted(items[line_b["id"]]["rfq_vendor_ids"]) == sorted(vendor_ids[:2]), str(items))
    check("and the request is on record", data(r)["collections"][-1]["lines"], str(data(r)["collections"]))
    asked_before = len(data(r)["collections"])
    r = api("post", f"/procurement/requisitions/{pr['id']}/collection", admin,
            json={"item_vendors": {str(line_a["id"]): vendor_ids[:1]}})
    check("asking a vendor already asked again is refused", r.status_code == 400, r.text[:150])
    after = data(api("get", f"/procurement/requisitions/{pr['id']}", admin))["collections"]
    check("and records nothing", len(after) == asked_before, str(len(after)))

    # ------------------------------------------------------------------
    banner("7. Quotations entered by hand, and a revision")
    rates = [(9500, 1800, 5), (9000, 2100, 10), (9800, None, 3)]
    quotes = []
    for vid, (rate_a, rate_b, days) in zip(vendor_ids, rates):
        lines = [{"requisition_item_id": line_a["id"], "quantity": 2, "unit_rate": rate_a, "discount_type": "PERCENT",
                  "discount_value": 5, "gst_rate": 18, "freight": 200, "delivery_days": days}]
        if rate_b:
            lines.append({"requisition_item_id": line_b["id"], "quantity": 4, "unit_rate": rate_b, "gst_rate": 18,
                          "delivery_days": days, "compliance": "PARTIAL" if vid == vendor_ids[1] else "COMPLIANT",
                          "deviation": "Older model" if vid == vendor_ids[1] else None})
        r = api("post", "/vendor-quotations", admin, json={
            "requisition_id": pr["id"], "vendor_id": vid, "vendor_reference": f"Q-{vid}",
            "quotation_date": date.today().isoformat(), "valid_until": IN_A_WEEK, "delivery_days": days,
            "payment_terms": "30 days", "attachments": [upload(admin, "quote.pdf")], "items": lines,
        })
        check(f"quotation from vendor {vid} recorded", ok(r), r.text[:200])
        quotes.append(data(r))

    q = quotes[0]
    expected_a = (2 * 9500 * 0.95) * 1.18 + 200
    expected_b = (4 * 1800) * 1.18
    check("line and quotation totals worked out on the server",
          abs(q["grand_total"] - round(expected_a + expected_b, 2)) < 0.02, f"{q['grand_total']} vs {expected_a + expected_b}")

    r = api("post", "/vendor-quotations", admin, json={"requisition_id": pr["id"], "vendor_id": vendor_ids[0],
                                                      "attachments": [upload(admin)],
                                                      "items": [{"requisition_item_id": line_a["id"], "quantity": 2, "unit_rate": 1}]})
    check("a second original from the same vendor is refused", r.status_code == 409, r.text[:120])

    original = quotes[1]
    r = api("post", f"/vendor-quotations/{original['id']}/revise", admin, json={
        "vendor_reference": f"Q-{vendor_ids[1]}-R1", "valid_until": IN_A_WEEK, "delivery_days": 10, "payment_terms": "45 days",
        "attachments": [upload(admin, "revised.pdf")],
        "items": [
            {"requisition_item_id": line_a["id"], "quantity": 2, "unit_rate": 8800, "gst_rate": 18, "delivery_days": 10},
            {"requisition_item_id": line_b["id"], "quantity": 4, "unit_rate": 2000, "gst_rate": 18, "delivery_days": 10},
        ],
    })
    check("a revision is recorded", ok(r) and data(r)["version_label"] == "Revision 1", r.text[:150])
    revised = data(r)
    kept = data(api("get", f"/vendor-quotations/{original['id']}", admin))
    check("the original is kept as it was", kept["grand_total"] == original["grand_total"]
          and kept["is_latest"] is False and kept["status"] == "REVISED", str(kept)[:200])

    # ------------------------------------------------------------------
    banner("8. The comparison")
    comp = data(api("get", f"/procurement/requisitions/{pr['id']}/comparison", admin))
    check("three latest quotations compared", len(comp["columns"]) == 3, str(len(comp["columns"])))
    check("the lowest total is flagged", len([c for c in comp["columns"] if c["lowest_total"]]) == 1)
    row_b = next(r for r in comp["rows"] if r["requisition_item_id"] == line_b["id"])
    check("line by line, with procurement's quantity", row_b["quantity"] == 4 and row_b["requested_quantity"] == 3)
    check("the fastest delivery is flagged", any(c["fastest_delivery"] for c in comp["columns"]))

    # ------------------------------------------------------------------
    banner("9. POs created from the chosen quotations, with payment terms")
    laptops_from = quotes[2]     # not the cheapest - the fastest
    r = api("post", "/purchase-orders", admin, json={
        "quotation_id": laptops_from["id"], "requisition_item_ids": [line_a["id"]],
        "payment_term_type": "ADVANCE_CREDIT", "advance_percent": 30, "credit_days": 20, "payment_mode": "PDC",
    })
    check("credit days must be one of the offered ones", r.status_code == 400, r.text[:150])
    r = api("post", "/purchase-orders", admin, json={
        "quotation_id": laptops_from["id"], "requisition_item_ids": [line_b["id"]],
    })
    check("a line the vendor did not quote is refused", r.status_code == 400, r.text[:150])
    r = api("post", "/purchase-orders", deepa, json={"quotation_id": laptops_from["id"]})
    check("an employee cannot create a PO", r.status_code == 403, f"{r.status_code}")
    r = api("post", "/purchase-orders", admin, json={
        "quotation_id": laptops_from["id"], "requisition_item_ids": [line_a["id"]],
        "payment_term_type": "ADVANCE_CREDIT", "advance_percent": 30, "credit_days": 45, "payment_mode": "PDC",
    })
    check("the laptops PO is created as a draft", ok(r) and data(r)["status"] == "DRAFT"
          and data(r)["po_number"].startswith("PO-"), r.text[:200])
    po_a = data(r)
    source = data(api("get", f"/vendor-quotations/{laptops_from['id']}", admin))
    line = next(i for i in source["items"] if i["requisition_item_id"] == line_a["id"])
    check("its value is the quotation's, not retyped", abs(po_a["grand_total"] - line["total"]) < 0.01,
          f"{po_a['grand_total']} vs {line['total']}")
    check("the quotation is marked selected", source["status"] == "SELECTED", source["status"])
    check("the terms are worded on the PO", po_a["payment_terms"].startswith("30% advance, 70% after 45 days"),
          po_a["payment_terms"])
    sched = po_a["payments"]
    check("the schedule is an advance and a balance after 45 days", [p["label"] for p in sched] == ["Advance", "After 45 days"]
          and abs(sched[0]["amount"] - round(po_a["grand_total"] * 0.30, 2)) < 0.02, str(sched))
    check("and the instalments add up to the PO total", abs(sum(p["amount"] for p in sched) - po_a["grand_total"]) < 0.005,
          f"{sum(p['amount'] for p in sched)} vs {po_a['grand_total']}")

    r = api("post", "/purchase-orders", admin, json={"quotation_id": quotes[0]["id"], "requisition_item_ids": [line_a["id"]]})
    check("a line already on a PO cannot go on another", r.status_code == 409, r.text[:150])

    r = api("post", "/purchase-orders", admin, json={"quotation_id": quotes[0]["id"], "requisition_item_ids": [line_b["id"]]})
    check("the monitors go to another vendor - a split purchase", ok(r), r.text[:150])
    po_b = data(r)
    r = api("post", f"/purchase-orders/{po_b['id']}/action", admin, json={"action": "SUBMIT"})
    check("a PO cannot be submitted without payment terms", r.status_code == 400, r.text[:150])
    r = api("put", f"/purchase-orders/{po_b['id']}", admin, json={
        "payment_term_type": "CREDIT_100", "credit_days": 30, "payment_mode": "BANK_TRANSFER"})
    check("the terms are set on the draft", ok(r) and [p["label"] for p in data(r)["payments"]] == ["After 30 days"]
          and data(r)["payments"][0]["amount"] == data(r)["grand_total"], r.text[:200])

    ws = data(api("get", f"/procurement/requisitions/{pr['id']}", admin))
    check("the PR waits on PO approval", ws["procurement_status"] == "PO_APPROVAL_PENDING", ws["procurement_status"])
    check("both lines show as ordered", sorted(ws["ordered_item_ids"]) == sorted([line_a["id"], line_b["id"]]))

    # ------------------------------------------------------------------
    banner("10. The PO is approved by amount - and not by whoever created it")
    r = api("put", f"/purchase-orders/{po_a['id']}/payments/{po_a['payments'][0]['id']}",
            admin, json={"paid": True})
    check("nothing is paid before the PO is approved", r.status_code == 409, r.text[:150])

    r = api("post", f"/purchase-orders/{po_a['id']}/action", admin, json={"action": "SUBMIT"})
    check("submitted for approval", ok(r) and data(r)["status"] == "PENDING_APPROVAL", r.text[:150])
    po_a = data(api("get", f"/purchase-orders/{po_a['id']}", admin))
    check("under the first band it goes to the requester's manager", [s["step"] for s in po_a["approvals"]] == ["REPORTING_MANAGER"]
          and "Priya" in po_a["waiting_on"]["approver_label"], str(po_a["approvals"]))

    waiting = data(api("get", "/purchase-requisitions/approvals", priya))["purchase_orders"]
    check("it is in the manager's Waiting on me", any(p["id"] == po_a["id"] for p in waiting))
    r = api("get", f"/purchase-orders/{po_a['id']}", priya)
    check("the manager can open it without procurement access", ok(r) and data(r)["can_decide"] is True, r.text[:150])
    check("a colleague cannot", api("get", f"/purchase-orders/{po_a['id']}", deepa).status_code == 403)
    r = api("post", f"/purchase-orders/{po_a['id']}/decision", admin, json={"decision": "APPROVE"})
    check("the person who created it cannot approve it", r.status_code == 403, f"{r.status_code}")
    r = api("post", f"/purchase-orders/{po_a['id']}/decision", priya, json={"decision": "REJECT"})
    check("a rejection needs remarks", r.status_code == 400, f"{r.status_code}")
    r = api("post", f"/purchase-orders/{po_a['id']}/decision", priya,
            json={"decision": "REJECT", "remarks": "Confirm the warranty first."})
    check("rejected back to draft", ok(r) and data(r)["status"] == "DRAFT", r.text[:150])
    r = api("post", f"/purchase-orders/{po_a['id']}/action", admin, json={"action": "SUBMIT"})
    check("resubmitted", ok(r) and data(r)["status"] == "PENDING_APPROVAL", r.text[:150])
    r = api("post", f"/purchase-orders/{po_a['id']}/decision", priya, json={"decision": "APPROVE", "remarks": "OK."})
    check("approved by the manager", ok(r) and data(r)["status"] == "APPROVED", r.text[:150])
    po_a = data(r)
    check("both rounds are on record", {s["round"] for s in po_a["approvals"]} == {1, 2}, str(po_a["approvals"]))

    # ------------------------------------------------------------------
    banner("11. Paying it")
    advance = po_a["payments"][0]
    url = f"/purchase-orders/{po_a['id']}/payments/{advance['id']}"
    check("the instalments take the PO's payment mode", advance["mode"] == "PDC", str(advance))
    r = api("put", url, admin, json={"paid": True})
    check("a post-dated cheque needs its details before it is paid", r.status_code == 400, r.text[:150])
    r = api("put", url, admin, json={"cheque_number": "004512", "bank_name": "HDFC Bank",
                                    "cheque_date": IN_A_WEEK, "paid": True})
    paid = next(p for p in data(r)["payments"] if p["id"] == advance["id"]) if ok(r) else {}
    check("the advance is recorded paid by cheque", ok(r) and paid.get("status") == "PAID"
          and paid.get("cheque_number") == "004512" and paid.get("paid_by_name"), r.text[:200])
    check("an employee cannot record payments",
          api("put", url, deepa, json={"paid": False}).status_code == 403)
    r = api("post", f"/purchase-orders/{po_a['id']}/action", admin, json={"action": "CANCEL", "remarks": "Test"})
    check("a PO with money paid cannot be cancelled", r.status_code == 409, r.text[:150])
    for action, status in (("SEND", "SENT"), ("ACKNOWLEDGE", "ACKNOWLEDGED"), ("CLOSE", "CLOSED")):
        r = api("post", f"/purchase-orders/{po_a['id']}/action", admin, json={"action": action})
        check(f"marked {status.lower()} by hand", ok(r) and data(r)["status"] == status, r.text[:150])

    for token, who in ((admin, "the desk"), (priya, "the approver")):
        pdf = requests.get(f"{BASE}/purchase-orders/{po_a['id']}/pdf", headers={"Authorization": f"Bearer {token}"}, timeout=60)
        check(f"the PO prints as a PDF for {who}", pdf.status_code == 200 and pdf.content[:4] == b"%PDF", f"{pdf.status_code}")

    api("post", f"/purchase-orders/{po_b['id']}/action", admin, json={"action": "SUBMIT"})
    r = api("post", f"/purchase-orders/{po_b['id']}/decision", priya, json={"decision": "APPROVE"})
    check("the monitors PO is approved too", ok(r) and data(r)["status"] == "APPROVED", r.text[:150])
    ws = data(api("get", f"/procurement/requisitions/{pr['id']}", admin))
    check("the PR shows PO generated once every line is on an approved PO", ws["procurement_status"] == "PO_GENERATED",
          ws["procurement_status"])

    # ------------------------------------------------------------------
    banner("12. The amount bands on a large PO, and a rejected PR")
    big_line = {"product_name": f"Test Server {TAG}", "uom": "Nos", "quantity": 1}
    r = api("post", "/purchase-requisitions", anil, json=pr_payload([big_line], submit=True))
    big_pr = data(r)
    made["prs"].append(big_pr["id"])
    check("a large need still only needs the manager", ok(r) and len(data(api("get", f"/purchase-requisitions/{big_pr['id']}", anil))["approvals"]) == 1)
    api("post", f"/purchase-requisitions/{big_pr['id']}/decision", priya, json={"decision": "APPROVE"})
    big_item = data(api("get", f"/procurement/requisitions/{big_pr['id']}", admin))["items"][0]
    r = api("post", "/vendor-quotations", admin, json={
        "requisition_id": big_pr["id"], "vendor_id": vendor_ids[0], "valid_until": IN_A_WEEK,
        "attachments": [upload(admin)], "items": [{"requisition_item_id": big_item["id"], "quantity": 1,
                                                   "unit_rate": 600000, "gst_rate": 18}]})
    r = api("post", "/purchase-orders", admin, json={"quotation_id": data(r)["id"], "payment_term_type": "ADVANCE_100",
                                                     "payment_mode": "BANK_TRANSFER"})
    big_po = data(r)
    big_quote_id = big_po["quotation_id"]
    from app.database.postgres import SessionLocal as _Session
    from app.services.procurement import masters as _masters

    _db = _Session()
    bands = _masters.steps_for_amount(big_po["grand_total"], _db)
    _db.close()
    check("above the top band: manager, department head, finance, director",
          bands == ["REPORTING_MANAGER", "DEPARTMENT_HEAD", "FINANCE", "DIRECTOR"], str(bands))
    r = api("post", f"/purchase-orders/{big_po['id']}/action", admin, json={"action": "SUBMIT"})
    if r.status_code == 409:
        # A step nobody holds falls to the super admins - but the only one
        # here created the PO, so it could never be decided. Refused up front.
        check("a step only its creator could decide is refused, saying what to set up",
              "Nobody can approve" in r.text, r.text[:200])
        check("and the PO stays a draft", data(api("get", f"/purchase-orders/{big_po['id']}", admin))["status"] == "DRAFT")
    else:
        chain = [s["step"] for s in data(api("get", f"/purchase-orders/{big_po['id']}", admin))["approvals"]]
        check("submitted, starting with the manager", ok(r) and chain[:1] == ["REPORTING_MANAGER"], str(chain))
    r = api("post", f"/purchase-orders/{big_po['id']}/action", admin, json={"action": "CANCEL", "remarks": "Not budgeted."})
    check("cancelled", ok(r) and data(r)["status"] == "CANCELLED", r.text[:150])
    ws = data(api("get", f"/procurement/requisitions/{big_pr['id']}", admin))
    check("its lines go back to being sourced", ws["procurement_status"] == "COMPARISON_IN_PROGRESS"
          and not ws["ordered_item_ids"], ws["procurement_status"])
    freed = data(api("get", f"/vendor-quotations/{big_quote_id}", admin))
    check("and its quotation is no longer marked selected", freed["status"] == "RECEIVED", freed["status"])
    r = api("post", "/purchase-orders", admin, json={"quotation_id": big_quote_id, "payment_term_type": "ADVANCE_100",
                                                     "payment_mode": "BANK_TRANSFER"})
    check("so a new PO can be made from it", ok(r), r.text[:150])
    if ok(r):
        api("post", f"/purchase-orders/{data(r)['id']}/action", admin, json={"action": "CANCEL", "remarks": "Test."})

    r = api("post", "/purchase-requisitions", anil, json=pr_payload([LINE_A], submit=True))
    rej = data(r)
    made["prs"].append(rej["id"])
    r = api("post", f"/purchase-requisitions/{rej['id']}/decision", priya,
            json={"decision": "REJECT", "remarks": "Not budgeted this quarter."})
    check("the manager rejects a PR", ok(r) and data(r)["status"] == "REJECTED", r.text[:150])
    history = {h["id"]: h for h in data(api("get", "/purchase-requisitions/approvals", priya))["decided"]}
    check("the manager's history shows the approval and the rejection",
          history.get(pr["id"], {}).get("my_decision") == "APPROVED"
          and history.get(rej["id"], {}).get("my_remarks") == "Not budgeted this quarter.")

    # ------------------------------------------------------------------
    banner("13. Procurement raises a requisition directly")
    r = api("post", "/procurement/requisitions", anil, json=pr_payload([LINE_B]))
    check("an employee cannot", r.status_code == 403, f"{r.status_code}")
    r = api("post", "/procurement/requisitions", admin, json=pr_payload([LINE_B]))
    check("it lands on the desk, approved and assigned to whoever raised it", ok(r)
          and data(r)["status"] == "APPROVED" and data(r)["procurement_status"] == "ASSIGNED"
          and data(r)["assigned_to"] == admin_id, r.text[:200])
    if ok(r):
        made["prs"].append(data(r)["id"])
        trail = {a["action"] for a in data(r)["activity"]}
        check("and its history says so", "PR Raised By Procurement" in trail, str(trail))

    deepa_id = data(api("get", "/auth/me", deepa))["user_id"]
    people = data(api("get", "/procurement/requesters", admin, params={"search": "Deepa"}))
    check("the desk can pick who a requisition is for", any(p["id"] == deepa_id for p in people), str(people)[:150])
    check("an employee cannot list them", api("get", "/procurement/requesters", anil).status_code == 403)
    r = api("post", "/procurement/requisitions", admin, json=pr_payload(
        [{**LINE_B, "tax_percent": 18, "est_unit_price": 2000, "vendor_ids": [vendor_ids[0], vendor_ids[1]]}],
        requested_for=deepa_id))
    check("raised for someone else, with GST and two vendors on the line", ok(r)
          and data(r)["requested_by"] == deepa_id and data(r)["procurement_status"] == "QUOTATION_COLLECTION", r.text[:200])
    if ok(r):
        made["prs"].append(data(r)["id"])
        line = data(r)["items"][0]
        check("procurement's estimated price is kept, with GST in the total", line["est_unit_price"] == 2000
              and abs(data(r)["estimated_total"] - 3 * 2000 * 1.18) < 0.01, str(line))
        check("the line keeps its GST and the vendors asked", line["tax_percent"] == 18
              and sorted(line["rfq_vendor_ids"]) == sorted(vendor_ids[:2]), str(line))
        check("the person it is for can open it", ok(api("get", f"/purchase-requisitions/{data(r)['id']}", deepa)))
    r = api("post", "/procurement/requisitions", admin, json=pr_payload([{**LINE_B, "vendor_ids": [999999999]}]))
    check("an unknown vendor is refused before anything is saved", r.status_code == 400, r.text[:150])

    # ------------------------------------------------------------------
    banner("14. The sales order picker shows only orders the person may see")
    everyone = {o["id"] for o in data(api("get", "/purchase-requisitions/sales-orders", admin))}
    his = {o["id"] for o in data(api("get", "/purchase-requisitions/sales-orders", anil))}
    check("he sees no more than the super admin", his <= everyone)
    hidden = sorted(everyone - his)
    if hidden:
        r = api("post", "/purchase-requisitions", anil, json=pr_payload([LINE_A], purchase_type="SALES_ORDER",
                                                                      sales_order_id=hidden[0]))
        check("and cannot link an order he may not see", r.status_code == 403, f"{r.status_code} {r.text[:120]}")
        if ok(r):
            made["prs"].append(data(r)["id"])
    else:
        print("  (no order outside his view among the latest 20 - link check skipped)")

    # ------------------------------------------------------------------
    banner("14a. Sales orders are for the sales chart only")
    # An employee outside the sales chart: no role, reporting to Priya.
    from app.database.postgres import SessionLocal
    from app.models.user import User
    from app.services.password_service import PasswordService

    db = SessionLocal()
    priya_id = data(api("get", "/auth/me", priya))["user_id"]
    clerk = User(first_name="Test", last_name=f"Clerk {TAG}", email=f"clerk.{TAG.lower()}@mailinator.com",
                 password=PasswordService.hash_password(TEAM_PASSWORD), employee_id=f"TC-{TAG}",
                 is_active=True, is_super_admin=False, must_change_password=False, reports_to_id=priya_id)
    db.add(clerk)
    db.commit()
    made["users"].append(str(clerk.id))
    db.close()
    clerk_token = login(f"clerk.{TAG.lower()}@mailinator.com", TEAM_PASSWORD)

    types = [t["value"] for t in data(api("get", "/purchase-requisitions/meta", clerk_token))["purchase_types"]]
    check("someone outside the sales chart is not offered the sales-order type", "SALES_ORDER" not in types, str(types))
    check("but the sales team is",
          "SALES_ORDER" in [t["value"] for t in data(api("get", "/purchase-requisitions/meta", anil))["purchase_types"]])
    check("they cannot list sales orders", api("get", "/purchase-requisitions/sales-orders", clerk_token).status_code == 403)
    r = api("post", "/purchase-requisitions", clerk_token, json=pr_payload([LINE_A], purchase_type="SALES_ORDER"))
    check("nor raise a requisition against one", r.status_code == 403, r.text[:150])
    r = api("post", "/purchase-requisitions", clerk_token, json=pr_payload([LINE_A], submit=True))
    check("they raise an ordinary requisition, which goes to their manager", ok(r)
          and "Priya" in (data(r)["waiting_on"] or {}).get("approver_label", ""), r.text[:200])
    if ok(r):
        made["prs"].append(data(r)["id"])
        clerk_pr = data(r)
        check("and see only their own on My Requisitions",
              [x["id"] for x in api("get", "/purchase-requisitions/mine", clerk_token).json()["data"]] == [clerk_pr["id"]])
        check("not someone else's", api("get", f"/purchase-requisitions/{pr['id']}", clerk_token).status_code == 403)

    # ------------------------------------------------------------------
    banner("14b. + Log Activity")
    r = api("post", f"/purchase-requisitions/{pr['id']}/activity", anil, json={"activity": "Call"})
    check("an activity needs remarks", r.status_code == 400, r.text[:150])
    r = api("post", f"/purchase-requisitions/{pr['id']}/activity", anil, json={"activity": "Gossip", "remarks": "x"})
    check("and one of the listed kinds", r.status_code == 400, r.text[:150])
    r = api("post", f"/purchase-requisitions/{pr['id']}/activity", anil,
            json={"activity": "Call", "remarks": "Asked procurement for an update."})
    check("the requester logs a call on their PR", ok(r) and any(
        a["action"] == "Call" and a["remarks"] == "Asked procurement for an update." and "Anil" in (a["actor_name"] or "")
        for a in data(r)["activity"]), r.text[:200])
    check("someone who cannot open the PR cannot log on it",
          api("post", f"/purchase-requisitions/{pr['id']}/activity", clerk_token,
              json={"activity": "Note", "remarks": "x"}).status_code == 403)
    r = api("post", f"/purchase-orders/{po_a['id']}/activity", priya, json={"activity": "Note", "remarks": "Checked the price."})
    check("an approver logs a note on the PO", ok(r) and data(r)["activity"][0]["action"] == "Note", r.text[:150])
    check("an outsider cannot log on the PO",
          api("post", f"/purchase-orders/{po_a['id']}/activity", deepa, json={"activity": "Note", "remarks": "x"}).status_code == 403)

    # ------------------------------------------------------------------
    banner("15. The desk KPIs count this run's records")
    kpi = data(api("get", "/procurement/analytics?days=30", admin))
    check("PO spend counts the two live POs", kpi["po_spend"]["count"] - kpi_before["po_spend"]["count"] == 2,
          f"{kpi['po_spend']} vs {kpi_before['po_spend']}")
    check("PO approval cycle measured for both", kpi["po_approval_cycle"]["basis"] - kpi_before["po_approval_cycle"]["basis"] == 2,
          str(kpi["po_approval_cycle"]))
    check("both POs count as competitive - three vendors quoted",
          kpi["competitive_rate"]["basis"] - kpi_before["competitive_rate"]["basis"] == 2, str(kpi["competitive_rate"]))
    check("sourcing cycle measured", kpi["sourcing_cycle"]["basis"] > kpi_before["sourcing_cycle"]["basis"],
          str(kpi["sourcing_cycle"]))
    r = api("get", "/procurement/requisitions?procurement_status=po_generated", admin)
    check("the PO Raised tab lists the PR", any(row["id"] == pr["id"] for row in data(r)), r.text[:150])
    check("an employee without procurement access is refused the KPIs",
          api("get", "/procurement/analytics", anil).status_code == 403)

    # ------------------------------------------------------------------
    banner("16. Stock and the audit trail")
    check("inventory did not move", stock_counts() == before_stock, f"{stock_counts()} vs {before_stock}")
    trail = {a["action"] for a in data(api("get", f"/purchase-requisitions/{pr['id']}", admin))["activity"]}
    for action in ("PR Created", "PR Submitted", "PR Returned For Correction", "PR Resubmitted", "PR Approved",
                   "PR Assigned To Procurement", "Internal Note Added", "Procurement Quantity Changed",
                   "Quotations Requested", "Vendor Quotation Recorded", "Quotation Revision 1 Recorded",
                   "Purchase Order Created", "Purchase Order Submitted For Approval", "Purchase Order Rejected",
                   "Purchase Order Approved", "Payment Marked Paid", "Purchase Order Closed"):
        check(f"audited: {action}", action in trail)

except Exception as exc:  # noqa: BLE001 - the report below still has to print
    import traceback
    traceback.print_exc()
    failed.append(f"the run stopped - {exc}")
    print(f"\n  STOPPED  {exc}")

finally:
    # PROCUREMENT_TEST_HOLD=<seconds> keeps the run's records for that long
    # before clearing them, so the screens can be looked at with real data.
    hold = int(os.environ.get("PROCUREMENT_TEST_HOLD", "0") or 0)
    if hold > 0:
        print(f"\nHolding the records for {hold}s before clearing up: requisitions {made['prs']}", flush=True)
        time.sleep(hold)

    banner("17. Clearing up")
    try:
        from sqlalchemy import text

        from app.database.postgres import SessionLocal

        db = SessionLocal()
        ids = made["prs"] or [-1]
        for sql in (
            "DELETE FROM notifications WHERE module = 'purchase_order' AND entity_id IN (SELECT id FROM purchase_order WHERE requisition_id = ANY(:ids))",
            "DELETE FROM purchase_order_payment WHERE purchase_order_id IN (SELECT id FROM purchase_order WHERE requisition_id = ANY(:ids))",
            "DELETE FROM purchase_order_approval WHERE purchase_order_id IN (SELECT id FROM purchase_order WHERE requisition_id = ANY(:ids))",
            "DELETE FROM purchase_order_item WHERE purchase_order_id IN (SELECT id FROM purchase_order WHERE requisition_id = ANY(:ids))",
            "DELETE FROM purchase_order WHERE requisition_id = ANY(:ids)",
            "DELETE FROM vendor_quotation_item WHERE quotation_id IN (SELECT id FROM vendor_quotation WHERE requisition_id = ANY(:ids))",
            "UPDATE vendor_quotation SET root_id = NULL WHERE requisition_id = ANY(:ids)",
            "DELETE FROM vendor_quotation WHERE requisition_id = ANY(:ids)",
            "DELETE FROM quotation_collection WHERE requisition_id = ANY(:ids)",
            "DELETE FROM purchase_requisition_approval WHERE requisition_id = ANY(:ids)",
            "DELETE FROM purchase_requisition_item WHERE requisition_id = ANY(:ids)",
            "DELETE FROM procurement_activity WHERE requisition_id = ANY(:ids)",
            "DELETE FROM notifications WHERE module = 'purchase_requisition' AND entity_id = ANY(:ids)",
            "DELETE FROM purchase_requisition WHERE id = ANY(:ids)",
        ):
            db.execute(text(sql), {"ids": ids})
        if made["users"]:
            db.execute(text("DELETE FROM users WHERE id = ANY(CAST(:u AS uuid[]))"), {"u": made["users"]})
        if made["vendors"]:
            db.execute(text("DELETE FROM procurement_activity WHERE entity_type = 'vendor' AND entity_id = ANY(:v)"), {"v": made["vendors"]})
            db.execute(text("DELETE FROM vendor WHERE id = ANY(:v)"), {"v": made["vendors"]})
        db.commit()
        db.close()
        print(f"   removed {len(made['prs'])} requisitions, {len(made['vendors'])} vendors and what hung off them")

    except Exception as exc:  # noqa: BLE001
        print("   cleanup problem:", exc)

    print(f"\n{len(passed)} passed, {len(failed)} failed")
    for name in failed:
        print("  FAILED", name)
    sys.exit(1 if failed else 0)
