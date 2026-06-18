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

from . import datasources, memory
from .config import DEFAULT_RADIUS_MILES
from .scorer import score_conditions as _score

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


if __name__ == "__main__":
    mcp.run()
