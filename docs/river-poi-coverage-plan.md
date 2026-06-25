# River POI coverage — accuracy-first national plan

_Last updated: 2026-06-25._

## The problem this fixes

The clicked MD access point ("Glencoe / Monkton") is a **hand-curated baseline**
coordinate (`39.576, -76.613`, marked approximate). It lands in a residential
stretch, so Apple/Google **reverse-geocode** it to a neighbor's house
("16519 Garfield Ave" / "16515 Falls Rd") — two different houses, because each
map app snaps an addressless coordinate to its own nearest postal address.

That's the failure mode we must NOT scale: **`data/access_points/<ST>.json` is
176 hand-placed approximate points.** Multiplying that nationwide would multiply
the inaccuracy. The fix is not "place more pins by hand" — it's to **source
every point from a dataset that already carries a real, surveyed/mapped
coordinate, and clip it to our river network.**

### The accuracy principle (non-negotiable)

1. **Authoritative coordinates only.** Federal/state GIS, OSM mapped nodes, or
   geometry we derive ourselves. **Never hand-place, never reverse-geocode.**
2. **Geometry-derived where possible.** A bridge over a river is exactly the
   intersection of a road and a flowline — that coordinate is correct *by
   construction*, with zero sourcing error.
3. **Relevance = river-clip.** Keep a POI only if it's within a type-specific
   buffer of a reach in the clickable-streams network (the same network the map
   renders). This is what makes a parking lot "a river access," not just a lot.
4. **Provenance + precision on every POI.** Carry `source`, `source_id`, and a
   `precision` class so the UI can show it and we can audit it.
5. **No silent inaccuracy.** A POI we can't place accurately is dropped, not
   guessed.

## We already have the pattern — generalize it

BlueLiner already ships "national authoritative source → clip to the river
network → R2 overlay." This plan generalizes it; it does not invent it.

| Existing | Source | Method |
| --- | --- | --- |
| **Trails** (`build_trails.py`) | USGS National Map Trails (layer 37, ~548k, public domain) | clip to within `--buffer-m` of clickable-streams in EPSG:5070 → GeoJSON + PMTiles → R2, `VITE_TRAILS_TILES_URL` |
| **Dams** (`dams.py`) | USACE NID (`NID_v1`, 92,469 pts, federal) | national point layer, surveyed coords |
| **Public lands** | PAD-US | viewport parcels |
| **River network** | NHDPlus (`clickable_streams` + `vaa`) | **the clipping + reach-association backbone** |

The river network is the asset that makes all of this work: any national POI can
be spatially joined to the nearest reach (`levelpathid`) so it appears in the
right river's panel, and clipped by distance-to-water for relevance.

**Gap:** OpenStreetMap is not used yet (`grep osm` → nothing). OSM is the single
richest source for the POI types we lack — fly shops, parking, fine-grained
access — with real mapped coordinates and one tag schema nationwide.

## The unified River-POI pipeline (build-time)

A single data-build job, modeled on `build_trails.py`, run in CI / a dev box
with open egress (NOT the runtime image, NOT the sandbox — egress allowlist):

```
for each POI type:
  pull national source(s)            # federal GIS | OSM extract | agency feed | derived
  → normalize to {lat, lon, name, type, source, source_id, precision, attrs}
  → river-clip: keep if within buffer_m(type) of a clickable_streams reach (EPSG:5070)
  → associate: nearest reach → levelpathid (panel placement)
  → dedupe across sources by (type, ~25 m, normalized name); precedence below
  → emit data/poi/<type>.geojson.gz  + PMTiles
publish to R2 (versioned prefix); toggle per-type in map-layers.ts
```

- **Dedupe precedence:** state agency (surveyed) > OSM (mapped) > derived. Merge
  attributes; keep the most precise coordinate.
- **Serving:** PMTiles overlays (like trails) for the big national types;
  per-type toggles reusing the existing `lyr-access-<type>` machinery in
  `map-layers.ts`. Live per-state agency feeds keep their runtime path.
- **Bounded + tiled:** river-clipping + PMTiles keeps tilesets small (proven by
  trails cutting 548k → riverside subset).

## POI taxonomy & authoritative sources

Buffers are the river-clip distance (smaller = "on the water", larger =
"reachable nearby").

| POI type | Primary source (real coords) | Buffer | License | Notes |
| --- | --- | --- | --- | --- |
| **Boat ramps / launches** | State agency ArcGIS (have, 33) + OSM `leisure=slipway` + RIDB (federal rec) | ~50 m | mixed / ODbL / PD | water-edge; high confidence |
| **Fishing / wading access** | State agency ArcGIS + OSM `leisure=fishing` | ~75 m | mixed / ODbL | replaces curated baselines |
| **Fly shops** | OSM `shop=fishing` | ~2 km (drive-to, not on-water) | ODbL | sparse in OSM; supplement later |
| **Parking along rivers** | OSM `amenity=parking` clipped tight to water/trailheads | ~150 m | ODbL | only keep near access/trailheads |
| **Bridges over rivers** | **Derived: road × flowline intersection** (TIGER/Line or OSM highways × clickable-streams); NBI/OSM `bridge` for labels | exact | PD / ODbL | coordinate exact by construction |
| **Trails (lines)** | USGS TNM Trails (have) | existing | PD | already shipping |
| **Trailheads (points)** | OSM `highway=trailhead` + TNM trail access points | ~200 m | ODbL / PD | the *point* a trail meets a road near water |
| **Dams / weirs** | NID (have) | n/a | PD | done |
| **Campgrounds (river)** | RIDB + OSM `tourism=camp_site` | ~300 m | PD / ODbL | optional enrichment |
| _(existing)_ gauges, stocking, hatches, public lands | USGS / agency / curated / PAD-US | — | — | already in app |

> Other candidates worth a line later: float put-in/take-outs (OSM
> `canoe`/`whitewater`), restrooms at access (OSM), USGS real-time gauges
> (have), state regulation/special-water boundaries (agency).

## Accuracy guarantees, concretely

- **Coordinate provenance** stamped per POI; popup shows source + an "Agency
  info"/OSM link. No anonymous hand-placed pins.
- **Bridges are geometry-derived** — the crossing is computed, so there is no
  sourcing error to audit.
- **Verification gate** (reuse the gis-endpoint-verify / coverage-survey
  primitives): in-state bbox; **within `buffer_m` of water** for every
  water-access type (a "fishing access" 1 km from any river is rejected);
  dedupe precedence; a sampled **POI accuracy audit** (N per type — confirm each
  is within buffer of water and plausible) before publish.
- **Directions deep-link fix** (`static/src/directions.ts`): pass the POI name
  as a label so Apple Maps shows "Glencoe / Monkton (Gunpowder Falls)" instead
  of reverse-geocoding to a house (`maps.apple.com/?daddr=lat,lon&q=<name>`).
  Google's `dir` URL can't force a label for a bare coordinate, but with
  accurate coords its reverse-geocode lands sensibly. Navigation already routes
  to the exact coordinate; this only cleans up the displayed label.
- **Retire the hand-curated baselines.** Replace the 176 approximate points
  (incl. MD's 6) with sourced points. Keep the editorial *notes* ("broad runs
  and slicks") only by attaching them to a **sourced, accurate** coordinate —
  never as the coordinate's origin.

## Migration of access points (the immediate win)

Access is the first type onto the new pipeline because it's where the
inaccuracy lives:

1. **Keep + expand live agency feeds** — they're surveyed and already work. The
   weekly `coverage-survey` already discovers **access** candidates for *all*
   states (access isn't trout-gated), so the stocking-style
   discover → field-dump → verify → promote loop applies directly.
2. **National OSM + RIDB fill** for states/reaches with no agency feed —
   `leisure=slipway`/`leisure=fishing`, river-clipped, deduped against agency.
3. **Repair/deprecate the curated baselines** — sourced points supersede them;
   famous-spot notes ride on a corrected coordinate.

## Licensing & attribution (must resolve before shipping OSM)

- **Federal** (TNM, NID, RIDB, Census TIGER/Line): public domain — free to use.
- **OpenStreetMap**: ODbL — requires **attribution** ("© OpenStreetMap
  contributors") and share-alike on the produced database. We already attribute
  basemap tiles; add OSM attribution. Confirm ODbL share-alike is acceptable for
  the published POI database (likely yes, since the data is open) — **flag for a
  human license check before Phase 2.**
- **State agency**: per-source terms (most public-domain or open); recorded in
  the registry as today.

## Realistic ceilings & risks (learned from stocking)

- **OSM coverage is uneven** — well-mapped on popular rivers, thin in remote
  areas. It's the best scalable source and improves over time, but it is not
  complete. Set expectations: this raises coverage *a lot*, not to 100%.
- **Fly shops in OSM are incomplete.** A commercial POI feed (Google/Yelp/Foursquare)
  would fill gaps but adds cost + licensing — out of scope for v1; OSM-only first.
- **Bridges need a national roads layer.** TIGER/Line (Census, PD) or OSM
  highways × clickable-streams — a large geometry build; do it VPU-streamed like
  `build_clickable_streams.py` to bound memory.
- **Dedup across OSM/agency/derived** is the main engineering risk — name +
  proximity matching, precedence rules, and an audit sample.
- **Sandbox can't reach the sources** — all builds run in CI/data-build, exactly
  like trails/streams/VAA.

## Phasing

- **Phase 0 (days):** ship the Directions label fix; repair MD's 6 baseline
  coordinates from OSM/agency as a correctness proof. Small, demonstrates the
  accuracy bar.
- **Phase 1 (the core):** stand up the unified River-POI build by generalizing
  `build_trails.py`. First type = **access/boat-ramps** from OSM
  `leisure=slipway`/`leisure=fishing` + RIDB, river-clipped + agency-deduped,
  national → supersedes the curated baselines. Verify (bbox + water-buffer +
  audit sample), host on R2, toggle in `map-layers.ts`.
- **Phase 2:** **fly shops** (`shop=fishing`) + **trailheads** (OSM/TNM) — after
  the OSM license check.
- **Phase 3:** **parking near water** (`amenity=parking`, tight clip) +
  **bridges** (derived road × flowline crossings).
- **Ongoing:** the existing weekly survey keeps expanding agency access feeds;
  periodic re-verify catches drift.

## Resolved decisions (2026-06-25)

1. **OSM ODbL share-alike — approved** for the published POI DB. Add
   "© OpenStreetMap contributors" attribution. Phase 2+ unblocked.
2. **Bridges — derive from Census TIGER/Line (public domain)**, not OSM
   highways. Recommended: keeps the bridge layer free of ODbL share-alike,
   authoritative + complete for US roads, and the crossing coordinate is still
   geometry-derived (road × flowline) so it's exact by construction.
3. **Curated baselines — fully retire.** No "editor's picks" layer. The 176
   hand-placed points are replaced by sourced coordinates; editorial notes only
   survive if re-attached to a sourced, accurate coordinate.
4. **Fly shops — OSM-only for v1.** No commercial POI source for now; revisit if
   OSM coverage proves too thin.

### Phase 0 status

- ✅ **Directions deep-link label fix** shipped (`directions.ts` +
  `_directions_row_html`): Apple Maps gets the POI name as `q=` so it stops
  reverse-geocoding the coordinate to a house. Routing unchanged.
- ~~Repair MD's 6 baseline coordinates~~ — **dropped**, superseded by decision 3
  (full retire); Phase 1's sourced access layer replaces them wholesale.
