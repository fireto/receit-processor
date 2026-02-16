"""Google Sheets integration: append, update, and delete expense rows."""

import os

import gspread
from google.oauth2.service_account import Credentials

from backend.config import SHEET_COLUMNS, ReceiptData

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_client: gspread.Client | None = None


def _get_client() -> gspread.Client:
    global _client
    if _client is None:
        sa_file = os.environ.get(
            "GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"
        )
        creds = Credentials.from_service_account_file(sa_file, scopes=SCOPES)
        _client = gspread.authorize(creds)
    return _client


def _get_worksheet() -> gspread.Worksheet:
    client = _get_client()
    sheet_id = os.environ["GOOGLE_SHEETS_ID"]
    worksheet_name = os.environ.get("GOOGLE_SHEETS_WORKSHEET", "Sheet1")
    spreadsheet = client.open_by_key(sheet_id)
    return spreadsheet.worksheet(worksheet_name)


def append_expense(receipt: ReceiptData) -> int:
    """Append an expense row to the sheet. Returns the row number."""
    ws = _get_worksheet()
    row = receipt.to_sheet_row()
    ws.append_row(row, value_input_option="USER_ENTERED")
    # Get the actual last row with data (not ws.row_count which includes empty rows)
    return len(ws.get_all_values())


def update_cell(row: int, column_name: str, value: str) -> None:
    """Update a single cell in the given row by column name."""
    if column_name not in SHEET_COLUMNS:
        raise ValueError(f"Unknown column: {column_name}")
    col_idx = SHEET_COLUMNS.index(column_name) + 1  # 1-based
    ws = _get_worksheet()
    ws.update_cell(row, col_idx, value)


def delete_row(row: int) -> None:
    """Delete a row from the sheet."""
    ws = _get_worksheet()
    ws.delete_rows(row)


def get_last_row_number() -> int:
    """Get the number of the last row with data."""
    ws = _get_worksheet()
    return len(ws.get_all_values())
