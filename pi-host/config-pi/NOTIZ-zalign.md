# NOTIZ A10 – Dual-Z-Lichtschranken (Stock `[z_align]`) vs. `[z_tilt]`

> **ENTWURF – Stand 17.10.2026, am Gerät UNGETESTET.** Nur offline (Upstream-Klipper 9cb9975, Batch + Offline-Tests, Firmware-Dekompilat). Alles mit **ANNAHME** ist nicht belegt. Zugehörig: `zalign.cfg`, `../k2_zalign.py`, `../test_k2_zalign.py`, `arbeitsdateien-lokal/upstream-test-a10/` (lokal).

## 0. Kurzfazit

1. **Stock-z_align** ist eine von der **Main-MCU selbst** gefahrene Abwärtsfahrt beider Z-Motoren auf je eine Gabellichtschranke (PA15 → stepper_z, PA8 → stepper_z1): jede Seite stoppt einzeln, Abbruch bei zu großer Wegdifferenz (`safe_dist`), Freifahren, langsame Feinmessung (`deltaError1`). Danach setzt der Host Z = 360, fährt schnell auf Z 54 und tastet erst dann mit der Düse an (§1).
2. **`[z_tilt]` ersetzt nur die Feinausrichtung** zur Düsenebene (das macht Stock zusätzlich, ebenfalls mit der Düse). Die Lichtschranken liefern dagegen die **reproduzierbare mechanische Referenz** nach jedem Stromlos-Zustand und die Z-max-Lage (§2).
3. **Hauptrisiko ohne Lichtschranken** (§3): Nach jedem Stromlos-Zustand ist die Spindeldifferenz unbekannt. `START_PRINT` lud das gespeicherte Mesh ohne `Z_TILT_ADJUST` → **seit 17.10.2026 behoben** (A10a, §3 Punkt 2: `_PI_ZTILT_WENN_NOETIG`). Im `Z_TILT_ADJUST` selbst gilt: Spindeldifferenz < 6,9 mm sicher, ab 6,9 mm Abbruch „kein Kontakt“ (tiefere Seite), ab **11,5 mm Kollisionsgefahr** (höhere Seite bei der XY-Fahrt auf Z 5).
4. **Ausgeliefert:** `zalign.cfg` Variante **L** (aktiv) = rein lesender Zustandscheck mit Upstream-`[gcode_button]` (kein eigenes Modul). Variante **B** = `[k2_zalign]` (Ausrichtung mit Upstream-Homing-Mechanik, je Schranke ein Endstop für genau einen Z-Stepper, in Etappen mit hartem `safe_dist`), **auskommentiert und zusätzlich gesperrt**.
5. **Offline-Nachweis:** Batch 13 Läufe + 28/28 Prüfungen (u. a. `stepper_stop_on_trigger` Z→trsync(PA15), Z1→trsync(PA8), 148 Etappen, 370 mm, 10,000 mm/s, X/Y 0 Schritte; Erfolgspfad mit Test-Fake inkl. Freifahren und „Z danach nicht referenziert“), `test_k2_zalign.py` 83/83, alle anderen Test-Suiten grün, Gesamtprüfung lauf-F EXIT 0 (§4.4).

## 1. Was Stock-z_align genau macht (Belege)

### 1.1 Konfiguration

`live-logs-2026-10-16/config-aktuell/printer.cfg:386–399`:

| Option | Wert | Bedeutung (Kommentar + Beleg) |
|---|---|---|
| `distance_ratio` | 0.85 | Anteil der schnellen Aufwärtsfahrt nach ZDOWN: 0,85 × 360 (`position_max`, Z. 93) = **306** = `za1:306.0` (Mitschnitt Z. 1651) |
| `quick_speed` | 30 mm/s | Abwärts schnell; MCU-Wert `quickSpeed=5000` (Mitschnitt Z. 1161) = Halbperiode in Ticks: 120 MHz / 5000 = 24 000 Flanken/s = 12 000 Schritte/s = 30 mm/s bei 400 Schritten/mm (rotation_distance 8, 16 µSchritte, Z. 89–90) |
| `slow_speed` | 10 mm/s | `slowSpeed=15000` → 10 mm/s (gleiche Rechnung) |
| `rising_dist` | 10 mm | `risingDist=8000` Flanken = 4000 Schritte = 10 mm |
| `safe_dist` | 40 mm | `safeDist:32000` Flanken = 40 mm – maximale **Wegdifferenz** der beiden Seiten (§1.3) |
| `filter_cnt` | 10 | aufeinanderfolgende ausgelöste Abtastungen, bis eine Seite als „angekommen“ gilt |
| `timeout` | 30 s | je Durchgang (Host, **ANNAHME**: Host-Code `z_align.py` liegt lokal nicht vor) |
| `retries` | 5 | Host-Wiederholungen (`cur_retries:0`, Mitschnitt Z. 1161) |
| `retry_tolerance` | 10 | „10 Schritte à 0,0025 mm“; Einheit gegenüber `deltaError1` (Flanken) **ANNAHME** |
| `endstop_pin_z` | PA15, PA8 | Main-MCU; Zuordnung PA15 ↔ stepper_z, PA8 ↔ stepper_z1 BELEGT durch `config/F008/printer.cfg:367–368` (`endstop_pin_z: PA15`, `endstop_pin_z1: PA8`) |
| `zd_up` | 0 | Dir-Pegel „weg von der Schranke“ = 0 = Z− (Bett hoch); Schranken liegen also **unten (Z-max)** |
| `zes_untrig` | 1 | Pegel 1 = nicht ausgelöst → ausgelöst = 0 |
| `zmax_safe_pox_diff` | 10 | „zmax_safe_pox = position_max − 10“; Verwendung **ANNAHME** (nicht im Mitschnitt) |

Ältere F008-Werte (`config/F008/printer.cfg:359–371`): quick 60, slow 20, rising 20 – Creality hat die Werte später halbiert.

### 1.2 Wann Stock es ausführt

- **Erstes `G28 Z` nach dem Start** automatisch: Mitschnitt `arbeitsdateien-lokal/lokale Analyse-Dateien` Z. 1158 `is_already_zodwn:False zdown_switch_enable:0` → Z. 1161 `send query_z_align …`. Spätere `G28 Z` nur mit `ZDOWN_SWITCH ENABLE=1` (Z. 2938/2941), sonst nicht (Z. 4056 `is_already_zodwn:True zdown_switch_enable:0`, kein `query_z_align`). Wann `is_already_zdown` zurückgesetzt wird: **ANNAHME** (Neustart/Motor aus).
- **G29** `gcode_macro.cfg:1002–1016`, **Z_AXIS_CALIBRATION** `:1210–1223`, **BED_MANUAL_CAL** `:1037–1051`: `ZDOWN_SWITCH ENABLE=1` + `G28 Z` → `Z_TILT_ADJUST` → `NEXT_HOMEZ_NACCU` + `G28 Z`.
- Kommentar `gcode_macro.cfg:999–1001`: nach der Lichtschranken-Nivellierung werden im G29 die **zuletzt gespeicherten Z_TILT-adjustments NICHT wiederhergestellt** (`SET_G29_FLAG VALUE=1`) – d. h. außerhalb von G29 stellt Stock sie nach ZDOWN wieder her. Konzept: Schranken = reproduzierbare mechanische Null, gespeicherte z_tilt-Korrektur darauf. Ausführung im Host-Code **ANNAHME** (Code fehlt; Mitschnitt zeigt nur `ZDOWN G29_flag is True`, Z. 1261).
- `START_PRINT` (Stock `gcode_macro.cfg:494–523`) enthält **kein** `Z_TILT_ADJUST`; die Ausrichtung kommt dort über das erste `G28 Z` (ZDOWN).

### 1.3 MCU-Ablauf (Main-FW `mcu0_140`, Dekompilat)

Dekompilate (Ghidra 12.1.4, Basis 0x08003000, Handler laut `arbeitsdateien-lokal/HANDLER-TABELLE-KORRIGIERT.txt:20–23`) liegen lokal unter `arbeitsdateien-lokal/upstream-test-a10/re/`:

- `config_z_align_add` @0x08006428 (`decomp-zalign.c:12–36`): Kanal `z_indx` < 4; **Step-Pin als Ausgang** (Z. 17), **Dir-Pin als Ausgang** mit aktuellem Pegel (Z. 20–22), **Schranken-Eingang `gpio_in_setup(zes_pin, zes_untrig)`** (Z. 27). Die MCU fährt die Z-Motoren also selbst über dieselben Step/Dir-Pins wie `config_stepper` (PB6/PB5, PB15/PA1).
- `query_z_align` @0x080062b4 (`decomp-zalign.c:348–455`): Speeds/rising = 0 → Shutdown (Z. 372); `enable≠1` → Stopp (Z. 378); Startzustand je Kanal lesen (Z. 395–404): **keine Schranke ausgelöst** → Dir „zur Schranke“ (`!zd_up`, Z. 414), Intervall quick (Z. 391–392); **eine ausgelöst** → zuerst **hoch** (`zd_up`, Z. 430) mit **slow** (Z. 437). Statusmeldung alle 60 000 000 Ticks = 0,5 s (Z. 444; Mitschnitt: `z_align_status` alle 0,5 s, Z. 1164–1256).
- Task @0x080067a8 (`decomp-tasks.c:1–270`), Abwärtsfahrt: je Kanal Eingang lesen; **frei → Step toggeln + Zähler++** (Z. 49–53, `FUN_080043f4` = Pin toggeln, `decomp-helfer.c`); ausgelöst → Filterzähler++, ab `filterCnt` gilt der Kanal als angekommen (Z. 57–60). **`max(Zähler) − min(Zähler) > safeDist` → Status 2 = Fehler, Stopp** (Z. 73–89). Alle angekommen: erster (schneller) Durchgang → hoch (Z. 153); Aufwärts: alle Kanäle `risingDist` hoch (Z. 203–207), danach **muss jede Schranke frei sein, sonst Status 2** (Z. 214–224); langsamer Durchgang → `deltaError = Zähler(Kanal 1) − Zähler(Kanal 0)` (Z. 113), nach dem zweiten Messdurchgang **Status 1 = fertig** (Z. 120–132).
- Mitschnitt: `deltaError1` am Ende 0 bzw. 1 Flanke (Z. 1256, 3186), Dauer 7,7 s bzw. 17,7 s (Z. 1259, 3189).

### 1.4 Polarität und Pull-up (wichtig für Upstream)

- GPIO-Kern @0x08004660 (`decomp-zalign.c:107–190`): Eingang mit `pull==1` → Pull-**up** (BOP, Z. 139–141), `pull==0` → Pull-**down** (BC, Z. 135–137), sonst floating (Z. 143–145). **Abweichung zu Upstream** (`src/stm32/stm32f1.c:209–245`: 0 = floating).
- Stock nutzt `zes_untrig=1` als Pull-Argument → **Pull-up**. `config_endstop` (@0x08005bf0, `decomp-zalign.c:913–923`) und `buttons_add` (@0x080048a0, `decomp-buttons.c`) rufen dieselbe Funktion mit `pull_up` aus dem Befehl → Upstream-`^` = Pull-up wie Stock. Daher **`^!PA15` / `^!PA8`** (ausgelöst = Pegel 0). Ohne `^` wäre der Eingang in dieser FW auf Pull-down (Nebenbefund; betrifft Stock und Pi gleichermaßen bei PB11/PB12/PC7, dort gleiche Bytes wie Stock).

### 1.5 Host-Teil nach ZDOWN (Mitschnitt)

`ZDOWN G29_flag is True, set zmax:[175.0, 175.0, 360.0 …]` (Z. 1261) → `G1 F1800 X175 Y175 Z54` (Z. 1263–1264, 30 mm/s) → `G28 Z` mit der Düse → `za1:306.0 za2:46.2425 z_max:352.2425` (Z. 1651–1654); zweiter Lauf 352,1625 (Z. 3586–3589) → **Lichtschranke ≈ 352,2 mm unter Düse/Bett-Kontakt, Streuung 0,08 mm** (inkl. Probe). Danach `Z_TILT_ADJUST`: `probe at 5,175 z=-0.007`, `345,175 z=0.058` (Z. 3784–3895), Korrektur `stepper_z = -0.010823`, `stepper_z1 = 0.063743` (Z. 3901–3903), **range 0,065 ≤ 0,1, Retries 0/10** (Z. 3905) → Referenzwert „mit Lichtschranken“.

**Korrektur Fremdbefund:** `arbeitsdateien-lokal/lokale Analyse-Dateien (lokal).md:57` nennt `Z_TILT_ADJUST` „optisch (Lichtschranken)“ – falsch: die Tastungen 8–9 sind Düsen-Tastungen (`probe at …`, `probe:start_probe … Z_TILT_ADJUST`, Mitschnitt Z. 3690–3895). Optisch ist nur `ZDOWN`.

## 2. Was unser `[z_tilt]` abdeckt

- `pi-host/config-pi/printer.cfg:422–432` = Stock `printer.cfg:106–117` 1:1 (z_positions −15/375, points 5/345 bei Y175, speed 300, horizontal_move_z 5, retries 10, retry_tolerance 0,1).
- Upstream `z_tilt.py`: Messung, Ausgleichsgerade, Korrektur Stepper für Stepper (Z. 29–67, mit `lift_speed` = Probe-`lift_speed` 5 mm/s, `probe.py:493`, printer.cfg:58), Wiederholung bis Toleranz (Z. 109–125), **Abbruch wenn der Fehler zweimal steigt** (Z. 102–108/117–119). **Keine Korrekturgrenze** (`max_adjust` gibt es nur bei `quad_gantry_level`). `applied` wird nur bei `stepper_enable:motor_off` zurückgesetzt (Z. 69–83).
- **Deckt ab:** Feinausrichtung zur Düsenebene (Stock macht genau das zusätzlich, §1.5).
- **Deckt nicht ab:** (a) reproduzierbare Lage nach Stromlos ohne Düse, (b) Z-max-Referenz (Stock fährt danach 306 mm schnell), (c) Schutz bei großer Verkippung vor dem ersten Antasten.
- `[stepper_z1] endstop_pin` (Upstream-Mehrfach-Endstop) ist **keine** Alternative: Upstream erlaubt kein Homing mit Mischung aus `probe:z_virtual_endstop` und normalen Endstops (`klippy/extras/homing.py:286–297`); Z-max-Homing auf die Schranken würde die Z-Null an die Schranken statt an die Düse koppeln.

## 3. Risiko ohne Lichtschranken

Geometrie: Spindeln X −15 / 375 (Abstand 390 mm), Messpunkte X 5 / 345, Antastmitte X 175. Bett als Gerade durch die Spindelhöhen: Randhöhe relativ zur Antastmitte = **170/390 = 0,436 × ΔSpindel**, Höhenunterschied der beiden Messpunkte = 340/390 = 0,872 × ΔSpindel (Δ = Höhenunterschied der Spindeln).

1. **Stromlos-Zustand** (Ausschalten, Not-Aus/Shutdown, `FIRMWARE_RESTART`, `M84`): Enable `!PA9` aus → **ANNAHME**: die Closed-loop-Motoren halten nicht, das Bett (Spindelsteigung 8 mm) kann einseitig absinken → Δ unbekannt. `[idle_timeout] 99999999` (printer.cfg) schaltet im Betrieb nie ab – die Lage hält, solange die Motoren bestromt sind.
2. **Druckstart:** `START_PRINT` (macros.cfg:347–383) = G28 → Reinigung → G28 Z → **gespeichertes Mesh laden** (Z. 370) – **ohne `Z_TILT_ADJUST`**. Nach einem Stromlos-Zustand ist die Verkippung beim Druck dann die zufällige, das Mesh (bei G29 nach z_tilt gemessen) passt nicht → Erstschichtfehler, bei Δ ≳ 0,3–0,5 mm Düse schleift auf der hohen Seite (Schätzung, **ANNAHME**). Stock vermeidet das über ZDOWN beim ersten G28 Z (§1.2). → **UMGESETZT 17.10.2026 (A10a):** `START_PRINT` ruft nach der Reinigung und vor dem maßgeblichen `G28 Z` das Makro `_PI_ZTILT_WENN_NOETIG` (macros.cfg) → `Z_TILT_ADJUST`, solange `printer.z_tilt.applied` False ist (Upstream setzt es bei Klipper-Start/`motor_off` zurück, `z_tilt.py:71–82`). Eigenes Makro, damit `applied` erst nach dem Homing gelesen wird. Nachweis: a45-Batch `lauf-start`/`lauf-sd` (Z_TILT-Stub genau einmal aufgerufen, Bahnprüfung 0 Fehler, render 0 Fehler).
3. **Erste Antastung `G28 Z`** (homing.cfg:186–196): in Bettmitte → Verkippung ändert die Mitte nicht. Starthöhe unbekannt → bis 1,5 × 360 = 540 mm Fahrweg mit 5 mm/s (`homing.py` forcepos, k2_prtouch `compute_probe_target_z`, k2_prtouch.py:1681–1691). Die XY-Fahrt zur Mitte (homing.cfg:193) erfolgt bei unbekannter Bett-Höhe – gleiches Risiko wie Stock (dort ZDOWN erst beim Z-Teil).
4. **`Z_TILT_ADJUST` nach `G28 Z`** (Mitte = 0, danach Z 5): Z auf `horizontal_move_z` 5, dann XY zum Punkt (`probe.py:458–476`), Antasten höchstens `probe_max_travel_mm` 8 mm (printer.cfg:68, k2_prtouch.py:1691) → Fenster am Rand **[−3 mm, +5 mm]**:
   - tiefere Seite unter −3 mm → **Abbruch „kein Kontakt“** (sicher) ab **Δ ≈ 6,9 mm**;
   - höhere Seite über +5 mm → **Düse fährt bei der XY-Fahrt ins Bett** ab **Δ ≈ 11,5 mm** (erster Punkt ist X 5: links höher → Kollision schon beim ersten Punkt).
5. **Korrekturrichtung:** Ist die Zuordnung `z_positions` ↔ stepper_z/stepper_z1 vertauscht (**ANNAHME**: stepper_z = links), korrigiert z_tilt in die falsche Richtung; Upstream bricht erst nach zwei Anstiegen ab („Retries aborting … increasing“, z_tilt.py:117–119) – die Verkippung ist dann ein Mehrfaches der Ausgangslage. Test T5 prüft das zuerst mit kleiner Verkippung.
6. **Mit Lichtschranken** (Stock) ist die Rest-Verkippung vor z_tilt 0,065 mm (§1.5) – weit innerhalb aller Grenzen.

## 4. Ersatz mit Upstream-Mitteln: `[k2_zalign]` (Variante B, gesperrt)

### 4.1 Prinzip

- Je Schranke ein **normaler Upstream-Endstop** (`config_endstop` + `trsync`, Dict-IDs 31–37 vorhanden; MCU-Befehle Formate = Upstream, Batch fehlerfrei), an jedem **genau ein** Z-Stepper (`MCU_endstop.add_stepper`, `klippy/mcu.py:345–411`).
- Fahrt über **`PrinterHoming.manual_home`** (`klippy/extras/homing.py:307–319`, wie `MANUAL_STEPPER … STOP_ON_ENDSTOP`) mit beiden Endstops in einem `HomingMove` (`homing.py:45–154`): jede Seite stoppt in der MCU einzeln (`stepper_stop_on_trigger`), die andere fährt weiter.
- **Etappen** (`chunk_dist` ≤ `safe_dist`): nach jeder Etappe Weg je Seite aus `get_mcu_position()` (MCU-Position nach dem Stopp, `klippy/stepper.py:158–182`) und Pegel aus `query_endstop()` (= `QUERY_ENDSTOPS`). Dadurch gilt `safe_dist` (Wegdifferenz nach dem ersten Auslösen, wie Stock §1.3) auch bei toter Schranke **hart**.
- Z wird zu Beginn nur für Z „referenziert“ (`toolhead.set_position(…, homing_axes="z")`, `klippy/toolhead.py:383–390`), jede Fahrt relativ von `axis_minimum` (abwärts) bzw. `axis_maximum` (Freifahren) aus; am Ende/Abbruch `clear_homing_state("z")` wie `SET_KINEMATIC_POSITION CLEAR=Z` (`klippy/extras/force_move.py:118–138`). Danach `G28 Z` mit der Düse.
- Lesend: `QUERY_ENDSTOPS` zeigt `zalign_z`/`zalign_z1` (auch Mainsail-Endstop-Panel), `KTWO_ZALIGN_STATUS`.

### 4.2 Ablauf `KTWO_ZALIGN` und Grenzen

| Phase | Was | Abbruch |
|---|---|---|
| 0 | Sperren: `allow_move`, `_PI_FREIGABE allow_move` (nur echtes True), Motor-Init (`k2_motor:ready`), kein Druck aktiv | jede Sperre → Fehler **ohne Bewegung** |
| 1 | Startzustand: beide frei | eine ausgelöst → Fehler **ohne Bewegung**; nur `LIFT=1` (Bett sichtbar unten) → `rising_dist` langsam hoch, dann müssen beide frei sein |
| A | abwärts `quick_speed`, Etappen | max_travel ohne Auslösen; nur eine Seite und Wegdifferenz ≥ safe_dist; Seite gestoppt aber Schranke frei (Prellen); Schranke wieder frei; Timeout |
| B | `rising_dist` hoch (`quick_speed`) | danach eine Schranke noch ausgelöst |
| C | abwärts `slow_speed`, Budget `rising_dist + slow_extra_dist`; Delta = Weg z1 − Weg z | wie A; Delta > `retry_tolerance` nach `retries`+1 Versuchen |
| Ende | `zmax_position: 0` → Z **nicht** referenziert; > 0 → Z = Wert | – |

| Grenze | Stock | Ersttest (zalign.cfg) | Modul-Default | hart (Modul) |
|---|---|---|---|---|
| quick_speed mm/s | 30 | 10 | 10 | ≤ 30 |
| slow_speed mm/s | 10 | 5 | 5 | ≤ 10, ≤ quick |
| rising_dist mm | 10 | 5 | 10 | ≤ 20 |
| safe_dist mm | 40 | 5 | 10 | ≤ 40 |
| chunk_dist mm | – | 2,5 | 5 | ≤ safe_dist |
| max_travel mm | – | 370 | 370 | ≤ 400 und Z-Bereich |
| retries / Toleranz | 5 / 10 Schr. | 3 / 0,025 mm | 3 / 0,025 | ≤ 5 / ≤ 0,5 |
| timeout s | 30 je Durchgang | 300 gesamt | 150 | ≤ 300 |

Unterschiede zu Stock: Etappen statt kontinuierlich (Halt alle 2,5 mm), Endstop-Filter Upstream 4 Abtastungen à 15 µs (`homing.py:8–10`) statt `filter_cnt` 10 – Prellen wird nicht weggefiltert, sondern **erkannt und abgebrochen**; ein Messdurchgang C statt zwei; Z danach nicht referenziert (Stock: Z = 360).

### 4.3 Nicht gewählte Wege

- Creality-Befehle `config_z_align*`/`query_z_align` weiter nutzen: kein Upstream-Weg, MCU fährt an Klippers Schrittplanung vorbei (gleiche Step-Pins, Positionsverlust für Klipper) → verworfen.
- `[stepper_z1] endstop_pin` / Z-max-Homing: §2.

### 4.4 Offline-Nachweis

- `arbeitsdateien-lokal/upstream-test-a10/` (lokal): `lauf-a10.sh` 13 Läufe, `pruefe-a10.py` **28/28**: L EXIT 0 (`buttons_add pin=PA15/PA8 pull_up=1`, `buttons_query invert=3`, keine Endstops darauf, `QUERY_BUTTON` → RELEASED); B EXIT 0 (`config_endstop pin=PA15/PA8 pull_up=1`, `QUERY_ENDSTOPS` kennt `zalign_z`/`zalign_z1`, Status/Plan, keine Z-Schritte); gesperrt / Freigabe fehlt / Motor-Init fehlt → Fehler ohne Z-Schritt; **B-frei** (Batch: Endstops antworten nicht) → Abbruch „keine Schranke innerhalb 370 mm“, **Z nur über trsync(PA15), Z1 nur über trsync(PA8)**, 148 Etappen, je 148 000 Schritte = 370 mm abwärts, X/Y 0 Schritte, max. 10,000 mm/s, kein Internal error; L+B gleichzeitig → `pin PA15 used multiple times in config`. **Mit Test-Fake** (nur Batch-Kopie: Schranke „ausgelöst“ ab fester MCU-Position, sonst echter Upstream-Code): Erfolgspfad A 100 mm (40 Etappen) → B 5 mm hoch (`toolhead.move`) → C 5 mm → „KTWO_ZALIGN fertig“, danach `G1 Z10` → „Must home axis first“ (Z wirklich nicht referenziert); Start ausgelöst → Abbruch ohne Bewegung, mit `LIFT=1` → 5 mm hoch mit 5 mm/s, dann ok.
- `pi-host/test_k2_zalign.py` **83/83** (Simulation: Normalfall, Startverkippung, safe_dist-Invariante über 20 Kombinationen, tote Schranken, LIFT, Polarität falsch, Prellen, instabiles Signal, Klemmen nach Freifahren, Retries, Timeout, zmax_position; Klipper-Hülle mit echtem Upstream-`ConfigWrapper`/`gcode`).
- Gesamtprüfung (Kopie von `upstream-test/lauf-modulfix.sh` mit eigenem Zielordner `upstream-test-a10/gesamt/`): lauf-F **EXIT 0** (`allocate_oids` 38 statt 37 = ein `buttons`-Objekt), übrige Läufe gleiches Fehlerbild wie zuvor, kein Internal error.

## 5. Testprotokoll am Gerät (A10)

**Grundregeln:** Bett leer, Hand am Not-Aus, nach jedem Schritt `klippy.log` prüfen. Jeder Schritt mit Bewegung nur mit Einzelfreigabe. Abbruch → STOP, nichts „nachjustieren“. Werte in die Tabelle §5.9.

### T0 Voraussetzung
README §4 Schritt 1 (MCU-Verbindung, ohne Bewegung) bestanden. `zalign.cfg` wie ausgeliefert (Variante L).

### T1 Lesen ohne Bewegung (Variante L)
1. `ZALIGN_STATUS` → beide `RELEASED` (sofern das Bett nicht ganz unten steht; Lage per Sicht notieren).
2. Schranke links mit Papier/Karte unterbrechen (falls zugänglich, **ANNAHME** Gabel erreichbar) → Meldung `zalign_z … AUSGELÖST` erwartet; rechts → `zalign_z1`. **Zuordnung links/rechts notieren** (stepper_z = links ist ANNAHME).
3. Bett steht sichtbar ganz unten → mindestens eine `PRESSED` erwartet.

Abbruch: dauernd `PRESSED` bei freier Gabel (Polarität/Pull-up falsch, Pegel an PA15/PA8 messen) · keine Reaktion (Kabel) · Zuordnung vertauscht (dann `pin_z`/`pin_z1` tauschen, NICHT weiter).

### T2 Vorbereitung Bewegung
README §4 Schritte 2–7 (Motor-Init, X/Y-Homing, Probe) bestanden.

### T3 Referenz OHNE Lichtschranken (z_tilt)
1. Kaltstart: Drucker ≥ 10 min aus, einschalten, `ZALIGN_STATUS` (Bettlage notieren).
2. Freigabe, `G28` (X/Y, dann Z in der Mitte).
3. **Kollisions-Vorprüfung:** `G1 Z15 F600`, `G1 X5 Y175 F3000` → Abstand Düse–Bett messen (Fühlerlehre/Lineal, d_links), `G1 X345 Y175` → d_rechts. **|d_links − d_rechts| > 4 mm → STOP** (= Δ > ~4,6 mm, Rand ±2 mm um die Mitte; Kontaktfenster −3/+5 mm §3.4 mit Reserve) – dann erst Variante B oder Rücksprache.
4. `G1 Z5`, `Z_TILT_ADJUST` → notieren: beide `probe at`-Werte der ersten Runde, `Making the following Z adjustments` (beide Stepper), je Runde `Retries n/10 … range`.
5. `Z_TILT_ADJUST` erneut (erwartet range < 0,1 in Runde 0, Korrekturen ≈ 0).
6. 30 min stehen lassen (Motoren an), `G28 Z`, `Z_TILT_ADJUST` → Drift notieren.
7. Stromlos-Wirkung: `M84`, 5 min, Bett per Sicht (abgesunken? einseitig?), dann 2.–5. wiederholen. Gesamt **≥ 3 Kaltstart-Zyklen**.

Abbruch: „kein Kontakt innerhalb probe_max_travel“ (Δ > ~6,9 mm) · „Retries aborting … increasing“ (Zuordnung/Richtung falsch, §3.5) · „Too many retries“ · eine Korrekturrunde > 3 mm · Geräusch/Blockade.

### T4 Variante B lesend
1. `cp pi-host/k2_zalign.py ~/klipper/klippy/extras/`; in `zalign.cfg` Block L (zwei `[gcode_button …]`) auskommentieren, Block B einkommentieren (nur die `#` vor den Optionszeilen entfernen, `allow_move: False` lassen); `FIRMWARE_RESTART`.
2. `QUERY_ENDSTOPS` → `zalign_z:open zalign_z1:open`; `KTWO_ZALIGN_STATUS` → GESPERRT + Gründe; `KTWO_ZALIGN DRY_RUN=1` → Plan; `KTWO_ZALIGN` → „gesperrt … Keine Bewegung.“

Abbruch: Config-Fehler (`pin … used multiple times` = Block L noch aktiv) · `TRIGGERED` bei freier Gabel.

### T5 Erste Ausrichtung (Einzelfreigabe)
1. Ausgangslage: Motor-Init fertig, `G28`, `G1 Z30` (Bett ~30 mm unter der Düse), Z_TILT aus T3 angewendet (kleine Verkippung).
2. `[k2_zalign] allow_move: True`, `FIRMWARE_RESTART`, Motor-Init, `SET_GCODE_VARIABLE MACRO=_PI_FREIGABE VARIABLE=allow_move VALUE=True`.
3. `KTWO_ZALIGN` – beobachten: beide Seiten fahren mit 10 mm/s **nach unten**, jede stoppt an ihrer Schranke, 5 mm hoch, langsam runter. Meldungen notieren (Phase A: Weg/Differenz z1−z, Phase C: Delta, Dauer).
4. Sofort Not-Aus bei: Bewegung **nach oben** in Phase A · eine Seite fährt nach dem Stopp der anderen sichtbar > 5 mm weiter (Modul müsste vorher abbrechen) · Verkanten/Geräusch · Fahne läuft auf Anschlag.

Erwartung: Phase-A-Differenz ≈ Verkippung zur Schrankenebene (nach z_tilt < 1 mm, **ANNAHME**), Delta ≤ 0,025 mm (Stock `deltaError1` 0–1 Flanke).

### T6 Z-max messen
Nach T5 (Bett an den Schranken, Z nicht referenziert): `GET_POSITION` (Zeile `mcu: … stepper_z:<A>`), `G28 Z` (tastet von ganz unten, ~70 s), `GET_POSITION` (`stepper_z:<B>`) → **zmax = (A − B) × 0,0025 mm** (Vorzeichen prüfen). Vergleich Stock 352,24 / 352,16 (§1.5). 3× wiederholen → Streuung. Erst danach über `zmax_position` (konservativ, z. B. Messwert − 5 mm) entscheiden – Achtung: mit referenziertem Z überspringt `G28 Z` die Reinigung (homing.cfg:175).

### T7 Vergleich MIT Lichtschranken-Vorausrichtung
Wie T3 (≥ 3 Kaltstarts, `M84`-Zyklen), aber `G28 X Y` → `ZALIGN_STOCKART` (= `KTWO_ZALIGN` → `G28 Z` → `Z_TILT_ADJUST` → `G28 Z`). Notieren wie T3 + Phase-A-Differenz. Erwartet: erste Z_TILT-Runde range ≈ 0,065 mm (Stock), Phase-A-Differenz je Kaltstart = Absinken nach Stromlos.

### T8 Auswertung / Entscheidung
- „Dual-Z ohne Lichtschranken stabil“, wenn in allen ≥ 3 Kaltstarts: Δ aus der ersten Z_TILT-Runde (|z345 − z5| × 390/340) < 3 mm, Konvergenz in ≤ 3 Runden, keine Abbrüche, Drift nach 30 min < 0,05 mm. Dann reicht z_tilt – aber nur, wenn `START_PRINT` nach Stromlos z_tilt ausführt (§3.2).
- Sonst: `KTWO_ZALIGN` vor `Z_TILT_ADJUST` beim ersten Homing nach Stromlos (Stock-Konzept) – Einbau in macros.cfg/homing.cfg erst nach Abstimmung.

### 5.9 Protokollvorlage

| Nr | Datum/Zeit | Variante | Kaltstart/M84? | Bettlage vorher (Sicht/ZALIGN_STATUS) | d_links/d_rechts (T3.3) | Phase-A z1−z [mm] | Delta C [mm] | ZALIGN Dauer [s] | z_tilt R0 z5 / z345 | Korrektur z / z1 | Runden bis ≤0,1 | Drift 30 min | zmax (T6) | Abbruch/Bemerkung |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

## 6. Offene Punkte / ANNAHMEN

1. Gabel der Lichtschranken von Hand erreichbar (T1.2); physische Seite von stepper_z (links).
2. Absinken des Betts bei stromlosen Closed-loop-Motoren (T3.7/T7 messen).
3. Stock-Host-Logik (`z_align.py`/`.so` fehlt lokal): Retry-Regel, Einheit `retry_tolerance`, Rücksetzen von `is_already_zdown`, Wiederherstellen der z_tilt-adjustments nach ZDOWN.
4. Fahnenlänge unter der Schranke (wie weit eine Seite nach dem Auslösen mechanisch weiterfahren darf) – `safe_dist` Ersttest 5 mm.
5. Upstream-Endstop-Filter (4 × 15 µs) ausreichend für die Gabellichtschranken (Stock filtert 10 Task-Durchläufe).
6. `zmax_position` erst nach T6.
