"""The payment splits a document can be issued on.

One list, offered by the proposal, carried onto the sales order and read
back by the proforma invoice - so the terms a client was quoted are the
terms the invoice asks for. A split typed on the proposal used to be
replaced further down the chain by a default sentence that could quietly
contradict the offer the client had accepted.

Each option carries the advance percentage as well as its wording, because
the figures on all three documents are worked out from that number. The
wording alone would leave "50% advance" printing a 30% figure.
"""

#: (advance %, the sentence that appears on the document)
PAYMENT_TERMS: list[tuple[float, str]] = [
    (100.0, "100% advance against Proforma Invoice."),
    (
        70.0,
        "70% advance against Proforma Invoice; 30% balance upon delivery "
        "challan verification.",
    ),
    (
        50.0,
        "50% advance against Proforma Invoice; 50% balance upon delivery "
        "challan verification.",
    ),
    (
        30.0,
        "30% advance against Proforma Invoice; 70% balance upon delivery "
        "challan verification.",
    ),
    (
        0.0,
        "100% against delivery challan verification, no advance payable.",
    ),
]

#: What a document starts on when nothing has been chosen.
DEFAULT_ADVANCE_PERCENT = 30.0


def options() -> list[dict]:
    """The list as the dropdowns on all three screens render it."""

    return [
        {"advance_percent": percent, "label": label}
        for percent, label in PAYMENT_TERMS
    ]


def wording_for(advance_percent) -> str:
    """The sentence that matches a split, for a document that carries only
    the number. An advance nobody has a wording for is described rather
    than left blank."""

    try:
        percent = round(float(advance_percent), 2)
    except (TypeError, ValueError):
        percent = DEFAULT_ADVANCE_PERCENT

    for option_percent, label in PAYMENT_TERMS:
        if abs(option_percent - percent) < 0.01:
            return label

    balance = 100 - percent

    return (
        f"{percent:g}% advance against Proforma Invoice; {balance:g}% balance "
        "upon delivery challan verification."
    )
