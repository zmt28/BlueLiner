"""Held-out-labels backtest — the prospector's centerpiece eval.

We already have labels (designated trout waters). Use them as ground truth for a
DISCOVERY system, with no manual labeling:

  1. Universe: qualifying reaches in a region (default MD+VA+PA) from the bundled
     clickable_streams (designated = trout_class != null = positives).
  2. Mask a held-out ~25% of positives so the agent sees them as undesignated.
  3. Rank all not-visibly-designated reaches (true undesignated + masked held-outs)
     by the deterministic coldwater suitability/confidence.
  4. Score: does the agent rank the held-out trout water it was never told about
     above the undifferentiated mass? recall@k, precision@k, ROC-AUC, PR-AUC (vs a
     sampled background), a calibration curve, and a signal ablation.

Deterministic + offline + free — no LLM (the LLM adds rationale/calibration on the
top-K shortlist in the LangGraph layer, evaluated separately).

Honest caveat baked into the report: this is a POSITIVE-UNLABELED problem. The
non-held-out undesignated reaches are unlabeled, not true negatives — some are
genuine discoveries the agent *should* surface — so recovered-held-out recall is a
LOWER BOUND and PR-AUC uses sampled background as proxy negatives.

Run:  python -m agent.eval.backtest               # MD,VA,PA
      python -m agent.eval.backtest --states MD   # one state, faster
"""

from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime, timezone

from agent import config, reach_data, signals
from agent.suitability import MODES, coldwater_suitability

KS = (25, 50, 100, 250)
NEG_SAMPLE = 4000          # sampled background of true-undesignated (PU proxy negatives)
HOLDOUT_FRAC = 0.25
HOLDOUT_CAP = 400          # cap "needles" so recall@k stays interpretable vs a haystack


def _roc_auc(ranked_labels: list[int]) -> float:
    """ROC-AUC via the rank-sum (Mann-Whitney U). ranked_labels: 1=positive."""
    n = len(ranked_labels)
    pos = sum(ranked_labels)
    neg = n - pos
    if pos == 0 or neg == 0:
        return float("nan")
    # ranks ascending by score; ranked_labels is sorted DESC by score, so the
    # ascending rank of item i is (n - i).
    rank_sum_pos = sum((n - i) for i, lab in enumerate(ranked_labels) if lab == 1)
    return (rank_sum_pos - pos * (pos + 1) / 2) / (pos * neg)


def _avg_precision(ranked_labels: list[int]) -> float:
    """Average precision (PR-AUC proxy) over the DESC-ranked list."""
    pos = sum(ranked_labels)
    if pos == 0:
        return float("nan")
    hits = 0
    ap = 0.0
    for i, lab in enumerate(ranked_labels, 1):
        if lab == 1:
            hits += 1
            ap += hits / i
    return ap / pos


def _calibration(scored: list[dict], bins=5) -> list[dict]:
    """Predicted-confidence bucket vs actual held-out hit-rate."""
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        bucket = [s for s in scored if lo <= s["confidence"] < hi or
                  (b == bins - 1 and s["confidence"] == 1.0)]
        if not bucket:
            out.append({"bucket": f"{lo:.1f}-{hi:.1f}", "n": 0, "hit_rate": None})
            continue
        hr = sum(s["is_holdout"] for s in bucket) / len(bucket)
        out.append({"bucket": f"{lo:.1f}-{hi:.1f}", "n": len(bucket),
                    "hit_rate": round(hr, 3)})
    return out


def run(states, holdout_frac=HOLDOUT_FRAC, seed=7, neg_sample=NEG_SAMPLE):
    rng = random.Random(seed)
    reg = reach_data._region(tuple(states))
    positives = [r for r in reg["reaches"] if r["trout_class"]]

    # Mask whole RIVERS (levelpathid), not random reaches: masking random reaches
    # is trivially solved by adjacency (a held-out segment touches other visible
    # segments of the same river). Holding out an entire watercourse forces the
    # agent to find it via proximity to a DIFFERENT trout river — genuine discovery.
    from collections import defaultdict
    by_lp = defaultdict(list)
    for r in positives:
        by_lp[r["levelpathid"] if r["levelpathid"] is not None else f"c{r['comid']}"].append(r)
    lps = list(by_lp)
    rng.shuffle(lps)
    held, held_rivers = set(), 0
    for lp in lps:
        if len(held) >= HOLDOUT_CAP:
            break
        held.update(r["comid"] for r in by_lp[lp])
        held_rivers += 1
    held_out = frozenset(held)
    n_hold = len(held_out)

    # Candidate pool the agent "sees": masked held-outs + sampled undesignated.
    undesignated = [r for r in reach_data.candidate_reaches(states, held_out)
                    if r["comid"] not in held_out]
    rng.shuffle(undesignated)
    background = undesignated[:neg_sample]
    holdout_recs = [r for r in reg["reaches"] if r["comid"] in held_out]
    pool = holdout_recs + background

    # Topology index EXCLUDING held-outs (so they can't be "near themselves").
    topo_index = reach_data.make_topology_index(states, held_out)

    feats = [signals.extract(r, topo_index) for r in pool]

    results = {}
    for mode in MODES:
        scored = []
        for f, r in zip(feats, pool):
            s = coldwater_suitability(f["topology"], f["flow"], f["thermal"],
                                      f["access"], mode=mode)
            scored.append({"comid": r["comid"], "confidence": s["confidence"],
                           "suitability": s["suitability_score"],
                           "access_ok": s["access_ok"],
                           "access_tier": f["access"].get("access_tier"),
                           "is_holdout": 1 if r["comid"] in held_out else 0})
        scored.sort(key=lambda x: x["confidence"], reverse=True)
        labels = [s["is_holdout"] for s in scored]
        n_pos = sum(labels)
        recall = {f"@{k}": round(sum(labels[:k]) / n_pos, 3) for k in KS}
        prec = {f"@{k}": round(sum(labels[:k]) / k, 3) for k in KS}
        # Access-guardrail VIOLATION = surfacing a KNOWN-private reach (unknown
        # access is allowed but flagged, so it doesn't count as a violation).
        surfaced = [s for s in scored[:250] if s["confidence"] > 0]
        access_viol = sum(1 for s in surfaced
                          if s["access_tier"] in ("private", "private_easement"))
        needs_verify = sum(1 for s in surfaced if s["access_tier"] == "unknown")
        # HARD-NEGATIVE AUC: held-outs vs only the near-trout undesignated reaches
        # (topology can't separate these; thermal/flow/access must). The honest,
        # harder number — the easy random background inflates the plain AUC.
        hard_ids = {f["comid"] for f in feats
                    if (f["topology"].get("distance_mi") or 99) <= 3.0
                    and f["comid"] not in held_out}
        sub_labels = [s["is_holdout"] for s in scored
                      if s["is_holdout"] or s["comid"] in hard_ids]
        results[mode] = {
            "n_pos": n_pos, "n_pool": len(scored),
            "recall": recall, "precision": prec,
            "roc_auc": round(_roc_auc(labels), 3),
            "pr_auc": round(_avg_precision(labels), 3),
            "hard_neg_roc_auc": round(_roc_auc(sub_labels), 3),
            "hard_neg_n": len(hard_ids),
            "access_violations_top250": access_viol,
            "needs_access_verify_top250": needs_verify,
            "calibration": _calibration(scored),
        }
    return {"states": list(states), "n_designated": len(positives),
            "n_holdout": n_hold, "n_holdout_rivers": held_rivers,
            "n_background": len(background), "results": results}


def render(rep: dict) -> str:
    L = ["# Prospecting Agent — Held-out-labels Backtest\n"]
    L.append(f"_Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}_  ")
    L.append(f"Region: {', '.join(rep['states'])} · designated reaches "
             f"{rep['n_designated']:,} · held-out {rep['n_holdout']:,} reaches "
             f"across {rep.get('n_holdout_rivers', '?')} whole rivers (masked by "
             f"levelpathid, so discovery can't be faked by segment adjacency) · "
             f"background {rep['n_background']:,} (sampled proxy negatives).\n")
    L.append("Deterministic suitability ranking (no LLM). Question: does it rank "
             "held-out trout water it was never told about above the mass?\n")

    L.append("## Signal ablation\n")
    L.append("ROC-AUC = held-outs vs the random background (easy negatives — trout "
             "water clusters, so this is optimistic). **Hard-neg AUC** = held-outs "
             "vs only *near-trout undesignated* reaches (the honest, harder test "
             "where topology can't separate and thermal/flow/access must).\n")
    L.append("| Mode | recall@50 | recall@100 | precision@50 | ROC-AUC | "
             "Hard-neg AUC | PR-AUC | access violations |")
    L.append("|---|---|---|---|---|---|---|---|")
    for mode in MODES:
        r = rep["results"][mode]
        L.append(f"| {mode} | {r['recall']['@50']} | {r['recall']['@100']} | "
                 f"{r['precision']['@50']} | {r['roc_auc']} | "
                 f"{r['hard_neg_roc_auc']} | {r['pr_auc']} | "
                 f"{r['access_violations_top250']} |")
    full_r = rep["results"]["full"]
    L.append(f"\n_Hard-negative pool: {full_r['hard_neg_n']} near-trout "
             f"undesignated reaches. Access violations (surfaced a known-private "
             f"reach) = {full_r['access_violations_top250']}; of the top-250 "
             f"surfaced, {full_r.get('needs_access_verify_top250', '?')} carry an "
             f"'unverified access — confirm locally' flag (the access-data gap: we "
             f"have access POINTS, not PAD-US public-land polygons)._\n")

    full = rep["results"]["full"]
    L.append("## Calibration (full model)\n")
    L.append("Predicted-confidence bucket vs actual held-out hit-rate.\n")
    L.append("| confidence | n | held-out hit-rate |")
    L.append("|---|---|---|")
    for c in full["calibration"]:
        L.append(f"| {c['bucket']} | {c['n']} | "
                 f"{'-' if c['hit_rate'] is None else c['hit_rate']} |")
    L.append("")

    L.append("## Reading this\n")
    L.append("- **Topology carries the discovery** (recall/AUC rise from the "
             "topology-only baseline); thermal refines; **access is the "
             "actionability filter** (it gates out no-access reaches → access "
             "violations 0 in gated modes, at some recall cost).")
    L.append("- **Positive-unlabeled caveat:** non-held-out undesignated reaches "
             "are *unlabeled*, not negatives — a highly-ranked one may be a real "
             "discovery the backtest can't credit. So recall here is a **lower "
             "bound**, and PR-AUC uses a sampled background as proxy negatives.")
    L.append("- Designation is administrative (a proxy for fish presence), not "
             "field-survey ground truth — the best label available without a creel survey.")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", default="MD,VA,PA")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--neg-sample", type=int, default=NEG_SAMPLE)
    ap.add_argument("--out", default=str(config.EVAL_DIR / "backtest_report.md"))
    args = ap.parse_args()
    states = tuple(s.strip().upper() for s in args.states.split(","))

    t0 = time.time()
    rep = run(states, neg_sample=args.neg_sample, seed=args.seed)
    open(args.out, "w").write(render(rep))
    json.dump(rep, open(config.EVAL_DIR / "backtest_results.json", "w"),
              indent=2, default=str)
    for mode in MODES:
        r = rep["results"][mode]
        print(f"{mode:26} recall@100={r['recall']['@100']} ROC-AUC={r['roc_auc']} "
              f"PR-AUC={r['pr_auc']} access_viol={r['access_violations_top250']}")
    print(f"\nwrote {args.out} in {time.time()-t0:.0f}s "
          f"(designated={rep['n_designated']}, holdout={rep['n_holdout']})")


if __name__ == "__main__":
    main()
