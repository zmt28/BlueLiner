"""Unit tests for the last-known-good seed mechanism in
build_clickable_streams (build resilience for the ~30 third-party trout
sources).

Covers: seed write/read round-trip, fallback-to-seed when a source's fetch
fails, the unreachable gate only firing with no-live-AND-no-seed, per-source
retry backoff, the preflight classification, the bounded preflight re-probe
wait loop, shaky-last fetch ordering + end-of-phase final retry, and
pagination truncation detection. All synthetic, no network -- the
fetch/probe/sleep/clock hooks are injected (pagination uses
httpx.MockTransport).

The module under test imports geopandas at import time (a build dev dep), so
like test_spatial_join_trout.py these skip when it's absent.
"""
import json
import os
import re
import sys

import httpx
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


# ───────────────────── preflight bounded wait loop ─────────────────────
# The bootstrap fix: a NO-DATA source gets a re-probe window (every ~120s up
# to --preflight-wait) instead of an instant exit 3, so one flapping server
# among ~30 can't block the publish that would create its seed.

class FakeClock:
    """Injected sleep+clock pair: sleep() advances the clock, recording the
    requested waits, so the schedule is asserted without real time."""

    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s

    def now(self):
        return self.t


def test_preflight_wait_schedule_and_budget_exhaustion():
    clk = FakeClock()
    probes = []

    def probe(s):
        probes.append((clk.t, s["label"]))
        return False  # never recovers

    recovered, still = b.preflight_wait_for_no_data(
        [dict(SRC, label="NY flapping")], wait_budget=300, interval=120,
        probe=probe, sleep=clk.sleep, clock=clk.now)
    assert recovered == [] and still == ["NY flapping"]
    # rounds at 120s and 240s, then a final round clipped to the 300s budget
    assert clk.sleeps == [120, 120, 60]
    assert [t for t, _ in probes] == [120, 240, 300]


def test_preflight_wait_success_mid_window_stops_early():
    clk = FakeClock()
    recovered, still = b.preflight_wait_for_no_data(
        [dict(SRC, label="NY")], wait_budget=1500, interval=120,
        probe=lambda s: clk.t >= 240,  # server back during round 2
        sleep=clk.sleep, clock=clk.now)
    assert recovered == ["NY"] and still == []
    assert clk.sleeps == [120, 120]  # no further rounds once everyone is back


def test_preflight_wait_reprobes_only_still_failing_sources():
    clk = FakeClock()
    calls = []

    def probe(s):
        calls.append((clk.t, s["label"]))
        return s["label"] == "CO" and clk.t >= 120

    recovered, still = b.preflight_wait_for_no_data(
        [dict(SRC, label="CO"), dict(SRC, label="NY")],
        wait_budget=240, interval=120,
        probe=probe, sleep=clk.sleep, clock=clk.now)
    assert recovered == ["CO"] and still == ["NY"]
    # CO recovered in round 1 and is NOT probed again in round 2
    assert calls == [(120, "CO"), (120, "NY"), (240, "NY")]


def test_preflight_wait_zero_budget_returns_immediately():
    clk = FakeClock()
    recovered, still = b.preflight_wait_for_no_data(
        [dict(SRC, label="NY")], wait_budget=0,
        probe=lambda s: True, sleep=clk.sleep, clock=clk.now)
    # no sleeping, no probing -- behaves exactly like the old instant verdict
    assert recovered == [] and still == ["NY"] and clk.sleeps == []


# ───────────── fetch ordering + end-of-phase final retry ─────────────

def test_shaky_sources_fetched_last_but_taggers_keep_registry_order(tmp_path):
    order = []

    def fetch(source):
        order.append(source["label"])
        return [(KEY, FakeGdf())]

    srcs = [dict(SRC, state="AA", label="AA shaky"),
            dict(SRC, state="BB", label="BB ok"),
            dict(SRC, state="CC", label="CC ok")]
    taggers, status = b.collect_trout_taggers(
        srcs, fetch=fetch, seed_dir=str(tmp_path), fetch_last={"AA shaky"})
    # preflight-shaky source pulled last (max recovery time) ...
    assert order == ["BB ok", "CC ok", "AA shaky"]
    # ... but output stays in registry order (= tagging precedence)
    assert [t["label"] for t in taggers] == ["AA shaky", "BB ok", "CC ok"]
    assert set(status.values()) == {"ok"}


def test_no_seed_failure_gets_final_retry_at_end_of_phase(tmp_path):
    calls = []

    def fetch(source):
        calls.append(source["label"])
        if source["label"] == "NY bare" and calls.count("NY bare") == 1:
            raise RuntimeError("flapping")
        return [(KEY, FakeGdf())]

    taggers, status = b.collect_trout_taggers(
        [dict(SRC, state="NY", label="NY bare"),
         dict(SRC, state="VA", label="VA ok")],
        fetch=fetch, seed_dir=str(tmp_path))
    # the failed no-seed source is retried once more AFTER everything else
    assert calls == ["NY bare", "VA ok", "NY bare"]
    assert status == {"NY bare": "ok", "VA ok": "ok"}
    assert [(t["label"], t["live"]) for t in taggers] == \
        [("NY bare", True), ("VA ok", True)]


def test_final_retry_exhaustion_is_unreachable(tmp_path):
    calls = []

    def dead(source):
        calls.append(source["label"])
        raise RuntimeError("still down")

    taggers, status = b.collect_trout_taggers(
        [dict(SRC, state="NY", label="NY bare")],
        fetch=dead, seed_dir=str(tmp_path))
    assert calls == ["NY bare", "NY bare"]  # initial + one final retry
    assert status == {"NY bare": "unreachable"} and taggers == []


def test_seeded_failure_skips_final_retry(tmp_path):
    sd = str(tmp_path)
    b.write_source_seed(SRC, {KEY: {7}}, seed_dir=sd)
    calls = []

    def dead(source):
        calls.append(source["label"])
        raise RuntimeError("down")

    taggers, status = b.collect_trout_taggers([SRC], fetch=dead, seed_dir=sd)
    # a seed already degrades gracefully -- no extra end-of-phase pull
    assert calls == ["CO Aquatic Management Waters"]
    assert status == {"CO Aquatic Management Waters": "bundled-seed"}
    assert taggers[0]["live"] is False


# ───────────────────── pagination truncation detection ─────────────────────

def _arcgis_transport(total: int, seen=None):
    """MockTransport for an ArcGIS layer with `total` features, OBJECTID
    1..total, honoring keyset pagination + resultRecordCount. When `seen` is
    a list, every /query request's (path, params) is appended to it so tests
    can assert exactly which query parameters went over the wire."""
    def handler(request):
        if not request.url.path.endswith("/query"):
            return httpx.Response(200, json={
                "objectIdField": "OBJECTID",
                "fields": [{"name": "OBJECTID"}]})
        params = dict(request.url.params)
        if seen is not None:
            seen.append((request.url.path, params))
        n = int(params.get("resultRecordCount", 1000))
        m = re.search(r"OBJECTID > (-?\d+)", params.get("where", ""))
        bound = max(int(m.group(1)), 0) if m else 0
        feats = [{"type": "Feature", "properties": {"OBJECTID": i},
                  "geometry": {"type": "Point", "coordinates": [1.0, 2.0, None]}}
                 for i in range(bound + 1, min(bound + n, total) + 1)]
        return httpx.Response(200, json={"type": "FeatureCollection",
                                         "features": feats})
    return httpx.MockTransport(handler)


def _mock_client(monkeypatch, transport, client_kwargs=None):
    real = httpx.Client

    def make(**kw):
        if client_kwargs is not None:
            client_kwargs.append(kw)
        return real(transport=transport, **kw)

    monkeypatch.setattr(b.httpx, "Client", make)


QUERY_URL = "https://example.test/arcgis/rest/services/X/MapServer/0/query?where=1%3D1"


def test_pagination_completes_and_strips_z(monkeypatch):
    _mock_client(monkeypatch, _arcgis_transport(total=250))
    feats = b.fetch_arcgis_features(QUERY_URL, page_size=100)
    assert len(feats) == 250
    assert sorted(f["properties"]["OBJECTID"] for f in feats) == \
        list(range(1, 251))
    assert feats[0]["geometry"]["coordinates"] == [1.0, 2.0]  # Z dropped


def test_pagination_truncation_raises_instead_of_shipping_partial(monkeypatch):
    # 1000 features but only 3 pages allowed: a silently-partial layer must
    # raise (so the per-source retry / seed fallback engage), never return.
    _mock_client(monkeypatch, _arcgis_transport(total=1000))
    monkeypatch.setattr(b, "MAX_PAGES", 3)
    with pytest.raises(RuntimeError, match="truncated"):
        b.fetch_arcgis_features(QUERY_URL, page_size=100)


def test_default_fetch_params_unchanged(monkeypatch):
    # No tuning overrides -> exactly the historical wire format: 1000/page,
    # NO maxAllowableOffset, default client timeout.
    seen, clients = [], []
    _mock_client(monkeypatch, _arcgis_transport(total=3, seen=seen), clients)
    feats = b.fetch_arcgis_features(QUERY_URL)
    assert len(feats) == 3
    for _, params in seen:
        assert params["resultRecordCount"] == "1000"
        assert "maxAllowableOffset" not in params
    assert all(kw["timeout"] == b.REQUEST_TIMEOUT for kw in clients)


def test_page_size_and_max_offset_land_in_query_params(monkeypatch):
    seen, clients = [], []
    _mock_client(monkeypatch, _arcgis_transport(total=250, seen=seen), clients)
    feats = b.fetch_arcgis_features(QUERY_URL, page_size=100,
                                    max_offset=0.0001, timeout=60.0)
    assert len(feats) == 250  # pagination still completes with tuning applied
    assert len(seen) >= 3  # 100/page over 250 features really paginated
    for _, params in seen:
        assert params["resultRecordCount"] == "100"
        assert params["maxAllowableOffset"] == "0.0001"
    assert all(kw["timeout"] == 60.0 for kw in clients)


# ─────────────── registry fetch tuning (page_size / max_offset) ───────────────

def test_source_fetch_kwargs_resolution():
    # untuned source -> empty (defaults preserved)
    assert b._source_fetch_kwargs({"state": "VA"}) == {}
    # source-level tuning; declaring page_size also raises the read timeout
    src = {"state": "CO", "page_size": 100, "max_offset": 0.0001}
    assert b._source_fetch_kwargs(src) == {
        "page_size": 100, "max_offset": 0.0001,
        "timeout": b.SLOW_SOURCE_TIMEOUT}
    # multi_layer sublayer override wins over the source-level value
    assert b._source_fetch_kwargs(src, {"id": 2, "page_size": 50}) == {
        "page_size": 50, "max_offset": 0.0001,
        "timeout": b.SLOW_SOURCE_TIMEOUT}
    # sublayer can tune a source that declares nothing itself
    assert b._source_fetch_kwargs({"state": "CO"}, {"id": 2}) == {}
    assert b._source_fetch_kwargs(
        {"state": "CO"}, {"id": 2, "max_offset": 0.0002}) == \
        {"max_offset": 0.0002}


def test_multi_layer_fetch_plumbs_registry_tuning(monkeypatch):
    # End-to-end through fetch_trout_source: the CO-style registry entry's
    # page_size/max_offset reach the wire for every sublayer, with a
    # per-sublayer override honored.
    seen, clients = [], []
    _mock_client(monkeypatch, _arcgis_transport(total=5, seen=seen), clients)
    src = {"state": "CO", "label": "CO Aquatic Management Waters",
           "mode": "multi_layer", "page_size": 100, "max_offset": 0.0001,
           "base": "https://example.test/arcgis/rest/services/X/FeatureServer",
           "layers": [
               {"id": 2, "class": "wild_reproduction", "tier": "class2",
                "native": True, "label": "dense polygons"},
               {"id": 3, "class": "stocked", "tier": "class3",
                "label": "sublayer override", "page_size": 25}]}
    out = b.fetch_trout_source(src)
    assert [key for key, _ in out] == [
        ("wild_reproduction", "class2", True), ("stocked", "class3", False)]
    by_layer = {}
    for path, params in seen:
        by_layer.setdefault(path, []).append(params)
    for params in by_layer["/arcgis/rest/services/X/FeatureServer/2/query"]:
        assert params["resultRecordCount"] == "100"
        assert params["maxAllowableOffset"] == "0.0001"
    for params in by_layer["/arcgis/rest/services/X/FeatureServer/3/query"]:
        assert params["resultRecordCount"] == "25"
        assert params["maxAllowableOffset"] == "0.0001"
    # declared page_size -> slow-source read timeout on every client
    assert all(kw["timeout"] == b.SLOW_SOURCE_TIMEOUT for kw in clients)


def test_co_registry_entry_declares_fetch_tuning():
    # The real data/trout/sources.json CO entry carries the tuning that fixes
    # the CPW bulk-pull timeout, and max_offset stays safely below the build's
    # own downstream simplification + join buffer.
    import trout_registry
    co = next(s for s in trout_registry.load_sources()
              if s.get("label") == "CO Aquatic Management Waters")
    assert b._source_fetch_kwargs(co) == {
        "page_size": 100, "max_offset": 0.0001,
        "timeout": b.SLOW_SOURCE_TIMEOUT}
    assert co["max_offset"] < b.SIMPLIFY_TOL
    assert co["max_offset"] < b.SPATIAL_JOIN_BUFFER_DEG


def test_metadata_failure_no_longer_silently_single_pages(monkeypatch):
    # A broken layer-metadata endpoint used to downgrade the pull to ONE
    # unpaginated page; now it propagates as a fetch failure.
    def handler(request):
        if not request.url.path.endswith("/query"):
            return httpx.Response(500)
        return httpx.Response(200, json={"features": []})
    _mock_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setattr(b, "MAX_RETRIES", 1)
    with pytest.raises(httpx.HTTPStatusError):
        b.fetch_arcgis_features(QUERY_URL, page_size=100)


# ───────────────────────── seed-write atomicity ─────────────────────────

def test_seed_write_replaces_corrupt_file_and_leaves_no_temp(tmp_path):
    sd = str(tmp_path)
    path = b.seed_path(SRC, sd)
    with open(path, "w") as f:
        f.write("{corrupt json")  # e.g. a crash mid-write under the old code
    out = b.write_source_seed(SRC, {KEY: {1, 2}}, seed_dir=sd)
    assert out == path and json.load(open(path))["comid_count"] == 2
    # atomic os.replace: no .tmp (or any other) residue alongside the seed
    assert os.listdir(sd) == [os.path.basename(path)]
