"""Who owns a sales order at each point after it is approved.

The discount chain decides whether an order may exist. From there it stops
being the salesperson's to push: accounts confirm the money has arrived,
inventory confirm the stock is there and send it out, and the salesperson
watches. That is how the business actually works, and until now the CRM
let whoever raised the order type any status they liked.

Each stage names the desk holding it. A desk is a role, and only someone
holding that role - or a super admin, who stands in for anybody - can move
an order out of that stage. Everyone who can see the order can still see
where it is; they simply cannot move it.

Rejecting at a desk puts the order On Hold with the reason, rather than
dropping it back to draft: the order is real, it is the paperwork behind
it that needs fixing.
"""

from dataclasses import dataclass

from app.core.workflow_status import SalesOrderStatus

#: The two desks an order passes through, as roles in Roles & Access.
ACCOUNTS = "Accounts"
INVENTORY = "Inventory"


@dataclass(frozen=True)
class Desk:
    """One stage of the journey, and who moves it on."""

    #: The role holding the order here.
    role: str
    #: What that desk is being asked to confirm.
    asks: str
    #: Where an approval sends it.
    approves_to: str
    #: What approving means, in the activity trail and the email.
    approval_action: str
    #: What the desk sees on its own screen.
    queue_label: str


#: status the order is sitting at -> the desk waiting on it.
DESKS: dict[str, Desk] = {
    SalesOrderStatus.CONFIRMED: Desk(
        role=ACCOUNTS,
        asks="Confirm the advance has been received against the proforma invoice.",
        approves_to=SalesOrderStatus.PAYMENT_VERIFIED,
        approval_action="Payment Verified",
        queue_label="Awaiting Payment",
    ),
    SalesOrderStatus.PAYMENT_VERIFIED: Desk(
        role=INVENTORY,
        asks="Confirm the stock is available and take the order into procurement.",
        approves_to=SalesOrderStatus.PROCUREMENT,
        approval_action="With Inventory / Procurement",
        queue_label="Awaiting Stock",
    ),
    SalesOrderStatus.PROCUREMENT: Desk(
        role=INVENTORY,
        asks="Confirm the stock is in hand and the order is ready to deliver.",
        approves_to=SalesOrderStatus.READY,
        approval_action="Ready To Dispatch",
        queue_label="In Procurement",
    ),
    SalesOrderStatus.READY: Desk(
        role=INVENTORY,
        asks="Send the order out.",
        approves_to=SalesOrderStatus.DISPATCHED,
        approval_action="Order Dispatched",
        queue_label="Ready To Dispatch",
    ),
    SalesOrderStatus.DISPATCHED: Desk(
        role=INVENTORY,
        asks="Confirm the order has reached the client.",
        approves_to=SalesOrderStatus.DELIVERED,
        approval_action="Order Delivered",
        queue_label="Out For Delivery",
    ),
    SalesOrderStatus.INSTALLED: Desk(
        role=ACCOUNTS,
        asks="Confirm the balance has been settled and close the order.",
        approves_to=SalesOrderStatus.COMPLETED,
        approval_action="Order Completed",
        queue_label="Awaiting Balance",
    ),
}

#: Delivered to installed is the salesperson's own: they are the ones on
#: site with the client. Left out of DESKS so the existing sales screens
#: keep working exactly as they do.
SALES_OWNED = {SalesOrderStatus.DELIVERED}

#: Which desk each screen shows.
DESK_STAGES: dict[str, list[str]] = {
    ACCOUNTS: [
        stage for stage, desk in DESKS.items() if desk.role == ACCOUNTS
    ],
    INVENTORY: [
        stage for stage, desk in DESKS.items() if desk.role == INVENTORY
    ],
}


def desk_for(status: str) -> Desk | None:
    """The desk an order at this status is waiting on, if any."""

    return DESKS.get(str(status or "").upper())


def role_names(user) -> set[str]:
    """Every role a user holds, however the caller happens to hold them.

    Accepts a User row or the dict the token is unpacked into, because
    both turn up: the routes have the dict, the services have the row.
    """

    if user is None:
        return set()

    roles = getattr(user, "roles", None)

    if roles is None and isinstance(user, dict):
        roles = user.get("roles") or []

    names: set[str] = set()

    for role in roles or []:
        name = getattr(role, "role_name", None)

        if name is None and isinstance(role, dict):
            name = role.get("role_name") or role.get("name")
        elif name is None and isinstance(role, str):
            name = role

        if name:
            names.add(str(name))

    return names


def holds_desk(user, role: str, *, is_super_admin: bool = False) -> bool:
    """Whether this user staffs that desk.

    A super admin staffs every desk - otherwise a company with nobody in
    the Accounts role could never move an order again.
    """

    if is_super_admin or getattr(user, "is_super_admin", False):
        return True

    return role in role_names(user)
