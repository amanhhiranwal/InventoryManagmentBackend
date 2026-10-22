"""What time it is, and how a time leaves this server.

Everything is stored as naive UTC - ``datetime.utcnow()`` - and was then
sent out by ``.isoformat()``, which writes no offset at all:

    2026-10-22T06:05:18

A browser reads a string like that as *local* time. On a machine set to
India that is 06:05 IST for a moment that was really 11:35 IST, so every
timestamp on the portal was five and a half hours early.

Nothing about storage changes here. What changes is that a time leaving
the server says which zone it is in, so the screen can put it back into
IST and show the same clock the person is reading.
"""

from datetime import date, datetime, timedelta, timezone

#: What the database holds.
UTC = timezone.utc

#: India Standard Time. Fixed, because India keeps no daylight saving -
#: so a fixed offset is the whole truth here and needs no tz database.
IST = timezone(timedelta(hours=5, minutes=30), "IST")


def as_utc(value: datetime) -> datetime:
    """Tag a stored, naive datetime as the UTC it actually is."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def to_ist(value: datetime) -> datetime:
    """The same moment, read off an Indian clock."""

    return as_utc(value).astimezone(IST)


def iso(value: datetime | date | None) -> str | None:
    """A timestamp as it should leave the server: with its offset on it.

    Dates are passed through untouched - a date has no time of day and
    so no zone to be wrong about.
    """

    if value is None:
        return None

    if isinstance(value, datetime):
        return as_utc(value).isoformat()

    return value.isoformat()


def now_ist() -> datetime:
    """Now, in IST. For deciding what day it is here."""

    return datetime.now(IST)


def today_ist() -> date:
    """Today's date in India, which is what a validity date is read against.

    Compared against UTC, a proposal valid to the 21st would lapse at
    5:30am on the 22nd India time - half a working morning early.
    """

    return now_ist().date()
