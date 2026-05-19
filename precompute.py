"""
Out-of-band refresher.

Precomputes per-state river snapshots and flowline geometry into
Postgres so user requests are fast local reads, never blocked on USGS /
NLDI / ArcGIS. This is the single mechanism behind "the map loads
instantly": `/api/rivers` and `/api/river_lines` just read what this
module has already assembled.

Standalone on purpose -- the in-process loop in `main.lifespan` calls
it now; a Render Cron Job / worker (or the `/internal/refresh` endpoint)
can call the exact same functions later with no code change.

`main` is imported lazily inside functions to avoid an import cycle
(`main` pulls this in only when it actually refreshes).
"""

import asyncio
import logging
import os

from states import STATES
import db
import stocking

logger = logging.getLogger("bluelines.precompute")

# Refreshed every cycle. The rest of the 51 states are computed lazily on
# first visit, then persisted. Override with FOCUSED_STATES="MD,VA,...".
_DEFAULT_FOCUSED = ["MD", "VA", "WV", "PA", "DE", "NJ", "NY", "NC", "TN",
                    "KY", "OH"]

# Geometry backfill is off the request path; modest parallelism gets a
# state's lines populated fast without hammering NLDI or memory.
_GEOM_BACKFILL_CONCURRENCY = 8


def focused_states() -> list[str]:
    env = os.environ.get("FOCUSED_STATES", "").strip()
    if env:
        picked = [s.strip().upper() for s in env.split(",")]
        return [s for s in picked if s in STATES]
    return [s for s in _DEFAULT_FOCUSED if s in STATES]


async def _backfill_geometry(rivers: list[dict]) -> None:
    """Persist NLDI flowline geometry for any of this state's gauges not
    already in `river_geom` (geometry is immutable, so once is enough)."""
    import main

    site_nos = [r["site_no"] for r in rivers if r.get("site_no")]
    if not site_nos:
        return
    try:
        have = await asyncio.to_thread(db.get_river_geoms, site_nos)
    except Exception:
        have = {}
    todo = [sn for sn in site_nos if sn not in have]
    if not todo:
        return

    sem = asyncio.Semaphore(_GEOM_BACKFILL_CONCURRENCY)

    async def _one(sn: str) -> None:
        async with sem:
            try:
                await asyncio.to_thread(main._nldi_flowline, sn)
            except Exception as exc:
                logger.warning("geom backfill failed for %s: %s", sn, exc)

    await asyncio.gather(*(_one(sn) for sn in todo))


async def refresh_state(st: str, *, backfill: bool = True) -> list[dict]:
    """Assemble a state's rivers from USGS and persist the snapshot (and,
    unless deferred, its flowline geometry). Returns the rivers list ([]
    on USGS empty/failure, which deliberately does not overwrite a good
    prior snapshot). `backfill=False` lets refresh_focused land every
    state's data fast, then backfill geometry in a second pass."""
    import main

    st = st.upper()
    if st not in STATES:
        return []
    data = await main._usgs_iv({"stateCd": STATES[st]["usgs_code"]}, st)
    ts = data.get("value", {}).get("timeSeries", [])
    trout = [main._trout_for_state(st)]  # non-blocking; tags fill next cycle
    stocked = await asyncio.to_thread(stocking.stocked_points, st)
    rivers = await main._assemble_rivers(ts, trout, stocked)
    if not rivers:
        logger.info("refresh %s: no rivers (USGS empty/unreachable)", st)
        return []
    await asyncio.to_thread(db.put_river_snapshot, st, rivers)
    main._state_rivers_cache[st] = rivers  # warm L1 so next request is instant
    if backfill:
        await _backfill_geometry(rivers)
    logger.info("refresh %s: persisted %d rivers", st, len(rivers))
    return rivers


_refresh_running = False


async def refresh_focused() -> None:
    """One full cycle over the focused states. Data first (every focused
    state's snapshot lands quickly), geometry second -- so no state's
    clickable lines wait behind another state's slow NLDI backfill.

    Single-flight: the in-process scheduler and the external GitHub
    Actions cron both call this; if a cycle is already running we skip
    rather than doubling the USGS load."""
    global _refresh_running
    if _refresh_running:
        logger.info("refresh_focused: already running, skipping")
        return
    _refresh_running = True
    try:
        persisted: dict[str, list[dict]] = {}
        for st in focused_states():
            try:
                rivers = await refresh_state(st, backfill=False)
                if rivers:
                    persisted[st] = rivers
            except Exception as exc:
                logger.warning("refresh_focused: %s data failed: %s", st, exc)
        for st, rivers in persisted.items():
            try:
                await _backfill_geometry(rivers)
            except Exception as exc:
                logger.warning("refresh_focused: %s geometry failed: %s", st, exc)
    finally:
        _refresh_running = False
