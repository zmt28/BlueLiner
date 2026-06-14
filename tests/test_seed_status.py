"""Tests for scripts/seed_status.py -- the scheduled cheap-skip decider.

The drift guard is the important one: seed_status.seed_slug re-implements
build_clickable_streams.seed_slug (to stay stdlib-only), so it must produce the
identical slug for every real registry source or the cron would look for the
wrong seed files.
"""
import json
import os
import sys
import importlib

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import seed_status as st  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _real_sources():
    with open(os.path.join(ROOT, "data", "trout", "sources.json")) as f:
        return json.load(f)["sources"]


def test_seed_slug_matches_build_script():
    """Drift guard: the re-implemented slug equals the build's for every
    real source (and the duplicated CT/EBTJV labels)."""
    pytest.importorskip("geopandas")
    b = importlib.import_module("build_clickable_streams")
    for s in _real_sources():
        assert st.seed_slug(s) == b.seed_slug(s), s.get("label")


def test_need_build_when_a_source_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "SEEDS_DIR", str(tmp_path))
    sources = [{"state": "VA", "label": "A"}, {"state": "PA", "label": "B"}]
    # bank a fresh seed only for A
    _write(tmp_path, st.seed_slug(sources[0]), age_days=1)
    r = st.evaluate(sources)
    assert r["missing"] == ["B"] and r["fresh"] == 1


def test_stale_seed_triggers_build(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "SEEDS_DIR", str(tmp_path))
    monkeypatch.setattr(st, "MAX_AGE_DAYS", 30)
    sources = [{"state": "VA", "label": "A"}]
    _write(tmp_path, st.seed_slug(sources[0]), age_days=99)
    r = st.evaluate(sources)
    assert r["stale"] and r["fresh"] == 0


def test_all_fresh_means_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "SEEDS_DIR", str(tmp_path))
    sources = [{"state": "VA", "label": "A"}, {"state": "PA", "label": "B"}]
    for s in sources:
        _write(tmp_path, st.seed_slug(s), age_days=1)
    r = st.evaluate(sources)
    assert not r["missing"] and not r["stale"] and r["fresh"] == 2


def test_legacy_seed_with_no_captured_at_is_not_stale(tmp_path, monkeypatch):
    """The legacy MD COMID list has no captured_at -- it's static reference
    data and must never count as stale (which would rebuild forever)."""
    monkeypatch.setattr(st, "SEEDS_DIR", str(tmp_path))
    legacy = tmp_path / "MD_legacy.json"
    legacy.write_text(json.dumps({"comids": [1, 2, 3]}))
    src = {"state": "MD", "label": "Legacy",
           "seed": os.path.relpath(str(legacy), ROOT)}
    monkeypatch.setattr(st, "ROOT", ROOT)
    r = st.evaluate([src])
    assert r["fresh"] == 1 and not r["stale"]


def _write(d, slug, age_days):
    from datetime import datetime, timezone, timedelta
    cap = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    (d / f"{slug}.json").write_text(json.dumps(
        {"captured_at": cap, "groups": []}))
