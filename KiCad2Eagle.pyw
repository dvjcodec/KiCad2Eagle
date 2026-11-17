# -*- coding: utf-8 -*-
"""KiCad2Eagle - konwerter projektow KiCad 10 (plaskich) do Eagle 7.5 (.brd + .sch) z weryfikacja."""
import os, sys, json, subprocess
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt5.QtGui import QFont, QDesktopServices, QTextCursor
from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QLineEdit, QPushButton, QCheckBox, QPlainTextEdit,
                             QFileDialog, QGridLayout, QHBoxLayout, QVBoxLayout, QGroupBox, QMessageBox)

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(APP, "konwerter"))
from runner import Runner, find_kicad_bin, find_eagle_bin  # noqa: E402

CFG = os.path.join(APP, "ustawienia.json")

STYLE = """
QWidget { background: #1e1f22; color: #dcdfe4; font-family: 'Segoe UI'; font-size: 10pt; }
QGroupBox { border: 1px solid #3a3d42; border-radius: 6px; margin-top: 10px; padding: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #8ab4f8; }
QLineEdit { background: #2b2d31; border: 1px solid #3a3d42; border-radius: 4px; padding: 4px; selection-background-color: #3d5a80; }
QLineEdit:focus { border-color: #5b8def; }
QPushButton { background: #2f3238; border: 1px solid #44484f; border-radius: 4px; padding: 5px 12px; }
QPushButton:hover { background: #3a3e45; }
QPushButton:disabled { color: #6b6f76; }
QPushButton#start { background: #2d6a3e; border-color: #3c8a52; font-weight: bold; padding: 7px 22px; }
QPushButton#start:hover { background: #357a48; }
QPushButton#stop { background: #7a2e2e; border-color: #9a3a3a; }
QPushButton#stop:disabled, QPushButton#start:disabled { background: #2b2d31; border-color: #3a3d42; color: #6b6f76; }
QCheckBox::indicator { width: 15px; height: 15px; }
QPlainTextEdit { background: #141517; border: 1px solid #3a3d42; border-radius: 4px; font-family: Consolas; font-size: 9pt; }
QLabel#status { font-weight: bold; padding: 4px; }
"""


class Worker(QThread):
    line = pyqtSignal(str)
    done = pyqtSignal(bool)

    def __init__(self, kw):
        super().__init__()
        self.runner = Runner(log=self.line.emit, **kw)

    def run(self):
        try:
            ok = self.runner.execute()
        except Exception as e:  # nie zostawiac GUI w stanie "pracuje"
            import traceback
            self.line.emit("BLAD: %s\n%s" % (e, traceback.format_exc()))
            ok = False
        self.done.emit(ok)


class Okno(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KiCad2Eagle – KiCad 10 → Eagle 7.5")
        self.resize(900, 680)
        self.worker = None
        cfg = self.load_cfg()

        self.pro = QLineEdit(cfg.get("projekt", ""))
        self.pro.setPlaceholderText("plik .kicad_pro")
        self.pro.textChanged.connect(self.pro_changed)
        self.out = QLineEdit(cfg.get("wynik", ""))
        self.out.setPlaceholderText("domyślnie <folder projektu>\\eagle75")
        self.kbin = QLineEdit(cfg.get("kicad_bin") or find_kicad_bin())
        self.ebin = QLineEdit(cfg.get("eagle_bin") or find_eagle_bin())
        self.out_auto = not cfg.get("wynik")
        self.out.textEdited.connect(lambda _: setattr(self, "out_auto", not self.out.text().strip()))

        g = QGridLayout()
        for r, (lab, ed, fn) in enumerate((("Projekt KiCad:", self.pro, self.pick_pro), ("Folder wyniku:", self.out, self.pick_out))):
            b = QPushButton("…"); b.setFixedWidth(36); b.clicked.connect(fn)
            g.addWidget(QLabel(lab), r, 0); g.addWidget(ed, r, 1); g.addWidget(b, r, 2)
        box_p = QGroupBox("Projekt"); box_p.setLayout(g)

        self.c_brd = QCheckBox("Płytka (.brd)"); self.c_sch = QCheckBox("Schemat (.sch)")
        self.c_ver = QCheckBox("Weryfikacja (import do KiCada, porównanie, netlista)")
        self.c_eag = QCheckBox("Test w Eagle (eaglecon)")
        for c, k in ((self.c_brd, "plytka"), (self.c_sch, "schemat"), (self.c_ver, "weryfikacja"), (self.c_eag, "eagle")):
            c.setChecked(cfg.get(k, True))
        h = QHBoxLayout()
        for c in (self.c_brd, self.c_sch, self.c_ver, self.c_eag):
            h.addWidget(c)
        h.addStretch()
        box_o = QGroupBox("Co zrobić"); box_o.setLayout(h)

        g2 = QGridLayout()
        for r, (lab, ed, fn) in enumerate((("KiCad bin:", self.kbin, lambda: self.pick_dir(self.kbin)),
                                           ("Eagle bin:", self.ebin, lambda: self.pick_dir(self.ebin)))):
            b = QPushButton("…"); b.setFixedWidth(36); b.clicked.connect(fn)
            g2.addWidget(QLabel(lab), r, 0); g2.addWidget(ed, r, 1); g2.addWidget(b, r, 2)
        bd = QPushButton("Wykryj"); bd.clicked.connect(self.detect)
        g2.addWidget(bd, 0, 3, 2, 1)
        box_t = QGroupBox("Programy"); box_t.setLayout(g2)

        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(20000)
        self.status = QLabel(""); self.status.setObjectName("status")
        self.b_start = QPushButton("Konwertuj"); self.b_start.setObjectName("start"); self.b_start.clicked.connect(self.start)
        self.b_stop = QPushButton("Przerwij"); self.b_stop.setObjectName("stop"); self.b_stop.setEnabled(False); self.b_stop.clicked.connect(self.cancel)
        self.b_open = QPushButton("Otwórz folder wyniku"); self.b_open.clicked.connect(self.open_out)
        self.b_rap = QPushButton("Otwórz raport"); self.b_rap.clicked.connect(self.open_rap)
        hb = QHBoxLayout()
        hb.addWidget(self.b_start); hb.addWidget(self.b_stop); hb.addWidget(self.status, 1); hb.addWidget(self.b_rap); hb.addWidget(self.b_open)

        v = QVBoxLayout(self)
        v.addWidget(box_p); v.addWidget(box_o); v.addWidget(box_t); v.addLayout(hb); v.addWidget(self.log, 1)
        self.pro_changed()
        self.msg("Projekt KiCad 10, płaski (bez arkuszy hierarchicznych). Pliki KiCada są tylko czytane – praca na kopiach w folderze tymczasowym.\n"
                 "Wynik: <nazwa>.brd, <nazwa>.sch, RAPORT_KONWERSJI.md i kopia źródeł w zrodlo\\.")

    # ---------------------------------------------------------------- ustawienia
    def load_cfg(self):
        try:
            return json.load(open(CFG, encoding="utf-8"))
        except Exception:
            return {}

    def save_cfg(self):
        d = {"projekt": self.pro.text(), "wynik": "" if self.out_auto else self.out.text(),
             "kicad_bin": self.kbin.text(), "eagle_bin": self.ebin.text(),
             "plytka": self.c_brd.isChecked(), "schemat": self.c_sch.isChecked(),
             "weryfikacja": self.c_ver.isChecked(), "eagle": self.c_eag.isChecked()}
        try:
            json.dump(d, open(CFG, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        except Exception:
            pass

    # ---------------------------------------------------------------- akcje
    def pro_changed(self):
        p = self.pro.text().strip()
        if self.out_auto and p:
            self.out.blockSignals(True)
            self.out.setText(os.path.join(os.path.dirname(p), "eagle75"))
            self.out.blockSignals(False)

    def pick_pro(self):
        start = os.path.dirname(self.pro.text()) if self.pro.text() else ""
        p, _ = QFileDialog.getOpenFileName(self, "Projekt KiCad", start, "Projekt KiCad (*.kicad_pro)")
        if p:
            self.pro.setText(os.path.normpath(p))

    def pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Folder wyniku", self.out.text() or os.path.dirname(self.pro.text()))
        if d:
            self.out_auto = False
            self.out.setText(os.path.normpath(d))

    def pick_dir(self, ed):
        d = QFileDialog.getExistingDirectory(self, "Folder bin", ed.text())
        if d:
            ed.setText(os.path.normpath(d))

    def detect(self):
        self.kbin.setText(find_kicad_bin()); self.ebin.setText(find_eagle_bin())
        self.msg("KiCad: %s\nEagle: %s" % (self.kbin.text() or "nie znaleziono", self.ebin.text() or "nie znaleziono"))

    def msg(self, s):
        self.log.appendPlainText(s)
        self.log.moveCursor(QTextCursor.End)

    def start(self):
        pro = self.pro.text().strip()
        if not pro.lower().endswith(".kicad_pro") or not os.path.exists(pro):
            QMessageBox.warning(self, "KiCad2Eagle", "Wskaż istniejący plik .kicad_pro."); return
        if not (self.c_brd.isChecked() or self.c_sch.isChecked()):
            QMessageBox.warning(self, "KiCad2Eagle", "Zaznacz płytkę lub schemat."); return
        if not os.path.exists(os.path.join(self.kbin.text(), "kicad-cli.exe")):
            QMessageBox.warning(self, "KiCad2Eagle", "Nie ma kicad-cli.exe w folderze KiCad bin."); return
        out = self.out.text().strip() or os.path.join(os.path.dirname(pro), "eagle75")
        name = os.path.splitext(os.path.basename(pro))[0]
        old = [x for x in (name + ".brd", name + ".sch") if os.path.exists(os.path.join(out, x))]
        if old and QMessageBox.question(self, "KiCad2Eagle", "W folderze wyniku są już:\n%s\n\nNadpisać?" % "\n".join(old)) != QMessageBox.Yes:
            return
        self.save_cfg()
        self.log.clear()
        self.worker = Worker(dict(pro=pro, wynik=out, plytka=self.c_brd.isChecked(), schemat=self.c_sch.isChecked(),
                                  weryfikacja=self.c_ver.isChecked(), eagle=self.c_eag.isChecked(),
                                  kicad_bin=self.kbin.text(), eagle_bin=self.ebin.text()))
        self.worker.line.connect(self.msg)
        self.worker.done.connect(self.finished)
        self.b_start.setEnabled(False); self.b_stop.setEnabled(True)
        self.status.setStyleSheet("color:#e6c07b"); self.status.setText("Pracuję…")
        self.worker.start()

    def cancel(self):
        if self.worker:
            self.worker.runner.cancel()

    def finished(self, ok):
        self.b_start.setEnabled(True); self.b_stop.setEnabled(False)
        stopped = self.worker.runner.stop
        if stopped:
            self.status.setStyleSheet("color:#e6c07b"); self.status.setText("Przerwano")
        elif ok:
            self.status.setStyleSheet("color:#7ec27e"); self.status.setText("Gotowe – bez różnic")
        else:
            self.status.setStyleSheet("color:#e06c75"); self.status.setText("Gotowe z uwagami – patrz raport")

    def out_dir(self):
        return self.out.text().strip() or os.path.join(os.path.dirname(self.pro.text()), "eagle75")

    def open_out(self):
        d = self.out_dir()
        if os.path.isdir(d):
            QDesktopServices.openUrl(QUrl.fromLocalFile(d))
        else:
            QMessageBox.information(self, "KiCad2Eagle", "Folder jeszcze nie istnieje.")

    def open_rap(self):
        p = os.path.join(self.out_dir(), "RAPORT_KONWERSJI.md")
        if os.path.exists(p):
            QDesktopServices.openUrl(QUrl.fromLocalFile(p))
        else:
            QMessageBox.information(self, "KiCad2Eagle", "Raportu jeszcze nie ma.")

    def closeEvent(self, e):
        if self.worker and self.worker.isRunning():
            if QMessageBox.question(self, "KiCad2Eagle", "Konwersja trwa. Przerwać i zamknąć?") != QMessageBox.Yes:
                e.ignore(); return
            self.worker.runner.cancel(); self.worker.wait(10000)
        self.save_cfg()
        e.accept()


if __name__ == "__main__":
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    w = Okno(); w.show()
    sys.exit(app.exec_())
