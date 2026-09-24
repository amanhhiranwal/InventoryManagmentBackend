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

#: Discount bands as (upper bound %, role that carries up to it). The last
#: band has no bound - beyond the CEO's 20% only the founder can sign.
DISCOUNT_BANDS: list[tuple[float | None, str]] = [
    (15.0, "AVP"),
    (20.0, "CEO"),
    (None, FOUNDER),
]

#: Roles that can apply a discount but never approve their own band.
APPLIES_ONLY = ("Area Manager", "Zonal Head")


def discount_ceiling(role_name: str) -> float | None:
    """The most this role can approve on its own, as a percentage.

    ``None`` means no ceiling - the founder signs any figure. It is not
    ``inf``, because this travels out as JSON.
    """

    for bound, role in DISCOUNT_BANDS:
        if role == role_name:
            return bound

    return 0.0


def approval_chain(price_type: str, discount_percent: float) -> list[str]:
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

    for bound, role in DISCOUNT_BANDS:
        chain.append(role)

        if bound is not None and discount <= bound:
            break

    return chain


def describe_chain(price_type: str, discount_percent: float) -> str:
    """One line explaining why these approvals are needed."""

    chain = approval_chain(price_type, discount_percent)

    if price_type == PriceType.DP:
        return "Dealer price is a transfer price and is set by the CEO."

    if not chain:
        return "No discount, so this needs no approval."

    discount = float(discount_percent or 0)

    if len(chain) == 1:
        return f"{discount:g}% discount is within the {chain[0]}'s authority."

    ceiling = discount_ceiling(chain[0])

    return (
        f"{discount:g}% discount is past the {chain[0]}'s {ceiling:g}%, "
        f"so it goes up to the {chain[-1]}."
    )
