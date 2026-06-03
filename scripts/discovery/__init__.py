"""Trout-source discovery spike (Phase 0).

A dev-only toolkit that probes state fisheries GIS catalogs, scores candidate
ArcGIS layers, and proposes wild/stocked classifications -- so adding a state
to the clickable-streams build becomes a *reviewed data row* instead of
hand-written Python. See docs/trout-discovery-spike.md for the spec and
go/no-go gates.

Module map:
  lexicon   -- regulation-vocabulary token tables (pure data)
  classify  -- label -> {wild_reproduction, stocked} | FLAG (pure, offline)
  eval      -- score `classify` against the 10 already-shipped states (offline)
  catalogs  -- candidate-endpoint generation (ArcGIS/CKAN/dir-walk) [network]
  probe     -- layer metadata + scoring [network]
  report    -- dossier + go/no-go memo rendering

Only `classify`/`eval`/`lexicon`/`report` run offline; `catalogs`/`probe` need
open egress (the GitHub Actions discovery job), since this sandbox's network
policy blocks state ArcGIS hosts.
"""
