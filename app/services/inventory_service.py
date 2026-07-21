from bson import ObjectId
from fastapi import HTTPException
from app.database.mongodb import sync_mongo_db

class InventoryService:
    templates_col = sync_mongo_db["inventory_templates"]
    items_col = sync_mongo_db["inventory_items"]

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
    def create_item(cls, name: str, serial_number: str, product_type_code: str, category: str, attributes: dict, image_base64: str = None) -> dict:
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

        # 2. Insert to collection
        doc = {
            "name": name.strip(),
            "serial_number": serial_number.strip().upper(),
            "product_type_code": product_type_code,
            "category": category.strip(),
            "attributes": validated_attrs,
            "image_base64": image_base64
        }
        
        # Check duplicate serial number
        exists = cls.items_col.find_one({"serial_number": doc["serial_number"]})
        if exists:
            raise HTTPException(status_code=400, detail=f"Inventory item with serial number '{serial_number}' already exists.")

        cls.items_col.insert_one(doc)
        doc["_id"] = str(doc["_id"])
        return doc

    @classmethod
    def get_items(cls, product_type_code: str = None, search: str = None) -> list:
        """
        Retrieves all items from database. Matches query params.
        """
        query = {}
        if product_type_code:
            query["product_type_code"] = product_type_code.upper().strip()
        
        if search:
            query["$or"] = [
                {"name": {"$regex": search, "$options": "i"}},
                {"serial_number": {"$regex": search, "$options": "i"}},
                {"category": {"$regex": search, "$options": "i"}}
            ]

        cursor = cls.items_col.find(query).sort("_id", -1)
        items = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            items.append(doc)
        return items

    @classmethod
    def update_item(cls, item_id: str, name: str, serial_number: str, product_type_code: str, category: str, attributes: dict, image_base64: str = None) -> dict:
        """
        Updates a dynamic inventory item, validating inputs against its template fields.
        """
        try:
            obj_id = ObjectId(item_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid MongoDB item ID format.")

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

        # Check duplicate serial number (excluding current item)
        serial_upper = serial_number.strip().upper()
        exists = cls.items_col.find_one({"serial_number": serial_upper, "_id": {"$ne": obj_id}})
        if exists:
            raise HTTPException(status_code=400, detail=f"Inventory item with serial number '{serial_number}' already exists.")

        # Update document
        res = cls.items_col.update_one(
            {"_id": obj_id},
            {
                "$set": {
                    "name": name.strip(),
                    "serial_number": serial_upper,
                    "product_type_code": product_type_code,
                    "category": category.strip(),
                    "attributes": validated_attrs,
                    "image_base64": image_base64
                }
            }
        )
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="Inventory item not found.")
            
        return {
            "_id": item_id,
            "name": name.strip(),
            "serial_number": serial_upper,
            "product_type_code": product_type_code,
            "category": category.strip(),
            "attributes": validated_attrs,
            "image_base64": image_base64
        }

    @classmethod
    def delete_item(cls, item_id: str) -> None:
        """
        Removes an inventory document item by ID.
        """
        try:
            obj_id = ObjectId(item_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid MongoDB item ID format.")

        res = cls.items_col.delete_one({"_id": obj_id})
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Inventory item not found.")
