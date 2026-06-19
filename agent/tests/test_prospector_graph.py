"""Tests for the prospector graph's deterministic spine (no LLM/key needed).

Exercises generate_candidates → gather_evidence → (ungauged) infer_thermal →
score → reflect_verify by calling the node functions directly and merging state,
so CI validates the graph's control flow and the deterministic scoring without an
API key. The `rank` node (strong-model rationale) is covered by the live demo.
"""

from __future__ import annotations

import pytest

from agent import prospector_nodes as N
from agent.prospector_graph import build_graph

pytest.importorskip("shapely")


def _merge(state, update):
    state = dict(state)
    state.update(update)
    return state


def test_graph_builds():
    build_graph()  # raises if the StateGraph wiring is invalid


def test_deterministic_spine_md():
    state = {"region": {"states": ["MD"], "shortlist_k": 6, "headless": True},
             "trace": []}
    state = _merge(state, N.generate_candidates(state))
    assert 0 < len(state["candidates"]) <= 6
    # nearest-trout pre-ranking: the shortlist leads should be very close.
    assert state["candidates"][0]["_topo"]["distance_mi"] is not None

    state = _merge(state, N.gather_evidence(state))
    assert set(state["evidence"]) == {c["comid"] for c in state["candidates"]}
    # offline -> all ungauged -> the conditional branch routes to infer_thermal.
    assert N.route_after_gather(state) == "infer_thermal"

    state = _merge(state, N.infer_thermal(state))
    assert all(e["thermal"].get("inferred") for e in state["evidence"].values())

    state = _merge(state, N.score(state))
    confs = [s["confidence"] for s in state["scored"]]
    assert confs == sorted(confs, reverse=True)        # ranked by confidence
    assert all(0.0 <= c <= 1.0 for c in confs)

    state = _merge(state, N.reflect_verify(state))
    # verified + excluded partition the scored set; no known-private survives.
    assert len(state["verified"]) + len(state["excluded"]) == len(state["scored"])

    nodes = [t["node"] for t in state["trace"]]
    assert nodes == ["generate_candidates", "gather_evidence", "infer_thermal",
                     "score", "reflect_verify"]
