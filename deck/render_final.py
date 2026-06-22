#!/usr/bin/env python3
"""Render the three FINAL deck graphics at full 1920x1080 (16:9) to high-res PNG,
reproducing the approved Canva layouts (build_pptx.py / build_arch_simple.py).
Hosted on GitHub raw, then inserted full-bleed into deck slides 4/6/15.
"""
import os, html, cairosvg
OUT=os.path.join(os.path.dirname(__file__),"assets"); os.makedirs(OUT,exist_ok=True)
NAVY="#0B2A3A"; PANEL="#102E41"; PANEL2="#143A50"; PANEL_DK="#0C2535"
BLUE="#5BA8C8"; BLUE_LT="#95C5D9"; GREEN="#4A8C5C"; GREEN_LT="#7FBE8E"
OCHRE="#B7892F"; CLAY="#B3473B"; CLAY_LT="#E0A59B"; PURPLE="#7A3DB8"; PURPLE_LT="#A878DA"
FG="#EAF2F6"; MUTE="#9DB6C2"; FAINT="#6E8C9C"; LINE="#3E5C6E"
F="DejaVu Sans"; MONO="DejaVu Sans Mono"
def esc(s): return html.escape(str(s),quote=True)
def rect(x,y,w,h,r=14,fill=PANEL,stroke=None,sw=2,dash=None):
    a=f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" ry="{r}" fill="{fill}"'
    if stroke: a+=f' stroke="{stroke}" stroke-width="{sw}"'
    if dash: a+=f' stroke-dasharray="{dash}"'
    return a+"/>"
def txt(x,y,s,size=16,fill=FG,weight="normal",anchor="start",family=F,ls=None):
    a=f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" fill="{fill}" font-weight="{weight}" text-anchor="{anchor}"'
    if ls is not None: a+=f' letter-spacing="{ls}"'
    return a+f'>{esc(s)}</text>'
def cbox(x,y,w,h,lines,fill=PANEL,stroke=LINE,sw=1.6,r=14,valign="middle",padx=16,padtop=14):
    """box with stacked text lines. lines: dict(t,size,color,b,font,align,sb,lh)."""
    out=[rect(x,y,w,h,r=r,fill=fill,stroke=stroke,sw=sw)]
    lhs=[ln.get("lh",ln["size"]*1.32) for ln in lines]
    total=sum(lhs)+sum(ln.get("sb",0) for ln in lines)
    cur = y+(h-total)/2 if valign=="middle" else y+padtop
    for ln,lh in zip(lines,lhs):
        cur+=ln.get("sb",0); base=cur+ln["size"]*0.80
        al=ln.get("align","start")
        tx = x+w/2 if al=="middle" else (x+w-padx if al=="end" else x+padx)
        out.append(txt(tx,base,ln["t"],size=ln["size"],fill=ln["color"],weight=("bold" if ln.get("b") else "normal"),anchor=al,family=ln.get("font",F)))
        cur+=lh
    return "\n".join(out)
def lbl(x,y,s,size,fill=FG,b=False,anchor="start",family=F,ls=None):
    return txt(x,y+2+size*0.82,s,size=size,fill=fill,weight=("bold" if b else "normal"),anchor=anchor,family=family,ls=ls)
def defs():
    def m(i,c): return (f'<marker id="{i}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>')
    return "<defs>"+m("ab",BLUE)+m("ag",GREEN_LT)+m("am",MUTE)+m("ap",PURPLE_LT)+m("ac",CLAY)+"</defs>"
def arr(x1,y1,x2,y2,color=MUTE,sw=2.2,dash=None,mk="am"):
    a=f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{sw}"'
    if dash: a+=f' stroke-dasharray="{dash}"'
    return a+f' marker-end="url(#{mk})"/>'
def render(name,body,scale=2,W=1920,H=1080):
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">{defs()}<rect width="{W}" height="{H}" fill="{NAVY}"/>{body}</svg>'
    open(os.path.join(OUT,name+".svg"),"w").write(svg)
    cairosvg.svg2png(bytestring=svg.encode(),write_to=os.path.join(OUT,name+".png"),output_width=W*scale,output_height=H*scale)
    print("wrote",name+".png")

# ---------------- simplified architecture ----------------
def arch():
    s=[]
    s.append(lbl(70,34,"Two Agents, One Trustworthy Spine",32,FG,b=True))
    s.append(lbl(70,92,"Two agents share one deterministic, guardrailed spine.",15,MUTE))
    for cx,t in [(185,"REQUESTS"),(550,"ORCHESTRATION"),(1015,"SHARED TRUSTWORTHY SPINE"),(1560,"DATA SOURCES")]:
        s.append(lbl(cx,150,t,11,FAINT,b=True,anchor="middle",ls=1.5))
    s.append(cbox(70,210,230,120,[{"t":"Trip Planner","size":15,"color":BLUE_LT,"b":True,"align":"middle"},{"t":"“where to fish?”","size":11,"color":MUTE,"align":"middle","sb":4}],stroke=BLUE,sw=2))
    s.append(cbox(70,470,230,120,[{"t":"Prospector","size":15,"color":PURPLE_LT,"b":True,"align":"middle"},{"t":"“find new trout water?”","size":11,"color":MUTE,"align":"middle","sb":4}],stroke=PURPLE,sw=2))
    s.append(cbox(360,210,420,150,[{"t":"Trip Planner","size":18,"color":BLUE_LT,"b":True},{"t":"hand-written tool loop","size":11,"color":MUTE,"sb":3},{"t":"Haiku retrieval → Sonnet ranking","size":11,"color":FG,"font":MONO,"sb":6}],stroke=BLUE,sw=2))
    s.append(cbox(360,470,420,150,[{"t":"Prospector","size":18,"color":PURPLE_LT,"b":True},{"t":"LangGraph","size":11,"color":MUTE,"sb":3},{"t":"branching · human-in-the-loop","size":11,"color":FG,"sb":6}],stroke=PURPLE,sw=2))
    s.append(rect(800,180,430,690,r=18,fill="#0E3144",stroke=BLUE,sw=2))
    def stage(y,h,color,title,tag=None,emph=False):
        s.append(cbox(820,y,390,h,[{"t":title,"size":16,"color":FG,"b":True}],fill=(PANEL2 if emph else PANEL),stroke=color,sw=(3 if emph else 2)))
        if tag: s.append(lbl(1196,y+h/2-9,tag,10,(GREEN_LT if color==GREEN else color),b=True,anchor="end"))
    stage(220,82,BLUE,"MCP tool belt","retrieval")
    stage(340,92,GREEN,"Deterministic scorer","the oracle",emph=True)
    stage(470,82,OCHRE,"Grounding contract")
    stage(580,92,CLAY,"Guardrail veto","rules decide")
    s.append(lbl(1015,710,"the model advises · the rules decide",13,BLUE_LT,b=True,anchor="middle"))
    s.append(arr(1015,302,1015,338)); s.append(arr(1015,432,1015,468)); s.append(arr(1015,552,1015,578))
    names=["USGS NWIS","USGS NLDI","NOAA","State ArcGIS","PAD-US","Postgres"]; gx=[1280,1570]; gy=[210,300,390]
    for i,nm in enumerate(names):
        s.append(cbox(gx[i%2],gy[i//2],250,72,[{"t":nm,"size":14,"color":FG,"b":True,"align":"middle"}],stroke=LINE,sw=1.4))
    s.append(arr(1280,246,1214,265,color=BLUE,mk="ab")); s.append(lbl(1230,244,"fetch",10,BLUE_LT,anchor="end"))
    s.append(lbl(1710,478,"OUTPUT",11,FAINT,b=True,anchor="middle",ls=1.5))
    s.append(cbox(1280,506,275,120,[{"t":"$0.02","size":34,"color":GREEN_LT,"b":True,"align":"middle"},{"t":"per decision","size":11,"color":MUTE,"align":"middle","sb":2}],stroke=GREEN,sw=2))
    s.append(cbox(1575,506,275,120,[{"t":"17–18 s","size":30,"color":BLUE_LT,"b":True,"align":"middle"},{"t":"latency","size":11,"color":MUTE,"align":"middle","sb":2}],stroke=BLUE,sw=2))
    s.append(lbl(1565,642,"→ ranked, grounded recommendations + guardrail verdicts, on the map",10.5,MUTE,anchor="middle"))
    s.append(arr(300,270,356,270,color=BLUE,mk="ab")); s.append(arr(300,530,356,530,color=PURPLE,mk="ap"))
    s.append(arr(780,285,798,300,color=BLUE,mk="ab")); s.append(arr(780,525,798,470,color=PURPLE,mk="ap"))
    s.append(arr(1230,610,1278,566,color=CLAY,sw=2.4,mk="ac"))
    s.append(rect(70,900,1780,110,r=14,fill=PANEL_DK,stroke=BLUE,sw=1.6,dash="7 6"))
    s.append(lbl(92,924,"OFFLINE EVAL HARNESS",11,BLUE_LT,b=True,ls=1))
    s.append(lbl(92,956,"The scorer here is the eval oracle — same code · validated on 25 planner scenarios + an honest discovery eval",11,MUTE))
    s.append(arr(812,900,816,434,color=GREEN_LT,sw=1.8,dash="6 5",mk="ag"))
    render("final_architecture","\n".join(s))

# ---------------- staircase ----------------
def staircase():
    s=[]
    s.append(lbl(70,36,"Trip Planner: The v0 → v3 Staircase",32,FG,b=True))
    s.append(lbl(70,96,"Grounding + guardrails turn a confident liar into a trustworthy assistant (25 scenarios)",15,MUTE))
    cols=[("v0","naive prompt, no tools",CLAY),("v1","tool-grounded",BLUE),("v2","+ catch-log memory",OCHRE),("v3","+ guardrails & grounding",GREEN)]
    rows=[("Recommendation agreement","higher is better",["8%","100%","100%","100%"],"high"),
          ("Safety violations","lower is better",["16%","0%","0%","0%"],"low"),
          ("Hallucinated readings","lower is better",["100%","4%","12%","0%"],"low")]
    LBLW=330; x0=70; gap=18; cw=(1850-x0-LBLW-gap*4)/4; top=170; headh=104; rowh=150; rgap=14
    for i,(v,sub,color) in enumerate(cols):
        cx=x0+LBLW+i*(cw+gap); emph=(v=="v3")
        s.append(cbox(cx,top,cw,headh,[{"t":v,"size":30,"color":color,"b":True,"align":"middle"},{"t":sub,"size":11,"color":(FG if emph else MUTE),"align":"middle","sb":2}],fill=(PANEL2 if emph else PANEL),stroke=color,sw=(3 if emph else 2)))
    ry=top+headh+22
    for ri,(lab,note,vals,good) in enumerate(rows):
        yy=ry+ri*(rowh+rgap)
        s.append(cbox(x0,yy,LBLW-10,rowh,[{"t":lab,"size":17,"color":FG,"b":True},{"t":note,"size":11,"color":FAINT,"sb":3}]) .replace(f'<rect x="{x0}"',f'<rect opacity="0" x="{x0}"'))
        for ci,(v,sub,color) in enumerate(cols):
            cx=x0+LBLW+ci*(cw+gap); val=vals[ci]; num=float(val.replace("%",""))
            cell=(GREEN if num>=90 else (OCHRE if num>=50 else CLAY)) if good=="high" else (GREEN if num==0 else (OCHRE if num<=15 else CLAY))
            s.append(cbox(cx,yy,cw,rowh,[{"t":val,"size":40,"color":cell,"b":True,"align":"middle"}],stroke=cell,sw=(2.6 if v=="v3" else 1.6)))
    s.append(lbl(70,1004,"v2’s hallucination bump (4% → 12%) is real — memory added unsourced numbers; the v3 grounding contract drove it to 0%.",14,MUTE))
    render("final_staircase","\n".join(s))

# ---------------- orchestration A/B ----------------
def orch():
    s=[]
    s.append(lbl(70,36,"Engineering Judgment: Right Tool for the Job",32,FG,b=True))
    s.append(lbl(960,150,"SAME v3 PLANNER · 25 SCENARIOS · ONLY ORCHESTRATION CHANGES",13,FAINT,b=True,anchor="middle",ls=1))
    cards=[(360,"Hand-written loop",BLUE,BLUE_LT,"17","lines of orchestration","linear planner"),
           (1010,"LangGraph",PURPLE,PURPLE_LT,"38","lines (2.2×)","branching + HITL")]
    cy=200; ch=420; cw=560
    for x,name,color,lt,lines,lsub,foot in cards:
        s.append(rect(x,cy,cw,ch,r=18,fill=PANEL,stroke=color,sw=2.4))
        s.append(lbl(x+cw/2,cy+28,name,24,lt,b=True,anchor="middle"))
        s.append(lbl(x+cw/2,cy+96,"100%",60,GREEN_LT,b=True,anchor="middle"))
        s.append(lbl(x+cw/2,cy+196,"scenario quality",13,MUTE,anchor="middle"))
        s.append(f'<line x1="{x+50}" y1="{cy+236}" x2="{x+cw-50}" y2="{cy+236}" stroke="{LINE}" stroke-width="1.4"/>')
        s.append(lbl(x+cw/2,cy+252,lines,52,lt,b=True,anchor="middle",family=MONO))
        s.append(lbl(x+cw/2,cy+346,lsub,13,MUTE,anchor="middle"))
        s.append(rect(x+cw/2-95,cy+374,190,32,r=16,fill=PANEL_DK,stroke=color,sw=1))
        s.append(lbl(x+cw/2,cy+382,foot,12,lt,anchor="middle"))
    s.append(lbl(960,300,"=",40,MUTE,b=True,anchor="middle"))
    s.append(lbl(960,456,"vs",22,MUTE,b=True,anchor="middle"))
    s.append(rect(360,664,1210,340,r=16,fill=PANEL_DK,stroke=LINE,sw=1.5))
    dy=664+38
    s.append(lbl(388,dy,"Decision",16,FG,b=True)); dy+=44
    for ln in ["Hand-loop for the linear Trip Planner — less code, fully legible.",
               "LangGraph for the branching, human-in-the-loop Prospector — where",
               "interrupt + durable checkpoints actually earn their cost."]:
        s.append(lbl(388,dy,ln,15.5,MUTE)); dy+=30
    dy+=14
    s.append(lbl(388,dy,"Frameworks are not a quality lever — claiming so would be a confound.",15.5,BLUE_LT,b=True))
    render("final_orchestration","\n".join(s))

arch(); staircase(); orch(); print("done")
