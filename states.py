"""
State configuration for BlueLines.

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
