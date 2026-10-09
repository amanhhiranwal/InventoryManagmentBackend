"""Who may open a stored file.

The file itself says nothing about who it belongs to - it is a uuid on
disk. So the question "may this person read it" is answered by finding
the record that carries the key and asking that record the question it
already knows how to answer.

That is deliberate. Every document type has its own rule about who may
see it: a sales order follows the reporting line but also lets the
accounts and inventory desks in while they are holding it; a proposal
does not. Writing a second rulebook here would mean two answers to the
same question, and the one guarding the file would be the one nobody
remembers to update. So this one asks the services themselves.

Until a record exists the key belongs to whoever uploaded it, because a
file is stored the moment it is chosen - before the order it will hang
off has been saved. That window is covered by the uploader check, which
also lets their managers in, exactly as a saved record would.
"""

import logging

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.attachment import Attachment

logger = logging.getLogger(__name__)

#: table -> the column the attachments are kept in. The JSON is matched
#: as text: a key is a uuid4 hex, so a substring match cannot collide
#: with anything else in the document, and this works the same whether
#: the column is JSON or the lead's packed description.
SOURCES = [
    ("sales_order", "attachments"),
    ("sales_quotation", "attachments"),
    ("sales_proforma_invoice", "attachments"),
    ("sales_opportunity", "attachments"),
    ("sales_lead", "description"),
]


def _guard_for(table: str):
    """The record's own "may this person open it" check."""

    if table == "sales_order":
        from app.services.sales_order_service import SalesOrderService

        return SalesOrderService.get_by_id, SalesOrderService.assert_can_edit

    if table == "sales_quotation":
        from app.services.quotation_service import QuotationService

        return QuotationService.get_by_id, QuotationService.assert_can_modify

    if table == "sales_proforma_invoice":
        from app.services.proforma_invoice_service import ProformaInvoiceService

        return ProformaInvoiceService.get_by_id, ProformaInvoiceService.assert_can_edit

    if table == "sales_opportunity":
        from app.services.opportunity_service import OpportunityService

        return OpportunityService.get_by_id, OpportunityService.assert_can_edit

    if table == "sales_lead":
        from app.models.lead import Lead
        from app.services.lead_service import LeadService

        def load_lead(lead_id, db):
            return db.query(Lead).filter(Lead.id == int(lead_id)).first()

        return load_lead, LeadService.assert_can_modify_lead

    return None, None


def holders(key: str, db: Session) -> list[tuple[str, int]]:
    """Every record carrying this key, as (table, id)."""

    found: list[tuple[str, int]] = []

    for table, column in SOURCES:
        try:
            rows = db.execute(
                text(
                    f"SELECT id FROM {table} "
                    f"WHERE CAST({column} AS TEXT) LIKE :needle"
                ),
                {"needle": f"%{key}%"},
            ).fetchall()
        except Exception as error:  # noqa: BLE001 - a missing table is not fatal
            db.rollback()
            logger.debug("Could not search %s for an attachment: %s", table, error)
            continue

        found.extend((table, row[0]) for row in rows)

    return found


def may_read(key: str, current_user: dict, db: Session) -> bool:
    """True when this person may open the file stored under this key."""

    if current_user.get("is_super_admin"):
        return True

    record = db.query(Attachment).filter(Attachment.key == key).first()

    # Their own upload, which is the only answer available before the
    # record it belongs to has been saved.
    if record and str(record.uploaded_by or "") == str(
        current_user.get("user_id") or ""
    ):
        return True

    # Anybody whose work this person oversees. Their manager can open the
    # record it is about to be attached to, so the file is no different.
    if record and record.uploaded_by:
        from app.services.hierarchy_service import HierarchyService

        if HierarchyService.can_see_user(current_user, record.uploaded_by, db):
            return True

    for table, record_id in holders(key, db):
        load, guard = _guard_for(table)

        if load is None:
            continue

        try:
            guard(load(record_id, db), current_user, db)

            return True
        except HTTPException:
            continue
        except Exception as error:  # noqa: BLE001 - a broken guard denies
            logger.warning(
                "Could not check %s %s for attachment access: %s",
                table,
                record_id,
                error,
            )
            continue

    return False


def files_for_email(
    keys,
    table: str,
    record_id: int,
    current_user: dict,
    db: Session,
) -> list[dict]:
    """The chosen attachments, shaped for EmailService.

    The dialog lists the files and lets the sender drop any of them, and
    until now none of that reached the server: the message went with the
    PDF alone, so a customer was told an annexure was attached and never
    got it, and unticking one changed nothing.

    A key is only honoured when it is on the record being sent, or when
    the sender uploaded it themselves - which is how a file added in the
    dialog gets through. Without that, the field would be a way to post
    any stored document in the business to any address.
    """

    from app.models.attachment import Attachment

    ready: list[dict] = []

    for key in list(keys or []):
        key = str(key or "")

        row = db.query(Attachment).filter(Attachment.key == key).first()

        theirs = row is not None and str(row.uploaded_by or "") == str(
            current_user.get("user_id") or ""
        )

        if not theirs and not on_record(key, table, record_id, db):
            logger.warning(
                "Attachment %s was not sent: it is not on %s %s",
                key,
                table,
                record_id,
            )
            continue

        from app.services.attachment_store import media_type, read_bytes

        content = read_bytes(key)

        if content is None:
            logger.warning("Attachment %s was not sent: the file is missing", key)
            continue

        ready.append(
            {
                "filename": (row.name if row else key),
                "content": content,
                "mime_type": (row.content_type if row else None) or media_type(key),
            }
        )

    return ready


def on_record(key: str, table: str, record_id: int, db: Session) -> bool:
    """Whether this key is actually attached to this record.

    Used when emailing: a send names the files to put in the message, and
    this is what stops that naming any stored file in the business.
    """

    column = dict(SOURCES).get(table)

    if column is None:
        return False

    try:
        found = db.execute(
            text(
                f"SELECT 1 FROM {table} WHERE id = :id "
                f"AND CAST({column} AS TEXT) LIKE :needle"
            ),
            {"id": record_id, "needle": f"%{key}%"},
        ).first()
    except Exception as error:  # noqa: BLE001
        db.rollback()
        logger.warning("Could not confirm attachment %s on %s: %s", key, table, error)

        return False

    return found is not None
