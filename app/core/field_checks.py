"""Shape checks for the fields a person types.

The forms mark these required and check them in the browser, which stops
an honest mistake and nothing else: the API is reachable without the form,
the Excel import does not go through it, and a browser check is a courtesy
rather than a rule. A seven-digit phone number or an address with no "@"
in it reaching the database is a customer nobody can call back.

Every check here says what is wrong with the value rather than that it is
invalid, because the person reading the message is the person who typed
it and "Invalid input" tells them nothing about which field or why.

Only the shape is checked. Whether a real person answers that number is
not something a regular expression can know.
"""

import re

from fastapi import HTTPException

#: Deliberately loose. The strict grammar for an address accepts things no
#: mail server does and rejects things that work, so this catches the
#: typos - a missing @, a missing dot, a stray space - and leaves the rest
#: to the first email that bounces.
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

#: Indian mobile numbers: ten digits opening 6, 7, 8 or 9. The country
#: code and the separators people type are stripped before this is tried.
MOBILE = re.compile(r"^[6-9]\d{9}$")

#: 15 characters: two state digits, a PAN, an entity digit, Z, a checksum.
GSTIN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]$")

#: Five letters, four digits, one letter.
PAN = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")

#: Six digits, never opening with a zero.
PIN = re.compile(r"^[1-9]\d{5}$")


def digits(value: str | None) -> str:
    """A phone number as the digits that matter.

    Drops the separators people type - spaces, dashes, brackets - and the
    country code, written any of the three usual ways. "+91 98765-43210",
    "0091 9876543210" and "09876543210" are one number.
    """

    raw = re.sub(r"\D", "", str(value or ""))

    for prefix in ("0091", "91", "0"):
        if raw.startswith(prefix) and len(raw) == len(prefix) + 10:
            return raw[len(prefix):]

    return raw


def _fail(message: str) -> None:
    raise HTTPException(status_code=400, detail=message)


def check_email(value: str | None, *, field: str = "Email address", required: bool = False) -> str:
    text = str(value or "").strip()

    if not text:
        if required:
            _fail(f"{field} is required.")
        return ""

    if not EMAIL.match(text):
        _fail(f"{field} “{text}” is not a valid email address.")

    return text


def check_mobile(value: str | None, *, field: str = "Mobile number", required: bool = False) -> str:
    text = str(value or "").strip()

    if not text:
        if required:
            _fail(f"{field} is required.")
        return ""

    cleaned = digits(text)

    if not cleaned:
        _fail(f"{field} “{text}” has no digits in it.")

    if len(cleaned) != 10:
        _fail(
            f"{field} “{text}” has {len(cleaned)} digits; "
            "an Indian mobile number has 10."
        )

    if not MOBILE.match(cleaned):
        _fail(
            f"{field} “{text}” starts with {cleaned[0]}; "
            "an Indian mobile number starts with 6, 7, 8 or 9."
        )

    return text


def check_gstin(value: str | None, *, field: str = "GSTIN") -> str:
    text = str(value or "").strip().upper()

    if not text:
        return ""

    if len(text) != 15:
        _fail(f"{field} “{text}” is {len(text)} characters; a GSTIN has 15.")

    if not GSTIN.match(text):
        _fail(
            f"{field} “{text}” is not in the right shape — two state "
            "digits, a PAN, an entity digit, Z, then a checksum character."
        )

    return text


def check_pan(value: str | None, *, field: str = "PAN") -> str:
    text = str(value or "").strip().upper()

    if not text:
        return ""

    if not PAN.match(text):
        _fail(
            f"{field} “{text}” is not in the right shape — five "
            "letters, four digits, then a letter."
        )

    return text


def check_pin(value: str | None, *, field: str = "PIN code") -> str:
    text = str(value or "").strip()

    if not text:
        return ""

    if not PIN.match(text):
        _fail(f"{field} “{text}” is not a six-digit Indian PIN code.")

    return text


def check_required(value: str | None, field: str) -> str:
    text = str(value or "").strip()

    if not text:
        _fail(f"{field} is required.")

    return text
