"""FastAPI application: receipt upload, edit, undo endpoints."""

import hmac
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.config import CATEGORIES, PAYMENT_METHODS, VERSION, ReceiptData
from backend.receipt_parser import AVAILABLE_PROVIDERS, decode_receipt_qr, parse_receipt
from backend.sheets import (
    append_expense,
    delete_row,
    get_last_row_number,
    lookup_category_by_bulstat,
    update_cell,
)

logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI(title="Receipt Processor")

AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require Bearer token for /api/* routes."""
    if AUTH_TOKEN and request.url.path.startswith("/api/"):
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        token = auth_header.removeprefix("Bearer ")
        if not hmac.compare_digest(token, AUTH_TOKEN):
            return JSONResponse(status_code=401, content={"detail": "Invalid token"})
    return await call_next(request)

# Track last written row per session for undo
_last_written_row: int | None = None

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


class UpdateRequest(BaseModel):
    column: str
    value: str


class ManualEntryRequest(BaseModel):
    date: str
    total_eur: float
    category: str
    payment_method: str | None = None
    notes: str = ""


@app.get("/api/config")
def get_config():
    """Return categories and payment methods for the frontend."""
    return {
        "version": VERSION,
        "categories": CATEGORIES,
        "payment_methods": PAYMENT_METHODS,
        "providers": AVAILABLE_PROVIDERS,
        "default_provider": os.environ.get("VISION_PROVIDER", "claude"),
    }


@app.post("/api/upload")
async def upload_receipt(
    file: UploadFile = File(...),
    provider: str | None = None,
):
    """Upload a receipt image, parse it, and write to Google Sheets."""
    global _last_written_row

    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    image_bytes = await file.read()
    mime_type = file.content_type

    # Decode QR code (fast, local — runs before AI call)
    qr_data = decode_receipt_qr(image_bytes)

    try:
        receipt = parse_receipt(image_bytes, mime_type, provider=provider)
    except Exception as e:
        logger.error("Failed to parse receipt: %s", e, exc_info=True)
        raise HTTPException(
            status_code=422, detail=f"Failed to parse receipt: {e}"
        )

    # Cross-validate QR data with AI output
    if qr_data and qr_data.get("amount") is not None:
        diff = abs(receipt.total_eur - qr_data["amount"])
        if diff > 0.01:
            logger.warning(
                "QR amount (%.2f) differs from AI-parsed amount (%.2f) by %.2f",
                qr_data["amount"], receipt.total_eur, diff,
            )

    # БУЛСТАТ → category auto-mapping
    if receipt.bulstat:
        try:
            historical_cat = lookup_category_by_bulstat(receipt.bulstat)
            if historical_cat and receipt.category == "Разни":
                logger.info(
                    "Auto-mapped БУЛСТАТ %s to category '%s' from history",
                    receipt.bulstat, historical_cat,
                )
                receipt.category = historical_cat
        except Exception as e:
            logger.warning("Failed to lookup category by БУЛСТАТ: %s", e)

    try:
        row_number = append_expense(receipt)
        _last_written_row = row_number
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to write to Google Sheets: {e}"
        )

    return {
        "row": row_number,
        "data": {
            "date": receipt.date,
            "total_eur": receipt.total_eur,
            "total_bgn": receipt.total_bgn,
            "category": receipt.category,
            "payment_method": receipt.payment_method,
            "notes": receipt.notes,
            "bulstat": receipt.bulstat,
        },
        "qr": qr_data,
    }


@app.post("/api/manual")
def manual_entry(req: ManualEntryRequest):
    """Create a manual expense entry and write to Google Sheets."""
    global _last_written_row

    receipt = ReceiptData(
        date=req.date,
        total_eur=req.total_eur,
        category=req.category,
        payment_method=req.payment_method or None,
        notes=req.notes,
    )

    try:
        row_number = append_expense(receipt)
        _last_written_row = row_number
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to write to Google Sheets: {e}"
        )

    return {
        "row": row_number,
        "data": {
            "date": receipt.date,
            "total_eur": receipt.total_eur,
            "total_bgn": receipt.total_bgn,
            "category": receipt.category,
            "payment_method": receipt.payment_method,
            "notes": receipt.notes,
        },
    }


@app.patch("/api/entry/{row}")
def update_entry(row: int, req: UpdateRequest):
    """Update a single field in an existing entry."""
    try:
        update_cell(row, req.column, req.value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to update: {e}"
        )
    return {"ok": True}


@app.delete("/api/entry/{row}")
def delete_entry(row: int):
    """Delete an entry (undo)."""
    try:
        delete_row(row)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to delete: {e}"
        )
    return {"ok": True}


@app.delete("/api/undo")
def undo_last():
    """Delete the last written entry."""
    global _last_written_row
    if _last_written_row is None:
        raise HTTPException(status_code=404, detail="Nothing to undo")
    try:
        delete_row(_last_written_row)
        _last_written_row = None
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to undo: {e}"
        )
    return {"ok": True}


# Serve frontend static files — mount on /static and add a catch-all for index.html
# This avoids the mount intercepting /api/* routes
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/{path:path}")
    async def serve_frontend(path: str):
        """Serve frontend files, falling back to index.html for SPA routing."""
        file_path = FRONTEND_DIR / path
        if path and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")
