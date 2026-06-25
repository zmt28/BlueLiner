"""Coverage-survey tests -- no network. A fake registry on disk and an injected
fake discovery/probe exercise gap computation, expected-empty marking, matrix
shaping, candidate rendering, unreachable handling, and crash-safety."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import coverage_survey as cs  # noqa: E402


# ---------------------------------------------------------------------------
# Fake registry fixture: writes the three sources.json under a tmp data root.
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_registry(tmp_path):
    def _src(states):
        return {"sources": [{"state": s, "url": "x"} for s in states]}

    (tmp_path / "trout").mkdir()
    (tmp_path / "stocking").mkdir()
    (tmp_path / "access_points").mkdir()
    # CA: has trout+stocking but no access. VA: all three.
    # EBTJV pseudo-state token must be ignored (not a real state).
    (tmp_path / "trout" / "sources.json").write_text(json.dumps(
        {"sources": [{"state": "CA"}, {"state": "VA"}, {"state": "EBTJV"}]}))
    (tmp_path / "stocking" / "sources.json").write_text(json.dumps(
        _src(["CA", "VA"])))
    (tmp_path / "access_points" / "sources.json").write_text(json.dumps(
        _src(["VA"])))
    return str(tmp_path)


def test_covered_states_ignores_pseudo_tokens(fake_registry):
    assert cs.covered_states("trout", fake_registry) == {"CA", "VA"}
    assert cs.covered_states("access", fake_registry) == {"VA"}


def test_gap_computation(fake_registry):
    m = cs.compute_gaps(fake_registry)
    # VA fully covered
    assert m["VA"] == {"trout": "Y", "stocking": "Y", "access": "Y"}
    # CA: trout/stocking covered, access is a real gap (access applies to all)
    assert m["CA"]["trout"] == "Y"
    assert m["CA"]["access"] == "gap"


def test_expected_empty_marking(fake_registry):
    m = cs.compute_gaps(fake_registry)
    # FL is not a trout state -> trout & stocking are "expected none",
    # but access is still a fillable gap.
    assert "FL" not in cs.TROUT_STATES
    assert m["FL"]["trout"] == "expected none"
    assert m["FL"]["stocking"] == "expected none"
    assert m["FL"]["access"] == "gap"
    # A trout state with no source is a real gap, never "expected none".
    assert "AZ" in cs.TROUT_STATES
    assert m["AZ"]["trout"] == "gap"


def test_expected_empty_not_in_fillable(fake_registry):
    m = cs.compute_gaps(fake_registry)
    gaps = cs.fillable_gaps(m)
    assert ("FL", "trout") not in gaps
    assert ("FL", "access") in gaps  # access everywhere
    assert ("AZ", "trout") in gaps


def test_matrix_shaping(fake_registry):
    m = cs.compute_gaps(fake_registry)
    lines = cs.render_matrix(m)
    assert lines[0].startswith("| State | Trout | Stocking | Access |")
    # one header + separator + 48 state rows
    assert len(lines) == 2 + len(cs.LOWER_48)
    va_row = next(li for li in lines if li.startswith("| VA |"))
    assert va_row.count("Y") == 3


def test_candidate_section_rendering():
    results = {
        ("AZ", "trout"): {"status": "candidates", "used_host": True,
                          "candidates": [{"url": "http://x/0", "name": "Trout",
                                          "geometryType": "esriGeometryPolyline",
                                          "count": 42}]},
    }
    out = "\n".join(cs.render_candidates_section(results))
    assert "### AZ / trout" in out
    assert "http://x/0" in out
    assert "42" in out
    assert "Trout" in out


def test_candidate_section_null_count_and_native_note():
    results = {
        ("WA", "trout"): {"status": "candidates", "used_host": True,
                          "candidates": [{"url": "http://x/1", "name": "n",
                                          "geometryType": "", "count": None}]},
    }
    out = "\n".join(cs.render_candidates_section(results))
    # WA is a native-overlay state -> the trout header carries the note
    assert "native" in out.lower()
    assert "| n |  | ? | http://x/1 |" in out


def test_unreachable_and_none_rendering():
    results = {
        ("KY", "access"): {"status": "unreachable", "used_host": True,
                           "candidates": []},
        ("OR", "stocking"): {"status": "none", "used_host": False,
                             "candidates": []},
    }
    out = "\n".join(cs.render_candidates_section(results))
    assert "host unreachable" in out
    assert "no candidate found" in out


# ---------------------------------------------------------------------------
# run_survey with injected discover -- no network.
# ---------------------------------------------------------------------------
def test_run_survey_uses_injected_discover(fake_registry):
    seen = []

    def fake_discover(client, st, dt, host_catalog):
        seen.append((st, dt))
        return {"status": "candidates", "used_host": True,
                "candidates": [{"url": "u", "name": st, "geometryType": "pt",
                                "count": 1}]}

    matrix, results = cs.run_survey(discover=fake_discover,
                                    registry_dir=fake_registry)
    gaps = set(cs.fillable_gaps(matrix))
    assert set(results.keys()) == gaps
    assert set(seen) == gaps
    assert all(r["status"] == "candidates" for r in results.values())


def test_run_survey_survives_throwing_probe(fake_registry):
    def boom(client, st, dt, host_catalog):
        raise RuntimeError("probe exploded")

    matrix, results = cs.run_survey(discover=boom, registry_dir=fake_registry)
    # every gap recorded as unreachable, nothing raised
    assert results
    assert all(r["status"] == "unreachable" for r in results.values())


def test_run_survey_respects_runtime_budget(fake_registry):
    calls = []

    def fake_discover(client, st, dt, host_catalog):
        calls.append((st, dt))
        return {"status": "none", "used_host": False, "candidates": []}

    # zero budget -> no discover calls, all gaps marked unreachable
    matrix, results = cs.run_survey(discover=fake_discover,
                                    registry_dir=fake_registry,
                                    runtime_budget=-1)
    assert calls == []
    assert all(r["status"] == "unreachable" for r in results.values())


def test_main_exits_zero(monkeypatch, tmp_path):
    # Point output at tmp and make discovery a no-op that returns nothing useful.
    monkeypatch.setattr(cs, "OUT_PATH", str(tmp_path / "COVERAGE.md"))

    def fake_discover(client, st, dt, host_catalog):
        return {"status": "none", "used_host": False, "candidates": []}

    monkeypatch.setattr(cs, "discover_gap", fake_discover)
    # also short-circuit run_survey's default to avoid any real client use:
    orig = cs.run_survey
    monkeypatch.setattr(cs, "run_survey",
                        lambda **kw: orig(discover=fake_discover, **kw))
    rc = cs.main()
    assert rc == 0
    assert os.path.exists(str(tmp_path / "COVERAGE.md"))
    body = (tmp_path / "COVERAGE.md").read_text()
    assert "# Coverage Survey" in body
    assert "## Coverage matrix" in body


def test_real_registry_gaps_match_known_worklist():
    """Sanity: the live registries reproduce the worklist's stated gap counts
    (27 trout / 30 stocking / 20 access with no source). Stocking dropped
    31->30 when MA was promoted (MassWildlife 2025 feed)."""
    m = cs.compute_gaps()
    for dt, expect in (("trout", 27), ("stocking", 30), ("access", 20)):
        no_source = sum(1 for st in cs.LOWER_48
                        if m[st][dt] in ("gap", "expected none"))
        assert no_source == expect, (dt, no_source)


def test_build_host_catalog_consolidates():
    cat = cs.build_host_catalog()
    # MT appears in both SERVER_ROOTS and (indirectly) seeds -> deduped list
    assert "MT" in cat
    assert all(isinstance(v, list) and len(v) == len(set(v))
               for v in cat.values())
