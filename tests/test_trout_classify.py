"""Offline tests for the trout-source discovery classifier (Phase-0 spike).

Locks in the two properties the go/no-go gate cares about, graded against the
10 already-shipped states as ground truth: zero mis-buckets (a stocked stream
must never auto-paint green) and high auto-accuracy. Pure -- no network.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from discovery import classify, eval as gold_eval, geo, catalogs, probe, report  # noqa: E402


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


# --- candidate ranking (the MD/VA recall fix) ---

def test_trout_named_candidates_rank_first():
    cands = [
        {"url": ".../FisheriesManagementAreas/MapServer", "title": "Fisheries"},
        {"url": ".../WildTroutStreams/MapServer", "title": "Wild Trout"},
        {"url": ".../CountyBoundaries/MapServer", "title": "Counties"},
    ]
    cands.sort(key=catalogs._relevance)
    assert "Trout" in cands[0]["url"]          # trout-named leads
    assert "Fisheries" in cands[1]["url"]       # then fish-named
    assert "County" in cands[2]["url"]          # other last


# --- category-field selection (the WI free-text miss fix) ---

def test_picks_coded_class_field_over_freetext_season():
    field_values = {
        # WI's free-text season field: one value incidentally says "catch and release".
        "SEASON_TXT": [
            "Open all year", "First Saturday in May to Oct. 15.",
            "Last Saturday in March to Nov. 15.",
            "First Saturday in May to Oct. 15; Extended catch and release Oct. 16 to Nov. 15.",
        ],
        # the real trout-class field: short coded values, all lexicon hits.
        "TROUT_CLASS": ["Class I", "Class II", "Class III"],
    }
    field, distinct = probe.pick_category_field(field_values)
    assert field == "TROUT_CLASS"
    assert distinct == ["Class I", "Class II", "Class III"]


def test_category_field_none_when_no_lexicon_signal():
    assert probe.pick_category_field(
        {"COUNTY": ["Dane", "Vilas"], "ID": ["a", "b"]}) == (None, [])


# --- word-boundary matching (wild != wildlife, class i != class ii) ---

def test_wild_does_not_match_wildlife():
    # KY's false hit: "US Fish and Wildlife" must not classify as wild.
    assert classify.classify("US Fish and Wildlife").bucket is None


def test_whole_word_signals_still_fire():
    assert classify.classify("Wilderness Trout Streams").bucket == classify.WILD
    assert classify.classify("Stocked").bucket == classify.STOCKED      # stem
    assert classify.classify("Wild Trout Waters").bucket == classify.WILD


# --- tier-A relevance gate (the ID/KY/UT false positives) ---

def _cand(**kw):
    base = dict(url="https://x/FeatureServer/0", title="", source="arcgis-search",
                geometry="esriGeometryPolyline", feature_count=100,
                anonymous_query=True, in_state=True, category_field=None,
                distinct_values=[], trout_named=False, score=1.0)
    base.update(kw)
    return base


def test_dossier_demotes_irrelevant_layer_from_A():
    # A geothermal/water-quality polygon: queryable, in-state, but no trout signal.
    d = report.build_dossier("UT", [_cand(geometry="esriGeometryPolygon",
                                           feature_count=3)], classify)
    assert d["tier"] == "C" and "wrong match" in d["caveats"][0]


def test_dossier_keeps_trout_category_layer_A():
    d = report.build_dossier("CT", [_cand(
        category_field="STOCKING_TABLE_MGT",
        distinct_values=["Wild Trout Management Area", "No Special Management"])],
        classify)
    assert d["tier"] == "A" and d["classify"]["field"] == "STOCKING_TABLE_MGT"


def test_dossier_prefers_relevant_over_higher_scored_junk():
    junk = _cand(url="https://x/Geothermal/FeatureServer/0", score=10.0)
    trout = _cand(url="https://x/WildTrout/FeatureServer/0", score=1.0,
                  trout_named=True)
    d = report.build_dossier("ZZ", [junk, trout], classify)
    assert d["tier"] == "A" and "WildTrout" in d["endpoint"]

