"""Guardrails on the bundled data: every focused mid-Atlantic state has
stocking data, and the hatch overrides parse cleanly. Catches the
"someone edited a file and broke a state's coverage" regression."""

import json
import os

import hatches
import stocking

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOCUSED_MID_ATLANTIC = ("MD", "VA", "WV", "PA")


def test_every_focused_state_has_stocking_baseline():
    for state in FOCUSED_MID_ATLANTIC:
        rows = stocking.stocked_points(state)
        assert rows, f"{state} has no stocking baseline"
        for r in rows[:3]:                       # spot-check structure
            assert {"water", "lat", "lon", "species", "category",
                    "season_months", "agency_url"} <= set(r)


def test_hatch_overrides_include_well_known_waters():
    """Curated overrides exist for the famous mid-Atlantic waters whose
    hatches diverge from their containing zone."""
    for name in ("gunpowder falls", "penns creek", "letort spring run",
                 "mossy creek"):
        assert name in hatches.RIVER_HATCH_OVERRIDES, f"missing override: {name}"


def test_zone_for_river_returns_override_when_named():
    """zone_for_river prefers the override; falls back to geo zone."""
    z_override = hatches.zone_for_river("Gunpowder Falls", 39.6, -76.7)
    assert "(curated)" in z_override["name"]
    assert any(e["common_name"] == "Sulphur" for e in z_override["chart"])

    z_geo = hatches.zone_for_river("Unknown Creek", 39.6, -76.7)
    assert "(curated)" not in z_geo["name"]      # fell back to geo zone


def test_validate_script_runs_clean():
    """The lint script reports no errors against the bundled data."""
    import subprocess, sys
    script = os.path.join(ROOT, "scripts", "validate_data.py")
    result = subprocess.run(
        [sys.executable, script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, (
        f"validate_data.py failed:\nSTDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}")


def test_overrides_json_loads_without_internal_keys_leaking():
    """The `_comment` and similar meta keys must not become river entries."""
    for key in hatches.RIVER_HATCH_OVERRIDES:
        assert not key.startswith("_"), f"meta key leaked: {key}"


# -- clickable-streams geometry bundle (the "bluelining" network base) --

def test_clickable_streams_bundle_structure_and_coverage():
    """The bundled clickable-streams layer parses, carries the expected
    per-flowline attributes, and gives famous mid-Atlantic waters
    whole-river coverage (one LevelPathID).
    Regenerate with scripts/build_clickable_streams.py."""
    import gzip
    path = os.path.join(ROOT, "data", "nhdplus",
                        "clickable_streams.geojson.gz")
    assert os.path.exists(path), "clickable_streams.geojson.gz missing"
    with gzip.open(path, "rt", encoding="utf-8") as f:
        fc = json.load(f)
    feats = fc.get("features", [])
    assert len(feats) > 90_000, f"unexpectedly few flowlines: {len(feats)}"

    required_props = {"comid", "levelpathid", "streamorder",
                      "lengthkm", "trout_class"}
    for feat in feats[:50]:
        p = feat["properties"]
        assert required_props <= set(p), (
            f"missing props: {required_props - set(p)}")
        assert feat["geometry"]["type"] in ("LineString", "MultiLineString")

    by_name: dict[str, set] = {}
    for feat in feats:
        nm = feat["properties"].get("gnis_name")
        if nm:
            by_name.setdefault(nm, set()).add(feat["properties"]["levelpathid"])
    for name in ("Monocacy River", "Gunpowder Falls", "Penns Creek"):
        assert name in by_name, f"{name} missing from clickable bundle"
        assert len(by_name[name]) == 1, (
            f"{name} spans {len(by_name[name])} LevelPathIDs, expected 1")


def test_clickable_streams_trout_class():
    """The bundle carries trout_class tags and includes well-known PA
    wild-trout streams."""
    import gzip
    path = os.path.join(ROOT, "data", "nhdplus",
                        "clickable_streams.geojson.gz")
    with gzip.open(path, "rt", encoding="utf-8") as f:
        fc = json.load(f)
    feats = fc["features"]

    trout_feats = [f for f in feats if f["properties"]["trout_class"]]
    assert len(trout_feats) > 20_000, (
        f"too few trout-tagged flowlines: {len(trout_feats)}")

    valid_classes = {"wild_reproduction", "class_a", "wilderness",
                     "stocked", "designated"}
    classes_seen = set()
    for f in trout_feats:
        tc = f["properties"]["trout_class"]
        assert tc in valid_classes, f"unexpected trout_class: {tc}"
        classes_seen.add(tc)
    assert len(classes_seen) >= 3, (
        f"expected >=3 trout classes, got {classes_seen}")

    by_name: dict[str, set] = {}
    for f in feats:
        nm = f["properties"].get("gnis_name")
        tc = f["properties"].get("trout_class")
        if nm and tc:
            by_name.setdefault(nm, set()).add(tc)
    for name in ("Penns Creek", "Kettle Creek", "Spruce Creek",
                 "Pine Creek", "Slate Run"):
        assert name in by_name, (
            f"PA wild-trout stream {name} missing trout tag")
