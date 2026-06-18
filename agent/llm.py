"""Thin Anthropic client wrapper + usage/cost accounting.

Keeps the agent loop legible: one place that calls the Messages API, one place
that tallies tokens and dollars so the eval report can show the cheap/strong
model split moving latency and cost. No thinking/effort/sampling params are set
-- these are short structured turns, and keeping the surface minimal avoids
model-specific 400s across the Haiku/Sonnet split.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import anthropic

from .config import model_price_per_mtok


@dataclass
class Usage:
    """Accumulates token usage and an estimated cost across a run."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    by_model: dict = field(default_factory=dict)

    def add(self, model: str, u) -> None:
        i = getattr(u, "input_tokens", 0) or 0
        o = getattr(u, "output_tokens", 0) or 0
        cr = getattr(u, "cache_read_input_tokens", 0) or 0
        self.input_tokens += i
        self.output_tokens += o
        self.cache_read_tokens += cr
        m = self.by_model.setdefault(model, {"input": 0, "output": 0})
        m["input"] += i
        m["output"] += o

    def cost_usd(self) -> float:
        total = 0.0
        for model, t in self.by_model.items():
            pin, pout = model_price_per_mtok(model)
            total += t["input"] / 1e6 * pin + t["output"] / 1e6 * pout
        return round(total, 5)

    def summary(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "by_model": self.by_model,
            "est_cost_usd": self.cost_usd(),
        }


class LLM:
    """Wraps anthropic.Anthropic and records usage into a shared meter."""

    def __init__(self, usage: Usage | None = None):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Add it to the environment to run "
                "the agent (see agent/README.md)."
            )
        self.client = anthropic.Anthropic()
        self.usage = usage or Usage()

    def message(self, *, model: str, system: str, messages: list,
                tools: list | None = None, tool_choice: dict | None = None,
                max_tokens: int = 2048, output_schema: dict | None = None):
        kwargs: dict = {
            "model": model, "max_tokens": max_tokens,
            "system": system, "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice
        if output_schema is not None:
            kwargs["output_config"] = {
                "format": {"type": "json_schema", "schema": output_schema}
            }
        resp = self.client.messages.create(**kwargs)
        self.usage.add(model, resp.usage)
        return resp
