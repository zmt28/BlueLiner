"""Structured run logging -- the monitoring evidence.

Each plan_trip call produces one JSONL record: the request, version, model
split, every tool call (name, args, latency, the `source` it cited), guardrail
vetoes, final confidence, token cost, and the final answer. This is what makes
"show me a real trace" answerable, and what a production deployment would ship
to a log sink to catch failure modes (hallucinated readings, guardrail vetoes,
latency/cost drift).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from . import config


@dataclass
class ToolCall:
    name: str
    args: dict
    latency_ms: int
    source: Optional[str] = None
    ok: bool = True


@dataclass
class RunTrace:
    request: dict
    version: int
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    cheap_model: str = ""
    strong_model: str = ""
    tool_calls: list = field(default_factory=list)
    violations: list = field(default_factory=list)
    confidence: Optional[str] = None
    usage: dict = field(default_factory=dict)
    latency_ms: int = 0
    result: dict = field(default_factory=dict)
    error: Optional[str] = None

    _t0: float = field(default=0.0, repr=False)

    def start(self):
        self._t0 = time.monotonic()
        return self

    def record_tool(self, name: str, args: dict, latency_ms: int,
                    source: Optional[str], ok: bool = True):
        self.tool_calls.append(asdict(ToolCall(name, args, latency_ms, source, ok)))

    def finish(self, result: dict, usage: dict, confidence: Optional[str] = None):
        self.latency_ms = int((time.monotonic() - self._t0) * 1000)
        self.result = result
        self.usage = usage
        self.confidence = confidence
        self.violations = result.get("violations", [])
        return self

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        return d

    def write(self, path=None) -> "RunTrace":
        path = path or (config.LOG_DIR / "runs.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(self.to_dict(), default=str) + "\n")
        return self
