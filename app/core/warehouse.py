"""Words the warehouse masters use, in one place.

A location is either a warehouse, which holds stock, or an office, which
does not. Every warehouse is divided into bins, and three of them always
exist: where goods are stored, where they wait for quality check, and
where rejected goods are kept apart until they go back or are written off.
"""

WAREHOUSE = "WAREHOUSE"
OFFICE = "OFFICE"
LOCATION_TYPES = (WAREHOUSE, OFFICE)

STORE = "STORE"
QC = "QC"
REJECT = "REJECT"
BIN_TYPES = (STORE, QC, REJECT)

#: Created with every warehouse: code, name, type.
DEFAULT_BINS = (
    ("STORE", "Main Store", STORE),
    ("QC", "Quality Check", QC),
    ("REJECT", "Rejected Goods", REJECT),
)


def location_type(location) -> str:
    """A location's type; a row saved before types existed is a warehouse."""

    return (getattr(location, "location_type", None) or WAREHOUSE).upper()
