#!/usr/bin/env python3
"""Two evidence cards built from REAL agent/eval outputs, in the deck palette.
Transparent background so they composite onto the indigo slides.
 - trace_card.png  -> slide 5 (Operating Principle / legibility)
 - eval_card.png   -> slide 8 (How I Measured It / the eval is the product)
Source: agent/eval/sample_trace.md, sample_cli_live.txt, report.md
"""
import os, html, cairosvg
OUT = os.path.join(os.path.dirname(__file__), "assets"); os.makedirs(OUT, exist_ok=True)
PANEL="#322F52"; TERM="#1F1E38"; PANEL_DK="#262540"; FOOT="#191830"
YEL="#F0C84C"; YEL_LT="#F7DD85"; GREEN="#6BC598"; RED="#E0685C"
FG="#FFFFFF"; MUTE="#C2C0D6"; FAINT="#8E8CAB"; LINE="#55527E"
F="DejaVu Sans"; MONO="DejaVu Sans Mono"
def esc(s): return html.escape(str(s), quote=True)
def rect(x,y,w,h,r=14,fill=PANEL,stroke=None,sw=1.5):
    a=f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" ry="{r}" fill="{fill}"'
    if stroke: a+=f' stroke="{stroke}" stroke-width="{sw}"'
    return a+"/>"
def line(x1,y1,x2,y2,color=LINE,sw=1.2,dash=None):
    a=f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{sw}"'
    if dash: a+=f' stroke-dasharray="{dash}"'
    return a+"/>"
def txt(x,y,s,size=14,fill=FG,weight="normal",anchor="start",family=F,ls=None):
    a=f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" fill="{fill}" font-weight="{weight}" text-anchor="{anchor}"'
    if ls is not None: a+=f' letter-spacing="{ls}"'
    return a+f'>{esc(s)}</text>'
def lbl(x,y,s,size=14,fill=FG,b=False,anchor="start",family=F,ls=None):
    return txt(x,y+2+size*0.82,s,size=size,fill=fill,weight=("bold" if b else "normal"),anchor=anchor,family=family,ls=ls)
def render(name,w,h,body,scale=2):
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{body}</svg>'
    open(os.path.join(OUT,name+".svg"),"w").write(svg)
    cairosvg.svg2png(bytestring=svg.encode(),write_to=os.path.join(OUT,name+".png"),output_width=w*scale,output_height=h*scale)
    print("wrote",name+".png")

# ---------------- trace card (slide 5) ----------------
def trace_card():
    W,H=820,600; s=[]
    s.append(rect(1,1,W-2,H-2,r=18,fill=TERM,stroke=LINE,sw=1.5))
    s.append(rect(1,1,6,H-2,r=3,fill=YEL))  # left accent
    s.append(lbl(28,22,"ONE DECISION, FULLY TRACED",12.5,FAINT,b=True,ls=1.5))
    s.append(lbl(28,46,"“Beaver Creek near Hagerstown - worth it this weekend?”",14,FG))
    s.append(lbl(28,76,"Haiku drives retrieval  ·  Sonnet writes the ranking",12.5,MUTE))
    s.append(line(28,104,W-26,104))
    s.append(lbl(28,114,"TOOLS CALLED, IN ORDER",11,FAINT,b=True,ls=1))
    tools=[("1","get_candidate_rivers",""),("2","get_forecast","NOAA · live"),
           ("3","get_river_conditions","2 rivers"),("4","get_access","2 rivers"),
           ("5","get_user_catch_history","")]
    y=144
    for n,name,desc in tools:
        s.append(txt(36,y,n,size=13.5,fill=YEL,weight="bold",family=MONO))
        s.append(txt(70,y,name,size=13.5,fill=FG,family=MONO))
        if desc: s.append(txt(360,y,desc,size=12,fill=MUTE,family=MONO))
        y+=29
    s.append(line(28,300,W-26,300))
    s.append(txt(30,332,"✗",size=15,fill=RED,weight="bold"))
    s.append(txt(56,332,"beaver-creek-md  blocked: private-only access, no public entry",size=13,fill="#EBA59C"))
    s.append(txt(30,372,"✓",size=15,fill=GREEN,weight="bold"))
    s.append(txt(56,372,"Gunpowder Falls  (green)",size=14.5,fill=GREEN,weight="bold"))
    s.append(txt(56,398,"56°F  ·  flow 1.09x median  ·  6 public access points",size=12.5,fill=MUTE))
    s.append(txt(56,422,"within your proven brown + rainbow trout bands; fresh reading",size=12,fill=FAINT))
    # footer band
    s.append(rect(1,H-58,W-2,57,r=18,fill=FOOT))
    s.append(rect(1,H-58,W-2,20,r=0,fill=FOOT))  # square top of footer band
    s.append(txt(W/2,H-22,"grounding ok ✓    unsourced: none    ·    16.6 s    ·    $0.023",size=13,fill=MUTE,anchor="middle",family=MONO))
    render("trace_card",W,H,"\n".join(s))

# ---------------- eval scoreboard card (slide 8) ----------------
def eval_card():
    W,H=820,600; s=[]
    s.append(rect(1,1,W-2,H-2,r=18,fill=PANEL,stroke=LINE,sw=1.5))
    s.append(lbl(28,22,"v3 EVAL  ·  25 SCENARIOS",12.5,FAINT,b=True,ls=1.5))
    # two zero-stat chips
    for cx0,label in [(28,"safety violations"),(414,"hallucinated readings")]:
        s.append(rect(cx0,52,378,92,r=12,fill=PANEL_DK,stroke=GREEN,sw=1.6))
        cx=cx0+189
        s.append(txt(cx,116,"0",size=42,fill=GREEN,weight="bold",anchor="middle"))
        s.append(txt(cx,138,label,size=12.5,fill=MUTE,anchor="middle"))
    s.append(lbl(28,162,"TOP-1 AGREEMENT, BY SCENARIO TYPE",11,FAINT,b=True,ls=1))
    cats=[("adversarial","memory"),("all-blocked","private"),("flood","stale"),
          ("ideal","tie"),("marginal","too-warm")]
    y=192
    for leftc,rightc in cats:
        for cx0,name in [(28,leftc),(414,rightc)]:
            s.append(rect(cx0,y,378,50,r=10,fill=PANEL_DK,stroke=LINE,sw=1))
            s.append(lbl(cx0+18,y+15,name,13.5,FG))
            s.append(txt(cx0+332,y+33,"100%",size=14,fill=GREEN,weight="bold",anchor="end"))
            s.append(txt(cx0+360,y+33,"✓",size=14,fill=GREEN,weight="bold",anchor="end"))
        y+=60
    s.append(txt(28,560,"Oracle: BlueLiner's own deterministic scorer, parity-tested.",size=11.5,fill=FAINT))
    s.append(txt(28,580,"Agreement = top pick is safe and in the oracle's best-rated tier.",size=11.5,fill=FAINT))
    render("eval_card",W,H,"\n".join(s))

trace_card(); eval_card(); print("done")
