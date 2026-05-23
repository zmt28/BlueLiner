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
        # Per-COMID NHD attributes (just gnis_name today). Lets us trim
        # an NLDI flowline walk to only the segments that share the
        # gauge's GNIS name, so a tributary gauge's downstream walk no
        # longer continues past the confluence onto the main stem.
        cur.execute(
            "CREATE TABLE IF NOT EXISTS comid_meta ("
            " comid TEXT PRIMARY KEY,"
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
            " lengthkm REAL)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_vaa_levelpath "
            "ON nhdplus_vaa(levelpathid)"
        )
        # Clickable-stream network (the "bluelining" layer). One row per
        # NHDPlus flowline that's fishing-relevant (StreamOrder >= 3, a
        # state-designated trout water, an order-3 tributary of trout
        # water, or a named order-5+ river). Bulk-loaded once from
        # data/nhdplus/clickable_streams.geojson.gz. Served by viewport
        # bbox + zoom tier so the client never pulls all ~100K at once
        # (the 512MB free tier can't hold them, and the client can't
        # render them). geom is the GeoJSON geometry as a JSON string;
        # min/max lon/lat are the precomputed bounding box for the
        # overlap query.
        cur.execute(
            "CREATE TABLE IF NOT EXISTS clickable_streams ("
            " comid BIGINT PRIMARY KEY,"
            " levelpathid BIGINT,"
            " gnis_name TEXT,"
            " streamorder INTEGER,"
            " trout_class TEXT,"
            " min_lon REAL, min_lat REAL, max_lon REAL, max_lat REAL,"
            " geom TEXT NOT NULL)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_clk_order "
            "ON clickable_streams(streamorder)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_clk_lon "
            "ON clickable_streams(min_lon, max_lon)"
        )
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


def get_comid_meta(comid: str) -> dict | None:
    """gnis_name (and friends) for an NHD COMID, or None."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("SELECT payload FROM comid_meta WHERE comid = ?"), (comid,))
        row = cur.fetchone()
    if not row:
        return None
    try:
        return json.loads(row["payload"])
    except (ValueError, TypeError):
        return None


def put_comid_meta(comid: str, meta: dict) -> None:
    _upsert("comid_meta", comid, "payload", json.dumps(meta),
            key_col="comid")


_VAA_COLS = ("comid", "hydroseq", "levelpathid", "streamlevel",
             "gnis_name", "lengthkm")


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


def get_vaas(comids: list[int]) -> dict[int, dict]:
    """Batched NHDPlusV2 attribute lookup -- one query for many COMIDs.
    Used by the per-flowline LevelPathID filter in _nldi_flowline."""
    if not comids:
        return {}
    ints = [int(c) for c in comids if c]
    placeholders = ",".join("?" for _ in ints)
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph(f"SELECT {','.join(_VAA_COLS)} FROM nhdplus_vaa "
                f"WHERE comid IN ({placeholders})"),
            tuple(ints))
        rows = cur.fetchall()
    return {r["comid"]: {k: r[k] for k in _VAA_COLS} for r in rows}


def vaa_loaded() -> bool:
    """True iff `nhdplus_vaa` has at least one row. Used by the startup
    loader to short-circuit on warm boots."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM nhdplus_vaa LIMIT 1")
        return cur.fetchone() is not None


def bulk_load_vaa(csv_gz_path: str) -> int:
    """Ingest NHDPlusV2 VAA rows from the bundled gzipped CSV. Skips
    silently if already loaded. Postgres uses COPY (~5s for 300K rows);
    SQLite falls back to batched executemany. Returns rows inserted."""
    import csv
    import gzip

    if vaa_loaded():
        return 0
    if not os.path.exists(csv_gz_path):
        return 0
    if _IS_PG:
        return _bulk_load_vaa_pg(csv_gz_path)
    return _bulk_load_vaa_sqlite(csv_gz_path)


def _bulk_load_vaa_pg(csv_gz_path: str) -> int:
    import csv
    import gzip
    with _conn() as conn, conn.cursor() as cur, \
            gzip.open(csv_gz_path, "rt") as f:
        # psycopg COPY: feed CSV directly, no row-by-row roundtrip
        with cur.copy(
                f"COPY nhdplus_vaa ({','.join(_VAA_COLS)}) "
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
                f"({','.join(_VAA_COLS)}) VALUES (?,?,?,?,?,?)",
                batch)
            total += len(batch)
            batch.clear()

        for row in reader:
            batch.append((
                int(row["comid"]),
                int(row["hydroseq"]) if row["hydroseq"] else None,
                int(row["levelpathid"]) if row["levelpathid"] else None,
                int(row["streamlevel"]) if row["streamlevel"] else None,
                row["gnis_name"] or None,
                float(row["lengthkm"]) if row["lengthkm"] else None,
            ))
            if len(batch) >= 5000:
                _flush()
        _flush()
        conn.commit()
        return total


# -- Clickable-stream network (Phase B) -------------------------------

_CLK_COLS = ("comid", "levelpathid", "gnis_name", "streamorder",
             "trout_class", "min_lon", "min_lat", "max_lon", "max_lat",
             "geom")


def clickable_loaded() -> bool:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM clickable_streams LIMIT 1")
        return cur.fetchone() is not None


def _feature_bbox(coords, gtype) -> tuple:
    pts = coords if gtype == "LineString" else [p for part in coords for p in part]
    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    return min(lons), min(lats), max(lons), max(lats)


# Drop the [x, y, z] Z dimension (NHD geometry is 3D but we only need 2D
# centerlines on the map) and round to 5 decimals (~1m precision -- well
# below stream-width resolution). Cuts the served GeoJSON payload by
# roughly 60% raw / 30% gzipped per /api/clickable_streams response,
# which is the biggest egress lever in the app.
def _trim_geom(geom: dict) -> dict:
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return geom
    if gtype == "LineString":
        geom["coordinates"] = [[round(c[0], 5), round(c[1], 5)] for c in coords]
    elif gtype == "MultiLineString":
        geom["coordinates"] = [
            [[round(c[0], 5), round(c[1], 5)] for c in line] for line in coords]
    return geom


def bulk_load_clickable_streams(geojson_gz_path: str) -> int:
    """Ingest the clickable-stream network from the bundled gzipped
    GeoJSON. Idempotent (skips when populated). **Streams** the
    FeatureCollection with ijson so the ~26 MB file is never fully held
    in memory -- a json.load() here spiked ~285 MB and OOM-killed the
    512 MB free-tier worker. Returns rows inserted."""
    import gzip
    import json

    import ijson

    if clickable_loaded():
        return 0
    if not os.path.exists(geojson_gz_path):
        return 0

    placeholders = ",".join("?" for _ in _CLK_COLS)
    insert = (f"INSERT OR IGNORE INTO clickable_streams "
              f"({','.join(_CLK_COLS)}) VALUES ({placeholders})") if not _IS_PG \
        else (f"INSERT INTO clickable_streams ({','.join(_CLK_COLS)}) "
              f"VALUES ({placeholders}) ON CONFLICT (comid) DO NOTHING")
    insert = _ph(insert)

    batch: list[tuple] = []
    total = 0
    with _conn() as conn:
        cur = conn.cursor()

        def _flush():
            nonlocal total
            if not batch:
                return
            cur.executemany(insert, batch)
            total += len(batch)
            batch.clear()

        with gzip.open(geojson_gz_path, "rb") as f:
            # ijson yields each feature as it's parsed (numbers as Decimal);
            # only the current feature + batch live in memory at once.
            for feat in ijson.items(f, "features.item"):
                geom = feat.get("geometry") or {}
                coords = geom.get("coordinates")
                gtype = geom.get("type")
                if not coords or gtype not in ("LineString", "MultiLineString"):
                    continue
                p = feat.get("properties", {})
                comid = p.get("comid")
                if comid is None:
                    continue
                try:
                    w, s, e, n = _feature_bbox(coords, gtype)
                except (ValueError, TypeError, IndexError):
                    continue
                batch.append((
                    int(comid),
                    int(p["levelpathid"]) if p.get("levelpathid") is not None else None,
                    str(p["gnis_name"]) if p.get("gnis_name") else None,
                    int(p["streamorder"]) if p.get("streamorder") is not None else None,
                    str(p["trout_class"]) if p.get("trout_class") else None,
                    float(w), float(s), float(e), float(n),
                    # Trim before persisting so fresh loads bake the savings into
                    # the DB; query_clickable_streams also trims at read to cover
                    # already-loaded rows. default=float coerces ijson Decimals.
                    json.dumps(_trim_geom(geom), separators=(",", ":"),
                               default=float),
                ))
                if len(batch) >= 2000:
                    _flush()
        _flush()
        conn.commit()
    return total


def query_clickable_streams(west: float, south: float, east: float,
                            north: float, min_order: int = 1,
                            limit: int = 4000) -> list[dict]:
    """Clickable streams whose bounding box overlaps the viewport and
    whose StreamOrder >= min_order (the zoom tier). Capped for safety."""
    import json
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _ph("SELECT comid, levelpathid, gnis_name, streamorder,"
                " trout_class, geom FROM clickable_streams"
                " WHERE streamorder >= ?"
                " AND max_lon >= ? AND min_lon <= ?"
                " AND max_lat >= ? AND min_lat <= ?"
                " ORDER BY streamorder DESC LIMIT ?"),
            (int(min_order), west, east, south, north, int(limit)))
        rows = cur.fetchall()
    out = []
    for r in rows:
        try:
            geom = json.loads(r["geom"])
        except (ValueError, TypeError):
            continue
        out.append({
            "comid": r["comid"], "levelpathid": r["levelpathid"],
            "gnis_name": r["gnis_name"], "streamorder": r["streamorder"],
            "trout_class": r["trout_class"],
            "geometry": _trim_geom(geom),    # idempotent on already-trimmed rows
        })
    return out


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
