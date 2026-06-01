"""
State configuration for Blueliner.

Every U.S. state (plus DC) -> its USGS NWIS state code (lowercase postal
abbreviation, used to query the USGS API) plus a display name and an
approximate map center. USGS NWIS covers all states, so gauges + conditions
are national. Trout/stocking/hatch data are still mid-Atlantic-focused and
expand progressively.
"""

STATES = {
    "AL": {"name": "Alabama", "usgs_code": "al", "center": [32.8, -86.8]},
    "AK": {"name": "Alaska", "usgs_code": "ak", "center": [64.0, -152.0]},
    "AZ": {"name": "Arizona", "usgs_code": "az", "center": [34.3, -111.7]},
    "AR": {"name": "Arkansas", "usgs_code": "ar", "center": [34.8, -92.4]},
    "CA": {"name": "California", "usgs_code": "ca", "center": [37.2, -119.3]},
    "CO": {"name": "Colorado", "usgs_code": "co", "center": [39.0, -105.5]},
    "CT": {"name": "Connecticut", "usgs_code": "ct", "center": [41.6, -72.7]},
    "DE": {"name": "Delaware", "usgs_code": "de", "center": [39.0, -75.5]},
    "DC": {"name": "District of Columbia", "usgs_code": "dc", "center": [38.9, -77.0]},
    "FL": {"name": "Florida", "usgs_code": "fl", "center": [28.6, -82.4]},
    "GA": {"name": "Georgia", "usgs_code": "ga", "center": [32.6, -83.4]},
    "HI": {"name": "Hawaii", "usgs_code": "hi", "center": [20.3, -156.4]},
    "ID": {"name": "Idaho", "usgs_code": "id", "center": [44.2, -114.5]},
    "IL": {"name": "Illinois", "usgs_code": "il", "center": [40.0, -89.2]},
    "IN": {"name": "Indiana", "usgs_code": "in", "center": [39.9, -86.3]},
    "IA": {"name": "Iowa", "usgs_code": "ia", "center": [42.0, -93.5]},
    "KS": {"name": "Kansas", "usgs_code": "ks", "center": [38.5, -98.4]},
    "KY": {"name": "Kentucky", "usgs_code": "ky", "center": [37.5, -85.3]},
    "LA": {"name": "Louisiana", "usgs_code": "la", "center": [31.0, -92.0]},
    "ME": {"name": "Maine", "usgs_code": "me", "center": [45.4, -69.2]},
    "MD": {"name": "Maryland", "usgs_code": "md", "center": [38.9784, -76.4922]},
    "MA": {"name": "Massachusetts", "usgs_code": "ma", "center": [42.3, -71.8]},
    "MI": {"name": "Michigan", "usgs_code": "mi", "center": [44.3, -85.4]},
    "MN": {"name": "Minnesota", "usgs_code": "mn", "center": [46.3, -94.3]},
    "MS": {"name": "Mississippi", "usgs_code": "ms", "center": [32.7, -89.7]},
    "MO": {"name": "Missouri", "usgs_code": "mo", "center": [38.5, -92.5]},
    "MT": {"name": "Montana", "usgs_code": "mt", "center": [47.0, -109.6]},
    "NE": {"name": "Nebraska", "usgs_code": "ne", "center": [41.5, -99.8]},
    "NV": {"name": "Nevada", "usgs_code": "nv", "center": [39.3, -116.6]},
    "NH": {"name": "New Hampshire", "usgs_code": "nh", "center": [43.7, -71.6]},
    "NJ": {"name": "New Jersey", "usgs_code": "nj", "center": [40.1, -74.7]},
    "NM": {"name": "New Mexico", "usgs_code": "nm", "center": [34.4, -106.1]},
    "NY": {"name": "New York", "usgs_code": "ny", "center": [42.9, -75.5]},
    "NC": {"name": "North Carolina", "usgs_code": "nc", "center": [35.6, -79.4]},
    "ND": {"name": "North Dakota", "usgs_code": "nd", "center": [47.5, -100.3]},
    "OH": {"name": "Ohio", "usgs_code": "oh", "center": [40.3, -82.8]},
    "OK": {"name": "Oklahoma", "usgs_code": "ok", "center": [35.6, -97.5]},
    "OR": {"name": "Oregon", "usgs_code": "or", "center": [44.0, -120.5]},
    "PA": {"name": "Pennsylvania", "usgs_code": "pa", "center": [40.9, -77.8]},
    "RI": {"name": "Rhode Island", "usgs_code": "ri", "center": [41.7, -71.6]},
    "SC": {"name": "South Carolina", "usgs_code": "sc", "center": [33.9, -80.9]},
    "SD": {"name": "South Dakota", "usgs_code": "sd", "center": [44.4, -100.2]},
    "TN": {"name": "Tennessee", "usgs_code": "tn", "center": [35.9, -86.4]},
    "TX": {"name": "Texas", "usgs_code": "tx", "center": [31.5, -99.3]},
    "UT": {"name": "Utah", "usgs_code": "ut", "center": [39.3, -111.7]},
    "VT": {"name": "Vermont", "usgs_code": "vt", "center": [44.0, -72.7]},
    "VA": {"name": "Virginia", "usgs_code": "va", "center": [37.4316, -78.6569]},
    "WA": {"name": "Washington", "usgs_code": "wa", "center": [47.4, -120.5]},
    "WV": {"name": "West Virginia", "usgs_code": "wv", "center": [38.5976, -80.4549]},
    "WI": {"name": "Wisconsin", "usgs_code": "wi", "center": [44.6, -89.9]},
    "WY": {"name": "Wyoming", "usgs_code": "wy", "center": [43.0, -107.6]},
}

# Approximate per-state bounding boxes: (lat_min, lat_max, lon_min, lon_max).
# Used only to decide which states' trout/stocking data to consult for a
# given map viewport -- over-inclusive is fine (consults a harmless extra
# neighbor); precision isn't needed.
STATE_BBOX = {
    "AL": (30.1, 35.1, -88.5, -84.9), "AK": (51.0, 71.5, -179.9, -129.0),
    "AZ": (31.3, 37.1, -114.9, -109.0), "AR": (33.0, 36.6, -94.7, -89.6),
    "CA": (32.5, 42.1, -124.5, -114.1), "CO": (36.9, 41.1, -109.1, -102.0),
    "CT": (40.9, 42.1, -73.8, -71.7), "DE": (38.4, 39.9, -75.8, -75.0),
    "DC": (38.79, 39.0, -77.13, -76.9), "FL": (24.4, 31.1, -87.7, -79.9),
    "GA": (30.3, 35.1, -85.7, -80.8), "HI": (18.8, 22.3, -160.3, -154.7),
    "ID": (41.9, 49.1, -117.3, -111.0), "IL": (36.9, 42.6, -91.6, -87.0),
    "IN": (37.7, 41.8, -88.1, -84.7), "IA": (40.3, 43.6, -96.7, -90.1),
    "KS": (36.9, 40.1, -102.1, -94.5), "KY": (36.4, 39.2, -89.6, -81.9),
    "LA": (28.9, 33.1, -94.1, -88.8), "ME": (42.9, 47.6, -71.2, -66.9),
    "MD": (37.8, 39.8, -79.6, -75.0), "MA": (41.2, 42.9, -73.6, -69.9),
    "MI": (41.6, 48.4, -90.5, -82.3), "MN": (43.4, 49.5, -97.3, -89.4),
    "MS": (30.1, 35.1, -91.7, -88.0), "MO": (35.9, 40.7, -95.9, -89.0),
    "MT": (44.3, 49.1, -116.1, -104.0), "NE": (39.9, 43.1, -104.1, -95.3),
    "NV": (35.0, 42.1, -120.1, -114.0), "NH": (42.6, 45.4, -72.6, -70.6),
    "NJ": (38.9, 41.4, -75.6, -73.9), "NM": (31.3, 37.1, -109.1, -103.0),
    "NY": (40.4, 45.1, -79.8, -71.8), "NC": (33.8, 36.6, -84.4, -75.4),
    "ND": (45.9, 49.1, -104.1, -96.5), "OH": (38.4, 42.0, -84.9, -80.5),
    "OK": (33.6, 37.1, -103.1, -94.4), "OR": (41.9, 46.3, -124.6, -116.4),
    "PA": (39.7, 42.3, -80.6, -74.7), "RI": (41.1, 42.1, -71.9, -71.1),
    "SC": (32.0, 35.3, -83.4, -78.5), "SD": (42.4, 45.95, -104.1, -96.4),
    "TN": (34.9, 36.7, -90.4, -81.6), "TX": (25.8, 36.6, -106.7, -93.5),
    "UT": (36.9, 42.1, -114.1, -109.0), "VT": (42.7, 45.1, -73.5, -71.5),
    "VA": (36.5, 39.5, -83.7, -75.2), "WA": (45.5, 49.1, -124.8, -116.9),
    "WV": (37.1, 40.7, -82.7, -77.7), "WI": (42.4, 47.1, -92.9, -86.8),
    "WY": (40.9, 45.1, -111.1, -104.0),
}


def states_in_bbox(west: float, south: float, east: float,
                    north: float) -> list[str]:
    """State codes whose bounding box intersects the given map viewport."""
    out = []
    for code, (la0, la1, lo0, lo1) in STATE_BBOX.items():
        if east < lo0 or west > lo1 or north < la0 or south > la1:
            continue
        out.append(code)
    return out


def point_in_state(lat: float, lon: float) -> str | None:
    """The state whose bounding box contains the point, or None. Boxes
    overlap at the edges, so on a tie the smallest-area box wins -- the
    tighter fit is the better guess for a point near a shared border."""
    best: str | None = None
    best_area = float("inf")
    for code, (la0, la1, lo0, lo1) in STATE_BBOX.items():
        if la0 <= lat <= la1 and lo0 <= lon <= lo1:
            area = (la1 - la0) * (lo1 - lo0)
            if area < best_area:
                best_area, best = area, code
    return best
