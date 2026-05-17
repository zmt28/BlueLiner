"""
Insect hatch guidance for the mid-Atlantic.

Pure in-memory lookups (zero network). Hatch timing is resolved to a
sub-state zone via an approximate bounding box, then to the current month.
Zone boundaries are intentionally coarse and tunable -- they capture the
broad freestone / limestone-tailwater / piedmont differences that drive fly
selection, not exact watershed lines.

Each zone's chart entry:
    insect, common_name, months (start,end), peak (start,end),
    hook_sizes, time_of_day, patterns[]
Months are 1-12. A range with start > end wraps the year (e.g. midges 10->4).
"""

# Ordered: first zone whose bbox contains the point wins; else the fallback.
# bbox = (lat_min, lat_max, lon_min, lon_max)
HATCH_ZONES = [
    {
        "name": "Mountain Freestone",
        "bbox": (37.0, 39.95, -82.75, -78.90),
        "blurb": "High-elevation Allegheny / Ridge-and-Valley freestone -- "
                 "later, classic mayfly slate.",
        "chart": [
            {"insect": "Baetis", "common_name": "Blue-Winged Olive",
             "months": (3, 5), "peak": (3, 4), "hook_sizes": "18-22",
             "time_of_day": "Midday, best on overcast/drizzle",
             "patterns": ["Parachute BWO", "RS2", "Pheasant Tail"]},
            {"insect": "Epeorus pleuralis", "common_name": "Quill Gordon",
             "months": (4, 5), "peak": (4, 4), "hook_sizes": "12-14",
             "time_of_day": "Early afternoon",
             "patterns": ["Quill Gordon dry", "Hare's Ear nymph"]},
            {"insect": "Ephemerella subvaria", "common_name": "Hendrickson",
             "months": (4, 5), "peak": (4, 5), "hook_sizes": "12-14",
             "time_of_day": "Afternoon",
             "patterns": ["Hendrickson dry", "Red Quill", "Pheasant Tail"]},
            {"insect": "Maccaffertium vicarium", "common_name": "March Brown",
             "months": (5, 6), "peak": (5, 6), "hook_sizes": "10-12",
             "time_of_day": "Afternoon into evening",
             "patterns": ["March Brown dry", "Hare's Ear"]},
            {"insect": "Ephemera guttulata", "common_name": "Green Drake",
             "months": (5, 6), "peak": (5, 6), "hook_sizes": "8-10",
             "time_of_day": "Evening",
             "patterns": ["Green Drake dry", "Coffin Fly", "Drake nymph"]},
            {"insect": "Brachycentrus", "common_name": "Caddis",
             "months": (4, 10), "peak": (5, 6), "hook_sizes": "14-18",
             "time_of_day": "Evening",
             "patterns": ["Elk Hair Caddis", "X-Caddis", "LaFontaine Pupa"]},
            {"insect": "Isonychia", "common_name": "Slate Drake",
             "months": (6, 10), "peak": (6, 9), "hook_sizes": "10-12",
             "time_of_day": "Evening, riffles",
             "patterns": ["Isonychia dry", "Zug Bug", "Leadwing Coachman"]},
            {"insect": "Terrestrials", "common_name": "Ants / Beetles / Hoppers",
             "months": (6, 10), "peak": (7, 9), "hook_sizes": "10-18",
             "time_of_day": "Midday, breezy banks",
             "patterns": ["Foam Beetle", "Parachute Ant", "Hopper"]},
            {"insect": "Chironomidae", "common_name": "Midges",
             "months": (10, 4), "peak": (12, 2), "hook_sizes": "20-26",
             "time_of_day": "Midday, slow water",
             "patterns": ["Zebra Midge", "Griffith's Gnat", "WD-40"]},
        ],
    },
    {
        "name": "Limestone & Tailwater",
        "bbox": (39.20, 39.80, -78.05, -76.40),
        "blurb": "Limestone-valley spring creeks and bottom-release "
                 "tailwaters (e.g. the Gunpowder) -- stable temps, prolific "
                 "Sulphurs, year-round BWO and midges.",
        "chart": [
            {"insect": "Chironomidae", "common_name": "Midges",
             "months": (1, 12), "peak": (11, 3), "hook_sizes": "20-26",
             "time_of_day": "All day; the winter staple",
             "patterns": ["Zebra Midge", "Griffith's Gnat", "Black Beauty"]},
            {"insect": "Baetis", "common_name": "Blue-Winged Olive",
             "months": (1, 12), "peak": (3, 4), "hook_sizes": "18-24",
             "time_of_day": "Midday, heaviest on gray days",
             "patterns": ["Sparkle Dun BWO", "RS2", "Pheasant Tail"]},
            {"insect": "Ephemerella invaria", "common_name": "Sulphur",
             "months": (5, 7), "peak": (5, 6), "hook_sizes": "14-18",
             "time_of_day": "Evening spinner fall",
             "patterns": ["Sulphur Comparadun", "Sulphur Spinner",
                          "Pheasant Tail"]},
            {"insect": "Brachycentrus", "common_name": "Grannom Caddis",
             "months": (4, 9), "peak": (4, 5), "hook_sizes": "14-18",
             "time_of_day": "Afternoon and evening",
             "patterns": ["Elk Hair Caddis", "Caddis Pupa", "X-Caddis"]},
            {"insect": "Tricorythodes", "common_name": "Trico",
             "months": (7, 9), "peak": (8, 9), "hook_sizes": "20-24",
             "time_of_day": "Morning spinner fall",
             "patterns": ["Trico Spinner", "Trico Dun", "WD-40"]},
            {"insect": "Terrestrials", "common_name": "Ants / Beetles / Hoppers",
             "months": (6, 10), "peak": (7, 9), "hook_sizes": "12-18",
             "time_of_day": "Midday along grassy banks",
             "patterns": ["Foam Beetle", "Parachute Ant", "Chubby Chernobyl"]},
        ],
    },
    {
        "name": "Blue Ridge / Piedmont",
        "bbox": (37.0, 39.70, -78.90, -76.40),
        "blurb": "Lower-elevation eastern foothills -- the freestone slate "
                 "shifted roughly two weeks earlier than the mountains.",
        "chart": [
            {"insect": "Baetis", "common_name": "Blue-Winged Olive",
             "months": (2, 4), "peak": (3, 3), "hook_sizes": "18-22",
             "time_of_day": "Midday, overcast best",
             "patterns": ["Parachute BWO", "RS2", "Pheasant Tail"]},
            {"insect": "Ephemerella subvaria", "common_name": "Hendrickson",
             "months": (3, 4), "peak": (4, 4), "hook_sizes": "12-14",
             "time_of_day": "Afternoon",
             "patterns": ["Hendrickson dry", "Red Quill"]},
            {"insect": "Maccaffertium vicarium", "common_name": "March Brown",
             "months": (4, 6), "peak": (5, 5), "hook_sizes": "10-12",
             "time_of_day": "Afternoon into evening",
             "patterns": ["March Brown dry", "Hare's Ear"]},
            {"insect": "Ephemerella invaria", "common_name": "Sulphur",
             "months": (4, 6), "peak": (5, 5), "hook_sizes": "14-18",
             "time_of_day": "Evening",
             "patterns": ["Sulphur Comparadun", "Sulphur Spinner"]},
            {"insect": "Brachycentrus", "common_name": "Caddis",
             "months": (4, 10), "peak": (4, 6), "hook_sizes": "14-18",
             "time_of_day": "Evening",
             "patterns": ["Elk Hair Caddis", "X-Caddis"]},
            {"insect": "Terrestrials", "common_name": "Ants / Beetles / Hoppers",
             "months": (5, 10), "peak": (7, 9), "hook_sizes": "10-18",
             "time_of_day": "Midday, breezy banks",
             "patterns": ["Foam Beetle", "Parachute Ant", "Hopper"]},
            {"insect": "Chironomidae", "common_name": "Midges",
             "months": (10, 4), "peak": (12, 2), "hook_sizes": "20-26",
             "time_of_day": "Midday, slow water",
             "patterns": ["Zebra Midge", "Griffith's Gnat"]},
        ],
    },
]

FALLBACK_ZONE = {
    "name": "Mid-Atlantic (general)",
    "bbox": None,
    "blurb": "General mid-Atlantic trout slate.",
    "chart": [
        {"insect": "Baetis", "common_name": "Blue-Winged Olive",
         "months": (10, 5), "peak": (3, 4), "hook_sizes": "18-22",
         "time_of_day": "Midday, overcast best",
         "patterns": ["Parachute BWO", "RS2", "Pheasant Tail"]},
        {"insect": "Ephemerella", "common_name": "Sulphur",
         "months": (5, 7), "peak": (5, 6), "hook_sizes": "14-18",
         "time_of_day": "Evening",
         "patterns": ["Sulphur Comparadun", "Sulphur Spinner"]},
        {"insect": "Brachycentrus", "common_name": "Caddis",
         "months": (4, 10), "peak": (5, 6), "hook_sizes": "14-18",
         "time_of_day": "Afternoon and evening",
         "patterns": ["Elk Hair Caddis", "X-Caddis"]},
        {"insect": "Terrestrials", "common_name": "Ants / Beetles / Hoppers",
         "months": (6, 10), "peak": (7, 9), "hook_sizes": "10-18",
         "time_of_day": "Midday",
         "patterns": ["Foam Beetle", "Parachute Ant", "Hopper"]},
        {"insect": "Chironomidae", "common_name": "Midges",
         "months": (1, 12), "peak": (12, 2), "hook_sizes": "20-26",
         "time_of_day": "Midday, slow water",
         "patterns": ["Zebra Midge", "Griffith's Gnat"]},
    ],
}


def _in_range(month: int, start: int, end: int) -> bool:
    if start <= end:
        return start <= month <= end
    return month >= start or month <= end


def zone_for(lat: float, lon: float) -> dict:
    """First zone whose bbox contains the point, else the general fallback."""
    for zone in HATCH_ZONES:
        lat_min, lat_max, lon_min, lon_max = zone["bbox"]
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return zone
    return FALLBACK_ZONE


def active_hatches(zone: dict, month: int) -> list[dict]:
    """Chart entries active in `month`, ones currently peaking listed first."""
    active = [e for e in zone["chart"]
              if _in_range(month, e["months"][0], e["months"][1])]
    active.sort(key=lambda e: (
        0 if _in_range(month, e["peak"][0], e["peak"][1]) else 1,
        e["months"][0],
    ))
    return active


def all_insect_names() -> list[str]:
    """Union of common names across every zone (for the client filter)."""
    names: list[str] = []
    for zone in HATCH_ZONES + [FALLBACK_ZONE]:
        for entry in zone["chart"]:
            if entry["common_name"] not in names:
                names.append(entry["common_name"])
    return sorted(names)
