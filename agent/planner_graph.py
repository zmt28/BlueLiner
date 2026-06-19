"""LangGraph orchestration of the trip-planner — for the controlled harness A/B.

This is the SAME pipeline as the hand-written loop in agent.py
(`retrieve → rank → guardrails`), expressed as a LangGraph StateGraph instead of
a `while`/`async with`. It deliberately reuses the exact same building blocks
(`_gather`, `_fetch_memory`, `_rank`, `apply_guardrails`), so the A/B isolates ONE
variable: the orchestration. Everything else — tools (MCP), scorer, guardrails,
prompts, model split — is identical.

The planner is LINEAR (no branching, no human interrupt), so the graph buys
nothing functional here — which is exactly the measured point: LangGraph earns
its place on the branching, human-in-the-loop prospector, not on this linear
workflow. The numbers from this A/B make that "right tool for the job" call
empirical rather than asserted.
"""

from __future__ import annotations

from typing import Optional, TypedDict

from mcp import ClientSession
from mcp.client.stdio import stdio_client

from langgraph.graph import END, START, StateGraph


class PlannerState(TypedDict, total=False):
    evidence: dict
    forecast: Optional[dict]
    memory: Optional[dict]
    proposal: dict


async def graph_pipeline(req, version, llm, trace, sourced):
    """Run retrieve → rank → guard as a LangGraph StateGraph. Returns
    (evidence, proposal) — same contract as agent._hand_pipeline."""
    # Imported here (not at module top) to avoid an import cycle; agent is fully
    # loaded by the time this runs.
    from .agent import (_gather, _fetch_memory, _rank, _collect_numbers,
                        apply_guardrails, _stdio_params)

    async with stdio_client(_stdio_params()) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()

            async def retrieve_node(state: PlannerState) -> dict:
                evidence, forecast = await _gather(session, llm, req, trace, sourced)
                memory = None
                if version >= 2 and req.user_id is not None:
                    memory = await _fetch_memory(session, req.user_id, trace)
                    _collect_numbers(memory, sourced)
                return {"evidence": evidence, "forecast": forecast, "memory": memory}

            def rank_node(state: PlannerState) -> dict:
                proposal = _rank(llm, req, state["evidence"],
                                 state.get("forecast"), state.get("memory"))
                return {"proposal": proposal}

            def guard_node(state: PlannerState) -> dict:
                proposal = state["proposal"]
                if version >= 3:
                    proposal = apply_guardrails(
                        proposal, state["evidence"], sourced, req=req,
                        forecast=state.get("forecast"), memory=state.get("memory"),
                        llm=llm)
                return {"proposal": proposal}

            g = StateGraph(PlannerState)
            g.add_node("retrieve", retrieve_node)
            g.add_node("rank", rank_node)
            g.add_node("guard", guard_node)
            g.add_edge(START, "retrieve")
            g.add_edge("retrieve", "rank")
            g.add_edge("rank", "guard")
            g.add_edge("guard", END)
            graph = g.compile()

            final = await graph.ainvoke({})
            return final["evidence"], final["proposal"]
