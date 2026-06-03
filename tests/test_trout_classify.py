"""Offline tests for the trout-source discovery classifier (Phase-0 spike).

Locks in the two properties the go/no-go gate cares about, graded against the
10 already-shipped states as ground truth: zero mis-buckets (a stocked stream
must never auto-paint green) and high auto-accuracy. Pure -- no network.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from discovery import classify, eval as gold_eval, geo  # noqa: E402


def test_strong_wild_signals_auto_bucket():
    for label in ("Virginia Wild Trout Streams", "Class A Wild Trout Streams",
                  "Wilderness Trout Streams", "Wild-Premier",
                  "Catch and Release/Artificial Flies Only", "Wild-Quality"):
        res = classify.classify(label)
        assert res.status == "auto" and res.bucket == classify.WILD, label


def test_strong_stocked_signals_auto_bucket():
    for label in ("WV Stocked Trout Streams", "Hatchery Supported Trout Waters",
                  "Delayed Harvest Trout Waters", "Stocked-Extended",
                  "Heavily Stocked"):
        res = classify.classify(label)
        assert res.status == "auto" and res.bucket == classify.STOCKED, label


def test_state_specific_terms_are_flagged_not_guessed():
    # The dangerous cases: must defer to a human, never auto-bucket.
    for label in ("Special Regulation Trout Waters", "Maryland Designated Use Trout",
                  "Class II", "Type 3 Trout Stream", "Other"):
        res = classify.classify(label)
        assert res.status == "flag" and res.bucket is None, label


def test_class_i_does_not_match_class_ii():
    # Padding guards the "class i" token from matching "class ii"/"class iii".
    assert classify.classify("Class III Trout Water").status == "flag"


def test_gold_eval_gates_pass():
    m = gold_eval.run()
    assert m.misbucket == 0, f"{m.misbucket} mis-buckets (gate: 0)"
    assert m.auto_accuracy >= gold_eval.GATE_AUTO_ACCURACY
    assert gold_eval.gates_pass(m)
    # Sanity: the gold set is the full 10-state vocabulary we captured.
    assert m.total >= 20 and m.auto >= 15


# --- geographic relevance gate (the Phase-0 CO/TN miss fix) ---

# Great Smoky Mountains extent (the layer that wrongly matched a CO search).
GRSM_EXTENT = {"xmin": -84.0, "ymin": 35.4, "xmax": -83.0, "ymax": 35.8,
               "spatialReference": {"wkid": 4326}}


def test_grsm_intersects_tn_and_nc_not_co():
    box = geo.to_wgs84(GRSM_EXTENT)
    assert geo.extent_intersects(box, "TN")
    assert geo.extent_intersects(box, "NC")
    assert not geo.extent_intersects(box, "CO")   # the bug this gate fixes


def test_unknown_extent_is_kept_not_dropped():
    # Can't verify geography -> don't drop the candidate.
    assert geo.extent_intersects(None, "CO")


def test_web_mercator_extent_reprojects():
    # A Colorado-ish box in EPSG:3857 should land inside CO after conversion.
    merc = {"xmin": -11700000, "ymin": 4500000, "xmax": -11500000,
            "ymax": 4700000, "spatialReference": {"latestWkid": 3857}}
    box = geo.to_wgs84(merc)
    assert box is not None and geo.extent_intersects(box, "CO")

