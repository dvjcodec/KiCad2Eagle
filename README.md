# KiCad2Eagle – konwerter KiCad 10 → Eagle 7.5

Aplikacja z GUI zamienia **płaski** projekt KiCad 10 (jeden arkusz schematu + płytka) na pliki Eagle 7.5:
`<nazwa>.brd` i `<nazwa>.sch`. Sprawdza też wynik: wczytuje płytkę z powrotem do KiCada i porównuje ją z oryginałem, kontroluje schemat i otwiera oba pliki w Eagle.

## Uruchomienie

Dwuklik na **`KiCad2Eagle.bat`**. Bat uruchamia Pythona 3.11 (`pythonw`, bez okna konsoli) z PyQt5.

1. **Projekt KiCad** – wskaż plik `.kicad_pro`. Obok niego muszą być `.kicad_pcb` i/lub `.kicad_sch` o tej samej nazwie, opcjonalnie `.kicad_dru`.
2. **Folder wyniku** – domyślnie `<folder projektu>\eagle75`. Pole wypełnia się samo, dopóki nie wpiszesz własnej ścieżki.
3. **Co zrobić**:
   - Płytka – tworzy `.brd`.
   - Schemat – tworzy `.sch`.
   - Weryfikacja – wczytuje `.brd` do KiCada i porównuje z oryginałem, kontroluje `.sch` i porównuje z netlistą KiCada.
   - Test w Eagle – `eaglecon` wczytuje oba pliki i szuka błędów i ostrzeżeń.
4. **Programy** – foldery `bin` KiCada i Eagle'a. Wykrywają się same (`%LOCALAPPDATA%\Programs\KiCad\*\bin`, `C:\Program Files\KiCad\*\bin`, `C:\EAGLE-*\bin`). Przycisk **Wykryj** szuka ich ponownie.
5. **Konwertuj**. Postęp widać w oknie logu, a **Przerwij** zatrzymuje pracę.

Po zakończeniu przyciski **Otwórz raport** i **Otwórz folder wyniku**.

Ustawienia (ostatni projekt, ścieżki, zaznaczenia) zapisują się w `ustawienia.json` obok aplikacji.

## Co powstaje w folderze wyniku

| Plik | Zawartość |
|---|---|
| `<nazwa>.brd` | płytka Eagle 7.5 (XML) z własną biblioteką `<NAZWA>` wbudowaną w plik |
| `<nazwa>.sch` | schemat Eagle 7.5, spójny z płytką (Eagle otwiera je jako parę) |
| `RAPORT_KONWERSJI.md` | statystyki, ostrzeżenia, reguły DRC przeniesione i pominięte, wyniki kontroli |
| `zrodlo\` | kopia plików KiCada, z których powstał wynik |

**Pliki KiCada są tylko czytane.** Cała praca idzie na kopiach w folderze tymczasowym `%TEMP%\kicad2eagle_*`, kasowanym na końcu. KiCad przy otwieraniu tworzy `.kicad_prl` i `.kicad_dru`, więc nie trzeba niczego sprzątać obok projektu.

## Wymagania

- KiCad 10 – jego Python (`pcbnew`) i `kicad-cli`.
- Python 3.11 z PyQt5 – do GUI.
- Eagle 7.5 – tylko do testu w Eagle (opcjonalnie).

## Co jest przenoszone

**Płytka**
- Warstwy miedzi: F→1, B→16, wewnętrzne kolejno 2, 3… oraz …14, 15.
- Elementy z obrotem i stroną.
- Obudowy: kopia z KiCada, wspólna dla identycznych footprintów.
- Pady, ścieżki, łuki i przelotki (również ślepe i zagrzebane).
- Strefy jako polygony Eagle, z priorytetem, kreskowaniem i odstępami.
- Obszary zabronione → tRestrict / bRestrict / vRestrict. Eagle 7 nie ma zakazu na warstwach wewnętrznych ani zakazu elementów – raport to odnotowuje.
- Kontur płytki.
- Klasy sieci (do 8) z szerokością, wierceniem i odstępem.
- Reguły DRC.
- Reguły `.kicad_dru` typu „odstęp między klasami” → macierz odstępów klas.

**Schemat**
- Symbole, w tym **wieloczęściowe**: każda jednostka to osobna bramka A, B, C…
- Przewody, połączenia, etykiety lokalne i globalne (jako etykiety z odnośnikiem).
- Symbole zasilania → piny Sup o nazwie sieci.
- Teksty i grafika.
- Wszystkie pola części jako atrybuty, np. LCSC, MPN.
- Nazwy sieci z netlisty KiCada.
- Schemat większy niż obszar Eagle'a jest automatycznie przesuwany (Eagle mieści ±2000 mm).

## Ograniczenia (Eagle 7.5 tego nie ma)

- **Schematy hierarchiczne** – konwerter przerywa z komunikatem. Spłaszcz schemat w KiCadzie.
- **Pady THT prostokątne/zaokrąglone, owale o nietypowej proporcji:** pad Eagle + dodatkowa miedź w obudowie.
  - Pady „long” mają w Eagle jedną proporcję na całą płytkę. Bierze się najczęstszą.
  - Otwory owalne → kontur frezowania na warstwie 46 Milling.
- Pierścienie padów na warstwach wewnętrznych liczy Eagle z reguł DRC.
- **Reguły `.kicad_dru`** zależne od elementu, sieci, warstwy lub innych ograniczeń niż clearance – nie są przenoszone. Raport wymienia je z nazwy.
- Maksymalnie 8 klas sieci. Nadmiarowe → Default (ostrzeżenie w raporcie).
- Polygony zapisane jako obrysy – w Eagle kliknij **Ratsnest**, żeby je wylać.
- Schemat:
  - brak znaczników „niepodłączony”, magistral i ramki arkusza;
  - DNP → atrybut `DNP=yes`;
  - teksty pinów czcionką Eagle'a.

## Weryfikacja – co znaczą wyniki

**Porównanie płytki**: `.brd` → `kicad-cli pcb import --format eagle` → porównanie z oryginałem, tolerancja 0,002 mm. Sprawdzane są:
- elementy (pozycja, strona);
- każdy pad (miejsce, sieć, warstwa SMD, wiercenie);
- wszystkie ścieżki i przelotki (sieć, warstwa, szerokość, końce);
- strefy (obrys, sieć, warstwa);
- sieci i kontur.

„Zero różnic połączeń” dopuszcza tylko różnice rozmiaru padów z listy ograniczeń. Dodatkową miedź w obudowie importer KiCada liczy osobno.

**Kontrola schematu** sprawdza:
- czy każdy pin leży na przewodzie swojej sieci (geometria według reguł Eagle'a);
- czy sieci schematu = sygnały płytki (te same pady), tak jak sprawdza to Eagle;
- czy każdy pin z netlisty KiCada jest w sieci o tej samej nazwie.

**Test w Eagle**: `eaglecon` wczytuje `.brd` (eksport wierceń) i `.sch` (wydruk HPGL). Każde „Error / Warning / unknown” w jego odpowiedzi to błąd.

## Wiersz poleceń

```
python konwerter\runner.py projekt.kicad_pro [--wynik FOLDER] [--bez-plytki] [--bez-schematu] [--bez-weryfikacji] [--bez-eagle] [--kicad-bin DIR] [--eagle-bin DIR]
```

## Pliki aplikacji

| Plik | Rola |
|---|---|
| `KiCad2Eagle.bat`, `KiCad2Eagle.pyw` | start i GUI (PyQt5) |
| `konwerter\runner.py` | przebieg: kopie, konwersja, weryfikacja, raport |
| `konwerter\brd.py` | `.kicad_pcb` → `.brd` (Python KiCada) |
| `konwerter\sch.py` | `.kicad_sch` → `.sch` |
| `konwerter\porownaj_brd.py` | kontrola płytki |
| `konwerter\sprawdz_sch.py` | kontrola schematu |
| `konwerter\common.py`, `sexp.py` | wspólne funkcje, parser S-wyrażeń KiCada |
| `szablony\` | bloki warstw Eagle i domyślne reguły DRC |

Testowane na 4-warstwowych projektach (518 i 192 elementy, część wieloczęściowa): zero różnic połączeń, Eagle 7.5 wczytuje wynik bez błędów i ostrzeżeń.
