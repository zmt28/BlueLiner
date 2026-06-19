# Demoing the agents in the BlueLiner UI (LOCAL ONLY)

This shows the **trip-planner** and the **prospector** running live inside the
real BlueLiner map — recommendations and discovered reaches light up on the map,
and the agent's reasoning (evidence, guardrail vetoes, grounding, cost/latency)
renders in a floating panel.

It is built so the **public deployment can never spend your Anthropic API key.**

---

## Why your API key is safe (the 110% answer)

The public site at the Render URL is a *different process* from the demo. The
protection is layered so that no single mistake exposes the key:

1. **The public app never has the endpoint.** Production runs `main:app`. The
   agent routes live in `agent/demo_api.py`, which `main.py` does **not** import.
   They only exist when you run `agent/demo_server.py` — a separate, local entry
   point. Hit `/api/agent/plan` on the public site and you get a plain 404.
2. **The public app can't even run the agent.** Production installs
   `requirements.txt`; the agent stack (`mcp`, `anthropic`, `langgraph`,
   `shapely`) is only in `agent/requirements.txt`. The code that would call
   Anthropic isn't deployed.
3. **The public app has no key.** `ANTHROPIC_API_KEY` lives only in your local
   shell when you launch the demo. It is never committed and must never be set
   on the Render web service.
4. **The key never touches the browser.** It stays server-side. The browser only
   ever calls your *local* server's `/api/agent/*` routes; the model calls
   happen in Python on your machine.
5. **Off by default, even locally.** Every endpoint is dead unless
   `AGENT_DEMO_ENABLED=1`, and you can require a shared secret with
   `AGENT_DEMO_TOKEN`. So even a stray deploy of `demo_server` with the flag
   unset exposes nothing.

**Blast-radius insurance:** use a **dedicated Anthropic Console key** for this,
with a low monthly spend cap, and revoke it after the interview. Then even a
worst case is bounded and reversible.

> Verify production isn't holding the key: in the Render dashboard (or via the
> Render MCP tools) confirm the web service's env has no `ANTHROPIC_API_KEY`.
> Even if it did, layers 1–2 mean it can't be spent — but remove it anyway.

---

## Run it locally

### Recommended: one server, production-like

```bash
# 1) install the agent deps (once)
pip install -r agent/requirements.txt

# 2) build the frontend so FastAPI serves it at / (includes the demo script tag)
npm install
npm run build

# 3) launch the LOCAL demo server with your key + the flag
export ANTHROPIC_API_KEY=sk-ant-...        # dedicated, capped, revocable key
AGENT_DEMO_ENABLED=1 uvicorn agent.demo_server:app --port 8000

# 4) open http://localhost:8000  → the 🎣 Agent panel appears bottom-right
```

(`demo_server` defaults `AGENT_DEMO_ENABLED=1` for you, so you can omit it; an
explicit `AGENT_DEMO_ENABLED=0` still wins if you want it off.)

### Alternative: Vite dev mode (hot reload, two terminals)

```bash
# terminal 1 — local agent server (the API + your key)
export ANTHROPIC_API_KEY=sk-ant-...
AGENT_DEMO_ENABLED=1 uvicorn agent.demo_server:app --port 8000

# terminal 2 — Vite dev server (proxies /api and /static to :8000)
npm run dev
# open http://localhost:5173
```

### Optional: lock the demo behind a token

```bash
export AGENT_DEMO_TOKEN=some-shared-secret
AGENT_DEMO_ENABLED=1 uvicorn agent.demo_server:app --port 8000
```

The panel prompts for the token once and stores it in `localStorage`; every
call then sends it as `X-Agent-Demo-Token`. Mismatch → 401.

---

## Using the panel

- **Plan a trip** — uses the current **map center** and the selected **state**
  as the angler's location. Optional free-text preferences. Toggle the
  orchestrator (hand loop vs LangGraph — identical results, the A/B point).
  Recommendations plot as numbered green/amber/red discs; guardrail-blocked
  rivers show faded red with the veto reason. The panel shows each pick's
  verdict, "why", grounding status, latency and cost.
- **Discover water** — runs the prospector over the selected state. Prospects
  plot as purple dashed reach lines with numbered markers, ranked by the
  deterministic confidence; the panel shows the evidence, "why not higher", and
  an access-verification flag. Click any card to fly the map to it.

Live USGS/NOAA is used when available, with the recorded fixtures as fallback,
so the demo never dead-ends if an upstream is slow.

---

## What's wired where

| Piece | File | Role |
|---|---|---|
| Gated API router | `agent/demo_api.py` | `/api/agent/health\|plan\|discover`, off unless `AGENT_DEMO_ENABLED=1`; lazy agent imports; sync handlers so `asyncio.run` works in the threadpool |
| Local entry point | `agent/demo_server.py` | wraps `main:app`, includes the router — **never deployed** |
| Self-gating panel | `static/agent-demo.js` | ships in the bundle but renders nothing unless `/api/agent/health` says `enabled:true` |
| Script tag | `static/index.html` | one `<script defer>` before `</head>` |
