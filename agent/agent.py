"""The trip-planning agent: a transparent, manual Anthropic tool-use loop.

Run modes are expressed as VERSIONS so the v0->v3 evolution is one switch and
the eval can drive each:

  v0  naive baseline   -- one strong-model call, NO tools. It guesses readings
                          from its own knowledge. This is the "what didn't work"
                          starting point: hallucinated numbers, no safety.
  v1  tool-grounded    -- cheap model drives an MCP tool loop to gather real
                          USGS/NOAA readings; strong model ranks from them.
  v2  + memory         -- also injects the signed-in angler's catch-log patterns.
  v3  + guardrails     -- runs the deterministic safety/grounding guardrails
                          after the model proposes, vetoes unsafe/ungrounded
                          output, and regenerates once if grounding fails.

The loop is intentionally hand-written (not a framework's tool runner) so every
step -- tool choice, the guardrail veto, the model split -- is visible and
walk-through-able.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from . import config, guardrails
from .llm import LLM, Usage
from .observability import RunTrace

_SYSTEM = (config.PROMPTS_DIR / "system.md").read_text()
_RANKER = (config.PROMPTS_DIR / "ranker.md").read_text()

_V0_SYSTEM = (
    "You are a knowledgeable fly-fishing buddy. Given an angler's location and "
    "dates, recommend the best nearby rivers to fish and, for each, give the "
    "current flow (cfs) and water temperature so they can decide. Be specific "
    "and confident. Return JSON only (see the shape in the user's message)."
)

REC_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "recommendations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "river_id": {"type": "string"},
                "name": {"type": "string"},
                "verdict": {"type": "string"},
                "overall_score": {"type": "string",
                                  "enum": ["green", "yellow", "red", "gray"]},
                "confidence": {"type": "string",
                               "enum": ["high", "medium", "low"]},
                "why": {"type": "array", "items": {"type": "string"}},
                "sources": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["river_id", "name", "verdict", "overall_score",
                         "confidence", "why", "sources"],
        }},
        "blocked": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"river_id": {"type": "string"},
                           "reason": {"type": "string"}},
            "required": ["river_id", "reason"],
        }},
        "notes": {"type": "string"},
    },
    "required": ["recommendations", "blocked", "notes"],
}

_JSON_SHAPE_HINT = json.dumps({
    "recommendations": [{"river_id": "", "name": "", "verdict": "",
                         "overall_score": "green|yellow|red",
                         "confidence": "high|medium|low",
                         "why": ["..."], "sources": ["..."]}],
    "blocked": [{"river_id": "", "reason": ""}], "notes": ""}, indent=2)


@dataclass
class TripRequest:
    lat: float
    lng: float
    state: Optional[str] = None
    dates: Optional[str] = None
    preferences: str = ""
    user_id: Optional[int] = None
    radius_miles: int = config.DEFAULT_RADIUS_MILES
    top_n: int = config.DEFAULT_TOP_N
    text: str = ""

    def user_message(self) -> str:
        lines = [
            self.text or "Where should I fish?",
            f"Location: lat {self.lat}, lng {self.lng}"
            + (f", state {self.state}" if self.state else ""),
            f"Search radius: {self.radius_miles} miles. Want top {self.top_n}.",
        ]
        if self.dates:
            lines.append(f"Dates: {self.dates}")
        if self.preferences:
            lines.append(f"Preferences: {self.preferences}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Tool-result plumbing
# --------------------------------------------------------------------------
def _unwrap(res) -> object:
    sc = getattr(res, "structuredContent", None)
    if isinstance(sc, dict):
        if set(sc.keys()) == {"result"}:
            return sc["result"]
        return sc
    if res.content:
        try:
            return json.loads(res.content[0].text)
        except Exception:
            return {"text": res.content[0].text}
    return None


def _source_of(obj) -> Optional[str]:
    if isinstance(obj, dict):
        return obj.get("source")
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return obj[0].get("source")
    return None


def _merge_evidence(evidence: dict, name: str, obj: object) -> None:
    """Fold a tool result into per-river evidence the guardrails will judge."""
    if name == "get_candidate_rivers" and isinstance(obj, list):
        for r in obj:
            e = evidence.setdefault(r["river_id"], {})
            e.update(name=r.get("name"), distance_miles=r.get("distance_miles"))
    elif name == "get_river_conditions" and isinstance(obj, dict) and "error" not in obj:
        e = evidence.setdefault(obj["river_id"], {})
        score = obj.get("score") or {}
        e.update(
            name=obj.get("name"),
            flow_cfs=obj.get("flow_cfs"), water_temp_f=obj.get("water_temp_f"),
            median_cfs=obj.get("median_cfs"),
            flow_vs_median_pct=obj.get("flow_vs_median_pct"),
            flow_ratio=score.get("flow_ratio"), rating=obj.get("rating"),
            last_updated_hours_ago=obj.get("last_updated_hours_ago"),
            source=obj.get("source"),
        )
    elif name == "get_access" and isinstance(obj, dict) and "error" not in obj:
        e = evidence.setdefault(obj["river_id"], {})
        e.update(access_tier=obj.get("access_tier"),
                 public_access=obj.get("public_access"),
                 access_points=obj.get("access_points"))


def _collect_numbers(obj, out: set) -> None:
    """Recursively gather every numeric value a tool returned, so the grounding
    check whitelists the full set of readings the agent actually saw this
    session (conditions, forecast temps/precip, medians, memory patterns, ...)."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_numbers(v, out)


def _parse_json(text: str) -> dict:
    """Extract the outermost JSON object from a model response."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(json)?", "", text).rstrip("`").strip()
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in response")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unbalanced JSON in response")


def _complete_json(llm: LLM, *, model: str, system: str, user: str,
                   max_tokens: int) -> dict:
    """One JSON-returning model call. Tries structured outputs, falls back to
    plain parsing if the SDK/model rejects the schema."""
    messages = [{"role": "user", "content": user}]
    try:
        resp = llm.message(model=model, system=system, messages=messages,
                           max_tokens=max_tokens, output_schema=REC_SCHEMA)
    except Exception:
        resp = llm.message(model=model, system=system, messages=messages,
                           max_tokens=max_tokens)
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    return _parse_json(text)


# --------------------------------------------------------------------------
# MCP-driven retrieval loop
# --------------------------------------------------------------------------
async def _gather(session: ClientSession, llm: LLM, req: TripRequest,
                  trace: RunTrace, sourced: set) -> tuple[dict, Optional[dict]]:
    """Cheap-model tool loop. Returns (evidence, forecast); fills `sourced` with
    every number any tool returned (for the grounding check)."""
    tools_list = await session.list_tools()
    anthropic_tools = [{"name": t.name, "description": t.description or "",
                        "input_schema": t.inputSchema} for t in tools_list.tools]

    evidence: dict = {}
    forecast: Optional[dict] = None
    messages = [{"role": "user", "content": req.user_message()}]

    for _ in range(config.MAX_AGENT_STEPS):
        resp = llm.message(model=config.CHEAP_MODEL, system=_SYSTEM,
                           messages=messages, tools=anthropic_tools,
                           max_tokens=config.RETRIEVAL_MAX_TOKENS)
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            break
        results = []
        for blk in resp.content:
            if getattr(blk, "type", "") != "tool_use":
                continue
            t0 = time.monotonic()
            res = await session.call_tool(blk.name, blk.input)
            obj = _unwrap(res)
            trace.record_tool(blk.name, dict(blk.input),
                              int((time.monotonic() - t0) * 1000), _source_of(obj))
            _merge_evidence(evidence, blk.name, obj)
            _collect_numbers(obj, sourced)
            if blk.name == "get_forecast":
                forecast = obj if isinstance(obj, dict) else forecast
            results.append({"type": "tool_result", "tool_use_id": blk.id,
                            "content": json.dumps(obj, default=str)})
        messages.append({"role": "user", "content": results})
    return evidence, forecast


async def _fetch_memory(session: ClientSession, user_id: int,
                        trace: RunTrace) -> Optional[dict]:
    t0 = time.monotonic()
    res = await session.call_tool("get_user_catch_history", {"user_id": user_id})
    obj = _unwrap(res)
    trace.record_tool("get_user_catch_history", {"user_id": user_id},
                      int((time.monotonic() - t0) * 1000), _source_of(obj))
    return obj if isinstance(obj, dict) else None


def _rank(llm: LLM, req: TripRequest, evidence: dict,
          forecast: Optional[dict], memory: Optional[dict],
          correction: Optional[str] = None) -> dict:
    payload = {
        "request": {"location": {"lat": req.lat, "lng": req.lng, "state": req.state},
                    "dates": req.dates, "preferences": req.preferences,
                    "top_n": req.top_n},
        "candidates_evidence": evidence,
        "forecast": forecast,
        "angler_patterns": memory,
    }
    user = "EVIDENCE (the only facts you may cite):\n" + json.dumps(payload, default=str)
    if correction:
        user += "\n\nCORRECTION: " + correction
    return _complete_json(llm, model=config.STRONG_MODEL, system=_RANKER,
                          user=user, max_tokens=config.RANKER_MAX_TOKENS)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def _stdio_params() -> StdioServerParameters:
    return StdioServerParameters(command="python", args=["-m", config.MCP_SERVER_MODULE],
                                 env={**os.environ})


def apply_guardrails(proposal, evidence, sourced, *, req, forecast, memory, llm):
    """v3 guardrails + one grounding-correction retry. Shared by BOTH the
    hand-written loop and the LangGraph orchestration, so the ONLY thing that
    differs between the two harnesses is the orchestration — never the safety
    logic, the tools, the scorer, or the prompts. That's what makes the A/B a
    controlled experiment."""
    guarded = guardrails.apply(proposal, evidence, sourced)
    if not guarded["grounding_ok"]:
        correction = (f"These numbers were not in any tool result and must be "
                      f"removed or replaced with sourced values: {guarded['unsourced']}.")
        proposal = _rank(llm, req, evidence, forecast, memory, correction)
        guarded = guardrails.apply(proposal, evidence, sourced)
    return guarded


async def _hand_pipeline(req, version, llm, trace, sourced):
    """Hand-written orchestration: one MCP session, gather -> (memory) -> rank ->
    guardrails. Returns (evidence, proposal)."""
    async with stdio_client(_stdio_params()) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            evidence, forecast = await _gather(session, llm, req, trace, sourced)
            memory = None
            if version >= 2 and req.user_id is not None:
                memory = await _fetch_memory(session, req.user_id, trace)
                _collect_numbers(memory, sourced)
            proposal = _rank(llm, req, evidence, forecast, memory)
            if version >= 3:
                proposal = apply_guardrails(proposal, evidence, sourced,
                                            req=req, forecast=forecast,
                                            memory=memory, llm=llm)
            return evidence, proposal


async def _plan_async(req: TripRequest, version: int, trace: RunTrace,
                      usage: Usage, orchestrator: str = "hand") -> dict:
    llm = LLM(usage=usage)
    trace.cheap_model = config.CHEAP_MODEL
    trace.strong_model = config.STRONG_MODEL

    evidence: dict = {}
    sourced: set = set()  # every number any tool returned this session

    if version == 0:
        # naive single call, no tools, no data (orchestrator-independent).
        user = req.user_message() + "\n\nReturn JSON only in this shape:\n" + _JSON_SHAPE_HINT
        proposal = _complete_json(llm, model=config.STRONG_MODEL,
                                  system=_V0_SYSTEM, user=user,
                                  max_tokens=config.RANKER_MAX_TOKENS)
    elif orchestrator == "graph":
        from . import planner_graph
        evidence, proposal = await planner_graph.graph_pipeline(
            req, version, llm, trace, sourced)
    else:
        evidence, proposal = await _hand_pipeline(req, version, llm, trace, sourced)

    # Grounding is enforced only in v3, but we always compute it for the report,
    # so the v0->v3 hallucination-rate drop is measurable.
    g_ok, unsourced = guardrails.check_grounding(proposal, evidence, sourced)
    recs = proposal.get("recommendations", [])
    result = {
        "version": version,
        "orchestrator": orchestrator,
        "recommendations": recs,
        "blocked": proposal.get("blocked", []),
        "notes": proposal.get("notes", ""),
        "violations": proposal.get("violations", []),
        "grounding": {"ok": g_ok, "unsourced": unsourced},
        "evidence_river_ids": sorted(evidence.keys()),
        "confidence": recs[0]["confidence"] if recs and "confidence" in recs[0] else None,
    }
    return result


def plan_trip(req: TripRequest, version: int = 3, *, log: bool = True,
              orchestrator: str = "hand") -> dict:
    """Plan a trip and return the recommendation. Synchronous entry point.
    `orchestrator` selects the hand-written loop or the LangGraph variant —
    identical tools/scorer/guardrails/prompts, only the sequencing differs."""
    trace = RunTrace(request=req.__dict__.copy(), version=version).start()
    usage = Usage()
    try:
        result = asyncio.run(_plan_async(req, version, trace, usage, orchestrator))
    except Exception as e:  # keep coverage measurable in the eval
        trace.error = f"{type(e).__name__}: {e}"
        result = {"version": version, "orchestrator": orchestrator,
                  "recommendations": [], "blocked": [],
                  "notes": "", "violations": [], "error": trace.error,
                  "grounding": {"ok": True, "unsourced": []},
                  "evidence_river_ids": [], "confidence": None}
    usage_summary = usage.summary()
    trace.finish(result, usage_summary, result.get("confidence"))
    result["usage"] = usage_summary
    result["latency_ms"] = trace.latency_ms
    if log:
        trace.write()
    return result
