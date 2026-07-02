# BlueLiner consolidated plan — July 2026

One worklist merging the **architecture review** (`docs/architecture-review-2026-07.md`) with
the **interaction-polish audit** (July 2026). Constraints unchanged: ≤ ~$25/mo, web/PWA only,
nationwide ambition. Priorities: map feel & polish first, product features second.

Sequencing: M1 → M2 → M3 → M4 → M5. M2 (interaction polish) and M3 (cartography) are
independent workstreams and can interleave; M2 items are individually small and make good
fill-in work. Items marked **[bug]** are behavior defects, not just polish.

---

## Milestone overview

| # | Milestone | Theme | Effort | Incremental cost |
|---|---|---|---|---|
| M1 | Light up what's built | Vector basemap + offline, deploy-flag work | days | ~$0 (R2 pennies) |
| M2 | Interaction polish | Fix how existing features *feel* | 1–2 weeks of small items | $0 |
| M3 | Cartography & smoothness | Terrain, tier-hero styling, render perf | 1–2 weeks | ~$0–1 |
| M4 | Product stickiness | Favorites → alerts, real search, speed | weeks | $0 |
| M5 | Data & platform hygiene | Bridge layer, coverage cadence, refactors | ongoing | $0 |

---

## M1 — Light up what's built (from architecture review, Phase 1)

- [ ] **M1.1** Build + publish the CONUS basemap archive (`scripts/build_basemap_tiles.sh` → R2),
      set `VITE_BASEMAP_TILES_URL` on Render, make `vector` the default base.
- [ ] **M1.2** Default the USGS hydro raster overlay **off** (auto-off when vector base active) —
      ends double-drawn water and label smothering. (`map-setup.ts:241`)
- [ ] **M1.3** Ship offline downloads (same flag; UI self-mounts). Device-test the SW asset path;
      consider small download concurrency (sequential today, `offline-tiles.ts:160`).
- [ ] **M1.4** Layer-order contract: explicit `beforeId` anchors (line overlays below base labels,
      symbols above); remove the promise-timing z-order dependency. (`map-layers.ts:163`)
- [ ] **M1.5** Boot hygiene: SW-cache or inline `/api/states` (removes the warm-boot network gate,
      `app-boot.ts:36`); bundle Lucide at build time instead of unpkg CDN (`index.html:33`).

---

## M2 — Interaction polish (new: pin-flow findings + polish audit)

### M2.a Feedback primitives (do first — several fixes below depend on them)

- [ ] **M2.a1** Minimal toast/snackbar component (the `--z-toast` token already exists,
      `tokens.css:216`; nothing uses it). Success + error variants, auto-dismiss.
- [ ] **M2.a2** Small styled confirm modal (reuse the pin-claim modal pattern,
      `index.html:647`) to replace native `confirm()`.

### M2.b Pin flow overhaul (the reported complaint + adjacent bugs)

- [ ] **M2.b1** **Ghost pin at the click point.** On map click in pin mode, immediately drop a
      provisional (semi-transparent, draggable) `.bl-pin` marker at `e.lngLat` before showing the
      form; commit on Save, remove on Cancel. Today only the coordinate is stored and the form is
      a `position: fixed` box in the bottom-right corner, disconnected from the click
      (`pins.ts:93-99`, `app.css:881-893`). Draggable ghost = free fine-tune of placement.
- [ ] **M2.b2** **[bug]** Pin mode doesn't suppress other click handlers: placing a pin on a
      stream/POI/public land also fires that feature's click (opens river panel / popup). Guard
      the stream + POI + lands + trails handlers on pin mode (`streams.ts:323`,
      `map-layers.ts:181,481,556`).
- [ ] **M2.b3** Pin mode is invisible on touch (crosshair cursor only, `pins.ts:88`). Show a
      dismissible hint chip ("Tap the map to place a pin") while armed; Esc cancels mode + form.
- [ ] **M2.b4** **[bug]** Save failure is silent and discards the note: on `!res.ok` the form
      hides and state clears anyway (`pins.ts:117-122`). Keep the form open, show inline error,
      preserve the note; remove the ghost only on success.
- [ ] **M2.b5** **[bug]** Pin delete ignores the response and has no confirmation: marker is
      removed even if the DELETE failed (`pins.ts:56-61`). Check `res.ok`; route through the
      M2.a2 confirm.
- [ ] **M2.b6** `loadPins()` has no error handling (unhandled rejection on network failure,
      `pins.ts:67-74`); humanize `created_at` in the pin popup (raw timestamp today, `pins.ts:41`).

### M2.c Selection & panels (audit P1)

- [ ] **M2.c1** **Pad the viewport when a panel opens.** Selecting a river/reach never repositions
      the map, so the 420px desktop drawer / mobile peek sheet covers the feature just clicked
      (`selection.ts:53`, `streams.ts:599`, `app.css:1152`). `map.easeTo({ padding: ... })` on
      open/close; pass the same padding to search's `flyTo` (`search.ts:149`).
- [ ] **M2.c2** **[bug]** "Near stocked water" and "Active hatch" filters are inert — the only
      consumer (`riverPasses()`, `rivers.ts:44`) is never called. Wire them into the overlay/search
      filtering, or hide the controls until they work (`index.html:351-363`).

### M2.d Forms & feedback (audit P1–P3)

- [ ] **M2.d1** Catch save: show "Catch saved ✓" toast before closing the modal — today it just
      vanishes (`catches.ts:215`).
- [ ] **M2.d2** **[bug]** Magic-link login advances to "Check your inbox" even when the send
      failed (empty catch, `auth.ts:177-191`; no error element in the modal). Advance only on
      `r.ok`; add inline error.
- [ ] **M2.d3** Replace native `confirm()` in catch delete (`catches.ts:297`) and account delete
      (`auth.ts:314`) with M2.a2.
- [ ] **M2.d4** Geolocation: pulse/spinner on `.locate-btn.is-active` while acquiring; toast on
      error/permission-denied; disabled state when unsupported (`controls.ts:329-346`).
- [ ] **M2.d5** Elevation "Gradient" tab: render "Loading gradient…" placeholder before the fetch
      (blank gap then pop today, `elevation-profile.ts:202-241`) — match the flow chart's pattern.

### M2.e Layers, basemap, map affordances (audit P2)

- [ ] **M2.e1** Layer toggles: `.catch` on the icon/tile mount path — on failure, uncheck +
      toast (today: checkbox stays checked with nothing shown, `map-layers.ts:163`). Optional
      fade-in on first tile load.
- [ ] **M2.e2** Basemap switch flashes empty (vector switch shows *no base* for a full style
      round-trip): add the new base before removing the old; drop the old on the new source's
      first `idle`/`sourcedata` (`map-setup.ts:227-228`).
- [ ] **M2.e3** Public-lands + trails layers: add pointer cursor on hover and `bl:poi-open`
      dispatch on click, matching every other POI layer (`map-layers.ts:481,556`).
- [ ] **M2.e4** Condition-filter empty result: when the overlay index is empty, show a small
      "No rivers match" chip instead of a silently dimmed map (`streams.ts:249-256`).

### M2.f Search quick fixes (audit P3 — distinct from M4's real-search project)

- [ ] **M2.f1** Arrow-key navigation (the `.is-active` style exists unused, `app.css:353`);
      Enter picks the active row (`search.ts:182-194`).
- [ ] **M2.f2** Highlight the matched substring in results (`<mark>`).
- [ ] **M2.f3** Empty-query "recents" are fake (first 5 rivers with a clock icon,
      `search.ts:89-100`): track real recents in localStorage, or relabel/re-icon.

### M2.g CSS pass (audit P4 — one sweep, half a day)

- [ ] **M2.g1** `:active` press states on buttons/tiles/rows (zero exist — taps feel dead on touch).
- [ ] **M2.g2** Global `:focus-visible { box-shadow: var(--ring-focus) }` for interactive elements
      (token exists, barely used; several inputs kill their outline).
- [ ] **M2.g3** `@media (prefers-reduced-motion: reduce)` neutralizing transitions/animations.
- [ ] **M2.g4** Thin tinted scrollbars in panel scroll regions.
- [ ] **M2.g5** Truncation/wrapping for long POI names in popups (`.ap-name` can blow out the
      420px popup).
- [ ] **M2.g6** Decide dark mode posture: at minimum dark panel/modal variants over the satellite
      base, or record light-only as intentional.

---

## M3 — Cartography & smoothness (from architecture review, Phase 2)

- [ ] **M3.1** Hillshade via free AWS Terrarium `raster-dem` (optionally `setTerrain` for 3D).
- [ ] **M3.2** Tier-hero restyle: stream colors/widths around gold/1/2/3 + wild on the vector
      base; along-stream name labels (self-hosted glyphs already exist).
- [ ] **M3.3** Fix the pan-time hotspot: move highlight/condition-overlay reapplication off
      `sourcedata` to `idle`/throttled or scope to new tiles (`streams.ts:317-518`).
- [ ] **M3.4** POI polish: drop `icon-allow-overlap` (let collision work) + `symbol-sort-key`;
      one icon-design pass over the flat Lucide discs.

---

## M4 — Product stickiness (from architecture review, Phase 3)

- [ ] **M4.1** Favorites → condition alerts → Web Push/Resend email ("Gunpowder just went
      green") — the retention loop TroutRoutes can't match without a scoring layer.
- [ ] **M4.2** Real search: prebuilt static index (rivers, gauges, counties, towns) searched
      client-side; free geocoder fallback. (M2.f's keyboard/highlight work carries over.)
- [ ] **M4.3** Perceived-speed pass: optimistic pin/catch writes (pairs with M2.b4/M2.d1 error
      handling), skeletons on panel loads.
- [ ] **M4.4** Scoring depth: trend arrows ("rising fast") + simple fishable-window signal from
      data already in the snapshot pipeline.

---

## M5 — Data model & platform hygiene (from architecture review, Phase 4; ongoing)

- [ ] **M5.1** Bridge-crossing access layer from OSM crossings × PAD-US/right-of-way heuristics,
      as a first-class entity in PMTiles.
- [ ] **M5.2** Access-point accuracy loop: `verified` provenance + in-app "report this spot";
      richer attributes where source fields carry them.
- [ ] **M5.3** Coverage cadence: promote from COVERAGE.md at a fixed rate (e.g. 2 states/month
      per dataset).
- [ ] **M5.4** Split `main.py` (clients/scoring/popup/routes); move popup rendering client-side.
- [ ] **M5.5** Merge the two bottom-sheet implementations (`snap-sheet.ts` vs
      `controls.ts:wireSheetDrag`); retire window bridges behind a typed event bus.
- [ ] **M5.6** Higher-zoom PAD-US variant (z12+, less simplification). **Non-goal:** parcel
      licensing.

---

## Non-goals (unchanged from the review)

Private-parcel/landowner data; native mobile apps; matching TroutRoutes' curated stream count
near-term — compete on live conditions, accuracy, openness, and a genuinely useful free tier.
