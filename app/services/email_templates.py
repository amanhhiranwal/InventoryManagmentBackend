"""One house style for every message the CRM sends.

A notification about a deal is a business letter, not a debug log. Each
one carries the company's own header, a short opening line, the facts as a
labelled table, an action button, and a signature from the person it came
from rather than "Synergy CRM Portal".

The plain-text alternative is built from the same pieces, so a client
reading in plain text gets the same letter without the styling.
"""

from html import escape

from sqlalchemy.orm import Session

from app.services.company_profile_service import CompanyProfileService

NAVY = "#1f477b"
INK = "#1f2d3d"
MUTED = "#64748b"
RULE = "#dbe2ec"
WASH = "#f4f7fb"


def _money(value) -> str:
    """Indian grouping, matching the proposal: 1,23,456.00."""

    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return "0.00"

    whole, decimals = f"{abs(amount):.2f}".split(".")

    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups) + "," + tail

    return f"{whole}.{decimals}"


def inr(value) -> str:
    return f"INR {_money(value)}"


class EmailLetter:
    """A message built the same way every time.

    ``facts`` are (label, value) pairs shown as a table; ``paragraphs`` are
    the body; ``sign_off`` names the person it is from. ``action`` is an
    optional (label, url) button.
    """

    def __init__(
        self,
        db: Session | None,
        *,
        heading: str,
        greeting: str | None = None,
        paragraphs: list[str] | None = None,
        facts: list[tuple[str, str]] | None = None,
        action: tuple[str, str] | None = None,
        note: str | None = None,
        sign_off_name: str | None = None,
        sign_off_title: str | None = None,
    ):
        profile = CompanyProfileService.as_lists(db)

        self.company = profile["company_legal_name"] or "Synergy Group"
        self.website = profile["company_website"]
        self.phone = profile["company_phone"]
        self.email = profile["company_email"]
        self.address = profile["company_address_lines"]

        self.heading = heading
        self.greeting = greeting
        self.paragraphs = [p for p in (paragraphs or []) if p]
        self.facts = [(k, v) for k, v in (facts or []) if v not in (None, "")]
        self.action = action
        self.note = note

        # Signed by the person who acted, falling back to whoever signs for
        # the company - never by the portal itself.
        self.sign_name = sign_off_name or profile["signatory_name"] or self.company
        self.sign_title = sign_off_title or (
            profile["signatory_title"] if not sign_off_name else None
        )

    # ------------------------------------------------------------------
    # Plain text
    # ------------------------------------------------------------------
    def text(self) -> str:
        lines: list[str] = []

        if self.greeting:
            lines += [self.greeting, ""]

        lines += [self.heading, ""]

        for paragraph in self.paragraphs:
            lines += [paragraph, ""]

        if self.facts:
            width = max(len(label) for label, _ in self.facts)
            lines += [f"{label.ljust(width)}  {value}" for label, value in self.facts]
            lines.append("")

        if self.action:
            label, url = self.action
            lines += [f"{label}: {url}", ""]

        if self.note:
            lines += [self.note, ""]

        lines += ["Regards,", self.sign_name]

        if self.sign_title:
            lines.append(self.sign_title)

        lines.append(self.company)

        contact = " | ".join(filter(None, [self.phone, self.email, self.website]))
        if contact:
            lines.append(contact)

        return "\n".join(lines).strip() + "\n"

    # ------------------------------------------------------------------
    # HTML
    # ------------------------------------------------------------------
    def html(self) -> str:
        def e(value) -> str:
            return escape(str(value or ""))

        facts_html = ""

        if self.facts:
            rows = "".join(
                f"""
                <tr>
                  <td style="padding:7px 14px;border-bottom:1px solid {RULE};
                             color:{MUTED};font-size:12px;white-space:nowrap;">{e(label)}</td>
                  <td style="padding:7px 14px;border-bottom:1px solid {RULE};
                             color:{INK};font-size:13px;font-weight:600;">{e(value)}</td>
                </tr>"""
                for label, value in self.facts
            )
            facts_html = f"""
              <table role="presentation" cellpadding="0" cellspacing="0" width="100%"
                     style="border:1px solid {RULE};border-radius:8px;
                            border-collapse:separate;overflow:hidden;
                            background:{WASH};margin:18px 0;">
                {rows}
              </table>"""

        action_html = ""

        if self.action:
            label, url = self.action
            action_html = f"""
              <p style="margin:22px 0 6px;">
                <a href="{e(url)}"
                   style="display:inline-block;background:{NAVY};color:#ffffff;
                          text-decoration:none;font-size:13px;font-weight:600;
                          padding:11px 22px;border-radius:8px;">{e(label)}</a>
              </p>"""

        paragraphs_html = "".join(
            f'<p style="margin:0 0 12px;color:{INK};font-size:14px;line-height:22px;">{e(p)}</p>'
            for p in self.paragraphs
        )

        greeting_html = (
            f'<p style="margin:0 0 14px;color:{INK};font-size:14px;">{e(self.greeting)}</p>'
            if self.greeting
            else ""
        )

        note_html = (
            f'<p style="margin:18px 0 0;color:{MUTED};font-size:12px;'
            f'line-height:19px;">{e(self.note)}</p>'
            if self.note
            else ""
        )

        sign_lines = [f"<strong>{e(self.sign_name)}</strong>"]

        if self.sign_title:
            sign_lines.append(e(self.sign_title))

        sign_lines.append(e(self.company))

        contact = " &nbsp;|&nbsp; ".join(
            e(part) for part in [self.phone, self.email, self.website] if part
        )

        return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#eef2f7;">
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%"
         style="background:#eef2f7;padding:28px 12px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" width="600"
             style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;
                    overflow:hidden;font-family:Segoe UI,Helvetica,Arial,sans-serif;
                    box-shadow:0 1px 3px rgba(16,24,40,.08);">

        <tr><td style="background:{NAVY};padding:18px 28px;">
          <span style="color:#ffffff;font-size:15px;font-weight:700;
                       letter-spacing:.3px;">{e(self.company)}</span>
        </td></tr>

        <tr><td style="padding:26px 28px 24px;">
          {greeting_html}
          <h1 style="margin:0 0 14px;color:{NAVY};font-size:18px;
                     line-height:25px;font-weight:700;">{e(self.heading)}</h1>
          {paragraphs_html}
          {facts_html}
          {action_html}
          {note_html}

          <table role="presentation" cellpadding="0" cellspacing="0"
                 style="margin-top:26px;border-top:1px solid {RULE};width:100%;">
            <tr><td style="padding-top:16px;color:{INK};font-size:13px;line-height:20px;">
              <span style="color:{MUTED};">Regards,</span><br/>
              {"<br/>".join(sign_lines)}
            </td></tr>
          </table>
        </td></tr>

        <tr><td style="background:{WASH};padding:14px 28px;border-top:1px solid {RULE};">
          <span style="color:{MUTED};font-size:11px;line-height:17px;">
            {contact or e(self.company)}
          </span>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body></html>"""
