"""Offline eval: grade `classify` against the 10 already-shipped states.

This is the spike's headline measurement -- "is the wild/stocked classification
trustworthy enough to automate?" -- and it needs no network: the gold labels in
gold.json are the real vocabularies we captured from each agency, paired with
the buckets the project actually assigned.

Three numbers decide it:
  * auto-accuracy   -- of the labels the classifier auto-buckets, how many match
                       the human call. A wrong auto-bucket is the dangerous case.
  * mis-bucket count-- auto-bucketed but wrong. The go/no-go gate is 0.
  * coverage        -- share auto-bucketed (vs deferred to a human). Lower is
                       fine; it just means more human review, not wrong tiles.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from . import classify

GOLD_PATH = os.path.join(os.path.dirname(__file__), "gold.json")


@dataclass
class Metrics:
    total: int = 0
    auto: int = 0
    auto_correct: int = 0
    misbucket: int = 0
    flagged: int = 0
    rows: list = field(default_factory=list)  # (state, value, gold, got, status, ok)

    @property
    def auto_accuracy(self) -> float:
        return self.auto_correct / self.auto if self.auto else 1.0

    @property
    def coverage(self) -> float:
        return self.auto / self.total if self.total else 0.0


def run(gold_path: str = GOLD_PATH) -> Metrics:
    with open(gold_path, encoding="utf-8") as f:
        gold = json.load(f)["states"]
    m = Metrics()
    for state, spec in gold.items():
        for lab in spec["labels"]:
            value, want = lab["value"], lab["bucket"]
            res = classify.classify(value)
            m.total += 1
            if res.status == "auto":
                m.auto += 1
                ok = res.bucket == want
                m.auto_correct += ok
                m.misbucket += (not ok)
                m.rows.append((state, value, want, res.bucket, "auto", ok))
            else:
                m.flagged += 1
                m.rows.append((state, value, want, "FLAG", "flag", None))
    return m


# Go/no-go gates from docs/trout-discovery-spike.md
GATE_AUTO_ACCURACY = 0.90
GATE_MISBUCKET = 0


def gates_pass(m: Metrics) -> bool:
    return m.auto_accuracy >= GATE_AUTO_ACCURACY and m.misbucket <= GATE_MISBUCKET


def format_report(m: Metrics) -> str:
    out = ["Classifier eval vs 10 shipped states (offline gold set)", ""]
    out.append(f"{'STATE':<5} {'GOT':<17} {'GOLD':<17} {'OK':<4} VALUE")
    for state, value, want, got, status, ok in m.rows:
        mark = "ok" if ok else ("flag" if status == "flag" else "MISS")
        out.append(f"{state:<5} {got:<17} {want:<17} {mark:<4} {value[:54]}")
    out += [
        "",
        f"labels          : {m.total}",
        f"auto-bucketed   : {m.auto}  (coverage {m.coverage:.0%})",
        f"auto-accuracy   : {m.auto_accuracy:.0%}  ({m.auto_correct}/{m.auto})",
        f"mis-buckets     : {m.misbucket}   <-- gate: 0",
        f"flagged (human) : {m.flagged}",
        "",
        f"GATES: {'PASS' if gates_pass(m) else 'FAIL'} "
        f"(auto-accuracy >= {GATE_AUTO_ACCURACY:.0%}, mis-buckets <= {GATE_MISBUCKET})",
    ]
    return "\n".join(out)


def main() -> int:
    m = run()
    print(format_report(m))
    return 0 if gates_pass(m) else 1
