"""Deterministic fishing-condition scorer -- the single source of truth.

This is a faithful re-implementation of Blueliner's production scorer
(`score_conditions` in main.py, ~L308-380), exposed with a clean Fahrenheit /
CFS signature instead of the raw USGS variable list the app passes internally.

It is used in THREE places, which is the whole point of having one copy:
  1. as an MCP tool the agent calls (so every rating it cites is grounded),
  2. to break ranking ties and make the safety-critical call deterministically
     -- the model explains, the scorer decides, and
  3. as the eval ORACLE that defines ground truth for the metrics.

Because the agent and the oracle are the same code, the eval measures the
agent's *judgment and safety*, not its arithmetic.

`tests/test_scorer_parity.py` pins this against the real `main.score_conditions`
across a grid of inputs, so it can never silently drift from production.

Thresholds (verbatim from main.py):
  Temp (F):  green 48-65 | yellow 45-48 or 65-68 | red >68 or <40 | else yellow
  Flow vs median:  green 0.5-2x | yellow 0.25-0.5x or 2-3x | poor otherwise
  Flow (no median):  red <0 or >10000 | yellow >5000 | else green
  Overall: worst of the per-metric states (red > yellow > green); gray if none.
"""

from __future__ import annotations

from typing import Optional, TypedDict

State = Optional[str]  # "green" | "yellow" | "red" | None


class Score(TypedDict):
    overall: str  # "green" | "yellow" | "red" | "gray"
    temp_state: State
    flow_state: State
    flow_ratio: Optional[float]
    reasons: list[str]
    inputs: dict


def _score_temp_f(temp_f: float) -> str:
    if 48 <= temp_f <= 65:
        return "green"
    if (45 <= temp_f < 48) or (65 < temp_f <= 68):
        return "yellow"
    if temp_f > 68 or temp_f < 40:
        return "red"
    return "yellow"  # 40 <= temp_f < 45


def _score_flow(flow_cfs: float, median_cfs: Optional[float]) -> tuple[str, Optional[float]]:
    if median_cfs and median_cfs > 0:
        ratio = flow_cfs / median_cfs
        if 0.5 <= ratio <= 2.0:
            return "green", ratio
        if (0.25 <= ratio < 0.5) or (2.0 < ratio <= 3.0):
            return "yellow", ratio
        return "red", ratio
    # Absolute fallback (no historical context available).
    if flow_cfs < 0 or flow_cfs > 10000:
        return "red", None
    if flow_cfs > 5000:
        return "yellow", None
    return "green", None


def score_conditions(
    water_temp_f: Optional[float] = None,
    flow_cfs: Optional[float] = None,
    median_cfs: Optional[float] = None,
) -> Score:
    """Score current readings for fishing suitability. Any input may be None
    (a gauge without that sensor); the overall is the worst of what's present,
    or 'gray' when nothing is scorable."""
    temp_state: State = None
    flow_state: State = None
    flow_ratio: Optional[float] = None
    reasons: list[str] = []

    if water_temp_f is not None:
        temp_state = _score_temp_f(water_temp_f)
        reasons.append(_temp_reason(water_temp_f, temp_state))

    if flow_cfs is not None:
        flow_state, flow_ratio = _score_flow(flow_cfs, median_cfs)
        reasons.append(_flow_reason(flow_cfs, median_cfs, flow_ratio, flow_state))

    states = [s for s in (temp_state, flow_state) if s is not None]
    if not states:
        overall = "gray"
    elif "red" in states:
        overall = "red"
    elif "yellow" in states:
        overall = "yellow"
    else:
        overall = "green"

    return Score(
        overall=overall,
        temp_state=temp_state,
        flow_state=flow_state,
        flow_ratio=round(flow_ratio, 2) if flow_ratio is not None else None,
        reasons=reasons,
        inputs={
            "water_temp_f": water_temp_f,
            "flow_cfs": flow_cfs,
            "median_cfs": median_cfs,
        },
    )


def _temp_reason(temp_f: float, state: str) -> str:
    if state == "green":
        return f"water {temp_f:.1f}F is in the optimal 48-65F trout band"
    if state == "yellow":
        return f"water {temp_f:.1f}F is marginal (45-48F or 65-68F)"
    if temp_f > 68:
        return f"water {temp_f:.1f}F is too warm (>68F stresses trout)"
    return f"water {temp_f:.1f}F is too cold (<40F)"


def _flow_reason(flow_cfs: float, median_cfs: Optional[float],
                 ratio: Optional[float], state: str) -> str:
    if ratio is not None:
        pct = f"{ratio:.2f}x median"
        if state == "green":
            return f"flow {flow_cfs:.0f} cfs is fishable ({pct}, within 0.5-2x)"
        if state == "yellow":
            return f"flow {flow_cfs:.0f} cfs is marginal ({pct})"
        return f"flow {flow_cfs:.0f} cfs is out of range ({pct})"
    if state == "green":
        return f"flow {flow_cfs:.0f} cfs (no median available; within absolute range)"
    if state == "yellow":
        return f"flow {flow_cfs:.0f} cfs is high (no median available)"
    return f"flow {flow_cfs:.0f} cfs is out of safe range (no median available)"
