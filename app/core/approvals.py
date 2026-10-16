"""Who has to approve a discount, and in what order.

Approval happens on the sales order. A proposal is a price put in front of
a customer to see what they say; the order is the commitment, and that is
where the money is actually given away. Quoting used to need the CEO's
signature whatever the figure, which put a senior approval in front of
every conversation and none in front of the commitment.

The sales hierarchy gives a discount away in bands. An Area Manager applies
one but cannot approve it; a Zonal Head has no discounting power either.
Above them the AVP carries the first 10%, the CEO the next 10%, and past
20% only the founder can sign it off.

Approval is cumulative, not a lookup: an 18% discount needs the AVP *and*
the CEO, because the AVP's authority runs out at 10% and someone has to
own the rest. A rejection anywhere ends it.

Dealer price is a different thing entirely. It is a transfer price rather
than a negotiation, so the question is not how much has been given away
but whether we are selling through the channel at all - which is the
founder's call. Every dealer order goes to them, discounted or not.

Which of the two a document is written against follows from the customer
type: an End Customer is quoted ECP, and everybody else - dealer,
distributor, OEM, corporate - is bought through at DTP.

These names are levels, not people. Who actually signs is read off the
raiser's own reporting line - their L1, then L2, then L3 - so two area
managers under different AVPs send their orders to different desks. That
resolution lives in approval_service; this module decides how far up the
line an order has to climb.
"""


class PriceType:
    #: End Customer Price - what a customer is quoted, and what a discount
    #: comes off.
    ECP = "ECP"
    #: Dealer Transfer Price. A fixed figure, not a negotiation: nobody
    #: below the CEO may discount it, and the CEO signs the price itself.
    DP = "DP"

    ALL = [ECP, DP]


#: The one customer type that buys at end customer price. Everybody else -
#: dealers, distributors, OEMs, corporates - is bought through at the
#: transfer price, so the list is stated the other way round: this is the
#: exception and DTP is the rule.
END_CUSTOMER_TYPES = ("end customer", "end-customer", "endcustomer")


def price_type_for(customer_type: str | None) -> str:
    """Which price list a document is written against.

    Decided by who is buying rather than chosen on a form. It used to be a
    dropdown beside the discount, which asked the salesperson a question
    the customer record already answers - and let a dealer be quoted at end
    customer price by picking the wrong entry.

    An unknown or missing type is treated as an end customer: quoting the
    higher price by mistake is a conversation, quoting the transfer price
    by mistake is a loss.
    """

    name = str(customer_type or "").strip().lower()

    if not name:
        return PriceType.ECP

    return PriceType.ECP if name in END_CUSTOMER_TYPES else PriceType.DP


#: The founder, in role terms. Super admins hold this authority implicitly;
#: this is the label shown on an approval step.
FOUNDER = "Founder"

#: Where the bands start before anyone has set them on the Masters screen.
#: The last has no bound - past the CEO's ceiling only the founder can sign.
DEFAULT_DISCOUNT_BANDS: list[tuple[float | None, str]] = [
    (10.0, "AVP"),
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


#: Proposals are no longer signed off. Kept as None so anything still
#: asking gets a clear "nobody" rather than an AttributeError.
MANDATORY_FOR_QUOTATION = None


def chain_for_raiser(
    price_type: str,
    discount_percent: float,
    raiser_roles: set[str] | None = None,
    is_super_admin: bool = False,
    db=None,
    document_type: str | None = None,
) -> list[str]:
    """Who must approve an order *this person* has raised.

    Two questions, in this order.

    **Is it theirs to give?** Each level owns discount up to its band's
    ceiling: the AVP 10%, the CEO 20%, the founder any figure. An Area
    Manager and a Zonal Head own none - they apply a discount, they do
    not approve one. A discount inside the raiser's own ceiling needs
    nobody: an AVP's 8% is an AVP's decision, and asking for it sent the
    request to every AVP in the business, the raiser included, where any
    of them could sign it.

    **If it is not, who is above them?** Everybody, in order, finishing
    with the founder. An Area Manager's 8% goes AVP, then CEO, then
    founder - not because 8% needs three signatures, but because an order
    leaving the floor is seen by the line above it on the way up.

    Dealer price is the separate question it always was. Selling through
    the channel is the founder's call, so a dealer order goes to them -
    unless they raised it themselves, or a super admin did.

    A super admin stands outside the sales line entirely and needs
    nobody; they are the people who set the bands.
    """

    from app.models.approval import ApprovalDocument

    if document_type == ApprovalDocument.QUOTATION:
        # A proposal is not a commitment. Nothing to sign.
        return []

    held = set(raiser_roles or ())

    if is_super_admin:
        return []

    ladder = [role for _, role in bands(db)]

    if price_type == PriceType.DP:
        return [] if FOUNDER in held else [FOUNDER]

    discount = max(0.0, float(discount_percent or 0))

    # The highest rung the raiser stands on. Everything at or below it is
    # theirs; the rungs above it are who they have to ask.
    standing = max(
        (index for index, role in enumerate(ladder) if role in held),
        default=-1,
    )

    ceiling = (
        discount_ceiling(ladder[standing], db) if standing >= 0 else 0.0
    )

    # A ceiling of None is the founder's: no figure is above it.
    if ceiling is None or discount <= ceiling:
        return []

    above = ladder[standing + 1:]

    if FOUNDER not in above:
        above = above + [FOUNDER]

    return above


def approval_chain(
    price_type: str,
    discount_percent: float,
    db=None,
    document_type: str | None = None,
) -> list[str]:
    """The levels that must approve, senior-most last.

    An empty list means nothing needs approving, which is now the answer
    for every proposal: a price shown to a customer is a conversation, and
    the commitment it may turn into is the sales order. That is where the
    signatures are.

    ``document_type`` is "QUOTATION" for a proposal. It is optional so the
    preview endpoint and anything asking a general "who would sign this?"
    question still work without it.
    """

    from app.models.approval import ApprovalDocument

    if document_type == ApprovalDocument.QUOTATION:
        # A proposal is not a commitment. Nothing to sign.
        return []

    if price_type == PriceType.DP:
        # Selling through the channel at all is the founder's call, so the
        # question is not how much has been given away. Every dealer order
        # goes to them, discounted or not.
        return [FOUNDER]

    discount = max(0.0, float(discount_percent or 0))

    chain: list[str] = []

    if discount > 0:
        for bound, role in bands(db):
            chain.append(role)

            if bound is not None and discount <= bound:
                break

    # Every sales order ends with the founder, whatever the discount and
    # whichever price list it is written against. The bands say who owns
    # the discount; this says who commits the business to the order. An
    # order at list price used to need nobody, which meant it was
    # confirmed the moment it was raised and went straight to invoicing
    # with no signature anywhere on it.
    if FOUNDER not in chain:
        chain.append(FOUNDER)

    return chain


def describe_chain(
    price_type: str,
    discount_percent: float,
    db=None,
    document_type: str | None = None,
    chain: list[str] | None = None,
    raiser: str | None = None,
) -> str:
    """One line explaining why these approvals are needed.

    Written from the reason rather than read off the finished chain: the
    sentence has to say what actually triggered it, or somebody reading
    the screen stops trusting the rest of it.

    ``chain`` is the chain the asker would actually raise - who signs
    depends on who is asking, and a discount inside their own authority
    needs nobody. Left out, the question is answered in general.

    ``raiser`` names whose authority is being talked about. Describing a
    request somebody else raised, "your own authority" means the reader's,
    which is not whose it is: the CEO reading an AVP's order is not the
    one the figure was past.
    """

    from app.models.approval import ApprovalDocument

    if document_type == ApprovalDocument.QUOTATION:
        return (
            "Proposals are not signed off. The approval is on the sales "
            "order, which is where the price is committed to."
        )

    discount = max(0.0, float(discount_percent or 0))

    if chain is None:
        chain = approval_chain(price_type, discount, db, document_type)

    if price_type == PriceType.DP:
        return (
            "Dealer orders are the founder's call, whatever the discount."
            if chain
            else "Dealer price is yours to set, so this needs no approval."
        )

    whose = f"{raiser}'s own authority" if raiser else "your own authority"

    if not chain:
        return (
            f"{discount:g}% is within {whose}, so this needs no approval."
            if discount
            else "No discount, so this needs no approval."
        )

    bands_part = [role for role in chain if role != FOUNDER]

    if not bands_part:
        return (
            f"{discount:g}% is past {whose}, so this goes to the {FOUNDER}."
            if discount
            else f"This goes to the {FOUNDER} to commit."
        )

    # Named from the chain rather than from a band comparison. The chain
    # is everyone above the asker, so the first role in it is not
    # necessarily the one whose ceiling was passed - saying "8% is past
    # the AVP's 10%" about an Area Manager's 8% was a sentence that was
    # simply untrue, on the screen that is meant to explain the rule.
    climbing = ", then the ".join(bands_part)

    return (
        f"{discount:g}% is past {whose}, so it goes to the {climbing}, "
        f"and then the {FOUNDER} commits the order."
    )
