"""The proposal PDF in the business proposal's format, and what Masters sets on it.

  * a three-line proposal is two pages: the cover and the offer;
  * forty lines spill onto more pages instead of running off the bottom;
  * a long About text shrinks to keep the cover one page;
  * the cover logo can be uploaded, served and reset - by a super admin only;
  * the cover photo is served even before one is uploaded (the bundled one);
  * the preview's brand carries the brand name, tagline and services line.

Run seed_sales_team.py first.

    docker exec -w /app backend_app python test_proposal_pdf.py
"""

import re
import sys
from io import BytesIO
from types import SimpleNamespace

from seed_sales_team import ADMIN_EMAIL, ADMIN_PASSWORD, TEAM_PASSWORD, api, login

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


def pages(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf))


def quotation(lines: int):
    """Just enough of a quotation to print."""

    from datetime import datetime

    items = [
        {"product": "Interactive Flat Panel", "model": f'65" Interactive Flat Panel SPX {n}',
         "sku": f"SG-IFP-65-{n}", "hsn": "85285900", "quantity": 1, "unit_price": 61000, "tax": 18}
        for n in range(lines)
    ]
    subtotal = 61000.0 * lines

    return SimpleNamespace(
        id=0, quote_number="QT-TEST", organization_name="Test School", contact_name="Test Contact",
        billing_address={"street": "Sector 19", "city": "Dwarka", "state": "Delhi", "zipCode": "110075", "country": "India"},
        items=items, gst_percent=18, subtotal=subtotal, freight_charges=0, installation_lumpsum=0,
        taxable_amount=subtotal, gst_amount=subtotal * 0.18, total_payable=subtotal * 1.18,
        advance_percent=50, advance_amount=subtotal * 1.18 / 2, on_delivery_amount=subtotal * 1.18 / 2,
        quotation_date=datetime(2026, 10, 19), validation_date=datetime(2026, 11, 18),
        terms=[{"checked": True, "label": "Liquidated damages capped at 0.5% per week."}],
        remarks=None, company_id=None, assigned_to_id=None, creator_id=None, opportunity_name=None,
    )


try:
    from app.database.postgres import SessionLocal
    from app.services import company_profile_service
    from app.services.quotation_pdf_service import QuotationPDFService

    db = SessionLocal()

    print("\nThe PDF")
    pdf = QuotationPDFService.render(quotation(3), db)
    check("it is a PDF", pdf.startswith(b"%PDF"))
    check("three lines make two pages: cover and offer", pages(pdf) == 2, str(pages(pdf)))

    long_pdf = QuotationPDFService.render(quotation(40), db)
    check("forty lines spill onto more pages", pages(long_pdf) > 2, str(pages(long_pdf)))

    real = company_profile_service.CompanyProfileService.as_lists

    def wordy(session):
        profile = real(session)
        profile["company_about_paragraphs"] = ["A very long About paragraph. " * 40] * 3
        return profile

    company_profile_service.CompanyProfileService.as_lists = staticmethod(wordy)
    try:
        wordy_pdf = QuotationPDFService.render(quotation(3), db)
    finally:
        company_profile_service.CompanyProfileService.as_lists = staticmethod(real)
    check("a long About text still leaves the cover one page", pages(wordy_pdf) == 2, str(pages(wordy_pdf)))

    company = QuotationPDFService.company(db)
    check("the brand name is short", company["brand_name"] and len(company["brand_name"].split()) == 1, company["brand_name"])
    check("a tagline and services line are printed", bool(company["tagline"] and company["services_line"]))

    print("\nMasters")
    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    zonal = login("zh.north@mailinator.com", TEAM_PASSWORD)

    import requests

    from seed_sales_team import BASE

    r = requests.get(f"{BASE}/company-profile/cover/image", timeout=30)
    check("the cover photo is served before any upload", r.status_code == 200 and r.content[:3] == b"\xff\xd8\xff", f"{r.status_code}")
    r = requests.get(f"{BASE}/company-profile/cover-logo/image", timeout=30)
    check("so is the cover logo", r.status_code == 200 and r.content[:4] == b"\x89PNG", f"{r.status_code}")
    default_logo = r.content

    from PIL import Image

    upload = BytesIO()
    Image.new("RGBA", (40, 10), (255, 255, 255, 255)).save(upload, "PNG")

    r = api("post", "/company-profile/cover-logo", zonal, files={"file": ("logo.png", upload.getvalue(), "image/png")})
    check("a zonal head cannot change it", r.status_code == 403, f"{r.status_code}")

    r = api("post", "/company-profile/cover-logo", admin, files={"file": ("logo.png", upload.getvalue(), "image/png")})
    check("a super admin can", r.status_code == 200 and r.json()["data"]["company_cover_logo"], f"{r.status_code} {r.text[:120]}")
    served = requests.get(f"{BASE}/company-profile/cover-logo/image", timeout=30).content
    check("and the uploaded one is served", served != default_logo and served[:4] == b"\x89PNG")

    r = api("post", "/company-profile/cover-logo", admin, files={"file": ("logo.gif", b"GIF89a", "image/gif")})
    check("a GIF is refused", r.status_code == 400, f"{r.status_code}")

    r = api("delete", "/company-profile/cover-logo", admin)
    check("resetting goes back to the default", r.status_code == 200 and r.json()["data"]["company_cover_logo"] == "")
    check("which is served again", requests.get(f"{BASE}/company-profile/cover-logo/image", timeout=30).content == default_logo)

    brand = api("get", "/quotations/brand", admin).json()["data"]
    check(
        "the preview's brand carries the new copy",
        all(brand.get(key) for key in ("brand_name", "tagline", "services_line")),
        str({k: brand.get(k) for k in ("brand_name", "tagline", "services_line")}),
    )

except Exception as exc:  # noqa: BLE001 - the report below still has to print
    failed.append(f"the run stopped - {exc}")
    print(f"\n  STOPPED  {exc}")

finally:
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    for name in failed:
        print(f"  FAILED  {name}")
    sys.exit(1 if failed else 0)
