#!/usr/bin/env python3
"""
One-shot migration: copy user content from OLD_DATABASE_URL into
NEW_DATABASE_URL. Used to move Blueliner off Render's free Postgres
(which expires at ~90 days) onto Neon free tier (persistent).

Copies:
  - `pins`        the only irreplaceable data (saved per-device map pins).
                  `id` is omitted so BIGSERIAL on the new DB assigns fresh
                  ids; clients refetch /api/pins on every load and never
                  persist ids, so renumbering is invisible.

`river_snapshot`, `river_stats`, and `gauge_meta` are deliberately NOT
copied -- the refresher regenerates them within a cycle or two.

Usage:
    OLD_DATABASE_URL=postgresql://... \\
    NEW_DATABASE_URL=postgresql://...?sslmode=require \\
    python scripts/migrate_pins.py

The script is idempotent: re-running inserts only new pins (id is fresh
each insert, but pins migrated twice would duplicate -- run once near
the cutover, not on a loop).
"""

import os
import sys

OLD = os.environ.get("OLD_DATABASE_URL", "").strip()
NEW = os.environ.get("NEW_DATABASE_URL", "").strip()
if not OLD or not NEW:
    sys.stderr.write("set OLD_DATABASE_URL and NEW_DATABASE_URL\n")
    sys.exit(2)
if OLD == NEW:
    sys.stderr.write("OLD_DATABASE_URL == NEW_DATABASE_URL; aborting\n")
    sys.exit(2)

# db.py reads DATABASE_URL at import time and selects the backend from
# it. Point it at NEW so init_db() creates the schema there.
os.environ["DATABASE_URL"] = NEW
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
import db  # noqa: E402

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402


def _count(conn, table: str) -> int:
    with conn.cursor() as c:
        c.execute(f"SELECT COUNT(*) AS n FROM {table}")
        return c.fetchone()["n"]


print("[migrate] initializing schema on NEW...")
db.init_db()

with psycopg.connect(OLD, row_factory=dict_row) as src, \
     psycopg.connect(NEW, row_factory=dict_row) as dst:

    with src.cursor() as cur:
        cur.execute(
            "SELECT lat, lon, note, created_at, owner_token FROM pins "
            "ORDER BY id"
        )
        pins = cur.fetchall()
    if pins:
        with dst.cursor() as cur:
            cur.executemany(
                "INSERT INTO pins (lat, lon, note, created_at, owner_token) "
                "VALUES (%(lat)s, %(lon)s, %(note)s, %(created_at)s, "
                "%(owner_token)s)",
                pins,
            )
        dst.commit()
    print(f"[migrate] pins:       attempted={len(pins):>5}  "
          f"new total={_count(dst, 'pins'):>5}")

print("[migrate] done.")
