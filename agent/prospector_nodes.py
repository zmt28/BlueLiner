"""Prospector graph nodes.

Each node reads and writes the typed ProspectState. The boundary the deck cares
about: LangGraph owns the STEPS and state; the tools (topology/access/thermal)
are the MCP-exposed functions, and the scorer/guardrails are plain Python — no
framework wrapping them.

Model split & measured restraint: `gather_evidence` calls the tools
deterministically (exhaustive and free over a shortlist — no per-candidate LLM
loop, which adds cost/latency without improving recall); the STRONG model is used
once in `rank` to write the grounded rationale. Confidence stays deterministic
(calibrated in the backtest); the LLM explains, it does not re-score.
"""

from __future__ import annotations

import json
import time

from . import config, guardrails, reach_data, signals
from .agent import _collect_numbers, _parse_json
from .llm import LLM, Usage
from .suitability import coldwater_suitability

_PROSPECTOR_SYSTEM = (config.PROMPTS_DIR / "prospector_system.md").read_text()


def _log(state, node, **fields):
    entry = {"node": node, **fields}
    return state.get("trace", []) + [entry]


# --------------------------------------------------------------------------
def generate_candidates(state):
    region = state["region"]
    states = tuple(region["states"])
    k = region.get("shortlist_k", 12)
    reaches = reach_data.candidate_reaches(states)
    idx = reach_data.make_topology_index(states)
    ranked = []
    for r in reaches:
        t = reach_data.topology_at(idx, r["lat"], r["lon"], r["gnis_name"])
        d = t["distance_mi"] if t["distance_mi"] is not None else 1e9
        ranked.append((d, r, t))
    ranked.sort(key=lambda x: x[0])
    shortlist = [{**r, "_topo": t} for _, r, t in ranked[:k]]
    return {"candidates": shortlist,
            "trace": _log(state, "generate_candidates",
                          n_region=len(reaches), shortlist=len(shortlist))}


def gather_evidence(state):
    """Deterministic gather via the MCP-exposed tools (topology already on the
    candidate; add access + nearest-gauge thermal)."""
    evidence = {}
    ungauged = 0
    for c in state["candidates"]:
        comid = c["comid"]
        access = reach_data.access_for(comid)
        # Nearest same-network gauge: offline we can't identify one -> ungauged,
        # which routes to infer_thermal (the conditional branch).
        thermal = {"water_temp_f": None, "gauged": False,
                   "note": "no same-network gauge identified"}
        if not thermal["gauged"]:
            ungauged += 1
        evidence[comid] = {
            "comid": comid, "name": c.get("gnis_name"),
            "levelpathid": c.get("levelpathid"),
            "topology": c["_topo"],
            "flow": {"streamorder": c.get("streamorder"), "lengthkm": c.get("lengthkm")},
            "access": access, "thermal": thermal,
        }
    return {"evidence": evidence,
            "trace": _log(state, "gather_evidence", n=len(evidence), ungauged=ungauged)}


def route_after_gather(state):
    """Conditional edge: if any candidate is ungauged, infer thermal first."""
    if any(not e["thermal"]["gauged"] for e in state["evidence"].values()):
        return "infer_thermal"
    return "score"


def infer_thermal(state):
    """For ungauged reaches, mark temperature as inferred (lower confidence). We
    don't fabricate a number — we flag the uncertainty, and the scorer's
    data-quality factor + the rationale's why_not_higher carry it forward."""
    n = 0
    for e in state["evidence"].values():
        if not e["thermal"]["gauged"]:
            e["thermal"]["inferred"] = True
            n += 1
    return {"evidence": state["evidence"],
            "trace": _log(state, "infer_thermal", inferred=n)}


def score(state):
    scored = []
    for e in state["evidence"].values():
        s = coldwater_suitability(e["topology"], e["flow"], e["thermal"],
                                  e["access"], mode="full")
        scored.append({"comid": e["comid"], "name": e["name"],
                       "suitability_score": s["suitability_score"],
                       "confidence": s["confidence"], "components": s["components"],
                       "access_ok": s["access_ok"],
                       "needs_access_verify": s["needs_access_verify"],
                       "reasons": s["reasons"]})
    scored.sort(key=lambda x: x["confidence"], reverse=True)
    return {"scored": scored, "trace": _log(state, "score", n=len(scored))}


def reflect_verify(state):
    """Guardrails: drop known-private (suitability zeroed them), keep the rest;
    flag unverified access. Abstain below a confidence floor."""
    floor = 0.4
    verified, excluded = [], []
    for s in state["scored"]:
        ev = state["evidence"][s["comid"]]
        tier = ev["access"].get("access_tier")
        if s["suitability_score"] == 0.0 and tier in ("private", "private_easement"):
            excluded.append({"comid": s["comid"], "reason": "private access only"})
        elif s["confidence"] < floor:
            excluded.append({"comid": s["comid"],
                             "reason": f"below confidence floor ({s['confidence']})"})
        else:
            verified.append(s)
    return {"verified": verified, "excluded": excluded,
            "trace": _log(state, "reflect_verify",
                          kept=len(verified), dropped=len(excluded))}


def rank(state):
    """STRONG model writes the grounded rationale for the verified shortlist;
    confidence stays the deterministic value. Grounding-checked after."""
    verified = state["verified"]
    if not verified:
        return {"ranked": [], "trace": _log(state, "rank", n=0)}

    payload = []
    sourced_numbers = set()
    for s in verified:
        ev = state["evidence"][s["comid"]]
        item = {"comid": s["comid"], "gnis_name": ev["name"],
                "topology": ev["topology"], "access": ev["access"],
                "thermal": ev["thermal"], "flow": ev["flow"],
                "suitability_score": s["suitability_score"],
                "confidence": s["confidence"]}
        payload.append(item)
        _collect_numbers(item, sourced_numbers)

    usage = Usage()
    llm = LLM(usage=usage)
    user = ("EVIDENCE (the only facts you may cite):\n"
            + json.dumps(payload, default=str))
    try:
        resp = llm.message(model=config.STRONG_MODEL, system=_PROSPECTOR_SYSTEM,
                           messages=[{"role": "user", "content": user}],
                           max_tokens=config.RANKER_MAX_TOKENS)
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        out = _parse_json(text)
        prospects = out.get("prospects", [])
    except Exception as e:
        return {"ranked": [], "usage": usage.summary(),
                "trace": _log(state, "rank", error=f"{type(e).__name__}: {e}")}

    # Confidence stays deterministic; the LLM only explains.
    det = {s["comid"]: s for s in verified}
    for p in prospects:
        d = det.get(p.get("comid"))
        if d:
            p["confidence"] = d["confidence"]
            p["suitability_score"] = d["suitability_score"]
            p["needs_access_verify"] = d["needs_access_verify"]

    # Grounding: every number in the rationale must trace to the evidence.
    pseudo = {"recommendations": [{"why": p.get("evidence", []),
                                   "verdict": p.get("descriptor", "")} for p in prospects]}
    g_ok, unsourced = guardrails.check_grounding(pseudo, {}, sourced_numbers)
    return {"ranked": prospects, "usage": usage.summary(),
            "trace": _log(state, "rank", n=len(prospects),
                          grounding_ok=g_ok, unsourced=unsourced,
                          cost_usd=usage.summary().get("est_cost_usd"))}


def human_confirm(state):
    """Human-in-the-loop gate. In headless/eval mode, pass through. Interactively,
    pause via LangGraph interrupt() and surface the top prospect for confirm/deny;
    the checkpointer persists state so the answer can come back later."""
    ranked = state.get("ranked", [])
    if state["region"].get("headless") or not ranked:
        return {"pending_confirmation": None,
                "trace": _log(state, "human_confirm", mode="headless_skip")}
    top = ranked[0]
    from langgraph.types import interrupt
    decision = interrupt({"prompt": "Confirm this prospect?", "prospect": top})
    return {"pending_confirmation": top,
            "confirmations": state.get("confirmations", []) + [{"comid": top["comid"],
                                                                "decision": decision}],
            "trace": _log(state, "human_confirm", awaited=top.get("comid"),
                          decision=decision)}


def update_flywheel(state):
    """Record confirm/deny + promote confirmed prospects to trip-planner
    candidates (stub). The metric that improves as confirmations accrue is the
    calibration of the deterministic confidence — recorded here for the flywheel."""
    confirmations = state.get("confirmations", [])
    record = {"ts": time.time(), "region": state["region"].get("states"),
              "n_prospects": len(state.get("ranked", [])),
              "confirmations": confirmations}
    path = config.LOG_DIR / "flywheel.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")
    return {"trace": _log(state, "update_flywheel",
                          recorded=len(confirmations), promoted=0)}
