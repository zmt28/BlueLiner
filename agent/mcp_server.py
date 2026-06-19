"""FastMCP server exposing Blueliner's data + scorer as agent tools.

Run standalone over stdio (`python -m agent.mcp_server`); the agent launches it
as an MCP client. Each tool wraps existing Blueliner functionality and returns
structured JSON with a `source` field so the agent can cite every reading.

Tools:
  get_candidate_rivers   -- rivers near a point (Blueliner catalog + distance)
  get_river_conditions   -- current readings + the deterministic scorer verdict
  get_flow_history       -- today's historical median for a gauge (USGS)
  get_forecast           -- NOAA multi-day forecast
  get_access             -- access tier (feeds the legality guardrail)
  score_conditions       -- DETERMINISTIC scorer; also the eval oracle
  get_user_catch_history -- the signed-in angler's catch-log patterns (memory)
"""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from . import datasources, memory, reach_data
from .config import DEFAULT_RADIUS_MILES
from .scorer import score_conditions as _score
from .suitability import coldwater_suitability as _coldwater

mcp = FastMCP("blueliner")


@mcp.tool()
def get_candidate_rivers(lat: float, lng: float,
                         radius_miles: int = DEFAULT_RADIUS_MILES,
                         state: Optional[str] = None) -> list[dict]:
    """Rivers within `radius_miles` of (lat, lng), nearest first. Optional
    two-letter `state` filter. Each river has river_id, name, distance_miles,
    and its USGS gauge site numbers."""
    return datasources.get_candidate_rivers(lat, lng, radius_miles, state)


@mcp.tool()
def get_river_conditions(river_id: str) -> dict:
    """Current aggregated conditions for a river: flow (cfs), flow_vs_median_pct,
    water_temp_f, the scorer rating (green/yellow/red) with reasons, hatch,
    stocking, and how stale the reading is. Use the river_id from
    get_candidate_rivers."""
    return datasources.get_river_conditions(river_id)


@mcp.tool()
def get_flow_history(site_no: str) -> dict:
    """Today's historical median discharge (cfs) for a USGS gauge -- the
    baseline the flow rating is measured against."""
    return datasources.get_flow_history(site_no)


@mcp.tool()
def get_forecast(lat: float, lng: float, days: int = 3) -> dict:
    """NOAA daytime forecast for the next `days` days near (lat, lng): air temp,
    precip probability, and sky."""
    return datasources.get_forecast(lat, lng, days)


@mcp.tool()
def get_access(river_id: str) -> dict:
    """Public-access info for a river: access_tier (public/permit/fee/private),
    point count, and a public_access boolean. Private-only water must not be
    recommended."""
    return datasources.get_access(river_id)


@mcp.tool()
def score_conditions(water_temp_f: Optional[float] = None,
                     flow_cfs: Optional[float] = None,
                     median_cfs: Optional[float] = None) -> dict:
    """DETERMINISTIC fishing-condition scorer (Blueliner's production rules).
    Returns temp_state, flow_state, overall (green/yellow/red/gray), the flow
    ratio, and plain-English reasons. This is ground truth -- cite it rather
    than judging conditions yourself."""
    return _score(water_temp_f=water_temp_f, flow_cfs=flow_cfs, median_cfs=median_cfs)


@mcp.tool()
def get_user_catch_history(user_id: int) -> dict:
    """The signed-in angler's catch-log patterns: per species, the water-temp
    and flow-vs-median bands under which they actually caught fish, with sample
    sizes. Empty for new/anonymous users. Use to personalize ranking, never to
    override safety."""
    return memory.summarize_user_patterns(user_id)


# --- Prospector (discovery) tools -----------------------------------------
# Wrap the bundled NHDPlus/PAD-US/trout data for the discovery agent. Same MCP
# surface; the deterministic scorer/guardrails stay framework-free Python.
@mcp.tool()
def get_undesignated_reaches(state: str, limit: int = 200) -> list[dict]:
    """Candidate stream reaches in a state that are NOT designated trout water
    (order >= 3 or trout-tagged), pre-ranked by topology proximity to known
    trout water. Each: comid, gnis_name, streamorder, levelpathid, lat, lng."""
    reaches = reach_data.candidate_reaches((state,))
    idx = reach_data.make_topology_index((state,))
    scored = []
    for r in reaches:
        t = reach_data.topology_at(idx, r["lat"], r["lon"], r["gnis_name"])
        scored.append((t.get("distance_mi") if t["distance_mi"] is not None else 1e9, r, t))
    scored.sort(key=lambda x: x[0])
    return [{"comid": r["comid"], "gnis_name": r["gnis_name"],
             "streamorder": r["streamorder"], "levelpathid": r["levelpathid"],
             "lat": r["lat"], "lng": r["lon"],
             "nearest_trout_mi": t["distance_mi"], "source": "clickable_streams"}
            for _, r, t in scored[:limit]]


@mcp.tool()
def get_reach_topology(comid: int, state: str) -> dict:
    """Proximity of a reach to the nearest designated trout reach (a geometry
    proxy for 'tributary of / connected to known trout water'). Returns
    nearest_trout, distance_mi, same_named_as_trout, is_tributary_proxy."""
    return reach_data.topology(comid, state)


@mcp.tool()
def get_reach_access(comid: int) -> dict:
    """Public-access tier near a reach (public/permit/fee/private/unknown) from
    bundled access points. Binding actionability filter — known-private water is
    excluded; unknown access is surfaced with a verify-locally flag."""
    return reach_data.access_for(comid)


@mcp.tool()
def get_designation_status(comid: int, masked: bool = False) -> dict:
    """Whether a reach is already a designated/known trout water. `masked=True`
    hides held-out designations for the backtest."""
    return reach_data.designation_status(comid, masked=masked)


@mcp.tool()
def coldwater_suitability(comid: int, state: str,
                          water_temp_f: Optional[float] = None) -> dict:
    """DETERMINISTIC coldwater-suitability score (0-1) + calibrated confidence for
    a reach, combining topology, size, thermal, and access. Grounding tool +
    ranking baseline. Cite this rather than judging suitability yourself."""
    r = reach_data.by_comid(comid)
    if r is None:
        return {"comid": comid, "error": "unknown comid"}
    idx = reach_data.make_topology_index((state,))
    topo = reach_data.topology_at(idx, r["lat"], r["lon"], r["gnis_name"])
    access = reach_data.access_for(comid)
    flow = {"streamorder": r["streamorder"], "lengthkm": r["lengthkm"]}
    thermal = {"water_temp_f": water_temp_f, "gauged": water_temp_f is not None}
    return _coldwater(topo, flow, thermal, access, mode="full")


if __name__ == "__main__":
    mcp.run()
