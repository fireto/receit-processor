"""SQLite connection and schema migrations.

Migrations are append-only: PRAGMA user_version records how many have run; they run at
startup (app lifespan). Money is integer euro cents; timestamps are ISO-8601 UTC text,
receipt dates ISO dates (YYYY-MM-DD).
"""

import sqlite3
from pathlib import Path

MIGRATIONS = [
    # 1 — core schema
    """
    CREATE TABLE receipt (
        id               INTEGER PRIMARY KEY,
        created_at       TEXT NOT NULL,
        status           TEXT NOT NULL CHECK (status IN
                             ('queued', 'reading', 'ok', 'check', 'retake', 'failed')),
        source           TEXT NOT NULL CHECK (source IN ('camera', 'gallery', 'manual')),
        -- header, as read (and edited)
        date             TEXT,
        time             TEXT,
        total_cents      INTEGER,
        eik              TEXT,
        store_name       TEXT,
        store_address    TEXT,
        payment          TEXT CHECK (payment IN ('cash', 'card')),
        card_last4       TEXT,
        category         TEXT,
        payment_method   TEXT,             -- a value of the Sheet's ПлатежноСредство
        notes            TEXT,
        qr_json          TEXT,
        self_check_json  TEXT,
        quality_json     TEXT,
        -- reading queue: start_at is the ladder index the next reading starts at,
        -- next_start_at the one "Прочети отново" would use (NULL: no better model)
        queued_at        TEXT,
        start_at         INTEGER NOT NULL DEFAULT 0,
        next_start_at    INTEGER,
        -- Google Sheet: the row, and when it was last written (NULL with status ok:
        -- a write or an update is pending)
        sheet_row        INTEGER,
        sheet_written_at TEXT,
        confirmed_at     TEXT
    );
    CREATE INDEX receipt_status ON receipt (status, queued_at);
    CREATE INDEX receipt_created ON receipt (created_at);

    CREATE TABLE receipt_photo (
        receipt_id  INTEGER NOT NULL REFERENCES receipt(id) ON DELETE CASCADE,
        seq         INTEGER NOT NULL,
        path        TEXT NOT NULL,         -- relative to the photos directory
        width       INTEGER NOT NULL,
        height      INTEGER NOT NULL,
        PRIMARY KEY (receipt_id, seq)
    );

    CREATE TABLE receipt_line (
        receipt_id        INTEGER NOT NULL REFERENCES receipt(id) ON DELETE CASCADE,
        seq               INTEGER NOT NULL,
        name              TEXT NOT NULL,
        qty               REAL,
        unit              TEXT CHECK (unit IN ('pcs', 'kg')),
        unit_price_cents  INTEGER,
        total_cents       INTEGER NOT NULL,
        discount_cents    INTEGER NOT NULL DEFAULT 0,
        vat_group         TEXT,
        PRIMARY KEY (receipt_id, seq)
    );

    CREATE TABLE receipt_discount (
        receipt_id  INTEGER NOT NULL REFERENCES receipt(id) ON DELETE CASCADE,
        seq         INTEGER NOT NULL,
        name        TEXT NOT NULL,
        cents       INTEGER NOT NULL,
        PRIMARY KEY (receipt_id, seq)
    );

    -- Every model call. Kept when its receipt is deleted (receipt_id -> NULL): it is
    -- this month's spend.
    CREATE TABLE reading (
        id             INTEGER PRIMARY KEY,
        receipt_id     INTEGER REFERENCES receipt(id) ON DELETE SET NULL,
        provider       TEXT NOT NULL,
        model          TEXT NOT NULL,
        started_at     TEXT NOT NULL,
        seconds        REAL,
        input_tokens   INTEGER,
        output_tokens  INTEGER,
        cost_usd       REAL,
        outcome        TEXT NOT NULL CHECK (outcome IN
                           ('ok', 'check_failed', 'quality', 'error')),
        raw_json       TEXT,
        error          TEXT
    );
    CREATE INDEX reading_started ON reading (started_at);
    CREATE INDEX reading_receipt ON reading (receipt_id);
    """,
]


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Run the pending migrations."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, step in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(f"BEGIN; {step}; PRAGMA user_version = {i}; COMMIT;")
