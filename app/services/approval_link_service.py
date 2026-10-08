"""One-click approve and reject, from the link in the email.

An approver gets a mail saying an order needs them. Opening the CRM,
signing in and finding it is three steps between them and a decision
they have already made, so the mail carries the two buttons.

That makes the link itself the authority, which is a real thing to give
away, so it is given away as narrowly as it can be:

  * Signed with the server's own secret, so a link cannot be composed by
    anybody who did not get the email.
  * Tied to one approval, one step of it, and one person. A link for the
    AVP's step cannot be used once it has moved to the CEO, and cannot
    be used by the CEO.
  * Single use. The step it was issued for has to still be waiting; the
    moment a decision lands the link is spent, so a forwarded mail
    approves nothing.
  * Short lived. Seven days, which is longer than an approval should sit
    and shorter than a mailbox is kept.
  * Recorded. The decision says it came from an email link, so a
    signature can be traced to the message that produced it.

What it does NOT do is sign anybody in. It decides that one approval and
nothing else; there is no session at the end of it.
"""

import hashlib
import hmac
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode

from app.core.config import settings

#: Long enough that nobody is chased by it, short enough that an old
#: mailbox is not a standing set of keys.
TTL_SECONDS = 7 * 24 * 60 * 60


def _secret() -> bytes:
    return (settings.JWT_SECRET_KEY or "").encode("utf-8")


def _b64(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _body(approval_id: int, user_id: str, step: int, decision: str, expires: int) -> str:
    return f"{approval_id}.{user_id}.{step}.{decision}.{expires}"


def make_token(
    approval_id: int,
    user_id: str,
    step: int,
    decision: str,
    now: int | None = None,
) -> str:
    """A link that decides one step of one approval, for one person."""

    expires = int(now or time.time()) + TTL_SECONDS
    body = _body(approval_id, user_id, step, decision, expires)
    signature = hmac.new(_secret(), body.encode("utf-8"), hashlib.sha256).digest()

    return f"{_b64(body.encode('utf-8'))}.{_b64(signature)}"


def read_token(token: str) -> dict | None:
    """What the token claims, or None if it is not ours or has expired.

    Compared in constant time, because a signature check that returns
    early tells somebody how much of their guess was right.
    """

    try:
        body_part, signature_part = str(token or "").split(".", 1)
        body = _unb64(body_part).decode("utf-8")
        signature = _unb64(signature_part)
    except Exception:  # noqa: BLE001 - anything malformed is simply not ours
        return None

    expected = hmac.new(_secret(), body.encode("utf-8"), hashlib.sha256).digest()

    if not hmac.compare_digest(signature, expected):
        return None

    try:
        approval_id, user_id, step, decision, expires = body.split(".")
        claim = {
            "approval_id": int(approval_id),
            "user_id": user_id,
            "step": int(step),
            "decision": decision,
            "expires": int(expires),
        }
    except Exception:  # noqa: BLE001
        return None

    if claim["expires"] < int(time.time()):
        return None

    if claim["decision"] not in ("approve", "reject"):
        return None

    return claim
