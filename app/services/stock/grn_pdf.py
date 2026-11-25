"""The GRN as a PDF: the same sheet the Stock In page shows and prints.

Company letterhead, GOODS RECEIVED NOTE (GRN), number, receipt date, PO
(or the dispatch a return came back on), supplier, delivery note; the
store row; one row per product with ordered, received, accepted and
rejected; then received by, inspected by and the date it was posted.
"""

from app.services.stock.sheet_pdf import Sheet, letterhead_markup, safe_filename, sheet_date, sheet_qty

NUMBERS = ["left", "left", "left", "right", "right", "right", "right", "left"]


def render(grn: dict, letterhead: dict) -> bytes:
    # S.No | Item Code | Description | Ordered | Received | Accepted | Rejected | Remarks
    sheet = Sheet([6, 14, 24, 9.5, 9.5, 9.5, 9.5, 18])

    sheet.full(letterhead_markup(letterhead), align="center", markup=True)
    sheet.full("GOODS RECEIVED NOTE (GRN)", bold=True, align="center")

    if grn.get("do_number"):
        against = ("Returned Against Dispatch:", grn["do_number"])
    else:
        against = ("Purchase Order (PO) Number:", grn.get("po_number") or "N/A")

    note = "N/A"
    if grn.get("delivery_note_no"):
        note = grn["delivery_note_no"]
        if grn.get("delivery_note_date"):
            note += f"  /  {sheet_date(grn['delivery_note_date'])}"

    for caption, content in (
        ("GRN Number:", grn.get("grn_number")),
        ("Date of Receipt:", sheet_date(grn.get("receipt_date"))),
        against,
        ("Supplier Name & Address:", "\n".join(p for p in (grn.get("supplier_name"), grn.get("supplier_address")) if p)),
        ("Delivery Note / Invoice Number & Date:", note),
    ):
        sheet.row([caption, "", "", content or "", "", "", "", ""], spans=[(0, 2), (3, 7)], bold={0})

    sheet.row(["S.No.", "Location / Item Code", "Description of Goods", "Qty Ordered", "Qty Received",
               "Qty Accepted", "Qty Rejected", "Remarks / Reason for Rejection"], bold=True, shade=True, align=NUMBERS)
    store = (grn.get("warehouse_name") or "") + (f" ({grn['warehouse_code']})" if grn.get("warehouse_code") else "")
    sheet.row(["", store, "", "", "", "", "", grn.get("transport_remarks") or ""], spans=[(1, 2), (3, 6)], bold={1})

    for line in grn.get("lines") or []:
        description = line.get("description") or ""
        if line.get("serials"):
            description += "\nS/N: " + ", ".join(line["serials"])
        rejected = None
        if line.get("qty_rejected") is not None:
            rejected = float(line["qty_rejected"]) + float(line.get("qty_damaged") or 0)
        remarks = line.get("remarks") or ""
        if line.get("qty_damaged"):
            remarks += ("\n" if remarks else "") + f"({sheet_qty(line['qty_damaged'])} damaged)"
        sheet.row([line.get("line_no"), line.get("sku"), description, sheet_qty(line.get("qty_ordered")),
                   sheet_qty(line.get("qty_received")), sheet_qty(line.get("qty_accepted")), sheet_qty(rejected), remarks],
                  align=NUMBERS)

    for caption, content, tall in (
        ("Received By:", grn.get("received_by_name") or "", False),
        ("Signature:", "", True),
        ("Inspected & Approved By:", grn.get("inspected_by_name") or "", False),
        ("Signature:", "", True),
        ("Date:", sheet_date(grn.get("posted_at")), False),
    ):
        sheet.row(["", caption, content, "", "", "", "", ""], spans=[(2, 7)], bold={1}, height=24 if tall else None)

    return sheet.render(f"GRN {grn.get('grn_number')}")


def filename(grn: dict) -> str:
    return f"GRN-{safe_filename(grn.get('grn_number') or grn.get('id'))}.pdf"
