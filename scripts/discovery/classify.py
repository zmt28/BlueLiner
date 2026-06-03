"""Lexicon classifier: one trout-regulation label -> wild / stocked / FLAG.

Pure and offline -- no I/O -- so it's unit-tested against the 10 already-shipped
states as ground truth. The discovery factory feeds each probed layer's category
vocabulary (or whole-layer name, or flag-field name) through `classify` to draft
a wild/stocked mapping; FLAG results are surfaced for human review, never shipped
as a silent guess.

Decision order (a strong signal always beats an ambiguous one):
  1. both a wild AND a stocked token present -> FLAG (genuinely mixed)
  2. only wild  -> wild_reproduction
  3. only stock -> stocked
  4. an ambiguous token present (or nothing matched) -> FLAG
So "Wild-Quality" -> wild (the wild token wins over the ambiguous "quality"),
while "Special Regulation Trout Waters" -> FLAG (no strong token at all).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import lexicon

WILD = "wild_reproduction"
STOCKED = "stocked"


@dataclass(frozen=True)
class Result:
    bucket: str | None        # WILD | STOCKED when status == "auto", else None
    status: str               # "auto" | "flag"
    matched: tuple[str, ...]  # the tokens that fired (for the dossier rationale)
    reason: str


def _norm(label: str) -> str:
    """Lowercase, collapse every run of non-alphanumerics to one space, and pad
    with spaces so word-boundary-sensitive tokens (e.g. "class a") match
    cleanly. Padding also stops the "class i" token from matching "class ii"."""
    t = re.sub(r"[^a-z0-9]+", " ", (label or "").lower())
    return " " + re.sub(r"\s+", " ", t).strip() + " "


def _hits(text: str, tokens) -> tuple[str, ...]:
    return tuple(tok for tok in tokens if f" {tok} " in text or tok in text)


def classify(label: str) -> Result:
    """Classify a single regulation/category/layer label."""
    text = _norm(label)
    wild = _hits(text, lexicon.WILD_TOKENS)
    stocked = _hits(text, lexicon.STOCKED_TOKENS)
    ambiguous = _hits(text, lexicon.AMBIGUOUS_TOKENS)

    if wild and stocked:
        return Result(None, "flag", wild + stocked,
                      "both wild and stocked signals present")
    if wild:
        return Result(WILD, "auto", wild, f"wild signal: {', '.join(wild)}")
    if stocked:
        return Result(STOCKED, "auto", stocked,
                      f"stocked signal: {', '.join(stocked)}")
    if ambiguous:
        return Result(None, "flag", ambiguous,
                      f"state-specific term: {', '.join(ambiguous)}")
    return Result(None, "flag", (), "no recognized trout-regulation vocabulary")


def classify_values(values) -> dict[str, Result]:
    """Classify a category field's distinct values -> {value: Result}."""
    return {v: classify(v) for v in values}
