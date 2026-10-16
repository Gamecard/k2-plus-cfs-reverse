# NOTIZ: Schnittpositions-Kalibrierung auf dem Pi (16.10.2026 Nacht)

> ENTWURF – am Gerät UNGETESTET. Betroffene Dateien: `pi-host/k2_motor_init.py`
> (`KTWO_MOTOR_CUT_CAL`), `config-pi/cutcal.cfg` (Makro `CALIBRATE_CUT_POS`,
> per `[include cutcal.cfg]` einbinden), `pi-host/test_k2_motor.py`,
> `arbeitsdateien-lokal/upstream-test-cutcal/`. Kurzfassung: `pi-host/README.md` §10.

## 1. Was Stock macht (Belege)

**Makro** (`live-logs-2026-10-16/config-aktuell/gcode_macro.cfg:790–800`):
`G28` (falls nicht `xyz`), `SET_LIMITS`, `MOTOR_SYS_PARAM NUM=1 DATA=1 ID=70
PARAMS=300 PARAMS_TYPE=float`, `MOTOR_CHECK_CUT_POS`, `M400`, `… PARAMS=100`,
`RESTORE_LIMITS`. `SET_LIMITS`/`RESTORE_LIMITS` stecken in `box_wrapper.so`
(nur Strings, Semantik unbelegt).

**Parameter:** `box.cfg` `pre_cut_cal_pos_x: -5` („Vorposition, spart Zeit“),
`check_cut_pos_x_min/max: -9.5/-5.5`, `cut_pos_y: 200`; `motor_control.cfg`
`cut_pos_offset: 0.4`, `motor_x_stall: PB11`.

**Live-Mitschnitt** einer Display-Kalibrierung, 16.10.2026 22:03:17–22:04:24
(Log-Uhr), `arbeitsdateien-lokal/cutpos-live-20261016-1601.log` (lokal):

| Zeile | Zeit | Request (`retries = 0, cmd = …`) | Antwort | Bedeutung |
|---|---|---|---|---|
| 945 | 17,336 | `81 09 00 06 01 46 00 00 96 43` | `f7 81 07 00 06 00 00 96 43 09` | ID 70 (Positionsregler-Kp) = 300 (Makro) |
| 957 | 18,526 | `81 04 00 11 04` | `f7 81 05 00 11 02 00 0e` | Stall-Mode lesen → 2 |
| 981 | 18,570 | `81 04 00 11 03` | `f7 81 04 00 11 03 13` | **Stall-Mode 3** an Motor x |
| – | 18,6–22,4 | **kein Frame an 0x81** | – | Suchfahrt; 2× „motor_x 81 check_protection_code“ (04:18,404 / 04:20,246) |
| 1471 | 04:22,423 | – | – | „final pos:[200.0, 200.0, 175.0, 86.19] x:-8.2 cut_pos_offset:0.4 cut_pos:-8.2+0.4=-7.79…“ |
| 1472/1474 | 22,426/23,057 | – | – | `save_config: set [box] cut_pos_x = -7.80`, `SAVE_CONFIG` (CXSAVE, mit Backup) |
| 1476 | 23,073 | `81 04 00 11 02` | `f7 81 04 00 11 02 14` | zurück auf den gelesenen Mode 2 |
| 1489 | 24,154 | `81 09 00 06 01 46 00 00 c8 42` | `f7 81 07 00 06 00 00 c8 42 d4` | Kp = 100 (Makro) |

**Stall-Erkennung (belegt durch Ausschluss):** Während der ~60 s Suchfahrt geht
KEIN RS485-Frame an 0x81 (auch kein `0C`/`14`), nur die Host-Meldung
`check_protection_code` – das ist der Stall-Pin-Pfad (PB11 = `motor_x_stall`),
kein Bus-Polling. Ob Stock den Pin per `buttons` oder anders abtastet, ist
unbelegt (Vanilla: `buttons.register_buttons`, Latch `cut_state`).

**Zeitbild (belegt):** die zwei Treffer liegen 1,84 s auseinander; X −5,0 …
−8,2 sind 33 Positionen, 33 × 1,84 s ≈ 60 s = gemessene Dauer → Anstoßen in
**0,1-mm-Schritten ab −5,0**, erster Treffer −8,1, zweiter −8,2, Ergebnis =
**2. Treffer + 0,4**. Das deckt sich mit der Vanilla-Rekonstruktion
(`lokale Analyse-Dateien` `MotorCutCalibrationHelper`:
Park X 50, Anstoß F10000, zurück F12000, Treffer ab `x <= check_cut_pos_x_max`,
Ausgang X 200). Live belegt davon nur Ausgang X 200 / Y 200 („final pos“).
REA (`motor_check_cut_pos` @0xe270c): `SET_VELOCITY_LIMIT ACCEL=25000`;
die dortige Lesart „Mode 4“ ist der LESE-Request `0x11 [04]` (live).

## 2. Pi-Ersatz

`KTWO_MOTOR_CUT_CAL` (in `[k2_motor]`) fährt die komplette Folge in EINEM
Befehl, damit der Rückbau auch bei jedem Fehler läuft:

1. Sperren prüfen (s. §3), `wait_moves`.
2. Beschleunigung auf `min(cut_cal_accel 25000, max_accel)` (≙ SET_LIMITS).
3. `81 09 00 06 01 46 00 00 96 43` (Kp 300, `cut_cal_pos_kp`).
4. Park X 50 / Y = `[k2_box] cut_pos_y` (200).
5. `81 04 00 11 04` lesen, `81 04 00 11 03` setzen (Echo-Prüfung).
6. Stall-Pin muss jetzt FREI sein (Pegelabfrage X-Endstop), sonst Abbruch.
7. Anstöße X = −5,0, −5,1, … bis `check_cut_pos_x_min` (−9,5): jeder Anstoß
   ist eine Upstream-`HomingMove` am **X-Endstop PB11** (`probe_pos=True`,
   `check_triggered=False`) – die MCU tastet PB11 ab und stoppt X/Y sofort
   beim Stall; ohne Auslösung 0,1 s Nachlauf + Pegelabfrage (Stall erst nach
   dem letzten Schritt). Danach zurück auf Park X 50.
8. Treffer zählen erst ab `x <= check_cut_pos_x_max`; nach dem 2. Treffer:
   `cut_pos_x = round(2. Treffer + cut_pos_offset, 2)`, muss in
   `[check_cut_pos_x_min, check_cut_pos_x_max]` liegen (sonst würde k2_box
   den Wert beim Neustart ablehnen).
9. Ausgang X 200 / Y 200, `configfile.set('k2_box', 'cut_pos_x', '%.2f')`.
10. Rückbau: `81 04 00 11 02`, `81 09 00 06 01 46 00 00 c8 42` (Kp =
    `x_controller_pos_loop_pid_param_kp`, 100), Beschleunigung zurück.

Frame-Folge identisch zum Live-Mitschnitt (Test + Batch). Das Makro
`CALIBRATE_CUT_POS` (`cutcal.cfg`) prüft die Sperren beim Rendern, referenziert
X/Y bei Bedarf (`G28 X Y`), klammert mit `SAVE_GCODE_STATE`/`RESTORE_…`, meldet
das Ergebnis (`printer.k2_motor.cut_cal`) und ruft mit `SAVE=1` `SAVE_CONFIG`.
`KTWO_MOTOR_CUT_CAL DRY_RUN=1` zeigt den Plan (ohne Sperren, nichts gesendet).

Neue optionale `[k2_motor]`-Optionen (Defaults = Stock, nicht in
`k2-motor.cfg` eingetragen): `cut_cal_start_x` (−5), `cut_cal_step` (0.1),
`cut_cal_park_x` (50), `cut_cal_exit_x` (200), `cut_cal_probe_speed`
(166.67 mm/s), `cut_cal_park_speed` (200), `cut_cal_accel` (25000),
`cut_cal_settle` (0.1 s), `cut_cal_pos_kp` (300); `cut_pos_offset` (0.4,
bisher nur durchgereicht) wird jetzt gelesen. Prüfbereich und Y kommen aus
`[k2_box]` (`check_cut_pos_x_min/max`, `cut_pos_y`), ohne k2_box Stock-Werte.

## 3. Sicherheit

- **Sperren** (Fehler OHNE Frame und OHNE Bewegung): `[k2_motor] allow_write:
  True` UND `_PI_FREIGABE allow_move` (das Modul liest das Makro selbst; fehlt
  es → gesperrt), kein aktives X/Y-Homing, X/Y referenziert, kein zweiter Lauf;
  im Makro zusätzlich Filamentsensor (Stock key854).
- **Harte Grenzen** (Config kann sie nicht aufweichen): X nie unter
  `check_cut_pos_x_min` und nie unter −10 bzw. `[stepper_x] position_min`;
  Prüfbereich innerhalb −10…0; Schritt 0,05–0,5 mm; max. 100 Anstöße (Stock 46);
  Anstoß ≤ 200 mm/s, Park ≤ 250 mm/s, Beschleunigung ≤ 25000 und ≤ `max_accel`;
  Park mind. 5 mm vor dem Start, ≤ X 100; Offset 0–2 mm; Y 50–350.
- **Abbruch ohne Stall** nach dem tiefsten Anstoß (`x_min`): G-Code-Fehler
  „kein Stall bis X=-9.50 … Abbruch nach max. Weg“; ebenso „nur ein Stall“,
  „Stall-Pin schon vor der Fahrt aktiv“, Ergebnis außerhalb des Bereichs.
- **Rückbau immer:** Kopf auf Park (nur bei Fehler, weg vom Schneidhebel),
  Mode 2 (auch wenn vorher 1/3 gelesen wurde), Kp 100, Beschleunigung. Fehler →
  G-Code-Fehler mit Rückbau-Bericht, nie Shutdown, nichts vorgemerkt.
- Mode 3 bleibt über `KTWO_MOTOR_STALL_MODE` weiterhin abgelehnt.

## 4. Abweichungen von Stock (bewusst)

- Stall-Erkennung über den vorhandenen X-Endstop (PB11) per MCU-trsync statt
  Host-Latch: kein neues MCU-Objekt, kein Config-Eingriff; der Anstoß stoppt
  beim Stall sofort (Stock fährt bis zum Ziel). Nachlauf-Abfrage deckt späte
  Stalls ab.
- Kp/Stall-Mode/Limits im Modul statt im Makro (Rückbau-Garantie).
- Nur X/Y müssen referenziert sein (`G28 X Y`), Stock: `G28` komplett.
- Kein automatisches SAVE_CONFIG (Upstream startet dabei neu); `SAVE=1` im Makro.
- Mode-Rückbau immer auf 2 (Stock: gelesener Wert, live = 2 → gleiche Frames).

## 5. HANDLUNGSBEDARF: SAVE_CONFIG-Konflikt mit k2-module.cfg

`k2-module.cfg` `[k2_box]` enthält `cut_pos_x: -7.80`. Upstream-`SAVE_CONFIG`
verweigert dann (Batch `lauf-save-konflikt`): **„SAVE_CONFIG section 'k2_box'
option 'cut_pos_x' conflicts with included value“** (`configfile.py`
`_disallow_include_conflicts`). Abhilfe (Datei gehört dem k2_box-Strang, hier
NICHT geändert): Zeile `cut_pos_x:` in `k2-module.cfg` entfernen bzw.
auskommentieren – `k2_box.py` hat den Default −7.80 (`CHANGE_DEFAULTS`). Batch
`lauf-save-ok` (Kopie ohne die Zeile): SAVE_CONFIG schreibt
`#*# [k2_box] / cut_pos_x = -7.60`, Neustart lädt den Wert fehlerfrei. Den
Kommentar über `[k2_box]` („kein CALIBRATE_CUT_POS auf dem Pi“) dabei anpassen.

## 6. Inbetriebnahme (Vorschlag)

1. `[include cutcal.cfg]` in printer.cfg, §5 erledigen, Neustart.
2. `KTWO_MOTOR_CUT_CAL DRY_RUN=1` → Plan prüfen (46 Anstöße −5,0…−9,5).
3. Voraussetzung: sensorless X/Y-Homing läuft (NOTIZ-stall.md §5), Filament
   entladen, Hand am Not-Aus.
4. Polarität PB11 in Mode 3 prüfen (ANNAHME s. §7): `KTWO_MOTOR_STALL_MODE`
   liest 2; die Kalibrierung bricht selbst ab, wenn PB11 nach Mode 3 schon
   aktiv ist.
5. `CALIBRATE_CUT_POS` → Ergebnis mit dem Stock-Wert −7,80 vergleichen
   (±0,1–0,2 mm plausibel), dann `SAVE_CONFIG` (oder gleich `SAVE=1`).

## 7. Offene ANNAHMEN

- PB11 meldet in Mode 3 einen Stall **high-aktiv** wie in Mode 1 (Endstop
  ohne `!`); Puls lang genug für die Endstop-Abtastung (4 × 15 µs) bzw. als
  Pegel noch 0,1 s nach dem letzten Schritt anliegend. Am Gerät unbelegt.
- Stock tastet in 0,1-mm-Schritten ab −5,0 und wertet den 2. Treffer (Zeitbild
  + Ergebnis −8,2+0,4 sprechen dafür; Code von MOTOR_CHECK_CUT_POS nicht
  dekompiliert). „2. Treffer“ = beliebiger späterer Anstoß (wie Vanilla), live
  waren es zwei aufeinanderfolgende.
- Park X 50, Anstoß 166,7 mm/s, Rückfahrt 200 mm/s aus Vanilla (Zeitbild
  1,84 s/Anstoß nicht vollständig erklärt – Stock ist langsamer, Grund offen).
- Beschleunigung 25000 (REA-String), Wirkung von `SET_LIMITS` unbelegt.
- Treffer vor `check_cut_pos_x_max` werden ignoriert (Vanilla würde sie in den
  nächsten zählenden Schritt mitnehmen).
- Filamentsensor-Sperre im Makro = Stock key854 (Bedeutung aus REA-Fehlertext).
- Das Ergebnis muss in `check_cut_pos_x_min/max` liegen (Stock prüft
  vermutlich den Messwert; live lagen Messwert und Ergebnis beide im Bereich).
