# Endpoint watch

A standing watcher for the flaky third-party state-GIS endpoints we're waiting
on. State government ArcGIS servers go down for minutes-to-hours and recover; the
watcher probes them on a schedule and, the moment one is reachable, captures what
we need and surfaces it in a report.

## What it watches

1. **`watchlist.json`** (this dir) -- INVESTIGATION endpoints. These are *not*
   candidate feeds; they're one-time captures we want when a retired/down server
   recovers. Each entry:

   ```json
   {
     "id": "stable-slug",
     "kind": "field_dump | discover | verify",
     "state": "MD",
     "url": "https://.../MapServer/0",
     "field": null,
     "note": "what we're after and why"
   }
   ```

   - **`field_dump`** -- probe a layer; if up, capture `name`/`geometry`/`fields`
     + 3 sample features. If `field` is set, also dump that field's distinct
     values. `field: null` means "dump everything so a human can identify the
     field," then set `field` for the next run.
   - **`discover`** -- if up, enumerate the folder's services / search the AGOL
     org and list trout/fish-named layers with record counts.
   - **`verify`** -- run the candidate 4-check (meta, count, f=geojson sample,
     in-state bbox) on a URL.

2. **`data/stocking/candidates.json`** + **`data/access_points/candidates.json`**
   -- the unverified feed leads. The watcher folds these in automatically as
   `verify`-kind entries. A candidate that PASSES the 4-check is flagged
   **READY TO PROMOTE** -- the watcher never edits `sources.json`; promotion
   stays human-reviewed.

## Flow

`scripts/endpoint_watch.py` loads the watchlist + both candidate files, probes
each entry (bounded timeout, a couple retries for flap tolerance), and writes a
markdown report. A DOWN host is reported as down, never an error -- the script
always exits 0 (it's a watcher, not a gate).

The report leads with a status table (`id | state | kind | UP/DOWN | captured`)
so a recovery is obvious at a glance, then per-entry detail for the reachable
ones.

## Where the report lands

`.github/workflows/endpoint-watch.yml` runs the watcher on a 6-hour cron (plus
`workflow_dispatch`):

- The **job step summary** (Actions tab) is the at-a-glance notification.
- The full report, including field dumps, is committed to the long-lived
  **`endpoint-watch`** branch as `gis_verify_out/WATCH.md` -- retrievable without
  cluttering `main`.

> The Claude Code sandbox's egress allowlist blocks most state GIS hosts, so a
> **local** run of `endpoint_watch.py` shows most entries DOWN. That's expected;
> the scheduled Actions runner has open egress and is where the probes fire.
