# -*- coding: utf-8 -*-
"""Minimalny parser S-wyrazen KiCad (tylko odczyt)."""
import re

_tok = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+')


class Q(str):
    """napis w cudzyslowie (odroznienie od symbolu)"""


def parse(text):
    stack = [[]]
    for m in _tok.finditer(text):
        t = m.group(0)
        if t == "(":
            stack.append([])
        elif t == ")":
            x = stack.pop()
            stack[-1].append(x)
        elif t[0] == '"':
            stack[-1].append(Q(t[1:-1].replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n")))
        else:
            stack[-1].append(t)
    return stack[0][0]


def tag(x):
    return x[0] if isinstance(x, list) and x else None


def kids(x, name):
    return [c for c in x[1:] if isinstance(c, list) and c and c[0] == name]


def kid(x, name, default=None):
    for c in x[1:]:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return default


def val(x, name, default=None, i=1):
    k = kid(x, name)
    if k is None or len(k) <= i:
        return default
    return k[i]


def at(x):
    k = kid(x, "at")
    if k is None:
        return (0.0, 0.0, 0.0)
    return (float(k[1]), float(k[2]), float(k[3]) if len(k) > 3 else 0.0)


def flag(x, name):
    """(hide yes) / (hide) / goly symbol hide"""
    for c in x[1:]:
        if c == name:
            return True
        if isinstance(c, list) and c and c[0] == name:
            return len(c) == 1 or c[1] in ("yes", "true")
    return False
