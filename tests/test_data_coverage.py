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
