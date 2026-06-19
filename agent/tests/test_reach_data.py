"""Tests for the prospector's candidate pool — specifically the rule that drops
same-stream extensions of already-known trout water.

A reach that shares a `levelpathid` with a designated reach is just another
segment of a stream we already know is trout water (the map renders its
designated sections per-reach), so it isn't a discovery. Production must exclude
every such reach; the held-out backtest must NOT exclude a fully-masked stream's
reaches (the agent has to rediscover those via a *different* nearby trout
stream). Both invariants are locked here.

Needs shapely (reach_data builds an STRtree) + the bundled clickable_streams
data, both present in CI. No LLM/MCP/key required.
"""

from __future__ import annotations

import pytest

pytest.importorskip("shapely")

from agent import reach_data  # noqa: E402

STATES = ("MD",)


def _designated_levelpaths(states, exclude=frozenset()):
    reg = reach_data._region(tuple(states))
    return {r["levelpathid"] for r in reg["designated_recs"]
            if r["levelpathid"] is not None and r["comid"] not in exclude}


def test_production_excludes_same_stream_extensions():
    """With nothing held out, no candidate may sit on a designated stream's
    levelpath."""
    des_lp = _designated_levelpaths(STATES)
    cands = reach_data.candidate_reaches(STATES)
    assert cands, "expected a non-empty candidate pool"
    offenders = [c for c in cands if c["levelpathid"] in des_lp]
    assert offenders == [], f"{len(offenders)} same-stream extensions leaked through"


def test_excluded_count_is_material():
    """Sanity: the rule actually removes a meaningful chunk (it's ~29% in MD),
    so a future regression that no-ops the filter gets caught."""
    reg = reach_data._region(STATES)
    # Reconstruct the pre-filter pool (size + fishability filters only).
    pre = [r for r in reg["reaches"]
           if not r["trout_class"] and not (r["streamorder"] < reach_data.MIN_ORDER)]
    post = reach_data.candidate_reaches(STATES)
    assert len(post) < len(pre)
    assert (len(pre) - len(post)) / len(pre) > 0.1


def test_held_out_streams_remain_candidates():
    """Backtest invariant: when a whole stream's designation is masked, its
    reaches stay in the candidate pool (so the agent can rediscover them)."""
    reg = reach_data._region(STATES)
    from collections import defaultdict
    by_lp = defaultdict(list)
    for r in reg["reaches"]:
        if r["trout_class"] and r["levelpathid"] is not None:
            by_lp[r["levelpathid"]].append(r)
    # Pick a designated stream that has fishable-order reaches to hold out.
    target_lp = next(lp for lp, rs in by_lp.items()
                     if any(x["streamorder"] >= reach_data.MIN_ORDER for x in rs))
    held = frozenset(r["comid"] for r in by_lp[target_lp])

    cand_ids = {c["comid"] for c in reach_data.candidate_reaches(STATES, held)}
    fishable_held = [r for r in by_lp[target_lp]
                     if r["streamorder"] >= reach_data.MIN_ORDER]
    # The masked stream's fishable reaches survive as candidates...
    assert all(r["comid"] in cand_ids for r in fishable_held)
    # ...and its (now-invisible) levelpath no longer drives exclusions.
    assert target_lp not in _designated_levelpaths(STATES, exclude=held)
