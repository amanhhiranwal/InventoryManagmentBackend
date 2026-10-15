"""Searching and paging a list, the same way on every screen.

Four pipeline screens show the same thing in different words: a list of
records somebody can search and turn pages through. Written four times it
was written four different ways - and three of them searched only the
rows the browser happened to be holding, so looking for a customer on
page 3 found nothing and the screen said there were none.

So the two parts live here:

  * ``search_clause`` builds the "does any of these columns contain what
    they typed" condition, with the characters LIKE treats as wildcards
    escaped - a customer called "A&B (India)" is a name, not a pattern.

  * ``page_of`` counts the matches and returns one page of them, in that
    order, so the count on screen is the count of matches rather than the
    count of rows that fit on the page.

Both take the query the caller has already narrowed to what that person
is allowed to see. Visibility is not this file's business and must never
depend on a search box.
"""

from sqlalchemy import or_


def _escaped(term: str) -> str:
    """What they typed, safe to put either side of a LIKE pattern."""

    return (
        str(term)
        .strip()
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def search_clause(term: str | None, columns: list):
    """A condition matching any of those columns, or None for no search."""

    text = (term or "").strip()

    if not text or not columns:
        return None

    like = f"%{_escaped(text)}%"

    return or_(*[column.ilike(like, escape="\\") for column in columns])


def month_bounds():
    """The start of this month and of last month, as the server sees them."""

    from datetime import datetime

    now = datetime.now()

    this_month = datetime(now.year, now.month, 1)

    last_month = (
        datetime(now.year - 1, 12, 1)
        if now.month == 1
        else datetime(now.year, now.month - 1, 1)
    )

    return this_month, last_month


def measure(query, created_at, cards: dict) -> dict:
    """The figures across the top of a list screen, in one query.

    ``cards`` is name -> {"where": a condition or None, "sum": a column or
    None}. Without a column the card is a count; with one it is a sum of
    that column, which is how "Order Value" and "Pipeline Value" are made.

    Each card comes back with the figure over everything, and the same
    figure over this month and over last month - which is what the little
    percentage beside it is made of. Counted by the database over the
    whole list rather than over the page on screen, because a total that
    only adds up what is visible is not a total.
    """

    from sqlalchemy import case, func

    this_month, last_month = month_bounds()

    def figure(where, column, window=None):
        conditions = [c for c in (where, window) if c is not None]

        if column is None:
            if not conditions:
                return func.count()

            return func.sum(case((and_all(conditions), 1), else_=0))

        if not conditions:
            return func.coalesce(func.sum(column), 0)

        return func.coalesce(
            func.sum(case((and_all(conditions), column), else_=0)), 0
        )

    columns = []

    for card in cards.values():
        where = card.get("where")
        column = card.get("sum")

        columns += [
            figure(where, column),
            figure(where, column, created_at >= this_month),
            figure(
                where,
                column,
                and_all([created_at >= last_month, created_at < this_month]),
            ),
        ]

    # The ordering goes before the aggregate: "order by id" over a query
    # that now selects only sums is a column Postgres refuses to group by,
    # and sorting one row achieves nothing anyway.
    row = query.order_by(None).with_entities(*columns).one()

    out = {}

    for index, name in enumerate(cards):
        start = index * 3

        out[name] = {
            "value": float(row[start] or 0),
            "this_month": float(row[start + 1] or 0),
            "last_month": float(row[start + 2] or 0),
        }

    return out


def and_all(conditions):
    """One condition from several, without importing and_ everywhere."""

    from sqlalchemy import and_

    conditions = [c for c in conditions if c is not None]

    if not conditions:
        return None

    return conditions[0] if len(conditions) == 1 else and_(*conditions)


def page_of(query, page: int | None, size: int | None) -> tuple[list, int]:
    """One page of a query, and how many rows it has altogether.

    Counted before the page is cut, so "Showing 1-10 of 73" is true. With
    no page asked for, everything comes back and the total is its length -
    which is what the callers that read a whole list into a picker still
    expect.
    """

    if not page or not size:
        rows = query.all()
        return rows, len(rows)

    total = query.order_by(None).count()

    rows = query.offset((max(1, page) - 1) * size).limit(size).all()

    return rows, total
