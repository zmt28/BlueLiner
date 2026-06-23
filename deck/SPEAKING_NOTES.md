# BlueLiner Agents — Speaking Notes

**Talk:** Senior AI PM final round, Red Ventures · **Length:** target ~24 min talking + demo, leave room for Q&A · **Deck:** 20 slides

## Delivery cheat sheet
- **One-sentence thesis:** "I shipped a real agentic system into a live product, and the interesting work wasn't the model — it was making it *trustworthy*: legible, grounded, guardrailed, and honestly evaluated."
- **Three themes to hammer** (every slide should ladder to one): (1) **Legibility over cleverness**, (2) **The eval is the product**, (3) **Bound the downside / be honest about limits**.
- **Tone:** you're a PM who can go deep technically but always returns to the product/business decision and the tradeoff. Volunteer the caveats before they're asked — that's the credibility move.
- **Pacing:** don't read the cards. The cards are receipts you *point at* ("you can see here…") while you talk. If you're over time, the compressible slides are 6, 9, 13, 19.
- **Red Ventures throughline:** this is a recommendation/qualification engine over mispriced inventory with hard business guardrails — say that explicitly on slides 11 and 19.

---

### Slide 1 — Shipping Trustworthy AI into a Live Product (~0:30)
**Goal:** Frame the talk in one breath; set the "real product" bar.
**Say:**
- "I'm Zion. I'm going to walk you through BlueLiner Agents — an agentic layer I built on top of a fly-fishing conditions app that's actually deployed and serving live data."
- "My focus today isn't 'I called an LLM.' It's the part that's hard in production: making an agent you can *trust* — and proving it."
**Transition:** "Let me start with why I built this as a real product instead of a slide-ware demo."

---

### Slide 2 — Why This Project (~0:45)
**Goal:** Establish credibility: this is live, cheap, and decisions were forced by shipping.
**Say:**
- "BlueLiner is a real app — live USGS flow and water-temperature data, NOAA weather, publicly deployed, about two cents a decision."
- "That matters because every design choice you'll see was forced by shipping, not theorized on a whiteboard. Real data is messy, costs are real, and safety is real."
- "So when I say a decision was hard, it's because production made it hard."
**Transition:** "Here's the user and the job they're hiring the product to do."

---

### Slide 3 — The Product & User (~1:30, includes the video)
**Goal:** Make the JTBD concrete; show the working app.
**Say:**
- "The core job: *'Where should I fish this weekend, and is it worth the drive?'* That's a decision under uncertainty with a real cost — gas, a day off."
- "The app already has the raw inputs: live flow and temp from USGS, 30-year medians for *today's* date so we can say 'is this normal,' trout designations, and public access points."
- "Every location gets scored Good / Fair / Poor / No-data and rendered on a map." *(Let the short video play — narrate one beat: "you can see conditions resolve on the map here.")*
- "The agent's job is to turn that map into a *recommendation* with a reason — and to refuse to recommend something unsafe or illegal."
**Transition:** "So how is it built? Two agents, one shared trustworthy spine."
**If asked "who's the user?":** "Primarily DIY anglers planning a trip; secondarily a discovery user looking for new water. Same data backbone."

---

### Slide 4 — Two Agents, One Trustworthy Spine (~2:30) — architecture
**Goal:** The system design landmark. Get them to absorb the spine in 20 seconds, then narrate the flow.
**Say:**
- "Two agents. The **Trip Planner** answers 'where should I fish?' The **Prospector** answers 'find me *new* trout water the maps miss.'"
- "They share one spine, and the spine is the whole point: an **MCP tool belt** for retrieval, a **deterministic scorer** — I call it the oracle — a **grounding contract**, and a **guardrail veto**."
- "Read the spine top to bottom: the model retrieves through tools, the scorer rates conditions deterministically, the grounding contract forces every number to trace to a tool result, and the guardrails can veto the model's answer outright."
- "The tagline is **'the model advises; the rules decide.'** The LLM never has final say on anything safety-critical."
- "Data sources on the right are all real — USGS, NOAA, state ArcGIS, PAD-US, Postgres. Output is a ranked, grounded recommendation at about two cents and ~17 seconds."
- "And the dashed band at the bottom matters: the scorer in the spine is *also* the eval oracle. Same code grades the agent and grades my tests. I'll come back to that."
**Transition:** "Before the mechanics, the principle that drove every choice."
**If asked "why MCP?":** "Tool calls are a clean contract and let me reuse the exact same tools across both agents and the eval — no second implementation to drift."
**If asked "is this multi-agent?":** "No — single agents. The Prospector is one agent expressed as a graph, not a swarm. I deliberately avoided agent-on-agent complexity I couldn't evaluate."

---

### Slide 5 — Operating Principle: Clarity over complexity (~1:30) — trace card
**Goal:** Establish the value system; use the trace as proof of legibility.
**Say:**
- "My operating principle was legibility over cleverness — optimize for explainability end to end."
- "Three consequences. It's **inspectable**: a hand-written tool loop, not a black-box framework, so I can walk every step. **Single source of truth**: the scorer is both the agent's tool and the eval's oracle. **Visible evolution**: the v0-to-v3 progression lives in git, and I show what *didn't* work."
- *(Point at the trace card)* "This is one real decision, fully traced — from an eval scenario. The angler asks about Beaver Creek near Hagerstown. Watch the tools fire in order, then the guardrail vetoes Beaver Creek for private-only access, and the agent recovers with Gunpowder Falls — grounded, 16.6 seconds, two cents."
**Transition:** "That legibility let me improve the planner in measurable steps. Here's the staircase."
**Important honesty note (say if relevant or if asked):** "That trace is an eval scenario — the live UI takes a map pin plus a preferences box and finds candidates by proximity; I use the scenario harness so the trace is reproducible."

---

### Slide 6 — Trip Planner: The v0 → v3 Staircase (~2:00)
**Goal:** Show iteration with numbers, including a regression you fixed.
**Say:**
- "Four versions, 25 scenarios, one variable at a time. **v0** is a naive prompt, no tools — it hallucinates readings. **v1** adds tool grounding. **v2** adds catch-log memory. **v3** adds guardrails and the grounding contract."
- "Recommendation agreement goes 8% → 100%. Safety violations 16% → 0%. Hallucinated readings 100% → 0%."
- "But be honest about the middle: **v2's hallucination rate got *worse*, 4% to 12%.** Adding memory made the model invent unsourced numbers. The v3 grounding contract is what drove it to zero. I show that because the regression is the most informative part."
**Transition:** "So how did v3 actually achieve trust? Two mechanisms."
**If asked "what's 'agreement'?":** "The top pick is safe *and* in the oracle's best-rated tier — not exact-match, which would be brittle."

---

### Slide 7 — How Trustworthiness Was Achieved (~1:45) — guardrail card
**Goal:** The trust mechanism, concretely.
**Say:**
- "Two mechanisms: grounding and hard guardrails."
- "**Grounding contract:** every number in the rationale must trace to a tool result this session. If it can't, regenerate once, then strip it. That's the 100%-to-0% hallucination drop."
- "**Guardrails are deterministic code, not prompts** — that's the key design call." *(Point at the rulebook)* "Flood: flow over 3x median, block. Too warm over 68°F, block — that's a trout-ethics call, warm water kills released fish. Too cold, demote. Private access, block. Stale data, demote and label."
- "Because they're code, v3 *cannot* recommend blocked water by construction. A prompt can be argued with; a veto can't."
**Transition:** "Claims like 100%-to-0% are only worth anything if the measurement is honest. So let me show you how I measured."
**If asked "why not just prompt the model to be safe?":** "Prompts are probabilistic and you can't unit-test them. Safety needs to be deterministic and regression-tested — which is exactly the bug on slide 10."

---

### Slide 8 — How I Measured It: The Eval Is the Product (~2:00) — scoreboard
**Goal:** This is your strongest PM slide — own evaluation methodology and its limits.
**Say:**
- "I evaluated against a deterministic oracle — BlueLiner's own scorer, parity-tested in 840 cases against production. So the grader is real product code, not vibes."
- "25 scenarios across the cases that actually break things: ideal, flood, too-warm, private, stale, adversarial, memory, ties, all-blocked." *(Point at scoreboard)* "v3: zero safety violations, zero hallucinated readings, 100% top-1 agreement across all ten types."
- "Now two honest caveats I want to *volunteer*: First, safety being 0% in v1 and v2 is **luck**, not design — only v3 makes it structural. Second, the personalization result is **confounded — n=4** — so I treat that signal as qualitative, not a number I'd defend."
- "Saying that out loud is the point. An eval you can't poke holes in isn't an eval."
**Transition:** "Speaking of holes — here's one I found in production."
**If asked "isn't grading yourself with your own scorer circular?":** "For *conditions* scoring, yes by design — the scorer is the spec. The eval tests whether the *agent* respects that spec end-to-end: tool use, grounding, guardrails, ranking. Different failure surface."

---

### Slide 9 — Cost & Latency per decision (~1:00)
**Goal:** Show you manage the economics and treat the model split as a lever.
**Say:**
- "Two cents and about 18 seconds per decision."
- "The lever is the model split: **Haiku** drives the cheap, tool-heavy retrieval loop; **Sonnet** writes the single final ranking. Cheap model does the many calls, strong model does the one that needs judgment."
- "And it's **config, not code** — I can move that split without touching logic, which is how I'd tune cost vs quality per surface."
**Transition:** "Now the production bug."
**If asked "18 seconds is slow":** "For a weekend trip-planning decision, latency tolerance is high; I optimized cost first. If this were interactive I'd stream partials and parallelize the retrieval fan-out."

---

### Slide 10 — Real Bug Discovery (~1:45) — code-diff card
**Goal:** Demonstrate engineering depth and a safety mindset under failure.
**Say:**
- "A safety control that fails *open* is worse than none, because it gives false confidence."
- "**The bug:** the model occasionally reformatted a river ID — underscore instead of hyphen. My evidence lookup missed, so the guardrail silently skipped and the veto never ran. It failed open." *(Point at the BEFORE block)*
- "**The fix** was one idea: canonicalize every ID *before* the veto — lowercase, normalize separators — so a format drift can never bypass safety. Plus a regression test that asserts blocked water stays blocked." *(Point at AFTER + green stamp)*
- "The lesson is a PM one: safety controls need adversarial tests, not happy-path tests."
**Transition:** "That's the Trip Planner. The second agent is a different and frankly harder problem — discovery."
**If asked "how did you catch it?":** "It surfaced in an adversarial eval scenario where the model echoed a reformatted id — the eval is what caught it, which is the slide-8 point made concrete."

---

### Slide 11 — The Discovery Challenge (~1:30) — inventory card
**Goal:** Reframe discovery as a business problem; land the Red Ventures parallel.
**Say:**
- "Discovery is really *finding undervalued inventory*."
- "Official maps show only *designated* trout water — about 36,000 reaches across three states. But plenty of fishable trout water is *undesignated*: invisible inventory the maps don't list." *(Point at the bar gap)*
- "**This is the Red Ventures parallel:** the value isn't in the inventory everyone already prices — it's in qualifying the inventory the market has mispriced."
- "So the Prospector scans 100,000-plus undesignated reaches and tries to surface the ones that are actually fishable, ranked and qualified."
**Transition:** "Here's how it ranks them — cheaply."
**If asked to make the RV link explicit:** "Same shape as performance marketing: huge candidate pool, a cheap deterministic qualifier to rank, an expensive model only on the shortlist, and a human confirm step that compounds into better calibration."

---

### Slide 12 — Prospector Mechanics (~1:45) — pipeline diagram
**Goal:** Show the cost-aware architecture and where the LLM earns its place.
**Say:**
- "The principle: cheap deterministic ranking at scale; the LLM *only* where it adds value."
- "100,000-plus reaches ranked by topology proximity to known trout water — a geometry proxy — plus flow, thermal, and access signals. **Deterministic ranks 60,000 reaches for free; the LLM only touches the top-K shortlist** to write the rationale."
- *(Point at the pipeline)* "It's a LangGraph state machine: generate candidates, gather evidence, a conditional branch for ungauged reaches, score, verify, then *rank* — the only LLM call — then a **human-confirm** step, then update the flywheel."
- "Confidence stays deterministic and calibrated; the model explains, it doesn't re-score. That keeps it cheap and auditable."
**Transition:** "And does it actually work? I built an eval that refuses to flatter itself."
**Honesty note (volunteer on slide 18 or if asked):** "The human-confirm step is wired as a durable interrupt and I've proven it end-to-end, but the live demo runs it headless — I'll be precise about that."

---

### Slide 13 — Measuring Discovery (~2:00) — discovery scoreboard
**Goal:** Your methodology-integrity showpiece. The unflattering number is the hero.
**Say:**
- "An eval that refuses to flatter itself. First, **mask whole rivers by flow path**, not random segments — otherwise discovery is just trivial in-painting. My v1 scored a fake 0.999 AUC doing exactly that; masking is the fix."
- "The honest number is **hard-negative AUC**: can it rank held-out trout water above *only near-trout decoys*? Topology alone gets 0.986 — nearly perfect at finding leads." *(Point at green chip)*
- "But here's the one I want you to look at: **enforce verified public access and it drops to 0.512 — basically chance.**" *(Point at amber chip)* "That's not a model failure. The binding constraint — public access — is my biggest *data* gap. Topology is a great lead generator; access is what I can't yet verify at scale."
- "And **positive-unlabeled**: an unlabeled reach isn't a negative, so my recall is a *lower bound* — the eval undercounts wins by construction."
- "Calibration holds where it matters: the 0.6-to-0.8 confidence bucket hits 53%."
**Transition:** "Which raises the other half of recommendation quality — what you *don't* show."
**This is the slide to slow down on. If asked "so discovery doesn't work?":** "Lead-gen works very well; *actionability* is gated by access data. That's a roadmap item, not a modeling dead-end — and naming it is the honest version of the result."

---

### Slide 14 — Defining Negative Space (~1:30) — exclusion card
**Goal:** Show product judgment: deciding what to suppress.
**Say:**
- "Deciding what *not* to surface is half of product quality."
- "**The bad result:** Elks Run — an undesignated reach on a stream we *already show on the map*, topology distance 0.0 miles. The model could label it a 'discovery.'"
- "**The judgment:** that's not a discovery, it's the map relabeled. Obvious is as disqualifying as wrong."
- "**The decision:** exclude same-stream extensions, don't just relabel them — a clearer label on a useless result is still useless." *(Point at receipts)* "That removed 29% of candidates, dropped distance-zero results to zero, and surfaced the real tributary leads. I reported the metric move *with its cause.*"
- "A recommender's credibility dies the first time it tells you something you already know."
**Transition:** "A quick word on engineering judgment, because the framework question always comes up."

---

### Slide 15 — Engineering Judgment: Right Tool for the Job (~1:30)
**Goal:** Pre-empt the "did you just bolt on LangGraph" critique; show controlled comparison.
**Say:**
- "Same v3 planner, 25 scenarios, I changed *only* the orchestration — hand-written loop versus LangGraph."
- "Result: identical quality, 100% both. The only deltas were operational — and the hand loop was 17 lines, LangGraph 38."
- "So **frameworks are not a quality lever** — claiming otherwise would be a confound. What LangGraph *buys* is capability: durable interrupts and checkpointing, not better answers."
- "That's why I made opposite calls: hand loop for the linear Trip Planner because it's less code and fully legible; LangGraph for the branching, human-in-the-loop Prospector, where the interrupt and durable checkpoints actually earn their cost."
**Transition:** "Shipping any of this means bounding the downside — starting with my own API key."
**If asked "would you use a framework again?":** "Where it earns it — branching, durable resume, human-in-the-loop. Not as a default."

---

### Slide 16 — Shipping It Safely (~1:30) — four-layer card
**Goal:** Show you think about blast radius, not just features.
**Say:**
- "The threat I cared about: the public app becoming a free, unmetered proxy to my API key."
- "Four independent layers, **any one of which is sufficient**." *(Point at stack)* "The public app never mounts the agent endpoint. Agent dependencies aren't even in the production image. There's no key on the web service. And it's off by default behind a flag plus an optional token."
- "Then blast-radius insurance: a dedicated, spend-capped, revocable key. So even a total failure is bounded — small, capped, and killable."
- "The honest framing: can I 110% guarantee it's safe? No one can. So I bounded the downside instead. That's the security posture of a PM, not a wish."
**Transition:** "Let me show you it actually working."

---

### Slide 17 — Live Demo (~2:00, flex)
**Goal:** Prove it's real. Keep it tight and pre-narrated so a hiccup doesn't sink you.
**Say (before you click):** "I'll run one Trip Planner decision: you'll see grounded recommendations, a guardrail-blocked river, and the real cost and latency."
**During:** narrate the three beats — "tools gathering live data… the guardrail blocking the private water… the grounded pick with its reasons, and the cost/latency in the corner."
**Safety net:** "If the network's unkind, I have the captured trace from slide 5 that shows the identical path."
**On the Prospector/HITL, be precise:** "The Discover tab runs the graph headless, so it won't pause live. The human-in-the-loop interrupt is wired and I've proven it end-to-end with a captured resume trace — I can show that artifact rather than fake a pause."
**Transition:** "If I had more time, here's where I'd take it."

---

### Slide 18 — Roadmap: What I'd Do Next (~1:15) — ladder
**Goal:** Show prioritization tied to evidence, not a wish list.
**Say:**
- "Four moves." *(Point at ladder)* "First, pull the human-in-the-loop into the UI — make that headless confirm interactive, since the mechanism already exists."
- "Second — and this is the **highest-leverage** one — close the access data gap by wiring PAD-US public-land polygons. Remember slide 13: access is the binding constraint, 139 of my top 250 leads are flagged 'access unverified.' That's a data-coverage problem, not a model problem."
- "Third, exact flow-network topology via NLDI for sharper shortlisting. Fourth, grow the confirm/deny flywheel so calibration improves as anglers confirm."
- "The point: **the eval pointed me at the roadmap — not at a better model.** That's the whole philosophy in one line."
**Transition:** "Which is really how I work as a PM."

---

### Slide 19 — How I Work (~1:00)
**Goal:** Convert the project into transferable PM principles. This is the "hire me" slide.
**Say:**
- "Five principles this talk demonstrated, and they transfer directly to Red Ventures."
- "Legibility over cleverness. The eval is the product — with an honest oracle and named caveats. Decisions defended with data and reported *with their cause*. Define the negative space — obvious is as bad as wrong. And bound the downside — security, cost, and reversibility are product calls, not afterthoughts."
- "Swap 'trout water' for 'mispriced inventory' and this is the same job: a grounded, guardrailed recommendation engine you can trust and prove."
**Transition:** "I'll stop there — thank you."

---

### Slide 20 — Thank you / Questions (~0:30)
**Say:** "Thank you — I'd love to dig into any of it: the eval design, the guardrails, the discovery methodology, or how I'd adapt this to a Red Ventures surface."

---

## Q&A prep — the hard ones (rehearse these)
- **"Your trace is an eval scenario, not the live UI — isn't that cheating?"** → "It's reproducible by design. The live UI takes a map pin and a preferences string; the scenario harness lets me show the *exact same code path* deterministically. Nothing in the path is faked — the guardrail and grounding logic are identical."
- **"The human-in-the-loop isn't in the demo."** → "Correct, and I'll be precise: it's implemented as a LangGraph interrupt with durable SqliteSaver checkpointing, and I've verified it end-to-end — the graph pauses, a resume command comes back later, state restores, the flywheel records the decision. The web demo runs headless because I didn't build the confirm-UI round-trip; that's roadmap item #1. The mechanism is real; the UI isn't."
- **"Discovery AUC collapses to 0.512 with access — so it doesn't work?"** → "Lead-gen works — 0.986. Actionability is gated by access data I can't verify at scale yet. I'd rather show you the honest 0.512 and a data roadmap than a flattering number that breaks in the field."
- **"Personalization?"** → "Confounded, n=4. I won't defend it as a number. Qualitatively the catch-log memory biases toward a user's proven temp/flow bands; proving it needs more users, which is a data problem."
- **"Why not a bigger model / fine-tune?"** → "The eval didn't point at the model — it pointed at data coverage and grounding. Spending on a model wouldn't move the binding constraint."
- **"Is two cents real?"** → "Measured, $0.0228 on the captured run. The Haiku/Sonnet split is the lever, and it's config-tunable per surface."
- **"How does this generalize to Red Ventures?"** → "Same architecture: large candidate pool, cheap deterministic qualifier, expensive model only on the shortlist, hard business guardrails the model can't override, a human-confirm flywheel, and an honest eval against a real oracle. The domain is interchangeable; the trustworthy spine is the product."
- **"What would you cut if you had half the time?"** → "I'd keep the spine and the eval and cut a second agent. Trust infrastructure first, surface area second."

## Timing summary
Core narrative ≈ 24 min; demo 2 min flex; the compressible slides if you're long are 6, 9, 13(trim to two numbers), 19. If you're short, expand slide 13 (methodology) and the Q&A on generalization.
