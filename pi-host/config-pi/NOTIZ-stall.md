# NOTIZ: Sensorless-Homing X/Y auf dem Pi (16.10.2026 spät)

> Wird später in `config-pi/README.md` übernommen. ENTWURF – am Gerät
> UNGETESTET. Betroffene Dateien: `pi-host/k2_motor_init.py` (G-Codes),
> `config-pi/homing.cfg`, `config-pi/k2-motor.cfg` (`homing_pos_kp`, Kommentar
> `allow_write`), `pi-host/test_k2_motor.py`,
> `arbeitsdateien-lokal/upstream-test-stall/`.

## 1. Was Stock sendet (Belege)

Quelle Makros: `live-logs-2026-10-16/config-aktuell/sensorless.cfg`
`[homing_override]` (Anfang: `MOTOR_STALL_MODE DATA=1`, `MOTOR_SYS_PARAM
NUM=1 DATA=1 ID=70 PARAMS=300 PARAMS_TYPE=float`; Ende: `MOTOR_STALL_MODE
DATA=2`, `MOTOR_CHECK_PROTECTION_AFTER_HOME DATA=11`, `MOTOR_SYS_PARAM …
PARAMS=100`).

Quelle Frames: `live-logs-2026-10-16/klippy.log`, `G28 X Y` (Homing ohne Z,
`x_axes is NULL`) 14:06:21–14:06:31:

| Zeile | Zeit | Request (`retries = 0, cmd = …`) | Antwort |
|---|---|---|---|
| 369155 | 21,679 | `81 04 00 11 01` | `f7 81 04 00 11 01 1d` (369157) |
| 369163 | 21,700 | `82 04 00 11 01` | `f7 82 04 00 11 01 1d` (369165) |
| 369172 | 21,775 | `81 09 00 06 01 46 00 00 96 43` (ID 70 = 300.0) | `f7 81 07 00 06 00 00 96 43 09` (369174) |
| – | 21,827 | `_handle_home_rails_begin` (Y), 2 Homing-Fahrten; danach X, 2 Fahrten | |
| – | 30,612 | `FORCE_MOVE stepper_y distance=-3.000 velocity=10.000 accel=0.000` | |
| 369268 | 30,980 | `81 04 00 11 02` | `f7 81 04 00 11 02 14` (369270) |
| 369281 | 31,014 | `82 04 00 11 02` | `f7 82 04 00 11 02 14` |
| 369289–369310 | 31,140–31,199 | `8x 04 00 0c 0b` an 81, 82, 83, 84 | je `f7 8x 0b 00 0c` + 8×`00` + CRC |
| 369316 | 31,235 | E transparent `f7 81 04 00 0c 0b 95` | `f7 81 0b 00 0c 00…00 fb` |
| 369319 | 31,246 | `81 09 00 06 01 46 00 00 c8 42` (ID 70 = 100.0) | `f7 81 07 00 06 00 00 c8 42 d4` (369321) |

Gleiches Muster bei zwei weiteren `G28` mit Z-Homing zwischen X und END:
14:08:44–14:09:39 (Zeilen 370495–371154, vorher `x_axes: xy`) und
14:21:50–14:22:47 (377056–377744). Übersicht aller Logs 05.–16.10.:
`11 [01]` und `11 [02]` beim Homing **nur an 0x81/0x82**; `11 [02]` an 0x83/0x84
nur beim Boot (Init P4); `11 [03]`/`[04]` nur bei `MOTOR_CHECK_CUT_POS`
(`[04]` = lesen → `f7 81 05 00 11 02 00 0e`, `klippy.log.2026-10-12:31417/31419`).

- **Wartezeiten:** keine. Abstand der Frames ~20 ms (Bus-Laufzeit), BEGIN →
  Homing-Start 37 ms; die Lücken nach `data_send` (bis ~1 s) sind die
  gemeinsame ttyS5-Queue (CFS-Polls dazwischen), keine Pausen. `G4 P2000`
  in `_HOME_X/_HOME_Y` ist im Stock auskommentiert.
- **„Kp ID 70“** = Sys-Param 70 `controller_pos_loop_pid_param_kp`
  (Positionsregler-Kp; Tabelle `nachbau/motor.py` SYS_PARAM_NAMES,
  PROTOKOLL-485.md §3.1). `NUM=1` = Motorindex 1 = 0x81 (Motor „x“), `DATA=1` =
  Sub 01 (schreiben). Flüchtig (bis Kaltstart). Motor x hat im Betrieb 100,
  Motor y laut `motor_control.cfg` 300 → beim Homing beide CoreXY-Motoren
  Kp 300. Kein `06 [03]` beim Homing.
- **Stall-Mode-Bedeutung** (Stock-Hilfetext, REA-MOTOR-BEFUND §3): 1 =
  Endschalter-/Blockiersignal, 2 = Notfall-Schutzausgang. FW-Befund: `0C [0b]`
  quittiert bei Mode 2 die Notfall-Leitung (Codes bleiben).

## 2. Neue G-Codes in `[k2_motor]`

Siehe `pi-host/README.md` §9. Kurz: `KTWO_MOTOR_HOMING PHASE=BEGIN|END`
(komplette Stock-Folge), `KTWO_MOTOR_STALL_MODE`, `KTWO_MOTOR_SYS_PARAM`,
`KTWO_MOTOR_PROTECTION`. Schreiben NUR mit `allow_write: True` in
`k2-motor.cfg` (bewusst KEIN `ALLOW_WRITE=1`-Override wie bei
`KTWO_MOTOR_INIT`, damit `printer.k2_motor.allow_write` in homing.cfg die
wirkliche Freigabe ist). Fehler → G-Code-Fehler, nie Shutdown.

## 3. Ablauf in homing.cfg

1. Sperren beim Rendern (vor jeder Bewegung, kein Frame):
   `_PI_FREIGABE allow_move` (alle Achsen); für X/Y zusätzlich
   `printer.k2_motor.allow_write` und die Not-Sperre `xy_stall_ok`
   (jetzt Default **True**; bleibt für ältere Test-G-Codes erhalten).
2. `M204 S500`, `KTWO_MOTOR_HOMING PHASE=BEGIN` (Fehler → Rückbau, Abbruch
   ohne Bewegung).
3. `_PI_HOME_Y`: `M204 S100`, falls schon referenziert und y < 8 → auf 8
   freifahren, `G28 Y`, `G1 Y10 F3600` (Stock `_HOME_Y`).
4. `_PI_HOME_X`: `M204 S100`, falls schon referenziert und < 10 mm vor X-max
   → freifahren, `G28 X`, `G1 X-10 F3600` (Stock `_HOME_X`).
   *Beschleunigung 100:* Stock-Kette `_IF_MOVE_XY` (M204 S2000) →
   `_IF_HOME_Z` (M204 S100) → G28. Live-Plausibilität: zweite Homing-Fahrt
   (Rückzug 10 mm + Antasten mit 50 mm/s) dauert 1,42 s (Y) / 1,40 s (X); mit
   100 mm/s² gerechnet ~1,1 s + Stall-Erkennung, mit 500 mm/s² nur ~0,5 s.
5. `FORCE_MOVE STEPPER=stepper_y DISTANCE=-3 VELOCITY=10` unter den
   Stock-Bedingungen (s. §4).
6. `KTWO_MOTOR_HOMING PHASE=END` (Mode 2, Schutzabfrage, Kp 100).
7. ggf. Z wie bisher (Bettmitte, `G28 Z`, `G1 Z5`), zum Schluss `M204` max_accel.

Abweichungen von Stock (bewusst): END schon vor Z (Stock: ganz am Ende) –
ein Z-Fehler (z. B. `[k2_prtouch]` gesperrt) lässt X/Y nicht im Homing-Modus;
reines `G28 Z` sendet keine Stall-/Kp-Frames (Stock bei jedem G28). Bricht
`G28 X/Y` selbst ab, setzt das Modul X/Y über `gcode:command_error` zurück.

## 4. FORCE_MOVE stepper_y -3 (CoreXY) – übernommen

- Stock-Bedingungen (sensorless.cfg): alle Achsen, `X`+`Y`, oder nur `X`
  wenn vorher genau `y` referenziert war (bzw. nur `Y` bei `x`) – 1:1
  übernommen. Live 14:06:30,612 nach `G28 X Y` belegt.
- Upstream-`force_move.manual_move` bewegt nur den Motor von `[stepper_y]`
  und stellt danach die alte Kinematik wieder her, OHNE die
  Toolhead-Position zu ändern. Stock loggt exakt dieselbe Zeile
  (`FORCE_MOVE stepper_y distance=-3.000 velocity=10.000 accel=0.000`) →
  gleiche Implementierung.
- CoreXY (Upstream: stepper_x = X+Y, stepper_y = X−Y): −3 mm am Motor
  stepper_y = Düse physisch X −1,5 / Y +1,5 mm, Koordinate unverändert. Die
  Stock-`position_endstop`-Werte (X 352.5 / Y −6.2, unverändert in unserer
  printer.cfg) gelten MIT diesem Versatz. Weglassen würde alle Koordinaten
  um 1,5/1,5 mm gegenüber Stock verschieben (Bettmitte, Mesh, Reinigungs-/
  Schnittpositionen). Vermuteter Zweck: Wagen nach dem Anschlag entlasten.
- Restrisiko (ANNAHME): Vorzeichen-/Achszuordnung bei Creality identisch
  (gleiche Pins/dir in printer.cfg, gleicher force_move-Code) – am Gerät
  prüfen: nach `G28` Position eines Referenzpunkts mit Stock vergleichen.

## 5. Inbetriebnahme (Vorschlag)

1. `KTWO_MOTOR_STALL_MODE` (lesen, geht ohne allow_write) → x/y = 2.
2. `allow_write: True` (nach Init-Prüfung, README §4), Neustart.
3. `KTWO_MOTOR_STALL_MODE DATA=1` → `QUERY_ENDSTOPS` mehrmals, Wagen per
   Hand gegen den Anschlag drücken bzw. leicht blockieren → PB11/PB12
   müssen „TRIGGERED“ zeigen; `KTWO_MOTOR_STALL_MODE DATA=2` zurück.
4. `SET_GCODE_VARIABLE MACRO=_PI_FREIGABE VARIABLE=allow_move VALUE=True`,
   Hand am Not-Aus, `G28 Y`, dann `G28 X`, dann `G28`.

## 6. Offene ANNAHMEN

- Polarität/Pull-up von PB11/PB12 in Mode 1 (Stock `endstop_pin: PB11` ohne
  `^`/`!` übernommen) – Schritt 5.3 klärt das.
- `wait_moves()` vor jedem Moduswechsel: im Stock-Log nicht belegt
  (FORCE_MOVE → DATA=2 liegt 0,37 s auseinander, FORCE_MOVE dauert 0,3 s),
  hier aus Sicherheit.
- Timeout 0x11 = 1,0 s (live nie Timeout, Wert unbelegt; wie `nachbau/motor.py`).
- Echo-Prüfung 0x06: Antwort = exakt die 4 Float-Bytes (live belegt); eine
  Abweichung gilt als Fehler (Stock wertet vermutlich nur „写入成功“).
- Schutzabfrage nach dem Homing: Stock bei Fehlerbits (V) `FORCE_MOVE
  stepper_y 0.01`, `M84`, `MOTOR_CLEAR_ERR_WARN_CODE` – hier NUR G-Code-Fehler
  (kein Motor-Aus, kein `0C [05]`).
- Rückbau bei Homing-Abbruch (Mode 2/Kp 100) ist eine Erweiterung; Stock
  lässt die Motoren dann in Mode 1/Kp 300.
- Beschleunigung 100 mm/s² beim X/Y-Homing aus der Makro-Kette abgeleitet
  (Qmode-Sonderfall der Stock-`M204`-Makros ignoriert).
