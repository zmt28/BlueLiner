from contextlib import asynccontextmanager
import secrets

from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import (RedirectResponse, FileResponse, Response,
                               HTMLResponse, JSONResponse)
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
from pydantic import BaseModel, Field
from shapely.geometry import shape, mapping
import httpx
from datetime import datetime, timezone
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import asyncio
import hashlib
import json
import logging
import os
import random
import re
import time

from states import STATES, STATE_BBOX, states_in_bbox
from trout import load_trout_streams, is_near_trout_stream
import trout
from arcgis import USER_AGENT
from cache import LruTtl
import hatches
import stocking
import db
import enrichment
import data_source
import access_points


logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("blueliner")

# How often the background refresher re-precomputes focused states, and
# the age past which a served snapshot is also refreshed in the
# background (stale-while-revalidate). USGS IV updates every ~15-60 min,
# so ~45 min is fresh enough and cheap. Override with REFRESH_INTERVAL
# (seconds).
_REFRESH_INTERVAL = float(os.environ.get("REFRESH_INTERVAL", str(45 * 60)))

# NHDPlusV2 VAA lookup -- bundled with the repo, loaded once at startup.
_VAA_BUNDLED_PATH = os.path.join(os.path.dirname(__file__), "data",
                                 "nhdplus", "vaa.csv.gz")

# Module-level caches. All bounded (LruTtl) -- unbounded per-state dicts
# were the runtime memory growth behind the 512MB OOM. Both are also
# persisted in Postgres (db.river_stats / db.river_geom) so this is just
# the fast L1 in front of a durable, cross-restart store.
_stats_cache: LruTtl = LruTtl(maxsize=6000)
# site_no -> NLDI flowline FeatureCollection. TTL'd so a transient empty
# (NLDI failure) retries later; successful geometry also lives in the DB.
_river_geom_cache: LruTtl = LruTtl(maxsize=1024, ttl=900.0)
# site_no -> {"comid", "gnis_name"} (the authoritative NHD identity).
# Immutable per site -- DB is the durable store; this is L1. TTL'd so
# transient NLDI failures retry; successful meta is also in Postgres.
_gauge_meta_cache: LruTtl = LruTtl(maxsize=2048, ttl=900.0)
# comid -> {"gnis_name"} for individual NHD flowline reaches. Used to
# trim a navigation walk so it doesn't continue past a confluence onto
# a differently-named river. Many comids per gauge, so bigger maxsize.
_comid_meta_cache: LruTtl = LruTtl(maxsize=8192, ttl=900.0)

# Bumped when walk distances or filtering logic change -- river_geom
# rows written under an older version are treated as cache-misses so
# they're refetched on next access, instead of serving stale geometry
# forever. (Geometry is otherwise immutable per site.)
_GEOM_SCHEMA_VERSION = 3

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The pins datastore must be ready before serving -- fast: runs the
    # idempotent migration against SQLite/Postgres.
    db.init_db()

    # Warm external feeds in the background so startup (and the platform
    # health check) never blocks. Both are cached + also loaded lazily, so
    # this is just a head start; the trout keyset fetch can be slow.
    async def _warm():
        try:
            await asyncio.to_thread(stocking.load_stocking, "VA")
        except Exception as exc:
            logger.warning("VA stocking warm failed: %s", exc)
        try:
            await asyncio.to_thread(load_trout_streams, "MD")
        except Exception as exc:
            logger.warning("MD trout warm failed: %s", exc)
        # NHDPlusV2 VAA: ~300K rows from data/nhdplus/vaa.csv.gz on
        # first boot. Idempotent (skips if already loaded), so this is
        # a no-op on warm restarts. Drives the LevelPathID flowline
        # filter that prevents cross-confluence bleed.
        try:
            n = await asyncio.to_thread(
                lambda: db.bulk_load_vaa(data_source.resolve_data_file(
                    _VAA_BUNDLED_PATH, "vaa.csv.gz")))
            if n:
                logger.info("NHDPlus VAA loaded: %d rows", n)
        except Exception as exc:
            logger.warning("NHDPlus VAA load failed: %s "
                           "(falling back to gnis filter)", exc)
        # (Clickable-streams + public-lands are served as static vector
        # tiles from R2 now; their boot-time DB ingest was retired in M3.)

    # The refresher is what makes the map instant: it keeps each focused
    # state's snapshot + flowline geometry warm in Postgres so requests
    # are local reads. Runs forever in the background; first cycle starts
    # immediately but never blocks startup or the health check.
    async def _refresher():
        import precompute
        while True:
            try:
                await precompute.refresh_focused()
            except Exception as exc:
                logger.warning("refresher cycle failed: %s", exc)
            await asyncio.sleep(_REFRESH_INTERVAL)

    warm_task = asyncio.create_task(_warm())
    refresh_task = asyncio.create_task(_refresher())
    yield
    warm_task.cancel()
    refresh_task.cancel()


app = FastAPI(lifespan=lifespan)
# Snapshot/line payloads are large and highly compressible JSON; gzip
# them (also lets a CDN/Cloudflare cache the compressed bytes).
app.add_middleware(GZipMiddleware, minimum_size=1024)

# The app shell (HTML + app.js/app.css/sw.js/manifest) changes every
# deploy. A CDN that caches by file extension (Cloudflare's default)
# will otherwise serve a stale app.js even to fresh/incognito clients,
# stranding them on an old build. `no-cache` tells the browser AND any
# intermediary cache to revalidate before serving -- Cloudflare won't
# edge-cache a response with no-cache -- so deploys propagate. The
# Immutable Vite-hashed bundles under /static/dist/assets/ + the
# vendored icons keep their default long-lived caching. The shell
# routes below get an explicit no-cache so a new deploy propagates
# immediately (the bundled JS/CSS filenames are content-hashed by
# Vite, so /map serves a new index.html referencing the new hashes,
# which busts the bundle cache on the first request).
_NO_CACHE_PATHS = {"/", "/map", "/sw.js", "/static/manifest.webmanifest"}


@app.middleware("http")
async def _shell_no_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in _NO_CACHE_PATHS:
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# -- Historical stats --

def _fetch_stats_network(site_nos: list[str]) -> dict[str, dict]:
    """USGS daily-median discharge for sites -> {site_no: {(month,day): cfs}}.

    Every requested site is present in the result (an empty dict when USGS
    had no rows) so the caller can persist a "looked, found nothing"
    marker and stop refetching it on every request. Bounded by a
    wall-clock budget; returns whatever was gathered on timeout. Runs in a
    background thread -- never on the request path.
    """
    out: dict[str, dict] = {s: {} for s in site_nos}
    url = "https://waterservices.usgs.gov/nwis/stat/"
    batch_size = 10
    deadline = time.monotonic() + 20.0
    with httpx.Client(
        timeout=15.0, headers={"User-Agent": USER_AGENT}
    ) as client:
        for i in range(0, len(site_nos), batch_size):
            if time.monotonic() > deadline:
                break
            batch = site_nos[i:i + batch_size]
            params = {
                "format": "rdb",
                "sites": ",".join(batch),
                "statReportType": "daily",
                "statTypeCd": "median",
                "parameterCd": "00060",
            }
            try:
                response = client.get(url, params=params)
                lines = response.text.strip().split("\n")
                header = None
                for line in lines:
                    if line.startswith("#"):
                        continue
                    parts = line.split("\t")
                    if header is None:
                        header = parts
                        continue
                    if len(parts) < 2 or parts[0].startswith("5s") or parts[0].startswith("-"):
                        continue
                    try:
                        row = dict(zip(header, parts))
                        site = row.get("site_no", "").strip()
                        month = int(row.get("month_nu", 0))
                        day = int(row.get("day_nu", 0))
                        val = float(row.get("p50_va", 0))
                        if site and month and day and val:
                            out.setdefault(site, {})[(month, day)] = val
                    except (ValueError, KeyError):
                        continue
            except Exception:
                continue
    return out


_stats_warming: set[str] = set()


def _schedule_stats_warm(site_nos: list[str]) -> None:
    """One-shot background fetch + persist for sites with no cached medians.
    Coloring sharpens on a later request once the warm completes."""
    todo = [s for s in site_nos if s not in _stats_warming]
    if not todo:
        return
    _stats_warming.update(todo)

    async def _warm():
        try:
            medians = await asyncio.to_thread(_fetch_stats_network, todo)
            for s, md in medians.items():
                _stats_cache[s] = md
                await asyncio.to_thread(
                    db.put_river_stats, s,
                    {f"{m}-{d}": v for (m, d), v in md.items()},
                )
        except Exception as exc:
            logger.warning("stats warm failed: %s", exc)
        finally:
            for s in todo:
                _stats_warming.discard(s)

    asyncio.create_task(_warm())


async def _ensure_medians_cached(site_nos: list[str]) -> None:
    """Make medians available without blocking the request: in-process ->
    Postgres -> (background) USGS. Sites still missing score on absolute
    thresholds now and get colored on the next request post-warm."""
    need = [s for s in site_nos if s not in _stats_cache]
    if not need:
        return
    from_db = await asyncio.to_thread(db.get_river_stats, need)
    for s, md in from_db.items():
        try:
            _stats_cache[s] = {
                tuple(int(x) for x in k.split("-")): v for k, v in md.items()
            }
        except (ValueError, AttributeError):
            continue
    missing = [s for s in need if s not in _stats_cache]
    if missing:
        _schedule_stats_warm(missing)


# -- Scoring --

SCORE_COLORS = {
    "green":  "#4A8C5C",   # --bl-cond-good-500 (moss)
    "yellow": "#B7892F",   # --bl-cond-fair-500 (ochre)
    "red":    "#B3473B",   # --bl-cond-poor-500 (clay)
    "gray":   "#7F8B9C",   # --bl-cond-none-500 (stone)
}

SCORE_LABELS = {
    "green": "GOOD",
    "yellow": "FAIR",
    "red": "POOR",
    "gray": "NO DATA",
}

SCORE_BG = {
    "green":  "#EEF5F0",   # --bl-cond-good-50
    "yellow": "#F8F0DA",   # --bl-cond-fair-50
    "red":    "#F5E1DD",   # --bl-cond-poor-50
    "gray":   "#EEF0F3",   # --bl-cond-none-50
}

# Maps the legacy color-name key to the design-system variant suffix.
# Used by templates that emit class-based markup (.cond--good etc.)
# instead of inline-styled badges.
COND_VARIANT = {
    "green":  "good",
    "yellow": "fair",
    "red":    "poor",
    "gray":   "none",
}


def score_conditions(variables: list[dict], historical_median: float | None = None) -> dict:
    """
    Evaluates a station's current readings for fishing suitability.

    Temperature thresholds (Fahrenheit, optimized for trout):
        Green: 48-65, Yellow: 45-48 or 65-68, Red: above 68 or below 40

    Flow scoring uses historical percentile context when available:
        Good: within 0.5x-2x of historical median
        Fair: 2x-3x or 0.25x-0.5x of median
        Poor: above 3x or below 0.25x of median
    Falls back to absolute thresholds when no historical data exists.
    """
    temp_score = None
    flow_score = None
    current_flow = None
    temp_f = None

    for var in variables:
        description = var.get("variable", "").lower()
        try:
            value = float(var.get("value", ""))
        except (ValueError, TypeError):
            continue

        if "temperature" in description and "water" in description:
            temp_f = value * 9 / 5 + 32
            if 48 <= temp_f <= 65:
                temp_score = "green"
            elif (45 <= temp_f < 48) or (65 < temp_f <= 68):
                temp_score = "yellow"
            elif temp_f > 68 or temp_f < 40:
                temp_score = "red"
            else:
                temp_score = "yellow"

        if "discharge" in description or "streamflow" in description:
            current_flow = value
            if historical_median and historical_median > 0:
                ratio = value / historical_median
                if 0.5 <= ratio <= 2.0:
                    flow_score = "green"
                elif (0.25 <= ratio < 0.5) or (2.0 < ratio <= 3.0):
                    flow_score = "yellow"
                else:
                    flow_score = "red"
            else:
                if value < 0:
                    flow_score = "red"
                elif value > 10000:
                    flow_score = "red"
                elif value > 5000:
                    flow_score = "yellow"
                else:
                    flow_score = "green"

    scores = [s for s in [temp_score, flow_score] if s is not None]
    if not scores:
        overall = "gray"
    elif "red" in scores:
        overall = "red"
    elif "yellow" in scores:
        overall = "yellow"
    else:
        overall = "green"

    return {
        "overall": overall,
        "temp": temp_score,
        "flow": flow_score,
        "current_flow": current_flow,
        "temp_f": round(temp_f, 1) if temp_f is not None else None,
    }


# -- Popup HTML --

_MONTH_ABBR = "JFMAMJJASOND"
_MONTH_FULL = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]


def _month_strip_html(months: tuple, peak: tuple) -> str:
    cells = ""
    for m in range(1, 13):
        on = hatches._in_range(m, months[0], months[1])
        pk = hatches._in_range(m, peak[0], peak[1])
        if pk:
            style = "background:#27ae60;color:#fff;font-weight:700"
        elif on:
            style = "background:#d5f5e3;color:#1e8449"
        else:
            style = "background:#eef0f2;color:#aab"
        cells += (
            f'<span style="display:inline-block;width:15px;text-align:center;'
            f'font-size:9px;padding:2px 0;{style}">{_MONTH_ABBR[m - 1]}</span>'
        )
    return f'<div style="display:flex;gap:1px;margin:4px 0">{cells}</div>'


def _hatch_section_html(zone: dict | None, active: list[dict] | None,
                        month: int) -> str:
    if not zone:
        return ""
    title = f"Hatching now &mdash; {_MONTH_FULL[month - 1]} &middot; {zone['name']}"
    if not active:
        body = ('<div style="font-size:12px;color:#777">No major mayfly/caddis '
                'hatches indexed this month &mdash; fish midges, eggs, and '
                'streamers.</div>')
    else:
        body = ""
        for e in active[:6]:
            patterns = ", ".join(e["patterns"][:2])
            body += f"""
                <div style="padding:6px 0;border-top:1px solid #e3efe8">
                    <div style="font-size:13px;font-weight:700;color:#1a1a2e">{e['common_name']}
                        <span style="font-weight:400;color:#8a8a8a;font-style:italic;font-size:11px">{e['insect']}</span></div>
                    {_month_strip_html(e['months'], e['peak'])}
                    <div style="font-size:11px;color:#555">Hooks {e['hook_sizes']} &middot; {e['time_of_day']}</div>
                    <div style="font-size:11px;color:#1e8449">Try: {patterns}</div>
                </div>"""
    # No <details> wrapper -- the hatch content lives inside the
    # dedicated Hatches tab in the new panel, so a collapsible inside
    # an already-selected tab is redundant. Just emit the title + body
    # as plain block content; the .bl-hatch class still controls the
    # green-tinted styling of the title row.
    return f"""
        <div class="bl-hatch">
            <div class="bl-hatch-title">{title}</div>
            <div class="bl-section-body">{body}</div>
        </div>"""


def _trend_html(site_no: str | None) -> str:
    if not site_no:
        return ""
    return f"""
        <div style="margin-top:8px">
            <button type="button" class="bl-trend-btn" data-site="{site_no}"
                style="background:#eaf2fb;color:#2c6fbf;border:1px solid #b8d4f0;border-radius:6px;
                padding:5px 10px;font-size:12px;cursor:pointer">Show 1-yr flow trend</button>
            <div class="bl-trend" data-site="{site_no}" style="margin-top:6px"></div>
        </div>"""


_CHIP_TROUT = (
    '<span class="pill pill--trout">'
    '<span class="pill-dot"></span>Trout water</span>'
)
_CHIP_STOCKED = (
    '<span class="pill pill--stocked">'
    '<span class="pill-dot"></span>Recently stocked</span>'
)
_CHIP_HATCH_NOW = (
    '<span class="pill pill--hatch">'
    '<span class="pill-dot"></span>Hatching now</span>'
)
_CHIP_ACCESS = (
    '<span class="pill pill--access">'
    '<span class="pill-dot"></span>Public access</span>'
)


def _readings_table_html(variables: list[dict]) -> str:
    rows = ""
    for i, variable in enumerate(variables):
        dt = datetime.fromisoformat(variable["dateTime"])
        bg = "#f8f9fa" if i % 2 == 0 else "#ffffff"
        rows += f"""
            <tr style="background:{bg}">
                <td style="padding:8px 10px;color:#555">{variable["variable"]}</td>
                <td style="padding:8px 10px;text-align:center;font-weight:600">{variable["value"]}</td>
                <td style="padding:8px 10px;text-align:center;color:#777;font-size:12px">{dt.strftime("%b %d, %Y at %I:%M %p")}</td>
            </tr>"""
    return f"""
        <table style="width:100%;border-collapse:collapse;font-size:13px">
            <tr style="border-bottom:2px solid #dee2e6">
                <th style="padding:6px 10px;text-align:left;color:#333;font-weight:600">Variable</th>
                <th style="padding:6px 10px;text-align:center;color:#333;font-weight:600">Value</th>
                <th style="padding:6px 10px;text-align:center;color:#333;font-weight:600">Updated</th>
            </tr>
            {rows}
        </table>"""


def _flow_context_html(conditions: dict, historical_median: float | None) -> str:
    if not historical_median or conditions.get("current_flow") is None:
        return ""
    current = conditions["current_flow"]
    date_label = datetime.now().strftime("%b %d")
    if current > historical_median * 1.15:
        trend, trend_color = "Above average", "#e67e22"
    elif current < historical_median * 0.85:
        trend, trend_color = "Below average", "#3498db"
    else:
        trend, trend_color = "Near median", "#27ae60"
    return f"""
        <div style="padding:8px 12px;background:#f0f4f8;border-radius:6px;margin:6px 0;font-size:13px;color:#444">
            <span style="font-weight:600">Flow context:</span>
            {current:.0f} cfs now vs. {historical_median:.0f} cfs median for {date_label}
            <span style="color:{trend_color};font-weight:600;margin-left:4px">{trend}</span>
        </div>"""


def _season_label(months: tuple) -> str:
    s, e = months
    if s == 1 and e == 12:
        return "Year-round"
    return f"{_MONTH_FULL[s - 1][:3]}–{_MONTH_FULL[e - 1][:3]}"


def _stocked_block_html(waters: list[dict]) -> str:
    if not waters:
        return ""
    items = ""
    for w in waters[:6]:
        species = ", ".join(w.get("species", []))
        link = (f'<a href="{w["agency_url"]}" target="_blank" '
                f'class="gauge-link">stocking schedule '
                f'<i data-lucide="arrow-up-right" aria-hidden="true"></i></a>'
                ) if w.get("agency_url") else ""
        items += f"""
            <div style="padding:5px 0;border-top:1px solid var(--bl-stocked-soft)">
                <div style="font-size:var(--fs-meta);font-weight:600;color:var(--fg-1)">{w["water"]}</div>
                <div style="font-size:var(--fs-caption);color:var(--bl-stocked)">{w.get("category", "")}
                    {("&middot; " + species) if species else ""}
                    &middot; {_season_label(w.get("season_months", (1, 12)))} {link}</div>
            </div>"""
    return f"""
        <div style="margin-top:10px;padding:8px 12px;background:var(--bl-stocked-soft);border:1px solid var(--bl-stocked);border-radius:var(--radius-2)">
            <div style="font-size:var(--fs-meta);font-weight:700;color:var(--bl-stocked)">Stocked nearby</div>
            {items}
        </div>"""


def _primary_gauge(gauges: list[dict]) -> dict | None:
    """The gauge that best represents the river: first one with a site_no
    that reports discharge, else the first gauge. Drives the top-of-card
    summary + auto-loaded flow chart."""
    for g in gauges:
        if g.get("site_no") and any(
            "discharge" in v.get("variable", "").lower() or
            "streamflow" in v.get("variable", "").lower()
            for v in g.get("variables", [])
        ):
            return g
    return gauges[0] if gauges else None


def _verdict_variant(river: dict) -> str:
    """Picks the .panel-verdict tone modifier from the river's overall
    score. is-fair / is-poor tint the callout box ochre / clay; is-none
    grays it out for limited-data rivers."""
    overall = river.get("overall", "gray")
    if overall == "yellow":
        return " is-fair"
    if overall == "red":
        return " is-poor"
    if overall == "gray":
        return " is-none"
    return ""


def _ranking_summary_html(river: dict) -> str:
    """One-line plain-English read on why the river is rated as it is,
    e.g. 'Flow is 20% below average and water temp is ideal.' Built from
    the primary gauge's flow-vs-median ratio + water temperature.

    Rendered into a .panel-verdict box (design-system component); the
    tone modifier (is-fair / is-poor / is-none) matches the overall
    score so the callout color reinforces the headline badge. Inline
    numeric insertions get wrapped in <strong> for prominence."""
    variant = _verdict_variant(river)
    primary = _primary_gauge(river["gauges"])
    if not primary:
        return ""
    cond = primary.get("conditions", {})
    median = primary.get("historical_median")
    parts: list[str] = []

    # The median is the historical daily median for today's date, so the
    # comparison is explicitly time-of-year-bound, not an annual average.
    cf = cond.get("current_flow")
    if cf is not None and median and median > 0:
        pct = round((cf / median - 1) * 100)
        if abs(pct) <= 15:
            parts.append("flow is near normal for this time of year")
        elif pct < 0:
            parts.append(
                f"flow is <strong>{abs(pct)}%</strong> below average for this time of year")
        else:
            parts.append(
                f"flow is <strong>{pct}%</strong> above average for this time of year")
    elif cf is not None:
        parts.append(f"flow is <strong>{cf:.0f}</strong> cfs")

    tf = cond.get("temp_f")
    if tf is not None:
        if tf < 40:
            parts.append("water is very cold")
        elif tf < 48:
            parts.append("water is cool")
        elif tf <= 65:
            parts.append("water temp is ideal")
        elif tf <= 68:
            parts.append("water is slightly warm")
        else:
            parts.append("water is too warm")

    if not parts:
        return f'<div class="panel-verdict{variant}">Limited live data right now.</div>'
    sentence = " and ".join(parts)
    sentence = sentence[0].upper() + sentence[1:] + "."
    return f'<div class="panel-verdict{variant}">{sentence}</div>'


def _panel_header_html(river: dict) -> str:
    """Top of the river panel: name + condition badge on the title row,
    feature pills, verdict callout, stat grid. This block fits in the
    mobile snap-sheet's peek view (~38vh) so the angler sees the
    headline info before deciding to expand the sheet.

    Uses the design-system component primitives:
        .cond + .cond-glyph + .cond--good/fair/poor/none
        .pill + .pill-dot + .pill--trout/stocked/hatch/access
        .panel-verdict (with is-fair / is-poor / is-none tone modifiers)
        .bl-num (tabular numerals on the stat-grid numbers)
    """
    overall = river["overall"]
    variant = COND_VARIANT.get(overall, "none")
    badge_label = SCORE_LABELS[overall]
    pills = []
    if river.get("on_trout"):
        pills.append(_CHIP_TROUT)
    if river.get("near_stocked"):
        pills.append(_CHIP_STOCKED)
    if river.get("active"):
        pills.append(_CHIP_HATCH_NOW)
    if river.get("access_count", 0):
        pills.append(_CHIP_ACCESS)
    pills_row = (
        f'<div class="bl-pills">{"".join(pills)}</div>' if pills else ""
    )
    n_gauges = len(river.get("gauges") or [])
    n_active = len(river.get("active") or [])
    n_stocked = len(river.get("stocked_waters") or [])
    n_access = int(river.get("access_count", 0))
    stats_html = (
        '<div class="bl-stats">'
        f'<div class="bl-stat"><div class="bl-stat-n bl-num">{n_gauges}</div>'
        f'<div class="bl-stat-label">Gauges</div></div>'
        f'<div class="bl-stat"><div class="bl-stat-n bl-num">{n_active}</div>'
        f'<div class="bl-stat-label">Active hatches</div></div>'
        f'<div class="bl-stat"><div class="bl-stat-n bl-num">{n_stocked}</div>'
        f'<div class="bl-stat-label">Stocked nearby</div></div>'
        f'<div class="bl-stat"><div class="bl-stat-n bl-num">{n_access}</div>'
        f'<div class="bl-stat-label">Access points</div></div>'
        '</div>'
    )
    return f"""
        <div class="bl-card-head">
            <div class="panel-title-row">
                <div class="bl-title">{river["name"]}</div>
                <span class="cond cond--{variant}">
                    <span class="cond-glyph"></span>{badge_label}
                </span>
            </div>
            {pills_row}
            {_ranking_summary_html(river)}
            {stats_html}
        </div>
    """


def _panel_gauge_section_html(g: dict, is_primary: bool) -> str:
    """One gauge's content block. Used inside the Conditions tab."""
    usgs = (
        f'<div style="padding:4px 0 2px;text-align:right">'
        f'<a href="https://waterdata.usgs.gov/nwis/uv?site_no={g["site_no"]}" '
        f'target="_blank" class="gauge-link">'
        f'View on USGS <i data-lucide="arrow-up-right" aria-hidden="true"></i></a></div>'
    ) if g.get("site_no") else ""
    # Primary gauge's chart renders above; skip its inline trend button.
    trend = "" if is_primary else _trend_html(g.get("site_no"))
    return f"""
        <details class="bl-section bl-gauge" open>
            <summary>{g["site_name"]}</summary>
            <div class="bl-section-body">
                {_flow_context_html(g["conditions"], g["historical_median"])}
                {_readings_table_html(g["variables"])}
                {trend}
                {usgs}
            </div>
        </details>"""


def _panel_tabs_html(river: dict, chart_html: str) -> str:
    """Four-tab content area: Conditions / Hatches / Stocking / Log.
    Pure CSS pattern -- a hidden radio per tab + sibling-selector show/
    hide on the panels. The Catch tab gets the .bl-catch-cta hook so
    wireCatch() can populate it on panel open (same as before, just
    relocated). flowchart placeholder + autoLoadFlowChart() hook still
    fire on open even when the Conditions tab isn't visible -- they
    populate the element silently, render kicks in when displayed."""
    primary = _primary_gauge(river["gauges"])
    gauges_html = "".join(
        _panel_gauge_section_html(g, g is primary)
        for g in river["gauges"]
    )
    conditions_panel = f"""
        <div class="bl-tab-panel" data-tab="conditions">
            {chart_html}
            {gauges_html}
        </div>"""
    hatches_panel = f"""
        <div class="bl-tab-panel" data-tab="hatches">
            {_hatch_section_html(river["hatch_zone"], river["active"], river["month"])}
        </div>"""
    stocking_panel = f"""
        <div class="bl-tab-panel" data-tab="stocking">
            {_stocked_block_html(river["stocked_waters"])}
        </div>"""
    catch_panel = """
        <div class="bl-tab-panel" data-tab="catch">
            <div class="bl-catch-cta"></div>
        </div>"""
    # Radios + labels for the tab bar. data-tab on the label matches the
    # panel so CSS `:checked ~ ... [data-tab="..."]` can drive visibility.
    tab_bar = """
        <div class="bl-tabs" role="tablist">
            <input type="radio" name="bl-tab" id="bl-tab-conditions" checked>
            <input type="radio" name="bl-tab" id="bl-tab-hatches">
            <input type="radio" name="bl-tab" id="bl-tab-stocking">
            <input type="radio" name="bl-tab" id="bl-tab-catch">
            <div class="bl-tab-bar">
                <label for="bl-tab-conditions" class="bl-tab" data-tab="conditions">Conditions</label>
                <label for="bl-tab-hatches" class="bl-tab" data-tab="hatches">Hatches</label>
                <label for="bl-tab-stocking" class="bl-tab" data-tab="stocking">Stocking</label>
                <label for="bl-tab-catch" class="bl-tab" data-tab="catch">Log catch</label>
            </div>
            <div class="bl-tab-panels">
                """ + conditions_panel + hatches_panel + stocking_panel + catch_panel + """
            </div>
        </div>"""
    return tab_bar


def build_river_popup_html(river: dict) -> str:
    primary = _primary_gauge(river["gauges"])
    chart_html = ""
    if primary and primary.get("site_no"):
        chart_html = (f'<div class="bl-flow-chart" '
                      f'data-site="{primary["site_no"]}"></div>')
    return f"""
        <div class="bl-card">
            {_panel_header_html(river)}
            <div class="bl-card-body">
                {_panel_tabs_html(river, chart_html)}
            </div>
        </div>
    """


# -- Helpers --

def _resolve_states(state: str) -> list[str] | None:
    # Single state only. A nationwide "all" would fan out to ~51 USGS
    # calls per request and blow the <10s budget -- broad/"near me"
    # discovery is Phase 5 (viewport loading), not a 51-state union.
    state = state.upper()
    return [state] if state in STATES else None


# USGS station names look like "GUNPOWDER FALLS NEAR GLENCOE, MD". The river
# is the part before the first locator word; "North Branch ..." stays
# distinct. Heuristic + tunable (see plan's HUC/GNIS follow-up).
_LOCATOR_RE = re.compile(r"\b(near|nr|at|abv|above|blw|below|ab|bl)\b", re.I)
_RANK = {"green": 0, "yellow": 1, "red": 2, "gray": 3}


def _river_key(site_name: str) -> tuple[str, str]:
    """(grouping_key, display_name) for a USGS station name."""
    base = re.sub(r",\s*[A-Za-z]{2}\.?\s*$", "", site_name).strip()
    head = base
    m = _LOCATOR_RE.search(base)
    if m:
        head = base[:m.start()]
    head = head.strip(" ,-.").strip() or base or site_name.strip()
    display = head.title()
    return display.lower(), display


_trout_warming: set[str] = set()


def _trout_for_state(st: str):
    """Cached trout gdf, or None while a one-shot background warm runs.

    The keyset fetch can be slow, so requests never block on it -- trout
    tags fill in once the background load caches.
    """
    if trout.is_cached(st):
        return trout.cached_streams(st)
    if st not in _trout_warming:
        _trout_warming.add(st)

        async def _warm():
            try:
                await asyncio.to_thread(load_trout_streams, st)
            except Exception as exc:
                logger.warning("trout warm failed for %s: %s", st, exc)
            finally:
                _trout_warming.discard(st)

        asyncio.create_task(_warm())
    return None


def _trout_geojson_str(layers: list) -> str:
    """Merge per-state TroutLayer feature lists into one GeoJSON
    FeatureCollection string. GZipMiddleware compresses the body."""
    features: list[dict] = []
    for layer in layers:
        if layer is not None:
            features.extend(layer.features)
    return json.dumps({"type": "FeatureCollection", "features": features},
                      separators=(",", ":"))


async def _assemble_rivers(time_series: list, trout_layers: list,
                           stocked_pts: list,
                           access_pts: list | None = None) -> list[dict]:
    """Shared core: aggregate USGS sites -> group into rivers -> popups.
    `trout_layers` is a list of TroutLayer|None (a gauge is on trout if
    near ANY). `access_pts` is the bundled+live access-point list for
    the state(s) being assembled; used to compute each river's
    nearby-access count for the panel stat grid. Defaults to empty so
    test callers that don't care about the count don't have to plumb
    it through."""
    access_pts = access_pts or []
    today = datetime.now()
    today_key = (today.month, today.day)
    month_now = today.month
    tgs = [g for g in trout_layers if g is not None]

    sites = defaultdict(lambda: {"variables": [], "site_no": None})
    for series in time_series:
        source_info = series.get("sourceInfo", {})
        site_name = source_info.get("siteName", "Unknown").capitalize()
        site_no = source_info.get("siteCode", [{}])[0].get("value", "")
        geo = source_info.get("geoLocation", {}).get("geogLocation", {})
        latitude = geo.get("latitude")
        longitude = geo.get("longitude")
        variable_description = series.get("variable", {}).get("variableDescription")
        values_list = series.get("values", [])
        if values_list:
            value_data = values_list[0].get("value", [])
            if value_data:
                value_entry = value_data[0]
                key = (site_name, latitude, longitude)
                sites[key]["variables"].append({
                    "variable": variable_description,
                    "value": value_entry.get("value"),
                    "dateTime": value_entry.get("dateTime"),
                })
                if site_no:
                    sites[key]["site_no"] = site_no

    discharge_site_nos = []
    for (_name, _lat, _lon), info in sites.items():
        sn = info.get("site_no")
        if sn and any(
            "discharge" in v.get("variable", "").lower() or
            "streamflow" in v.get("variable", "").lower()
            for v in info["variables"]
        ):
            discharge_site_nos.append(sn)
    if discharge_site_nos:
        await _ensure_medians_cached(discharge_site_nos)

    # Authoritative NHD identity in one batched DB read. Gauges backfilled
    # by precompute have an entry; others fall back to the station-name
    # heuristic in _river_key.
    all_site_nos = [info["site_no"] for info in sites.values()
                    if info.get("site_no")]
    gauge_metas: dict[str, dict] = {}
    if all_site_nos:
        try:
            gauge_metas = await asyncio.to_thread(
                db.get_gauge_metas, all_site_nos)
        except Exception as exc:
            logger.warning("gauge_metas read failed: %s", exc)

    groups: dict[str, dict] = {}
    for (site_name, latitude, longitude), info in sites.items():
        if not latitude or not longitude:
            continue
        variables = info["variables"]
        site_no = info.get("site_no")
        historical_median = _stats_cache.get(site_no, {}).get(today_key) if site_no else None
        conditions = score_conditions(variables, historical_median)
        on_trout = any(is_near_trout_stream(latitude, longitude, g) for g in tgs)
        gnis = (gauge_metas.get(site_no, {}).get("gnis_name") if site_no
                else None)
        lpid = (gauge_metas.get(site_no, {}).get("levelpathid") if site_no
                else None)
        if gnis:
            key, display = gnis.strip().lower(), gnis.strip()
        else:
            key, display = _river_key(site_name)
        g = groups.setdefault(key, {
            "name": display, "lats": [], "lons": [],
            "on_trout": False, "gauges": [],
            # Collected so the client can match a clicked clickable-stream
            # reach by NHD levelpath even when NHD/NLDI disagree on the
            # gauge's GNIS name -- a more durable fallback than name-only.
            "levelpathids": set(),
        })
        g["lats"].append(latitude)
        g["lons"].append(longitude)
        g["on_trout"] = g["on_trout"] or on_trout
        if lpid is not None:
            g["levelpathids"].add(lpid)
        g["gauges"].append({
            "site_name": site_name, "site_no": site_no,
            "variables": variables, "conditions": conditions,
            "historical_median": historical_median,
        })

    rivers: list[dict] = []
    for g in groups.values():
        clat = sum(g["lats"]) / len(g["lats"])
        clon = sum(g["lons"]) / len(g["lons"])
        overall = min(
            (gg["conditions"]["overall"] for gg in g["gauges"]),
            key=lambda o: _RANK.get(o, 3),
        )
        # Per-river curated override first (famous waters), else the
        # geographic zone for the centroid.
        zone = hatches.zone_for_river(g["name"], clat, clon)
        active = hatches.active_hatches(zone, month_now)
        # ~0.03 degrees ≈ 3 km buffer for the panel stat-grid count.
        # Reuses access_points.nearby_access (same helper that backs the
        # on-map access layer) so the count agrees with what the user
        # sees as markers around the river.
        access_count = len(access_points.nearby_access(
            clat, clon, access_pts, buffer_deg=0.03))
        stocked_waters = stocking.nearby_stocked(clat, clon, stocked_pts)
        river = {
            "name": g["name"], "lat": clat, "lon": clon, "overall": overall,
            "on_trout": g["on_trout"], "near_stocked": bool(stocked_waters),
            "hatch_zone": zone, "active": active, "month": month_now,
            "stocked_waters": stocked_waters,
            "access_count": access_count,
            "gauges": sorted(g["gauges"], key=lambda x: x["site_name"]),
        }
        site_no = next(
            (gg["site_no"] for gg in river["gauges"] if gg.get("site_no")), None)
        rivers.append({
            "name": river["name"], "lat": clat, "lon": clon, "site_no": site_no,
            "conditions": {"overall": overall},
            "color": SCORE_COLORS[overall], "label": SCORE_LABELS[overall],
            "on_trout": river["on_trout"], "near_stocked": river["near_stocked"],
            "hatch_zone": zone["name"],
            "active_hatches": [e["common_name"] for e in active],
            "levelpathids": sorted(g["levelpathids"]),
            "popup_html": build_river_popup_html(river),
        })
    return rivers


_STATE_RIVERS_TTL = 120.0  # USGS IV updates ~15-60 min; short cache is plenty
# Bounded + TTL'd: expired entries are actually evicted (the old soft-TTL
# dict only ever grew, one assembled-rivers list per state).
_state_rivers_cache: LruTtl = LruTtl(maxsize=64, ttl=_STATE_RIVERS_TTL)


_state_refreshing: set[str] = set()


def _snapshot_stale(updated_at: str) -> bool:
    try:
        ts = datetime.fromisoformat(updated_at)
    except (ValueError, TypeError):
        return True
    return (datetime.now(timezone.utc) - ts).total_seconds() > _REFRESH_INTERVAL


def _schedule_state_refresh(st: str) -> None:
    """Deduped background precompute for a state -- used for stale or
    never-computed (lazy) states so the request path never blocks on
    USGS. No-ops cleanly when there's no running loop (unit tests)."""
    if st in _state_refreshing:
        return
    _state_refreshing.add(st)

    async def _run():
        try:
            import precompute
            await precompute.refresh_state(st)
        except Exception as exc:
            logger.warning("state refresh failed for %s: %s", st, exc)
        finally:
            _state_refreshing.discard(st)

    try:
        asyncio.create_task(_run())
    except RuntimeError:
        _state_refreshing.discard(st)


async def _rivers_for_state_cached(st: str) -> list[dict]:
    """Snapshot-first: process L1 -> Postgres snapshot -> (background)
    precompute. Never blocks on USGS on the request path. A focused state
    always has a fresh snapshot; a lazy state's first visitor gets [] and
    it fills within one refresh (the client auto-retries)."""
    hit = _state_rivers_cache.get(st)
    if hit is not None:
        return hit
    snap = await asyncio.to_thread(db.get_river_snapshot, st)
    if snap is not None:
        rivers, updated_at = snap
        if rivers:
            _state_rivers_cache[st] = rivers
            if _snapshot_stale(updated_at):
                _schedule_state_refresh(st)  # stale-while-revalidate
            return rivers
    _schedule_state_refresh(st)
    return []


async def _rivers_for_states(states_to_load: list[str]) -> list[dict]:
    per_state = await asyncio.gather(
        *(_rivers_for_state_cached(st) for st in states_to_load)
    )
    return [r for group in per_state for r in group]


_BBOX_MAX_STATES = 4


def _bbox_overlap_area(bbox: tuple[float, float, float, float],
                       sb: tuple[float, float, float, float]) -> float:
    w, s, e, n = bbox
    la0, la1, lo0, lo1 = sb  # STATE_BBOX is (lat_min,lat_max,lon_min,lon_max)
    ow = min(e, lo1) - max(w, lo0)
    oh = min(n, la1) - max(s, la0)
    return ow * oh if ow > 0 and oh > 0 else 0.0


async def _rivers_for_bbox(bbox: tuple[float, float, float, float]) -> list[dict]:
    # USGS's own bBox IV query proved unreliable; instead reuse the proven
    # per-state path (cached) for the states the viewport touches and clip to
    # the box. Same data/trout/stocking as the zoomed-out overview.
    w, s, e, n = bbox
    states = states_in_bbox(w, s, e, n)
    # STATE_BBOX is intentionally over-inclusive; a small box can resolve
    # to several states and each pulls a trout gdf. Cap to the few with
    # the largest actual overlap so one pan can't load many states' geo.
    if len(states) > _BBOX_MAX_STATES:
        states = sorted(
            states,
            key=lambda c: _bbox_overlap_area(bbox, STATE_BBOX[c]),
            reverse=True,
        )[:_BBOX_MAX_STATES]
    per_state = await asyncio.gather(
        *(_rivers_for_state_cached(st) for st in states)
    )
    out: list[dict] = []
    seen: set = set()
    for group in per_state:
        for r in group:
            if not (w <= r["lon"] <= e and s <= r["lat"] <= n):
                continue
            sid = r.get("site_no") or (r["name"], r["lat"], r["lon"])
            if sid in seen:
                continue
            seen.add(sid)
            out.append(r)
    return out


# -- Routes --

_DEFAULT_ROOT_STATE = "MD"


def _root_state(request: Request) -> str:
    """Pick the state code the root redirect should land on.

    Priority:
      1. Explicit `?state=XX` query param (the user told us what they
         want -- e.g. someone pasted a Colorado map link without the
         /map prefix).
      2. Cloudflare's edge geolocation header `CF-IPCountry` +
         `CF-Region-Code`. Present only when blueliner.app is proxied
         through Cloudflare (orange-cloud DNS); they cost nothing,
         require no API call, and CF caches them at the edge. We
         require US country to avoid landing a Canadian user on `BC`
         (which `STATES` doesn't know about anyway).
      3. Default to MD -- the historical behavior, kept as a backstop
         for direct-origin hits and dev.

    Anything that doesn't validate against `STATES` falls through to
    the next tier so a malformed header or query param can't
    300-redirect the user to a broken `/map?state=zz`."""
    raw = (request.query_params.get("state") or "").strip().upper()
    if raw in STATES:
        return raw
    if (request.headers.get("CF-IPCountry") or "").upper() == "US":
        region = (request.headers.get("CF-Region-Code") or "").strip().upper()
        if region in STATES:
            return region
    return _DEFAULT_ROOT_STATE


@app.head("/")
@app.get("/")
async def root(request: Request):
    return RedirectResponse(url=f"/map?state={_root_state(request)}")


@app.get("/healthz")
async def healthz():
    try:
        await asyncio.to_thread(db.healthcheck)
    except Exception as exc:
        logger.error("healthcheck failed: %s", exc)
        raise HTTPException(status_code=503, detail="unhealthy")
    return {"status": "ok"}


_USGS_IV_URL = "https://waterservices.usgs.gov/nwis/iv/"
_EMPTY_IV = {"value": {"timeSeries": []}}


async def _usgs_iv(extra: dict, label: str) -> dict:
    """USGS NWIS instantaneous-values fetch, graceful-empty on any failure
    (a public app must never 500 because USGS is slow/down/rate-limiting)."""
    params = {"format": "json", "siteStatus": "active",
              "siteType": "ST,FA-WWTP,SP,ST-TS", **extra}
    try:
        async with httpx.AsyncClient(
            timeout=25.0, headers={"User-Agent": USER_AGENT}
        ) as client:
            r = await client.get(_USGS_IV_URL, params=params)
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        logger.warning("USGS IV fetch failed for %s: %s", label, exc)
        return _EMPTY_IV


@app.get("/streams")
async def get_streams(state: str = Query(default="MD", description="Two-letter state code")):
    """Raw USGS NWIS instantaneous values for a state (legacy passthrough)."""
    state = state.upper()
    if state not in STATES:
        return {"error": f"Unsupported state: {state}. Supported: {', '.join(STATES.keys())}"}
    return await _usgs_iv({"stateCd": STATES[state]["usgs_code"]}, state)


@app.get("/map")
async def map_shell():
    """Serves the static client shell; state/filters are resolved client-side.

    When the Vite production build artifact exists at
    `static/dist/index.html`, serve that (the version with hashed CSS/JS
    asset references). Falls back to the source `static/index.html` when
    no build has been run -- the dev path, where Vite's dev server
    serves the shell itself on :5173 and this route is only hit by
    direct curls / health checks.
    """
    dist_index = os.path.join(STATIC_DIR, "dist", "index.html")
    if os.path.exists(dist_index):
        return FileResponse(dist_index)
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/sw.js")
async def service_worker():
    # Served from root so the service worker's scope covers the whole app
    # (a /static/ path would only control /static/* requests).
    return FileResponse(
        os.path.join(STATIC_DIR, "sw.js"), media_type="application/javascript"
    )


def _cached_response(request: Request, body: str | bytes, *, max_age: int,
                     s_max_age: int | None = None, swr: int = 86400,
                     media_type: str = "application/json") -> Response:
    """Response with ETag + Cache-Control so the browser, the service
    worker, and any CDN/Cloudflare in front can serve repeats instantly
    and revalidate in the background. Honors If-None-Match.

    `s_max_age` lets the shared cache (Cloudflare) hold longer than the
    browser when an endpoint's payload is stabler at the edge than the
    per-tab freshness contract -- defaults to `max_age`."""
    body_bytes = body.encode() if isinstance(body, str) else body
    etag = '"' + hashlib.sha256(body_bytes).hexdigest()[:32] + '"'
    s = s_max_age if s_max_age is not None else max_age
    headers = {
        "Cache-Control": (f"public, max-age={max_age}, s-maxage={s}, "
                          f"stale-while-revalidate={swr}"),
        "ETag": etag,
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body_bytes, media_type=media_type, headers=headers)


def _cached_json(request: Request, payload, *, max_age: int,
                 s_max_age: int | None = None, swr: int = 86400) -> Response:
    """JSON-encode `payload` and serve through `_cached_response`."""
    return _cached_response(
        request, json.dumps(payload, separators=(",", ":")),
        max_age=max_age, s_max_age=s_max_age, swr=swr,
    )


@app.get("/api/states")
async def api_states(request: Request):
    """Supported states (code, name, map center) -- the client builds the
    selector and centering from this so states.py is the single source.
    Effectively static -> cache hard."""
    payload = [
        {"code": code, "name": info["name"], "center": info["center"]}
        for code, info in sorted(STATES.items(), key=lambda kv: kv[1]["name"])
    ]
    return _cached_json(request, payload, max_age=86400)


def _parse_bbox(bbox: str) -> tuple[float, float, float, float]:
    """'west,south,east,north' -> validated tuple. Raises HTTP 400."""
    try:
        w, s, e, n = (float(x) for x in bbox.split(","))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="bbox must be 'w,s,e,n'")
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise HTTPException(status_code=400, detail="bbox out of range / unordered")
    if (e - w) > 6 or (n - s) > 6:
        # USGS bBox is size-limited and a huge area is too slow; the client
        # only requests bbox when zoomed in, so this is a safety net.
        raise HTTPException(status_code=400, detail="bbox too large; zoom in")
    return (w, s, e, n)


@app.get("/api/rivers")
async def api_rivers(
    request: Request,
    state: str = Query(default="MD", description="Two-letter state code."),
    bbox: str | None = Query(default=None, description="west,south,east,north"),
):
    if bbox is not None:
        wsen = _parse_bbox(bbox)
        rivers = await _rivers_for_bbox(wsen)
        return _cached_json(request, {"bbox": list(wsen), "rivers": rivers},
                            max_age=300)
    states_to_load = _resolve_states(state)
    if states_to_load is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported state: {state}. Supported: {', '.join(sorted(STATES))}",
        )
    rivers = await _rivers_for_states(states_to_load)
    return _cached_json(request, {"state": state.upper(), "rivers": rivers},
                        max_age=300)


async def _river_lines_payload(
    rivers: list[dict],
) -> tuple[dict, list[dict]]:
    """Merge persisted flowlines for these rivers into one
    FeatureCollection -- a pure Postgres read, no external calls. Each
    feature carries site_no + color so the client styles + popups it.
    Also returns the rivers still missing geometry so the caller can
    prioritize backfilling exactly what the user is looking at."""
    by_site = {r["site_no"]: r for r in rivers if r.get("site_no")}
    if not by_site:
        return {"type": "FeatureCollection", "features": []}, []
    geoms = await asyncio.to_thread(db.get_river_geoms, list(by_site))
    feats: list[dict] = []
    for sn, fc in geoms.items():
        color = by_site.get(sn, {}).get("color")
        for f in fc.get("features", []):
            feats.append({"type": "Feature",
                          "properties": {"site_no": sn, "color": color},
                          "geometry": f.get("geometry")})
    missing = [by_site[sn] for sn in by_site if sn not in geoms]
    return {"type": "FeatureCollection", "features": feats}, missing


_lines_backfilling: set[str] = set()


def _schedule_lines_backfill(rivers: list[dict]) -> None:
    """Background NLDI backfill for the rivers the user is viewing right
    now -- prioritizes their state's geometry over the periodic
    refresher's round-robin so clickable lines appear fast on first
    visit. Deduped; no-ops without a running loop (unit tests)."""
    todo = [r for r in rivers
            if r.get("site_no") and r["site_no"] not in _lines_backfilling]
    if not todo:
        return
    for r in todo:
        _lines_backfilling.add(r["site_no"])

    async def _run():
        try:
            import precompute
            await precompute._backfill_geometry(todo)
        except Exception as exc:
            logger.warning("lines backfill failed: %s", exc)
        finally:
            for r in todo:
                _lines_backfilling.discard(r["site_no"])

    try:
        asyncio.create_task(_run())
    except RuntimeError:
        for r in todo:
            _lines_backfilling.discard(r["site_no"])


@app.get("/api/river_lines")
async def api_river_lines(
    request: Request,
    state: str = Query(default="MD", description="Two-letter state code."),
    bbox: str | None = Query(default=None, description="west,south,east,north"),
):
    """Every precomputed clickable flowline for a state/viewport in one
    gzipped payload -- a pure Postgres read, never blocks on NLDI. This
    replaces the slow per-river /api/river_geom fan-out."""
    if bbox is not None:
        rivers = await _rivers_for_bbox(_parse_bbox(bbox))
    else:
        states_to_load = _resolve_states(state)
        if states_to_load is None:
            raise HTTPException(status_code=400,
                                detail=f"Unsupported state: {state}")
        rivers = await _rivers_for_states(states_to_load)
    payload, missing = await _river_lines_payload(rivers)
    if missing:
        _schedule_lines_backfill(missing)  # prioritize the viewed state
    return _cached_json(request, payload, max_age=300)


_REFRESH_TOKEN = os.environ.get("REFRESH_TOKEN", "")


@app.post("/internal/refresh")
async def internal_refresh(request: Request):
    """Trigger a focused-states refresh. An external scheduler (GitHub
    Actions / cron-job.org) hits this on a cadence; the same call doubles
    as a keep-warm ping so the free-tier web service never sleeps.
    Token-gated (set REFRESH_TOKEN; unset => always 403)."""
    if not _REFRESH_TOKEN or request.headers.get("x-refresh-token") != _REFRESH_TOKEN:
        raise HTTPException(status_code=403, detail="forbidden")
    import precompute
    asyncio.create_task(precompute.refresh_focused())
    return {"status": "scheduled", "states": precompute.focused_states()}


@app.get("/api/trout")
async def api_trout(request: Request,
                    state: str = Query(default="MD",
                                       description="Two-letter state code.")):
    states_to_load = _resolve_states(state)
    if states_to_load is None:
        raise HTTPException(status_code=400, detail=f"Unsupported state: {state}")
    # Non-blocking: cached layer or empty until the background warm completes.
    layers = [_trout_for_state(st) for st in states_to_load]
    body = _trout_geojson_str(layers)
    # While warming, the response is the empty FC string -- short TTL so
    # Cloudflare doesn't pin an empty layer for the day. Real responses
    # change rarely (upstream dataset refreshes), so cache them hard.
    warming = all(layer is None for layer in layers)
    if warming:
        return _cached_response(request, body, max_age=60, s_max_age=300)
    return _cached_response(request, body, max_age=3600, s_max_age=86400)


@app.get("/api/access")
async def api_access(request: Request,
                     state: str = Query(default="MD",
                                        description="Two-letter state code.")):
    """Angler access points (boat ramps, walk-ins, piers, parking, wading
    spots) as GeoJSON. Bundled per-state baseline + a state-DNR live
    overlay for any state whose ArcGIS endpoint has been verified
    (`access_points.ACCESS_SOURCES`)."""
    states_to_load = _resolve_states(state)
    if states_to_load is None:
        raise HTTPException(status_code=400, detail=f"Unsupported state: {state}")
    # Build the FeatureCollection in a thread so a slow live overlay
    # fetch doesn't block the request handler.
    fcs = await asyncio.to_thread(
        lambda: [access_points.access_points_geojson(st)
                 for st in states_to_load])
    features: list[dict] = []
    for fc in fcs:
        features.extend(fc.get("features", []))
    body = json.dumps({"type": "FeatureCollection", "features": features},
                      separators=(",", ":"))
    # Baseline data is in-memory + stable across deploys; live overlay
    # changes rarely. Long browser cache + day-long CDN cache like
    # /api/trout.
    return _cached_response(request, body, max_age=3600, s_max_age=86400)


_EMPTY_FC = {"type": "FeatureCollection", "features": []}


def _simplify_fc(fc: dict, tol: float = 0.0005) -> dict:
    """Decimate flowline vertices (~50m) before persisting/serving so the
    merged per-state /api/river_lines payload stays small. Best-effort
    per feature; on any geometry error keep the original."""
    out: list[dict] = []
    for f in fc.get("features", []):
        try:
            g = shape(f["geometry"]).simplify(tol)
            if g.is_empty:
                continue
            out.append({"type": "Feature",
                        "properties": f.get("properties", {}),
                        "geometry": mapping(g)})
        except Exception:
            out.append(f)
    return {"type": "FeatureCollection", "features": out}


def _fetch_nav(client: httpx.Client, base: str, nav: str, dist: str) -> list:
    try:
        r = _nldi_get(client, f"{base}/{nav}/flowlines",
                      params={"distance": dist})
        if r is None:
            return []
        r.raise_for_status()
        return r.json().get("features", [])
    except Exception:
        return []


def _nldi_flowline(site_no: str) -> dict:
    """Main-stem flowline around a USGS gauge from USGS NLDI.

    Three-tier filter against the "tributary flowline visually extends
    onto the larger main stem at the confluence" failure mode:

      1. **LevelPathID** (preferred). The gauge's COMID has a NHDPlusV2
         LevelPathID -- a deterministic ID for the river's main path
         through confluences. Walk the full reach (UM 75 / DM 40 km),
         then drop any feature whose LevelPathID disagrees. No string
         matching, no fallback to unfiltered: an empty result means
         "we couldn't identify the same river," which is honest.
      2. **gnis_name** (degraded). For gauges outside the loaded VAA
         regions (i.e. nhdplus_vaa has no row for the COMID), fall
         back to NHD's name field via per-COMID NLDI lookups. Less
         reliable because NHD's name attribution is incomplete; the
         no-fallback rule still applies.
      3. **No filter** (last resort). No gnis_name either -> conservative
         short walk (UM 15 / DM 10 km), no filter; just bounded by
         walk distance.

    Served from process LRU -> Postgres -> NLDI with write-through.
    Rows written under an older walk/filter schema (no `_walk_version`,
    or older than the current constant) are treated as misses so they
    refetch."""
    if site_no in _river_geom_cache:
        return _river_geom_cache[site_no]
    try:
        stored = db.get_river_geom(site_no)
    except Exception as exc:
        logger.warning("river_geom read failed for %s: %s", site_no, exc)
        stored = None
    if (stored is not None
            and stored.get("_walk_version") == _GEOM_SCHEMA_VERSION):
        _river_geom_cache[site_no] = stored
        return stored

    meta = _nldi_gauge_meta(site_no)
    target_lpid = meta.get("levelpathid") if meta else None
    target_gnis = (meta.get("gnis_name") or "").strip().lower() if meta else ""
    have_identity = bool(target_lpid or target_gnis)
    nav = (("UM", "75"), ("DM", "40")) if have_identity \
          else (("UM", "15"), ("DM", "10"))

    base = f"https://api.water.usgs.gov/nldi/linked-data/nwissite/USGS-{site_no}/navigation"
    feats: list[dict] = []
    try:
        with httpx.Client(timeout=15.0, headers={"User-Agent": USER_AGENT}) as c:
            with ThreadPoolExecutor(max_workers=2) as ex:
                parts = list(ex.map(
                    lambda nd: _fetch_nav(c, base, nd[0], nd[1]), nav))
            for p in parts:
                feats.extend(p)
    except Exception:
        feats = []

    if feats:
        if target_lpid is not None:
            feats = _filter_flowlines_by_levelpath(feats, int(target_lpid))
        elif target_gnis:
            feats = _filter_flowlines_by_gnis(feats, target_gnis)

    if feats:
        fc = _simplify_fc({"type": "FeatureCollection", "features": feats})
        fc["_walk_version"] = _GEOM_SCHEMA_VERSION
        _river_geom_cache[site_no] = fc
        try:
            db.put_river_geom(site_no, fc)
        except Exception as exc:
            logger.warning("river_geom persist failed for %s: %s", site_no, exc)
        return fc
    _river_geom_cache[site_no] = _EMPTY_FC  # transient: TTL retries later
    return _EMPTY_FC


def _extract_comid(feature: dict) -> int | None:
    """Pull NHDPlus COMID from a flowline feature's properties.
    NLDI returns it under `nhdplus_comid`; some older responses
    used `comid`."""
    props = feature.get("properties") or {}
    cid = props.get("nhdplus_comid") or props.get("comid")
    if cid is None or cid == "":
        return None
    try:
        return int(cid)
    except (TypeError, ValueError):
        return None


def _filter_flowlines_by_levelpath(features: list[dict],
                                   target_lpid: int) -> list[dict]:
    """Keep only flowline features sharing the gauge's NHDPlusV2
    LevelPathID. One batched DB query for the COMIDs in the walk.
    No fallback to unfiltered: empty is honest. If the bundled VAA
    doesn't cover the walked COMIDs at all (e.g. an out-of-region
    walk hop), the caller's tier-2 gnis filter still won't run --
    so geometry can disappear entirely if filtering wipes it,
    which is intentional ("never wrong, sometimes empty")."""
    comids = [_extract_comid(f) for f in features]
    have = [c for c in comids if c is not None]
    if not have:
        return features                          # nothing to look up
    try:
        vaas = db.get_vaas(have)
    except Exception as exc:
        logger.warning("get_vaas failed (%d comids): %s", len(have), exc)
        return features                          # degrade to unfiltered
    return [f for f, c in zip(features, comids)
            if c is not None
            and (vaas.get(c) or {}).get("levelpathid") == target_lpid]


def _filter_flowlines_by_gnis(features: list[dict], target: str) -> list[dict]:
    """Tier-2 fallback when no LevelPathID is available: filter by
    NHD gnis_name. Per-COMID lookups run in parallel and are cached
    forever in Postgres. No fallback to unfiltered (previous safety
    net was exactly what re-introduced the cross-river bleed)."""
    comids = []
    for f in features:
        props = f.get("properties") or {}
        cid = props.get("nhdplus_comid") or props.get("comid") or ""
        comids.append(str(cid) if cid else "")
    if not any(comids):
        return features                          # nothing to filter against
    with ThreadPoolExecutor(max_workers=8) as ex:
        names = list(ex.map(_comid_gnis_lower, comids))
    return [f for f, n in zip(features, names) if n == target]


def _comid_gnis_lower(comid: str) -> str:
    if not comid:
        return ""
    return (_comid_meta(comid).get("gnis_name") or "").strip().lower()


_NLDI_BASE = "https://api.water.usgs.gov/nldi/linked-data"

# Retry/backoff for NLDI throttling. USGS NLDI rate-limits at ~10
# req/s/IP and starts returning 429 quickly when the focused-states
# geom backfill fans out hundreds of navigation calls. Without retry
# the gauge just lost its flowline for the full refresh interval (~45
# min) -- the geometry would render as an unnamed dot on the map.
# Exponential backoff with jitter retries the call a few times so the
# transient throttle window passes; longer outages still fail clean
# and retry on the next refresh cycle.
_NLDI_RETRY_STATUSES = (429, 503)
_NLDI_MAX_RETRIES = 3       # 4 attempts total (initial + 3 retries)
_NLDI_BACKOFF_BASE = 0.5    # seconds; doubles each attempt
_NLDI_BACKOFF_MAX = 8.0     # cap for both the backoff and Retry-After


def _nldi_get(client: httpx.Client, url: str, *,
              params: dict | None = None) -> httpx.Response | None:
    """GET an NLDI URL with backoff on 429/503.

    Returns the final httpx.Response (caller decides whether to call
    raise_for_status / json()), or None on a network-level error
    (timeout, DNS, connection reset). Honors the Retry-After response
    header when present, capped at _NLDI_BACKOFF_MAX so a hostile or
    misconfigured upstream can't park us indefinitely."""
    last: httpx.Response | None = None
    for attempt in range(_NLDI_MAX_RETRIES + 1):
        try:
            last = client.get(url, params=params)
        except httpx.RequestError as exc:
            logger.warning("NLDI request error %s on %s: %s",
                           type(exc).__name__, url, exc)
            return None
        if last.status_code not in _NLDI_RETRY_STATUSES:
            return last
        if attempt == _NLDI_MAX_RETRIES:
            logger.warning("NLDI %s after %d retries: %s",
                           last.status_code, _NLDI_MAX_RETRIES, url)
            return last
        ra = last.headers.get("Retry-After")
        wait: float
        if ra:
            try:
                wait = min(float(ra), _NLDI_BACKOFF_MAX)
            except ValueError:
                wait = min(_NLDI_BACKOFF_BASE * (2 ** attempt),
                           _NLDI_BACKOFF_MAX)
        else:
            wait = min(_NLDI_BACKOFF_BASE * (2 ** attempt),
                       _NLDI_BACKOFF_MAX)
        wait += random.uniform(0.0, 0.5)
        time.sleep(wait)
    return last


def _nldi_gauge_meta(site_no: str) -> dict:
    """Authoritative NHD identity for a USGS gauge.

    Returns {comid, gnis_name, levelpathid}:
      - `comid`/`gnis_name` from NLDI (two calls: gauge -> COMID,
        COMID -> reach attributes).
      - `levelpathid` from the local NHDPlusV2 VAA table when available
        (drives the topologically-correct flowline filter).

    Process LRU -> Postgres -> NLDI/VAA with write-through. Empties
    (network/lookup failures) are NOT persisted so they retry, but
    stay briefly in the process cache to throttle re-attempts."""
    if site_no in _gauge_meta_cache:
        return _gauge_meta_cache[site_no]
    try:
        stored = db.get_gauge_meta(site_no)
    except Exception as exc:
        logger.warning("gauge_meta read failed for %s: %s", site_no, exc)
        stored = None
    if stored is not None:
        # Backfill levelpathid on older rows (written before VAA landed)
        # without an extra NLDI roundtrip. Cheap local lookup; persist
        # so the next read short-circuits.
        if "levelpathid" not in stored and stored.get("comid"):
            try:
                vaa = db.get_vaa(int(stored["comid"]))
            except Exception:
                vaa = None
            stored = dict(stored,
                          levelpathid=(vaa or {}).get("levelpathid"))
            try:
                db.put_gauge_meta(site_no, stored)
            except Exception as exc:
                logger.warning("gauge_meta backfill failed for %s: %s",
                               site_no, exc)
        _gauge_meta_cache[site_no] = stored
        return stored

    meta: dict = {}
    try:
        with httpx.Client(timeout=10.0, headers={"User-Agent": USER_AGENT}) as c:
            r1 = _nldi_get(c, f"{_NLDI_BASE}/nwissite/USGS-{site_no}")
            if r1 is None:
                raise httpx.RequestError("nwissite lookup failed")
            r1.raise_for_status()
            feats = r1.json().get("features") or []
            comid = None
            if feats:
                comid = feats[0].get("properties", {}).get("comid")
            gnis = None
            if comid:
                r2 = _nldi_get(c, f"{_NLDI_BASE}/comid/{comid}")
                if r2 is not None:
                    r2.raise_for_status()
                    feats2 = r2.json().get("features") or []
                    if feats2:
                        gnis = feats2[0].get("properties", {}).get("gnis_name")
            if comid:
                lpid = None
                try:
                    vaa = db.get_vaa(int(comid))
                    if vaa:
                        lpid = vaa.get("levelpathid")
                except Exception:
                    pass
                meta = {"comid": str(comid),
                        "gnis_name": gnis or None,
                        "levelpathid": lpid}
    except Exception:
        meta = {}

    _gauge_meta_cache[site_no] = meta
    if meta:
        try:
            db.put_gauge_meta(site_no, meta)
        except Exception as exc:
            logger.warning("gauge_meta persist failed for %s: %s", site_no, exc)
    return meta


def _comid_meta(comid: str) -> dict:
    """NHD attributes ({gnis_name}) for an individual flowline reach by
    COMID. Same caching pattern as _nldi_gauge_meta -- process LRU ->
    Postgres -> NLDI with write-through; empties not persisted so they
    retry. One call per unfamiliar COMID; subsequent gauges on the
    same river hit Postgres."""
    if not comid:
        return {}
    if comid in _comid_meta_cache:
        return _comid_meta_cache[comid]
    try:
        stored = db.get_comid_meta(comid)
    except Exception as exc:
        logger.warning("comid_meta read failed for %s: %s", comid, exc)
        stored = None
    if stored is not None:
        _comid_meta_cache[comid] = stored
        return stored

    meta: dict = {}
    try:
        with httpx.Client(timeout=10.0, headers={"User-Agent": USER_AGENT}) as c:
            r = _nldi_get(c, f"{_NLDI_BASE}/comid/{comid}")
            if r is None:
                raise httpx.RequestError("comid lookup failed")
            r.raise_for_status()
            feats = r.json().get("features") or []
            if feats:
                gnis = feats[0].get("properties", {}).get("gnis_name")
                meta = {"gnis_name": gnis or None}
    except Exception:
        meta = {}

    _comid_meta_cache[comid] = meta
    if meta:
        try:
            db.put_comid_meta(comid, meta)
        except Exception as exc:
            logger.warning("comid_meta persist failed for %s: %s", comid, exc)
    return meta


@app.get("/api/river_geom")
async def api_river_geom(
    request: Request,
    site_no: str = Query(..., pattern=r"^[0-9A-Za-z-]{4,20}$",
                         description="USGS site number"),
):
    """Clickable river flowline geometry (USGS NLDI), cached per site.

    Real geometry is ~immutable per site (invalidated only by bumping
    `_GEOM_SCHEMA_VERSION`), so cache hard. Empty results mean NLDI
    failed transiently -- short TTL so the edge retries soon."""
    fc = await asyncio.to_thread(_nldi_flowline, site_no)
    if not fc.get("features"):
        return _cached_json(request, fc, max_age=60, s_max_age=300)
    return _cached_json(request, fc, max_age=86400, s_max_age=604800)


@app.get("/api/history")
async def api_history(
    request: Request,
    site_no: str = Query(..., pattern=r"^[0-9A-Za-z-]{4,20}$",
                         description="USGS site number"),
):
    """Proxies ~1 year of USGS daily values (discharge + water temp).

    History is served live from USGS, never stored locally.
    """
    url = "https://waterservices.usgs.gov/nwis/dv/"
    params = {
        "format": "json",
        "sites": site_no,
        "period": "P365D",
        "parameterCd": "00060,00010",
        "statCd": "00003",
        "siteStatus": "all",
    }
    async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": USER_AGENT}) as client:
        resp = await client.get(url, params=params)
    try:
        data = resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail="USGS daily values unavailable")

    series = []
    for ts in data.get("value", {}).get("timeSeries", []):
        var = ts.get("variable", {})
        code = var.get("variableCode", [{}])[0].get("value")
        name = var.get("variableName") or var.get("variableDescription")
        unit = var.get("unit", {}).get("unitCode")
        points = []
        for v in (ts.get("values") or [{}])[0].get("value", []):
            try:
                val = float(v.get("value"))
            except (TypeError, ValueError):
                continue
            if val <= -999999:  # USGS no-data sentinel
                continue
            points.append({"date": v.get("dateTime"), "value": val})
        if points:
            series.append({"parameter": code, "name": name, "unit": unit,
                            "points": points})
    payload = {"site_no": site_no, "series": series}
    # Daily values update ~daily; hold ~hour at edge, less when empty so
    # a transient USGS gap doesn't pin no-data for an hour.
    if not series:
        return _cached_json(request, payload, max_age=60, s_max_age=300)
    return _cached_json(request, payload, max_age=900, s_max_age=3600)


class PinIn(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    note: str = Field(default="", max_length=500)


# Best-effort, per-process fixed-window limiter on the one public write
# endpoint. Not exact across gunicorn workers -- it's abuse mitigation, not
# a quota. (A shared store, e.g. Redis, would be the multi-instance answer.)
_PIN_RATE_MAX = int(os.environ.get("PIN_RATE_MAX", "20"))
_PIN_RATE_WINDOW = 60.0
_pin_hits: dict[str, tuple[float, int]] = {}


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit_pins(request: Request) -> None:
    now = time.time()
    if len(_pin_hits) > 5000:  # bound memory: drop stale windows
        for k, (s, _) in list(_pin_hits.items()):
            if now - s >= _PIN_RATE_WINDOW:
                _pin_hits.pop(k, None)
    ip = _client_ip(request)
    start, count = _pin_hits.get(ip, (now, 0))
    if now - start >= _PIN_RATE_WINDOW:
        start, count = now, 0
    count += 1
    _pin_hits[ip] = (start, count)
    if count > _PIN_RATE_MAX:
        retry = int(_PIN_RATE_WINDOW - (now - start)) + 1
        raise HTTPException(
            status_code=429, detail="Too many pins, slow down.",
            headers={"Retry-After": str(retry)},
        )


_SESSION_COOKIE = "bl_session"


def _session_user(request: Request) -> dict | None:
    """Validated session-cookie user, or None. Lookup is one indexed
    DB hit (token_hash PK); negligible per-request cost."""
    cookies = getattr(request, "cookies", None) or {}
    token = (cookies.get(_SESSION_COOKIE) or "").strip()
    if not token:
        return None
    try:
        return db.user_from_session(token)
    except Exception:
        return None


def _owner(request: Request, required: bool = True) -> str | None:
    """Derive a stable owner id for write-scoped resources.

    Resolution order:
      1. Authenticated session cookie -> `user:{id}` (the "real" owner).
      2. Legacy device-token header -> SHA-256(token) (anonymous flow).

    Keeps anonymous pins working while accounts roll out; signed-in
    users own pins under a stable user-namespaced id even across
    devices.
    """
    user = _session_user(request)
    if user:
        return f"user:{user['id']}"
    token = request.headers.get("x-device-token", "").strip()
    if not (8 <= len(token) <= 200):
        if required:
            raise HTTPException(status_code=400, detail="Missing identity")
        return None
    return hashlib.sha256(token.encode()).hexdigest()


def _device_owner(request: Request) -> str | None:
    """Just the device-token-derived owner (no session fallback).
    Used by the pin-claim flow: 'what anonymous pins does this device
    have that we could relink to the freshly-signed-in user?'"""
    token = request.headers.get("x-device-token", "").strip()
    if not (8 <= len(token) <= 200):
        return None
    return hashlib.sha256(token.encode()).hexdigest()


_PINS_NO_CACHE = {"Cache-Control": "private, no-store"}


@app.get("/api/pins")
async def api_list_pins(request: Request):
    """Per-device pins (keyed by x-device-token). Explicit `private,
    no-store` so a future CDN cache rule can't accidentally cross-serve
    one device's pins to another."""
    owner = _owner(request, required=False)
    pins = [] if owner is None else await asyncio.to_thread(db.list_pins, owner)
    return Response(content=json.dumps(pins), media_type="application/json",
                    headers=_PINS_NO_CACHE)


@app.post("/api/pins")
async def api_add_pin(pin: PinIn, request: Request):
    _rate_limit_pins(request)
    owner = _owner(request, required=True)
    return await asyncio.to_thread(db.add_pin, pin.lat, pin.lon, pin.note, owner)


@app.delete("/api/pins/{pin_id}")
async def api_delete_pin(pin_id: int, request: Request):
    owner = _owner(request, required=True)
    deleted = await asyncio.to_thread(db.delete_pin, pin_id, owner)
    if not deleted:
        raise HTTPException(status_code=404, detail="Pin not found")
    return {"ok": True}


# -- Accounts (Phase 1) ------------------------------------------------

class _MagicLinkIn(BaseModel):
    email: EmailStr


class _DisplayNameIn(BaseModel):
    display_name: str


_AUTH_RATE_MAX = 10              # per IP per window
_AUTH_RATE_WINDOW = 600.0         # 10 min
_auth_hits: dict[str, list[float]] = {}


def _rate_limit_auth(request: Request) -> None:
    """Cheap per-IP rate-limit on magic-link issuance. Same pattern as
    `_rate_limit_pins`; protects Resend's free-tier budget + slows
    enumeration attempts."""
    ip = (request.client.host if request.client else "unknown") or "unknown"
    now = time.time()
    bucket = [t for t in _auth_hits.get(ip, []) if now - t < _AUTH_RATE_WINDOW]
    if len(bucket) >= _AUTH_RATE_MAX:
        raise HTTPException(status_code=429, detail="Too many requests")
    bucket.append(now)
    _auth_hits[ip] = bucket


def _set_session_cookie(response: Response, token: str) -> None:
    """30-day session cookie. HttpOnly + SameSite=Lax + Secure-in-prod."""
    response.set_cookie(
        key=_SESSION_COOKIE,
        value=token,
        max_age=30 * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=bool(os.environ.get("RENDER")),   # auto-true on Render
        path="/",
    )


@app.post("/api/auth/request-link", status_code=204)
async def api_request_magic_link(body: _MagicLinkIn, request: Request):
    """Issue a magic-link to the supplied email. Always returns 204 --
    no account-enumeration leak (the UI shows 'Check your inbox' state
    unconditionally)."""
    _rate_limit_auth(request)
    email = body.email.strip().lower()
    token = secrets.token_urlsafe(24)             # 192 bits, URL-safe
    consume_url = (
        f"{str(request.base_url).rstrip('/')}/auth/consume?token={token}")
    try:
        await asyncio.to_thread(db.create_magic_link, email, token)
    except Exception as exc:
        logger.warning("create_magic_link failed for %s: %s", email, exc)
        return Response(status_code=204)         # still no enumeration
    import email_send                              # local import: optional dep
    try:
        await asyncio.to_thread(
            email_send.send_magic_link, email, consume_url,
            db.MAGIC_LINK_TTL_MINUTES)
    except Exception as exc:
        logger.warning("send_magic_link failed for %s: %s", email, exc)
    return Response(status_code=204)


@app.get("/auth/consume", response_class=HTMLResponse)
async def auth_consume(token: str = Query(..., min_length=8, max_length=64)):
    """Validate the magic-link token, mint a session, set cookie,
    redirect to /. On failure, render a small error page with a link
    to request a fresh one. Server-rendered HTML keeps this independent
    of the SPA so first-time users don't hit a blank page mid-load."""
    email = await asyncio.to_thread(db.consume_magic_link, token)
    if not email:
        return HTMLResponse(_consume_error_html(), status_code=400)

    user = await asyncio.to_thread(db.upsert_user_by_email, email)
    sess_token = secrets.token_urlsafe(32)
    # Best-effort persist of UA/IP for the session row.
    # (Not asked here; just no client context to capture cleanly.)
    await asyncio.to_thread(db.create_session, user["id"], sess_token,
                            None, None)
    resp = HTMLResponse(_consume_success_html(user["email"]))
    _set_session_cookie(resp, sess_token)
    return resp


def _consume_success_html(email: str) -> str:
    safe = email.replace("<", "&lt;").replace(">", "&gt;")
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<meta http-equiv='refresh' content='0; url=/'>"
        "<title>Signed in</title>"
        "<style>body{font-family:system-ui,sans-serif;display:flex;"
        "align-items:center;justify-content:center;height:100vh;margin:0;"
        "background:#f7f9fc;color:#222}"
        ".card{background:#fff;padding:24px 32px;border-radius:10px;"
        "box-shadow:0 1px 6px rgba(0,0,0,.08);text-align:center}"
        ".ok{font-size:48px;color:#27ae60;line-height:1}"
        "</style>"
        "<div class='card'>"
        "<div class='ok'>&#10003;</div>"
        f"<h3 style='margin:12px 0 4px'>Signed in as {safe}</h3>"
        "<p style='color:#666;margin:0'>Redirecting&hellip;</p>"
        "</div>")


def _consume_error_html() -> str:
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<title>Link expired</title>"
        "<style>body{font-family:system-ui,sans-serif;display:flex;"
        "align-items:center;justify-content:center;height:100vh;margin:0;"
        "background:#f7f9fc;color:#222}"
        ".card{background:#fff;padding:24px 32px;border-radius:10px;"
        "box-shadow:0 1px 6px rgba(0,0,0,.08);text-align:center;"
        "max-width:380px}"
        ".warn{font-size:48px;color:#e67e22;line-height:1}"
        "a.btn{display:inline-block;background:#1e6fd9;color:#fff;"
        "text-decoration:none;padding:10px 18px;border-radius:6px;"
        "margin-top:12px;font-weight:600}"
        "</style>"
        "<div class='card'>"
        "<div class='warn'>&#9888;</div>"
        "<h3 style='margin:12px 0 8px'>This sign-in link is no longer valid</h3>"
        "<p style='color:#666'>It may have expired or already been used.</p>"
        "<a class='btn' href='/'>Back to Blueliner</a>"
        "</div>")


@app.post("/api/auth/logout", status_code=204)
async def api_logout(request: Request):
    token = request.cookies.get(_SESSION_COOKIE, "").strip()
    if token:
        await asyncio.to_thread(db.delete_session, token)
    resp = Response(status_code=204)
    resp.delete_cookie(_SESSION_COOKIE, path="/")
    return resp


@app.get("/api/me")
async def api_me(request: Request):
    user = _session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not signed in")
    return {"id": user["id"], "email": user["email"],
            "display_name": user.get("display_name")}


@app.patch("/api/me")
async def api_me_update(body: _DisplayNameIn, request: Request):
    user = _session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not signed in")
    await asyncio.to_thread(
        db.update_user_display_name, user["id"], body.display_name)
    refreshed = await asyncio.to_thread(db.get_user, user["id"])
    return {"id": refreshed["id"], "email": refreshed["email"],
            "display_name": refreshed.get("display_name")}


@app.delete("/api/me", status_code=204)
async def api_me_delete(request: Request):
    user = _session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not signed in")
    await asyncio.to_thread(db.soft_delete_user, user["id"])
    resp = Response(status_code=204)
    resp.delete_cookie(_SESSION_COOKIE, path="/")
    return resp


@app.get("/api/pins/claimable")
async def api_pins_claimable(request: Request):
    """List the device-token-owned pins this signed-in user could
    claim. Empty when no device token, no anonymous pins, or none
    that belong solely to the device (already-claimed ones don't
    show here because they're under the user owner now)."""
    user = _session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not signed in")
    device = _device_owner(request)
    if not device:
        return []
    return await asyncio.to_thread(db.list_pins_for_device_token, device)


@app.post("/api/pins/claim")
async def api_pins_claim(request: Request):
    """Relink the device-token-owned anonymous pins to the signed-in
    user. One-shot: subsequent calls find no anonymous pins and do
    nothing (returns claimed=0)."""
    user = _session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not signed in")
    device = _device_owner(request)
    if not device:
        return {"claimed": 0}
    n = await asyncio.to_thread(
        db.claim_pins, device, f"user:{user['id']}")
    return {"claimed": n}


# -- Catch log (Phase 2) ----------------------------------------------

class _CatchIn(BaseModel):
    occurred_at: str | None = None
    river_name: str | None = None
    river_site_no: str | None = None
    lat: float | None = None
    lon: float | None = None
    species: str
    length_in: float | None = None
    fly_used: str | None = None
    notes: str | None = None


class _CatchPatch(BaseModel):
    occurred_at: str | None = None
    river_name: str | None = None
    species: str | None = None
    length_in: float | None = None
    fly_used: str | None = None
    notes: str | None = None


def _require_user(request: Request) -> dict:
    user = _session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to log catches")
    return user


def _parse_when(occurred_at: str | None) -> datetime:
    """Parse the client's occurred_at (ISO) into an aware UTC datetime;
    fall back to now on anything unparseable."""
    if occurred_at:
        try:
            dt = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass
    return datetime.now(timezone.utc)


@app.get("/api/catches/enrichment-preview")
async def api_enrichment_preview(
    request: Request,
    lat: float = Query(...), lon: float = Query(...),
    site_no: str | None = Query(default=None),
    river_name: str | None = Query(default=None),
    occurred_at: str | None = Query(default=None),
):
    """Live conditions snapshot for the catch form's 'auto-captured'
    block, before save. Requires sign-in (same surface as logging)."""
    _require_user(request)
    when = _parse_when(occurred_at)
    env = await asyncio.to_thread(
        enrichment.build_env, lat, lon, site_no, river_name, when)
    return env


@app.post("/api/catches", status_code=201)
async def api_add_catch(body: _CatchIn, request: Request):
    user = _require_user(request)
    if not (body.species or "").strip():
        raise HTTPException(status_code=422, detail="Species is required")
    when = _parse_when(body.occurred_at)
    # Build the authoritative env snapshot server-side at save time.
    env = None
    if body.lat is not None and body.lon is not None:
        env = await asyncio.to_thread(
            enrichment.build_env, body.lat, body.lon,
            body.river_site_no, body.river_name, when)
    data = body.model_dump()
    data["occurred_at"] = when.isoformat()
    data["species"] = body.species.strip()
    catch = await asyncio.to_thread(db.add_catch, user["id"], data, env)
    return catch


@app.get("/api/catches")
async def api_list_catches(
    request: Request,
    species: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    limit: int = Query(default=200, le=500),
):
    user = _require_user(request)
    items = await asyncio.to_thread(
        db.list_catches, user["id"], species=species,
        date_from=date_from, date_to=date_to, limit=limit)
    total = await asyncio.to_thread(db.count_catches, user["id"])
    return {"total": total, "catches": items}


@app.get("/api/catches/{catch_id}")
async def api_get_catch(catch_id: int, request: Request):
    user = _require_user(request)
    catch = await asyncio.to_thread(db.get_catch, catch_id)
    if not catch or catch["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Catch not found")
    return catch


@app.patch("/api/catches/{catch_id}")
async def api_update_catch(catch_id: int, body: _CatchPatch,
                           request: Request):
    user = _require_user(request)
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    if "species" in data:
        data["species"] = (data["species"] or "").strip()
    if "occurred_at" in data:
        data["occurred_at"] = _parse_when(data["occurred_at"]).isoformat()
    updated = await asyncio.to_thread(
        db.update_catch, catch_id, user["id"], data)
    if not updated:
        raise HTTPException(status_code=404, detail="Catch not found")
    return updated


@app.delete("/api/catches/{catch_id}", status_code=204)
async def api_delete_catch(catch_id: int, request: Request):
    user = _require_user(request)
    ok = await asyncio.to_thread(db.delete_catch, catch_id, user["id"])
    if not ok:
        raise HTTPException(status_code=404, detail="Catch not found")
    return Response(status_code=204)
