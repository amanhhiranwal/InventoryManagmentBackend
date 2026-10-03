"""The label that goes on the box: a barcode, a QR and what they say.

A warehouse scans one of two things. A barcode reader at a desk wants
Code128, which carries the SKU and nothing else. A phone wants a QR, which
can carry enough to identify the item without a lookup - so it carries the
SKU, the name and the HSN as plain text.

Both are drawn from the SKU itself rather than stored, so a label can
never be out of date with the product it is on, and nothing has to be
regenerated when a name changes.

Drawn as SVG: it needs nothing the PDF did not already bring in, it stays
crisp at whatever size the sticker is printed, and the screen can show the
same markup it prints.
"""

import io

from reportlab.graphics import renderSVG
from reportlab.graphics.barcode import createBarcodeDrawing, qr
from reportlab.graphics.shapes import Drawing, String

#: Label canvas, in points: 75 x 30 mm, which is what a Code128 of a
#: seventeen-character SKU needs beside a QR without the two colliding.
LABEL_W = 250
LABEL_H = 85

#: The QR sits in the right-hand column, the barcode fills the rest.
QR_SIZE = 56


def _qr_drawing(payload: str, size: float) -> Drawing:
    widget = qr.QrCodeWidget(payload)

    bounds = widget.getBounds()
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]

    drawing = Drawing(size, size, transform=[size / width, 0, 0, size / height, 0, 0])
    drawing.add(widget)

    return drawing


def label_svg(sku: str, name: str = "", hsn: str = "") -> str:
    """One product's label as SVG markup."""

    sku = (sku or "").strip()

    # The QR carries enough to identify the item off a phone without a
    # round trip; the barcode carries only the SKU, which is all a desk
    # scanner can type into a field.
    payload = "\n".join(part for part in (sku, name, f"HSN {hsn}" if hsn else "") if part)

    label = Drawing(LABEL_W, LABEL_H)

    # The barcode is scaled to the column it has rather than drawn at its
    # natural width, so a long SKU shrinks the bars instead of running
    # under the QR beside it.
    # The QR takes the left column and the barcode the rest, at its
    # natural width. Scaling the bars to fit a narrower column was how
    # they ended up running under the QR - a label is cheap to make wider
    # and a squashed Code128 is harder for a scanner to read.
    square = _qr_drawing(payload or sku or "-", QR_SIZE)
    square.translate(4, 6)
    label.add(square)

    code = createBarcodeDrawing(
        "Code128", value=sku or "-", barHeight=30, humanReadable=True, fontSize=6,
    )
    code.translate(QR_SIZE + 12, 6)
    label.add(code)

    label.add(String(4, LABEL_H - 12, (name or "")[:40], fontSize=7, fontName="Helvetica-Bold"))

    if hsn:
        label.add(String(4, LABEL_H - 22, f"HSN {hsn}", fontSize=6, fontName="Helvetica"))

    buffer = io.StringIO()
    renderSVG.drawToFile(label, buffer)

    return buffer.getvalue()
