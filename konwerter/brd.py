# -*- coding: utf-8 -*-
"""KiCad 10 .kicad_pcb -> Eagle 7.5 .brd (XML).

Uruchamiac Pythonem KiCada (ma modul pcbnew):
  python.exe brd.py --pcb X.kicad_pcb --pro X.kicad_pro [--dru X.kicad_dru] --out X.brd
                    --map mapa_padow.json --raport raport_brd.json [--lib NAZWA]
mapa_padow.json  - dla schematu: obudowa -> {numer padu KiCad -> [pady Eagle]}, element -> obudowa
raport_brd.json  - statystyki, ostrzezenia, reguly .kicad_dru przeniesione / nieprzeniesione
"""
import sys, os, math, json, re, collections, argparse
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pcbnew
from common import f, ang, rot, ename, netname, a, arc_curve, align, EAGLE_VERSION, escape
import sexp

ap = argparse.ArgumentParser()
ap.add_argument("--pcb", required=True); ap.add_argument("--pro", required=True)
ap.add_argument("--dru"); ap.add_argument("--out", required=True)
ap.add_argument("--map", required=True); ap.add_argument("--raport", required=True)
ap.add_argument("--lib")
ap.add_argument("--szablony", default=os.path.join(os.path.dirname(HERE), "szablony"))
A = ap.parse_args()

b = pcbnew.LoadBoard(A.pcb)
if b is None:
    sys.exit("BLAD: nie mozna wczytac plytki %s" % A.pcb)
PROJ = os.path.splitext(os.path.basename(A.pcb))[0]
LIB = ename(A.lib or PROJ)
warn = collections.Counter()

MM = 1e6
def mm(v): return v / MM

bb = b.GetBoardEdgesBoundingBox()
if bb.GetWidth() <= 0 or bb.GetHeight() <= 0:
    bb = b.ComputeBoundingBox(False)
    warn["brak konturu Edge.Cuts - poczatek ukladu z obwiedni elementow"] += 1
OX, OY = bb.GetLeft(), bb.GetBottom()          # lewy dolny rog konturu = (0,0) w Eagle
def X(v): return mm(v - OX)
def Y(v): return mm(OY - v)

# ---------------------------------------------------------------- warstwy miedzi
custack = list(b.GetEnabledLayers().CuStack())    # F.Cu, In1.Cu, ..., B.Cu
inner = [l for l in custack if l not in (pcbnew.F_Cu, pcbnew.B_Cu)]
if len(inner) > 14:
    sys.exit("BLAD: Eagle ma najwyzej 16 warstw miedzi, plytka ma %d" % len(custack))
CU = {pcbnew.F_Cu: 1, pcbnew.B_Cu: 16}
h = (len(inner) + 1) // 2
for i, l in enumerate(inner[:h]):
    CU[l] = 2 + i
for i, l in enumerate(inner[h:]):
    CU[l] = 15 - (len(inner) - h - 1) + i
EAGLE_CU = sorted(CU.values())
LAYERMAP = {b.GetLayerName(k): v for k, v in CU.items()}

GLAYER = {
    pcbnew.F_SilkS: 21, pcbnew.B_SilkS: 22, pcbnew.F_Fab: 51, pcbnew.B_Fab: 52,
    pcbnew.F_CrtYd: 39, pcbnew.B_CrtYd: 40, pcbnew.F_Mask: 29, pcbnew.B_Mask: 30,
    pcbnew.F_Paste: 31, pcbnew.B_Paste: 32, pcbnew.Dwgs_User: 48, pcbnew.Cmts_User: 49,
    pcbnew.Edge_Cuts: 20, pcbnew.Margin: 48, pcbnew.Eco1_User: 48, pcbnew.Eco2_User: 48,
    pcbnew.F_Adhes: 35, pcbnew.B_Adhes: 36,
}
GLAYER.update(CU)
NAMELAYER = {pcbnew.F_SilkS: 25, pcbnew.B_SilkS: 26}
VALLAYER = {pcbnew.F_SilkS: 27, pcbnew.B_SilkS: 28}

# ---------------------------------------------------------------- proporcja padow "long"
ratios = collections.Counter()
for fp in b.GetFootprints():
    for p in fp.Pads():
        if p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH and p.GetShape(pcbnew.F_Cu) == pcbnew.PAD_SHAPE_OVAL:
            sz = p.GetSize(pcbnew.F_Cu)
            if sz.x != sz.y:
                ratios[round(max(sz.x, sz.y) / min(sz.x, sz.y), 2)] += 1
LONG_RATIO = ratios.most_common(1)[0][0] if ratios else 2.0   # Eagle: jedno wydluzenie na cala plytke


# ---------------------------------------------------------------- grafika -> XML
def W(x1, y1, x2, y2, w, L, curve=None):
    return '<wire x1="%s" y1="%s" x2="%s" y2="%s" width="%s" layer="%d"%s/>' % (
        f(x1), f(y1), f(x2), f(y2), f(w), L, (' curve="%s"' % f(curve, 3)) if curve else "")


def shape_xml(g, L, conv):
    out = []
    w = mm(g.GetWidth())
    st = g.GetShape()
    filled = g.IsSolidFill() if hasattr(g, "IsSolidFill") else g.IsFilled()
    if st == pcbnew.SHAPE_T_SEGMENT:
        x1, y1 = conv(g.GetStart()); x2, y2 = conv(g.GetEnd())
        if abs(x1 - x2) + abs(y1 - y2) > 1e-6:
            out.append(W(x1, y1, x2, y2, w, L))
    elif st == pcbnew.SHAPE_T_ARC:
        x1, y1 = conv(g.GetStart()); xm, ym = conv(g.GetArcMid()); x2, y2 = conv(g.GetEnd())
        out.append(W(x1, y1, x2, y2, w, L, arc_curve(x1, y1, xm, ym, x2, y2)))
    elif st == pcbnew.SHAPE_T_CIRCLE:
        cx, cy = conv(g.GetCenter()); r = mm(g.GetRadius())
        out.append('<circle x="%s" y="%s" radius="%s" width="%s" layer="%d"/>' % (f(cx), f(cy), f(r), "0" if filled else f(w), L))
    elif st == pcbnew.SHAPE_T_RECT:
        pts = [conv(p) for p in g.GetRectCorners()]
        if filled:
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            out.append('<rectangle x1="%s" y1="%s" x2="%s" y2="%s" layer="%d"/>' % (f(min(xs)), f(min(ys)), f(max(xs)), f(max(ys)), L))
        if not filled or w > 0:
            for i in range(4):
                p, q = pts[i], pts[(i + 1) % 4]
                out.append(W(p[0], p[1], q[0], q[1], w, L))
    elif st == pcbnew.SHAPE_T_POLY:
        ps = g.GetPolyShape()
        for oi in range(ps.OutlineCount()):
            ol = ps.Outline(oi)
            pts = [conv(ol.CPoint(i)) for i in range(ol.PointCount())]
            if filled:
                out.append('<polygon width="%s" layer="%d">' % (f(max(w, 0.0)), L) +
                           "".join('<vertex x="%s" y="%s"/>' % (f(x), f(y)) for x, y in pts) + "</polygon>")
            else:
                for i in range(len(pts)):
                    p, q = pts[i], pts[(i + 1) % len(pts)]
                    out.append(W(p[0], p[1], q[0], q[1], w, L))
            if ps.HoleCount(oi):
                warn["otwory w wielokacie grafiki pominiete"] += 1
    elif st == pcbnew.SHAPE_T_BEZIER:
        pts = [conv(p) for p in (g.GetStart(), g.GetBezierC1(), g.GetBezierC2(), g.GetEnd())]
        for p, q in zip(pts, pts[1:]):
            out.append(W(p[0], p[1], q[0], q[1], w, L))
        warn["krzywa Beziera -> lamana"] += 1
    else:
        warn["nieobslugiwany ksztalt grafiki %s" % g.GetShapeStr()] += 1
    return out


def text_attrs(t, conv, layer, absolute_angle, mirror=False):
    x, y = conv(t.GetTextPos())
    size = mm(t.GetTextHeight())
    th = mm(t.GetTextThickness())
    ratio = int(round(min(31, max(0, th / size * 100)))) if size > 0 else 8
    hh = {pcbnew.GR_TEXT_H_ALIGN_LEFT: -1, pcbnew.GR_TEXT_H_ALIGN_CENTER: 0, pcbnew.GR_TEXT_H_ALIGN_RIGHT: 1}.get(t.GetHorizJustify(), 0)
    vv = {pcbnew.GR_TEXT_V_ALIGN_TOP: -1, pcbnew.GR_TEXT_V_ALIGN_CENTER: 0, pcbnew.GR_TEXT_V_ALIGN_BOTTOM: 1}.get(t.GetVertJustify(), 0)
    return 'x="%s" y="%s" size="%s" layer="%d" font="vector" ratio="%d" rot="%s" align="%s"' % (
        f(x), f(y), f(size), layer, ratio, rot(absolute_angle, mirror), align(hh, vv))


# ---------------------------------------------------------------- obudowy
def slot_mill(x, y, dx, dy, a_, L=46):
    """kontur otworu owalnego (warstwa 46 Milling)"""
    if dy > dx:
        dx, dy = dy, dx; a_ += 90
    r = dy / 2.0; hl = (dx - dy) / 2.0
    ca, sa = math.cos(math.radians(a_)), math.sin(math.radians(a_))
    def P(u, v): return (x + u * ca - v * sa, y + u * sa + v * ca)
    p1, p2, p3, p4 = P(-hl, -r), P(hl, -r), P(hl, r), P(-hl, r)
    return [W(p1[0], p1[1], p2[0], p2[1], 0, L), W(p2[0], p2[1], p3[0], p3[1], 0, L, 180),
            W(p3[0], p3[1], p4[0], p4[1], 0, L), W(p4[0], p4[1], p1[0], p1[1], 0, L, 180)]


def rect_poly(x, y, dx, dy, a_, L, w=0.0254):
    ca, sa = math.cos(math.radians(a_)), math.sin(math.radians(a_))
    hx, hy = dx / 2.0 - w / 2.0, dy / 2.0 - w / 2.0
    pts = [(-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)]
    return '<polygon width="%s" layer="%d">' % (f(w), L) + "".join(
        '<vertex x="%s" y="%s"/>' % (f(x + u * ca - v * sa), f(y + u * sa + v * ca)) for u, v in pts) + "</polygon>"


def build_package(c):
    """c: kopia footprintu na stronie TOP, orientacja 0, pozycja 0.
    Zwraca (linie XML, nazwy padow Eagle w kolejnosci c.Pads(), mapa numer->nazwy)"""
    conv = lambda p: (mm(p.x), -mm(p.y))
    items, names = [], []
    used = collections.Counter()
    pmap = collections.OrderedDict()
    for p in c.Pads():
        num = p.GetNumber()
        attr = p.GetAttribute()
        x, y = conv(p.GetPosition())
        pa = p.GetOrientationDegrees()
        if attr == pcbnew.PAD_ATTRIB_NPTH:
            ds = p.GetDrillSize()
            dx, dy = mm(ds.x), mm(ds.y)
            items.append('<hole x="%s" y="%s" drill="%s"/>' % (f(x), f(y), f(min(dx, dy))))
            if abs(dx - dy) > 1e-4:
                items += slot_mill(x, y, dx, dy, pa)
                warn["otwor owalny -> warstwa 46 Milling"] += 1
            names.append(None)
            continue
        lay = p.GetLayerSet()
        if attr != pcbnew.PAD_ATTRIB_PTH and not (lay.Contains(pcbnew.F_Cu) or lay.Contains(pcbnew.B_Cu)):
            sz = p.GetSize(pcbnew.F_Cu)
            for kl, L in ((pcbnew.F_Paste, 31), (pcbnew.B_Paste, 32), (pcbnew.F_Mask, 29), (pcbnew.B_Mask, 30)):
                if lay.Contains(kl):
                    items.append('<rectangle x1="%s" y1="%s" x2="%s" y2="%s" layer="%d" rot="%s"/>' % (
                        f(x - mm(sz.x) / 2), f(y - mm(sz.y) / 2), f(x + mm(sz.x) / 2), f(y + mm(sz.y) / 2), L, rot(pa)))
            warn["pad bez miedzi (pasta/maska) -> prostokat na tCream/tStop"] += 1
            names.append(None)
            continue
        base = ename(num) if num else "NC"
        used[base] += 1
        nm = base if used[base] == 1 else "%s_%d" % (base, used[base])
        while nm in names:
            used[base] += 1; nm = "%s_%d" % (base, used[base])
        names.append(nm)
        pmap.setdefault(num, []).append(nm)
        stop = "yes" if (lay.Contains(pcbnew.F_Mask) or lay.Contains(pcbnew.B_Mask)) else "no"
        off = p.GetOffset(pcbnew.F_Cu)
        if off.x or off.y:
            warn["przesuniecie padu wzgledem otworu pominiete"] += 1
        sh = p.GetShape(pcbnew.F_Cu)
        sz = p.GetSize(pcbnew.F_Cu)
        sx, sy = mm(sz.x), mm(sz.y)
        if attr in (pcbnew.PAD_ATTRIB_SMD, pcbnew.PAD_ATTRIB_CONN):
            L = 1 if lay.Contains(pcbnew.F_Cu) else 16
            if sh == pcbnew.PAD_SHAPE_RECT: rnd = 0
            elif sh == pcbnew.PAD_SHAPE_ROUNDRECT: rnd = int(round(200 * p.GetRoundRectRadiusRatio(pcbnew.F_Cu)))
            elif sh in (pcbnew.PAD_SHAPE_CIRCLE, pcbnew.PAD_SHAPE_OVAL): rnd = 100
            else:
                rnd = 0; warn["pad SMD o ksztalcie %s -> prostokat" % p.ShowPadShape(pcbnew.F_Cu)] += 1
            cream = "yes" if (lay.Contains(pcbnew.F_Paste) or lay.Contains(pcbnew.B_Paste)) else "no"
            items.append('<smd name=%s x="%s" y="%s" dx="%s" dy="%s" layer="%d" roundness="%d" rot="%s" stop="%s" cream="%s"/>' % (
                a(nm), f(x), f(y), f(sx), f(sy), L, min(100, rnd), rot(pa), stop, cream))
            continue
        # PTH
        ds = p.GetDrillSize()
        dx, dy = mm(ds.x), mm(ds.y)
        drill = min(dx, dy)
        extra = []
        mn, mx_ = min(sx, sy), max(sx, sy)
        prot = pa
        if sh == pcbnew.PAD_SHAPE_CIRCLE or (sh == pcbnew.PAD_SHAPE_OVAL and abs(sx - sy) < 1e-4):
            shape, dia = "round", sx
        elif sh == pcbnew.PAD_SHAPE_RECT and abs(sx - sy) < 1e-4:
            shape, dia = "square", sx
        elif sh == pcbnew.PAD_SHAPE_OVAL:
            if abs(mx_ / mn - LONG_RATIO) < 0.05:
                shape, dia = "long", mn
                if sy > sx: prot = pa + 90
            else:
                shape, dia = "round", mn
                hl = (mx_ - mn) / 2.0
                aa = pa + (90 if sy > sx else 0)
                ca, sa = math.cos(math.radians(aa)), math.sin(math.radians(aa))
                for L in (1, 16):
                    extra.append(W(x - hl * ca, y - hl * sa, x + hl * ca, y + hl * sa, mn, L))
                warn["pad owalny THT %.2fx%.2f: okragly + miedz w obudowie" % (sx, sy)] += 1
        else:
            shape, dia = "square", mn
            if abs(sx - sy) > 1e-4 or sh != pcbnew.PAD_SHAPE_RECT:
                for L in (1, 16):
                    extra.append(rect_poly(x, y, sx, sy, pa, L))
                warn["pad THT %s %.2fx%.2f: kwadrat + wielokat miedzi" % (p.ShowPadShape(pcbnew.F_Cu), sx, sy)] += 1
        items.append('<pad name=%s x="%s" y="%s" drill="%s" diameter="%s" shape="%s" rot="%s" stop="%s"/>' % (
            a(nm), f(x), f(y), f(drill), f(dia), shape, rot(prot), stop))
        items += extra
        if abs(dx - dy) > 1e-4:
            items += slot_mill(x, y, dx, dy, pa)
            warn["otwor owalny -> warstwa 46 Milling"] += 1
    for g in c.GraphicalItems():
        L = GLAYER.get(g.GetLayer())
        if L is None:
            warn["warstwa grafiki obudowy %s pominieta" % g.GetLayerName()] += 1
            continue
        if g.GetClass() == "PCB_SHAPE":
            items += shape_xml(g, L, conv)
        elif g.GetClass() == "PCB_TEXT":
            if not g.IsVisible():
                continue
            txt = g.GetText()
            txt = txt.replace("${REFERENCE}", ">NAME").replace("${VALUE}", ">VALUE").replace("%R", ">NAME").replace("%V", ">VALUE")
            # Eagle sam trzyma tekst czytelnie, wiec 180 st. == 0 st.
            items.append("<text %s>%s</text>" % (text_attrs(g, conv, L, g.GetTextAngleDegrees() % 180.0), escape(txt)))
        else:
            warn["grafika obudowy %s pominieta" % g.GetClass()] += 1
    items = sorted(set(norm_wire(i) for i in items))
    return items, names, pmap


_wre = re.compile(r'<wire x1="([^"]*)" y1="([^"]*)" x2="([^"]*)" y2="([^"]*)" (width="[^"]*" layer="[^"]*")/>')
def norm_wire(s):
    m = _wre.fullmatch(s)
    if not m:
        return s
    p1 = (float(m.group(1)), float(m.group(2))); p2 = (float(m.group(3)), float(m.group(4)))
    if p2 < p1:
        return '<wire x1="%s" y1="%s" x2="%s" y2="%s" %s/>' % (m.group(3), m.group(4), m.group(1), m.group(2), m.group(5))
    return s


def canonical(fp):
    c = pcbnew.FOOTPRINT(fp)
    if c.IsFlipped():
        c.Flip(c.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    c.SetOrientationDegrees(0)
    c.SetPosition(pcbnew.VECTOR2I(0, 0))
    return c


def anchor_points(fp):
    pts = [p.GetPosition() for p in fp.Pads()]
    for g in fp.GraphicalItems():
        if g.GetClass() == "PCB_SHAPE" and g.GetShape() in (pcbnew.SHAPE_T_SEGMENT, pcbnew.SHAPE_T_RECT, pcbnew.SHAPE_T_CIRCLE, pcbnew.SHAPE_T_ARC):
            pts.append(g.GetStart()); pts.append(g.GetEnd())
    return pts


def fit_rotation(fp, c):
    E = (X(fp.GetPosition().x), Y(fp.GetPosition().y))
    P = [(X(p.x) - E[0], Y(p.y) - E[1]) for p in anchor_points(fp)]
    Q = [(mm(p.x), -mm(p.y)) for p in anchor_points(c)]
    mir = fp.IsFlipped()
    if mir:
        Q = [(-qx, qy) for qx, qy in Q]
    sd = sc = 0.0
    for (qx, qy), (px, py) in zip(Q, P):
        sd += qx * px + qy * py
        sc += qx * py - qy * px
    if abs(sd) + abs(sc) > 1e-9:
        th = math.degrees(math.atan2(sc, sd))
    else:   # brak punktow poza srodkiem - orientacja z KiCad
        th = fp.GetOrientationDegrees() * (-1 if mir else 1)
    th = round(th * 1000) / 1000.0
    ct, st = math.cos(math.radians(th)), math.sin(math.radians(th))
    res = max([math.hypot(qx * ct - qy * st - px, qx * st + qy * ct - py) for (qx, qy), (px, py) in zip(Q, P)] or [0])
    return th, mir, res


# ---------------------------------------------------------------- elementy i obudowy
packages = collections.OrderedDict()
pkg_by_sig, pkg_pmap, elem_pkg, pad_ename = {}, {}, {}, {}
elements = []
maxres = 0.0
for fp in b.GetFootprints():
    ref = fp.GetReference()
    c = canonical(fp)
    items, names, pmap = build_package(c)
    sig = "\n".join(items) + "|" + "|".join(str(n) for n in names)
    base = ename(fp.GetFPID().GetUniStringLibItemName())
    if sig in pkg_by_sig:
        pname = pkg_by_sig[sig]
    else:
        pname = base; k = 1
        while pname in packages:
            k += 1; pname = "%s_V%d" % (base, k)
        packages[pname] = items
        pkg_by_sig[sig] = pname
        pkg_pmap[pname] = pmap
    for i, n in enumerate(names):
        pad_ename[(ref, i)] = n
    th, mir, res = fit_rotation(fp, c)
    maxres = max(maxres, res)
    if res > 0.001:
        warn["dopasowanie obrotu > 1 um: %s (%.4f mm)" % (ref, res)] += 1
    if ename(ref) in elem_pkg:
        warn["powtorzone oznaczenie elementu %s" % ref] += 1
    elem_pkg[ename(ref)] = pname
    elements.append((fp, pname, th, mir))

# ---------------------------------------------------------------- klasy sieci + reguly .kicad_dru
pro = json.load(open(A.pro, encoding="utf-8"))
classes = pro.get("net_settings", {}).get("classes", [])
cls = {c["name"]: c for c in classes}
if "Default" not in cls:
    cls["Default"] = {"name": "Default", "clearance": 0.2, "track_width": 0.25, "via_drill": 0.4}
order = ["Default"] + [c["name"] for c in classes if c["name"] != "Default"]
if len(order) > 8:
    for n in order[8:]:
        warn["klasa sieci %s ponad limit 8 klas Eagle -> Default" % n] += 1
    order = order[:8]
cnum = {n: i for i, n in enumerate(order)}

dru_ok, dru_skip = [], []
special = {}
def to_mm(s):
    m = re.match(r"([-0-9.]+)\s*(mm|mil|in)?", s)
    v = float(m.group(1)); u = m.group(2) or "mm"
    return v * {"mm": 1, "mil": 0.0254, "in": 25.4}[u]
if A.dru and os.path.exists(A.dru):
    txt = "\n".join(l for l in open(A.dru, encoding="utf-8").read().splitlines() if not l.strip().startswith("#"))
    try:
        root = sexp.parse("(root " + txt + ")")
    except Exception as e:
        root = ["root"]; warn["nie mozna odczytac .kicad_dru: %s" % e] += 1
    for r in sexp.kids(root, "rule"):
        name = r[1]
        cond = sexp.val(r, "condition", "")
        cons = [c for c in sexp.kids(r, "constraint")]
        clr = [c for c in cons if c[1] == "clearance" and sexp.kid(c, "min")]
        if not clr:
            dru_skip.append((name, "ograniczenie inne niz clearance (min)")); continue
        if re.search(r"memberOf|intersects|NetName|Pad_Type|Layer|Reference|insideArea|enclosedByArea|Name|Footprint|existsOnLayer", cond):
            # wyjatki B.NetName != ... mozna pominac (regula ostrzejsza), reszta nie do przeniesienia
            c2 = re.sub(r"&&\s*B\.NetName\s*!=\s*'[^']*'", "", cond)
            if re.search(r"memberOf|intersects|NetName|Pad_Type|Layer|Reference|insideArea|enclosedByArea|Name|Footprint|existsOnLayer", c2):
                dru_skip.append((name, "warunek zalezny od elementu/sieci/warstwy")); continue
            note = "wyjatki B.NetName pominiete"
        else:
            note = ""
        Acl = set(re.findall(r"A\.NetClass\s*==\s*'([^']*)'", cond))
        Bpos = set(re.findall(r"B\.NetClass\s*==\s*'([^']*)'", cond))
        Bneg = set(re.findall(r"B\.NetClass\s*!=\s*'([^']*)'", cond))
        if not Acl:
            dru_skip.append((name, "brak warunku A.NetClass")); continue
        v = to_mm(sexp.kid(clr[0], "min")[1])
        Bset = Bpos if Bpos else (set(order) - Bneg)
        if re.search(r"\.Type\s*==", cond):
            note = (note + "; " if note else "") + "w Eagle dla wszystkich obiektow, nie tylko sciezek"
        npairs = 0
        for p in Acl:
            for q in Bset:
                if p in cnum and q in cnum:
                    k = tuple(sorted((cnum[p], cnum[q])))
                    special[k] = max(special.get(k, 0), v); npairs += 1
        if npairs:
            dru_ok.append((name, "%s mm, %d par klas%s" % (f(v, 3), npairs, ("; " + note) if note else "")))
        else:
            dru_skip.append((name, "klasy spoza listy klas"))
classes_xml = []
for n in order:
    i = cnum[n]; c = cls[n]
    cl = []
    for j in range(i + 1):
        v = special.get((j, i), max(c.get("clearance", 0.2), cls[order[j]].get("clearance", 0.2)))
        cl.append('<clearance class="%d" value="%s"/>' % (j, f(v)))
    classes_xml.append('<class number="%d" name=%s width="%s" drill="%s">%s</class>' % (
        i, a(ename(n)), f(c.get("track_width", 0)), f(c.get("via_drill", 0)), "".join(cl)))

# ---------------------------------------------------------------- reguly projektowe
ds = b.GetDesignSettings()
dr = open(os.path.join(A.szablony, "reguly_drc.xml"), encoding="utf-8").read().strip()
dr = re.sub(r'<designrules name="[^"]*">', '<designrules name=%s>' % a(PROJ), dr)
clr = cls["Default"].get("clearance", 0.2)
thick = mm(ds.GetBoardThickness())
P = {
    "layerSetup": "(" + "*".join(str(l) for l in EAGLE_CU) + ")",
    "mtCopper": " ".join(["0.035mm"] * 16),
    "mtIsolate": " ".join(["%smm" % f(max(0.05, (thick - 0.035 * len(EAGLE_CU)) / max(1, len(EAGLE_CU) - 1)))] * 15),
    "mdWireWire": "%smm" % f(clr), "mdWirePad": "%smm" % f(clr), "mdWireVia": "%smm" % f(clr),
    "mdPadPad": "%smm" % f(clr), "mdPadVia": "%smm" % f(clr), "mdViaVia": "%smm" % f(clr),
    "mdSmdPad": "%smm" % f(clr), "mdSmdVia": "%smm" % f(clr), "mdSmdSmd": "%smm" % f(clr),
    "mdViaViaSameLayer": "%smm" % f(clr),
    "mdCopperDimension": "%smm" % f(mm(ds.m_CopperEdgeClearance)),
    "mdDrill": "%smm" % f(mm(ds.m_HoleToHoleMin)),
    "msWidth": "%smm" % f(mm(ds.m_TrackMinWidth)),
    "msDrill": "%smm" % f(mm(ds.m_MinThroughDrill)),
    "rvPadTop": "0", "rvPadBottom": "0", "rlMinPadTop": "0mm", "rlMaxPadTop": "0mm",
    "rlMinPadBottom": "0mm", "rlMaxPadBottom": "0mm",
    "rvPadInner": "0.25", "rlMinPadInner": "0.15mm", "rlMaxPadInner": "0.5mm",
    "rvViaOuter": "0", "rlMinViaOuter": "0mm", "rlMaxViaOuter": "0mm",
    "rvViaInner": "0", "rlMinViaInner": "0.15mm", "rlMaxViaInner": "0.15mm",
    "psElongationLong": "%d" % int(round((LONG_RATIO - 1) * 100)),
    "mvStopFrame": "0", "mlMinStopFrame": "%smm" % f(mm(ds.m_SolderMaskExpansion)),
    "mlMaxStopFrame": "%smm" % f(mm(ds.m_SolderMaskExpansion)),
    "mvCreamFrame": "0", "mlMinCreamFrame": "0mm", "mlMaxCreamFrame": "0mm",
    "srRoundness": "0", "srMinRoundness": "0mm", "srMaxRoundness": "0mm",
    "slThermalIsolate": "0.3mm",
}
for k, v in P.items():
    pat = r'<param name="%s" value="[^"]*"/>' % k
    if re.search(pat, dr):
        dr = re.sub(pat, '<param name="%s" value="%s"/>' % (k, v), dr)
    else:
        warn["brak parametru DRC %s we wzorze" % k] += 1

# ---------------------------------------------------------------- plain
plain = []
convb = lambda p: (X(p.x), Y(p.y))
for d in b.GetDrawings():
    L = GLAYER.get(d.GetLayer())
    if L is None:
        warn["rysunek plytki na %s pominiety" % d.GetLayerName()] += 1
        continue
    if d.GetClass() == "PCB_SHAPE":
        plain += shape_xml(d, L, convb)
        if d.GetLayer() in CU:
            warn["grafika na warstwie miedzi (bez sieci)"] += 1
    elif d.GetClass() == "PCB_TEXT":
        plain.append("<text %s>%s</text>" % (text_attrs(d, convb, L, d.GetTextAngleDegrees(), d.IsMirrored()), escape(d.GetShownText(False))))
    else:
        warn["rysunek plytki %s pominiety" % d.GetClass()] += 1

# ---------------------------------------------------------------- elementy XML
elem_xml = []
for fp, pname, th, mir in elements:
    ref = ename(fp.GetReference())
    x, y = X(fp.GetPosition().x), Y(fp.GetPosition().y)
    atts = []
    for fld, nm, lmap in ((fp.Reference(), "NAME", NAMELAYER), (fp.Value(), "VALUE", VALLAYER)):
        L = lmap.get(fld.GetLayer(), GLAYER.get(fld.GetLayer(), 48))
        disp = "value" if fld.IsVisible() else "off"
        atts.append('<attribute name="%s" %s display="%s"/>' % (nm, text_attrs(fld, convb, L, fld.GetDrawRotation().AsDegrees(), fld.IsMirrored()), disp))
    for fld in fp.GetFields():
        n = fld.GetName()
        if n in ("Reference", "Value", "Footprint", "Datasheet", "Description") or not fld.GetText().strip():
            continue
        L = 28 if fp.IsFlipped() else 27
        atts.append('<attribute name=%s value=%s x="%s" y="%s" size="1" layer="%d"%s display="off"/>' % (
            a(ename(n)), a(fld.GetText()), f(x), f(y), L, ' rot="MR0"' if fp.IsFlipped() else ""))
    if fp.IsDNP():  # Eagle 7.5 nie zna populate="no"
        atts.append('<attribute name="DNP" value="yes" x="%s" y="%s" size="1" layer="%d"%s display="off"/>' % (
            f(x), f(y), 28 if fp.IsFlipped() else 27, ' rot="MR0"' if fp.IsFlipped() else ""))
        warn["element DNP -> atrybut DNP=yes (Eagle 7 nie ma 'nie montowac')"] += 1
    # Eagle: "MRa" = obrot o a, potem lustro X  ->  dla spodu kat -th
    elem_xml.append('<element name=%s library=%s package=%s value=%s x="%s" y="%s" smashed="yes" rot="%s"%s>%s</element>' % (
        a(ref), a(LIB), a(pname), a(fp.GetValue()), f(x), f(y), rot(-th if mir else th, mir),
        "", "".join(atts)))

# ---------------------------------------------------------------- sygnaly
sig = collections.OrderedDict()
def S(net):
    nm = netname(net)
    if nm not in sig:
        n = b.FindNet(net)
        nc = n.GetNetClassName() if n else "Default"
        sig[nm] = {"class": cnum.get(nc.split(",")[0], 0), "items": []}
    return sig[nm]["items"]
for fp in b.GetFootprints():
    for i, p in enumerate(fp.Pads()):
        n = p.GetNetname()
        if not n or pad_ename.get((fp.GetReference(), i)) is None:
            continue
        S(n).append('<contactref element=%s pad=%s/>' % (a(ename(fp.GetReference())), a(pad_ename[(fp.GetReference(), i)])))
ntr = nvia = narc = 0
for t in b.GetTracks():
    n = t.GetNetname()
    if t.GetClass() == "PCB_VIA":
        v = t.Cast()
        top, bot = v.TopLayer(), v.BottomLayer()
        ext = "%d-%d" % (CU.get(top, 1), CU.get(bot, 16))
        if v.GetViaType() != pcbnew.VIATYPE_THROUGH:
            warn["przelotka slepa/zagrzebana/mikro (extent %s)" % ext] += 1
        p = v.GetPosition()
        it = '<via x="%s" y="%s" extent="%s" drill="%s" diameter="%s"/>' % (f(X(p.x)), f(Y(p.y)), ext, f(mm(v.GetDrillValue())), f(mm(v.GetWidth(pcbnew.F_Cu))))
        nvia += 1
    else:
        L = CU.get(t.GetLayer())
        if L is None:
            warn["sciezka na nieznanej warstwie"] += 1; continue
        s, e = t.GetStart(), t.GetEnd()
        if t.GetClass() == "PCB_ARC":
            m = t.Cast().GetMid()
            it = W(X(s.x), Y(s.y), X(e.x), Y(e.y), mm(t.GetWidth()), L, arc_curve(X(s.x), Y(s.y), X(m.x), Y(m.y), X(e.x), Y(e.y)))
            narc += 1
        else:
            if s == e:
                warn["sciezka zerowej dlugosci pominieta"] += 1
                continue
            it = W(X(s.x), Y(s.y), X(e.x), Y(e.y), mm(t.GetWidth()), L)
        ntr += 1
    if not n:
        warn["sciezka/przelotka bez sieci"] += 1
        continue
    S(n).append(it)

nz = nra = 0
zones = [z for z in b.Zones()]
prios = collections.defaultdict(list)
for z in zones:
    if not z.GetIsRuleArea():
        for lid in z.GetLayerSet().Seq():
            prios[lid].append(z.GetAssignedPriority())
def poly_xml(ol, L, attrs, w):
    pts = [(X(ol.CPoint(i).x), Y(ol.CPoint(i).y)) for i in range(ol.PointCount())]
    return '<polygon width="%s" layer="%d"%s>' % (f(w), L, attrs) + "".join('<vertex x="%s" y="%s"/>' % (f(x), f(y)) for x, y in pts) + "</polygon>"
for z in zones:
    if z.GetIsRuleArea():
        o = z.Outline()
        lays = list(z.GetLayerSet().Seq())
        tgt = set()
        if z.GetDoNotAllowTracks() or z.GetDoNotAllowZoneFills() or z.GetDoNotAllowPads():
            for lid in lays:
                if lid == pcbnew.F_Cu: tgt.add(41)
                elif lid == pcbnew.B_Cu: tgt.add(42)
                elif lid in CU: warn["obszar zabroniony na warstwie wewnetrznej - Eagle 7 ma tylko tRestrict/bRestrict"] += 1
        if z.GetDoNotAllowVias():
            tgt.add(43)
        if z.GetDoNotAllowFootprints():
            warn["zakaz elementow w obszarze zabronionym pominiety"] += 1
        for L in sorted(tgt):
            for oi in range(o.OutlineCount()):
                plain.append(poly_xml(o.Outline(oi), L, "", 0.0254)); nra += 1
        continue
    for lid in z.GetLayerSet().Seq():
        L = CU.get(lid)
        if L is None:
            continue
        # Eagle: obrys polygonu = os linii o szerokosci width (miedz siega width/2 dalej)
        o = pcbnew.SHAPE_POLY_SET(z.Outline())
        o.Inflate(-z.GetMinThickness() // 2, pcbnew.CORNER_STRATEGY_ALLOW_ACUTE_CORNERS, 1000)
        ps = sorted(set(prios[lid]), reverse=True)
        rank = max(1, min(6, 1 + ps.index(z.GetAssignedPriority())))
        th = z.GetPadConnection() != pcbnew.ZONE_CONNECTION_FULL
        orph = "yes" if z.GetIslandRemovalMode() == pcbnew.ISLAND_REMOVAL_MODE_NEVER else "no"
        hatch = ""
        if z.GetFillMode() == pcbnew.ZONE_FILL_MODE_HATCH_PATTERN:
            hatch = ' pour="hatch" spacing="%s"' % f(mm(z.GetHatchGap() + z.GetHatchThickness()))
        for oi in range(o.OutlineCount()):
            xml = poly_xml(o.Outline(oi), L, ' isolate="%s" thermals="%s" orphans="%s" rank="%d"%s' % (
                f(mm(z.GetLocalClearance() or 0)), "yes" if th else "no", orph, rank, hatch), mm(z.GetMinThickness()))
            if o.HoleCount(oi):
                warn["otwory w obrysie strefy pominiete"] += 1
            if z.GetNetname():
                S(z.GetNetname()).append(xml)
            else:
                plain.append(xml)
            nz += 1

sig_xml = ['<signal name=%s class="%d">\n%s\n</signal>' % (a(nm), d["class"], "\n".join(d["items"])) for nm, d in sig.items()]

# ---------------------------------------------------------------- zapis
layers = open(os.path.join(A.szablony, "warstwy_brd.xml"), encoding="utf-8").read().strip()
for n in EAGLE_CU + [39, 40, 41, 42, 43, 46, 48, 49, 51, 52]:
    layers = re.sub(r'(<layer number="%d" [^>]*visible=)"no"( active=)"no"' % n, r'\1"yes"\2"yes"', layers)
pk_xml = ['<package name=%s>\n%s\n</package>' % (a(pn), "\n".join(items)) for pn, items in packages.items()]
out = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE eagle SYSTEM "eagle.dtd">
<eagle version="%s">
<drawing>
<settings>
<setting alwaysvectorfont="yes"/>
<setting verticaltext="up"/>
</settings>
<grid distance="0.05" unitdist="mm" unit="mm" style="lines" multiple="1" display="no" altdistance="0.01" altunitdist="mm" altunit="mm"/>
%s
<board>
<description>%s - konwersja z KiCad (KiCad2Eagle)</description>
<plain>
%s
</plain>
<libraries>
<library name=%s>
<packages>
%s
</packages>
</library>
</libraries>
<attributes>
</attributes>
<variantdefs>
</variantdefs>
<classes>
%s
</classes>
%s
<elements>
%s
</elements>
<signals>
%s
</signals>
</board>
</drawing>
</eagle>
""" % (EAGLE_VERSION, layers, escape(PROJ), "\n".join(plain), a(LIB), "\n".join(pk_xml), "\n".join(classes_xml), dr,
       "\n".join(elem_xml), "\n".join(sig_xml))
open(A.out, "w", encoding="utf-8", newline="\n").write(out)
json.dump({"lib": LIB, "pkg_pads": pkg_pmap, "elem_pkg": elem_pkg, "classes": order, "layermap": LAYERMAP},
          open(A.map, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
stats = collections.OrderedDict([
    ("warstwy miedzi", "%d (%s)" % (len(custack), ", ".join("%s->%d" % kv for kv in LAYERMAP.items()))),
    ("elementy", len(elements)), ("na spodzie", sum(1 for e in elements if e[3])),
    ("obudowy", len(packages)), ("sygnaly", len(sig)), ("sciezki", ntr), ("luki", narc), ("przelotki", nvia),
    ("polygony (strefy)", nz), ("obszary zabronione", nra), ("klasy sieci", len(order)),
    ("wydluzenie padow long", "%d %%" % int(round((LONG_RATIO - 1) * 100))),
    ("maks. blad dopasowania obrotu [mm]", round(maxres, 6)),
])
json.dump({"stats": stats, "warn": dict(warn), "dru_ok": dru_ok, "dru_skip": dru_skip},
          open(A.raport, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
for k, v in stats.items():
    print("%s: %s" % (k, v))
for n, d in dru_ok:
    print("REGULA przeniesiona: %s (%s)" % (n, d))
for n, d in dru_skip:
    print("REGULA nieprzeniesiona: %s (%s)" % (n, d))
for k, v in sorted(warn.items()):
    print("UWAGA: %s x %d" % (k, v))
