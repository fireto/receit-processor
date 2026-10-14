"""Fiscal QR code decoding (ported from the PoC)."""

import io

import pytest

from backend.reading.qr import decode_qr, parse_qr_text, qr_total_cents

QR_TEXT = "DT123456*0001234*2026-10-12*18:42:07*12.34"


def test_parse_fiscal_qr_text():
    qr = parse_qr_text(QR_TEXT)
    assert qr == {
        "fp_number": "DT123456",
        "receipt_number": "0001234",
        "date": "2026-10-12",
        "time": "18:42:07",
        "amount": 12.34,
        "amount_cents": 1234,
    }


def test_amount_cents_is_exact():
    # float(x) * 100 would give 1015.9999999999999 → 1015
    assert parse_qr_text("A*B*2026-10-12*10:00:00*10.16")["amount_cents"] == 1016


def test_parse_rejects_non_fiscal_text():
    assert parse_qr_text("https://example.com") is None
    assert parse_qr_text("A*B*C*D*abc")["amount"] is None


def test_qr_total_cents_takes_the_first_photo_with_a_code(monkeypatch):
    calls = []

    def fake(image):
        calls.append(image)
        return {"amount_cents": 500} if image == b"2" else None

    monkeypatch.setattr("backend.reading.qr.decode_qr", fake)
    assert qr_total_cents([b"1", b"2", b"3"]) == 500
    assert calls == [b"1", b"2"]


def test_decode_garbage_returns_none():
    assert decode_qr(b"not an image") is None


def test_decode_generated_qr_image():
    pytest.importorskip("qrcode")
    try:
        import pyzbar.pyzbar  # noqa: F401
    except ImportError:
        pytest.skip("zbar shared library not installed")
    import qrcode

    buf = io.BytesIO()
    qrcode.make(QR_TEXT).save(buf, format="PNG")
    qr = decode_qr(buf.getvalue())
    assert qr["amount_cents"] == 1234
    assert qr["date"] == "2026-10-12"
