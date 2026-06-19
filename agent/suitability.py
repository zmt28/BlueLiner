"""Deterministic coldwater-suitability scorer for the prospecting agent.

Combines named, inspectable signals into a 0-1 suitability score + a calibrated
confidence. This is BOTH the agent's grounding tool (so every prospect's score
traces to explicit features) AND the deterministic ranking baseline the backtest
runs on (so recall@k / PR-AUC are computed without any LLM). Temp bands reuse the
trip-planner's scorer (green 48-65F) so "coldwater" means the same thing
everywhere.

`mode` enables the signal ablation in the backtest:
  topology | topology_thermal | topology_thermal_access | full
"""

from __future__ import annotations

from typing import Optional

from .reach_data import TOPO_FAR_MI, TOPO_NEAR_MI

# Topology-DOMINANT by design. The thesis (a coldwater parent stream implies
# fishable tributaries) makes proximity the overwhelming prior. Size/flow is only
# a soft floor — trout thrive in SMALL cold tributaries, so weighting size up
# actively demotes the water we want (measured: it collapsed held-out recall).
# Offline thermal is unavailable (no same-network gauge readings), so it's a
# minor term here and the real thermal refinement happens on the LLM/live-fetch
# shortlist, not in this broad ranker.
W_TOPO = 0.80
W_THERMAL = 0.10
W_FLOW = 0.10

MODES = ("topology", "topology_thermal", "topology_thermal_access", "full")


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _topology_component(topo: dict) -> tuple[float, str]:
    if topo.get("same_named_as_trout"):
        return 1.0, (f"same watercourse as designated trout water "
                     f"({topo.get('nearest_trout')})")
    d = topo.get("distance_mi")
    if d is None:
        return 0.0, "no designated trout water nearby"
    score = _clamp((TOPO_FAR_MI - d) / (TOPO_FAR_MI - TOPO_NEAR_MI))
    rel = "tributary-proximate to" if topo.get("is_tributary_proxy") else f"{d} mi from"
    return score, (f"{rel} designated trout water "
                   f"({topo.get('nearest_trout')}, {topo.get('nearest_trout_class')})")


def _flow_component(flow: dict) -> tuple[float, str]:
    # "Adequate to hold fish year-round" is a THRESHOLD, not a more-is-better
    # signal: a small order-3 tributary is exactly what we're hunting, so a big
    # order-5 river isn't "better". Plateau at order 3.
    order = flow.get("streamorder") or 0
    length = flow.get("lengthkm") or 0.0
    base = 1.0 if order >= 3 else (0.6 if order == 2 else 0.4)
    length_factor = 1.0 if length >= 1.0 else 0.8
    return base * length_factor, (f"stream order {order}, {length:.1f} km "
                                  f"(adequate to hold fish)" if order >= 3
                                  else f"small (order {order})")


def _thermal_component(thermal: dict) -> tuple[float, str, bool]:
    """Returns (score, reason, gauged)."""
    temp = thermal.get("water_temp_f")
    gauged = thermal.get("gauged", False)
    if temp is None:
        return 0.5, "temperature inferred (no same-network gauge) — lower confidence", False
    if 48 <= temp <= 65:
        return 1.0, f"nearest gauge {temp:.0f}F — in the trout band", gauged
    if (45 <= temp < 48) or (65 < temp <= 68):
        return 0.6, f"nearest gauge {temp:.0f}F — marginal", gauged
    return 0.2, f"nearest gauge {temp:.0f}F — outside trout range", gauged


def coldwater_suitability(topology: dict, flow: dict, thermal: dict,
                          access: dict, mode: str = "full") -> dict:
    """Score a reach's coldwater fishing potential. `access_ok` is a binding
    actionability gate, not a score term."""
    topo_s, topo_r = _topology_component(topology)
    flow_s, flow_r = _flow_component(flow)
    therm_s, therm_r, gauged = _thermal_component(thermal)
    access_ok = bool(access.get("access_ok"))

    components = {"topology": round(topo_s, 3), "thermal": round(therm_s, 3),
                  "flow": round(flow_s, 3), "access_ok": access_ok}
    reasons = [topo_r]

    # Build the score from the INCLUDED components, renormalized. Thermal only
    # counts when actually gauged — an inferred constant just dilutes topology
    # (offline there are no same-network gauge readings, so thermal is the
    # LLM/live-fetch layer's job on the shortlist, not the broad ranker's).
    comp = {"topology": topo_s, "thermal": therm_s, "flow": flow_s}
    w = {"topology": W_TOPO, "thermal": W_THERMAL, "flow": W_FLOW}
    included = ["topology"]
    if mode in ("topology_thermal", "topology_thermal_access", "full") and gauged:
        included.append("thermal")
        reasons.append(therm_r)
    if mode in ("topology_thermal_access", "full"):
        included.append("flow")
        reasons.append(flow_r)
    score = sum(w[k] * comp[k] for k in included) / sum(w[k] for k in included)

    # Confidence blends signal strength with data quality (gauged vs inferred,
    # topology certainty). Used for the calibration curve.
    quality = 1.0 if gauged else 0.75
    if topology.get("distance_mi") is not None and topology["distance_mi"] > TOPO_FAR_MI:
        quality *= 0.8

    # Actionability handling (gated modes). We only have access-POINT data, not
    # PAD-US public-land polygons, so absence of a nearby point != private.
    #   known private  -> hard-exclude (real access violation if surfaced)
    #   unknown access -> NOT excluded; demote + "verify locally" (uncertainty
    #                     guardrail), since it may well be public land
    #   public/permit  -> full
    gated = mode in ("topology_thermal_access", "full")
    tier = access.get("access_tier")
    surfaced_score = score
    needs_access_verify = False
    if gated:
        if tier in ("private", "private_easement"):
            surfaced_score = 0.0           # legality guardrail: never surface private
            reasons.append("known private access — excluded")
        elif not access_ok:                # unknown: absence of a mapped point != private
            needs_access_verify = True     # uncertainty flag, NOT a rank demotion
            reasons.append("access unverified — confirm public access locally")

    confidence = round(_clamp(surfaced_score * quality), 3)

    return {
        "suitability_score": round(surfaced_score, 3),
        "confidence": confidence,
        "components": components,
        "access_ok": access_ok,
        "needs_access_verify": needs_access_verify,
        "reasons": reasons,
        "mode": mode,
    }
