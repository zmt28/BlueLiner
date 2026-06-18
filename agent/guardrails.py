"""Deterministic safety guardrails -- non-overridable, run AFTER the model proposes.

The model ranks and explains; these rules decide the safety-critical parts and
can veto, regardless of the model's reasoning. Five checks (spec section 6):

  1. Flood/safety  -- flow > FLOOD_RATIO x median  -> BLOCK "unsafe high water"
  2. Trout ethics  -- water > 68F (too warm)        -> BLOCK "water too warm"
                      water < 40F (too cold)         -> DEMOTE + warn
  3. Legality      -- private-only access            -> BLOCK "no public access"
  4. Grounding     -- every cited reading must trace to a tool result this
                      session; unsourced numbers are rejected
  5. Staleness     -- reading older than STALE_HOURS -> lower confidence + label

Every veto is returned as a structured Violation and logged, so the monitoring
story shows exactly why a river was dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from . import config


@dataclass
class Violation:
    rule: str            # flood | too_warm | too_cold | access | staleness | grounding
    river_id: Optional[str]
    action: str          # block | demote | warn | confidence | grounding
    detail: str


# Threshold constants are domain knowledge, not "readings" -- allow them so the
# grounding check doesn't flag the model for quoting the optimal-temp band.
_DOMAIN_CONSTANTS = {40.0, 45.0, 48.0, 65.0, 68.0, 0.25, 0.5, 2.0, 3.0}
_SMALL_COUNTS = {0.0, 1.0, 2.0, 3.0, 4.0, 5.0}
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _allowed_numbers(evidence: dict) -> set[float]:
    allowed: set[float] = set(_DOMAIN_CONSTANTS) | set(_SMALL_COUNTS)
    for ev in evidence.values():
        for key in ("flow_cfs", "water_temp_f", "median_cfs",
                    "flow_vs_median_pct", "flow_ratio", "distance_miles",
                    "last_updated_hours_ago", "access_points"):
            v = ev.get(key)
            if isinstance(v, (int, float)):
                allowed.add(float(v))
                allowed.add(round(float(v)))
    return allowed


def _is_sourced(n: float, allowed: set[float]) -> bool:
    tol = max(1.0, 0.02 * abs(n))
    return any(abs(n - a) <= tol for a in allowed)


def check_grounding(proposal: dict, evidence: dict) -> tuple[bool, list[float]]:
    """Scan the recommendation rationale for numbers not traceable to a tool
    result. Returns (ok, unsourced_numbers)."""
    allowed = _allowed_numbers(evidence)
    unsourced: list[float] = []
    for rec in proposal.get("recommendations", []):
        text = " ".join(str(b) for b in rec.get("why", []))
        text += " " + str(rec.get("verdict", ""))
        for m in _NUM_RE.findall(text):
            n = float(m)
            if not _is_sourced(n, allowed):
                unsourced.append(n)
    return (len(unsourced) == 0), unsourced


def apply(proposal: dict, evidence: dict) -> dict:
    """Filter/demote/annotate the model's proposal per the safety rules.

    `evidence` maps river_id -> merged conditions+access dict gathered from
    tools this session. Returns a corrected proposal plus the violation log.
    """
    violations: list[Violation] = []
    survivors: list[dict] = []
    blocked: list[dict] = list(proposal.get("blocked", []))
    blocked_ids = {b.get("river_id") for b in blocked}

    def block(river_id, reason, rule):
        if river_id not in blocked_ids:
            blocked.append({"river_id": river_id, "reason": reason})
            blocked_ids.add(river_id)
        violations.append(Violation(rule, river_id, "block", reason))

    for rec in proposal.get("recommendations", []):
        rid = rec.get("river_id")
        ev = evidence.get(rid, {})
        ratio = ev.get("flow_ratio")
        temp = ev.get("water_temp_f")
        public = ev.get("public_access", True)
        stale_h = ev.get("last_updated_hours_ago")

        # 1. Flood
        if isinstance(ratio, (int, float)) and ratio > config.FLOOD_RATIO:
            block(rid, f"unsafe high water (flow {ratio:.1f}x median)", "flood")
            continue
        # 3. Legality
        if public is False:
            block(rid, "no public access (private water)", "access")
            continue
        # 2a. Too warm (ethical block)
        if isinstance(temp, (int, float)) and temp > config.TEMP_MAX_F:
            block(rid, f"water too warm ({temp:.0f}F stresses trout)", "too_warm")
            continue

        # 2b. Too cold -> demote + warn (poor, not unsafe to fish)
        if isinstance(temp, (int, float)) and temp < config.TEMP_MIN_F:
            rec.setdefault("warnings", []).append(f"water cold ({temp:.0f}F); slow fishing")
            rec["_demote"] = True
            violations.append(Violation("too_cold", rid, "demote",
                                        f"water {temp:.0f}F below {config.TEMP_MIN_F:.0f}F"))
        # 5. Staleness -> lower confidence + label
        if isinstance(stale_h, (int, float)) and stale_h > config.STALE_HOURS:
            rec["confidence"] = "low"
            rec.setdefault("warnings", []).append(
                f"stale data (reading {stale_h:.0f}h old)")
            violations.append(Violation("staleness", rid, "confidence",
                                        f"reading {stale_h:.0f}h old (> {config.STALE_HOURS:.0f}h)"))
        survivors.append(rec)

    # Demoted survivors sink below clean ones, order otherwise preserved.
    survivors.sort(key=lambda r: 1 if r.get("_demote") else 0)
    for r in survivors:
        r.pop("_demote", None)

    grounding_ok, unsourced = check_grounding({"recommendations": survivors}, evidence)
    if not grounding_ok:
        violations.append(Violation(
            "grounding", None, "grounding",
            f"unsourced numbers in rationale: {sorted(set(unsourced))}"))

    return {
        "recommendations": survivors,
        "blocked": blocked,
        "notes": proposal.get("notes", ""),
        "violations": [v.__dict__ for v in violations],
        "grounding_ok": grounding_ok,
        "unsourced": sorted(set(unsourced)),
    }
