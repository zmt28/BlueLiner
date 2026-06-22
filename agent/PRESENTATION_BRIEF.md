# BlueLiner Agents — Presentation Design Brief

**Purpose of this document:** everything a designer needs to build the slide deck for a
final-round **Senior AI Product Manager** interview presentation. It is self-contained —
all content, numbers, narrative, talk track, visual direction, and an asset list are here.
You should not need to read the codebase.

> Format note for the designer: build ~18–20 content slides + an appendix. Target a
> **20–30 minute talk + Q&A**. Every slide below lists a **key message** (the one thing
> that slide must land), **on-slide content** (kept sparse — this is a talk, not a doc),
> a **visual** suggestion, and a **speaker note** (what the presenter says; not on the slide).

---

## 1. The context (read first)

- **Audience:** final-round interview panel for a **Senior AI Product Manager** role.
  Assume a mix of product, engineering, and leadership. They are evaluating **product
  judgment, technical literacy, evaluation rigor, intellectual honesty, and the ability
  to make and defend decisions** — not raw model output.
- **The artifact:** I built a working **agentic layer on top of a real, deployed product**
  — *BlueLiner*, a live web app that scores real-time stream conditions for fly fishers
  (aggregates USGS water data + NOAA weather, scores it, renders it on an interactive map).
  Two agents were built:
  1. **Trip Planner** — decision-support agent: "where should I fish, and is it safe/worth it?"
  2. **Prospector** — generative *discovery* agent: finds undesignated-but-fishable trout water.
- **Why it matters:** this is not a toy demo. It runs on **live data, real users, real cost,
  and a public deployment** — so it forced real product decisions (trust, safety, cost,
  security, "what's worth surfacing").

## 2. The throughline (the single thesis)

> **"Trustworthy AI is a product discipline, not a model feature. I built two agents on a
> live product and made every claim grounded, every safety rule enforced, every result
> measured against an honest oracle — and I can show the receipts."**

Everything in the deck should ladder up to this. The differentiator vs. a typical candidate
is **honesty + measurement + judgment**, not "I used LLMs."

## 3. Narrative arc (the spine)

1. **Hook** — I shipped agents into a real product, here's the proof.
2. **The product & user** — what BlueLiner is, the job-to-be-done.
3. **What I built** — two agents, one shared trustworthy spine.
4. **Trip Planner** — naive → grounded → personalized → guardrailed (v0→v3), measured.
5. **Prospector** — discovery as "undervalued inventory," measured honestly.
6. **A product decision** — define the negative space (same-stream extensions).
7. **Engineering judgment** — orchestration A/B (right tool for the job).
8. **Shipping safely** — the security posture as a PM artifact.
9. **Live demo.**
10. **Roadmap + how I work.**

---

## 4. Slide-by-slide outline

### Slide 1 — Title
- **Key message:** set the frame: trustworthy AI, shipped into a real product.
- **On-slide:** "BlueLiner Agents — Shipping Trustworthy AI into a Live Product." Presenter
  name, role applied for, date. Small subtitle: "An agentic layer over a real-time
  stream-conditions app."
- **Visual:** the BlueLiner map (Maryland stream network) as a full-bleed background, dimmed,
  with the title overlaid. Brand navy `#0B2A3A`.
- **Speaker note:** "I want to show you how I think about product by walking through something
  I actually built — two AI agents on top of a live app I run."

### Slide 2 — Why this artifact (credibility)
- **Key message:** real product, live data, real cost — not a sandbox.
- **On-slide:** 3 chips: "Live data (USGS + NOAA)" · "Public deployment" · "Real API cost
  (~$0.02/decision)." One line: "Every decision below was forced by shipping, not theorizing."
- **Visual:** three icon chips in the condition palette (green/blue/ochre).
- **Speaker note:** Briefly: I maintain BlueLiner; it serves live conditions to fly fishers.
  Building agents on it meant facing trust, safety, cost, and security for real.

### Slide 3 — The product & the user
- **Key message:** clear job-to-be-done grounds everything.
- **On-slide:** "JTBD: *Where should I fish this weekend — and is it worth the drive?*"
  Below: inputs the app already has (live flow/temperature, 30-yr USGS medians, trout
  designations, public access). A condition legend: Good / Fair / Poor / No-data.
- **Visual:** screenshot of a BlueLiner river detail with the colored condition discs.
- **Speaker note:** Fly fishing is conditions-driven — flow and water temp make or break a day
  (and trout ethics: don't fish warm water). The app scores this; the agent makes it a decision.

### Slide 4 — What I built (overview)
- **Key message:** two agents, one shared *trustworthy spine*.
- **On-slide:** two columns — **Trip Planner** (decision support) and **Prospector**
  (discovery) — sitting on a shared base bar labeled: "Deterministic scorer · MCP tools ·
  Guardrails · Eval harness." Caption: "Same spine; right tool per job."
- **Visual:** simple architecture block diagram (see Asset list A1).
- **Speaker note:** I deliberately reused one spine so I could measure both honestly and so
  the demo can't diverge from what I evaluated.

### Slide 5 — Operating principle: legibility over cleverness
- **Key message:** I optimized for explainability end-to-end.
- **On-slide:** three principles:
  - "Every step is walk-through-able" (hand-written tool loop, not a black-box runner).
  - "The scorer is the single source of truth" — *the agent's tool is literally the eval's oracle.*
  - "The naive→v3 evolution is visible in git" — I show what didn't work.
- **Visual:** a small "v0 → v1 → v2 → v3" staircase motif (reused later).
- **Speaker note:** A senior PM should be able to explain *why* the system did what it did.
  I traded a little cleverness for a lot of legibility.

### Slide 6 — Trip Planner: the v0→v3 staircase (the headline)
- **Key message:** grounding + guardrails turn a confident liar into a trustworthy assistant.
- **On-slide:** THE results table (25 injected scenarios, deterministic):

  | Version | Top-1 agreement ↑ | Safety violations ↓ | Hallucinated readings ↓ |
  |---|---|---|---|
  | v0 — naive prompt, no tools | **8%** | **16%** | **100%** |
  | v1 — tool-grounded (USGS/NOAA + scorer) | 100% | 0% | 4% |
  | v2 — + catch-log memory | 100% | 0% | 12% |
  | v3 — + guardrails & grounding contract | **100%** | **0%** | **0%** |

- **Visual:** animate the staircase row by row; color the v0 cells clay `#B3473B`, the v3
  cells moss `#4A8C5C`. This is the deck's centerpiece chart.
- **Speaker note:** v0 invents every number and recommends flooded/warm/private water. v1
  (grounding in real tool results) is the big jump. v3 makes safety and grounding a
  *guarantee*, not luck.

### Slide 7 — How I made it trustworthy
- **Key message:** two mechanisms — grounding contract + hard guardrails.
- **On-slide:** two cards:
  - **Grounding contract:** "Every number in the answer must trace to a tool result; if not,
    regenerate once, then strip it." → hallucinated readings 100% → 0%.
  - **Safety guardrails (hard rules, enforced in code, not prompts):** flood (flow > 3× median)
    · water temp out of trout-ethics band (block > 68°F / demote < 40°F) · private-access block
    · staleness demotion. → "v3 *cannot* recommend blocked water, regardless of what the model says."
- **Visual:** two cards, with small icons (water drop, thermometer, lock, clock).
- **Speaker note:** Guardrails are deterministic Python *after* the model proposes — the model
  advises, the rules decide. That's how you get a guarantee.

### Slide 8 — How I measured it (the eval is the product)
- **Key message:** I evaluated against a deterministic oracle, and I state the caveats.
- **On-slide:**
  - "Oracle = BlueLiner's own scorer, parity-tested (840 cases) against the production code."
  - "25 scenarios: ideal, flood, too-warm, private, stale, adversarial, memory, ties, all-blocked."
  - Honest caveats (verbatim, as bullets):
    - "Safety 0% at v1/v2 is *luck*; only v3 cannot recommend blocked water *by construction*."
    - "Personalization is confounded (n=4) — the real signal is qualitative: v2/v3 rationales
      cite the angler's catch-log; v1 can't."
- **Visual:** a clean "eval loop" diagram: scenario → agent → scorer-oracle → metrics.
- **Speaker note:** The honesty here is the point. I'd rather show a confounded result and name
  it than overclaim.

### Slide 9 — The cost / latency lever (PM framing)
- **Key message:** quality, cost, and latency are tunable levers I chose deliberately.
- **On-slide:** "Model split: **Haiku** drives the cheap, tool-heavy retrieval loop; **Sonnet**
  writes the final ranking." Result: **~$0.02 / decision, ~17–18s.** One line: "Config, not
  code — the deck can move the lever live."
- **Visual:** a simple two-lane diagram (cheap retrieval lane → strong ranking lane) with a
  cost meter.
- **Speaker note:** A PM should treat cost/latency/quality as a portfolio. Most of the calls are
  cheap retrieval; the one expensive call is where judgment matters.

### Slide 10 — A real bug I found and fixed (depth)
- **Key message:** I find and close real failure modes, including security-shaped ones.
- **On-slide:** "The guardrail safety-bypass." Before: the model reformatted a river's id
  (`gunpowder_falls_md` vs `gunpowder-falls-md`) → the safety lookup missed → the veto was
  silently skipped. Fix: canonicalize ids before the veto + a regression test. One line:
  "A safety control that fails *open* is worse than none."
- **Visual:** a tiny before/after code-flow with a red "veto skipped" → green "veto enforced."
- **Speaker note:** This is the kind of thing that doesn't show in a happy-path demo but is
  exactly what kills trust in production.

### Slide 11 — Prospector: the discovery problem (the business analogy)
- **Key message:** discovery = finding undervalued inventory.
- **On-slide:** "Official maps only show *designated* trout water. But plenty of fishable trout
  water is **undesignated** — invisible inventory." Analogy callout: "Red Ventures: the value is
  in *qualifying inventory the market has mispriced*." The agent's job: surface undesignated-but-
  fishable reaches, ranked and qualified.
- **Visual:** map with designated reaches highlighted vs. faint undesignated lines; a few faint
  lines circled as "candidates."
- **Speaker note:** Tie explicitly to the business framing the panel will recognize — the agent
  is an inventory-qualification engine.

### Slide 12 — Prospector: how it works
- **Key message:** cheap deterministic ranking at scale; LLM only where it adds value.
- **On-slide:** pipeline: "100K+ reaches → **topology proximity** to known trout water
  (geometry proxy) → flow/size + thermal + access signals → deterministic confidence →
  **LLM writes the rationale on the shortlist only** → human-in-the-loop confirm → flywheel."
  One line: "Deterministic ranks 60K reaches for free; the LLM touches only the top-K."
- **Visual:** the LangGraph pipeline diagram (Asset A2) with the human-confirm interrupt marked.
- **Speaker note:** The model doesn't *score* — it *explains*. Confidence stays deterministic and
  calibrated; the LLM earns its cost only on the rationale.

### Slide 13 — Measuring discovery honestly (the hard part)
- **Key message:** I built an eval that refuses to flatter itself.
- **On-slide:** "Held-out-labels backtest (MD/VA/PA, 36,378 designated reaches):"
  - "**Mask whole rivers** (by flow path), not random reaches — else 'discovery' is trivial
    segment in-painting (the first version scored a fake AUC of 0.999)."
  - "**Hard-negative AUC** = held-out trout vs. only *near-trout* undesignated reaches — the honest,
    harder number."
  - "**Positive-unlabeled:** unlabeled reaches aren't negatives — so my recall is a *lower bound*.
    My eval undercounts my wins by construction."
- **Visual:** two AUC gauges side by side: "optimistic (random negatives) ~0.99" vs "honest
  (hard negatives) ~0.51 for the gated model." Label clearly.
- **Speaker note:** The interesting finding: topology is a near-perfect *lead generator*, but the
  binding constraint — public access — is also our biggest *data gap*. The bottleneck is data
  coverage, not modeling.

### Slide 14 — Product decision: define the negative space ⭐ (signature slide)
- **Key message:** deciding what NOT to surface is half of product quality.
- **On-slide:**
  - The bad result: *"Elks Run — undesignated reach on the same-named designated trout stream,
    distance 0.0 mi."*
  - The judgment: "That's not a discovery — it's the rest of a stream we already show on the map.
    **Obvious is as disqualifying as wrong.**"
  - The decision: "**Exclude** same-stream extensions (don't just label them) — a clearer label on
    a useless result is still useless."
  - Receipts: "Removed **29% of candidates**; distance-0 results → **0**; surfaced the genuine
    *different-tributary* leads. And it aligned the live agent with the eval's definition of a
    discovery — I reported the metric move *with its cause*, not just the prettier number."
- **Visual:** before/after of the ranked list (left: trivial same-stream items at top; right:
  real tributary leads). Big pull-quote treatment.
- **Speaker note (the soundbite):** "A recommender's credibility dies the first time it tells you
  something you already know."

### Slide 15 — Engineering judgment: right tool for the job (controlled A/B)
- **Key message:** I measured the framework instead of cargo-culting it.
- **On-slide:** "Same v3 planner, 25 scenarios, **only the orchestration changes** (hand-written
  loop vs. LangGraph):"

  | | Quality (agreement/safety/halluc) | Orchestration code |
  |---|---|---|
  | Hand-written loop | 100% / 0% / 0% | **17 lines** |
  | LangGraph | 100% / 0% / 0% | **38 lines (2.2×)** |

  Decision: "Hand-loop for the **linear** planner; LangGraph for the **branching, human-in-the-loop**
  prospector — where `interrupt()` + durable checkpoints genuinely earn it."
- **Visual:** balance-scale graphic; identical quality, unequal code/complexity.
- **Speaker note:** Frameworks aren't a quality lever; claiming so would be a confound. The
  honest result tells you *where* the framework pays for itself.

### Slide 16 — Shipping it safely (security as a PM artifact)
- **Key message:** I bounded a real downside before it could bite.
- **On-slide:** "The threat: the public app becoming a free, unmetered proxy to my API key."
  Four independent layers (any one suffices):
  1. Public app never mounts the agent endpoint.
  2. Agent dependencies aren't in the production image.
  3. No API key on the web service.
  4. Off by default (feature flag, optional token).
  Plus: "Blast-radius insurance: a dedicated, spend-capped, revocable key."
- **Visual:** four concentric "defense-in-depth" rings.
- **Speaker note:** "Can you 110% guarantee it's safe?" The answer is "yes — and I bounded the
  downside anyway." Reversibility is a product decision.

### Slide 17 — LIVE DEMO
- **Key message:** it's real; watch it work.
- **On-slide:** just "Live demo" + 3 tiny prompts: "Plan a trip · Discover water · Explain the why."
- **Visual:** full-screen handoff to the app. (See the **Demo script**, section 7.)
- **Speaker note:** Keep it tight; narrate the grounding/guardrail/why-not-higher moments.

### Slide 18 — Roadmap / what I'd do next
- **Key message:** I know the next highest-leverage move.
- **On-slide:**
  - "Close the access data gap (wire PAD-US public-land polygons) — the bottleneck is data
    coverage of the binding constraint, not modeling."
  - "Exact flow-network topology (NLDI) for the shortlist."
  - "Grow the confirm/deny flywheel → calibration improves as anglers confirm."
- **Visual:** a simple now/next/later roadmap bar.
- **Speaker note:** Note that the eval *pointed* at the roadmap — access coverage, not a better model.

### Slide 19 — How I work (the close)
- **Key message:** restate the PM principles the whole talk demonstrated.
- **On-slide:** five principles:
  1. **Legibility over cleverness.**
  2. **The eval is the product** — honest oracle, named caveats.
  3. **Decisions defended with data** (and reported with their cause).
  4. **Define the negative space** — obvious is as bad as wrong.
  5. **Bound the downside** — security/cost/reversibility as product calls.
- **Visual:** five clean principle tiles in the brand palette.
- **Speaker note:** "These are the habits I'd bring to your team — independent of the tech stack."

### Slide 20 — Q&A / Appendix divider
- **On-slide:** "Thank you — questions?" with a faint metrics strip at the bottom.
- **Visual:** return to the map background from slide 1 (bookend).

---

## 5. Key metrics cheat-sheet (exact, for charts & callouts)

**Trip Planner — 25 injected scenarios, deterministic oracle:**
| Version | Top-1 agree | Exact-best | Safety viol | Hallucinated | Coverage | Latency | Cost/run |
|---|---|---|---|---|---|---|---|
| v0 | 8.0% | 8.0% | 16.0% | 100.0% | 100% | ~25.0s | $0.0178 |
| v1 | 100% | 92.0% | 0.0% | 4.0% | 100% | ~17.5s | $0.0226 |
| v2 | 100% | 92.0% | 0.0% | 12.0% | 100% | ~17.2s | $0.0226 |
| v3 | 100% | 84.0% | 0.0% | 0.0% | 100% | ~18.1s | $0.0238 |

- Personalization (4 memory scenarios, confounded): top pick = angler's pattern river —
  v0 0% → v1 75% → v2 75% → **v3 100%**.

**Prospector — held-out backtest (MD/VA/PA; 36,378 designated reaches; 405 held out across whole
rivers; 4,000 sampled background):**
- Topology-only: recall@100 **0.247**, hard-negative AUC **0.986**, PR-AUC 0.991.
- Full (access-gated) model: recall@100 0.119, hard-negative AUC **~0.51 (≈ chance)**, PR-AUC 0.49,
  **access violations: 0**.
- Product decision impact (MD): same-stream extensions = **29% of candidates (1,765 / 6,012)**;
  distance-0 results → **0**.

**Orchestration A/B — v3 planner, 25 scenarios, only orchestration changes:**
- Quality identical: 100% agreement / 0% safety / 0% hallucination both ways.
- Hand-written loop: 19,421 ms, $0.02647, **17 lines** of orchestration.
- LangGraph: 19,790 ms, $0.02523, **38 lines (2.2×)**.

**Testing:** **860 automated tests** (840 scorer-parity vs production + guardrail + suitability +
prospector-graph + reach-data candidate-pool invariants).

## 6. Soundbites (room-ready lines — sprinkle as pull-quotes)

- "Trustworthy AI is a product discipline, not a model feature."
- "The model advises; the rules decide." (guardrails)
- "A safety control that fails *open* is worse than none." (the bug)
- "My eval undercounts my wins by construction." (positive-unlabeled honesty)
- "A recommender's credibility dies the first time it tells you something you already know."
  (same-stream exclusion — the signature line)
- "Obvious is as disqualifying as wrong."
- "Frameworks aren't a quality lever — so I measured where one actually pays for itself."
- "Can you 110% guarantee it's safe? Yes — and I bounded the downside anyway."
- "The eval pointed at the roadmap: the bottleneck is data coverage of the binding constraint, not a better model."

## 7. Live demo script (for the presenter; design just needs the DEMO slide)

1. **Open** the BlueLiner map (Maryland), agent panel docked on the left.
2. **Plan a trip:** type a preference ("dry-fly, wadeable"), click *Plan from map center*.
   Narrate: recommendations plot green/amber/red; open one card → read the grounded "why";
   point out a guardrail-blocked river (private/flood) with its veto reason; note latency + cost.
3. **Toggle orchestrator** (hand vs LangGraph) → same result. One line: "framework doesn't change quality."
4. **Discover water:** click *Discover in this state*. Purple dashed reaches appear; open a card →
   read the rationale + "why not higher" + access-verify flag. Click a card → map flies to it.
5. **Land the product decision:** "Notice these are *different* streams near trout water — not the
   untagged rest of a known stream. I cut those on purpose."
- Fallback: recorded screen capture in case of network/live-data flakiness.

## 8. Anticipated Q&A (prep the presenter; not slides)

- **"Isn't the scorer-as-oracle circular?"** The scorer is BlueLiner's *existing, production* scoring
  logic, parity-tested in 840 cases; the eval tests whether the *agent* selects/acts correctly, not
  whether the scorer is right. Conditions are injected, so ground truth is exact.
- **"Personalization n=4 is thin."** Agreed and stated on-slide; the durable signal is qualitative
  (rationales cite the catch log). I'd grow the scenario set and run a within-angler A/B next.
- **"Why not multi-agent?"** The prospector's subtasks are deterministic; an agent-per-geometry-calc
  is overhead by construction. I argued it rather than building a strawman.
- **"Why did the discovery metrics improve when you removed same-stream extensions — isn't that
  gaming?"** No — those reaches are positive-unlabeled (likely real trout water) that were wrongly
  counted as negatives. The honest metric (gated hard-neg AUC) stayed ~chance; I reported the move
  *with its cause*.
- **"Hallucination at 0% — really?"** On these injected scenarios, yes, by the grounding contract +
  one regeneration. In the wild I'd monitor it as a live metric, not claim a permanent zero.
- **"What's the business case?"** Trip Planner = retention/engagement (better decisions, less
  drive-wasted churn). Prospector = inventory expansion (qualified new water = differentiated content).

## 9. Visual & brand direction

**Palette (from the BlueLiner design system — use these exact hexes):**
- Brand ink / primary: `#0B2A3A` (deep navy — backgrounds, headers)
- Light blue / accent: `#5BA8C8` and `#95C5D9` (water motif, links, highlights)
- Condition **Good** (moss): `#4A8C5C` (deep `#2F6B3D`)
- Condition **Fair** (ochre): `#B7892F` (deep `#8A5A14`)
- Condition **Poor** (clay): `#B3473B` (deep `#8A3327`)
- **No-data / neutral** (stone): `#7F8B9C`
- **Prospector accent** (purple): `#7A3DB8` (use ONLY for prospector/discovery content)
- Surfaces: white / very light stone `#EEF0F3`.

**Use the palette semantically:** green = good/positive deltas, clay = the v0/failure state,
ochre = caution/caveats, purple = the prospector. Don't introduce off-brand colors.

**Type:** clean humanist sans (system UI / Inter / Source Sans). Big numbers for metrics. Sparse
slides — headline + 3–5 bullets max. Let the data and the map carry the visuals.

**Iconography:** thin line icons (Lucide-style — the app already uses Lucide): water drop, fish,
thermometer, lock, clock, map-pin, layers, shield/rings (security), scale (A/B).

**Chart styling:** the v0→v3 table is the hero — make it a bold, animated staircase. AUC comparisons
as paired gauges. Keep gridlines minimal; label everything (assume the room reads it from a distance).

**Tone:** confident, precise, honest. This deck's personality is "senior, data-led, no hype." Avoid
AI clichés (no glowing brains, no robots). The water/map aesthetic IS the visual identity.

## 10. Asset list (what design needs to source or create)

- **A1.** Architecture block diagram: two agents on a shared spine (scorer · MCP tools · guardrails · eval).
- **A2.** Prospector pipeline diagram: generate → gather → (ungauged?) infer-thermal → score →
  verify → rank → **human-confirm (interrupt)** → flywheel. Mark the conditional branch + the interrupt.
- **A3.** Trip-Planner flow: request → cheap retrieval loop (MCP/USGS/NOAA) → strong ranker →
  guardrails/grounding → answer.
- **A4.** The v0→v3 staircase chart (hero).
- **A5.** Paired AUC gauges (optimistic vs honest).
- **A6.** Before/after ranked-list mock for the same-stream-exclusion slide.
- **A7.** Defense-in-depth concentric rings (security).
- **A8.** Screenshots from the running app (presenter to provide): the map with the left-docked
  agent panel; a trip-plan result with colored discs + a blocked river; a discovery result with
  purple dashed reaches; one open "why" card. (These exist in the live local demo.)
- **A9.** BlueLiner logo mark (the three blue waves) — available in the app header.

## 11. Glossary (so design labels things correctly)

- **Grounding / grounded:** every number in the answer traces back to a real tool result (no invented readings).
- **Guardrail:** a hard, deterministic safety rule applied *after* the model proposes (flood, temperature, private access, staleness).
- **Oracle:** the deterministic scorer used as ground truth in the eval (BlueLiner's production scoring logic).
- **Top-1 agreement:** the agent's top pick is safe and in the oracle's best rating tier.
- **Hallucinated reading:** a flow/temperature number in the answer that no tool actually returned.
- **Designated trout water:** a stream officially classified as trout habitat by a state agency (what the map shows).
- **Undesignated reach:** a stream segment with no official trout classification — the prospector's candidate pool.
- **Same-stream extension:** an undesignated segment of a stream that is *already partly designated* — excluded as a non-discovery.
- **Topology (proxy):** geometric proximity to the nearest designated trout water — the prospector's main signal.
- **Held-out backtest:** hide some designations, see if the agent rediscovers them — the discovery eval.
- **Hard-negative AUC:** the honest separability number (held-out trout vs. only near-trout undesignated reaches).
- **Positive-unlabeled:** unlabeled reaches aren't true negatives, so recall is a lower bound.
- **MCP:** Model Context Protocol — how the agent's tools are exposed.
- **Orchestration:** the code that sequences the agent's steps (hand-written loop vs. LangGraph).
