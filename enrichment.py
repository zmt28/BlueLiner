"""
Auto-enrichment for catch logs: capture the fishing conditions at log
time so the angler doesn't have to type them, and so patterns ("what
produces fish") emerge over a season.

`build_env` composes several best-effort sources into one flat dict.
Every source degrades independently to None -- a catch save must never
fail because USGS or NOAA is slow. Returned dict is stored verbatim on
the catch as an immutable point-in-time snapshot.
"""

import logging
from datetime import datetime, timezone

import httpx

import db
import hatches
import weather
from arcgis import USER_AGENT

logger = logging.getLogger("blueliner.enrichment")

_USGS_IV_URL = "https://waterservices.usgs.gov/nwis/iv/"
_SYNODIC = 29.530588853                 # days, mean lunar month
# A known new moon: 2000-01-06 18:14 UTC.
_KNOWN_NEW_MOON = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
_MOON_PHASES = [
    "New moon", "Waxing crescent", "First quarter", "Waxing gibbous",
    "Full moon", "Waning gibbous", "Last quarter", "Waning crescent",
]


def moon_phase(when: datetime) -> str:
    """Named lunar phase for a datetime. Accurate to the named phase,
    which is all the angler cares about."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    days = (when - _KNOWN_NEW_MOON).total_seconds() / 86400.0
    age = days % _SYNODIC
    # 8 phases, each spanning 1/8 of the synodic month, centered so the
    # cardinal phases (new/first/full/last) land mid-bucket.
    idx = int((age / _SYNODIC) * 8 + 0.5) % 8
    return _MOON_PHASES[idx]


def _usgs_now(site_no: str) -> dict:
    """Current discharge (cfs) + water temp (F) for a single USGS site.
    Synchronous -- callers run build_env in a worker thread."""
    out = {"flow_cfs": None, "water_temp_f": None}
    try:
        with httpx.Client(timeout=12.0,
                          headers={"User-Agent": USER_AGENT}) as c:
            r = c.get(_USGS_IV_URL, params={
                "format": "json", "sites": site_no,
                "parameterCd": "00060,00010", "siteStatus": "all"})
            r.raise_for_status()
            for ts in r.json().get("value", {}).get("timeSeries", []):
                var = ts.get("variable", {})
                code = var.get("variableCode", [{}])[0].get("value")
                vals = (ts.get("values") or [{}])[0].get("value", [])
                if not vals:
                    continue
                try:
                    v = float(vals[0].get("value"))
                except (TypeError, ValueError):
                    continue
                if v <= -999999:                   # USGS no-data sentinel
                    continue
                if code == "00060":
                    out["flow_cfs"] = round(v, 1)
                elif code == "00010":
                    out["water_temp_f"] = round(v * 9 / 5 + 32, 1)
    except Exception as exc:
        logger.info("USGS IV (now) failed for %s: %s", site_no, exc)
    return out


def _median_context(site_no: str, when: datetime,
                    flow_cfs: float | None) -> tuple:
    """(median_cfs, label) from the warm river_stats cache. Label is a
    short human comparison vs. the historical median for this date."""
    if not site_no:
        return None, None
    try:
        stats = db.get_river_stats([site_no]).get(site_no) or {}
    except Exception:
        stats = {}
    median = stats.get(f"{when.month}-{when.day}")
    if not isinstance(median, (int, float)) or median <= 0:
        return None, None
    if flow_cfs is None:
        return round(median, 1), None
    ratio = flow_cfs / median
    if ratio < 0.5:
        label = "well below average"
    elif ratio < 0.85:
        label = "below average"
    elif ratio <= 1.15:
        label = "near average"
    elif ratio <= 2.0:
        label = "above average"
    else:
        label = "well above average"
    return round(median, 1), label


def build_env(lat: float, lon: float, river_site_no: str | None,
              river_name: str | None, when: datetime) -> dict:
    """Compose the auto-captured conditions snapshot. All keys always
    present; values are None when a source can't be resolved."""
    env: dict = {
        "flow_cfs": None, "water_temp_f": None,
        "flow_median_cfs": None, "flow_vs_median": None,
        "air_temp_f": None, "pressure_inhg": None, "conditions": None,
        "moon_phase": None, "active_hatches": [],
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }

    if river_site_no:
        now = _usgs_now(river_site_no)
        env["flow_cfs"] = now["flow_cfs"]
        env["water_temp_f"] = now["water_temp_f"]
        median, label = _median_context(river_site_no, when, now["flow_cfs"])
        env["flow_median_cfs"] = median
        env["flow_vs_median"] = label

    try:
        w = weather.fetch_observation(lat, lon)
        env["air_temp_f"] = w.get("air_temp_f")
        env["pressure_inhg"] = w.get("pressure_inhg")
        env["conditions"] = w.get("conditions")
    except Exception as exc:
        logger.info("weather enrichment failed: %s", exc)

    try:
        env["moon_phase"] = moon_phase(when)
    except Exception:
        pass

    try:
        zone = hatches.zone_for_river(river_name or "", lat, lon)
        active = hatches.active_hatches(zone, when.month)
        env["active_hatches"] = [e["common_name"] for e in active[:4]]
    except Exception as exc:
        logger.info("hatch enrichment failed: %s", exc)

    return env
