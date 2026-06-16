"""Declarative trout-source registry + pure classification engine.

Single source of truth for which state layers feed the clickable-streams trout
tagging and how each maps to a `trout_class`. `build_clickable_streams.py`
iterates `load_sources()` and calls `row_bucket()`, so adding a state is a data
edit to `data/trout/sources.json`, not new Python -- and the discovery dossiers
(scripts/discovery) emit `draft_registry_entry` blocks in exactly this shape.

`row_bucket` is pure (no I/O), so it's unit-tested offline against the 10
already-shipped states to guarantee the registry reproduces the old per-state
functions byte-for-byte.

Wild vs stocked -- the nationwide classification principle:
  WILD (`wild_reproduction`)  the stream has documented natural reproduction,
                              native or not, EVEN IF also stocked. Test:
                              "are there wild-spawned trout here?"
  STOCKED (`stocked`)         pure put-and-take / put-grow-take, no meaningful
                              natural reproduction.
  Apply consistently across states. Two deliberate carve-outs:
    * Stricter on edges: where a class has only marginal/limited reproduction
      and the fishery is stocking-dependent (e.g. MI Type 3, MO Red Ribbon),
      keep it `stocked` unless the wild population is clearly self-supporting.
    * Western coverage: where a state publishes only a quality/biomass tier and
      no reproduction or native-origin data (WY/UT), treat the top "Blue Ribbon"
      tier as wild (de-facto wild fisheries) and drop the lower tiers rather
      than guess. Use real native/origin data (CO, MT) when available.

Source modes:
  single        whole layer -> one class                     {class}
  multi_layer   one class per sublayer                        {base, layers:[{id,class}]}
  field_map     exact value->class dict (unmapped dropped)    {field, map}
  field_prefix  ordered substring rules over coalesced fields {fields, rules:[{contains,bucket}]}
                (unmatched dropped)
  flags         any "Yes" flag field -> stocked, else default {stocked_flags, default}

Tier + filters (Phase 2): alongside `trout_class`, sources may carry an optional
quality TIER spec (`tier` / `tier_map` / `tier_rules` / `tier_default`, or per
multi_layer-sublayer `tier`) and a `native` flag. `row_tier()` / `layer_tier()`
derive gold/class1/class2/class3 (falling back from the class where unspecified);
`class_is_wild()` and `is_native()` derive the wild/native filters. See
docs/trout-tier-normalization-rubric.md.
"""
from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES_PATH = os.path.join(ROOT, "data", "trout", "sources.json")


def load_sources(path: str = SOURCES_PATH) -> list[dict]:
    """Load the ordered list of trout-source entries."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)["sources"]


def _prefix_bucket(value, rules: list[dict]) -> str | None:
    """First rule whose any-substring matches the lowercased value (NC PMTW)."""
    if not value:
        return None
    v = str(value).lower()
    for rule in rules:
        if any(tok in v for tok in rule["contains"]):
            return rule["bucket"]
    return None


def _is_yes(value) -> bool:
    return value is not None and str(value).strip().lower() == "yes"


def row_bucket(source: dict, attrs) -> str | None:
    """Map one feature's attributes to a `trout_class`, or None to drop it.

    `attrs` is any mapping (a dict, or a pandas row supporting .get) of
    field-name -> value. Mirrors the old fetch_trout_* per-row logic exactly:
      - field_map / field_prefix return None for unmatched rows (dropped on
        groupby, as before);
      - flags always returns a class (GA tagged every feature: stocked or the
        default wild).
    """
    mode = source["mode"]
    if mode == "single":
        return source["class"]
    if mode == "field_map":
        # `default` (optional): bucket for values not in the map (else None=drop).
        return source["map"].get(attrs.get(source["field"])) or source.get("default")
    if mode == "field_prefix":
        for field in source["fields"]:
            bucket = _prefix_bucket(attrs.get(field), source["rules"])
            if bucket:
                return bucket
        # `default` (optional): e.g. CT's FMA layer is wild only for "(Class 1)",
        # everything else -> stocked. Absent default -> None (drop), as for NC.
        return source.get("default")
    if mode == "flags":
        if any(_is_yes(attrs.get(col)) for col in source["stocked_flags"]):
            return "stocked"
        return source["default"]
    raise ValueError(f"unknown trout-source mode: {mode!r} ({source.get('state')})")


# ───────────── Tier + wild/native (Phase 2 nationwide model) ─────────────
# Quality TIER (gold/class1/class2/class3) is the color axis; `wild` and
# `native` are orthogonal filters. An explicit per-source tier spec wins; else
# tier falls back from the trout_class. The eastern-gold upgrade (premier-wild on
# a named river of stream order >= 4) needs reach attributes and is applied in
# build_clickable_streams.py -- see docs/trout-tier-normalization-rubric.md.
WILD_CLASSES = {"wild_reproduction", "class_a", "wilderness"}
FALLBACK_CLASS_TIER = {
    "class_a": "class1", "wilderness": "class1",
    "wild_reproduction": "class2", "stocked": "class3", "designated": "class3",
}

# ───────────── River-level coherence (best-class-wins) ─────────────
# A single named river often carries several per-reach designations: MD's
# Des_Use codes a famous continuous trout river like the Gunpowder as a
# patchwork of III-P (wild), IV (stocked) and I-P (warmwater, dropped) reaches,
# so the map fragments green/blue/grey along one river. The build harmonizes
# each (levelpathid, gnis_name) group to its STRONGEST reach so the whole named
# river renders as one class/tier.
#
# CLASS_PRECEDENCE (strongest -> weakest) mirrors reach_trout.CLASS_PRECEDENCE
# -- the panel chip's "strongest class wins per river" rule -- so the rendered
# lines and the panel header agree. TIER_PRECEDENCE is the color axis; within
# the winning class the best tier wins, so a wild river the size ladder split
# into class1 (green) and class2 (blue) reaches paints uniformly.
CLASS_PRECEDENCE = ["class_a", "wilderness", "wild_reproduction",
                    "designated", "stocked"]
_CLASS_RANK = {c: i for i, c in enumerate(CLASS_PRECEDENCE)}
TIER_PRECEDENCE = ["gold", "class1", "class2", "class3"]
_TIER_RANK = {t: i for i, t in enumerate(TIER_PRECEDENCE)}


def reach_priority(trout_class, tier) -> tuple[int, int]:
    """Sort key for river-level coherence -- LOWER is stronger. Primary axis is
    the trout_class (wild designations outrank stocked, mirroring the panel
    chip); secondary is the tier color rank. Unclassified/None sorts weakest on
    both axes. Pure."""
    return (_CLASS_RANK.get(trout_class, len(CLASS_PRECEDENCE)),
            _TIER_RANK.get(tier, len(TIER_PRECEDENCE)))


def strongest_reach(reaches):
    """Best (trout_class, tier) among an iterable of (trout_class, tier) pairs
    by reach_priority, or None when every reach is unclassified (class None).
    Pure -- the build groups a river's reaches and promotes them all to this."""
    best = None
    best_key = None
    for cls, tier in reaches:
        if cls is None:
            continue
        key = reach_priority(cls, tier)
        if best_key is None or key < best_key:
            best, best_key = (cls, tier), key
    return best


def class_is_wild(trout_class) -> bool:
    """`is_wild` flag from a final trout_class (uniform across all modes)."""
    return trout_class in WILD_CLASSES


def _prefix_pick(value, rules: list[dict], key: str) -> str | None:
    """First rule whose any-substring matches the lowercased value -> rule[key]."""
    if not value:
        return None
    v = str(value).lower()
    for rule in rules:
        if any(tok in v for tok in rule["contains"]):
            return rule.get(key)
    return None


def row_tier(source: dict, attrs) -> str | None:
    """Quality tier for one feature, or None to drop. Explicit per-source tier
    spec wins (`tier` / `tier_map` / `tier_rules` / `tier_default`); otherwise
    fall back from the trout_class. multi_layer tiers are per-sublayer -- use
    layer_tier()."""
    mode = source["mode"]
    if mode == "single":
        return source.get("tier") or FALLBACK_CLASS_TIER.get(source["class"])
    if mode == "field_map":
        tm = source.get("tier_map")
        if tm is not None:
            t = tm.get(attrs.get(source["field"]))
            if t:
                return t
            if source.get("tier_default"):
                return source["tier_default"]
        return FALLBACK_CLASS_TIER.get(row_bucket(source, attrs))
    if mode == "field_prefix":
        if "tier_rules" in source:
            for field in source["fields"]:
                t = _prefix_pick(attrs.get(field), source["tier_rules"], "tier")
                if t:
                    return t
            if source.get("tier_default"):
                return source["tier_default"]
        return FALLBACK_CLASS_TIER.get(row_bucket(source, attrs))
    if mode == "flags":
        return FALLBACK_CLASS_TIER.get(row_bucket(source, attrs))
    if mode == "multi_layer":
        return None
    raise ValueError(f"unknown trout-source mode: {mode!r} ({source.get('state')})")


def layer_tier(layer: dict) -> str | None:
    """multi_layer per-sublayer tier: explicit `tier` or fallback from `class`."""
    return layer.get("tier") or FALLBACK_CLASS_TIER.get(layer["class"])


def is_native(source: dict, layer: dict | None = None) -> bool:
    """`native` filter flag -- a property of the layer/source, not the class
    value. For multi_layer pass the sublayer; else honors source-level `native`."""
    if layer is not None and "native" in layer:
        return bool(layer["native"])
    return bool(source.get("native", False))


def refine_tier(base_tier, is_wild, gnis_name, streamorder,
                class1_min_order: int = 3, gold_min_order: int = 4):
    """Size-based tier refinement for WILD reaches (rubric §5.1(ii) + §5.3), using
    the NHDPlus gnis_name/streamorder the build carries. One consistent rule --
    bigger named wild water ranks higher:
      * generic wild (base `class2`) on a named river of order >= class1_min_order
        -> `class1`;
      * DESIGNATED premier-wild (base `class1`) on a named river of order >=
        gold_min_order -> `gold` (eastern-gold).
    Gold is gated on *base* class1, so a size-promoted class2->class1 reach does
    NOT become gold -- only state-designated premier waters do. Non-wild,
    unnamed, and stocked reaches are unchanged. Pure."""
    if not (is_wild and gnis_name and streamorder is not None):
        return base_tier
    if base_tier == "class1" and streamorder >= gold_min_order:
        return "gold"
    if base_tier == "class2" and streamorder >= class1_min_order:
        return "class1"
    return base_tier


def classify_fields(source: dict) -> list[str]:
    """The attribute fields a multi-bucket source classifies on (for the
    'field absent -> skip the state' guard in the builder)."""
    if source["mode"] == "field_map":
        return [source["field"]]
    if source["mode"] == "field_prefix":
        return list(source["fields"])
    return []
