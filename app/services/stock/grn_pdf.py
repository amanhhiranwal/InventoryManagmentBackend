"""The GRN as a PDF, laid out as the warehouse's GRN SHEET workbook.

Same 8 columns at the workbook's widths; the letterhead (light blue, logo
at its right); GOODS RECEIVED NOTE (GRN) on its grey bar; the five
label/value rows; the item table (header shaded, 8 rows at least); and the
received / inspected sign-off rows. The store the goods came into follows
the receipt date, as the workbook has no row of its own for it.
"""

from app.services.stock.sheet_pdf import ExcelGrid, safe_filename, sheet_date, sheet_qty, text

#: Column widths in Excel units, from GRN SHEET (A..H).
WIDTHS = [5.43, 31.57, 8.43, 8.71, 8.71, 8.71, 8.71, 18.43]
LETTERHEAD_FILL = "#D9E1F2"
TITLE_FILL = "#D0CFCF"
HEADER_FILL = "#DEEBF7"
ITEM_ROWS = 8


def _letterhead_markup(head: dict) -> str:
    """Three lines, as the workbook has them: the name; the address with the
    CIN after it; the GST number with phone | email after it."""

    contact = " | ".join(p for p in (head.get("phone_number"), head.get("email")) if p)
    lines = (
        str(head.get("company_name") or "").upper(),
        " ".join(p for p in (head.get("address"), f"CIN : {head['cin_number']}" if head.get("cin_number") else "") if p),
        " ".join(p for p in (f"GST No : {head['gst_number']}" if head.get("gst_number") else "", contact) if p),
    )
    return "<br/>".join(text(x) for x in lines if x)


def render(grn: dict, letterhead: dict) -> bytes:
    g = ExcelGrid(WIDTHS)

    g.row([g.letterhead(_letterhead_markup(letterhead), size=9, family="sans", color="#000000", height=40.9)]
          + [None] * 7, height=40.9, spans=[(0, 7)], fills={(0, 7): LETTERHEAD_FILL}, valign="top")
    g.row([{"value": "GOODS RECEIVED NOTE (GRN)", "size": 10, "bold": True, "align": "center"}] + [None] * 7,
          spans=[(0, 7)], fills={(0, 7): TITLE_FILL})

    received = sheet_date(grn.get("receipt_date"))
    if grn.get("warehouse_name"):
        received += f" at {grn['warehouse_name']}"
    against = (("Returned Against Dispatch:", grn["do_number"]) if grn.get("do_number")
               else ("Purchase Order (PO) Number:", grn.get("po_number") or "N/A"))
    note = "N/A"
    if grn.get("delivery_note_no"):
        note = grn["delivery_note_no"] + (f"  /  {sheet_date(grn['delivery_note_date'])}" if grn.get("delivery_note_date") else "")
    supplier = "\n".join(p for p in (grn.get("supplier_name"), grn.get("supplier_address")) if p) or "N/A"

    for caption, value, height, valign in (
        ("GRN Number:", grn.get("grn_number"), None, "middle"),
        ("Date of Receipt:", received, None, "middle"),
        (*against, None, "middle"),
        ("Supplier Name & Address:", supplier, 37.5, "top"),
        ("Delivery Note / Invoice Number & Date:", note, None, "middle"),
    ):
        g.row([{"value": caption, "size": 9, "bold": True, "align": "right"}, None, None,
               {"value": value or "", "size": 11}, None, None, None, None],
              height=height, spans=[(0, 2), (3, 7)], valign=valign)

    head = {"size": 7, "bold": True, "align": "center"}
    g.row([{"value": "S.No.", "size": 10}]
          + [{"value": v, **head} for v in ("Item Code / SKU", "Description of Goods", "Qty Ordered", "Qty Received",
                                            "Qty Accepted", "Qty Rejected", "Remarks / Reason for Rejection")],
          height=27, fills={0: LETTERHEAD_FILL, (1, 7): HEADER_FILL})

    body = {"size": 7, "bold": True, "align": "center"}
    lines = grn.get("lines") or []
    for line in lines:
        description = line.get("description") or ""
        if line.get("serials"):
            description += "\nS/N: " + ", ".join(line["serials"])
        rejected = None
        if line.get("qty_rejected") is not None:
            rejected = float(line["qty_rejected"]) + float(line.get("qty_damaged") or 0)
        remarks = line.get("remarks") or ""
        if line.get("qty_damaged"):
            remarks += ("\n" if remarks else "") + f"({sheet_qty(line['qty_damaged'])} damaged)"
        g.row([{"value": line.get("line_no"), "size": 11, "align": "center"},
               {"value": line.get("sku") or "N/A", **body}, {"value": description, **body},
               {"value": sheet_qty(line.get("qty_ordered")), **body}, {"value": sheet_qty(line.get("qty_received")), **body},
               {"value": sheet_qty(line.get("qty_accepted")), **body}, {"value": sheet_qty(rejected), **body},
               {"value": remarks, **body}], height=None if len(description) > 40 else 27)
    for _ in range(max(0, ITEM_ROWS - len(lines))):
        g.row([None] * 8, height=15)

    for caption, value, height in (
        ("Received By:", grn.get("received_by_name") or "", 15),
        ("Signature:", "", 36),
        ("Inspected & Approved By:", grn.get("inspected_by_name") or "", 15),
        ("Signature:", "", 29.1),
        ("Date:", sheet_date(grn.get("posted_at")), 15.75),
    ):
        g.row([None, {"value": caption, "size": 9, "bold": True, "align": "right"},
               {"value": value, "size": 8, "bold": True, "align": "center"}, None, None, None, None, None],
              height=height, spans=[(2, 7)])

    return g.render(f"GRN {grn.get('grn_number')}")


def filename(grn: dict) -> str:
    return f"GRN-{safe_filename(grn.get('grn_number') or grn.get('id'))}.pdf"
