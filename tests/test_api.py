"""Integration tests for FastAPI endpoints."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.config import CATEGORIES, PAYMENT_METHODS, ReceiptData

TEST_TOKEN = "test-pin"
AUTH_HEADER = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture
def client():
    """Create a TestClient with mocked sheets module and auth configured."""
    import backend.main
    backend.main._last_written_row = None
    backend.main.AUTH_TOKEN = TEST_TOKEN

    with patch("backend.main.append_expense") as mock_append, \
         patch("backend.main.update_cell") as mock_update, \
         patch("backend.main.delete_row") as mock_delete, \
         patch("backend.main.get_last_row_number") as mock_last, \
         patch("backend.main.lookup_category_by_bulstat") as mock_lookup, \
         patch("backend.main.decode_receipt_qr") as mock_qr:
        mock_append.return_value = 42
        mock_last.return_value = 42
        mock_lookup.return_value = None
        mock_qr.return_value = None

        test_client = TestClient(backend.main.app)
        test_client._mock_append = mock_append
        test_client._mock_update = mock_update
        test_client._mock_delete = mock_delete
        test_client._mock_lookup = mock_lookup
        test_client._mock_qr = mock_qr
        yield test_client


class TestAuth:
    def test_no_token_returns_401(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 401

    def test_wrong_token_returns_401(self, client):
        resp = client.get("/api/config", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_valid_token_passes(self, client):
        resp = client.get("/api/config", headers=AUTH_HEADER)
        assert resp.status_code == 200


class TestGetConfig:
    def test_returns_categories_and_payments(self, client):
        resp = client.get("/api/config", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["categories"] == CATEGORIES
        assert data["payment_methods"] == PAYMENT_METHODS
        assert "providers" in data
        assert set(data["providers"]) == {"claude", "gemini", "grok"}
        assert "default_provider" in data


class TestUpload:
    @patch("backend.main.parse_receipt")
    def test_upload_success(self, mock_parse, client, sample_image_bytes):
        mock_parse.return_value = ReceiptData(
            date="15.02.2026",
            total_eur=23.45,
            category="Храна",
            payment_method="Revolut",
            notes="хляб, мляко",
            bulstat="123456789",
        )

        resp = client.post(
            "/api/upload",
            headers=AUTH_HEADER,
            files={"file": ("receipt.jpg", sample_image_bytes, "image/jpeg")},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["row"] == 42
        assert data["data"]["date"] == "15.02.2026"
        assert data["data"]["total_eur"] == 23.45
        assert data["data"]["category"] == "Храна"
        assert data["data"]["bulstat"] == "123456789"
        assert "qr" in data
        client._mock_append.assert_called_once()

    @patch("backend.main.parse_receipt")
    def test_upload_bulstat_category_automapping(self, mock_parse, client, sample_image_bytes):
        mock_parse.return_value = ReceiptData(
            date="15.02.2026",
            total_eur=10.0,
            category="Разни",
            bulstat="123456789",
        )
        client._mock_lookup.return_value = "Храна"

        resp = client.post(
            "/api/upload",
            headers=AUTH_HEADER,
            files={"file": ("receipt.jpg", sample_image_bytes, "image/jpeg")},
        )

        assert resp.status_code == 200
        assert resp.json()["data"]["category"] == "Храна"
        client._mock_lookup.assert_called_once_with("123456789")

    def test_upload_rejects_non_image(self, client):
        resp = client.post(
            "/api/upload",
            headers=AUTH_HEADER,
            files={"file": ("doc.txt", b"hello", "text/plain")},
        )
        assert resp.status_code == 400
        assert "image" in resp.json()["detail"].lower()

    @patch("backend.main.parse_receipt")
    def test_upload_parse_failure(self, mock_parse, client, sample_image_bytes):
        mock_parse.side_effect = Exception("OCR failed")

        resp = client.post(
            "/api/upload",
            headers=AUTH_HEADER,
            files={"file": ("receipt.jpg", sample_image_bytes, "image/jpeg")},
        )

        assert resp.status_code == 422
        assert "parse" in resp.json()["detail"].lower()

    @patch("backend.main.parse_receipt")
    def test_upload_sheets_failure(self, mock_parse, client, sample_image_bytes):
        mock_parse.return_value = ReceiptData(
            date="15.02.2026", total_eur=10.0, category="Храна"
        )
        client._mock_append.side_effect = Exception("Sheets API error")

        resp = client.post(
            "/api/upload",
            headers=AUTH_HEADER,
            files={"file": ("receipt.jpg", sample_image_bytes, "image/jpeg")},
        )

        assert resp.status_code == 500
        assert "google sheets" in resp.json()["detail"].lower()


class TestUpdateEntry:
    def test_update_category(self, client):
        resp = client.patch(
            "/api/entry/5",
            headers=AUTH_HEADER,
            json={"column": "Категория", "value": "Бебе"},
        )
        assert resp.status_code == 200
        client._mock_update.assert_called_once_with(5, "Категория", "Бебе")

    def test_update_invalid_column(self, client):
        client._mock_update.side_effect = ValueError("Unknown column")
        resp = client.patch(
            "/api/entry/5",
            headers=AUTH_HEADER,
            json={"column": "BadColumn", "value": "x"},
        )
        assert resp.status_code == 400


class TestDeleteEntry:
    def test_delete_row(self, client):
        resp = client.delete("/api/entry/5", headers=AUTH_HEADER)
        assert resp.status_code == 200
        client._mock_delete.assert_called_once_with(5)

    def test_delete_failure(self, client):
        client._mock_delete.side_effect = Exception("API error")
        resp = client.delete("/api/entry/5", headers=AUTH_HEADER)
        assert resp.status_code == 500


class TestUndo:
    @patch("backend.main.parse_receipt")
    def test_undo_after_upload(self, mock_parse, client, sample_image_bytes):
        mock_parse.return_value = ReceiptData(
            date="15.02.2026", total_eur=10.0, category="Храна"
        )

        client.post(
            "/api/upload",
            headers=AUTH_HEADER,
            files={"file": ("receipt.jpg", sample_image_bytes, "image/jpeg")},
        )

        resp = client.delete("/api/undo", headers=AUTH_HEADER)
        assert resp.status_code == 200

    def test_undo_nothing_returns_404(self, client):
        resp = client.delete("/api/undo", headers=AUTH_HEADER)
        assert resp.status_code == 404
