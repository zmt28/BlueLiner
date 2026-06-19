"""LOCAL-ONLY demo server. DO NOT DEPLOY.

Wraps the real BlueLiner app (`main:app`) and bolts on the gated agent demo
router so the trip-planner and prospector can be driven from the live UI.

The public deployment runs `main:app` directly — it never imports this module,
never installs the agent dependencies, and has no ANTHROPIC_API_KEY. This file
is the ONLY place the agent endpoints exist, and it defaults the demo flag on
for itself, so the intended workflow is simply:

    export ANTHROPIC_API_KEY=sk-ant-...      # your local key, never committed
    AGENT_DEMO_ENABLED is defaulted on here
    uvicorn agent.demo_server:app --reload --port 8000

Then open http://localhost:8000 — the agent panel appears because /api/agent/health
returns enabled. To lock it down further, set AGENT_DEMO_TOKEN before launch.
"""

from __future__ import annotations

import os

# This entry point IS the demo, so default the master switch on. An explicit
# AGENT_DEMO_ENABLED=0 in the environment still wins (setdefault won't clobber).
os.environ.setdefault("AGENT_DEMO_ENABLED", "1")

from main import app  # noqa: E402  (real BlueLiner app — static, /api/rivers, etc.)
from agent.demo_api import router  # noqa: E402

app.include_router(router)
