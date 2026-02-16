"""Shared test fixtures."""

import os
from unittest.mock import MagicMock, patch

import pytest

from backend.config import ReceiptData


@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    """Set required environment variables for all tests."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    monkeypatch.setenv("GOOGLE_SHEETS_ID", "test-sheet-id")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "test_sa.json")
    monkeypatch.setenv("VISION_PROVIDER", "claude")


@pytest.fixture
def sample_receipt_data():
    """A valid ReceiptData instance for testing."""
    return ReceiptData(
        date="15.02.2026",
        total_eur=23.45,
        category="Храна",
        payment_method="Revolut",
        notes="хляб, мляко, сирене",
    )


@pytest.fixture
def sample_api_response():
    """A valid JSON response as would come from a vision API."""
    return {
        "date": "15.02.2026",
        "total_eur": 23.45,
        "category": "Храна",
        "payment_method": "Revolut",
        "notes": "хляб, мляко, сирене",
    }


@pytest.fixture
def sample_image_bytes():
    """Minimal JPEG bytes for testing (1x1 pixel)."""
    # Minimal valid JPEG
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
        b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
        b"\x1f\x1e\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342"
        b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
        b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00"
        b"\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
        b"\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04"
        b"\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07"
        b"\x22q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n\x16"
        b"\x17\x18\x19\x1a%&\'()*456789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz"
        b"\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99"
        b"\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7"
        b"\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5"
        b"\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1"
        b"\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa"
        b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd2\x8a+\xff\xd9"
    )


@pytest.fixture
def mock_worksheet():
    """A mocked gspread Worksheet."""
    ws = MagicMock()
    ws.append_row = MagicMock()
    ws.update_cell = MagicMock()
    ws.delete_rows = MagicMock()
    ws.get_all_values = MagicMock(return_value=[["header"] * 9, ["data"] * 9])
    ws.row_count = 2
    return ws
