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
- **Persist**: `data-build.yml` commits changed seed files back to the
  triggering branch after a successful build (`[no ci]`).
- **Read**: when a source's live fetch fails (after per-request backoff plus
  3 per-source attempts with 15s/45s waits), the build falls back to its seed
  and logs "tagging from a prior capture" with the capture date/staleness.
  The unreachable gate (`--require-trout`, exit 3) only fires for a source
  with NEITHER live data NOR a seed.
- **Bootstrap**: this directory starts with no seeds; the first post-merge
  full build populates it. Missing/empty seeds are handled gracefully (the
  source is simply gate-relevant until its first capture). The legacy
  explicit `seed:` registry key still works as a pre-seeded entry (an auto
  capture here takes precedence once it exists), but no source currently uses
  it — MD's `seed:` was retired when it moved to a `Des_Use` `field_map`, since
  its hand-captured single-class file no longer matched the per-class output.
