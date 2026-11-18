"""Checking a GSTIN.

The GST network's own taxpayer search is only open to GST Suvidha
Providers (GSPs) and their clients, with a client id, secret and an
allow-listed server; the free page on gst.gov.in sits behind a CAPTCHA.
So the check made here is the offline one every GSTIN carries: the
shape, a real state code, the PAN inside it and the check digit the GST
network computes. It catches typing mistakes and made-up numbers, but it
cannot say whether a registration is active or cancelled.

``live`` is where a GSP lookup goes once the business has an account:
it reports "not_configured" until then, so the screen can say plainly
that the registration status was not checked.
"""

from fastapi import HTTPException

from app.core.field_checks import check_gstin

#: State and union territory names by GST state code.
GST_STATES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "25": "Daman and Diu", "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra",
    "28": "Andhra Pradesh (old)", "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala",
    "33": "Tamil Nadu", "34": "Puducherry", "35": "Andaman and Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh", "97": "Other Territory", "99": "Centre Jurisdiction",
}


def verify(gstin: str | None) -> dict:
    """What can be known about this GSTIN without the GST network."""

    raw = str(gstin or "").strip().upper()
    if not raw:
        raise HTTPException(status_code=400, detail="Enter a GSTIN to check.")

    try:
        text = check_gstin(raw)
    except HTTPException as error:
        return {"gstin": raw, "valid": False, "reason": error.detail, "live": {"status": "not_checked"}}

    return {
        "gstin": text,
        "valid": True,
        "state_code": text[:2],
        "state": GST_STATES.get(text[:2]),
        "pan": text[2:12],
        "reason": "The format, state code and check digit are right.",
        "live": {
            "status": "not_configured",
            "detail": "Registration status (active or cancelled) needs a GST Suvidha Provider account.",
        },
    }
