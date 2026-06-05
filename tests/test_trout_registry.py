"""Behavior-preservation tests for the declarative trout-source registry.

Phase 1 replaced the per-state fetch_trout_* functions with
data/trout/sources.json + trout_registry.row_bucket. These tests assert the
registry engine reproduces the OLD per-state classification *exactly*, using the
shipped 10-state values as the oracle (the build's _nc_bucket / NY MGMTCAT map /
GA flag logic). Pure + offline -- no network, no geopandas.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import trout_registry as reg  # noqa: E402

ALL_SOURCES = reg.load_sources()
SOURCES = {s["state"]: s for s in ALL_SOURCES}            # last wins per state
BY_LABEL = {s.get("label", s["state"]): s for s in ALL_SOURCES}


def test_registry_covers_states_with_valid_modes():
    assert {"MD", "VA", "PA", "NJ", "VT", "MA", "WV", "NY", "NC", "GA", "CT"} \
        <= {s["state"] for s in ALL_SOURCES}
    for s in ALL_SOURCES:
        assert s["mode"] in {"single", "multi_layer", "field_map",
                             "field_prefix", "flags"}
        if s["mode"] == "single":
            assert s["class"] and s.get("url")
        if s["mode"] == "multi_layer":
            assert s.get("base") and all(l.get("id") is not None and l.get("class")
                                         for l in s["layers"])


def test_single_bucket_states():
    assert reg.row_bucket(SOURCES["MD"], {}) == "designated"
    assert reg.row_bucket(SOURCES["VA"], {}) == "wild_reproduction"
    assert reg.row_bucket(SOURCES["WV"], {}) == "stocked"
    assert reg.row_bucket(SOURCES["NJ"], {}) == "stocked"


def test_ny_field_map_matches_old_mgmtcat_logic():
    ny = SOURCES["NY"]
    expect = {"Stocked": "stocked", "Stocked-Extended": "stocked",
              "Wild-Quality": "wild_reproduction", "Wild-Premier": "wild_reproduction",
              "Other": "wild_reproduction"}
    for value, want in expect.items():
        assert reg.row_bucket(ny, {"MGMTCAT": value}) == want
    # Unmapped value and missing field both drop (None), as the old .map did.
    assert reg.row_bucket(ny, {"MGMTCAT": "Heritage"}) is None
    assert reg.row_bucket(ny, {}) is None


def test_nc_field_prefix_matches_old_nc_bucket():
    nc = SOURCES["NC"]
    cases = {
        "Wild Trout Waters": "wild_reproduction",
        "Catch and Release/Artificial Flies and Lures Only Trout Waters": "wild_reproduction",
        "Special Regulation Trout Waters": "wild_reproduction",
        "Hatchery Supported Trout Waters": "stocked",
        "Delayed Harvest Trout Waters": "stocked",
        # Hurricane-Helene suffixed variants still bucket on the prefix:
        "Hatchery Supported Trout Waters - CLOSED UNTIL FURTHER NOTICE": "stocked",
        "Delayed Harvest Trout Waters - CLOSED UNTIL FURTHER NOTICE": "stocked",
    }
    for value, want in cases.items():
        assert reg.row_bucket(nc, {"FIRST_WRC_": value}) == want
    # Unmatched -> dropped.
    assert reg.row_bucket(nc, {"FIRST_WRC_": "Trout Pond"}) is None
    assert reg.row_bucket(nc, {"FIRST_WRC_": "", "WRC_Class": None}) is None
    # Coalesce: a null in the first field falls through to the second.
    assert reg.row_bucket(nc, {"FIRST_WRC_": None,
                               "WRC_Class": "Wild Trout Waters"}) == "wild_reproduction"


def test_ga_flags_match_old_mask_logic():
    ga = SOURCES["GA"]
    assert reg.row_bucket(ga, {"Hvy_stock": "Yes", "Delay_har": "No"}) == "stocked"
    assert reg.row_bucket(ga, {"Hvy_stock": "No", "Delay_har": "Yes"}) == "stocked"
    assert reg.row_bucket(ga, {"Hvy_stock": "No", "Delay_har": "No"}) == "wild_reproduction"
    assert reg.row_bucket(ga, {"Hvy_stock": " ", "Delay_har": " "}) == "wild_reproduction"
    assert reg.row_bucket(ga, {}) == "wild_reproduction"          # default, all tagged


def test_pa_multi_layer_classes():
    pa = SOURCES["PA"]
    classes = {l["class"] for l in pa["layers"]}
    assert classes == {"wild_reproduction", "class_a", "wilderness", "stocked"}


def test_field_prefix_default_bucket():
    # CT FMA: only "(Class 1)" WTMAs are wild; everything else -> default stocked.
    ct = BY_LABEL["CT (WTMA)"]
    f = ct["fields"][0]
    assert reg.row_bucket(ct, {f: "Heather Reaves Wild Trout Management Area (Class 1)"}) \
        == "wild_reproduction"
    assert reg.row_bucket(ct, {f: "Heather Reaves Wild Trout Management Area (Class 2)"}) \
        == "stocked"
    assert reg.row_bucket(ct, {f: "Trophy Trout Lake"}) == "stocked"   # default


def test_field_prefix_without_default_still_drops():
    # NC has no default -> unmatched rows stay None (dropped), unchanged.
    assert reg.row_bucket(SOURCES["NC"], {"FIRST_WRC_": "Trout Pond"}) is None


def test_wi_class_i_and_ii_wild_iii_stocked():
    # Nationwide principle: Class I (self-sustaining) and Class II (natural
    # reproduction + supplemental stocking) -> wild; Class III (no reproduction,
    # put-and-take) -> stocked. Rules ordered III/3 before II/I so "class i"
    # can't leak into the Class II/III rows.
    wi = SOURCES["WI"]
    f = wi["fields"][0]
    assert reg.row_bucket(wi, {f: "Class I"}) == "wild_reproduction"
    assert reg.row_bucket(wi, {f: "Class II"}) == "wild_reproduction"
    assert reg.row_bucket(wi, {f: "Class III"}) == "stocked"
    # Case-insensitive + tolerant of the rendered-label vs stored-code casing.
    assert reg.row_bucket(wi, {f: "CLASS II"}) == "wild_reproduction"
    assert reg.row_bucket(wi, {f: "CLASS III"}) == "stocked"
    # Arabic fallback, in case the code isn't roman.
    assert reg.row_bucket(wi, {f: "Class 2"}) == "wild_reproduction"
    assert reg.row_bucket(wi, {f: "Class 3"}) == "stocked"
    # Unclassified / missing -> dropped (no default).
    assert reg.row_bucket(wi, {f: ""}) is None
    assert reg.row_bucket(wi, {}) is None


def test_mi_type12_wild_type34_stocked_nondesignated_dropped():
    # Nationwide principle: Type 1 (self-sustaining) and Type 2 (natural
    # reproduction + supplemental stocking) -> wild. Type 3 stays stocked
    # ('stricter on edges' -- marginal reproduction, stocking-dependent); Type 4
    # (no reproduction) -> stocked. GR/BTRA qualifiers follow the type number;
    # every "Non Designated" variant and the bare "GR Designated" drop.
    mi = SOURCES["MI"]
    f = mi["field"]
    assert reg.row_bucket(mi, {f: "Type 1 Designated"}) == "wild_reproduction"
    assert reg.row_bucket(mi, {f: "Type 1 BTRA Designated"}) == "wild_reproduction"
    assert reg.row_bucket(mi, {f: "GR Type 1 Designated"}) == "wild_reproduction"
    assert reg.row_bucket(mi, {f: "Type 2 Designated"}) == "wild_reproduction"
    assert reg.row_bucket(mi, {f: "GR Type 2 Designated"}) == "wild_reproduction"
    assert reg.row_bucket(mi, {f: "Type 3 Designated"}) == "stocked"
    assert reg.row_bucket(mi, {f: "Type 3 BTRA Designated"}) == "stocked"
    assert reg.row_bucket(mi, {f: "Type 4 Designated"}) == "stocked"
    # field_map is exact -> unmapped / non-designated rows drop (no default).
    assert reg.row_bucket(mi, {f: "Non Designated"}) is None
    assert reg.row_bucket(mi, {f: "Type 3 Non Designated"}) is None
    assert reg.row_bucket(mi, {f: "GR Designated"}) is None
    assert reg.row_bucket(mi, {f: ""}) is None
    assert reg.row_bucket(mi, {}) is None


def test_me_priority_high_and_very_high_wild_not_dropped():
    # ME wild-brook-trout priority reaches: ifw_prty Very High / High -> wild;
    # 'Not' (barrier-maintenance / not-connect) and anything else drop. Exact
    # values verified live via the probe-layer run.
    me = SOURCES["ME"]
    f = me["field"]
    assert reg.row_bucket(me, {f: "Very High"}) == "wild_reproduction"
    assert reg.row_bucket(me, {f: "High"}) == "wild_reproduction"
    assert reg.row_bucket(me, {f: "Not"}) is None
    assert reg.row_bucket(me, {f: "Moderate"}) is None
    assert reg.row_bucket(me, {f: ""}) is None
    assert reg.row_bucket(me, {}) is None


def test_mo_blue_ribbon_wild_rest_stocked():
    # MO ribbons encode management: Blue Ribbon = wild; Red/White/Trout Park/
    # Lake Taneycomo = stocked. Exact AreaType values verified via probe-layer.
    mo = SOURCES["MO"]
    f = mo["field"]
    assert reg.row_bucket(mo, {f: "Blue Ribbon"}) == "wild_reproduction"
    assert reg.row_bucket(mo, {f: "Red Ribbon"}) == "stocked"
    assert reg.row_bucket(mo, {f: "White Ribbon"}) == "stocked"
    assert reg.row_bucket(mo, {f: "Trout Park"}) == "stocked"
    assert reg.row_bucket(mo, {f: "Lake Taneycomo"}) == "stocked"
    # field_map has no default -> unmapped drops.
    assert reg.row_bucket(mo, {f: "Something Else"}) is None
    assert reg.row_bucket(mo, {}) is None


def test_ia_wild_species_wild_blank_stocked():
    # IA wild_trt: any naturally-reproducing species (incl. combos) -> wild;
    # blank / no species -> stocked (default). Values verified via probe-layer.
    ia = SOURCES["IA"]
    f = ia["fields"][0]
    assert reg.row_bucket(ia, {f: "Brook"}) == "wild_reproduction"
    assert reg.row_bucket(ia, {f: "Brown"}) == "wild_reproduction"
    assert reg.row_bucket(ia, {f: "Rainbow"}) == "wild_reproduction"
    assert reg.row_bucket(ia, {f: "Brook, Brown"}) == "wild_reproduction"
    assert reg.row_bucket(ia, {f: "Brown, Rainb"}) == "wild_reproduction"
    # blank / absent -> stocked via default (put-and-take, no natural repro).
    assert reg.row_bucket(ia, {f: " "}) == "stocked"
    assert reg.row_bucket(ia, {f: ""}) == "stocked"
    assert reg.row_bucket(ia, {}) == "stocked"


def test_co_multi_layer_native_wild_sportfish_stocked():
    # CO SB181 aquatic units: native conservation / cutthroat crucial habitat /
    # gold medal -> wild; sportfish management -> stocked. Wild sublayers first
    # so wild wins on overlap.
    co = SOURCES["CO"]
    assert co["mode"] == "multi_layer"
    by_id = {l["id"]: l["class"] for l in co["layers"]}
    assert by_id[2] == "wild_reproduction"   # Native Species Conservation
    assert by_id[0] == "wild_reproduction"   # Cutthroat Crucial Habitat
    assert by_id[1] == "wild_reproduction"   # Gold Medal (premier tier)
    assert by_id[3] == "stocked"             # Sportfish Management
    assert co["layers"][-1]["class"] == "stocked"  # stocked listed last


def test_nv_lahontan_single_wild():
    nv = SOURCES["NV"]
    assert nv["mode"] == "single"
    assert reg.row_bucket(nv, {}) == "wild_reproduction"


def test_ca_heritage_wild_single_wild():
    ca = SOURCES["CA"]
    assert ca["mode"] == "single"
    assert reg.row_bucket(ca, {}) == "wild_reproduction"


def test_sc_trout_category_wild_cr_vs_stocked():
    # SC trout_category: w (wild) + cr (catch-and-release wild) -> wild;
    # dh (delayed harvest) / pt (put-take) / pg (put-grow) -> stocked.
    # Mirrors the NC precedent.
    sc = SOURCES["SC"]
    f = sc["field"]
    assert reg.row_bucket(sc, {f: "w"}) == "wild_reproduction"
    assert reg.row_bucket(sc, {f: "cr"}) == "wild_reproduction"
    assert reg.row_bucket(sc, {f: "dh"}) == "stocked"
    assert reg.row_bucket(sc, {f: "pt"}) == "stocked"
    assert reg.row_bucket(sc, {f: "pg"}) == "stocked"
    assert reg.row_bucket(sc, {f: ""}) is None
    assert reg.row_bucket(sc, {}) is None


def test_wy_and_ut_blue_ribbon_single_wild():
    # Western carve-out: Blue Ribbon premier-water tiers -> wild (whole layer).
    for st in ("WY", "UT"):
        s = SOURCES[st]
        assert s["mode"] == "single"
        assert reg.row_bucket(s, {}) == "wild_reproduction"


def test_row_tier_gold_and_ladders():
    # explicit gold + the class1/2/3 ladders
    assert reg.row_tier(SOURCES["WY"], {}) == "gold"
    assert reg.row_tier(SOURCES["UT"], {}) == "gold"
    assert reg.row_tier(SOURCES["MO"], {"AreaType": "Blue Ribbon"}) == "gold"
    assert reg.row_tier(SOURCES["MO"], {"AreaType": "Red Ribbon"}) == "class2"
    assert reg.row_tier(SOURCES["MO"], {"AreaType": "White Ribbon"}) == "class3"
    wi, wf = SOURCES["WI"], SOURCES["WI"]["fields"][0]
    assert [reg.row_tier(wi, {wf: c}) for c in ("Class I", "Class II", "Class III")] \
        == ["class1", "class2", "class3"]
    mi, mf = SOURCES["MI"], SOURCES["MI"]["field"]
    assert reg.row_tier(mi, {mf: "Type 1 Designated"}) == "class1"
    assert reg.row_tier(mi, {mf: "Type 2 Designated"}) == "class2"
    assert reg.row_tier(mi, {mf: "Type 4 Designated"}) == "class3"


def test_row_tier_regulation_states():
    assert reg.row_tier(SOURCES["NC"], {"WRC_Class": "Wild Trout Waters"}) == "class1"
    assert reg.row_tier(SOURCES["NC"], {"WRC_Class": "Hatchery Supported Trout Waters"}) == "class2"
    assert reg.row_tier(SOURCES["NY"], {"MGMTCAT": "Wild-Premier"}) == "class1"
    assert reg.row_tier(SOURCES["NY"], {"MGMTCAT": "Stocked"}) == "class3"
    assert reg.row_tier(SOURCES["SC"], {"trout_category": "w"}) == "class1"
    assert reg.row_tier(SOURCES["SC"], {"trout_category": "pt"}) == "class3"
    # CT WTMA (BY_LABEL: SOURCES['CT'] is the stocked entry, last-wins)
    wtma = BY_LABEL["CT (WTMA)"]
    assert reg.row_tier(wtma, {"MGMT_AREA": "X Wild Trout Management Area (Class 1)"}) == "class1"
    assert reg.row_tier(wtma, {"MGMT_AREA": "Trophy Trout Area"}) == "class3"  # tier_default


def test_row_tier_falls_back_from_class():
    # sources with no explicit tier spec derive tier from the trout_class
    assert reg.row_tier(SOURCES["VA"], {}) == "class2"   # wild_reproduction
    assert reg.row_tier(SOURCES["NJ"], {}) == "class3"   # stocked
    assert reg.row_tier(SOURCES["MD"], {}) == "class3"   # designated
    assert reg.layer_tier({"class": "class_a"}) == "class1"  # PA-style sublayer


def test_wild_and_native_flags():
    assert reg.class_is_wild("wild_reproduction") and reg.class_is_wild("class_a")
    assert not reg.class_is_wild("stocked")
    assert reg.is_native(SOURCES["NV"]) is True
    assert reg.is_native(SOURCES["VA"]) is False


def test_co_multilayer_tiers_and_native():
    co = SOURCES["CO"]
    by_id = {l["id"]: l for l in co["layers"]}
    assert reg.layer_tier(by_id[1]) == "gold"     # Gold Medal
    assert reg.layer_tier(by_id[2]) == "class2"   # Native Species Conservation
    assert reg.layer_tier(by_id[3]) == "class3"   # Sportfish (stocked)
    assert reg.is_native(co, by_id[0]) is True    # Cutthroat Crucial Habitat
    assert reg.is_native(co, by_id[1]) is False   # Gold Medal not native-flagged
    assert reg.is_native(co, by_id[3]) is False   # Sportfish


def test_refine_tier_size_ladder():
    r = reg.refine_tier
    # gold: DESIGNATED premier-wild (base class1) on a named river, order >= 4
    assert r("class1", True, "Penns Creek", 5) == "gold"
    assert r("class1", True, "Penns Creek", 4) == "gold"     # at the gold threshold
    assert r("class1", True, "Spring Creek", 3) == "class1"  # < 4 -> stays class1
    # size promotion: generic wild (base class2) on a named river, order >= 3
    assert r("class2", True, "Big Wild River", 3) == "class1"
    assert r("class2", True, "Big Wild River", 5) == "class1"  # promoted, NOT gold
    assert r("class2", True, "Small Brook", 2) == "class2"     # < 3 -> stays class2
    # guards: unnamed / not-wild / no-order / stocked tiers unchanged
    assert r("class2", True, None, 6) == "class2"           # unnamed
    assert r("class1", False, "Big River", 6) == "class1"   # not wild
    assert r("class2", True, "Big River", None) == "class2"  # no order
    assert r("class3", True, "Big River", 6) == "class3"    # stocked tier untouched
    assert r("gold", True, "Big River", 6) == "gold"        # already gold


def test_ebtjv_native_overlay_field_map():
    # Range-wide eastern brook trout native overlay (TU/EBTJV portfolio):
    # brook-trout-present catchments -> wild_reproduction + is_native; everything
    # else (no brook trout / non-native-only / species-unspecified) drops.
    e = SOURCES["EBTJV"]
    assert e["mode"] == "field_map" and e["field"] == "Trout_community"
    assert reg.is_native(e) is True
    for present in ("Allopatric", "Allopatric EBT", "Sympatric",
                    "Sympatric EBT & BNT", "Sympatric EBT & RBT",
                    "Sympatric EBT, BNT, & RBT"):
        assert reg.row_bucket(e, {"Trout_community": present}) == "wild_reproduction"
        # tier falls back from wild_reproduction -> class2 (generic wild)
        assert reg.row_tier(e, {"Trout_community": present}) == "class2"
    for absent in ("No brook trout", "No trout", "No trout documented",
                   "Brown trout only", "Rainbow trout only",
                   "Brown & rainbow trout only", "Wild trout"):
        assert reg.row_bucket(e, {"Trout_community": absent}) is None
    # missing field drops too (guards the build's classify-field check)
    assert reg.row_bucket(e, {}) is None
    assert reg.classify_fields(e) == ["Trout_community"]
    # EBTJV and the western cutthroat overlays trail the state sources, so every
    # state source keeps trout_class/tier precedence (is_native OR-merges).
    states = [s["state"] for s in ALL_SOURCES]
    i = states.index("EBTJV")
    assert all(s.get("native") for s in ALL_SOURCES[i:])


def test_western_cutthroat_native_overlays():
    # Occupied/conservation-population cutthroat layers -> whole layer is native
    # + self-sustaining wild (the cutthroat analog of NV Lahontan). Distinct,
    # non-colliding source codes so they don't shadow the WY/UT Blue Ribbon
    # tier-gold sources.
    for code in ("UTCT", "YCT", "RGCT", "BCT"):
        s = SOURCES[code]
        assert s["mode"] == "single"
        assert reg.row_bucket(s, {}) == "wild_reproduction"
        assert reg.is_native(s) is True
        assert reg.class_is_wild(reg.row_bucket(s, {}))
    # the pre-existing UT Blue Ribbon (tier gold) is not shadowed
    assert reg.row_tier(SOURCES["UT"], {}) == "gold"


def test_gila_and_redband_native_overlays():
    # Gila trout: field_map on Status2017 keeps current/restored native
    # populations, drops eliminated/potential reaches.
    g = SOURCES["GILA"]
    assert g["mode"] == "field_map" and reg.is_native(g) is True
    assert reg.row_bucket(g, {"Status2017": "Current population"}) == "wild_reproduction"
    assert reg.row_bucket(g, {"Status2017": "Recently restored"}) == "wild_reproduction"
    assert reg.row_bucket(g, {"Status2017": "Eliminated"}) is None
    assert reg.row_bucket(g, {"Status2017": "Potential Recovery Stream"}) is None
    assert reg.classify_fields(g) == ["Status2017"]


def test_psmfc_streamnet_native_overlays():
    # PSMFC StreamNet per-species distribution layers -> whole-layer native +
    # self-sustaining wild (Westslope cutthroat, bull trout char, redband).
    for code in ("WCT", "BULL", "RBT"):
        s = SOURCES[code]
        assert s["mode"] == "single"
        assert reg.row_bucket(s, {}) == "wild_reproduction"
        assert reg.is_native(s) is True
    # bull trout + redband exclude historical reaches server-side (current only)
    assert "Historical" in SOURCES["BULL"]["url"]
    assert "Historical" in SOURCES["RBT"]["url"]


def test_ct_is_two_ordered_sources_wild_first():
    ct = [s for s in ALL_SOURCES if s["state"] == "CT"]
    assert [s["label"] for s in ct] == ["CT (WTMA)", "CT (stocked)"]  # wild claims first
    assert ct[0]["mode"] == "field_prefix" and ct[0]["default"] == "stocked"
    assert ct[1]["mode"] == "single" and ct[1]["class"] == "stocked"
