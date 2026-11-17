# -*- coding: utf-8 -*-
"""Kontrola schematu: .sch (Eagle) <-> .brd (Eagle) <-> netlista KiCad.
python sprawdz_sch.py --sch X.sch [--brd X.brd] --net netlista.net --raport wynik.json
1) geometria: kazdy pinref lezy na przewodzie swojego segmentu (pozycja pinu wg regul Eagle: MRa = obrot a, potem lustro X)
2) zgodnosc sch/brd jak w Eagle (czesci, obudowy, wartosci, siec -> zbior padow)
3) zgodnosc z netlista KiCad (kazdy pin w sieci o tej samej nazwie)
4) piny Sup w sieci o nazwie pinu; niepodlaczone piny Pwr, ktore Eagle polaczylby sam
"""
import sys, os, math, json, collections, argparse, xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sexp import parse, kids, kid, val
from common import ename, netname

ap = argparse.ArgumentParser()
ap.add_argument("--sch", required=True); ap.add_argument("--brd"); ap.add_argument("--net", required=True)
ap.add_argument("--raport", required=True)
A = ap.parse_args()
S = ET.parse(A.sch).getroot()
B = ET.parse(A.brd).getroot() if A.brd and os.path.exists(A.brd) else None
net = parse(open(A.net, encoding="utf-8").read())
err = collections.Counter(); ex = collections.defaultdict(list); res = collections.OrderedDict()
def bad(k, s):
    err[k] += 1
    if len(ex[k]) < 6: ex[k].append(s)

lib = S.find(".//library")
syms = {s.get("name"): {p.get("name"): (float(p.get("x")), float(p.get("y")), p.get("direction")) for p in s.findall("pin")}
        for s in lib.find("symbols")}
dsets = {}
for ds in lib.find("devicesets"):
    gates = {g.get("name"): g.get("symbol") for g in ds.findall("gates/gate")}
    devs = {}
    for d in ds.find("devices"):
        cons = {(c.get("gate"), c.get("pin")): c.get("pad").split() for c in d.findall("connects/connect")}
        devs[d.get("name")] = (d.get("package"), cons)
    dsets[ds.get("name")] = (gates, devs)
parts = {p.get("name"): p for p in S.find(".//parts")}
insts = {(i.get("part"), i.get("gate")): i for i in S.findall(".//instances/instance")}

def tr(rotstr, x, y):
    m = "M" in rotstr
    a_ = math.radians(float(rotstr.split("R")[1]))
    X = x * math.cos(a_) - y * math.sin(a_); Y = x * math.sin(a_) + y * math.cos(a_)
    return (-X, Y) if m else (X, Y)

npin = 0
sch_net = collections.defaultdict(set)
for n in S.findall(".//nets/net"):
    nm = n.get("name")
    for seg in n.findall("segment"):
        ws = [tuple(float(w.get(k)) for k in ("x1", "y1", "x2", "y2")) for w in seg.findall("wire")]
        for pr in seg.findall("pinref"):
            pt, gt, pn = pr.get("part"), pr.get("gate"), pr.get("pin")
            sch_net[nm].add((pt, gt, pn))
            ins = insts.get((pt, gt))
            if ins is None:
                bad("pinref do nieumieszczonej bramki", "%s %s" % (pt, gt)); continue
            sym = dsets[parts[pt].get("deviceset")][0][gt]
            if pn not in syms[sym]:
                bad("pinref do nieistniejacego pinu", "%s.%s.%s" % (pt, gt, pn)); continue
            px, py, d = syms[sym][pn]
            dx, dy = tr(ins.get("rot", "R0"), px, py)
            P = (float(ins.get("x")) + dx, float(ins.get("y")) + dy)
            npin += 1
            if d == "sup" and nm != pn.split("@")[0]:
                bad("pin zasilania w sieci o innej nazwie", "%s %s w %s" % (pt, pn, nm))
            if not ws:
                continue
            ok = False
            for x1, y1, x2, y2 in ws:
                vx, vy = x2 - x1, y2 - y1; L2 = vx * vx + vy * vy
                t = min(1, max(0, ((P[0] - x1) * vx + (P[1] - y1) * vy) / L2)) if L2 else 0
                if math.hypot(x1 + t * vx - P[0], y1 + t * vy - P[1]) < 0.002:
                    ok = True; break
            if not ok:
                bad("pin nie lezy na przewodzie swojego segmentu", "%s.%s @%.3f,%.3f (%s)" % (pt, pn, P[0], P[1], nm))
res["pinref sprawdzone geometrycznie"] = npin

# piny Pwr bez polaczenia, ktore Eagle podlaczylby sam do sieci o tej nazwie
conn = set((p, g, n) for s in sch_net.values() for p, g, n in s)
for pn, p in parts.items():
    gates, devs = dsets[p.get("deviceset")]
    for g, sym in gates.items():
        if (pn, g) not in insts: continue
        for pin, (_, _, d) in syms[sym].items():
            if d == "pwr" and (pn, g, pin) not in conn and pin.split("@")[0] in sch_net:
                bad("niepodlaczony pin Pwr - Eagle polaczy go z siecia o tej nazwie", "%s.%s" % (pn, pin))

if B is not None:
    elems = {e.get("name"): e for e in B.findall(".//elements/element")}
    brd_sig = {s.get("name"): set((c.get("element"), c.get("pad")) for c in s.findall("contactref")) for s in B.findall(".//signals/signal")}
    for pn, p in parts.items():
        gates, devs = dsets[p.get("deviceset")]
        pkg, cons = devs[p.get("device")]
        if pkg is None: continue
        e = elems.get(pn)
        if e is None:
            bad("czesc bez elementu na plytce", pn); continue
        if e.get("package") != pkg: bad("inna obudowa", pn)
        if e.get("value") != p.get("value"): bad("inna wartosc", "%s %s / %s" % (pn, p.get("value"), e.get("value")))
    for en in elems:
        if en not in parts: bad("element na plytce bez czesci w schemacie", en)
    sch_pads = {}
    for nm, pins in sch_net.items():
        s = set()
        for pt, gt, pn in pins:
            gates, devs = dsets[parts[pt].get("deviceset")]
            pkg, cons = devs[parts[pt].get("device")]
            for pad in cons.get((gt, pn), []):
                s.add((pt, pad))
        sch_pads[nm] = s
    same = 0
    for nm in sorted(set(sch_pads) | set(brd_sig)):
        a_, b_ = sch_pads.get(nm, set()), brd_sig.get(nm, set())
        if a_ == b_: same += 1
        elif a_ or b_:
            bad("siec rozna sch/brd", "%s: tylko sch %s, tylko brd %s" % (nm, sorted(a_ - b_)[:3], sorted(b_ - a_)[:3]))
    res["sieci sch / brd / identyczne pady"] = "%d / %d / %d" % (len(sch_pads), len(brd_sig), same)

pin_net = {}
for n in kids(kid(net, "nets"), "net"):
    nodes = kids(n, "node")
    if len(nodes) >= 2:
        for nd in nodes:
            pin_net[(val(nd, "ref"), val(nd, "pin"))] = netname(val(n, "name"))
eagle_pin_net = {}
for nm, pins in sch_net.items():
    for pt, gt, pn in pins:
        eagle_pin_net[(pt, gt, pn)] = nm
# numer pinu KiCad -> (bramka, pin Eagle): z connects lub z kolejnosci w symbolu
ok = 0
for (ref, num), nm in pin_net.items():
    pt = ename(ref.lstrip("#"))
    if pt not in parts:
        if not ref.startswith("#FLG"):
            bad("pin z netlisty bez czesci", "%s.%s" % (ref, num))
        continue
    gates, devs = dsets[parts[pt].get("deviceset")]
    pkg, cons = devs[parts[pt].get("device")]
    if pkg is None:
        cand = [(g, p) for g, sym in gates.items() for p in syms[sym]]
    else:
        cand = [k for k, pads in cons.items() if any(x == ename(num) or x.startswith(ename(num) + "_") for x in pads)]
    hit = [eagle_pin_net.get((pt,) + c) for c in cand]
    if nm in hit: ok += 1
    else: bad("pin w innej sieci niz w KiCad", "%s.%s KiCad %s, Eagle %s" % (ref, num, nm, hit))
res["piny z netlisty KiCad zgodne"] = "%d / %d" % (ok, len(pin_net))
json.dump({"wyniki": res, "roznice": dict(err), "przyklady": dict(ex)}, open(A.raport, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
for k, v in res.items(): print("%s: %s" % (k, v))
print("ROZNICE: brak" if not err else "ROZNICE:")
for k, v in err.items():
    print("  %s: %d" % (k, v))
    for s in ex[k]: print("     ", s)
