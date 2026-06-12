"""Regression tests for build_clickable_streams.spatial_join_trout.

Guards the trout->NHD spatial join against the data-build.yml failure that
followed commit e852bac (a SECOND EBTJV "Wild trout" entry sharing the native
entry's FeatureServer URL): the build crashed at VPU 13 (Rio Grande) with

    KeyError: 'ComID'

Root cause: that VPU's NHDFlowline geometry spells its id column "ComID"
(mixed case), and the EBTJV catchment overlay carries its OWN "ComID" attribute
field. gpd.sjoin sees a case-exact column collision and suffixes both to
<name>_left/<name>_right, after which `joined[id_col]` is a KeyError. The first
(native) EBTJV entry never tripped it: its catchment bbox doesn't reach VPU 13,
and the eastern VPUs spell the column "COMID" (no case collision). The added
"Wild trout" entry's small, scattered group DID bbox-overlap VPU 13, where the
"ComID"/"ComID" collision finally fired.

These tests need geopandas/shapely (the build's dev deps), so they live apart
from the pure/offline test_trout_registry.py.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

gpd = pytest.importorskip("geopandas")
shapely = pytest.importorskip("shapely")
from shapely import geometry as sg  # noqa: E402

import build_clickable_streams as b  # noqa: E402


def _nhd(id_field):
    """A 2-line NHD frame whose id column is named `id_field` (so we can model
    both 'COMID' eastern VPUs and the 'ComID' Rio Grande VPU)."""
    return gpd.GeoDataFrame(
        {id_field: [101, 102]},
        geometry=[sg.LineString([(-106.0, 34.0), (-106.1, 34.1)]),
                  sg.LineString([(-105.0, 35.0), (-105.1, 35.1)])],
        crs=4326,
    )


def _trout(with_comid_col, overlap):
    """A trout-overlay group like an EBTJV split: it optionally carries its own
    'ComID' attribute field, and either overlaps line 101 or sits far away."""
    pts = ([sg.Point(-106.05, 34.05).buffer(0.05)] if overlap
           else [sg.Point(-50.0, 40.0).buffer(0.05)])
    cols = {"Trout_community": ["Wild trout"]}
    if with_comid_col:
        cols = {"ComID": [9001], **cols}
    return gpd.GeoDataFrame(cols, geometry=pts, crs=4326)


def test_id_column_collision_does_not_raise():
    # The exact data-build.yml crash: NHD id column "ComID" + a trout layer that
    # also carries a "ComID" field. Must return the overlapping COMID, not raise.
    nhd = _nhd("ComID")
    trout = _trout(with_comid_col=True, overlap=True)
    attrs = {101: {}, 102: {}}
    assert b.spatial_join_trout(trout, nhd, attrs) == {101}


def test_id_column_collision_zero_overlap_returns_empty():
    # Collision present AND the source matches zero features (the EBTJV "Wild
    # trout" group bbox-overlapping VPU 13 but not actually intersecting it):
    # skip cleanly with an empty set rather than KeyError.
    nhd = _nhd("ComID")
    trout = _trout(with_comid_col=True, overlap=False)
    assert b.spatial_join_trout(trout, nhd, {101: {}, 102: {}}) == set()


def test_uppercase_id_column_still_joins():
    # The common eastern-VPU spelling "COMID" with a no-collision trout layer
    # must keep working (behavior preservation for the 30+ existing sources).
    nhd = _nhd("COMID")
    trout = _trout(with_comid_col=False, overlap=True)
    assert b.spatial_join_trout(trout, nhd, {101: {}, 102: {}}) == {101}


def test_degenerate_geometry_member_is_tolerated():
    # A group with an empty/degenerate geometry member (which can inflate
    # total_bounds and force a join against a non-overlapping VPU) must still
    # return only the real overlap, not raise.
    nhd = _nhd("ComID")
    trout = gpd.GeoDataFrame(
        {"ComID": [9001, 9002], "Trout_community": ["Wild trout", "Wild trout"]},
        geometry=[sg.Point(-106.05, 34.05).buffer(0.05), sg.Polygon()],
        crs=4326,
    )
    assert b.spatial_join_trout(trout, nhd, {101: {}, 102: {}}) == {101}


def test_all_attrs_membership_confines_to_region():
    # COMIDs absent from all_attrs (a different VPU) are dropped, mirroring the
    # per-VPU confinement the build relies on.
    nhd = _nhd("ComID")
    trout = _trout(with_comid_col=True, overlap=True)
    assert b.spatial_join_trout(trout, nhd, {999: {}}) == set()
