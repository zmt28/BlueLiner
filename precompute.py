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

# Geometry backfill is off the request path; a few in parallel is plenty
# and keeps NLDI load (and memory) modest on the free tier.
_GEOM_BACKFILL_CONCURRENCY = 4


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


async def refresh_state(st: str) -> int:
    """Assemble a state's rivers from USGS and persist the snapshot +
    flowline geometry. Returns the river count (0 on USGS empty/failure,
    which deliberately does not overwrite a good prior snapshot)."""
    import main

    st = st.upper()
    if st not in STATES:
        return 0
    data = await main._usgs_iv({"stateCd": STATES[st]["usgs_code"]}, st)
    ts = data.get("value", {}).get("timeSeries", [])
    trout = [main._trout_for_state(st)]  # non-blocking; tags fill next cycle
    stocked = await asyncio.to_thread(stocking.stocked_points, st)
    rivers = await main._assemble_rivers(ts, trout, stocked)
    if not rivers:
        logger.info("refresh %s: no rivers (USGS empty/unreachable)", st)
        return 0
    await asyncio.to_thread(db.put_river_snapshot, st, rivers)
    main._state_rivers_cache[st] = rivers  # warm L1 so next request is instant
    await _backfill_geometry(rivers)
    logger.info("refresh %s: persisted %d rivers", st, len(rivers))
    return len(rivers)


async def refresh_focused() -> None:
    """One full cycle over the focused states (sequential -- bounds peak
    memory and avoids hammering USGS)."""
    for st in focused_states():
        try:
            await refresh_state(st)
        except Exception as exc:
            logger.warning("refresh_focused: %s failed: %s", st, exc)
