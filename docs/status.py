"""Build the status document the team reads.

One document covering the whole system: what it does, who does what, the
journey a deal takes, the rules that keep it honest, every status, the
data model, how the tax is worked out, the catalogue, the proforma
invoice, what is tested and where the work stands.

The first version was written once by hand and then had to be rewritten by
hand every time a figure moved, which is why it was four days out of date
within a week. It is a script now: the suite names and their check counts
are listed in one place, the headline total is summed from them rather
than typed, and rebuilding takes one command.

    python3 docs/status.py              # writes docs/Synergy-CRM-Status.pdf
    python3 docs/status.py --html       # stops at the HTML, for a quick look
    python3 docs/status.py --out DIR    # writes the PDF somewhere else too

Rendered through Chrome's headless print, because that is what is on the
machine and it honours the same CSS the screen does.
"""

import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent

PRETTY_DATE = "3 October 2026"
PREVIOUS_DATE = "29 September 2026"

FRONTEND_SHA = "564f105"
BACKEND_SHA = "514d4cf"

#: suite -> (title, file, what it proves, checks). The total is summed,
#: never typed, so the headline figure cannot drift from the rows under it.
SUITES = [
    (
        "One deal, end to end",
        "test_role_journeys.py",
        "The whole journey in order, with every role doing its own part and "
        "being refused everybody else's. Twelve sections, from signing in to "
        "deleting. This is the suite that matches the manual script.",
        140,
    ),
    (
        "Whole flow and visibility",
        "test_full_sales_flow.py",
        "The same chain of documents, but after every single step it checks "
        "who can see it: the Zonal Head yes, the other Zonal Head no, the "
        "Area Manager next door no, the AVP and CEO yes.",
        103,
    ),
    (
        "The discount chain",
        "test_approval_chain.py",
        "Quotations at 10%, 18% and 25% each go to exactly the right people "
        "in the right order; dealer price goes straight to the CEO; an "
        "undiscounted quote needs nobody.",
        60,
    ),
    (
        "The two desks",
        "test_fulfilment_desks.py",
        "A desk sees only its own queue and is refused the other; a "
        "salesperson is refused every stage the desks own; an approval moves "
        "the order exactly one step; a rejection with no reason is refused.",
        48,
    ),
    (
        "Lead to customer, and the tax on it",
        "test_lead_to_customer.py",
        "One deal walked from lead to invoice on the catalogue we actually "
        "sell, then the invoice read as a customer reads it: the HSN against "
        "each line, the tax grouped under both codes, CGST and SGST inside "
        "the state and IGST out of it, the terms it is payable on, and who "
        "may see it.",
        48,
    ),
    (
        "After the proforma invoice",
        "test_fulfilment_after_pi.py",
        "Money recorded on the invoice reaches the order — but does not "
        "move the order by itself, because verifying it is the accounts "
        "desk's call.",
        33,
    ),
    (
        "Hierarchy and company scope",
        "test_hierarchy_and_company_scope.py",
        "Builds a throwaway team five levels deep and checks a record "
        "follows the reporting line. Files one product under two companies "
        "and checks each side sees only its own.",
        30,
    ),
    (
        "Catalogue and the shelf",
        "test_stock_movements.py",
        "Only the warehouse may correct a count or delete a product; stock "
        "leaves on dispatch and not before; a cancelled dispatch puts it "
        "back.",
        26,
    ),
]

TOTAL = sum(checks for *_, checks in SUITES)

ROLES = [
    (
        "Super Admin", "everything",
        "Dashboard, Sales, Users, Inventory, Customers, Accounts, "
        "Procurement, Reports, Masters, Workflows",
        "Sets the company up: users, roles, permissions, the product "
        "catalogue, the discount bands, the bank details. Can stand in for "
        "anybody at any stage.",
    ),
    (
        "CEO", "sales floor",
        "Dashboard, Sales, Users, Inventory, Customers, Reports",
        "Sees every region's deals. Approves discounts between 15% and 20%, "
        "and sets the dealer price. Cannot push an order through the desks.",
    ),
    (
        "AVP", "sales floor",
        "Dashboard, Sales, Users, Inventory, Customers, Reports",
        "First signature on any discount, up to 15%. Sees every Zonal Head "
        "below them.",
    ),
    (
        "Zonal Head", "sales floor",
        "Dashboard, Sales, Users, Inventory, Customers, Reports",
        "Sees their own Area Managers' work and nobody else's zone. Can "
        "apply a discount but has no power to approve one.",
    ),
    (
        "Area Manager", "sales floor",
        "Dashboard, Sales, Users, Inventory, Customers, Reports",
        "Does the selling: customers, leads, opportunities, proposals, sales "
        "orders, proforma invoices. Signs off the installation. Cannot "
        "approve their own discount or close their own order.",
    ),
    (
        "Accounts", "desk",
        "Accounts Desk — and nothing else",
        "Two jobs at two points: confirm the advance arrived (order becomes "
        "Payment Verified), and confirm the balance and close the order "
        "(Completed).",
    ),
    (
        "Inventory", "desk",
        "Inventory, Procurement Desk",
        "Keeps the catalogue and the shelf. Takes an order through "
        "Procurement, Ready, Dispatched and Delivered. Only this role may "
        "correct a stock count.",
    ),
]

JOURNEY = [
    ("Add the customer", "Area Manager",
     "Company name, the person to talk to, their state and the customer "
     "type. The enquiry is attached at the same time as a lead.",
     "a customer record, and a lead at <b>New</b>."),
    ("Chase the lead", "Area Manager",
     "Log the call and move it on: <b>Contacted</b> once you have spoken to "
     "them, <b>Qualified</b> once you believe it is a real budget. "
     "<b>Lost</b>, with a reason.",
     "lead at <b>Qualified</b>. Every change is written into its own "
     "history, with who and when."),
    ("Turn it into an opportunity", "Area Manager",
     "Only qualified leads are offered, so nobody can open a deal on an "
     "enquiry that was never checked. Every field already filled on the "
     "lead is carried across.",
     "lead becomes <b>Converted</b> and is finished with. Opportunity opens "
     "at <b>Qualification</b>."),
    ("Raise the proposal", "Area Manager",
     "Products come from the one shared catalogue, so the rate, the unit, "
     "the HSN, freight, installation, GST, ORC and the payment terms are "
     "all on the same form.",
     "proposal at <b>Draft</b>, with a number like <span class='mono'>"
     "QT-3250</span>."),
    ("Send the discount up, if there is one", "Area Manager",
     "The system works out the chain from the discount itself and emails "
     "everyone in the reporting line, so it cannot stall quietly in one "
     "inbox. An undiscounted price skips this entirely.",
     "proposal at <b>Pending Approval</b>, and a request waiting on the "
     "first role in the chain."),
    ("The signatures, in order", "AVP, then CEO, then Founder",
     "Each approval moves the request exactly one step. Nobody can jump the "
     "queue — the CEO cannot sign before the AVP has. A rejection "
     "anywhere ends it and hands the document back.",
     "the request is <b>Approved</b> and the proposal is released to be "
     "sent."),
    ("Send it to the client", "Area Manager",
     "The proposal goes out as a PDF on the company's letterhead. When the "
     "client agrees, mark it <b>Accepted</b>.",
     "proposal <b>Sent</b>, then <b>Accepted</b>."),
    ("Convert it to a sales order", "Area Manager",
     "One button on the proposal. Everything is carried over — "
     "including the split it was accepted on — and the proposal number "
     "stays on the order, so the two are always tied together.",
     "order at <b>Draft</b>, like <span class='mono'>SO-00244</span>. "
     "Approve it and it becomes <b>Confirmed</b>, and from that moment the "
     "salesperson can no longer push it."),
    ("Raise the proforma invoice and record the advance", "Area Manager",
     "The invoice asks the client for the advance. When the money is "
     "recorded on it, the figure reaches the order as well, so the "
     "outstanding balance is right in both places.",
     "invoice <b>Generated</b>. The order does <i>not</i> move — "
     "recording a payment is not the same as verifying it."),
    ("Accounts verify the advance", "Accounts Desk",
     "The order is already sitting in the accounts queue under “"
     "Awaiting Payment”, showing exactly what is being asked. A "
     "rejection needs a reason.",
     "order at <b>Payment Verified</b>, and it appears on the warehouse "
     "queue. A rejection puts it <b>On Hold</b> with the reason."),
    ("The warehouse take it through", "Procurement Desk",
     "Four confirmations, one at a time: stock is there or on order "
     "(<b>Procurement</b>), it is in hand (<b>Ready</b>), it has gone "
     "(<b>Dispatched</b>), it has arrived (<b>Delivered</b>).",
     "order at <b>Delivered</b>, and the shelf count has dropped by exactly "
     "what went out."),
    ("Sign off the installation", "Area Manager",
     "The salesperson is the one on site with the client, so this step is "
     "theirs and not the warehouse's.",
     "order at <b>Installed</b>, back on the accounts queue under “"
     "Awaiting Balance”."),
    ("Settle the balance and close it", "Accounts Desk",
     "The remaining balance is recorded on the invoice, and accounts close "
     "the order. The salesperson cannot close their own order, however keen "
     "they are.",
     "order <b>Completed</b>. Nothing moves after this."),
]

DISCOUNT_BANDS = [
    ("None", "Nobody", "The list price goes out on the salesperson's own authority."),
    ("Up to 15%", "AVP", "Inside the AVP's band."),
    ("15.01% to 20%", "AVP, then CEO", "Past the AVP's ceiling, so the CEO owns the excess."),
    ("Above 20%", "AVP, then CEO, then Founder", "Past the CEO's ceiling too."),
    ("Dealer price", "CEO only, whatever the figure",
     "A transfer price, not a negotiation. Nobody below the CEO may move it."),
]

DESKS = [
    ("Confirmed", "Accounts", "Confirm the advance has been received against the proforma invoice.", "Payment Verified"),
    ("Payment Verified", "Inventory", "Confirm the stock is available and take the order into procurement.", "Procurement"),
    ("Procurement", "Inventory", "Confirm the stock is in hand and the order is ready to deliver.", "Ready"),
    ("Ready", "Inventory", "Send the order out.", "Dispatched"),
    ("Dispatched", "Inventory", "Confirm the order has reached the client.", "Delivered"),
    ("Delivered", "Area Manager", "Sign off that it is installed and working on site.", "Installed"),
    ("Installed", "Accounts", "Confirm the balance has been settled and close the order.", "Completed"),
]

STATUSES = {
    "Lead": [
        ("NEW", "Just written down. Nobody has spoken to them yet.", "Contacted, Lost"),
        ("CONTACTED", "We have spoken to them. Still deciding whether it is real.", "Qualified, Lost"),
        ("QUALIFIED", "Real budget, real requirement. Ready to become a deal.", "Converted, Lost"),
        ("CONVERTED", "It became an opportunity. Reached only through the conversion button, never typed.", "— nothing"),
        ("LOST", "Dead, with a reason on the record.", "— nothing"),
    ],
    "Opportunity": [
        ("QUALIFICATION", "The deal is open. Working out whether it fits.", "any later stage, Won or Lost"),
        ("REQUIREMENT", "We know exactly what they need.", "any later stage, Won or Lost"),
        ("DEMO", "They have seen it working.", "any later stage, Won or Lost"),
        ("PROPOSAL", "A proposal is with them.", "any later stage, Won or Lost"),
        ("NEGOTIATION", "Haggling over price or terms.", "Won or Lost"),
        ("WON", "They bought. Records who won it, when, and why.", "— nothing"),
        ("LOST", "They did not buy, with a reason.", "— nothing"),
    ],
    "Proposal": [
        ("DRAFT", "Being written. The only state where it can still be edited.", "Pending Approval, Sent, Expired"),
        ("PENDING_APPROVAL", "It carries a discount and is waiting on the chain. It cannot go to the client yet.", "Sent, Draft, Expired"),
        ("SENT", "With the client.", "Accepted, Rejected, Expired"),
        ("ACCEPTED", "They said yes. Ready to become a sales order.", "— nothing"),
        ("REJECTED", "They said no.", "— nothing"),
        ("EXPIRED", "Past its validity date. Replaced by a new revision, never revived in place.", "— nothing"),
    ],
    "Sales order": [
        ("DRAFT", "Raised, not yet committed to.", "Pending Approval, Confirmed, Cancelled"),
        ("PENDING_APPROVAL", "Waiting on the discount chain before anything is committed.", "Confirmed, Draft, Cancelled"),
        ("CONFIRMED", "Approved and real. Now with accounts.", "Payment Verified, On Hold, Cancelled"),
        ("PAYMENT_VERIFIED", "Accounts have seen the money against the proforma invoice.", "Procurement, On Hold, Cancelled"),
        ("PROCUREMENT", "With the warehouse: either picked from stock or on order.", "Ready, On Hold, Cancelled"),
        ("READY", "Stock is in hand and the order can go out.", "Dispatched, On Hold, Cancelled"),
        ("DISPATCHED", "It has left. This is where stock comes off the shelf.", "Delivered, On Hold, Cancelled"),
        ("DELIVERED", "It reached the client.", "Installed, On Hold, Cancelled"),
        ("INSTALLED", "Working on site, signed off by the salesperson. Back with accounts.", "Completed, On Hold, Cancelled"),
        ("COMPLETED", "Balance settled, order closed by accounts.", "— nothing"),
        ("ON_HOLD", "Something is wrong, with the reason on the record.", "any earlier stage, or Cancelled"),
        ("CANCELLED", "Dead. If it had already been dispatched, the stock comes back.", "— nothing"),
    ],
    "Proforma invoice": [
        ("DRAFT", "Being written. The only state where the lines and dates can still change.", "Generated, Cancelled"),
        ("GENERATED", "Issued. The figures are frozen.", "Sent, Cancelled"),
        ("SENT", "With the customer. It can still be withdrawn, but not un-sent.", "Cancelled"),
        ("CANCELLED", "Withdrawn.", "— nothing"),
    ],
    "Approval request": [
        ("PENDING", "Waiting on whoever is at the current step of the chain.", "Approved, Rejected, Cancelled"),
        ("APPROVED", "Every step signed. The document is released.", "— nothing"),
        ("REJECTED", "Somebody said no. The document goes back to be reworked.", "— nothing"),
        ("CORRECTION_REQUIRED", "Not a no, but not yet — send it back with notes.", "Pending"),
        ("CANCELLED", "Withdrawn before a decision.", "— nothing"),
        ("NOT_REQUIRED", "No discount, so nothing needed signing in the first place.", "— nothing"),
    ],
}

GST_ROWS = [
    ("85285900", "Interactive flat panels, standees", "18%"),
    ("84714190", "75″ panels filed as data processing machines", "18%"),
    ("85291029", "OPS compute modules", "18%"),
    ("85258900", "Cameras and video bars", "18%"),
    ("85184000", "Microphones and array mics", "18%"),
    ("84733099", "Stands and mounting hardware", "18%"),
]

DONE = [
    (
        "The tax is worked out properly, and shown",
        "A sale inside Uttar Pradesh is charged as CGST plus SGST; a sale "
        "out of it as one IGST line. Which one applies is decided by the "
        "billing address against our own registration — not chosen by "
        "hand, and not a flat 18% on everything as before. The invoice "
        "carries an HSN-wise table so a reader can check the arithmetic "
        "rather than take the total on trust.",
    ),
    (
        "Every product carries its HSN, and it travels",
        "The code is entered once on the product and carried onto the "
        "opportunity, the proposal, the order and the invoice. A line that "
        "arrives without one is filled from the catalogue by SKU, because a "
        "line that omits the code has not been classified differently — "
        "it has lost it on the way.",
    ),
    (
        "The catalogue is the real one, and fully priced",
        "Thirty-one products built from the Noida 65 stock dashboard, at the "
        "rates and HSN codes written on it. The seven the sheet named "
        "without a figure are now priced too. The six invented “"
        "Qonevo” products every screen used to price against are gone.",
    ),
    (
        "The proforma invoice is a GST document",
        "Buyer and consignee each with GSTIN and state code, the document's "
        "own references stated once, the goods with HSN and rate per line, "
        "the tax split as the two states call for, the HSN-wise summary, "
        "both figures in words, bank details, the UPI code, E. & O.E and the "
        "authorised signature. Preview and the downloaded PDF are one "
        "component, so they cannot differ.",
    ),
    (
        "Payment terms follow the deal instead of being retyped",
        "One field, with the advance read back out of the sentence rather "
        "than entered beside it. On the invoice it is read-only: the split "
        "is the one the client accepted, and a document that lets its own "
        "terms be rewritten stops being a record of what was agreed.",
    ),
    (
        "Three Masters that had no screen now have one",
        "Bank Details — beneficiary, bank, account, branch, IFSC, "
        "SWIFT, the UPI VPA and its QR — plus Lead Source and States. "
        "The bank block used to be read straight from the deployment's "
        "environment file, so an account number corrected on screen never "
        "reached the document the customer pays against.",
    ),
    (
        "Every product can be labelled",
        "A Print Label button on each inventory row produces a label with a "
        "QR code and a Code 128 barcode carrying the SKU, drawn by the "
        "server so the warehouse and the office print the same thing.",
    ),
]

FIXED = [
    (
        "Any signed-in user could raise a proposal",
        "Only <span class='mono'>lead</span> had create and update "
        "permissions. The other sales modules had read and nothing else, so "
        "every write route fell back to “any signed-in user” "
        "— the accounts clerk and the warehouse could both raise a "
        "proposal or an order straight at the API. Three write permissions "
        "now exist and are granted to the four sales roles.",
    ),
    (
        "Document numbers collided instead of being reused",
        "The counter only moves forward, which stops a deleted document's "
        "number being handed on. It had no way to catch up, though: a number "
        "issued outside it left the counter behind the data, and every "
        "allocation then collided and told the user “Resource already "
        "exists”. Raising a proposal had been failing outright.",
    ),
    (
        "A line printed a quantity of zero beside a correct total",
        "The quotation form wrote the quantity under one key and every "
        "document read it back from another. Both are written now, and the "
        "records already stored were repaired. One line disagreeing with the "
        "total under it is worse than either being wrong alone.",
    ),
    (
        "The AVP could see the whole of Masters",
        "An aborted test run had left the AVP role holding forty-four "
        "permissions instead of fourteen — Roles & Access, Company "
        "Profile, user and company creation, and both fulfilment desks. The "
        "grants are restated from one file now and the database made to "
        "match it.",
    ),
    (
        "Two suites had stopped testing what they claimed",
        "A SKU meant to be empty held ten, and a “short” order "
        "asked for four units against thirty in hand — so the "
        "shortfall warning was being asserted against orders it comfortably "
        "covered. Both set or compute what they need now.",
    ),
    (
        "Old records could not show what was built",
        "Every document in the database had been raised against the retired "
        "demo products, carried no HSN, and fell back to a flat rate. "
        "Cleared with your go-ahead, counters kept so none of those numbers "
        "can be issued again.",
    ),
]

WAITING = [
    (
        "Staging and production still need the line repair",
        "<span class='mono'>repair_lines.py</span> has run against "
        "development only. The same records exist in the other environments "
        "with the quantity under the wrong key and no HSN. It takes "
        "<span class='mono'>--dry-run</span> first.",
    ),
    (
        "One rate inferred rather than read",
        "The dashboard prices SPX9 and CPX11 at ₹1,50,000 and leaves "
        "CPX9 blank. CPX9 has been given the same figure as the 98″ "
        "beside it. It is the only number in the catalogue not read off the "
        "sheet.",
    ),
    (
        "The email steps",
        "Sending a proposal or an invoice to a client sends a real email "
        "from the real account. Nothing has been sent, and nothing will be "
        "until you say go.",
    ),
    (
        "Approve from the email",
        "An approver clicking Approve in the notification email rather than "
        "signing in. It needs a decision from you: a signed one-time link, "
        "or a link that still makes them sign in. The first is one click, "
        "the second is harder to misuse.",
    ),
    (
        "The upload steps",
        "Lead attachments, Add From Excel, the PO document on a sales order, "
        "GST / PAN / certificate of incorporation, and proposal annexures "
        "all need a real file. Hand over one and those steps get tested too.",
    ),
]


def esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


CSS = """
@page { size: A4; margin: 18mm 16mm 20mm; }
* { box-sizing: border-box; }

body {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 10.5pt; line-height: 1.48; color: #1f2937; margin: 0;
}

h1 { font-size: 30pt; line-height: 1.15; margin: 0 0 14px; color: #1e3a8a; }
h2 { font-size: 17pt; margin: 0 0 4px; color: #111827; page-break-after: avoid; }
h3 { font-size: 12pt; margin: 24px 0 8px; color: #111827; page-break-after: avoid; }
h2 .n { color: #2563eb; margin-right: 8px; }
p { margin: 0 0 10px; }

.lede { color: #6b7280; font-size: 11pt; margin: 0 0 14px; }
.rule { width: 56px; height: 5px; background: #2563eb; margin-bottom: 26px; }

.cover { padding-top: 130px; }
.cover .sub { font-size: 12.5pt; color: #4b5563; line-height: 1.6; margin-bottom: 120px; }

.meta { border-top: 1px solid #d1d5db; padding-top: 12px; font-size: 9.5pt; }
.meta div { margin-bottom: 3px; }
.meta b { color: #1e3a8a; display: inline-block; min-width: 92px; }

.toc { margin-top: 20px; font-size: 10pt; }
.toc div { padding: 6px 0; border-bottom: 1px dotted #d1d5db; }
.toc .n { color: #6b7280; display: inline-block; width: 22px; }
.toc .t { color: #374151; }
.toc .d { color: #9ca3af; }

section { page-break-before: always; }
section.cover { page-break-before: avoid; }

table { width: 100%; border-collapse: collapse; margin: 10px 0 16px; font-size: 9.5pt; }
th { background: #f3f4f6; text-align: left; font-weight: 600; color: #374151;
     padding: 7px 9px; border-bottom: 1px solid #d1d5db; }
td { padding: 7px 9px; border-bottom: 1px solid #e5e7eb; vertical-align: top; }
td.num, th.num { text-align: right; }
tr { page-break-inside: avoid; }
tfoot td { font-weight: 700; border-top: 1px solid #9ca3af; border-bottom: none; }

code, .mono { font-family: "SFMono-Regular", Menlo, Consolas, monospace;
              font-size: 9pt; color: #374151; }

.note { border-left: 3px solid #2563eb; background: #eff6ff;
        padding: 11px 14px; margin: 14px 0; page-break-inside: avoid; }
.note.warn { border-left-color: #ea580c; background: #fff7ed; }
.note.good { border-left-color: #16a34a; background: #f0fdf4; }
.note > b:first-child { display: block; margin-bottom: 3px; }

.pill { display: inline-block; padding: 1px 7px; border-radius: 9px;
        font-size: 8pt; font-weight: 600; }
.pill.all { background: #e0e7ff; color: #3730a3; }
.pill.floor { background: #dbeafe; color: #1e40af; }
.pill.desk { background: #dcfce7; color: #166534; }
.pill.ok { background: #dcfce7; color: #166534; }

.headline { font-size: 15pt; font-weight: 700; color: #111827; }

ul { margin: 0 0 12px; padding-left: 18px; }
li { margin-bottom: 6px; }

.item { margin-bottom: 11px; page-break-inside: avoid; }
.item b { color: #111827; }

.step { display: flex; gap: 10px; margin-bottom: 13px; page-break-inside: avoid; }
.step .no { flex: 0 0 22px; height: 22px; border-radius: 50%; background: #2563eb;
            color: #fff; font-size: 9pt; font-weight: 700; text-align: center;
            line-height: 22px; }
.step .body { flex: 1; }
.step .who { display: inline-block; background: #eef2ff; color: #3730a3;
             border-radius: 4px; padding: 1px 7px; font-size: 8pt;
             font-weight: 600; margin-left: 6px; vertical-align: 2px; }
.step .after { font-size: 9pt; color: #6b7280; margin-top: 2px; }
.step .after b { color: #374151; }
.step h4 { margin: 0 0 3px; font-size: 11pt; color: #111827; }

.flow { display: flex; gap: 7px; margin: 16px 0 6px; }
.flow .box { flex: 1; border: 1px solid #c7d2fe; background: #eef2ff;
             border-radius: 7px; padding: 9px 6px; text-align: center;
             font-size: 8.5pt; }
.flow .box b { display: block; font-size: 9.5pt; margin-bottom: 2px; hyphens: none; }
.flow .box.now { border-color: #ea580c; background: #fff7ed; }
.flow .box.done { border-color: #86efac; background: #f0fdf4; }
.flow .box.todo { border-color: #d1d5db; background: #f9fafb; color: #6b7280; }

.cap { font-size: 8.5pt; color: #9ca3af; margin-top: 2px; }
.foot { margin-top: 28px; padding-top: 10px; border-top: 1px solid #e5e7eb;
        font-size: 8.5pt; color: #9ca3af; }
"""


def suites_table() -> str:
    rows = "".join(
        f"<tr><td><b>{esc(name)}</b><br><span class='mono'>{esc(f)}</span></td>"
        f"<td>{esc(what)}</td><td class='num'>{n}</td></tr>"
        for name, f, what, n in SUITES
    )
    return (
        "<table><thead><tr><th style='width:27%'>Suite</th><th>What it proves</th>"
        "<th class='num' style='width:10%'>Checks</th></tr></thead>"
        f"<tbody>{rows}</tbody>"
        f"<tfoot><tr><td>Total</td><td></td><td class='num'>{TOTAL}</td></tr></tfoot>"
        "</table>"
    )


def roles_table() -> str:
    rows = "".join(
        f"<tr><td><b>{esc(role)}</b><br>"
        f"<span class='pill {'all' if tag == 'everything' else ('desk' if tag == 'desk' else 'floor')}'>"
        f"{esc(tag)}</span></td><td>{esc(menu)}</td><td>{esc(does)}</td></tr>"
        for role, tag, menu, does in ROLES
    )
    return (
        "<table><thead><tr><th style='width:18%'>Role</th>"
        "<th style='width:30%'>Sees in the sidebar</th>"
        "<th>What is theirs to do</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def journey_steps() -> str:
    return "".join(
        f"<div class='step'><div class='no'>{i}</div><div class='body'>"
        f"<h4>{esc(title)}<span class='who'>{esc(who)}</span></h4>"
        f"<p style='margin:0'>{body}</p>"
        f"<p class='after'><b>After this:</b> {after}</p>"
        "</div></div>"
        for i, (title, who, body, after) in enumerate(JOURNEY, 1)
    )


def bands_table() -> str:
    rows = "".join(
        f"<tr><td><b>{esc(band)}</b></td><td>{esc(who)}</td><td>{esc(why)}</td></tr>"
        for band, who, why in DISCOUNT_BANDS
    )
    return (
        "<table><thead><tr><th style='width:20%'>Discount given</th>"
        "<th style='width:32%'>Who has to sign, in this order</th>"
        "<th>Why</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def desks_table() -> str:
    tone = {"Accounts": "desk", "Inventory": "floor", "Area Manager": "all"}
    rows = "".join(
        f"<tr><td>{esc(at)}</td>"
        f"<td><span class='pill {tone.get(held, 'floor')}'>{esc(held)}</span></td>"
        f"<td>{esc(asked)}</td><td>{esc(to)}</td></tr>"
        for at, held, asked, to in DESKS
    )
    return (
        "<table><thead><tr><th style='width:18%'>Order sitting at</th>"
        "<th style='width:14%'>Held by</th><th>What that desk is being asked</th>"
        "<th style='width:17%'>Moves to</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def status_tables() -> str:
    out = []

    for kind, rows in STATUSES.items():
        body = "".join(
            f"<tr><td><b class='mono'>{esc(s)}</b></td><td>{esc(words)}</td>"
            f"<td>{esc(moves)}</td></tr>"
            for s, words, moves in rows
        )
        out.append(
            f"<h3>{esc(kind)}</h3>"
            "<table><thead><tr><th style='width:22%'>Status</th>"
            "<th>In plain words</th><th style='width:27%'>Can move to</th>"
            "</tr></thead>"
            f"<tbody>{body}</tbody></table>"
        )

    return "".join(out)


def gst_table() -> str:
    rows = "".join(
        f"<tr><td class='mono'>{esc(c)}</td><td>{esc(d)}</td>"
        f"<td class='num'>{esc(r)}</td></tr>"
        for c, d, r in GST_ROWS
    )
    return (
        "<table><thead><tr><th style='width:18%'>HSN</th><th>What it covers</th>"
        "<th class='num' style='width:12%'>GST</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def items(pairs) -> str:
    return "".join(f"<div class='item'><b>{esc(h)}</b> {b}</div>" for h, b in pairs)


HTML = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Synergy CRM — Flow, Data Model and Status</title>
<style>{CSS}</style></head>
<body>

<section class="cover">
  <div class="rule"></div>
  <h1>Synergy CRM<br>How the system works</h1>
  <p class="sub">
    The journey of one deal, the data behind it, every status it can hold,<br>
    how the tax is worked out, and what we have tested so far.
  </p>

  <div class="meta">
    <div><b>Prepared for</b> the Synergy team — sales, accounts, warehouse and management</div>
    <div><b>Date</b> {PRETTY_DATE}</div>
    <div><b>Replaces</b> the issue of {PREVIOUS_DATE}</div>
    <div><b>Covers</b> frontend <span class="mono">{FRONTEND_SHA}</span> ·
         backend <span class="mono">{BACKEND_SHA}</span></div>
    <div><b>Test result</b> {TOTAL} automated checks, 0 failures, re-run on {PRETTY_DATE}</div>
  </div>

  <div class="toc">
    <div><span class="n">1</span><span class="t">What this system does</span>
      <span class="d">— one page, no jargon</span></div>
    <div><span class="n">2</span><span class="t">Who does what</span>
      <span class="d">— the seven roles</span></div>
    <div><span class="n">3</span><span class="t">The journey, step by step</span>
      <span class="d">— customer to completed order</span></div>
    <div><span class="n">4</span><span class="t">The three rules that keep it honest</span>
      <span class="d">— discounts, desks, stock</span></div>
    <div><span class="n">5</span><span class="t">Every status, and what it means</span></div>
    <div><span class="n">6</span><span class="t">The data model</span>
      <span class="d">— what is stored, and where</span></div>
    <div><span class="n">7</span><span class="t">How the tax is worked out</span>
      <span class="d">— CGST, SGST, IGST and the HSN behind them</span></div>
    <div><span class="n">8</span><span class="t">The catalogue we price against</span></div>
    <div><span class="n">9</span><span class="t">The proforma invoice</span>
      <span class="d">— what it carries, and why</span></div>
    <div><span class="n">10</span><span class="t">What we have tested</span>
      <span class="d">— {TOTAL} checks across {len(SUITES)} suites</span></div>
    <div><span class="n">11</span><span class="t">Where we are today</span>
      <span class="d">— done, fixed, and waiting on you</span></div>
  </div>
</section>

<section>
  <h2><span class="n">1</span>What this system does</h2>
  <p class="lede">If you read nothing else, read this page. It is the whole
  business in nine sentences.</p>

  <p>Synergy sells interactive panels, OPS modules, standees, cameras and
  the hardware around them to schools, colleges and businesses. Every sale
  follows the same path, and this system is that path written down so that
  nothing is lost, nobody has to remember, and everybody can see where a
  deal has got to.</p>

  <p><b>Somebody shows interest.</b> A salesperson writes them down as a
  <i>customer</i>, and the enquiry itself as a <i>lead</i>. The lead is
  chased: contacted, then either qualified as real or marked lost.</p>

  <p><b>A real lead becomes an opportunity.</b> This is where the deal is
  shaped — what they want, how much it is worth, when they are likely
  to buy.</p>

  <p><b>A proposal goes out.</b> Products are picked from one shared
  catalogue, so the price is the company's price and not whatever somebody
  remembered. If the salesperson gives a discount, the system decides who
  has to sign it off before the proposal can leave the building.</p>

  <p><b>The customer says yes.</b> The proposal becomes a <i>sales order</i>,
  and a <i>proforma invoice</i> asks for the advance.</p>

  <p><b>Accounts confirm the money.</b> Not the salesperson — the
  salesperson cannot mark their own deal as paid.</p>

  <p><b>The warehouse sends it out.</b> They confirm the stock, get it
  ready, dispatch it and confirm it arrived. Stock leaves the shelf at the
  moment of dispatch, and the shelf count changes by itself.</p>

  <p><b>The salesperson signs off the installation</b> once it is working on
  site, and <b>accounts close the order</b> when the balance is in.</p>

  <p>At every one of those handovers the system tells the next person it is
  their turn, and refuses to let anyone skip a step or do somebody else's
  job.</p>

  <div class="note">
    <b>The one idea worth remembering</b>
    A deal is never “done” because one person said so. Each stage
    names the role that owns it. Only that role can move it on —
    everybody senior to them can watch, and even they cannot push.
  </div>

  <div class="flow">
    <div class="box"><b>Interest</b>customer, lead, opportunity</div>
    <div class="box"><b>Price</b>proposal, and who signs the discount</div>
    <div class="box"><b>Order</b>sales order and proforma invoice</div>
    <div class="box"><b>Money</b>accounts verify the advance</div>
    <div class="box"><b>Goods</b>picked, dispatched, delivered</div>
    <div class="box"><b>Close</b>installed, then completed</div>
  </div>
  <p class="cap">Figure 1 — The six things that happen to every deal.
  Management watch every stage and can move none of them.</p>
</section>

<section>
  <h2><span class="n">2</span>Who does what</h2>
  <p class="lede">Seven roles. A person's role decides three separate
  things: which menu items they see, whose records appear inside those
  screens, and which buttons actually work.</p>

  {roles_table()}

  <div class="note">
    <b>Why the two desks see almost nothing else</b>
    An accounts clerk does not need a sales pipeline and a warehouse hand
    does not need a dashboard. Signing in drops them onto the one screen
    they work, with their own queue already filtered.
  </div>

  <h3>Whose records you can see</h3>
  <p>Separate from the menu, and it catches people out: two Area Managers
  in the same zone see the same screens but not the same rows. A record is
  visible to you if <b>you created it</b>, <b>it is assigned to you</b>, or
  <b>the person who created it reports to you</b>, directly or further down
  the line. So a Zonal Head sees both of their Area Managers and neither of
  the other zone's; the AVP sees both Zonal Heads; the CEO sees everyone. A
  super admin is outside the rule entirely.</p>

</section>

<section>
  <h2><span class="n">3</span>The journey, step by step</h2>
  <p class="lede">One deal, from the first phone call to a closed order.
  The role beside each step is the one that owns it.</p>

  {journey_steps()}

  <div class="note good">
    <b>What happens at every one of those steps, without anybody asking</b>
    The change is written into that record's own history with the person's
    name and the time; the next person's notification bell gets an entry
    that links straight to the record; and everyone above them in the
    reporting line can see it happen without being able to touch it.
  </div>
</section>

<section>
  <h2><span class="n">4</span>The three rules that keep it honest</h2>
  <p class="lede">Almost every refusal you will meet in the system comes
  from one of these three rules. They are worth knowing by heart.</p>

  <h3>Rule 1 — A discount is signed by whoever's authority it uses up</h3>
  <p>Approval is <b>cumulative, not a lookup</b>. An 18% discount does not
  simply “belong to the CEO” — it needs the AVP <i>and</i>
  the CEO, because the AVP's authority runs out at 15% and somebody has to
  own the rest.</p>

  {bands_table()}

  <ul>
    <li><b>An Area Manager and a Zonal Head can apply a discount but can
    never approve one</b> — not even a small one, and never their own.</li>
    <li>A rejection at any step ends the chain. The document goes back to
    Draft to be reworked.</li>
    <li>The bands are not hard-coded. A super admin changes them on
    <b>Masters → Proposal Approval</b>, and the new ceilings apply
    immediately without a deployment.</li>
  </ul>

  <h3>Rule 2 — Each stage of an order names the desk that owns it</h3>
  <p>Once an order is confirmed it stops being the salesperson's to push.
  Everybody who can see the order can still see where it has got to; they
  simply cannot move it.</p>

  {desks_table()}

  <p>A super admin stands in for anybody, at any stage. Nobody else can. A
  desk sees its own queue and is refused the other desk's entirely.</p>

  <h3>Rule 3 — Stock leaves the shelf on dispatch, and not a moment before</h3>
  <ul>
    <li><b>Out at Dispatched.</b> Not when it is picked, because a picked
    order can still be put back.</li>
    <li><b>Back on Cancelled or On Hold</b>, if it had already gone out.</li>
    <li><b>Every movement is written down</b> — which product, how
    many, which order, who did it, when. “What did this order take off
    the shelf?” always has an answer.</li>
    <li><b>It cannot happen twice.</b> Dispatching the same order again
    moves nothing, so a double click cannot empty the shelf.</li>
    <li><b>A short line empties rather than going negative.</b> If the shelf
    has three and the order wants four, the count lands on zero and the
    shortfall is visible, instead of reading minus one.</li>
    <li><b>It never blocks the dispatch.</b> A count that cannot be written
    must not stop an order going out, so a failed deduction is recorded and
    visible rather than silent.</li>
  </ul>

  <div class="note warn">
    <b>And one about writing at all</b>
    Seeing a record and changing one are different questions. Until this
    month only leads checked the second — every other write route in
    the pipeline accepted any signed-in user, so the accounts clerk and the
    warehouse could raise a proposal straight at the API, even though
    neither has a sales menu. Three write permissions now close that, held
    by the four sales roles and nobody else.
  </div>

  <div class="note">
    <b>And one rule about deleting</b>
    A record raised in error can be deleted — but only until something
    is built on top of it. A lead that became an opportunity is refused, and
    the refusal names the opportunity. An opportunity with a proposal on it
    is refused. A proposal with a sales order behind it, or an approval
    still pending, is refused. Deleting a record also clears its history and
    its notifications, so a bell entry never survives its subject.
  </div>
</section>

<section>
  <h2><span class="n">5</span>Every status, and what it means</h2>
  <p class="lede">These are the exact words the system stores. The screen
  may print them more prettily, but the value underneath is always one of
  these — and a move that is not listed here is refused with a message
  saying what <i>is</i> allowed.</p>

  {status_tables()}

  <div class="note">
    <b>One thing that is not obvious, and matters</b>
    Old records from before this vocabulary existed are translated on the
    way in, not left alone. A lead stored as <span class="mono">dead</span>
    reads as <b>Lost</b>; an order stored as
    <span class="mono">payment pending</span> reads as <b>Confirmed</b>. So
    an old record and a new one always answer the same question the same way.
  </div>
</section>

<section>
  <h2><span class="n">6</span>The data model</h2>
  <p class="lede">What is stored, and where. For a non-technical reader the
  shapes matter more than the field names: <b>one box is one kind of
  thing</b>, and an arrow means “this one points at that one”.</p>

  <h3>Two databases, on purpose</h3>
  <table>
    <thead><tr><th style="width:18%">Store</th><th style="width:34%">Holds</th>
    <th>Why there</th></tr></thead>
    <tbody>
      <tr><td><b>PostgreSQL</b><br><span class="pill floor">relational</span></td>
          <td>People, roles, permissions, companies, master lists, and the
          five documents of the deal — lead, opportunity, proposal, sales
          order, proforma invoice — with their histories.</td>
          <td>These records point at each other constantly and must never
          disagree. A proposal without its opportunity, or money on an
          invoice that does not reach the order, is a wrong answer, and this
          is the store that refuses to let that happen.</td></tr>
      <tr><td><b>MongoDB</b><br><span class="pill desk">document</span></td>
          <td>The product catalogue and the shelf, the stock movement
          ledger, and the customer directory.</td>
          <td>A product's attributes differ by product type — a panel has
          a screen size, a cable has a length — so there is no one fixed
          set of columns to hold them.</td></tr>
    </tbody>
  </table>

  <h3>The deal chain</h3>
  <p>Five documents, each raised off the one above it. Every change to any
  of them writes a history row; every change somebody needs to know about
  writes a notification.</p>

  <table>
    <thead><tr><th style="width:26%">Table</th><th style="width:22%">Points at</th>
    <th>What it carries</th></tr></thead>
    <tbody>
      <tr><td><span class="mono">sales_lead</span></td><td>—</td>
          <td>status, creator, who it is assigned to, customer type, state,
          lead source</td></tr>
      <tr><td><span class="mono">sales_opportunity</span></td>
          <td>one lead, at most</td>
          <td>status, deal value, priority, the product lines</td></tr>
      <tr><td><span class="mono">sales_quotation</span></td>
          <td>one opportunity</td>
          <td>quote number, status, the lines with their HSN, discount,
          total payable, the advance it is offered on</td></tr>
      <tr><td><span class="mono">sales_order</span></td>
          <td>one opportunity; the proposal number as <i>text</i></td>
          <td>order number, status, PO number, grand total, advance
          received, payment terms</td></tr>
      <tr><td><span class="mono">sales_proforma_invoice</span></td>
          <td>one sales order</td>
          <td>PI number, status, grand total, amount paid, balance due</td></tr>
      <tr><td><span class="mono">sales_approval</span></td>
          <td>a proposal <i>or</i> an order, by number</td>
          <td>the steps, the current step, the discount it was raised on,
          who asked and who decided</td></tr>
      <tr><td><span class="mono">sales_*_activity</span> × 5</td>
          <td>its own document</td>
          <td>action, description, from and to status, who and when. One
          table per document, deleted with it.</td></tr>
      <tr><td><span class="mono">notifications</span></td>
          <td>any document, by module and id</td>
          <td>one row per person who needs telling, with the actor's name on
          it</td></tr>
      <tr><td><span class="mono">document_counter</span></td><td>—</td>
          <td>one row per series. Only ever moves forward, so a deleted
          document's number is never handed out again.</td></tr>
    </tbody>
  </table>

  <div class="note warn">
    <b>Two soft links worth knowing about</b>
    A sales order carries its proposal number as <i>text</i> — the real
    key it is tied by is the opportunity. And an approval points at
    “proposal number 3250” rather than holding a key, because one
    approval table serves both proposals and orders. Both work, and both
    mean the database itself cannot catch a mismatch — only the code can.
  </div>

  <h3>People, permissions and the shelf</h3>
  <table>
    <thead><tr><th style="width:30%">Group</th><th>Tables</th></tr></thead>
    <tbody>
      <tr><td><b>Who can do what</b><br><span class="pill floor">PostgreSQL</span></td>
          <td><span class="mono">users</span> (the reporting line lives here,
          as <span class="mono">reports_to_id</span>) —
          <span class="mono">user_roles</span> —
          <span class="mono">roles</span> —
          <span class="mono">role_permissions</span> —
          <span class="mono">permissions</span>. A role is a bundle of
          permissions, changed on Roles &amp; Access, never in code.
          <span class="mono">user_companies</span> files a person under a
          company.</td></tr>
      <tr><td><b>The lists a super admin keeps</b><br>
          <span class="pill floor">PostgreSQL</span></td>
          <td><span class="mono">sales_state</span>,
          <span class="mono">sales_customer_type</span>,
          <span class="mono">sales_lead_source</span>,
          <span class="mono">product_types</span>,
          <span class="mono">category_groups</span>,
          <span class="mono">locations</span>,
          <span class="mono">workflows</span>,
          <span class="mono">app_setting</span>. Every dropdown in the
          application comes from one of these, and so do the discount bands
          and the bank details.</td></tr>
      <tr><td><b>The shelf and the customers</b><br>
          <span class="pill desk">MongoDB</span></td>
          <td><span class="mono">inventory_items</span> (SKU, attributes
          including the HSN, rate, unit, stock, the company it is filed
          under), <span class="mono">inventory_movements</span>,
          <span class="mono">inventory_templates</span>,
          <span class="mono">customers</span>.</td></tr>
    </tbody>
  </table>

  <div class="note">
    <b>Why a movement ledger instead of just a number</b>
    The shelf count could have been a single number that goes up and down.
    It is not, and this is the reason: when somebody asks “why does it
    say ten when I counted twelve last week?”, a number cannot answer.
    The ledger can — it lists every movement, with the order that caused
    it and the person who did it. The count on the product is the running
    total; the ledger is the explanation. When the two ever disagree, the
    ledger is right.
  </div>
</section>

<section>
  <h2><span class="n">7</span>How the tax is worked out</h2>
  <p class="lede">Nobody chooses this on a form. It follows from two things
  the records already hold: where we are registered, and where the customer
  is billed.</p>

  <div class="flow">
    <div class="box"><b>The product</b>carries an HSN code</div>
    <div class="box"><b>The code</b>gives the rate</div>
    <div class="box"><b>The two states</b>give the split</div>
    <div class="box"><b>The invoice</b>shows both, per code</div>
  </div>
  <p class="cap">Figure 2 — Four steps, none of them a choice somebody
  makes per document.</p>

  <h3>Inside the state, and out of it</h3>
  <p>We are registered in Uttar Pradesh, state code 09. When the billing
  address is also in Uttar Pradesh the tax is halved between the centre and
  the state — CGST 9% and SGST 9% — and the invoice shows both
  lines. When it is anywhere else it is one IGST line at 18%. The total tax
  is the same either way; only the heading it is collected under changes,
  and that heading is what the customer's own accountant reclaims against.</p>

  <p>Where the two halves cannot divide evenly, the second takes the extra
  paisa, so the pair always adds back to the whole.</p>

  <h3>The codes we classify under</h3>
  {gst_table()}

  <div class="note">
    <b>Why the rates are ours and not looked up</b>
    The GST portal's HSN lookup answers what a code is called, not what it
    is taxed at — it returns a description and nothing else. So the
    rate lives in a table of our own, which can be audited and corrected.
    Changing a rate is one line in one file, and every document follows.
  </div>

  <div class="note warn">
    <b>Checked against your own paperwork</b>
    The split was verified to the rupee against your printed delivery
    challan: ₹1,10,000 of goods plus ₹12,500 of charges, CGST
    ₹11,025 and SGST ₹11,025, grand total ₹1,44,550.
  </div>
</section>

<section>
  <h2><span class="n">8</span>The catalogue we price against</h2>
  <p class="lede">Built from the Noida 65 stock dashboard, with the rates
  and codes written on it. Nothing in it is indicative.</p>

  <h3>Panels, priced by the dashboard's own column</h3>
  <p>SPX carries no camera and CPX does, and the number is the size —
  6 is 65″, 7 is 75″, 8 is 86″, 9 is 98″ and 11 is
  110″. The LangoV100 row is the only row priced on the sheet, so it
  reads as the price list for the column rather than for that board alone:
  ₹68,000 for SPX6 up to ₹1,50,000 at the top. No camera premium
  is added on top, because the figure written against CPX already carries it.</p>

  <h3>What is in it</h3>
  <table>
    <thead><tr><th style="width:32%">Group</th><th>Detail</th>
    <th class="num" style="width:12%">Lines</th></tr></thead>
    <tbody>
      <tr><td><b>Interactive flat panels</b></td>
          <td>The twelve cells the dashboard shows stock against, out of
          forty-five possible — seeding the empty thirty-three would be
          a catalogue of things nobody sells.</td><td class="num">12</td></tr>
      <tr><td><b>OPS compute modules</b></td>
          <td>i5 and i7, 256GB and 512GB, by generation, ₹22,000 to
          ₹40,000, plus two bare units at ₹30,000.</td>
          <td class="num">12</td></tr>
      <tr><td><b>Standees</b></td>
          <td>Touch ₹60,000 and non-touch ₹57,000 — priced by
          the panel inside, not by cabinet size.</td><td class="num">2</td></tr>
      <tr><td><b>Cameras, mic and stand</b></td>
          <td>₹1,949, ₹2,500 and ₹3,000 for the cameras,
          ₹50,000 for the array mic, ₹11,000 for the panel stand.</td>
          <td class="num">5</td></tr>
    </tbody>
    <tfoot><tr><td>Total</td><td></td><td class="num">31</td></tr></tfoot>
  </table>

  <div class="note good">
    <b>Everything is priced</b>
    The seven lines the dashboard named without a figure were carried at
    zero and shown as <b>Price not set</b> in amber in every picker, rather
    than guessed at — an invented price on a customer's quotation is
    worse than a visible blank. The rates are in. The amber warning now
    means a real gap rather than a known one.
  </div>
</section>

<section>
  <h2><span class="n">9</span>The proforma invoice</h2>
  <p class="lede">Rebuilt to carry what a GST document has to carry, set in
  the same visual language as the rest of the application.</p>

  <table>
    <thead><tr><th style="width:34%">What it carries</th>
    <th>Why it is there</th></tr></thead>
    <tbody>
      <tr><td><b>Buyer and Consignee</b>, each with GSTIN and state code</td>
          <td>The pair of state codes is what decides how the tax splits, so
          both are printed where a reader can check it.</td></tr>
      <tr><td><b>Voucher no., date, buyer's reference, place of supply</b></td>
          <td>What the customer's accounts department matches the payment
          against. Stated once — they used to be printed twice on one
          page, in a banner and again in a panel.</td></tr>
      <tr><td><b>Goods</b> — description, HSN/SAC, GST rate, quantity,
          rate, amount</td>
          <td>The rate column is read from the server's own tax summary, so
          it cannot disagree with the tax charged below it.</td></tr>
      <tr><td><b>Every charge and deduction listed</b></td>
          <td>Discount, ORC, freight and installation each on their own line
          rather than folded into a subtotal — a customer querying a
          figure needs to see what made it.</td></tr>
      <tr><td><b>CGST and SGST, or IGST</b></td>
          <td>Never both, and never a flat “GST” that hides which
          was charged.</td></tr>
      <tr><td><b>HSN-wise tax summary</b></td>
          <td>Taxable value and tax under each code, so the arithmetic can
          be checked rather than taken on trust.</td></tr>
      <tr><td><b>Both figures in words</b></td>
          <td>Digits can be altered with a pen and words cannot. Written in
          lakh and crore, because that is how the digits beside them are
          grouped.</td></tr>
      <tr><td><b>Payment terms</b></td>
          <td>Carried from the proposal the client accepted, stated with the
          other conditions at the foot, and read-only there.</td></tr>
      <tr><td><b>Bank block and UPI code</b></td>
          <td>Read from Masters → Bank Details. The QR is the image
          your bank issued — one generated from a mistyped VPA would
          scan perfectly and pay nobody.</td></tr>
      <tr><td><b>E. &amp; O.E, <i>for</i> the company, authorised signature</b></td>
          <td>A company signs a document, not a person on their own
          account.</td></tr>
    </tbody>
  </table>

  <div class="note">
    <b>Preview and download are the same thing</b>
    Both render one component. There is no second template for the PDF, so
    the two cannot drift apart — which is how a preview comes to show
    something the customer never receives.
  </div>
</section>

<section>
  <h2><span class="n">10</span>What we have tested</h2>
  <p class="lede">Two kinds of testing. Eight automated suites that run the
  whole business against the real API in a few minutes, and a walkthrough
  done by hand in the browser, screen by screen, as each role.</p>

  <div class="note good">
    <b>Result, re-run on {PRETTY_DATE}</b>
    <span class="headline">{TOTAL} checks passed. 0 failed.</span>
    Every suite clears up after itself — the records it creates are
    deleted and the stock it consumes is put back — so running them
    does not leave litter behind for the next person.
  </div>

  {suites_table()}

  <h3>The refusals we specifically went looking for</h3>
  <p>A test that only checks the happy path proves very little. These are
  things the suites confirm are <b>not</b> possible — each one was a
  real risk, and each is now a passing check.</p>

  <table>
    <thead><tr><th style="width:52%">Somebody tries to…</th>
    <th>What happens</th></tr></thead>
    <tbody>
      <tr><td>A Zonal Head approves a discount</td><td>Refused — they have no discounting power at all</td></tr>
      <tr><td>The CEO signs before the AVP has</td><td>Refused — the chain runs in order, no jumping</td></tr>
      <tr><td>A salesperson approves their own discount</td><td>Refused</td></tr>
      <tr><td>A salesperson marks their own order as paid</td><td>Refused — that is the accounts desk's</td></tr>
      <tr><td>Accounts move an order into Procurement or Ready</td><td>Refused — those are the warehouse's</td></tr>
      <tr><td>The warehouse signs off the installation</td><td>Refused — the salesperson is the one on site</td></tr>
      <tr><td>The salesperson closes their own order</td><td>Refused — accounts close it</td></tr>
      <tr><td>Accounts or the warehouse raise a proposal</td><td>Refused — they hold no sales write permission</td></tr>
      <tr><td>A desk opens the other desk's queue</td><td>Refused</td></tr>
      <tr><td>A Zonal Head opens the other zone's records</td><td>Not visible at all</td></tr>
      <tr><td>An Area Manager opens the record of the manager beside them</td><td>Not visible at all</td></tr>
      <tr><td>A salesperson corrects a stock count</td><td>Refused — only the warehouse may</td></tr>
      <tr><td>One company's salesperson sees another company's products</td><td>Not visible at all</td></tr>
      <tr><td>An opportunity is opened on a lead never qualified</td><td>The lead is not even offered</td></tr>
      <tr><td>A lead that became an opportunity is deleted</td><td>Refused, and the refusal names the opportunity</td></tr>
      <tr><td>An order is dispatched twice</td><td>Nothing moves the second time — the shelf is safe</td></tr>
      <tr><td>An order wants four and the shelf has three</td><td>The count lands on zero, never negative, and the shortfall is visible</td></tr>
      <tr><td>A status is moved somewhere the workflow does not allow</td><td>Refused, with a message listing what <i>is</i> allowed</td></tr>
      <tr><td>Accounts verify an advance with no proforma invoice</td><td>Refused — raise one and record the advance first</td></tr>
      <tr><td>A deleted order's number is handed to the next one raised</td><td>It is not — references only ever move forward</td></tr>
    </tbody>
  </table>

  <h3>And the arithmetic we check, not just the permissions</h3>
  <ul>
    <li>A proposal is priced off the catalogue — the suite asserts the
    exact figure, so a wrong rate is caught rather than merely looking
    plausible.</li>
    <li>The HSN survives lead → opportunity → proposal → order
    → invoice without being retyped, and the tax groups under every
    code separately.</li>
    <li>Inside the state the two halves are equal and together are the tax
    charged; out of state it is one IGST line at the same total.</li>
    <li>The advance recorded on the invoice reaches the order, to the rupee,
    and the terms printed agree with the figure — including on a split
    that is not one of the standard ones.</li>
    <li>A dispatched order takes <b>every</b> line off the shelf, not just
    the first, and the shelf falls by exactly the quantity that went out.</li>
    <li>The sidebar each role receives matches, item for item, the list in
    the manual test script — so the two cannot drift apart without a
    test failing.</li>
  </ul>

  <h3>Running them yourself</h3>
  <p>One line each; they print a PASS or FAIL per check with a total at the
  bottom. Nothing needs cleaning up afterwards.</p>
  <table><tbody>
    {"".join(f"<tr><td class='mono'>docker exec -w /app backend_app python {esc(f)}</td></tr>" for _, f, _, _ in SUITES)}
  </tbody></table>
  <p class="cap">If the demo data is ever wiped, seed it again first with
  <span class="mono">seed_sales_team.py</span>,
  <span class="mono">seed_fulfilment_roles.py</span>,
  <span class="mono">seed_role_access.py</span> and
  <span class="mono">seed_synergy_catalogue.py</span>.</p>
</section>

<section>
  <h2><span class="n">11</span>Where we are today</h2>
  <p class="lede">{PRETTY_DATE}. Both repositories are pushed —
  frontend <span class="mono">{FRONTEND_SHA}</span>, backend
  <span class="mono">{BACKEND_SHA}</span>.</p>

  <div class="flow">
    <div class="box done"><b>Flow and roles</b>built and tested</div>
    <div class="box done"><b>Tax and catalogue</b>built and tested</div>
    <div class="box now"><b>Sign-off</b>we are here</div>
    <div class="box todo"><b>Email to clients</b>on your word</div>
    <div class="box todo"><b>Demo outside</b>after credentials change</div>
  </div>
  <p class="cap">Figure 3 — The two steps after this one are decisions,
  not build work.</p>

  <h3>Finished since the last issue</h3>
  {items(DONE)}

  <h3>What we found and fixed</h3>
  <p>Found while testing rather than reported. Two of them were stopping
  work outright.</p>
  {items(FIXED)}

  <h3>What is waiting on you</h3>
  {items(WAITING)}

  <div class="note warn">
    <b>Before this is shown to anyone outside the team</b>
    The ten demo accounts share one password on a local development
    database, and their Mailinator inboxes are readable by anyone who knows
    the address. Change the password and move to real addresses first, and
    never send anything confidential to those accounts.
  </div>

  <p class="foot">
    Synergy CRM — flow, data model, statuses and test status ·
    {PRETTY_DATE} · frontend <span class="mono">{FRONTEND_SHA}</span>
    · backend <span class="mono">{BACKEND_SHA}</span> ·
    {TOTAL} automated checks, 0 failures
  </p>
</section>

</body></html>
"""


def main() -> int:
    html_path = HERE / "Synergy-CRM-Status.html"
    html_path.write_text(HTML, encoding="utf-8")
    print(f"  wrote {html_path}")

    if "--html" in sys.argv:
        return 0

    pdf_path = HERE / "Synergy-CRM-Status.pdf"

    result = subprocess.run(
        [
            "google-chrome", "--headless", "--disable-gpu", "--no-sandbox",
            "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}",
            html_path.as_uri(),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )

    if not pdf_path.exists():
        print("  chrome did not produce a PDF:")
        print(result.stderr[-800:])
        return 1

    print(f"  wrote {pdf_path}  ({pdf_path.stat().st_size // 1024} KB)")

    if "--out" in sys.argv:
        target = Path(sys.argv[sys.argv.index("--out") + 1]).expanduser()
        target.mkdir(parents=True, exist_ok=True)

        copied = target / "Synergy-CRM-Flow-and-Test-Status.pdf"
        shutil.copy2(pdf_path, copied)
        print(f"  copied to {copied}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
