"""
User-content datastore for BlueLines.

The *only* place that holds SQL. Two backends, selected at import time:

- Postgres  -- when `DATABASE_URL` is a postgres URL (production on Render).
- SQLite    -- otherwise (local dev, CI, tests). `BLUELINES_DB` sets the path.

Callers use the named functions below; SQL is written once with `?`
placeholders and translated per backend. Stores user-generated content
(saved map pins now; fishing-session logs and accounts later) -- never USGS
readings, which are fetched live.
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DATABASE_URL = os.environ.get("DATABASE_URL", "")
_IS_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))

# SQLite path (used only when not on Postgres). Kept as a module global so
# tests can monkeypatch it.
DB_PATH = os.environ.get(
    "BLUELINES_DB",
    os.path.join(os.path.dirname(__file__), "bluelines.db"),
)

if _IS_PG:
    import psycopg
    from psycopg.rows import dict_row


def _ph(sql: str) -> str:
    """Canonical SQL uses `?`; Postgres (psycopg) wants `%s`."""
    return sql.replace("?", "%s") if _IS_PG else sql


def _raw_connect():
    if _IS_PG:
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def _conn():
    """Commit on success, rollback on error, always close (both backends)."""
    conn = _raw_connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _owner_column_present(conn) -> bool:
    cur = conn.cursor()
    if _IS_PG:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'pins' AND column_name = 'owner_token'"
        )
        return cur.fetchone() is not None
    cur.execute("PRAGMA table_info(pins)")
    return any(r["name"] == "owner_token" for r in cur.fetchall())


def init_db() -> None:
    if _IS_PG:
        ddl = """
            CREATE TABLE IF NOT EXISTS pins (
                id BIGSERIAL PRIMARY KEY,
                lat DOUBLE PRECISION NOT NULL,
                lon DOUBLE PRECISION NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL
            )
        """
    else:
        ddl = """
            CREATE TABLE IF NOT EXISTS pins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL
            )
        """
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(ddl)
        # Additive migration: pins predate per-device ownership. Existing
        # rows keep owner_token = NULL (unowned -- not shown to any device).
        if not _owner_column_present(conn):
            cur.execute("ALTER TABLE pins ADD COLUMN owner_token TEXT")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_pins_owner ON pins (owner_token)"
        )


def healthcheck() -> bool:
    with _conn() as conn:
        conn.cursor().execute("SELECT 1")
    return True


def list_pins(owner: str) -> list[dict]:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph(
                "SELECT id, lat, lon, note, created_at FROM pins "
                "WHERE owner_token = ? ORDER BY created_at DESC"
            ),
            (owner,),
        )
        return [dict(r) for r in cur.fetchall()]


def add_pin(lat: float, lon: float, note: str, owner: str) -> dict:
    created_at = datetime.now(timezone.utc).isoformat()
    insert = _ph(
        "INSERT INTO pins (lat, lon, note, created_at, owner_token) "
        "VALUES (?, ?, ?, ?, ?)"
        + (" RETURNING id, lat, lon, note, created_at" if _IS_PG else "")
    )
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(insert, (lat, lon, note, created_at, owner))
        if _IS_PG:
            return dict(cur.fetchone())
        # SQLite: fetch the row by the autoincrement id.
        cur.execute(
            "SELECT id, lat, lon, note, created_at FROM pins WHERE id = ?",
            (cur.lastrowid,),
        )
        return dict(cur.fetchone())


def delete_pin(pin_id: int, owner: str) -> bool:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("DELETE FROM pins WHERE id = ? AND owner_token = ?"),
            (pin_id, owner),
        )
        return cur.rowcount > 0
