"""Allocating a document's reference number, once and only once.

Every series - sales orders, quotations, proforma invoices - used to work
out its next number by looking at what was already there: the highest row,
or the highest reference printed. That is fine until something is deleted,
because the number then falls free and the next document takes it. In the
stock ledger one order number had come to mean seven different orders.

So the number comes from a counter row instead, one per series, which only
ever moves forward. Two useful properties fall out of incrementing it
inside the caller's transaction:

  * an insert that rolls back releases the number again, so invoice
    numbers stay gapless, which is what an auditor expects;
  * a delete does not, so a number is never handed out twice.

The counter also serialises two people raising an order at the same
instant, which "highest row plus one" never did - both would have read the
same number.
"""

from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session


def next_number(db: Session, series: str, issued_so_far: Callable[[], int]) -> int:
    """The next number in ``series``, reserved for this transaction.

    ``issued_so_far`` is only called the first time a series is used, to
    start the counter above whatever the database already contains. It is
    a callable rather than a value so an established counter never pays
    for the scan.
    """

    row = db.execute(
        text(
            "UPDATE document_counter SET seq = seq + 1 "
            "WHERE name = :name RETURNING seq"
        ),
        {"name": series},
    ).first()

    if row is not None:
        return int(row[0])

    # First use. On a database that already holds documents, start above
    # the highest reference ever printed so nothing is issued twice. The
    # upsert covers two callers reaching this branch together.
    row = db.execute(
        text(
            "INSERT INTO document_counter (name, seq) VALUES (:name, :seq) "
            "ON CONFLICT (name) DO UPDATE SET seq = document_counter.seq + 1 "
            "RETURNING seq"
        ),
        {"name": series, "seq": max(0, issued_so_far()) + 1},
    ).first()

    return int(row[0])


def highest_issued(db: Session, column, offset: int = 0) -> int:
    """The largest number already printed on a document of this kind.

    Read from the reference itself rather than from the row id, because
    the two drift apart: ids skip whenever an insert is rolled back.
    ``offset`` is subtracted from what is found, for a series like
    ``QT-3091`` whose numbering starts from a base.
    """

    highest = 0

    for (reference,) in db.query(column).all():
        digits = "".join(ch for ch in (reference or "") if ch.isdigit())

        if digits:
            highest = max(highest, int(digits) - offset)

    return highest
