"""Seed a synthetic catch log so the memory/personalization story is demoable.

A fresh clone has an empty DB, so there's no catch history to summarize. This
seeds a realistic angler (user_id resolved from a demo email) whose browns
cluster at cool water (~52-60F) and near-median flow (~0.7-1.3x), best on
sulphurs -- the pattern the v2 ranker uses to break ties in the `memory-*` eval
scenarios. Writes through Blueliner's own db.add_catch, so the rows are
indistinguishable from real ones.

Run (with the SAME BLUELINER_DB the agent/eval will use):
    python -m agent.seed_memory
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import db
from agent import memory

DEMO_EMAIL = "demo-angler@blueliner.app"
MOONS = ["New moon", "First quarter", "Full moon", "Last quarter", "Waxing gibbous"]


def _env(water_temp_f, ratio, median, hatch, rng):
    flow = round(median * ratio, 1)
    if ratio < 0.85:
        label = "below average"
    elif ratio <= 1.15:
        label = "near average"
    else:
        label = "above average"
    return {
        "flow_cfs": flow, "flow_median_cfs": median, "flow_vs_median": label,
        "water_temp_f": water_temp_f,
        "air_temp_f": round(rng.uniform(55, 78), 1),
        "pressure_inhg": round(rng.uniform(29.7, 30.3), 2),
        "conditions": rng.choice(["Clear", "Partly Cloudy", "Overcast"]),
        "moon_phase": rng.choice(MOONS),
        "active_hatches": [hatch],
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def _catches(rng):
    rows = []
    base = datetime.now(timezone.utc) - timedelta(days=200)
    # 12 browns: cool water, near-median flow, mostly sulphurs.
    for i in range(12):
        temp = round(rng.uniform(52, 60), 1)
        ratio = round(rng.uniform(0.7, 1.3), 2)
        median = rng.choice([78.0, 110.0, 130.0])
        hatch = rng.choice(["sulphurs", "sulphurs", "blue-winged olives"])
        rows.append(("brown trout", "Gunpowder Falls", "01582500",
                     temp, ratio, median, hatch))
    # 4 rainbows: slightly warmer, a touch higher flow.
    for i in range(4):
        rows.append(("rainbow trout", "Spring Creek", "01546500",
                     round(rng.uniform(55, 62), 1), round(rng.uniform(0.9, 1.4), 2),
                     78.0, "tricos"))
    # 2 brookies: cold mountain water.
    for i in range(2):
        rows.append(("brook trout", "Rapidan River", "01665500",
                     round(rng.uniform(48, 54), 1), round(rng.uniform(0.8, 1.1), 2),
                     50.0, "quill gordons"))
    rng.shuffle(rows)
    out = []
    for n, (sp, river, site, temp, ratio, median, hatch) in enumerate(rows):
        occurred = (base + timedelta(days=n * 9)).isoformat()
        env = _env(temp, ratio, median, hatch, rng)
        out.append(({"species": sp, "river_name": river, "river_site_no": site,
                     "occurred_at": occurred, "length_in": round(rng.uniform(9, 18), 1),
                     "fly_used": hatch}, env))
    return out


def main():
    db.init_db()
    user = db.upsert_user_by_email(DEMO_EMAIL)
    uid = user["id"]
    existing = db.count_catches(uid)
    if existing:
        print(f"user_id={uid} already has {existing} catches; skipping seed.")
    else:
        rng = random.Random(42)
        for data, env in _catches(rng):
            db.add_catch(uid, data, env)
        print(f"seeded {db.count_catches(uid)} catches for user_id={uid} ({DEMO_EMAIL})")
    print("\nmemory summary the agent will see:")
    print(" ", memory.summarize_user_patterns(uid)["summary"])
    print(f"\n(user_id={uid} -- the memory-* eval scenarios reference user_id=1)")


if __name__ == "__main__":
    main()
