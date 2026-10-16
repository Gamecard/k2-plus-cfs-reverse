> **ENTWURF – Stand 16.10.2026 abends.** Sensor-Lesepfad und Swap-Leitung am Gerät GETESTET (ohne Bewegung, PRTOUCH-LESETEST-BEFUND-2026-10-16.md, Fingerdruck-Test 19/19). **Hauptweg ist jetzt eine normale Upstream-Klipper-Probe** auf `!PC7` (wie Stock). Antasten mit Bewegung ist weiterhin **UNGETESTET**. Offline-Tests: 138/138 + Review-Test (davon 21 gegen die echten Upstream-Dateien `probe.py`/`homing.py`/`manual_probe.py`/`gcode.py`/`pins.py`). Vor der ersten echten Fahrt: Abschnitt A.5 (ANNAHMEN P1–P6) und A.6 (Erstlauf) beachten, erst mit `allow_move: False`.

# [k2_prtouch] – Kraftsensor-Düse des K2 Plus unter Upstream-Klipper

## A. Hauptweg: Upstream-Probe (`mode: probe`, Default)

### A.1 Prinzip (BELEGT)

```
Düse scharf (start_prtouch_pres, Antast-Parameter)
  → Kontakt: Düsen-MCU zieht PA15 auf 0
  → Main-MCU sieht PC7 = 0
  → normaler Upstream-MCU_endstop auf !PC7 + trsync stoppt Z IN DER MCU
  → danach stop_prtouch_pres sta_swap=1 (PA15/PC7 wieder 1)
```

Genau so macht es Stock (`PRTouchEndstopWrapper`, `config_endstop pin=PC7 pull_up=0`,
`step_swap_pin: !PC7`, `probe:z_virtual_endstop`). `[k2_prtouch]` registriert sich
deshalb als Objekt `probe` (wie `[bltouch]`/`[load_cell_probe]`), und damit
funktionieren die Standardbefehle: `PROBE`, `QUERY_PROBE`, `PROBE_ACCURACY`,
`PROBE_CALIBRATE`, `Z_OFFSET_APPLY_PROBE`, `G28 Z` (über
`endstop_pin: probe:z_virtual_endstop`), `BED_MESH_CALIBRATE`, `Z_TILT_ADJUST`,
`SCREWS_TILT_CALCULATE` …

Ablauf je Antastung (`K2PrtouchProbeCore.run_probe`), Stock-Reihenfolge laut
Emulation von `probe_prepare`/`probe_finish`, ergänzt um eigene Prüfungen (*):

1. *Freigabe: `allow_move: True`, sonst Fehler ohne jede Bewegung (schon in
   `start_probe_session`, also auch vor den XY-Fahrten von `BED_MESH_CALIBRATE`).
2. *Geschwindigkeit ≤ 5 mm/s (auch `PROBE_SPEED=` und bei `G28` die
   `homing_speed`/`second_homing_speed` aus `[stepper_z]`), sonst Fehler.
3. `toolhead.wait_moves()` – scharf geschaltet wird im Stillstand (Schwelle
   relativ zum Ruhewert beim Scharfschalten, Fingerdruck-Test §6).
4. *Signalweg-Test über den Stopp-Endstop: `sta_swap=0` → Endstop muss
   „ausgelöst“ melden, `sta_swap=1` → „frei“ (`swap_check`, Default an).
5. `cont_prtouch_step` → `start_prtouch_pres` (ack `err != 0` → Abbruch) →
   `start_prtouch_step` (ack geprüft).
6. *Wartezeit `arm_settle_s` (Default 0,2 s, ANNAHME P2), Ruhetest
   (`rest_check`, `dif ≤ rest_max_dif`).
7. *Endstop darf jetzt NICHT ausgelöst sein, sonst Abbruch („Düse meldet bereits
   Kontakt VOR der Fahrt“).
8. Fahrt: Upstream `homing.probing_move()` (HomingMove/trsync). Ziel außerhalb
   von G28: höchstens `probe_max_travel_mm` (Default 8, max 10) unter der
   aktuellen Höhe; bei G28 wie Upstream bis `position_min`.
9. Immer (try/finally, auch bei jeder Ausnahme): `stop_prtouch_step` →
   `stop_prtouch_pres sta_swap=1`. Optional (`diag_readout: True`) danach der
   eingefrorene Ring in Stock-Reihenfolge (Status `[oid,2,0,0,0]`, FTR/SRC
   idx 0…56 mit len 64−idx, step analog) – nur Diagnose, in `KTWO_PRTOUCH_STATUS`.

Fehler werden als `printer.command_error` gemeldet (kein Klipper-Shutdown).

### A.2 printer.cfg-Beispiel (Upstream-Klipper auf dem Pi)

```ini
# k2_prtouch.py nach klippy/extras/k2_prtouch.py kopieren.
# KEIN zusätzliches [probe]/[bltouch] – [k2_prtouch] IST die Probe.

[mcu]
serial: /dev/serial/by-id/<Main-MCU>        # Platzhalter
[mcu nozzle_mcu]
serial: /dev/serial/by-id/<Düsen-MCU>       # Platzhalter (bzw. Stock-Anbindung)

[k2_prtouch]
mode: probe                 # Hauptweg (Rückfall: steps)
allow_move: False           # ERST nach TEST_SWAP/READ/QUERY_PROBE auf True
speed: 5.0                  # Antastgeschwindigkeit, hart <= 5 mm/s (Stock 5)
lift_speed: 5.0
z_offset: 0.0               # Stock 0 (Düse = Taster); PROBE_CALIBRATE schreibt hierher
#position_endstop: 0.0      # Default = z_offset
samples: 1                  # Upstream-Optionen wie [probe]:
sample_retract_dist: 2.0
#samples_result: median
#samples_tolerance: 0.100
#samples_tolerance_retries: 0
probe_max_travel_mm: 8.0    # Weg je Antastung außerhalb G28 (max 10)
arm_settle_s: 0.2           # ANNAHME P2
swap_check: True            # Signalweg-Test PA15->PC7 vor jeder Fahrt
rest_check: True            # Ruhetest nach dem Scharfschalten
rest_max_dif: 3000
#diag_readout: False

# Pins/MCUs (Stock):
main_mcu: mcu
nozzle_mcu: nozzle_mcu
step_swap_pin: !PC7         # Endstop-Pin, ausgelöst = PC7 low
pres_swap_pin: nozzle_mcu:PA15
pres_cs0_pin: nozzle_mcu:PB13, nozzle_mcu:PB14   # clk=PB13, sdo=PB14 (BELEGT)

# Antast-Parameter im Stock-Stil (BELEGT-Umrechnung, s. A.3):
pres_cfg_regs: 60
pres_acq_tkms: 0.78125
pres_tri_fter: 5, 1, 0.8
pres_tri_hold: 4000, 10000, 500
pres_ded_tkms: 128.0
step_acq_tkms: 0.390625

[stepper_z]
# … step/dir/enable/rotation wie Stock …
endstop_pin: probe:z_virtual_endstop
position_min: -10
position_max: 360
homing_speed: 5             # MUSS <= 5 sein (sonst lehnt k2_prtouch G28 ab)
second_homing_speed: 5      # dito
homing_retract_dist: 5      # 0 = nur eine Antastung beim G28

[stepper_z1]
# … wie Stock, aber OHNE endstop_pin (Upstream nutzt den Endstop von
# stepper_z für alle Z-Stepper; k2_prtouch hängt z und z1 an den Endstop).

[safe_z_home]               # G28 Z in Bettmitte (Upstream-Modul)
home_xy_position: 175, 175
z_hop: 5
z_hop_speed: 5

[bed_mesh]                  # Werte wie Stock
speed: 100
horizontal_move_z: 5
mesh_min: 5, 5
mesh_max: 345, 345
probe_count: 9, 9
mesh_pps: 2, 2
algorithm: bicubic
bicubic_tension: 0.2
fade_start: 5.0
fade_end: 50.0
split_delta_z: 0.01
move_check_distance: 1

[z_tilt]                    # Werte wie Stock
z_positions:
    -15, 175
    375, 175
points:
    5, 175
    345, 175
speed: 300
horizontal_move_z: 5
retries: 10
retry_tolerance: 0.1
```

Ablauf danach z. B. `G28` → `Z_TILT_ADJUST` → `G28 Z` → `BED_MESH_CALIBRATE`
(Temperaturen wie Stock vorher selbst einstellen: Düse 140 °C, Bett 50 °C – das
Modul heizt nicht).

### A.3 Stock-Konfig → MCU-Parameter (BELEGT per Emulation 16.10.2026)

| MCU-Parameter | Berechnung | Stock |
|---|---|---|
| `cfg_regs` | `pres_cfg_regs` | 60 |
| `acq_tick` | `round(pres_acq_tkms/1000 × 120 MHz)` | 93750 |
| `ned_tftr` | `pres_tri_fter[0]` | 5 |
| `ned_hftr` | `round(pres_tri_fter[1] × 1000)` | 1000 |
| `ned_lftr` | `round(pres_tri_fter[2] × 1000)` | 800 |
| `min/max/add_hold` | `pres_tri_hold` | 4000/10000/500 |
| `lmt_dead` | `int(pres_ded_tkms)` (abgeschnitten) | 128 |
| `aqc_tick` (step) | `round(step_acq_tkms/1000 × 120 MHz)` | 46875 |

`start_prtouch_pres [oid, 60, 93750, 5, 1000, 800, 4000, 10000, 500, 128]`,
`start_prtouch_step [oid, 46875]` – getestet inkl. Variation
(7, 3, 0.25 / 4100, 10200, 530 / 96.5 / 0.5 ms → 60000, 7, 3000, 250, 4100,
10200, 530, 96; aqc 60000). Die alten Annahmen (`ned_lftr_scale` 10,
`ned_hftr` 1, `lmt_dead` 200, `aqc_tick` 45700) waren FALSCH; `ned_lftr_scale`
wird jetzt mit Fehlermeldung abgelehnt. Direkte MCU-Optionen (`cfg_regs`,
`acq_tick`, `aqc_tick`, `ned_*`, `*_hold`, `lmt_dead`) übersteuern einzeln.
`clock_freq` (Default 120 MHz, BELEGT für beide MCUs) geht in beide Umrechnungen ein.
Hinweis: `ned_tftr`/`ned_hftr` sind im Dictionary `%c`; Stock sendet trotzdem 1000
(VLQ), wir senden exakt wie Stock.

### A.4 Geprüfte Upstream-API

Upstream-Klipper lokal, nur gelesen: Commit
`9cb99754c035b90206ef0a02660f674fc9517e9b` (13.10.2026, „mcu: Report a normal
CommandError if CommandWrapper fails“). Gegen diese Signaturen gebaut:

| Upstream | Datei:Zeile | Nutzung in k2_prtouch |
|---|---|---|
| `ProbeCommandHelper(config, probe, query_endstop=None, can_set_z_offset=True)` | `klippy/extras/probe.py:39-40` | PROBE/QUERY_PROBE/… |
| `lookup_minimum_z(config)` | `probe.py:188` | Fahrziel G28 |
| `LookupZSteppers(config, add_stepper_cb)` | `probe.py:196-206` | z/z1 an den Endstop |
| `HomingViaProbeHelper(config, position_endstop, query_endstop_cb=None)` | `probe.py:209-243` | `probe:z_virtual_endstop` |
| `DescendToEndstopHelper` (Vorbild, nachgebaut mit Wegbegrenzung) | `probe.py:246-275` | `K2DescendHelper` |
| `ProbeParameterHelper(config)`, `get_probe_params(gcmd)` | `probe.py:278-315` | speed/samples/… |
| `SampleAveragingHelper(config, param_helper, start_session_cb)` | `probe.py:318-407` | Mehrfachproben |
| `ProbeOffsetsHelper` (Vorbild, `z_offset` dort Pflicht) | `probe.py:410-416` | `K2ProbeOffsets` (Default 0) |
| `ProbeEndstopWrapper` / `PrinterProbe` (Vorbild Session-API) | `probe.py:545-628` | Wrapper/Probe-Objekt |
| `PrinterBLTouch`: `add_object('probe', …)` | `klippy/extras/bltouch.py:281-306` | Registrierung |
| `LoadCellPrinterProbe` (Vorbereitung vor der Fahrt) | `klippy/extras/load_cell_probe.py:664-726` | Vorbild |
| `Homing.home_rails` → `_do_home_z_via_probe` | `klippy/extras/homing.py:251-294` | G28 Z (`PROBE_SPEED`=homing_speed!) |
| `PrinterHoming.check_probe_first_home(gcmd)`, `probing_move(mcu_probe, pos, speed, check_movement=True)` | `homing.py:320-336` | Fahrt |
| `create_probe_result(test_pos, offsets)` | `klippy/extras/manual_probe.py:18-22` | Ergebnis |
| `PrinterPins.setup_pin('endstop', desc)` (`!` = invert) | `klippy/pins.py:114-118`, `67-95` | Endstop `!PC7` |
| `PinResolver.update_command` (`[ _]pin=NAME`) | `pins.py:16`, `41-53`; `klippy/mcu.py:1022-1025` | Pin-Namen in `config_prtouch_*` |
| `MCU_endstop` (`home_start/home_wait/query_endstop`, `pin_value ^ invert`) | `mcu.py:345-411` | Stopp + Abfragen |
| `MCU.lookup_query_command(msgformat, respformat, oid=None, cq=None, is_async=False)` | `mcu.py:1202-1205` | prtouch-Befehle |
| `MCU.register_config_callback/create_oid/add_config_cmd(cmd, is_init=False, on_restart=False)` | `mcu.py:1183-1188` | Config-Phase |
| `get_printer_mcu` (`'mcu nozzle_mcu'`) | `mcu.py:1257-1260` | MCU-Lookup |
| `MCU_stepper.get_oid()` | `klippy/stepper.py:132` | `oid_x/y/zstp` |
| `toolhead.wait_moves()`, `manual_move()` | `klippy/toolhead.py:410-428` | Stillstand vor Arm |
| `msgproto` Enumeration `*_pin` | `klippy/msgproto.py:125-135` | Pin-Namen |

Dabei behobene Hüllen-Fehler (alte Version wäre auf Upstream gescheitert):
MCU-Lookup `nozzle_mcu` → Upstream-Name `mcu nozzle_mcu`; Pins jetzt als Namen
statt über das nicht existierende `ppins`-Objekt/`get_enumeration`; Stepper-OIDs
über `toolhead.get_kinematics().get_steppers()` (Upstream legt keine
`stepper_x`-Objekte an); fehlende Config-Schlüssel (`stepper_*_oid`, `can_brk_*`)
werden jetzt gelesen; `stop_prtouch_pres` sendet immer `sta_swap=1`.
Nachtrag 16.10.2026 abends (Batch-Lauf gegen Upstream): G-Code-Namen `K2_*`
waren für Upstream ungültig (`register_command`: 2. Zeichen Ziffer) → jetzt
`KTWO_PRTOUCH_*`; der Integrationstest registriert die Befehle seitdem über
die ECHTE `gcode.GCodeDispatch.register_command` und prüft die Zerlegung durch
den Upstream-Parser (`args_r`). Die Reihenfolge-Falle (`import statistics`
landete auf `extras/statistics.py`, weil andere Module `klippy/extras/` vorn in
`sys.path` einfügten) ist in k2_motor/k2_box/k2_belt behoben – k2_prtouch
selbst war nicht betroffen.

### A.5 ANNAHMEN des Hauptwegs (offline nicht prüfbar, im Code markiert)

- **P1** Stopp unter Bewegung: PA15 → PC7 → `endstop_home`/trsync reagiert schnell
  genug (Upstream-Abtastung 4 × 15 µs). Belegt ist nur der Stand (Fingerdruck).
- **P2** `arm_settle_s` 0,2 s genügt zur Ruhewertübernahme der Düsen-MCU.
- **P3** Ruhetest per `read_prtouch_pres` auch mit zusätzlich gestartetem
  `start_prtouch_step` (live belegt nur pres-seitig, Test D).
- **P4** Signalweg-Test vor jeder Fahrt (sta_swap 0/1 ohne laufende Messung) stört
  nicht (entspricht Stock-TEST_SWAP).
- **P5** `start_prtouch_step`/`stop_prtouch_step` antworten mit `ack_prtouch`.
- **P6** Endstop und `config_prtouch_step` teilen PC7 (wie Stock) – auf Upstream ohne
  Pin-Konflikt, da `config_prtouch_step` den Pin nur als Namen trägt.
- Ladepfad: Datei liegt als `klippy/extras/k2_prtouch.py` (Upstream-Module per
  `from . import probe`); offline über ein `extras`-Paket getestet.

### A.6 Erstlauf (Pflicht-Reihenfolge)

1. `allow_move: False`: `KTWO_PRTOUCH_TEST_SWAP`, `KTWO_PRTOUCH_READ`, `QUERY_PROBE`
   (muss `open` melden), `KTWO_PRTOUCH_STATUS`.
2. `PROBE` mit `allow_move: False` → muss mit „allow_move“-Fehler abbrechen, ohne
   Bewegung.
3. `allow_move: True`, Z per `SET_KINEMATIC_POSITION Z=10` (nur für den Test!),
   Düse ~3 mm über dem Bett, Hand am Not-Aus: `PROBE` – Stopp beobachten.
4. `PROBE_ACCURACY SAMPLES=5` (Streuung), dann `G28 Z`, dann
   `BED_MESH_CALIBRATE` und gegen Stock-Profil (−0,061 … 0,224) plausibilisieren.

### A.7 Tests (offline, `python3 test_k2_prtouch.py`)

Neu (65 Prüfungen): Stock-Umrechnung inkl. Variation; Kern mit Fakes – Arm vor
Fahrt/Disarm danach in Stock-Reihenfolge, Disarm bei Ausnahme (auch fremde
Ausnahmen, Disarm-Fehler), Abbruch bei ack `err≠0`, Abbruch wenn PC7 vor der Fahrt
schon ausgelöst (mit/ohne Signalweg-Test), Ruhetest-Abbruch, `allow_move: False`
blockiert ohne jeden MCU-Befehl, Geschwindigkeitsgrenze (Config, `PROBE_SPEED=`,
G28-homing_speed), Wegbegrenzung; MCU-Adapter (sta_swap=1, eingefrorener Ring in
Stock-Abfragefolge). Integration gegen die **echten Upstream-Dateien** (wenn
`arbeitsdateien-lokal/upstream-klipper` vorhanden, sonst SKIP): `PROBE`,
`QUERY_PROBE`, `PROBE_ACCURACY SAMPLES=3`, `G28 Z` über die echte
`Homing`-Klasse, Abbruch bei `allow_move: False`/`homing_speed 10`/hängender
Leitung, `mode: steps`. Gesamt 138/138 + Review-Test; `test_k2_box.py` und
`test_k2_motor.py` je 9/9.

### Tests gegen echte Upstream-Dateien

`KLIPPER_SRC=/pfad/zu/klipper python3 test_k2_prtouch.py` – ohne die Variable werden die 21 Upstream-Prüfungen übersprungen (geprüft gegen Upstream-Commit 9cb9975).

## B. Rückfall: zeitgesteuerte Schrittfahrt (`mode: steps` / `KTWO_PRTOUCH_PROBE_STEPS`)

Der bisherige Weg (`K2PrtouchController.probe_point`) bleibt unverändert
erhalten. `KTWO_PRTOUCH_PROBE_STEPS` (früher `K2_PRTOUCH_PROBE`) gibt es in beiden
Modi; `KTWO_PRTOUCH_HOME_Z` und `KTWO_PRTOUCH_MESH` nur bei `mode: steps` (dann ohne
`probe`-Objekt, kein `probe:z_virtual_endstop`). Der folgende Altbericht
beschreibt diesen Rückfallweg; durch A überholte Punkte sind markiert.

---

# Altbericht – Rückfall-Schrittfahrt (Stand 16.10.2026 mittags)

Stand: 16.10.2026. Ablage: `lokale Arbeitsdateien `
Ergebnis: `k2_prtouch.py`, `test_k2_prtouch.py`, `BERICHT.md` (dieser).
Regeln: KEIN Netzwerk, KEIN ssh/curl, KEIN Druckerzugriff. Nur analysiert +
entworfen + getestet mit Fakes. Alle Dateipfade unten sind Lese-Belege.

## 0. Einordnung

Der eingebaute Kraftsensor (Düse als Taster, PrTouch v3) wird mit der
UNVERÄNDERTEN Creality-MCU-Firmware genutzt und ersetzt
`prtouch_v3_wrapper.so`. Kernbefund (BELEGT):

- Die MCU erkennt den Kontakt (Schwellen `min/max/add_hold`, `ned_*`,
  `tri_chxs`), stoppt aber über die prtouch-Befehle NICHTS (KORREKTUR: der Stopp entsteht über die Swap-Leitung + normalen Endstop, s. Teil A) (STOPP-BEWEIS §§2-3: kein
  `stepper_stop_on_trigger`/`trsync`/`endstop_home` in den prtouch-Pfaden;
  Swap Düse→Main nur HIGH bei config/start, Main liest einmal).
- Der Host fährt zeitgesteuert (live 5 mm/s), zeichnet `pres` 64×0,78 ms
  (49,8 ms) + `step` 64×0,38 ms (24,4 ms) auf und rechnet den Kontakt
  nachträglich (`best_indx → step_bst_indx → zoft`, live `zoft`
  0,010–0,040 mm, `tri_time` immer 0,000, `CAL_TURN == best_indx` an allen
  99 Punkten).
- `trig_pos:None` überall, `TEST_SWAP` (PY 78/PRES 71/STEP 71) vor jeder
  Phase, `PRES_CHECK SUCCESS`, Temperaturen 140/50 °C (180 °C Reinigung),
  Lüfter 0,6, Hub 5 mm, Mesh 9×9 Spirale 2,9 s/Punkt, 1× `can_brk False`
  mit versetzter Wiederholung (−0,25/+0,25 mm).

Folge (ÜBERHOLT durch Teil A – der Stopp läuft über Endstop/trsync, nicht nur über den Host): Ein sicheres Ersatzmodul darf NIEMALS eine lange, blinde Tauchfahrt
machen. Es muss in kurzen Schritten fahren, nach jedem Schritt per M400
synchronisieren, lesen/auswerten und hostseitig abbrechen.

## 1. Architektur

```
printer.cfg [k2_prtouch]
  → PrinterK2Prtouch (Klipper-Hülle, ANNAHMEN Upstream-API)
    → K2PrtouchController (reine Logik, ohne Klipper testbar)
      → pres-MCU (KlippyPresMcu / FakePres)   Düse nozzle_mcu
      → step-MCU (KlippyStepMcu / FakeStep)   Main mcu
      → Toolhead   (KlippyToolhead / FakeToolhead)
```

- `k2_prtouch.py` ist ohne Klipper importierbar (reine Helfer +
  Controller). Alle MCU-/Bewegungs-Zugriffe sind hinter `PrtouchMcuOps` /
  `ToolheadOps` gekapselt (Stil wie `k2_motor_init.py`/`k2_box.py`).
- Einmalig (register_config_callback, vor connect):
  `config_prtouch_pres` auf `nozzle_mcu` (idx, swp PA15, clk/sdo –
  s. ANNAHMEN), `config_prtouch_step` auf `mcu` (X/Y/Z-OIDs, swp !PC7).
- Pro Antastung: `TEST_SWAP` + Ruhetest → `start_prtouch_step` (`aqc_tick`)
  + `start_prtouch_pres` (`cfg_regs=60`, `acq_tick=93750`, `ned_*`,
  `holds 4000/10000/500`, `lmt_dead`) → Schleife aus
  `move_down(step_mm) + wait_moves + read_pres/step + decode + detect` →
  bei Kontakt `stop_*` + Lift + `Z = A + zoft` + `can_brk`, ggf. genau
  einmal versetzt wiederholen.
- Dekodierung: `ticks` u32-LE → Sekunden `(t−t_last)/freq`,
  `datas` s32-LE (pres roh, step Zähler → mm via `step_dist_mm`
  0,0025, relativ zum Ende). `tri_pose ≈ 0`, `bst_pose` interpoliert,
  `zoft = bst−tri`.
- G-Codes: `KTWO_PRTOUCH_PROBE_STEPS [X Y DRY_RUN]`, `KTWO_PRTOUCH_HOME_Z`,
  `KTWO_PRTOUCH_MESH [COUNT]`, `KTWO_PRTOUCH_TEST_SWAP`, `KTWO_PRTOUCH_READ`,
  `KTWO_PRTOUCH_STATUS`. `get_status()` für Moonraker (allow_move, step,
  letztes Ergebnis – kein Bus-Poll).
- `apax` ist bewusst NICHT enthalten (BELEGT 0 Treffer in 11532 Log-Zeilen,
  Host-Befund: separates Aux-Modul, für Z-Probe entbehrlich).

## 2. Sicherheitskonzept (S1–S5, im Code + Tests)

- **S1 Kurze Schritte:** `step_mm` Default 0,02, Maximum 0,05 (hart in
  `parse_options` + doppelt in `probe_point`); `probe_speed` Default 5,0,
  Maximum 5,0; nach JEDEM Schritt `wait_moves()` (M400). Test (b)+(g).
- **S2 Harte Grenzen:** `max_travel_mm` Default 5,0 (Stock-Hub);
  `emergency_thresh` Default 15000 (|roh−Null| → Abbruch + Fehler);
  `probe_timeout_s` Default 30 s. Bei JEDEM Fehler: `stop_*`, Lift
  `z_lift_mm` (Default 2,0), klare Meldung, kein Weiterprobe (außer
  genau einer `can_brk`-Wiederholung). Tests (b)+(c).
- **S3 Freigabe:** `allow_move` Default False → nur Swap/Ruhe/Lesen/Plan,
  keine Z-Bewegung. `KTWO_PRTOUCH_PROBE_STEPS DRY_RUN=1` ebenfalls bewegungslos.
  Test (d).
- **S4 Vorabtests:** `TEST_SWAP`-Äquivalent (beide MCU-Seiten müssen
  antworten; Fehlertext wie Stock `PR_ERR_CODE_SWAP_PIN_DETECTI`) +
  Ruhetest (`dif ≤ rest_max_dif` Default 3000, analog PRES_CHECK).
  Abbruch VOR Bewegung. Test (e).
- **S5 Temperaturen:** Nur Warnung bei Abweichung von 140/50 °C (±15),
  niemals Heizen. `get_temps()`-Fehler → Hinweis, kein Abbruch.
- **Überfahrweg:** Fake-Spike erscheint im ersten Fenster nach Kontakt;
  Schleife bricht im selben Durchlauf ab → maximal 1× `step_mm`
  Überfahrt (live Fenster 49,8/24,4 ms ≈ 0,25/0,12 mm bei 5 mm/s, hier
  per Schritte auf 0,02 mm begrenzt). Test (g): `min_z ≥ wahr−step_mm`,
  `Z-Fehler < 0,02 mm` (a).

## 3. printer.cfg-Beispiel (Rückfall `mode: steps`)

```ini
# Dateien kopieren: k2_prtouch.py → klippy/extras/k2_prtouch.py
# (Upstream-Klipper auf dem Pi-Host, MCU-FW unverändert lassen).

[k2_prtouch]
mode: steps
# Sicherheit: erst DRY_RUN testen, dann True (S3):
allow_move: False
step_mm: 0.02            # Maximum 0.05 (S1)
probe_speed: 5.0         # Maximum 5.0 (S1)
lift_speed: 5.0
max_travel_mm: 5.0       # Stock-Hub (S2)
z_lift_mm: 2.0           # Anhebung nach Kontakt/Fehler (S2)
probe_timeout_s: 30.0
contact_thresh: 3000     # ANNAHME s. §4
emergency_thresh: 15000  # ANNAHME s. §4
rest_max_dif: 3000       # ANNAHME PRES_CHECK-Analogon

# MCUs/Pins (Stock aus printer-live.cfg):
main_mcu: mcu
nozzle_mcu: nozzle_mcu
pres_idx: 0
pres_swap_pin: nozzle_mcu:PA15
step_swap_pin: !PC7
pres_cs0_pin: nozzle_mcu:PB13, nozzle_mcu:PB14
# ANNAHME clk/sdo aus cs0 (Reihenfolge tauschbar):
# pres_clk_pin: nozzle_mcu:PB13
# pres_sdo_pin: nozzle_mcu:PB14
# pres_clk_sdo_swap: False

# Sampling im Stock-Stil (Umrechnung BELEGT, s. Teil A.3):
pres_cfg_regs: 60
pres_acq_tkms: 0.78125
pres_tri_fter: 5, 1, 0.8
pres_tri_hold: 4000, 10000, 500
pres_ded_tkms: 128.0
step_acq_tkms: 0.390625

# Datenformat (ANNAHME, zentral):
# tick_format: <I
# data_format: <i
# step_dist_mm: 0.0025
# retry_dx: -0.25
# retry_dy: 0.25

# homing_override-Vorschlag (Z-Homing über eigenen G-Code):
# [homing_override]
# gcode:
#   {% if params.Z is defined %}
#     KTWO_PRTOUCH_HOME_Z
#   {% else %}
#     G28 X Y
#   {% endif %}
# Danach: BED_MESH_PROFILE LOAD="default" wie Stock (sensorless.cfg).
```

Erstlauf-Reihenfolge: `KTWO_PRTOUCH_TEST_SWAP` → `KTWO_PRTOUCH_READ` →
`KTWO_PRTOUCH_PROBE_STEPS DRY_RUN=1` → `allow_move: True` + `KTWO_PRTOUCH_PROBE_STEPS` →
`KTWO_PRTOUCH_HOME_Z` → `KTWO_PRTOUCH_MESH COUNT=3`.

## 4a. Stand nach Gerätetest 16.10.2026 (PRTOUCH-LESETEST-BEFUND)

- **1 Datenformat: WIDERLEGT → gepackt** (`packing: packed`, `decode_packed`), live 315+3/318 Pakete exakt.
- **2 clk/sdo: BELEGT** PB13 = clk, PB14 = sdo (Emulation des Stock-Moduls, wörtlicher `config_prtouch_pres`).
- **7 read-Args: BELEGT** `is_src=1` = Rohwerte, je Abfrage ≤ 8 Samples aus 64er-Ring (`idx` = Ringposition) → 8 Blöcke + `merge_ring`.
- **10 Ruhetest:** echte Ruhe-DIF 756–1 292 (64–2 500 Samples) → Schranke 3000 passt.
- **13 Swap: BELEGT** Ablauf `sta_swap` 0/1 + Rücklesen `expar0` (0, 1) an der Main-MCU; Versionen in `expar1`.
- **14 Stepper-OIDs:** Stock nimmt den letzten Z-Stepper (`stepper_z1`).
- **Neu:** Stock nutzt zusätzlich einen normalen Endstop auf `PC7` mit trsync (Z-Stopp über Swap-Leitung) – Pi-Weg über Standard-Probe prüfen (Befund §3).
- **Fingerdruck-Test BELEGT:** Antast-Modus zieht bei Kontakt PA15/PC7 auf 0 (19/19, 0 Fehlauslöser) → Standard-Probe auf `!PC7` als Hauptweg.
- **3/4/5/6 Antast-Parameter: BELEGT** (Emulation abends, Tabelle Teil A.3).
- Offen: Main-MCU-Lesepfad, Stopp unter Bewegung (Teil A.5 P1).

## 4. ANNAHMEN (vollständig, vor Gerätetest zu klären)

1. **Datenformat ticks/datas:** u32-LE / s32-LE, 4 B, little (Optionen
   `tick_format <I`, `data_format <i`). BELEGT: `%.*s`-Arrays, 64 Samples,
   `from_bytes/little/signed`-Strings in `unzip_data`. Byte-Breite
   statisch nicht entscheidbar (SPEZIFIKATION §3, MCU-Befund §6).
2. **clk/sdo-Belegung:** Ableitung aus `pres_cs0_pin` PB13/PB14, Reihenfolge
   per `pres_clk_sdo_swap` (Default PB13=clk, PB14=sdo). BELEGT Negativ:
   stehen NICHT in `printer-live.cfg` (SPEZIFIKATION §1.3); Herkunft aus
   Wrapper-Build (`adc_par/clk_par/sdo_par`). Nur per Ghidra-Cross-Ref
   oder Live-MCU-Mitschnitt beweisbar.
3. **ned-Mapping:** `pres_tri_fter (5,1,0.8)` → `ned_tftr/hftr/lftr` in
   Format-Reihenfolge. BELEGT Werte/Stellenzahl, ANNAHME Zuordnung.
   Zusatz: `ned_lftr` 0,8 (float) vs `%u` → Skalierung `ned_lftr_scale`
   (Default 10 → MCU-Wert 8). Exakter Faktor offen.
4. **holds-Mapping:** `pres_tri_hold (4000,10000,500)` →
   `min/max/add_hold` in Format-Reihenfolge. BELEGT Werte, ANNAHME
   Zuordnung (Host-Befund als V markiert).
5. **`lmt_dead`:** Default 200 (ANNAHME: `prth_err_dead` 0,2 × 1000).
   Kandidaten `prth_err_dead 0,2` vs `%u`, `pres_ded_tkms` 128,0,
   `pres_chk_hold` 15000 (SPEZIFIKATION §2.1). Nur per Roh-Frame klärbar.
6. **`aqc_tick`:** Default 45700 aus STEP-Fenster 0,0244 s (Spanne
   45600–46000 je Punkt). `step_acq_tkms`-Key existiert als Tupel,
   fehlt in `liveconfig_*` (SPEZIFIKATION §2.2).
7. **`read_*`-Args:** `ch=0` (BELEGT alle Logs nur CH0), `len=64`
   (ANNAHME Fenstergröße), `is_src=1` FTR / `idx=0` Ringoffset
   (ANNAHME; `is_src` 0/1 = SRC vs FTR/CAL aus Wellen-Namen).
8. **Kontaktkriterium:** Schwellwert `contact_thresh` 3000 + 2
   aufeinanderfolgende Samples (ANNAHME; exakte `ned_*`+`holds`/`dead`+
   CAL-Formel nur per Roh-Mitschnitt). Live-Plausibilität: FTR ±600
   Ruhe vs −9489 Spitze, TEST_PRES_AMPLITUDE `threshld` 3000.
9. **Notgrenze:** 15000 (ANNAHME Rolle von `pres_chk_hold`; BELEGT
   Existenz). Größer als normale Spitze (~9500), kleiner als defekt
   (50000+).
10. **Ruhetest:** `dif ≤ 3000` über 64 Samples (ANNAHME Analogon zu
    PRES_CHECK `dif=55285` über 7668 Samples + `threshld` 3000).
11. **can_brk:** UND-Regel (best≤53 UND step≤42 UND zoft≥0,040 → False)
    aus genau 1 Beobachtung (T58). Nachbarschafts-Vergleich
    (`final deutlich über Umgebung`) nur in `KTWO_PRTOUCH_MESH`
    (ANNAHME Schranke 0,05 mm).
12. **zoft-Semantik:** `Z = A + zoft` mit `A` = Z bei Trigger (tiefster
    Punkt). BELEGT `C=A+B` an 93 Punkten, `zoft` 0,010–0,040.
13. **Swap:** Nur Zeitbasis (BELEGT Fehlertext + TEST_SWAP + R/W-Richtung);
    `ticks`-Fusion als `(t−t_last)/freq` ohne extra Offset (BELEGT
    `step_bst_time == pres_bst_time` an allen 99). Pegel/Flanke offen.
14. **Stepper-OIDs:** Auto-Lookup via `get_oid/_oid/oid` (ANNAHME je nach
    Upstream-Version); Fallback manuelle `stepper_*_oid` in Config.
15. **Klipper-APIs (alle ANNAHME, Version beachten):**
    `register_config_callback`, `create_oid`, `add_config_cmd`,
    `lookup_query_command(cmd, resp, oid=)`, `pins.lookup_pin` (Altcode, ungenutzt),
    `mcu.get_enumeration('pin')`, `toolhead.manual_move/wait_moves/
    get_position`, `heater.get_temp()`, `bed_mesh.get_mesh_limits()`,
    `SET_KINEMATIC_POSITION`, Reactor-`pause`. Vor Gerätetest gegen
    Upstream-Quellen prüfen (s. §5).
16. **Upstream-Probe-API:** ÜBERHOLT – jetzt Hauptweg (Teil A). Alt: Bewusst NICHT voll implementiert (zu
    versionsabhängig). Stattdessen eigener `KTWO_PRTOUCH_MESH` (ANNAHME
    `bed_mesh`-Übergabe) + `KTWO_PRTOUCH_HOME_Z` via
    `SET_KINEMATIC_POSITION` (homing_override-Vorschlag oben).
17. **Temperaturkomp/Fallkomp/Outlier/Gauß/Regional:** Weggelassen
    (`samples=1`, `z_offset=0`, Komp rein per `zoft`; im Nivellierungs-Log
    0 Zeilen → still oder inaktiv). Bei Bedarf nachrüsten
    (`prth_tmp_comp`, `bed_mesh_fall_comp_*`, `probe_outlier_check`).

## 5. Vor dem ersten echten Test am Gerät (Pflicht)

1. **Config gegen Upstream prüfen:** Klipper-Version notieren;
   `register_config_callback`-, `lookup_query_command`-,
   `manual_move`/`wait_moves`-, `SET_KINEMATIC_POSITION`-Signaturen in
   den Upstream-Quellen (`klippy/mcu.py`, `toolhead.py`, `homing.py`,
   `probe.py`, `gcode.py`) gegenlesen. Fakes ersetzen keine API-Prüfung.
2. **Pins per Ghidra/Live-Mitschnitt belegen:** `clk/sdo`-Belegung
   (PB13/PB14-Annahme!), `pres_idx` (0/1 → PB13/PB14), Stepper-OIDs,
   `lmt_dead`/`ned_lftr`-Skalierung, `is_src/sta_swap`-Kodierung. Erst
   mit gezieltem MCU-Debug-Mitschnitt (`start/read/resault/stop/ack`
   mit `ticks/tri_chxs/buf_len/acq_tick`) sind die Roh-Frames beweisbar
   (SPEZIFIKATION Offene Punkte). Bis dahin: NUR DRY_RUN + READ +
   TEST_SWAP.
3. **Trockenlauf ohne Bewegung:** `allow_move: False` lassen;
   `KTWO_PRTOUCH_TEST_SWAP`, `KTWO_PRTOUCH_READ`, `KTWO_PRTOUCH_PROBE_STEPS DRY_RUN=1`
   müssen Plan + Ruhewerte liefern, ohne dass sich Z bewegt.
4. **Erste echte Antastung vorbereitet:** Bett frei, Düse sauber,
   Hand am Notausschalter; `step_mm` 0,02, `max_travel` klein (z.B.
   1,0 für den allerersten Versuch über dem Bett), `z_lift` 2,0;
   Temperaturen 140/50 einhalten (Warnung ernst nehmen, selbst heizen
   per Stock-Makro G29-Fluss, nicht per Modul).
5. **Logs sichern:** `klippy.log` + `KTWO_PRTOUCH_STATUS` nach jedem
   Versuch; bei `emergency/max_travel/timeout/can_brk False` sofort
   stoppen, Ursache (ANNAHMEN §4) prüfen, keine Wiederholung mit
   `allow_move` erzwingen.
6. **Mesh erst nach Einzelpunkten:** Erst mehrere `KTWO_PRTOUCH_PROBE_STEPS`
   an Bettmitte (Streuung < 0,02?), dann `KTWO_PRTOUCH_MESH COUNT=3`,
   Matrix gegen Stock-Profil (`default` −0,061…0,224) plausibilisieren.
   `can_brk False` → versetzten Wiederholungswert verwenden (wie T59),
   schlechten Wert NICHT ins Mesh übernehmen.

## 6. Tests

`python3 test_k2_prtouch.py` (ohne Klipper, deterministische Fakes):

- (a) Kontakt bei bekannter Höhe → `|z−wahr| < 0,02`, `zoft` plausibel.
- (b) kein `down`-Schritt > `step_mm`, Summe ≤ `max_travel`; Config-Härten
  (`step_mm>0,05`, `speed>5` → ValueError); `max_travel`-Abbruch begrenzt.
- (c) `emergency_always` → Status `emergency` nach ≤2 Schritten +
  angehoben (`z > min_z`).
- (d) `allow_move=False` / `DRY_RUN=1` → 0 Moves, Z unverändert.
- (e) `swap_ok=False` → `swap_error` (Stock-Wortlaut
  `PR_ERR_CODE_SWAP_PIN_DETECTI`); `rest_noisy` → `rest_error`; beide vor
  Bewegung (0 Moves).
- (f) alle 17 Formatstrings byte-exakt in beiden JSON-Dictionaries
  (inkl. `aqc_tick`/`resault_*`).
- (g) Stopp spätestens 1 Schritt nach Kontakt (`wahr−min_z ≤ step_mm`),
  `waits ≥ downs` (M400 nach jedem Schritt).
- (x) `can_brk`-Heuristik: T58-Tripel → False, Normal → True.

Stand bei Abgabe des Altberichts: 43/43; aktuell 138/138 (s. Teil A.7).
