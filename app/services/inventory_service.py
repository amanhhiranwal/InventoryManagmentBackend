from bson import ObjectId
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.database.mongodb import sync_mongo_db
from app.services.company_scope_service import CompanyScopeService


class InventoryService:
    """Products, filed under the company that stocks them.

    Two companies can carry the same product: each gets its own item, and
    each only sees its own, so a serial number is unique within a company
    rather than across the whole database. An item with no company is shared
    with every company - items created before companies were tracked are
    like that.
    """

    templates_col = sync_mongo_db["inventory_templates"]
    items_col = sync_mongo_db["inventory_items"]

    @classmethod
    def _decorate(cls, doc: dict, names: dict[str, str]) -> dict:
        doc["_id"] = str(doc["_id"])
        company_id = doc.get("company_id")
        doc["company_id"] = company_id
        doc["company_name"] = names.get(str(company_id)) if company_id else None
        return doc

    @classmethod
    def _serial_clash(cls, serial: str, company_id: str | None, exclude=None) -> bool:
        """Whether that serial is already used inside the same company."""

        query: dict = {"serial_number": serial, "company_id": company_id}
        if exclude is not None:
            query["_id"] = {"$ne": exclude}
        return cls.items_col.find_one(query) is not None

    @classmethod
    def _get_in_scope(cls, item_id: str, current_user: dict, db: Session):
        """An item by id, refused when it belongs to another company."""

        try:
            obj_id = ObjectId(item_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid MongoDB item ID format.")

        item = cls.items_col.find_one({"_id": obj_id})
        if item is None:
            raise HTTPException(status_code=404, detail="Inventory item not found.")

        allowed = CompanyScopeService.visible_company_ids(current_user, db)
        company_id = item.get("company_id")

        if allowed is not None and company_id and str(company_id) not in allowed:
            raise HTTPException(
                status_code=403,
                detail="This product belongs to a company you are not assigned to.",
            )

        return obj_id, item

    @classmethod
    def save_template(cls, product_type_code: str, fields: list) -> dict:
        """
        Saves or updates the dynamic fields layout template for a product type.
        """
        product_type_code = product_type_code.upper().strip()
        doc = {
            "product_type_code": product_type_code,
            "fields": fields
        }
        cls.templates_col.update_one(
            {"product_type_code": product_type_code},
            {"$set": doc},
            upsert=True
        )
        return doc

    @classmethod
    def get_template(cls, product_type_code: str) -> dict:
        """
        Retrieves the dynamic fields layout template for a product type.
        """
        product_type_code = product_type_code.upper().strip()
        template = cls.templates_col.find_one({"product_type_code": product_type_code})
        if not template:
            return {"product_type_code": product_type_code, "fields": []}
        
        # Serialize ObjectID
        template["_id"] = str(template["_id"])
        return template

    @classmethod
    def create_item(cls, name: str, serial_number: str, product_type_code: str, category: str, attributes: dict, image_base64: str = None, company_id: str = None, current_user: dict = None, db: Session = None) -> dict:
        """
        Creates a dynamic inventory item, validating inputs against its template fields.
        """
        product_type_code = product_type_code.upper().strip()
        
        # 1. Fetch field template validation
        template = cls.get_template(product_type_code)
        fields = template.get("fields", [])

        validated_attrs = {}
        for f in fields:
            f_name = f.get("name")
            f_label = f.get("label", f_name)
            f_type = f.get("type", "text")
            f_required = f.get("required", False)

            val = attributes.get(f_name)

            # Check if required
            if f_required and (val is None or str(val).strip() == ""):
                raise HTTPException(status_code=400, detail=f"Attribute '{f_label}' is required for category {product_type_code}.")

            if val is not None:
                # Type cast check
                if f_type == "number":
                    try:
                        val = float(val)
                    except ValueError:
                        raise HTTPException(status_code=400, detail=f"Attribute '{f_label}' must be a numeric value.")
                elif f_type == "boolean":
                    val = bool(val)
                else:
                    val = str(val).strip()
                
                validated_attrs[f_name] = val

        # 2. Explicitly preserve standard stock parameters if passed in attributes
        standard_fields = ["rate", "rate_per_unit", "unit", "instock", "stock", "case_size"]
        for key in standard_fields:
            if key in attributes:
                val = attributes[key]
                if key in ["rate", "rate_per_unit", "instock", "stock", "case_size"]:
                    try:
                        val = float(val)
                    except (ValueError, TypeError):
                        val = 0.0
                else:
                    val = str(val).strip()
                validated_attrs[key] = val

        # 3. Insert to collection
        owner_id = CompanyScopeService.resolve_owner(
            current_user or {}, company_id, db
        )

        doc = {
            "name": name.strip(),
            "serial_number": serial_number.strip().upper(),
            "product_type_code": product_type_code,
            "category": category.strip(),
            "attributes": validated_attrs,
            "image_base64": image_base64,
            "company_id": owner_id,
        }

        # The same serial may exist under another company - this is the same
        # product stocked twice, not a duplicate.
        if cls._serial_clash(doc["serial_number"], owner_id):
            raise HTTPException(status_code=400, detail=f"Inventory item with serial number '{serial_number}' already exists for this company.")

        cls.items_col.insert_one(doc)
        return cls._decorate(doc, CompanyScopeService.company_names(db))

    @classmethod
    def get_items(cls, product_type_code: str = None, search: str = None, company_id: str = None, current_user: dict = None, db: Session = None) -> list:
        """
        Retrieves all items from database. Matches query params.
        """
        query = dict(
            CompanyScopeService.mongo_filter(current_user or {}, db, company_id)
        )
        if product_type_code:
            query["product_type_code"] = product_type_code.upper().strip()
        
        if search:
            query["$or"] = [
                {"name": {"$regex": search, "$options": "i"}},
                {"serial_number": {"$regex": search, "$options": "i"}},
                {"category": {"$regex": search, "$options": "i"}}
            ]

        names = CompanyScopeService.company_names(db)
        cursor = cls.items_col.find(query).sort("_id", -1)
        return [cls._decorate(doc, names) for doc in cursor]

    @classmethod
    def update_item(cls, item_id: str, name: str, serial_number: str, product_type_code: str, category: str, attributes: dict, image_base64: str = None, company_id: str = None, company_set: bool = False, current_user: dict = None, db: Session = None) -> dict:
        """
        Updates a dynamic inventory item, validating inputs against its template fields.
        """
        obj_id, existing = cls._get_in_scope(item_id, current_user or {}, db)

        product_type_code = product_type_code.upper().strip()
        
        # 1. Fetch field template validation
        template = cls.get_template(product_type_code)
        fields = template.get("fields", [])

        validated_attrs = {}
        for f in fields:
            f_name = f.get("name")
            f_label = f.get("label", f_name)
            f_type = f.get("type", "text")
            f_required = f.get("required", False)

            val = attributes.get(f_name)

            # Check if required
            if f_required and (val is None or str(val).strip() == ""):
                raise HTTPException(status_code=400, detail=f"Attribute '{f_label}' is required for category {product_type_code}.")

            if val is not None:
                # Type cast check
                if f_type == "number":
                    try:
                        val = float(val)
                    except ValueError:
                        raise HTTPException(status_code=400, detail=f"Attribute '{f_label}' must be a numeric value.")
                elif f_type == "boolean":
                    val = bool(val)
                else:
                    val = str(val).strip()
                
                validated_attrs[f_name] = val

        # 2. Explicitly preserve standard stock parameters if passed in attributes
        standard_fields = ["rate", "rate_per_unit", "unit", "instock", "stock", "case_size"]
        for key in standard_fields:
            if key in attributes:
                val = attributes[key]
                if key in ["rate", "rate_per_unit", "instock", "stock", "case_size"]:
                    try:
                        val = float(val)
                    except (ValueError, TypeError):
                        val = 0.0
                else:
                    val = str(val).strip()
                validated_attrs[key] = val

        # Moving a product to another company is allowed, but only to one the
        # caller is in; leaving the field out keeps it where it is.
        owner_id = (
            CompanyScopeService.assert_company_allowed(current_user or {}, company_id, db)
            if company_set
            else existing.get("company_id")
        )

        # Check duplicate serial number within the same company
        serial_upper = serial_number.strip().upper()
        if cls._serial_clash(serial_upper, owner_id, exclude=obj_id):
            raise HTTPException(status_code=400, detail=f"Inventory item with serial number '{serial_number}' already exists for this company.")

        # Update document
        doc = {
            "name": name.strip(),
            "serial_number": serial_upper,
            "product_type_code": product_type_code,
            "category": category.strip(),
            "attributes": validated_attrs,
            "image_base64": image_base64,
            "company_id": owner_id,
        }

        cls.items_col.update_one({"_id": obj_id}, {"$set": doc})

        return cls._decorate(
            {**doc, "_id": obj_id}, CompanyScopeService.company_names(db)
        )

    @classmethod
    def delete_item(cls, item_id: str, current_user: dict = None, db: Session = None) -> None:
        """
        Removes an inventory document item by ID.
        """
        obj_id, _ = cls._get_in_scope(item_id, current_user or {}, db)
        cls.items_col.delete_one({"_id": obj_id})
