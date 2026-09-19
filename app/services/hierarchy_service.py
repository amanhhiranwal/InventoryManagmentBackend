"""Role hierarchy and record visibility.

The hierarchy is drawn on the Workflows page: each workflow is a chart of
role nodes, and an edge runs from a senior role to the role directly below
it - e.g. CEO -> AVP -> Zonal Head -> Area Manager. Every workflow is read
together as one chart.

Who can see whose records follows from it:

- A user always sees their own records.
- A user sees the records of users whose role sits *below* theirs.
  Users on the same level (two Zonal Heads) never see each other's, and
  nobody sees upwards.
- Where a junior has a Reports To manager set, only the managers in that
  chain see them - so Zonal Head North sees their own Area Managers and
  not Zonal Head South's. A junior with no manager set stays visible to
  every role above theirs, so nothing disappears before the reporting lines
  have been filled in.
- Anyone in a user's Reports To chain sees that user, whatever the roles.
- A super admin sees everything.
"""

from collections import deque
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.user_role import UserRole
from app.models.workflow import Workflow


def _role_edges(workflows) -> list[tuple[str, str]]:
    """Senior -> junior role id pairs from the saved workflow charts."""

    pairs: list[tuple[str, str]] = []

    for wf in workflows:
        nodes = wf.nodes if isinstance(wf.nodes, list) else []
        edges = wf.edges if isinstance(wf.edges, list) else []

        role_by_node = {
            n.get("id"): (n.get("data") or {}).get("role_id")
            for n in nodes
            if isinstance(n, dict)
        }

        for edge in edges:
            if not isinstance(edge, dict):
                continue

            senior = role_by_node.get(edge.get("source"))
            junior = role_by_node.get(edge.get("target"))

            if senior and junior and senior != junior:
                pairs.append((str(senior), str(junior)))

    return pairs


def _descendants(start: set[str], children: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    queue = deque(start)

    while queue:
        node = queue.popleft()
        for child in children.get(node, ()):
            if child not in seen:
                seen.add(child)
                queue.append(child)

    return seen


def find_cycle(pairs: list[tuple[str, str]]) -> bool:
    """Whether the senior -> junior pairs loop back on themselves."""

    children: dict[str, set[str]] = {}
    for senior, junior in pairs:
        children.setdefault(senior, set()).add(junior)

    return any(role in _descendants({role}, children) for role in children)


class HierarchyService:

    # ---------------- Roles ---------------- #

    @staticmethod
    def role_children(db: Session) -> dict[str, set[str]]:
        children: dict[str, set[str]] = {}
        for senior, junior in _role_edges(db.query(Workflow).all()):
            children.setdefault(senior, set()).add(junior)
        return children

    @staticmethod
    def user_role_ids(user_id: str, db: Session) -> set[str]:
        if not user_id:
            return set()

        rows = (
            db.query(UserRole.role_id)
            .filter(UserRole.user_id == UUID(str(user_id)))
            .all()
        )
        return {str(r.role_id) for r in rows}

    @staticmethod
    def junior_role_ids(role_ids: set[str], db: Session) -> set[str]:
        """Every role below any of the given roles, not counting them."""

        below = _descendants(set(role_ids), HierarchyService.role_children(db))
        return below - set(role_ids)

    @staticmethod
    def role_levels(db: Session) -> dict[str, dict]:
        """Depth and direct parents of each role in the chart.

        Depth 1 is the top of a chart. Roles that are not on any chart are
        left out.
        """

        children = HierarchyService.role_children(db)
        parents: dict[str, set[str]] = {}
        for senior, juniors in children.items():
            for junior in juniors:
                parents.setdefault(junior, set()).add(senior)

        all_roles = set(children) | set(parents)
        levels: dict[str, int] = {}

        # Longest path from a top role, so a role is always placed below
        # every role above it even where charts overlap.
        def depth(role: str, trail: frozenset = frozenset()) -> int:
            if role in levels:
                return levels[role]
            if role in trail:
                return 1
            ups = parents.get(role, set())
            value = 1 + max((depth(p, trail | {role}) for p in ups), default=0)
            levels[role] = value
            return value

        return {
            role: {"level": depth(role), "parent_role_ids": sorted(parents.get(role, set()))}
            for role in all_roles
        }

    # ---------------- Users ---------------- #

    @staticmethod
    def manager_chain(user_id: str, managers: dict[str, str | None]) -> list[str]:
        """The user's managers, nearest first."""

        chain: list[str] = []
        current = managers.get(user_id)

        while current and current not in chain and current != user_id:
            chain.append(current)
            current = managers.get(current)

        return chain

    @staticmethod
    def visible_user_ids(current_user: dict, db: Session) -> set[str] | None:
        """Users whose records the current user may see.

        None means unrestricted (super admin).
        """

        if current_user.get("is_super_admin"):
            return None

        user_id = str(current_user.get("user_id") or "")
        if not user_id:
            return set()

        my_roles = HierarchyService.user_role_ids(user_id, db)
        junior_roles = HierarchyService.junior_role_ids(my_roles, db)

        users = db.query(User.id, User.reports_to_id).all()
        managers = {
            str(u.id): (str(u.reports_to_id) if u.reports_to_id else None)
            for u in users
        }

        roles_by_user: dict[str, set[str]] = {}
        for row in db.query(UserRole.user_id, UserRole.role_id).all():
            roles_by_user.setdefault(str(row.user_id), set()).add(str(row.role_id))

        visible = {user_id}

        for other in managers:
            if other == user_id:
                continue

            chain = HierarchyService.manager_chain(other, managers)

            if user_id in chain:
                visible.add(other)
                continue

            if roles_by_user.get(other, set()) & junior_roles and not chain:
                visible.add(other)

        return visible

    @staticmethod
    def can_see_user(current_user: dict, other_user_id, db: Session) -> bool:
        visible = HierarchyService.visible_user_ids(current_user, db)
        return visible is None or str(other_user_id) in visible

    @staticmethod
    def assert_role_below(current_user: dict, role_id: str, db: Session) -> None:
        """Only roles below the caller's own may be looked at - not their
        own level (a peer's setup) and not anything above."""

        if current_user.get("is_super_admin"):
            return

        mine = HierarchyService.user_role_ids(current_user.get("user_id"), db)

        if str(role_id) not in HierarchyService.junior_role_ids(mine, db):
            raise HTTPException(
                status_code=403,
                detail="You can only view roles below your own in the hierarchy.",
            )

    # ---------------- Reports To ---------------- #

    @staticmethod
    def validate_reports_to(
        user_id: str | None,
        manager_id: str | None,
        role_ids: list[str],
        db: Session,
    ) -> UUID | None:
        """Check a Reports To choice and return it as a UUID.

        The manager must exist, must not be the user or anyone reporting to
        them, and - where the hierarchy relates their roles - must hold a
        role above the user's.
        """

        if not manager_id:
            return None

        try:
            manager_uuid = UUID(str(manager_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Reports To user.")

        manager = db.query(User).filter(User.id == manager_uuid).first()
        if manager is None:
            raise HTTPException(status_code=404, detail="Reports To user not found.")

        if user_id and str(manager_uuid) == str(user_id):
            raise HTTPException(status_code=400, detail="A user cannot report to themselves.")

        if user_id:
            managers = {
                str(u.id): (str(u.reports_to_id) if u.reports_to_id else None)
                for u in db.query(User.id, User.reports_to_id).all()
            }
            if str(user_id) in HierarchyService.manager_chain(str(manager_uuid), managers):
                raise HTTPException(
                    status_code=400,
                    detail="That would create a reporting loop: the chosen manager already reports to this user.",
                )

        if manager.is_super_admin or not role_ids:
            return manager_uuid

        manager_roles = {str(r.id) for r in manager.roles}
        below_manager = HierarchyService.junior_role_ids(manager_roles, db)
        user_roles = {str(r) for r in role_ids}

        charted = set(HierarchyService.role_levels(db))
        if user_roles & charted and manager_roles & charted and not user_roles & below_manager:
            raise HTTPException(
                status_code=400,
                detail="Reports To must be someone whose role is above this user's role in the hierarchy.",
            )

        return manager_uuid
