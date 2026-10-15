"""The label that goes on the box: a barcode, a QR, and what they say.

A warehouse scans one of two things. A barcode reader at a desk wants
Code128, which carries the serial number and nothing else, because that
is all it can type into a field. A phone wants a QR, and a QR can carry
enough to be worth scanning: a link straight to this product in the CRM,
so whoever is holding the box can see what it is, what it costs and how
many are left.

Both identify the same product by the same serial number, and both are
drawn from the record rather than stored, so a label can never be out of
date with the product it is on and the same product always produces the
same label - byte for byte. Scanning the sticker on the shelf and
scanning the one on screen land in the same place.

Drawn as SVG: it needs nothing the PDF did not already bring in, it stays
crisp at whatever size the sticker is printed, and the screen can show
the same markup it prints.
"""

import io

from reportlab.graphics import renderSVG
from reportlab.graphics.barcode import createBarcodeDrawing, qr
from reportlab.graphics.shapes import Drawing, String

#: Label canvas, in points: wide enough for a Code128 of a long serial
#: beside a QR, and tall enough to say what the thing is. A sticker that
#: only carries a code means walking back to a screen to find out what is
#: in the box.
LABEL_W = 320
LABEL_H = 132

#: The QR sits in the left-hand column, the barcode under the text.
QR_SIZE = 72


def _qr_drawing(payload: str, size: float) -> Drawing:
    widget = qr.QrCodeWidget(payload)

    bounds = widget.getBounds()
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]

    drawing = Drawing(size, size, transform=[size / width, 0, 0, size / height, 0, 0])
    drawing.add(widget)

    return drawing


def _fit(text: str, width: float, size: float) -> str:
    """Cut text to what fits, with an ellipsis when it had to be cut.

    Helvetica averages about 0.5 of its point size per character, which is
    close enough for a sticker and does not need the font metrics loaded.
    """

    text = " ".join(str(text or "").split())

    room = int(width / (size * 0.5))

    if len(text) <= room:
        return text

    return text[: max(1, room - 1)] + "…"


def _wrap(text: str, width: float, size: float, lines: int) -> list[str]:
    """The text broken over at most that many lines, on word boundaries."""

    words = " ".join(str(text or "").split()).split(" ")

    room = max(8, int(width / (size * 0.5)))

    out: list[str] = []
    current = ""

    for word in words:
        candidate = f"{current} {word}".strip()

        if len(candidate) <= room:
            current = candidate
            continue

        out.append(current)
        current = word

        if len(out) == lines:
            break

    if current and len(out) < lines:
        out.append(current)

    if not out:
        return []

    # Whatever did not fit is marked rather than dropped silently.
    used = len(" ".join(out))

    if used < len(" ".join(words)):
        out[-1] = _fit(out[-1] + " …", width, size)

    return out


def label_svg(
    sku: str,
    name: str = "",
    hsn: str = "",
    model: str = "",
    specification: str = "",
    unit: str = "",
    url: str = "",
) -> str:
    """One product's label as SVG markup.

    ``url`` is where the QR points - this product's page in the CRM. With
    no address to send anyone to, the QR carries the same facts as plain
    text instead, so it is still worth scanning away from the network.
    """

    sku = (sku or "").strip()
    model = (model or "").strip()
    specification = (specification or "").strip()

    # What the QR is for: standing at the shelf with a phone and wanting
    # to know what this is. A link opens the product; the text fallback
    # at least says what it is without one.
    payload = (url or "").strip() or "\n".join(
        part
        for part in (
            sku,
            name,
            f"Model {model}" if model else "",
            specification,
            f"HSN {hsn}" if hsn else "",
        )
        if part
    )

    label = Drawing(LABEL_W, LABEL_H)

    square = _qr_drawing(payload or sku or "-", QR_SIZE)
    square.translate(8, LABEL_H - QR_SIZE - 8)
    label.add(square)

    text_left = QR_SIZE + 20
    text_width = LABEL_W - text_left - 8

    cursor = LABEL_H - 18

    label.add(
        String(
            text_left,
            cursor,
            _fit(name, text_width, 9),
            fontSize=9,
            fontName="Helvetica-Bold",
        )
    )

    if model:
        cursor -= 12
        label.add(
            String(
                text_left,
                cursor,
                _fit(f"Model {model}", text_width, 7.5),
                fontSize=7.5,
                fontName="Helvetica-Bold",
            )
        )

    for line in _wrap(specification, text_width, 6.5, 3):
        cursor -= 9
        label.add(String(text_left, cursor, line, fontSize=6.5, fontName="Helvetica"))

    footnote = "   ".join(
        part for part in (f"HSN {hsn}" if hsn else "", f"Unit {unit}" if unit else "") if part
    )

    if footnote:
        cursor -= 10
        label.add(
            String(text_left, cursor, _fit(footnote, text_width, 6.5), fontSize=6.5)
        )

    # The barcode runs the full width under everything, so a desk scanner
    # gets the longest bars the sticker can give it.
    code = createBarcodeDrawing(
        "Code128", value=sku or "-", barHeight=26, humanReadable=True, fontSize=7,
    )
    code.translate(8, 6)
    label.add(code)

    buffer = io.StringIO()
    renderSVG.drawToFile(label, buffer)

    return buffer.getvalue()
