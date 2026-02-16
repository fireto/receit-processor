"""Integration tests for FastAPI endpoints."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.config import CATEGORIES, PAYMENT_METHODS, ReceiptData


@pytest.fixture
def client():
    """Create a TestClient with mocked sheets module."""
    # Reset the last written row state
    import backend.main
    backend.main._last_written_row = None

    with patch("backend.main.append_expense") as mock_append, \
         patch("backend.main.update_cell") as mock_update, \
         patch("backend.main.delete_row") as mock_delete, \
         patch("backend.main.get_last_row_number") as mock_last:
        mock_append.return_value = 42
        mock_last.return_value = 42

        # Store mocks on the client for assertions
        test_client = TestClient(backend.main.app)
        test_client._mock_append = mock_append
        test_client._mock_update = mock_update
        test_client._mock_delete = mock_delete
        yield test_client


class TestGetConfig:
    def test_returns_categories_and_payments(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["categories"] == CATEGORIES
        assert data["payment_methods"] == PAYMENT_METHODS


class TestUpload:
    @patch("backend.main.parse_receipt")
    def test_upload_success(self, mock_parse, client, sample_image_bytes):
        mock_parse.return_value = ReceiptData(
            date="15.02.2026",
            total_eur=23.45,
            category="Храна",
            payment_method="Revolut",
            notes="хляб, мляко",
        )

        resp = client.post(
            "/api/upload",
            files={"file": ("receipt.jpg", sample_image_bytes, "image/jpeg")},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["row"] == 42
        assert data["data"]["date"] == "15.02.2026"
        assert data["data"]["total_eur"] == 23.45
        assert data["data"]["category"] == "Храна"
        client._mock_append.assert_called_once()

    def test_upload_rejects_non_image(self, client):
        resp = client.post(
            "/api/upload",
            files={"file": ("doc.txt", b"hello", "text/plain")},
        )
        assert resp.status_code == 400
        assert "image" in resp.json()["detail"].lower()

    @patch("backend.main.parse_receipt")
    def test_upload_parse_failure(self, mock_parse, client, sample_image_bytes):
        mock_parse.side_effect = Exception("OCR failed")

        resp = client.post(
            "/api/upload",
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
            files={"file": ("receipt.jpg", sample_image_bytes, "image/jpeg")},
        )

        assert resp.status_code == 500
        assert "google sheets" in resp.json()["detail"].lower()


class TestUpdateEntry:
    def test_update_category(self, client):
        resp = client.patch(
            "/api/entry/5",
            json={"column": "Категория", "value": "Бебе"},
        )
        assert resp.status_code == 200
        client._mock_update.assert_called_once_with(5, "Категория", "Бебе")

    def test_update_invalid_column(self, client):
        client._mock_update.side_effect = ValueError("Unknown column")
        resp = client.patch(
            "/api/entry/5",
            json={"column": "BadColumn", "value": "x"},
        )
        assert resp.status_code == 400


class TestDeleteEntry:
    def test_delete_row(self, client):
        resp = client.delete("/api/entry/5")
        assert resp.status_code == 200
        client._mock_delete.assert_called_once_with(5)

    def test_delete_failure(self, client):
        client._mock_delete.side_effect = Exception("API error")
        resp = client.delete("/api/entry/5")
        assert resp.status_code == 500


class TestUndo:
    @patch("backend.main.parse_receipt")
    def test_undo_after_upload(self, mock_parse, client, sample_image_bytes):
        mock_parse.return_value = ReceiptData(
            date="15.02.2026", total_eur=10.0, category="Храна"
        )

        # Upload first
        client.post(
            "/api/upload",
            files={"file": ("receipt.jpg", sample_image_bytes, "image/jpeg")},
        )

        # Then undo
        resp = client.delete("/api/undo")
        assert resp.status_code == 200

    def test_undo_nothing_returns_404(self, client):
        resp = client.delete("/api/undo")
        assert resp.status_code == 404
