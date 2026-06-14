"""Endpoint-watcher tests -- no network. A fake httpx transport answers the
ArcGIS shapes (layer meta, count, geojson, distinct, folder, AGOL search) so we
can exercise load_entries / run / build_report end-to-end and assert the report
shaping, READY-TO-PROMOTE flagging, and graceful malformed/missing handling."""
import json
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import endpoint_watch as ew  # noqa: E402


# --------------------------------------------------------------------------
# A scripted transport: route on (path, params) -> json body.
# --------------------------------------------------------------------------
def make_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), headers=ew.UA)


def _json(body):
    return httpx.Response(200, json=body)


# A layer that is "up" with a clean field set + in-state (MD) samples.
def up_layer_handler(request):
    p = dict(request.url.params)
    if p.get("returnCountOnly") == "true":
        return _json({"count": 42})
    if p.get("returnDistinctValues") == "true":
        fld = p.get("outFields")
        return _json({"features": [
            {"attributes": {fld: "Use III"}},
            {"attributes": {fld: "Use IV"}},
            {"attributes": {fld: "Use III"}},  # dup collapses
        ]})
    if p.get("f") == "geojson":
        return _json({"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [-76.6, 39.3]},
             "properties": {"USE_CLASS": "Use III", "NAME": "Gunpowder"}},
        ]})
    # meta
    return _json({
        "name": "DesignatedUse_Trout",
        "geometryType": "esriGeometryPolyline",
        "fields": [
            {"name": "USE_CLASS", "type": "esriFieldTypeString", "alias": "Use"},
            {"name": "NAME", "type": "esriFieldTypeString", "alias": "Name"},
        ],
    })


def down_handler(request):
    # ArcGIS server-error envelope at HTTP 200 -- must read as DOWN.
    return _json({"status": "error", "messages": ["Could not access servers"]})


def folder_handler(request):
    return _json({
        "folders": ["Sub"],
        "services": [
            {"name": "Fisheries/WildTrout", "type": "MapServer"},
            {"name": "Admin/Parcels", "type": "MapServer"},
        ],
    })


# --------------------------------------------------------------------------
# load_entries
# --------------------------------------------------------------------------
def test_load_entries_includes_watchlist_and_candidates():
    entries, warnings = ew.load_entries()
    ids = {e["id"] for e in entries}
    assert "md-designated-use-field" in ids
    assert "md-fisheries-folder" in ids
    assert "nv-lct-successor" in ids
    # candidates folded in as verify-kind, flagged _is_candidate
    cands = [e for e in entries if e.get("_is_candidate")]
    assert cands and all(e["kind"] == "verify" for e in cands)
    # the 1 stocking + 7 access candidates seeded in the repo
    assert len(cands) == 8
    assert not warnings


def test_malformed_watchlist_warns_not_crashes(tmp_path, monkeypatch):
    bad = tmp_path / "watchlist.json"
    bad.write_text("{ this is not json")
    monkeypatch.setattr(ew, "WATCHLIST", str(bad))
    entries, warnings = ew.load_entries()
    assert any("malformed" in w for w in warnings)
    # candidates still load
    assert any(e.get("_is_candidate") for e in entries)


def test_missing_watchlist_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(ew, "WATCHLIST", str(tmp_path / "nope.json"))
    entries, warnings = ew.load_entries()
    assert any("missing or malformed" in w for w in warnings)


def test_skips_malformed_watchlist_entry(tmp_path, monkeypatch):
    wl = tmp_path / "watchlist.json"
    wl.write_text(json.dumps({"entries": [
        {"id": "ok", "kind": "verify", "state": "MD", "url": "http://x/0"},
        {"id": "nokind", "kind": "bogus", "url": "http://x/1"},  # bad kind
        {"id": "nourl", "kind": "verify"},                        # no url
    ]}))
    monkeypatch.setattr(ew, "WATCHLIST", str(wl))
    entries, warnings = ew.load_entries()
    ids = {e["id"] for e in entries}
    assert "ok" in ids and "nokind" not in ids and "nourl" not in ids
    assert sum("malformed watchlist entry" in w for w in warnings) == 2


# --------------------------------------------------------------------------
# captures (UP) via mock transport
# --------------------------------------------------------------------------
def test_field_dump_capture_with_distinct():
    entry = {"id": "md", "kind": "field_dump", "state": "MD",
             "url": "http://md/0", "field": "USE_CLASS"}
    with make_client(up_layer_handler) as c:
        up, lines, extra = ew.capture_field_dump(c, entry)
    assert up
    text = "\n".join(lines)
    assert "DesignatedUse_Trout" in text
    assert "USE_CLASS" in text
    assert "sample features" in text
    # distinct values queried + de-duped
    assert "distinct `USE_CLASS`" in text
    assert "Use III" in text and "Use IV" in text


def test_field_dump_down_is_not_up():
    entry = {"id": "md", "kind": "field_dump", "state": "MD",
             "url": "http://md/0", "field": None}
    with make_client(down_handler) as c:
        up, lines, extra = ew.capture_field_dump(c, entry)
    assert not up and not lines


def test_discover_lists_fish_named_services_only():
    entry = {"id": "f", "kind": "discover", "state": "MD", "url": "http://md/svc"}
    with make_client(folder_handler) as c:
        up, lines, extra = ew.capture_discover(c, entry)
    assert up
    text = "\n".join(lines)
    assert "Fisheries/WildTrout" in text
    assert "Parcels" not in text   # noise service filtered out


def test_verify_pass_on_candidate_flags_ready_to_promote():
    entry = {"id": "cand-x", "kind": "verify", "state": "MD",
             "url": "http://md/0", "_is_candidate": True}
    with make_client(up_layer_handler) as c:
        up, lines, extra = ew.capture_verify(c, entry)
    assert up
    assert extra.get("ready_to_promote") is True
    assert any("READY TO PROMOTE" in ln for ln in lines)
    assert any("PASS" in ln for ln in lines)


def test_verify_pass_on_watchlist_entry_not_flagged():
    entry = {"id": "wl-x", "kind": "verify", "state": "MD", "url": "http://md/0"}
    with make_client(up_layer_handler) as c:
        up, lines, extra = ew.capture_verify(c, entry)
    assert up and not extra.get("ready_to_promote")


def test_verify_out_of_state_bbox_fails():
    def handler(request):
        p = dict(request.url.params)
        if p.get("returnCountOnly") == "true":
            return _json({"count": 9})
        if p.get("f") == "geojson":  # point in CA, declared NV -> outside bbox
            return _json({"type": "FeatureCollection", "features": [
                {"geometry": {"type": "Point", "coordinates": [-122.0, 37.0]},
                 "properties": {}}]})
        return _json({"name": "L", "geometryType": "esriGeometryPoint",
                      "fields": []})
    entry = {"id": "c", "kind": "verify", "state": "NV", "url": "http://x/0",
             "_is_candidate": True}
    with make_client(handler) as c:
        up, lines, extra = ew.capture_verify(c, entry)
    assert up and not extra.get("ready_to_promote")
    assert any("outside NV bbox" in ln for ln in lines)


# --------------------------------------------------------------------------
# report shaping
# --------------------------------------------------------------------------
def test_build_report_status_table_and_promote():
    results = [
        {"id": "a", "state": "MD", "kind": "field_dump", "note": "n", "up": True,
         "lines": ["- detail"], "extra": {}},
        {"id": "b", "state": "NV", "kind": "discover", "note": "", "up": False,
         "lines": [], "extra": {}},
        {"id": "c", "state": "CA", "kind": "verify", "note": "", "up": True,
         "lines": ["- ok"], "extra": {"ready_to_promote": True}},
    ]
    md = ew.build_report(results, warnings=["w1"])
    assert "| id | state | kind | status | captured |" in md
    assert "| a | MD | field_dump | UP | yes |" in md
    assert "| b | NV | discover | DOWN | - |" in md
    assert "| c | CA | verify | UP | PROMOTE |" in md
    assert "2/3 reachable" in md
    assert "1 READY TO PROMOTE" in md
    assert "Warnings: w1" in md
    assert "Captured detail" in md


def test_build_report_all_down_message():
    results = [{"id": "a", "state": "MD", "kind": "verify", "note": "", "up": False,
                "lines": [], "extra": {}}]
    md = ew.build_report(results, warnings=[])
    assert "0/1 reachable" in md
    assert "No watched endpoints were reachable" in md


# --------------------------------------------------------------------------
# run() never crashes on a throwing capture
# --------------------------------------------------------------------------
def test_run_survives_probe_exception(monkeypatch):
    def boom(client, entry):
        raise RuntimeError("host exploded")
    monkeypatch.setitem(ew.CAPTURES, "verify", boom)
    with make_client(down_handler) as c:
        results = ew.run(c, [{"id": "x", "kind": "verify", "state": "MD",
                              "url": "http://x/0", "note": ""}])
    assert results[0]["up"] is False
    assert "probe error" in results[0]["note"]


def test_main_writes_report_and_exits_zero(tmp_path, monkeypatch):
    out = tmp_path / "WATCH.md"
    monkeypatch.setattr(ew, "OUT_FILE", str(out))
    monkeypatch.setattr(ew, "WATCHLIST", str(tmp_path / "missing.json"))
    monkeypatch.setattr(ew, "CANDIDATE_FILES", [])
    # no network: every host unreachable -> transport raises ConnectError,
    # which _get swallows. Use a real client but point at nothing reachable.
    rc = ew.main()
    assert rc == 0
    assert out.exists()
    assert "Endpoint watch" in out.read_text()
