"""The GoogleSheet adapter (backend/sheet_sync.py) over backend.sheets."""

from unittest.mock import patch

from backend import sheet_sync
from backend.config import ReceiptData


def test_named_ranges_are_cached():
    clock = [1000.0]
    sheet = sheet_sync.GoogleSheet(clock=lambda: clock[0])
    with patch("backend.sheets.get_categories", return_value=["Храна"]) as cats:
        assert sheet.categories() == ["Храна"]
        assert sheet.categories() == ["Храна"]
        assert cats.call_count == 1
        clock[0] += sheet_sync.CACHE_SECONDS + 1
        sheet.categories()
        assert cats.call_count == 2


def test_operations_delegate_to_the_sheets_module():
    sheet = sheet_sync.GoogleSheet()
    data = ReceiptData(date="14.10.2026", total_eur=12.34, category="Храна")
    with patch("backend.sheets.append_expense", return_value=12) as append, \
         patch("backend.sheets.update_expense") as update, \
         patch("backend.sheets.delete_row") as delete, \
         patch("backend.sheets.get_row", return_value={"Дата": "x"}) as get_row, \
         patch("backend.sheets.lookup_category_by_bulstat", return_value="Аптека") as eik, \
         patch("backend.sheets.get_payment_methods", return_value=["Cash"]):
        assert sheet.append(data) == 12
        sheet.update(12, data)
        sheet.delete(12)
        assert sheet.row(12) == {"Дата": "x"}
        assert sheet.category_for_eik("123") == "Аптека"
        assert sheet.payment_methods() == ["Cash"]
    append.assert_called_once_with(data)
    update.assert_called_once_with(12, data)
    delete.assert_called_once_with(12)
    get_row.assert_called_once_with(12)
    eik.assert_called_once_with("123")


def test_make_sheet_picks_the_fake_from_the_environment(monkeypatch):
    monkeypatch.setenv("RECEIPTS_SHEET", "fake")
    from backend.fakes import FakeSheet
    assert isinstance(sheet_sync.make_sheet(), FakeSheet)
    monkeypatch.delenv("RECEIPTS_SHEET")
    assert isinstance(sheet_sync.make_sheet(), sheet_sync.GoogleSheet)
