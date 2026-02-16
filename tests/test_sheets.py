"""Tests for sheets.py — Google Sheets operations with mocked gspread."""

from unittest.mock import MagicMock, patch

import pytest

from backend.config import SHEET_COLUMNS, ReceiptData
from backend.sheets import (
    append_expense,
    delete_row,
    get_last_row_number,
    lookup_category_by_bulstat,
    update_cell,
)


@pytest.fixture(autouse=True)
def reset_sheets_client():
    """Reset the cached gspread client between tests."""
    import backend.sheets
    backend.sheets._client = None
    yield
    backend.sheets._client = None


@pytest.fixture
def mock_gspread(mock_worksheet):
    """Patch gspread authorization and worksheet retrieval."""
    with patch("backend.sheets.Credentials") as mock_creds, \
         patch("backend.sheets.gspread.authorize") as mock_auth:
        mock_client = MagicMock()
        mock_spreadsheet = MagicMock()
        mock_spreadsheet.worksheet.return_value = mock_worksheet
        mock_client.open_by_key.return_value = mock_spreadsheet
        mock_auth.return_value = mock_client
        yield mock_worksheet


class TestAppendExpense:
    def test_appends_correct_row(self, mock_gspread, sample_receipt_data):
        row_num = append_expense(sample_receipt_data)

        mock_gspread.append_row.assert_called_once()
        call_args = mock_gspread.append_row.call_args
        row = call_args[0][0]

        assert row[0] == "15.02.2026"
        assert row[1] == "Храна"
        assert row[5] == "Revolut"
        assert row[8] == "хляб, мляко, сирене"

    def test_uses_user_entered_input_option(self, mock_gspread, sample_receipt_data):
        append_expense(sample_receipt_data)

        call_kwargs = mock_gspread.append_row.call_args[1]
        assert call_kwargs["value_input_option"] == "USER_ENTERED"

    def test_returns_row_number(self, mock_gspread, sample_receipt_data):
        # get_all_values returns header + 1 data row = 2 rows, then append adds 1 → 3
        mock_gspread.get_all_values.return_value = [["h"] * 10, ["d"] * 10, ["new"] * 10]
        row_num = append_expense(sample_receipt_data)
        assert row_num == 3

    def test_row_has_correct_column_count(self, mock_gspread, sample_receipt_data):
        append_expense(sample_receipt_data)
        row = mock_gspread.append_row.call_args[0][0]
        assert len(row) == len(SHEET_COLUMNS)

    def test_ggbg_column_empty(self, mock_gspread, sample_receipt_data):
        append_expense(sample_receipt_data)
        row = mock_gspread.append_row.call_args[0][0]
        assert row[4] == ""  # GGBG лв

    def test_comma_decimal_format(self, mock_gspread, sample_receipt_data):
        append_expense(sample_receipt_data)
        row = mock_gspread.append_row.call_args[0][0]
        # BGN and EUR should use comma as decimal separator
        assert "," in row[2]  # Цена лв
        assert "," in row[3]  # Цена €


class TestUpdateCell:
    def test_updates_correct_cell(self, mock_gspread):
        update_cell(5, "Категория", "Бебе")
        col_idx = SHEET_COLUMNS.index("Категория") + 1
        mock_gspread.update_cell.assert_called_once_with(5, col_idx, "Бебе")

    def test_update_notes(self, mock_gspread):
        update_cell(3, "Пояснения", "тест")
        col_idx = SHEET_COLUMNS.index("Пояснения") + 1
        mock_gspread.update_cell.assert_called_once_with(3, col_idx, "тест")

    def test_unknown_column_raises(self, mock_gspread):
        with pytest.raises(ValueError, match="Unknown column"):
            update_cell(1, "NonExistent", "value")


class TestDeleteRow:
    def test_deletes_correct_row(self, mock_gspread):
        delete_row(5)
        mock_gspread.delete_rows.assert_called_once_with(5)


class TestGetLastRowNumber:
    def test_returns_row_count(self, mock_gspread):
        mock_gspread.get_all_values.return_value = [["h"] * 10, ["d"] * 10, ["d"] * 10]
        assert get_last_row_number() == 3


class TestLookupCategoryByBulstat:
    def test_returns_none_for_empty_bulstat(self, mock_gspread):
        assert lookup_category_by_bulstat("") is None
        assert lookup_category_by_bulstat(None) is None

    def test_returns_none_when_no_matches(self, mock_gspread):
        mock_gspread.get_all_values.return_value = [
            SHEET_COLUMNS,
            ["01.01.2026", "Храна", "19,56", "10,00", "", "Cash", "", "", "тест", "999999999"],
        ]
        assert lookup_category_by_bulstat("123456789") is None

    def test_returns_category_for_matching_bulstat(self, mock_gspread):
        mock_gspread.get_all_values.return_value = [
            SHEET_COLUMNS,
            ["01.01.2026", "Храна", "19,56", "10,00", "", "Cash", "", "", "тест", "123456789"],
            ["02.01.2026", "Козметика", "9,78", "5,00", "", "Cash", "", "", "шампоан", "999999999"],
        ]
        assert lookup_category_by_bulstat("123456789") == "Храна"

    def test_returns_most_frequent_category(self, mock_gspread):
        mock_gspread.get_all_values.return_value = [
            SHEET_COLUMNS,
            ["01.01.2026", "Храна", "19,56", "10,00", "", "", "", "", "", "123456789"],
            ["02.01.2026", "Козметика", "9,78", "5,00", "", "", "", "", "", "123456789"],
            ["03.01.2026", "Храна", "15,00", "7,67", "", "", "", "", "", "123456789"],
        ]
        assert lookup_category_by_bulstat("123456789") == "Храна"

    def test_returns_none_when_header_missing_bulstat(self, mock_gspread):
        mock_gspread.get_all_values.return_value = [
            ["Дата", "Категория", "Цена лв"],  # No БУЛСТАТ column
            ["01.01.2026", "Храна", "19,56"],
        ]
        assert lookup_category_by_bulstat("123456789") is None
