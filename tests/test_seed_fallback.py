"""Unit tests for the last-known-good seed mechanism in
build_clickable_streams (build resilience for the ~30 third-party trout
sources).

Covers: seed write/read round-trip, fallback-to-seed when a source's fetch
fails, the unreachable gate only firing with no-live-AND-no-seed, per-source
retry backoff, and the preflight classification. All synthetic, no network --
the fetch/probe/sleep hooks are injected.

The module under test imports geopandas at import time (a build dev dep), so
like test_spatial_join_trout.py these skip when it's absent.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

pytest.importorskip("geopandas")

import build_clickable_streams as b  # noqa: E402


class FakeGdf:
    """Stands in for a GeoDataFrame in collect_trout_taggers (which only
    needs len() and .total_bounds there -- no joins happen at collect time)."""
    total_bounds = (0.0, 0.0, 1.0, 1.0)

    def __init__(self, n=1):
        self._n = n

    def __len__(self):
        return self._n


SRC = {"state": "CO", "label": "CO Aquatic Management Waters",
       "mode": "single", "class": "wild_reproduction",
       "url": "https://example.test/arcgis/rest/services/X/MapServer/0/query?where=1%3D1"}

KEY = ("wild_reproduction", "class2", True)


# ───────────────────────── slug / paths ─────────────────────────

def test_seed_slug_is_stable_and_filesystem_safe():
    assert b.seed_slug(SRC) == "co-co-aquatic-management-waters"
    assert b.seed_slug({"state": "CT", "label": "CT (WTMA)"}) == "ct-ct-wtma"
    # label optional -> state-only slug; never empty/odd characters
    assert b.seed_slug({"state": "MD"}) == "md"


# ───────────────────────── write/read round-trip ─────────────────────────

def test_seed_write_read_round_trip(tmp_path):
    sd = str(tmp_path)
    groups = {KEY: {30, 10, 20}, ("stocked", "class3", False): {99}}
    path = b.write_source_seed(SRC, groups, seed_dir=sd)
    assert path == b.seed_path(SRC, sd) and os.path.exists(path)

    raw = json.load(open(path))
    assert raw["state"] == "CO" and raw["comid_count"] == 4
    assert raw["captured_at"]  # staleness metadata stored
    # compact sorted int lists
    assert [g["comids"] for g in raw["groups"]] == [[99], [10, 20, 30]]

    loaded = dict(b.load_source_seed(SRC, seed_dir=sd))
    assert loaded == {KEY: {10, 20, 30}, ("stocked", "class3", False): {99}}


def test_seed_write_skips_empty_and_unchanged(tmp_path):
    sd = str(tmp_path)
    # nothing captured -> no file (don't clobber anything with emptiness)
    assert b.write_source_seed(SRC, {KEY: set()}, seed_dir=sd) is None
    assert b.write_source_seed(SRC, {}, seed_dir=sd) is None

    path = b.write_source_seed(SRC, {KEY: {1, 2}}, seed_dir=sd)
    before = open(path).read()
    # identical capture -> skipped write, captured_at preserved (this is what
    # makes the workflow's "only commit when seeds changed" guard meaningful)
    assert b.write_source_seed(SRC, {KEY: {2, 1}}, seed_dir=sd) is None
    assert open(path).read() == before
    # a real change rewrites
    assert b.write_source_seed(SRC, {KEY: {1, 2, 3}}, seed_dir=sd) == path
    assert json.load(open(path))["comid_count"] == 3


def test_legacy_seed_key_still_works(tmp_path):
    # The explicit `seed:` registry key (MD) is just a pre-seeded entry: the
    # old single-class shape maps to one group with the class-fallback tier
    # and the source's registry native flag.
    legacy = tmp_path / "MD_designated_comids.json"
    legacy.write_text(json.dumps(
        {"state": "MD", "trout_class": "designated", "comids": [5, 6]}))
    src = {"state": "MD", "label": "MD", "mode": "single",
           "class": "designated", "seed": str(legacy)}
    sd = str(tmp_path / "seeds")  # empty/missing auto-seed dir is fine
    assert b.load_source_seed(src, seed_dir=sd) == \
        [(("designated", "class3", False), {5, 6})]
    # ... but a fresher auto capture takes precedence over the legacy file
    b.write_source_seed(src, {("designated", "class3", False): {7}}, seed_dir=sd)
    assert b.load_source_seed(src, seed_dir=sd) == \
        [(("designated", "class3", False), {7})]


def test_missing_seed_dir_is_graceful(tmp_path):
    # bootstrap: most sources have no seed until the first post-merge build
    sd = str(tmp_path / "does-not-exist")
    assert b.find_seed_file(SRC, seed_dir=sd) is None
    assert b.load_source_seed(SRC, seed_dir=sd) == []


# ───────────────────── fetch fallback + gate ─────────────────────

def _down(source):
    raise RuntimeError("server flapping")


def test_fallback_to_seed_on_fetch_failure(tmp_path):
    sd = str(tmp_path)
    b.write_source_seed(SRC, {KEY: {11, 12}}, seed_dir=sd)
    taggers, status = b.collect_trout_taggers([SRC], fetch=_down, seed_dir=sd)
    assert status == {"CO Aquatic Management Waters": "bundled-seed"}
    assert len(taggers) == 1 and taggers[0]["live"] is False
    assert taggers[0]["groups"] == [(KEY, {11, 12})]


def test_gate_fires_only_with_no_live_and_no_seed(tmp_path):
    sd = str(tmp_path)
    seeded = dict(SRC, state="MD", label="MD seeded")
    b.write_source_seed(seeded, {KEY: {1}}, seed_dir=sd)
    bare = dict(SRC, state="VA", label="VA bare")

    taggers, status = b.collect_trout_taggers(
        [seeded, bare], fetch=_down, seed_dir=sd)
    # seed-backed source is NOT "unreachable" (doesn't trip --require-trout);
    # the no-live-no-seed one is.
    assert status == {"MD seeded": "bundled-seed", "VA bare": "unreachable"}
    assert [t["label"] for t in taggers] == ["MD seeded"]


def test_live_fetch_keeps_registry_order_and_ok_status(tmp_path):
    sd = str(tmp_path)
    first = dict(SRC, state="MD", label="MD live")
    second = dict(SRC, state="CO", label="CO seeded")
    b.write_source_seed(second, {KEY: {42}}, seed_dir=sd)

    def fetch(source):
        if source["label"] == "CO seeded":
            raise RuntimeError("down")
        return [(KEY, FakeGdf(3))]

    taggers, status = b.collect_trout_taggers([first, second],
                                              fetch=fetch, seed_dir=sd)
    assert status == {"MD live": "ok", "CO seeded": "bundled-seed"}
    # tagger order == registry order == precedence (first writer wins)
    assert [(t["label"], t["live"]) for t in taggers] == \
        [("MD live", True), ("CO seeded", False)]
    key, gdf, bounds = taggers[0]["groups"][0]
    assert key == KEY and bounds == (0.0, 0.0, 1.0, 1.0)


def test_empty_live_result_is_ok_but_writes_nothing(tmp_path):
    # a reachable layer with zero features: status ok, no seed fallback,
    # no groups (and later no seed write -- covered by skips_empty above)
    taggers, status = b.collect_trout_taggers(
        [SRC], fetch=lambda s: [], seed_dir=str(tmp_path))
    assert status == {"CO Aquatic Management Waters": "ok"}
    assert taggers[0]["live"] is True and taggers[0]["groups"] == []


# ───────────────────────── per-source retries ─────────────────────────

def test_source_retries_back_off_then_succeed():
    calls, waits = [], []

    def flaky(source):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("mid-pagination drop")
        return [(KEY, FakeGdf())]

    out = b.fetch_trout_source_with_retries(
        SRC, fetch=flaky, sleep=waits.append)
    assert len(calls) == 3 and waits == [15, 45]
    assert out[0][0] == KEY


def test_source_retries_exhaust_and_raise():
    calls, waits = [], []

    def dead(source):
        calls.append(1)
        raise RuntimeError("still down")

    with pytest.raises(RuntimeError, match="still down"):
        b.fetch_trout_source_with_retries(SRC, fetch=dead, sleep=waits.append)
    # 3 attempts total, waiting 15s then 45s between them (no wait after last)
    assert len(calls) == 3 and waits == [15, 45]


# ───────────────────────── preflight ─────────────────────────

def test_preflight_classifies_reachable_seed_and_no_data(tmp_path):
    sd = str(tmp_path)
    up = dict(SRC, state="VA", label="VA up")
    down_seeded = dict(SRC, state="MD", label="MD down seeded")
    down_bare = dict(SRC, state="CO", label="CO down bare")
    b.write_source_seed(down_seeded, {KEY: {1}}, seed_dir=sd)

    rows = b.preflight_sources([up, down_seeded, down_bare], seed_dir=sd,
                               probe=lambda s: s["label"] == "VA up")
    assert rows == [("VA up", "reachable"),
                    ("MD down seeded", "will-use-seed"),
                    ("CO down bare", "NO DATA")]


def test_source_probe_urls():
    assert b.source_probe_urls(SRC) == \
        ["https://example.test/arcgis/rest/services/X/MapServer/0"]
    multi = {"state": "PA", "mode": "multi_layer",
             "base": "https://example.test/MapServer",
             "layers": [{"id": 3, "class": "class_a"},
                        {"id": 5, "class": "stocked"}]}
    assert b.source_probe_urls(multi) == \
        ["https://example.test/MapServer/3", "https://example.test/MapServer/5"]
