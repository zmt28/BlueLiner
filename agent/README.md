# Blueliner Trip-Planning Agent

An agentic layer over Blueliner: given an angler's location, dates, and
preferences, it plans the best river(s) to fish by **selecting and calling tools
over Blueliner's live data**, grounds every claim in real readings, respects
**non-overridable safety guardrails**, personalizes from the user's **catch log
(memory)**, and is measured by an **eval harness that uses Blueliner's
deterministic scorer as the oracle**. A proactive mode watches saved rivers and
alerts when they come into shape.

It's a standalone package — it reuses Blueliner's `db`/scorer rules and the same
USGS/NOAA upstreams, but does not modify core app behavior.

## Architecture

```
angler request ─▶ agent.py  (manual Anthropic tool-use loop)
                    │  cheap model (Haiku) drives retrieval; strong model (Sonnet) ranks
                    ▼
              mcp_server.py  (FastMCP, stdio)  ── 7 tools wrapping Blueliner data + scorer
                    │
        ┌───────────┼───────────────┬─────────────────┐
   USGS NWIS     NOAA api        Blueliner DB      deterministic scorer
   (flow/temp,   (forecast)      (catch log =      (agent/scorer.py;
    medians)                      memory)           tool + eval ORACLE)
                    │
              guardrails.py  (flood / trout-temp / access / grounding / staleness — vetoes)
                    │
        recommendation + rationale + confidence  (+ structured run trace -> logs/runs.jsonl)
```

Data resolution for every reading: **injected (eval) → live (USGS/NOAA) →
recorded fixture**. Every tool result carries a `source` so the agent can cite
it and a trace shows live-vs-fixture-vs-injected at a glance.

## Layout

| File | Role |
|---|---|
| `mcp_server.py` | FastMCP server exposing the 7 tools over stdio |
| `agent.py` | manual tool-use loop; versions v0→v3; `plan_trip(req, version)` |
| `scorer.py` | deterministic scorer, mirror of `main.score_conditions` (single source of truth) |
| `datasources.py` | live USGS/NOAA fetch + fixture fallback + eval injection |
| `guardrails.py` | the five non-overridable safety/grounding rules |
| `memory.py` | catch-log → compact, model-readable patterns |
| `llm.py` | Anthropic client wrapper + token/cost accounting |
| `observability.py` | one JSONL trace per run |
| `watch.py` | proactive mode (notify-only) |
| `seed_memory.py` | synthetic catch log for the memory demo |
| `prompts/` | `system.md` (grounding contract) + `ranker.md` |
| `eval/` | `build_scenarios.py`, `scenarios.jsonl`, `run_eval.py`, `report.md` |
| `tests/` | scorer-parity (840 cases) + guardrail unit tests |

## Setup

```bash
pip install -r agent/requirements.txt        # anthropic + mcp (on top of app deps)
export ANTHROPIC_API_KEY=sk-ant-...           # Console key; the agent needs it to run
export BLUELINER_DB=$PWD/agent_demo.db        # use one DB for seeding + agent
python -m agent.seed_memory                   # seed the demo angler's catch log
```

## Run

```bash
# Interactive plan (v3 = grounded + guarded). Live USGS/NOAA with fixture fallback.
python -m agent.cli --lat 39.29 --lng -76.61 --radius 90        # see cli.py

# The eval (the headline deliverable): v0 → v3 over the scenario set.
python -m agent.eval.build_scenarios          # regenerate scenarios.jsonl (oracle-baked)
python -m agent.eval.run_eval                 # writes eval/report.md + results.json
python -m agent.eval.run_eval --versions 3 --limit 4   # quick smoke

# Proactive alert (dev mode logs the email if RESEND_API_KEY is unset).
python -m agent.watch --demo

# Tests (no API key needed).
pytest -q agent/tests
```

## The versions (the iteration story)

| Version | What it adds | What it fixes |
|---|---|---|
| **v0** | single prompt, no tools | baseline — invents readings, no safety |
| **v1** | MCP tools + USGS/NOAA + scorer | grounding: every number traces to a tool result |
| **v2** | catch-log memory | personalization: breaks ties toward the angler's productive conditions |
| **v3** | deterministic guardrails + grounding contract | safety violations → 0; hallucinated readings → 0 |

## Guardrails (non-overridable)

1. **Flood** — flow > 3× median → block "unsafe high water".
2. **Trout ethics** — water > 68°F → block; < 40°F → demote + warn.
3. **Legality** — private-only access → block "no public access".
4. **Grounding** — every cited number must trace to a tool result this session;
   unsourced numbers are rejected and the answer is regenerated once.
5. **Staleness** — reading older than 6 h → lower confidence + label.

## Presentation map

| Requirement | Where |
|---|---|
| Objective / success metrics | this README + `eval/report.md` |
| Iterations (v0→v3, what didn't work) | `eval/report.md` + git history (one commit per version) |
| Technical decisions | `mcp_server.py` (MCP), `agent.py` (loop + model split), `memory.py` |
| Context required | `prompts/system.md` (grounding contract), `memory.py`, `guardrails.py` (business rules) |
| Improvement levers | cheap/strong split in `config.py`; metric deltas in `report.md` |
| Human review & guardrails | `guardrails.py` (veto) + `watch.py` (notify-only autonomy boundary) |
| Monitoring & evaluation | `eval/` (scorer-as-oracle) + `logs/runs.jsonl` traces |
| Expansion path | proactive mode (`watch.py`) + self-healing data agent (below) |

## Expansion path (stretch — not built)

A **self-healing data-quality agent**: watches the USGS/NLDI/ArcGIS feeds and
precompute snapshots, diagnoses anomalies (nonsense gauge, river
misattribution, stale snapshot), and opens a GitHub issue/PR or quarantines a
gauge behind a flag — a human merges. Same shape as this agent (tools + a
human-in-the-loop approval boundary), pointed at data integrity instead of trip
planning. See `STRETCH.md`.
