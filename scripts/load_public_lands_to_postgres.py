#!/usr/bin/env python3
"""
One-shot loader: stream the PAD-US public_lands geojson.gz into a
remote Postgres from your laptop, bypassing the prod web service.

Why this exists: the lifespan-hook loader in main.py can't complete
on Render's free tier -- the worker is killed (healthcheck timeout
or OOM) before the 290K-row insert commits, the all-or-nothing
transaction rolls back, and the table stays empty no matter how many
redeploys.

Running the load from outside the web service sidesteps every one of
those constraints: no healthcheck, no SIGTERM, no resource cap.

This script *does not* reuse db.bulk_load_public_lands directly,
because that function commits exactly once at the very end -- which
is the right semantic for a worker (all-or-nothing avoids partial
loads being mistaken for complete) but fragile over a long
residential connection. Instead, we inline the load with per-batch
commits: each 2000-row batch is its own short transaction. If the
SSL connection drops mid-load (residential NAT timeout, transient
Render hiccup, anything), only the in-flight batch is lost and
previous batches stay durable. You can re-run the script and it'll
truncate + reload from the top.

Usage:
    # Get the External Connection string from Render: blueliner-db
    # -> Connect -> External Connection. Looks like:
    # postgresql://USER:PASS@dpg-xxxxxxxxxxxx.oregon-postgres.render.com/DBNAME

    export DATABASE_URL='postgresql://...'
    python scripts/load_public_lands_to_postgres.py

    # Optional: custom path to the gzipped geojson
    python scripts/load_public_lands_to_postgres.py \\
        --geojson /path/to/public_lands.geojson.gz

    # Skip the TRUNCATE confirmation prompt
    python scripts/load_public_lands_to_postgres.py --yes

Takes ~5-15 minutes over residential upload. Safe to interrupt + re-
run: the script TRUNCATEs any partial state before starting.
"""

import argparse
import gzip
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_GEOJSON = os.path.join(
    ROOT, "data", "public_lands", "public_lands.geojson.gz")
BATCH_SIZE = 2000


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--geojson", default=DEFAULT_GEOJSON,
                   help=f"Path to gzipped geojson "
                        f"(default: {DEFAULT_GEOJSON})")
    p.add_argument("--yes", action="store_true",
                   help="Skip the TRUNCATE confirmation prompt.")
    args = p.parse_args()

    if not os.environ.get("DATABASE_URL", "").startswith(
            ("postgres://", "postgresql://")):
        print("ERROR: DATABASE_URL must be set to a Postgres connection "
              "string before running this.", file=sys.stderr)
        print("  Render dashboard -> blueliner-db -> Connect -> External "
              "Connection -> copy URL", file=sys.stderr)
        print("  export DATABASE_URL='postgresql://...'", file=sys.stderr)
        return 1

    if not os.path.exists(args.geojson):
        print(f"ERROR: geojson not found at {args.geojson}", file=sys.stderr)
        print(f"  Run scripts/build_public_lands.py first.", file=sys.stderr)
        return 1

    sys.path.insert(0, ROOT)
    import db
    import ijson

    if not db._IS_PG:
        print("ERROR: db module did not detect a Postgres URL.",
              file=sys.stderr)
        return 1

    size_mb = os.path.getsize(args.geojson) / 1e6
    target = os.environ["DATABASE_URL"].split("@")[-1]
    print(f"[load] source: {args.geojson} ({size_mb:.1f} MB gz)")
    print(f"[load] target: {target}")

    # Make sure the schema exists.
    print("[load] ensuring schema (init_db) ...")
    db.init_db()

    # Check current state + truncate if any rows present. The whole point of
    # this script is a clean one-shot reload; partial state from a previous
    # failed run would otherwise survive the idempotency check in
    # bulk_load_public_lands (which we're bypassing anyway).
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM public_lands")
        existing = cur.fetchone()
        existing_n = existing["count"] if isinstance(existing, dict) \
            else existing[0]
    if existing_n:
        print(f"[load] public_lands has {existing_n:,} existing rows.")
        if not args.yes:
            ans = input("  TRUNCATE and reload? [y/N] ").strip().lower()
            if ans != "y":
                print("[load] aborted.")
                return 1
        with db._conn() as conn:
            cur = conn.cursor()
            cur.execute("TRUNCATE public_lands")
            conn.commit()
        print(f"[load] truncated.")

    placeholders = ",".join("%s" for _ in db._PL_COLS)
    insert_sql = (f"INSERT INTO public_lands ({','.join(db._PL_COLS)}) "
                  f"VALUES ({placeholders})")

    batch: list[tuple] = []
    total = 0
    started = time.time()
    last_report = started

    print(f"[load] streaming features in batches of {BATCH_SIZE:,} "
          f"(commit per batch -- robust to connection drops) ...")

    # One long-lived connection, but with frequent commits so each
    # batch is durable on its own. If the SSL drops mid-load only the
    # in-flight batch is lost.
    with db._conn() as conn:
        cur = conn.cursor()
        with gzip.open(args.geojson, "rb") as f:
            for feat in ijson.items(f, "features.item"):
                geom = feat.get("geometry") or {}
                coords = geom.get("coordinates")
                gtype = geom.get("type")
                if not coords or gtype not in ("Polygon", "MultiPolygon"):
                    continue
                p_in = feat.get("properties", {})
                try:
                    w, s, e, n = db._polygon_bbox(coords, gtype)
                except (ValueError, TypeError, IndexError):
                    continue
                batch.append((
                    str(p_in["unit_name"]) if p_in.get("unit_name") else None,
                    str(p_in["manager_type"]) if p_in.get("manager_type") else None,
                    str(p_in["manager_name"]) if p_in.get("manager_name") else None,
                    str(p_in["designation"]) if p_in.get("designation") else None,
                    str(p_in["public_access"]) if p_in.get("public_access") else None,
                    str(p_in["state_nm"]) if p_in.get("state_nm") else None,
                    float(w), float(s), float(e), float(n),
                    json.dumps(geom, separators=(",", ":"), default=float),
                ))
                if len(batch) >= BATCH_SIZE:
                    cur.executemany(insert_sql, batch)
                    conn.commit()
                    total += len(batch)
                    batch.clear()
                    now = time.time()
                    if now - last_report >= 5:
                        rate = total / max(now - started, 0.001)
                        print(f"  {total:>7,} rows  "
                              f"({rate:.0f} rows/s, "
                              f"{(now - started)/60:.1f} min elapsed)")
                        last_report = now
        if batch:
            cur.executemany(insert_sql, batch)
            conn.commit()
            total += len(batch)
            batch.clear()

    elapsed = time.time() - started
    print(f"\n[done] inserted {total:,} rows in {elapsed/60:.1f} min "
          f"({total / max(elapsed, 0.001):.0f} rows/s avg)")
    print(f"[done] now run `VACUUM ANALYZE public_lands;` in TablePlus "
          f"so the planner picks up the GiST index immediately.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
