#!/usr/bin/env python3
"""
One-shot loader: stream the PAD-US public_lands geojson.gz into a
remote Postgres from your laptop, bypassing the prod web service.

Why this exists: the lifespan-hook loader in main.py works fine on a
paid Render plan but cannot complete on free tier. The free-tier
worker is killed (healthcheck timeout / resource limit) before the
~290K-row insert commits, the all-or-nothing transaction rolls back,
and the table stays permanently empty no matter how many redeploys.

Running the load from outside the web service sidesteps every one of
those constraints: no healthcheck, no SIGTERM, no resource cap. The
table ends up fully populated; on the next prod boot the lifespan's
idempotency check (`SELECT 1 FROM public_lands LIMIT 1`) sees rows
and cleanly skips.

Usage:
    # Get the External Connection string from Render: blueliner-db
    # -> Connect -> External Connection. Looks like:
    # postgresql://USER:PASS@dpg-xxxxxxxxxxxx.oregon-postgres.render.com/DBNAME

    export DATABASE_URL='postgresql://...'
    python scripts/load_public_lands_to_postgres.py

    # Optional: custom path to the gzipped geojson
    python scripts/load_public_lands_to_postgres.py \\
        --geojson /path/to/public_lands.geojson.gz

Defaults to data/public_lands/public_lands.geojson.gz under the repo
root -- the same file the build script writes.

Takes ~5-15 minutes over a residential connection (uploads ~290K
rows, each carrying a multi-KB JSON geometry blob, to the remote
Postgres). Safe to interrupt + re-run: the loader has an idempotency
check that skips when the table already has rows.
"""

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_GEOJSON = os.path.join(
    ROOT, "data", "public_lands", "public_lands.geojson.gz")


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--geojson", default=DEFAULT_GEOJSON,
                   help=f"Path to gzipped geojson "
                        f"(default: {DEFAULT_GEOJSON})")
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

    if not db._IS_PG:
        print("ERROR: db module did not detect a Postgres URL.",
              file=sys.stderr)
        return 1

    size_mb = os.path.getsize(args.geojson) / 1e6
    print(f"[load] source: {args.geojson} ({size_mb:.1f} MB gz)")
    print(f"[load] target: {os.environ['DATABASE_URL'].split('@')[-1]}")

    # Make sure the schema exists (matches what main.py's lifespan does).
    print("[load] ensuring schema (init_db) ...")
    db.init_db()

    if db.public_lands_loaded():
        print("[load] public_lands already has rows; nothing to do. "
              "Run `TRUNCATE public_lands;` first if you want to reload.")
        return 0

    print("[load] streaming features into Postgres "
          "(takes 5-15 min on residential upload) ...")
    started = time.time()
    n = db.bulk_load_public_lands(args.geojson)
    elapsed = time.time() - started
    print(f"[done] inserted {n:,} rows in {elapsed/60:.1f} min")
    print(f"[done] now run `VACUUM ANALYZE public_lands;` in TablePlus "
          f"so the planner picks up the new GiST index immediately.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
