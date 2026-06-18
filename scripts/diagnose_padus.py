#!/usr/bin/env python3
"""
Diagnose why a known piece of public land is (or isn't) in the BlueLiner
public-lands layer, by querying PAD-US directly for the parcels around a
point and reporting -- per parcel -- the fields the build keys on plus
whether the OLD and NEW build rules would keep it.

This is the "verify before you trust the fix" companion to
build_public_lands.py: point it at a spot you KNOW is public (e.g. the
Gunpowder Falls State Park catch-and-release section below Prettyboy Dam)
and it tells you whether PAD-US codes it OA/RA/UK/XA, who manages it, and
whether each build rule emits it. That distinguishes the two failure
modes nationwide:

  * Pub_Access = UK on government land  -> the OLD build dropped it;
    the NEW build promotes it to Open Access.
  * thin riverside corridor             -> the OLD 1.7 km simplify
    collapsed it; the NEW topology-preserving simplify keeps it.

Runs in the DATA-BUILD / CI environment (open egress) -- the Claude Code
sandbox's allowlist blocks the USGS GIS hosts.

Dev dependencies (NOT in runtime requirements.txt):
    pip install httpx shapely

Examples:
    # Gunpowder Falls SP, below Prettyboy Dam (the missing state park)
    python scripts/diagnose_padus.py --lat 39.6126 --lon -76.6889

    # Torrey C. Brown Rail Trail (the corridor that paints green)
    python scripts/diagnose_padus.py --lat 39.5826 --lon -76.6605
"""

import argparse
import json
import os
import sys

# Reuse the SAME decision logic the build uses, so this script reports
# exactly what build_public_lands.py would emit -- not a re-implementation
# that could drift.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_public_lands as bpl  # noqa: E402

# PAD-US 4.0 Fee layer on the USGS GAP ArcGIS server. Overridable in case
# USGS bumps the service path (the gis-endpoint-verify workflow is the
# canonical place to re-confirm live URLs).
DEFAULT_URL = ("https://gis1.usgs.gov/arcgis/rest/services/padus4_0/"
               "Fee/MapServer/0/query")
UA = {"User-Agent": "Blueliner-databuild/1.0 (+https://blueliner.app)"}

# PAD-US source fields -> the names the build/decision logic expects.
FIELDS = ["Unit_Nm", "Loc_Nm", "Mang_Type", "Mang_Name", "Des_Tp",
          "Pub_Access", "State_Nm"]


def _envelope(lat: float, lon: float, half_deg: float) -> str:
    """A small lon/lat envelope around the point, as the ArcGIS geometry
    JSON for an intersects query."""
    return json.dumps({
        "xmin": lon - half_deg, "ymin": lat - half_deg,
        "xmax": lon + half_deg, "ymax": lat + half_deg,
        "spatialReference": {"wkid": 4326},
    })


def fetch_parcels(url: str, lat: float, lon: float, half_deg: float):
    """Return the intersecting PAD-US Fee features, geometry in EPSG:5070
    (metres) so width is a true ground distance."""
    import httpx
    params = {
        "where": "1=1",
        "geometry": _envelope(lat, lon, half_deg),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": ",".join(FIELDS),
        "outSR": "5070",          # CONUS Albers metres -> metric area/len
        "returnGeometry": "true",
        "f": "geojson",
    }
    with httpx.Client(timeout=60.0, headers=UA, follow_redirects=True) as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return (r.json() or {}).get("features", [])


def main() -> int:
    from shapely.geometry import shape

    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--half-deg", type=float, default=0.01,
                   help="half-size of the search envelope in degrees "
                        "(~1.1 km; default 0.01)")
    p.add_argument("--url", default=DEFAULT_URL)
    args = p.parse_args()

    try:
        feats = fetch_parcels(args.url, args.lat, args.lon, args.half_deg)
    except Exception as e:  # noqa: BLE001 -- diagnostic, surface anything
        print(f"ERROR querying PAD-US: {e}", file=sys.stderr)
        print("  (run from a host with open egress; the Claude Code "
              "sandbox blocks USGS GIS)", file=sys.stderr)
        return 1

    if not feats:
        print(f"No PAD-US Fee parcels intersect "
              f"{args.lat},{args.lon} (+/-{args.half_deg} deg).")
        print("  -> the land here is absent from PAD-US Fee entirely "
              "(check the Easement/Designation layers or the source data).")
        return 0

    print(f"{len(feats)} PAD-US Fee parcel(s) near {args.lat},{args.lon}:\n")
    for f in feats:
        a = f.get("properties") or {}
        geom = f.get("geometry")
        g = shape(geom) if geom else None
        width = bpl.corridor_width_m(g.area, g.length) if g else float("nan")
        corridor = g is not None and bpl.is_corridor(g.area, g.length)
        access = bpl.decide_access(
            a.get("Pub_Access"), a.get("Mang_Type"),
            a.get("Mang_Name"), a.get("Des_Tp"))
        old_kept = a.get("Pub_Access") in bpl.KEEP_PUB_ACCESS
        new_kept = access is not None and not corridor
        new_verdict = f"True (as {access!r})" if new_kept else "False"
        name = a.get("Unit_Nm") or a.get("Loc_Nm") or "(unnamed)"
        print(f"  • {name}")
        print(f"      Pub_Access={a.get('Pub_Access')!r}  "
              f"Mang_Type={a.get('Mang_Type')!r}  "
              f"Des_Tp={a.get('Des_Tp')!r}")
        print(f"      Mang_Name={a.get('Mang_Name')!r}")
        print(f"      est. width={width:.0f} m"
              f"{'  (corridor)' if corridor else ''}")
        print(f"      OLD build kept: {old_kept}   NEW build kept: {new_verdict}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
