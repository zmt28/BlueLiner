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

### Dams — federal, public domain

> **Verified 2026-06-14 (gis-endpoint-verify):** the BTS NTAD Dams MapServer
> URL below (`maps.bts.dot.gov/.../NTAD/Dams/MapServer`) is **dead** — both an
> explicit layer probe and a service enumeration returned nothing (`meta:
> None`; root unreachable). That host/path is retired. **Pivot:** make NHD
> FType 343 the primary dam geometry (it's bundled + free, no external host),
> and rediscover the live NID/NTAD attribute service via ArcGIS Online search
> (round-2 probe queued in `gis_verify_request.txt`) before relying on it.

- **Primary geometry (free, bundled, no host):** NHD **FType 343 (Dam/Weir)**
  features (FCode 34300/34305/34306) ship inside the same `NHDSnapshot.7z` the
  clickable-streams build already downloads. They live in `NHDPoint.shp` /
  `NHDArea.shp`, which the build does **not** currently extract — today
  `build_clickable_streams.py:255` extracts only `NHDFlowline.shp`. Adding those
  two layers to the extract list yields dam points **already snapped to the NHD
  network** (carries `GNIS_NAME` where named + the parent `COMID`/reach), so we
  can key dams to the river the user tapped. No new download, no new host, no
  reachability risk. This alone is enough for a v1 dams layer.
- **Attribute enrichment (optional, later):** USACE **National Inventory of
  Dams (NID)** adds the rich fields (dam name, river, purpose, height, max
  storage) but the BTS re-serve is gone — the round-2 probe uses the prober's
  AGOL `search:` mode to find the *current* hosted NID/NTAD feature service and
  capture its real schema. Join those attributes onto the NHD-343 points by
  proximity once the working endpoint is confirmed. Federal public domain.

### River trails — USGS National Map, public domain

> **Verified 2026-06-14:** the transportation service is reachable and the
> National Digital Trails layer id is **`/37` 'Trails'** (548,437 polylines) —
> **not `/8`** (that's `Roads 10M-Scale`). Confirmed schema: `name`,
> `maplabel`, `trailtype`, `trailsurface`, `hikerpedestrian`, `bicycle`,
> `packsaddle`, `motorcycle`, `ohvover50inches`, `permanentidentifier`,
> `trailnumber`, `routetype`, `seasonopen`, `pets`. (Layer `/11` 'National
> Trails' is the 34,220-feature named-trails subset, e.g. Appalachian/PCT.)

- **USGS National Map — National Digital Trails**, layer
  `https://carto.nationalmap.gov/arcgis/rest/services/transportation/MapServer/37`.
  Public domain, ~548k features, and the host (`*.nationalmap.gov`) is **already
  on the sandbox egress allowlist** alongside the other USGS services we call.
  Built as an R2 static **line** layer (mirror public-lands), with a build-time
  **spatial join to the stream network** so we only ship trail segments within
  ~N meters of a fishable reach (the "trails that run alongside rivers"
  intent) — `spatial_join_trout()` in `build_clickable_streams.py` (~:997) is the
  proximity-join template. Optionally narrow to foot traffic (`hikerpedestrian`
  truthy) so the layer is angler-walkable trails, not OHV/motorcycle routes.
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
Primary path is now NHD FType 343 (the BTS NTAD service is dead — see the
verification note above). Extend `build_clickable_streams.py:255` extract list
with `NHDPoint.shp`/`NHDArea.shp`, filter FType 343 (FCode 34300/34305/34306),
emit dam points carrying `GNIS_NAME` + parent COMID → R2 alongside the streams
artifact. Frontend: dams point layer in `map-layers.ts` (`makePoiElement("dam")`),
`#lyr-dams` toggle in the **Water features** section. NID attribute enrichment
(height/purpose) waits on the round-2 AGOL discovery confirming a live service
(`scripts/build_dams.py` mirror of `build_public_lands.py`). Verify: dams render;
river popup counts upstream dams.

### Phase 3 — River trails
`scripts/build_trails.py`: fetch USGS National Digital Trails → spatial-join to
the stream network (keep segments within ~N m of a reach) → R2 line layer +
PMTiles. Source layer confirmed: `transportation/MapServer/37 'Trails'`.
Frontend: line layer in `map-layers.ts` (public-lands source/layer pattern),
`#lyr-trails` toggle in the **Land & trails** section, Lucide route icon.
Verify: only near-river trails ship; toggle works; SW caches the tiles.

### Phase 4 — Standard sets (later)
Preset bundles in the Map Layers tab (e.g. "Wade fishing" = walk-in + wading +
parking + trails; "Boat" = ramps + dams). Pure UI over the per-type toggles
Phase 1 already exposes.

## CI endpoint verification (blocked hosts)

The sandbox egress allowlist blocks BTS / most state GIS, so endpoints are
confirmed in CI: put request lines in `scripts/gis_verify_request.txt`, push,
and read the committed `gis_verify_out/REPORT.txt`. The prober speaks ArcGIS
(it appends `?f=json` + `/query` itself), so request lines are bare layer URLs
or `svc:`/`search:` directives — not full query strings.

**Round 1 (done, 2026-06-14):** trails layer confirmed = `/37 'Trails'`; BTS
NTAD Dams MapServer dead. **Round 2 (queued):** discover the live NID/NTAD dam
service via AGOL search + capture the trail layer's coded domains:

```
# Dams — let AGOL search find the current hosted NID/NTAD service (the
# hardcoded BTS MapServer is retired); the prober verifies each hit.
search: US|National Inventory of Dams
search: US|NTAD dams national
# Trails — full field dump on the confirmed layer (coded domains for the
# trailtype / surface / use-flag normalizer).
US|https://carto.nationalmap.gov/arcgis/rest/services/transportation/MapServer/37
```

RIDB (recreation.gov) is a **keyed REST API, not ArcGIS**, so this prober can't
exercise it — it's verified separately when the federal-land access supplement
is built. Read the committed `REPORT.txt` for exact field names before wiring
any normalizer — never assume schema for a host the sandbox can't reach.

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
