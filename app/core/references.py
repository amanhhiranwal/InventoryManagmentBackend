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


def next_number(
    db: Session,
    series: str,
    issued_so_far: Callable[[], int],
    taken: Callable[[int], bool] | None = None,
) -> int:
    """The next number in ``series``, reserved for this transaction.

    ``issued_so_far`` is only called the first time a series is used, to
    start the counter above whatever the database already contains. It is
    a callable rather than a value so an established counter never pays
    for the scan.

    ``taken`` says whether a number is already printed on a document. It
    is what lets a counter that has fallen behind catch up: the counter
    only moves forward, so it cannot drift past the data, but it can sit
    behind it - a number issued outside the counter, a database restored
    from a partial copy - and then every allocation collides with a
    unique index and the user is told the resource already exists. One
    indexed lookup confirms the number is free; only a clash pays for the
    scan that lifts the counter clear.
    """

    row = db.execute(
        text(
            "UPDATE document_counter SET seq = seq + 1 "
            "WHERE name = :name RETURNING seq"
        ),
        {"name": series},
    ).first()

    if row is not None:
        allocated = int(row[0])

        if taken is None or not taken(allocated):
            return allocated

        # Behind the data. Lift the counter clear of everything issued and
        # take the next one. Still forward-only: the counter is raised, never
        # lowered, so nothing already handed out can come round again.
        row = db.execute(
            text(
                "UPDATE document_counter SET seq = GREATEST(seq, :floor) + 1 "
                "WHERE name = :name RETURNING seq"
            ),
            {"name": series, "floor": max(allocated, issued_so_far())},
        ).first()

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


def peek_next(db: Session, series: str, issued_so_far: Callable[[], int]) -> int:
    """What the next number *would* be, without taking it.

    For showing a reference on a form before anything is saved. It reads
    rather than increments, so opening a form never burns a number and two
    people opening one at the same time both see the same figure - which
    is why what they see is a preview and not a promise. The number is
    only theirs once the record is written.
    """

    row = db.execute(
        text("SELECT seq FROM document_counter WHERE name = :name"),
        {"name": series},
    ).first()

    return (int(row[0]) if row is not None else max(0, issued_so_far())) + 1
