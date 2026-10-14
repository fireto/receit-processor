"""Decoding the Bulgarian fiscal QR code (ported from the PoC).

The code reads "<FP number>*<receipt number>*<yyyy-mm-dd>*<HH:MM:SS>*<total>".
"""

from __future__ import annotations

import io
import logging
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)


def parse_qr_text(text: str) -> dict | None:
    parts = text.strip().split("*")
    if len(parts) < 4:
        return None
    amount = amount_cents = None
    if len(parts) > 4:
        try:
            value = Decimal(parts[4].strip().replace(",", "."))
            amount_cents = int((value * 100).to_integral_value())
            amount = float(value)
        except InvalidOperation:
            logger.warning("QR amount not a number: %r", parts[4])
    return {
        "fp_number": parts[0],
        "receipt_number": parts[1],
        "date": parts[2],
        "time": parts[3] or None,
        "amount": amount,
        "amount_cents": amount_cents,
    }


def decode_qr(image: bytes) -> dict | None:
    """Decode the fiscal QR code of one photo; None when there is none (or no zbar)."""
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode as pyzbar_decode
    except ImportError:
        logger.warning("pyzbar/zbar or Pillow not available, skipping QR decode")
        return None
    try:
        codes = pyzbar_decode(Image.open(io.BytesIO(image)))
    except Exception as e:  # noqa: BLE001 — any unreadable image means "no QR"
        logger.warning("QR decode failed: %s", e)
        return None
    for code in codes:
        if code.type != "QRCODE":
            continue
        try:
            parsed = parse_qr_text(code.data.decode("utf-8"))
        except UnicodeDecodeError:
            continue
        if parsed:
            return parsed
    return None


def qr_total_cents(photos: list[bytes]) -> int | None:
    """The total from the first photo that carries a fiscal QR code."""
    for photo in photos:
        qr = decode_qr(photo)
        if qr:
            return qr.get("amount_cents")
    return None
