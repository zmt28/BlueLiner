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

- **`gold`** — premier, nationally/regionally renowned water. The top tier a
  state recognizes.
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
| State's flagship/premier wild designation on a large named river | `gold` **[CALL — see §5.1]** |
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
| CA | Heritage → `gold` **[CALL]**, Wild Trout → `class1` |

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
| MO | Blue Ribbon → `class1` **[CALL — gold? see §5.1]**; Red/White/Park → `class2`/`class3` |
| SC | w/cr → `class1`; dh/pt/pg → `class2`/`class3` |
| NV | Lahontan native streams → `class2` (conservation, `native`+`wild` flags) |

## 5. Open judgment calls **[CALL]** — need sign-off

1. **Eastern "gold".** "Gold Medal/Blue Ribbon" is a *western* term; eastern
   states don't designate it. A "use the state's gold designation" rule gives the
   entire East **zero** gold-tier streams — inconsistent nationwide. Options:
   (a) define a principled eastern gold criterion (e.g., the state's single
   highest wild class on a large named river → gold: PA Class A on a major
   limestone river, the famous spring creeks); (b) accept that `gold` is
   western-only and the East tops out at `class1`; (c) drop `gold` entirely and
   use 3 tiers. **Recommend (a)** for true nationwide consistency.
2. **Tier vs. wild independence.** Should `wild` ever force a tier floor (e.g.,
   wild ⇒ ≥ `class2`), or stay fully independent? **Recommend independent** — a
   marginal wild trickle can be `class3`; wild is the filter.
3. **Access/length weighting.** How much should our access-points layer + reach
   length move the tier, vs. just using the state class? (TroutRoutes weighs
   access heavily; we have access points but not their proprietary composite.)
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
