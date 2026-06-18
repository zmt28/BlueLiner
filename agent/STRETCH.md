# Expansion path — self-healing data-quality agent (design, not built)

The trip-planning agent is only as good as Blueliner's data. The natural next
agent points the **same shape** (tools + an LLM loop + a human-in-the-loop
approval boundary) at **data integrity** instead of trip planning.

## Problem
Blueliner stitches together flaky public feeds: USGS NWIS/NLDI, state ArcGIS
endpoints, and the precompute snapshots in Postgres. Failures are quiet —
a gauge reporting nonsense (−999999 sentinels, a frozen value), a river
mis-attributed to the wrong NHD levelpath, a snapshot that silently went stale.
Today a human notices when the map looks wrong.

## Shape
A scheduled agent (reuse `endpoint-watch.yml` / `refresh-precompute.yml` cron)
with read-only tools over the data layer:

- `sample_gauge(site_no)` — recent IV values + the cached median.
- `snapshot_age(state)` — how stale each per-state `river_snapshot` is.
- `nhd_identity(site_no)` — the cached `gauge_meta` vs a fresh NLDI lookup.
- `feed_health(source)` — last-success + error rate per `sources.json` feed.

The agent diagnoses anomalies (z-score on the gauge series, identity mismatch,
snapshot older than N×refresh-interval), then takes ONE of two **bounded**
actions:

1. **Quarantine** — set a `quarantined` flag on a gauge so the app and this
   trip-planner skip it (reversible, low blast radius). Autonomous.
2. **Open a GitHub issue/PR** — for anything structural (a feed mapping change,
   a misattributed river). A human reviews and merges. Never auto-merges.

## Why it fits the rubric
- **Adjacent-system integration:** writes to the same Postgres + the existing
  GitHub Actions/`gh` workflows already in this repo.
- **Autonomy boundary:** quarantining behind a flag is reversible and safe to
  automate; schema/registry edits require human merge — the same notify-vs-act
  line the trip-planner draws between guardrail vetoes and the proactive emailer.
- **Eval reuse:** the same scorer-as-oracle idea — inject known-bad gauge series
  as fixtures and assert the agent quarantines/flags them and leaves good ones
  alone (precision/recall on anomaly detection).

This is the callback to auto-generated audit tickets: the agent turns silent
data rot into a reviewable queue instead of a user-visible map bug.
