"""Unit tests for the river-POI builder's pure + shapely core
(scripts/build_river_poi.py). Source fetching, EPSG:5070 projection, and the
geopandas stack run only in the data-build job; here we cover normalization,
cross-source dedupe, and the STRtree clip/associate with synthetic planar
coordinates (shapely only, no geopandas/pyproj)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "scripts"))
import build_river_poi as bp  # noqa: E402


def test_normalize_osm_types_and_ids():
    pts = bp.normalize_osm([
        {"type": "node", "id": 1, "lat": 39.5, "lon": -76.6,
         "tags": {"leisure": "slipway", "name": "Ramp A"}},
        {"type": "way", "id": 2, "center": {"lat": 39.6, "lon": -76.7},
         "tags": {"leisure": "fishing", "name": "Spot B"}},
        {"type": "node", "id": 3, "lat": 39.7, "lon": -76.8,
         "tags": {"leisure": "fishing", "access": "private"}},
        {"type": "node", "id": 4, "tags": {"leisure": "slipway"}},  # no coords
    ])
    assert len(pts) == 3                                  # coordless dropped
    by_id = {p["source_id"]: p for p in pts}
    assert by_id["n1"]["type"] == "boat_ramp" and by_id["n1"]["access"] == "public"
    assert by_id["w2"]["type"] == "wading_access"         # way center used
    assert by_id["n3"]["access"] == "private"
    assert all(p["source"] == "osm" for p in pts)


def test_normalize_osm_parking_and_trailhead():
    pts = bp.normalize_osm([
        {"type": "way", "id": 20, "center": {"lat": 39.5, "lon": -76.6},
         "tags": {"amenity": "parking", "name": "River Lot"}},
        {"type": "node", "id": 21, "lat": 39.6, "lon": -76.7,
         "tags": {"highway": "trailhead", "name": "NCR Trailhead"}},
        {"type": "node", "id": 22, "lat": 39.7, "lon": -76.8,
         "tags": {"man_made": "pier"}},
        {"type": "node", "id": 23, "lat": 39.8, "lon": -76.9,
         "tags": {"highway": "residential"}},   # not an access tag -> dropped
    ])
    by_id = {p["source_id"]: p["type"] for p in pts}
    assert by_id == {"w20": "parking", "n21": "walk_in", "n22": "pier"}


def test_normalize_osm_geojson_centroids_ways():
    # osmium export emits ways/areas as Line/Polygon; we take the centroid.
    p = bp.normalize_osm_geojson({
        "type": "Feature", "id": "w99",
        "geometry": {"type": "LineString",
                     "coordinates": [[-76.60, 39.40], [-76.62, 39.42]]},
        "properties": {"amenity": "parking", "name": "Lot"}})
    assert p["type"] == "parking" and p["source"] == "osm"
    assert abs(p["lon"] - -76.61) < 1e-6 and abs(p["lat"] - 39.41) < 1e-6
    # A feature with none of our tags is dropped, not mis-bucketed.
    assert bp.normalize_osm_geojson({
        "type": "Feature", "id": "n1",
        "geometry": {"type": "Point", "coordinates": [-76.6, 39.4]},
        "properties": {"highway": "residential"}}) is None


def _agency_feat(props, lon=-77.5, lat=38.5):
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": props}


def test_normalize_agency_type_flags_first_truthy_wins():
    src = {"state": "MD", "agency_url": "https://x.gov", "name_field": "N",
           "type_flags": {"RAMP": "boat_ramp", "PIER": "pier",
                          "SHORE_FISH": "walk_in"}}
    pts = bp.normalize_agency([
        _agency_feat({"N": "Ramp+Pier", "RAMP": "Y", "PIER": "Y"}),
        _agency_feat({"N": "Pier only", "RAMP": "N", "PIER": "Y"}),
        _agency_feat({"N": "Nothing", "RAMP": "N", "PIER": "N"}),
    ], src)
    assert [p["type"] for p in pts] == ["boat_ramp", "pier", "walk_in"]
    assert all(p["source"] == "agency" for p in pts)


def test_normalize_agency_fixed_type_notes_and_keyword():
    src = {"state": "VA", "agency_url": "https://x.gov",
           "name_field": "Facility_N", "fixed_type": "pier",
           "notes_field": "Body_of_Wa"}
    (p,) = bp.normalize_agency(
        [_agency_feat({"Facility_N": "Cameron Run", "Body_of_Wa": "Lake Cook"})],
        src)
    assert p["type"] == "pier" and p["notes"] == "Lake Cook"
    assert p["access"] == "public" and p["agency_url"] == "https://x.gov"
    # type_field free text -> keyword normalizer (KY "Any Boat" etc.)
    kt = {"state": "KY", "name_field": "N", "type_field": "AccessType"}
    got = [bp.normalize_agency([_agency_feat({"N": "x", "AccessType": v})], kt)[0]
           ["type"] for v in ("Any Boat", "Small Boat Only", "Bank Access")]
    assert got == ["boat_ramp", "boat_ramp", "walk_in"]


def test_normalize_agency_dedupe_collapses_parcels():
    # NY PFR publishes thousands of tiny bank parcels; dedupe keeps one pin
    # per named water per ~1 km cell.
    src = {"state": "NY", "agency_url": "https://x.gov", "name_field": "W",
           "fixed_type": "walk_in", "dedupe": True}
    feats = [
        _agency_feat({"W": "Beaver Kill"}, lon=-74.9012, lat=41.9501),
        _agency_feat({"W": "Beaver Kill"}, lon=-74.9014, lat=41.9503),  # same cell
        _agency_feat({"W": "Beaver Kill"}, lon=-74.93, lat=41.96),      # next cell
    ]
    assert len(bp.normalize_agency(feats, src)) == 2


def test_normalize_type_maps_strings_onto_enum():
    n = bp._normalize_type
    assert n("Boat Ramp") == "boat_ramp"
    assert n("BOAT LAUNCH") == "boat_ramp"
    assert n("Fishing Pier") == "pier"
    assert n("Wading Access") == "wading_access"
    assert n("Parking Lot") == "parking"
    assert n("Trail to river") == "walk_in"
    assert n(None) == "walk_in"


def test_normalize_agency_centroids_non_point_geometry():
    feat = {"type": "Feature",
            "geometry": {"type": "LineString",
                         "coordinates": [[-77.0, 38.0], [-77.2, 38.2]]},
            "properties": {"N": "Reach"}}
    (p,) = bp.normalize_agency([feat], {"state": "X", "name_field": "N"})
    assert abs(p["lon"] - -77.1) < 1e-6 and abs(p["lat"] - 38.1) < 1e-6


def test_normalize_ridb_drops_null_island_and_types_launches():
    pts = bp.normalize_ridb([
        {"FacilityID": 10, "FacilityName": "Smith Boat Launch",
         "FacilityLatitude": 40.0, "FacilityLongitude": -77.0},
        {"FacilityID": 11, "FacilityName": "River Fishing Site",
         "FacilityLatitude": 0, "FacilityLongitude": 0},     # null island -> drop
        {"FacilityID": 12, "FacilityName": "Quiet Trailhead",
         "FacilityLatitude": 40.1, "FacilityLongitude": -77.1},
    ])
    assert {p["source_id"]: p["type"] for p in pts} == {
        "10": "boat_ramp", "12": "walk_in"}


def test_dedupe_prefers_authoritative_source():
    pts = [
        {"lat": 39.50000, "lon": -76.60000, "type": "boat_ramp", "source": "osm"},
        {"lat": 39.50005, "lon": -76.60000, "type": "boat_ramp", "source": "agency"},
        {"lat": 39.70000, "lon": -76.60000, "type": "boat_ramp", "source": "osm"},
        {"lat": 39.50000, "lon": -76.60000, "type": "pier", "source": "osm"},
    ]
    out = bp.dedupe(pts)
    ramps = [p for p in out if p["type"] == "boat_ramp"]
    # the two ~5 m-apart ramps collapse to ONE, keeping the agency coordinate;
    # the far ramp + the different-type pier at the same spot both survive.
    assert len(ramps) == 2
    near = min(ramps, key=lambda p: abs(p["lat"] - 39.5))
    assert near["source"] == "agency"
    assert any(p["type"] == "pier" for p in out)
    assert len(out) == 3


def test_fast_lonlat_point_line_and_ring_mean():
    assert bp._fast_lonlat({"type": "Point", "coordinates": [-76.6, 39.4]}) == \
        (-76.6, 39.4)
    # a 2-vertex line's ring-mean is its midpoint (matches the old centroid)
    lon, lat = bp._fast_lonlat(
        {"type": "LineString", "coordinates": [[-76.60, 39.40], [-76.62, 39.42]]})
    assert abs(lon - -76.61) < 1e-9 and abs(lat - 39.41) < 1e-9
    # polygon ring descends one level; mean of the 4 corners
    lon, lat = bp._fast_lonlat({"type": "Polygon", "coordinates": [
        [[-76.6, 39.4], [-76.6, 39.5], [-76.5, 39.5], [-76.5, 39.4]]]})
    assert abs(lon - -76.55) < 1e-9 and abs(lat - 39.45) < 1e-9
    assert bp._fast_lonlat({"type": "Polygon", "coordinates": []}) is None


def test_clip_records_vectorized_keeps_near_drops_far():
    """The build's chunked, vectorized clip: projects lon/lat to EPSG:5070,
    keeps points within buffer of a reach, stamps the nearest levelpathid.
    Exercised across a chunk boundary (chunk=2)."""
    from shapely import STRtree, LineString
    from pyproj import Transformer
    tf = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)

    def P(lon, lat):
        return tf.transform(lon, lat)
    reach0 = LineString([P(-76.600, 39.400), P(-76.601, 39.405)])
    reach1 = LineString([P(-78.500, 37.500), P(-78.501, 37.505)])
    tree = STRtree([reach0, reach1])
    recs = [
        {"lon": -76.6001, "lat": 39.4001, "name": "near0", "type": "parking"},
        {"lon": -78.5001, "lat": 37.5001, "name": "near1", "type": "walk_in"},
        {"lon": -80.0, "lat": 35.0, "name": "far", "type": "parking"},
    ]
    out = bp.clip_records(recs, tree, [5000, 6000], tf, buffer_m=150.0, chunk=2)
    assert {(r["name"], r["levelpathid"]) for r in out} == {
        ("near0", 5000), ("near1", 6000)}


def test_clip_and_associate_keeps_near_drops_far():
    from shapely.geometry import LineString
    streams = [LineString([(0, 0), (1000, 0)])]   # planar metres (EPSG:5070-ish)
    lpids = [5000]
    pts = [
        (500.0, 30.0, {"name": "near", "type": "boat_ramp"}),   # 30 m off -> keep
        (500.0, 200.0, {"name": "far", "type": "walk_in"}),     # 200 m off -> drop
    ]
    kept = bp.clip_and_associate(pts, streams, lpids, buffer_m=75.0)
    assert [k["name"] for k in kept] == ["near"]
    assert kept[0]["levelpathid"] == 5000
