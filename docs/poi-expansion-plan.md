# POI Expansion Plan: dams, river trails, per-type access toggles, grouped legend/filters

Owner decisions (2026-06-14):
- **Per-type access toggles** in v1 — one toggle per access kind (boat ramp,
  walk-in, wading, pier, parking), not one blanket "Access points" switch.
  *"Let's have per type toggles, then work standard sets into the map layers
  tab later like TroutRoutes does."* → standard-set preset bundles are a
  **later** phase, not now.
- **Group filters + legend into labeled sections.** *"Group into labeled
  sections."*

Plan approved-pending-review before implementation begins. Phases 2–3 add real
data pipelines (R2-hosted national layers), so this is design-first like the
UI-simplification and trout-coverage plans.

## Goals

1. Add the POI classes the user named — **dams on rivers**, **walk-in access**,
   **parking**, **boat ramps**, and **trails that run alongside rivers** —
   sourced only from public-domain / agency data we can verify.
2. Fold them into the legend / filters / map-layers panel in a way that scales
   past today's flat 6-row list, via **labeled sections** and **per-type
   access toggles**.
3. Keep the existing plug-and-play POI machinery (one glyph + one `data-poi`
   row + one `wireLayerToggle` call per type) — no new rendering primitives.

## Data sources (confirmed in the discovery pass)

### Dams — federal, public domain, two complementary sources
- **Primary, national static layer:** USACE **National Inventory of Dams (NID)**
  re-served by BTS as the NTAD Dams MapServer
  (`https://maps.bts.dot.gov/services/rest/services/NTAD/Dams/MapServer`).
  Federal public-domain data. Key fields: `damName`, `riverOrStreamName`,
  `purposes` (H/S/C/R flags), `damHeight`, `maxStorage`, `ownerType`. We snapshot
  it at build time into an **R2-hosted national static layer** (mirror
  `scripts/build_public_lands.py` — fetch → normalize → GeoJSON/PMTiles → R2),
  not a live per-request feed (it's ~92k features and changes annually).
- **Free supplement, already in the pipeline:** NHD **FType 343 (Dam/Weir)**
  features (FCode 34300/34305/34306) ship inside the same `NHDSnapshot.7z` the
  clickable-streams build already downloads. They live in `NHDPoint.shp` /
  `NHDArea.shp`, which the build does **not** currently extract — today
  `build_clickable_streams.py:255` extracts only `NHDFlowline.shp`. Adding those
  two layers to the extract list yields dam points **already snapped to the NHD
  network**, so we can carry the parent `COMID`/reach for free and key dams to
  the river the user tapped. No new download, no new host.
  - NID gives the rich attributes + national coverage; NHD FType 343 gives the
    on-network geometry. v1 ships NID as the visible layer; NHD-343 COMID
    association is the enrichment that lets a river popup say "2 dams upstream".

### River trails — USGS, public domain
- **USGS National Map — National Digital Trails**
  (`https://carto.nationalmap.gov/arcgis/rest/services/transportation/MapServer`,
  trails layer — **verify the exact layer id** in CI, expected ~8). Public
  domain, ~277k mi, and the host (`*.nationalmap.gov`) is **already on the
  sandbox egress allowlist** alongside the other USGS services we call. Built as
  an R2 static **line** layer (mirror public-lands), with a build-time
  **spatial join to the stream network** so we only ship trail segments within
  ~N meters of a fishable reach (the "trails that run alongside rivers"
  intent) — `spatial_join_trout()` in `build_clickable_streams.py` (~:997) is the
  proximity-join template.
- **Explicitly NOT OpenStreetMap** for trails — OSM is ODbL (share-alike
  attribution obligations we don't want to take on for a bundled layer). USGS
  public-domain data sidesteps that entirely.

### Access (boat ramps / walk-in / wading / pier / parking) — keep the existing pattern
- These stay on the **per-state live-feed + baseline** registries
  (`data/access_points/sources.json`), which already classify type via
  `type_field`/`type_flags`/`fixed_type`. The work here is **UI** (split the one
  toggle into per-type toggles) + **coverage** (the standing watcher keeps
  filling states), not a new national layer.
- **RIDB** (`https://ridb.recreation.gov/api/v1/facilities`, needs an API key)
  is a *supplement* for federal-land ramps/trailheads, folded into per-state
  access where a state agency feed is thin — not its own national layer.

### Do NOT use
- OSM/Overpass for any bundled layer (ODbL share-alike).
- Per-state dam registries (50 schemas) when NID already unifies them nationally.
- A live per-request dams/trails feed (too large; static R2 + SW cache instead).

## UI design — grouped sections + per-type toggles

Today the Filters "Show on map" list (`index.html:368-410`) and the legend
"Points" list (`legend.ts:49-57`) are flat. Adding dams + trails + 5 access
types would make a 12-row wall. Group both into the **same three labeled
sections**, in this order:

| Section | Map-layer rows |
| --- | --- |
| **Access & facilities** | Boat ramp · Walk-in · Wading · Pier · Parking |
| **Water features** | Stocked waters · Dams · *(gauges — legend only)* |
| **Land & trails** | Public lands · River trails |

(Saved pins + the stream/waterway line toggles stay in their own existing rows;
this regrouping is about the POI/overlay block.)

### Per-type access toggles
- Replace the single `#lyr-access` toggle with five: `#lyr-access-boat_ramp`,
  `#lyr-access-walk_in`, `#lyr-access-wading_access`, `#lyr-access-pier`,
  `#lyr-access-parking`, each with its matching `data-poi` glyph.
- `map-layers.ts ensureAccess` already fetches once and renders per-feature with
  `makePoiElement(p.type)`; switch it to keep **per-type marker buckets** and a
  `setAccessTypeVisible(type, on)` so toggling a type shows/hides its bucket
  without refetching. `bl_layers` persistence (controls.ts) gains the 5 keys.
- Keeps the door open for **"standard sets"** later (Phase 4): a preset that
  flips a curated subset of these toggles at once, TroutRoutes-style.

### New glyphs
- Add **`dam`** to `PoiType` + `GLYPH_PATHS` in `poi-icons.ts` (Lucide has no
  dam; hand-draw a barrier/weir glyph in the same 24×24 stroke-2 style as
  `pier`). Trails render as a **line** layer (public-lands pattern), so they use
  a Lucide line/route icon in the panel/legend, not a POI disc — consistent with
  how streams/waterways/public-lands rows already use Lucide for line/fill
  layers.
- `legend.ts POINT_ROWS` gains `dam`; the render functions split `POINT_ROWS`
  into the three section groups (a small `[section, rows[]]` structure) so
  legend + filters share one source of truth and can't drift.

## Phases (each a small PR)

### Phase 1 — UI regroup + per-type access toggles (UI-only, no new data)
Grouped sections in `index.html` + `legend.ts`; split `#lyr-access` into 5
per-type toggles; `ensureAccess` per-type buckets + `setAccessTypeVisible`;
`bl_layers` keys; `dam` glyph added (unused until Phase 2). Verify: typecheck;
each access type toggles independently; persistence survives reload; legend
mirrors filters.

### Phase 2 — Dams
`scripts/build_dams.py` (mirror `build_public_lands.py`): fetch NID/BTS NTAD →
normalize fields → GeoJSON.gz + PMTiles → R2 under the data prefix. Extend
`build_clickable_streams.py:255` extract list with `NHDPoint.shp`/`NHDArea.shp`,
filter FType 343, attach parent COMID (enrichment for river popups). `/api/dams`
optional (static R2 layer can load directly like public-lands). Frontend: dams
map layer in `map-layers.ts` (point bucket, `makePoiElement("dam")`), `#lyr-dams`
toggle in the **Water features** section. **CI-verify the BTS endpoint first**
(see below). Verify: dams render; river popup counts upstream dams.

### Phase 3 — River trails
`scripts/build_trails.py`: fetch USGS National Digital Trails → spatial-join to
the stream network (keep segments within ~N m of a reach) → R2 line layer +
PMTiles. Frontend: line layer in `map-layers.ts` (public-lands source/layer
pattern), `#lyr-trails` toggle in the **Land & trails** section, Lucide route
icon. **CI-verify the TNM trails layer id first.** Verify: only near-river
trails ship; toggle works; SW caches the tiles.

### Phase 4 — Standard sets (later)
Preset bundles in the Map Layers tab (e.g. "Wade fishing" = walk-in + wading +
parking + trails; "Boat" = ramps + dams). Pure UI over the per-type toggles
Phase 1 already exposes.

## CI endpoint verification (blocked hosts)

The sandbox egress allowlist blocks BTS, most TNM sub-services, USFS/BLM/NPS,
and RIDB, so confirm each before building against it: append to
`scripts/gis_verify_request.txt` and let `gis-endpoint-verify.yml` write
`gis_verify_out/REPORT.txt`:

```
# Dams (BTS NTAD NID) — confirm layer 0 query + field names
US|https://maps.bts.dot.gov/services/rest/services/NTAD/Dams/MapServer/0/query?where=1=1&outFields=*&resultRecordCount=1&f=json
# USGS National Digital Trails — confirm the trails layer id (expected ~8)
US|https://carto.nationalmap.gov/arcgis/rest/services/transportation/MapServer?f=json
# RIDB facilities (needs key) — confirm reachability/shape for the access supplement
US|https://ridb.recreation.gov/api/v1/facilities?limit=1
```

Read the committed `REPORT.txt` for exact `outFields` before wiring any
normalizer — never assume field names for a host the sandbox can't reach.

## Why this is low-risk

- **No new rendering primitives.** Every addition reuses an existing template:
  POI disc (`makePoiElement`), line layer (public-lands), static-R2 build
  (`build_public_lands.py`), proximity join (`spatial_join_trout`),
  `wireLayerToggle` + `bl_layers`.
- **Dams come partly free** — NHD FType 343 is already in the archive we
  download; only the extract list and a national NID snapshot are new.
- **Public-domain only** — NID, NHD, USGS trails are all federal public domain;
  no ODbL/share-alike obligations enter the bundle.
- **Coverage keeps converging** without this plan — the access per-type split is
  pure UI; the standing watcher independently fills the per-state access feeds.
