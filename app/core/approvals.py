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

Which of the two a document is written against follows from the customer
type: an End Customer is quoted ECP, and everybody else - dealer,
distributor, OEM, corporate - is bought through at DTP.

And a proposal always ends at the CEO. Whatever the discount, and whether
there is one at all, no price leaves the building without that signature.
A sales order raised off an already-signed proposal is not sent to the
CEO a second time.
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


#: The signature a proposal cannot leave without.
MANDATORY_FOR_QUOTATION = "CEO"


def approval_chain(
    price_type: str,
    discount_percent: float,
    db=None,
    document_type: str | None = None,
) -> list[str]:
    """The roles that must approve, senior-most last.

    An empty list means nothing needs approving. That is still possible for
    a sales order raised off a proposal the CEO has already signed, but no
    longer for a proposal itself: every price we put in front of a customer
    carries the CEO's signature, whatever the discount and whether there is
    one at all.

    ``document_type`` is "QUOTATION" for a proposal. It is optional so the
    preview endpoint and anything asking a general "who would sign this?"
    question still work without it.
    """

    from app.models.approval import ApprovalDocument

    is_quotation = document_type == ApprovalDocument.QUOTATION

    if price_type == PriceType.DP:
        # The transfer price is the CEO's to set, whatever the figure.
        return ["CEO"]

    discount = max(0.0, float(discount_percent or 0))

    chain: list[str] = []

    if discount > 0:
        for bound, role in bands(db):
            chain.append(role)

            if bound is not None and discount <= bound:
                break

    if not is_quotation:
        return chain

    # The chain is built from the discount bands, which say who owns how
    # much. The CEO's signature is a separate requirement on top of that,
    # so it is added rather than substituted - a 25% discount still passes
    # the AVP and ends at the founder, and the CEO is in the middle where
    # the bands already put them.
    if MANDATORY_FOR_QUOTATION not in chain:
        ceiling = discount_ceiling(MANDATORY_FOR_QUOTATION, db)

        if not chain or ceiling is None:
            chain.append(MANDATORY_FOR_QUOTATION)
        else:
            # Slot them in by seniority rather than on the end, so a
            # founder never signs before the CEO has.
            at = len(chain)

            for index, role in enumerate(chain):
                bound = discount_ceiling(role, db)

                if bound is None or bound > ceiling:
                    at = index
                    break

            chain.insert(at, MANDATORY_FOR_QUOTATION)

    return chain


def describe_chain(
    price_type: str,
    discount_percent: float,
    db=None,
    document_type: str | None = None,
) -> str:
    """One line explaining why these approvals are needed.

    The discount and the CEO rule are two separate reasons, so the
    sentence names whichever actually applies. Reading them off the
    finished chain said "10% is past the AVP's 15%" whenever the CEO had
    been added for the other reason, which is both wrong and the kind of
    wrong that makes somebody distrust the rest of the screen.
    """

    from app.models.approval import ApprovalDocument

    is_quotation = document_type == ApprovalDocument.QUOTATION

    if price_type == PriceType.DP:
        return "Dealer transfer price is fixed and is signed by the CEO."

    discount = max(0.0, float(discount_percent or 0))

    # What the discount alone would have called for.
    by_discount = approval_chain(price_type, discount, db)

    if not by_discount:
        if is_quotation:
            return "Every proposal is signed by the CEO before it is sent."

        return "No discount, so this needs no approval."

    owner = by_discount[-1]
    ceiling = discount_ceiling(by_discount[0], db)

    if len(by_discount) == 1:
        reason = f"{discount:g}% discount is within the {owner}'s authority."
    else:
        reason = (
            f"{discount:g}% discount is past the {by_discount[0]}'s "
            f"{ceiling:g}%, so it goes up to the {owner}."
        )

    if is_quotation and MANDATORY_FOR_QUOTATION not in by_discount:
        reason += " Every proposal also carries the CEO's signature."

    return reason
