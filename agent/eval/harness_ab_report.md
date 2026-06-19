# Controlled Harness A/B — hand-written loop vs LangGraph

_Generated 2026-06-19T18:29:26+00:00_  
Trip-planner v3 over 25 scenarios. ONE variable changes (the orchestration); tools (MCP), scorer, guardrails, prompts, and the Haiku/Sonnet split are identical.

| Orchestrator | Top-1 agreement | Safety violations | Hallucinated | Coverage | Avg latency | Cost/run | Orchestration LOC |
|---|---|---|---|---|---|---|---|
| **hand** | 100.0% | 0.0% | 0.0% | 100.0% | 19421 ms | $0.02647 | 17 |
| **graph** | 100.0% | 0.0% | 0.0% | 100.0% | 19790 ms | $0.02523 | 38 |

## Reading this

- **Quality is flat** — same tools/scorer/guardrails/prompts produce the same agreement/safety/hallucination under either harness. The framework is not a quality lever, and claiming it is would be a confound.
- The deltas are **operational** — latency, cost, and the amount of orchestration code. On this LINEAR pipeline LangGraph adds glue (state class, node wrappers, graph wiring) and a dependency for no functional gain: no branching to express, no human interrupt, no checkpoint to resume.
- **The decision this justifies:** hand-written loop for the linear trip-planner; LangGraph for the branching, human-in-the-loop prospector (where `interrupt()` + durable checkpointing genuinely earn their place). Same building blocks, right tool per workflow.
