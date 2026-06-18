"""Blueliner Trip-Planning Agent.

An agentic layer over the existing Blueliner app: an LLM-driven agent that,
given an angler's location/dates/preferences, plans the best river(s) to fish
by selecting and calling tools over Blueliner's live data, grounds every claim
in real readings, respects hard safety guardrails, personalizes from the user's
catch log, and is measured by an eval harness that uses Blueliner's
deterministic scorer as ground truth.

Standalone module: talks to Blueliner's data sources and DB; does not modify
core app behavior.
"""

__all__ = ["__version__"]
__version__ = "0.3.0"
