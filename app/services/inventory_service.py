import re

from bson import ObjectId
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.database.mongodb import sync_mongo_db
from app.services.company_scope_service import CompanyScopeService


def _clean_warranty_rates(raw) -> dict:
    """{term name: {"mode": "PERCENT"|"AMOUNT", "rate": float}}, sanitised.

    Whatever the form sends is reduced to the two things that decide a
    price. A term with no rate is dropped rather than stored as zero: the
    absence of a figure and a figure of nothing read the same on screen,
    but only one of them means "not offered on this product".
    """

    if not isinstance(raw, dict):
        return {}

    cleaned: dict[str, dict] = {}

    for name, entry in raw.items():
        label = str(name or "").strip()

        if not label or not isinstance(entry, dict):
            continue

        try:
            rate = float(entry.get("rate") or 0)
        except (TypeError, ValueError):
            continue

        if rate <= 0:
            continue

        mode = str(entry.get("mode") or "PERCENT").upper()

        cleaned[label] = {
            "mode": "AMOUNT" if mode == "AMOUNT" else "PERCENT",
            "rate": rate,
        }

    return cleaned


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
        if not serial or not str(serial).strip():
            return False

        query: dict = {"serial_number": str(serial).strip().upper(), "company_id": company_id}
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
        # hsn_code is a standard field rather than a template one: every
        # product has an HSN or SAC, a GST invoice is required to carry it,
        # and it must not depend on whoever set up the product type.
        #
        # dtp_rate is standard for the same reason. "rate" is the end
        # customer price; this is the fixed transfer price a dealer, a
        # distributor or an OEM is bought through at. Which of the two a
        # document uses follows from the customer type, so a product that
        # can be sold through the channel needs both on the same record.
        # model_number and specification are standard too. A product type
        # may or may not define a model field, and what it was called
        # would then depend on whoever set that type up - but every
        # product has a model, it is what a customer reads on a proposal
        # line, and a service call is logged against it.
        standard_fields = [
            "rate", "rate_per_unit", "dtp_rate", "unit",
            "instock", "stock", "case_size", "hsn_code",
            "model_number", "specification",
        ]
        for key in standard_fields:
            if key in attributes:
                val = attributes[key]
                if key in ["rate", "rate_per_unit", "dtp_rate", "instock", "stock", "case_size"]:
                    try:
                        val = float(val)
                    except (ValueError, TypeError):
                        val = 0.0
                else:
                    val = str(val).strip()
                validated_attrs[key] = val

        # The model says which one it is and the specification says what it
        # is. Both are on the proposal line a customer reads, on the sticker
        # on the box and in the list the warehouse scans, so a product
        # without them is one nobody downstream can identify.
        for key, label in (
            ("model_number", "Model Number"),
            ("specification", "Specification"),
        ):
            if not str(validated_attrs.get(key) or "").strip():
                raise HTTPException(
                    status_code=400,
                    detail=f"{label} is required.",
                )

        # What extending the cover costs, per term, for this product. The
        # lengths themselves are a master - everything is quoted on the same
        # three - but the price of them is not: five years on a panel and
        # five years on a camera are different undertakings, so the figure
        # belongs on the product rather than beside the name.
        # What cover costs is a commercial decision, not a stock one. The
        # warehouse maintains products - counts, codes, case sizes - and a
        # warranty rate sitting on the same form put the price of cover
        # within reach of anyone who could correct a stock figure. Only a
        # super admin, who holds Masters, may set it; for anyone else the
        # field is simply not read.
        if "warranty_rates" in attributes and (current_user or {}).get(
            "is_super_admin"
        ):
            validated_attrs["warranty_rates"] = _clean_warranty_rates(
                attributes.get("warranty_rates")
            )

        # 3. Insert to collection
        owner_id = CompanyScopeService.resolve_owner(
            current_user or {}, company_id, db
        )

        # Support multiple serial numbers (separated by / , or newline)
        raw_serials = [
            s.strip().upper()
            for s in re.split(r"[/,\n]+", serial_number or "")
            if s.strip()
        ]
        if not raw_serials:
            raw_serials = [""]

        for s in raw_serials:
            if s and cls._serial_clash(s, owner_id):
                raise HTTPException(
                    status_code=400,
                    detail=f"Inventory item with serial number '{s}' already exists for this company.",
                )

        initial_stock = float(validated_attrs.get("instock", 0.0) or 0.0)
        stock_each = 1.0 if len(raw_serials) > 1 and initial_stock in (float(len(raw_serials)), 1.0, 0.0) else (initial_stock / len(raw_serials) if len(raw_serials) > 1 else initial_stock)

        names = CompanyScopeService.company_names(db) if db else {}
        docs = []
        for s in raw_serials:
            item_attrs = dict(validated_attrs)
            if len(raw_serials) > 1:
                item_attrs["instock"] = stock_each
                item_attrs["stock"] = stock_each
            docs.append({
                "name": name.strip(),
                "serial_number": s,
                "product_type_code": product_type_code,
                "category": category.strip(),
                "attributes": item_attrs,
                "image_base64": image_base64,
                "company_id": owner_id,
            })

        if len(docs) > 1:
            cls.items_col.insert_many(docs)
            primary_doc = dict(docs[0])
            primary_doc["attributes"]["instock"] = initial_stock if initial_stock > 0 else float(len(raw_serials))
            primary_doc["attributes"]["stock"] = primary_doc["attributes"]["instock"]
            return cls._decorate(primary_doc, names)
        else:
            cls.items_col.insert_one(docs[0])
            return cls._decorate(docs[0], names)

    #: How many units a product holds, whichever field it was written to.
    _HELD = {
        "$convert": {
            "input": {"$ifNull": ["$attributes.instock", "$attributes.stock"]},
            "to": "double",
            "onError": 0,
            "onNull": 0,
        }
    }

    _RATE = {
        "$convert": {
            "input": "$attributes.rate",
            "to": "double",
            "onError": 0,
            "onNull": 0,
        }
    }

    @classmethod
    def picker_catalogue(cls) -> list[dict]:
        """Every product on the shelf, one row per model, for the Add Product pickers.

        Not scoped to the caller's companies: a salesperson quotes whatever
        the business holds, whichever company it is filed under, and most
        profiles carry no company at all. No serial numbers either - a
        picker needs the model, its rates and how many there are.
        """

        def text(field: str) -> dict:
            return {"$trim": {"input": {"$toString": {"$ifNull": [field, ""]}}}}

        # The model, or the name when the model is missing or blank - the
        # sheet leaves Model Number optional.
        model_key = {
            "$toLower": {
                "$cond": [
                    {"$eq": [text("$attributes.model_number"), ""]},
                    text("$name"),
                    text("$attributes.model_number"),
                ]
            }
        }

        pipeline = [
            {"$match": {"issued": {"$ne": True}}},
            {
                "$group": {
                    "_id": model_key,
                    "name": {"$first": "$name"},
                    "model_number": {"$first": "$attributes.model_number"},
                    "product_type_code": {"$first": "$product_type_code"},
                    "category": {"$first": "$category"},
                    # The first record's rate, the one the server prices a
                    # line by, so the picker and the saved line agree.
                    "rate": {"$first": cls._RATE},
                    "rate_per_unit": {"$first": "$attributes.rate_per_unit"},
                    "dtp_rate": {"$first": "$attributes.dtp_rate"},
                    "hsn_code": {"$first": "$attributes.hsn_code"},
                    "instock": {"$sum": cls._HELD},
                }
            },
            {"$sort": {"name": 1, "model_number": 1}},
        ]

        # Read in the order the server's price lookup reads, so "first"
        # means the same record for both.
        pipeline.insert(1, {"$sort": {"_id": 1}})

        return [
            {
                "name": row.get("name") or "",
                "model_number": row.get("model_number") or "",
                "product_type_code": row.get("product_type_code") or "",
                "category": row.get("category") or "",
                "rate": row.get("rate") or row.get("rate_per_unit") or 0,
                "dtp_rate": row.get("dtp_rate") or 0,
                "hsn_code": row.get("hsn_code") or "",
                "instock": row.get("instock") or 0,
            }
            for row in cls.items_col.aggregate(pipeline)
            if row["_id"]
        ]

    @classmethod
    def _scope(cls, search, company_id, current_user, db) -> dict:
        """The catalogue this caller is looking at, before any category."""

        query = dict(
            CompanyScopeService.mongo_filter(current_user or {}, db, company_id)
        )

        # A unit issued to an order has left the shelf. Its record stays,
        # for the outgoing trail and in case the order is put back.
        query["issued"] = {"$ne": True}

        if search:
            # Escaped: a serial number with a dot or a bracket in it is a
            # serial number, not a pattern.
            safe = re.escape(str(search).strip())

            query["$or"] = [
                {"name": {"$regex": safe, "$options": "i"}},
                {"serial_number": {"$regex": safe, "$options": "i"}},
                {"category": {"$regex": safe, "$options": "i"}},
                {"attributes.model_number": {"$regex": safe, "$options": "i"}},
            ]

        return query

    @classmethod
    def get_items(
        cls,
        product_type_code: str = None,
        search: str = None,
        company_id: str = None,
        current_user: dict = None,
        db: Session = None,
        page: int = None,
        size: int = None,
        grouped: bool = True,
    ) -> list:
        """One page of the catalogue, or all of it when no page is asked for.

        When grouped is True (default), items sharing the same product model,
        name, category and company are merged into a single product row with
        aggregate in-stock count and the complete list of hardware serial numbers.
        """

        query = cls._scope(search, company_id, current_user, db)

        if product_type_code:
            query["product_type_code"] = product_type_code.upper().strip()

        names = CompanyScopeService.company_names(db) if db else {}

        if not grouped:
            cursor = cls.items_col.find(query, {"image_base64": 0}).sort("_id", -1)
            if page and size:
                cursor = cursor.skip((max(1, page) - 1) * size).limit(size)
            return [
                {**cls._decorate(doc, names), "has_image": True}
                for doc in cursor
            ]

        pipeline = [
            {"$match": query},
            {
                "$group": {
                    "_id": {
                        "company_id": "$company_id",
                        "product_type_code": "$product_type_code",
                        "model_number": {
                            "$toLower": {
                                "$trim": {
                                    "input": {
                                        "$toString": {
                                            "$ifNull": ["$attributes.model_number", ""]
                                        }
                                    }
                                }
                            }
                        },
                        "name": {
                            "$toLower": {
                                "$trim": {
                                    "input": {
                                        "$toString": {
                                            "$ifNull": ["$name", ""]
                                        }
                                    }
                                }
                            }
                        },
                    },
                    "doc_id": {"$first": "$_id"},
                    "item_ids": {"$push": {"$toString": "$_id"}},
                    "name": {"$first": "$name"},
                    "product_type_code": {"$first": "$product_type_code"},
                    "category": {"$first": "$category"},
                    "company_id": {"$first": "$company_id"},
                    "attributes": {"$first": "$attributes"},
                    "serial_numbers": {"$addToSet": "$serial_number"},
                    "primary_serial": {"$first": "$serial_number"},
                    "total_stock": {"$sum": cls._HELD},
                    "max_id": {"$max": "$_id"},
                    "has_image": {
                        "$max": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$ne": ["$image_base64", None]},
                                        {"$ne": ["$image_base64", ""]},
                                    ]
                                },
                                True,
                                False,
                            ]
                        }
                    },
                }
            },
            {"$sort": {"max_id": -1}},
        ]

        if page and size:
            cursor_agg = pipeline + [
                {"$skip": (max(1, page) - 1) * size},
                {"$limit": size},
            ]
        else:
            cursor_agg = pipeline

        cursor = cls.items_col.aggregate(cursor_agg)

        items = []
        for doc in cursor:
            doc["_id"] = str(doc.pop("doc_id"))
            doc["serial_number"] = doc.pop("primary_serial") or ""
            serials = [
                str(s).strip() for s in (doc.get("serial_numbers") or []) if s
            ]
            doc["serial_numbers"] = sorted(serials)
            doc["serials_count"] = len(doc["serial_numbers"])
            attrs = dict(doc.get("attributes") or {})
            total_stock = float(doc.pop("total_stock", 0.0) or 0.0)
            attrs["instock"] = total_stock
            attrs["stock"] = total_stock
            doc["attributes"] = attrs
            has_image = doc.pop("has_image", False)
            doc.pop("max_id", None)
            items.append({**cls._decorate(doc, names), "has_image": bool(has_image)})

        return items

    @classmethod
    def summarise(
        cls,
        search: str = None,
        company_id: str = None,
        current_user: dict = None,
        db: Session = None,
        grouped: bool = True,
    ) -> dict:
        """The figures across the top, and a count per product type.

        Worked out by the database over the whole catalogue rather than by
        adding up the rows that happen to be on screen - which is the only
        way they stay true once the list is a page at a time.
        """

        query = cls._scope(search, company_id, current_user, db)

        if not grouped:
            totals = list(
                cls.items_col.aggregate([
                    {"$match": query},
                    {
                        "$group": {
                            "_id": None,
                            "products": {"$sum": 1},
                            "units": {"$sum": cls._HELD},
                            "value": {"$sum": {"$multiply": [cls._HELD, cls._RATE]}},
                            "thin": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {"$gt": [cls._HELD, 0]},
                                                {"$lte": [cls._HELD, 3]},
                                            ]
                                        },
                                        1,
                                        0,
                                    ]
                                }
                            },
                            "out": {
                                "$sum": {"$cond": [{"$lte": [cls._HELD, 0]}, 1, 0]}
                            },
                        }
                    },
                ])
            )

            counts = {
                str(row["_id"] or ""): row["n"]
                for row in cls.items_col.aggregate([
                    {"$match": query},
                    {"$group": {"_id": "$product_type_code", "n": {"$sum": 1}}},
                ])
            }
        else:
            group_product = {
                "$group": {
                    "_id": {
                        "company_id": "$company_id",
                        "product_type_code": "$product_type_code",
                        "model_number": {
                            "$toLower": {
                                "$trim": {
                                    "input": {
                                        "$toString": {
                                            "$ifNull": ["$attributes.model_number", ""]
                                        }
                                    }
                                }
                            }
                        },
                        "name": {
                            "$toLower": {
                                "$trim": {
                                    "input": {
                                        "$toString": {
                                            "$ifNull": ["$name", ""]
                                        }
                                    }
                                }
                            }
                        },
                    },
                    "product_type_code": {"$first": "$product_type_code"},
                    "stock": {"$sum": cls._HELD},
                    "value": {"$sum": {"$multiply": [cls._HELD, cls._RATE]}},
                }
            }

            totals = list(
                cls.items_col.aggregate([
                    {"$match": query},
                    group_product,
                    {
                        "$group": {
                            "_id": None,
                            "products": {"$sum": 1},
                            "units": {"$sum": "$stock"},
                            "value": {"$sum": "$value"},
                            "thin": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {"$gt": ["$stock", 0]},
                                                {"$lte": ["$stock", 3]},
                                            ]
                                        },
                                        1,
                                        0,
                                    ]
                                }
                            },
                            "out": {
                                "$sum": {"$cond": [{"$lte": ["$stock", 0]}, 1, 0]}
                            },
                        }
                    },
                ])
            )

            counts = {
                str(row["_id"] or ""): row["n"]
                for row in cls.items_col.aggregate([
                    {"$match": query},
                    group_product,
                    {"$group": {"_id": "$product_type_code", "n": {"$sum": 1}}},
                ])
            }

        figures = totals[0] if totals else {}

        return {
            "products": int(figures.get("products") or 0),
            "units": float(figures.get("units") or 0),
            "value": float(figures.get("value") or 0),
            "thin": int(figures.get("thin") or 0),
            "out": int(figures.get("out") or 0),
            "categories": len([code for code, n in counts.items() if n]),
            "counts": counts,
        }

    @classmethod
    def image_for(cls, item_id: str, current_user: dict = None, db: Session = None):
        """One product's picture, fetched only when something shows it."""

        _, item = cls._get_in_scope(item_id, current_user or {}, db)

        return item.get("image_base64")

    @classmethod
    def update_item(cls, item_id: str, name: str, serial_number: str, product_type_code: str, category: str, attributes: dict, image_base64: str = None, company_id: str = None, company_set: bool = False, image_set: bool = False, current_user: dict = None, db: Session = None, serial_numbers: list[str] | None = None) -> dict:
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
        # hsn_code is a standard field rather than a template one: every
        # product has an HSN or SAC, a GST invoice is required to carry it,
        # and it must not depend on whoever set up the product type.
        #
        # dtp_rate is standard for the same reason. "rate" is the end
        # customer price; this is the fixed transfer price a dealer, a
        # distributor or an OEM is bought through at. Which of the two a
        # document uses follows from the customer type, so a product that
        # can be sold through the channel needs both on the same record.
        # model_number and specification are standard too. A product type
        # may or may not define a model field, and what it was called
        # would then depend on whoever set that type up - but every
        # product has a model, it is what a customer reads on a proposal
        # line, and a service call is logged against it.
        standard_fields = [
            "rate", "rate_per_unit", "dtp_rate", "unit",
            "instock", "stock", "case_size", "hsn_code",
            "model_number", "specification",
        ]
        for key in standard_fields:
            if key in attributes:
                val = attributes[key]
                if key in ["rate", "rate_per_unit", "dtp_rate", "instock", "stock", "case_size"]:
                    try:
                        val = float(val)
                    except (ValueError, TypeError):
                        val = 0.0
                else:
                    val = str(val).strip()
                validated_attrs[key] = val

        # The model says which one it is and the specification says what it
        # is. Both are on the proposal line a customer reads, on the sticker
        # on the box and in the list the warehouse scans, so a product
        # without them is one nobody downstream can identify.
        for key, label in (
            ("model_number", "Model Number"),
            ("specification", "Specification"),
        ):
            if not str(validated_attrs.get(key) or "").strip():
                raise HTTPException(
                    status_code=400,
                    detail=f"{label} is required.",
                )

        # What extending the cover costs, per term, for this product. The
        # lengths themselves are a master - everything is quoted on the same
        # three - but the price of them is not: five years on a panel and
        # five years on a camera are different undertakings, so the figure
        # belongs on the product rather than beside the name.
        # Only a super admin may move what cover costs - see create_item.
        # Everyone else's save keeps whatever is already stored, rather than
        # silently clearing it by sending a form that never had the figures.
        if (current_user or {}).get("is_super_admin"):
            if "warranty_rates" in attributes:
                validated_attrs["warranty_rates"] = _clean_warranty_rates(
                    attributes.get("warranty_rates")
                )
        else:
            kept = (existing.get("attributes") or {}).get("warranty_rates")

            if kept:
                validated_attrs["warranty_rates"] = kept

        # Moving a product to another company is allowed, but only to one the
        # caller is in; leaving the field out keeps it where it is.
        owner_id = (
            CompanyScopeService.assert_company_allowed(current_user or {}, company_id, db)
            if company_set
            else existing.get("company_id")
        )

        # The product's units: the record being edited and every sibling
        # sharing its model, name, type and company. The list shows them as
        # one row, so the form's In Stock is their total.
        existing_model = str((existing.get("attributes") or {}).get("model_number") or "").strip().lower()
        existing_name = str(existing.get("name") or "").strip().lower()
        existing_company = existing.get("company_id")
        existing_type = existing.get("product_type_code")

        sibling_query: dict = {
            "_id": {"$ne": obj_id},
            "company_id": existing_company,
            "product_type_code": existing_type,
            "name": {"$regex": f"^{re.escape(existing_name)}$", "$options": "i"},
        }
        if existing_model:
            sibling_query["attributes.model_number"] = {
                "$regex": f"^{re.escape(existing_model)}$", "$options": "i"
            }

        siblings = list(cls.items_col.find(sibling_query, {"image_base64": 0}))

        plan = cls._plan_units(
            existing, siblings, serial_number, serial_numbers,
            validated_attrs.get("instock"), owner_id,
        )

        primary_serial = plan["primary_serial"]

        # Update document
        doc = {
            "name": name.strip(),
            "serial_number": primary_serial,
            "product_type_code": product_type_code,
            "category": category.strip(),
            "attributes": {
                **validated_attrs,
                "instock": plan["primary_stock"],
                "stock": plan["primary_stock"],
            },
            "company_id": owner_id,
        }

        # The picture is only touched when the form sent one. The list no
        # longer carries images - they are up to two megabytes each - so a
        # form that never saw the picture must not be able to erase it by
        # saving a change of name.
        if image_set:
            doc["image_base64"] = image_base64

        cls.items_col.update_one({"_id": obj_id}, {"$set": doc})

        # Synchronize common product attributes across sibling serial units if any exist
        shared_attrs = {
            f"attributes.{k}": v
            for k, v in validated_attrs.items()
            if k not in ["instock", "stock"]
        }
        update_siblings = {
            "name": name.strip(),
            "product_type_code": product_type_code,
            "category": category.strip(),
            "company_id": owner_id,
            **shared_attrs,
        }
        if image_set:
            update_siblings["image_base64"] = image_base64

        cls.items_col.update_many(sibling_query, {"$set": update_siblings})

        # Then the units themselves: serials renamed, removed and added,
        # and the stock left where the plan put it.
        for unit_id, serial in plan["renames"]:
            cls.items_col.update_one({"_id": unit_id}, {"$set": {"serial_number": serial}})

        # New serials copy the edited record, so they are added before it
        # can be removed.
        if plan["adds"]:
            template = cls.items_col.find_one({"_id": obj_id}) or doc
            cls.items_col.insert_many([
                {
                    **{k: v for k, v in template.items() if k != "_id"},
                    "serial_number": serial,
                    "attributes": {
                        **(template.get("attributes") or {}),
                        "instock": plan["first_add_stock"] if index == 0 else 1.0,
                        "stock": plan["first_add_stock"] if index == 0 else 1.0,
                    },
                }
                for index, serial in enumerate(plan["adds"])
            ])

        if plan["deletes"]:
            cls.items_col.delete_many({"_id": {"$in": plan["deletes"]}})

        for unit_id, quantity in plan["stock"]:
            cls.items_col.update_one(
                {"_id": unit_id},
                {"$set": {"attributes.instock": quantity, "attributes.stock": quantity}},
            )

        return cls._decorate(
            {**doc, "_id": obj_id}, CompanyScopeService.company_names(db) if db else {}
        )

    @classmethod
    def _plan_units(cls, primary, siblings, serial_number, serial_numbers, total, owner_id) -> dict:
        """Work out every change to a product's units before writing any.

        ``total`` is the product's In Stock as the form shows it - the sum
        over all its units. It used to be written onto the one record being
        edited while the siblings kept their own counts, so each save added
        the rest of the stock on top again. The record being edited now
        takes whatever the others do not hold.

        ``serial_numbers``, when sent, is the product's whole list: serials
        missing from it are removed, new ones added, and a removed one is
        reused for an added one when a typo is corrected. The count must then
        equal the list - one unit per serial - and each record is set to one.
        Left out, the single ``serial_number`` names the edited record as
        before.

        Nothing is written here, so a refusal leaves the product untouched.
        """

        def held(unit) -> float:
            attributes = unit.get("attributes") or {}
            value = attributes.get("instock", attributes.get("stock"))
            try:
                return float(value or 0)
            except (TypeError, ValueError):
                return 0.0

        primary_id = primary["_id"]
        group_ids = [primary_id] + [unit["_id"] for unit in siblings]

        def clash(serial: str) -> bool:
            return cls.items_col.find_one({
                "serial_number": serial,
                "company_id": owner_id,
                "_id": {"$nin": group_ids},
            }) is not None

        renames: list = []
        deletes: list = []
        adds: list[str] = []
        primary_serial = str(primary.get("serial_number") or "").strip().upper()
        primary_kept = True

        if serial_numbers is None:
            primary_serial = (serial_number or "").strip().upper()

            if primary_serial and cls._serial_clash(primary_serial, owner_id, exclude=primary_id):
                raise HTTPException(
                    status_code=400,
                    detail=f"Inventory item with serial number '{serial_number}' already exists for this company.",
                )

            remaining = [(unit["_id"], held(unit)) for unit in siblings]
        else:
            wanted: list[str] = []
            for raw in serial_numbers:
                for piece in re.split(r"[/,\n]+", str(raw or "")):
                    serial = piece.strip().upper()
                    if serial and serial not in wanted:
                        wanted.append(serial)

            if not wanted:
                raise HTTPException(status_code=400, detail="Keep at least one serial number on the product.")

            for serial in wanted:
                if clash(serial):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Serial number '{serial}' already belongs to another product in this company.",
                    )

            units = [primary] + siblings
            have = {str(u.get("serial_number") or "").strip().upper() for u in units}
            to_add = [serial for serial in wanted if serial not in have]
            dropped = [
                u for u in units
                if str(u.get("serial_number") or "").strip()
                and str(u.get("serial_number") or "").strip().upper() not in wanted
            ]

            # A removed serial is reused for an added one before anything
            # is deleted or created.
            while dropped and to_add:
                unit, serial = dropped.pop(0), to_add.pop(0)
                if unit["_id"] == primary_id:
                    primary_serial = serial
                else:
                    renames.append((unit["_id"], serial))

            for unit in dropped:
                if unit["_id"] == primary_id:
                    primary_kept = False
                else:
                    deletes.append(unit["_id"])

            adds = to_add
            remaining = [
                (unit["_id"], held(unit)) for unit in siblings if unit["_id"] not in deletes
            ]

        primary_stock = held(primary)
        stock: list = []
        first_add_stock = 1.0

        if serial_numbers is not None:
            # A serialised product holds one unit per serial number, so the
            # count and the list must agree. A count above the list is
            # stock nobody can name; below it, serials with nothing behind
            # them.
            if total is not None and float(total) != len(wanted):
                difference = int(abs(float(total) - len(wanted)))
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"In stock is {float(total):g} but {len(wanted)} serial number"
                        f"{'' if len(wanted) == 1 else 's'} are listed. "
                        + (
                            f"Add {difference} more serial number{'' if difference == 1 else 's'}"
                            if float(total) > len(wanted)
                            else f"Remove {difference} serial number{'' if difference == 1 else 's'}"
                        )
                        + f", or set In stock to {len(wanted)}."
                    ),
                )

            # Every unit then holds exactly one; a record left without a
            # serial holds none.
            primary_stock = 1.0
            stock = [
                (unit["_id"], 1.0 if str(unit.get("serial_number") or "").strip() else 0.0)
                for unit in siblings
                if unit["_id"] not in deletes
            ]
            if not primary_kept:
                stock = [entry for entry in stock if entry[0] != primary_id]

        elif total is not None:
            total = float(total)
            others = sum(quantity for _, quantity in remaining)

            # The record being edited takes whatever its siblings do not
            # hold, so the product's total is what was typed.
            primary_stock = total - others

            if primary_stock < 0:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"In stock can't be lower than {others:g}: that many units are "
                        "recorded under this product's other serial numbers."
                    ),
                )

        if not primary_kept:
            deletes.append(primary_id)

        return {
            "primary_serial": primary_serial,
            "primary_stock": primary_stock,
            "renames": renames,
            "deletes": deletes,
            "adds": adds,
            "stock": stock,
            "first_add_stock": first_add_stock,
        }

    @classmethod
    def delete_item(cls, item_id: str, all_units: bool = False, current_user: dict = None, db: Session = None) -> None:
        """
        Removes an inventory document item by ID.
        If all_units is True, removes all serial units belonging to that product model.
        """
        obj_id, existing = cls._get_in_scope(item_id, current_user or {}, db)

        if all_units:
            existing_model = str((existing.get("attributes") or {}).get("model_number") or "").strip().lower()
            existing_name = str(existing.get("name") or "").strip().lower()
            existing_company = existing.get("company_id")
            existing_type = existing.get("product_type_code")

            del_query: dict = {
                "company_id": existing_company,
                "product_type_code": existing_type,
                "name": {"$regex": f"^{re.escape(existing_name)}$", "$options": "i"},
            }
            if existing_model:
                del_query["attributes.model_number"] = {
                    "$regex": f"^{re.escape(existing_model)}$", "$options": "i"
                }
            cls.items_col.delete_many(del_query)
        else:
            cls.items_col.delete_one({"_id": obj_id})
