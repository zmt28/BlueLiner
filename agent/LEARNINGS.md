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

- **LangGraph install needs a constraint here.** `pip install langgraph
  langgraph-checkpoint-sqlite` tripped on a Debian-managed `PyJWT`; pinning
  `PyJWT==2.7.0` via `-c constraints.txt` let it resolve. (langgraph pulls
  `langchain-core` — that's expected and fine; the "no LangChain" boundary is
  about not using LangChain's chains/agents, not avoiding the small core dep.)
- **A PR tracks its branch head — mind the multi-PR cadence.** Pushing step-2
  commits to the same branch as an open step-1 PR would silently absorb them into
  that PR. Workflow that keeps PRs clean: land the step-1 PR, fast-forward the
  branch to the merged main, then continue — so each step is its own reviewable PR.

## 6. Prospector (discovery agent) — eval methodology learnings

These came out of building the held-out-labels backtest on real MD/VA/PA reaches
(103.6K bundled reaches, 36.4K designated trout reaches as labels). Most are
about making a *discovery* eval honest — a strong "how I measured / what didn't
work" thread.

### 6.1 Mask whole RIVERS, not random reaches (segment in-painting)
- **What:** first version held out random reaches and got ROC-AUC 0.999 —
  suspiciously perfect. A masked reach is geometrically adjacent to other visible
  segments of the *same* river, so "proximity to trout water" recovers it
  trivially. That's segment in-painting, not discovery.
- **Fix:** mask whole watercourses by `levelpathid`, so a held-out river must be
  found via proximity to a *different* trout river — genuine discovery.
- **Slide:** evaluation rigor — "the first eval was measuring the wrong thing."

### 6.2 Easy negatives inflate AUC → add a hard-negative test
- Trout water clusters geographically, so held-out trout rivers separate from a
  *random* undesignated background near-perfectly (AUC ~0.99) — optimistic. Added
  a **hard-negative AUC**: held-outs vs only *near-trout undesignated* reaches.
  That's the honest, harder number where topology can't separate and the other
  signals must. Bracketing optimistic (random) vs pessimistic (hard) AUC is the
  senior move.

### 6.3 For trout, SIZE is a weak/negative signal — topology dominates
- Naively weighting stream-order/flow into suitability *collapsed* held-out
  recall (the gated AUC inverted, hard-neg AUC < 0.5). Reason: **trout thrive in
  small cold tributaries** (order 2-3), but the undesignated background is filtered
  to order ≥3 — so "bigger = better" demotes exactly the water we want. Lesson:
  the broad ranker is **topology-dominant** (0.8 weight); size is a soft floor, not
  a discriminator.

### 6.4 Offline thermal is uninformative — it belongs on the shortlist, not the broad ranker
- Without network, there are no same-network gauge readings, so a thermal term is
  a constant that just *dilutes* topology. Decision: thermal counts only when
  actually gauged; the real thermal refinement happens on the **top-K shortlist**
  via the LLM/live-fetch layer (the v1→v2 lever), not in the deterministic broad
  ranker. This also motivates the architecture: deterministic topology ranks 60K
  reaches for free; the LLM touches only the shortlist (bounded cost).

### 6.5 Access is the binding constraint AND the data gap (the RV punchline)
- **Finding:** topology is a near-perfect *lead generator* (AUC 0.99), but
  enforcing access **collapses** recall (AUC 0.99 → 0.77, recall@100 0.25 → 0.07).
  Not a model bug: we only have access **POINTS** (PAD-US public-land *polygons*
  were retired to vector tiles), and they're sparse and **skewed toward private
  easements on trout water**, so a naive access filter zeroes the very reaches we
  want.
- **Design response (matches the uncertainty guardrail):** access is a *guardrail*
  (hard-exclude only KNOWN-private — absence of a mapped point ≠ private) plus an
  *uncertainty flag* ("verify access locally"), NOT a rank demotion. Access
  violations (surfacing known-private) = 0; ~most surfaced prospects carry the
  verify-access flag — that count *is* the data-gap metric.
- **The slide:** "the value and the bottleneck are both in qualifying inventory on
  the binding constraint" — and here the bottleneck is *data coverage of that
  constraint*, so the roadmap lever is wiring PAD-US polygons back in, not more
  modeling. Direct RV "undervalued inventory" mapping.

### 6.6 Topology is a geometry-proximity PROXY (documented limitation)
- The bundled NHDPlus VAA keeps no downstream pointers (`comid`, `hydroseq`,
  `levelpathid`, `streamlevel`, `gnis_name`, `lengthkm` only), so we can't walk the
  flow network to *prove* "tributary of a trout stream." We approximate with
  shapely geometry proximity to the nearest designated trout reach (offline,
  scales to 60K). Exact flow-network tributary topology via NLDI navigation is the
  expansion path — but it doesn't scale to a whole-region backtest under rate
  limits.

### 6.7 Positive-unlabeled, stated plainly
- Non-held-out undesignated reaches are *unlabeled*, not negatives — a top-ranked
  one may be a real discovery the backtest can't credit. So recall is a **lower
  bound** and PR-AUC uses sampled background as proxy negatives. "My eval
  undercounts my wins by construction" is itself a differentiator.

### 6.8 A product critique tightened BOTH the output and the eval (same-stream extensions)
- **The critique (from review):** the agent's top results were things like "Elks
  Run — undesignated reach on the same-named designated trout stream, distance
  0.0 mi." That's not a discovery — it's *the rest of a stream we already know is
  trout water*, and the map already renders designations per-reach, so the angler
  can see it. At distance 0.0 these dominated the ranking and **buried** the
  genuinely novel leads (a *different* tributary near trout water).
- **The fix, in one principled unit:** drop any candidate whose `levelpathid`
  matches a known-trout reach's. Same `levelpathid` = same continuous flow path =
  same stream. Chose levelpathid over name because names ("Mill Creek", "Beaver
  Creek") repeat across genuinely different streams — name-matching would wrongly
  delete real discoveries. This is the **same masking unit as §6.1**, so the live
  agent and the eval now define "a valid discovery" identically. In MD this
  removed **29% of candidates** (1,765 / 6,012) and dropped distance-0 results to
  **zero**, leaving 81 real near-a-*different*-trout-stream leads.
- **The honest eval delta (isolated, MD/VA/PA):** topology-only barely moved
  (recall@100 0.247→0.247, hard-neg AUC 0.981→0.986) — it never leaned on these.
  The **full** (access-gated) model improved (hard-neg AUC 0.316→~0.50,
  recall@100 0.069→0.096) because the removed extensions were **positive-unlabeled**
  (§6.7) — almost-certainly-trout water that was being counted as *negatives* and
  unfairly penalizing the gated ranker. The full model's hard-neg AUC is *still*
  ~chance (~0.50), so the honest §6.5 finding ("access-gating costs
  discrimination") is **preserved, not cooked** — the gain is removing mislabeled
  negatives, not a real lift in separability.
- **The slide:** a sharp product question ("isn't this result useless?") drove a
  change that cleaned the product, aligned live behavior with the eval's
  definition of success, *and* surfaced a measurement bias — and I reported the
  metric move with its cause instead of just banking the prettier number.

## 7. LangGraph orchestration — what the framework did (and didn't) buy

### 7.1 LangGraph moved reliability/legibility, NOT quality — say this plainly
- The prospector's ranking quality is the **deterministic** suitability/confidence
  (calibrated in the backtest); the LLM only writes the rationale. So adopting
  LangGraph did **not** change recall/precision/calibration — and claiming it did
  would be the confound to avoid. What LangGraph bought: explicit, inspectable
  control flow (the graph diagram *is* the trace), a clean conditional branch
  (ungauged → `infer_thermal`), a first-class human-in-the-loop **interrupt**, and
  **durable checkpointing**. That's an operationalization/legibility win, framed
  honestly.

### 7.2 The durable interrupt actually works (the operationalization proof)
- Verified end-to-end: the graph **paused** at `human_confirm` (interrupt firing on
  the top prospect), and after a later `Command(resume=...)` the `SqliteSaver`
  checkpointer **restored state and resumed** to `update_flywheel`, which recorded
  the confirmation. This is the concrete "resumes across cron/session boundaries"
  benefit for the proactive flywheel — not a slide-only claim.

### 7.3 Right tool for the job: graph for the prospector, hand-loop for the planner
- The trip-planner is **linear** (retrieve → rank → guard) and a hand-written
  Anthropic tool-use loop kept it maximally legible — a graph would have been
  over-abstraction. The prospector has **real branching + a human interrupt +
  proactive resumption**, which is exactly what earns LangGraph. Presenting both,
  and *why each*, is the "measured framework choice" slide.

### 7.4 Measured-restraint decisions (the "what I chose not to build" slide)
- **LangGraph, not LangChain, and only for orchestration:** tools stayed on MCP,
  scorer/guardrails stayed plain Python, no higher-level chains.
- **Deterministic gather; LLM only on the shortlist:** `gather_evidence` calls the
  tools deterministically (exhaustive + free); one strong-model call writes the
  rationale over the verified shortlist. A per-candidate LLM tool-loop adds
  cost/latency without improving recall (the deterministic gather is exhaustive).
  One Sonnet call per discovery ≈ $0.04; deterministic topology ranks the whole
  region for free.
- **Single agent, not multi-agent:** one discovery graph, not separate
  topology/thermal/access agents + an orchestrator (more latency/cost, no recall
  gain at this scale).
- **Confidence stays deterministic — the LLM explains, it does not re-score.**
  Keeps the calibration curve meaningful and the rationale grounded.

## 8. The controlled harness A/B — measured, not asserted (the framework slide)

Ran the trip-planner v3 over the same 25 scenarios under **both** orchestrations
(hand-written loop vs LangGraph), holding tools/scorer/guardrails/prompts/model
split identical — `apply_guardrails` and `_gather`/`_rank` are literally the same
functions, so the only variable is the sequencing.

| Orchestrator | Agreement | Safety | Hallucinated | Avg latency | Cost/run | Orchestration LOC |
|---|---|---|---|---|---|---|
| hand  | 100% | 0% | 0% | 19,421 ms | $0.0265 | 17 |
| graph | 100% | 0% | 0% | 19,790 ms | $0.0252 | 38 |

- **Quality is identical** — agreement/safety/hallucination flat. The framework is
  **not a quality lever**; claiming it boosted metrics would be a confound. Saying
  this out loud (and showing it) is the differentiator vs. a framework-chaser.
- The only real delta is **2.2× the orchestration code** (38 vs 17 lines) — a state
  class, node wrappers, graph wiring, and a dependency — for a **linear** workflow
  with nothing to branch on and no interrupt to resume. Latency/cost are within
  noise.
- **The decision this justifies, empirically:** hand-written loop for the linear
  trip-planner; LangGraph for the branching, human-in-the-loop prospector (where
  `interrupt()` + durable checkpointing earned their place — see §7.2). Same
  building blocks, right tool per workflow.
- Pair with the argued (not built) **single-vs-multi-agent** restraint: the
  prospector's subtasks are deterministic, so a multi-agent bake-off would measure
  a strawman — "an agent per geometry calc is overhead by construction."

## 9. Demoing in the product — the security posture as a PM artifact

The live demo runs both agents *inside the real BlueLiner map*. Building it
surfaced a product-security decision worth a slide, because the app is publicly
deployed and the model calls cost real money against my key.

- **The threat I actually had to close:** not "someone steals the key from the
  browser" (it's server-side), but "the public deployment becomes a free,
  unmetered proxy to my Anthropic account." A naive `app.include_router` in
  `main.py` would have shipped a public `/api/agent/plan` that *anyone* could
  spend my key through. That's the failure mode I designed against.
- **Defense in depth beats a single flag.** The key can't be spent in production
  because of *four* independent layers, any one of which suffices: (1) the public
  app never imports the router; (2) the agent dependencies aren't installed in
  the production image; (3) no `ANTHROPIC_API_KEY` on the web service; (4) the
  endpoint is off unless `AGENT_DEMO_ENABLED=1`, optionally behind a token.
  Listing them as independent layers — not "I added an if-statement" — is the
  security-literate framing.
- **Self-gating UI over conditional builds.** `static/agent-demo.js` ships in the
  same bundle the public site serves, then calls `/api/agent/health` and renders
  nothing unless the server says `enabled:true`. So I didn't need a separate
  "demo build" of the frontend; the *server's* posture is the single source of
  truth for whether the feature exists. One artifact, gated at the trust boundary.
- **Blast-radius thinking is a PM instinct, not just a security one.** The right
  answer to "can you 110% guarantee it's safe?" isn't only "yes, here's why" —
  it's *also* "and I bounded the downside anyway": a dedicated, spend-capped,
  revocable Console key. Reversibility/containment as a product decision.
- **A demo is an architecture forcing-function.** Wiring the UI is what made me
  reuse the exact same `plan_trip` / `run_discovery` entry points the eval drives
  — the demo and the eval exercise identical code, so the demo can't quietly
  diverge from the measured system. (Sync FastAPI handlers, so the agents'
  internal `asyncio.run` runs cleanly in the threadpool — a small but real
  integration gotcha.)

## 10. Product decisions — what the agent should (and shouldn't) surface

The methodology lives in §6; this section is the **product judgment** itself,
stated as decisions, because "what counts as a useful result" is a product call
before it's a modeling one.

### 10.1 Don't "discover" what the product already shows (same-stream extensions)
- **Decision:** the prospector does **not** surface a reach that is just another
  segment of an already-designated trout stream — even though it's technically
  "undesignated." Cut entirely, not down-ranked, not badged.
- **Why (the product reasoning, not the model's):** BlueLiner already colors the
  stream network **per reach**, so a partly-designated stream's known sections are
  *already on the map*. A "discovery" that points at the untagged remainder of
  that same stream tells the angler nothing they couldn't already see — it's noise
  dressed as insight. The bar for a discovery is **net-new, actionable
  information**; restating visible data fails that bar. (This is why "exclude"
  beat "label it": a clearer label on a useless result is still a useless result,
  and at distance 0.0 it was *outranking* the real leads.)
- **The line I'd say in the room:** "A recommender's credibility dies the first
  time it tells you something you already know. Same-stream extensions were ~29%
  of candidates and sat at the very top — so the fix wasn't cosmetic, it was the
  difference between a demo that looks obvious and one that looks insightful."
- **Mechanism + rigor:** one principled unit — exclude any candidate sharing a
  designated reach's `levelpathid` (same flow path = same stream; chosen over
  name because names repeat across different streams). It's the **same unit the
  backtest masks by**, so live behavior and the eval now agree on what a discovery
  is. Full mechanics, the held-out invariant, and the honest metric delta are in
  **§6.8** (the change also exposed a positive-unlabeled labeling bias and I
  reported the metric move *with its cause* rather than banking the prettier
  number).
- **Generalizable principle for the deck:** *define the negative space.* Half of
  product quality for a generative/discovery feature is deciding what it must
  refuse to emit — here, "obvious" is as disqualifying as "wrong."

## Running facts for the deck
- Trip-planner v0→v3: agreement **8→100%**, safety **16→0% (enforced)**,
  hallucination **100→0%**, personalization **0→100%** (n=4, confounded).
- ~$0.02/run, ~17s/run at the Haiku+Sonnet split; full 25-scenario × 4-version
  sweep ≈ 33 min, ≈ $2.3; total session spend ≈ $5 (two runs + artifacts).
- 860 agent tests (840 scorer-parity + guardrail + suitability + prospector-graph
  + reach-data candidate-pool invariants).
- Prospector: dropping same-stream extensions removed **29% of MD candidates**
  and zeroed distance-0 results, leaving genuine near-a-*different*-stream leads.
