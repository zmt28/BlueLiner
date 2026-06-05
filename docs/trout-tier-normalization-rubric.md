# Trout tier-normalization rubric — DRAFT v0.1 (4B / Phase 1)

Keystone artifact for the nationwide pivot. Defines how every state's native
classification vocabulary maps to one consistent 4-tier scheme
(`gold` / `class1` / `class2` / `class3`), plus how the orthogonal `wild` and
`native` filters are derived. This is the "logic" the guiding principle is about:
one rubric nationwide; per-state exceptions only where coverage demands, each
documented.

> **Status: draft for review.** Sections marked **[CALL]** are judgment calls for
> sign-off before this becomes a spec. This mirrors how TroutRoutes built theirs
> ("roll all the regional/state classification systems into a single format") —
> but on public/owned inputs.

## 1. The three axes (keep them separate)

| Axis | What it answers | UI role | Source |
|---|---|---|---|
| **Tier** (`gold`/`class1`/`class2`/`class3`) | How good/prestigious is the fishery? | **stream color** | this rubric, over state class + length + access |
| **`wild`** | Naturally-reproducing trout present? | **filter** | existing 22-state registry (`wild_reproduction`/`class_a`/`wilderness`) |
| **`native`** | Native species present? | **filter** | TU CC-BY portfolios (EBTJV etc.) + CO/CA/NV native layers |

The tier is a **quality axis, not a wild/stocked axis.** A famous wild spring
creek and a famous stocked tailwater can both be `gold`; `wild` then
distinguishes them as a filter. Keeping these orthogonal is what makes the logic
consistent (it's why our old 2-bucket model kept colliding with western quality
data).

## 2. Tier definitions

- **`gold`** — premier, renowned water; the exclusive top tier (cf. TroutRoutes'
  "top few %"). Assigned two ways (see §5.1): an explicit state premier
  designation, **or** the eastern-gold criterion for states that don't designate
  one.
- **`class1`** — high-quality named trout water; strong fishery, good reputation.
- **`class2`** — solid everyday trout water.
- **`class3`** — lighter / overlooked / marginal or pure put-and-take water.

## 3. Mapping principle

States give us one of two input types:

- **Type A — explicit quality/biomass tier** (CO Gold Medal; WY Blue/Red/Yellow/
  Green; UT Blue Ribbon; MT biomass classes). **Map the tier directly** — this is
  the cleanest, most consistent case.
- **Type B — wild/stocked or regulation classes** (most of the East/Midwest). No
  native quality ranking, so **derive** a tier from `(state class) + length +
  access`, per the defaults below.

### Type-B default ladder
| State class character | Tier |
|---|---|
| Explicit premier designation, or top-wild on a named river (order ≥ 4) — see §5.1 | `gold` |
| Premier wild (Class A, Wilderness, Heritage, catch-&-release wild) | `class1` |
| Ordinary wild reproduction | `class2` |
| Stocked put-grow-take / delayed harvest (popular, good access) | `class2` |
| Stocked put-and-take / marginal reproduction | `class3` |

Modifiers (where data exists): **+1 tier** for high public access (our
access-points layer) and/or substantial length; small isolated reaches trend
toward `class3`.

## 4. Worked mapping — the 22 onboarded states

Type A (direct):
| State | Native class → tier |
|---|---|
| CO | Gold Medal → `gold`; (native conservation/sportfish carry `wild`/`native`, tier via §5.2) |
| WY | Blue Ribbon → `gold` (Red/Yellow/Green → `class1`/`2`/`3` when ingested) |
| UT | Blue Ribbon → `gold` |
| CA | Heritage → `gold` (explicit premier); Wild Trout → `class1` |

Type B (derived):
| State | Class → tier |
|---|---|
| PA | Class A/Wilderness → `class1`; Natural Repro → `class2`; Stocked → `class3` |
| WI | Class I → `class1`; Class II → `class2`; Class III → `class3` |
| MI | Type 1 → `class1`; Type 2 → `class2`; Type 3/4 → `class3` |
| NC | Wild + C&R → `class1`; Hatchery Supported → `class2`; Delayed Harvest → `class2` |
| NY | Wild-Premier → `class1`; Wild-Quality → `class2`; Stocked/-Extended → `class3` |
| GA | (wild default) → `class2`; Heavily Stocked/Delayed Harvest → `class3`/`class2` |
| VA/MA/VT/ME | wild designation → `class1`/`class2` |
| NJ/WV/MD | stocked/designated → `class3`/`class2` |
| CT | WTMA Class 1 → `class1`; stocked → `class3` |
| IA | wild_trt present → `class2`; blank (stocked) → `class3` |
| MO | Blue Ribbon → `gold` (MO's explicit premier designation); Red → `class2`; White/Park → `class3` |
| SC | w/cr → `class1`; dh/pt/pg → `class2`/`class3` |
| NV | Lahontan native streams → `class2` (conservation, `native`+`wild` flags) |

## 5. Open judgment calls **[CALL]** — need sign-off

1. **Eastern "gold" — RESOLVED: principled eastern-gold rule.** A reach is `gold`
   if **either**:
   - **(i) Explicit premier designation** — the state vouches for it: CO Gold
     Medal, WY/UT/MT Blue Ribbon, CA Heritage Trout Waters, and any state
     "Trophy / Heritage / Premier Trout" sub-designation. Size-independent
     (mirrors the West: the state already named it the top tier).
   - **(ii) Premier-wild on a substantial water** (eastern fallback, for states
     with no premier sub-designation) — the reach carries the state's **top wild
     class** (`class_a`, `wilderness`, top `wild_reproduction`) **and** is a
     **named river** (NHDPlus `gnis_name` present) of **stream order ≥ 4**. The
     size gate keeps `gold` exclusive — the prominent rivers (a PA Class A on a
     major limestone river), not every headwater Class A trickle (those stay
     `class1`).

   Consistent nationwide ("gold = the state's premier water, by designation or by
   top-wild-on-a-real-river") and uses only NHDPlus attributes we already carry
   (`gnis_name`, `streamorder`). **Tunable:** the order threshold and the
   require-a-name flag are calibration knobs — run it, check the gold fraction
   against the "top few %" target, adjust.
2. **Tier vs. wild independence.** Should `wild` ever force a tier floor (e.g.,
   wild ⇒ ≥ `class2`), or stay fully independent? **Recommend independent** — a
   marginal wild trickle can be `class3`; wild is the filter.
3. **Access/length weighting — RESOLVED: size ladder.** Calibration (VPU 02)
   showed designation alone leaves `class1` starved (generic wild all fell to
   `class2`). Fix (`trout_registry.refine_tier`, applied in the build with the
   NHDPlus gnis_name/streamorder): a consistent "bigger named wild water ranks
   higher" rule -- generic wild (`class2`) on a named river of order >= 3 ->
   `class1`; designated premier-wild (`class1`) on order >= 4 -> `gold`. Gold
   stays gated on *base* class1 so size-promoted reaches don't inflate it. The
   two order thresholds are the tunable knobs; access-point weighting is deferred
   (we have the layer; not needed for a sane v0.1 spread).
4. **CO/NV-type native-only data.** CO's native-conservation and NV's Lahontan
   layers tell us `wild`/`native` but not a quality tier. Default them to
   `class2`, or leave tier null (uncolored until a quality source covers them)?

## 6. Honest limitation

Without TroutRoutes' proprietary access/biomass composite, our **Type-B tiers are
coarser** — driven by state class + length + our access points. The eastern tier
assignments will be approximate, not a clone of TroutRoutes'. That's an
acceptable, documented coverage compromise: **consistency of *logic* is the
goal, not pixel-matching their product.** Type-A (western) tiers will be precise
because the states publish the ranking directly.

## 7. Wild & native filter derivations (nationwide)

- **`wild`** = `trout_class ∈ {wild_reproduction, class_a, wilderness}` from the
  registry, where we have it; backfilled across the eastern brook-trout range by
  TU's EBTJV portfolio (CC-BY).
- **`native`** = membership in a native-species layer: TU CC-BY conservation
  portfolios (Eastern + Great Lakes brook trout; Western Native Trout / Steelhead
  atlases) + our CO cutthroat / CA heritage / NV Lahontan layers.

## 8. TU public-portal characterization (Phase 1)

- **Org:** `trout.maps.arcgis.com` (ArcGIS Online), public hub at
  `mapping-trout.opendata.arcgis.com`. Datasets downloadable as GeoJSON / KML /
  Shapefile / CSV with GeoServices / WMS / WFS APIs.
- **License:** at least the **Eastern Brook Trout Conservation Portfolio**
  (TU + NFWF + EBTJV) is **CC-BY 3.0** — legal to use with attribution. Confirm
  per-layer licensing as each is ingested.
- **Coverage:** brook-trout conservation portfolios for the **Eastern** and
  **Great Lakes** ranges (native brook trout); TU also publishes Western Native
  Trout / Steelhead atlases — together a near-national native backbone.
- **Caveat:** the discoverable items are **web apps / web maps**, not direct
  feature services. When we build the `native` filter we'll pull each app's
  underlying FeatureServer (via the probe tooling) and verify per-layer license +
  NHD/COMID linkage. Sufficient for Phase 1: the backbone **exists, is public,
  and is CC-licensed.**

## 9. Next
- Resolve the §5 **[CALL]s** with sign-off (esp. §5.1 eastern-gold — the keystone
  consistency decision).
- Then promote this draft to a spec and extend `trout_registry` with a `tier`
  output alongside `trout_class`, and wire the TU native layers as the `native`
  feeder.
