# Trout Coverage Expansion Plan: fewer false "Unclassified" streams

Motivating example (Ithaca NY): TroutRoutes classes Cayuga Inlet (Class 1),
Fall Creek and Six Mile Creek (Class 2); BlueLiner shows all three
Unclassified, and Fall Creek's designation chip flips along its own length.
Goal: nationally, streams that demonstrably hold trout should carry a class;
"Unclassified" should mean "no evidence", not "no source consulted". Do NOT
mirror TroutRoutes stream-by-stream — expand evidence sources.

## Root causes (from this codebase)

1. **Designation != distribution.** Our state sources are mostly
   *management* layers (NY DEC's Inland Trout Stream Fishing = managed
   reaches only). Rivers that hold wild trout but aren't specially managed
   never match. TroutRoutes layers in *distribution/survey* data.
2. **Per-COMID tagging with no continuity.** `build_clickable_streams.py`
   tags each flowline independently; a creek matched on 3 of 30 segments
   shows a flickering chip (the Fall Creek symptom).
3. **EBTJV is in the build but catchment-based** — only flowlines inside
   brook-trout-occupied catchments tag; brown/rainbow wild fisheries in the
   East (Fall Creek is one) get nothing from it.

## Workstreams

### W1 — Add fish-distribution sources per region (biggest win)
New registry entries in `data/trout/sources.json` (modes already support
this; verify every endpoint via the existing CI prober):
- **NY**: DEC publishes statewide trout-survey/distribution datasets beyond
  the management layer (e.g. "Trout Streams" classification inventory on
  data.gis.ny.gov, the org we already verified for PFR). Discovery round via
  `scripts/gis_verify_request.txt` (`states: NY` + keyword tweak for
  "distribution", "survey", "classified").
- **East-wide**: EBTJV "wild trout community" catchments beyond
  brook-trout-only classes (we currently keep only EBT-present classes —
  revisit dropping 'Wild trout' species-unspecified, which is exactly the
  brown-trout water we're missing). One-line map change in sources.json.
- **West**: StreamNet Fish_Dist has more species layers (redband done;
  add Oncorhynchus mykiss / brown trout distribution layers where present).
- **State gaps with known leads**: MN designated trout streams
  (SLAM layer 49), NH (FishStocking org has survey layers), TN, AR, OK, AL,
  WV wild-trout — run prober rounds with `TROUT`-keyword focus.
- Classification rule: distribution-only evidence maps to a *weaker* class
  (class2/"trout present") so it never outranks an agency designation —
  precedence order in sources.json already gives state sources first-writer.

### W2 — Name/levelpath continuity smoothing (fixes chip flapping)
In the build (step after per-COMID tagging): for each (gnis_name,
levelpathid) group where >=N% of length is tagged, extend the dominant
trout_class to the group's untagged flowlines, flagged `inferred: true`
(popup copy: "Trout water (inferred along reach)"). Conservative threshold
(e.g. 40% by length) + never infer across class boundaries (keep the
strongest contiguous class). This alone fixes Fall Creek's mixed chips.

### W3 — River-level chip in the panel
The panel header chips should describe the RIVER, not the clicked pixel:
derive "Trout water" from any tagged reach in the levelpath group, and
label access as "5 access points nearby" (it already is proximity-based —
just make the copy say so). Small main.py/popup change; removes the
remaining user-visible inconsistency even before W2 ships.

### W4 — Measurement
Add a coverage report to the build: per state, % of gauged rivers and % of
network length carrying a trout_class, before/after. Spot-check list
(Cayuga Inlet, Fall Creek, Six Mile Creek + 2-3 per region) asserted in
tests like the existing PA wild-trout test.

## Sequencing
1. W3 (panel copy/derivation — no rebuild needed)
2. W1 NY + EBTJV revisit -> data rebuild via data-build.yml -> verify the
   three Ithaca examples
3. W2 smoothing + W4 report in the same rebuild cycle
4. W1 remaining states in prober rounds (reuse the stocking/access CI loop)
