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
commits + automatic resume on connection drop:

  - Each 2000-row batch is its own short transaction. Connection
    drop loses at most one batch.
  - On psycopg.OperationalError the script reconnects, re-counts
    rows in public_lands, and skips the first N valid features in
    the stream to pick up exactly where the previous attempt
    committed. Up to MAX_ATTEMPTS reconnects per run.
  - --resume re-uses the same logic for a deliberate re-run after
    interruption: keep whatever's in the table, continue loading.

Usage:
    # Get the External Connection string from Render: blueliner-db
    # -> Connect -> External Connection. Looks like:
    # postgresql://USER:PASS@dpg-xxxxxxxxxxxx.oregon-postgres.render.com/DBNAME

    export DATABASE_URL='postgresql://...'
    python scripts/load_public_lands_to_postgres.py

    # Optional: custom path to the gzipped geojson
    python scripts/load_public_lands_to_postgres.py \\
        --geojson /path/to/public_lands.geojson.gz

    # If the table has rows and you want a clean reload:
    python scripts/load_public_lands_to_postgres.py --yes

    # If the table has rows and you want to continue where you stopped:
    python scripts/load_public_lands_to_postgres.py --resume

Total wall clock 5-15 min over residential upload. SSL drops on the
256 MB Postgres tier are common (backend OOM-killed by fat polygons)
but the auto-retry handles them transparently.
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
MAX_ATTEMPTS = 20          # SSL drops on the 256 MB tier are bursty; need
                           # generous headroom so a multi-drop run still
                           # finishes without operator intervention.
RETRY_DELAY_S = 3


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--geojson", default=DEFAULT_GEOJSON,
                   help=f"Path to gzipped geojson "
                        f"(default: {DEFAULT_GEOJSON})")
    p.add_argument("--yes", action="store_true",
                   help="If table has rows, TRUNCATE without prompting.")
    p.add_argument("--resume", action="store_true",
                   help="If table has rows, keep them and resume loading "
                        "from where the previous attempt stopped. Pairs "
                        "with the script's auto-retry on connection drop.")
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
    import psycopg          # for OperationalError; db.py also imports this

    if not db._IS_PG:
        print("ERROR: db module did not detect a Postgres URL.",
              file=sys.stderr)
        return 1

    size_mb = os.path.getsize(args.geojson) / 1e6
    target = os.environ["DATABASE_URL"].split("@")[-1]
    print(f"[load] source: {args.geojson} ({size_mb:.1f} MB gz)")
    print(f"[load] target: {target}")

    # NB: we do *not* call db.init_db() here. The schema was created by
    # an earlier prod deploy and is fully in place. Running init_db()
    # from the laptop re-issues CREATE INDEX IF NOT EXISTS statements;
    # those need ShareLock on public_lands, which conflicts with the
    # ShareUpdateExclusiveLock autovacuum holds when it runs on the
    # TOAST table -- and statement_timeout fires before the lock clears.
    # Skipping init_db() here makes the laptop load immune to that
    # transient lock contention. The web service still runs init_db()
    # on boot and is idempotent; if it ever fails with the same
    # timeout, it'll succeed on the next deploy when autovacuum isn't
    # active.

    # Inspect current state. Three possible starting states:
    #   - empty table -> fresh load
    #   - partial rows from a previous interrupted run + --resume -> continue
    #     where we left off (skip the first N valid features in the stream)
    #   - partial rows + --yes (or interactive 'y') -> TRUNCATE first, then
    #     fresh load
    # Default with rows present + no flag = prompt the user.
    existing_n = _count_rows(db)
    if existing_n:
        print(f"[load] public_lands has {existing_n:,} existing rows.")
        if args.resume:
            print(f"[load] --resume given; will skip the first {existing_n:,} "
                  f"valid features and continue from there.")
        elif args.yes:
            _truncate(db)
            print(f"[load] truncated.")
            existing_n = 0
        else:
            ans = input("  [t]runcate and reload, [r]esume, or "
                        "[a]bort? ").strip().lower()
            if ans in ("t", "truncate"):
                _truncate(db)
                existing_n = 0
            elif ans in ("r", "resume"):
                pass
            else:
                print("[load] aborted.")
                return 1

    placeholders = ",".join("%s" for _ in db._PL_COLS)
    insert_sql = (f"INSERT INTO public_lands ({','.join(db._PL_COLS)}) "
                  f"VALUES ({placeholders})")

    print(f"[load] streaming features in batches of {BATCH_SIZE:,} "
          f"(commit per batch + auto-resume on connection drop) ...")
    started = time.time()
    attempt = 0
    skip_n = existing_n      # features-already-loaded counter; grows across
                             # attempts if we lose the connection mid-load.

    # Retry-on-drop loop: each attempt opens a fresh connection, skips
    # features already durable in the DB (committed by earlier attempts),
    # and resumes loading from there. Connection drops (Postgres backend
    # OOM-killed by a fat polygon on the 256 MB tier, residential NAT
    # timeout, etc.) become a few-second pause instead of a full restart.
    while True:
        attempt += 1
        try:
            inserted_this_attempt = _do_load(
                db, ijson, args.geojson, insert_sql, skip_n, started)
            skip_n += inserted_this_attempt
            break               # full stream consumed, we're done
        except psycopg.OperationalError as exc:
            # Refresh skip_n from the DB rather than trusting the in-memory
            # counter: the last batch may have been committed even if the
            # client saw the connection drop afterwards.
            try:
                skip_n = _count_rows(db)
            except Exception:
                pass            # if even the count fails, keep the in-memory value
            print(f"\n[retry] connection dropped after {skip_n:,} rows: "
                  f"{type(exc).__name__}: {str(exc)[:120]}")
            if attempt >= MAX_ATTEMPTS:
                print(f"[error] giving up after {MAX_ATTEMPTS} attempts.",
                      file=sys.stderr)
                print(f"[error] {skip_n:,} rows are durable in the DB; "
                      f"re-run with --resume to continue.", file=sys.stderr)
                return 1
            print(f"[retry] reconnecting in {RETRY_DELAY_S}s "
                  f"(attempt {attempt + 1}/{MAX_ATTEMPTS}) ...")
            time.sleep(RETRY_DELAY_S)

    elapsed = time.time() - started
    print(f"\n[done] {skip_n:,} rows in public_lands "
          f"({elapsed/60:.1f} min total, {attempt} attempt"
          f"{'s' if attempt != 1 else ''})")
    print(f"[done] now run `VACUUM ANALYZE public_lands;` in TablePlus "
          f"so the planner picks up the GiST index immediately.")
    return 0


def _count_rows(db) -> int:
    """Current row count of public_lands. Helper because we look at it in
    several places (initial check, retry-resume, final report)."""
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM public_lands")
        row = cur.fetchone()
    return row["count"] if isinstance(row, dict) else row[0]


def _truncate(db) -> None:
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("TRUNCATE public_lands")
        conn.commit()


def _do_load(db, ijson, geojson_path: str, insert_sql: str,
             skip_n: int, started: float) -> int:
    """Stream the geojson and insert features past index `skip_n`. Commits
    per batch. Returns the number of rows inserted on this attempt.
    Raises psycopg.OperationalError on connection drop (caught by main's
    retry loop)."""
    import gzip
    valid_count = 0          # valid features SEEN so far (inserted or skipped)
    inserted = 0             # rows actually committed on this attempt
    batch: list[tuple] = []
    last_report = time.time()

    with db._conn() as conn:
        cur = conn.cursor()
        with gzip.open(geojson_path, "rb") as f:
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
                valid_count += 1
                if valid_count <= skip_n:
                    continue   # already loaded in a previous attempt
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
                    inserted += len(batch)
                    batch.clear()
                    now = time.time()
                    if now - last_report >= 5:
                        total = skip_n + inserted
                        rate = inserted / max(now - started, 0.001)
                        print(f"  {total:>7,} rows  "
                              f"({rate:.0f} rows/s, "
                              f"{(now - started)/60:.1f} min elapsed)")
                        last_report = now
        if batch:
            cur.executemany(insert_sql, batch)
            conn.commit()
            inserted += len(batch)
            batch.clear()
    return inserted


if __name__ == "__main__":
    sys.exit(main())
