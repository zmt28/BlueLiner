#!/usr/bin/env python3
"""Generate BlueLiner deck graphics (architecture diagram + two data graphics).

Hand-authored SVG -> high-res PNG via cairosvg. On-brand navy/water palette.
Outputs to deck/assets/*.png at 2x for crisp placement in Canva (1920x1080 slides).
"""
import os, html
import cairosvg

OUT = os.path.join(os.path.dirname(__file__), "assets")
os.makedirs(OUT, exist_ok=True)

# ---- palette -------------------------------------------------------------
NAVY      = "#0B2A3A"   # base background
PANEL     = "#102E41"   # panel fill
PANEL2    = "#143A50"   # lighter panel
PANEL_DK  = "#0C2535"   # darker inset
BLUE      = "#5BA8C8"   # water-blue accent (Trip Planner)
BLUE_LT   = "#95C5D9"
GREEN     = "#4A8C5C"   # good / trusted (scorer)
GREEN_LT  = "#7FBE8E"
OCHRE     = "#B7892F"   # caution / grounding
CLAY      = "#B3473B"   # failure / guardrail veto / block
PURPLE    = "#7A3DB8"   # discovery / prospector
PURPLE_LT = "#A878DA"
FG        = "#EAF2F6"   # primary text
MUTE      = "#9DB6C2"   # muted text
FAINT     = "#6E8C9C"   # faint labels
LINE      = "#3E5C6E"   # connectors

FONT  = "DejaVu Sans"
MONO  = "DejaVu Sans Mono"

def esc(s): return html.escape(str(s), quote=True)

def rect(x, y, w, h, r=14, fill=PANEL, stroke=None, sw=2, opacity=None, dash=None):
    a = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" ry="{r}" fill="{fill}"'
    if stroke: a += f' stroke="{stroke}" stroke-width="{sw}"'
    if dash:   a += f' stroke-dasharray="{dash}"'
    if opacity is not None: a += f' opacity="{opacity}"'
    return a + "/>"

def accent_left(x, y, w, h, color, r=14):
    # colored accent bar clipped to the left edge of a rounded panel
    return (f'<path d="M{x+r},{y} h{w-2*r} a{r},{r} 0 0 1 {r},{r} v{h-2*r} '
            f'a{r},{r} 0 0 1 -{r},{r} h-{w-2*r} a{r},{r} 0 0 1 -{r},-{r} v-{h-2*r} '
            f'a{r},{r} 0 0 1 {r},-{r} z" fill="none"/>'
            f'<rect x="{x}" y="{y}" width="6" height="{h}" rx="3" fill="{color}"/>')

def txt(x, y, s, size=16, fill=FG, weight="normal", anchor="start", family=FONT, ls=None, opacity=None):
    a = (f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
         f'fill="{fill}" font-weight="{weight}" text-anchor="{anchor}"')
    if ls is not None: a += f' letter-spacing="{ls}"'
    if opacity is not None: a += f' opacity="{opacity}"'
    return a + f'>{esc(s)}</text>'

def mtxt(x, y, lines, size=15, fill=FG, weight="normal", anchor="start", lh=21, family=FONT):
    out = []
    for i, ln in enumerate(lines):
        out.append(txt(x, y + i*lh, ln, size=size, fill=fill, weight=weight, anchor=anchor, family=family))
    return "\n".join(out)

def arrow(x1, y1, x2, y2, color=BLUE, sw=2.4, dash=None, marker="arrow"):
    a = f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{sw}"'
    if dash: a += f' stroke-dasharray="{dash}"'
    a += f' marker-end="url(#{marker})"/>'
    return a

def defs():
    def m(mid, color):
        return (f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" '
                f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                f'<path d="M0,0 L10,5 L0,10 z" fill="{color}"/></marker>')
    return ("<defs>" + m("arrow", BLUE) + m("arrowg", GREEN_LT) + m("arrowm", MUTE)
            + m("arrowp", PURPLE_LT) + "</defs>")

def render(name, w, h, body, scale=2):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
           f'viewBox="0 0 {w} {h}">{defs()}'
           f'<rect width="{w}" height="{h}" fill="{NAVY}"/>{body}</svg>')
    sp = os.path.join(OUT, name + ".svg"); open(sp, "w").write(svg)
    pp = os.path.join(OUT, name + ".png")
    cairosvg.svg2png(bytestring=svg.encode(), write_to=pp, output_width=int(w*scale), output_height=int(h*scale))
    print("wrote", pp, f"({int(w*scale)}x{int(h*scale)})")

# =========================================================================
# G1 — Architecture diagram  (1760 x 780)
# =========================================================================
def architecture():
    W, H = 1760, 780
    s = []
    def header(cx, label): s.append(txt(cx, 40, label, size=16, fill=FAINT, weight="bold", anchor="middle", ls=2))

    # ---- column headers
    header(137, "REQUESTS"); header(461, "ORCHESTRATION")
    header(934, "SHARED TRUSTWORTHY SPINE"); header(1478, "DATA SOURCES")

    # ---- A. Requests
    s.append(rect(24, 96, 226, 150, fill=PANEL, stroke=BLUE, sw=2)); s.append(accent_left(24,96,226,150,BLUE))
    s.append(txt(40, 132, "Trip Planner", size=18, weight="bold", fill=BLUE_LT))
    s.append(mtxt(40, 162, ["“Where should I fish", "this weekend — and is", "it worth the drive?”"], size=14, fill=FG, lh=22))
    s.append(rect(24, 356, 226, 150, fill=PANEL, stroke=PURPLE, sw=2)); s.append(accent_left(24,356,226,150,PURPLE))
    s.append(txt(40, 392, "Prospector", size=18, weight="bold", fill=PURPLE_LT))
    s.append(mtxt(40, 422, ["“Find undesignated", "but fishable", "trout water”"], size=14, fill=FG, lh=22))

    # ---- B. Orchestration
    s.append(rect(274, 82, 374, 212, fill=PANEL, stroke=BLUE, sw=2)); s.append(accent_left(274,82,374,212,BLUE))
    s.append(txt(296, 120, "Trip Planner", size=20, weight="bold", fill=BLUE_LT))
    s.append(txt(296, 146, "hand-written tool loop", size=15, fill=MUTE))
    s.append(mtxt(296, 184, ["Haiku  →  cheap, tool-heavy", "             retrieval loop",
                              "Sonnet →  final ranking"], size=15, fill=FG, lh=24, family=MONO))
    s.append(rect(296, 252, 168, 28, r=14, fill=PANEL_DK, stroke=BLUE, sw=1))
    s.append(txt(380, 271, "17 lines orchestration", size=13, fill=BLUE_LT, anchor="middle"))

    s.append(rect(274, 338, 374, 236, fill=PANEL, stroke=PURPLE, sw=2)); s.append(accent_left(274,338,374,236,PURPLE))
    s.append(txt(296, 376, "Prospector", size=20, weight="bold", fill=PURPLE_LT))
    s.append(txt(296, 402, "LangGraph", size=15, fill=MUTE))
    s.append(mtxt(296, 440, ["branching state machine", "human-in-the-loop confirm", "durable checkpoints · interrupt"], size=15, fill=FG, lh=24))
    s.append(rect(296, 528, 150, 28, r=14, fill=PANEL_DK, stroke=PURPLE, sw=1))
    s.append(txt(371, 547, "38 lines (2.2×)", size=13, fill=PURPLE_LT, anchor="middle"))

    # ---- C. Spine container
    s.append(rect(672, 60, 524, 580, r=18, fill="#0E3144", stroke=BLUE, sw=2))
    sx, sw_ = 692, 484
    def stage(y, hh, color, title, lines, tag=None, emph=False):
        s.append(rect(sx, y, sw_, hh, r=12, fill=PANEL2 if emph else PANEL, stroke=color, sw=3 if emph else 2))
        s.append(rect(sx, y, 6, hh, r=3, fill=color))
        s.append(txt(sx+22, y+30, title, size=17, weight="bold", fill=FG))
        s.append(mtxt(sx+22, y+54, lines, size=13.5, fill=MUTE, lh=19))
        if tag:
            s.append(txt(sx+sw_-16, y+30, tag, size=12.5, weight="bold", fill=color, anchor="end"))
    stage(118, 86, BLUE,  "MCP tool belt", ["conditions · gauges · 30-yr medians", "trout designations · access · catch-log memory"], tag="retrieval")
    s.append(arrow(934, 204, 934, 222, color=MUTE, marker="arrowm"))
    stage(224, 104, GREEN, "Deterministic scorer", ["single source of truth", "water-temp band  +  flow-vs-median"], tag="the oracle", emph=True)
    s.append(txt(sx+22, 224+96, "parity-tested 840 cases against production", size=12, fill=GREEN_LT))
    s.append(arrow(934, 328, 934, 346, color=MUTE, marker="arrowm"))
    stage(348, 86, OCHRE, "Grounding contract", ["every number must trace to a tool result —", "else regenerate once, then strip it"])
    s.append(arrow(934, 434, 934, 452, color=MUTE, marker="arrowm"))
    stage(452, 122, CLAY, "Guardrail veto", ["flood (flow >3× median) · trout-ethics temp band", "private-access block · staleness demotion"], tag="rules decide")
    s.append(txt(sx+22, 452+108, "IDs canonicalized before the veto — the bug fix", size=12, fill="#E0A59B"))
    s.append(txt(934, 624, "the model advises  ·  the rules decide", size=14, fill=BLUE_LT, weight="bold", anchor="middle"))

    # ---- D. Data sources (2-col grid, upper right)
    src = [("USGS NWIS", "flow + temp · IV + daily"),
           ("USGS NLDI", "topology · COMID"),
           ("NOAA", "weather enrichment"),
           ("State ArcGIS", "trout designations"),
           ("PAD-US", "public access / lands"),
           ("Postgres", "catch-log memory")]
    gx = [1220, 1486]; gy = [70, 156, 242]; gw, gh = 250, 74
    for i, (nm, sub) in enumerate(src):
        cx = gx[i % 2]; cy = gy[i // 2]
        s.append(rect(cx, cy, gw, gh, r=12, fill=PANEL, stroke=LINE, sw=1.5))
        s.append(rect(cx, cy, 6, gh, r=3, fill=BLUE))
        s.append(txt(cx+20, cy+31, nm, size=14.5, weight="bold", fill=FG))
        s.append(txt(cx+20, cy+54, sub, size=12, fill=MUTE))
    # connector data grid -> tool belt
    s.append(arrow(1220, 178, 1180, 166, color=BLUE, sw=2.4))
    s.append(txt(1216, 150, "fetch", size=11.5, fill=BLUE_LT, anchor="end"))

    # ---- E. Output (lower right, below the data grid)
    s.append(txt(1478, 372, "OUTPUT", size=16, fill=FAINT, weight="bold", anchor="middle", ls=2))
    s.append(rect(1220, 388, 250, 212, r=12, fill=PANEL, stroke=LINE, sw=1.5))
    s.append(txt(1345, 422, "Delivered", size=16, weight="bold", fill=FG, anchor="middle"))
    s.append(mtxt(1345, 456, ["ranked recommendations", "+ grounded citations", "+ guardrail verdicts", "", "rendered on the map"], size=13.5, fill=MUTE, lh=27, anchor="middle"))
    s.append(rect(1486, 388, 250, 100, r=12, fill=PANEL, stroke=GREEN, sw=2))
    s.append(txt(1611, 442, "$0.02", size=40, weight="bold", fill=GREEN_LT, anchor="middle"))
    s.append(txt(1611, 470, "per decision", size=13, fill=MUTE, anchor="middle"))
    s.append(rect(1486, 500, 250, 100, r=12, fill=PANEL, stroke=BLUE, sw=2))
    s.append(txt(1611, 554, "17–18 s", size=38, weight="bold", fill=BLUE_LT, anchor="middle"))
    s.append(txt(1611, 582, "end-to-end latency", size=13, fill=MUTE, anchor="middle"))

    # ---- flow arrows
    s.append(arrow(250, 168, 272, 178, color=BLUE))           # TP req -> TP orch
    s.append(arrow(250, 428, 272, 446, color=PURPLE, marker="arrowp"))  # PR req -> PR orch
    s.append(arrow(648, 180, 670, 210, color=BLUE))           # TP orch -> spine
    s.append(arrow(648, 452, 670, 360, color=PURPLE, marker="arrowp"))  # PR orch -> spine
    s.append(arrow(1196, 512, 1216, 486, color=CLAY, sw=2.6)) # guardrail -> output

    # ---- Eval harness band
    s.append(rect(24, 648, 1712, 96, r=14, fill=PANEL_DK, stroke=BLUE, sw=1.6, dash="7 6"))
    s.append(txt(44, 682, "OFFLINE EVAL HARNESS", size=15, weight="bold", fill=BLUE_LT, ls=1))
    s.append(txt(44, 712, "Planner — 25 scenarios: ideal · flood · too-warm · private · stale · adversarial · memory · ties · all-blocked", size=13.5, fill=MUTE))
    s.append(txt(44, 732, "Discovery — flow-path masking · hard-negative AUC · positive-unlabeled (recall = lower bound)", size=13.5, fill=MUTE))
    # dashed arrow eval -> scorer (routed up the spine's left margin)
    s.append(arrow(680, 644, 688, 298, color=GREEN_LT, sw=2, dash="6 5", marker="arrowg"))
    s.append(txt(706, 684, "↑ the scorer here is the eval oracle — same code", size=12.5, fill=GREEN_LT, anchor="start"))

    # ---- legend
    leg = [(BLUE,"Trip Planner"),(PURPLE,"Prospector"),(GREEN,"trusted / oracle"),(OCHRE,"grounding"),(CLAY,"guardrail / block")]
    lx = 470
    s.append(txt(lx-16, 768, "", size=12))
    for color, label in leg:
        s.append(f'<rect x="{lx}" y="758" width="16" height="16" rx="4" fill="{color}"/>')
        s.append(txt(lx+22, 771, label, size=13, fill=MUTE))
        lx += 30 + len(label)*7.6 + 22
    render("architecture", W, H, "\n".join(s))

# =========================================================================
# G2 — v0 -> v3 staircase  (1780 x 650)
# =========================================================================
def staircase():
    W, H = 1780, 650
    s = []
    cols = [
        ("v0", "naive prompt, no tools", CLAY),
        ("v1", "tool-grounded", BLUE),
        ("v2", "+ catch-log memory", OCHRE),
        ("v3", "+ guardrails & grounding", GREEN),
    ]
    rows = [
        ("Recommendation agreement w/ oracle", ["8%","100%","100%","100%"], "high"),
        ("Safety violations",                  ["16%","0%","0%","0%"],      "low"),
        ("Hallucinated readings",              ["100%","4%","12%","0%"],    "low"),
    ]
    LBLW = 300            # row-label column width
    x0 = 20; gap = 16
    cw = (W - LBLW - x0*1 - gap*4) / 4
    top = 18; head_h = 96; row_h = 118; row_gap = 12
    # ascending step motif behind columns
    for i in range(4):
        cx = LBLW + x0 + i*(cw+gap)
        lift = (3-i)*16
        s.append(f'<rect x="{cx}" y="{top+lift}" width="{cw}" height="{H-top-lift-70}" rx="16" fill="{PANEL_DK}" opacity="0.55"/>')
    # column headers
    for i,(v,sub,color) in enumerate(cols):
        cx = LBLW + x0 + i*(cw+gap)
        emph = (v=="v3")
        s.append(rect(cx, top, cw, head_h, r=14, fill=PANEL2 if emph else PANEL, stroke=color, sw=3 if emph else 2))
        s.append(txt(cx+cw/2, top+46, v, size=34, weight="bold", fill=color, anchor="middle"))
        s.append(txt(cx+cw/2, top+76, sub, size=14.5, fill=FG if emph else MUTE, anchor="middle"))
    # rows
    ry = top + head_h + 20
    for ri,(label, vals, good) in enumerate(rows):
        yy = ry + ri*(row_h+row_gap)
        s.append(txt(x0+6, yy+row_h/2-6, label, size=18, weight="bold", fill=FG, anchor="start"))
        # tiny helper note under label
        note = {"high":"higher is better","low":"lower is better"}[good]
        s.append(txt(x0+6, yy+row_h/2+22, note, size=12.5, fill=FAINT, anchor="start"))
        for ci,(v,sub,color) in enumerate(cols):
            cx = LBLW + x0 + ci*(cw+gap)
            val = vals[ci]
            num = float(val.replace('%',''))
            if good=="high": cell = GREEN if num>=90 else (OCHRE if num>=50 else CLAY)
            else:            cell = GREEN if num==0 else (OCHRE if num<=15 else CLAY)
            s.append(rect(cx, yy, cw, row_h, r=12, fill=PANEL, stroke=cell, sw=2.5 if v=="v3" else 1.6))
            s.append(txt(cx+cw/2, yy+row_h/2+18, val, size=46, weight="bold", fill=cell, anchor="middle"))
    # footnote
    s.append(txt(x0+6, H-26, "v2’s hallucination bump (4% → 12%) is real — memory added unsourced numbers; the v3 grounding contract drove it to 0%.", size=14.5, fill=MUTE))
    render("staircase", W, H, "\n".join(s))

# =========================================================================
# G3 — Orchestration A/B  (874 x 680)
# =========================================================================
def orchestration_ab():
    W, H = 874, 680
    s = []
    s.append(txt(W/2, 40, "SAME v3 PLANNER · 25 SCENARIOS · ONLY ORCHESTRATION CHANGES", size=13.5, weight="bold", fill=FAINT, anchor="middle", ls=1))
    cards = [
        (24,  "Hand-written loop", BLUE, BLUE_LT, "100%", "17", "lines of orchestration", "linear planner"),
        (454, "LangGraph", PURPLE, PURPLE_LT, "100%", "38", "lines (2.2×)", "branching + HITL"),
    ]
    cw = 396; cy = 64; ch = 360
    for x, name, color, lt, qual, lines, lines_sub, foot in cards:
        s.append(rect(x, cy, cw, ch, r=16, fill=PANEL, stroke=color, sw=2.4)); s.append(rect(x, cy, 6, ch, r=3, fill=color))
        s.append(txt(x+cw/2, cy+50, name, size=24, weight="bold", fill=lt, anchor="middle"))
        s.append(txt(x+cw/2, cy+120, qual, size=64, weight="bold", fill=GREEN_LT, anchor="middle"))
        s.append(txt(x+cw/2, cy+152, "scenario quality", size=14, fill=MUTE, anchor="middle"))
        s.append(f'<line x1="{x+40}" y1="{cy+182}" x2="{x+cw-40}" y2="{cy+182}" stroke="{LINE}" stroke-width="1.5"/>')
        s.append(txt(x+cw/2, cy+250, lines, size=58, weight="bold", fill=lt, anchor="middle", family=MONO))
        s.append(txt(x+cw/2, cy+282, lines_sub, size=14.5, fill=MUTE, anchor="middle"))
        s.append(rect(x+cw/2-90, cy+306, 180, 30, r=15, fill=PANEL_DK, stroke=color, sw=1))
        s.append(txt(x+cw/2, cy+326, foot, size=13.5, fill=lt, anchor="middle"))
    # equals / vs markers between cards
    s.append(txt(W/2, cy+128, "=", size=44, weight="bold", fill=MUTE, anchor="middle"))
    s.append(txt(W/2, cy+250, "vs", size=26, weight="bold", fill=MUTE, anchor="middle"))
    # decision strip
    s.append(rect(24, 452, W-48, 196, r=16, fill=PANEL_DK, stroke=LINE, sw=1.5))
    s.append(txt(48, 492, "Decision", size=18, weight="bold", fill=FG))
    s.append(mtxt(48, 524, [
        "Hand-loop for the linear Trip Planner — less code, fully legible.",
        "LangGraph for the branching, human-in-the-loop Prospector —",
        "where interrupt + durable checkpoints actually earn their cost.",
    ], size=15.5, fill=MUTE, lh=26))
    s.append(txt(48, 624, "Frameworks are not a quality lever — claiming so would be a confound.", size=15.5, weight="bold", fill=BLUE_LT))
    render("orchestration_ab", W, H, "\n".join(s))

if __name__ == "__main__":
    architecture()
    staircase()
    orchestration_ab()
    print("done")
