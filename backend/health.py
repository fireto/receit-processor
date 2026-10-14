"""/healthz: the reader thread runs, nothing waits too long, the data dir is writable."""

import os
from datetime import datetime, timedelta
from pathlib import Path

from backend import db

MAX_QUEUED = timedelta(minutes=10)


def check(db_path: Path | str, now: datetime, *, reader_alive: bool,
          photos_dir: Path) -> tuple[bool, dict]:
    details: dict = {"reader": "running" if reader_alive else "dead",
                     "oldest_queued_minutes": None, "database": "writable",
                     "photos": "writable", "sheet_pending": None}
    ok = reader_alive
    try:
        conn = db.connect(db_path)
        try:
            oldest = conn.execute("SELECT min(queued_at) FROM receipt "
                                  "WHERE status IN ('queued', 'reading')").fetchone()[0]
            if oldest:
                age = now - datetime.fromisoformat(oldest)
                details["oldest_queued_minutes"] = int(age.total_seconds() // 60)
                ok = ok and age <= MAX_QUEUED
            # informational: ok receipts whose Sheet write is still pending
            details["sheet_pending"] = conn.execute(
                "SELECT count(*) FROM receipt WHERE status = 'ok' "
                "AND sheet_written_at IS NULL").fetchone()[0]
            # a real write: rewrite the header's user_version with its own value
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(f"PRAGMA user_version = {int(version)}")
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        details["database"] = f"not writable: {exc}"
        ok = False
    photos = Path(photos_dir)
    while not photos.exists() and photos != photos.parent:  # created on the first upload
        photos = photos.parent
    if not os.access(photos, os.W_OK | os.X_OK):
        details["photos"] = "not writable"
        ok = False
    return ok, details
