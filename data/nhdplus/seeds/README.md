# Last-known-good trout-source seeds

One JSON file per trout source from `data/trout/sources.json`, named by a
stable slug of `state + label` (see `seed_slug()` in
`scripts/build_clickable_streams.py`). Each file is that source's tagged
NHDPlusV2 COMID set as captured by the most recent successful **live**
fetch+join of a full national build — everything needed to re-tag the state
without its endpoint:

```json
{
  "version": 1,
  "state": "CO",
  "label": "CO Aquatic Management Waters",
  "captured_at": "2026-06-12T00:00:00+00:00",
  "git_sha": "<build sha>",
  "comid_count": 1234,
  "groups": [
    {"trout_class": "wild_reproduction", "tier": "class2", "native": true,
     "comid_count": 1234, "comids": [3806659, 3806661]}
  ]
}
```

`comids` are sorted ints (stable NHDPlusV2 identifiers, so a capture stays
valid across builds). `groups` preserves the (class, tier, native) split a
source produced, so seed-based tagging reproduces the live tagging exactly.

## Lifecycle

- **Write**: `build_clickable_streams.py` captures every live source's joined
  COMID set during the build and writes/refreshes its seed afterwards —
  full-region builds only (a `--regions` subset would clobber a national
  capture), and only when the content actually changed (`captured_at` is
  preserved otherwise).
- **Persist**: `data-build.yml` accumulates changed seed files on the
  long-lived **`data-seeds`** branch via a `peter-evans/create-pull-request`
  step (NOT a direct push — `main` is branch-protected and the Actions bot
  cannot push to it, which is why earlier direct-to-main seed commits silently
  vanished). One standing PR `data-seeds → main` is kept open and refreshed each
  daily warming run; its body carries the build's seed-coverage summary. A human
  MERGES that PR (the review gate) once coverage is complete; only then can the
  seeds reach a production publish.
- **Overlay**: a non-publish run (cron / `upload=false`) first overlays the
  `data-seeds` branch's seeds on top of main's checkout, so the build's coverage
  + fallback see the full accumulated set. Publish runs (`upload=true`) do NOT
  overlay — they build only from the seeds merged into `main`.
- **Read**: when a source's live fetch fails (after per-request backoff plus
  3 per-source attempts with 15s/45s waits), the build falls back to its seed
  and logs "tagging from a prior capture" with the capture date/staleness.
  The unreachable gate (`--require-trout`, exit 3) only fires for a source
  with NEITHER live data NOR a seed.
- **Bootstrap**: this directory starts with no seeds; the first scheduled
  warming run creates the `data-seeds` branch from scratch (the overlay no-ops
  gracefully when the branch doesn't exist yet). The daily cron then converges
  coverage hands-off — each source needs only one up-window ever — and a cheap
  pre-build guard skips the ~55-min build once every source has a seed on disk.
  Aging-seed refresh is operator-driven: a manual `upload=false` dispatch
  always builds and re-banks live captures (no auto freshness logic). Missing/empty seeds are handled gracefully (the
  source is simply gate-relevant until its first capture). The legacy
  explicit `seed:` registry key (MD → `data/nhdplus/MD_designated_comids.json`)
  still works as a pre-seeded entry; an auto capture here takes precedence
  once it exists.
