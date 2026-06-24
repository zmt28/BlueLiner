"""Tests for the stream elevation/gradient profile.

Three layers, all offline:
  - build_nhdplus_vaa pure helpers (S3 discovery + elevation cleaning)
  - main.build_elevation_profile / _section_reaches (the profile math)
  - db round-trip for the new elevation columns + vaa_levelpath_reaches
"""
import gzip
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import build_nhdplus_vaa as bvaa  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402


# -- VAA build: S3 discovery + elevation cleaning --------------------------

def test_parse_archive_key():
    i = bvaa.parse_archive_key(
        "NHDPlusV21/Data/NHDPlusMA/NHDPlusV21_MA_02_NHDPlusAttributes_09.7z")
    assert i and i["vpu_id"] == "MA_02" and i["comp"] == "NHDPlusAttributes"
    assert i["vintage"] == 9 and i["url"].endswith("Attributes_09.7z")
    sa = bvaa.parse_archive_key(
        "NHDPlusV21/Data/NHDPlusSA/NHDPlus03N/NHDPlusV21_SA_03N_NHDSnapshot_07.7z")
    assert sa and sa["vpu_id"] == "SA_03N" and sa["comp"] == "NHDSnapshot"
    assert bvaa.parse_archive_key("NHDPlusV21/Data/readme.txt") is None


def test_vpu_in_conus():
    assert bvaa.vpu_in_conus("MA_02") and bvaa.vpu_in_conus("PN_17")
    assert bvaa.vpu_in_conus("SA_03N")        # sub-VPU letter suffix
    assert not bvaa.vpu_in_conus("AK_19")
    assert not bvaa.vpu_in_conus("HI_20")
    assert not bvaa.vpu_in_conus("PR_21")


def test_select_latest_archives_pairs_and_picks_newest():
    keys = [
        "NHDPlusV21/Data/NHDPlusMA/NHDPlusV21_MA_02_NHDPlusAttributes_08.7z",
        "NHDPlusV21/Data/NHDPlusMA/NHDPlusV21_MA_02_NHDPlusAttributes_09.7z",
        "NHDPlusV21/Data/NHDPlusMA/NHDPlusV21_MA_02_NHDSnapshot_04.7z",
        # MS_05 has attributes but no snapshot -> incomplete -> dropped
        "NHDPlusV21/Data/NHDPlusMS/NHDPlus05/NHDPlusV21_MS_05_NHDPlusAttributes_09.7z",
        # AK present + complete but out of CONUS -> excluded
        "NHDPlusV21/Data/NHDPlusAK/NHDPlusV21_AK_19_NHDPlusAttributes_01.7z",
        "NHDPlusV21/Data/NHDPlusAK/NHDPlusV21_AK_19_NHDSnapshot_01.7z",
    ]
    sel = bvaa.select_latest_archives(keys)
    assert set(sel) == {"MA_02"}
    assert sel["MA_02"]["vaa"].endswith("Attributes_09.7z")   # newest vintage
    assert sel["MA_02"]["snap"].endswith("Snapshot_04.7z")


def test_clean_elev_drops_sentinel():
    assert bvaa._clean_elev(-9998) is None      # NHDPlus NODATA
    assert bvaa._clean_elev("-9998") is None
    assert bvaa._clean_elev(67000) == 67000
    assert bvaa._clean_elev("670.0") == 670
    assert bvaa._clean_elev(None) is None
    assert bvaa._clean_elev("") is None
    assert bvaa._clean_elev("nope") is None


# -- Profile math ----------------------------------------------------------

def _reach(comid, hydroseq, name, lengthkm, maxe, mine):
    return {"comid": comid, "hydroseq": hydroseq, "gnis_name": name,
            "lengthkm": lengthkm, "maxelevsmo": maxe, "minelevsmo": mine}


# Upstream -> downstream (hydroseq DESC). Two Morgan Run reaches then the
# levelpath continues onto the Patapsco (same levelpathid, different name).
SECTION = [
    _reach(1, 400, "Morgan Run", 3.0, 25000, 24000),
    _reach(2, 300, "Morgan Run", 4.0, 24000, 22500),
    _reach(3, 200, "Patapsco River", 5.0, 22500, 20000),
]


def test_section_reaches_contiguous_block_by_focus():
    sec = main._section_reaches(SECTION, None, 1)
    assert [r["comid"] for r in sec] == [1, 2]    # only the Morgan Run block


def test_section_reaches_by_name_when_no_focus():
    sec = main._section_reaches(SECTION, "patapsco river", None)
    assert [r["comid"] for r in sec] == [3]


def test_profile_filters_to_named_section_and_computes_summary():
    p = main.build_elevation_profile(SECTION, focus_comid=1)
    assert p["name"] == "Morgan Run"
    # 3 km + 4 km = 7 km ~= 4.35 mi of Morgan Run (NOT the Patapsco)
    assert abs(p["length_mi"] - 4.3) < 0.2
    # 25000 cm -> 22500 cm = 2500 cm ~= 82 ft of drop
    assert 80 <= p["elev_change_ft"] <= 84
    assert p["high_ft"] > p["low_ft"]
    assert p["grade_ft_per_mi"] > 0 and p["grade_pct"] > 0
    assert p["reach_count"] == 2
    assert p["points"][0]["d"] == 0.0          # starts at the upstream end
    assert p["focus"] is not None              # clicked reach marked


def test_profile_none_when_insufficient_data():
    assert main.build_elevation_profile([], name="x") is None
    # one usable reach in the section -> can't form a 2-point line
    assert main.build_elevation_profile(SECTION, name="patapsco river") is None
    # reaches missing elevation are unusable
    no_elev = [_reach(9, 10, "Bare Run", 2.0, None, None),
               _reach(10, 9, "Bare Run", 2.0, None, None)]
    assert main.build_elevation_profile(no_elev, focus_comid=9) is None


def test_profile_decimates_to_cap():
    big = [_reach(i, 1000 - i, "Long River", 0.5,
                  50000 - i * 10, 50000 - (i + 1) * 10)
           for i in range(900)]
    p = main.build_elevation_profile(big, focus_comid=0)
    assert p is not None
    assert len(p["points"]) <= main._PROFILE_MAX_POINTS + 1


# -- DB: elevation columns + levelpath query -------------------------------

def _write_vaa_csv(path, rows, header=None):
    cols = header or ("comid,hydroseq,levelpathid,streamlevel,gnis_name,"
                      "lengthkm,maxelevsmo,minelevsmo")
    with gzip.open(path, "wt") as f:
        f.write(cols + "\n")
        for r in rows:
            f.write(r + "\n")


def test_db_ingests_elevation_and_orders_by_hydroseq(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    csv_gz = str(tmp_path / "vaa.csv.gz")
    _write_vaa_csv(csv_gz, [
        "1,300,55,3,Test Run,3.0,67000,66000",
        "2,200,55,3,Test Run,4.0,66000,64000",
        "3,100,55,3,,1.5,64000,63000",
    ])
    assert db.bulk_load_vaa(csv_gz) == 3
    reaches = db.vaa_levelpath_reaches(55)
    # hydroseq DESC = headwaters first
    assert [r["comid"] for r in reaches] == [1, 2, 3]
    assert reaches[0]["maxelevsmo"] == 67000 and reaches[0]["minelevsmo"] == 66000
    assert db.get_vaa(1)["maxelevsmo"] == 67000


def test_db_tolerates_legacy_csv_without_elevation(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    csv_gz = str(tmp_path / "old.csv.gz")
    _write_vaa_csv(csv_gz, ["9,10,7,3,Old Creek,1.0"],
                   header="comid,hydroseq,levelpathid,streamlevel,"
                          "gnis_name,lengthkm")
    assert db.bulk_load_vaa(csv_gz) == 1
    v = db.get_vaa(9)
    assert v["maxelevsmo"] is None and v["minelevsmo"] is None
