# UI Simplification Plan: gauges-on-selection, unified legend, icon-led layers

Owner decisions (2026-06-12): at-a-glance conditions = **verdict on tap only**
(option a+c below; no always-on verdict pins, no map-wide conditions mode in
v1). Plan approved-pending-review before implementation begins.

## Goals

1. **Gauge pins only on river selection.** No standalone condition discs by
   default; tapping a river reveals its gauges; deselecting removes them.
2. **One legend, one meaning per color.** Line color = stream tier only.
   Condition colors appear only on the selected river's gauge discs + line.
   Every POI type gets a distinct glyph icon (TroutRoutes-style monochrome
   marker), not a color code.
3. **Filters/Layers panel** rows use the same glyphs as the map markers.

## Key findings that de-risk this

- **River lines are already a full tap target.** `streams.ts:377-425` opens
  the river panel from a line tap and bridges to the gauged river by GNIS
  name/levelpathid — gauge pins are a redundant affordance, so removing them
  does not orphan any flow.
- **The river verdict is already client-side.** `/api/rivers` ships
  `conditions.overall` (worst-gauge verdict, computed in `main.py:925-928`)
  per river plus trimmed `gauges[]` — no API changes needed anywhere in this
  plan.
- **Deselection has a single choke point.** Every close path (X, ESC, map
  tap, snap-sheet drag-down) funnels through `closeRiverPanel()`
  (`river-panel.ts:115-124`) — one hook removes the pins everywhere.
- The Filters pane already uses `[icon][label][toggle]` rows
  (`index.html:339-376`); the gap is only that panel icons don't match map
  markers.

## Phases (each a small PR)

### Phase 0 — Centralize river selection (enabler)
New `static/src/selection.ts`: `selectRiver(river, streamProps?)` /
`clearRiverSelection()` + module-private `_selectedRiver`. Route the three
existing select sites (`rivers.ts:110-119`, `streams.ts:381-387`,
`search.ts:134-141`) and `closeRiverPanel` through it. No behavior change.
Keep selection.ts a leaf module to avoid import cycles. Verify: typecheck +
all select/deselect paths still work.

### Phase 1 — Gauge pins on selection only
In `rivers.ts`, delete the catalog-wide marker loop in `renderRivers()`
(`:100-128`); add `showGaugesFor(river)` / `hideSelectedGauges()` building
the same `.marker--good|fair|poor|none` elements for the selected river's
gauges only; call from selection.ts. Edge cases:
- Hold the selected river **by reference** so pins survive the z9
  viewport-mode swap of `allRivers` (`rivers.ts:156-161`); re-match by
  `site_no` when fresh data lands (SW stale-while-revalidate re-resolve).
- Marker count drops from hundreds to <=10 — perf win.
- Mobile: pins appear above the peek snap-sheet; optional `fitBounds` with
  bottom padding as polish.
Verify: clean map on load; tap river -> discs on its gauges; every close
path removes them; pan/zoom across z9 with a selection keeps them.

### Phase 2 — Verdict-colored selection highlight
Replace the flat-red selection paint (`streams.ts:156-163`) with a verdict
color: `_applyHighlight` sets feature-state `{selected, verdict}` from
`_selectedRiver.conditions.overall`; `colorExpr()` matches on it. The
"selected" signal moves to width-8 + opacity (already in `WIDTH_EXPR`).
Ungauged reaches keep flat red (no verdict feature-state). This also fixes
the collision between selection-red and verdict-red. Search results keep
their existing condition chip (`search.ts:104-110`). ~30 lines.

### Phase 3 — POI icon module + marker restyle
New `static/src/poi-icons.ts`: inline-SVG glyphs (Lucide path artwork, ISC)
per POI type (`boat_ramp`, `walk_in`, `wading_access`, `pier`, `parking`,
`stocked`, `gauge`, `pin`; future `camping`, `dam`, `fly_shop`) +
`makePoiElement(type)` (brand-blue disc, white glyph, white ring) +
`poiLegendHtml`/`poiRowIconHtml`. Replace `ACCESS_TYPE_META` letter/color
markers (`map-layers.ts:46-61`) and the mustard stocked dot (`:145-150`).
Inline SVG (not `data-lucide` hydration) because markers are built in tight
loops and must work offline. Perf guard for 1000+ live-feed access points:
zoom-gate access markers below ~z10 in `setAccessVisible`; symbol-layer +
sprite migration is a separate future project.

### Phase 4 — Legend rewrite
Replace the three-dot-system legend (`index.html:380-414`) with:
- **Stream tier**: SVG line-squiggle swatches, generated from
  `TIER_COLOR`/`TIER_LABEL` exports (`streams.ts:42-59`) via a tiny
  `legend.ts` so legend and map can't drift (today's hexes are hand-copied).
- **Gauge conditions**: keep the four shape-coded discs, retitled "shown on
  the river you select".
- **Points**: the actual `poi-icons.ts` glyphs at 18px.
- **Public lands**: keep fill swatches (polygon layer; unique meaning).

### Phase 5 — Filters/Layers panel icon alignment
Swap generic Lucide icons in `.filter-row-icon` for `poi-icons.ts` glyphs.
Optionally move the "Show on map" section from the Filters pane to the
Layers pane (TroutRoutes parity) — pure HTML movement, but checkbox IDs
(`lyr-*`) must be preserved or `bl_layers` prefs reset
(`controls.ts:392-452`).

### Phase 6 (deferred) — map-wide "Conditions" line-color mode
Feature-state-paint all gauged rivers' reaches by verdict, fade the rest.
Riskiest (per-`sourcedata` matching, partial coverage UX). Build only if
Phases 1-2 leave users wanting at-a-glance verdicts at state zoom. This is
also what gives the Condition filter its visible meaning back.

## Known UX trade-offs (accepted for v1)

- At state zoom (z7) the thinned tile network replaces 22px discs as the tap
  affordance; the 16px hit casing helps, but watch for "where did the pins
  go" feedback. Search + filters remain the zoomed-out conditions path.
- The Condition dropdown (`cond-select`, `index.html:315-321`) visibly does
  nothing once pins are gone (it still scopes search). Annotate it or hide
  it until Phase 6.

## Test/verify

No frontend unit tests exist; rely on `npm run typecheck`, `npm run dev`
against `uvicorn main:app`, and manual passes at mobile width (snap-sheet
breakpoint 700px, tab bar 760px — they differ: `snap-sheet.ts:55`,
`controls.ts:114`).
