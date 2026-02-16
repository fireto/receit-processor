"""Tests for receipt_parser.py — multi-model vision parsing."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.config import BGN_PER_EUR, CATEGORIES, ReceiptData
from backend.receipt_parser import (
    _parse_json_response,
    _validate_receipt_data,
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
        assert receipt.payment_method == "Revolut"
        assert receipt.notes == "хляб, мляко, сирене"

    def test_unknown_category_falls_back_to_razni(self):
        data = {"date": "01.01.2026", "total_eur": 5.0, "category": "NonExistent"}
        receipt = _validate_receipt_data(data)
        assert receipt.category == "Разни"

    def test_unknown_payment_method_becomes_none(self):
        data = {
            "date": "01.01.2026",
            "total_eur": 5.0,
            "category": "Храна",
            "payment_method": "Bitcoin",
        }
        receipt = _validate_receipt_data(data)
        assert receipt.payment_method is None

    def test_missing_fields_use_defaults(self):
        data = {}
        receipt = _validate_receipt_data(data)
        assert receipt.date == ""
        assert receipt.total_eur == 0.0
        assert receipt.category == "Разни"
        assert receipt.payment_method is None
        assert receipt.notes == ""

    def test_all_categories_are_valid(self):
        for cat in CATEGORIES:
            data = {"category": cat}
            receipt = _validate_receipt_data(data)
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
        assert len(row) == 9
        assert row[0] == "15.02.2026"  # date
        assert row[1] == "Храна"  # category
        assert "," in row[2]  # BGN with comma decimal
        assert "," in row[3]  # EUR with comma decimal
        assert row[4] == ""  # GGBG empty
        assert row[5] == "Revolut"  # payment
        assert row[6] == ""  # extra fee
        assert row[7] == ""  # payback
        assert row[8] == "хляб, мляко, сирене"  # notes

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
        with patch.dict("backend.receipt_parser._PROVIDERS", {"claude": mock_fn}):
            receipt = parse_receipt(sample_image_bytes, "image/jpeg", provider="claude")

        mock_fn.assert_called_once_with(sample_image_bytes, "image/jpeg")
        assert receipt.category == "Храна"
        assert receipt.total_eur == 23.45

    def test_gemini_provider(self, sample_api_response, sample_image_bytes):
        mock_fn = MagicMock(return_value=sample_api_response)
        with patch.dict("backend.receipt_parser._PROVIDERS", {"gemini": mock_fn}):
            receipt = parse_receipt(sample_image_bytes, "image/jpeg", provider="gemini")

        mock_fn.assert_called_once_with(sample_image_bytes, "image/jpeg")
        assert receipt.category == "Храна"

    def test_grok_provider(self, sample_api_response, sample_image_bytes):
        mock_fn = MagicMock(return_value=sample_api_response)
        with patch.dict("backend.receipt_parser._PROVIDERS", {"grok": mock_fn}):
            receipt = parse_receipt(sample_image_bytes, "image/jpeg", provider="grok")

        mock_fn.assert_called_once_with(sample_image_bytes, "image/jpeg")
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
