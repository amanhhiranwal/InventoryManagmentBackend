"""Build the status document the team reads.

The first version of this was written once by hand and then had to be
rewritten by hand every time the figures moved, which is why it was four
days out of date within a week. It is a script now: the suite names and
their check counts are listed in one place at the top, the prose refers to
them rather than repeating them, and rebuilding takes one command.

    python3 docs/status.py              # writes docs/Synergy-CRM-Status.pdf
    python3 docs/status.py --html       # stops at the HTML, for a quick look

Rendered through Chrome's headless print, because that is what is on the
machine and it honours the same CSS the screen does.
"""

import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent

TODAY = date(2026, 10, 3)
PRETTY_DATE = "3 October 2026"

#: suite -> (what it proves, checks). The total is summed, never typed,
#: so the headline figure cannot drift from the rows under it.
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
        "New. One deal walked from lead to invoice on the catalogue we "
        "actually sell, then the invoice read as a customer reads it: the "
        "HSN against each line, the tax grouped under both codes, CGST and "
        "SGST inside the state and IGST out of it, and the terms it is "
        "payable on.",
        36,
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

#: What changed since the last issue of this document, 29 September.
DONE = [
    (
        "The tax is worked out properly, and shown",
        "A sale inside Uttar Pradesh is charged as CGST plus SGST; a sale "
        "out of it as one IGST line. Which one applies is decided by the "
        "billing address against our own registration — not chosen by "
        "hand, and not a flat 18% on everything as before. The invoice "
        "carries an HSN-wise table so a reader can check the arithmetic "
        "rather than take the total on trust, and the rate for each code "
        "comes from our own table rather than being typed per document.",
    ),
    (
        "Every product carries its HSN, and it travels",
        "The code is entered once on the product and is carried onto the "
        "opportunity, the proposal, the order and the invoice. It was being "
        "typed onto each document, or more often not at all — every "
        "invoice in the database had a dash where the code belongs, which "
        "is why the tax had nothing to group by.",
    ),
    (
        "The catalogue is the real one",
        "Twenty-nine products built from the Noida 65 stock dashboard: the "
        "panels as they are actually stocked, the OPS modules by generation, "
        "the standees, the cameras and the stand. The rates and the HSN "
        "codes are the ones written on that sheet. The six invented "
        "“Qonevo” products that every screen used to price against "
        "are gone.",
    ),
    (
        "Prices nobody has set say so",
        "Seven lines on the sheet are named but not priced — the four "
        "cameras, the mic and the stand, and the two non-assembled OPS. They "
        "show “Price not set” in amber in every product picker "
        "rather than ₹0.00, which reads like a free product and reaches "
        "the customer that way.",
    ),
    (
        "The proforma invoice is a GST document",
        "Buyer and consignee each with their GSTIN and state code, the "
        "document's own references, the goods with HSN and rate per line, "
        "the tax split shown as the two states call for, the HSN-wise "
        "summary, both figures in words, bank details, the UPI code, "
        "E. & O.E and the authorised signature. Preview and the downloaded "
        "PDF are one component, so they cannot differ.",
    ),
    (
        "Payment terms follow the deal instead of being retyped",
        "One list, served from one place, offered on the proposal, the order "
        "and the invoice. Picking a split sets both the percentage every "
        "figure is worked out from and the sentence printed on the document, "
        "so the wording and the arithmetic cannot drift apart. An invoice "
        "raised from an order that carries only the number now derives the "
        "sentence rather than printing no terms at all.",
    ),
    (
        "Bank details are editable by a super admin",
        "Beneficiary, bank, account, branch, IFSC, SWIFT, the UPI VPA and "
        "its QR image now live on the Company Profile screen. The invoice "
        "used to read them straight from the deployment's environment file, "
        "so an account number corrected on screen never reached the document "
        "the customer pays against — the one field where a stale value "
        "sends money to the wrong place.",
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
        "Document numbers collided instead of being reused",
        "The counter that hands out QT, SO and PI numbers only moves "
        "forward, which stops a deleted document's number being handed to "
        "the next one raised. It had no way to catch up, though: a number "
        "issued outside it left the counter sitting behind the data, and "
        "every allocation then collided with the unique index and told the "
        "user “Resource already exists”. Raising a proposal had "
        "been failing outright. It now checks the number it took is free, "
        "and on a clash lifts itself clear of everything already printed. "
        "Still forward-only — raised, never lowered.",
        "fixed",
    ),
    (
        "The AVP could see the whole of Masters",
        "An aborted test run had left the AVP role holding forty-four "
        "permissions instead of fourteen — Roles & Access, Company "
        "Profile, user and company creation, and both fulfilment desks. The "
        "grants are restated from one file now and the database made to "
        "match it.",
        "fixed",
    ),
    (
        "Test litter was sitting in live lists",
        "A role, a user, three permissions and two leads, all named with the "
        "timestamp of the run that created them, had been left behind by "
        "runs that stopped half way. Removed.",
        "fixed",
    ),
    (
        "Old records could not show what was built",
        "Every quotation, order and invoice in the database had been raised "
        "against the retired demo products. They carried no HSN, so they "
        "fell back to a flat rate and could not demonstrate the tax split at "
        "all. Cleared, with your go-ahead — seventeen proposals, four "
        "orders, three invoices and the twelve stock movements belonging to "
        "them, all of which pointed at a product that no longer exists. The "
        "counters were kept, so none of those numbers can be issued again.",
        "fixed",
    ),
]

WAITING = [
    (
        "Seven prices",
        "The cameras, the mic, the panel stand and the two non-assembled OPS "
        "modules are on the sheet without a figure. They are in the "
        "catalogue at zero and marked unpriced until you send the rates.",
    ),
    (
        "One rate inferred rather than read",
        "The dashboard prices SPX9 and CPX11 at ₹1,50,000 and leaves "
        "CPX9 blank. CPX9 has been given the same figure as the 98″ "
        "beside it. It is the only number in the catalogue that was not read "
        "off the sheet.",
    ),
    (
        "The email steps",
        "Sending a proposal or an invoice to a client sends a real email "
        "from the real account. Nothing has been sent, and nothing will be "
        "until you say go.",
    ),
    (
        "Approve from the email",
        "An approver clicking Approve in the notification email, rather than "
        "signing in to do it. It needs a decision from you first: a signed "
        "one-time link, or a link that still makes them sign in. The first "
        "is one click and the second is harder to misuse.",
    ),
    (
        "Before this leaves the office",
        "The demo accounts are on Mailinator, whose inboxes are public — "
        "anyone who knows the address can read them — and they share one "
        "password. Both need changing before any demo outside the team.",
    ),
]

GST_ROWS = [
    ("85285900", "Interactive flat panels, standees", "18%"),
    ("84714190", "75″ panels filed as data processing machines", "18%"),
    ("85291029", "OPS compute modules", "18%"),
    ("85258900", "Cameras and video bars", "18%"),
    ("85184000", "Microphones and array mics", "18%"),
    ("84733099", "Stands and mounting hardware", "18%"),
]


def esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


CSS = """
@page { size: A4; margin: 18mm 16mm 20mm; }

* { box-sizing: border-box; }

body {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 10.5pt;
  line-height: 1.48;
  color: #1f2937;
  margin: 0;
}

h1 { font-size: 30pt; line-height: 1.15; margin: 0 0 14px; color: #1e3a8a; }
h2 { font-size: 17pt; margin: 0 0 4px; color: #111827; page-break-after: avoid; }
h3 { font-size: 12pt; margin: 26px 0 8px; color: #111827; page-break-after: avoid; }

h2 .n { color: #2563eb; margin-right: 8px; }

p { margin: 0 0 10px; }

.lede { color: #6b7280; font-size: 11pt; margin: 0 0 14px; }

.rule { width: 56px; height: 5px; background: #2563eb; margin-bottom: 26px; }

.cover { padding-top: 150px; }
.cover .sub { font-size: 12.5pt; color: #4b5563; line-height: 1.6; margin-bottom: 150px; }

.meta { border-top: 1px solid #d1d5db; padding-top: 12px; font-size: 9.5pt; }
.meta div { margin-bottom: 3px; }
.meta b { color: #1e3a8a; display: inline-block; min-width: 92px; }

.toc { margin-top: 22px; font-size: 10pt; }
.toc div { padding: 7px 0; border-bottom: 1px dotted #d1d5db; }
.toc .n { color: #6b7280; display: inline-block; width: 20px; }
.toc .t { color: #374151; }
.toc .d { color: #9ca3af; }

section { page-break-before: always; }
section.cover { page-break-before: avoid; }

table { width: 100%; border-collapse: collapse; margin: 10px 0 16px; font-size: 9.5pt; }
th {
  background: #f3f4f6; text-align: left; font-weight: 600; color: #374151;
  padding: 7px 9px; border-bottom: 1px solid #d1d5db;
}
td { padding: 7px 9px; border-bottom: 1px solid #e5e7eb; vertical-align: top; }
td.num, th.num { text-align: right; }
tr { page-break-inside: avoid; }
tfoot td { font-weight: 700; border-top: 1px solid #9ca3af; border-bottom: none; }

code, .mono {
  font-family: "SFMono-Regular", Menlo, Consolas, monospace;
  font-size: 9pt; color: #374151;
}

.note {
  border-left: 3px solid #2563eb; background: #eff6ff;
  padding: 11px 14px; margin: 14px 0; page-break-inside: avoid;
}
.note.warn { border-left-color: #ea580c; background: #fff7ed; }
.note.good { border-left-color: #16a34a; background: #f0fdf4; }
.note > b:first-child { display: block; margin-bottom: 3px; }

.pill {
  display: inline-block; padding: 1px 7px; border-radius: 9px;
  font-size: 8pt; font-weight: 600;
}
.pill.ok { background: #dcfce7; color: #166534; }
.pill.next { background: #f1f5f9; color: #475569; }
.pill.you { background: #ffedd5; color: #9a3412; }

.headline { font-size: 15pt; font-weight: 700; color: #111827; }

ul { margin: 0 0 12px; padding-left: 18px; }
li { margin-bottom: 7px; }

.item { margin-bottom: 11px; page-break-inside: avoid; }
.item b { color: #111827; }

.flow { display: flex; gap: 7px; margin: 16px 0 6px; }
.flow .step {
  flex: 1; border: 1px solid #c7d2fe; background: #eef2ff; border-radius: 7px;
  padding: 9px 6px; text-align: center; font-size: 8.5pt;
}
.flow .step b { hyphens: none; }
.flow .step b { display: block; font-size: 9.5pt; margin-bottom: 2px; }
.flow .step.now { border-color: #ea580c; background: #fff7ed; }
.flow .step.done { border-color: #86efac; background: #f0fdf4; }
.flow .step.todo { border-color: #d1d5db; background: #f9fafb; color: #6b7280; }

.cap { font-size: 8.5pt; color: #9ca3af; margin-top: 2px; }
"""


def suites_table() -> str:
    rows = "".join(
        f"<tr><td><b>{esc(name)}</b><br><span class='mono'>{esc(f)}</span></td>"
        f"<td>{esc(what)}</td><td class='num'>{n}</td></tr>"
        for name, f, what, n in SUITES
    )
    return f"""
<table>
  <thead><tr><th style="width:27%">Suite</th><th>What it proves</th>
  <th class="num" style="width:10%">Checks</th></tr></thead>
  <tbody>{rows}</tbody>
  <tfoot><tr><td>Total</td><td></td><td class="num">{TOTAL}</td></tr></tfoot>
</table>"""


def gst_table() -> str:
    rows = "".join(
        f"<tr><td class='mono'>{esc(c)}</td><td>{esc(d)}</td>"
        f"<td class='num'>{esc(r)}</td></tr>"
        for c, d, r in GST_ROWS
    )
    return f"""
<table>
  <thead><tr><th style="width:18%">HSN</th><th>What it covers</th>
  <th class="num" style="width:12%">GST</th></tr></thead>
  <tbody>{rows}</tbody>
</table>"""


def items(pairs) -> str:
    return "".join(
        f"<div class='item'><b>{esc(h)}</b> {esc(b)}</div>" for h, b in pairs
    )


def fixed_table() -> str:
    rows = "".join(
        f"<tr><td><b>{esc(h)}</b></td><td>{esc(b)}</td>"
        f"<td><span class='pill ok'>{esc(s)}</span></td></tr>"
        for h, b, s in FIXED
    )
    return f"""
<table>
  <thead><tr><th style="width:27%">What we found</th>
  <th>What was changed</th><th style="width:10%">State</th></tr></thead>
  <tbody>{rows}</tbody>
</table>"""


HTML = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Synergy CRM — Status</title>
<style>{CSS}</style></head>
<body>

<section class="cover">
  <div class="rule"></div>
  <h1>Synergy CRM<br>Where the build stands</h1>
  <p class="sub">
    What the system now does with tax, the catalogue it prices against,<br>
    the document a customer is asked to pay, and what is tested.
  </p>

  <div class="meta">
    <div><b>Prepared for</b> the Synergy team — sales, accounts, warehouse and management</div>
    <div><b>Date</b> {PRETTY_DATE}</div>
    <div><b>Replaces</b> the status issued 29 September 2026</div>
    <div><b>Test result</b> {TOTAL} automated checks, 0 failures, re-run on {PRETTY_DATE}</div>
  </div>

  <div class="toc">
    <div><span class="n">1</span><span class="t">What changed since the last issue</span>
      <span class="d">— in one page</span></div>
    <div><span class="n">2</span><span class="t">How the tax is worked out</span>
      <span class="d">— CGST, SGST, IGST and the HSN behind them</span></div>
    <div><span class="n">3</span><span class="t">The catalogue we price against</span>
      <span class="d">— and the seven prices still missing</span></div>
    <div><span class="n">4</span><span class="t">The proforma invoice</span>
      <span class="d">— what it now carries, and why each part is there</span></div>
    <div><span class="n">5</span><span class="t">What we tested</span>
      <span class="d">— {TOTAL} checks across {len(SUITES)} suites</span></div>
    <div><span class="n">6</span><span class="t">What we found and fixed</span>
      <span class="d">— including one that was stopping work</span></div>
    <div><span class="n">7</span><span class="t">What is waiting on you</span>
      <span class="d">— five things, none of them large</span></div>
  </div>
</section>

<section>
  <h2><span class="n">1</span>What changed since the last issue</h2>
  <p class="lede">
    The last status said the flow worked. This one is about the documents
    that come out of it being correct — which is a different question,
    and the one that was quietly failing.
  </p>

  {items(DONE)}

  <div class="note good">
    <b>The one thing worth remembering</b>
    A proforma invoice is the document a customer pays against. Until this
    phase it priced from six products that do not exist, taxed everything at
    a flat rate it was never told to use, printed no HSN, and read the bank
    account from a file nobody in the office can edit. Each of those is now
    read from a single place that somebody owns.
  </div>
</section>

<section>
  <h2><span class="n">2</span>How the tax is worked out</h2>
  <p class="lede">
    Nobody chooses this on a form. It follows from two things the records
    already hold: where we are registered, and where the customer is billed.
  </p>

  <div class="flow">
    <div class="step"><b>The product</b>carries an HSN code</div>
    <div class="step"><b>The code</b>gives the rate</div>
    <div class="step"><b>The two states</b>give the split</div>
    <div class="step"><b>The invoice</b>shows both, per code</div>
  </div>
  <p class="cap">Figure 1 — Four steps, none of them a choice somebody makes per document.</p>

  <h3>Inside the state, and out of it</h3>
  <p>
    We are registered in Uttar Pradesh, state code 09. When the billing
    address is also in Uttar Pradesh the tax is halved between the centre and
    the state — CGST 9% and SGST 9% — and the invoice shows both
    lines. When it is anywhere else it is one IGST line at 18%. The total
    tax is the same either way; only the heading it is collected under
    changes, and that heading is what the customer's own accountant reclaims
    against.
  </p>
  <p>
    Where the two halves cannot divide evenly, the second takes the extra
    paisa, so the pair always adds back to the whole.
  </p>

  <h3>The codes we classify under</h3>
  {gst_table()}

  <div class="note">
    <b>Why the rates are ours and not looked up</b>
    The GST portal's HSN lookup answers what a code is called, not what it is
    taxed at — it returns a description and nothing else. So the rate
    lives in a table of our own, which can be audited and corrected. Changing
    a rate is one line in one file, and every document follows.
  </div>

  <div class="note warn">
    <b>Checked against your own paperwork</b>
    The split was verified to the rupee against your printed delivery challan:
    ₹1,10,000 of goods plus ₹12,500 of charges, CGST ₹11,025 and
    SGST ₹11,025, grand total ₹1,44,550.
  </div>
</section>

<section>
  <h2><span class="n">3</span>The catalogue we price against</h2>
  <p class="lede">
    Built from the Noida 65 stock dashboard, with the rates and codes written
    on it. Nothing in it is indicative.
  </p>

  <h3>Panels, priced by the dashboard's own column</h3>
  <p>
    SPX carries no camera and CPX does, and the number is the size — 6 is
    65″, 7 is 75″, 8 is 86″, 9 is 98″ and 11 is 110″.
    The LangoV100 row is the only row priced on the sheet, so it reads as the
    price list for the column rather than for that board alone: ₹68,000
    for SPX6 up to ₹1,50,000 at the top. No camera premium is added on
    top, because the figure written against CPX already carries it.
  </p>

  <h3>What is in it</h3>
  <table>
    <thead><tr><th style="width:34%">Group</th><th>Detail</th>
    <th class="num" style="width:12%">Lines</th></tr></thead>
    <tbody>
      <tr><td><b>Interactive flat panels</b></td>
          <td>The twelve cells the dashboard shows stock against, out of
              forty-five possible — seeding the empty thirty-three would
              be a catalogue of things nobody sells.</td>
          <td class="num">12</td></tr>
      <tr><td><b>OPS compute modules</b></td>
          <td>i5 and i7, 256GB and 512GB, by generation, ₹22,000 to
              ₹40,000.</td>
          <td class="num">10</td></tr>
      <tr><td><b>Standees</b></td>
          <td>Touch ₹60,000 and non-touch ₹57,000 — priced by
              the panel inside, not by cabinet size.</td>
          <td class="num">2</td></tr>
      <tr><td><b>Cameras, mic and stand</b></td>
          <td>Named on the sheet, none of them priced.</td>
          <td class="num">5</td></tr>
    </tbody>
    <tfoot><tr><td>Total</td><td></td><td class="num">29</td></tr></tfoot>
  </table>

  <div class="note warn">
    <b>Seven lines have no price</b>
    The four cameras, the array mic, the panel stand and the two
    non-assembled OPS modules appear on your sheet by name with no figure
    against them. They are in the catalogue at zero and every product picker
    shows them as <b>Price not set</b> in amber rather than ₹0.00 —
    which reads like a free product, and reaches a customer's quotation that
    way. Send the rates and they go in.
  </div>
</section>

<section>
  <h2><span class="n">4</span>The proforma invoice</h2>
  <p class="lede">
    Rebuilt to carry what a GST document has to carry, set in the same visual
    language as the rest of the application.
  </p>

  <table>
    <thead><tr><th style="width:34%">What it carries</th><th>Why it is there</th></tr></thead>
    <tbody>
      <tr><td><b>Buyer and Consignee</b>, each with GSTIN and state code</td>
          <td>The pair of state codes is what decides how the tax splits, so
              both are printed where a reader can check it.</td></tr>
      <tr><td><b>Voucher no., date, buyer's reference, place of supply</b></td>
          <td>What the customer's accounts department matches the payment
              against.</td></tr>
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
          <td>Taxable value and tax under each code, so the arithmetic can be
              checked rather than taken on trust.</td></tr>
      <tr><td><b>Both figures in words</b></td>
          <td>Digits can be altered with a pen and words cannot. Written in
              lakh and crore, because that is how the digits beside them are
              grouped.</td></tr>
      <tr><td><b>Payment terms</b></td>
          <td>Carried from the proposal the client accepted, not a default
              sentence that can quietly contradict the offer.</td></tr>
      <tr><td><b>Bank block and UPI code</b></td>
          <td>Read from the Company Profile screen. The QR is the image your
              bank issued — one generated from a mistyped VPA would scan
              perfectly and pay nobody.</td></tr>
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
  <h2><span class="n">5</span>What we tested</h2>
  <p class="lede">
    Eight automated suites that run the whole business against the real API in
    a few minutes, plus a walkthrough by hand in the browser.
  </p>

  <div class="note good">
    <b>Result, re-run on {PRETTY_DATE}</b>
    <span class="headline">{TOTAL} checks passed. 0 failed.</span>
    Every suite clears up after itself — the records it creates are
    deleted and the stock it consumes is put back — so running them does
    not leave litter behind for the next person.
  </div>

  {suites_table()}

  <h3>What the new suite goes looking for</h3>
  <p>
    The other suites prove each stage <i>moves</i>. This one proves that what
    comes out the far end is a document somebody could pay against, which was
    failing silently. It buys real panels and a real OPS module under two
    different HSN codes, so the tax table has to have two rows and add up.
  </p>
  <ul>
    <li>The HSN survives lead → opportunity → proposal → order
        → invoice without being retyped.</li>
    <li>The tax is grouped under both codes, not lumped under one.</li>
    <li>Inside the state it splits into CGST and SGST, the halves are equal,
        and together they are the tax charged.</li>
    <li>Out of state it is one IGST line with no CGST or SGST — and the
        same total, because only the heading changes.</li>
    <li>Each code's taxable value adds to the whole, and the grand total is
        the taxable value plus the tax.</li>
    <li>The place of supply follows the buyer, not us.</li>
    <li>The invoice states its terms, they are the split the proposal was
        quoted on, and the sentence agrees with the figure.</li>
  </ul>

  <h3>Running them yourself</h3>
  <p>One line each; they print a PASS or FAIL per check with a total at the bottom.</p>
  <table><tbody>
    {"".join(f"<tr><td class='mono'>docker exec -w /app backend_app python {esc(f)}</td></tr>" for _, f, _, _ in SUITES)}
  </tbody></table>
</section>

<section>
  <h2><span class="n">6</span>What we found and fixed</h2>
  <p class="lede">
    Four things, found while testing rather than reported. One of them was
    stopping work outright.
  </p>

  {fixed_table()}

  <div class="note warn">
    <b>The one that was stopping work</b>
    Raising a proposal had begun failing with “Resource already
    exists” and no explanation. The counter that hands out numbers was
    sitting at 3,211 while numbers up to 3,249 were already printed, so every
    attempt collided. It was not visible as a bug — it reads like a
    duplicate record — and it would have come back on any database where
    a number was ever issued outside the counter.
  </div>
</section>

<section>
  <h2><span class="n">7</span>What is waiting on you</h2>
  <p class="lede">
    Five things. None of them is large, and three are a single message.
  </p>

  {items(WAITING)}

  <h3>Where the work stands</h3>
  <div class="flow">
    <div class="step done"><b>Flow and roles</b>built and tested</div>
    <div class="step done"><b>Tax and catalogue</b>built and tested</div>
    <div class="step now"><b>Prices and sign-off</b>we are here</div>
    <div class="step todo"><b>Email to clients</b>on your word</div>
    <div class="step todo"><b>Demo outside</b>after credentials change</div>
  </div>
  <p class="cap">Figure 2 — The two steps after this one are decisions, not build work.</p>
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
            "google-chrome",
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path}",
            html_path.as_uri(),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    if not pdf_path.exists():
        print("  chrome did not produce a PDF:")
        print(result.stderr[-800:])
        return 1

    print(f"  wrote {pdf_path}  ({pdf_path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
