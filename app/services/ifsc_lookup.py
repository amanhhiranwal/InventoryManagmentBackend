"""The bank branch behind an IFSC, from Razorpay's public IFSC service.

https://ifsc.razorpay.com/<IFSC> answers with the bank, branch, address,
city, state and MICR of a branch, or 404 for a code no branch has. It
needs no key. The vendor form asks it so the bank name and branch are
filled from the code rather than typed by hand.

Failures are said, never hidden: a malformed code is 400, an unknown one
404, and the service being down or slow is 502 - the form then lets the
bank details be typed as before.
"""

import requests
from fastapi import HTTPException

from app.services.procurement.vendors import IFSC

SERVICE = "https://ifsc.razorpay.com/"
TIMEOUT_SECONDS = 8


def lookup(code: str) -> dict:
    ifsc = (code or "").strip().upper()

    if not IFSC.match(ifsc):
        raise HTTPException(
            status_code=400,
            detail=f"{ifsc or 'That'} is not an IFSC - four letters, 0, then six characters.",
        )

    try:
        answer = requests.get(SERVICE + ifsc, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail="The IFSC lookup could not be reached. Enter the bank details by hand.",
        ) from error

    if answer.status_code == 404:
        raise HTTPException(status_code=404, detail=f"No bank branch has the IFSC {ifsc}.")

    if answer.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"The IFSC lookup answered {answer.status_code}. Enter the bank details by hand.",
        )

    try:
        data = answer.json()
    except ValueError as error:
        raise HTTPException(
            status_code=502,
            detail="The IFSC lookup sent an answer that could not be read. Enter the bank details by hand.",
        ) from error

    return {
        "ifsc": data.get("IFSC") or ifsc,
        "bank": data.get("BANK") or "",
        "branch": data.get("BRANCH") or "",
        "address": data.get("ADDRESS") or "",
        "city": data.get("CITY") or "",
        "district": data.get("DISTRICT") or "",
        "state": data.get("STATE") or "",
        "micr": data.get("MICR") or "",
    }
