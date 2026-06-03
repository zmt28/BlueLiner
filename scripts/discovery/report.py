"""Dossier + go/no-go memo rendering.

A dossier is the human-review artifact for one state: the best endpoint we
found, its tier, a drafted wild/stocked mapping (with FLAGs called out), and a
Phase-1-shaped `draft_registry_entry` ready to drop into the future
data/trout/sources.json. The memo aggregates the run into a tier table.

Tiers (fall-through, matching the spec):
  A  live anonymously-queryable line/polygon API  -> ready to wire
  B  download-only (no live query)                -> needs bundling
  C  point geometry / coarse only                 -> federal-baseline fallback
  D  nothing usable found                          -> defer (like ME/TN)
"""
from __future__ import annotations

import json
import os


def _bucket_map(distinct, classify) -> tuple[dict, list]:
    mapping, flags = {}, []
    for value in distinct:
        res = classify.classify(value)
        if res.status == "auto":
            mapping[value] = res.bucket
        else:
            mapping[value] = "FLAG:review"
            flags.append({"value": value, "reason": res.reason})
    return mapping, flags


def build_dossier(state: str, scored: list[dict], classify) -> dict:
    scored = sorted(scored, key=lambda s: s["score"], reverse=True)
    if not scored:
        return {"state": state, "tier": "D", "confidence": "low",
                "caveats": ["no candidate endpoint reached"], "candidates": 0}

    best = scored[0]
    geom = best["geometry"]
    usable = best["anonymous_query"] and best["feature_count"] > 0
    if not usable:
        tier = "D"
    elif geom == "esriGeometryPoint":
        tier = "C"  # points don't suit the NHD line join -> federal fallback
    else:
        tier = "A"

    dossier = {
        "state": state, "tier": tier, "candidates": len(scored),
        "endpoint": best["url"], "geometry": geom,
        "feature_count": best["feature_count"], "source": best["source"],
        "caveats": [],
    }

    if tier in ("C", "D"):
        dossier["confidence"] = "low"
        dossier["caveats"].append(
            "point/empty geometry -- route to a federal multi-state baseline"
            if tier == "C" else "no usable live layer -- defer")
        return dossier

    if best["category_field"]:
        mapping, flags = _bucket_map(best["distinct_values"], classify)
        dossier["classify"] = {"field": best["category_field"], "values": mapping}
        dossier["flags"] = flags
        dossier["confidence"] = "medium" if flags else "high"
        rule_field = best["category_field"]
        classify_block = {"field": rule_field, "values": mapping}
    else:
        # No category field -> single-bucket; infer from the layer title.
        res = classify.classify(best.get("title", ""))
        bucket = res.bucket or "FLAG:review"
        dossier["classify"] = {"whole_layer": bucket}
        dossier["flags"] = [] if res.status == "auto" else [
            {"value": best.get("title", ""), "reason": res.reason}]
        dossier["confidence"] = "high" if res.status == "auto" else "medium"
        classify_block = {"bucket": bucket}

    dossier["draft_registry_entry"] = {
        "state": state, "url": best["url"].rstrip("/") + "/query?where=1=1",
        "geometry": "line" if geom == "esriGeometryPolyline" else "polygon",
        "classify": classify_block, "confidence": dossier["confidence"],
    }
    return dossier


def write_dossier(dossier: dict, out_dir: str) -> None:
    path = os.path.join(out_dir, f"{dossier['state']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dossier, f, indent=2)


def write_memo(dossiers: list[dict], out_dir: str) -> None:
    lines = ["# Trout-source discovery -- run memo", "",
             "| State | Tier | Conf | Endpoint | Flags |",
             "|---|---|---|---|---|"]
    for d in sorted(dossiers, key=lambda x: x["state"]):
        ep = (d.get("endpoint", "") or "")[:60]
        nflags = len(d.get("flags", []))
        lines.append(f"| {d['state']} | {d['tier']} | "
                     f"{d.get('confidence', '-')} | {ep} | {nflags} |")
    tier_a = sum(d["tier"] == "A" for d in dossiers)
    lines += ["",
              f"Tier-A (ready to wire): {tier_a}/{len(dossiers)}",
              "",
              "Classifier accuracy vs the 10 shipped states: run "
              "`python scripts/discover_trout_sources.py eval`."]
    with open(os.path.join(out_dir, "MEMO.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
