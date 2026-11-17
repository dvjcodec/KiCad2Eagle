# -*- coding: utf-8 -*-
"""Przebieg konwersji KiCad -> Eagle 7.5 (uzywany przez GUI i z wiersza polecen).

python runner.py <projekt.kicad_pro> [--wynik FOLDER] [--bez-plytki] [--bez-schematu] [--bez-weryfikacji] [--bez-eagle]
"""
import os, sys, re, glob, json, shutil, tempfile, subprocess, datetime, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
NOISE = re.compile(r"Adding duplicate image handler|pgm_base\.cpp|^\s*$")
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _ver_key(path):
    m = re.findall(r"(\d+)", path)
    return [int(x) for x in m]


def find_kicad_bin():
    cands = []
    la = os.environ.get("LOCALAPPDATA", "")
    for pat in (os.path.join(la, "Programs", "KiCad", "*", "bin"), r"C:\Program Files\KiCad\*\bin", r"C:\Program Files (x86)\KiCad\*\bin"):
        for d in glob.glob(pat):
            if os.path.exists(os.path.join(d, "kicad-cli.exe")) and os.path.exists(os.path.join(d, "python.exe")):
                cands.append(d)
    cands.sort(key=_ver_key, reverse=True)
    return cands[0] if cands else ""


def find_eagle_bin():
    cands = []
    for pat in (r"C:\EAGLE-*\bin", r"C:\Program Files\EAGLE-*\bin", r"C:\Program Files (x86)\EAGLE-*\bin", r"D:\EAGLE-*\bin"):
        for d in glob.glob(pat):
            if os.path.exists(os.path.join(d, "eaglecon.exe")):
                cands.append(d)
    cands.sort(key=_ver_key, reverse=True)
    return cands[0] if cands else ""


class Przerwano(Exception):
    pass


class Runner:
    def __init__(self, pro, wynik=None, plytka=True, schemat=True, weryfikacja=True, eagle=True,
                 kicad_bin=None, eagle_bin=None, log=print):
        self.pro = os.path.abspath(pro)
        self.base = os.path.splitext(self.pro)[0]
        self.name = os.path.basename(self.base)
        self.wynik = os.path.abspath(wynik or os.path.join(os.path.dirname(self.pro), "eagle75"))
        self.plytka, self.schemat, self.weryfikacja, self.eagle = plytka, schemat, weryfikacja, eagle
        self.kicad_bin = kicad_bin or find_kicad_bin()
        self.eagle_bin = eagle_bin if eagle_bin is not None else find_eagle_bin()
        self.log = log
        self.proc = None
        self.stop = False
        self.rap = {"start": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "kroki": []}

    # ---------------------------------------------------------------- pomocnicze
    def run(self, args, cwd=None):
        if self.stop:
            raise Przerwano()
        self.log("> " + " ".join('"%s"' % x if " " in x else x for x in args))
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        self.proc = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     creationflags=CREATE_NO_WINDOW)
        lines = []
        for raw in self.proc.stdout:
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:  # kicad-cli pisze po polsku w stronie kodowej Windows
                line = raw.decode("cp1250", errors="replace")
            line = line.rstrip()
            if NOISE.search(line):
                continue
            lines.append(line)
            self.log("  " + line)
        rc = self.proc.wait()
        self.proc = None
        if self.stop:
            raise Przerwano()
        return rc, lines

    def kpy(self, script, *args):
        return self.run([os.path.join(self.kicad_bin, "python.exe"), os.path.join(HERE, script)] + list(args))

    def cancel(self):
        self.stop = True
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass

    def step(self, nazwa, ok, opis=""):
        self.rap["kroki"].append((nazwa, ok, opis))
        self.log(("[OK] " if ok else "[BLAD] ") + nazwa + ((" - " + opis) if opis else ""))

    # ---------------------------------------------------------------- przebieg
    def execute(self):
        tmp = tempfile.mkdtemp(prefix="kicad2eagle_")
        try:
            return self._execute(tmp)
        except Przerwano:
            self.log("Przerwano.")
            return False
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _execute(self, tmp):
        if not self.kicad_bin or not os.path.exists(os.path.join(self.kicad_bin, "kicad-cli.exe")):
            self.log("BLAD: nie znaleziono KiCada (kicad-cli.exe, python.exe). Wskaz folder bin KiCada.")
            return False
        src = {e: self.base + e for e in (".kicad_pro", ".kicad_pcb", ".kicad_sch", ".kicad_dru")}
        if not os.path.exists(src[".kicad_pcb"]):
            self.plytka = False
            self.log("Brak pliku plytki %s - tylko schemat." % src[".kicad_pcb"])
        if not os.path.exists(src[".kicad_sch"]):
            self.schemat = False
            self.log("Brak pliku schematu %s - tylko plytka." % src[".kicad_sch"])
        if not (self.plytka or self.schemat):
            self.log("BLAD: nie ma czego konwertowac.")
            return False
        os.makedirs(os.path.join(self.wynik, "zrodlo"), exist_ok=True)
        work = os.path.join(tmp, "zrodlo")
        os.makedirs(work)
        self.log("Kopia zrodel -> %s" % os.path.join(self.wynik, "zrodlo"))
        for e, p in src.items():
            if os.path.exists(p):
                shutil.copy2(p, os.path.join(self.wynik, "zrodlo"))
                shutil.copy2(p, work)
                self.rap.setdefault("zrodla", []).append((os.path.basename(p), datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M")))
        W = lambda e: os.path.join(work, self.name + e)
        brd = os.path.join(self.wynik, self.name + ".brd")
        sch = os.path.join(self.wynik, self.name + ".sch")
        mapa = os.path.join(tmp, "mapa_padow.json")
        r_brd, r_sch = os.path.join(tmp, "raport_brd.json"), os.path.join(tmp, "raport_sch.json")
        netl = os.path.join(tmp, "netlista.net")
        ok_all = True

        # ---- plytka
        if self.plytka:
            self.log("\n=== Plytka ===")
            args = ["--pcb", W(".kicad_pcb"), "--pro", W(".kicad_pro"), "--out", brd, "--map", mapa, "--raport", r_brd]
            if os.path.exists(W(".kicad_dru")):
                args += ["--dru", W(".kicad_dru")]
            rc, _ = self.kpy("brd.py", *args)
            self.step("konwersja plytki", rc == 0 and os.path.exists(brd), os.path.basename(brd) if rc == 0 else "kod %d" % rc)
            if rc == 0:
                self.rap["brd"] = json.load(open(r_brd, encoding="utf-8"))
            else:
                ok_all = False; self.plytka = False
        # ---- schemat
        if self.schemat:
            self.log("\n=== Schemat ===")
            rc, _ = self.run([os.path.join(self.kicad_bin, "kicad-cli.exe"), "sch", "export", "netlist", "--format", "kicadsexpr",
                              "-o", netl, W(".kicad_sch")])
            self.step("eksport netlisty KiCad", rc == 0 and os.path.exists(netl))
            if rc == 0:
                args = ["--sch", W(".kicad_sch"), "--net", netl, "--out", sch, "--raport", r_sch]
                if self.plytka:
                    args += ["--brd", brd, "--map", mapa]
                rc, lines = self.kpy("sch.py", *args)
                msg = next((l for l in lines if l.startswith("BLAD")), "")
                self.step("konwersja schematu", rc == 0 and os.path.exists(sch), msg or os.path.basename(sch))
                if rc == 0:
                    self.rap["sch"] = json.load(open(r_sch, encoding="utf-8"))
                else:
                    ok_all = False; self.schemat = False
            else:
                ok_all = False; self.schemat = False

        # ---- weryfikacja
        if self.weryfikacja and self.plytka:
            self.log("\n=== Weryfikacja plytki (import .brd z powrotem do KiCada) ===")
            vdir = os.path.join(tmp, "weryfikacja"); os.makedirs(vdir)
            vbrd = os.path.join(vdir, "kopia.brd")
            # importer KiCada nie zna warstwy 46 (Milling) w obudowach
            with open(brd, encoding="utf-8") as fi, open(vbrd, "w", encoding="utf-8", newline="\n") as fo:
                for line in fi:
                    if 'layer="46"' not in line:
                        fo.write(line)
            reimp = os.path.join(vdir, "reimport.kicad_pcb")
            rc, _ = self.run([os.path.join(self.kicad_bin, "kicad-cli.exe"), "pcb", "import", "--format", "eagle", "-o", reimp, vbrd])
            if rc == 0 and os.path.exists(reimp):
                rv = os.path.join(tmp, "wer_brd.json")
                rc, _ = self.kpy("porownaj_brd.py", "--orig", W(".kicad_pcb"), "--reimport", reimp, "--raport", rv)
                if rc == 0:
                    d = json.load(open(rv, encoding="utf-8")); self.rap["wer_brd"] = d
                    tol = set(d["roznice"]) <= {"inny rozmiar padu (obwiednia) > 0,01 mm"}
                    self.step("porownanie plytki", tol, "zero roznic polaczen" if tol else "sa roznice - patrz raport")
                    ok_all &= tol
                else:
                    self.step("porownanie plytki", False, "kod %d" % rc); ok_all = False
            else:
                self.step("import .brd do KiCada", False, "kod %d" % rc); ok_all = False
        if self.weryfikacja and self.schemat:
            self.log("\n=== Weryfikacja schematu ===")
            rv = os.path.join(tmp, "wer_sch.json")
            args = ["--sch", sch, "--net", netl, "--raport", rv] + (["--brd", brd] if self.plytka else [])
            rc, _ = self.kpy("sprawdz_sch.py", *args)
            if rc == 0:
                d = json.load(open(rv, encoding="utf-8")); self.rap["wer_sch"] = d
                self.step("kontrola schematu", not d["roznice"], "zero roznic" if not d["roznice"] else "sa roznice - patrz raport")
                ok_all &= not d["roznice"]
            else:
                self.step("kontrola schematu", False, "kod %d" % rc); ok_all = False

        # ---- Eagle 7.5 (eaglecon)
        if self.eagle and (self.plytka or self.schemat):
            exe = os.path.join(self.eagle_bin or "", "eaglecon.exe")
            if not self.eagle_bin or not os.path.exists(exe):
                self.step("test w Eagle", False, "nie znaleziono eaglecon.exe - pominiety")
            else:
                self.log("\n=== Test w Eagle (eaglecon) ===")
                edir = os.path.join(tmp, "eagle"); os.makedirs(edir)
                self.rap["eagle"] = []
                for f_, dev, out, layers in ((brd, "EXCELLON", "wiercenia.drd", ["Drills", "Holes"]),
                                             (sch, "HPGL", "test.plt", ["Info"])):
                    if not os.path.exists(f_) or (f_ == brd and not self.plytka) or (f_ == sch and not self.schemat):
                        continue
                    shutil.copy2(f_, edir)
                    rc, lines = self.run([exe, "-X", "-N", "-d" + dev, "-o" + out, os.path.basename(f_)] + layers, cwd=edir)
                    probs = [l for l in lines if re.search(r"error|invalid|warning|unknown", l, re.I)]
                    extra = ""
                    if dev == "EXCELLON" and os.path.exists(os.path.join(edir, out)):
                        n = sum(1 for l in open(os.path.join(edir, out), errors="replace") if l.startswith("X"))
                        extra = ", otworow w pliku wiercen: %d" % n
                    okk = rc == 0 and not probs
                    self.rap["eagle"].append((os.path.basename(f_), okk, probs[:20]))
                    self.step("Eagle wczytal %s" % os.path.basename(f_), okk, ("bez bledow i ostrzezen" if okk else "%d komunikatow" % len(probs)) + extra)
                    ok_all &= okk

        self.raport(ok_all)
        self.log("\nGotowe. Wynik: %s" % self.wynik)
        return ok_all

    # ---------------------------------------------------------------- raport
    def raport(self, ok_all):
        R = self.rap
        L = ["# Raport konwersji KiCad → Eagle 7.5", "",
             "Projekt: `%s`  " % self.pro, "Data: %s  " % R["start"], "Wynik: **%s**" % ("bez różnic połączeń, Eagle bez błędów" if ok_all else "są uwagi – patrz niżej"), ""]
        L += ["| Plik | Zmieniony |", "|---|---|"] + ["| `zrodlo\\%s` | %s |" % z for z in R.get("zrodla", [])] + [""]
        L += ["## Kroki", "", "| Krok | Wynik | Opis |", "|---|---|---|"]
        L += ["| %s | %s | %s |" % (n, "OK" if ok else "BŁĄD", o) for n, ok, o in R["kroki"]] + [""]
        for key, tyt in (("brd", "Płytka"), ("sch", "Schemat")):
            if key not in R: continue
            d = R[key]
            L += ["## %s – statystyki" % tyt, ""] + ["- %s: %s" % kv for kv in d["stats"].items()] + [""]
            if d.get("warn"):
                L += ["Ostrzeżenia konwersji (liczba wystąpień):", ""] + ["- %s × %d" % kv for kv in sorted(d["warn"].items())] + [""]
            if d.get("dru_ok") or d.get("dru_skip"):
                L += ["Reguły `.kicad_dru`:", ""]
                L += ["- przeniesiona (macierz odstępów klas): **%s** – %s" % tuple(r) for r in d.get("dru_ok", [])]
                L += ["- NIEprzeniesiona: **%s** – %s" % tuple(r) for r in d.get("dru_skip", [])] + [""]
            if d.get("conflicts"):
                L += ["Konflikty nazw sieci:", ""] + ["- %s" % c for c in d["conflicts"]] + [""]
        for key, tyt in (("wer_brd", "Weryfikacja płytki (import .brd do KiCada i porównanie z oryginałem, tolerancja 0,002 mm)"),
                         ("wer_sch", "Weryfikacja schematu (geometria pinów, zgodność z płytką jak w Eagle, netlista KiCad)")):
            if key not in R: continue
            d = R[key]
            L += ["## " + tyt, ""] + ["- %s: %s" % kv for kv in d["wyniki"].items()]
            if d["roznice"]:
                L += ["", "Różnice:", ""]
                for k, v in d["roznice"].items():
                    L.append("- %s: %d" % (k, v))
                    for s in d["przyklady"].get(k, [])[:5]:
                        L.append("  - `%s`" % s)
            if d.get("rozmiary_padow"):
                L += ["", "Rozmiary padów (oczekiwane przybliżenia: dodatkowa miedź w obudowie liczona przez importer KiCada osobno, "
                      "pady „long” o jednej proporcji na płytkę):", ""]
                L += ["- %s × %d" % kv for kv in d["rozmiary_padow"].items()]
            L.append("")
        if "eagle" in R:
            L += ["## Test w Eagle 7.5 (eaglecon)", ""]
            for f_, ok, probs in R["eagle"]:
                L.append("- `%s`: %s" % (f_, "wczytany bez błędów i ostrzeżeń" if ok else "komunikaty:"))
                L += ["  - `%s`" % p for p in probs]
            L.append("")
        L += ["## Ograniczenia Eagle 7.5 (ogólne)", "",
              "- Polygony są zapisane jako obrysy – w Eagle kliknij **Ratsnest**, żeby je wylać.",
              "- Reguły warunkowe `.kicad_dru` przechodzą tylko jako macierz odstępów klas (dla wszystkich obiektów, nie tylko ścieżek).",
              "- Pady THT prostokątne/zaokrąglone/owalne o innej proporcji: pad Eagle + miedź w obudowie; otwory owalne – kontur na warstwie 46 Milling.",
              "- Pierścienie padów THT na warstwach wewnętrznych liczy Eagle z reguł DRC (25 % wiercenia, 0,15–0,5 mm).",
              "- Schemat: brak znaczników „niepodłączony”, ramki arkusza i magistral; nazwy pinów stałą czcionką Eagle (1,778 mm).",
              "- Płytka > 100×80 mm lub > 2 warstwy wymaga odpowiedniej licencji Eagle (Standard/Professional).", ""]
        p = os.path.join(self.wynik, "RAPORT_KONWERSJI.md")
        open(p, "w", encoding="utf-8").write("\n".join(L))
        self.log("Raport: %s" % p)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pro"); ap.add_argument("--wynik")
    ap.add_argument("--bez-plytki", action="store_true"); ap.add_argument("--bez-schematu", action="store_true")
    ap.add_argument("--bez-weryfikacji", action="store_true"); ap.add_argument("--bez-eagle", action="store_true")
    ap.add_argument("--kicad-bin"); ap.add_argument("--eagle-bin")
    A = ap.parse_args()
    r = Runner(A.pro, A.wynik, not A.bez_plytki, not A.bez_schematu, not A.bez_weryfikacji, not A.bez_eagle,
               A.kicad_bin, A.eagle_bin)
    sys.exit(0 if r.execute() else 1)
