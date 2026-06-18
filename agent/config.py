"""Central configuration for the Blueliner agent.

Everything tunable lives here so the presentation can point at single levers:
the cheap/strong model split (cost & latency), the guardrail thresholds
(safety), and the staleness window (user trust). All are overridable via env
vars so a demo can show a lever moving without a code edit.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- Paths --------------------------------------------------------------
PKG_DIR = Path(__file__).resolve().parent
REPO_DIR = PKG_DIR.parent
FIXTURES_DIR = PKG_DIR / "fixtures"
PROMPTS_DIR = PKG_DIR / "prompts"
EVAL_DIR = PKG_DIR / "eval"
LOG_DIR = Path(os.environ.get("AGENT_LOG_DIR", PKG_DIR / "logs"))

# --- Model split (the cost / latency lever) -----------------------------
# Cheap model drives the tool-heavy retrieval loop; strong model writes the
# final ranking + rationale. IDs are config so the deck can show the lever.
CHEAP_MODEL = os.environ.get("AGENT_CHEAP_MODEL", "claude-haiku-4-5")
STRONG_MODEL = os.environ.get("AGENT_STRONG_MODEL", "claude-sonnet-4-6")

# Max tool-use iterations before we force a stop (prevents runaway loops).
MAX_AGENT_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "12"))

# Token ceilings (kept modest — these are short structured turns).
RETRIEVAL_MAX_TOKENS = int(os.environ.get("AGENT_RETRIEVAL_MAX_TOKENS", "2048"))
RANKER_MAX_TOKENS = int(os.environ.get("AGENT_RANKER_MAX_TOKENS", "3072"))

# --- Guardrail thresholds (the safety lever) ----------------------------
# Flood: flow above this multiple of the historical median is unsafe.
FLOOD_RATIO = float(os.environ.get("AGENT_FLOOD_RATIO", "3.0"))
# Trout-ethics temperature band (Fahrenheit). Outside -> demote + warn.
TEMP_MIN_F = float(os.environ.get("AGENT_TEMP_MIN_F", "40"))
TEMP_MAX_F = float(os.environ.get("AGENT_TEMP_MAX_F", "68"))
# Staleness: conditions older than this many hours lower confidence.
STALE_HOURS = float(os.environ.get("AGENT_STALE_HOURS", "6"))

# --- Defaults -----------------------------------------------------------
DEFAULT_RADIUS_MILES = int(os.environ.get("AGENT_DEFAULT_RADIUS_MILES", "90"))
DEFAULT_TOP_N = int(os.environ.get("AGENT_TOP_N", "3"))

# --- Data source resolution ---------------------------------------------
# AGENT_SCENARIO: path to an injected-conditions fixture (set by the eval
# harness). When present, the data tools serve deterministic injected values
# instead of calling live USGS/NOAA -- this is what makes the eval reproducible
# without touching the network. Unset in interactive/proactive mode, where the
# tools go live (USGS/NOAA) with a recorded-fixture fallback.
SCENARIO_PATH = os.environ.get("AGENT_SCENARIO")

# Live fetch timeout (seconds) before falling back to recorded fixtures.
LIVE_TIMEOUT = float(os.environ.get("AGENT_LIVE_TIMEOUT", "8"))

# Where the MCP server module lives (for the agent to spawn over stdio).
MCP_SERVER_MODULE = "agent.mcp_server"


def model_price_per_mtok(model: str) -> tuple[float, float]:
    """(input, output) USD per 1M tokens. Used only for cost estimation in the
    eval report -- not load-bearing. Mirrors the public price sheet."""
    table = {
        "claude-haiku-4-5": (1.0, 5.0),
        "claude-sonnet-4-6": (3.0, 15.0),
        "claude-opus-4-8": (5.0, 25.0),
    }
    return table.get(model, (3.0, 15.0))
