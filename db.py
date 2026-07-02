"""
User-content datastore for Blueliner.

The *only* place that holds SQL. Two backends, selected at import time:

- Postgres  -- when `DATABASE_URL` is a postgres URL (production on Render).
- SQLite    -- otherwise (local dev, CI, tests). `BLUELINES_DB` sets the path.

Callers use the named functions below; SQL is written once with `?`
placeholders and translated per backend. Stores user-generated content
(saved map pins now; fishing-session logs and accounts later) -- never USGS
readings, which are fetched live.
"""

import hashlib
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
    "BLUELINER_DB",
    os.path.join(os.path.dirname(__file__), "blueliner.db"),
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
        # Durable cross-restart cache. site_no is a stable USGS id, so the
        # PK doubles as the cache key; river_stats carries a created_at so
        # callers can treat medians as stale after ~30 days. Identical DDL
        # on both backends (TEXT PK works everywhere).
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
        # Authoritative per-gauge NHD identity: comid + gnis_name +
        # levelpathid from NLDI/VAA. Used to label rivers by their real NHD
        # name (so a small tributary's gauge doesn't visually label the
        # larger river) and to match clicked reaches by levelpath. Immutable
        # per site -- once cached, forever.
        cur.execute(
            "CREATE TABLE IF NOT EXISTS gauge_meta ("
            " site_no TEXT PRIMARY KEY,"
            " payload TEXT NOT NULL,"
            " created_at TEXT NOT NULL)"
        )
        # NHDPlusV2 Value-Added Attributes -- the authoritative routing
        # topology for every NHD flowline in the loaded regions. Used to
        # filter NLDI walks by LevelPathID so flowlines geometrically
        # cannot bleed across river identities at confluences.
        # Bulk-loaded once from data/nhdplus/vaa.csv.gz on first boot;
        # ~300K rows for HUC-02 + HUC-05 (mid-Atlantic). The data is
        # frozen at the NHDPlusV2 release so this table never needs to
        # refresh; expand by re-running scripts/build_nhdplus_vaa.py.
        cur.execute(
            "CREATE TABLE IF NOT EXISTS nhdplus_vaa ("
            " comid BIGINT PRIMARY KEY,"
            " hydroseq BIGINT,"
            " levelpathid BIGINT,"
            " streamlevel INTEGER,"
            " gnis_name TEXT,"
            " lengthkm REAL,"
            # Smoothed reach-end elevations (cm) from NHDPlus elevslope.dbf;
            # drive the stream elevation/gradient profile. Added after the
            # original 6-col table shipped -> the ALTER below backfills the
            # columns on existing deployments (the next VAA reload populates
            # them; until then they're NULL and the profile endpoint 404s).
            " maxelevsmo INTEGER,"
            " minelevsmo INTEGER)"
        )
        for _col in ("maxelevsmo", "minelevsmo"):
            if _IS_PG:
                cur.execute(
                    f"ALTER TABLE nhdplus_vaa ADD COLUMN IF NOT EXISTS "
                    f"{_col} INTEGER")
            else:
                try:
                    cur.execute(
                        f"ALTER TABLE nhdplus_vaa ADD COLUMN {_col} INTEGER")
                except Exception:
                    pass   # SQLite: column already exists -> ignore
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_vaa_levelpath "
            "ON nhdplus_vaa(levelpathid)"
        )
        # clickable_streams + public_lands were retired when those layers
        # moved to static vector tiles (M3). river_geom + comid_meta were
        # retired with the river-lines flowline layer (its client layer went
        # in PR #90; this drops the now-dead server cache). Drop the legacy
        # tables so existing deployments reclaim the storage -- a one-time
        # cleanup (a no-op once dropped); removable once all envs migrated.
        cur.execute("DROP TABLE IF EXISTS clickable_streams")
        cur.execute("DROP TABLE IF EXISTS public_lands")
        cur.execute("DROP TABLE IF EXISTS river_geom")
        cur.execute("DROP TABLE IF EXISTS comid_meta")
        # Real user accounts (Phase 1). Magic-link auth: no passwords
        # to manage. `display_name` defaults to the email's local part
        # at first sign-in; `deleted_at` enables soft delete so
        # claimed pins survive account purge.
        if _IS_PG:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS users ("
                " id BIGSERIAL PRIMARY KEY,"
                " email TEXT UNIQUE NOT NULL,"
                " display_name TEXT,"
                " created_at TEXT NOT NULL,"
                " deleted_at TEXT)"
            )
        else:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS users ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " email TEXT UNIQUE NOT NULL,"
                " display_name TEXT,"
                " created_at TEXT NOT NULL,"
                " deleted_at TEXT)"
            )
        # Active sessions. token_hash = SHA-256 of the opaque cookie
        # value; the plaintext exists only in the user's cookie and
        # in transit, never at rest. Server-side row lets us revoke
        # on logout or account deletion.
        cur.execute(
            "CREATE TABLE IF NOT EXISTS sessions ("
            " token_hash TEXT PRIMARY KEY,"
            " user_id BIGINT NOT NULL,"
            " created_at TEXT NOT NULL,"
            " last_seen_at TEXT NOT NULL,"
            " user_agent TEXT,"
            " ip TEXT)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_user "
            "ON sessions(user_id)"
        )
        # Outstanding magic links. Same hash-at-rest pattern: only the
        # email recipient ever has the plaintext token. Single-use:
        # consumed (`used_at` set) on first redemption, time-bounded by
        # `expires_at`.
        cur.execute(
            "CREATE TABLE IF NOT EXISTS magic_links ("
            " token_hash TEXT PRIMARY KEY,"
            " email TEXT NOT NULL,"
            " created_at TEXT NOT NULL,"
            " expires_at TEXT NOT NULL,"
            " used_at TEXT)"
        )
        # Catch log (Phase 2). Private by default; the visibility /
        # share_geom / share_token columns are inert until Phase 3
        # turns on sharing. `env` is an immutable JSON snapshot of the
        # auto-captured conditions at log time (flow, water/air temp,
        # pressure, moon, hatches).
        if _IS_PG:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS catches ("
                " id BIGSERIAL PRIMARY KEY,"
                " user_id BIGINT NOT NULL,"
                " created_at TEXT NOT NULL,"
                " occurred_at TEXT NOT NULL,"
                " river_name TEXT,"
                " river_site_no TEXT,"
                " lat DOUBLE PRECISION,"
                " lon DOUBLE PRECISION,"
                " species TEXT,"
                " length_in REAL,"
                " fly_used TEXT,"
                " notes TEXT,"
                " visibility TEXT NOT NULL DEFAULT 'private',"
                " share_geom TEXT,"
                " share_token TEXT,"
                " env TEXT)"
            )
        else:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS catches ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " user_id INTEGER NOT NULL,"
                " created_at TEXT NOT NULL,"
                " occurred_at TEXT NOT NULL,"
                " river_name TEXT,"
                " river_site_no TEXT,"
                " lat REAL,"
                " lon REAL,"
                " species TEXT,"
                " length_in REAL,"
                " fly_used TEXT,"
                " notes TEXT,"
                " visibility TEXT NOT NULL DEFAULT 'private',"
                " share_geom TEXT,"
                " share_token TEXT,"
                " env TEXT)"
            )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_catches_user_time "
            "ON catches(user_id, occurred_at)"
        )

        # Favorite waters (M4.1). One row per (user, gauged river);
        # `last_overall` is the river's verdict at the last precompute
        # pass -- the alert check compares fresh verdicts against it and
        # emails on meaningful transitions. `notify` is the per-favorite
        # alert opt-out.
        if _IS_PG:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS favorites ("
                " user_id BIGINT NOT NULL,"
                " site_no TEXT NOT NULL,"
                " name TEXT NOT NULL,"
                " state TEXT NOT NULL,"
                " lat DOUBLE PRECISION,"
                " lon DOUBLE PRECISION,"
                " notify INTEGER NOT NULL DEFAULT 1,"
                " last_overall TEXT,"
                " created_at TEXT NOT NULL,"
                " PRIMARY KEY (user_id, site_no))"
            )
        else:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS favorites ("
                " user_id INTEGER NOT NULL,"
                " site_no TEXT NOT NULL,"
                " name TEXT NOT NULL,"
                " state TEXT NOT NULL,"
                " lat REAL,"
                " lon REAL,"
                " notify INTEGER NOT NULL DEFAULT 1,"
                " last_overall TEXT,"
                " created_at TEXT NOT NULL,"
                " PRIMARY KEY (user_id, site_no))"
            )
        # The alert check scans per state on every precompute pass.
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_favorites_state "
            "ON favorites(state)"
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


# Order MUST match data/nhdplus/vaa.csv.gz's header (build_nhdplus_vaa.py
# OUT_COLUMNS) -- the Postgres COPY maps columns positionally, not by name.
_VAA_COLS = ("comid", "hydroseq", "levelpathid", "streamlevel",
             "gnis_name", "lengthkm", "maxelevsmo", "minelevsmo")


def get_vaa(comid: int) -> dict | None:
    """NHDPlusV2 attributes for a single COMID, or None."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph(f"SELECT {','.join(_VAA_COLS)} FROM nhdplus_vaa "
                "WHERE comid = ?"),
            (int(comid),))
        row = cur.fetchone()
    if not row:
        return None
    return {k: row[k] for k in _VAA_COLS}


_VAA_PROFILE_COLS = ("comid", "hydroseq", "gnis_name", "lengthkm",
                     "maxelevsmo", "minelevsmo")


def vaa_levelpath_reaches(levelpathid: int) -> list[dict]:
    """Every reach on a levelpath, ordered upstream -> downstream
    (NHDPlus hydroseq DESCending = headwaters first), with the fields the
    stream elevation/gradient profile needs. Empty list if none / not
    loaded."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph(f"SELECT {','.join(_VAA_PROFILE_COLS)} FROM nhdplus_vaa "
                "WHERE levelpathid = ? ORDER BY hydroseq DESC"),
            (int(levelpathid),))
        rows = cur.fetchall()
    return [{k: r[k] for k in _VAA_PROFILE_COLS} for r in rows]


def vaa_loaded() -> bool:
    """True iff `nhdplus_vaa` has at least one row. Used by the startup
    loader to short-circuit on warm boots."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM nhdplus_vaa LIMIT 1")
        return cur.fetchone() is not None


def vaa_has_elevation() -> bool:
    """True iff the table holds at least one row with a non-NULL
    `maxelevsmo`. Distinguishes a table loaded from the original
    6-column (elevation-less) CSV from one loaded from the national
    8-column file that backs the elevation profile."""
    with _conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT 1 FROM nhdplus_vaa "
                "WHERE maxelevsmo IS NOT NULL LIMIT 1")
        except Exception:
            return False
        return cur.fetchone() is not None


def _csv_has_elevation(csv_gz_path: str) -> bool:
    """True iff the gzipped CSV's header declares the elevation columns."""
    import csv
    import gzip
    try:
        with gzip.open(csv_gz_path, "rt") as f:
            return "maxelevsmo" in next(csv.reader(f))
    except Exception:
        return False


def _truncate_vaa() -> None:
    """Empty `nhdplus_vaa`. DELETE (not TRUNCATE) for SQLite portability;
    only ever called for the one-time elevation reload below."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM nhdplus_vaa")
        conn.commit()


def bulk_load_vaa(csv_gz_path: str) -> int:
    """Ingest NHDPlusV2 VAA rows from the bundled gzipped CSV. Skips
    silently if already loaded. Postgres uses COPY (~5s for 300K rows);
    SQLite falls back to batched executemany. Returns rows inserted.

    Self-upgrading reload: a warm table that predates the elevation
    columns is wiped and reloaded when the incoming CSV carries elevation
    (the national rollout). This lets a fresh `DATA_BASE_URL` cut over
    with just a redeploy -- no manual `TRUNCATE nhdplus_vaa`. Once the
    table holds elevation, subsequent boots short-circuit as before.
    """
    import csv
    import gzip

    if not os.path.exists(csv_gz_path):
        return 0
    reload = False
    if vaa_loaded():
        # Reload ONLY to upgrade a pre-elevation table to an
        # elevation-bearing CSV. Any other warm boot short-circuits.
        if vaa_has_elevation() or not _csv_has_elevation(csv_gz_path):
            return 0
        reload = True
    if _IS_PG:
        # Truncate + COPY happen in ONE transaction (see _bulk_load_vaa_pg)
        # so a failed national load rolls back to the existing rows instead
        # of leaving the table empty.
        return _bulk_load_vaa_pg(csv_gz_path, truncate_first=reload)
    if reload:
        _truncate_vaa()
    return _bulk_load_vaa_sqlite(csv_gz_path)


def _bulk_load_vaa_pg(csv_gz_path: str, truncate_first: bool = False) -> int:
    import csv
    import gzip
    # COPY maps columns POSITIONALLY to the listed column names, so the
    # column list must match the CSV's header order. Read the header to
    # tolerate an older CSV that predates the elevation columns (the
    # bundled dev file) as well as the national 8-column file.
    with gzip.open(csv_gz_path, "rt") as hf:
        header = next(csv.reader(hf))
    known = set(_VAA_COLS)
    cols = [c for c in header if c in known]
    with _conn() as conn, conn.cursor() as cur, \
            gzip.open(csv_gz_path, "rt") as f:
        # The national table is ~2.7M rows; that single COPY runs well past
        # the server's default statement_timeout (~15-30s on Render's free
        # tier), which cancels it mid-load and -- because the truncate
        # below shares this transaction -- would otherwise leave the table
        # empty. Lift the cap for THIS transaction only (SET LOCAL reverts
        # at COMMIT); bounded, not disabled, so a wedged load can't hang
        # boot forever.
        cur.execute("SET LOCAL statement_timeout = '600s'")
        if truncate_first:
            # Same transaction as the COPY: if the load fails, the wipe
            # rolls back with it, so the table is never left empty.
            cur.execute("TRUNCATE nhdplus_vaa")
        # psycopg COPY: feed CSV directly, no row-by-row roundtrip
        with cur.copy(
                f"COPY nhdplus_vaa ({','.join(cols)}) "
                "FROM STDIN WITH (FORMAT CSV, HEADER TRUE, NULL '')") as copy:
            for chunk in iter(lambda: f.read(65536), ""):
                copy.write(chunk)
        cur.execute("SELECT COUNT(*) AS n FROM nhdplus_vaa")
        n = cur.fetchone()["n"]
        conn.commit()
        return int(n)


def _bulk_load_vaa_sqlite(csv_gz_path: str) -> int:
    import csv
    import gzip
    with _conn() as conn, gzip.open(csv_gz_path, "rt") as f:
        reader = csv.DictReader(f)
        batch: list[tuple] = []
        total = 0

        def _flush():
            nonlocal total
            if not batch:
                return
            conn.executemany(
                f"INSERT OR IGNORE INTO nhdplus_vaa "
                f"({','.join(_VAA_COLS)}) "
                f"VALUES ({','.join('?' * len(_VAA_COLS))})",
                batch)
            total += len(batch)
            batch.clear()

        def _i(v):
            return int(v) if v not in (None, "") else None

        for row in reader:
            batch.append((
                int(row["comid"]),
                _i(row.get("hydroseq")),
                _i(row.get("levelpathid")),
                _i(row.get("streamlevel")),
                row.get("gnis_name") or None,
                float(row["lengthkm"]) if row.get("lengthkm") else None,
                # Tolerate an old CSV that predates the elevation columns.
                _i(row.get("maxelevsmo")),
                _i(row.get("minelevsmo")),
            ))
            if len(batch) >= 5000:
                _flush()
        _flush()
        conn.commit()
        return total


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


# -- Accounts (Phase 1) -------------------------------------------------

# Magic-link tokens live ~15 min; older rows are stale and get rejected.
MAGIC_LINK_TTL_MINUTES = 15


def upsert_user_by_email(email: str) -> dict:
    """Find an existing live (non-deleted) user by email, or create one.
    Display name defaults to the email's local-part on first creation
    (settable later in /api/me)."""
    email = email.strip().lower()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("SELECT id, email, display_name, created_at"
                " FROM users WHERE email = ? AND deleted_at IS NULL"),
            (email,))
        row = cur.fetchone()
        if row:
            return dict(row)
        default_name = email.split("@", 1)[0]
        if _IS_PG:
            cur.execute(
                _ph("INSERT INTO users (email, display_name, created_at)"
                    " VALUES (?, ?, ?) RETURNING id"),
                (email, default_name, now))
            uid = cur.fetchone()["id"]
        else:
            cur.execute(
                _ph("INSERT INTO users (email, display_name, created_at)"
                    " VALUES (?, ?, ?)"),
                (email, default_name, now))
            uid = cur.lastrowid
        conn.commit()
        return {"id": uid, "email": email,
                "display_name": default_name, "created_at": now}


def get_user(user_id: int) -> dict | None:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("SELECT id, email, display_name, created_at"
                " FROM users WHERE id = ? AND deleted_at IS NULL"),
            (user_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def update_user_display_name(user_id: int, name: str) -> None:
    name = (name or "").strip()
    if not name:
        return
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("UPDATE users SET display_name = ?"
                " WHERE id = ? AND deleted_at IS NULL"),
            (name, user_id))
        conn.commit()


def soft_delete_user(user_id: int) -> None:
    """Mark account deleted; scrub PII; revoke all sessions. Row stays
    for foreign-key sanity on legacy pins/catches owned by this user."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("UPDATE users SET email = ?, display_name = NULL,"
                " deleted_at = ? WHERE id = ?"),
            (f"deleted-{user_id}-{now}", now, user_id))
        cur.execute(
            _ph("DELETE FROM sessions WHERE user_id = ?"), (user_id,))
        conn.commit()


# Magic links --------------------------------------------------------

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_magic_link(email: str, token: str) -> None:
    """Persist the SHA-256 of the link token. Plaintext is emailed and
    never stored. Idempotent by token_hash collision (effectively never)."""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=MAGIC_LINK_TTL_MINUTES)
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("INSERT INTO magic_links (token_hash, email, created_at,"
                " expires_at) VALUES (?, ?, ?, ?)"),
            (_hash(token), email.strip().lower(),
             now.isoformat(), expires.isoformat()))
        conn.commit()


def consume_magic_link(token: str) -> str | None:
    """Single-use redemption. Returns the email on success, None on
    expired/used/unknown. Marks the row used atomically."""
    now = datetime.now(timezone.utc)
    th = _hash(token)
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("SELECT email, expires_at, used_at FROM magic_links"
                " WHERE token_hash = ?"),
            (th,))
        row = cur.fetchone()
        if not row:
            return None
        if row["used_at"]:
            return None
        try:
            if datetime.fromisoformat(row["expires_at"]) < now:
                return None
        except (TypeError, ValueError):
            return None
        cur.execute(
            _ph("UPDATE magic_links SET used_at = ?"
                " WHERE token_hash = ? AND used_at IS NULL"),
            (now.isoformat(), th))
        # If another worker raced us, rowcount will be 0; treat as miss.
        if cur.rowcount == 0:
            return None
        conn.commit()
        return row["email"]


# Sessions -----------------------------------------------------------

def create_session(user_id: int, token: str,
                   user_agent: str | None, ip: str | None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("INSERT INTO sessions (token_hash, user_id, created_at,"
                " last_seen_at, user_agent, ip)"
                " VALUES (?, ?, ?, ?, ?, ?)"),
            (_hash(token), user_id, now, now, user_agent, ip))
        conn.commit()


def user_from_session(token: str) -> dict | None:
    """Validate a session cookie's token and return the owning user.
    Touches `last_seen_at` opportunistically (best-effort, ignore
    update failures so reads stay fast)."""
    if not token:
        return None
    th = _hash(token)
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("SELECT u.id, u.email, u.display_name, u.created_at"
                " FROM sessions s JOIN users u ON u.id = s.user_id"
                " WHERE s.token_hash = ? AND u.deleted_at IS NULL"),
            (th,))
        row = cur.fetchone()
        if not row:
            return None
        try:
            cur.execute(
                _ph("UPDATE sessions SET last_seen_at = ?"
                    " WHERE token_hash = ?"),
                (datetime.now(timezone.utc).isoformat(), th))
            conn.commit()
        except Exception:
            pass
    return dict(row)


def delete_session(token: str) -> None:
    if not token:
        return
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("DELETE FROM sessions WHERE token_hash = ?"),
            (_hash(token),))
        conn.commit()


# Pin claim flow -----------------------------------------------------

def list_pins_for_device_token(device_owner: str) -> list[dict]:
    """Pins anonymously owned by a specific device-token hash.
    Used to populate the post-sign-in 'claim your pins' prompt."""
    return list_pins(device_owner)


def claim_pins(device_owner: str, user_owner: str) -> int:
    """Relink anonymous device-token-owned pins to the user-namespaced
    owner. Returns the number of rows relinked."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("UPDATE pins SET owner_token = ? WHERE owner_token = ?"),
            (user_owner, device_owner))
        n = cur.rowcount or 0
        conn.commit()
    return int(n)


# -- Catch log (Phase 2) ----------------------------------------------

_CATCH_COLS = ("id", "user_id", "created_at", "occurred_at", "river_name",
               "river_site_no", "lat", "lon", "species", "length_in",
               "fly_used", "notes", "visibility", "share_geom",
               "share_token", "env")

# User-editable fields (everything except identity, timestamps that are
# set server-side, and the immutable env snapshot).
_CATCH_EDITABLE = ("occurred_at", "river_name", "river_site_no", "lat",
                   "lon", "species", "length_in", "fly_used", "notes")


def _row_to_catch(row) -> dict:
    out = {k: row[k] for k in _CATCH_COLS}
    if out.get("env"):
        try:
            out["env"] = json.loads(out["env"])
        except (ValueError, TypeError):
            out["env"] = None
    return out


def add_catch(user_id: int, data: dict, env: dict | None) -> dict:
    """Insert a catch for a user. `data` carries the user-supplied
    fields; `env` is the server-built conditions snapshot."""
    now = datetime.now(timezone.utc).isoformat()
    cols = ["user_id", "created_at", "occurred_at", "river_name",
            "river_site_no", "lat", "lon", "species", "length_in",
            "fly_used", "notes", "visibility", "env"]
    vals = [
        user_id, now,
        data.get("occurred_at") or now,
        data.get("river_name"), data.get("river_site_no"),
        data.get("lat"), data.get("lon"),
        data.get("species"), data.get("length_in"),
        data.get("fly_used"), data.get("notes"),
        data.get("visibility") or "private",
        json.dumps(env) if env is not None else None,
    ]
    placeholders = ",".join("?" for _ in cols)
    with _conn() as conn:
        cur = conn.cursor()
        if _IS_PG:
            cur.execute(
                _ph(f"INSERT INTO catches ({','.join(cols)}) "
                    f"VALUES ({placeholders}) RETURNING id"),
                tuple(vals))
            cid = cur.fetchone()["id"]
        else:
            cur.execute(
                _ph(f"INSERT INTO catches ({','.join(cols)}) "
                    f"VALUES ({placeholders})"),
                tuple(vals))
            cid = cur.lastrowid
        conn.commit()
    return get_catch(cid)


def get_catch(catch_id: int) -> dict | None:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph(f"SELECT {','.join(_CATCH_COLS)} FROM catches WHERE id = ?"),
            (catch_id,))
        row = cur.fetchone()
    return _row_to_catch(row) if row else None


def list_catches(user_id: int, *, species: str | None = None,
                 date_from: str | None = None, date_to: str | None = None,
                 limit: int = 200) -> list[dict]:
    """A user's catches, newest first, with optional filters."""
    where = ["user_id = ?"]
    params: list = [user_id]
    if species:
        where.append("species = ?")
        params.append(species)
    if date_from:
        where.append("occurred_at >= ?")
        params.append(date_from)
    if date_to:
        where.append("occurred_at <= ?")
        params.append(date_to)
    params.append(int(limit))
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph(f"SELECT {','.join(_CATCH_COLS)} FROM catches "
                f"WHERE {' AND '.join(where)} "
                "ORDER BY occurred_at DESC, id DESC LIMIT ?"),
            tuple(params))
        rows = cur.fetchall()
    return [_row_to_catch(r) for r in rows]


def update_catch(catch_id: int, user_id: int, data: dict) -> dict | None:
    """Update user-editable fields on a catch the user owns. The `env`
    snapshot is immutable and never touched here. Returns the updated
    catch, or None if it doesn't exist / isn't theirs."""
    fields = [k for k in _CATCH_EDITABLE if k in data]
    if not fields:
        return get_catch(catch_id)
    sets = ", ".join(f"{f} = ?" for f in fields)
    params = [data[f] for f in fields] + [catch_id, user_id]
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph(f"UPDATE catches SET {sets} "
                "WHERE id = ? AND user_id = ?"),
            tuple(params))
        changed = cur.rowcount
        conn.commit()
    if not changed:
        return None
    return get_catch(catch_id)


def delete_catch(catch_id: int, user_id: int) -> bool:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("DELETE FROM catches WHERE id = ? AND user_id = ?"),
            (catch_id, user_id))
        deleted = cur.rowcount
        conn.commit()
    return bool(deleted)


def count_catches(user_id: int) -> int:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("SELECT COUNT(*) AS n FROM catches WHERE user_id = ?"),
            (user_id,))
        return int(cur.fetchone()["n"])


# -- Favorites (M4.1) --------------------------------------------------


def list_favorites(user_id: int) -> list[dict]:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("SELECT site_no, name, state, lat, lon, notify,"
                " last_overall, created_at FROM favorites"
                " WHERE user_id = ? ORDER BY created_at DESC"),
            (user_id,))
        return [
            {"site_no": r["site_no"], "name": r["name"], "state": r["state"],
             "lat": r["lat"], "lon": r["lon"], "notify": bool(r["notify"]),
             "last_overall": r["last_overall"], "created_at": r["created_at"]}
            for r in cur.fetchall()
        ]


def add_favorite(user_id: int, site_no: str, name: str, state: str,
                 lat: float | None, lon: float | None) -> dict:
    """Upsert (re-favoriting refreshes name/coords, keeps notify +
    last_overall so re-adding doesn't re-trigger an alert)."""
    created_at = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("INSERT INTO favorites (user_id, site_no, name, state,"
                " lat, lon, notify, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 1, ?)"
                " ON CONFLICT (user_id, site_no) DO UPDATE SET"
                " name = excluded.name, state = excluded.state,"
                " lat = excluded.lat, lon = excluded.lon"),
            (user_id, site_no, name, state, lat, lon, created_at))
    return {"site_no": site_no, "name": name, "state": state,
            "lat": lat, "lon": lon, "notify": True,
            "last_overall": None, "created_at": created_at}


def remove_favorite(user_id: int, site_no: str) -> bool:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("DELETE FROM favorites WHERE user_id = ? AND site_no = ?"),
            (user_id, site_no))
        return cur.rowcount > 0


def set_favorite_notify(user_id: int, site_no: str, on: bool) -> bool:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("UPDATE favorites SET notify = ?"
                " WHERE user_id = ? AND site_no = ?"),
            (1 if on else 0, user_id, site_no))
        return cur.rowcount > 0


def favorites_for_state(state: str) -> list[dict]:
    """Every favorite in a state, with the owner's email -- the alert
    check's working set (one indexed scan per precompute pass)."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("SELECT f.user_id, f.site_no, f.name, f.notify,"
                " f.last_overall, u.email FROM favorites f"
                " JOIN users u ON u.id = f.user_id"
                " WHERE f.state = ? AND u.deleted_at IS NULL"),
            (state,))
        return [
            {"user_id": r["user_id"], "site_no": r["site_no"],
             "name": r["name"], "notify": bool(r["notify"]),
             "last_overall": r["last_overall"], "email": r["email"]}
            for r in cur.fetchall()
        ]


def set_favorite_verdict(user_id: int, site_no: str, overall: str) -> None:
    with _conn() as conn:
        conn.cursor().execute(
            _ph("UPDATE favorites SET last_overall = ?"
                " WHERE user_id = ? AND site_no = ?"),
            (overall, user_id, site_no))
