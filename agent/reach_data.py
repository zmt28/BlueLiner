"""Offline reach data for the prospecting agent.

Everything here comes from BUNDLED Blueliner data (no network):
  - candidate reaches + designation labels: data/nhdplus/clickable_streams.geojson.gz
    (103.6K reaches; properties comid/levelpathid/gnis_name/streamorder/lengthkm/
     trout_class). trout_class != null  ==>  a designated/known trout reach.
  - access tier: the app's access_points module (the national river-POI overlay;
    empty offline when neither a bundled overlay nor DATA_BASE_URL is present).

Topology note (documented limitation): the bundled NHDPlus VAA keeps no downstream
pointers, so we can't walk the flow network to prove "tributary of a trout
stream." Instead we approximate the topology signal with GEOMETRY PROXIMITY to
the nearest designated trout reach (shapely STRtree). That's offline, scalable
to a whole-region backtest, and explainable; exact flow-network tributary
topology via NLDI navigation is noted as an expansion (doesn't scale to 60K
reaches under rate limits).
"""

from __future__ import annotations

import gzip
import json
import math
from functools import lru_cache
from typing import Optional

from shapely import STRtree
from shapely.geometry import LineString, Point

import access_points
from states import point_in_state

from . import config

CLICKABLE = config.REPO_DIR / "data" / "nhdplus" / "clickable_streams.geojson.gz"
MIN_ORDER = 3                  # NHDPlus stream order floor for "holds fish year-round"
TOPO_NEAR_MI = 1.5             # within this of trout water -> strong topology signal
TOPO_FAR_MI = 6.0             # beyond this -> ~0 topology signal
_MI_PER_DEG = 69.0             # rough; fine for a decaying proximity score


def _centroid(coords) -> tuple[float, float]:
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    return sum(ys) / len(ys), sum(xs) / len(xs)   # (lat, lon)


@lru_cache(maxsize=1)
def _load() -> dict:
    """Parse the bundled reaches once. Returns {records, by_comid}."""
    with gzip.open(CLICKABLE) as f:
        gj = json.load(f)
    records, by_comid = [], {}
    for ft in gj["features"]:
        p = ft["properties"]
        geom = ft["geometry"]
        coords = geom["coordinates"]
        if geom["type"] == "MultiLineString":
            coords = [c for part in coords for c in part]
        if not coords or len(coords) < 2:
            continue
        lat, lon = _centroid(coords)
        rec = {
            "comid": int(p["comid"]),
            "levelpathid": p.get("levelpathid"),
            "gnis_name": p.get("gnis_name"),
            "streamorder": p.get("streamorder") or 0,
            "lengthkm": p.get("lengthkm") or 0.0,
            "trout_class": p.get("trout_class"),   # None => undesignated
            "lat": lat, "lon": lon,
            "coords": coords,
            "state": point_in_state(lat, lon),
        }
        records.append(rec)
        by_comid[rec["comid"]] = rec
    return {"records": records, "by_comid": by_comid}


def by_comid(comid: int) -> Optional[dict]:
    return _load()["by_comid"].get(int(comid))


@lru_cache(maxsize=8)
def _region(states: tuple[str, ...]) -> dict:
    """Reaches in a set of states + a topology index over designated reaches.

    Returns {reaches, designated_tree, designated_geoms, designated_recs}.
    """
    states = tuple(s.upper() for s in states)
    reaches = [r for r in _load()["records"] if r["state"] in states]
    designated_recs = [r for r in reaches if r["trout_class"]]
    designated_geoms = [LineString(r["coords"]) for r in designated_recs]
    tree = STRtree(designated_geoms) if designated_geoms else None
    return {"reaches": reaches, "designated_tree": tree,
            "designated_geoms": designated_geoms, "designated_recs": designated_recs}


# --------------------------------------------------------------------------
# Designation (with masking for the backtest)
# --------------------------------------------------------------------------
def designation_status(comid: int, masked: bool = False,
                       held_out: Optional[set] = None) -> dict:
    """Whether a reach is a designated/known trout water.

    `masked=True` + `held_out` hides the held-out designations so the agent
    sees them as undesignated (the backtest's whole premise)."""
    r = by_comid(comid)
    if r is None:
        return {"comid": comid, "designated": False, "trout_class": None,
                "source": "unknown"}
    designated = bool(r["trout_class"])
    if masked and held_out and comid in held_out:
        designated = False  # held out -> looks undesignated to the agent
    return {"comid": comid,
            "designated": designated,
            "trout_class": r["trout_class"] if designated else None,
            "source": "clickable_streams"}


# --------------------------------------------------------------------------
# Candidate pool
# --------------------------------------------------------------------------
def candidate_reaches(states, held_out: Optional[set] = None) -> list[dict]:
    """Reaches that LOOK undesignated to the agent: truly undesignated, plus any
    masked held-out designations. Filtered to fishable size (order >= MIN_ORDER
    or trout-tagged-but-held-out), and — crucially — with same-stream extensions
    of KNOWN trout water removed.

    Why drop same-stream extensions: a reach that shares a `levelpathid` with a
    designated reach is just another segment of a stream we already know is trout
    water. The map already renders that stream's designated sections (color is
    per-reach), so surfacing the untagged remainder isn't a discovery — it's
    restating the obvious, and at distance ~0 it would dominate the ranking and
    bury the genuinely novel leads (a DIFFERENT tributary near trout water).

    This uses the same masking unit as the backtest: in the held-out evaluation a
    whole stream's designation is masked by `levelpathid`, so it contributes no
    *visible* trout levelpath here and its reaches correctly STAY candidates —
    the agent must rediscover them via a different nearby trout stream, not via
    its own (hidden) designation. Production (no held-outs) excludes every
    same-stream extension."""
    held_out = held_out or set()
    reg = _region(tuple(states))
    # Levelpaths of trout water the agent is allowed to see (held-outs excluded).
    visible_trout_levelpaths = {
        r["levelpathid"] for r in reg["designated_recs"]
        if r["levelpathid"] is not None and r["comid"] not in held_out
    }
    out = []
    for r in reg["reaches"]:
        visible_designated = bool(r["trout_class"]) and r["comid"] not in held_out
        if visible_designated:
            continue  # the agent excludes already-known trout water
        if r["streamorder"] < MIN_ORDER and not r["trout_class"]:
            continue  # too small to reliably hold fish (and not trout-tagged)
        # Same-stream extension of visible trout water -> not a discovery. (A
        # held-out reach isn't dropped here: its whole stream is masked, so its
        # levelpath isn't in the visible set.)
        if r["comid"] not in held_out and r["levelpathid"] in visible_trout_levelpaths:
            continue
        out.append(r)
    return out


# --------------------------------------------------------------------------
# Topology signal (geometry proximity to designated trout water)
# --------------------------------------------------------------------------
def make_topology_index(states, held_out: Optional[frozenset] = None) -> dict:
    """Spatial index over the trout reaches the agent is ALLOWED to see. In the
    backtest, held_out designations are excluded so a masked reach can't be
    trivially 'near itself'."""
    reg = _region(tuple(states))
    if not held_out:
        return {"tree": reg["designated_tree"], "geoms": reg["designated_geoms"],
                "recs": reg["designated_recs"]}
    recs = [r for r in reg["designated_recs"] if r["comid"] not in held_out]
    geoms = [LineString(r["coords"]) for r in recs]
    return {"tree": STRtree(geoms) if geoms else None, "geoms": geoms, "recs": recs}


def topology_at(index: dict, lat: float, lon: float,
                gnis_name: Optional[str] = None) -> dict:
    tree = index["tree"]
    if tree is None:
        return {"nearest_trout": None, "distance_mi": None,
                "is_tributary_proxy": False, "same_named_as_trout": False}
    pt = Point(lon, lat)
    i = int(tree.nearest(pt))
    geom = index["geoms"][i]
    near = index["recs"][i]
    dist_mi = round(pt.distance(geom) * _MI_PER_DEG, 2)
    same = bool(gnis_name and near["gnis_name"] and gnis_name == near["gnis_name"])
    return {
        "nearest_trout": near["gnis_name"] or f"comid {near['comid']}",
        "nearest_trout_class": near["trout_class"],
        "distance_mi": dist_mi,
        "same_named_as_trout": same,
        "is_tributary_proxy": dist_mi <= TOPO_NEAR_MI or same,
    }


def topology(comid: int, states) -> dict:
    """Runtime topology for one reach (no masking) — the MCP-tool path."""
    r = by_comid(comid)
    if r is None:
        return {"comid": comid, "nearest_trout": None, "distance_mi": None,
                "is_tributary_proxy": False, "same_named_as_trout": False}
    out = topology_at(make_topology_index(states), r["lat"], r["lon"], r["gnis_name"])
    out["comid"] = comid
    return out


# --------------------------------------------------------------------------
# Access tier (binding actionability filter)
# --------------------------------------------------------------------------
def access_for(comid: int) -> dict:
    """Best public-access tier near a reach, from bundled access points."""
    r = by_comid(comid)
    if r is None or not r["state"]:
        return {"comid": comid, "access_tier": "unknown", "access_ok": False,
                "nearest_access_mi": None}
    pts = access_points.load_access_points(r["state"])
    near = access_points.nearby_access(r["lat"], r["lon"], pts, buffer_deg=0.05)
    if not near:
        return {"comid": comid, "access_tier": "unknown", "access_ok": False,
                "nearest_access_mi": None}
    # Best tier available nearby (public > permit > fee > private).
    rank = {"public": 0, "permit": 1, "fee": 2, "private_easement": 3, "private": 3}
    best = min(near, key=lambda a: rank.get(a.get("access"), 4))
    tier = best.get("access", "unknown")
    d_mi = round(_haversine_mi(r["lat"], r["lon"], best["lat"], best["lon"]), 2)
    return {"comid": comid, "access_tier": tier,
            "access_ok": tier in ("public", "permit", "fee"),
            "nearest_access_mi": d_mi, "access_point": best.get("name")}


def _haversine_mi(lat1, lon1, lat2, lon2) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))
