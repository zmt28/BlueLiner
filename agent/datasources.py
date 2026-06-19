"""Data sources behind the MCP tools.

Resolution order for every reading (this is the "live with fixture fallback"
decision made up front):

  1. EVAL MODE  -- if AGENT_SCENARIO points at an injected-conditions file, serve
     those deterministic values. This is what lets the eval define ground truth
     and stay reproducible without touching the network.
  2. LIVE       -- otherwise hit the real upstreams the app uses: USGS NWIS
     (instantaneous values + daily medians) and NOAA api.weather.gov.
  3. FIXTURE    -- if a live fetch fails (timeout, sandbox egress, USGS gap),
     fall back to the recorded baseline in fixtures/rivers.json so the demo
     never breaks.

Every return value carries a `source` field so the agent can cite where a
number came from, and so a trace shows live-vs-fixture-vs-injected at a glance.
The scorer applied here is agent/scorer.py -- the same code as the eval oracle.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

import httpx

from . import config
from .scorer import score_conditions

_UA = "Blueliner-Agent/0.3 (+https://blueliner.app)"
_USGS_IV = "https://waterservices.usgs.gov/nwis/iv/"
_USGS_STAT = "https://waterservices.usgs.gov/nwis/stat/"
_NOAA = "https://api.weather.gov"


# --------------------------------------------------------------------------
# Catalog + scenario loading
# --------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _catalog() -> dict:
    with open(config.FIXTURES_DIR / "rivers.json") as f:
        data = json.load(f)
    return {r["river_id"]: r for r in data["rivers"]}


def _scenario() -> Optional[dict]:
    """Injected conditions for eval mode, or None for live mode.

    Reads AGENT_SCENARIO from the environment at call time (not import time) so
    in-process callers (e.g. the proactive watcher) can toggle it too, and the
    eval can rewrite it per scenario. Re-reads cheaply.
    """
    import os
    path = os.environ.get("AGENT_SCENARIO")
    if not path:
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except OSError:
        return None


def haversine_miles(lat1, lng1, lat2, lng2) -> float:
    r = 3958.8  # earth radius, miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


# --------------------------------------------------------------------------
# Live upstream fetchers (best-effort; any failure returns None)
# --------------------------------------------------------------------------
def _usgs_iv(site_no: str) -> Optional[dict]:
    """Latest instantaneous flow (00060) + water temp (00010) for a site."""
    try:
        with httpx.Client(timeout=config.LIVE_TIMEOUT, headers={"User-Agent": _UA}) as c:
            r = c.get(_USGS_IV, params={
                "format": "json", "sites": site_no,
                "parameterCd": "00060,00010", "siteStatus": "all",
            })
            r.raise_for_status()
            data = r.json()
    except Exception:
        return None
    flow_cfs = water_temp_f = None
    for ts in data.get("value", {}).get("timeSeries", []):
        code = ts.get("variable", {}).get("variableCode", [{}])[0].get("value")
        vals = (ts.get("values") or [{}])[0].get("value", [])
        if not vals:
            continue
        try:
            v = float(vals[-1].get("value"))
        except (TypeError, ValueError):
            continue
        if v <= -999999:
            continue
        if code == "00060":
            flow_cfs = v
        elif code == "00010":
            water_temp_f = round(v * 9 / 5 + 32, 1)
    if flow_cfs is None and water_temp_f is None:
        return None
    return {"flow_cfs": flow_cfs, "water_temp_f": water_temp_f}


def _usgs_median(site_no: str) -> Optional[float]:
    """Historical median discharge for today's month/day from the USGS daily
    statistics service."""
    try:
        with httpx.Client(timeout=config.LIVE_TIMEOUT, headers={"User-Agent": _UA}) as c:
            r = c.get(_USGS_STAT, params={
                "format": "rdb", "sites": site_no,
                "statReportType": "daily", "statTypeCd": "median",
                "parameterCd": "00060",
            })
            r.raise_for_status()
            text = r.text
    except Exception:
        return None
    now = datetime.now(timezone.utc)
    cols = None
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if cols is None:
            cols = parts  # header row
            continue
        if parts and parts[0] == "5s":  # rdb type row
            continue
        row = dict(zip(cols, parts))
        try:
            if int(row.get("month_nu", -1)) == now.month and \
               int(row.get("day_nu", -1)) == now.day:
                return float(row.get("mean_va") or row.get("p50_va") or row.get("median_va"))
        except (ValueError, TypeError):
            continue
    return None


def _noaa_forecast(lat: float, lng: float, days: int) -> Optional[list]:
    try:
        with httpx.Client(timeout=config.LIVE_TIMEOUT, headers={"User-Agent": _UA}) as c:
            pts = c.get(f"{_NOAA}/points/{lat:.4f},{lng:.4f}")
            pts.raise_for_status()
            fc_url = pts.json()["properties"]["forecast"]
            fc = c.get(fc_url)
            fc.raise_for_status()
            periods = fc.json()["properties"]["periods"]
    except Exception:
        return None
    out = []
    for p in periods[: days * 2]:
        if not p.get("isDaytime", True):
            continue
        out.append({
            "name": p.get("name"),
            "temp_f": p.get("temperature"),
            "precip_pct": (p.get("probabilityOfPrecipitation") or {}).get("value"),
            "sky": p.get("shortForecast"),
            "wind": p.get("windSpeed"),
        })
        if len(out) >= days:
            break
    return out or None


# --------------------------------------------------------------------------
# Tool-facing functions
# --------------------------------------------------------------------------
def get_candidate_rivers(lat: float, lng: float, radius_miles: int,
                         state: Optional[str] = None) -> list[dict]:
    """Rivers within `radius_miles` of (lat, lng), optionally filtered to a
    state. In eval mode, returns exactly the scenario's candidate set."""
    cat = _catalog()
    scenario = _scenario()
    if scenario is not None:
        ids = scenario.get("candidates", list(cat.keys()))
        rivers = [cat[i] for i in ids if i in cat]
        source = "injected (eval scenario)"
    else:
        rivers = list(cat.values())
        source = "blueliner-catalog"

    out = []
    for r in rivers:
        dist = haversine_miles(lat, lng, r["lat"], r["lng"])
        if scenario is None and dist > radius_miles:
            continue
        if state and r["state"] != state.upper():
            continue
        out.append({
            "river_id": r["river_id"], "name": r["name"], "state": r["state"],
            "lat": r["lat"], "lng": r["lng"],
            "distance_miles": round(dist, 1),
            "gauges": r["gauges"], "source": source,
        })
    out.sort(key=lambda x: x["distance_miles"])
    return out


def get_river_conditions(river_id: str) -> dict:
    """Current aggregated rating + readings + the scorer verdict for a river."""
    cat = _catalog()
    river = cat.get(river_id)
    if river is None:
        return {"river_id": river_id, "error": "unknown river_id"}

    scenario = _scenario()
    if scenario is not None and river_id in scenario.get("conditions", {}):
        c = scenario["conditions"][river_id]
        flow, temp, median = c.get("flow_cfs"), c.get("water_temp_f"), c.get("median_cfs")
        hours = c.get("last_updated_hours_ago", 0.5)
        source = "injected (eval scenario)"
    else:
        live = _usgs_iv(river["gauges"][0]) if river["gauges"] else None
        if live is not None:
            median = _usgs_median(river["gauges"][0])
            flow, temp = live["flow_cfs"], live["water_temp_f"]
            hours = 0.25
            source = f"usgs-live ({river['gauges'][0]})"
        else:
            fx = river["fixture"]
            flow, temp, median = fx["flow_cfs"], fx["water_temp_f"], fx["median_cfs"]
            hours = fx["last_updated_hours_ago"]
            source = "recorded-fixture"

    score = score_conditions(water_temp_f=temp, flow_cfs=flow, median_cfs=median)
    pct = round(score["flow_ratio"] * 100) if score["flow_ratio"] is not None else None
    return {
        "river_id": river_id, "name": river["name"], "state": river["state"],
        "site_no": river["gauges"][0] if river["gauges"] else None,
        "flow_cfs": flow, "water_temp_f": temp, "median_cfs": median,
        "flow_vs_median_pct": pct,
        "rating": score["overall"],
        "score": score,
        "hatch": river.get("hatch"), "stocking": river.get("stocking"),
        "last_updated_hours_ago": round(hours, 1),
        "source": source,
    }


def get_flow_history(site_no: str) -> dict:
    """Recent daily flow + today's historical median for a gauge."""
    median = _usgs_median(site_no)
    if median is not None:
        return {"site_no": site_no, "median_today_cfs": round(median, 1),
                "source": f"usgs-stat ({site_no})"}
    # Fixture fallback: find the catalog river using this gauge.
    for r in _catalog().values():
        if site_no in r["gauges"]:
            return {"site_no": site_no,
                    "median_today_cfs": r["fixture"]["median_cfs"],
                    "source": "recorded-fixture"}
    return {"site_no": site_no, "median_today_cfs": None, "source": "unavailable"}


def get_forecast(lat: float, lng: float, days: int = 3) -> dict:
    scenario = _scenario()
    if scenario is not None and "forecast" in scenario:
        return {"days": scenario["forecast"][:days], "source": "injected (eval scenario)"}
    live = _noaa_forecast(lat, lng, days)
    if live is not None:
        return {"days": live, "source": "noaa-live"}
    return {"days": [], "source": "unavailable",
            "note": "NOAA forecast unavailable; proceed on gauge readings."}


def get_access(river_id: str) -> dict:
    """Access tier + point count for the legality guardrail."""
    cat = _catalog()
    river = cat.get(river_id)
    if river is None:
        return {"river_id": river_id, "error": "unknown river_id"}
    scenario = _scenario()
    tier = river["access_tier"]
    points = river["access_points"]
    source = "blueliner-catalog"
    if scenario is not None and river_id in scenario.get("conditions", {}):
        c = scenario["conditions"][river_id]
        tier = c.get("access_tier", tier)
        source = "injected (eval scenario)"
    return {"river_id": river_id, "access_tier": tier,
            "access_points": points,
            "public_access": tier in ("public", "permit", "fee"),
            "source": source}
