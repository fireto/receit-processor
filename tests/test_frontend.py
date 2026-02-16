"""E2E tests for the frontend using Playwright.

These tests start the FastAPI server and test the full PWA flow
in a headless browser. They require playwright to be installed:
    pip install playwright && playwright install chromium

Run with: pytest tests/test_frontend.py -v
"""

import json
import subprocess
import time
from unittest.mock import patch

import pytest

from backend.config import ReceiptData

# Skip all tests in this module if playwright is not installed
playwright = pytest.importorskip("playwright")

from playwright.sync_api import sync_playwright


@pytest.fixture(scope="module")
def server():
    """Start the FastAPI server for E2E testing."""
    proc = subprocess.Popen(
        ["python", "-m", "uvicorn", "backend.main:app", "--port", "8765"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(2)  # Wait for server to start
    yield "http://localhost:8765"
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture(scope="module")
def browser_page(server):
    """Launch a headless browser and navigate to the app."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 390, "height": 844},  # iPhone-like viewport
        )
        page = context.new_page()
        page.goto(server)
        yield page
        browser.close()


class TestFrontendLoads:
    def test_page_title(self, browser_page):
        assert "Receipt" in browser_page.title()

    def test_upload_area_visible(self, browser_page):
        upload = browser_page.locator("#uploadArea")
        assert upload.is_visible()

    def test_file_input_exists(self, browser_page):
        file_input = browser_page.locator("#fileInput")
        assert file_input.count() == 1

    def test_result_card_hidden_initially(self, browser_page):
        card = browser_page.locator("#resultCard")
        assert not card.is_visible()

    def test_categories_loaded(self, browser_page):
        options = browser_page.locator("#resCategory option")
        assert options.count() >= 20  # We have 20 categories

    def test_payment_methods_loaded(self, browser_page):
        options = browser_page.locator("#resPayment option")
        # 11 payment methods + 1 empty option
        assert options.count() >= 12

    def test_mobile_viewport(self, browser_page):
        viewport = browser_page.viewport_size
        assert viewport["width"] == 390
        assert viewport["height"] == 844
