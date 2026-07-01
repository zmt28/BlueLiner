"""River-level trout-class index over the bundled clickable-streams data.

The panel header's "Trout water" chip must describe the RIVER, not the
clicked pixel (W3 of docs/trout-coverage-expansion-plan.md): a creek
tagged on 3 of its 30 flowlines should read as trout water anywhere along
it. This module answers "does ANY flowline in this river's levelpath
group carry a trout_class, and which is the strongest?" from an in-memory
index built once over data/nhdplus/clickable_streams.geojson.gz.

Index shape (built once, then O(1) per lookup -- no per-request scans):
    levelpathid -> strongest trout_class on any flowline of that levelpath
    normalized gnis_name -> strongest trout_class under that name

Lookup precedence mirrors the client's reach matching: levelpathid first
(the durable NHD identity), normalized name only as a fallback when no
levelpath evidence exists. The name index is national, so a generic name
("Mill Creek") can collide across states -- acceptable for a fallback,
which is why levelpath evidence always wins.

Memory: only the strongest class per group is kept -- ~15K levelpathid
entries + ~4K name entries, a few MB total. The build streams the gzip in
chunks and regex-extracts the flat "properties" objects, so it never
materializes the ~49 MB document or its geometries (json.load of the full
bundle peaks ~270 MB -- too much headroom to burn on the free tier).
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import re
import threading

import data_source

logger = logging.getLogger("blueliner.reach_trout")

_BUNDLED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "data", "nhdplus", "clickable_streams.geojson.gz")
_BUNDLE_NAME = "clickable_streams.geojson.gz"

# Strongest -> weakest. Drives "strongest class wins" when a river carries
# several designations along its length. Wild designations outrank
# managed/stocked ones; mirrors WILD_CLASSES in scripts/trout_registry.py.
CLASS_PRECEDENCE = ["class_a", "wilderness", "wild_reproduction",
                    "designated", "stocked"]
_CLASS_RANK = {c: i for i, c in enumerate(CLASS_PRECEDENCE)}
_WEAKEST = len(CLASS_PRECEDENCE)

# Full agency-designation labels (matches STREAM_CLASS_LABEL in streams.ts).
CLASS_LABEL = {
    "class_a": "Class A wild trout",
    "wilderness": "Wilderness trout",
    "wild_reproduction": "Wild reproduction",
    "designated": "Designated trout",
    "stocked": "Stocked trout",
}

# The nationwide quality TIER is the axis the map colors streams by (green =
# class1, blue = class2, ...); it's a separate signal from the agency
# trout_class. Strongest-first, mirroring ALL_TIERS / TIER_LABEL in streams.ts.
TIER_PRECEDENCE = ["gold", "class1", "class2", "class3", "unclassified"]
_TIER_RANK = {t: i for i, t in enumerate(TIER_PRECEDENCE)}
_WEAKEST_TIER = len(TIER_PRECEDENCE)
# Short chip labels (the map legend uses longer "Class 1 — high quality" copy).
TIER_LABEL = {
    "gold": "Gold",
    "class1": "Class 1",
    "class2": "Class 2",
    "class3": "Class 3",
}

# Flat properties objects only (no nested braces), so non-greedy to the
# first "}" is exact. DOTALL in case a writer ever pretty-prints.
_PROPS_RE = re.compile(r'"properties"\s*:\s*(\{.*?\})', re.S)
_CHUNK = 4 * 1024 * 1024   # stream the gzip in 4 MB text chunks
_OVERLAP = 8 * 1024        # carry-over so no properties blob is split

_lock = threading.Lock()
# (by_levelpathid, by_norm_name) once built; None until first use.
_index: tuple[dict[int, str], dict[str, str]] | None = None
# Parallel tier index (by_levelpathid, by_norm_name), built in the same scan.
_tier_index: tuple[dict[int, str], dict[str, str]] | None = None


def _norm_name(s) -> str | None:
    """Normalized stream name, or None for null-ish placeholders. Mirrors
    _cleanName/_normName in streams.ts ("nan" is a stringified pandas NaN
    from the build -- unnamed reaches must not collapse into one name)."""
    n = (str(s) if s is not None else "").strip().lower()
    if not n or n in ("nan", "none"):
        return None
    return n


def _note(table: dict, key, val: str, rank: dict) -> None:
    """Keep the strongest value seen for `key` under `rank` (idempotent, so
    re-seeing a feature in a chunk-overlap region is harmless)."""
    weakest = len(rank)
    if rank.get(val, weakest) < rank.get(table.get(key), weakest):
        table[key] = val


def _scan(path: str):
    """Scan a clickable-streams geojson.gz into the class + tier lookup tables:
    (cls_by_lpid, cls_by_name, tier_by_lpid, tier_by_name). Pure w.r.t. module
    state (tests point it at synthetic files)."""
    cls_lpid: dict[int, str] = {}
    cls_name: dict[str, str] = {}
    tier_lpid: dict[int, str] = {}
    tier_name: dict[str, str] = {}
    if not os.path.exists(path):
        logger.warning("clickable-streams bundle missing at %s; "
                       "river-level trout chip disabled", path)
        return cls_lpid, cls_name, tier_lpid, tier_name
    carry = ""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            buf = carry + chunk
            for m in _PROPS_RE.finditer(buf):
                try:
                    props = json.loads(m.group(1))
                except ValueError:
                    continue
                lpid = props.get("levelpathid")
                lpid = lpid if isinstance(lpid, int) else None
                name = _norm_name(props.get("gnis_name"))
                cls = props.get("trout_class")
                if cls in _CLASS_RANK:
                    if lpid is not None:
                        _note(cls_lpid, lpid, cls, _CLASS_RANK)
                    if name:
                        _note(cls_name, name, cls, _CLASS_RANK)
                tier = props.get("tier")
                if tier in _TIER_RANK:
                    if lpid is not None:
                        _note(tier_lpid, lpid, tier, _TIER_RANK)
                    if name:
                        _note(tier_name, name, tier, _TIER_RANK)
            carry = buf[-_OVERLAP:]
    logger.info("river trout index: %d levelpaths (%d tiered), %d names",
                len(cls_lpid), len(tier_lpid), len(cls_name))
    return cls_lpid, cls_name, tier_lpid, tier_name


def build_index(path: str) -> tuple[dict[int, str], dict[str, str]]:
    """Back-compat: just the trout_class tables (cls_by_lpid, cls_by_name)."""
    cls_lpid, cls_name, _, _ = _scan(path)
    return cls_lpid, cls_name


def ensure_loaded() -> None:
    """Build the class + tier indexes once (thread-safe). Call off the event
    loop -- the scan takes a couple of seconds on first use; no-op afterwards."""
    global _index, _tier_index
    if _index is not None:
        return
    with _lock:
        if _index is None:
            path = data_source.resolve_data_file(_BUNDLED_PATH, _BUNDLE_NAME)
            cls_lpid, cls_name, tier_lpid, tier_name = _scan(path)
            _index = (cls_lpid, cls_name)
            _tier_index = (tier_lpid, tier_name)


def _strongest(index, rank, levelpathids, name):
    """Strongest value in `index` (by_lpid, by_name) across the river's
    levelpath group; falls back to the normalized name when the group has no
    evidence. Shared by river_trout_class + river_tier."""
    by_lpid, by_name = index
    best = None
    for lp in levelpathids or ():
        try:
            v = by_lpid.get(int(lp))
        except (TypeError, ValueError):
            continue
        if v is not None and (best is None or rank[v] < rank[best]):
            best = v
    if best is not None:
        return best
    norm = _norm_name(name)
    return by_name.get(norm) if norm else None


def river_trout_class(levelpathids=None, name: str | None = None) -> str | None:
    """Strongest trout_class anywhere on the river: any flowline sharing
    one of `levelpathids` wins; the normalized `name` is consulted only
    when the levelpath group carries no evidence."""
    ensure_loaded()
    return _strongest(_index, _CLASS_RANK, levelpathids, name)


def river_tier(levelpathids=None, name: str | None = None) -> str | None:
    """Strongest nationwide quality TIER on the river (the map's color axis) --
    same levelpath-group-then-name lookup as river_trout_class."""
    ensure_loaded()
    return _strongest(_tier_index, _TIER_RANK, levelpathids, name)
