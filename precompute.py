"""
Out-of-band refresher.

Precomputes per-state river snapshots into Postgres so user requests are
fast local reads, never blocked on USGS / NLDI / ArcGIS. This is the
single mechanism behind "the map loads instantly": `/api/rivers` just
reads what this module has already assembled. A second pass backfills each
gauge's authoritative NHD identity (gauge_meta: gnis_name + levelpathid)
so rivers group correctly and clicked reaches match by levelpath.

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
import favorites
import stocking

logger = logging.getLogger("blueliner.precompute")

# Refreshed every cycle. The rest of the 51 states are computed lazily on
# first visit, then persisted. Override with FOCUSED_STATES="MD,VA,...".
_DEFAULT_FOCUSED = ["MD", "VA", "WV", "PA", "DE", "NJ", "NY", "NC", "TN",
                    "KY", "OH"]

# gauge_meta backfill is off the request path; modest parallelism gets a
# state's gauge identities populated fast without hammering NLDI or memory.
_GAUGE_META_BACKFILL_CONCURRENCY = 8


def focused_states() -> list[str]:
    env = os.environ.get("FOCUSED_STATES", "").strip()
    if env:
        picked = [s.strip().upper() for s in env.split(",")]
        return [s for s in picked if s in STATES]
    return [s for s in _DEFAULT_FOCUSED if s in STATES]


async def _backfill_gauge_meta(site_nos: list[str]) -> None:
    """Persist authoritative NHD identity (gauge_meta: gnis_name +
    levelpathid) for this state's gauges so `_assemble_rivers` groups
    rivers by NHD identity and the client can match a clicked reach by
    levelpath. Immutable per site, so once is enough; subsequent cycles
    short-circuit on the DB.

    Takes site numbers (not the full rivers lists) so callers can release
    the heavy assembled-rivers objects before the slow NLDI backfill pass."""
    import main

    if not site_nos:
        return
    try:
        have_meta = await asyncio.to_thread(db.get_gauge_metas, site_nos)
    except Exception:
        have_meta = {}
    # Re-process gauges with no row AND those whose row has a null
    # levelpathid (written while the national VAA was empty -- the COPY
    # timeout). _nldi_gauge_meta re-resolves the levelpathid from the now
    # populated VAA off the stored comid, so a river's levelpathids fill in
    # and a clicked reach matches the gauge by levelpath, not name alone.
    todo_meta = [sn for sn in site_nos
                 if sn not in have_meta
                 or have_meta[sn].get("levelpathid") is None]
    if not todo_meta:
        return

    sem = asyncio.Semaphore(_GAUGE_META_BACKFILL_CONCURRENCY)

    async def _backfill_one(sn: str) -> None:
        async with sem:
            try:
                await asyncio.to_thread(main._nldi_gauge_meta, sn)
            except Exception as exc:
                logger.warning("gauge_meta backfill failed for %s: %s", sn, exc)

    await asyncio.gather(*(_backfill_one(sn) for sn in todo_meta))


async def refresh_state(st: str, *, backfill: bool = True) -> list[dict]:
    """Assemble a state's rivers from USGS and persist the snapshot (and,
    unless deferred, its gauges' NHD identity). Returns the rivers list ([]
    on USGS empty/failure, which deliberately does not overwrite a good
    prior snapshot). `backfill=False` lets refresh_focused land every
    state's data fast, then backfill gauge_meta in a second pass."""
    import main

    st = st.upper()
    if st not in STATES:
        return []
    data = await main._usgs_iv({"stateCd": STATES[st]["usgs_code"]}, st)
    ts = data.get("value", {}).get("timeSeries", [])
    trout = [main._trout_for_state(st)]  # non-blocking; tags fill next cycle
    stocked = await asyncio.to_thread(stocking.stocked_points, st)
    # Flow-trend context (M4.4): the prior snapshot's per-gauge flows +
    # its age give a direction ("rising fast") with zero extra USGS load.
    prev_flows: dict[str, float] = {}
    prev_hours: float | None = None
    try:
        prev = await asyncio.to_thread(db.get_river_snapshot, st)
        if prev:
            prev_rivers, prev_updated = prev
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(prev_updated)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            prev_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
            for r in prev_rivers:
                for g in r.get("gauges") or []:
                    cf = (g.get("conditions") or {}).get("current_flow")
                    if g.get("site_no") and cf:
                        prev_flows[g["site_no"]] = cf
    except Exception:
        logger.exception("flow-trend prior snapshot read failed for %s", st)
    # Access renders from the static PMTiles map layer now, so precompute no
    # longer loads the (104k-point) access overlay into the app process.
    rivers = await main._assemble_rivers(
        ts, trout, stocked, prev_flows=prev_flows, prev_hours=prev_hours)
    if not rivers:
        logger.info("refresh %s: no rivers (USGS empty/unreachable)", st)
        return []
    await asyncio.to_thread(db.put_river_snapshot, st, rivers)
    # Favorite-water alerts (M4.1): diff fresh verdicts against each
    # favorite's stored state and email on meaningful transitions.
    # Best-effort -- an alert failure must never fail the snapshot.
    try:
        await asyncio.to_thread(favorites.check_favorite_alerts, st, rivers)
    except Exception:
        logger.exception("favorite alerts failed for %s", st)
    main._state_rivers_cache[st] = rivers  # warm L1 so next request is instant
    if backfill:
        site_nos = [r["site_no"] for r in rivers if r.get("site_no")]
        await _backfill_gauge_meta(site_nos)
    logger.info("refresh %s: persisted %d rivers", st, len(rivers))
    return rivers


_refresh_running = False


async def refresh_focused() -> None:
    """One full cycle over the focused states. Data first (every focused
    state's snapshot lands quickly), gauge_meta second -- so no state's
    river identities wait behind another state's slow NLDI backfill.

    Single-flight: the in-process scheduler and the external GitHub
    Actions cron both call this; if a cycle is already running we skip
    rather than doubling the USGS load."""
    global _refresh_running
    if _refresh_running:
        logger.info("refresh_focused: already running, skipping")
        return
    _refresh_running = True
    try:
        # Retain only the site numbers each state needs for the gauge_meta
        # pass, not the full assembled-rivers lists -- holding all focused
        # states' rivers at once was a recurring memory spike on the 512MB
        # free tier. The rivers themselves are released after each
        # refresh_state (the 120s-TTL L1 cache is their only remaining ref).
        persisted_sites: dict[str, list[str]] = {}
        for st in focused_states():
            try:
                rivers = await refresh_state(st, backfill=False)
                if rivers:
                    persisted_sites[st] = [
                        r["site_no"] for r in rivers if r.get("site_no")
                    ]
            except Exception as exc:
                logger.warning("refresh_focused: %s data failed: %s", st, exc)
        for st, site_nos in persisted_sites.items():
            try:
                await _backfill_gauge_meta(site_nos)
            except Exception as exc:
                logger.warning("refresh_focused: %s gauge_meta failed: %s",
                               st, exc)
        # Auth housekeeping (M5.1): idle sessions + dead magic links.
        # Best-effort; a sweep failure must never fail the cycle.
        try:
            n_sess, n_links = await asyncio.to_thread(db.prune_auth_rows)
            if n_sess or n_links:
                logger.info("pruned %d idle sessions, %d dead magic links",
                            n_sess, n_links)
        except Exception:
            logger.exception("auth prune failed")
    finally:
        _refresh_running = False
