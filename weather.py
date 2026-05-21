"""
NOAA (api.weather.gov) current-observation lookup for catch enrichment.

Free, no API key, US-only -- fine for the mid-Atlantic focus. Two
hops: resolve coordinates -> nearest observation station (immutable
per location, cached hard), then that station's latest observation
(cached briefly since "latest" only changes ~hourly). Every failure
degrades to None/{} so logging a catch never blocks on weather.
"""

import logging

import httpx

from cache import LruTtl

logger = logging.getLogger("blueliner.weather")

_UA = "Blueliner/1.0 (+https://blueliner.app)"
_BASE = "https://api.weather.gov"

# coords -> station id (immutable per location; None cached too, to
# avoid re-hammering coords NOAA can't resolve)
_station_cache: LruTtl = LruTtl(maxsize=4096)
# station id -> observation dict (short TTL)
_obs_cache: LruTtl = LruTtl(maxsize=2048, ttl=900.0)


def _val(field):
    """NOAA wraps measurements as {'value': x, 'unitCode': ...}."""
    if not isinstance(field, dict):
        return None
    return field.get("value")


def _c_to_f(c):
    return round(c * 9 / 5 + 32, 1) if isinstance(c, (int, float)) else None


def _pa_to_inhg(pa):
    return round(pa / 3386.389, 2) if isinstance(pa, (int, float)) else None


def _nearest_station(lat: float, lon: float) -> str | None:
    key = (round(lat, 3), round(lon, 3))
    if key in _station_cache:
        return _station_cache.get(key)
    station = None
    try:
        with httpx.Client(timeout=10.0, headers={"User-Agent": _UA}) as c:
            r = c.get(f"{_BASE}/points/{lat:.4f},{lon:.4f}")
            r.raise_for_status()
            stations_url = r.json()["properties"]["observationStations"]
            r2 = c.get(stations_url)
            r2.raise_for_status()
            feats = r2.json().get("features", [])
            if feats:
                station = feats[0]["properties"]["stationIdentifier"]
    except Exception as exc:
        logger.info("NOAA station lookup failed for %.4f,%.4f: %s",
                    lat, lon, exc)
    _station_cache.put(key, station)
    return station


def fetch_observation(lat: float, lon: float) -> dict:
    """Latest NOAA observation near (lat, lon). Returns a dict with
    air_temp_f / pressure_inhg / conditions (any may be None), plus
    observed_at + station, or {} if the lookup fails entirely."""
    station = _nearest_station(lat, lon)
    if not station:
        return {}
    cached = _obs_cache.get(station)
    if cached is not None:
        return cached
    out: dict = {}
    try:
        with httpx.Client(timeout=10.0, headers={"User-Agent": _UA}) as c:
            r = c.get(f"{_BASE}/stations/{station}/observations/latest")
            r.raise_for_status()
            props = r.json().get("properties", {})
            out = {
                "air_temp_f": _c_to_f(_val(props.get("temperature"))),
                "pressure_inhg": _pa_to_inhg(_val(props.get("barometricPressure"))),
                "conditions": (props.get("textDescription") or "").strip() or None,
                "observed_at": props.get("timestamp"),
                "station": station,
            }
    except Exception as exc:
        logger.info("NOAA observation failed for %s: %s", station, exc)
        return {}
    _obs_cache.put(station, out)
    return out
