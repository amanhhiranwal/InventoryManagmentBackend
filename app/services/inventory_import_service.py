"""Loading a sheet of products instead of typing them one at a time.

The warehouse is handed a catalogue by a supplier, or keeps its own in
Excel. Retyping forty products into a form is how a digit gets dropped, so
the same fields the form asks for are a spreadsheet: one row per product,
the columns the Add Inventory Product screen has, and nothing else.

Two things are worth knowing about it.

**Serial numbers can be several.** A row whose Hardware Serial Number
reads ``SG-IFP-01/SG-IFP-02/SG-IFP-03`` is three products, not one with a
strange name: each serial is saved as its own record, carrying the rest of
the row. That is how a supplier writes a delivery of three identical
panels, and a serial number that only exists inside a slash-separated
string is one nobody can scan, search or sell against.

**Nothing is written until the whole sheet is good.** Every row is checked
first; if any of them cannot be loaded, none of them are, and the reply
says which row and why. A half-loaded sheet is worse than a refused one -
the person loading it has no way of knowing which half.

The sheet carries the standard fields every product has. Anything a
category defines for itself is filled in afterwards, on the product.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.database.mongodb import sync_mongo_db
from app.models.product_type import ProductType
from app.services.company_scope_service import CompanyScopeService
from app.services.inventory_service import InventoryService

#: The columns, in the order they appear on the sheet. ``key`` is what the
#: product record calls the field; ``aliases`` are the headings that have
#: been seen meaning it, because a sheet that has been round a supplier
#: and back comes with "SKU" where the template said "Hardware Serial
#: Number".
COLUMNS: list[dict] = [
    {
        "key": "model_number",
        "header": "Model Number",
        "required": True,
        "aliases": ["model", "model no", "model number"],
        "help": "The manufacturer's model, as it is printed on the box.",
    },
    {
        "key": "name",
        "header": "Product Name",
        "required": True,
        "aliases": ["product name", "item name", "name", "product"],
        "help": "What the product is called on a proposal line.",
    },
    {
        "key": "specification",
        "header": "Specification",
        "required": True,
        "aliases": ["specification", "specifications", "spec", "description"],
        "help": "Free text: size, capacity, finish - whatever a customer asks about.",
    },
    {
        "key": "serial_number",
        "header": "Hardware Serial Number",
        "required": False,
        "aliases": [
            "hardware serial number",
            "serial number",
            "serial no",
            "serial",
            "sku",
        ],
        "help": (
            "Optional. Several on one row, separated by / or comma, are "
            "loaded as that many products. Leave blank for non-serialized stock."
        ),
    },
    {
        "key": "product_type_code",
        "header": "Product Type Code",
        "required": True,
        "aliases": [
            "product type code",
            "product type",
            "type code",
            "category code",
        ],
        "help": "A code from the Product Types master, e.g. IFP.",
    },
    {
        "key": "company",
        "header": "Company",
        "required": False,
        "aliases": ["company", "company name", "entity"],
        "help": "Which company stocks it. Left blank, it is shared with all of them.",
    },
    {
        "key": "rate",
        "header": "ECP Rate",
        "required": True,
        "aliases": ["ecp rate", "rate", "price", "mrp", "end customer price"],
        "help": "The end customer price, per unit, in rupees.",
    },
    {
        "key": "dtp_rate",
        "header": "DTP Rate",
        "required": False,
        "aliases": ["dtp rate", "dealer rate", "dtp", "transfer price"],
        "help": "The dealer transfer price, if the product is sold through the channel.",
    },
    {
        "key": "instock",
        "header": "In-Stock Qty",
        "required": True,
        "aliases": ["in stock qty", "in stock", "stock", "quantity", "qty"],
        "help": (
            "How many are on the shelf. On a row carrying several serial "
            "numbers each one is a single unit, so this is how many serial "
            "numbers there are."
        ),
    },
    {
        "key": "case_size",
        "header": "Case Size",
        "required": True,
        "aliases": ["case size", "case", "pack size", "units per case"],
        "help": "How many go in a case. 1 when they are sold singly.",
    },
    {
        "key": "unit",
        "header": "Unit",
        "required": True,
        "aliases": ["unit", "uom", "units"],
        "help": "From the Units master, e.g. Nos, Kg, Box.",
    },
    {
        "key": "hsn_code",
        "header": "HSN / SAC Code",
        "required": False,
        "aliases": ["hsn sac code", "hsn code", "hsn", "sac", "hsn sac"],
        "help": "HSN for goods, SAC for a service. It has to be on the invoice.",
    },
]

#: Figures, as opposed to text.
NUMERIC = {"rate", "dtp_rate", "instock", "case_size"}

#: Stands in for a cell holding a formula that was never worked out - see
#: _rows_from_xlsx. Not something anybody could type, so it cannot collide
#: with a real value.
UNCALCULATED = "\x00formula"


def _normalise(heading) -> str:
    """A column heading reduced to what it means.

    "HSN / SAC Code *" and "hsn sac code" are the same column; so are
    "In-Stock Qty" and "in stock qty".
    """

    text = str(heading or "").strip().lower()
    text = text.replace("*", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)

    return re.sub(r"\s+", " ", text).strip()


#: key -> the heading it is called on the sheet, for saying which column.
_HEADER_BY_KEY: dict[str, str] = {c["key"]: c["header"] for c in COLUMNS}

#: heading -> key, built once from the aliases above.
_BY_HEADING: dict[str, str] = {}

for column in COLUMNS:
    _BY_HEADING[_normalise(column["header"])] = column["key"]

    for alias in column["aliases"]:
        _BY_HEADING.setdefault(_normalise(alias), column["key"])


@dataclass
class Draft:
    """One product the sheet asks for, as it will be saved."""

    row: int
    name: str
    serial_number: str
    product_type_code: str
    category: str
    company_id: str | None
    company_name: str | None
    attributes: dict = field(default_factory=dict)

    def as_preview(self) -> dict:
        return {
            "row": self.row,
            "name": self.name,
            "serial_number": self.serial_number,
            "product_type_code": self.product_type_code,
            "category": self.category,
            "company_name": self.company_name,
            "model_number": self.attributes.get("model_number", ""),
            "unit": self.attributes.get("unit", ""),
            "rate": self.attributes.get("rate", 0),
            "dtp_rate": self.attributes.get("dtp_rate", 0),
            "instock": self.attributes.get("instock", 0),
            "case_size": self.attributes.get("case_size", 1),
            "hsn_code": self.attributes.get("hsn_code", ""),
            # So the screen can say "5 Years at 10%" before anything is
            # saved, rather than after somebody opens the product.
            "warranty_rates": self.attributes.get("warranty_rates") or {},
        }


def _cell(value) -> str:
    """A cell as text, with Excel's floats for whole numbers tidied up.

    A case size typed as 1 comes back as 1.0, and a product filed under
    "1.0 per case" reads like a mistake because it is one.
    """

    if value is None:
        return ""

    if isinstance(value, float) and value.is_integer():
        return str(int(value))

    return str(value).strip()


def _rows_from_xlsx(content: bytes) -> list[list[str]]:
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover - the image installs it
        raise HTTPException(
            status_code=500,
            detail="This server cannot read .xlsx files. Upload a CSV instead.",
        )

    def grid(data_only: bool) -> list[list[str]]:
        book = load_workbook(io.BytesIO(content), data_only=data_only, read_only=True)

        # The template's own tab if it is there, so the examples and the
        # notes beside it are never read as products.
        sheet = (
            book["Products"] if "Products" in book.sheetnames else book.worksheets[0]
        )

        return [
            [_cell(cell) for cell in row]
            for row in sheet.iter_rows(values_only=True)
        ]

    try:
        rows = grid(True)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="That file could not be opened as a spreadsheet.",
        )

    # A formula is read as the answer Excel last worked out and saved with
    # the file. A sheet written by a program rather than by Excel has the
    # formula and no answer, and the cell then reads as empty - so a
    # quantity sitting in plain sight on screen is reported missing. Said
    # plainly instead: the cell is marked, and whatever needed it says so.
    try:
        written = grid(False)
    except Exception:  # pragma: no cover - it opened once already
        return rows

    for index, row in enumerate(rows):
        if index >= len(written):
            break

        for position, value in enumerate(row):
            if value or position >= len(written[index]):
                continue

            if str(written[index][position]).startswith("="):
                rows[index][position] = UNCALCULATED

    return rows


def _rows_from_csv(content: bytes) -> list[list[str]]:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 decodes anything
        raise HTTPException(status_code=400, detail="That file could not be read.")

    return [[_cell(cell) for cell in row] for row in csv.reader(io.StringIO(text))]


def _find_header(rows: list[list[str]]) -> tuple[int, dict[int, str], dict[int, str]]:
    """Which row is the headings, which column is which field, and the rest.

    Looked for rather than assumed, because the template carries a title
    and a note above the headings, and a sheet that has been edited has
    them somewhere else again.

    The columns that are not one of ours come back too, under their own
    headings. The warranty rates are read from those: what cover costs is
    a column per term, and the terms are a master, so they cannot be
    written down here.
    """

    for index, row in enumerate(rows[:25]):
        mapping: dict[int, str] = {}
        extra: dict[int, str] = {}

        for position, heading in enumerate(row):
            label = str(heading or "").strip()

            if not label:
                continue

            key = _BY_HEADING.get(_normalise(label))

            # First column wins, so a sheet with two "Rate" columns is not
            # read off the second one.
            if key and key not in mapping.values():
                mapping[position] = key
            elif not key:
                extra[position] = label

        if "name" in mapping.values():
            return index, mapping, extra

    raise HTTPException(
        status_code=400,
        detail=(
            "No headings were found in that file. It needs a row with at "
            "least Product Name - download the "
            "template to see the columns."
        ),
    )


def read_sheet(content: bytes, filename: str) -> list[dict]:
    """The file as a list of {key: text} rows, with the sheet row number."""

    name = (filename or "").lower()

    if name.endswith(".csv"):
        rows = _rows_from_csv(content)
    elif name.endswith(".xlsx") or name.endswith(".xlsm"):
        rows = _rows_from_xlsx(content)
    elif name.endswith(".xls"):
        raise HTTPException(
            status_code=400,
            detail=(
                "The old .xls format cannot be read. Open it in Excel and "
                "save as .xlsx, or as CSV."
            ),
        )
    else:
        raise HTTPException(
            status_code=400,
            detail="Upload an Excel (.xlsx) or CSV (.csv) file.",
        )

    header_index, mapping, extra = _find_header(rows)

    read: list[dict] = []

    for offset, row in enumerate(rows[header_index + 1 :]):
        values = {
            key: (row[position] if position < len(row) else "")
            for position, key in mapping.items()
        }

        # A sheet has blank rows in it - at the end, between sections, where
        # somebody deleted a line. They are not products.
        if not any(str(v).strip() for v in values.values()):
            continue

        values["_row"] = header_index + offset + 2  # 1-based, as Excel shows it
        values["_extra"] = {
            heading: (row[position] if position < len(row) else "")
            for position, heading in extra.items()
        }

        read.append(values)

    return read


def _singular(text: str) -> str:
    """"4 years" and "4 year" are the same length of cover."""

    return " ".join(
        word[:-1] if len(word) > 2 and word.endswith("s") else word
        for word in text.split()
    )


def _warranty_from(extra: dict, terms: list[str], row: int) -> tuple[dict, list[str]]:
    """What this row says cover costs, per term.

    The terms are a master - "3 Years", "4 Years", "5 Years" - so the
    columns cannot be written down here: they are matched by name against
    whatever Masters holds, "4 Year Warranty Rate" beside "4 Year Rate
    Mode". A term with no figure is simply not offered on this product,
    which is a different thing from being offered at nothing.
    """

    by_term = {_singular(_normalise(t)): t for t in terms}

    rates: dict[str, str] = {}
    modes: dict[str, str] = {}
    problems: list[str] = []

    for heading, value in extra.items():
        flat = _normalise(heading)

        for suffix, bucket in (("warranty rate", rates), ("rate mode", modes)):
            if not flat.endswith(suffix):
                continue

            prefix = _singular(flat[: -len(suffix)].strip())

            if prefix in by_term:
                bucket[by_term[prefix]] = str(value or "").strip()

            break

    cover: dict[str, dict] = {}

    for term, raw in rates.items():
        if not raw:
            continue

        figure, problem = _number(raw, f"{term} warranty rate", row, 0)

        if problem:
            problems.append(problem)
            continue

        if not figure:
            continue

        # "%" survives nothing that strips punctuation, so the mode is read
        # off the cell as it was typed. Percent unless it plainly says
        # money - which is the way most of them are quoted.
        said = str(modes.get(term, "")).strip().lower()

        money = any(
            token in said for token in ("₹", "rs", "inr", "amount", "flat", "fixed")
        )

        cover[term] = {
            "mode": "AMOUNT" if money else "PERCENT",
            "rate": figure,
        }

    return cover, problems


def _uncalculated(row: int, label: str) -> str:
    return (
        f"Row {row}: {label} is a formula the sheet does not carry an answer "
        "for. Open it in Excel, let it calculate, and save it again - or "
        "paste the figure in place of the formula."
    )


def _number(raw: str, label: str, row: int, minimum: float) -> tuple[float | None, str | None]:
    if str(raw) == UNCALCULATED:
        return None, _uncalculated(row, label)

    text = str(raw or "").strip().replace(",", "")

    # ₹ and the like, so a sheet typed as currency is not a sheet of errors.
    text = re.sub(r"[^0-9.\-]", "", text)

    if text in ("", "-", "."):
        return None, f"Row {row}: {label} is missing."

    try:
        value = float(text)
    except ValueError:
        return None, f"Row {row}: {label} is not a number ('{raw}')."

    if value < minimum:
        return None, f"Row {row}: {label} cannot be less than {minimum:g}."

    return value, None


def plan(
    content: bytes,
    filename: str,
    current_user: dict,
    db: Session,
) -> dict:
    """What loading this file would create, and everything wrong with it.

    Nothing is written. The same work is done whether the caller is asking
    for a preview or about to commit, so what the screen showed and what
    the button does cannot drift apart.
    """

    rows = read_sheet(content, filename)

    if not rows:
        raise HTTPException(
            status_code=400,
            detail="That file has headings but no products under them.",
        )

    # The masters a row is checked against, read once.
    types = {
        str(t.code).upper(): t
        for t in db.query(ProductType).all()
    }

    units = {
        str(u.get("name", "")).strip().lower(): str(u.get("name", "")).strip()
        for u in sync_mongo_db["inventory_units"].find()
    }

    names = CompanyScopeService.company_names(db)
    allowed = CompanyScopeService.visible_company_ids(current_user, db)

    by_name = {
        name.strip().lower(): company_id
        for company_id, name in names.items()
        if allowed is None or company_id in allowed
    }

    # What cover costs is a commercial figure that happens to live on the
    # product record, so only a super admin may set it - the same rule the
    # form follows. For anyone else the columns are simply not read.
    terms: list[str] = []

    if (current_user or {}).get("is_super_admin"):
        from app.models.warranty_term import WarrantyTerm

        terms = [str(t.name) for t in db.query(WarrantyTerm).all() if t.name]

    errors: list[str] = []
    drafts: list[Draft] = []

    #: (company, serial) -> the row that claimed it first.
    claimed: dict[tuple[str | None, str], int] = {}

    # Every serial already in the catalogue, read once. Asking the
    # database per serial number is fine for the three on a delivery note
    # and is a thousand round trips for a supplier's price list - which is
    # exactly the sheet somebody uploads and then watches a spinner for.
    taken: set[tuple[str | None, str]] = {
        (
            str(row.get("company_id")) if row.get("company_id") else None,
            str(row.get("serial_number") or "").upper().strip(),
        )
        for row in InventoryService.items_col.find(
            {}, {"serial_number": 1, "company_id": 1}
        )
        if str(row.get("serial_number") or "").strip()
    }

    for values in rows:
        row = values["_row"]
        before = len(errors)

        # A formula with no saved answer, anywhere on the row. Caught here
        # rather than column by column, so the complaint names the column
        # instead of calling a cell with a number plainly in it empty.
        stale = [
            _HEADER_BY_KEY.get(key, key)
            for key, value in values.items()
            if not key.startswith("_") and str(value) == UNCALCULATED
        ]

        if stale:
            for label in stale:
                errors.append(_uncalculated(row, label))

            continue

        name = str(values.get("name", "")).strip()
        serial_raw = str(values.get("serial_number", "")).strip()

        if not name:
            errors.append(f"Row {row}: Product Name is missing.")

        # Both go on the sticker and on the proposal line, so a product
        # loaded without them is one nobody downstream can identify.
        for key, label in (
            ("model_number", "Model Number"),
            ("specification", "Specification"),
        ):
            if not str(values.get(key, "")).strip():
                errors.append(f"Row {row}: {label} is missing.")

        code = str(values.get("product_type_code", "")).strip().upper()

        if not code:
            errors.append(f"Row {row}: Product Type Code is missing.")
        elif code not in types:
            errors.append(
                f"Row {row}: there is no product type '{code}'. "
                f"The codes in Masters are: {', '.join(sorted(types)) or 'none yet'}."
            )

        unit_raw = str(values.get("unit", "")).strip()

        if not unit_raw:
            errors.append(f"Row {row}: Unit is missing.")
        elif units and unit_raw.lower() not in units:
            errors.append(
                f"Row {row}: there is no unit '{unit_raw}'. "
                f"The units in Masters are: {', '.join(sorted(units.values()))}."
            )

        rate, problem = _number(values.get("rate"), "ECP Rate", row, 0)
        if problem:
            errors.append(problem)

        instock, problem = _number(values.get("instock"), "In-Stock Qty", row, 0)
        if problem:
            errors.append(problem)

        case_size, problem = _number(values.get("case_size"), "Case Size", row, 1)
        if problem:
            errors.append(problem)

        # Optional, so blank is nothing rather than an error.
        dtp_rate = 0.0
        dtp_raw = str(values.get("dtp_rate", "")).strip()

        if dtp_raw:
            parsed, problem = _number(dtp_raw, "DTP Rate", row, 0)

            if problem:
                errors.append(problem)
            else:
                dtp_rate = parsed

        company_raw = str(values.get("company", "")).strip()
        company_id: str | None = None

        if company_raw:
            company_id = by_name.get(company_raw.lower())

            if company_id is None:
                known = ", ".join(sorted(names[c] for c in by_name.values())) or "none"
                errors.append(
                    f"Row {row}: '{company_raw}' is not a company you can file "
                    f"products under. Yours are: {known}."
                )
        else:
            try:
                company_id = CompanyScopeService.resolve_owner(current_user, None, db)
            except HTTPException as refused:
                errors.append(f"Row {row}: {refused.detail}")

        cover, cover_problems = _warranty_from(
            values.get("_extra") or {}, terms, row
        )

        errors.extend(cover_problems)

        if len(errors) > before:
            # Something above is wrong with this row. Carry on reading the
            # rest of the sheet - the point is to report all of it at once -
            # but do not plan a product out of it.
            continue

        # One product per serial number. If none are provided, a single
        # non-serialized product record is created with the full in-stock count.
        serials = [
            s.strip().upper()
            for s in re.split(r"[/,\n]+", serial_raw)
            if s.strip()
        ]

        if not serials:
            drafts.append(
                Draft(
                    row=row,
                    name=name,
                    serial_number="",
                    product_type_code=code,
                    category=types[code].category,
                    company_id=company_id,
                    company_name=names.get(str(company_id)) if company_id else None,
                    attributes={
                        "model_number": str(values.get("model_number", "")).strip(),
                        "specification": str(values.get("specification", "")).strip(),
                        "hsn_code": str(values.get("hsn_code", "")).strip(),
                        "unit": units.get(unit_raw.lower(), unit_raw),
                        "rate": rate,
                        "rate_per_unit": rate,
                        "dtp_rate": dtp_rate,
                        "instock": instock,
                        "stock": instock,
                        "case_size": case_size,
                        **({"warranty_rates": cover} if cover else {}),
                    },
                )
            )
            continue

        # A serial number names one physical thing. So on a row carrying
        # several, the stock is one each, and the quantity column is a
        # statement about the row - either the total, or the one each.
        # Anything else is two different claims about the same delivery,
        # and guessing which was meant is how a stock figure ends up
        # seventy-five times too high.
        each = instock

        if len(serials) > 1:
            if instock in (len(serials), 1):
                each = 1.0
            else:
                errors.append(
                    f"Row {row}: there are {len(serials)} serial numbers but "
                    f"In-Stock Qty says {instock:g}. Each serial number is one "
                    f"unit, so put {len(serials)} there (the total), or 1 (one "
                    "of each) - or give each serial number its own row."
                )
                continue

        for serial in serials:
            if (company_id, serial) in claimed:
                errors.append(
                    f"Row {row}: serial number '{serial}' is already used on "
                    f"row {claimed[(company_id, serial)]} of this sheet."
                )
                continue

            if (str(company_id) if company_id else None, serial) in taken:
                where = names.get(str(company_id), "all companies") if company_id else "all companies"
                errors.append(
                    f"Row {row}: a product with serial number '{serial}' is "
                    f"already in the catalogue under {where}."
                )
                continue

            claimed[(company_id, serial)] = row

            drafts.append(
                Draft(
                    row=row,
                    name=name,
                    serial_number=serial,
                    product_type_code=code,
                    category=types[code].category,
                    company_id=company_id,
                    company_name=names.get(str(company_id)) if company_id else None,
                    attributes={
                        "model_number": str(values.get("model_number", "")).strip(),
                        "specification": str(values.get("specification", "")).strip(),
                        "hsn_code": str(values.get("hsn_code", "")).strip(),
                        "unit": units.get(unit_raw.lower(), unit_raw),
                        "rate": rate,
                        "rate_per_unit": rate,
                        "dtp_rate": dtp_rate,
                        "instock": each,
                        "stock": each,
                        "case_size": case_size,
                        **({"warranty_rates": cover} if cover else {}),
                    },
                )
            )

    return {"rows": len(rows), "drafts": drafts, "errors": errors}


def load(
    content: bytes,
    filename: str,
    current_user: dict,
    db: Session,
    commit: bool,
) -> dict:
    """Read the file, and when ``commit`` save every product in it.

    All or nothing: a sheet with one bad row writes nothing at all.
    """

    planned = plan(content, filename, current_user, db)

    drafts: list[Draft] = planned["drafts"]
    errors: list[str] = planned["errors"]

    created = 0

    if commit and not errors and drafts:
        InventoryService.items_col.insert_many(
            [
                {
                    "name": draft.name,
                    "serial_number": draft.serial_number,
                    "product_type_code": draft.product_type_code,
                    "category": draft.category,
                    "attributes": draft.attributes,
                    "image_base64": None,
                    "company_id": draft.company_id,
                }
                for draft in drafts
            ]
        )

        created = len(drafts)

    return {
        "rows": planned["rows"],
        "products": len(drafts),
        "created": created,
        "committed": bool(created),
        "errors": errors,
        "items": [draft.as_preview() for draft in drafts],
    }


def template_workbook(db: Session, current_user: dict) -> bytes:
    """The blank sheet to hand the inventory team, as .xlsx.

    Built here rather than kept as a file, so the columns on it are the
    columns this server accepts, and the product types, units and
    companies listed on it are the ones that exist today.
    """

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    book = Workbook()

    heading_fill = PatternFill("solid", fgColor="233353")
    required_fill = PatternFill("solid", fgColor="FFF4E5")
    white_bold = Font(bold=True, color="FFFFFF", size=11)

    products = book.active
    products.title = "Products"

    headers = [
        column["header"] + (" *" if column["required"] else "")
        for column in COLUMNS
    ]

    # A pair of columns per warranty term, because the terms are a master
    # and what each costs belongs on the product. Only on a super admin's
    # template: nobody else's upload reads them.
    cover_headers: list[str] = []

    if (current_user or {}).get("is_super_admin"):
        from app.models.warranty_term import WarrantyTerm

        for term in db.query(WarrantyTerm).all():
            if term.name:
                cover_headers += [
                    f"{term.name} Warranty Rate",
                    f"{term.name} Rate Mode",
                ]

    headers += cover_headers

    products.append(headers)

    for index, column in enumerate(COLUMNS, start=1):
        cell = products.cell(row=1, column=index)
        cell.font = white_bold
        cell.fill = heading_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)

        letter = get_column_letter(index)
        products.column_dimensions[letter].width = max(16, len(column["header"]) + 6)

        if column["required"]:
            # Shaded all the way down, so a required column is obvious in
            # the row somebody is actually typing in.
            for row in range(2, 400):
                products.cell(row=row, column=index).fill = required_fill

    products.freeze_panes = "A2"
    products.row_dimensions[1].height = 30

    # The examples live on their own tab. On the Products tab they would
    # be loaded as products by anyone who forgot to delete them.
    example = book.create_sheet("Example rows")
    example.append(headers)

    example.append([
        "SPX-75 (Lango V100)",
        '75" Interactive Flat Panel SPX',
        '75" 4K UHD, 20-point touch, Android 13, 8GB / 128GB',
        "SG-IFP-75-A1/SG-IFP-75-A2/SG-IFP-75-A3",
        next(iter(sorted(t.code for t in db.query(ProductType).all())), "IFP"),
        "",
        71000,
        58000,
        3,
        1,
        "Nos",
        "85285900",
    ])

    example.append([
        "WM-TILT-86",
        'Wall Mount - 86"',
        "Tilting, VESA 800x400, up to 90kg",
        "SG-WM-86-0007",
        next(iter(sorted(t.code for t in db.query(ProductType).all())), "IFP"),
        "",
        4500,
        "",
        24,
        4,
        "Nos",
        "83024190",
    ])

    for index, column in enumerate(COLUMNS, start=1):
        cell = example.cell(row=1, column=index)
        cell.font = white_bold
        cell.fill = heading_fill
        example.column_dimensions[get_column_letter(index)].width = max(
            16, len(column["header"]) + 6
        )

    example["A5"] = (
        "The first row is three panels delivered together: three serial "
        "numbers separated by / on one row, loaded as three products with 1 "
        "in stock each. Each serial number is one unit, so In-Stock Qty is "
        "the 3 of them (writing 1, meaning one of each, is read the same "
        "way). The second row is a product that is not serialised one by "
        "one: a single code with 24 of them on the shelf."
    )
    example["A5"].font = Font(italic=True, color="555555")

    guide = book.create_sheet("How to fill this in")
    guide.append(["Column", "Required", "What to put in it"])

    for index in range(1, 4):
        cell = guide.cell(row=1, column=index)
        cell.font = white_bold
        cell.fill = heading_fill

    for column in COLUMNS:
        guide.append([
            column["header"],
            "Required" if column["required"] else "Optional",
            column["help"],
        ])

    guide.column_dimensions["A"].width = 26
    guide.column_dimensions["B"].width = 12
    guide.column_dimensions["C"].width = 90

    guide.append([])
    guide.append([
        "Serial numbers",
        "",
        "Optional. Several on one row, separated by / or comma, are loaded as "
        "that many products - each with its own serial number, and 1 in stock, "
        "because a serial number names one physical thing. Leave blank for "
        "non-serialized stock.",
    ])
    guide.append([
        "Warranty rates",
        "",
        "A pair of columns per term from Masters: the rate, and whether it "
        "is a percentage or an amount. Leave a rate blank where that term "
        "is not offered on the product.",
    ])
    guide.append([
        "If a row is wrong",
        "",
        "Nothing on the sheet is loaded until every row is right. The "
        "upload screen says which row and what is wrong with it.",
    ])

    for row in guide.iter_rows(min_row=2):
        row[2].alignment = Alignment(wrap_text=True, vertical="top")

    # What the CRM will accept today, so a sheet is not filled in with
    # product types and units that do not exist.
    reference = book.create_sheet("Reference")
    reference.append(["Product Type Code", "Product Type", "", "Unit", "", "Company"])

    for index in (1, 2, 4, 6):
        cell = reference.cell(row=1, column=index)
        cell.font = white_bold
        cell.fill = heading_fill

    product_types = sorted(db.query(ProductType).all(), key=lambda t: str(t.code))

    units = sorted(
        str(u.get("name", ""))
        for u in sync_mongo_db["inventory_units"].find()
    )

    names = CompanyScopeService.company_names(db)
    allowed = CompanyScopeService.visible_company_ids(current_user, db)

    companies = sorted(
        name for company_id, name in names.items()
        if allowed is None or company_id in allowed
    )

    for offset in range(max(len(product_types), len(units), len(companies))):
        row = offset + 2

        if offset < len(product_types):
            reference.cell(row=row, column=1, value=str(product_types[offset].code))
            reference.cell(row=row, column=2, value=str(product_types[offset].name))

        if offset < len(units):
            reference.cell(row=row, column=4, value=units[offset])

        if offset < len(companies):
            reference.cell(row=row, column=6, value=companies[offset])

    for letter, width in (("A", 22), ("B", 28), ("D", 16), ("F", 34)):
        reference.column_dimensions[letter].width = width

    buffer = io.BytesIO()
    book.save(buffer)

    return buffer.getvalue()
