"""Gated demo API for showing the agents inside the BlueLiner UI — LOCAL ONLY.

Security posture (the whole point of this file):
  * This router is NOT mounted by the public app. `main:app` — what Render runs —
    never imports it, has no agent dependencies, and has no ANTHROPIC_API_KEY.
    The ONLY way these endpoints exist is via `agent/demo_server.py`, a separate
    entry point you run on your own machine.
  * Even when mounted, every endpoint is dead unless AGENT_DEMO_ENABLED=1, and
    can be further locked behind AGENT_DEMO_TOKEN (a shared secret the panel
    sends on every call). So a stray deploy of demo_server with the flag unset
    still exposes nothing.
  * Net effect: the API key lives only in your local shell env, is reachable
    only from your local server, and the public site can never spend it.

Handlers are plain `def` (not async): FastAPI runs them in a worker thread with
no running event loop, so `plan_trip`/`run_discovery` calling `asyncio.run(...)`
internally is safe. Agent imports are lazy (inside handlers) so importing this
module never drags in mcp/anthropic/langgraph.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/agent", tags=["agent-demo"])


# --------------------------------------------------------------------------
# Gating
# --------------------------------------------------------------------------
def _enabled() -> bool:
    return os.environ.get("AGENT_DEMO_ENABLED", "").strip() in ("1", "true", "yes")


def _check_token(token: Optional[str]) -> None:
    """If AGENT_DEMO_TOKEN is set, require a matching X-Agent-Demo-Token header.
    Unset -> no token required (fine for a localhost-only demo)."""
    expected = os.environ.get("AGENT_DEMO_TOKEN", "").strip()
    if expected and (token or "").strip() != expected:
        raise HTTPException(status_code=401, detail="bad or missing demo token")


def _guard(token: Optional[str]) -> None:
    if not _enabled():
        # 403, not 404: the route exists here, it's just switched off.
        raise HTTPException(status_code=403, detail="agent demo disabled")
    _check_token(token)


# --------------------------------------------------------------------------
# Request bodies
# --------------------------------------------------------------------------
class PlanBody(BaseModel):
    lat: float
    lng: float
    state: Optional[str] = None
    dates: Optional[str] = None
    preferences: str = ""
    user_id: Optional[int] = None
    text: str = ""
    version: int = 3
    orchestrator: str = "hand"


class DiscoverBody(BaseModel):
    states: str = "MD"          # comma-separated, e.g. "MD,PA"
    shortlist_k: int = 8


# --------------------------------------------------------------------------
# Health — the self-gating signal the frontend reads before rendering anything
# --------------------------------------------------------------------------
@router.get("/health")
def health():
    """Always answers. When disabled, says so and leaks nothing else; the panel
    stays hidden. On the public deployment this route doesn't exist at all (the
    router isn't mounted), so the fetch 404s and the panel also stays hidden."""
    if not _enabled():
        return {"enabled": False}
    from . import config
    return {
        "enabled": True,
        "has_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "token_required": bool(os.environ.get("AGENT_DEMO_TOKEN", "").strip()),
        "agents": ["trip_planner", "prospector"],
        "models": {"cheap": config.CHEAP_MODEL, "strong": config.STRONG_MODEL},
    }


# --------------------------------------------------------------------------
# Trip planner
# --------------------------------------------------------------------------
@router.post("/plan")
def plan(body: PlanBody, x_agent_demo_token: Optional[str] = Header(default=None)):
    _guard(x_agent_demo_token)
    from .agent import TripRequest, plan_trip
    from . import datasources

    req = TripRequest(lat=body.lat, lng=body.lng, state=body.state,
                      dates=body.dates, preferences=body.preferences,
                      user_id=body.user_id, text=body.text)
    result = plan_trip(req, version=body.version, orchestrator=body.orchestrator)

    # Enrich recs + blocks with map coordinates from the catalog (no network).
    cat = datasources._catalog()

    def _coords(river_id: str) -> dict:
        r = cat.get(river_id)
        if not r:
            return {}
        return {"lat": r["lat"], "lng": r["lng"], "state": r["state"]}

    for rec in result.get("recommendations", []):
        rec.update(_coords(rec.get("river_id", "")))
    for blk in result.get("blocked", []):
        blk.update(_coords(blk.get("river_id", "")))
    return result


# --------------------------------------------------------------------------
# Prospector
# --------------------------------------------------------------------------
@router.post("/discover")
def discover(body: DiscoverBody, x_agent_demo_token: Optional[str] = Header(default=None)):
    _guard(x_agent_demo_token)
    from . import prospector_graph, reach_data

    states = tuple(s.strip().upper() for s in body.states.split(",") if s.strip())
    final = prospector_graph.run_discovery(states, shortlist_k=body.shortlist_k,
                                           headless=True)

    def _geo(comid) -> dict:
        r = reach_data.by_comid(comid)
        if not r:
            return {}
        # coords are already GeoJSON/MapLibre-native [lng, lat]; pass through.
        line = [[c[0], c[1]] for c in r.get("coords", [])]
        return {"lat": r["lat"], "lng": r["lon"], "state": r.get("state"),
                "gnis_name": r.get("gnis_name"),
                "streamorder": r.get("streamorder"), "line": line}

    prospects = []
    for p in final.get("ranked", []):
        prospects.append({**p, **_geo(p.get("comid"))})

    return {
        "states": list(states),
        "prospects": prospects,
        "excluded": final.get("excluded", []),
        "trace": final.get("trace", []),
        "usage": final.get("usage"),
    }
