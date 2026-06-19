# Learnings Log — Blueliner Agent (living doc for the presentation)

A running record of the non-obvious decisions, bugs, and measurement insights
found while building this system. Each entry notes **what happened**, **why it
matters**, **the fix/decision**, and the **deck slide** it serves. Append as the
system evolves (trip-planner → prospector/LangGraph → harness A/B).

---

## 1. Bugs found & fixed — the "concrete failure I found and fixed" slides

### 1.1 A guardrail could be silently bypassed by an id reformat
- **What:** the ranking model sometimes reformats a `river_id`
  (`gunpowder_falls_md` vs the catalog's `gunpowder-falls-md`). In v3 that made
  the guardrail's evidence lookup miss → `ev = {}` → the flood/temp/access checks
  had nothing to evaluate → the river could slip through **with the safety veto
  silently skipped**.
- **Why it matters:** the scariest failures are the silent ones. A guardrail that
  *looks* present but no-ops on a formatting quirk is worse than no guardrail,
  because you trust it.
- **Fix:** canonicalize each recommendation's id against the evidence keys
  (hyphen/underscore/case tolerant) before the safety checks, and write the
  canonical id back. Locked with a test (`test_id_reformatting_cannot_bypass_guardrail`).
- **Slide:** Human review & guardrails — "deterministic guardrails are only safe
  if they can't be dodged by model output noise."

### 1.2 The hallucination metric over-counted (forecast + derived numbers)
- **What:** my grounding check flagged any number in the rationale not within
  tolerance of a *per-river condition* number. That falsely flagged (a) **forecast
  values** (air temp 82–86°F, precip %) the agent legitimately got from
  `get_forecast`, and (b) **derived percentages** ("flow is 10% above the 300
  median" — correct arithmetic on sourced numbers; evidence stored it as "110% of
  median", level vs delta).
- **Why it matters:** the metric conflated *invented readings* (a real failure, in
  v0) with *citing a tool result / doing correct math* (not a failure). It made v3
  look like it still hallucinated 16% when the true rate was 0%.
- **Fix:** the grounding allowed-set now includes **every number any tool returned
  this session** (threaded through as `extra`), plus derived percent-deltas.
  Truly-invented numbers still flag (test).
- **Slide:** Monitoring & evaluation — "I caught a measurement bug in my own eval
  by reading the traces, not the dashboard."

### 1.3 Regex artifact: ranges parsed as negative numbers
- **What:** the number regex read `82-86` as `82` and `-86` (the hyphen as a minus
  sign), so any range in the rationale ("82-86°F", "0-3%") created phantom
  negatives that were never in any tool result → more false hallucination flags.
- **Why it matters:** a second, sneakier source of the same over-counting; found
  only because a unit test for the fix in 1.2 failed on the range string.
- **Fix:** `(?<![\d.])-?\d+...` — a `-` is a negative sign only when not preceded
  by a digit/dot.
- **Slide:** same as 1.2 — measurement rigor; "the test that caught the fix's own
  blind spot."

---

## 2. Eval & measurement insights — the "honest evaluation" slides

### 2.1 The honest finding: tool-grounding does the heavy lifting; the contract is the guarantee
- After fixing 1.2/1.3, hallucination went **100% (v0) → 4% (v1) → 0% (v3)**. The
  big drop is **v1** (grounding every number in a tool result), not v3. v3's
  grounding *contract* (regenerate on an unsourced number) is the **guarantee**
  that closes the last few %, plus it's what surfaced bug 1.1.
- **Why it matters:** the tempting story is "my guardrail fixed hallucination." The
  true, more credible story is "grounding-via-tools fixed it; the contract makes it
  non-negotiable." Senior audiences trust the nuanced version.

### 2.2 Hallucination went *up* when memory was added (v1 4% → v2 12%)
- **What:** adding the catch-log to context made the model weave in the angler's
  pattern numbers and occasionally **round a band** ("you do well at 52–60°F" when
  the log says 52.2–57.1°F).
- **First hypothesis (partly right):** the grounding check didn't whitelist numbers
  from the `get_user_catch_history` tool result — those are legitimately sourced.
  (Fixed by 1.2's "every tool number" rule. The residual is genuine rounding.)
- **Why it matters:** memory is not free — more context = more surface for
  imprecision. It gives v3's contract a concrete job (clean up what memory
  reintroduces).
- **Slide:** Improvement levers / trade-offs — "personalization added a small
  hallucination cost that the grounding contract then absorbed."

### 2.3 Positive-only / oracle nature
- The trip-planner's oracle (injected conditions + the deterministic scorer) is
  clean because conditions are injected — no label noise. The **prospector**
  inherits a genuine **positive-unlabeled** problem (undesignated reaches are
  unlabeled, not negative), so its recall is a *lower bound*. Flag this when we get
  there.

---

## 3. Metric-design decisions — the "how I measured" slides

### 3.1 Top-1 agreement = "safe + best rating tier", not exact match
- **Decision:** primary agreement credits any river the oracle rates in its **best
  safe tier** (e.g. any green), not only the single oracle-best after an arbitrary
  tiebreak. Exact-best is kept as a stricter secondary column.
- **Why:** the oracle's tiebreak (closest-to-median) is arbitrary among equally-good
  greens; penalizing the agent for picking a different green is noise. Worse: the
  **memory scenarios** would mark v2 *wrong* for correctly picking the angler's
  cool-water river over the oracle's ratio-tiebreak pick. The metric must reward the
  product goal ("a great, safe, legal pick"), not an arbitrary disambiguation.

### 3.2 Exact-best *drops* at v3 (92% → 84%) — and that's v3 being more right
- **What:** adding guardrails *lowered* exact-best agreement with the oracle.
- **Why:** the guardrails reorder for **staleness/freshness** that the pure-scorer
  oracle ignores (e.g. demoting a stale-but-green reading below a fresh one). So
  v3's #1 sometimes differs from the oracle's exact pick **while staying in the best
  tier** (top-1 agreement stays 100%).
- **Slide:** "When your agent disagrees with your oracle, sometimes the agent is
  right — the oracle is a proxy, know its blind spots."

### 3.3 Safety 0% at v1/v2 is luck, not a guarantee
- v1/v2 also scored 0% safety violations on the 25 scenarios — but they have no
  guardrail, so that's the model self-avoiding (variance). Only **v3 cannot
  recommend blocked water by construction.** v0's 16% is the real "ungrounded is
  unsafe" signal. Never present v1/v2's 0% as a guarantee.

### 3.4 Personalization is confounded (n=4)
- v1 (no memory) already hits 75% on the memory scenarios because **cooler water is
  generically better for trout**, so the catch-log's *quantitative* lift is muted.
  The clearer evidence of memory is **qualitative** — v2/v3 rationales explicitly
  cite the angler's logged band ("your proven 52.2–57.1°F brown-trout band"), which
  v1 cannot. Treat the small-n delta as directional; show the rationale as the proof.

---

## 4. Architecture & technical decisions

- **Scorer as single source of truth.** The agent's scorer is a faithful mirror of
  the production `main.score_conditions`, pinned by an **840-case parity test**. The
  same function is the agent's grounding tool *and* the eval oracle — so the eval
  measures judgment & safety, not arithmetic, and can never drift from what the app
  shows users.
- **Real MCP server + a hand-written tool-use loop** (not a framework's runner) —
  legibility over cleverness: every tool choice, the guardrail veto, and the model
  split are visible and walk-through-able.
- **Cheap/strong model split** — Haiku drives the tool-heavy retrieval loop, Sonnet
  writes the ranking. Config-driven so it's a visible cost/latency lever
  (~$0.02/run, ~17s).
- **Data resolution: injected → live → fixture.** Eval is deterministic/offline
  (injected); the demo hits live USGS/NOAA with a recorded-fixture fallback so it
  never breaks. Every tool result carries a `source` for grounding + the trace.
- **`AGENT_SCENARIO` read from env at call time** (not import time) so in-process
  callers (the proactive watcher) can inject conditions too, not just the subprocess.

---

## 5. Operationalization / build-process learnings

- **Latency is skewed by structured-output schema compilation.** The first call with
  a new JSON schema is slow (~29s, one-time compile; 24h cached after), so v0's
  avg latency looked inflated. Warm up the schema before timing.
- **Background jobs:** a `nohup`-detached process gets killed by the harness; run the
  long job as a *tracked* background task. Use `python -u` or stdout block-buffering
  hides per-version progress until the process exits.
- **Commit messages with backticks** get command-substituted inside a double-quoted
  shell string (``as `extra` `` ran `extra`), mangling the message — use a
  single-quoted heredoc or `-F`.
- **Sandbox egress:** USGS NWIS + NOAA api.weather.gov are reachable here; the live
  data path genuinely works (verified real flows), which is why the fixture fallback
  is a safety net, not the primary path.

---

## Running facts for the deck
- Trip-planner v0→v3: agreement **8→100%**, safety **16→0% (enforced)**,
  hallucination **100→0%**, personalization **0→100%** (n=4, confounded).
- ~$0.02/run, ~17s/run at the Haiku+Sonnet split; full 25-scenario × 4-version
  sweep ≈ 33 min, ≈ $2.3; total session spend ≈ $5 (two runs + artifacts).
- 850 tests (840 scorer-parity + 10 guardrail).
