"""Looking an HSN up on the GST portal.

What the portal's search actually answers is "is this a real code, and
what is it called" - it returns `{"c": "85285900", "n": "OTHER"}`. It does
not return the tax rate. Rates come from the rate notifications, which
this endpoint does not serve, so the slab still comes from our own table
in app/core/gst.py. This fills in the description and confirms the code
exists.

It is also not a published API. It needs browser-like headers to answer at
all, and it drops the connection after a handful of calls in quick
succession - so every answer is cached for the life of the process and a
failure is swallowed rather than raised. Nothing here is allowed to stop
an invoice being raised: a lookup that cannot complete simply leaves the
description blank.
"""

import json
import logging
import urllib.request
from threading import Lock

logger = logging.getLogger(__name__)

SEARCH_URL = (
    "https://services.gst.gov.in/commonservices/hsn/search/qsearch"
    "?inputText={code}&selectedType=byCode&category=null"
)

#: The portal refuses a plain request, so it is asked the way its own page
#: asks. Nothing identifying is sent - the query is a commodity code.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://services.gst.gov.in/services/searchhsnsac",
}

#: code -> description, or None where the portal could not answer. Held for
#: the life of the process: a commodity code's description does not change
#: between restarts, and the portal will not take being asked twice.
_CACHE: dict[str, str | None] = {}
_LOCK = Lock()

TIMEOUT_SECONDS = 6


def describe(hsn: str | None) -> str | None:
    """What the portal calls this code, or None.

    None means "could not find out" - not "invalid". The two are worth
    keeping apart, because a lookup that timed out must not make a real
    code look wrong on a document.
    """

    code = "".join(ch for ch in str(hsn or "") if ch.isdigit())

    if not code:
        return None

    with _LOCK:
        if code in _CACHE:
            return _CACHE[code]

    description = _fetch(code)

    with _LOCK:
        _CACHE[code] = description

    return description


def _fetch(code: str) -> str | None:
    try:
        request = urllib.request.Request(SEARCH_URL.format(code=code), headers=HEADERS)

        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", errors="replace")

        # The portal answers a rejected request with an HTML page and a
        # 200, so the status alone does not tell us it worked.
        if not raw.lstrip().startswith("{"):
            logger.info("HSN lookup for %s was refused by the portal", code)
            return None

        rows = (json.loads(raw) or {}).get("data") or []

        for row in rows:
            if str(row.get("c") or "").strip() == code:
                return str(row.get("n") or "").strip() or None

        return str(rows[0].get("n") or "").strip() or None if rows else None
    except Exception:  # noqa: BLE001 - a lookup must never stop an invoice
        logger.info("HSN lookup for %s could not complete", code, exc_info=False)
        return None
