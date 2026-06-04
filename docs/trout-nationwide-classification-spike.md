# Spike plan — nationwide trout classification (gold / class 1 / 2 / 3 + wild & native filters)

Status: proposed. Time-box the gating phase (Phase 0) before anything else.

## 1. Why rethink

The current model is a per-state registry (`data/trout/sources.json`, 22 states /
23 sources) that the build joins to NHDPlus flowlines, collapsed into **two
buckets**: `wild_reproduction` (green) and `stocked` (blue). It works where a
state publishes a clean wild/stocked designation, but it has hit a structural
wall:

- **Coverage gaps that aren't tooling failures.** MT, ID, AZ, NM, OR/WA don't
  publish a clean per-state wild/stocked line layer — the data lives in
  federal/TU/self-hosted/non-spatial sources. No amount of probing changes that.
- **Inconsistent semantics.** "Class I/II", "Type 1–4", "Blue Ribbon",
  "Gold Medal", "delayed harvest" mean different things per state; every onboard
  is a bespoke judgment call mapped onto our two buckets.
- **The model is binary** (wild vs stocked) while anglers (and TroutRoutes)
  think in **quality tiers**.

The reiterated guiding principle:

> *Whatever logic we end up with should be as consistent as possible nationwide.
> Only when compromises between consistency of logic and coverage of all known
> trout streams are unavoidable should we consider making exceptions.*

A single nationwide classification is the **maximally consistent** option — one
logic, every reach, no per-state exceptions. That is exactly what this principle
points toward. The proposed pivot:

- **Coloring** by a nationwide 4-tier class (gold medal / class 1 / class 2 /
  class 3), the scheme TroutRoutes uses.
- **Filters** layered on top: "wild trout rivers" and "native trout rivers."

## 2. The gating risk (read first)

The "gold / class 1 / 2 / 3, every stream, no exceptions" classification is
**almost certainly TroutRoutes' proprietary product** (TroutRoutes was acquired
by onX; the stream-tier classification is the paid app's core differentiator),
not a free Trout Unlimited download. TU holds nationwide *conservation* data
(Conservation Success Index, native ranges), but normalizing every reach into a
gold/class scheme is the commercial artifact.

**Everything hinges on whether we can legally obtain and use this dataset.** Do
not design UI, change the schema, or remove the existing registry until Phase 0
resolves this. There is a viable fallback (Phase 4B) if we can't license it.

## 3. Hypothesis to de-risk

> We can replace the 50-state patchwork with **one nationwide classification
> dataset** as the base coloring, expose **wild** and **native** as filters,
> keep the UI bounded, join it cleanly to our NHDPlus geometry, and reuse the
> 22-state registry as the wild/native feeder + fallback — without throwing work
> away.

## 4. Phases & decision gates

### Phase 0 — Provenance & licensing (GATING; time-box ~1 day)
The make-or-break. Answer, with sources:
- **What is the dataset, exactly?** TroutRoutes/onX proprietary? A TU public/CC
  release? Derived from a reproducible public source (NHD + a published model)?
- **License terms** for app use (commercial product, redistribution of derived
  tiles, attribution).
- **Access path:** bulk download, API, vector tiles, or partnership-only.
- If unclear, **contact TU** (and/or onX) directly to ask about data licensing.

**Gate 0:** Legally usable for our app? → Phase 1. Not usable / license-only →
jump to **Phase 4B (build our own)**. Do not proceed to UI/data design on data we
can't use.

### Phase 1 — Data characterization (if Gate 0 passes)
Acquire a multi-region sample (e.g. PA, WI, MT, CO, CA) and resolve:
- **Geometry & linkage:** NHD/COMID-keyed (→ trivial join) or standalone
  geometry (→ reuse the build's `buffer + sjoin(intersects)` path, as for CO/CA
  polygons)?
- **Schema:** the class field; any `wild` / `native` fields, or must we derive
  them?
- **Semantics:** what do gold / class 1 / 2 / 3 actually encode — quality,
  biomass, management, fishery type?
- **Completeness & freshness:** truly every reach? update cadence? size on the
  free tier (cf. `cache.py` / R2 hosting in `data_source.py`)?
- **The consistency audit (the whole point):** sample the *same kind* of water
  across states — a WI Class I wild stream, a PA Class A, a MT blue-ribbon, a
  stocked put-and-take — and confirm the class assignment means the **same thing
  everywhere**. If it just re-encodes each state's scheme, it doesn't actually
  buy consistency and the premise is weaker than it looks.

**Gate 1:** Consistent nationwide semantics **and** carries/derives wild+native?
→ Phase 2.

### Phase 2 — Data-model & pipeline design
- **Schema:** extend the reach record from `{trout_class}` to a base
  `class_tier ∈ {gold, class1, class2, class3}` plus booleans `is_wild`,
  `is_native`. (`build_clickable_streams.py` already emits `trout_class`; this is
  an additive change to the emitted properties + the frontend bucket table.)
- **Join:** COMID-keyed → join in the build, **retire the per-state fetch for the
  base layer**. Standalone geometry → existing buffer+intersect.
- **Reuse, don't discard, the registry:** the 22-state wild/stocked work becomes
  the **`is_wild` feeder**; the native layers (CO cutthroat, CA heritage, NV
  Lahontan) become the **`is_native` feeder**. Define precedence when our state
  data and the national tier disagree (proposal: national tier drives color; our
  designations drive the wild/native flags — they're orthogonal axes).

**Gate 2:** Clean join + a migration that preserves existing work? → Phase 3.

### Phase 3 — UI/UX (honor the "don't make it a million options" constraint)
Current surface is 2 colors + one *Map Style: wild/stocked/all* toggle
(`static/src/streams.ts`). Proposed surface:
- **Color = 4 tiers** (gold / class 1 / 2 / 3). One legend; pick a
  colorblind-safe ramp; reuse the `match`-on-`trout_class` machinery already in
  `streams.ts`.
- **Two filter toggles:** "Wild trout rivers", "Native trout rivers" (additive,
  on top of the tier coloring). The existing wild/stocked style toggle collapses
  into the "Wild" filter.
- **Total control surface:** 1 tier legend + 2 filter toggles + basemap — bounded,
  not a slider farm. Validate against the simplicity bar; mock it; check the
  mobile snap-sheet (`snap-sheet.ts`). Decide colorblind palette + whether
  "stocked" needs to remain visible anywhere.

**Gate 3:** Control surface stays bounded and legible? → Phase 4.

### Phase 4 — Prove it end-to-end
- **4A (Gate 0 passed):** thin vertical slice — one region: national data →
  recolored clickable streams → both filters working → mobile sheet. Confirms
  pipeline + UX before the full migration.
- **4B (Gate 0 FAILED — fallback):** **build our own nationwide normalization.**
  Map each state's native scheme into the common 4-tier target ourselves, using
  the registry as the foundation (we've already normalized 22 states to
  wild/stocked — extend the target vocabulary to gold/class1/2/3 and keep going).
  More work, fully owned, and **still the consistency-maximizing design**:
  one target scheme, per-state mappings as the unavoidable coverage compromise.
  This is the path the guiding principle endorses even without TU's data.

## 5. How this serves the principle

| Element | Consistency | Coverage compromise (only where unavoidable) |
|---|---|---|
| National 4-tier base | One scheme, every reach | n/a — this is the consistency win |
| `is_wild` / `is_native` filters | Derived from a uniform rule where data exists | Falls back to our state designations where the national set is thin |
| Existing registry | Becomes the wild/native feeder + 4B fallback | Per-state mappings are the documented exceptions |

The national dataset (or our own normalization in 4B) is the consistent base;
our state work supplies the wild/native detail and patches gaps — the exceptions
are explicit and only where coverage demands.

## 6. Risks
- **Licensing (top).** Likely proprietary; Phase 0 may end the TU-data path. 4B
  exists precisely for this.
- **Hidden inconsistency.** The national tiers may just re-encode state schemes;
  the Phase-1 consistency audit catches this.
- **UI creep.** 4 colors + 2 filters is the ceiling; resist adding more.
- **Premature teardown.** Keep the 22-state registry until a national base is
  proven — it's both the wild/native feeder and the fallback.

## 7. Recommendation
Run **Phase 0 only**, first, time-boxed. Everything downstream depends on it.
Don't touch the schema, the UI, or the registry until licensing + the
consistency audit are settled. If TU's data is usable, this is a big
consistency-and-coverage win in one move; if not, 4B reaches the same destination
on data we own — and the registry we've built is the head start either way.
