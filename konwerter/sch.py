# -*- coding: utf-8 -*-
"""KiCad 10 .kicad_sch (jeden arkusz) -> Eagle 7.5 .sch (XML), zgodny z .brd z brd.py.

python sch.py --sch X.kicad_sch --net netlista.net --out X.sch --raport raport_sch.json
              [--brd X.brd --map mapa_padow.json] [--lib NAZWA]
Bez --brd/--map urzadzenia nie maja obudow (sam schemat).
"""
import sys, os, re, math, json, collections, argparse
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sexp import parse, kids, kid, val, at, flag
from common import f, rot, ename, netname, a, arc_curve, align, EAGLE_VERSION, escape

ap = argparse.ArgumentParser()
ap.add_argument("--sch", required=True); ap.add_argument("--net", required=True)
ap.add_argument("--out", required=True); ap.add_argument("--raport", required=True)
ap.add_argument("--brd"); ap.add_argument("--map"); ap.add_argument("--lib")
ap.add_argument("--szablony", default=os.path.join(os.path.dirname(HERE), "szablony"))
A = ap.parse_args()

sch = parse(open(A.sch, encoding="utf-8").read())
net = parse(open(A.net, encoding="utf-8").read())
PROJ = os.path.splitext(os.path.basename(A.sch))[0]
if kids(sch, "sheet"):
    sys.exit("BLAD: schemat hierarchiczny (%d arkuszy podrzednych) - KiCad2Eagle obsluguje tylko schematy jednoarkuszowe" % len(kids(sch, "sheet")))
brd = open(A.brd, encoding="utf-8").read() if A.brd and os.path.exists(A.brd) else None
mp = json.load(open(A.map, encoding="utf-8")) if A.map and os.path.exists(A.map) else None
LIB = ename(A.lib or (mp or {}).get("lib") or PROJ)
warn = collections.Counter()
if mp is None:
    warn["brak plytki - urzadzenia bez obudow (sam schemat)"] += 1

R = lambda v: round(v * 1000)           # klucz punktu: 1 um
DEFW = 0.1524                           # domyslna grubosc linii KiCad (6 mil)
LIMIT = 1990.0                          # Eagle 7.5: wspolrzedne w zakresie +-2000 mm

# ------------------------------------------------------------ uklad wspolrzednych
paper = kid(sch, "paper")
H = float(paper[3]) if paper is not None and len(paper) > 3 else 297.0
def content_xy():
    xs, ys = [], []
    def walk(x):
        if not isinstance(x, list) or not x: return
        if x[0] == "lib_symbols": return
        if x[0] in ("at", "xy") and len(x) >= 3:
            try:
                xs.append(float(x[1])); ys.append(float(x[2]))
            except ValueError:
                pass
        for c in x[1:]: walk(c)
    walk(sch)
    return xs, ys
xs, ys = content_xy()
XOFF = YOFF = 0.0
if xs:
    if max(xs) - min(xs) > 2 * LIMIT or max(ys) - min(ys) > 2 * LIMIT:
        sys.exit("BLAD: zawartosc schematu ma %.0f x %.0f mm - Eagle 7.5 przyjmuje najwyzej 4000 x 4000 mm" % (max(xs) - min(xs), max(ys) - min(ys)))
    if max(abs(min(xs)), abs(max(xs))) > LIMIT:
        XOFF = round((min(xs) + max(xs)) / 2 / 2.54) * 2.54
    ey = [H - y for y in ys]
    if max(abs(min(ey)), abs(max(ey))) > LIMIT:
        YOFF = round((min(ey) + max(ey)) / 2 / 2.54) * 2.54
def EX(x): return x - XOFF
def EY(y): return H - y - YOFF

# ------------------------------------------------------------ netlista KiCad
pin_net = {}
for n in kids(kid(net, "nets"), "net"):
    nodes = kids(n, "node")
    if len(nodes) < 2:
        continue
    for nd in nodes:
        pin_net[(val(nd, "ref"), val(nd, "pin"))] = val(n, "name")

if brd:
    sig_class = {m.group(1): m.group(2) for m in re.finditer(r'<signal name="([^"]*)" class="(\d+)">', brd)}
    classes_xml = re.search(r"<classes>.*?</classes>", brd, re.S).group(0)
    packages_xml = re.search(r"<packages>.*?</packages>", brd, re.S).group(0)
else:
    sig_class = {}
    classes_xml = '<classes>\n<class number="0" name="default" width="0" drill="0">\n</class>\n</classes>'
    packages_xml = "<packages>\n</packages>"

# ------------------------------------------------------------ symbole
libs = {s[1]: s for s in kids(kid(sch, "lib_symbols"), "symbol")}

def lib_units(ls):
    """{numer jednostki: [podsymbole]} - jednostka 0 (wspolna) doklejona do pierwszej"""
    per = collections.defaultdict(list); common = []
    for u in kids(ls, "symbol"):
        m = re.match(r".*_(\d+)_(\d+)$", u[1])
        if not m: continue
        un, st = int(m.group(1)), int(m.group(2))
        if st not in (0, 1): continue
        (common if un == 0 else per[un]).append(u)
    if not per:
        per[1] = []
    first = min(per)
    out = collections.OrderedDict()
    for un in sorted(per):
        out[un] = per[un] + (common if un == first else [])
        if un != first and common and any(kids(c, "pin") for c in common):
            warn["piny jednostki wspolnej przypisane tylko do pierwszej bramki"] += 1
    return out

def pin_len_parts(L):
    for nm, v in (("long", 7.62), ("middle", 5.08), ("short", 2.54), ("point", 0.0)):
        if L >= v - 1e-6:
            return nm, v
    return "point", 0.0

DIR = {"input": "in", "output": "out", "bidirectional": "io", "tri_state": "hiz", "passive": "pas",
       "power_in": "pwr", "power_out": "out", "open_collector": "oc", "open_emitter": "oc",
       "unspecified": "pas", "free": "pas", "no_connect": "nc"}

def pin_ename(name, number):
    n = name if name not in ("", "~") else number
    if "~{" in n:
        n = re.sub(r"~\{([^}]*)\}", r"!\1!", n).rstrip("!")
    return ename(n)

def gfx(item, L, conv=lambda x, y: (x, y)):
    t = item[0]
    stroke = kid(item, "stroke")
    w = float(val(stroke, "width", 0) or 0) if stroke else 0.0
    w = w or DEFW
    fill = val(kid(item, "fill"), "type", "none") if kid(item, "fill") else "none"
    out = []
    def Wr(p, q, c=None):
        return '<wire x1="%s" y1="%s" x2="%s" y2="%s" width="%s" layer="%d"%s/>' % (
            f(p[0]), f(p[1]), f(q[0]), f(q[1]), f(w), L, (' curve="%s"' % f(c, 3)) if c else "")
    if t == "rectangle":
        x1, y1 = map(float, kid(item, "start")[1:3]); x2, y2 = map(float, kid(item, "end")[1:3])
        p = [conv(x1, y1), conv(x2, y1), conv(x2, y2), conv(x1, y2)]
        if fill == "outline":
            xs_ = [q[0] for q in p]; ys_ = [q[1] for q in p]
            out.append('<rectangle x1="%s" y1="%s" x2="%s" y2="%s" layer="%d"/>' % (f(min(xs_)), f(min(ys_)), f(max(xs_)), f(max(ys_)), L))
        for i in range(4):
            out.append(Wr(p[i], p[(i + 1) % 4]))
    elif t == "polyline":
        pts = [conv(float(q[1]), float(q[2])) for q in kids(kid(item, "pts"), "xy")]
        if fill == "outline" and len(pts) >= 3:
            out.append('<polygon width="%s" layer="%d">%s</polygon>' % (f(w), L, "".join('<vertex x="%s" y="%s"/>' % (f(x), f(y)) for x, y in pts)))
        else:
            for p, q in zip(pts, pts[1:]):
                if p != q: out.append(Wr(p, q))
    elif t == "circle":
        cx, cy = conv(*map(float, kid(item, "center")[1:3])); r = float(val(item, "radius"))
        out.append('<circle x="%s" y="%s" radius="%s" width="%s" layer="%d"/>' % (f(cx), f(cy), f(r), "0" if fill == "outline" else f(w), L))
    elif t == "arc":
        s = conv(*map(float, kid(item, "start")[1:3])); m = conv(*map(float, kid(item, "mid")[1:3])); e = conv(*map(float, kid(item, "end")[1:3]))
        out.append(Wr(s, e, arc_curve(s[0], s[1], m[0], m[1], e[0], e[1])))
    elif t == "bezier":
        pts = [conv(float(q[1]), float(q[2])) for q in kids(kid(item, "pts"), "xy")]
        for p, q in zip(pts, pts[1:]):
            out.append(Wr(p, q))
        warn["krzywa Beziera -> lamana"] += 1
    return out

def text_xml(item, L, conv, angle_override=None, with_align=True):
    x, y, an = at(item)
    x, y = conv(x, y)
    eff = kid(item, "effects")
    fnt = kid(eff, "font") if eff else None
    size = float(kid(fnt, "size")[1]) if fnt and kid(fnt, "size") else 1.27
    th = float(val(fnt, "thickness", 0) or 0) if fnt else 0
    ratio = int(round(min(31, max(6, th / size * 100)))) if th else 10
    j = kid(eff, "justify") if eff else None
    js = j[1:] if j else []
    hh = -1 if "left" in js else (1 if "right" in js else 0)
    vv = -1 if "top" in js else (1 if "bottom" in js else 0)
    a_ = an if angle_override is None else angle_override
    s = 'x="%s" y="%s" size="%s" layer="%d" font="vector" ratio="%d" rot="%s"' % (f(x), f(y), f(size), L, ratio, rot(a_))
    return s + (' align="%s"' % align(hh, vv) if with_align else "")

symbols = collections.OrderedDict()     # (lib_id, nazwa zasilania) -> {units: {un: {...}}, prefix}
used_sym_names = set()
def build_symbol(lid, power_name=None):
    key = (lid, power_name)
    if key in symbols:
        return symbols[key]
    ls = libs[lid]
    base = ename(lid.split(":")[-1])
    if power_name:
        base = "SUPPLY_" + ename(power_name)
    hide_names = flag(kid(ls, "pin_names"), "hide") if kid(ls, "pin_names") else False
    hide_nums = flag(kid(ls, "pin_numbers"), "hide") if kid(ls, "pin_numbers") else False
    units = lib_units(ls)
    multi = len(units) > 1
    out_units = collections.OrderedDict()
    for un, parts_ in units.items():
        gate = chr(64 + un) if multi and un <= 26 else ("G$%d" % un if multi else "G$1")
        nm = base + ("_" + gate if multi else "")
        k = 1; nm0 = nm
        while nm in used_sym_names:
            k += 1; nm = "%s_%d" % (nm0, k)
        used_sym_names.add(nm)
        items, pins = [], []
        for u in parts_:
            for g in u[1:]:
                if isinstance(g, list) and g[0] in ("rectangle", "polyline", "circle", "arc", "bezier"):
                    items += gfx(g, 94)
                elif isinstance(g, list) and g[0] == "text":
                    items.append("<text %s>%s</text>" % (text_xml(g, 94, lambda x, y: (x, y)), escape(g[1])))
            for p in kids(u, "pin"):
                x, y, an = at(p)
                L = float(val(p, "length", 0))
                pname, pnum = val(p, "name", ""), val(p, "number", "")
                en = pin_ename(pname, pnum) if not power_name else ename(power_name)
                pins.append([pnum, en, (x, y), flag(p, "hide"), p[1], L, an, pname])
        tot = collections.Counter(p[1] for p in pins); seen = collections.Counter()
        for p in pins:
            if tot[p[1]] > 1:
                seen[p[1]] += 1; p[1] = "%s@%d" % (p[1], seen[p[1]])
        for pnum, en, (x, y), hidden, ptype, L, an, pname in pins:
            ln, lv = ("point", 0.0) if hidden else pin_len_parts(L)
            if not hidden and L - lv > 1e-6:
                ca, sa = math.cos(math.radians(an)), math.sin(math.radians(an))
                items.append('<wire x1="%s" y1="%s" x2="%s" y2="%s" width="%s" layer="94"/>' % (
                    f(x + lv * ca), f(y + lv * sa), f(x + L * ca), f(y + L * sa), f(DEFW)))
            show_name = not hide_names and pname not in ("", "~") and not hidden
            show_num = not hide_nums and not hidden
            vis = {(True, True): "both", (True, False): "pin", (False, True): "pad", (False, False): "off"}[(show_name, show_num)]
            d = "sup" if power_name else DIR.get(ptype, "pas")
            items.append('<pin name=%s x="%s" y="%s" visible="%s" length="%s" direction="%s" rot="%s"/>' % (
                a(en), f(x), f(y), "off" if power_name else vis, ln, d, rot(an)))
        for prop in kids(ls, "property"):
            if prop[1] == "Reference" and not power_name:
                items.append("<text %s>&gt;NAME</text>" % text_xml(prop, 95, lambda x, y: (x, y)))
            if prop[1] == "Value":
                items.append("<text %s>&gt;VALUE</text>" % text_xml(prop, 96, lambda x, y: (x, y)))
        out_units[un] = {"gate": gate, "name": nm, "pins": pins,
                         "xml": '<symbol name=%s>\n%s\n</symbol>' % (a(nm), "\n".join(items))}
    pref = [p for p in kids(ls, "property") if p[1] == "Reference"]
    s = {"units": out_units, "ds": base, "prefix": ename(re.sub(r"[^A-Za-z]", "", pref[0][2])) if pref and pref[0][2] else ""}
    symbols[key] = s
    return s

# ------------------------------------------------------------ instancje
def T_inst(X, Y, A_, mir):
    """lib (Y w gore) -> Eagle: najpierw lustro, potem obrot (konwencja KiCad)"""
    def M(lx, ly):
        if mir == "x": lx, ly = lx, -ly
        elif mir == "y": lx, ly = -lx, ly
        ca, sa = math.cos(math.radians(A_)), math.sin(math.radians(A_))
        return (EX(X) + lx * ca - ly * sa, EY(Y) + lx * sa + ly * ca)
    return M

def eagle_rot(A_, mir):
    T = T_inst(XOFF, H - YOFF, A_, mir)
    ex = T(1, 0); ey = T(0, 1)
    det = ex[0] * ey[1] - ex[1] * ey[0]
    if det > 0:
        return rot(math.degrees(math.atan2(ex[1], ex[0])))
    return rot(math.degrees(math.atan2(ex[1], -ex[0])), True)   # Eagle MRa: obrot a, potem lustro X

insts = []
for s in kids(sch, "symbol"):
    lid = val(s, "lib_id")
    X, Y, A_ = at(s)
    props = {p[1]: p for p in kids(s, "property")}
    ls = libs[lid]
    is_power = flag(ls, "power") or lid.startswith("power:")
    ref = props["Reference"][2]; value = props["Value"][2]
    insts.append(dict(lid=lid, X=X, Y=Y, A=A_, mir=val(s, "mirror"), props=props, ref=ref, value=value,
                      unit=int(val(s, "unit", "1")), power=is_power, dnp=flag(s, "dnp")))

# ------------------------------------------------------------ przewody, etykiety
wires = []
for w in kids(sch, "wire"):
    pts = [(float(q[1]), float(q[2])) for q in kids(kid(w, "pts"), "xy")]
    st = kid(w, "stroke"); ww = float(val(st, "width", 0) or 0) if st else 0
    for p, q in zip(pts, pts[1:]):
        if p != q:
            wires.append(((EX(p[0]), EY(p[1])), (EX(q[0]), EY(q[1])), ww or DEFW))
labels = []
for kind in ("label", "global_label", "hierarchical_label"):
    for l in kids(sch, kind):
        x, y, an = at(l)
        labels.append(dict(text=l[1], p=(EX(x), EY(y)), item=l, an=an, glob=(kind != "label")))
if kids(sch, "bus") or kids(sch, "bus_entry"):
    warn["magistrale (bus) pominiete - polaczenia przez etykiety zostaja"] += 1

parent = list(range(len(wires)))
def find(i):
    while parent[i] != i:
        parent[i] = parent[parent[i]]; i = parent[i]
    return i
def union(i, j):
    i, j = find(i), find(j)
    if i != j: parent[i] = j
endp = collections.defaultdict(list)
for i, (p, q, w) in enumerate(wires):
    endp[(R(p[0]), R(p[1]))].append(i); endp[(R(q[0]), R(q[1]))].append(i)
for k, l in endp.items():
    for i in l[1:]: union(l[0], i)
def on_wire(pt, w, tol=0.0005):
    (x1, y1), (x2, y2), _ = w
    dx, dy = x2 - x1, y2 - y1; L2 = dx * dx + dy * dy
    t = ((pt[0] - x1) * dx + (pt[1] - y1) * dy) / L2
    if t < -1e-9 or t > 1 + 1e-9: return False
    return math.hypot(x1 + t * dx - pt[0], y1 + t * dy - pt[1]) < tol
grid = collections.defaultdict(list)
G = 5.0
for i, (p, q, w) in enumerate(wires):
    for gx in range(int(min(p[0], q[0]) // G), int(max(p[0], q[0]) // G) + 1):
        for gy in range(int(min(p[1], q[1]) // G), int(max(p[1], q[1]) // G) + 1):
            grid[(gx, gy)].append(i)
def wires_at(pt):
    return [i for i in grid.get((int(pt[0] // G), int(pt[1] // G)), []) if on_wire(pt, wires[i])]
tjunc = set()
for i, (p, q, w) in enumerate(wires):
    for e in (p, q):
        for j in wires_at(e):
            if j != i:
                union(i, j)
                if (R(e[0]), R(e[1])) not in [(R(wires[j][0][0]), R(wires[j][0][1])), (R(wires[j][1][0]), R(wires[j][1][1]))]:
                    tjunc.add((R(e[0]), R(e[1])))

# ------------------------------------------------------------ piny -> grupy przewodow
group_pins = collections.defaultdict(list)
lonely = []
pin_points = collections.defaultdict(list)
part_names = {}
pin_total = pin_hits = 0
for ins in insts:
    if ins["power"] and (ins["value"] == "PWR_FLAG" or ins["lid"].endswith(":PWR_FLAG")):
        ins["skip"] = True
        continue
    pw = ins["value"] if ins["power"] else None
    sym = build_symbol(ins["lid"], netname(pw) if pw else None)
    if ins["unit"] not in sym["units"]:
        warn["jednostka %d symbolu %s nie istnieje w bibliotece" % (ins["unit"], ins["lid"])] += 1
        ins["skip"] = True
        continue
    U = sym["units"][ins["unit"]]
    pname = ename(ins["ref"].lstrip("#"))
    if pname in part_names and part_names[pname] != ins["ref"]:
        warn["kolizja nazwy czesci %s" % pname] += 1
    part_names[pname] = ins["ref"]
    ins.update(pname=pname, sym=sym, U=U)
    T = T_inst(ins["X"], ins["Y"], ins["A"], ins["mir"])
    for pnum, en, (lx, ly), hidden, ptype, L, an, pn in U["pins"]:
        pt = T(lx, ly)
        pin_points[(R(pt[0]), R(pt[1]))].append((pname, U["gate"], en))
        n = pin_net.get((ins["ref"], pnum))
        if ins["power"]:
            n = n or pw
        hit = wires_at(pt)
        rec = (pname, U["gate"], en, ins["ref"], pnum, n)
        if hit:
            group_pins[find(hit[0])].append(rec)
        elif n:
            lonely.append(rec + (pt,))
        if n:
            pin_total += 1; pin_hits += bool(hit)

groups = collections.defaultdict(list)
for i in range(len(wires)):
    groups[find(i)].append(i)
glabels = collections.defaultdict(list)
for lb in labels:
    hit = wires_at(lb["p"])
    if hit:
        glabels[find(hit[0])].append(lb)
    else:
        warn["etykieta poza przewodem: %s" % lb["text"]] += 1
nets = collections.OrderedDict()
unnamed = 0
conflicts = []
for g, wl in groups.items():
    names = collections.Counter(netname(pp[5]) for pp in group_pins[g] if pp[5])
    lab = set(netname(l["text"]) for l in glabels[g])
    if names:
        nm = names.most_common(1)[0][0]
        if len(names) > 1:
            conflicts.append("%s: %s" % (nm, dict(names)))
    elif lab:
        nm = sorted(lab)[0]
    else:
        unnamed += 1; nm = "N$%d" % unnamed
    nets.setdefault(nm, []).append(g)

def wire_xml(i):
    (x1, y1), (x2, y2), w = wires[i]
    return '<wire x1="%s" y1="%s" x2="%s" y2="%s" width="%s" layer="91"/>' % (f(x1), f(y1), f(x2), f(y2), f(w))

def label_xml(lb):
    return "<label %s%s/>" % (text_xml(lb["item"], 95, lambda x, y: (EX(x), EY(y)), with_align=False),
                              ' xref="yes"' if lb["glob"] else "")

deg = collections.Counter()
for (p, q, w) in wires:
    deg[(R(p[0]), R(p[1]))] += 1; deg[(R(q[0]), R(q[1]))] += 1
for k in list(deg):
    deg[k] += len(pin_points.get(k, []))
junctions = set(k for k, v in deg.items() if v >= 3) | tjunc

net_xml = collections.OrderedDict()
for nm, gl in nets.items():
    segs = []
    for g in gl:
        body = [wire_xml(i) for i in groups[g]]
        pts = set()
        for i in groups[g]:
            for e in wires[i][:2]: pts.add((R(e[0]), R(e[1])))
        body += ['<junction x="%s" y="%s"/>' % (f(k[0] / 1000), f(k[1] / 1000)) for k in sorted(pts & junctions)]
        body += [label_xml(lb) for lb in glabels[g]]
        body += ['<pinref part=%s gate=%s pin=%s/>' % (a(pp[0]), a(pp[1]), a(pp[2])) for pp in group_pins[g]]
        segs.append("<segment>\n%s\n</segment>" % "\n".join(body))
    net_xml[nm] = segs
lonely_n = 0
for pname, gate, en, ref, pnum, n, pt in lonely:
    nm = netname(n)
    others = [pp for pp in pin_points[(R(pt[0]), R(pt[1]))] if pp != (pname, gate, en)]
    seg = '<segment>\n<pinref part=%s gate=%s pin=%s/>\n%s</segment>' % (
        a(pname), a(gate), a(en), "".join('<pinref part=%s gate=%s pin=%s/>\n' % (a(o[0]), a(o[1]), a(o[2])) for o in others))
    net_xml.setdefault(nm, []).append(seg)
    lonely_n += 1
if lonely_n:
    warn["piny polaczone bez przewodu (styk pin-pin / ukryte) - segment bez przewodu"] += lonely_n

# ------------------------------------------------------------ devicesety
devsets = collections.OrderedDict()
for ins in insts:
    if ins.get("skip"):
        continue
    sym = ins["sym"]
    ds = devsets.setdefault(id(sym), {"sym": sym, "pkgs": collections.OrderedDict(), "power": ins["power"]})
    pk = None
    if not ins["power"] and mp is not None:
        pk = mp["elem_pkg"].get(ename(ins["ref"]))
        if pk is None:
            warn["czesc bez elementu na plytce: %s" % ins["ref"]] += 1
    ds["pkgs"][pk] = True
    ins["pkg"] = pk
used_ds = set()
dev_xml = []
for key, ds in devsets.items():
    sym = ds["sym"]
    dsn = sym["ds"]; k = 1
    while dsn in used_ds:
        k += 1; dsn = "%s_%d" % (sym["ds"], k)
    used_ds.add(dsn); ds["name"] = dsn
    multi = len(ds["pkgs"]) > 1
    ds["devname"] = {}
    devs = []
    for pk in ds["pkgs"]:
        dn = "" if not multi or pk is None else pk
        ds["devname"][pk] = dn
        if pk is None:
            devs.append('<device name=%s>\n<technologies>\n<technology name=""/>\n</technologies>\n</device>' % a(dn))
            continue
        pads = mp["pkg_pads"][pk]
        cons = []
        for un, U in sym["units"].items():
            for pnum, en, *_ in U["pins"]:
                if pnum in pads:
                    cons.append('<connect gate=%s pin=%s pad=%s/>' % (a(U["gate"]), a(en), a(" ".join(pads[pnum]))))
                elif pnum:
                    warn["pin bez padu w obudowie (%s pin %s)" % (dsn, pnum)] += 1
        devs.append('<device name=%s package=%s>\n<connects>\n%s\n</connects>\n<technologies>\n<technology name=""/>\n</technologies>\n</device>' % (
            a(dn), a(pk), "\n".join(cons)))
    gates = "\n".join('<gate name=%s symbol=%s x="%s" y="0"/>' % (a(U["gate"]), a(U["name"]), f(i * 25.4))
                      for i, U in enumerate(sym["units"].values()))
    dev_xml.append('<deviceset name=%s prefix=%s uservalue="yes">\n<gates>\n%s\n</gates>\n<devices>\n%s\n</devices>\n</deviceset>' % (
        a(dsn), a(sym["prefix"] if not ds["power"] else ""), gates, "\n".join(devs)))

part_xml, inst_xml = collections.OrderedDict(), []
conv_s = lambda x, y: (EX(x), EY(y))
for ins in insts:
    if ins.get("skip"):
        continue
    ds = devsets[id(ins["sym"])]
    if ins["pname"] not in part_xml:
        attrs = ['<attribute name=%s value=%s/>' % (a(ename(k)), a(p[2])) for k, p in ins["props"].items()
                 if k not in ("Reference", "Value", "Footprint", "Datasheet", "Description") and p[2].strip()]
        if ins["dnp"]:  # Eagle 7.5 nie zna populate="no"
            attrs.append('<attribute name="DNP" value="yes"/>'); warn["czesc DNP -> atrybut DNP=yes (Eagle 7 nie ma 'nie montowac')"] += 1
        part_xml[ins["pname"]] = '<part name=%s library=%s deviceset=%s device=%s value=%s%s>%s</part>' % (
            a(ins["pname"]), a(LIB), a(ds["name"]), a(ds["devname"][ins["pkg"]]),
            a(netname(ins["value"]) if ins["power"] else ins["value"]), "", "".join(attrs))
    at_ = []
    for k, nm, L in (("Reference", "NAME", 95), ("Value", "VALUE", 96)):
        p = ins["props"][k]
        hide = flag(kid(p, "effects") or ["x"], "hide") or flag(p, "hide") or (ins["power"] and k == "Reference")
        at_.append('<attribute name="%s" %s display="%s"/>' % (nm, text_xml(p, L, conv_s), "off" if hide else "value"))
    inst_xml.append('<instance part=%s gate=%s x="%s" y="%s" smashed="yes" rot="%s">\n%s\n</instance>' % (
        a(ins["pname"]), a(ins["U"]["gate"]), f(EX(ins["X"])), f(EY(ins["Y"])), eagle_rot(ins["A"], ins["mir"]), "\n".join(at_)))

plain = []
for t in kids(sch, "text"):
    plain.append("<text %s>%s</text>" % (text_xml(t, 97, conv_s), escape(t[1])))
for g in kids(sch, "polyline") + kids(sch, "rectangle") + kids(sch, "circle") + kids(sch, "arc"):
    plain += gfx(g, 97, conv_s)
nnc = len(kids(sch, "no_connect"))
if nnc:
    warn["znaczniki 'niepodlaczony' pominiete (Eagle 7 ich nie ma)"] += nnc
for t in ("text_box", "image", "table"):
    if kids(sch, t):
        warn["%s pominiete" % t] += len(kids(sch, t))

layers = open(os.path.join(A.szablony, "warstwy_sch.xml"), encoding="utf-8").read().strip()
sym_xml = "\n".join(U["xml"] for s in symbols.values() for U in s["units"].values())
xml = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE eagle SYSTEM "eagle.dtd">
<eagle version="%s">
<drawing>
<settings>
<setting alwaysvectorfont="yes"/>
<setting verticaltext="up"/>
</settings>
<grid distance="1.27" unitdist="mm" unit="mm" style="lines" multiple="1" display="no" altdistance="0.635" altunitdist="mm" altunit="mm"/>
%s
<schematic xreflabel="%%F%%N/%%S.%%C%%R" xrefpart="/%%S.%%C%%R">
<description>%s - konwersja z KiCad (KiCad2Eagle)</description>
<libraries>
<library name=%s>
%s
<symbols>
%s
</symbols>
<devicesets>
%s
</devicesets>
</library>
</libraries>
<attributes>
</attributes>
<variantdefs>
</variantdefs>
%s
<parts>
%s
</parts>
<sheets>
<sheet>
<plain>
%s
</plain>
<instances>
%s
</instances>
<busses>
</busses>
<nets>
%s
</nets>
</sheet>
</sheets>
</schematic>
</drawing>
</eagle>
""" % (EAGLE_VERSION, layers, escape(PROJ), a(LIB), packages_xml, sym_xml, "\n".join(dev_xml), classes_xml,
       "\n".join(part_xml.values()), "\n".join(plain), "\n".join(inst_xml),
       "\n".join('<net name=%s class="%s">\n%s\n</net>' % (a(nm), sig_class.get(nm, "0"), "\n".join(segs)) for nm, segs in net_xml.items()))
open(A.out, "w", encoding="utf-8", newline="\n").write(xml)
nunits = sum(len(s["units"]) for s in symbols.values())
stats = collections.OrderedDict([
    ("symbole (bramki)", nunits), ("devicesety", len(devsets)), ("czesci", len(part_xml)), ("instancje", len(inst_xml)),
    ("czesci wieloczesciowe", sum(1 for s in symbols.values() if len(s["units"]) > 1)),
    ("przewody", len(wires)), ("etykiety", len(labels)), ("etykiety globalne", sum(1 for l in labels if l["glob"])),
    ("sieci", len(net_xml)), ("sieci bez nazwy (N$)", unnamed),
    ("piny z siecia", pin_total), ("piny na przewodzie", pin_hits),
    ("przesuniecie ukladu [mm]", "x %s, y %s" % (f(XOFF), f(YOFF))),
])
json.dump({"stats": stats, "warn": dict(warn), "conflicts": conflicts},
          open(A.raport, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
for k, v in stats.items():
    print("%s: %s" % (k, v))
for c in conflicts[:20]:
    print("KONFLIKT nazw sieci w grupie przewodow:", c)
for k, v in sorted(warn.items()):
    print("UWAGA: %s x %d" % (k, v))
