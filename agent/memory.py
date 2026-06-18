"""Durable context = the signed-in angler's enriched catch log.

`summarize_user_patterns` reads the user's catches from Blueliner's Postgres/
SQLite (via the app's `db` module) and distills them into compact, model-
readable notes -- the conditions present when this angler actually caught fish.
Injected into the ranking context for signed-in users; skipped cleanly for
anonymous ones.

Privacy: only ever queries by `user_id`, so one angler's rows never leak into
another's context. Summaries cite sample sizes so the model (and the reader)
can weight thin evidence appropriately.
"""

from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Optional

import db  # Blueliner's root module (dual SQLite/Postgres)


def _ratio(env: dict) -> Optional[float]:
    flow = env.get("flow_cfs")
    med = env.get("flow_median_cfs")
    if isinstance(flow, (int, float)) and isinstance(med, (int, float)) and med > 0:
        return flow / med
    return None


def _span(values: list[float]) -> Optional[list[float]]:
    """Robust [low, high] band: 10th-90th percentile, or min/max for small n."""
    vals = sorted(v for v in values if isinstance(v, (int, float)))
    if not vals:
        return None
    if len(vals) < 5:
        return [round(vals[0], 1), round(vals[-1], 1)]
    lo = vals[int(0.1 * (len(vals) - 1))]
    hi = vals[int(0.9 * (len(vals) - 1))]
    return [round(lo, 1), round(hi, 1)]


def summarize_user_patterns(user_id: int, max_species: int = 4) -> dict:
    """Compact summary of the conditions under which this angler catches fish."""
    try:
        catches = db.list_catches(user_id, limit=500)
    except Exception:
        catches = []

    if not catches:
        return {"user_id": user_id, "total_catches": 0,
                "summary": "No catch history for this angler (treat as a new user)."}

    by_species: dict[str, dict] = {}
    for c in catches:
        env = c.get("env") or {}
        sp = (c.get("species") or "unknown").strip().lower()
        b = by_species.setdefault(sp, {"n": 0, "temps": [], "ratios": [], "hatches": []})
        b["n"] += 1
        if isinstance(env.get("water_temp_f"), (int, float)):
            b["temps"].append(env["water_temp_f"])
        r = _ratio(env)
        if r is not None:
            b["ratios"].append(r)
        for h in (env.get("active_hatches") or []):
            b["hatches"].append(h)

    species_out = []
    for sp, b in sorted(by_species.items(), key=lambda kv: -kv[1]["n"])[:max_species]:
        top_hatch = Counter(b["hatches"]).most_common(1)
        species_out.append({
            "species": sp, "n": b["n"],
            "water_temp_f": _span(b["temps"]),
            "flow_ratio": _span(b["ratios"]),
            "top_hatch": top_hatch[0][0] if top_hatch else None,
        })

    return {
        "user_id": user_id,
        "total_catches": len(catches),
        "species": species_out,
        "summary": _render(species_out, len(catches)),
    }


def _render(species_out: list[dict], total: int) -> str:
    """One-paragraph, token-cheap brief for the ranking model."""
    if not species_out:
        return f"{total} catches logged but no enriched conditions to summarize."
    parts = []
    for s in species_out:
        bits = [f"{s['species']} (n={s['n']})"]
        if s["water_temp_f"]:
            bits.append(f"water {s['water_temp_f'][0]}-{s['water_temp_f'][1]}F")
        if s["flow_ratio"]:
            bits.append(f"flow {s['flow_ratio'][0]}-{s['flow_ratio'][1]}x median")
        if s["top_hatch"]:
            bits.append(f"best hatch: {s['top_hatch']}")
        parts.append(", ".join(bits))
    return "This angler's productive conditions -- " + "; ".join(parts) + "."
