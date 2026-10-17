#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2_box.py – Klipper-Zusatzmodul [k2_box] für die CFS-Box (K2 Plus).

Offener Ersatz für Crealitys ``box_wrapper.so`` (Stock, armhf, proprietär),
soweit der Box-Förderpfad betroffen ist. Gegenstück zu ``k2_motor_init.py``
(``[k2_motor]``): gleiche Stilregeln – reine Logik ohne Klipper testbar,
MCU-/Port-Zugriffe gekapselt, Schreiben nur mit ``allow_write``.

Grundlagen (nur gelesen, nichts gesendet):
- nachbau/box.py (ParseData/BoxState/BoxAction – WIEDERVERWENDET, nicht neu
  erfunden: RFID/Remain/Messrad/Box-State/Version/Buffer/Sensor/Hardware-
  Status, CfsStatusError/StatusBytes, FUNC_RETRIES, preload_force_timeout)
- nachbau/cfsseq.py (Extrude-/Retrude-Live-Sequenzen – WIEDERVERWENDET:
  build_extrude_sequence/build_retrude_sequence, EXTRUDE_LIVE_CMDS_B,
  RETRUDE_LIVE_CMDS_A, EXTRUDE_MODE_*/RETRUDE_P_*, slot_to_mask)
- nachbau/boxmini.py (has_filament-Semantik: remain 0 = unbekannt, nie False)
- nachbau/auto_addr.py (Adressvergabe – WIEDERVERWENDET via Adapter)
- nachbau/serial485.py (build_frame/parse_frame/CRC-8, Bus-Lock MAX_BUS_HOLD_S)
- PROTOKOLL-485.md (Func-Tabelle CFS-App R153, §6 Live-Gegenprobe: Retry-/
  Timeout-Tabelle §6.2, Extrude/Retrude live §6.5, 0x0a-Layout §6.6/§6.8,
  Schutzmechanismen §6.9; Sicherheitsregeln: nie 0x50/0x06, keine Bursts,
  ein gemeinsamer RS485-Transport/Lock für Motoren+CFS+Belt auf ttyS5)
- GHIDRA-CFS-BEFUND-2026-10-15.md + GHIDRA-CFS-BEFUND2-2026-10-16.md
  (0x0d op 0-3, 0x10 8 Modi, 0x11 p 0/1, 0x0a-Bytes Temp/Feuchte/Block/Hub,
  Status-Codes 0x0c/0x16/0x19/0x30/0x50/0x51/0x52, 0x06-FW-Bug, 0xEA/0xEB)
- LIVE-ABFRAGEN-2026-10-16.md (nur Motor – hier als Negativbeleg: Box braucht
  keinen Transparent-Pfad, nur RS485 0x01)
- CHECKLISTE.md Abschnitt „Kanal A“ (echter Live-Ablauf Preload/Extrude/
  Retrude + Fehler 0x0c bei ~245 mm und 0x19 beim Rückzug – als Testvorbild)
- pi-host/k2_transport.py (Rs485Transport-Schnittstelle + nachbau-Suche
  ensure_nachbau_path/check_nachbau_module; Bus-Lock-Teilung: EIN Port, EINE Queue, EIN Lock)
- Original-Makros config/F008_CR0CN240319C13/box.cfg + gcode_macro.cfg
  (BOX_LOAD_MATERIAL-Kette HEATING→CUT→RETRUDE→EXTRUDE→FLUSH→END,
  BOX_QUIT_MATERIAL-Kette, M8200 P/C/R/L/W/F/O → CR_BOX_PRE_OPT/CUT/
  RETRUDE/EXTRUDE/WASTE/FLUSH/END_OPT, BOX_CUT_MATERIAL) sowie
  live-1.1.7.0-configs/box-live.cfg ([box] bus:serial485, Tn_retrude -10,
  Tn_extrude 140/220 °C/360, buffer_empty_len 30 – reine Doku, keine
  Toolhead-Bewegung hier)

Ablauf (wie Stock/Box-Live, nur Box-Seite – kein Toolhead/Motor):
  A0 Adressvergabe via AutoAddr (erst nach ``k2_motor:ready`` – gleiche
     Reihenfolge wie Stock: Motor-Init → auto_addr:start; hier Event)
  A1 zyklischer Status-Poll 0x0a (retries=0, alle ~4 s wie Original)
  A2 lesend jederzeit: RFID (0x02), Remain (0x03), Version/SN (0x14),
     Hardware-Status (0x15), Sensor (0x08), Buffer (0x05), Messrad (0x0e [01])
  A3 Preload 0x0d op 0-3 (op 2/3 lange Timeouts + retries=0, keine Bursts)
  A4 Extrude (Load, Box-Seite): 0x04 [00 01] IDLE → 0x0f [01] an →
     0x08 [00] Sensor → 0x10 [m 00 00] Stage1 → 0x10 [m 04 00] Start →
     0x10 [m 05 00] x5 Odometer-Polls → 0x10 [m 06 00] Stopp/Buffer →
     0x10 [m 07 03] Extruder-Check (p=3 mm) → 0x05 Buffer →
     0x04 [m 00] PRINT → 0x0f [00] aus. Byte-identisch zu
     cfsseq.build_extrude_sequence(mask, pushes=5) == EXTRUDE_LIVE_CMDS_B
     (Beleg PROTOKOLL-485.md §6.5, cfsseq-Selbsttest).
     Abbruch bei Status != 0 mit CfsStatusError-Text (z.B. 0x0c).
  A5 Retrude (Unload, Box-Seite): 0x04 [00 01] → 0x08 [00] →
     0x11 [m 00] x4 → 0x11 [m 01] (Beleg RETRUDE_LIVE_CMDS_A gefiltert,
     cfsseq.build_retrude_sequence; 0x0e-/0x03-Zwischenabfragen sind
     Diagnose und nicht Teil der Sequenz).
  A6 Cut als Platzhalter (braucht Motor/Toolhead – sendet NICHTS).

Sicherheit (PROTOKOLL-485 §6.8/§6.9, BEFUND2 §5-§8):
- Schreiben (0x0d/0x04/0x0f/0x10/0x11, Auto-Addr 0xA0, GPIO gibt es hier
  nicht) nur mit ``allow_write: True`` (Default False → nur lesen + Plan).
  Lesend (0x02/0x03/0x0a/0x0e-lesen/0x14/0x15/0x05/0x08) jederzeit.
- NIE 0x50 (Config #6 schreiben + Reset) und NIE 0x06-Feeder senden
  (0x50 resettet die Box, 0x06 hat FW-Bug Maske aus r7 → immer Status 1;
  Beleg Func-Tabelle). Guard im Code + Test.
- Keine Bursts: Mechanik-Kommandos (0x10/0x11, 0x0d op 2/3) mit retries=0
  und langen Timeouts (Box antwortet erst nach dem Vorgang; ein Retransmit
  würde ihn doppelt starten; Beleg BEFUND2 §5: keine Duplikaterkennung).
  Original-Retries für den Rest via box.func_retries (0x0a Poll retries=0).
- Bus-Lock mit [k2_motor] teilen: EIN gemeinsamer RS485-Transport/Lock
  (ttyS5, 230400, Halbduplex, Lock über die ganze Transaktion, vgl.
  serial485 MAX_BUS_HOLD_S). Dieses Modul öffnet den Port NICHT selbst,
  sondern bekommt ``bus`` injiziert – dieselbe bus-Instanz wie [k2_motor].

Klipper-Einbindung (Dateien kopieren, s. BERICHT.md): ``k2_box.py`` nach
``klippy/extras/`` (als ``k2_box.py``), ``[k2_box]`` in printer.cfg.
Alle Bus-Zugriffe laufen über BoxAction (gekapselt, Tests mit Fake-Bus).
Ohne Klipper importierbar (reine Logik + Fakes, s. test_k2_box.py).

G-Codes:
- ``KTWO_BOX_STATUS`` – frischer 0x0a-Poll (lesend) + Bericht.
- ``KTWO_BOX_LOAD SLOT=A..D [DRY_RUN=1]`` – Box-Load (Extrude-Ablauf A4).
  Default: Plan ausgeben, nichts senden (ohne allow_write bzw. mit DRY_RUN).
- ``KTWO_BOX_UNLOAD SLOT=A..D [DRY_RUN=1]`` – Box-Unload (Retrude-Ablauf A5).
- ``KTWO_BOX_PRELOAD SLOT=A..D OP=0..3 [DRY_RUN=1]`` – 0x0d op (0 disable,
  1 enable, 2 Force mit RFID, 3 Force ohne RFID).

Filamentwechsel (Ergänzung 16.10.2026, Ersatz für CR_BOX_* / M8200-Kette /
Stock-Tn aus box_wrapper.so – Belege s. README-k2_box.md §8 und
config-pi/NOTIZ-cfs.md). Jeder Schritt ist einzeln aufrufbar und ein
Glied der Makrokette (config-pi/macros.cfg: T0–T15, M8200):

- ``KTWO_BOX_PRE_OPT [TNN=T1A] [Z_HOP=mm] [SOURCE=T]`` (= M8200 P /
  CR_BOX_PRE_OPT): Kette beginnen, Kopfsensor sperren, Zustand sichern,
  Z-Hop. SOURCE=T = T<n>-Befehl (nachholbar, s. unten).
- ``KTWO_BOX_CUT`` (= M8200 C / CR_BOX_CUT): Schneidfahrt
  pre_cut_pos → cut_pos_x (SAVE_CONFIG-Wert) → zurück.
- ``KTWO_BOX_GO_EXTRUDE_POS`` / ``KTWO_BOX_SAFE_POS``: in den / aus dem
  Abfallschacht (Stock BOX_GO_TO_EXTRUDE_POS / BOX_MOVE_TO_SAFE_POS).
- ``KTWO_BOX_RETRUDE [SLOT=] [LENGTH=]`` (= M8200 R): Box 0x11 p0×4 + p1,
  dazwischen Extruder-Rückzug Tn_retrude (live 3× −10 mm).
- ``KTWO_BOX_EXTRUDE TNN=T1A|SLOT=A`` (= M8200 L I0): Box-Einzug bis zum
  Kopfsensor, Stage 7 (Extruder greift), Box PRINT.
- ``KTWO_BOX_FLUSH [TNN=] [LENGTH=] [VELOCITY=] [TEMP=|BASE_TEMP=]``
  (= M8200 F): Spülen wie Stock (17.10.2026, FLUSH-BEFUND-2026-10-17.md):
  Menge farbabhängig (nachbau/flush.py, unbekannt -> 90 + 70 mm),
  Abschnitte cal_flush_list, 20 % v + 80 % 1,2·v, Temperatur/Geschwindig-
  keit aus der Material-DB (``material_database``, aus = bisher), Blasen,
  Blech nach jedem Abschnitt, Silikon-Kreise nur am Ende (README §12).
- ``KTWO_BOX_WIPE`` (Abstreifen, Stock-T0-Beispiel 10.1–10.3),
  ``KTWO_BOX_WASTE`` (= M8200 W, hier nur Hinweis),
  ``KTWO_BOX_END_OPT [RETURN=0|1]`` (= M8200 O), ``KTWO_BOX_WAIT``
  (Box-Zustand abwarten, Stock BOX_MODE_WAIT), ``KTWO_BOX_ERROR_CLEAR
  [HEAD_LOADED=1]``,
  ``KTWO_BOX_RESUME_FIX [CLEAR=1]`` (aus RESUME, Z nach Abbruch im
  Wechsel; CLEAR=1 aus PAUSE = Z steckt im Rückfahrziel, s. unten).

Abbruch im laufenden Druck (17.10.2026, config-pi/NOTIZ-reinigung-resume.md
§6a): ``PAUSE CFS_RETURN_X/Y/Z=<Druckposition vor dem Wechsel>`` statt der
Fehlerposition (z. B. am Schneidhebel X -7,8 / Y200 oder im Schacht) – das
PAUSE-Makro merkt sie als Rückfahrziel, RESUME fährt dorthin und setzt die
Upstream-PAUSE_STATE darauf (RESUME_BASE = Nullfahrt). Fahrten in den Schacht
verlassen den Schneidbereich (X < pre_cut_pos_x) zuerst nur in X. Hatte der
Wechsel schon auf die Wechsel-Temperatur geheizt (M109, max(Ziel, 220)),
bekommt PAUSE zusätzlich ``CFS_TEMP=<Druck-Zieltemperatur vor dem Wechsel>``
– das Makro merkt sie statt des aktuellen Ziels als hotend_temp, RESUME
heizt also wieder auf die Drucktemperatur (Fix 17.10.2026 spät).

RESUME nach abgebrochenem Wechsel (17.10.2026 spät, BOOTLOADER-HOST-BEFUND
§5, config-pi/NOTIZ-reinigung-resume.md §6a.7, README-k2_box.md §11):

- ``_fail`` merkt den Abbruch (Schritt, Ziel-TNN, Herkunft – Stock
  ``recode_err``/``save_resume_tnn``); Status ``change_aborted``,
  ``resume_tnn``, ``head_filament``.
- ``KTWO_BOX_RESUME_CHECK [MOVE=0|1] [TEMP=]`` (aus dem RESUME-Makro, vor
  jeder Bewegung): Kopf-Filamentsensor meldet Filament -> RESUME frei;
  LEER -> RESUME gesperrt (Druck bleibt pausiert, Meldung: T<n> in der
  Pause, dann RESUME); unbekannt (kein Sensor) -> gesperrt bis
  ``KTWO_BOX_ERROR_CLEAR HEAD_LOADED=1``. Mit ``resume_redo_change: True``
  (Default False) + beiden Freigaben + MOVE=1 holt die vorhandene Kette
  einen abgebrochenen T<n>-Wechsel nach (Stock ``error_resume_process``),
  jeder Fehler sperrt RESUME (Stock ``resume_err``).

Auto-Nachladen bei Runout (A7, 17.10.2026 – Belege/ANNAHMEN in
config-pi/NOTIZ-runout.md, README-k2_box.md §9):

- ``KTWO_BOX_RUNOUT [SLOT=A..D] [TEMP=] [DRY_RUN=1]`` (= Stock
  BOX_CHECK_MATERIAL_REFILL aus printer.cfg runout_gcode): leerer Slot ->
  Ersatz-Slot (Gleich-Material-Gruppe oder RFID gleiches Material + Farbe,
  Filament am Einlauf) -> Kette PRE_OPT/E30/EXTRUDE/FLUSH/END_OPT -> RESUME.
  Nur mit ``runout_refill`` (Default AUS) UND beiden Freigaben; sonst bzw.
  bei jedem Fehler bleibt der Druck pausiert (Meldung, nie Shutdown).
- ``KTWO_BOX_AUTO_REFILL [ENABLE=0|1]`` (= Stock BOX_ENABLE_AUTO_REFILL),
  ``KTWO_BOX_SAME_MATERIAL [LIST=T1A,T1C/T1B,T1D] [CLEAR=1]`` (= Stock
  BOX_UPDATE_SAME_MATERIAL_LIST, Format ANNAHME).

Freigaben: Box-Frames nur mit ``allow_write``, Kopf-/Extruder-Bewegung und
Heizen nur mit ``allow_move`` (beide Default False → nur Plan). Im Druck
(print_stats printing/paused) braucht ein Wechsel BEIDE Freigaben, sonst
Pause. Fehler im Druck → PAUSE (kein Shutdown, kein Druckabbruch),
Folgeschritte der Kette werden übersprungen; außerhalb eines Drucks →
G-Code-Fehler. Lange Box-Antworten (mode 0 ~4,5 s, 0x11 p1 bis 23 s) laufen
in einem Hilfs-Thread, der Reactor pausiert derweil (ReactorBusProxy).
"""

import logging
import math
import os
import struct
import threading
import time

# Import-Regel (Fix 16.10.2026, s. k2_transport.py): eigener Ordner NIE in
# sys.path; Geschwister relativ (Klipper-Paket extras), Fallback absolut für
# die Offline-Tests; nachbau/ hängt k2_transport.ensure_nachbau_path() an.
try:
    from . import k2_transport as _K2T
except ImportError:
    import k2_transport as _K2T
_NACHBAU = _K2T.ensure_nachbau_path()

try:
    import box as _BOX  # noqa: F401
    _K2T.check_nachbau_module(_BOX)
    from box import (BoxAction, CfsStatusError, StatusBytes,  # noqa: F401
                     func_retries, preload_force_timeout, status_name,
                     parse_box_state, get_rfid, get_remain_len,
                     get_measuring_wheel, parse_version_sn,
                     parse_buffer_state, parse_filament_sensor_state,
                     parse_get_hardware_status, get_box_status,
                     BOX_STATE_NAMES, BOX_STATE_IDLE, BOX_STATE_ERROR,
                     PRELOAD_DISABLE, PRELOAD_ENABLE, PRELOAD_FORCE_READ,
                     PRELOAD_FORCE_TENSION_ONLY)
    _HAVE_BOX = True
except Exception as _e:  # pragma: no cover
    raise ImportError("nachbau/box.py wird benötigt (gleicher Stil wie "
                      "k2_motor_init.py -> k2_transport.py): %s" % _e)

try:
    import cfsseq as _SEQ  # noqa: F401
    _K2T.check_nachbau_module(_SEQ)
    from cfsseq import (slot_to_mask, RETRUDE_TO_BUFFER_REPEATS,  # noqa: F401
                        EXTRUDE_MODE_STAGE1, EXTRUDE_MODE_FEED_START,
                        EXTRUDE_MODE_QUERY, EXTRUDE_MODE_STOP_BUFFER,
                        EXTRUDE_MODE_EXTRUDER_CHECK, EXTRUDER_CHECK_MM,
                        RETRUDE_P_TO_BUFFER, RETRUDE_P_PULL_HUB,
                        EXTRUDE_LIVE_CMDS_B, RETRUDE_LIVE_CMDS_A,
                        build_extrude_sequence, build_retrude_sequence,
                        requests_from_log_cmds, filter_funs)
    _HAVE_SEQ = True
except Exception as _e:  # pragma: no cover
    raise ImportError("nachbau/cfsseq.py wird benötigt: %s" % _e)

try:
    import auto_addr as _AA  # noqa: F401
    _K2T.check_nachbau_module(_AA)
    _HAVE_AA = True
except Exception as _e:  # pragma: no cover
    raise ImportError("nachbau/auto_addr.py wird benötigt: %s" % _e)

try:
    import serial485 as _S485  # noqa: F401
    _K2T.check_nachbau_module(_S485)
    _HAVE_S485 = True
except Exception:  # pragma: no cover
    _S485 = None
    _HAVE_S485 = False

# Spül-Rechnung + Material-DB (17.10.2026, FLUSH-BEFUND-2026-10-17.md,
# PAUSE-RESUME-MATERIAL-BEFUND-2026-10-17.md). WEICH importiert: fehlen sie
# (alte Installation), gilt das bisherige Verhalten (feste Spülmenge
# [90, 70], max(Ziel, Tn_extrude_temp), feste Geschwindigkeiten) + Meldung.
try:
    import flush as _FLUSH  # noqa: F401
    _K2T.check_nachbau_module(_FLUSH)
except Exception:  # pragma: no cover
    _FLUSH = None
try:
    import material_db as _MDB  # noqa: F401
    _K2T.check_nachbau_module(_MDB)
except Exception:  # pragma: no cover
    _MDB = None

# =====================================================================
# Konstanten (Belege s. Docstring; Werte aus box.py/cfsseq.py wiederverwendet)
# =====================================================================

BOX_ADDR_DEFAULT = _BOX.BOX_ADDR  # 0x01 (Beleg PROTOKOLL-485.md §2)
SLOT_LETTERS = "ABCD"
SLOT_MASKS = (0x01, 0x02, 0x04, 0x08)
MASK_ALL = 0x0F

# NIE senden (Guard, Beleg PROTOKOLL-485.md Func-Tabelle + §6.9):
# - 0x50 schreibt Config #6 + speichert + resettet (gleicher Wert -> Status 3).
# - 0x06 Feeder hat FW-Bug (Maske aus r7 = Tick-Wert, praktisch immer Status 1).
FORBIDDEN_FUNS = (0x50, 0x06)

FUNC_GET_BOX_STATE = _BOX.FUNC_GET_BOX_STATE  # 0x0a
FUNC_SET_BOX_MODE = _BOX.FUNC_SET_BOX_MODE  # 0x04
FUNC_PRELOAD_INHIBIT = _BOX.FUNC_PRELOAD_INHIBIT  # 0x0f
FUNC_EXTRUDE = _BOX.FUNC_EXTRUDE  # 0x10
FUNC_RETRUDE = _BOX.FUNC_RETRUDE  # 0x11

POLL_INTERVAL_DEFAULT = 4.0  # s, Original-Poll alle ~4 s (Beleg §6.2, 10 560 Polls)
ODO_POLLS_DEFAULT = 5  # Auftrag A4: 5 Odometer-Polls (Beleg EXTRUDE_LIVE_CMDS_B)
RETRUDE_REPEATS_DEFAULT = RETRUDE_TO_BUFFER_REPEATS  # 4 (Beleg §6.5, 16 von 26)

PRELOAD_OP_NAMES = {
    PRELOAD_DISABLE: "disable (Insert-Reading/Preload aus)",
    PRELOAD_ENABLE: "enable (Insert-Reading/Preload an)",
    PRELOAD_FORCE_READ: "force-read (Forced Preload MIT RFID)",
    PRELOAD_FORCE_TENSION_ONLY: "tension-only (Forced Preload OHNE RFID)",
}


# ---------------------------------------------------------------------
# Filamentwechsel: Stock-Werte (Optionen im Stock-Stil, Namen wie [box])
# ---------------------------------------------------------------------
# Belege: live-logs-2026-10-16/config-aktuell/box.cfg ([box]-Abschnitt +
# kommentiertes T0-Beispiel), printer.cfg SAVE_CONFIG ``[box] cut_pos_x =
# -7.80`` (Cut-Kalibrierung 16.10.2026, LIVE-ABFRAGEN-2026-10-16.md),
# Live-Wechsel klippy.log.2026-10-12 00:24:49–00:26:35 (Unload+Load T1A:
# 3x "extrude = -10.0, velocity (600,)", "length: [90, 70]", "extrude =
# 160, velocity 360, temp 220"), klippy.log.2026-10-13 02:31 ("timeout =
# 90" beim Einzug, stage7). Kleinbuchstaben, weil Klipper Optionsnamen
# klein schreibt (Stock "Tn_retrude" -> "tn_retrude").
CHANGE_DEFAULTS = {
    # Schneiden (box.cfg + SAVE_CONFIG; T0-Beispiel Schritt 3-5)
    "pre_cut_pos_x": 10.0,
    "pre_cut_pos_y": 200.0,
    "cut_pos_x": -7.80,          # SAVE_CONFIG-Wert (kalibriert 16.10.2026)
    "cut_pos_y": 200.0,
    "cut_velocity": 30000.0,     # mm/min
    "cut_accel": 5000.0,         # T0-Beispiel SET_VELOCITY_LIMIT ACCEL=5000
    # ANNAHME: kurz am Anschlag verweilen. Batch-Lauf 16.10.2026: mit
    # Input-Shaper (mzv 42 Hz) kehrt der Kopf ohne Halt schon bei X -7,69
    # statt -7,80 um (dekodierte MCU-Schritte, upstream-test-cfs).
    "cut_dwell": 0.1,            # s
    "check_cut_pos_x_min": -9.5,
    "check_cut_pos_x_max": -5.5,
    # Extruder (box.cfg)
    "tn_retrude": -10.0,         # mm je Schritt (live 3x)
    "tn_retrude_velocity": 600.0,
    "tn_extrude_temp": 220.0,    # Fallback-Temperatur (Stock Tn_extrude_temp)
    # Stock-Fallback der Spülmenge ohne bekannte Farben (get_flush_len gibt
    # Tn_extrude ROH zurück, cal_flush_list(140) = [90, 70], FLUSH-BEFUND §2)
    "tn_extrude": 140.0,
    "tn_extrude_velocity": 360.0,
    "buffer_empty_len": 30.0,    # Schneide -> Extruderzahnrad
    # Schacht / Abstreifer (box.cfg + T0-Beispiel Schritt 6, 10, 11)
    "extrude_pos_x": 133.0,
    "extrude_pos_y": 378.0,
    "extrude_entry_pos_x": 139.0,  # T0-Beispiel 6.1 "G0 X139"
    "safe_pos_x": 225.0,
    "safe_pos_y": 345.0,
    "clean_left_pos_x": 135.0,
    "clean_left_pos_y": 378.0,
    "clean_right_pos_x": 160.0,
    "clean_right_pos_y": 378.0,
    "clean_velocity": 12000.0,
    "clean_pos_left_x": 160.0,
    "clean_pos_right_x": 170.0,
    "clean_pos_middle_y": 374.0,
    "wipe_circle_radius": 4.0,   # T0-Beispiel G2 I4 / G3 I-4
    "wipe_circle_velocity": 10000.0,
    "wipe_accel": 5000.0,
    "wipe_shakes": 3,            # T0-Beispiel 10.3 (3x 160 <-> 133)
    "box_first_clean_length": 90.0,  # live "length: [90, 70]" (mockconfig 90)
    "box_need_clean_length": 70.0,
    # Spülen wie Stock (FLUSH-BEFUND-2026-10-17.md §2/§4/§6, BoxCfg-Defaults
    # §7.3): cal_flush_list-Höchstlänge je Abschnitt, Düsenvolumen, Faktoren
    "box_need_clean_length_max": 100.0,
    "nozzle_volume": 183.0,
    "flush_multiplier": 1.0,
    "diff_material_type_min_flush_len": 370.0,
    "flush_velocity_factor": 1.2,    # 80 %-Teil je Abschnitt (außer letzter)
    "max_temp_flush_times": 3,
    "baseline_nozzle_diameter": 0.4,
    # Bauteillüfter beim Spülen (Stock M106 P0 S50 + blow: fan0 5 s voll)
    "flush_fan_start": 50.0,         # M106 P0 S<..> zu Beginn
    "flush_blow_time": 5.0,          # s
    # Reinigung (Stock nozzle_clean): Kreis-Startversatz monotonic() % 3,
    # SET_VELOCITY_LIMIT ACCEL=5000 ACCEL_TO_DECEL=5000 -> Upstream-
    # Äquivalent MINIMUM_CRUISE_RATIO=0 (accel_to_decel == accel)
    "flush_wipe_offset": 3.0,        # mm, 0 = immer dieselbe Stelle
    "wipe_minimum_cruise_ratio": 0.0,
    # Verstopfungsprüfung (Stock key845, |Messrad-Summe| < |diff_length|)
    "flush_clog_min_mm": 10.0,
    "travel_velocity": 12000.0,  # T0-Beispiel F12000
    "z_velocity": 600.0,         # T0-Beispiel G0 Z.. F600
    "change_z_hop": 0.4,         # T0-Beispiel "G0 Z50.4" bei Z50
    # Wartezeiten (live belegt bzw. ANNAHME, s. NOTIZ-cfs.md)
    "extrude_sensor_timeout": 90.0,  # live "timeout = 90"
    "extrude_poll_interval": 2.0,    # live 0x10 mode 5 alle ~2 s
    "stage7_delay": 2.0,             # ANNAHME (live 3,7-3,8 s bis mode 7)
    # = preload_force_timeout(1 Slot) 380 s (BOOTLOADER-HOST-NACHTRAG N6:
    # Box-Auto-Preload 1 Slot bis 364 s; vorher 200 s = zu kurz)
    "box_wait_timeout": float(_BOX.preload_force_timeout(0x01)),
    "box_wait_interval": 1.0,
    # Auto-Nachladen bei Runout (A7, config-pi/NOTIZ-runout.md): Stock-
    # printer.cfg runout_gcode "G91 / G0 E30 F600 / G90" (nur wenn heiß) –
    # Restfilament durch die Zahnräder (= buffer_empty_len 30), hier im
    # Abfallschacht statt am Parkpunkt.
    "runout_tail_length": 30.0,
    "runout_tail_velocity": 600.0,
}
CHANGE_INT_KEYS = ("wipe_shakes", "max_temp_flush_times")
FLUSH_MODES = ("color", "fixed")
FLUSH_CLEAN_MODES = ("stock", "legacy")
# Stock-Fallback der RESUME-Vorextrusion ohne Materialdaten:
# [filament_rack] velocity 360 (OHNE ·60) · 0,8 = 288 mm/min (B.3/B.5)
PRIME_FALLBACK_VELOCITY = 360.0
TEMP_LIMIT_MARGIN = 10.0  # K unter [extruder] max_temp (DB-Werte kappen)

# Box-Fehler, die nach einem Runout erwartet und vor dem Nachladen per
# 0x04 [00 01] (IDLE, löscht ERROR – live belegt) quittiert werden dürfen:
# 0x50 Runout im Druck-Slot, 0x51 Buffer-Nachschub zu langsam, 0x52 kein
# Vorschub (BEFUND2 §4: print_check-Thread, „außer es lag schon ein Runout
# (0x50) vor“). ANNAHME: Stock quittiert sie beim Auto-Nachladen ebenso.
RUNOUT_CLEARABLE_ERRORS = (_BOX.STATUS_RUNOUT, _BOX.STATUS_SPEED_ERR,
                           _BOX.STATUS_NO_FEED)

CHAIN_STATE_NAME = "_ktwo_box_chain"
STEP_STATE_NAME = "_ktwo_box_step"


class BoxChangeError(RuntimeError):
    """Wechsel-Fehler ohne Box-Statuscode (Timeout, Sensor, Zustand)."""


def tnn_from_index(index):
    """Werkzeug-Index 0..15 -> TNN wie Stock-M8200 L (box.cfg):
    addr = I/4 + 1, Slot = 'ABCD'[I % 4] -> 0 = T1A, 1 = T1B, 4 = T2A,
    15 = T4D (Beleg box.cfg + PROTOKOLL-485.md „M8200-Sequenzen“)."""
    if isinstance(index, bool):
        raise ValueError("Werkzeug-Index muss 0..15 sein: %r" % (index,))
    try:
        i = int(str(index).strip().lstrip("="))
    except ValueError:
        raise ValueError("Werkzeug-Index muss 0..15 sein: %r" % (index,))
    if not 0 <= i <= 15:
        raise ValueError("Werkzeug-Index muss 0..15 sein: %r" % (index,))
    return "T%d%s" % (i // 4 + 1, SLOT_LETTERS[i % 4])


def parse_tnn(tnn):
    """'T1A'..'T4D' -> (box_addr 1..4, slot_index 0..3, letter, 'T1A')."""
    s = str(tnn).strip().upper().strip('"').strip("'")
    if (len(s) == 3 and s[0] == "T" and s[1] in "1234"
            and s[2] in SLOT_LETTERS):
        idx = SLOT_LETTERS.index(s[2])
        return int(s[1]), idx, s[2], s
    raise ValueError("TNN muss T1A..T4D sein (Box 1-4, Slot A-D): %r" % (tnn,))


def _n(value):
    """Zahl für G-Code (max. 3 Nachkommastellen, ohne Nullen)."""
    txt = ("%.3f" % float(value)).rstrip("0").rstrip(".")
    return "0" if txt in ("-0", "") else txt


def gcode_extruder_move(length, velocity):
    """Relativer Extruder-Schritt (G91/G90 wie Stock-QUIT_MATERIAL)."""
    return ["G91", "G1 E%s F%s" % (_n(length), _n(velocity)), "G90"]


def gcode_go_extrude_pos(c):
    """BOX_GO_TO_EXTRUDE_POS (Stock-T0-Beispiel Schritt 6–6.3):
    G0 Y345 F12000 / G0 X139 / G0 Y378 / G0 X133."""
    f = _n(c["travel_velocity"])
    return ["G90",
            "G0 Y%s F%s" % (_n(c["safe_pos_y"]), f),
            "G0 X%s F%s" % (_n(c["extrude_entry_pos_x"]), f),
            "G0 Y%s F%s" % (_n(c["extrude_pos_y"]), f),
            "G0 X%s F%s" % (_n(c["extrude_pos_x"]), f)]


def gcode_leave_cut_zone(c, pos):
    """Kopf im Schneidbereich (X < pre_cut_pos_x, Hebel-Anschlag links, z. B.
    nach einem abgebrochenen Schnitt): zuerst NUR X auf pre_cut_pos_x, keine
    Y-Bewegung in Hebelnähe. Beleg Stock box.cfg:13 ``pre_cut_pos_x: 10
    #预撞刀的位置，保证垂直撞刀`` (Vor-Schneidposition, Hebel senkrecht
    anfahren) und Rückweg des Schnitts ``G0 X10`` + ``M400`` (gcode_cut,
    live „cut to return OK“); erst danach Y (T0-Beispiel 6.: ``G0 Y345`` bei
    X10). Das M400 hält dort an: ohne Halt zöge der Input-Shaper die folgende
    Y-Fahrt schon vor X10 an (Batch resumecut: 0,03 mm Y bei X < 9,4).
    Sonst [] (pos None = unbekannt -> nichts)."""
    if pos is None or float(pos[0]) >= float(c["pre_cut_pos_x"]) - 0.01:
        return []
    return ["G0 X%s F%s" % (_n(c["pre_cut_pos_x"]), _n(c["travel_velocity"])),
            "M400"]


def gcode_safe_pos(c):
    """Schacht verlassen (Stock-T0-Beispiel Schritt 11): G0 X160 / G0 Y345."""
    f = _n(c["travel_velocity"])
    return ["G90",
            "G0 X%s F%s" % (_n(c["clean_right_pos_x"]), f),
            "G0 Y%s F%s" % (_n(c["safe_pos_y"]), f)]


def gcode_cut(c, restore_accel=None):
    """Schneidfahrt (T0-Beispiel Schritt 3–5 + CR_BOX_CUT):
    Vor-Schneidposition X10 Y200 (F12000) → ACCEL 5000 → Hebel gegen den
    Anschlag fahren bis cut_pos_x (SAVE_CONFIG -7.80) mit cut_velocity →
    G4 cut_dwell (ANNAHME, s. CHANGE_DEFAULTS) → zurück auf pre_cut_pos_x →
    Beschleunigung zurück → M400.
    ANNAHME: Rückweg mit travel_velocity (Stock-Geschwindigkeit unbelegt)."""
    f = _n(c["travel_velocity"])
    lines = ["G90",
             "G0 X%s Y%s F%s" % (_n(c["pre_cut_pos_x"]),
                                 _n(c["pre_cut_pos_y"]), f),
             "SET_VELOCITY_LIMIT ACCEL=%s" % _n(c["cut_accel"]),
             "G0 X%s Y%s F%s" % (_n(c["cut_pos_x"]), _n(c["cut_pos_y"]),
                                 _n(c["cut_velocity"]))]
    if c.get("cut_dwell", 0) > 0:
        lines.append("G4 P%s" % _n(float(c["cut_dwell"]) * 1000.0))
    lines.append("G0 X%s F%s" % (_n(c["pre_cut_pos_x"]), f))
    if restore_accel is not None:
        lines.append("SET_VELOCITY_LIMIT ACCEL=%s" % _n(restore_accel))
    lines.append("M400")
    return lines


def gcode_circle(start_x, start_y, center_dx, radius, clockwise, feed,
                 segments=16):
    """Vollkreis als G1-Polygon (Ersatz für Stock "G2/G3 I±4 J0 P1"; Upstream-
    gcode_arcs kennt P nicht und Start == Ziel ist dort mehrdeutig)."""
    cx = start_x + center_dx
    a0 = math.atan2(0.0, start_x - cx)
    out = []
    for i in range(1, segments + 1):
        step = 2.0 * math.pi * i / segments
        a = a0 - step if clockwise else a0 + step
        out.append("G1 X%s Y%s F%s" % (_n(cx + radius * math.cos(a)),
                                       _n(start_y + radius * math.sin(a)),
                                       _n(feed)))
    return out


def gcode_wipe(c, restore_accel=None):
    """Abstreifen am Schacht (Stock-T0-Beispiel 10.1–10.3):
    Blech-Schnitt X160→X135 (Y378), Kreis am Silikon um (164|374) und
    (166|374), dann 3x Schütteln X160↔X133 auf Y378 (endet an extrude_pos)."""
    cv = _n(c["clean_velocity"])
    r = float(c["wipe_circle_radius"])
    my = c["clean_pos_middle_y"]
    lines = ["G90", "SET_VELOCITY_LIMIT ACCEL=%s" % _n(c["wipe_accel"]),
             "G0 X%s F%s" % (_n(c["clean_right_pos_x"]), cv),
             "G0 X%s F%s" % (_n(c["clean_left_pos_x"]), cv),
             "G0 X%s Y%s F%s" % (_n(c["clean_pos_left_x"]), _n(my), cv)]
    lines += gcode_circle(c["clean_pos_left_x"], my, r, r, True,
                          c["wipe_circle_velocity"])
    lines.append("G0 X%s Y%s F%s" % (_n(c["clean_pos_right_x"]), _n(my), cv))
    lines += gcode_circle(c["clean_pos_right_x"], my, -r, r, False,
                          c["wipe_circle_velocity"])
    for _ in range(int(c["wipe_shakes"])):
        lines.append("G0 X%s Y%s F%s" % (_n(c["clean_right_pos_x"]),
                                         _n(c["clean_right_pos_y"]), cv))
        lines.append("G0 X%s Y%s F%s" % (_n(c["extrude_pos_x"]),
                                         _n(c["extrude_pos_y"]), cv))
    if restore_accel is not None:
        lines.append("SET_VELOCITY_LIMIT ACCEL=%s" % _n(restore_accel))
    return lines


def flush_segments(c, length=None):
    """Spül-Abschnitte: live [box_first_clean_length, box_need_clean_length]
    = [90, 70] (Summe 160 = "extrude = 160"). LENGTH > 0 ersetzt das durch
    Abschnitte von höchstens box_first_clean_length (ANNAHME)."""
    if length is None:
        segs = [float(c["box_first_clean_length"]),
                float(c["box_need_clean_length"])]
        return [s for s in segs if s > 0]
    total = float(length)
    if total <= 0:
        return []
    size = max(float(c["box_first_clean_length"]), 1.0)
    out = []
    while total > 1e-9:
        part = min(size, total)
        out.append(part)
        total -= part
    return out


def gcode_flush_segment(length, velocity, factor=1.0):
    """Ein Spül-Abschnitt, live 20 %/80 % geteilt (18+72 bzw. 14+56 mm);
    der 80 %-Teil mit velocity·factor (Stock flush_velocity_factor 1.2)."""
    first = round(float(length) * 0.2, 3)
    return ["G91",
            "G1 E%s F%s" % (_n(first), _n(velocity)),
            "G1 E%s F%s" % (_n(float(length) - first),
                            _n(float(velocity) * float(factor))),
            "G90"]


# ---------------------------------------------------------------------
# Material-DB + Spülen wie Stock (17.10.2026) – reine Bausteine
# ---------------------------------------------------------------------
# Belege: FLUSH-BEFUND-2026-10-17.md (§2 Menge, §4 Geschwindigkeit, §5
# Temperatur, §6 Abschnitte/Ablauf, §7 Reinigung/Blasen) und
# PAUSE-RESUME-MATERIAL-BEFUND-2026-10-17.md §B (DB-Felder). Rechnung über
# nachbau/flush.py bzw. nachbau/material_db.py (nur importiert).

def load_material_db(path):
    """material_database.json laden -> (MaterialDB oder None, Meldung).

    Leerer Pfad = aus (None, ""). Fehlende Datei/kaputtes JSON/fehlendes
    material_db-Modul -> None + Meldung; der Aufrufer nutzt dann das
    bisherige Verhalten (Tn_extrude_temp, feste Geschwindigkeiten)."""
    raw = str(path or "").strip()
    if not raw:
        return None, ""
    p = os.path.expanduser(raw)
    if _MDB is None:
        return None, ("Material-DB %s: nachbau/material_db.py fehlt – "
                      "Fallback Tn_extrude_temp/feste Geschwindigkeiten" % p)
    try:
        db = _MDB.MaterialDB.load(p)
    except Exception as exc:
        return None, ("Material-DB %s nicht ladbar (%s) – Fallback "
                      "Tn_extrude_temp/feste Geschwindigkeiten" % (
                          p, exc.__class__.__name__ + ": " + str(exc)))
    return db, "Material-DB %s geladen (%d Einträge)" % (p, len(db))


def material_params(db, key, fallback_temp):
    """Stock-Abfragen für einen Material-Schlüssel (RFID mat_id, 6-stellig,
    DB-ID = mat_id[1:6], material_db.db_id_from_key) -> dict.

    known = Eintrag gefunden; temp/max_temp = int(kvParam.nozzle_temperature)
    bzw. int(base.maxTemp), nicht lesbar/unplausibel -> fallback_temp (Stock
    Tn_extrude_temp 220, temp_known/max_known False); vol =
    kvParam.filament_max_volumetric_speed (mm³/s) oder None; mtype =
    base.meterialType; name nur für Meldungen."""
    fb = float(fallback_temp)
    out = {"key": key, "known": False, "temp": fb, "temp_known": False,
           "max_temp": fb, "max_known": False, "vol": None, "mtype": None,
           "name": None}
    if db is None or not key:
        return out
    entry = db.find(key)
    if entry is None:
        return out
    out["known"] = True

    def _plaus(t):
        return t is not None and 100 <= t <= 450

    t = db.nozzle_temp(key, fallback=None)
    if _plaus(t):
        out["temp"], out["temp_known"] = float(t), True
    mt = db.max_temp(key, fallback=None)
    if _plaus(mt):
        out["max_temp"], out["max_known"] = float(mt), True
    vol = db.max_volumetric_speed(key)
    if vol is not None and vol > 0:
        out["vol"] = float(vol)
    out["mtype"] = db.material_type(key)
    try:
        nm = entry.get("base", {}).get("name")
        out["name"] = nm if isinstance(nm, str) else None
    except Exception:
        out["name"] = None
    return out


def flush_cfg(c):
    """k2_box-Optionen -> Schlüssel von nachbau/flush.py (Stock-[box]-Namen)."""
    return {
        "nozzle_volume": float(c["nozzle_volume"]),
        "flush_multiplier": float(c["flush_multiplier"]),
        "diff_material_type_min_flush_len":
            float(c["diff_material_type_min_flush_len"]),
        "box_first_clean_length": float(c["box_first_clean_length"]),
        "box_need_clean_length": float(c["box_need_clean_length"]),
        "box_need_clean_length_max": float(c["box_need_clean_length_max"]),
        "Tn_extrude": float(c["tn_extrude"]),
        "Tn_extrude_velocity": [float(c["tn_extrude_velocity"])],
        "max_temp_flush_times": int(c["max_temp_flush_times"]),
        "flush_velocity_factor": float(c["flush_velocity_factor"]),
    }


def plan_flush_lengths(c, length=None, src_color=None, dst_color=None,
                       different=False):
    """Spül-Abschnitte -> (Liste mm, Begründung).

    flush_mode color (Stock MCMBW.get_flush_len + BoxAction.get_flush_len +
    cal_flush_list): Farbvolumen get_flushing_volume(alt, neu) + nozzle_volume
    -> /2,4 · flush_multiplier; anderes Material ×2, mindestens
    diff_material_type_min_flush_len (370); sonst mindestens box_first +
    box_need (160). Farbe alt oder neu unbekannt -> Tn_extrude ROH (140,
    Stock) -> cal_flush_list -> [90, 70] = bisheriges Ergebnis. LENGTH> 0 ->
    cal_flush_list(LENGTH) (Stock teilt jede Länge so).
    flush_mode fixed (bzw. flush.py fehlt / box_*_clean_length 0): wie bis
    17.10. (flush_segments)."""
    mode = c.get("flush_mode", "color")
    first = float(c["box_first_clean_length"])
    need = float(c["box_need_clean_length"])
    if mode != "color" or _FLUSH is None or first <= 0 or need <= 0:
        if mode != "color":
            why = "flush_mode fixed"
        elif _FLUSH is None:
            why = "nachbau/flush.py fehlt – fest"
        else:
            why = "box_first/need_clean_length 0 – fest"
        return flush_segments(c, length), why
    fc = flush_cfg(c)
    if length is not None:
        if float(length) <= 0:
            return [], "LENGTH=0"
        return ([float(x) for x in _FLUSH.cal_flush_list(float(length), fc)],
                "LENGTH=%s (cal_flush_list)" % _n(length))

    def _parse(cv):
        try:
            return _FLUSH.parse_color_value(cv)
        except (TypeError, ValueError):  # Stock convert_scv -> False
            return None

    scv, tcv = _parse(src_color), _parse(dst_color)
    if scv is None or tcv is None:
        total = fc["Tn_extrude"]
        why = ("Farbe %s unbekannt -> Tn_extrude %s mm (Stock-Fallback)" % (
            "alt" if scv is None else "neu", _n(total)))
    else:
        vol, fl = _FLUSH.color_flush_len(scv, tcv, fc)
        total = _FLUSH.finish_flush_len(fl, bool(different), fc)
        why = ("Farbe %s -> %s: Volumen %d mm³ + Düse %s -> %s mm%s -> %s mm"
               % (src_color, dst_color, vol, _n(fc["nozzle_volume"]), _n(fl),
                  " (anderes Material: ×2, min %s)" % _n(
                      fc["diff_material_type_min_flush_len"])
                  if different else "", _n(total)))
    return [float(x) for x in _FLUSH.cal_flush_list(float(total), fc)], why


def flush_velocity_base(c, vol=None, nozzle=None):
    """Stock get_flush_velocity: min(360, vol/2,4·60) (vol = Material-
    max_volumetric_speed; ohne -> Tn_extrude_velocity) · Düse/0,4 -> mm/min."""
    base = float(c["baseline_nozzle_diameter"])
    nz = float(nozzle) if nozzle else base
    if _FLUSH is None:
        return float(c["tn_extrude_velocity"])
    v, _p = _FLUSH.flush_velocity(vol, nz, base, cfg=flush_cfg(c))
    return float(v[0])


def flush_segment_lines(c, seg, i, n, velocity, temp, max_temp):
    """Ein Spül-Abschnitt wie Stock check_flush_temp_and_extrude (Z.1336–
    1359, Rechnung flush.segment_plan): Index = i, letzter Abschnitt (n > 1)
    = max_temp_flush_times; Index 0 + max_temp > temp: M104 S<max> +
    TEMPERATURE_WAIT MINIMUM=<max-5> (Stock quickly_wait_heating(max-5),
    Upstream-M109 würde beim Abkühlen warten); Index == max_temp_flush_times
    -> M104 S<temp> („restore target temp“) und Faktor 1, sonst
    20 % · v + 80 % · v · flush_velocity_factor."""
    fc = flush_cfg(c)
    idx = fc["max_temp_flush_times"] if (i != 0 and i == n - 1) else i
    boost = float(max_temp) > float(temp) + 0.01
    if _FLUSH is not None:
        steps = _FLUSH.segment_plan(float(seg), idx, float(temp),
                                    float(max_temp), [float(velocity)], fc)
    else:  # pragma: no cover - bisheriges Profil
        steps = [("E", float(seg) * 0.2, float(velocity)),
                 ("E", float(seg) * 0.8, float(velocity))]
    lines, moves = [], []
    for st in steps:
        if st[0] == "WAIT":
            if boost:
                lines += ["M104 S%s" % _n(max_temp),
                          "TEMPERATURE_WAIT SENSOR=extruder MINIMUM=%s"
                          % _n(float(max_temp) - 5.0)]
        elif st[0] == "M104":
            if boost and abs(float(st[1]) - float(max_temp)) > 0.01:
                lines.append("M104 S%s" % _n(st[1]))
        else:
            moves.append("G1 E%s F%s" % (_n(st[1]), _n(st[2])))
    return lines + ["G91"] + moves + ["G90"]


def gcode_blow(fan, seconds, restore_value, full=255.0):
    """Stock BoxAction.blow (Z.2733–2740): fan0 5 s voll, dann alter Wert."""
    return ["SET_PIN PIN=%s VALUE=%s" % (fan, _n(full)),
            "G4 P%s" % _n(float(seconds) * 1000.0), "M400",
            "SET_PIN PIN=%s VALUE=%s" % (fan, _n(restore_value))]


def _velocity_limit(accel, mcr=None):
    line = "SET_VELOCITY_LIMIT ACCEL=%s" % _n(accel)
    if mcr is not None:
        line += " MINIMUM_CRUISE_RATIO=%s" % _n(mcr)
    return line


def gcode_nozzle_clean(c, circle, offset=0.0, restore_accel=None,
                       restore_mcr=None, arcs=True):
    """Stock BoxAction.nozzle_clean(circle) (Z.3149–3182, FLUSH-BEFUND §7.1)
    ab extrude_pos: ACCEL wipe_accel (+ MINIMUM_CRUISE_RATIO, Upstream-
    Ersatz für ACCEL_TO_DECEL=ACCEL, nur wenn der alte Wert bekannt ist) ->
    Blech X clean_pos_right_x (170) -> X clean_left_pos_x (135) -> nur mit
    circle: Silikon-Kreise ab X clean_pos_left_x + offset bzw.
    clean_pos_right_x − offset (Y clean_pos_middle_y, G2 I+r / G3 I−r J0;
    ohne [gcode_arcs] 16-Eck) -> 3× X clean_right_pos_x/Y clean_right_pos_y
    <-> X extrude_pos_x/Y clean_left_pos_y -> nur mit circle: Schacht
    verlassen X clean_right_pos_x -> Y safe_pos_y -> M400 -> alte Werte.
    Ohne circle bleibt die Düse im Schacht (extrude_pos). Der Stock-Zweig
    „Kopf außerhalb (Y < safe_pos_y)“ entfällt: k2_box fährt vorher immer
    in den Schacht."""
    cv = _n(c["clean_velocity"])
    r = float(c["wipe_circle_radius"])
    wv = c["wipe_circle_velocity"]
    my = c["clean_pos_middle_y"]
    mcr = c.get("wipe_minimum_cruise_ratio") if restore_mcr is not None \
        else None
    lines = ["G90", _velocity_limit(c["wipe_accel"], mcr),
             "G0 X%s F%s" % (_n(c["clean_pos_right_x"]), cv),
             "G0 X%s F%s" % (_n(c["clean_left_pos_x"]), cv)]
    if circle:
        sx = float(c["clean_pos_left_x"]) + float(offset)
        ex = float(c["clean_pos_right_x"]) - float(offset)
        lines.append("G0 X%s Y%s F%s" % (_n(sx), _n(my),
                                         _n(c["travel_velocity"])))
        if arcs:
            lines.append("G2 I%s J0 F%s" % (_n(r), _n(wv)))
        else:
            lines += gcode_circle(sx, my, r, r, True, wv)
        lines.append("G0 X%s Y%s F%s" % (_n(ex), _n(my), _n(wv)))
        if arcs:
            lines.append("G3 I%s J0 F%s" % (_n(-r), _n(wv)))
        else:
            lines += gcode_circle(ex, my, -r, r, False, wv)
    for _ in range(int(c["wipe_shakes"])):
        lines.append("G0 X%s Y%s F%s" % (_n(c["clean_right_pos_x"]),
                                         _n(c["clean_right_pos_y"]), cv))
        lines.append("G0 X%s Y%s F%s" % (_n(c["extrude_pos_x"]),
                                         _n(c["clean_left_pos_y"]), cv))
    if circle:
        lines.append("G0 X%s F%s" % (_n(c["clean_right_pos_x"]), cv))
        lines.append("G0 Y%s F%s" % (_n(c["safe_pos_y"]), cv))
    lines.append("M400")
    if restore_accel is not None:
        lines.append(_velocity_limit(restore_accel, restore_mcr))
    return lines


def preload_block_reason(op, mask, head, printing, force=False):
    """Stock-Schutz von communication_set_pre_loading (BOOTLOADER-HOST-
    BEFUND §3, NACHTRAG N2/N3) -> None = senden erlaubt, sonst Klartext.
    - op 2/3 mit Maske 0: Fehlgebrauch (NUM als Slotindex, [00 02]-Bursts).
    - Druck läuft/pausiert: nichts senden, außer force (Stock force=True).
    - op 2/3 nur bei LEEREM Kopf-Filamentsensor (head False); Filament oder
      unbekannt (kein Sensor) -> nein (Stock: get_filament_sensor_detect)."""
    force_op = op in (PRELOAD_FORCE_READ, PRELOAD_FORCE_TENSION_ONLY)
    if force_op and int(mask) == 0:
        return ("Maske 0 bei op %d abgelehnt (Fehlgebrauch NUM=0 als Slot – "
                "Stock-[00 02]-Bursts, NACHTRAG N2)" % op)
    if printing and not force:
        return ("Druck läuft/pausiert – Preload nur mit FORCE=1 (Stock "
                "force=True)")
    if force_op and head is not False:
        return ("op %d nur bei leerem Kopf-Filamentsensor (Kopfsensor %s; "
                "Stock sendet bei Filament im Kopf nicht)" % (
                    op, "meldet Filament" if head else "unbekannt"))
    return None


def retrude_extruder_steps(c, length=None):
    """Extruder-Rückzug je 0x11-p0 (live 3x -10 mm = buffer_empty_len 30).
    LENGTH (Stock "M8200 R E…"/CR_BOX_RETRUDE LENGTH) = Gesamtlänge,
    aufgeteilt in |tn_retrude|-Schritte (ANNAHME)."""
    step = abs(float(c["tn_retrude"]))
    total = abs(float(c["buffer_empty_len"] if length is None else length))
    if step <= 0 or total <= 0:
        return []
    out = []
    while total > 1e-9:
        part = min(step, total)
        out.append(-part)
        total -= part
    return out


# ---------------------------------------------------------------------
# Auto-Nachladen bei Runout (A7) – reine Bausteine
# ---------------------------------------------------------------------
# Stock (box_wrapper.so, nur Strings/Status belegt, Ablauf unbelegt):
# BOX_CHECK_MATERIAL_REFILL -> check_material_refill / material_auto_refill /
# is_material_available; Ersatz kommt aus der Gleich-Material-Liste
# (BOX_UPDATE_SAME_MATERIAL_LIST, Status 'same_material', Log
# "same_material_list: %s, same_tnn_list: %s", "same_tnn_list len smaller
# than 2", "no tray with ingredients found", Schalter 'auto_refill' /
# BOX_ENABLE_AUTO_REFILL). Diese Liste schickt Creality Print an die Stock-
# Oberfläche, NICHT im G-Code (Orca-K2-Plus-Profile: nur M8200) – auf dem
# Pi fehlt die Quelle. Daher: manuelle Liste (KTWO_BOX_SAME_MATERIAL) ODER
# Vergleich über das RFID-Etikett (mat_id + col aus dem 40-Zeichen-vender,
# box.split_rfid_vendor; vgl. Vanilla box.py runout_recovery: gleiches
# Material UND gleiche Farbe, Slot belegt).

def rfid_material(value):
    """RFID-Wert eines Slots (40-Zeichen-vender) -> (mat_id, col) oder None
    ("unknown"/"busy"/leer/kaputt = unbekannt)."""
    if not isinstance(value, str) or len(value.strip()) != _BOX.RFID_VENDOR_LEN:
        return None
    try:
        f = _BOX.split_rfid_vendor(value.strip())
    except ValueError:
        return None
    mat, col = f.get("mat_id", ""), f.get("col", "").upper()
    if not mat.strip("0") or not col:
        return None
    return mat, col


def parse_same_material(text, box_addr=BOX_ADDR_DEFAULT):
    """KTWO_BOX_SAME_MATERIAL LIST= -> [[Slotbuchstaben], ...].

    Format (ANNAHME, Stock-Format von BOX_UPDATE_SAME_MATERIAL_LIST
    unbelegt): Gruppen mit '/' getrennt, Mitglieder mit ',' – z. B.
    ``T1A,T1C/T1B,T1D``. Nur Box ``box_addr``; jede Gruppe >= 2 Slots
    (Stock: "same_tnn_list len smaller than 2" = kein Nachladen), jeder
    Slot höchstens in einer Gruppe."""
    groups, seen = [], set()
    for part in str(text).strip().strip('"').split("/"):
        part = part.strip()
        if not part:
            continue
        letters = []
        for tok in part.split(","):
            tok = tok.strip()
            if not tok:
                continue
            box, _idx, letter, tnn = parse_tnn(tok)
            if box != int(box_addr):
                raise ValueError("%s liegt in Box %d, konfiguriert ist nur "
                                 "Box %d" % (tnn, box, int(box_addr)))
            if letter in seen:
                raise ValueError("%s steht in mehr als einer Gruppe" % tnn)
            if letter not in letters:
                letters.append(letter)
        if len(letters) < 2:
            raise ValueError("Gruppe %r hat weniger als 2 Slots" % (part,))
        seen.update(letters)
        groups.append(letters)
    if not groups:
        raise ValueError("LIST leer (Format: T1A,T1C/T1B,T1D)")
    return groups


def select_runout_slot(current, inlet_mask, rfid, same_groups=None,
                       require_color=True):
    """Ersatz-Slot für den leer gelaufenen Slot ``current`` (Buchstabe).

    Regel (s. Block oben): Kandidaten nur mit Filament am Einlauf (0x08 [00]
    Sensor A), nie der leere Slot selbst, Reihenfolge ab dem Slot NACH dem
    leeren (A→B→C→D→A, ANNAHME; Stock-Reihenfolge unbelegt).
    1. Gleich-Material-Liste gesetzt -> nur Mitglieder der Gruppe des leeren
       Slots (in Listenreihenfolge ab dem leeren Slot, zyklisch).
    2. Sonst RFID: gleiche mat_id UND (require_color) gleiche Farbe wie das
       Etikett des leeren Slots; Slots ohne lesbares Etikett nie.
    Gibt (Buchstabe oder None, Begründung) zurück."""
    cur = str(current).upper()
    if cur not in SLOT_LETTERS:
        raise ValueError("Slot muss A..D sein: %r" % (current,))
    inlet = int(inlet_mask) & MASK_ALL

    def present(letter):
        return bool(inlet & SLOT_MASKS[SLOT_LETTERS.index(letter)])

    if same_groups:
        group = next((g for g in same_groups if cur in g), None)
        if not group:
            return None, ("Slot %s steht in keiner Gleich-Material-Gruppe "
                          "(KTWO_BOX_SAME_MATERIAL)" % cur)
        k = group.index(cur)
        order = [x for x in group[k + 1:] + group[:k]]
        for letter in order:
            if present(letter):
                return letter, ("Gleich-Material-Gruppe %s, Slot %s hat "
                                "Filament am Einlauf" % ("+".join(group),
                                                         letter))
        return None, ("kein Slot der Gleich-Material-Gruppe %s hat Filament "
                      "am Einlauf (Stock „no tray with ingredients found“)"
                      % "+".join(group))
    ref = rfid_material((rfid or {}).get(cur))
    if ref is None:
        return None, ("Material/Farbe von Slot %s unbekannt (kein lesbares "
                      "RFID-Etikett) – Gruppe per KTWO_BOX_SAME_MATERIAL "
                      "festlegen" % cur)
    k = SLOT_LETTERS.index(cur)
    order = SLOT_LETTERS[k + 1:] + SLOT_LETTERS[:k]
    for letter in order:
        if not present(letter):
            continue
        m = rfid_material((rfid or {}).get(letter))
        if m is None or m[0] != ref[0]:
            continue
        if require_color and m[1] != ref[1]:
            continue
        return letter, ("RFID gleiches Material %s%s, Filament am Einlauf" % (
            ref[0], (" + Farbe %s" % ref[1]) if require_color else ""))
    return None, ("kein belegter Slot mit gleichem Material %s%s (RFID) – "
                  "Stock „no tray with ingredients found“" % (
                      ref[0], (" und Farbe %s" % ref[1]) if require_color
                      else ""))


def check_not_forbidden(fun):
    """Guard: 0x50/0x06 dürfen nie gesendet werden (Beleg s. FORBIDDEN_FUNS)."""
    if fun in FORBIDDEN_FUNS:
        raise ValueError("fun 0x%02x ist verboten (0x50 resettet, 0x06 FW-Bug)" % fun)
    return fun


def parse_slot(slot):
    """SLOT A..D (Auftrag) -> (index 0..3, letter, mask).

    Akzeptiert Buchstaben A-D (case-insensitiv) und Indizes 0-3 (Komfort,
    ANNAHME – Original-G-Code NUM=0..3 numerisch, Beleg PROTOKOLL-485.md:
    NUM=A wirft key60). Masken (0x01/0x02/0x04/0x08) werden NICHT akzeptiert
    (Verwechslungsgefahr) – dafür slot_to_mask() verwenden.
    """
    if isinstance(slot, bool):
        raise ValueError("SLOT muss A..D oder 0..3 sein: %r" % (slot,))
    if isinstance(slot, int) and slot in range(4):
        idx = int(slot)
        return idx, SLOT_LETTERS[idx], SLOT_MASKS[idx]
    if isinstance(slot, str):
        s = slot.strip().upper()
        if len(s) == 1 and s in SLOT_LETTERS:
            idx = SLOT_LETTERS.index(s)
            return idx, s, SLOT_MASKS[idx]
        if s in ("0", "1", "2", "3"):
            idx = int(s)
            return idx, SLOT_LETTERS[idx], SLOT_MASKS[idx]
    raise ValueError("SLOT muss A..D (oder 0..3) sein: %r" % (slot,))


def parse_preload_op(op):
    """OP 0..3 -> int (s. PRELOAD_OP_NAMES)."""
    if isinstance(op, bool):
        raise ValueError("OP muss 0..3 sein: %r" % (op,))
    if isinstance(op, int) and op in PRELOAD_OP_NAMES:
        return int(op)
    if isinstance(op, str):
        s = op.strip()
        if s in ("0", "1", "2", "3"):
            return int(s)
        low = s.lower()
        for code, name in PRELOAD_OP_NAMES.items():
            if low == name.split(" ")[0]:
                return code
    raise ValueError("OP muss 0..3 sein (0 disable, 1 enable, 2 force-read, "
                     "3 tension-only): %r" % (op,))


# =====================================================================
# Config (rein + Klipper-Hülle, gleicher Stil wie k2_motor_init.py)
# =====================================================================

def parse_options(opts):
    """Reines Dict (keys klein, Werte Strings) -> strukturierte Config.

    Unterstützte Optionen (ANNAHME für neue Keys, Defaults sicher):
    - box_addr (Default 1), allow_write (Default False),
      shutdown_on_failure (Default False), auto_init (Default True),
      poll_interval (Default 4.0 s).
    - Filamentwechsel: allow_move (Default False – Bewegung/Heizen),
      pause_on_failure (Default True), filament_sensor (Default
      "filament_sensor"), cut_sensor (Default leer) und alle Stock-[box]-
      Werte aus CHANGE_DEFAULTS (Positionen, Längen, Temperaturen,
      Geschwindigkeiten; cut_pos_x Default = SAVE_CONFIG -7.80).
    - Runout/RESUME: runout_refill, runout_require_color, runout_resume,
      resume_redo_change (Default False, s. cmd_resume_check).
    - Material/Spülen (17.10.2026, README-k2_box.md §12): material_database
      (Pfad, Default leer = aus), flush_mode color|fixed (Default color),
      flush_clean stock|legacy (Default stock), flush_blow (Default True),
      flush_blow_fan (Default fan0), flush_clog_check (Default False) und
      die Stock-[box]-Werte nozzle_volume, flush_multiplier, … (s. oben).
    """
    o = {str(k).strip().lower(): v for k, v in dict(opts).items()}

    def _int(key, default):
        try:
            return int(str(o.get(key, default)).strip())
        except ValueError:
            raise ValueError("%s muss int sein: %r" % (key, o.get(key)))

    def _float(key, default):
        try:
            return float(str(o.get(key, default)).strip())
        except ValueError:
            raise ValueError("%s muss Zahl sein: %r" % (key, o.get(key)))

    def _bool(key, default):
        v = str(o.get(key, str(default))).strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off"):
            return False
        raise ValueError("%s muss bool sein: %r" % (key, o.get(key)))

    def _str(key, default=""):
        return str(o.get(key, default) or "").strip()

    cfg = {
        "box_addr": _int("box_addr", BOX_ADDR_DEFAULT),
        "allow_write": _bool("allow_write", False),
        "shutdown_on_failure": _bool("shutdown_on_failure", False),
        "auto_init": _bool("auto_init", True),
        "poll_interval": _float("poll_interval", POLL_INTERVAL_DEFAULT),
        # Filamentwechsel (Ergänzung 16.10.2026)
        "allow_move": _bool("allow_move", False),
        "pause_on_failure": _bool("pause_on_failure", True),
        # Stock [box] filament_sensor: filament_sensor (Kopfsensor PA11)
        "filament_sensor": _str("filament_sensor", "filament_sensor"),
        # Stock [box] switch_pin: !nozzle_mcu:PB9 (Schneidhebel) – hier nur
        # als Name eines [filament_switch_sensor …] (leer = keine Prüfung)
        "cut_sensor": _str("cut_sensor", ""),
        # Auto-Nachladen bei Runout (A7, NOTIZ-runout.md): Default AUS;
        # wirkt zusätzlich nur mit allow_write UND allow_move.
        "runout_refill": _bool("runout_refill", False),
        "runout_require_color": _bool("runout_require_color", True),
        "runout_resume": _bool("runout_resume", True),
        # RESUME nach abgebrochenem T<n>-Wechsel bei LEEREM Kopf: Wechsel
        # automatisch nachholen (Stock error_resume_process, BOOTLOADER-
        # HOST-BEFUND §5.2). Default AUS – dann sperrt RESUME nur
        # (cmd_resume_check); wirkt zusätzlich nur mit allow_write UND
        # allow_move.
        "resume_redo_change": _bool("resume_redo_change", False),
        # Material-DB (Creality material_database.json, NICHT im Repo; von
        # install.sh vom Drucker kopiert). Leer = aus: bisheriges Verhalten.
        "material_database": _str("material_database", ""),
        # Spülmenge: color = Stock (Farbvolumen flush.py, unbekannte Farben
        # -> Tn_extrude 140 -> [90, 70] wie bisher); fixed = immer
        # [box_first, box_need] bzw. LENGTH= in box_first-Stücken (bis 17.10.)
        "flush_mode": _str("flush_mode", "color").lower(),
        # Reinigung je Abschnitt: stock = Blech zwischen den Abschnitten,
        # Silikon-Kreise nur am Ende; legacy = volles Abstreifen nach jedem
        "flush_clean": _str("flush_clean", "stock").lower(),
        "flush_blow": _bool("flush_blow", True),
        "flush_blow_fan": _str("flush_blow_fan", "fan0"),
        "flush_clog_check": _bool("flush_clog_check", False),
    }
    for key, default in CHANGE_DEFAULTS.items():
        if key in CHANGE_INT_KEYS:
            cfg[key] = _int(key, default)
        else:
            cfg[key] = _float(key, default)
    if not 0x01 <= cfg["box_addr"] <= 0x04:
        raise ValueError("box_addr muss 1..4 sein (Auto-Addr-Tabelle MB): %r"
                         % (cfg["box_addr"],))
    if not cfg["poll_interval"] > 0:
        raise ValueError("poll_interval muss > 0 sein: %r" % (cfg["poll_interval"],))
    # Stock-Kalibriergrenzen (box.cfg check_cut_pos_x_min/max)
    if not (cfg["check_cut_pos_x_min"] <= cfg["cut_pos_x"]
            <= cfg["check_cut_pos_x_max"]):
        raise ValueError("cut_pos_x %.2f außerhalb check_cut_pos_x_min/max "
                         "[%.2f, %.2f] (Stock-Kalibriergrenzen)" % (
                             cfg["cut_pos_x"], cfg["check_cut_pos_x_min"],
                             cfg["check_cut_pos_x_max"]))
    for key in ("cut_velocity", "cut_accel", "tn_retrude_velocity",
                "tn_extrude_velocity", "clean_velocity", "travel_velocity",
                "z_velocity", "wipe_circle_velocity", "wipe_accel",
                "extrude_sensor_timeout", "extrude_poll_interval",
                "box_wait_timeout", "box_wait_interval",
                "runout_tail_velocity"):
        if not cfg[key] > 0:
            raise ValueError("%s muss > 0 sein: %r" % (key, cfg[key]))
    if cfg["cut_dwell"] > 2.0:
        raise ValueError("cut_dwell > 2 s unplausibel: %r" % (cfg["cut_dwell"],))
    for key in ("buffer_empty_len", "tn_extrude_temp", "change_z_hop", "cut_dwell",
                "stage7_delay", "box_first_clean_length",
                "box_need_clean_length", "wipe_circle_radius", "wipe_shakes"):
        if cfg[key] < 0:
            raise ValueError("%s darf nicht negativ sein: %r" % (key, cfg[key]))
    if cfg["tn_retrude"] > 0:
        raise ValueError("tn_retrude muss <= 0 sein (Rückzug, Stock -10): %r"
                         % (cfg["tn_retrude"],))
    if not 0.0 <= cfg["runout_tail_length"] <= 100.0:
        raise ValueError("runout_tail_length muss 0..100 mm sein (Stock 30): %r"
                         % (cfg["runout_tail_length"],))
    if cfg["change_z_hop"] > 5.0:
        raise ValueError("change_z_hop > 5 mm unplausibel (Stock 0.4): %r"
                         % (cfg["change_z_hop"],))
    if cfg["flush_mode"] not in FLUSH_MODES:
        raise ValueError("flush_mode muss %s sein: %r" % (
            "|".join(FLUSH_MODES), cfg["flush_mode"]))
    if cfg["flush_clean"] not in FLUSH_CLEAN_MODES:
        raise ValueError("flush_clean muss %s sein: %r" % (
            "|".join(FLUSH_CLEAN_MODES), cfg["flush_clean"]))
    # Stock-Grenzen (BoxCfg.__init__ getX min/max, FLUSH-BEFUND §7.3)
    for key, lo, hi in (("nozzle_volume", 0.0, 300.0),
                        ("flush_multiplier", 0.0, 10.0),
                        ("flush_velocity_factor", 1.0, 5.0),
                        ("max_temp_flush_times", 0, 10),
                        ("diff_material_type_min_flush_len", 0.0, 1000.0),
                        ("box_need_clean_length_max", 0.0, 600.0),
                        ("tn_extrude", 0.0, 200.0),
                        ("flush_fan_start", 0.0, 255.0),
                        ("flush_blow_time", 0.0, 30.0),
                        ("flush_wipe_offset", 0.0, 10.0),
                        ("flush_clog_min_mm", 0.0, 100.0)):
        if not lo <= cfg[key] <= hi:
            raise ValueError("%s muss %s..%s sein: %r" % (key, lo, hi, cfg[key]))
    if not 0.0 <= cfg["wipe_minimum_cruise_ratio"] < 1.0:
        raise ValueError("wipe_minimum_cruise_ratio muss 0..<1 sein (Upstream "
                         "MINIMUM_CRUISE_RATIO): %r"
                         % (cfg["wipe_minimum_cruise_ratio"],))
    if not cfg["baseline_nozzle_diameter"] > 0:
        raise ValueError("baseline_nozzle_diameter muss > 0 sein: %r"
                         % (cfg["baseline_nozzle_diameter"],))
    return cfg


def parse_klipper_config(config):
    """Klipper-ConfigWrapper -> parse_options (ANNAHME zur API, s. BERICHT).

    Versucht get_options()/get wie in k2_motor_init.parse_klipper_config.
    """
    keys = []
    try:
        if hasattr(config, "get_options"):
            keys = list(config.get_options())
        elif hasattr(config, "get_prefix_options"):
            # Upstream-ConfigWrapper (configfile.py): alle Optionen des
            # Abschnitts; jede gelesene Option gilt als benutzt.
            keys = list(config.get_prefix_options(""))
        elif hasattr(config, "_options"):
            keys = list(dict(config._options).keys())
        elif isinstance(config, dict):
            keys = list(config.keys())
    except Exception:
        keys = []
    opts = {}

    def _g(name, default=None):
        try:
            if hasattr(config, "get"):
                return config.get(name, default)
        except Exception:
            pass
        if isinstance(config, dict):
            return config.get(name, default)
        return default

    if not keys and isinstance(config, dict):
        return parse_options(config)
    for k in keys:
        opts[k.lower()] = _g(k, "")
    for k in (("box_addr", "allow_write", "shutdown_on_failure", "auto_init",
               "poll_interval", "allow_move", "pause_on_failure",
               "filament_sensor", "cut_sensor", "runout_refill",
               "runout_require_color", "runout_resume",
               "resume_redo_change", "material_database", "flush_mode",
               "flush_clean", "flush_blow", "flush_blow_fan",
               "flush_clog_check")
              + tuple(CHANGE_DEFAULTS)):
        if k not in opts:
            v = _g(k, None)
            if v is not None:
                opts[k] = v
    return parse_options(opts)


# =====================================================================
# Auto-Addr-Adapter (Box-Seite, erst nach k2_motor:ready)
# =====================================================================


def reactor_sleep(reactor):
    """Relative Pause für Klipper: reactor.pause() erwartet eine ABSOLUTE
    Weckzeit (upstream reactor.py: pause(waketime)). Ohne Reactor -> time.sleep."""
    if reactor is None or not hasattr(reactor, "pause") \
            or not hasattr(reactor, "monotonic"):
        return time.sleep
    return lambda s: reactor.pause(reactor.monotonic() + float(s))


class ReactorBusProxy:
    """Bus-Aufrufe in einem Hilfs-Thread; der Klipper-Reactor pausiert
    derweil in kleinen Schritten (andere Timer laufen weiter).

    Grund: Box-Antworten kommen erst NACH dem Vorgang (0x10 mode 0 live
    4,3–5,4 s, 0x11 p1 0,8–22,4 s, 0x0d op 2 31–37 s; Beleg livelog-cfs/
    BEFUND-LIVE.md §3). Ein blockierender Aufruf im Reactor würde Heizungs-
    PWM-Updates (max_duration) und die MCU-Uhr-Synchronisation aufhalten –
    bei heißer Düse droht ein MCU-Shutdown. Der Bus-Lock (serial485,
    threading.Lock) serialisiert weiterhin alle Teilnehmer.
    Callbacks des Controllers (Sensor, Extruder-Bewegung) laufen NICHT im
    Thread, sondern im Reactor (nur transact/transact_full sind verlagert).
    """

    def __init__(self, bus, reactor, step=0.02):
        self.bus = bus
        self.reactor = reactor
        self.step = float(step)

    def _run(self, name, *args, **kwargs):
        res = {}

        def _work():
            try:
                res["v"] = getattr(self.bus, name)(*args, **kwargs)
            except BaseException as exc:  # an den Aufrufer weiterreichen
                res["e"] = exc

        t = threading.Thread(target=_work, name="k2_box-bus")
        t.daemon = True
        t.start()
        while t.is_alive():
            self.reactor.pause(self.reactor.monotonic() + self.step)
        t.join()
        if "e" in res:
            raise res["e"]
        return res.get("v")

    def transact_full(self, *args, **kwargs):
        return self._run("transact_full", *args, **kwargs)

    def transact(self, *args, **kwargs):
        return self._run("transact", *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.bus, name)

class BoxAutoAddrSerial:
    """Adapter: auto_addr-Serial-API um einen Box-Bus (serial485-artig).

    auto_addr.AutoAddr braucht ``cmd_send_data_with_response(data, timeout,
    retransmit)`` mit Wire-Bytes OHNE Head/CRC ([addr][len][status][fun]
    [daten...]) und liefert den VOLLEN Antwort-Frame ([F7]...[crc]) oder
    None bei Timeout (Beleg auto_addr.py send_package/data_handler).

    Umsetzung hier: Wire zerlegen -> bus.transact_full(addr, fun, payload,
    timeout, retries=0, status, expect_fun=fun, expect_addr=None) – mit
    expect_addr=None, weil Broadcasts (0xFE) mit Geräte- ODER neuer Adresse
    antworten (Beleg PROTOKOLL-485.md §6.8: A1 an 0xFE, A0-Antwort von neuer
    Adresse, 3x). retries=0 wie Original (Auto-Addr/Broadcasts, Beleg §6.2).
    """

    def __init__(self, bus, allow_write=True):
        if bus is None or not hasattr(bus, "transact_full"):
            raise ValueError("BoxAutoAddrSerial braucht bus.transact_full()")
        self.bus = bus
        self.allow_write = bool(allow_write)
        self.calls = []  # [(addr, fun, tuple(payload))] – Diagnose

    def cmd_send_data_with_response(self, data, timeout, retransmit):
        wire = bytes(data)
        if len(wire) < 4:
            return None
        addr, _length, status, fun = wire[0], wire[1], wire[2], wire[3]
        payload = list(wire[4:])
        self.calls.append((addr, fun, tuple(payload)))
        # 0xA0 (Adressvergabe) ist schreibend – ohne allow_write blocken.
        if fun == 0xA0 and not self.allow_write:
            return None
        check_not_forbidden(fun)
        try:
            _raddr, _rstatus, _rfun, _rdata, rraw = self.bus.transact_full(
                addr, fun, payload, timeout=timeout, retries=0,
                status=status, expect_fun=fun, expect_addr=None)
        except (TimeoutError, ValueError):
            return None
        return bytes(rraw)


# =====================================================================
# Controller – reine Box-Logik (ohne Klipper importierbar)
# =====================================================================

class K2BoxController:
    """Box-Abläufe gegen den Bus (vgl. BoxAction.communication_*).

    ``bus`` ist serial485-artig (transact/transact_full, z.B.
    serial485.Serial485 für /dev/ttyS5 – DIESELBE Instanz wie [k2_motor],
    damit EIN Lock den Halbduplex-Bus serialisiert). Alle BoxAction-Aufrufe
    laufen darüber (Statusprüfung via CfsStatusError, s. box.BoxAction).
    """

    def __init__(self, cfg, bus, auto_addr_obj=None, pause=None,
                 logger=None, send_event=None):
        self.cfg = dict(cfg)
        if bus is None or not hasattr(bus, "transact_full"):
            # Fallback auf transact (Status dann unbekannt – wie BoxAction).
            if bus is None or not hasattr(bus, "transact"):
                raise ValueError("K2BoxController braucht bus.transact_full() "
                                 "(serial485-artig, geteilt mit [k2_motor])")
        self.bus = bus
        self.action = BoxAction(bus, addr=self.cfg.get("box_addr", BOX_ADDR_DEFAULT))
        self.auto_addr = auto_addr_obj
        self._addr_store = {}
        self.pause = pause if pause is not None else time.sleep
        self.log = logger if logger is not None else self._print_log()
        self.send_event = send_event
        self.motor_ready = False
        self.is_ready = False
        self.last_poll = None  # parse_box_state-Dict (Cache für Moonraker)
        self.last_poll_raw = None  # StatusBytes
        self.box_fw = None  # z.B. "153" aus 0x14 (für parse_box_state)
        self.slot_cache = {}  # letter -> {rfid, remain}
        self._stop = threading.Event()
        self._thread = None
        # Filamentwechsel: zuletzt geladener Slot (None = leer/unbekannt),
        # busy = langer Box-Vorgang läuft (Poll-Timer pausiert dann).
        self.loaded_slot = None
        self.busy = False
        self.clock = time.monotonic
        # Auto-Nachladen (A7): Slot, den die Box im PRINT-Zustand zuletzt im
        # Hub gemeldet hat (0x0a b4), und letztes LESBARES RFID-Etikett je
        # Slot (die leere Spule liefert evtl. nur noch "unknown").
        self.print_slot = None
        self.tag_cache = {}

    class _print_log:
        def info(self, msg):
            print(msg)

        def error(self, msg):
            print("FEHLER: %s" % msg)

    # --- Helfer -------------------------------------------------------
    def _require_write(self, name):
        """Schreib-Guard: ohne allow_write -> PermissionError, NICHTS gesendet."""
        if not self.cfg.get("allow_write"):
            raise PermissionError(
                "%s braucht allow_write: True (Default False → nur lesen + "
                "Plan, nichts gesendet)" % name)
        return True

    def _odo_mm(self, raw4):
        """4 B Odometer-Float BE (0x10 mode 5) -> mm (Betrag, positiv)."""
        raw = bytes(raw4)
        if len(raw) != 4:
            raise ValueError("Odometer-Antwort muss 4 Bytes haben: %r" % (raw,))
        (neg,) = struct.unpack(">f", raw)
        return -float(neg)

    # --- Plan-Text (Trockenlauf, kein Bus) ------------------------------
    def describe_plan(self):
        out = ["K2-Box-Plan (Trockenlauf, nichts gesendet)"]
        out.append("box_addr=0x%02X allow_write=%s poll_interval=%.1f s" % (
            self.cfg.get("box_addr", BOX_ADDR_DEFAULT),
            self.cfg.get("allow_write"), self.cfg.get("poll_interval",
                                                      POLL_INTERVAL_DEFAULT)))
        out.append("A0 Adressvergabe (Auto-Addr 0xA0-0xA3, erst nach k2_motor:ready)")
        out.append("A1 Status-Poll 0x0a (retries=0, alle ~%.0f s wie Original)" % (
            self.cfg.get("poll_interval", POLL_INTERVAL_DEFAULT),))
        out.append("A2 lesend: 0x02 RFID / 0x03 Remain / 0x14 Version / 0x15 HW / "
                   "0x08 Sensor / 0x05 Buffer / 0x0e Messrad (retries wie Original)")
        out.append("A3 Preload 0x0d op 0 disable / 1 enable / 2 Force+RFID / "
                   "3 Force ohne RFID (op 2/3: Timeout >=380 s, retries=0, keine Bursts)")
        out.append("A4 Extrude (Load): 04 IDLE → 0f an → 08 Sensor → 10/00 → 10/04 → "
                   "10/05 x%d → 10/06 → 10/07 p=3 → 05 → 04 PRINT → 0f aus" % ODO_POLLS_DEFAULT)
        out.append("A5 Retrude (Unload): 04 IDLE → 08 Sensor → 11 p0 x%d → 11 p1" % (
            RETRUDE_REPEATS_DEFAULT,))
        out.append("A6 Cut: Kopfbewegung pre_cut_pos -> cut_pos_x %s -> zurück "
                   "(KTWO_BOX_CUT, allow_move; sendet KEIN Box-Frame)" % (
                       _n(self.cfg.get("cut_pos_x", CHANGE_DEFAULTS["cut_pos_x"])),))
        out.append("Verboten: nie 0x50 (Reset) / nie 0x06-Feeder (FW-Bug); "
                   "Bus-Lock mit [k2_motor] teilen (eine ttyS5-Instanz).")
        if not self.cfg.get("allow_write"):
            out.append("Hinweis: allow_write=False → nur lesen + Plan (keine "
                       "0x0d/0x04/0x0f/0x10/0x11, kein 0xA0).")
        return "\n".join(out)

    # --- A0 Adressvergabe ------------------------------------------------
    def on_motor_ready(self):
        """Event k2_motor:ready → Adressvergabe starten (s. PrinterK2Box)."""
        self.motor_ready = True
        return self.ensure_addr(motor_ready=True)

    def ensure_addr(self, motor_ready=None):
        """Auto-Addr-Ablauf (startup + ein Online-Zyklus).

        Erst nach k2_motor:ready (Beleg Stock-Bootsequenz PROTOKOLL-485.md
        §3.1 Schritt 11: Motor-Init → auto_addr:start). Ohne motor_ready →
        Status wait_motor, NICHTS gesendet. Ohne allow_write → nur lesende
        Anteile? Entwurf: ganz übersprungen (0xA0 ist schreibend), Status
        skipped – lesende Polls laufen trotzdem (Adresse meist schon vergeben).
        """
        ready = self.motor_ready if motor_ready is None else bool(motor_ready)
        if not ready:
            info = "wartet auf k2_motor:ready (nichts gesendet)"
            try:
                self.log.info(info)
            except Exception:
                pass
            return {"status": "wait_motor", "log": info, "ready": False}
        if not self.cfg.get("allow_write"):
            info = ("allow_write=False: keine Adressvergabe (0xA0 schreibend) – "
                    "nur lesende Polls")
            try:
                self.log.info(info)
            except Exception:
                pass
            return {"status": "skipped", "log": info, "ready": False}
        if self.auto_addr is None:
            serial = BoxAutoAddrSerial(self.bus, allow_write=True)
            self.auto_addr = _AA.AutoAddr(serial, store=self._addr_store)
        try:
            self.auto_addr.startup()
            txt = self.auto_addr.online_once()
        except Exception as exc:
            try:
                self.log.error("Auto-Addr: %s" % exc)
            except Exception:
                pass
            return {"status": "addr_error", "log": str(exc), "ready": False}
        self.is_ready = True
        try:
            if self.send_event is not None:
                self.send_event("k2_box:ready")
        except Exception as exc:
            try:
                self.log.info("Event k2_box:ready nicht sendbar: %s" % exc)
            except Exception:
                pass
        return {"status": "ready", "log": txt, "ready": True}

    # --- A1 Status-Poll (lesend, retries=0) -------------------------------
    def poll_status(self):
        """0x0a-Poll (retries=0 wie Original), Cache für Moonraker.

        Lesend – auch ohne allow_write erlaubt (Herzschlag alle ~4 s, Beleg
        PROTOKOLL-485.md §6.2: 10 560 Polls, retries=0). Gibt
        parse_box_state-Dict zurück (mit temp_c/rh/hub_mask/state).
        """
        raw = self.action.communication_get_box_state()
        self.last_poll_raw = raw
        try:
            parsed = parse_box_state(raw, box_fw=self.box_fw)
        except Exception:
            parsed = parse_box_state(raw)
        self.last_poll = parsed
        if parsed.get("kind") == "state" \
                and parsed.get("state") == _BOX.BOX_STATE_PRINT \
                and parsed.get("hub_mask") in SLOT_MASKS:
            self.print_slot = SLOT_LETTERS[SLOT_MASKS.index(
                parsed.get("hub_mask"))]
        return parsed

    def start_background(self, interval=None):
        """Hintergrund-Poll (Threading, offline/Test) statt Reactor-Timer."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        wait = float(interval if interval is not None
                     else self.cfg.get("poll_interval", POLL_INTERVAL_DEFAULT))

        def loop():
            while not self._stop.is_set():
                try:
                    self.poll_status()
                except Exception:
                    pass
                if self._stop.wait(wait):
                    break

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop_background(self):
        self._stop.set()

    # --- A2 lesende Abfragen (auch ohne allow_write) ----------------------
    def get_rfid(self, slot):
        idx, letter, _mask = parse_slot(slot)
        mask = SLOT_MASKS[idx]
        raw = self.action.communication_get_rfid(mask)
        parsed = get_rfid(raw)
        val = parsed.get(letter)
        self.slot_cache.setdefault(letter, {})["rfid"] = val
        if rfid_material(val) is not None:
            self.tag_cache[letter] = val
        return val

    def get_rfid_all(self):
        """RFID aller Slots mit EINEM 0x02 [0f] (lesend, Beleg API-box_wrapper
        communication_get_rfid(1, 15)) -> {Buchstabe: Wert}."""
        raw = self.action.communication_get_rfid(MASK_ALL)
        parsed = get_rfid(raw)
        for letter, val in parsed.items():
            self.slot_cache.setdefault(letter, {})["rfid"] = val
            if rfid_material(val) is not None:
                self.tag_cache[letter] = val
        return dict(parsed)

    def inlet_mask(self):
        """Einlauf-Sensoren (0x08 [00] = Sensor A, Filament je Slot) – lesend."""
        return int(self.get_sensor(0x00)) & MASK_ALL

    def get_remain(self, mask=MASK_ALL):
        if isinstance(mask, bool) or not isinstance(mask, int) \
                or not 0 <= mask <= MASK_ALL:
            raise ValueError("Maske muss 0x00..0x0f sein: %r" % (mask,))
        raw = self.action.communication_get_remain_len(mask)
        parsed = get_remain_len(bytes([mask]), raw)
        for letter, val in parsed.items():
            self.slot_cache.setdefault(letter, {})["remain"] = val
        return parsed

    def get_version_sn(self):
        raw = self.action.communication_get_version_sn()
        ver, sn = parse_version_sn(raw)
        self.box_fw = ver
        return ver, sn

    def get_hardware_status(self):
        raw = self.action.communication_get_hardware_status()
        return parse_get_hardware_status(raw)

    def get_sensor(self, pos=0x00):
        raw = self.action.communication_get_filament_sensor_state(pos)
        return parse_filament_sensor_state(raw)

    def get_buffer(self):
        raw = self.action.communication_get_buffer_state()
        return parse_buffer_state(raw)

    def get_wheel(self):
        return self.action.communication_measuring_wheel()

    # --- A3 Preload -------------------------------------------------------
    def do_preload(self, slot, op, timeout=None, dry_run=False, guard=None):
        """0x0d [Maske][op] (op 0-3, Beleg GHIDRA §3).
        ANMERKUNG N5 (BOOTLOADER-HOST-NACHTRAG): Stock sendet nach jedem
        (Re-)Connect 0x0d [0f 01]/[0f 00] (preloading_en) – hier bewusst
        NICHT (offener Gerätetest: Auto-Preload nach Box-Reset ohne Host-
        [0f 01]?).

        op 2/3: Antwort erst nach dem Vorgang → langer Timeout
        (preload_force_timeout) + retries=0 (kein Burst, Beleg BEFUND2 §5).
        Schreibend → allow_write nötig (auch op 0/1 ändern PRE[1] global).
        guard (17.10.2026, Stock-Schutz, s. preload_block_reason): dict
        head (Kopfsensor True/False/None), printing (Druck/Pause), force
        (FORCE=1) – gesperrt -> PermissionError, NICHTS gesendet. Die
        Klipper-Hülle (KTWO_BOX_PRELOAD) übergibt es immer; ohne guard
        (Controller-Tests, Werkzeuge) nur die Maske-0-Sperre.
        """
        idx, letter, mask = parse_slot(slot)
        op = parse_preload_op(op)
        if dry_run or not self.cfg.get("allow_write"):
            if dry_run:
                return {"status": "dry_run", "slot": letter, "op": op,
                        "plan": "0x0d [%02x %02x] (%s)" % (
                            mask, op, PRELOAD_OP_NAMES[op])}
            self._require_write("Preload SLOT=%s OP=%d" % (letter, op))
        g = dict(guard) if guard is not None else {"head": False,
                                                   "printing": False}
        why = preload_block_reason(op, mask, g.get("head"),
                                   bool(g.get("printing")),
                                   bool(g.get("force")))
        if why:
            raise PermissionError("Preload SLOT=%s OP=%d gesperrt: %s – "
                                  "nichts gesendet" % (letter, op, why))
        check_not_forbidden(_BOX.FUNC_SET_PRE_LOADING)
        raw = self.action.communication_set_pre_loading(mask, op,
                                                       timeout=timeout)
        return {"status": "ok", "slot": letter, "op": op, "raw": bytes(raw)}

    # --- A4 Extrude (Load, Box-Seite) --------------------------------------
    def do_extrude(self, slot, odo_polls=ODO_POLLS_DEFAULT, dry_run=False,
                   sensor_fn=None, sensor_timeout=90.0, poll_interval=2.0,
                   sensor_check=0.1, stage7_fn=None, stage7_delay=0.0):
        """Box-Extrude A4 (Byte-folge == cfsseq.build_extrude_sequence).

        Abbruch bei Status != 0: BoxAction wirft CfsStatusError (mit
        Status-Name, z.B. 0x0c Odometer-Stillstand) – hier mit Slot-Kontext
        angereichert und propagiert (Auftrag: „Abbruch mit CfsStatusError-Text“).

        Wechsel-Erweiterung (16.10.2026, Beleg Live-Wechsel 12./13.10.):
        - ``sensor_fn`` (callable -> bool, Kopf-Filamentsensor): statt fester
          ``odo_polls`` wird mode 5 alle ``poll_interval`` s abgefragt, bis
          der Kopfsensor Filament meldet (live: mode 6 kommt aus dem
          Sensor-Ereignis, „timeout = 90“). Kein Sensor nach
          ``sensor_timeout`` s -> Box per 0x04 [00 01] auf IDLE (Fördern
          stoppen, ANNAHME) + BoxChangeError.
        - ``stage7_fn`` (callable): zwischen mode 6 und mode 7 (live Ereignis
          ``extrude_process_stage7``) – Drucker lässt den Extruder greifen;
          danach ``stage7_delay`` s warten, dann mode 7 p=3 (Box prüft, dass
          das Odometer > p mm in 300 ms läuft, sonst 0x0e).
        Ohne beide Callbacks bleibt die Frame-Folge byte-identisch zu
        EXTRUDE_LIVE_CMDS_B.
        """
        idx, letter, mask = parse_slot(slot)
        if isinstance(odo_polls, bool) or not isinstance(odo_polls, int) \
                or odo_polls < 1:
            raise ValueError("odo_polls muss int >= 1 sein: %r" % (odo_polls,))
        if dry_run or not self.cfg.get("allow_write"):
            if dry_run:
                return {"status": "dry_run", "slot": letter,
                        "plan": self.describe_plan()}
            self._require_write("Extrude SLOT=%s" % letter)
        for fun in (FUNC_SET_BOX_MODE, FUNC_PRELOAD_INHIBIT, FUNC_EXTRUDE):
            check_not_forbidden(fun)
        odo_trace = []

        def _run(mode, p, label):
            try:
                raw = self.action.communication_extrude(mask, mode, p)
            except CfsStatusError as exc:
                raise CfsStatusError(
                    exc.status,
                    "%s SLOT=%s: %s" % (label, letter, exc.name),
                    exc.data, exc.fun)
            if mode == _SEQ.EXTRUDE_MODE_QUERY:
                try:
                    odo_trace.append(self._odo_mm(bytes(raw)))
                except ValueError:
                    pass
            return raw

        def _stop_feed():
            # Best effort: IDLE stoppt/löscht (live 0x04 [00 01] aus ERROR).
            try:
                self.action.communication_set_box_mode_feed()
            except Exception:
                pass

        self.busy = True
        try:
            self.action.communication_set_box_mode_feed()
            self.action.communication_set_preload_inhibit(True)
            self.action.communication_get_filament_sensor_state(0x00)
            _run(_SEQ.EXTRUDE_MODE_STAGE1, 0x00, "Stage1 (mode 0)")
            _run(_SEQ.EXTRUDE_MODE_FEED_START, 0x00, "Feed-Start (mode 4)")
            if sensor_fn is None:
                for _ in range(odo_polls):
                    _run(_SEQ.EXTRUDE_MODE_QUERY, 0x00, "Odometer-Poll (mode 5)")
            else:
                t0 = self.clock()
                _run(_SEQ.EXTRUDE_MODE_QUERY, 0x00, "Odometer-Poll (mode 5)")
                last = self.clock()
                while not sensor_fn():
                    now = self.clock()
                    if now - t0 > float(sensor_timeout):
                        _stop_feed()
                        raise BoxChangeError(
                            "Einzug SLOT=%s: Kopf-Filamentsensor nach %.0f s "
                            "nicht erreicht (Odometer %s mm) – Box auf IDLE" % (
                                letter, float(sensor_timeout),
                                round(odo_trace[-1], 1) if odo_trace else "?"))
                    if now - last >= float(poll_interval):
                        _run(_SEQ.EXTRUDE_MODE_QUERY, 0x00,
                             "Odometer-Poll (mode 5)")
                        last = self.clock()
                    self.pause(float(sensor_check))
            _run(_SEQ.EXTRUDE_MODE_STOP_BUFFER, 0x00, "Stopp/Buffer (mode 6)")
            if stage7_fn is not None:
                stage7_fn()
                if stage7_delay:
                    self.pause(float(stage7_delay))
            _run(_SEQ.EXTRUDE_MODE_EXTRUDER_CHECK, EXTRUDER_CHECK_MM,
                 "Extruder-Check (mode 7 p=3)")
            self.action.communication_get_buffer_state()
            self.action.communication_set_box_mode_print(mask)
            self.action.communication_set_preload_inhibit(False)
        finally:
            self.busy = False
        self.loaded_slot = letter
        return {"status": "ok", "slot": letter, "odo_mm": list(odo_trace)}

    def do_load(self, slot, dry_run=False, odo_polls=ODO_POLLS_DEFAULT,
                **extrude_kw):
        """Box-Load = Extrude-Ablauf A4 (Toolhead-Heizen/Fahren macht der
        Aufrufer per Makro bzw. KTWO_BOX_EXTRUDE – hier nur Box-Förderung)."""
        if dry_run:
            idx, letter, _m = parse_slot(slot)
            return {"status": "dry_run", "slot": letter,
                    "plan": self.describe_plan()}
        return self.do_extrude(slot, odo_polls=odo_polls, **extrude_kw)

    # --- A5 Retrude (Unload, Box-Seite) ------------------------------------
    def do_retrude(self, slot,
                   to_buffer_repeats=RETRUDE_REPEATS_DEFAULT, dry_run=False,
                   after_p0=None):
        """Box-Retrude A5. ``after_p0(i)`` (optional) läuft nach der i-ten
        0x11-p0-Antwort (0-basiert) – Wechsel: Extruder-Rückzug tn_retrude
        (live 3x "extrude = -10.0" je nach p0 1–3, dann p0 4 + p1)."""
        idx, letter, mask = parse_slot(slot)
        if isinstance(to_buffer_repeats, bool) or not isinstance(
                to_buffer_repeats, int) or to_buffer_repeats < 0:
            raise ValueError("to_buffer_repeats muss int >= 0 sein: %r"
                             % (to_buffer_repeats,))
        if dry_run or not self.cfg.get("allow_write"):
            if dry_run:
                return {"status": "dry_run", "slot": letter,
                        "plan": self.describe_plan()}
            self._require_write("Retrude SLOT=%s" % letter)
        for fun in (FUNC_SET_BOX_MODE, FUNC_RETRUDE):
            check_not_forbidden(fun)
        self.busy = True
        try:
            self.action.communication_set_box_mode_feed()
            if to_buffer_repeats:
                self.action.communication_get_filament_sensor_state(0x00)
                for i in range(to_buffer_repeats):
                    self.action.communication_retrude(
                        mask, RETRUDE_P_TO_BUFFER)
                    if after_p0 is not None:
                        after_p0(i)
            self.action.communication_retrude(mask, RETRUDE_P_PULL_HUB)
        except CfsStatusError as exc:
            raise CfsStatusError(
                exc.status, "Retrude SLOT=%s: %s" % (letter, exc.name),
                exc.data, exc.fun)
        finally:
            self.busy = False
        if self.loaded_slot in (None, letter):
            self.loaded_slot = None
        return {"status": "ok", "slot": letter}

    def do_unload(self, slot, dry_run=False,
                  to_buffer_repeats=RETRUDE_REPEATS_DEFAULT):
        if dry_run:
            idx, letter, _m = parse_slot(slot)
            return {"status": "dry_run", "slot": letter,
                    "plan": self.describe_plan()}
        return self.do_retrude(slot, to_buffer_repeats=to_buffer_repeats,
                               dry_run=False)

    # --- A6 Cut (reine Kopfbewegung, kein Box-Frame) -------------------------
    def do_cut(self, slot=None, restore_accel=None):
        """Schneidfahrt als G-Code-Plan (sendet NICHTS an die Box).

        Beleg: Live-Cut 12.10. 00:24:50–00:24:53 und 16.10. 22:02:35–22:02:39
        – zwischen „[box] cut sensor detected“ und „cut to return OK“ nur
        0x0a-Polls/0x0e, KEIN Box- oder Motor-Frame → Cut = Kopfbewegung
        gegen den Schneidhebel (+ Schaltersensor switch_pin PB9). Positionen
        aus box.cfg/SAVE_CONFIG (s. gcode_cut). Ausführen macht die
        Klipper-Hülle (KTWO_BOX_CUT, nur mit allow_move).
        """
        if slot is not None:
            parse_slot(slot)  # nur Plausibilität (Cut ist slot-unabhängig)
        return {"status": "plan", "gcode": gcode_cut(self.cfg, restore_accel)}

    # --- Wechsel: Wartebedingungen (lesend) ----------------------------------
    def hub_mask(self):
        """Hub-Sensor (0x08 [01] = Sensor B, live auch 0x0a b4) – lesend."""
        return int(self.get_sensor(0x01)) & MASK_ALL

    def wait_box_ready(self, timeout=CHANGE_DEFAULTS["box_wait_timeout"],
                       interval=1.0):
        """Box-Zustand abwarten (Stock BOX_MODE_WAIT): 0x0a pollen, bis
        IDLE oder PRINT. PRELOAD/RELOAD/0x30-Ereignis -> weiter warten
        (Preload live 31–37 s je Slot); ERROR -> CfsStatusError mit
        gespeichertem Fehlercode; Timeout -> BoxChangeError. Lesend."""
        t0 = self.clock()
        last = None
        while True:
            st = self.poll_status()
            last = st
            kind = st.get("kind")
            state = st.get("state")
            if kind in ("state", "state_v113"):
                if state == _BOX.BOX_STATE_ERROR:
                    code = st.get("error_code") or st.get("frame_status") or 0
                    raise CfsStatusError(
                        int(code), "Box im Zustand ERROR (Fehler 0x%02x %s) – "
                        "KTWO_BOX_ERROR_CLEAR" % (int(code), status_name(int(code))),
                        b"", FUNC_GET_BOX_STATE)
                if state in (BOX_STATE_IDLE, _BOX.BOX_STATE_PRINT):
                    return st
            if self.clock() - t0 > float(timeout):
                raise BoxChangeError(
                    "Box nach %.0f s nicht bereit (Zustand %s)" % (
                        float(timeout), (last or {}).get("state_name", "?")))
            self.pause(float(interval))

    # --- Status-Objekt für Moonraker (kein Bus) ------------------------------
    def get_status(self, eventtime=None):
        """Moonraker-Status (nur Cache, kein Bus – Klipper ruft oft auf).

        Keys: ready, box_addr, state, state_name, hub_mask, temp_c, rh,
        slots ({A..D: {rfid, remain}}), box_fw. Vor dem ersten Poll:
        state unknown.
        """
        _ = eventtime
        base = {"ready": bool(self.is_ready),
                "box_addr": self.cfg.get("box_addr", BOX_ADDR_DEFAULT),
                "box_fw": self.box_fw,
                "loaded_slot": self.loaded_slot,
                "busy": bool(self.busy),
                "state": None, "state_name": "unknown",
                "hub_mask": None, "temp_c": None, "rh": None,
                "slots": {k: dict(v) for k, v in self.slot_cache.items()}}
        poll = self.last_poll or {}
        if poll.get("kind") == "state":
            base.update({"state": poll.get("state"),
                         "state_name": poll.get("state_name", "unknown"),
                         "hub_mask": poll.get("hub_mask"),
                         "temp_c": poll.get("temp_c"),
                         "rh": poll.get("rh")})
        elif poll.get("kind") == "event":
            base.update({"state": None, "state_name": "event",
                         "hub_mask": None,
                         "events": dict(poll.get("events", {}))})
        return base


# =====================================================================
# Klipper-Hülle [k2_box] (ANNAHMEN zur Upstream-API, s. BERICHT.md)
# =====================================================================

class PrinterK2Box:
    """Klipper-Objekt [k2_box] – verdrahtet Controller mit Klipper."""

    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        try:
            self.cfg = parse_klipper_config(config)
        except Exception as exc:
            raise config.error("[k2_box] Config-Fehler: %s" % exc)
        # Material-DB (17.10.2026): Fehler beim Laden = KEIN Config-Fehler,
        # sondern Fallback auf das bisherige Verhalten + Meldung (Connect).
        self.material_db, self.material_db_info = load_material_db(
            self.cfg.get("material_database"))
        if self.material_db_info:
            try:
                (logging.info if self.material_db is not None
                 else logging.warning)("[k2_box] %s" % self.material_db_info)
            except Exception:
                pass
        # Material in der Düse (Stock box_action.remain_material: color/type
        # nach jedem erfolgreichen Spülen, remain_material.save). Nur im
        # Speicher (Stock persistiert) – nach dem Start unbekannt.
        self.remain_material = {"type": None, "color": None, "tnn": ""}
        self.flush_last = ""
        self._bus = None  # ANNAHME: geteilte ttyS5-Instanz (s. _ensure_bus)
        self.controller = None
        self.printer.register_event_handler("klippy:connect",
                                            self.handle_connect)
        # Adressvergabe erst nach Motor-Init (gleiche Reihenfolge wie Stock).
        try:
            self.printer.register_event_handler("k2_motor:ready",
                                                self.handle_motor_ready)
        except Exception:
            pass
        self.gcode.register_command("KTWO_BOX_STATUS", self.cmd_status,
                                    desc="K2-Box-Status (0x0a-Poll, lesend)")
        self.gcode.register_command("KTWO_BOX_LOAD", self.cmd_load,
                                    desc="K2-Box-Load SLOT=A..D [DRY_RUN=1]")
        self.gcode.register_command("KTWO_BOX_UNLOAD", self.cmd_unload,
                                    desc="K2-Box-Unload SLOT=A..D [DRY_RUN=1]")
        self.gcode.register_command("KTWO_BOX_PRELOAD", self.cmd_preload,
                                    desc="K2-Box-Preload SLOT=A..D OP=0..3 "
                                         "[FORCE=1] (op 2/3 nur Kopf leer)")
        # Filamentwechsel (T0–T15 / M8200-Kette, s. Modul-Docstring)
        self.chain = self._new_chain()
        self.chain_error = ""
        self.pending_resume_z = None
        # Startposition eines im Druck abgebrochenen Wechsels – nur für ein
        # Wiederholungs-T<n> IN DERSELBEN PAUSE (s. cmd_pre_opt).
        self._keep_start_pos = None
        # Position vor einem Einzelschritt im laufenden Druck ohne Kette
        # (z. B. M8200 C ohne P) -> Rückfahrziel bei dessen Abbruch.
        self._step_start = None
        # Extruder-Ziel vor einem solchen Einzelschritt (PAUSE CFS_TEMP, s.
        # _pause_temp); in der Kette übernimmt das chain["orig_target"].
        self._step_target = None
        # Abgebrochener Wechsel im Druck (_fail, Pausenpfad): RESUME prüft
        # dann den Kopfsensor (cmd_resume_check) – leer = gesperrt bzw.
        # Nachholen (resume_redo_change). Dict step/tnn/source oder None.
        # Stock-Gegenstück: BoxSave.recode_err -> save_resume_tnn
        # (BOOTLOADER-HOST-BEFUND §5.1). Lebt bis zum erfolgreichen RESUME
        # (KTWO_BOX_RESUME_FIX) bzw. bis zu einer NEUEN Pause, die nicht
        # aus _fail stammt (KTWO_BOX_RESUME_FIX CLEAR=1, s. _pause_from_fail).
        self.aborted = None
        self._pause_from_fail = False
        # Auto-Nachladen bei Runout (A7): Laufzeit-Schalter (Start = Config
        # runout_refill, Stock BOX_ENABLE_AUTO_REFILL), Gleich-Material-Liste
        # (Stock BOX_UPDATE_SAME_MATERIAL_LIST, hier manuell), letzte Meldung.
        self.auto_refill = bool(self.cfg.get("runout_refill"))
        self.same_material = []
        self.runout_last = ""
        for name, func, desc in (
                ("KTWO_BOX_PRE_OPT", self.cmd_pre_opt,
                 "Wechsel beginnen (= M8200 P) [TNN=T1A] [Z_HOP=mm]"),
                ("KTWO_BOX_CUT", self.cmd_cut,
                 "Filament schneiden (= M8200 C), Kopfbewegung"),
                ("KTWO_BOX_GO_EXTRUDE_POS", self.cmd_go_extrude_pos,
                 "Kopf in den Abfallschacht (Stock BOX_GO_TO_EXTRUDE_POS)"),
                ("KTWO_BOX_SAFE_POS", self.cmd_safe_pos,
                 "Abfallschacht verlassen (Stock BOX_MOVE_TO_SAFE_POS)"),
                ("KTWO_BOX_RETRUDE", self.cmd_retrude,
                 "Filament in die Box zurück (= M8200 R) [SLOT=] [LENGTH=]"),
                ("KTWO_BOX_EXTRUDE", self.cmd_extrude,
                 "Filament einziehen (= M8200 L I..) TNN=T1A|SLOT=A"),
                ("KTWO_BOX_FLUSH", self.cmd_flush,
                 "Spülen + Abstreifen (= M8200 F) [LENGTH=] [VELOCITY=] [TEMP=]"),
                ("KTWO_BOX_WIPE", self.cmd_wipe,
                 "Düse am Schacht abstreifen (Stock-T0-Beispiel 10.1-10.3)"),
                ("KTWO_BOX_WASTE", self.cmd_waste,
                 "Abfallschacht-Prüfung (= M8200 W, hier nur Hinweis)"),
                ("KTWO_BOX_END_OPT", self.cmd_end_opt,
                 "Wechsel abschließen (= M8200 O) [RETURN=0|1]"),
                ("KTWO_BOX_WAIT", self.cmd_wait,
                 "Box-Zustand abwarten (IDLE/PRINT) [TIMEOUT=s]"),
                ("KTWO_BOX_ERROR_CLEAR", self.cmd_error_clear,
                 "Wechsel-Fehler quittieren (+ Box 0x04 IDLE mit allow_write)"),
                ("KTWO_BOX_RESUME_FIX", self.cmd_resume_fix,
                 "Nach RESUME: Z auf Höhe vor dem abgebrochenen Wechsel "
                 "[CLEAR=1] (CLEAR aus PAUSE: Z steckt im Rückfahrziel)"),
                ("KTWO_BOX_RESUME_CHECK", self.cmd_resume_check,
                 "RESUME-Prüfung nach abgebrochenem Wechsel: Kopf leer -> "
                 "gesperrt bzw. nachholen (resume_redo_change) [MOVE=0|1] "
                 "[TEMP=] (aus RESUME)"),
                ("KTWO_BOX_RUNOUT", self.cmd_runout,
                 "Runout: aus der CFS nachladen (= Stock BOX_CHECK_MATERIAL_"
                 "REFILL) [SLOT=A..D] [TEMP=] [DRY_RUN=1]"),
                ("KTWO_BOX_AUTO_REFILL", self.cmd_auto_refill,
                 "Auto-Nachladen an/aus (= Stock BOX_ENABLE_AUTO_REFILL) "
                 "[ENABLE=0|1]"),
                ("KTWO_BOX_SAME_MATERIAL", self.cmd_same_material,
                 "Gleich-Material-Gruppen (= Stock BOX_UPDATE_SAME_MATERIAL_"
                 "LIST) [LIST=T1A,T1C/T1B,T1D] [CLEAR=1]")):
            self.gcode.register_command(name, func, desc=desc)

    def _ensure_bus(self):
        # ANNAHME: EIN geteilter RS485-Bus (dieselbe Instanz wie [k2_motor],
        # Halbduplex-Lock über die ganze Transaktion). Betreiber stellt ihn
        # als printer-Objekt 'k2_485' bereit (Upstream hat keinen zentralen
        # 485-Manager); ohne Bus bleibt das Modul im Diagnose-Modus.
        if self._bus is not None:
            return self._bus
        for name in ("k2_485", "k2_rs485", "serial485"):
            try:
                self._bus = self.printer.lookup_object(name)
                return self._bus
            except Exception:
                continue
        raise RuntimeError("RS485-Bus nicht verdrahtet: Abschnitt [k2_485] fehlt "
                           "in printer.cfg (printer-Objekt 'k2_485', dieselbe "
                           "Instanz wie [k2_motor]/[k2_belt])")

    def _ensure_controller(self):
        if self.controller is not None:
            return self.controller
        bus = self._ensure_bus()
        if hasattr(self.reactor, "pause") and hasattr(self.reactor, "monotonic"):
            # Lange Box-Antworten nicht im Reactor blockieren (s. Proxy).
            bus = ReactorBusProxy(bus, self.reactor)
        self.controller = K2BoxController(
            self.cfg, bus, pause=reactor_sleep(self.reactor),
            logger=self.gcode,
            send_event=getattr(self.printer, "send_event", None))
        if hasattr(self.reactor, "monotonic"):
            self.controller.clock = self.reactor.monotonic
        return self.controller

    def handle_connect(self):
        try:
            self._ensure_controller()
        except Exception as exc:
            try:
                self.gcode.respond_info("[k2_box] Connect ohne Bus: %s" % exc)
            except Exception:
                pass
            return
        # Poll-Timer erst nach k2_motor:ready starten (Adresse!). ANNAHME
        # Upstream-API: reactor.register_timer(cb, reactor.NOW).
        if self.cfg.get("auto_init"):
            try:
                self.gcode.respond_info(
                    "[k2_box] bereit (warte auf k2_motor:ready für Auto-Addr)")
            except Exception:
                pass
        if self.material_db_info:
            try:
                self.gcode.respond_info("[k2_box] %s" % self.material_db_info)
            except Exception:
                pass

    def handle_motor_ready(self, *args):
        try:
            ctl = self._ensure_controller()
        except Exception as exc:
            try:
                self.gcode.respond_info("[k2_box] kein Bus für Auto-Addr: %s"
                                        % exc)
            except Exception:
                pass
            return
        res = ctl.on_motor_ready()
        try:
            self.gcode.respond_info("[k2_box] Auto-Addr: %s" % (res,))
        except Exception:
            pass
        # Zyklischer 0x0a-Poll (ANNAHME Reactor-Timer, Intervall poll_interval).
        try:
            if hasattr(self.reactor, "register_timer"):
                interval = float(self.cfg.get("poll_interval",
                                              POLL_INTERVAL_DEFAULT))

                def _poll(eventtime):
                    if not ctl.busy:  # kein Poll während langer Box-Vorgänge
                        try:
                            ctl.poll_status()
                        except Exception:
                            pass
                    return eventtime + interval

                self.reactor.register_timer(
                    _poll, getattr(self.reactor, "NOW", 0))
        except Exception:
            pass

    def get_status(self, eventtime=None):
        if self.controller is None:
            st = {"ready": False, "state_name": "unknown",
                  "loaded_slot": None, "busy": False}
        else:
            st = self.controller.get_status(eventtime)
        loaded = st.get("loaded_slot")
        st.update({
            "allow_write": bool(self.cfg.get("allow_write")),
            "allow_move": bool(self.cfg.get("allow_move")),
            # Wechsel vollständig freigegeben (macros.cfg T0: sonst Einfarb)
            "change_enabled": bool(self.cfg.get("allow_write")
                                   and self.cfg.get("allow_move")),
            "loaded_tnn": ("T%d%s" % (self.cfg.get("box_addr", 1), loaded)
                           if loaded else ""),
            "chain_active": bool(self.chain.get("active")),
            "chain_tnn": self.chain.get("tnn") or "",
            "chain_skip": bool(self.chain.get("skip")),
            "chain_error": self.chain_error or "",
            "pending_resume_z": self.pending_resume_z,
            # RESUME nach abgebrochenem Wechsel (cmd_resume_check):
            # change_aborted -> RESUME-Makro ruft KTWO_BOX_RESUME_CHECK;
            # resume_tnn = Ziel des abgebrochenen Wechsels (Stock-Name);
            # head_filament = Kopfsensor True/False/None (None = unbekannt)
            "change_aborted": bool(self.aborted),
            "resume_tnn": (self.aborted or {}).get("tnn") or "",
            "head_filament": self._head_filament(),
            "resume_redo_change": bool(self.cfg.get("resume_redo_change")),
            # Auto-Nachladen (Stock-Statusnamen 'auto_refill'/'same_material')
            "auto_refill": 1 if self.auto_refill else 0,
            "same_material": [["T%d%s" % (self.cfg.get("box_addr", 1), x)
                               for x in g] for g in self.same_material],
            "runout_last": self.runout_last or "",
            # Material/Spülen (17.10.2026): prime_speed > 0 = RESUME-
            # Vorextrusion aus der Material-DB (macros.cfg _PI_RESUME_PRIME),
            # 0 = Makro-Wert resume_prime_speed (DB aus)
            "material_db": self.material_db is not None,
            "material_db_info": self.material_db_info or "",
            "remain_material": dict(self.remain_material),
            "prime_speed": self._prime_speed(),
            "flush_mode": self.cfg.get("flush_mode", "color"),
            "flush_last": self.flush_last or "",
        })
        return st

    # -- G-Code-Helfer ---------------------------------------------------
    @staticmethod
    def _slot_param(gcmd):
        for key in ("SLOT", "slot"):
            try:
                val = gcmd.get(key, None)
            except Exception:
                val = None
            if val is not None:
                return val
        raise ValueError("SLOT=A..D fehlt (z.B. KTWO_BOX_LOAD SLOT=B)")

    def cmd_status(self, gcmd):
        try:
            ctl = self._ensure_controller()
        except Exception as exc:
            gcmd.respond_info("[k2_box] kein Bus: %s" % exc)
            return
        try:
            poll = ctl.poll_status()
        except Exception as exc:
            gcmd.respond_info("[k2_box] Poll-Fehler: %s" % exc)
            return
        st = ctl.get_status()
        gcmd.respond_info(
            "[k2_box] Zustand=%s Hub=0x%02x Temp=%s °C Feuchte=%s %% Slots=%s" % (
                st.get("state_name"), st.get("hub_mask") or 0,
                st.get("temp_c"), st.get("rh"), st.get("slots")))

    def cmd_load(self, gcmd):
        slot = self._slot_param(gcmd)
        try:
            dry = bool(gcmd.get_int("DRY_RUN", 0))
        except Exception:
            dry = False
        try:
            ctl = self._ensure_controller()
        except Exception as exc:
            gcmd.respond_info("[k2_box] kein Bus: %s" % exc)
            return
        if dry or not self.cfg.get("allow_write"):
            ctl2 = ctl
            try:
                res = ctl2.do_load(slot, dry_run=True)
                gcmd.respond_info("[k2_box] Trockenlauf (nichts gesendet): %s"
                                  % (res.get("plan"),))
            except Exception as exc:
                gcmd.respond_info("[k2_box] %s" % exc)
            if not dry:
                gcmd.respond_info("[k2_box] ALLOW_WRITE=0 (allow_write=False): "
                                  "nur gelesen, nichts geschrieben.")
            return
        try:
            res = ctl.do_load(slot)
        except CfsStatusError as exc:
            msg = ("[k2_box] Load SLOT=%s abgebrochen: Box-Status 0x%02x (%s)" % (
                slot, exc.status, exc.name))
            gcmd.respond_info(msg)
            if self.cfg.get("shutdown_on_failure"):
                raise gcmd.error(msg) if hasattr(gcmd, "error") else RuntimeError(msg)
            return
        except (ValueError, PermissionError) as exc:
            gcmd.respond_info("[k2_box] %s" % exc)
            return
        except (RuntimeError, OSError) as exc:
            # RS485-Bus/Port ([k2_485] Port fehlt/gezogen, Timeout): G-Code-
            # Fehler (bricht Makro/Druck ab) statt "Internal error"+Shutdown.
            msg = "[k2_box] Load SLOT=%s: RS485-Bus-Fehler: %s" % (slot, exc)
            gcmd.respond_info(msg)
            raise gcmd.error(msg) if hasattr(gcmd, "error") else RuntimeError(msg)
        gcmd.respond_info("[k2_box] Load SLOT=%s ok (Odometer %s mm)" % (
            res.get("slot"), res.get("odo_mm")))

    def cmd_unload(self, gcmd):
        slot = self._slot_param(gcmd)
        try:
            dry = bool(gcmd.get_int("DRY_RUN", 0))
        except Exception:
            dry = False
        try:
            ctl = self._ensure_controller()
        except Exception as exc:
            gcmd.respond_info("[k2_box] kein Bus: %s" % exc)
            return
        if dry or not self.cfg.get("allow_write"):
            try:
                res = ctl.do_unload(slot, dry_run=True)
                gcmd.respond_info("[k2_box] Trockenlauf (nichts gesendet): %s"
                                  % (res.get("plan"),))
            except Exception as exc:
                gcmd.respond_info("[k2_box] %s" % exc)
            if not dry:
                gcmd.respond_info("[k2_box] ALLOW_WRITE=0 (allow_write=False): "
                                  "nur gelesen, nichts geschrieben.")
            return
        try:
            res = ctl.do_unload(slot)
        except CfsStatusError as exc:
            msg = ("[k2_box] Unload SLOT=%s abgebrochen: Box-Status 0x%02x (%s)"
                   % (slot, exc.status, exc.name))
            gcmd.respond_info(msg)
            if self.cfg.get("shutdown_on_failure"):
                raise gcmd.error(msg) if hasattr(gcmd, "error") else RuntimeError(msg)
            return
        except (ValueError, PermissionError) as exc:
            gcmd.respond_info("[k2_box] %s" % exc)
            return
        except (RuntimeError, OSError) as exc:
            # RS485-Bus/Port ([k2_485] Port fehlt/gezogen, Timeout): G-Code-
            # Fehler (bricht Makro/Druck ab) statt "Internal error"+Shutdown.
            msg = "[k2_box] Unload SLOT=%s: RS485-Bus-Fehler: %s" % (slot, exc)
            gcmd.respond_info(msg)
            raise gcmd.error(msg) if hasattr(gcmd, "error") else RuntimeError(msg)
        gcmd.respond_info("[k2_box] Unload SLOT=%s ok" % (res.get("slot"),))

    def cmd_preload(self, gcmd):
        slot = self._slot_param(gcmd)
        try:
            op = gcmd.get_int("OP", None)
        except Exception:
            op = None
        if op is None:
            try:
                op = gcmd.get("OP", None)
            except Exception:
                op = None
        try:
            dry = bool(gcmd.get_int("DRY_RUN", 0))
        except Exception:
            dry = False
        try:
            ctl = self._ensure_controller()
        except Exception as exc:
            gcmd.respond_info("[k2_box] kein Bus: %s" % exc)
            return
        if dry or not self.cfg.get("allow_write"):
            try:
                res = ctl.do_preload(slot, op if op is not None else 1,
                                     dry_run=True)
                gcmd.respond_info("[k2_box] Trockenlauf (nichts gesendet): %s"
                                  % (res.get("plan"),))
            except Exception as exc:
                gcmd.respond_info("[k2_box] %s" % exc)
            if not dry:
                gcmd.respond_info("[k2_box] ALLOW_WRITE=0 (allow_write=False): "
                                  "nur gelesen, nichts geschrieben.")
            return
        try:
            force = bool(gcmd.get_int("FORCE", 0))
        except Exception:
            force = False
        # Stock-Schutz (preload_block_reason): op 2/3 nur bei leerem
        # Kopfsensor, im Druck/Pause nur mit FORCE=1
        guard = {"head": self._head_filament(),
                 "printing": self._print_active(), "force": force}
        try:
            res = ctl.do_preload(slot, op, guard=guard)
        except CfsStatusError as exc:
            gcmd.respond_info("[k2_box] Preload abgebrochen: Box-Status 0x%02x "
                              "(%s)" % (exc.status, exc.name))
            return
        except (ValueError, PermissionError) as exc:
            gcmd.respond_info("[k2_box] %s" % exc)
            return
        except (RuntimeError, OSError) as exc:
            # RS485-Bus/Port ([k2_485] Port fehlt/gezogen, Timeout): G-Code-
            # Fehler (bricht Makro/Druck ab) statt "Internal error"+Shutdown.
            msg = "[k2_box] Preload SLOT=%s: RS485-Bus-Fehler: %s" % (slot, exc)
            gcmd.respond_info(msg)
            raise gcmd.error(msg) if hasattr(gcmd, "error") else RuntimeError(msg)
        gcmd.respond_info("[k2_box] Preload SLOT=%s OP=%s ok" % (
            res.get("slot"), res.get("op")))

    # =================================================================
    # Filamentwechsel – Klipper-Seite (T0–T15 / M8200-Kette)
    # =================================================================
    # Aufteilung (Beleg Live-Wechsel 12.10. 00:24–00:26, box.cfg T0-Beispiel):
    #   Box (RS485, allow_write): 0x04/0x0f/0x08/0x10/0x11/0x05 – Retrude,
    #     Einzug, Box-Zustand.
    #   Drucker (allow_move): Z-Hop, Schneidfahrt, Schacht, Heizen (M109),
    #     Extruder-Rückzug je p0, Stage-7-Greifen, Spülen, Abstreifen,
    #     Rückfahrt. Lüfter sichern/zurück machen die Makros (macros.cfg).
    @staticmethod
    def _new_chain():
        return {"active": False, "skip": False, "skip_reason": "",
                "tnn": "", "start_pos": None, "hop": 0.0,
                "paused_at_start": False, "source": "",
                "state_saved": False, "sensor_was_enabled": None,
                "orig_target": None, "temp_changed": False}

    def _now(self):
        try:
            return self.reactor.monotonic()
        except Exception:
            return time.monotonic()

    def _lookup(self, name):
        try:
            return self.printer.lookup_object(name)
        except Exception:
            return None

    def _obj_status(self, name):
        obj = self._lookup(name)
        if obj is None or not hasattr(obj, "get_status"):
            return {}
        try:
            return dict(obj.get_status(self._now()))
        except Exception:
            return {}

    def _print_active(self):
        # Pausiert zählt mit (auch ohne virtual_sdcard, z. B. PAUSE aus dem
        # Runout-Handler vor KTWO_BOX_RUNOUT).
        return (self._obj_status("print_stats").get("state") in (
            "printing", "paused") or self._is_paused())

    def _is_paused(self):
        return bool(self._obj_status("pause_resume").get("is_paused"))

    def _released(self):
        return bool(self.cfg.get("allow_write") and self.cfg.get("allow_move"))

    def _sensor_state(self, name):
        """[filament_switch_sensor <name>] -> True/False, None = fehlt."""
        if not name:
            return None
        st = self._obj_status("filament_switch_sensor %s" % (name,))
        if "filament_detected" not in st:
            return None
        return bool(st["filament_detected"])

    def _head_filament(self):
        return self._sensor_state(self.cfg.get("filament_sensor"))

    def _respond_error(self, msg):
        try:
            if hasattr(self.gcode, "respond_raw"):
                self.gcode.respond_raw("!! %s" % (msg,))
            else:
                self.gcode.respond_info(msg)
        except Exception:
            pass
        try:
            logging.error(msg)
        except Exception:
            pass

    def _script(self, lines):
        self.gcode.run_script_from_command("\n".join(lines))

    def _move(self, gcmd, title, lines, wrap=True):
        """Bewegungs-/Heiz-Zeilen ausführen (nur allow_move) – sonst Plan.

        wrap: in SAVE/RESTORE_GCODE_STATE einpacken (G90/G91 des Aufrufers
        bleiben erhalten; RESTORE ohne MOVE korrigiert die E-Basis)."""
        if not lines:
            return False
        if not self.cfg.get("allow_move"):
            gcmd.respond_info("[k2_box] PLAN %s (allow_move: False – nichts "
                              "bewegt/geheizt):\n  %s" % (title,
                                                          "\n  ".join(lines)))
            return False
        body = list(lines)
        if wrap:
            body = (["SAVE_GCODE_STATE NAME=%s" % STEP_STATE_NAME] + body
                    + ["RESTORE_GCODE_STATE NAME=%s" % STEP_STATE_NAME])
        try:
            self._script(body)
        except Exception:
            if wrap:
                try:
                    self._script(["RESTORE_GCODE_STATE NAME=%s"
                                  % STEP_STATE_NAME])
                except Exception:
                    pass
            raise
        return True

    def _plan_note(self):
        if self.cfg.get("allow_move") and self.cfg.get("allow_write"):
            return ""
        return " (Teil-Plan: allow_write=%s, allow_move=%s)" % (
            self.cfg.get("allow_write"), self.cfg.get("allow_move"))

    def _box_plan(self, gcmd, title, text):
        gcmd.respond_info("[k2_box] PLAN Box %s (allow_write: False – nichts "
                          "gesendet): %s" % (title, text))

    def _homed_ok(self):
        homed = str(self._obj_status("toolhead").get("homed_axes", ""))
        return all(a in homed for a in "xyz")

    def _gcode_pos(self):
        pos = self._obj_status("gcode_move").get("gcode_position")
        if pos is None:
            return None
        try:
            return [float(pos[0]), float(pos[1]), float(pos[2])]
        except Exception:
            return None

    def _max_accel(self):
        try:
            return float(self._obj_status("toolhead").get("max_accel"))
        except Exception:
            return None

    # --- Material (RFID-Etikett -> Material-DB), 17.10.2026 -------------
    def _cached_tag(self, letter):
        """(mat_id, col) aus dem Etikett-Cache, OHNE Bus (get_status)."""
        ctl = self.controller
        if ctl is None or not letter:
            return None
        m = rfid_material(ctl.tag_cache.get(letter))
        if m is None:
            m = rfid_material((ctl.slot_cache.get(letter) or {}).get("rfid"))
        return m

    def _slot_tag(self, letter):
        """(mat_id, col) des Slots: Cache, sonst RFID lesen (0x02, lesend);
        None = unbekannt (kein Bus, kein/kaputtes Etikett)."""
        if not letter:
            return None
        m = self._cached_tag(letter)
        if m is not None:
            return m
        try:
            return rfid_material(self._ensure_controller().get_rfid(letter))
        except Exception:
            return None

    def _nozzle_key(self):
        """Material in der Düse: remain_material.type (Stock, nach dem
        letzten Spülen), sonst Etikett des geladenen Slots (Cache)."""
        if self.remain_material.get("type"):
            return self.remain_material["type"]
        ctl = self.controller
        m = self._cached_tag(ctl.loaded_slot) if ctl is not None else None
        return m[0] if m else None

    def _mat(self, key):
        return material_params(self.material_db, key,
                               self.cfg["tn_extrude_temp"])

    def _temp_limit(self):
        """[extruder] max_temp − 10 K (DB-Temperaturen kappen), None = ?"""
        try:
            mt = float(self._obj_status("configfile")["settings"]
                       ["extruder"]["max_temp"])
        except Exception:
            return None
        return mt - TEMP_LIMIT_MARGIN

    def _base_temp(self, gcmd):
        """Basis der Wechsel-Temperatur: BASE_TEMP= (Nachholen/Runout:
        Drucktemperatur vor der Pause) oder aktuelles Extruder-Ziel."""
        b = gcmd.get_float("BASE_TEMP", None, minval=0.)
        if b is not None:
            return float(b)
        try:
            return float(self._obj_status("extruder").get("target") or 0.0)
        except Exception:
            return 0.0

    def _db_temp(self, mp, label, notes, field="temp"):
        """Material-Temperatur aus der DB (gekappt), sonst Tn_extrude_temp
        (Stock-Fallback) + Hinweis in notes (nur mit geladener DB)."""
        fb = float(self.cfg["tn_extrude_temp"])
        if self.material_db is None:
            return fb
        ok = mp.get("temp_known") if field == "temp" else mp.get("max_known")
        if not ok:
            notes.append("%s: Material %s – Fallback Tn_extrude_temp %s °C "
                         "(Stock)" % (label, (
                             "unbekannt (kein RFID-Etikett)" if not mp.get("key")
                             else "%s nicht in der Material-DB" % mp["key"]
                             if not mp.get("known") else
                             "%s ohne %s" % (mp["key"], "nozzle_temperature"
                                             if field == "temp" else "maxTemp")),
                             _n(fb)))
            return fb
        t = float(mp[field])
        lim = self._temp_limit()
        if lim is not None and t > lim:
            notes.append("%s: DB-Temperatur %s °C über [extruder] max_temp−%s "
                         "-> %s °C" % (label, _n(t), _n(TEMP_LIMIT_MARGIN),
                                       _n(lim)))
            t = lim
        return t

    def _respond_notes(self, gcmd, notes):
        for n in notes:
            try:
                gcmd.respond_info("[k2_box] HINWEIS %s" % (n,))
            except Exception:
                pass

    def _change_temp(self, gcmd, key=None, label="Wechsel"):
        """Wechsel-Temperatur (Rückzug/Einzug): TEMP= (fest) oder
        max(Basis, Material-Temperatur). Basis = BASE_TEMP= bzw. aktuelles
        Extruder-Ziel; Material-Temperatur = kvParam.nozzle_temperature aus
        der Material-DB ([k2_box] material_database), Schlüssel = RFID-
        material_type (mat_id) des Slots (Stock get_material_target_temp,
        live „get next material temp: 220“). Ohne DB bzw. unbekanntes
        Material: Tn_extrude_temp 220 (Stock-Fallback, mit DB + Hinweis).
        Die Temperatur steht NICHT im RFID-Etikett – nur der Schlüssel."""
        t = gcmd.get_float("TEMP", None)
        if t is not None:
            return float(t)
        notes = []
        mt = self._db_temp(self._mat(key), label, notes)
        self._respond_notes(gcmd, notes)
        return max(self._base_temp(gcmd), mt)

    def _prime_speed(self):
        """RESUME-Vorextrusion wie Stock RESUME_EXTERNAL
        (remain_material_velocity · 0,8 = vol/2,4·60·0,8, live 460 / 280):
        nur mit Material-DB; unbekanntes Material -> 360·0,8 = 288 (Stock-
        Fallback). 0.0 = DB aus -> macros.cfg resume_prime_speed (460)."""
        if self.material_db is None:
            return 0.0
        mp = self._mat(self._nozzle_key())
        if mp.get("vol"):
            return float(mp["vol"]) / 2.4 * 60.0 * 0.8
        return PRIME_FALLBACK_VELOCITY * 0.8

    def _grab_velocity(self, key):
        """Stage-7-Greifen (Extruder buffer_empty_len): tn_extrude_velocity,
        mit DB gedeckelt auf vol/2,4·60 (PAUSE-RESUME-MATERIAL-BEFUND C P3,
        TPU 50 mm/min). Pi-Erweiterung – Stock-Geschwindigkeit unbelegt."""
        v = float(self.cfg["tn_extrude_velocity"])
        if self.material_db is None:
            return v
        mp = self._mat(key)
        if mp.get("vol"):
            return min(v, float(mp["vol"]) / 2.4 * 60.0)
        return v

    def _nozzle_diameter(self):
        try:
            return float(self._obj_status("configfile")["settings"]
                         ["extruder"]["nozzle_diameter"])
        except Exception:
            return float(self.cfg["baseline_nozzle_diameter"])

    def _min_cruise_ratio(self):
        try:
            v = self._obj_status("toolhead").get("minimum_cruise_ratio")
            return None if v is None else float(v)
        except Exception:
            return None

    def _pin(self, name):
        """[output_pin <name>] -> (Wert 0..1, scale) oder None (fehlt)."""
        if not name:
            return None
        obj = self._lookup("output_pin %s" % (name,))
        if obj is None:
            return None
        try:
            val = float(obj.get_status(self._now()).get("value") or 0.0)
        except Exception:
            val = 0.0
        scale = getattr(obj, "scale", None)
        try:
            scale = float(scale) if scale else None
        except Exception:
            scale = None
        return val, scale

    def _extruder_target(self):
        """Aktuelles Extruder-Ziel in °C, None = nicht lesbar."""
        st = self._obj_status("extruder")
        if "target" not in st:
            return None
        try:
            return float(st.get("target") or 0.0)
        except Exception:
            return None

    def _heat(self, gcmd, temp):
        # orig_target = Ziel VOR dem ersten Heizen der Kette (vor M109, damit
        # es auch bei einem Abbruch während M109 stimmt – M109 setzt das Ziel
        # vor dem Warten). END_OPT stellt es wieder her, _fail gibt es an
        # PAUSE (CFS_TEMP, s. _pause_temp). Nicht lesbar -> 0.0 (= unbekannt).
        ch = self.chain
        if ch.get("active") and ch.get("orig_target") is None:
            t = self._extruder_target()
            ch["orig_target"] = 0.0 if t is None else t
        if self._move(gcmd, "Heizen", ["M109 S%s" % _n(temp)], wrap=False):
            if ch.get("active"):
                ch["temp_changed"] = True

    def _set_head_sensor(self, enable):
        name = self.cfg.get("filament_sensor")
        if not name or self._lookup("filament_switch_sensor %s" % name) is None:
            return
        try:
            self._script(["SET_FILAMENT_SENSOR SENSOR=%s ENABLE=%d"
                          % (name, 1 if enable else 0)])
        except Exception:
            pass

    def _check_tnn(self, raw):
        box, idx, letter, tnn = parse_tnn(raw)
        if box != int(self.cfg.get("box_addr", BOX_ADDR_DEFAULT)):
            raise BoxChangeError(
                "%s liegt in Box %d, konfiguriert ist nur Box %d ([k2_box] "
                "box_addr) – weitere Boxen ANNAHME/ungetestet" % (
                    tnn, box, int(self.cfg.get("box_addr", 1))))
        return box, idx, letter, tnn

    def _already_loaded(self, idx, letter):
        """Slot schon geladen? Hub-Sensor (0x08 [01], lesend) + Kopfsensor."""
        try:
            ctl = self._ensure_controller()
        except Exception:
            return False
        try:
            hub = ctl.hub_mask()
        except Exception:
            hub = None
        head = self._head_filament()
        if hub is None:
            return ctl.loaded_slot == letter and head is not False
        if hub == SLOT_MASKS[idx] and head is not False:
            ctl.loaded_slot = letter
            self._remember_tag(ctl, letter)
            return True
        if hub == 0:
            ctl.loaded_slot = None
        return False

    @staticmethod
    def _remember_tag(ctl, letter):
        """RFID-Etikett des geladenen Slots merken (lesend, best effort) –
        für das Auto-Nachladen, falls die leere Spule später nichts liefert."""
        try:
            ctl.get_rfid(letter)
        except Exception:
            pass

    def _go_extrude_pos(self, gcmd):
        c = self.cfg
        pos = self._gcode_pos()
        if pos is not None and abs(pos[0] - c["extrude_pos_x"]) < 0.5 \
                and abs(pos[1] - c["extrude_pos_y"]) < 0.5:
            return False
        lines = gcode_go_extrude_pos(c)
        # Kopf noch am Schneidhebel (abgebrochener Schnitt, Schritt von Hand
        # wiederholt): erst nur X heraus, sonst zöge "G0 Y345" den Kopf
        # seitlich am Hebel entlang.
        lead = gcode_leave_cut_zone(c, pos)
        if lead:
            lines = lines[:1] + lead + lines[1:]
        return self._move(gcmd, "Abfallschacht (BOX_GO_TO_EXTRUDE_POS)", lines)

    @staticmethod
    def _exc_text(exc):
        if isinstance(exc, CfsStatusError):
            return "Box-Status 0x%02x (%s)" % (exc.status, exc.name)
        txt = str(exc)
        return txt if txt else type(exc).__name__

    def _chain_cleanup(self, gcmd=None):
        """Nicht-mechanisches Aufräumen: Kopfsensor, G-Code-Zustand."""
        ch = self.chain
        if ch.get("sensor_was_enabled") is not None:
            self._set_head_sensor(ch["sensor_was_enabled"])
        if ch.get("state_saved"):
            try:
                self._script(["RESTORE_GCODE_STATE NAME=%s" % CHAIN_STATE_NAME])
            except Exception:
                pass
        self.chain = self._new_chain()

    def _resume_target(self):
        """Rückfahrziel für RESUME nach einem Wechsel-Fehler im LAUFENDEN
        Druck: die Druckposition, an der die Kette begann (PRE_OPT, vor dem
        Z-Hop) bzw. vor dem fehlgeschlagenen Einzelschritt (ohne Kette, z. B.
        M8200 C ohne P). Upstream-PAUSE_STATE hielte sonst die Fehlerposition
        (am Schneidhebel, im Schacht) – RESUME_BASE führe gerade dorthin und
        der Druck ginge dort weiter. None = kein eigenes Ziel nötig: Kette in
        einer Pause gestartet (PAUSE_STATE ist dann schon die Druckposition),
        Position unbekannt, oder Kopf steht noch dort (±0,01 mm)."""
        ch = self.chain
        if ch.get("active"):
            sp = None if ch.get("paused_at_start") else ch.get("start_pos")
        else:
            sp = self._step_start
        if sp is None:
            return None
        sp = [float(v) for v in sp[:3]]
        pos = self._gcode_pos()
        if pos is not None and max(abs(pos[i] - sp[i]) for i in range(3)) < 0.01:
            return None
        return sp

    def _pause_temp(self):
        """Druck-Zieltemperatur für ``PAUSE CFS_TEMP`` nach einem Abbruch
        im LAUFENDEN Druck -> (temp, hinweis).

        Hintergrund: das PAUSE-Makro merkt ``printer.extruder.target`` als
        hotend_temp (wie Stock PAUSE_EXTERNAL, live
        config/F008_CR0CN240319C13/gcode_macro.cfg:761) und RESUME heizt
        darauf (macros.cfg _PI_RESUME_PREPARE M109, Stock RESUME_EXTERNAL
        ebd. :885-892). Hat der Wechsel schon geheizt (_heat -> M109
        Wechsel-Temperatur max(Ziel, Material-/Tn_extrude_temp)), wäre das die
        Wechsel- statt der Drucktemperatur. temp = Ziel vor dem ersten
        Heizen (Kette: orig_target; Einzelschritt: _step_target), None =
        kein CFS_TEMP nötig/möglich:
        - noch nicht geheizt bzw. Ziel unverändert (±0,01 °C) -> PAUSE merkt
          ohnehin das richtige Ziel;
        - Ziel vor dem Wechsel 0/nicht lesbar -> UNBEKANNT: PAUSE merkt wie
          bisher das aktuelle Ziel (lieber auf Wechsel-Temperatur als kalt
          weiterdrucken), hinweis erklärt das."""
        ch = self.chain
        orig = ch.get("orig_target") if ch.get("active") else self._step_target
        if orig is None:
            return None, ""
        cur = self._extruder_target()
        if orig <= 0.0:
            if cur is not None and cur > 0.0:
                return None, (
                    " Druck-Zieltemperatur vor dem Wechsel unbekannt (0) – "
                    "RESUME heizt auf das aktuelle Ziel %s °C; ggf. vor "
                    "RESUME SET_GCODE_VARIABLE MACRO=_PI_PARAM "
                    "VARIABLE=hotend_temp VALUE=<°C>." % (_n(cur),))
            return None, ""
        if cur is not None and abs(cur - orig) < 0.01:
            return None, ""
        return float(orig), ""

    @staticmethod
    def _tool_of(tnn):
        """'T1B' -> 'T1' (Werkzeugnummer wie macros.cfg T0..T15)."""
        try:
            box, idx, _l, _t = parse_tnn(tnn)
        except ValueError:
            return ""
        return "T%d" % ((box - 1) * len(SLOT_LETTERS) + idx)

    def _record_abort(self, gcmd, name):
        """Abgebrochenen Wechsel für RESUME merken (Stock recode_err ->
        save_resume_tnn): Schritt, Ziel-TNN, Herkunft der Kette. Ein
        Einzelschritt ohne eigene Kette/TNN (z. B. KTWO_BOX_CUT von Hand in
        der Pause) behält Ziel und Herkunft des früheren Abbruchs."""
        ch = self.chain
        old = self.aborted or {}
        if ch.get("active") and ch.get("tnn"):
            tnn, src = ch["tnn"], ch.get("source") or ""
        else:
            tnn = ""
            try:
                raw = gcmd.get("TNN", None)
            except Exception:
                raw = None
            if raw:
                try:
                    tnn = parse_tnn(raw)[3]
                except ValueError:
                    tnn = ""
            if not tnn:
                tnn = old.get("tnn") or ""
            src = (ch.get("source") if ch.get("active") else "") or (
                old.get("source", "") if tnn and tnn == old.get("tnn") else "")
        self.aborted = {"step": name, "tnn": tnn, "source": src or ""}

    def _fail(self, gcmd, name, text, record=True):
        """Fehler im Wechsel: im Druck PAUSE (kein Shutdown, kein Abbruch),
        Folgeschritte überspringen; sonst Aufräumen + G-Code-Fehler.

        Im Druck merkt ``_record_abort`` den abgebrochenen Wechsel (Ziel-TNN):
        RESUME prüft dann den Kopfsensor (cmd_resume_check, leer = gesperrt
        bzw. Nachholen). ``record=False`` nur für eine Ablehnung ohne jede
        Aktion (PRE_OPT bei offenem Fehler).

        Löst DIESER Fehler die Pause aus (Druck lief), bekommt PAUSE das
        Rückfahrziel ``CFS_RETURN_X/Y/Z`` (= _resume_target, Z ohne Hop):
        das PAUSE-Makro (macros.cfg) merkt es statt der Fehlerposition und
        quittiert mit ``KTWO_BOX_RESUME_FIX CLEAR=1`` (Z ist Teil des
        Ziels); ohne dieses Makro (Upstream-PAUSE ignoriert die Parameter)
        bleibt der alte Z-Fallback pending_resume_z. In einer schon
        bestehenden Pause (Wiederholung von Hand, Runout-Kette) weder Ziel
        noch Z-Fallback – PAUSE_STATE ist dort bereits die Druckposition.

        Temperatur (Fix 17.10.2026 spät): hatte der Wechsel schon geheizt,
        bekommt die NEUE Pause ``CFS_TEMP=<Ziel vor dem Wechsel>``
        (_pause_temp) – das PAUSE-Makro merkt es als hotend_temp statt der
        Wechsel-Temperatur. In einer bestehenden Pause kein CFS_TEMP:
        hotend_temp stammt dort schon aus der ersten Pause."""
        msg = "[k2_box] %s fehlgeschlagen: %s" % (name, text)
        if self._print_active() and self.cfg.get("pause_on_failure"):
            if not self.chain_error:
                self.chain_error = msg
            if record:
                self._record_abort(gcmd, name)
            ch = self.chain
            was_paused = self._is_paused()
            if ch.get("start_pos") is not None:
                self._keep_start_pos = list(ch["start_pos"])
            target = None if was_paused else self._resume_target()
            if not was_paused and ch.get("hop") and ch.get("active") \
                    and ch.get("start_pos") is not None:
                self.pending_resume_z = float(ch["start_pos"][2])
            # Drucktemperatur vor dem Wechsel (nur für eine NEUE Pause: in
            # einer bestehenden hat PAUSE hotend_temp schon gemerkt – ein
            # Wiederholungs-T<n> heizt von der Pausentemperatur aus)
            temp, temp_hint = (None, "") if was_paused else self._pause_temp()
            hint = ""
            if target is not None:
                hint = (" Rückfahrziel für RESUME = Druckposition vor dem "
                        "Wechsel X%s Y%s Z%s (nicht die Fehlerposition)."
                        % tuple(_n(v) for v in target))
            if temp is not None:
                hint += (" RESUME heizt auf die Drucktemperatur vor dem "
                         "Wechsel %s °C (nicht die Wechsel-Temperatur %s °C)."
                         % (_n(temp), _n(self._extruder_target() or 0.0)))
            hint += temp_hint
            if was_paused and ch.get("active") and ch.get("temp_changed") \
                    and ch.get("orig_target") is not None:
                # Abbruch IN einer Pause (Wiederholung von Hand, Nachholen im
                # RESUME, Runout-Kette): Düse zurück auf das Ziel vor diesem
                # Wechsel (= Pausentemperatur, macros.cfg PAUSE M104 S140) wie
                # END_OPT im Erfolgsfall – sonst stünde sie die ganze Pause
                # auf Wechsel-Temperatur. RESUME heizt aus hotend_temp.
                try:
                    if self._move(gcmd, "Zieltemperatur zurück (Abbruch in "
                                  "der Pause)", ["M104 S%s" % _n(
                                      ch["orig_target"])], wrap=False):
                        ch["temp_changed"] = False
                        hint += (" Düse zurück auf %s °C (Ziel vor diesem "
                                 "Wechsel)." % (_n(ch["orig_target"]),))
                except Exception as exc:
                    hint += " Zieltemperatur zurück fehlgeschlagen: %s." % (exc,)
            # Reihenfolge (Fix 17.10. spät): ein T<n> bei offenem Fehler lehnt
            # PRE_OPT ab -> erst quittieren, dann wiederholen. RESUME prüft
            # danach den Kopfsensor (leer = gesperrt, cmd_resume_check).
            self._respond_error(
                "%s – Druck wird PAUSIERT (kein Abbruch). Ursache beheben, "
                "KTWO_BOX_ERROR_CLEAR, Wechsel (T<n>) bzw. Schritt von Hand "
                "wiederholen, dann RESUME (gesperrt, solange der "
                "Kopf-Filamentsensor leer meldet).%s" % (msg, hint))
            if not was_paused:
                line = "PAUSE"
                if target is not None:
                    line += (" CFS_RETURN_X=%s CFS_RETURN_Y=%s CFS_RETURN_Z=%s"
                             % tuple(_n(v) for v in target))
                if temp is not None:
                    line += " CFS_TEMP=%s" % (_n(temp),)
                # PAUSE-Makro ruft KTWO_BOX_RESUME_FIX CLEAR=1: diese Pause
                # stammt aus _fail -> den gerade gemerkten Abbruch behalten
                self._pause_from_fail = True
                try:
                    self._script([line])
                except Exception as exc:
                    self._respond_error("[k2_box] PAUSE fehlgeschlagen: %s"
                                        % (exc,))
                finally:
                    self._pause_from_fail = False
            return None
        self._chain_cleanup(gcmd)
        if hasattr(gcmd, "error"):
            raise gcmd.error(msg)
        raise RuntimeError(msg)

    def _run_step(self, gcmd, name, body, chain_step=True, need_release=True):
        ch = self.chain
        if chain_step and ch.get("active") and (ch.get("skip")
                                                or self.chain_error):
            why = (ch.get("skip_reason") if ch.get("skip")
                   else "Wechsel-Fehler offen")
            gcmd.respond_info("[k2_box] %s übersprungen (%s)" % (name, why))
            return None
        if need_release and self._print_active() and not self._released():
            return self._fail(gcmd, name, (
                "Filamentwechsel im Druck nicht freigegeben ([k2_box] "
                "allow_write=%s, allow_move=%s) – nichts ausgeführt" % (
                    self.cfg.get("allow_write"), self.cfg.get("allow_move"))))
        # Einzelschritt ohne Kette im laufenden Druck: Position merken
        # (Rückfahrziel, falls der Schritt abbricht – s. _resume_target),
        # ebenso das Extruder-Ziel (Drucktemperatur für PAUSE CFS_TEMP).
        single = (not ch.get("active") and self._print_active()
                  and not self._is_paused())
        self._step_start = self._gcode_pos() if single else None
        self._step_target = self._extruder_target() if single else None
        try:
            return body(gcmd)
        except Exception as exc:
            return self._fail(gcmd, name, self._exc_text(exc))
        finally:
            self._step_start = None
            self._step_target = None

    # -- G-Codes ---------------------------------------------------------
    def cmd_pre_opt(self, gcmd):
        if self.chain.get("active"):  # alte Kette ohne END_OPT
            self._chain_cleanup(gcmd)
        ch = self._new_chain()
        ch["active"] = True
        # Herkunft der Kette: T = T<n>-Befehl (macros.cfg _KTWO_TOOL_CHANGE
        # bzw. Nachholen im RESUME) – nur dieser Wechsel wird beim RESUME
        # nachgeholt (Stock: resume_tnn nur für Tn_action-Fehler, nicht für
        # die M8200-Kette, BOOTLOADER-HOST-BEFUND §5.1); leer = M8200/Runout/
        # von Hand.
        ch["source"] = str(gcmd.get("SOURCE", "") or "").strip().upper()
        self.chain = ch
        if self.chain_error:
            # Ablehnung ohne jede Aktion: den gemerkten Abbruch (Ziel-TNN)
            # NICHT durch diesen leeren Versuch ersetzen (record=False)
            return self._fail(gcmd, "PRE_OPT", (
                "Wechsel-Fehler offen (%s) – erst KTWO_BOX_ERROR_CLEAR, dann "
                "den Wechsel wiederholen" % (self.chain_error,)), record=False)

        def body(gcmd):
            c = self.cfg
            hop = gcmd.get_float("Z_HOP", c["change_z_hop"], minval=0.,
                                 maxval=5.)
            tnn_raw = gcmd.get("TNN", None)
            if tnn_raw:
                _b, idx, letter, tnn = self._check_tnn(tnn_raw)
                ch["tnn"] = tnn
                if self._already_loaded(idx, letter):
                    ch["skip"] = True
                    ch["skip_reason"] = "%s ist bereits geladen" % (tnn,)
                    gcmd.respond_info("[k2_box] PRE_OPT: %s bereits geladen – "
                                      "kein Wechsel" % (tnn,))
                    return
            if c.get("allow_move") and not self._homed_ok():
                raise BoxChangeError("Achsen nicht referenziert (homed_axes=%r)"
                                     " – erst G28" % (self._obj_status(
                                         "toolhead").get("homed_axes", ""),))
            # Kopfsensor sperren (Stock disable_filament_sensor): beim
            # Rückzug fällt er ab -> Upstream würde sonst pausieren.
            sens = self._obj_status("filament_switch_sensor %s"
                                    % (c.get("filament_sensor"),))
            if "enabled" in sens:
                ch["sensor_was_enabled"] = bool(sens["enabled"])
                self._set_head_sensor(False)
            # Startposition: Wiederholungs-T<n> IN der Pause nach einem
            # Abbruch -> Startposition des abgebrochenen Wechsels; sonst (Druck
            # läuft, kein Druck) die aktuelle Position und eine alte Start-
            # position verwerfen (Fix 17.10.: nach CANCEL/RESUME hätte END_OPT
            # sonst zur Position eines früheren Wechsels zurückgefahren).
            ch["paused_at_start"] = self._is_paused()
            if ch["paused_at_start"] and self._keep_start_pos:
                ch["start_pos"] = list(self._keep_start_pos)
            else:
                self._keep_start_pos = None
                ch["start_pos"] = self._gcode_pos()
            self._script(["SAVE_GCODE_STATE NAME=%s" % CHAIN_STATE_NAME])
            ch["state_saved"] = True
            if hop > 0:
                zmax = None
                try:
                    zmax = float(self._obj_status("toolhead")
                                 .get("axis_maximum")[2])
                except Exception:
                    zmax = None
                pos = self._gcode_pos()
                if zmax is not None and pos is not None:
                    hop = max(0.0, min(hop, zmax - pos[2]))
                if hop > 0 and self._move(gcmd, "Z-Hop", [
                        "G91", "G1 Z%s F%s" % (_n(hop), _n(c["z_velocity"])),
                        "G90"]):
                    ch["hop"] = hop
            gcmd.respond_info("[k2_box] Wechsel %s begonnen (allow_write=%s, "
                              "allow_move=%s)" % (ch.get("tnn") or "(ohne TNN)",
                                                  c.get("allow_write"),
                                                  c.get("allow_move")))
        return self._run_step(gcmd, "PRE_OPT", body, chain_step=False)

    def cmd_cut(self, gcmd):
        def body(gcmd):
            c = self.cfg
            old = self._max_accel()
            cs = c.get("cut_sensor")
            if c.get("allow_move") and cs and self._sensor_state(cs) is False:
                raise BoxChangeError("Schneidhebel-Sensor %s meldet vor dem "
                                     "Schnitt nicht bereit" % (cs,))
            try:
                self._move(gcmd, "Schneiden (CR_BOX_CUT)", gcode_cut(c, old))
            except Exception:
                if c.get("allow_move") and old is not None:
                    try:
                        self._script(["SET_VELOCITY_LIMIT ACCEL=%s" % _n(old)])
                    except Exception:
                        pass
                raise
            if c.get("allow_move") and cs and self._sensor_state(cs) is False:
                raise BoxChangeError("Schneidhebel-Sensor %s nach dem Schnitt "
                                     "nicht zurück (Stock „cut to return“)"
                                     % (cs,))
            gcmd.respond_info("[k2_box] CUT: Schneidfahrt X%s -> X%s -> X%s%s"
                              % (_n(c["pre_cut_pos_x"]), _n(c["cut_pos_x"]),
                                 _n(c["pre_cut_pos_x"]), self._plan_note()))
        return self._run_step(gcmd, "CUT", body)

    def cmd_go_extrude_pos(self, gcmd):
        def body(gcmd):
            self._go_extrude_pos(gcmd)
        return self._run_step(gcmd, "GO_EXTRUDE_POS", body)

    def cmd_safe_pos(self, gcmd):
        def body(gcmd):
            self._move(gcmd, "Schacht verlassen (BOX_MOVE_TO_SAFE_POS)",
                       gcode_safe_pos(self.cfg))
        return self._run_step(gcmd, "SAFE_POS", body)

    def cmd_retrude(self, gcmd):
        def body(gcmd):
            c = self.cfg
            slot = gcmd.get("SLOT", None)
            length = gcmd.get_float("LENGTH", None, minval=0.)
            steps = retrude_extruder_steps(c, length)
            vel = c["tn_retrude_velocity"]
            self._go_extrude_pos(gcmd)
            if self._head_filament() is not False:  # Stock: nur mit Filament
                # Material in der Düse (remain_material bzw. geladener Slot)
                self._heat(gcmd, self._change_temp(
                    gcmd, self._nozzle_key() if self.material_db is not None
                    else None, "Rückzug"))

            def _extr(i):
                if i < len(steps):
                    self._move(gcmd, "Extruder-Rückzug %d/%d" % (i + 1, len(steps)),
                               gcode_extruder_move(steps[i], vel) + ["M400"])

            if not c.get("allow_write"):
                self._box_plan(gcmd, "RETRUDE", (
                    "0x08 [01] Hub -> 0x04 [00 01] -> 0x08 [00] -> 0x11 [m 00] "
                    "x%d (nach p0 1-%d je Extruder %s mm F%s) -> 0x11 [m 01]"
                    % (RETRUDE_REPEATS_DEFAULT, len(steps), _n(c["tn_retrude"]),
                       _n(vel))))
                for i in range(len(steps)):
                    _extr(i)
                return
            ctl = self._ensure_controller()
            ctl.wait_box_ready(c["box_wait_timeout"], c["box_wait_interval"])
            if slot is None:
                hub = ctl.hub_mask()
                if hub == 0:
                    ctl.loaded_slot = None
                    gcmd.respond_info("[k2_box] RETRUDE: Hub leer – kein "
                                      "Box-Rückzug nötig")
                    return
                if hub not in SLOT_MASKS:
                    raise BoxChangeError("Hub meldet mehrere Slots (0x%02x)"
                                         % (hub,))
                slot = SLOT_LETTERS[SLOT_MASKS.index(hub)]
            res = ctl.do_retrude(slot, after_p0=_extr)
            for i in range(RETRUDE_REPEATS_DEFAULT, len(steps)):
                _extr(i)
            if self._head_filament() is True:
                gcmd.respond_info("[k2_box] WARNUNG: Kopfsensor meldet nach dem "
                                  "Rückzug noch Filament")
            gcmd.respond_info("[k2_box] RETRUDE Slot %s ok (Hub frei)"
                              % (res.get("slot"),))
        return self._run_step(gcmd, "RETRUDE", body)

    def cmd_extrude(self, gcmd):
        def body(gcmd):
            c = self.cfg
            tnn_raw = gcmd.get("TNN", None)
            slot = gcmd.get("SLOT", None)
            if tnn_raw:
                _b, idx, letter, tnn = self._check_tnn(tnn_raw)
            elif slot is not None:
                idx, letter, _m = parse_slot(slot)
                tnn = "T%d%s" % (int(c.get("box_addr", 1)), letter)
            else:
                raise ValueError("TNN=T1A..T1D oder SLOT=A..D fehlt")
            mask = SLOT_MASKS[idx]
            # Ziel-Material nur mit Material-DB nachschlagen (sonst kein
            # zusätzlicher RFID-Lesezugriff, Verhalten wie bisher)
            key = None
            if self.material_db is not None:
                tag = self._slot_tag(letter)
                key = tag[0] if tag else None
            self._go_extrude_pos(gcmd)
            self._heat(gcmd, self._change_temp(gcmd, key, "Einzug %s" % tnn))
            grab = gcode_extruder_move(c["buffer_empty_len"],
                                       self._grab_velocity(key))
            if not c.get("allow_write"):
                self._box_plan(gcmd, "EXTRUDE %s" % tnn, (
                    "0x04 [00 01] -> 0x0f [01] -> 0x08 [00] -> 0x10 [%02x 00 00] "
                    "-> [%02x 04 00] -> [%02x 05 00] alle %s s bis Kopfsensor "
                    "(max %s s) -> [%02x 06 00] -> Stage 7 Extruder +%s mm -> "
                    "[%02x 07 03] -> 0x05 -> 0x04 [%02x 00] -> 0x0f [00]" % (
                        mask, mask, mask, _n(c["extrude_poll_interval"]),
                        _n(c["extrude_sensor_timeout"]), mask,
                        _n(c["buffer_empty_len"]), mask, mask)))
                self._move(gcmd, "Stage 7: Extruder greift (Trockenlauf)",
                           grab + ["M400"])
                return
            ctl = self._ensure_controller()
            ctl.wait_box_ready(c["box_wait_timeout"], c["box_wait_interval"])
            hub = ctl.hub_mask()
            head = self._head_filament()
            if hub and hub != mask:
                other = (SLOT_LETTERS[SLOT_MASKS.index(hub)]
                         if hub in SLOT_MASKS else "0x%02x" % hub)
                raise BoxChangeError("Hub belegt durch Slot %s – erst "
                                     "KTWO_BOX_RETRUDE (CFS blockiert sonst "
                                     "alle Einzüge)" % (other,))
            if head is True:
                if hub == mask:
                    ctl.loaded_slot = letter
                    gcmd.respond_info("[k2_box] EXTRUDE: %s bereits geladen"
                                      % (tnn,))
                    return
                raise BoxChangeError("Kopfsensor meldet noch Filament – erst "
                                     "KTWO_BOX_CUT + KTWO_BOX_RETRUDE")
            sensor_fn = None
            if head is not None:
                sensor_fn = lambda: self._head_filament() is True  # noqa: E731
            else:
                gcmd.respond_info("[k2_box] WARNUNG: kein Kopfsensor "
                                  "(filament_sensor) – feste %d Odometer-Polls "
                                  "(ANNAHME)" % (ODO_POLLS_DEFAULT,))

            def _stage7():
                # Ohne M400: die Bewegung läuft, während mode 7 prüft.
                self._move(gcmd, "Stage 7: Extruder greift", grab)

            res = ctl.do_extrude(
                letter, sensor_fn=sensor_fn,
                sensor_timeout=c["extrude_sensor_timeout"],
                poll_interval=c["extrude_poll_interval"],
                stage7_fn=_stage7, stage7_delay=c["stage7_delay"])
            self._move(gcmd, "Bewegungen abwarten", ["M400"], wrap=False)
            self._remember_tag(ctl, letter)
            odo = res.get("odo_mm") or []
            gcmd.respond_info("[k2_box] EXTRUDE %s ok (Odometer %s mm, %d Polls)"
                              % (tnn, round(odo[-1], 1) if odo else "?",
                                 len(odo)))
        return self._run_step(gcmd, "EXTRUDE", body)

    # --- Spülen wie Stock (17.10.2026, FLUSH-BEFUND-2026-10-17.md) -------
    # Stock MCMBW.material_change_flush (Z.4494–4517): M106 P0 S50 ->
    # Spülmenge get_flush_len -> Geschwindigkeit get_flush_velocity ->
    # Temperaturen get_flush_temp/get_flush_max_temp -> quickly_wait_heating
    # -> Tn_Extrude: je Abschnitt (cal_flush_list) Extrusion 20 %/80 % ->
    # blow -> nozzle_clean(circle=False), letzter Abschnitt nozzle_clean()
    # mit Kreisen -> Messrad-Prüfung (key845) -> remain_material.save.
    def _flush_target(self, gcmd):
        """Ziel-Slot des Spülens: TNN= oder zuletzt geladener Slot."""
        raw = gcmd.get("TNN", None)
        if raw:
            _b, _idx, letter, tnn = self._check_tnn(raw)
            return letter, tnn
        ctl = self.controller
        letter = ctl.loaded_slot if ctl is not None else None
        if letter:
            return letter, "T%d%s" % (int(self.cfg.get("box_addr", 1)), letter)
        return None, ""

    def _flush_plan(self, gcmd, letter, length, velocity):
        """Spül-Plan (rein rechnend + RFID lesen) -> dict."""
        c = self.cfg
        db = self.material_db
        notes = []
        new_tag = self._slot_tag(letter) if (
            letter and (c["flush_mode"] == "color" or db is not None)) else None
        new_key = new_tag[0] if new_tag else None
        new_col = new_tag[1] if new_tag else None
        old_key = self.remain_material.get("type")
        old_col = self.remain_material.get("color")
        new_p, old_p = self._mat(new_key), self._mat(old_key)
        different = False
        if db is not None and old_key and new_key:
            try:
                different = bool(db.is_different_type(old_key, new_key))
            except Exception:
                different = False
        segs, why = plan_flush_lengths(c, length, old_col, new_col, different)
        # Geschwindigkeit (Stock get_flush_velocity, Material = neu)
        if velocity is not None:
            v = float(velocity)
            vwhy = "VELOCITY="
        else:
            vol = new_p.get("vol") if db is not None else None
            v = flush_velocity_base(c, vol, self._nozzle_diameter())
            vwhy = ("min(360, %s/2,4·60)" % _n(vol)) if vol else (
                "Tn_extrude_velocity, max 360")
            if db is not None and not vol:
                notes.append("Spülen: Geschwindigkeit des Materials %s "
                             "unbekannt – %s mm/min (Stock-Fallback)"
                             % (new_key or "(ohne Etikett)", _n(v)))
        # Temperaturen (Stock get_flush_temp/get_flush_max_temp, Z.4507)
        t_param = gcmd.get_float("TEMP", None)
        if t_param is not None:
            temp = max_temp = float(t_param)
            twhy = "TEMP= (fest, ohne maxTemp-Stufe)"
        else:
            base = self._base_temp(gcmd)
            t_old = self._db_temp(old_p, "Spülen alt", notes)
            t_new = self._db_temp(new_p, "Spülen neu", notes)
            temp = max(base, t_old, t_new)
            max_temp = temp
            if db is not None and t_old >= t_new:
                mx_old = self._db_temp(old_p, "Spülen alt", [], "max_temp")
                mx_new = self._db_temp(new_p, "Spülen neu", [], "max_temp")
                max_temp = max(temp, mx_old, mx_new)
            twhy = ("max(Ziel %s, alt %s, neu %s)%s" % (
                _n(base), _n(t_old), _n(t_new),
                (", Spitze maxTemp %s (alt >= neu)" % _n(max_temp))
                if max_temp > temp + 0.01 else ""))
        return {"segs": segs, "why": why, "v": v, "vwhy": vwhy,
                "temp": temp, "max_temp": max_temp, "twhy": twhy,
                "new_key": new_key, "new_col": new_col, "different": different,
                "notes": notes}

    def cmd_flush(self, gcmd):
        def body(gcmd):
            c = self.cfg
            length = gcmd.get_float("LENGTH", None, minval=0.)
            vel = gcmd.get_float("VELOCITY", None, above=0.)
            letter, tnn = self._flush_target(gcmd)
            pl = self._flush_plan(gcmd, letter, length, vel)
            segs, v, temp, max_temp = (pl["segs"], pl["v"], pl["temp"],
                                       pl["max_temp"])
            n = len(segs)
            factor = float(c["flush_velocity_factor"])
            legacy = c["flush_clean"] == "legacy"
            self._respond_notes(gcmd, pl["notes"])
            gcmd.respond_info(
                "[k2_box] FLUSH-Plan %s: %s mm (%s; %s), F%s (%s, 80 %%-Teil "
                "×%s außer dem letzten), %s °C (%s)" % (
                    tnn or "(Ziel unbekannt)", _n(sum(segs)),
                    " + ".join(_n(x) for x in segs) or "-", pl["why"], _n(v),
                    pl["vwhy"], _n(factor), _n(temp), pl["twhy"]))
            if not segs:
                return
            self._go_extrude_pos(gcmd)
            blow_fan = c.get("flush_blow_fan") if c.get("flush_blow") else ""
            fan = self._pin(blow_fan) if blow_fan else None
            if blow_fan and fan is None and c.get("allow_move"):
                gcmd.respond_info("[k2_box] FLUSH: Blasen übersprungen – "
                                  "[output_pin %s] fehlt" % (blow_fan,))
            fan_en = self._pin(blow_fan + "_en") if fan is not None else None
            if fan is not None and c["flush_fan_start"] > 0:
                self._move(gcmd, "Bauteillüfter (Stock M106 P0 S50)",
                           ["M106 P0 S%s" % _n(c["flush_fan_start"]), "M400"],
                           wrap=False)
            self._heat(gcmd, temp)
            old_accel = self._max_accel()
            old_mcr = self._min_cruise_ratio()
            arcs = self._lookup("gcode_arcs") is not None
            clog = (c.get("flush_clog_check") and c.get("allow_move")
                    and c.get("allow_write"))
            wheel_sum, wheel_last, ran = 0.0, None, False
            if clog:
                try:
                    wheel_last = float(self._ensure_controller().get_wheel())
                except Exception as exc:
                    clog = False
                    gcmd.respond_info("[k2_box] FLUSH: Messrad nicht lesbar "
                                      "(%s) – keine Verstopfungsprüfung"
                                      % (self._exc_text(exc),))
            try:
                for i, seg in enumerate(segs):
                    last = i == n - 1
                    ran |= bool(self._move(
                        gcmd, "Spülen %d/%d (%s mm)" % (i + 1, n, _n(seg)),
                        flush_segment_lines(c, seg, i, n, v, temp, max_temp)
                        + ["M400"]))
                    if clog:
                        cur = float(self._ensure_controller().get_wheel())
                        wheel_sum += cur - wheel_last
                        wheel_last = cur
                    if fan is not None:
                        # Stock: alter Wert = fan0.last_value · scale
                        # (nach M106 P0 S50), voll = scale (Pi 255)
                        cur = self._pin(blow_fan) or fan
                        sc = fan[1] or 255.0
                        self._move(gcmd, "Blasen %d/%d (Stock blow)" % (i + 1, n),
                                   gcode_blow(blow_fan, c["flush_blow_time"],
                                              cur[0] * sc, sc), wrap=False)
                    if legacy:
                        self._move(gcmd, "Abstreifen %d/%d" % (i + 1, n),
                                   gcode_wipe(c, old_accel))
                        continue
                    off = 0.0
                    if last and c["flush_wipe_offset"] > 0:
                        # Stock clean_offset_pos = reactor.monotonic() % 3
                        off = self._now() % float(c["flush_wipe_offset"])
                    self._move(gcmd, "Reinigung %d/%d (%s)" % (
                        i + 1, n, "Blech + Silikon-Kreise, Versatz %s mm"
                        % _n(off) if last else "Blech, bleibt im Schacht"),
                        gcode_nozzle_clean(c, last, off, old_accel, old_mcr,
                                           arcs))
                if max_temp > temp + 0.01 and n == 1:
                    # nur ein Abschnitt: Stock stellt nie zurück -> hier wie
                    # bei mehreren Abschnitten auf die Spültemperatur
                    self._move(gcmd, "Spültemperatur zurück",
                               ["M104 S%s" % _n(temp)], wrap=False)
            except Exception:
                if c.get("allow_move") and old_accel is not None:
                    try:
                        self._script([_velocity_limit(old_accel, old_mcr)])
                    except Exception:
                        pass
                raise
            finally:
                if fan is not None and c.get("allow_move"):
                    # Stock material_flush -> box_save.restore_fan
                    lines = []
                    if fan_en is not None:
                        lines.append("SET_PIN PIN=%s VALUE=%s" % (
                            blow_fan + "_en", _n(fan_en[0] * (fan_en[1] or 1.0))))
                    lines.append("SET_PIN PIN=%s VALUE=%s" % (
                        blow_fan, _n(fan[0] * (fan[1] or 255.0))))
                    try:
                        self._move(gcmd, "Bauteillüfter zurück", lines,
                                   wrap=False)
                    except Exception:
                        pass
            if clog and ran and abs(wheel_sum) < abs(c["flush_clog_min_mm"]):
                gcmd.respond_info(
                    "[k2_box] WARNUNG FLUSH: Messrad bewegte sich nur %s mm "
                    "(< %s mm) – Düse verstopft? (Stock key845 „the nozzle is "
                    "blocked“; hier nur Warnung, flush_clog_check)" % (
                        _n(wheel_sum), _n(c["flush_clog_min_mm"])))
            if ran:
                # Stock remain_material.save(color, type) nach Erfolg
                self.remain_material = {"type": pl["new_key"],
                                        "color": pl["new_col"], "tnn": tnn}
            self.flush_last = ("%s: %s mm (%s), F%s, %s °C" % (
                tnn or "?", _n(sum(segs)), " + ".join(_n(x) for x in segs),
                _n(v), _n(temp)))
            gcmd.respond_info("[k2_box] FLUSH %s mm (%s) mit F%s ok%s" % (
                _n(sum(segs)), " + ".join(_n(x) for x in segs), _n(v),
                self._plan_note()))
        return self._run_step(gcmd, "FLUSH", body)

    def cmd_wipe(self, gcmd):
        def body(gcmd):
            self._go_extrude_pos(gcmd)
            self._move(gcmd, "Abstreifen", gcode_wipe(self.cfg, self._max_accel()))
        return self._run_step(gcmd, "WIPE", body)

    def cmd_waste(self, gcmd):
        def body(gcmd):
            gcmd.respond_info("[k2_box] WASTE: Abfallschacht-Prüfung (Stock "
                              "CR_BOX_WASTE, Kamera/[load_ai]) gibt es auf dem "
                              "Pi nicht – übersprungen")
        return self._run_step(gcmd, "WASTE", body, need_release=False)

    def cmd_end_opt(self, gcmd):
        ch = self.chain
        if not ch.get("active"):
            gcmd.respond_info("[k2_box] END_OPT: kein Wechsel aktiv")
            return
        ret = gcmd.get_int("RETURN", 1)
        if ch.get("skip"):
            gcmd.respond_info("[k2_box] END_OPT: %s – nichts zu tun"
                              % (ch.get("skip_reason"),))
        elif self.chain_error:
            gcmd.respond_info("[k2_box] END_OPT: Wechsel nach Fehler beendet "
                              "(keine Bewegung) – %s" % (self.chain_error,))
        else:
            try:
                c = self.cfg
                pos = self._gcode_pos()
                if pos is not None and pos[1] > c["safe_pos_y"] + 0.5:
                    self._move(gcmd, "Schacht verlassen", gcode_safe_pos(c))
                sp = ch.get("start_pos")
                if ret and sp is not None:
                    lines = ["G90", "G0 X%s Y%s F%s" % (
                        _n(sp[0]), _n(sp[1]), _n(c["travel_velocity"]))]
                    cur = self._gcode_pos()
                    if self._is_paused() and cur is not None \
                            and float(sp[2]) < cur[2] + 0.001:
                        # In der Pause senkt nur RESUME (X/Y in Pausenhöhe,
                        # dann Z senkrecht) – sonst stünde die Düse auf
                        # Druckhöhe über dem Objekt und RESUME führe von dort
                        # quer zum Schacht.
                        gcmd.respond_info(
                            "[k2_box] END_OPT: Druck pausiert – Z bleibt auf "
                            "%s (Start-Z %s senkt RESUME)" % (_n(cur[2]),
                                                             _n(sp[2])))
                    else:
                        lines.append("G0 Z%s F%s" % (_n(sp[2]),
                                                     _n(c["z_velocity"])))
                    self._move(gcmd, "Rückfahrt zur Startposition", lines)
                if ch.get("temp_changed") and ch.get("orig_target") is not None:
                    self._move(gcmd, "Zieltemperatur zurück",
                               ["M104 S%s" % _n(ch["orig_target"])], wrap=False)
                self._keep_start_pos = None
                gcmd.respond_info("[k2_box] Wechsel %s abgeschlossen%s"
                                  % (ch.get("tnn") or "", self._plan_note()))
            except Exception as exc:
                self._fail(gcmd, "END_OPT", self._exc_text(exc))
        if self.chain.get("active"):
            self._chain_cleanup(gcmd)

    def cmd_wait(self, gcmd):
        def body(gcmd):
            c = self.cfg
            timeout = gcmd.get_float("TIMEOUT", c["box_wait_timeout"], above=0.)
            ctl = self._ensure_controller()
            st = ctl.wait_box_ready(timeout, c["box_wait_interval"])
            gcmd.respond_info("[k2_box] Box bereit: %s" % (st.get("state_name"),))
        return self._run_step(gcmd, "WAIT", body, need_release=False)

    def cmd_error_clear(self, gcmd):
        old = self.chain_error
        self.chain_error = ""
        msg = "[k2_box] Wechsel-Fehler quittiert" + (" (%s)" % old if old else "")
        if self.cfg.get("allow_write"):
            try:
                ctl = self._ensure_controller()
                ctl.action.communication_set_box_mode_feed()  # 0x04 [00 01]
                msg += "; Box 0x04 [00 01] IDLE (löscht Box-ERROR, live belegt)"
            except Exception as exc:
                msg += "; Box nicht erreichbar: %s" % (exc,)
        # HEAD_LOADED=1: Bestätigung von Hand, dass der Kopf geladen ist –
        # NUR wirksam, wenn der Kopfsensor keinen Zustand liefert (nicht
        # konfiguriert/fehlt). Meldet er "leer", hat der Sensor Vorrang.
        if gcmd.get_int("HEAD_LOADED", 0) and self.aborted:
            head = self._head_filament()
            if head is False:
                msg += ("; HEAD_LOADED abgelehnt: Kopfsensor %s meldet KEIN "
                        "Filament (Sensor hat Vorrang) – erst laden (T<n>)"
                        % (self.cfg.get("filament_sensor"),))
            else:
                self.aborted = None
                msg += ("; Kopf als geladen bestätigt (HEAD_LOADED=1, "
                        "Kopfsensor %s) – RESUME prüft den Kopf nach diesem "
                        "Abbruch nicht mehr" % (
                            "unbekannt" if head is None else "meldet Filament",))
        elif self.aborted:
            msg += ("; RESUME prüft den Kopf-Filamentsensor (abgebrochener "
                    "Wechsel %s): leer = gesperrt" % (
                        self.aborted.get("tnn") or "ohne Ziel-TNN",))
        gcmd.respond_info(msg)

    def cmd_resume_fix(self, gcmd):
        # CLEAR=1 (aus dem PAUSE-Makro bei JEDER neuen Pause): ein Rückfahrziel
        # mit Z (PAUSE CFS_RETURN_Z) ersetzt die Z-Korrektur; zugleich keine
        # Altlast aus einer per CANCEL beendeten Pause (sonst führe ein
        # späteres RESUME auf ein fremdes Z).
        if gcmd.get_int("CLEAR", 0):
            if self.pending_resume_z is not None:
                gcmd.respond_info("[k2_box] RESUME_FIX: Z-Korrektur auf %s "
                                  "verworfen (Rückfahrziel der Pause enthält "
                                  "Z bzw. neue Pause)"
                                  % (_n(self.pending_resume_z),))
            self.pending_resume_z = None
            # Neue Pause, die NICHT aus _fail stammt (Hand-PAUSE, Runout,
            # neuer Druck nach CANCEL): ein alter Abbruch-Merker ist veraltet
            if self.aborted and not self._pause_from_fail:
                self.aborted = None
            return
        # Ohne CLEAR = Ende eines RESUME (nach RESUME_BASE): der Abbruch ist
        # erledigt (RESUME hatte den Kopf geprüft bzw. nachgeholt)
        self.aborted = None
        z = self.pending_resume_z
        if z is None:
            return
        self.pending_resume_z = None
        try:
            self._move(gcmd, "Z nach abgebrochenem Wechsel zurück",
                       ["G90", "G1 Z%s F%s" % (_n(z), _n(self.cfg["z_velocity"]))])
        except Exception as exc:
            raise gcmd.error("[k2_box] RESUME_FIX: %s" % (exc,))

    # =================================================================
    # RESUME nach abgebrochenem Wechsel: Kopfsensor prüfen / nachholen
    # (17.10.2026 spät, BOOTLOADER-HOST-BEFUND §5, NOTIZ-reinigung-resume
    # §6a.7, README-k2_box.md §11)
    # =================================================================
    # Stock (box_wrapper.so error_resume_process @0x7cd28): CFS an,
    # pausiert, resume_tnn gesetzt, Kopfsensor LEER -> go_to_extrude_pos,
    # box_retrude_material, box_extrude_material, extruder_extrude,
    # material_change_flush; Fehler -> resume_err. Kopfsensor belegt ->
    # nichts. Pi: Kopf leer -> RESUME GESPERRT (Default); mit
    # resume_redo_change + allow_write + allow_move + MOVE=1 holt die
    # vorhandene Kette den Wechsel nach (PRE_OPT Z_HOP=0 -> Schacht/Heizen
    # -> RETRUDE (nur wenn der Hub belegt ist) -> EXTRUDE -> WASTE -> FLUSH
    # -> END_OPT RETURN=0); jeder Fehler -> chain_error, RESUME gesperrt,
    # Druck bleibt pausiert, keine Rückfahrt.
    def _abort_text(self, ab):
        tnn = ab.get("tnn") or ""
        tool = self._tool_of(tnn) if tnn else ""
        return "%s fehlgeschlagen%s" % (
            ab.get("step") or "?",
            ("; Ziel %s = %s" % (tnn, tool)) if tnn else "; Ziel-TNN unbekannt")

    def _resume_block(self, gcmd, ab, head_txt, todo, why=""):
        msg = ("RESUME gesperrt nach abgebrochenem CFS-Wechsel (%s): %s. "
               "Druck bleibt PAUSIERT. %s%s" % (
                   self._abort_text(ab), head_txt, todo,
                   (" Kein automatisches Nachholen: %s." % (why,))
                   if why else ""))
        raise gcmd.error(msg)

    def _redo_block_reason(self, gcmd, ab):
        """None = Nachholen erlaubt, sonst Klartext, warum nicht."""
        c = self.cfg
        if not c.get("resume_redo_change"):
            return "aus ([k2_box] resume_redo_change: False)"
        if not self._released():
            return ("CFS-Wechsel nicht freigegeben ([k2_box] allow_write=%s, "
                    "allow_move=%s)" % (c.get("allow_write"),
                                        c.get("allow_move")))
        if not gcmd.get_int("MOVE", 0):
            return ("RESUME darf nicht fahren (_PI_FREIGABE allow_move, "
                    "Referenz X/Y/Z oder Pausenposition fehlt)")
        if ab.get("source") != "T":
            return ("Abbruch stammt nicht aus einem T<n>-Wechsel (M8200-Kette, "
                    "Runout oder Einzelschritt) – Stock holt nur T<n>-Wechsel "
                    "nach")
        if not ab.get("tnn"):
            return "Ziel-TNN des abgebrochenen Wechsels unbekannt"
        if self.chain.get("active"):
            return "Wechselkette noch offen (%s)" % (
                self.chain.get("tnn") or "ohne TNN",)
        if not self._homed_ok():
            return "X/Y/Z nicht referenziert"
        return None

    def cmd_resume_check(self, gcmd):
        """Aus dem RESUME-Makro (nur pausiert, nur bei change_aborted), VOR
        jeder Bewegung: Kopfsensor meldet Filament -> frei; leer -> gesperrt
        bzw. Nachholen (resume_redo_change); unbekannt -> gesperrt, bis
        KTWO_BOX_ERROR_CLEAR HEAD_LOADED=1. MOVE=1: RESUME darf danach
        fahren (macros.cfg). Fehler = gcmd.error -> RESUME bricht ab."""
        ab = self.aborted
        if not ab:
            gcmd.respond_info("[k2_box] RESUME-Prüfung: kein abgebrochener "
                              "Wechsel offen")
            return
        if not self._is_paused():
            gcmd.respond_info("[k2_box] RESUME-Prüfung: nicht pausiert – "
                              "nichts zu prüfen")
            return
        if self.chain_error:
            raise gcmd.error(
                "RESUME gesperrt – CFS-Wechsel fehlgeschlagen: %s. Ursache "
                "beheben, KTWO_BOX_ERROR_CLEAR, Wechsel (T<n>) bzw. Schritt "
                "von Hand wiederholen, dann RESUME." % (self.chain_error,))
        sens = self.cfg.get("filament_sensor") or "(nicht konfiguriert)"
        tnn = ab.get("tnn") or ""
        tool = self._tool_of(tnn) if tnn else ""
        load = ("%s (lädt %s)" % (tool, tnn) if tool
                else "T<n> des gewünschten Werkzeugs")
        head = self._head_filament()
        if head is True:
            gcmd.respond_info(
                "[k2_box] RESUME-Prüfung: Kopfsensor %s meldet Filament nach "
                "dem abgebrochenen Wechsel (%s) – RESUME frei (ob es das "
                "Ziel-Filament ist, prüft der Pi nicht; Stock ebenso)."
                % (sens, self._abort_text(ab)))
            return
        if head is None:
            name = self.cfg.get("filament_sensor")
            what = ("Kopf-Filamentsensor (nicht konfiguriert, [k2_box] "
                    "filament_sensor leer) liefert keinen Zustand" if not name
                    else "Kopf-Filamentsensor %s liefert keinen Zustand "
                    "([filament_switch_sensor %s] fehlt)" % (name, name))
            return self._resume_block(
                gcmd, ab, "%s – ob der Kopf leer ist, ist UNBEKANNT (RESUME "
                "könnte ohne Filament weiterdrucken)" % (what,),
                "Kopf von Hand prüfen bzw. laden (%s), dann "
                "KTWO_BOX_ERROR_CLEAR HEAD_LOADED=1 und RESUME." % (load,),
                "ohne Kopfsensor nie (Stock prüft ihn ebenso)")
        why = self._redo_block_reason(gcmd, ab)
        if why:
            hint = ""
            if self.chain.get("active"):
                hint = (" Offene Kette (M8200): alternativ KTWO_BOX_EXTRUDE "
                        "TNN=%s als Kettenschritt." % (tnn or "T1A..T1D",))
            return self._resume_block(
                gcmd, ab, "Kopf-Filamentsensor %s meldet KEIN Filament – "
                "RESUME würde ohne Filament weiterdrucken" % (sens,),
                "In der Pause nachholen: %s, dann RESUME.%s" % (load, hint),
                why)
        return self._redo_change(gcmd, ab)

    def _redo_change(self, gcmd, ab):
        c = self.cfg
        tnn = ab["tnn"]
        temp, tparam = self._runout_temp(gcmd, tnn)
        gcmd.respond_info(
            "[k2_box] RESUME: Kopf leer nach abgebrochenem Wechsel (%s) – "
            "Wechsel wird NACHGEHOLT (resume_redo_change, Stock "
            "error_resume_process): Schacht, %s °C, Rückzug nur bei belegtem "
            "Hub, Einzug %s, Spülen; danach RESUME wie üblich"
            % (self._abort_text(ab), _n(temp), tnn))
        self._script(["KTWO_BOX_PRE_OPT TNN=%s Z_HOP=0 SOURCE=T" % (tnn,)])
        ch = self.chain
        if ch.get("active") and not ch.get("skip") and not self.chain_error:
            def heat(gcmd):
                # Schacht zuerst und ankommen (M400: Tropfen beim Aufheizen
                # fallen in den Schacht), dann heizen: auch ein Box-Rückzug
                # mit Extruder-E-10 braucht die heiße Düse (RETRUDE heizt
                # nur, wenn der Kopfsensor Filament meldet).
                if self._go_extrude_pos(gcmd):
                    self._move(gcmd, "Schacht erreicht", ["M400"], wrap=False)
                self._heat(gcmd, temp)
            self._run_step(gcmd, "REDO_HEAT", heat)
        self._script([
            "KTWO_BOX_RETRUDE",
            "KTWO_BOX_EXTRUDE TNN=%s %s" % (tnn, tparam),
            "KTWO_BOX_WASTE",
            "KTWO_BOX_FLUSH TNN=%s %s" % (tnn, tparam),
            "KTWO_BOX_END_OPT RETURN=0"])
        if self.chain_error:
            raise gcmd.error(
                "RESUME gesperrt: Nachholen des Wechsels %s fehlgeschlagen "
                "(%s) – Druck bleibt PAUSIERT, keine Rückfahrt (Stock "
                "resume_err). Ursache beheben, KTWO_BOX_ERROR_CLEAR, dann "
                "RESUME (holt erneut nach) oder %s von Hand."
                % (tnn, self.chain_error, self._tool_of(tnn)))
        if self._head_filament() is not True:
            raise gcmd.error(
                "RESUME gesperrt: nach dem Nachholen von %s meldet der "
                "Kopfsensor %s kein Filament – Druck bleibt PAUSIERT. Kopf "
                "prüfen, %s von Hand, dann RESUME." % (
                    tnn, c.get("filament_sensor"), self._tool_of(tnn)))
        gcmd.respond_info("[k2_box] RESUME: Wechsel auf %s nachgeholt "
                          "(Kopfsensor meldet Filament) – weiter mit Schacht/"
                          "Vorextrusion/Rückfahrt" % (tnn,))
        return None

    # =================================================================
    # Auto-Nachladen bei Runout (A7) – Ersatz für Stock
    # BOX_CHECK_MATERIAL_REFILL (printer.cfg runout_gcode)
    # =================================================================
    # Ablauf (Belege + ANNAHMEN: config-pi/NOTIZ-runout.md):
    #   Upstream pausiert VOR runout_gcode (pause_on_runout: True,
    #   filament_switch_sensor._runout_event_handler: "PAUSE\n" + Vorlage).
    #   KTWO_BOX_RUNOUT prüft lesend (Freigaben, Kopfsensor, Box-Zustand
    #   PRINT/0x50, aktueller Slot, Hub + Einlauf leer, RFID/Gruppe), wählt
    #   den Ersatz-Slot (select_runout_slot) und fährt die vorhandene Kette:
    #   PRE_OPT (ohne Z-Hop, PAUSE hat schon gehoben) -> Schacht + Heizen +
    #   Restfilament E30 F600 (Stock) -> EXTRUDE -> WASTE -> FLUSH ->
    #   END_OPT RETURN=1 -> RESUME. Kein Rückzug/Schnitt: Hub und Kopf sind
    #   leer (der Spulenrest ist schon durch). Jeder Abbruch: Druck bleibt
    #   pausiert + Meldung (nie Shutdown).
    def _runout_stop(self, gcmd, text, dry=False):
        msg = "[k2_box] RUNOUT: kein automatisches Nachladen – %s" % (text,)
        self.runout_last = msg
        if dry:
            gcmd.respond_info(msg + " (DRY_RUN)")
            return None
        if self._print_active():
            self._respond_error(
                "%s – Druck bleibt PAUSIERT. Filament von Hand nachlegen bzw. "
                "wechseln (z. B. T<n>), dann RESUME." % (msg,))
            if not self._is_paused():
                try:
                    self._script(["PAUSE"])
                except Exception as exc:
                    self._respond_error("[k2_box] PAUSE fehlgeschlagen: %s"
                                        % (exc,))
            return None
        if hasattr(gcmd, "error"):
            raise gcmd.error(msg)
        raise RuntimeError(msg)

    def _runout_base(self, gcmd):
        """Drucktemperatur vor der Pause (_PI_PARAM.hotend_temp, macros.cfg
        PAUSE), sonst aktuelles Ziel – Basis für Nachholen/Runout."""
        base = 0.0
        try:
            base = float(self._obj_status("gcode_macro _PI_PARAM")
                         .get("hotend_temp") or 0.0)
        except Exception:
            base = 0.0
        if base <= 0.0:
            try:
                base = float(self._obj_status("extruder").get("target") or 0.0)
            except Exception:
                base = 0.0
        return base

    def _runout_temp(self, gcmd, tnn=None):
        """Lade-/Heiztemperatur (Nachholen/Runout) -> (temp, Kettenparameter).

        TEMP= -> fest (an EXTRUDE/FLUSH als TEMP= weitergereicht). Sonst
        max(_runout_base, Material-Temperatur des Ziels) wie _change_temp
        (Material-DB, sonst Tn_extrude_temp 220); EXTRUDE/FLUSH bekommen
        dann BASE_TEMP=<Basis>, damit sie selbst wie Stock rechnen (FLUSH:
        max(alt, neu) + maxTemp-Stufe). Ohne DB ergibt das wie bisher
        max(Drucktemperatur, 220)."""
        t = gcmd.get_float("TEMP", None, above=0.)
        if t is not None:
            return float(t), "TEMP=%s" % _n(t)
        base = self._runout_base(gcmd)
        key = None
        if self.material_db is not None and tnn:
            try:
                tag = self._slot_tag(parse_tnn(tnn)[2])
            except ValueError:
                tag = None
            key = tag[0] if tag else None
        notes = []
        mt = self._db_temp(self._mat(key), "Nachladen %s" % (tnn or "?"),
                           notes)
        self._respond_notes(gcmd, notes)
        return max(base, mt), "BASE_TEMP=%s" % _n(base)

    def _runout_analyse(self, gcmd):
        """Nur lesend: darf/kann nachgeladen werden? -> Plan-Dict, sonst
        BoxChangeError mit Klartext (Bus-Fehler als RuntimeError)."""
        c = self.cfg
        if not self.auto_refill:
            raise BoxChangeError("Auto-Nachladen aus ([k2_box] runout_refill: "
                                 "False bzw. KTWO_BOX_AUTO_REFILL ENABLE=0)")
        if not self._released():
            raise BoxChangeError(
                "CFS-Wechsel nicht freigegeben ([k2_box] allow_write=%s, "
                "allow_move=%s)" % (c.get("allow_write"), c.get("allow_move")))
        if self.chain.get("active") or self.chain_error:
            raise BoxChangeError("Wechsel aktiv oder Wechsel-Fehler offen (%s)"
                                 % (self.chain_error or self.chain.get("tnn"),))
        if self._head_filament() is True:
            raise BoxChangeError("Kopfsensor meldet Filament – kein Runout "
                                 "(Fehlauslösung?)")
        ctl = self._ensure_controller()
        st = ctl.poll_status()
        if st.get("kind") not in ("state", "state_v113"):
            st = ctl.poll_status()  # Ereignis (0x30) -> einmal nachfragen
        state = st.get("state")
        code = int(st.get("error_code") or st.get("frame_status") or 0)
        clear = False
        if state == _BOX.BOX_STATE_ERROR:
            if code not in RUNOUT_CLEARABLE_ERRORS:
                raise BoxChangeError("Box im Zustand ERROR (0x%02x %s) – "
                                     "KTWO_BOX_ERROR_CLEAR" % (
                                         code, status_name(code)))
            clear = True
        elif state != _BOX.BOX_STATE_PRINT:
            raise BoxChangeError(
                "CFS druckt nicht (Box-Zustand %s) – Runout einer externen "
                "Spule? (ANNAHME: CFS-Druck = Box im PRINT)" % (
                    st.get("state_name", "?"),))
        slot = gcmd.get("SLOT", None) or ctl.loaded_slot or ctl.print_slot
        if not slot:
            raise BoxChangeError("aktiver CFS-Slot unbekannt (kein Wechsel "
                                 "seit dem Start; SLOT=A..D angeben)")
        idx, letter, mask = parse_slot(slot)
        hub = ctl.hub_mask()
        if hub & mask:
            raise BoxChangeError(
                "Hub meldet noch Filament von Slot %s – Spule nicht leer "
                "(Bruch/Verhakung zwischen Hub und Kopf?)" % (letter,))
        if hub:
            raise BoxChangeError("Hub durch anderen Slot belegt (0x%02x)" % hub)
        inlet = ctl.inlet_mask()
        if inlet & mask:
            raise BoxChangeError(
                "Einlauf von Slot %s meldet noch Filament – keine leere Spule "
                "(Bruch zwischen Box und Kopf?)" % (letter,))
        rfid = ctl.get_rfid_all()
        if rfid_material(rfid.get(letter)) is None \
                and letter in ctl.tag_cache:
            rfid[letter] = ctl.tag_cache[letter]  # Etikett vom Laden
        try:
            remain = ctl.get_remain(MASK_ALL)
        except Exception:
            remain = {}
        target, why = select_runout_slot(letter, inlet, rfid,
                                         self.same_material,
                                         c["runout_require_color"])
        if target is None:
            raise BoxChangeError(why)
        return {"slot": letter, "target": target, "why": why,
                "clear_error": clear, "code": code, "inlet": inlet,
                "remain": remain}

    def cmd_runout(self, gcmd):
        dry = bool(gcmd.get_int("DRY_RUN", 0))
        try:
            plan = self._runout_analyse(gcmd)
        except Exception as exc:
            return self._runout_stop(gcmd, self._exc_text(exc), dry)
        c = self.cfg
        box = int(c.get("box_addr", BOX_ADDR_DEFAULT))
        old_tnn = "T%d%s" % (box, plan["slot"])
        tnn = "T%d%s" % (box, plan["target"])
        head = ("[k2_box] RUNOUT: %s leer -> Ersatz %s (%s; Einlauf 0x%02x, "
                "Rest %s)" % (old_tnn, tnn, plan["why"], plan["inlet"],
                              plan["remain"]))
        if dry:
            self.runout_last = head + " (DRY_RUN)"
            gcmd.respond_info(head + " – DRY_RUN: nichts bewegt/gesendet")
            return None
        gcmd.respond_info(head)
        temp, tparam = self._runout_temp(gcmd, tnn)
        paused = self._is_paused()
        if plan["clear_error"]:
            try:
                self._ensure_controller().action.communication_set_box_mode_feed()
                gcmd.respond_info("[k2_box] RUNOUT: Box-Fehler 0x%02x per 0x04 "
                                  "[00 01] quittiert" % (plan["code"],))
            except Exception as exc:
                return self._runout_stop(gcmd, "Box-Fehler 0x%02x nicht "
                                         "quittierbar: %s" % (
                                             plan["code"], self._exc_text(exc)))
        self._script(["KTWO_BOX_PRE_OPT TNN=%s Z_HOP=%s" % (
            tnn, "0" if paused else _n(c["change_z_hop"]))])
        if self.chain.get("active") and not self.chain.get("skip"):
            def tail(gcmd):
                # Stock runout_gcode "G0 E30 F600" (wenn heiß): Rest durch die
                # Zahnräder – hier im Schacht, nach dem Heizen.
                self._go_extrude_pos(gcmd)
                self._heat(gcmd, temp)
                self._move(gcmd, "Runout: Restfilament durch den Extruder "
                           "(Stock E30 F600)", gcode_extruder_move(
                               c["runout_tail_length"],
                               c["runout_tail_velocity"]) + ["M400"])
            if c["runout_tail_length"] > 0:
                self._run_step(gcmd, "RUNOUT_TAIL", tail)
        self._script([
            "KTWO_BOX_EXTRUDE TNN=%s %s" % (tnn, tparam),
            "KTWO_BOX_WASTE",
            "KTWO_BOX_FLUSH TNN=%s %s" % (tnn, tparam),
            "KTWO_BOX_END_OPT RETURN=1"])
        if self.chain_error:
            self.runout_last = ("[k2_box] RUNOUT: Nachladen %s -> %s "
                                "fehlgeschlagen (%s) – Druck bleibt pausiert"
                                % (old_tnn, tnn, self.chain_error))
            self._respond_error(self.runout_last)
            return None
        self.runout_last = "[k2_box] RUNOUT: %s leer -> %s nachgeladen" % (
            old_tnn, tnn)
        gcmd.respond_info(self.runout_last)
        if paused and self._is_paused():
            if c["runout_resume"]:
                try:
                    self._script(["RESUME"])
                except Exception as exc:
                    self._respond_error(
                        "[k2_box] RUNOUT: RESUME fehlgeschlagen (%s) – Druck "
                        "bleibt pausiert, von Hand fortsetzen" % (exc,))
            else:
                gcmd.respond_info("[k2_box] RUNOUT: runout_resume: False – "
                                  "Druck bleibt pausiert, RESUME von Hand")
        return None

    def cmd_auto_refill(self, gcmd):
        en = gcmd.get_int("ENABLE", None)
        if en is not None:
            self.auto_refill = bool(en)
        gcmd.respond_info(
            "[k2_box] Auto-Nachladen: %s (wirkt nur mit allow_write=%s UND "
            "allow_move=%s; Regel: %s, Farbe %s)" % (
                "AN" if self.auto_refill else "AUS",
                self.cfg.get("allow_write"), self.cfg.get("allow_move"),
                ("Gruppen %s" % self.get_status()["same_material"])
                if self.same_material else "RFID gleiches Material",
                "muss passen" if self.cfg.get("runout_require_color")
                else "egal"))

    def cmd_same_material(self, gcmd):
        if gcmd.get_int("CLEAR", 0):
            self.same_material = []
        else:
            raw = gcmd.get("LIST", None)
            if raw is not None:
                try:
                    self.same_material = parse_same_material(
                        raw, self.cfg.get("box_addr", BOX_ADDR_DEFAULT))
                except ValueError as exc:
                    msg = "[k2_box] SAME_MATERIAL: %s" % (exc,)
                    if hasattr(gcmd, "error"):
                        raise gcmd.error(msg)
                    raise RuntimeError(msg)
        groups = self.get_status()["same_material"]
        gcmd.respond_info("[k2_box] Gleich-Material-Gruppen: %s" % (
            groups if groups else "keine (Ersatz per RFID-Vergleich)",))


def load_config(config):
    """Klipper-Einstieg [k2_box] (Upstream-Muster)."""
    return PrinterK2Box(config)
