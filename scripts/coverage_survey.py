"""Standing per-state coverage survey for trout / stocking / access data.

[NETWORK -- real discovery runs in the Actions coverage-survey job; the dev
sandbox's egress allowlist blocks most state ArcGIS hosts, so a LOCAL run shows
nearly everything "unreachable (retry)". That's expected -- the survey is built
to retry weekly on the runner.]

WHAT THIS IS
------------
BlueLiner classifies trout streams and shows stocking / access points from
~per-state agency ArcGIS layers declared in
``data/{trout,stocking,access_points}/sources.json``. Coverage is uneven. This
script is the broad, weekly "what's missing across the lower 48 and what did we
find" pass. It complements the narrow 6h endpoint-watch (which re-verifies
already-known specific candidates): this one recomputes the gaps from the
registries every run and, for each FILLABLE gap, discovers candidate
state-agency layers (walking known hosts + ArcGIS-Online search) and captures a
light metadata probe of the top few.

REPORT ONLY. It never edits any ``sources.json`` / ``candidates.json`` -- picking
the right layer and designing its field_map / species_flags / type_flags needs
human judgment. This is the discovery worklist that FEEDS that promotion
pipeline.

OUTPUT: ``gis_verify_out/COVERAGE.md`` (matrix + per-gap candidates + summary)
and, under Actions, the matrix to ``$GITHUB_STEP_SUMMARY``. Always exits 0.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import httpx  # noqa: E402

# Reuse the discovery + probing primitives -- do NOT reinvent them.
import gis_endpoint_verify as gv  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Curated reference sets
# ---------------------------------------------------------------------------

# Lower-48 only (the survey's scope). DC excluded.
LOWER_48 = [
    "AL", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
]

# States with a real coldwater trout fishery. Trout & stocking gaps OUTSIDE
# this set are EXPECTED-EMPTY (negligible/no trout) -- the report marks them
# "expected none" and the survey does not waste discovery probes there. Access
# gaps apply to ALL states (boat ramps / fishing access exist everywhere), so
# this set does not gate access discovery.
#
# Built from the ~40 states with stocked or wild trout programs; the dozen
# warmwater-only states are deliberately omitted (FL LA MS AL KS NE OK SD IL
# IN ND -- the expected-empty set called out in the worklist).
TROUT_STATES = {
    "AZ", "AR", "CA", "CO", "CT", "DE", "GA", "ID", "IA", "KY", "ME", "MD",
    "MA", "MI", "MN", "MO", "MT", "NV", "NH", "NJ", "NM", "NY", "NC", "OH",
    "OR", "PA", "RI", "SC", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI",
    "WY",
}

# Western trout states that already get range-wide WILD-trout baseline coverage
# from the NATIVE overlays (WCT / BULL / RBT / YCT / BCT / RGCT / GILA in
# data/trout/sources.json) even with no state trout source. A state source
# would ADD management tiers, not first-light. The report flags these so a
# human deprioritizes them.
NATIVE_OVERLAY_STATES = {"ID", "MT", "OR", "WA", "NM", "NV"}

DATATYPES = ("trout", "stocking", "access")

# Datatype-specific keyword sets fed to AGOL search terms (the in-name filter
# itself reuses gv.interesting() / gv.NOISE_KW).
DATATYPE_KW = {
    "trout": ("trout", "designated", "wild", "classified", "coldwater", "class"),
    "stocking": ("stock", "plant", "trout"),
    "access": ("access", "ramp", "launch", "boat", "fishing", "pier"),
}

# AGOL search-term templates per datatype, formatted with the state name.
SEARCH_TERMS = {
    "trout": ("{n} wild trout streams", "{n} designated trout waters"),
    "stocking": ("{n} fish stocking locations", "{n} trout stocking"),
    "access": ("{n} boat ramp fishing access", "{n} water access sites"),
}

# ---------------------------------------------------------------------------
# Bounding knobs (keep the weekly crawl cheap and finite)
# ---------------------------------------------------------------------------
CANDIDATES_PER_GAP = 5        # cap captured candidates per gap
HOST_TIMEOUT = 15.0           # per-request timeout
TOTAL_RUNTIME_BUDGET = 1500.0 # seconds; stop discovery after this, still report
OUT_PATH = os.path.join(ROOT, "gis_verify_out", "COVERAGE.md")


# ---------------------------------------------------------------------------
# Consolidated per-state host catalog (merge every known root we have)
# ---------------------------------------------------------------------------
def build_host_catalog() -> dict[str, list[str]]:
    """Merge SERVER_ROOTS + ORG_CATALOGS (gis_endpoint_verify, which already
    folds in discovery.catalogs SEED_ARCGIS_HOSTS) into one ST -> [roots]."""
    cat: dict[str, list[str]] = {}
    for src in (getattr(gv, "SERVER_ROOTS", {}), getattr(gv, "ORG_CATALOGS", {})):
        for st, roots in src.items():
            bucket = cat.setdefault(st, [])
            for r in roots:
                if r not in bucket:
                    bucket.append(r)
    return cat


def state_name(st: str) -> str:
    return gv.STATE_NAMES.get(st, st)


# ---------------------------------------------------------------------------
# Gap computation -- read the three registries, a state is covered iff it has a
# source entry for that datatype.
# ---------------------------------------------------------------------------
_REGISTRY_FOLDER = {"trout": "trout", "stocking": "stocking",
                    "access": "access_points"}


def _registry_path(datatype: str, base: str | None = None) -> str:
    """Path to a datatype's sources.json. `base` overrides the data/ root (for
    tests injecting a fake registry); it should contain
    {trout,stocking,access_points}/sources.json directly."""
    folder = _REGISTRY_FOLDER[datatype]
    if base:
        return os.path.join(base, folder, "sources.json")
    return os.path.join(ROOT, "data", folder, "sources.json")


def covered_states(datatype: str, registry_dir: str | None = None) -> set[str]:
    """Set of 2-letter state codes that have at least one source entry of this
    datatype. Non-state tokens (EBTJV / WCT / native-overlay pseudo-states) are
    ignored -- they aren't per-state designations."""
    path = _registry_path(datatype, registry_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return set()
    sources = doc.get("sources", doc) if isinstance(doc, dict) else doc
    out: set[str] = set()
    for s in sources or []:
        if not isinstance(s, dict):
            continue
        st = s.get("state")
        if isinstance(st, str) and st.upper() in LOWER_48:
            out.add(st.upper())
    return out


def compute_gaps(registry_dir: str | None = None) -> dict[str, dict[str, str]]:
    """Return matrix[state][datatype] = status string, one of:
        'Y'             -- covered (a source exists)
        'gap'           -- fillable gap (discover candidates)
        'expected none' -- trout/stocking gap in a non-trout state (skip probes)
    """
    coverage = {dt: covered_states(dt, registry_dir) for dt in DATATYPES}
    matrix: dict[str, dict[str, str]] = {}
    for st in LOWER_48:
        row: dict[str, str] = {}
        for dt in DATATYPES:
            if st in coverage[dt]:
                row[dt] = "Y"
            elif dt in ("trout", "stocking") and st not in TROUT_STATES:
                row[dt] = "expected none"
            else:
                row[dt] = "gap"
        matrix[st] = row
    return matrix


def fillable_gaps(matrix: dict[str, dict[str, str]]) -> list[tuple[str, str]]:
    """List of (state, datatype) that are actual fillable gaps (status 'gap')."""
    out = []
    for st in LOWER_48:
        for dt in DATATYPES:
            if matrix[st][dt] == "gap":
                out.append((st, dt))
    return out


# ---------------------------------------------------------------------------
# Discovery + light probe for one gap
# ---------------------------------------------------------------------------
def _light_probe(client, layer_url: str) -> dict | None:
    """Metadata (?f=json) + returnCountOnly. NOT a full verify -- keeps the
    crawl bounded. -> {url, name, geometryType, count} or None if unreachable."""
    meta = gv.get(client, layer_url, {"f": "json"})
    if not meta or "_http" in meta or "error" in meta:
        return None
    cnt = gv.get(client, layer_url + "/query",
                 {"where": "1=1", "returnCountOnly": "true", "f": "json"})
    return {
        "url": layer_url,
        "name": meta.get("name") or "",
        "geometryType": meta.get("geometryType") or meta.get("type") or "",
        "count": (cnt or {}).get("count"),
    }


def discover_gap(client, st: str, datatype: str,
                 host_catalog: dict[str, list[str]]) -> dict:
    """For one fillable gap, find candidate layers from known host roots and/or
    AGOL search, keyword-filter with gv.interesting()/NOISE_KW, light-probe the
    top few. Returns a result dict:
        {'status': 'candidates'|'unreachable'|'none',
         'used_host': bool, 'candidates': [probe dicts]}
    """
    kw = DATATYPE_KW[datatype]

    def name_matches(name: str) -> bool:
        n = (name or "").lower()
        if any(k in n for k in gv.NOISE_KW):
            return False
        return any(k in n for k in kw)

    layer_urls: list[str] = []
    seen: set[str] = set()
    any_root_reached = False
    roots = host_catalog.get(st, [])

    # 1) walk known host roots (state-authoritative)
    for root in roots:
        try:
            services = gv.walk_root(client, root)
        except Exception:
            services = []
        if services:
            any_root_reached = True
        for svc_url, _svc_name in services:
            try:
                layers = gv.service_layers(client, svc_url)
            except Exception:
                layers = []
            for lurl, lname, _lgeom in layers:
                if name_matches(lname) and lurl not in seen:
                    seen.add(lurl)
                    layer_urls.append(lurl)

    # 2) AGOL search fallback (esp. for states with NO known host)
    agol_reached = False
    for tmpl in SEARCH_TERMS[datatype]:
        term = tmpl.format(n=state_name(st))
        try:
            items = gv.agol_search(client, term)
            agol_reached = True
        except Exception:
            items = []
        for item in items:
            url = (item.get("url") or "").rstrip("/")
            if not url or "/rest/services" not in url:
                continue
            if url.rsplit("/", 1)[-1].isdigit():  # direct layer url
                if name_matches(item.get("title", "")) and url not in seen:
                    seen.add(url)
                    layer_urls.append(url)
                continue
            try:
                layers = gv.service_layers(client, url)
            except Exception:
                layers = []
            for lurl, lname, _lgeom in layers:
                if name_matches(lname) and lurl not in seen:
                    seen.add(lurl)
                    layer_urls.append(lurl)

    # 3) light-probe the top few candidates
    probed: list[dict] = []
    for lurl in layer_urls:
        if len(probed) >= CANDIDATES_PER_GAP:
            break
        try:
            p = _light_probe(client, lurl)
        except Exception:
            p = None
        if p:
            probed.append(p)

    used_host = bool(roots)
    if probed:
        return {"status": "candidates", "used_host": used_host,
                "candidates": probed}
    # nothing captured: distinguish unreachable vs genuinely no candidate
    if not any_root_reached and not agol_reached:
        return {"status": "unreachable", "used_host": used_host, "candidates": []}
    return {"status": "none", "used_host": used_host, "candidates": []}


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------
def render_matrix(matrix: dict[str, dict[str, str]]) -> list[str]:
    cell = {"Y": "Y", "gap": "gap", "expected none": "expected none"}
    lines = ["| State | Trout | Stocking | Access |",
             "|-------|-------|----------|--------|"]
    for st in LOWER_48:
        r = matrix[st]
        lines.append(f"| {st} | {cell[r['trout']]} | "
                     f"{cell[r['stocking']]} | {cell[r['access']]} |")
    return lines


def render_candidates_section(results: dict[tuple[str, str], dict]) -> list[str]:
    lines: list[str] = []
    for (st, dt), res in results.items():
        header = f"### {st} / {dt}"
        if st in NATIVE_OVERLAY_STATES and dt == "trout":
            header += "  _(has range-wide native wild-trout overlay; a state " \
                      "source would ADD management tiers)_"
        lines.append(header)
        status = res["status"]
        if status == "candidates":
            lines.append("Discovered candidate layers (light probe -- a human "
                         "picks one and designs its field mapping):")
            lines.append("")
            lines.append("| name | geometryType | count | url |")
            lines.append("|------|--------------|-------|-----|")
            for c in res["candidates"]:
                cnt = "?" if c["count"] is None else c["count"]
                lines.append(f"| {c['name']} | {c['geometryType']} | {cnt} | "
                             f"{c['url']} |")
        elif status == "unreachable":
            lines.append("_host unreachable (retry next run)_")
        else:
            lines.append("_no candidate found_")
        lines.append("")
    return lines


def render_report(matrix, results, host_catalog) -> str:
    gaps = fillable_gaps(matrix)
    with_cands = sum(1 for r in results.values() if r["status"] == "candidates")
    # host-catalog coverage among gap states
    gap_states = sorted({st for st, _ in gaps})
    have_host = sum(1 for st in gap_states if host_catalog.get(st))

    out: list[str] = []
    out.append("# Coverage Survey")
    out.append("")
    out.append(f"_Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}._ "
               "Report-only standing worklist -- feeds the candidate-promotion "
               "pipeline; never auto-edits any registry.")
    out.append("")
    out.append(f"**Summary:** {len(gaps)} fillable gaps across the lower 48; "
               f"{with_cands} produced candidate layers this run. "
               f"Of {len(gap_states)} gap states, {have_host} have a known "
               f"agency host (rest fall back to ArcGIS-Online search).")
    out.append("")
    out.append("Legend: **Y** = a source exists; **gap** = fillable (discovery "
               "runs); **expected none** = non-trout state, no coldwater "
               "fishery to source. Western trout states "
               "(ID/MT/OR/WA/NM/NV) already get baseline wild-trout coverage "
               "from the range-wide NATIVE overlays "
               "(WCT/BULL/RBT/YCT/BCT/RGCT/GILA) even without a state trout "
               "source -- a state source would ADD management tiers.")
    out.append("")
    out.append("## Coverage matrix")
    out.append("")
    out.extend(render_matrix(matrix))
    out.append("")
    out.append("## Discovered candidates per gap")
    out.append("")
    if results:
        out.extend(render_candidates_section(results))
    else:
        out.append("_No fillable gaps to discover._")
        out.append("")
    return "\n".join(out) + "\n"


def write_step_summary(matrix) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("## Coverage survey matrix\n\n")
            for line in render_matrix(matrix):
                f.write(line + "\n")
            f.write("\n")
    except Exception as e:  # pragma: no cover
        print(f"  (could not write step summary: {e})")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run_survey(discover=discover_gap, registry_dir=None,
               runtime_budget=TOTAL_RUNTIME_BUDGET):
    """Compute gaps, discover candidates for each fillable gap (bounded by
    runtime_budget), render the report. `discover` is injectable for tests.
    Returns (matrix, results)."""
    matrix = compute_gaps(registry_dir)
    host_catalog = build_host_catalog()
    gaps = fillable_gaps(matrix)
    results: dict[tuple[str, str], dict] = {}

    start = time.monotonic()
    with httpx.Client(timeout=HOST_TIMEOUT, headers=gv.UA,
                      follow_redirects=True) as client:
        for st, dt in gaps:
            if time.monotonic() - start > runtime_budget:
                results[(st, dt)] = {"status": "unreachable", "used_host":
                                     bool(host_catalog.get(st)), "candidates": []}
                continue
            try:
                results[(st, dt)] = discover(client, st, dt, host_catalog)
            except Exception as e:  # a throwing probe must never crash the run
                print(f"  GAP-EXC {st}/{dt} {type(e).__name__}: {e}")
                results[(st, dt)] = {"status": "unreachable",
                                     "used_host": bool(host_catalog.get(st)),
                                     "candidates": []}
    return matrix, results


def main() -> int:
    matrix, results = run_survey()
    host_catalog = build_host_catalog()
    report = render_report(matrix, results, host_catalog)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    write_step_summary(matrix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
