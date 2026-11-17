# -*- coding: utf-8 -*-
"""Kontrola plytki: oryginal .kicad_pcb <-> .brd wczytany z powrotem do KiCada
(kicad-cli pcb import --format eagle). Uruchamiac Pythonem KiCada.
python.exe porownaj_brd.py --orig X.kicad_pcb --reimport Y.kicad_pcb --raport wynik.json
"""
import sys, os, json, collections, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcbnew
from common import ename, netname

ap = argparse.ArgumentParser()
ap.add_argument("--orig", required=True); ap.add_argument("--reimport", required=True); ap.add_argument("--raport", required=True)
A_ = ap.parse_args()
A = pcbnew.LoadBoard(A_.orig); B = pcbnew.LoadBoard(A_.reimport)
if A is None or B is None:
    sys.exit("BLAD: nie mozna wczytac plytki do porownania")
TOL = 2000  # nm
err = collections.Counter(); ex = collections.defaultdict(list); allbad = collections.defaultdict(list)
res = collections.OrderedDict()
def bad(k, s):
    err[k] += 1; allbad[k].append(s)
    if len(ex[k]) < 5: ex[k].append(s)
def N(s): return netname(str(s))
def cu(bd, item):
    # zbior warstw miedzi: F/B oraz wewnetrzne po kolejnosci (In1 w KiCad -> In1 po imporcie)
    return set(bd.GetLayerName(l) for l in item.GetLayerSet().CuStack())

fa = {ename(f.GetReference()): f for f in A.GetFootprints()}
fb = {str(f.GetReference()).upper(): f for f in B.GetFootprints()}
res["elementy"] = "%d / %d" % (len(fa), len(fb))
r0 = sorted(set(fa) & set(fb))[0] if set(fa) & set(fb) else None
dx = fb[r0].GetPosition().x - fa[r0].GetPosition().x if r0 else 0
dy = fb[r0].GetPosition().y - fa[r0].GetPosition().y if r0 else 0
def mv(p): return (p.x + dx, p.y + dy)
def near(p, q, t=TOL): return abs(p[0] - q[0]) <= t and abs(p[1] - q[1]) <= t

npads = 0
for r, f in fa.items():
    g = fb.get(r)
    if g is None:
        bad("brak elementu", r); continue
    if f.IsFlipped() != g.IsFlipped(): bad("inna strona", r)
    if not near(mv(f.GetPosition()), (g.GetPosition().x, g.GetPosition().y)): bad("inna pozycja elementu", r)
    gp = list(g.Pads())
    for p in f.Pads():
        if p.GetAttribute() != pcbnew.PAD_ATTRIB_PTH and not (p.IsOnLayer(pcbnew.F_Cu) or p.IsOnLayer(pcbnew.B_Cu)):
            continue
        npads += 1
        P = mv(p.GetPosition())
        cand = [q for q in gp if near((q.GetPosition().x, q.GetPosition().y), P)]
        if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
            if not cand: bad("brak otworu NPTH", "%s @%.3f,%.3f" % (r, P[0] / 1e6, P[1] / 1e6))
            continue
        cand2 = [q for q in cand if q.GetAttribute() == p.GetAttribute()]
        if not cand2:
            bad("brak padu w tym miejscu", "%s.%s" % (r, p.GetNumber())); continue
        same = [q for q in cand2 if cu(A, p) == cu(B, q)]
        # kilka padow w jednym miejscu (np. pad termiczny na F i B): wybierz najblizszy rozmiarem
        sz = lambda q: abs(q.GetSize().x - p.GetSize().x) + abs(q.GetSize().y - p.GetSize().y)
        q = min(same or cand2, key=sz)
        if p.GetNetname() and N(p.GetNetname()) != N(q.GetNetname()):
            bad("inna siec padu", "%s.%s %s != %s" % (r, p.GetNumber(), p.GetNetname(), q.GetNetname()))
        if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD and cu(A, p) != cu(B, q):
            bad("inna warstwa padu SMD", "%s.%s" % (r, p.GetNumber()))
        la = pcbnew.F_Cu if p.IsOnLayer(pcbnew.F_Cu) else pcbnew.B_Cu
        lb = pcbnew.F_Cu if q.IsOnLayer(pcbnew.F_Cu) else pcbnew.B_Cu
        ba = p.GetEffectivePolygon(la).BBox(); bb_ = q.GetEffectivePolygon(lb).BBox()
        if max(abs(ba.GetWidth() - bb_.GetWidth()), abs(ba.GetHeight() - bb_.GetHeight())) > 10000:
            bad("inny rozmiar padu (obwiednia) > 0,01 mm", "%s.%s %.3fx%.3f -> %.3fx%.3f" % (
                r, p.GetNumber(), ba.GetWidth() / 1e6, ba.GetHeight() / 1e6, bb_.GetWidth() / 1e6, bb_.GetHeight() / 1e6))
        if p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH and abs(min(p.GetDrillSize().x, p.GetDrillSize().y) - q.GetDrillSize().x) > 1000:
            bad("inne wiercenie", "%s.%s" % (r, p.GetNumber()))
for r in fb:
    if r not in fa: bad("element nadmiarowy po imporcie", r)
res["pady sprawdzone"] = npads

def items(bd, shift):
    T, V = [], []
    for t in bd.GetTracks():
        n = N(t.GetNetname())
        if t.GetClass() == "PCB_VIA":
            p = t.GetPosition()
            V.append((n, t.Cast().GetDrillValue(), t.Cast().GetWidth(pcbnew.F_Cu), (p.x + shift[0], p.y + shift[1])))
        else:
            s, e = t.GetStart(), t.GetEnd()
            T.append((n, bd.GetLayerName(t.GetLayer()), t.GetWidth(), (s.x + shift[0], s.y + shift[1]), (e.x + shift[0], e.y + shift[1])))
    return T, V
def match(LA, LB, key, pts):
    idx = collections.defaultdict(list)
    for i, it in enumerate(LB): idx[key(it)].append(i)
    used = set(); miss = []
    for it in LA:
        ok = False
        for i in idx.get(key(it), []):
            if i in used: continue
            pa, pb = pts(it), pts(LB[i])
            if all(near(x, y) for x, y in zip(pa, pb)) or (len(pa) == 2 and near(pa[0], pb[1]) and near(pa[1], pb[0])):
                used.add(i); ok = True; break
        if not ok: miss.append(it)
    return miss, [LB[i] for i in range(len(LB)) if i not in used]
Ta, Va = items(A, (dx, dy)); Tb, Vb = items(B, (0, 0))
m1, m2 = match(Ta, Tb, lambda t: (t[0], t[1], t[2]), lambda t: (t[3], t[4]))
res["sciezki"] = "%d / %d, bez pary %d / %d" % (len(Ta), len(Tb), len(m1), len(m2))
for k in m1[:5]: bad("sciezka tylko w oryginale", str(k))
for k in m2[:5]: bad("sciezka tylko po imporcie", str(k))
if len(m1) > 5: err["sciezka tylko w oryginale"] = len(m1)
if len(m2) > 5: err["sciezka tylko po imporcie"] = len(m2)
m1, m2 = match(Va, Vb, lambda t: (t[0], t[1], t[2]), lambda t: (t[3],))
res["przelotki"] = "%d / %d, bez pary %d / %d" % (len(Va), len(Vb), len(m1), len(m2))
for k in m1[:5]: bad("przelotka tylko w oryginale", str(k))
if len(m1) > 5: err["przelotka tylko w oryginale"] = len(m1)

def simplify(P):
    # usuwa punkty powtorzone i wspolliniowe (rowniez na styku konca i poczatku obrysu) - nie zmieniaja ksztaltu
    ch = True
    while ch and len(P) > 3:
        ch = False
        for i in range(len(P)):
            a, b, c = P[i - 1], P[i], P[(i + 1) % len(P)]
            if b == a or (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) == 0:
                del P[i]; ch = True; break
    return P
def zones(bd, shift):
    Z = collections.Counter()
    for z in bd.Zones():
        if z.GetIsRuleArea(): continue
        for oi in range(z.Outline().OutlineCount()):
            o = z.Outline().Outline(oi)
            P = [(round((o.CPoint(i).x + shift[0]) / 10000), round((o.CPoint(i).y + shift[1]) / 10000)) for i in range(o.PointCount())]
            pts = tuple(sorted(simplify(P)))
            for l in z.GetLayerSet().CuStack():
                Z[(bd.GetLayerName(l), N(z.GetNetname()), pts)] += 1
    return Z
Za, Zb = zones(A, (dx, dy)), zones(B, (0, 0))
res["strefy (obrys+siec+warstwa)"] = "%d / %d, identyczne %d" % (sum(Za.values()), sum(Zb.values()), sum((Za & Zb).values()))
if sum((Za - Zb).values()): bad("strefa o innym obrysie", "%d" % sum((Za - Zb).values()))
na = set(N(n) for n in A.GetNetsByName().keys() if str(n)); nb = set(N(n) for n in B.GetNetsByName().keys() if str(n))
res["sieci"] = "%d / %d" % (len(na), len(nb))
if na - nb: bad("siec brakujaca po imporcie", ", ".join(sorted(na - nb)[:10]))
ea = A.GetBoardEdgesBoundingBox(); eb = B.GetBoardEdgesBoundingBox()
res["kontur [mm]"] = "%.3f x %.3f / %.3f x %.3f" % (ea.GetWidth() / 1e6, ea.GetHeight() / 1e6, eb.GetWidth() / 1e6, eb.GetHeight() / 1e6)
sizes = collections.Counter(x.split(" ", 1)[1] for x in allbad.get("inny rozmiar padu (obwiednia) > 0,01 mm", []))
out = {"wyniki": res, "roznice": dict(err), "przyklady": dict(ex), "rozmiary_padow": dict(sizes)}
json.dump(out, open(A_.raport, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
for k, v in res.items(): print("%s: %s" % (k, v))
print("ROZNICE: brak" if not err else "ROZNICE:")
for k, v in err.items():
    print("  %s: %d" % (k, v))
    for s in ex[k]: print("     ", s)
for k, v in sizes.items():
    print("  rozmiar padu %s  x%d" % (k, v))
