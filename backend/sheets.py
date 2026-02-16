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


def _get_named_range(name: str) -> list[str]:
    """Read values from a named range, returning a flat list of non-empty strings."""
    client = _get_client()
    sheet_id = os.environ["GOOGLE_SHEETS_ID"]
    spreadsheet = client.open_by_key(sheet_id)
    result = spreadsheet.values_get(name)
    values = result.get("values", [])
    return [cell for row in values for cell in row if cell.strip()]


def get_categories() -> list[str]:
    """Read categories from the named range Категории."""
    return _get_named_range("Категории")


def get_payment_methods() -> list[str]:
    """Read payment methods from the named range ПлатежноСредство."""
    return _get_named_range("ПлатежноСредство")


def lookup_category_by_bulstat(bulstat: str) -> str | None:
    """Find the most common category for a given БУЛСТАТ from previous entries.

    Returns the category string or None if no previous entries found.
    """
    if not bulstat:
        return None

    ws = _get_worksheet()
    rows = ws.get_all_values()
    if len(rows) < 2:  # Only header or empty
        return None

    header = rows[0]
    try:
        bulstat_col = header.index("БУЛСТАТ")
        category_col = header.index("Категория")
    except ValueError:
        return None

    categories: dict[str, int] = {}
    for row in rows[1:]:
        if len(row) > bulstat_col and row[bulstat_col] == bulstat:
            if len(row) > category_col and row[category_col]:
                cat = row[category_col]
                categories[cat] = categories.get(cat, 0) + 1

    if not categories:
        return None

    return max(categories, key=categories.get)
