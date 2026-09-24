from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.customer_type import CustomerType
from app.schemas.customer_type import CreateCustomerTypeRequest


class CustomerTypeService:
    @staticmethod
    def create(request: CreateCustomerTypeRequest, db: Session) -> CustomerType:
        code_upper = request.code.upper().strip()
        
        # Check duplicate code
        exists = db.query(CustomerType).filter(CustomerType.code == code_upper).first()
        if exists:
            raise HTTPException(status_code=400, detail=f"Customer type code '{request.code}' already exists.")

        ct = CustomerType(
            name=request.name.strip(),
            code=code_upper,
            description=request.description.strip() if request.description else None
        )
        db.add(ct)
        db.commit()
        db.refresh(ct)
        return ct

    #: Institution was replaced by Dealer. It is no longer seeded and is
    #: dropped on the next seed once nothing refers to it - records raised
    #: while it existed keep resolving until then.
    RETIRED_CODES = ("INSTITUTION",)

    @staticmethod
    def seed_default_customer_types(db: Session):
        defaults = [
            {"id": 1, "name": "Distributor", "code": "DISTRIBUTOR", "description": "Wholesale distribution partner"},
            {"id": 2, "name": "OEM", "code": "OEM", "description": "Original equipment manufacturer"},
            {"id": 3, "name": "End Customer", "code": "END_CUSTOMER", "description": "Direct end user or consumer"},
            {"id": 5, "name": "Corporate", "code": "CORPORATE", "description": "Corporate enterprise client"},
            {"id": 6, "name": "Other", "code": "OTHER", "description": "Any customer that does not fit the types above"},
            {"id": 7, "name": "Dealer", "code": "DEALER", "description": "Reseller buying at dealer price"},
        ]
        for d in defaults:
            exists = db.query(CustomerType).filter(CustomerType.code == d["code"]).first()
            if not exists:
                # Keep the well-known id when it is free, but let the sequence
                # pick one when a type someone added has already taken it -
                # a clash would otherwise fail the whole seed on every read.
                id_taken = db.query(CustomerType).filter(CustomerType.id == d["id"]).first()
                db.add(CustomerType(
                    **({} if id_taken else {"id": d["id"]}),
                    name=d["name"],
                    code=d["code"],
                    description=d["description"],
                ))
            else:
                exists.name = d["name"]

        CustomerTypeService._retire(db)

        db.commit()

    @staticmethod
    def _retire(db: Session) -> None:
        """Drop a replaced type once nothing refers to it.

        Deleting one that is still referenced would break the records that
        point at it, so it is left in place and simply stops being offered
        by the forms until those records have moved on.
        """

        from sqlalchemy import text

        for code in CustomerTypeService.RETIRED_CODES:
            row = db.query(CustomerType).filter(CustomerType.code == code).first()

            if row is None:
                continue

            referenced = any(
                db.execute(
                    text(f"select 1 from {table} where customer_type_id = :id limit 1"),
                    {"id": row.id},
                ).first()
                for table in ("sales_lead", "sales_opportunity", "sales_quotation")
            )

            if not referenced:
                db.delete(row)

    @staticmethod
    def get_all(db: Session) -> list[CustomerType]:
        """The types the forms may offer.

        A retired type is left in the table so records raised while it
        existed still resolve, but it is not offered for anything new.
        """

        CustomerTypeService.seed_default_customer_types(db)

        return (
            db.query(CustomerType)
            .filter(CustomerType.code.notin_(CustomerTypeService.RETIRED_CODES))
            .order_by(CustomerType.id.asc())
            .all()
        )

    @staticmethod
    def delete(ct_id: int, db: Session) -> None:
        ct = db.query(CustomerType).filter(CustomerType.id == ct_id).first()
        if not ct:
            raise HTTPException(status_code=404, detail="Customer Type not found.")
        
        db.delete(ct)
        db.commit()
