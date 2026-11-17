# -*- coding: utf-8 -*-
"""Wspolne funkcje konwertera KiCad 10 -> Eagle 7.5 (XML)."""
import math, re
from xml.sax.saxutils import escape, quoteattr

LIB = "KICAD2EAGLE"
EAGLE_VERSION = "7.5.0"


def f(v, nd=4):
    """liczba -> tekst bez zbednych zer"""
    s = ("%." + str(nd) + "f") % v
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in ("-0", ""):
        s = "0"
    return s


def ang(a):
    a = a % 360.0
    if abs(a - 360.0) < 1e-6:
        a = 0.0
    return f(a, 3)


def rot(a, mirror=False, spin=False):
    return ("S" if spin else "") + ("M" if mirror else "") + "R" + ang(a)


_bad = re.compile(r"[^A-Z0-9_+\-./()$!#&%*:;=?\[\]<>|~^@]")


def ename(s):
    """nazwa Eagle: wielkie litery, bez spacji i przecinkow"""
    s = s.upper().replace(" ", "_").replace(",", "_")
    s = _bad.sub("_", s)
    return s or "_"


def netname(s):
    if s.startswith("/"):
        s = s[1:]
    s = s.replace("~{", "!").replace("}", "") if "~{" in s else s
    return ename(s)


def a(v):
    return quoteattr(str(v))


def arc_curve(sx, sy, mx, my, ex, ey):
    """kat (stopnie, + = CCW) luku przez 3 punkty w ukladzie z osia Y w gore"""
    d = 2 * (sx * (my - ey) + mx * (ey - sy) + ex * (sy - my))
    if abs(d) < 1e-12:
        return 0.0
    ux = ((sx * sx + sy * sy) * (my - ey) + (mx * mx + my * my) * (ey - sy) + (ex * ex + ey * ey) * (sy - my)) / d
    uy = ((sx * sx + sy * sy) * (ex - mx) + (mx * mx + my * my) * (sx - ex) + (ex * ex + ey * ey) * (mx - sx)) / d
    a1 = math.atan2(sy - uy, sx - ux)
    am = math.atan2(my - uy, mx - ux)
    a2 = math.atan2(ey - uy, ex - ux)
    ccw = (a2 - a1) % (2 * math.pi)
    ccm = (am - a1) % (2 * math.pi)
    if ccm <= ccw:
        return math.degrees(ccw)
    return -math.degrees(2 * math.pi - ccw)


def align(h, v):
    """KiCad justify (h: -1 lewo,0 srodek,1 prawo; v: -1 gora,0 srodek,1 dol) -> Eagle align"""
    hs = {-1: "left", 0: "center", 1: "right"}[h]
    vs = {-1: "top", 0: "center", 1: "bottom"}[v]
    if vs == "center" and hs == "center":
        return "center"
    if vs == "center":
        return "center-" + hs
    return vs + "-" + hs


def layers_block(template_text, visible=()):
    m = re.search(r"<layers>.*?</layers>", template_text, re.S)
    t = m.group(0)
    for n in visible:
        t = re.sub(r'(<layer number="%d" [^>]*visible=)"no"( active=)"no"' % n, r'\1"yes"\2"yes"', t)
    return t
