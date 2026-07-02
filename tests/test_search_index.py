"""M4.2: search-index builder's pure parsers (USGS RDB + Census
gazetteer). The fetch paths need egress and run in CI / the data-build
environment; the live smoke test is `--states MD --skip-census`."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "scripts"))
import build_search_index as bsi  # noqa: E402


RDB = """# USGS comment
# more comment
agency_cd\tsite_no\tstation_nm\tsite_tp_cd\tdec_lat_va\tdec_long_va
5s\t15s\t50s\t7s\t16s\t16s
USGS\t01581920\tGUNPOWDER FALLS NEAR PARKTON, MD\tST\t39.6194444\t-76.6625
USGS\t01582000\tLITTLE FALLS AT BLUE MOUNT, MD\tST\t39.6666\t-76.5833
USGS\t01582999\tNO COORDS SITE\tST\t\t
USGS\t\tMISSING SITE NO\tST\t39.0\t-76.0
"""


def test_parse_usgs_rdb():
    rows = bsi.parse_usgs_rdb(RDB, "MD")
    assert len(rows) == 2                       # bad rows dropped
    site_no, name, st, lat, lon = rows[0]
    assert site_no == "01581920"
    assert name == "Gunpowder Falls Near Parkton, Md"   # title-cased
    assert st == "MD"
    assert lat == 39.6194 and lon == -76.6625   # 4-dp rounding


GAZ = (
    "USPS\tGEOID\tANSICODE\tNAME\tLSAD\tFUNCSTAT\tALAND\tAWATER\t"
    "ALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG\n"
    "MD\t24005\t01695314\tBaltimore County\t06\tA\t1\t1\t1\t1\t"
    "39.4431\t-76.6165\n"
    "PR\t72001\t01804480\tAdjuntas Municipio\t13\tA\t1\t1\t1\t1\t"
    "18.1810\t-66.7580\n"
)


def test_parse_gazetteer_counties_filters_non_conus():
    rows = bsi.parse_gazetteer(GAZ, "county")
    assert rows == [["Baltimore County", "MD", 39.4431, -76.6165]]


def test_parse_gazetteer_survives_utf8_bom():
    # The real Census files carry a UTF-8 BOM; a plain utf-8 decode left
    # "﻿USPS" as the first header key and zeroed the whole parse
    # (CI run 2 of the index build). Both decode paths must survive it.
    bommed = "﻿" + GAZ
    assert bsi.parse_gazetteer(bommed, "county") == [
        ["Baltimore County", "MD", 39.4431, -76.6165]]
    # And the canonical fix: utf-8-sig at decode time.
    decoded = bommed.encode("utf-8").decode("utf-8-sig")
    assert bsi.parse_gazetteer(decoded, "county")[0][0] == "Baltimore County"


GAZ_PLACES = (
    "USPS\tGEOID\tANSICODE\tNAME\tLSAD\tFUNCSTAT\tALAND\tAWATER\t"
    "ALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG\n"
    "MD\t2460325\t02390597\tParkton CDP\t57\tS\t1\t1\t1\t1\t"
    "39.6420\t-76.6620\n"
    "PA\t4262416\t01214759\tRenovo borough\t21\tA\t1\t1\t1\t1\t"
    "41.3264\t-77.7508\n"
)


def test_parse_gazetteer_places_strips_suffixes():
    rows = bsi.parse_gazetteer(GAZ_PLACES, "place")
    assert [r[0] for r in rows] == ["Parkton", "Renovo"]
