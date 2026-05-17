"""
User-content datastore for BlueLines.

SQLite via the stdlib `sqlite3` (no ORM). This module is the *only* place that
holds SQL, so swapping to Postgres later is contained here: keep callers using
the named functions below, keep statements parametrized, and keep SQL ANSI-ish.

This stores user-generated content (saved map pins now; fishing-session logs
and accounts later) -- never USGS readings, which are fetched live.
"""

import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.environ.get(
    "BLUELINES_DB",
    os.path.join(os.path.dirname(__file__), "bluelines.db"),
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _connect() as conn:
        # `INTEGER PRIMARY KEY AUTOINCREMENT` is the one SQLite-specific bit.
        # Postgres: use `id SERIAL PRIMARY KEY` (or `GENERATED ... AS IDENTITY`).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL
            )
            """
        )


def healthcheck() -> bool:
    with _connect() as conn:
        conn.execute("SELECT 1")
    return True


def list_pins() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, lat, lon, note, created_at FROM pins ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def add_pin(lat: float, lon: float, note: str) -> dict:
    created_at = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO pins (lat, lon, note, created_at) VALUES (?, ?, ?, ?)",
            (lat, lon, note, created_at),
        )
        row = conn.execute(
            "SELECT id, lat, lon, note, created_at FROM pins WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    return dict(row)


def delete_pin(pin_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM pins WHERE id = ?", (pin_id,))
    return cur.rowcount > 0
