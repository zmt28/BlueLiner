"""Typed graph state threaded through the prospector's LangGraph nodes.

One TypedDict, mutated step by step. Keeping the state explicit (rather than
hidden in a framework's memory) is half the point of using LangGraph here — the
deck can show exactly what each node reads and writes.
"""

from __future__ import annotations

from typing import Optional, TypedDict


class ProspectState(TypedDict, total=False):
    region: dict                      # {"states": [...], "shortlist_k": int, "headless": bool}
    candidates: list[dict]            # reaches under consideration (pre-ranked shortlist)
    evidence: dict                    # comid -> gathered signals (topology/access/thermal)
    scored: list[dict]                # comid + suitability + components + confidence
    verified: list[dict]              # passed guardrails + grounding
    ranked: list[dict]                # final prospects with calibrated confidence
    excluded: list[dict]              # {comid, reason} dropped by guardrails
    pending_confirmation: Optional[dict]   # the prospect awaiting human confirm
    confirmations: list[dict]         # human confirm/deny results
    trace: list[dict]                 # per-node log for observability
    usage: dict                       # token/cost accounting
