"""Regulation-vocabulary lexicon for trout-water classification.

Maps the words state fisheries agencies print on their trout layers to our two
frontend buckets -- wild (green) / stocked (blue). Tokens are matched as
substrings against a punctuation-normalized, lowercased label, so a stem like
"stock" catches "Stocked", "Heavily Stocked", and the abbreviated field name
"Hvy_stock" alike.

Design rule: AMBIGUOUS tokens are NEVER auto-bucketed. They're real regulation
words whose wild-vs-stocked meaning is state-specific -- NC "Special Regulation"
sections are wild trout managed under restrictive harvest, while another state's
"special regulation" might be a stocked trophy stretch; WI "Class I/II/III" and
MI "Type 1-4" are bespoke schemes. The classifier flags these for a human
instead of guessing, because a wrong auto-bucket (a stocked stream painted green)
is worse than a deferred one.

These tables are the spike's single most important tunable. The eval
(`eval.py`) measures them against the 10 states we've already shipped by hand,
so any edit here is graded against ground truth before we trust it at scale.
"""
from __future__ import annotations

import re

# A strong wild signal -- naturally reproducing / wild-managed water.
WILD_TOKENS = (
    "wild",
    "natural reproduction",
    "naturally reproducing",
    "self sustaining",
    "self-sustaining",
    "wilderness",
    "heritage",
    "native trout",
    "catch and release",
    "catch-and-release",
    "artificials only",
    "artificial only",
    "artificial lures only",
    "artificial flies",
    "class a",          # PA Class A Wild Trout (no Class I/II/III collision)
)

# A strong stocked signal -- hatchery-supported / put-and-take.
STOCKED_TOKENS = (
    "stock",            # stem: Stocked / Heavily Stocked / Hvy_stock
    "hatchery",
    "put and take",
    "put-and-take",
    "put & take",
    "delayed harvest",
    "delay",            # stem: catches the abbreviated GA "Delay_har" flag
    "catchable",
    "approved trout",
)

# Genuinely state-specific -- always FLAG, never auto-bucket.
AMBIGUOUS_TOKENS = (
    "special regulation",
    "special reg",
    "trophy",
    "premier",
    "quality",
    "designated",       # e.g. MD "Designated Use Trout" -- agency-specific
    "class i",          # WI Class I/II/III tiers are not 1:1 wild/stocked
    "class ii",
    "class iii",
    "class 1",
    "class 2",
    "class 3",
    "type 1",           # MI Type 1-4 designated-trout-stream scheme
    "type 2",
    "type 3",
    "type 4",
    "managed",
    "other",
    "seasonal",
)

# Tokens matched as a substring/prefix (so "stock" catches Stocked/Stocking and
# the abbreviated "Hvy_stock"/"Delay_har" flag names). Everything else is matched
# as a whole word, so "wild" does NOT fire on "wildlife"/"wilderness" and
# "class i" does NOT fire on "class ii" -- the precision bugs the batch exposed.
_STEM_TOKENS = frozenset({"stock", "delay"})


def _matches(text: str, token: str) -> bool:
    if token in _STEM_TOKENS:
        return token in text
    return re.search(rf"\b{re.escape(token)}\b", text) is not None


def hits(text: str, tokens) -> list[str]:
    """The tokens that fire on `text` (case-insensitive, word-boundary aware)."""
    t = (text or "").lower()
    return [tok for tok in tokens if _matches(t, tok)]
