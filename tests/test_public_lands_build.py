"""Unit tests for the public-lands build filters (scripts/build_public_lands).

Pure + offline: the corridor screen is plain arithmetic and the access
policy is plain Python, so these run without pyogrio/geopandas/GDAL (which
the build imports lazily, inside functions). The optional shapely-backed
test exercises the metric width on real polygon geometry.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import build_public_lands as bpl  # noqa: E402


# -- access policy: government-owned UK is promoted, private UK dropped --

def test_confirmed_access_passes_through():
    assert bpl.decide_access("OA", "PVT") == "OA"   # OA kept even on private
    assert bpl.decide_access("RA", "STAT") == "RA"  # restricted preserved


@pytest.mark.parametrize("mtype", ["FED", "STAT", "LOC", "DIST", "JNT", "TERR"])
def test_uk_government_land_promoted_to_open_access(mtype):
    # The Prettyboy / Gunpowder-SP failure mode: real public land coded
    # Unknown by the agency. Government ownership -> treat as Open Access.
    assert bpl.decide_access("UK", mtype) == "OA"


@pytest.mark.parametrize("mtype", ["PVT", "NGO", "TRIB", "UNK", None])
def test_uk_nongovernment_land_dropped(mtype):
    # Private ranches / NGO preserves / restricted tribal land coded UK
    # stay dropped -- promoting them would send anglers to locked gates.
    assert bpl.decide_access("UK", mtype) is None


def test_closed_and_unknown_codes_dropped():
    assert bpl.decide_access("XA", "STAT") is None   # explicitly closed
    assert bpl.decide_access(None, "STAT") is None
    assert bpl.decide_access("", "STAT") is None


def test_military_uk_not_promoted():
    # Federally owned but closed: a UK military reservation must NOT turn
    # green even though Mang_Type is government (FED).
    assert bpl.decide_access(
        "UK", "FED", "Department of Defense", "Military") is None
    assert bpl.decide_access(
        "UK", "FED", "U.S. Army", "Army Installation") is None
    assert bpl.decide_access(
        "UK", "FED", "U.S. Air Force Base", None) is None
    # ...but a normal federal forest with a "navy"-free name is fine.
    assert bpl.decide_access(
        "UK", "FED", "U.S. Forest Service", "National Forest") == "OA"


# -- corridor screen: thin linear polygons (rail-trails) are excluded ----

def test_rail_trail_corridor_is_excluded():
    # ~15 m wide, ~20 km long footpath corridor: area/perimeter -> ~15 m.
    area, perim = 300_000.0, 40_030.0
    assert bpl.corridor_width_m(area, perim) < 20
    assert bpl.is_corridor(area, perim)


def test_riverside_park_is_kept():
    # A genuine riverside park strip ~150 m wide, ~3 km long is well over
    # the 40 m corridor threshold and must survive.
    area, perim = 450_000.0, 6_300.0     # ~143 m mean width
    assert bpl.corridor_width_m(area, perim) > bpl.CORRIDOR_MIN_WIDTH_M
    assert not bpl.is_corridor(area, perim)


def test_chunky_forest_is_kept():
    # A compact 5 km x 5 km forest: width estimate is huge, never a corridor.
    area, perim = 25_000_000.0, 20_000.0
    assert not bpl.is_corridor(area, perim)


def test_degenerate_perimeter_is_treated_as_zero_width():
    assert bpl.corridor_width_m(100.0, 0.0) == 0.0
    assert bpl.is_corridor(100.0, 0.0)   # zero width -> corridor (dropped)


def test_corridor_threshold_is_metric_and_modest():
    # Sanity: the threshold separates footpaths (<40 m) from parcels with
    # generous margin, and is expressed in metres.
    assert 30 <= bpl.CORRIDOR_MIN_WIDTH_M <= 60


# -- shapely-backed: width on real geometry (skips if shapely absent) ----

def test_width_matches_shapely_geometry():
    shapely = pytest.importorskip("shapely")
    from shapely.geometry import box
    # A 20 m x 5000 m metric rectangle -> mean width ~20 m -> corridor.
    rect = box(0, 0, 5000, 20)
    assert bpl.corridor_width_m(rect.area, rect.length) == pytest.approx(20, abs=1)
    assert bpl.is_corridor(rect.area, rect.length)
    # A 400 m x 400 m block -> width estimate 200 m -> kept.
    blk = box(0, 0, 400, 400)
    assert not bpl.is_corridor(blk.area, blk.length)
