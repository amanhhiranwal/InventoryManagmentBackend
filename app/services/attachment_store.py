"""Where attached files live, and what is allowed to become one.

Separate from the route because two other things need it: the access
check, and the sending of an email that carries one. A stored file is
read in exactly one place so there is one answer to "where is it" and
one list of what may be stored.
"""

import re
from pathlib import Path

#: Alongside the other uploads, which in production is a docker volume
#: and so survives a deploy.
ATTACHMENT_DIR = Path(__file__).resolve().parents[2] / "uploads" / "attachments"

#: What a CRM record is allowed to carry. Deliberately short: these are
#: documents people send each other, and anything executable has no
#: business being handed back out of here later.
ALLOWED = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": (
        "application/vnd.openxmlformats-officedocument"
        ".wordprocessingml.document"
    ),
    ".xls": "application/vnd.ms-excel",
    ".xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

MAX_BYTES = 10 * 1024 * 1024

#: A key this service issued: uuid4 hex and one known extension.
#: Anything else is refused before it reaches the filesystem, so a key
#: cannot describe a path.
KEY = re.compile(r"^[0-9a-f]{32}(\.[a-z0-9]{1,5})$")


def path_for(key: str) -> Path | None:
    """The file this key names, or None if the key is not one of ours."""

    if not KEY.match(key or ""):
        return None

    path = ATTACHMENT_DIR / key

    return path if path.exists() else None


def read_bytes(key: str) -> bytes | None:
    path = path_for(key)

    return path.read_bytes() if path else None


def media_type(key: str) -> str:
    return ALLOWED.get(Path(key).suffix.lower(), "application/octet-stream")
