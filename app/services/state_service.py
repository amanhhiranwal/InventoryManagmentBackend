from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import gst
from app.models.state import State
from app.schemas.state import CreateStateRequest

#: Every state and union territory, with the postal abbreviation stored
#: against it. The two-digit GST code is not kept here - it is resolved
#: from the name in app/core/gst.py, which is what decides whether a sale
#: is taxed as CGST plus SGST or as a single IGST line. Keeping one of
#: them in two places is how they come to disagree.
#:
#: The list used to stop at thirteen states, so a customer in Madhya
#: Pradesh could not be recorded at all.
DEFAULT_STATES: list[tuple[str, str]] = [
    ("Jammu and Kashmir", "JK"),
    ("Himachal Pradesh", "HP"),
    ("Punjab", "PB"),
    ("Chandigarh", "CH"),
    ("Uttarakhand", "UK"),
    ("Haryana", "HR"),
    ("Delhi", "DL"),
    ("Rajasthan", "RJ"),
    ("Uttar Pradesh", "UP"),
    ("Bihar", "BR"),
    ("Sikkim", "SK"),
    ("Arunachal Pradesh", "AR"),
    ("Nagaland", "NL"),
    ("Manipur", "MN"),
    ("Mizoram", "MZ"),
    ("Tripura", "TR"),
    ("Meghalaya", "ML"),
    ("Assam", "AS"),
    ("West Bengal", "WB"),
    ("Jharkhand", "JH"),
    ("Odisha", "OD"),
    ("Chhattisgarh", "CG"),
    ("Madhya Pradesh", "MP"),
    ("Gujarat", "GJ"),
    ("Dadra and Nagar Haveli and Daman and Diu", "DD"),
    ("Maharashtra", "MH"),
    ("Karnataka", "KA"),
    ("Goa", "GA"),
    ("Lakshadweep", "LD"),
    ("Kerala", "KL"),
    ("Tamil Nadu", "TN"),
    ("Puducherry", "PY"),
    ("Andaman and Nicobar Islands", "AN"),
    ("Telangana", "TG"),
    ("Andhra Pradesh", "AP"),
    ("Ladakh", "LA"),
]


def _repair_id_sequence(db: Session) -> None:
    """Put the id sequence back above the highest id in the table.

    The first thirteen states were seeded with their ids written out, and
    an explicit id does not move the sequence along. So the sequence sat
    at 3 while the table held 13, and the next state anybody added was
    handed an id that already existed - a primary key collision, reported
    as "Resource already exists" about a state that was plainly not
    there. Madhya Pradesh could not be added, and the message pointed at
    the wrong thing entirely.

    Cheap, idempotent, and only meaningful on Postgres; anything else is
    left alone.
    """

    try:
        db.execute(
            text(
                "SELECT setval("
                "  pg_get_serial_sequence('sales_state', 'id'),"
                "  GREATEST(COALESCE((SELECT MAX(id) FROM sales_state), 1), 1)"
                ")"
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001 - not Postgres, or no sequence to fix
        db.rollback()


class StateService:
    @staticmethod
    def publish_codes(db: Session) -> None:
        """Hand Masters' GST codes to the tax calculation.

        Only the codes somebody has actually set are published; the rest
        fall through to the statutory table, which is where they should
        come from.
        """

        try:
            gst.set_configured_codes(
                {
                    name: code
                    for name, code in db.query(State.name, State.gst_code).all()
                    if (code or "").strip()
                }
            )
        except Exception:  # noqa: BLE001 - tax still has the statutory table
            db.rollback()

    @staticmethod
    def update(state_id: int, request, db: Session) -> State:
        """Correct a state from Masters."""

        st = db.query(State).filter(State.id == state_id).first()

        if not st:
            raise HTTPException(status_code=404, detail="State not found.")

        if request.name is not None and request.name.strip():
            st.name = request.name.strip()

        if request.code is not None and request.code.strip():
            wanted = request.code.upper().strip()

            clash = (
                db.query(State)
                .filter(State.code == wanted, State.id != st.id)
                .first()
            )

            if clash:
                raise HTTPException(
                    status_code=400,
                    detail=f"{clash.name} already uses the abbreviation {wanted}.",
                )

            st.code = wanted

        if request.gst_code is not None:
            digits = "".join(ch for ch in request.gst_code if ch.isdigit())

            if request.gst_code.strip() and len(digits) != 2:
                raise HTTPException(
                    status_code=400,
                    detail="A GST state code is two digits, for example 23.",
                )

            st.gst_code = digits or None

        if request.country is not None and request.country.strip():
            st.country = request.country.strip()

        db.commit()
        db.refresh(st)

        StateService.publish_codes(db)

        return st

    @staticmethod
    def create(request: CreateStateRequest, db: Session) -> State:
        name = request.name.strip()
        code_upper = request.code.upper().strip()

        if not name:
            raise HTTPException(status_code=400, detail="State name is required.")

        clash = (
            db.query(State)
            .filter((State.code == code_upper) | (State.name.ilike(name)))
            .first()
        )

        if clash:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{clash.name} ({clash.code}) is already on the list."
                ),
            )

        # Before inserting, not after: this is what the collision was.
        _repair_id_sequence(db)

        digits = "".join(
            ch for ch in str(getattr(request, "gst_code", "") or "") if ch.isdigit()
        )

        if str(getattr(request, "gst_code", "") or "").strip() and len(digits) != 2:
            raise HTTPException(
                status_code=400,
                detail="A GST state code is two digits, for example 23.",
            )

        st = State(
            name=name,
            code=code_upper,
            gst_code=digits or None,
            country=request.country.strip() if request.country else "India",
        )
        db.add(st)
        db.commit()
        db.refresh(st)

        StateService.publish_codes(db)

        return st

    @staticmethod
    def seed_default_states(db: Session) -> int:
        """Add any state that is missing, and leave the rest alone.

        It used to rewrite every seeded row's name on each call, which
        quietly undid a correction somebody had made on the Masters
        screen. Now it only ever adds.
        """

        existing_codes = {
            (code or "").upper() for (code,) in db.query(State.code).all()
        }
        existing_names = {
            (name or "").strip().lower() for (name,) in db.query(State.name).all()
        }

        missing = [
            (name, code)
            for name, code in DEFAULT_STATES
            if code.upper() not in existing_codes
            and name.lower() not in existing_names
        ]

        if not missing:
            return 0

        _repair_id_sequence(db)

        for name, code in missing:
            db.add(
                State(
                    name=name,
                    code=code,
                    gst_code=gst.STATE_CODES.get(name.lower()),
                    country="India",
                )
            )

        db.commit()

        return len(missing)

    @staticmethod
    def get_all(db: Session) -> list[State]:
        StateService.seed_default_states(db)
        StateService.publish_codes(db)

        # Alphabetical: the list is long enough now that seeding order is
        # no use to somebody looking for one.
        return db.query(State).order_by(State.name.asc()).all()

    @staticmethod
    def delete(st_id: int, db: Session) -> None:
        st = db.query(State).filter(State.id == st_id).first()

        if not st:
            raise HTTPException(status_code=404, detail="State not found.")

        db.delete(st)
        db.commit()
