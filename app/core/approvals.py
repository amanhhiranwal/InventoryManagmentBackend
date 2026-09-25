"""Who has to approve a discount, and in what order.

The sales hierarchy gives a discount away in bands. An Area Manager applies
one but cannot approve it; a Zonal Head has no discounting power either.
Above them the AVP carries the first 15%, the CEO the next 5%, and past 20%
only the founder can sign it off.

Approval is cumulative, not a lookup: an 18% discount needs the AVP *and*
the CEO, because the AVP's authority runs out at 15% and someone has to
own the rest. A rejection anywhere ends it.

Dealer price is a different thing entirely. It is a transfer price rather
than a negotiation, so it is not discounted at all - the CEO approves the
price itself and nobody below can move it.
"""


class PriceType:
    #: End Customer Price - what a customer is quoted, and what a discount
    #: comes off.
    ECP = "ECP"
    #: Dealer Price, the transfer price. Non-negotiable.
    DP = "DP"

    ALL = [ECP, DP]


#: The founder, in role terms. Super admins hold this authority implicitly;
#: this is the label shown on an approval step.
FOUNDER = "Founder"

#: Where the bands start before anyone has set them on the Masters screen.
#: The last has no bound - past the CEO's ceiling only the founder can sign.
DEFAULT_DISCOUNT_BANDS: list[tuple[float | None, str]] = [
    (15.0, "AVP"),
    (20.0, "CEO"),
    (None, FOUNDER),
]

#: Kept for anything still importing the old name.
DISCOUNT_BANDS = DEFAULT_DISCOUNT_BANDS


def bands(db=None) -> list[tuple[float | None, str]]:
    """The bands in force, as a super admin has set them.

    Read from the Quotation Approval screen, falling back to the defaults
    above, so the ceilings can be changed without a deployment. A bad or
    missing setting falls back rather than refusing to price anything.
    """

    if db is None:
        return DEFAULT_DISCOUNT_BANDS

    try:
        import json

        from app.models.app_setting import AppSetting

        row = (
            db.query(AppSetting)
            .filter(AppSetting.key == "discount_bands")
            .first()
        )

        if row is None or not (row.value or "").strip():
            return DEFAULT_DISCOUNT_BANDS

        stored = json.loads(row.value)
        parsed: list[tuple[float | None, str]] = []

        for entry in stored:
            role = str(entry.get("role") or "").strip()
            bound = entry.get("to_percent")

            if not role:
                continue

            parsed.append((None if bound is None else float(bound), role))

        return parsed or DEFAULT_DISCOUNT_BANDS
    except Exception:  # pragma: no cover - never block on a bad setting
        return DEFAULT_DISCOUNT_BANDS

#: Roles that can apply a discount but never approve their own band.
APPLIES_ONLY = ("Area Manager", "Zonal Head")


def discount_ceiling(role_name: str, db=None) -> float | None:
    """The most this role can approve on its own, as a percentage.

    ``None`` means no ceiling - the founder signs any figure. It is not
    ``inf``, because this travels out as JSON.
    """

    for bound, role in bands(db):
        if role == role_name:
            return bound

    return 0.0


def approval_chain(price_type: str, discount_percent: float, db=None) -> list[str]:
    """The roles that must approve, senior-most last.

    An empty list means nothing needs approving: an undiscounted end
    customer price goes out on the salesperson's own authority.
    """

    if price_type == PriceType.DP:
        # The transfer price is the CEO's to set, whatever the figure.
        return ["CEO"]

    discount = max(0.0, float(discount_percent or 0))

    if discount <= 0:
        return []

    chain: list[str] = []

    for bound, role in bands(db):
        chain.append(role)

        if bound is not None and discount <= bound:
            break

    return chain


def describe_chain(price_type: str, discount_percent: float, db=None) -> str:
    """One line explaining why these approvals are needed."""

    chain = approval_chain(price_type, discount_percent, db)

    if price_type == PriceType.DP:
        return "Dealer price is a transfer price and is set by the CEO."

    if not chain:
        return "No discount, so this needs no approval."

    discount = float(discount_percent or 0)

    if len(chain) == 1:
        return f"{discount:g}% discount is within the {chain[0]}'s authority."

    ceiling = discount_ceiling(chain[0], db)

    return (
        f"{discount:g}% discount is past the {chain[0]}'s {ceiling:g}%, "
        f"so it goes up to the {chain[-1]}."
    )
