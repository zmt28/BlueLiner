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

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

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
        # Durable cross-restart caches. site_no is a stable USGS id, so the
        # PK doubles as the cache key. river_geom is immutable per site
        # (NLDI flowline geometry never changes); river_stats carries a
        # created_at so callers can treat medians as stale after ~30 days.
        # Identical DDL on both backends (TEXT PK works everywhere).
        cur.execute(
            "CREATE TABLE IF NOT EXISTS river_geom ("
            " site_no TEXT PRIMARY KEY,"
            " geojson TEXT NOT NULL,"
            " created_at TEXT NOT NULL)"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS river_stats ("
            " site_no TEXT PRIMARY KEY,"
            " medians TEXT NOT NULL,"
            " created_at TEXT NOT NULL)"
        )
        # Out-of-band precomputed per-state river snapshot (the exact
        # `rivers` list /api/rivers returns). Served instantly so a user
        # request never blocks on USGS; the background refresher keeps it
        # fresh. Survives cold starts (Postgres), so even a just-woken
        # free-tier worker paints from the last snapshot, not a live fetch.
        cur.execute(
            "CREATE TABLE IF NOT EXISTS river_snapshot ("
            " state TEXT PRIMARY KEY,"
            " payload TEXT NOT NULL,"
            " updated_at TEXT NOT NULL)"
        )
        # Authoritative per-gauge NHD identity: comid + gnis_name from
        # NLDI. Used to label rivers by their real NHD name (so a small
        # tributary's gauge doesn't visually label the larger river its
        # flowline reaches). Immutable per site -- once cached, forever.
        cur.execute(
            "CREATE TABLE IF NOT EXISTS gauge_meta ("
            " site_no TEXT PRIMARY KEY,"
            " payload TEXT NOT NULL,"
            " created_at TEXT NOT NULL)"
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


def _upsert(table: str, key: str, payload_col: str, payload: str,
            key_col: str = "site_no", ts_col: str = "created_at") -> None:
    ts = datetime.now(timezone.utc).isoformat()
    if _IS_PG:
        sql = _ph(
            f"INSERT INTO {table} ({key_col}, {payload_col}, {ts_col}) "
            f"VALUES (?, ?, ?) ON CONFLICT ({key_col}) DO UPDATE SET "
            f"{payload_col} = EXCLUDED.{payload_col}, "
            f"{ts_col} = EXCLUDED.{ts_col}"
        )
    else:
        sql = (
            f"INSERT OR REPLACE INTO {table} "
            f"({key_col}, {payload_col}, {ts_col}) VALUES (?, ?, ?)"
        )
    with _conn() as conn:
        conn.cursor().execute(sql, (key, payload, ts))


def get_river_geom(site_no: str) -> dict | None:
    """Cached NLDI flowline FeatureCollection for a site, or None.

    Geometry is immutable per site, so there is no staleness check.
    """
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("SELECT geojson FROM river_geom WHERE site_no = ?"),
            (site_no,),
        )
        row = cur.fetchone()
    if not row:
        return None
    try:
        return json.loads(row["geojson"])
    except (ValueError, TypeError):
        return None


def put_river_geom(site_no: str, fc: dict) -> None:
    _upsert("river_geom", site_no, "geojson", json.dumps(fc))


def get_river_geoms(site_nos: list[str]) -> dict[str, dict]:
    """Batched flowline lookup for the /api/river_lines payload -- one
    query instead of a per-site connection in a loop."""
    if not site_nos:
        return {}
    placeholders = ",".join("?" for _ in site_nos)
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph(
                "SELECT site_no, geojson FROM river_geom "
                f"WHERE site_no IN ({placeholders})"
            ),
            tuple(site_nos),
        )
        rows = cur.fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        try:
            out[r["site_no"]] = json.loads(r["geojson"])
        except (ValueError, TypeError):
            continue
    return out


def get_river_snapshot(state: str) -> tuple[list, str] | None:
    """(rivers, updated_at_iso) for a state's last precomputed snapshot,
    or None if it has never been computed."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("SELECT payload, updated_at FROM river_snapshot WHERE state = ?"),
            (state,),
        )
        row = cur.fetchone()
    if not row:
        return None
    try:
        return json.loads(row["payload"]), row["updated_at"]
    except (ValueError, TypeError):
        return None


def put_river_snapshot(state: str, rivers: list) -> None:
    _upsert("river_snapshot", state, "payload", json.dumps(rivers),
            key_col="state", ts_col="updated_at")


def get_gauge_meta(site_no: str) -> dict | None:
    """NHD identity ({comid, gnis_name}) for a USGS gauge, or None."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("SELECT payload FROM gauge_meta WHERE site_no = ?"),
            (site_no,),
        )
        row = cur.fetchone()
    if not row:
        return None
    try:
        return json.loads(row["payload"])
    except (ValueError, TypeError):
        return None


def get_gauge_metas(site_nos: list[str]) -> dict[str, dict]:
    """Batched NHD-identity lookup -- one query for many sites, used by
    the per-request `_assemble_rivers` path to label rivers by GNIS."""
    if not site_nos:
        return {}
    placeholders = ",".join("?" for _ in site_nos)
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph(
                "SELECT site_no, payload FROM gauge_meta "
                f"WHERE site_no IN ({placeholders})"
            ),
            tuple(site_nos),
        )
        rows = cur.fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        try:
            out[r["site_no"]] = json.loads(r["payload"])
        except (ValueError, TypeError):
            continue
    return out


def put_gauge_meta(site_no: str, meta: dict) -> None:
    _upsert("gauge_meta", site_no, "payload", json.dumps(meta))


_STATS_MAX_AGE = timedelta(days=30)


def get_river_stats(site_nos: list[str]) -> dict[str, dict]:
    """Per-site daily median map ({"m-d": cfs}) for rows fresher than
    ~30 days. Stale/missing sites are simply absent from the result."""
    if not site_nos:
        return {}
    placeholders = ",".join("?" for _ in site_nos)
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph(
                "SELECT site_no, medians, created_at FROM river_stats "
                f"WHERE site_no IN ({placeholders})"
            ),
            tuple(site_nos),
        )
        rows = cur.fetchall()
    out: dict[str, dict] = {}
    now = datetime.now(timezone.utc)
    for r in rows:
        try:
            created = datetime.fromisoformat(r["created_at"])
        except (ValueError, TypeError):
            continue
        if now - created > _STATS_MAX_AGE:
            continue
        try:
            out[r["site_no"]] = json.loads(r["medians"])
        except (ValueError, TypeError):
            continue
    return out


def put_river_stats(site_no: str, medians: dict) -> None:
    _upsert("river_stats", site_no, "medians", json.dumps(medians))
