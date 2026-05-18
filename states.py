"""
State configuration for BlueLines.

Each supported state maps to its USGS state code (used to query the USGS
NWIS API) plus display name and a map center. To add a state, add an entry.

(TIGER/Line waterway shapefiles and their county FIPS lists were removed
along with the waterway overlay -- the basemap renders water, and the
overlay's per-county loading didn't scale and was memory-heavy.)
"""

STATES = {
    "MD": {"name": "Maryland", "usgs_code": "md", "center": [38.9784, -76.4922]},
    "VA": {"name": "Virginia", "usgs_code": "va", "center": [37.4316, -78.6569]},
    "WV": {"name": "West Virginia", "usgs_code": "wv", "center": [38.5976, -80.4549]},
}
