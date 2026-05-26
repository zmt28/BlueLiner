"""
Insect hatch guidance for trout waters across the lower 48.

Pure in-memory lookups (zero network). Resolution order:
    1. Per-river override (data/hatches/overrides.json, keyed by
       lowercased river name -- preferred for famous waters whose
       hatches diverge from their surrounding region).
    2. Geographic zone via approximate bounding box.
    3. Fallback to a continental-US generic chart.

Zone boundaries are intentionally coarse and tunable -- they capture
broad regional patterns that drive fly selection (Mountain West
salmonflies, Driftless Tricos, Sierra PMDs, Smokies early Quill
Gordons, etc.), not exact watershed lines. The mid-Atlantic zones are
the most fine-grained because that's where the per-river overrides
are heaviest; other regions are single-zone for now and split as
overrides land.

Each chart entry:
    insect, common_name, months (start,end), peak (start,end),
    hook_sizes, time_of_day, patterns[]
Months are 1-12. A range with start > end wraps the year (e.g. midges 10->4).
"""

import json
import os

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
    # ---------- National regional zones (lower 48 outside mid-Atlantic) ----------
    {
        "name": "Northeast / New England",
        "bbox": (41.0, 47.5, -73.5, -67.0),
        "blurb": "Catskills / Adirondacks / Greens / Whites freestone -- "
                 "classic eastern mayfly progression, ~2 weeks later "
                 "than mid-Atlantic, with Maine Hex on still water.",
        "chart": [
            {"insect": "Baetis", "common_name": "Blue-Winged Olive",
             "months": (4, 11), "peak": (4, 5), "hook_sizes": "18-22",
             "time_of_day": "Midday, overcast best",
             "patterns": ["Parachute BWO", "RS2", "Pheasant Tail"]},
            {"insect": "Ephemerella subvaria", "common_name": "Hendrickson",
             "months": (4, 6), "peak": (5, 5), "hook_sizes": "12-14",
             "time_of_day": "Afternoon",
             "patterns": ["Hendrickson dry", "Red Quill"]},
            {"insect": "Maccaffertium vicarium", "common_name": "March Brown",
             "months": (5, 7), "peak": (6, 6), "hook_sizes": "10-12",
             "time_of_day": "Afternoon into evening",
             "patterns": ["March Brown dry", "Hare's Ear"]},
            {"insect": "Ephemerella invaria", "common_name": "Sulphur",
             "months": (5, 7), "peak": (6, 6), "hook_sizes": "14-18",
             "time_of_day": "Evening",
             "patterns": ["Sulphur Comparadun", "Sulphur Spinner"]},
            {"insect": "Stenacron / Stenonema", "common_name": "Cahills",
             "months": (6, 8), "peak": (6, 7), "hook_sizes": "12-14",
             "time_of_day": "Evening",
             "patterns": ["Light Cahill", "Dark Cahill"]},
            {"insect": "Hexagenia limbata", "common_name": "Hex",
             "months": (6, 7), "peak": (6, 7), "hook_sizes": "6-8",
             "time_of_day": "Late evening / dark, silt-bottom pools",
             "patterns": ["Hex Spinner", "Hex Nymph"]},
            {"insect": "Brachycentrus", "common_name": "Caddis",
             "months": (5, 9), "peak": (6, 7), "hook_sizes": "14-18",
             "time_of_day": "Evening",
             "patterns": ["Elk Hair Caddis", "X-Caddis"]},
            {"insect": "Terrestrials", "common_name": "Ants / Beetles / Hoppers",
             "months": (7, 10), "peak": (8, 9), "hook_sizes": "10-18",
             "time_of_day": "Midday, breezy banks",
             "patterns": ["Foam Beetle", "Parachute Ant", "Hopper"]},
            {"insect": "Chironomidae", "common_name": "Midges",
             "months": (11, 4), "peak": (12, 2), "hook_sizes": "20-26",
             "time_of_day": "Midday, slow water",
             "patterns": ["Zebra Midge", "Griffith's Gnat"]},
        ],
    },
    {
        "name": "Southern Appalachians",
        "bbox": (33.5, 36.99, -85.0, -81.5),
        "blurb": "Smokies / Pisgah / Cohutta freestone -- the earliest "
                 "spring slate east of the Mississippi, with Quill Gordon "
                 "kicking the season off in March.",
        "chart": [
            {"insect": "Baetis", "common_name": "Blue-Winged Olive",
             "months": (2, 4), "peak": (3, 3), "hook_sizes": "18-22",
             "time_of_day": "Midday, overcast best",
             "patterns": ["Parachute BWO", "RS2"]},
            {"insect": "Epeorus pleuralis", "common_name": "Quill Gordon",
             "months": (3, 4), "peak": (3, 4), "hook_sizes": "12-14",
             "time_of_day": "Early afternoon, first warm days",
             "patterns": ["Quill Gordon dry", "Hare's Ear"]},
            {"insect": "Ephemerella subvaria", "common_name": "Hendrickson",
             "months": (3, 5), "peak": (4, 4), "hook_sizes": "12-14",
             "time_of_day": "Afternoon",
             "patterns": ["Hendrickson dry", "Red Quill"]},
            {"insect": "Ephemerella invaria", "common_name": "Sulphur",
             "months": (4, 6), "peak": (5, 5), "hook_sizes": "14-18",
             "time_of_day": "Evening",
             "patterns": ["Sulphur Comparadun", "Pheasant Tail"]},
            {"insect": "Isoperla / Suwallia", "common_name": "Yellow Sally",
             "months": (5, 7), "peak": (6, 6), "hook_sizes": "14-16",
             "time_of_day": "Afternoon, runs and riffles",
             "patterns": ["Yellow Stimulator", "Yellow Sally dry"]},
            {"insect": "Isonychia", "common_name": "Slate Drake",
             "months": (5, 10), "peak": (6, 9), "hook_sizes": "10-12",
             "time_of_day": "Evening, riffles",
             "patterns": ["Isonychia dry", "Zug Bug"]},
            {"insect": "Geometridae", "common_name": "Inchworms",
             "months": (5, 7), "peak": (6, 6), "hook_sizes": "12-14",
             "time_of_day": "All day, overhanging trees",
             "patterns": ["Green Weenie", "Foam Inchworm"]},
            {"insect": "Terrestrials", "common_name": "Ants / Beetles / Hoppers",
             "months": (6, 10), "peak": (7, 9), "hook_sizes": "10-18",
             "time_of_day": "Midday, sunny banks",
             "patterns": ["Foam Beetle", "Parachute Ant", "Hopper"]},
            {"insect": "Chironomidae", "common_name": "Midges",
             "months": (11, 3), "peak": (12, 2), "hook_sizes": "20-26",
             "time_of_day": "Midday, slow water",
             "patterns": ["Zebra Midge", "Griffith's Gnat"]},
        ],
    },
    {
        "name": "Driftless / Upper Midwest",
        "bbox": (42.0, 46.5, -94.0, -89.5),
        "blurb": "Wisconsin / Minnesota / NE Iowa limestone spring "
                 "creeks -- year-round BWO, summer Trico clouds, and the "
                 "Hex hatch on bigger rivers in late June.",
        "chart": [
            {"insect": "Baetis", "common_name": "Blue-Winged Olive",
             "months": (1, 12), "peak": (3, 4), "hook_sizes": "18-24",
             "time_of_day": "Midday, heaviest on gray days",
             "patterns": ["Sparkle Dun BWO", "RS2", "Pheasant Tail"]},
            {"insect": "Scuds / Sowbugs", "common_name": "Scuds / Sowbugs",
             "months": (1, 12), "peak": (1, 12), "hook_sizes": "14-18",
             "time_of_day": "All day, watercress mats",
             "patterns": ["Pink Scud", "Sowbug", "Czech Nymph"]},
            {"insect": "Ephemerella invaria", "common_name": "Sulphur",
             "months": (5, 7), "peak": (5, 6), "hook_sizes": "14-18",
             "time_of_day": "Evening",
             "patterns": ["Sulphur Comparadun", "Pheasant Tail"]},
            {"insect": "Hexagenia limbata", "common_name": "Hex",
             "months": (6, 7), "peak": (6, 7), "hook_sizes": "6-8",
             "time_of_day": "Late evening into dark",
             "patterns": ["Hex Spinner", "Hex Nymph"]},
            {"insect": "Tricorythodes", "common_name": "Trico",
             "months": (7, 10), "peak": (8, 9), "hook_sizes": "20-24",
             "time_of_day": "Morning spinner fall (can be huge)",
             "patterns": ["Trico Spinner", "Trico Dun", "WD-40"]},
            {"insect": "Brachycentrus / Hydropsyche", "common_name": "Caddis",
             "months": (4, 9), "peak": (5, 6), "hook_sizes": "14-18",
             "time_of_day": "Afternoon and evening",
             "patterns": ["Elk Hair Caddis", "X-Caddis", "Caddis Pupa"]},
            {"insect": "Terrestrials", "common_name": "Ants / Beetles / Hoppers",
             "months": (6, 10), "peak": (7, 9), "hook_sizes": "10-18",
             "time_of_day": "Midday, grassy banks",
             "patterns": ["Foam Beetle", "Parachute Ant", "Hopper"]},
            {"insect": "Chironomidae", "common_name": "Midges",
             "months": (1, 12), "peak": (11, 3), "hook_sizes": "20-26",
             "time_of_day": "All day; winter staple",
             "patterns": ["Zebra Midge", "Griffith's Gnat", "Black Beauty"]},
        ],
    },
    {
        "name": "Great Lakes",
        "bbox": (43.0, 47.0, -89.0, -82.0),
        "blurb": "Michigan / Wisconsin / Ontario tribs -- resident "
                 "trout in summer, steelhead and salmon runs the rest "
                 "of the year. AuSable Hex in late June is the big "
                 "moment for dry-fly anglers.",
        "chart": [
            {"insect": "Baetis", "common_name": "Blue-Winged Olive",
             "months": (3, 5), "peak": (4, 4), "hook_sizes": "18-22",
             "time_of_day": "Midday",
             "patterns": ["Parachute BWO", "RS2"]},
            {"insect": "Ephemera simulans", "common_name": "Brown Drake",
             "months": (5, 6), "peak": (6, 6), "hook_sizes": "8-10",
             "time_of_day": "Evening",
             "patterns": ["Brown Drake dry", "Drake Spinner"]},
            {"insect": "Hexagenia limbata", "common_name": "Hex",
             "months": (6, 7), "peak": (6, 7), "hook_sizes": "4-8",
             "time_of_day": "Late evening into dark",
             "patterns": ["Hex Spinner", "Hex Dun", "Hex Nymph"]},
            {"insect": "Tricorythodes", "common_name": "Trico",
             "months": (7, 10), "peak": (8, 9), "hook_sizes": "20-24",
             "time_of_day": "Morning spinner fall",
             "patterns": ["Trico Spinner", "Trico Dun"]},
            {"insect": "Brachycentrus / Hydropsyche", "common_name": "Caddis",
             "months": (5, 9), "peak": (6, 7), "hook_sizes": "14-18",
             "time_of_day": "Evening",
             "patterns": ["Elk Hair Caddis", "X-Caddis"]},
            {"insect": "Steelhead nymphs",
             "common_name": "Steelhead nymphs / eggs",
             "months": (10, 4), "peak": (3, 4), "hook_sizes": "6-12",
             "time_of_day": "All day, deep runs",
             "patterns": ["Sucker Spawn", "Egg Pattern", "Stonefly Nymph"]},
            {"insect": "Chironomidae", "common_name": "Midges",
             "months": (11, 3), "peak": (12, 2), "hook_sizes": "20-26",
             "time_of_day": "Midday",
             "patterns": ["Zebra Midge", "Griffith's Gnat"]},
        ],
    },
    {
        "name": "Northern Rockies",
        "bbox": (44.0, 49.0, -116.0, -104.0),
        "blurb": "Montana / Yellowstone / north Idaho / NW Wyoming -- "
                 "big-bug country. Salmonflies, Skwala, Drakes, Golden "
                 "Stones. Cutthroat / brown / rainbow / hybrid mix.",
        "chart": [
            {"insect": "Skwala parallela", "common_name": "Skwala Stone",
             "months": (3, 4), "peak": (3, 4), "hook_sizes": "8-10",
             "time_of_day": "Afternoon, banks",
             "patterns": ["Skwala dry", "Stonefly Nymph"]},
            {"insect": "Baetis", "common_name": "Blue-Winged Olive",
             "months": (3, 5), "peak": (4, 4), "hook_sizes": "18-22",
             "time_of_day": "Midday, overcast",
             "patterns": ["Parachute BWO", "RS2"]},
            {"insect": "Pteronarcys californica", "common_name": "Salmonfly",
             "months": (5, 7), "peak": (6, 6), "hook_sizes": "2-6",
             "time_of_day": "Afternoon, banks. The big bug.",
             "patterns": ["Chubby Salmonfly", "Stonefly Nymph", "Pat's Rubberlegs"]},
            {"insect": "Hesperoperla pacifica", "common_name": "Golden Stone",
             "months": (6, 7), "peak": (6, 7), "hook_sizes": "8-10",
             "time_of_day": "Afternoon, banks",
             "patterns": ["Yellow Stimulator", "Pat's Rubberlegs"]},
            {"insect": "Ephemerella excrucians", "common_name": "PMD",
             "months": (6, 9), "peak": (7, 7), "hook_sizes": "14-18",
             "time_of_day": "Late morning into afternoon",
             "patterns": ["PMD Comparadun", "Sparkle Dun", "Pheasant Tail"]},
            {"insect": "Drunella grandis / flavilinea", "common_name": "Drakes",
             "months": (6, 8), "peak": (6, 7), "hook_sizes": "10-14",
             "time_of_day": "Afternoon",
             "patterns": ["Green Drake dry", "Flav dry"]},
            {"insect": "Brachycentrus / Hydropsyche", "common_name": "Caddis",
             "months": (5, 9), "peak": (7, 7), "hook_sizes": "14-18",
             "time_of_day": "Evening",
             "patterns": ["Elk Hair Caddis", "X-Caddis"]},
            {"insect": "Melanoplus", "common_name": "Hoppers",
             "months": (7, 10), "peak": (8, 9), "hook_sizes": "8-12",
             "time_of_day": "Midday, breezy banks",
             "patterns": ["Chubby Chernobyl", "Morrish Hopper"]},
            {"insect": "Tricorythodes", "common_name": "Trico",
             "months": (8, 9), "peak": (8, 9), "hook_sizes": "20-22",
             "time_of_day": "Morning",
             "patterns": ["Trico Spinner"]},
            {"insect": "Chironomidae", "common_name": "Midges",
             "months": (1, 12), "peak": (11, 3), "hook_sizes": "20-26",
             "time_of_day": "Midday, slow water",
             "patterns": ["Zebra Midge", "Griffith's Gnat"]},
        ],
    },
    {
        "name": "Southern Rockies / Intermountain",
        "bbox": (34.5, 44.0, -113.0, -104.0),
        "blurb": "Colorado / NM north / Utah / SW Wyoming -- high-elevation "
                 "freestones plus mysis-fed tailwaters (San Juan, Frying "
                 "Pan, Taylor). BWO, caddis, hopper-dropper country.",
        "chart": [
            {"insect": "Baetis", "common_name": "Blue-Winged Olive",
             "months": (3, 11), "peak": (4, 5), "hook_sizes": "18-22",
             "time_of_day": "Midday, overcast",
             "patterns": ["Parachute BWO", "RS2", "Pheasant Tail"]},
            {"insect": "Brachycentrus / Rhyacophila", "common_name": "Caddis",
             "months": (5, 9), "peak": (6, 7), "hook_sizes": "14-18",
             "time_of_day": "Evening",
             "patterns": ["Elk Hair Caddis", "X-Caddis", "Caddis Pupa"]},
            {"insect": "Ephemerella excrucians", "common_name": "PMD",
             "months": (6, 9), "peak": (7, 8), "hook_sizes": "14-18",
             "time_of_day": "Late morning into afternoon",
             "patterns": ["PMD Comparadun", "Sparkle Dun"]},
            {"insect": "Isoperla", "common_name": "Yellow Sally",
             "months": (5, 7), "peak": (6, 6), "hook_sizes": "14-16",
             "time_of_day": "Afternoon, riffles",
             "patterns": ["Yellow Stimulator"]},
            {"insect": "Drunella grandis", "common_name": "Green Drake",
             "months": (6, 8), "peak": (7, 7), "hook_sizes": "10-12",
             "time_of_day": "Afternoon, high elevation",
             "patterns": ["Green Drake dry", "Para Drake"]},
            {"insect": "Melanoplus", "common_name": "Hoppers",
             "months": (7, 10), "peak": (8, 9), "hook_sizes": "8-12",
             "time_of_day": "Midday, banks",
             "patterns": ["Chubby Chernobyl", "Morrish Hopper"]},
            {"insect": "Tricorythodes", "common_name": "Trico",
             "months": (8, 10), "peak": (8, 9), "hook_sizes": "20-22",
             "time_of_day": "Morning",
             "patterns": ["Trico Spinner"]},
            {"insect": "Mysis relicta", "common_name": "Mysis Shrimp",
             "months": (1, 12), "peak": (1, 12), "hook_sizes": "16-22",
             "time_of_day": "All day, below mysis-bearing dams",
             "patterns": ["Mysis Shrimp", "Pink/White Mysis"]},
            {"insect": "Chironomidae", "common_name": "Midges",
             "months": (1, 12), "peak": (11, 3), "hook_sizes": "20-26",
             "time_of_day": "Midday; winter staple",
             "patterns": ["Zebra Midge", "Griffith's Gnat", "Black Beauty"]},
        ],
    },
    {
        "name": "Pacific Northwest",
        "bbox": (42.0, 49.0, -125.0, -117.0),
        "blurb": "Washington / Oregon / west Idaho -- Skwalas in early "
                 "spring, salmonflies on the Deschutes, and the "
                 "October Caddis as a late-season bookend. Steelhead "
                 "swing on the side.",
        "chart": [
            {"insect": "Skwala parallela", "common_name": "Skwala Stone",
             "months": (3, 4), "peak": (3, 4), "hook_sizes": "8-10",
             "time_of_day": "Afternoon, banks",
             "patterns": ["Skwala dry", "Stonefly Nymph"]},
            {"insect": "Rhithrogena morrisoni", "common_name": "March Brown",
             "months": (4, 5), "peak": (4, 5), "hook_sizes": "12-14",
             "time_of_day": "Afternoon",
             "patterns": ["March Brown dry", "Hare's Ear"]},
            {"insect": "Pteronarcys californica", "common_name": "Salmonfly",
             "months": (5, 6), "peak": (5, 6), "hook_sizes": "2-6",
             "time_of_day": "Afternoon, banks",
             "patterns": ["Chubby Salmonfly", "Pat's Rubberlegs"]},
            {"insect": "Hesperoperla pacifica", "common_name": "Golden Stone",
             "months": (6, 7), "peak": (6, 7), "hook_sizes": "8-10",
             "time_of_day": "Afternoon",
             "patterns": ["Yellow Stimulator"]},
            {"insect": "Ephemerella excrucians", "common_name": "PMD",
             "months": (6, 8), "peak": (6, 7), "hook_sizes": "14-18",
             "time_of_day": "Late morning",
             "patterns": ["PMD Comparadun"]},
            {"insect": "Drunella grandis", "common_name": "Green Drake",
             "months": (6, 7), "peak": (6, 7), "hook_sizes": "10-12",
             "time_of_day": "Afternoon",
             "patterns": ["Green Drake dry"]},
            {"insect": "Baetis", "common_name": "Blue-Winged Olive",
             "months": (3, 5), "peak": (4, 4), "hook_sizes": "18-22",
             "time_of_day": "Midday, overcast",
             "patterns": ["Parachute BWO", "RS2"]},
            {"insect": "Dicosmoecus", "common_name": "October Caddis",
             "months": (9, 11), "peak": (10, 10), "hook_sizes": "6-10",
             "time_of_day": "Afternoon",
             "patterns": ["October Caddis dry", "Orange Stimulator"]},
            {"insect": "Chironomidae", "common_name": "Midges",
             "months": (1, 12), "peak": (11, 3), "hook_sizes": "20-26",
             "time_of_day": "Midday",
             "patterns": ["Zebra Midge", "Griffith's Gnat"]},
        ],
    },
    {
        "name": "Sierra Nevada / California",
        "bbox": (35.0, 42.0, -122.0, -118.0),
        "blurb": "Sierra spring creeks and freestones plus the Lower Sac / "
                 "Pit / Hat tailwaters -- PMD-heavy, with caddis "
                 "year-round and salmonflies on the big rivers in spring.",
        "chart": [
            {"insect": "Baetis", "common_name": "Blue-Winged Olive",
             "months": (3, 11), "peak": (4, 5), "hook_sizes": "18-22",
             "time_of_day": "Midday, overcast",
             "patterns": ["Parachute BWO", "RS2"]},
            {"insect": "Pteronarcys californica", "common_name": "Salmonfly",
             "months": (5, 6), "peak": (5, 6), "hook_sizes": "4-6",
             "time_of_day": "Afternoon (Lower Sac, Pit, Hat)",
             "patterns": ["Chubby Salmonfly", "Pat's Rubberlegs"]},
            {"insect": "Ephemerella excrucians", "common_name": "PMD",
             "months": (5, 9), "peak": (6, 7), "hook_sizes": "14-18",
             "time_of_day": "Late morning into afternoon",
             "patterns": ["PMD Comparadun", "Sparkle Dun"]},
            {"insect": "Isoperla", "common_name": "Yellow Sally",
             "months": (5, 7), "peak": (6, 6), "hook_sizes": "14-16",
             "time_of_day": "Afternoon",
             "patterns": ["Yellow Stimulator"]},
            {"insect": "Brachycentrus / Hydropsyche", "common_name": "Caddis",
             "months": (4, 10), "peak": (6, 7), "hook_sizes": "14-18",
             "time_of_day": "Evening",
             "patterns": ["Elk Hair Caddis", "X-Caddis", "Caddis Pupa"]},
            {"insect": "Tricorythodes", "common_name": "Trico",
             "months": (7, 9), "peak": (8, 9), "hook_sizes": "20-22",
             "time_of_day": "Morning",
             "patterns": ["Trico Spinner"]},
            {"insect": "Melanoplus", "common_name": "Hoppers",
             "months": (7, 10), "peak": (8, 9), "hook_sizes": "8-12",
             "time_of_day": "Midday, banks",
             "patterns": ["Chubby Chernobyl", "Morrish Hopper"]},
            {"insect": "Chironomidae", "common_name": "Midges",
             "months": (1, 12), "peak": (11, 3), "hook_sizes": "20-26",
             "time_of_day": "Midday, slow water",
             "patterns": ["Zebra Midge", "Griffith's Gnat"]},
        ],
    },
    {
        "name": "Ozarks",
        "bbox": (35.0, 38.0, -94.5, -91.0),
        "blurb": "White River / Norfork / Little Red / Current -- "
                 "cold tailwaters out of warm Ozark dams. Sowbug-and-"
                 "midge fishery year-round with light spring mayfly "
                 "and caddis activity.",
        "chart": [
            {"insect": "Asellus / Sowbugs", "common_name": "Sowbugs / Scuds",
             "months": (1, 12), "peak": (1, 12), "hook_sizes": "14-18",
             "time_of_day": "All day -- the staple",
             "patterns": ["Sowbug", "Pink Scud", "Czech Nymph"]},
            {"insect": "Chironomidae", "common_name": "Midges",
             "months": (1, 12), "peak": (1, 12), "hook_sizes": "20-26",
             "time_of_day": "All day, especially low-water windows",
             "patterns": ["Zebra Midge", "Disco Midge", "Griffith's Gnat"]},
            {"insect": "Baetis", "common_name": "Blue-Winged Olive",
             "months": (10, 4), "peak": (3, 4), "hook_sizes": "18-22",
             "time_of_day": "Midday, overcast",
             "patterns": ["Parachute BWO", "RS2"]},
            {"insect": "Brachycentrus / Hydropsyche", "common_name": "Caddis",
             "months": (4, 8), "peak": (5, 6), "hook_sizes": "14-18",
             "time_of_day": "Evening",
             "patterns": ["Elk Hair Caddis", "X-Caddis"]},
            {"insect": "Ephemerella invaria", "common_name": "Sulphur",
             "months": (5, 6), "peak": (5, 6), "hook_sizes": "14-18",
             "time_of_day": "Evening",
             "patterns": ["Sulphur Comparadun"]},
            {"insect": "Terrestrials", "common_name": "Ants / Beetles / Hoppers",
             "months": (5, 10), "peak": (7, 9), "hook_sizes": "10-18",
             "time_of_day": "Midday, low water",
             "patterns": ["Foam Beetle", "Parachute Ant", "Hopper"]},
        ],
    },
]

FALLBACK_ZONE = {
    "name": "Continental US (general)",
    "bbox": None,
    "blurb": "Generic trout slate -- the bug families below are widely "
             "distributed across coldwater habitats and a reasonable "
             "starting point until a regional or per-river override "
             "lands. Outside cold-water range, this is a stub.",
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


def _load_overrides() -> dict[str, list[dict]]:
    """Per-river hatch overrides; tuple-ify month ranges from JSON lists."""
    path = os.path.join(os.path.dirname(__file__), "data", "hatches",
                        "overrides.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        raw = json.load(f)
    out: dict[str, list[dict]] = {}
    for key, entries in raw.items():
        if key.startswith("_") or not isinstance(entries, list):
            continue
        cleaned = []
        for e in entries:
            ent = dict(e)
            for fld in ("months", "peak"):
                if fld in ent and isinstance(ent[fld], list):
                    ent[fld] = tuple(ent[fld])
            cleaned.append(ent)
        out[key.lower().strip()] = cleaned
    return out


RIVER_HATCH_OVERRIDES: dict[str, list[dict]] = _load_overrides()


def zone_for_river(river_name: str, lat: float, lon: float) -> dict:
    """Per-river override (curated for famous waters) if defined,
    otherwise the geographic zone for the coordinates."""
    if river_name:
        ov = RIVER_HATCH_OVERRIDES.get(river_name.strip().lower())
        if ov:
            return {"name": f"{river_name} (curated)",
                    "blurb": f"Curated hatch list for {river_name}.",
                    "chart": ov}
    return zone_for(lat, lon)


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
