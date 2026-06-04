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


def classify_fields(source: dict) -> list[str]:
    """The attribute fields a multi-bucket source classifies on (for the
    'field absent -> skip the state' guard in the builder)."""
    if source["mode"] == "field_map":
        return [source["field"]]
    if source["mode"] == "field_prefix":
        return list(source["fields"])
    return []
