from sqlalchemy.orm import Session

from app.core.references import highest_issued, next_number
from app.models.sales_order import SalesOrder


class SalesOrderRepository:

    @staticmethod
    def create(db: Session, order: SalesOrder) -> SalesOrder:
        db.add(order)
        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def save(db: Session, order: SalesOrder) -> SalesOrder:
        db.add(order)
        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def delete(db: Session, order: SalesOrder) -> None:
        db.delete(order)
        db.commit()

    @staticmethod
    def get_by_id(db: Session, order_id: int) -> SalesOrder | None:
        return db.query(SalesOrder).filter(SalesOrder.id == order_id).first()

    @staticmethod
    def get_by_opportunity(db: Session, opportunity_id: int) -> list[SalesOrder]:
        return (
            db.query(SalesOrder)
            .filter(SalesOrder.opportunity_id == opportunity_id)
            .order_by(SalesOrder.id.desc())
            .all()
        )

    @staticmethod
    def get_visible(
        db: Session,
        visible_creator_ids: list[str],
    ) -> list[SalesOrder]:
        """Return sales orders the caller may see.

        An empty ``visible_creator_ids`` means unrestricted super admin access,
        matching ``get_visible_creator_user_ids``. Rows with no creator (rows
        migrated over from the previous Mongo collection) stay visible so the
        migration does not hide historical orders.
        """

        from uuid import UUID

        from sqlalchemy import or_

        query = db.query(SalesOrder)

        if visible_creator_ids:
            creator_uuids = [UUID(uid) for uid in visible_creator_ids]
            query = query.filter(
                or_(
                    SalesOrder.creator_id.in_(creator_uuids),
                    SalesOrder.creator_id.is_(None),
                )
            )

        return query.order_by(SalesOrder.id.desc()).all()

    @staticmethod
    def next_order_number(db: Session) -> str:
        """Allocate the next SO-XXXXX reference.

        Taken from a counter that only moves forward. It used to be the
        highest row plus one, which handed a deleted order's number to the
        next one raised - SO-00036 had been seven different orders, and
        every stock movement they made is filed under that one name.
        """

        return "SO-{:05d}".format(
            next_number(
                db,
                "sales_order",
                lambda: highest_issued(db, SalesOrder.order_number),
            )
        )
