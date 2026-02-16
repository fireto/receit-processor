"""Tests for receipt_parser.py — multi-model vision parsing."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.config import BGN_PER_EUR, ReceiptData
from backend.receipt_parser import (
    _parse_json_response,
    _validate_receipt_data,
    decode_receipt_qr,
    parse_receipt,
)


class TestParseJsonResponse:
    def test_plain_json(self):
        text = '{"date": "15.02.2026", "total_eur": 10.5}'
        result = _parse_json_response(text)
        assert result["date"] == "15.02.2026"
        assert result["total_eur"] == 10.5

    def test_json_in_code_fence(self):
        text = '```json\n{"date": "15.02.2026", "total_eur": 10.5}\n```'
        result = _parse_json_response(text)
        assert result["date"] == "15.02.2026"

    def test_json_in_plain_fence(self):
        text = '```\n{"date": "15.02.2026", "total_eur": 10.5}\n```'
        result = _parse_json_response(text)
        assert result["date"] == "15.02.2026"

    def test_json_with_surrounding_text(self):
        text = 'Here is the result:\n{"date": "15.02.2026", "total_eur": 10.5}\nDone.'
        result = _parse_json_response(text)
        assert result["total_eur"] == 10.5

    def test_no_json_raises(self):
        with pytest.raises(ValueError, match="No valid JSON"):
            _parse_json_response("This is not JSON at all")


class TestValidateReceiptData:
    def test_valid_data(self, sample_api_response):
        receipt = _validate_receipt_data(sample_api_response)
        assert receipt.date == "15.02.2026"
        assert receipt.total_eur == 23.45
        assert receipt.category == "Храна"
        assert receipt.notes == "хляб, мляко, сирене"
        assert receipt.bulstat == "123456789"

    def test_unknown_category_falls_back_to_razni(self):
        categories = ["Храна", "Разни"]
        data = {"date": "01.01.2026", "total_eur": 5.0, "category": "NonExistent"}
        receipt = _validate_receipt_data(data, categories)
        assert receipt.category == "Разни"

    def test_category_accepted_when_no_categories_provided(self):
        data = {"date": "01.01.2026", "total_eur": 5.0, "category": "Anything"}
        receipt = _validate_receipt_data(data)
        assert receipt.category == "Anything"

    def test_missing_fields_use_defaults(self):
        data = {}
        receipt = _validate_receipt_data(data)
        assert receipt.date == ""
        assert receipt.total_eur == 0.0
        assert receipt.category == "Разни"
        assert receipt.payment_method is None
        assert receipt.notes == ""
        assert receipt.bulstat is None
        assert receipt.card_last4 is None

    def test_card_last4_extracted(self):
        data = {"card_last4": "0889"}
        receipt = _validate_receipt_data(data)
        assert receipt.card_last4 == "0889"

    def test_card_last4_non_digits_stripped(self):
        data = {"card_last4": "**0889"}
        receipt = _validate_receipt_data(data)
        assert receipt.card_last4 == "0889"

    def test_card_last4_wrong_length_becomes_none(self):
        data = {"card_last4": "12345"}
        receipt = _validate_receipt_data(data)
        assert receipt.card_last4 is None

    def test_card_last4_null_stays_none(self):
        data = {"card_last4": None}
        receipt = _validate_receipt_data(data)
        assert receipt.card_last4 is None

    def test_bulstat_normalized_to_digits(self):
        data = {"bulstat": "BG 123-456-789"}
        receipt = _validate_receipt_data(data)
        assert receipt.bulstat == "123456789"

    def test_bulstat_bg_prefix_stripped(self):
        data = {"bulstat": "BG123456789"}
        receipt = _validate_receipt_data(data)
        assert receipt.bulstat == "123456789"

    def test_bulstat_null_stays_none(self):
        data = {"bulstat": None}
        receipt = _validate_receipt_data(data)
        assert receipt.bulstat is None

    def test_valid_category_accepted(self):
        categories = ["Храна", "Козметика", "Разни"]
        for cat in categories:
            data = {"category": cat}
            receipt = _validate_receipt_data(data, categories)
            assert receipt.category == cat


class TestBgnCalculation:
    def test_bgn_from_eur(self, sample_receipt_data):
        expected = round(23.45 * BGN_PER_EUR, 2)
        assert sample_receipt_data.total_bgn == expected

    def test_zero_eur(self):
        receipt = ReceiptData(date="01.01.2026", total_eur=0, category="Разни")
        assert receipt.total_bgn == 0.0

    def test_bgn_rounding(self):
        receipt = ReceiptData(date="01.01.2026", total_eur=1.0, category="Разни")
        assert receipt.total_bgn == round(BGN_PER_EUR, 2)


class TestSheetRow:
    def test_to_sheet_row_format(self, sample_receipt_data):
        row = sample_receipt_data.to_sheet_row()
        assert len(row) == 10
        assert row[0] == "15.02.2026"  # date
        assert row[1] == "Храна"  # category
        assert "," in row[2]  # BGN with comma decimal
        assert "," in row[3]  # EUR with comma decimal
        assert row[4] == ""  # GGBG empty
        assert row[5] == "Revolut"  # payment
        assert row[6] == ""  # extra fee
        assert row[7] == ""  # payback
        assert row[8] == "хляб, мляко, сирене"  # notes
        assert row[9] == ""  # bulstat (not set in fixture)

    def test_to_sheet_row_no_payment(self):
        receipt = ReceiptData(
            date="01.01.2026", total_eur=10.0, category="Храна"
        )
        row = receipt.to_sheet_row()
        assert row[5] == ""


class TestParseReceiptProviders:
    """Test parse_receipt with each provider by patching the _PROVIDERS dict."""

    def test_claude_provider(self, sample_api_response, sample_image_bytes):
        mock_fn = MagicMock(return_value=sample_api_response)
        categories = ["Храна", "Разни"]
        with patch.dict("backend.receipt_parser._PROVIDERS", {"claude": mock_fn}):
            receipt = parse_receipt(sample_image_bytes, "image/jpeg", provider="claude", categories=categories)

        mock_fn.assert_called_once_with(sample_image_bytes, "image/jpeg", categories)
        assert receipt.category == "Храна"
        assert receipt.total_eur == 23.45

    def test_gemini_provider(self, sample_api_response, sample_image_bytes):
        mock_fn = MagicMock(return_value=sample_api_response)
        categories = ["Храна", "Разни"]
        with patch.dict("backend.receipt_parser._PROVIDERS", {"gemini": mock_fn}):
            receipt = parse_receipt(sample_image_bytes, "image/jpeg", provider="gemini", categories=categories)

        mock_fn.assert_called_once_with(sample_image_bytes, "image/jpeg", categories)
        assert receipt.category == "Храна"

    def test_grok_provider(self, sample_api_response, sample_image_bytes):
        mock_fn = MagicMock(return_value=sample_api_response)
        categories = ["Храна", "Разни"]
        with patch.dict("backend.receipt_parser._PROVIDERS", {"grok": mock_fn}):
            receipt = parse_receipt(sample_image_bytes, "image/jpeg", provider="grok", categories=categories)

        mock_fn.assert_called_once_with(sample_image_bytes, "image/jpeg", categories)
        assert receipt.category == "Храна"

    def test_unknown_provider_raises(self, sample_image_bytes):
        with pytest.raises(ValueError, match="Unknown provider"):
            parse_receipt(sample_image_bytes, "image/jpeg", provider="unknown")

    def test_defaults_to_env_provider(self, sample_api_response, sample_image_bytes, monkeypatch):
        monkeypatch.setenv("VISION_PROVIDER", "claude")
        mock_fn = MagicMock(return_value=sample_api_response)
        with patch.dict("backend.receipt_parser._PROVIDERS", {"claude": mock_fn}):
            parse_receipt(sample_image_bytes, "image/jpeg")
        mock_fn.assert_called_once()

    def test_parser_error_propagates(self, sample_image_bytes):
        mock_fn = MagicMock(side_effect=Exception("API error"))
        with patch.dict("backend.receipt_parser._PROVIDERS", {"claude": mock_fn}):
            with pytest.raises(Exception, match="API error"):
                parse_receipt(sample_image_bytes, "image/jpeg", provider="claude")


class TestDecodeReceiptQr:
    def test_returns_none_for_image_without_qr(self, sample_image_bytes):
        result = decode_receipt_qr(sample_image_bytes)
        assert result is None

    def test_returns_none_when_pyzbar_not_available(self, sample_image_bytes):
        with patch.dict("sys.modules", {"pyzbar": None, "pyzbar.pyzbar": None}):
            result = decode_receipt_qr(sample_image_bytes)
            assert result is None

    def test_parses_valid_qr_data(self):
        """Test QR parsing with mocked pyzbar output."""
        mock_code = MagicMock()
        mock_code.type = "QRCODE"
        mock_code.data = b"FP12345*0001*15.02.2026*14:30*23.45"

        # Mock PIL and pyzbar via sys.modules since libzbar
        # may not be installed on the test host (only in Docker).
        # `from PIL import Image` reads sys.modules["PIL"].Image
        # `from pyzbar.pyzbar import decode` reads sys.modules["pyzbar.pyzbar"].decode
        mock_pil = MagicMock()
        mock_pyzbar = MagicMock()
        mock_pyzbar_pyzbar = MagicMock()
        mock_pyzbar_pyzbar.decode.return_value = [mock_code]

        with patch.dict("sys.modules", {
            "PIL": mock_pil,
            "PIL.Image": mock_pil.Image,
            "pyzbar": mock_pyzbar,
            "pyzbar.pyzbar": mock_pyzbar_pyzbar,
        }):
            result = decode_receipt_qr(b"fake-image")

        assert result is not None
        assert result["fp_number"] == "FP12345"
        assert result["receipt_number"] == "0001"
        assert result["date"] == "15.02.2026"
        assert result["time"] == "14:30"
        assert result["amount"] == 23.45
