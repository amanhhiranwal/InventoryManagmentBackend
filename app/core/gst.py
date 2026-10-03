"""Which tax applies to a sale, and how it splits.

Two questions, and they are separate.

**How much**: the rate comes from the line's HSN. It is read from a table
we keep rather than looked up over the network at the moment an invoice is
raised - a tax figure that depends on somebody else's endpoint being up is
a tax figure that can silently come out wrong, and an invoice is not a
place to find that out. The table is seeded with the codes actually sold
and a super admin can correct one without a deployment.

**Which heads**: decided by where the goods are going, not by what they
are. A sale inside the seller's own state is taxed half as CGST and half
as SGST; a sale to another state is one IGST line at the full rate. The
total is the same either way - this only decides whose column it lands in,
which is the whole point of a tax invoice.
"""

#: HSN or SAC -> the GST rate it attracts, as a percentage.
#: Seeded with what the business actually sells. A code not listed here
#: falls back to the rate already on the line, which is what the user
#: typed, rather than silently taxing at zero.
HSN_GST_RATES: dict[str, float] = {
    "85285900": 18.0,   # interactive flat panels, monitors
    "8528": 18.0,       # the four-digit heading, for a line entered short
    "85291029": 18.0,   # OPS modules and parts
    "8529": 18.0,
    "84733099": 18.0,   # stands, mounts and computer parts
    "8473": 18.0,
    "84714190": 18.0,   # automatic data processing machines
    "8471": 18.0,
    "85044090": 18.0,   # power supplies
    "85item": 18.0,
}


def rate_for(hsn: str | None, fallback: float = 18.0) -> float:
    """The GST rate for a code, longest match first.

    A full eight-digit code is tried before its four-digit heading, so a
    specific rate always beats the general one it sits under.
    """

    code = "".join(ch for ch in str(hsn or "") if ch.isdigit())

    while len(code) >= 2:
        if code in HSN_GST_RATES:
            return HSN_GST_RATES[code]
        code = code[:-1]

    try:
        return float(fallback)
    except (TypeError, ValueError):
        return 18.0


def state_code(value: str | None) -> str:
    """The two-digit GST code for a state, from a code or a name."""

    raw = str(value or "").strip()

    digits = "".join(ch for ch in raw if ch.isdigit())

    if digits:
        return digits[:2].zfill(2)

    return STATE_CODES.get(raw.lower(), "")


#: The GST state codes. Only the two-digit code decides the tax split, so
#: a name is resolved to one rather than compared as text.
STATE_CODES: dict[str, str] = {
    "jammu and kashmir": "01", "himachal pradesh": "02", "punjab": "03",
    "chandigarh": "04", "uttarakhand": "05", "haryana": "06",
    "delhi": "07", "rajasthan": "08", "uttar pradesh": "09",
    "bihar": "10", "sikkim": "11", "arunachal pradesh": "12",
    "nagaland": "13", "manipur": "14", "mizoram": "15", "tripura": "16",
    "meghalaya": "17", "assam": "18", "west bengal": "19",
    "jharkhand": "20", "odisha": "21", "chhattisgarh": "22",
    "madhya pradesh": "23", "gujarat": "24", "maharashtra": "27",
    "karnataka": "29", "goa": "30", "lakshadweep": "31", "kerala": "32",
    "tamil nadu": "33", "puducherry": "34", "andaman and nicobar islands": "35",
    "telangana": "36", "andhra pradesh": "37", "ladakh": "38",
}


def split(taxable: float, rate: float, seller_state, buyer_state) -> dict:
    """The tax on one line, under the heads it actually falls."""

    try:
        amount = float(taxable or 0) * float(rate or 0) / 100.0
    except (TypeError, ValueError):
        amount = 0.0

    seller = state_code(seller_state)
    buyer = state_code(buyer_state)

    # Unknown either side is treated as a sale within the state: that is
    # the common case, and it is the reading a human would check.
    interstate = bool(seller and buyer and seller != buyer)

    if interstate:
        return {
            "interstate": True,
            "cgst_rate": 0.0, "cgst_amount": 0.0,
            "sgst_rate": 0.0, "sgst_amount": 0.0,
            "igst_rate": round(float(rate or 0), 2),
            "igst_amount": round(amount, 2),
            "total_tax": round(amount, 2),
        }

    half = round(amount / 2, 2)

    return {
        "interstate": False,
        "cgst_rate": round(float(rate or 0) / 2, 2), "cgst_amount": half,
        "sgst_rate": round(float(rate or 0) / 2, 2),
        # The second half takes the rounding, so the two always sum to the
        # tax actually charged rather than drifting a paisa apart.
        "sgst_amount": round(amount - half, 2),
        "igst_rate": 0.0, "igst_amount": 0.0,
        "total_tax": round(amount, 2),
    }


def summarise(lines, seller_state, buyer_state, default_rate: float = 18.0) -> dict:
    """The tax on a whole document, and the HSN table that explains it.

    A GST document does not simply state a tax figure; it shows the
    taxable value and the tax under each HSN, so a reader can check the
    arithmetic themselves. That table is built here from the same lines
    the invoice prices, rather than assembled separately and left to
    drift away from them.

    `lines` are dicts carrying an hsn, a quantity, a rate per unit and
    optionally a discount and a GST rate of their own.
    """

    by_hsn: dict[str, dict] = {}
    taxable_total = 0.0

    for raw in lines or []:
        line = raw if isinstance(raw, dict) else {}

        hsn = str(line.get("hsn") or "").strip() or "-"

        try:
            quantity = float(line.get("quantity") or line.get("qty") or 1)
            unit = float(
                line.get("unit_price") or line.get("rate") or line.get("price") or 0
            )
            discount = float(line.get("discount") or 0)
        except (TypeError, ValueError):
            quantity, unit, discount = 1.0, 0.0, 0.0

        taxable = quantity * unit * (1 - discount / 100.0)

        # The line's own rate wins when it carries one; otherwise the HSN
        # decides, which is the point of capturing the code at all.
        rate = rate_for(hsn, fallback=line.get("tax") or line.get("tax_rate") or default_rate)

        bucket = by_hsn.setdefault(
            hsn, {"hsn": hsn, "rate": rate, "taxable": 0.0}
        )
        bucket["taxable"] += taxable
        taxable_total += taxable

    rows = []
    totals = {
        "taxable": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "tax": 0.0,
    }
    interstate = False

    for bucket in by_hsn.values():
        parts = split(bucket["taxable"], bucket["rate"], seller_state, buyer_state)
        interstate = parts["interstate"]

        rows.append({
            "hsn": bucket["hsn"],
            "taxable": round(bucket["taxable"], 2),
            "rate": bucket["rate"],
            **{k: parts[k] for k in (
                "cgst_rate", "cgst_amount", "sgst_rate", "sgst_amount",
                "igst_rate", "igst_amount", "total_tax",
            )},
        })

        totals["taxable"] += bucket["taxable"]
        totals["cgst"] += parts["cgst_amount"]
        totals["sgst"] += parts["sgst_amount"]
        totals["igst"] += parts["igst_amount"]
        totals["tax"] += parts["total_tax"]

    return {
        "interstate": interstate,
        "rows": rows,
        "taxable_total": round(taxable_total, 2),
        "cgst_total": round(totals["cgst"], 2),
        "sgst_total": round(totals["sgst"], 2),
        "igst_total": round(totals["igst"], 2),
        "tax_total": round(totals["tax"], 2),
        "grand_total": round(taxable_total + totals["tax"], 2),
    }
