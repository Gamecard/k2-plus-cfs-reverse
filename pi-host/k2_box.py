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

- ``KTWO_BOX_PRE_OPT [TNN=T1A] [Z_HOP=mm]`` (= M8200 P / CR_BOX_PRE_OPT):
  Kette beginnen, Kopfsensor sperren, Zustand sichern, Z-Hop.
- ``KTWO_BOX_CUT`` (= M8200 C / CR_BOX_CUT): Schneidfahrt
  pre_cut_pos → cut_pos_x (SAVE_CONFIG-Wert) → zurück.
- ``KTWO_BOX_GO_EXTRUDE_POS`` / ``KTWO_BOX_SAFE_POS``: in den / aus dem
  Abfallschacht (Stock BOX_GO_TO_EXTRUDE_POS / BOX_MOVE_TO_SAFE_POS).
- ``KTWO_BOX_RETRUDE [SLOT=] [LENGTH=]`` (= M8200 R): Box 0x11 p0×4 + p1,
  dazwischen Extruder-Rückzug Tn_retrude (live 3× −10 mm).
- ``KTWO_BOX_EXTRUDE TNN=T1A|SLOT=A`` (= M8200 L I0): Box-Einzug bis zum
  Kopfsensor, Stage 7 (Extruder greift), Box PRINT.
- ``KTWO_BOX_FLUSH [TNN=] [LENGTH=] [VELOCITY=] [TEMP=]`` (= M8200 F):
  Spülen in Abschnitten (live 90 + 70 mm) mit Abstreifen.
- ``KTWO_BOX_WIPE`` (Abstreifen, Stock-T0-Beispiel 10.1–10.3),
  ``KTWO_BOX_WASTE`` (= M8200 W, hier nur Hinweis),
  ``KTWO_BOX_END_OPT [RETURN=0|1]`` (= M8200 O), ``KTWO_BOX_WAIT``
  (Box-Zustand abwarten, Stock BOX_MODE_WAIT), ``KTWO_BOX_ERROR_CLEAR``,
  ``KTWO_BOX_RESUME_FIX [CLEAR=1]`` (aus RESUME, Z nach Abbruch im
  Wechsel; CLEAR=1 aus PAUSE = Z steckt im Rückfahrziel, s. unten).

Abbruch im laufenden Druck (17.10.2026, config-pi/NOTIZ-reinigung-resume.md
§6a): ``PAUSE CFS_RETURN_X/Y/Z=<Druckposition vor dem Wechsel>`` statt der
Fehlerposition (z. B. am Schneidhebel X -7,8 / Y200 oder im Schacht) – das
PAUSE-Makro merkt sie als Rückfahrziel, RESUME fährt dorthin und setzt die
Upstream-PAUSE_STATE darauf (RESUME_BASE = Nullfahrt). Fahrten in den Schacht
verlassen den Schneidbereich (X < pre_cut_pos_x) zuerst nur in X.

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
    "tn_extrude_temp": 220.0,
    "tn_extrude": 140.0,         # Stock: Vorheiz-/Einzugswert, hier nur Doku
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
    "travel_velocity": 12000.0,  # T0-Beispiel F12000
    "z_velocity": 600.0,         # T0-Beispiel G0 Z.. F600
    "change_z_hop": 0.4,         # T0-Beispiel "G0 Z50.4" bei Z50
    # Wartezeiten (live belegt bzw. ANNAHME, s. NOTIZ-cfs.md)
    "extrude_sensor_timeout": 90.0,  # live "timeout = 90"
    "extrude_poll_interval": 2.0,    # live 0x10 mode 5 alle ~2 s
    "stage7_delay": 2.0,             # ANNAHME (live 3,7-3,8 s bis mode 7)
    "box_wait_timeout": 200.0,       # ANNAHME (Preload <= ~3 min/Slot)
    "box_wait_interval": 1.0,
    # Auto-Nachladen bei Runout (A7, config-pi/NOTIZ-runout.md): Stock-
    # printer.cfg runout_gcode "G91 / G0 E30 F600 / G90" (nur wenn heiß) –
    # Restfilament durch die Zahnräder (= buffer_empty_len 30), hier im
    # Abfallschacht statt am Parkpunkt.
    "runout_tail_length": 30.0,
    "runout_tail_velocity": 600.0,
}
CHANGE_INT_KEYS = ("wipe_shakes",)

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


def gcode_flush_segment(length, velocity):
    """Ein Spül-Abschnitt, live 20 %/80 % geteilt (18+72 bzw. 14+56 mm)."""
    first = round(float(length) * 0.2, 3)
    return ["G91",
            "G1 E%s F%s" % (_n(first), _n(velocity)),
            "G1 E%s F%s" % (_n(float(length) - first), _n(velocity)),
            "G90"]


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
               "runout_require_color", "runout_resume")
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
    def do_preload(self, slot, op, timeout=None, dry_run=False):
        """0x0d [Maske][op] (op 0-3, Beleg GHIDRA §3).

        op 2/3: Antwort erst nach dem Vorgang → langer Timeout
        (preload_force_timeout) + retries=0 (kein Burst, Beleg BEFUND2 §5).
        Schreibend → allow_write nötig (auch op 0/1 ändern PRE[1] global).
        """
        idx, letter, mask = parse_slot(slot)
        op = parse_preload_op(op)
        if dry_run or not self.cfg.get("allow_write"):
            if dry_run:
                return {"status": "dry_run", "slot": letter, "op": op,
                        "plan": "0x0d [%02x %02x] (%s)" % (
                            mask, op, PRELOAD_OP_NAMES[op])}
            self._require_write("Preload SLOT=%s OP=%d" % (letter, op))
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

    def wait_box_ready(self, timeout=200.0, interval=1.0):
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
                                    desc="K2-Box-Preload SLOT=A..D OP=0..3")
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
            # Auto-Nachladen (Stock-Statusnamen 'auto_refill'/'same_material')
            "auto_refill": 1 if self.auto_refill else 0,
            "same_material": [["T%d%s" % (self.cfg.get("box_addr", 1), x)
                               for x in g] for g in self.same_material],
            "runout_last": self.runout_last or "",
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
            res = ctl.do_preload(slot, op)
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
                "paused_at_start": False,
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

    def _change_temp(self, gcmd):
        """Wechsel-Temperatur: TEMP= oder max(Ziel, Tn_extrude_temp 220).
        ANNAHME: Stock nimmt Material-Temperaturen aus dem RFID-Tag
        (live "get next material temp: 220", max 240) – hier nicht verfügbar."""
        t = gcmd.get_float("TEMP", None)
        if t is None:
            try:
                target = float(self._obj_status("extruder").get("target") or 0.0)
            except Exception:
                target = 0.0
            t = max(target, float(self.cfg["tn_extrude_temp"]))
        return float(t)

    def _heat(self, gcmd, temp):
        ch = self.chain
        if ch.get("active") and ch.get("orig_target") is None:
            try:
                ch["orig_target"] = float(
                    self._obj_status("extruder").get("target") or 0.0)
            except Exception:
                ch["orig_target"] = 0.0
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

    def _fail(self, gcmd, name, text):
        """Fehler im Wechsel: im Druck PAUSE (kein Shutdown, kein Abbruch),
        Folgeschritte überspringen; sonst Aufräumen + G-Code-Fehler.

        Löst DIESER Fehler die Pause aus (Druck lief), bekommt PAUSE das
        Rückfahrziel ``CFS_RETURN_X/Y/Z`` (= _resume_target, Z ohne Hop):
        das PAUSE-Makro (macros.cfg) merkt es statt der Fehlerposition und
        quittiert mit ``KTWO_BOX_RESUME_FIX CLEAR=1`` (Z ist Teil des
        Ziels); ohne dieses Makro (Upstream-PAUSE ignoriert die Parameter)
        bleibt der alte Z-Fallback pending_resume_z. In einer schon
        bestehenden Pause (Wiederholung von Hand, Runout-Kette) weder Ziel
        noch Z-Fallback – PAUSE_STATE ist dort bereits die Druckposition."""
        msg = "[k2_box] %s fehlgeschlagen: %s" % (name, text)
        if self._print_active() and self.cfg.get("pause_on_failure"):
            if not self.chain_error:
                self.chain_error = msg
            ch = self.chain
            was_paused = self._is_paused()
            if ch.get("start_pos") is not None:
                self._keep_start_pos = list(ch["start_pos"])
            target = None if was_paused else self._resume_target()
            if not was_paused and ch.get("hop") and ch.get("active") \
                    and ch.get("start_pos") is not None:
                self.pending_resume_z = float(ch["start_pos"][2])
            hint = ""
            if target is not None:
                hint = (" Rückfahrziel für RESUME = Druckposition vor dem "
                        "Wechsel X%s Y%s Z%s (nicht die Fehlerposition)."
                        % tuple(_n(v) for v in target))
            self._respond_error(
                "%s – Druck wird PAUSIERT (kein Abbruch). Ursache beheben, "
                "Schritt bzw. Wechsel (T<n>) von Hand wiederholen, dann "
                "KTWO_BOX_ERROR_CLEAR und RESUME.%s" % (msg, hint))
            if not was_paused:
                line = "PAUSE"
                if target is not None:
                    line += (" CFS_RETURN_X=%s CFS_RETURN_Y=%s CFS_RETURN_Z=%s"
                             % tuple(_n(v) for v in target))
                try:
                    self._script([line])
                except Exception as exc:
                    self._respond_error("[k2_box] PAUSE fehlgeschlagen: %s"
                                        % (exc,))
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
        # (Rückfahrziel, falls der Schritt abbricht – s. _resume_target).
        self._step_start = (self._gcode_pos() if not ch.get("active")
                            and self._print_active()
                            and not self._is_paused() else None)
        try:
            return body(gcmd)
        except Exception as exc:
            return self._fail(gcmd, name, self._exc_text(exc))
        finally:
            self._step_start = None

    # -- G-Codes ---------------------------------------------------------
    def cmd_pre_opt(self, gcmd):
        if self.chain.get("active"):  # alte Kette ohne END_OPT
            self._chain_cleanup(gcmd)
        ch = self._new_chain()
        ch["active"] = True
        self.chain = ch
        if self.chain_error:
            return self._fail(gcmd, "PRE_OPT", (
                "Wechsel-Fehler offen (%s) – erst beheben und "
                "KTWO_BOX_ERROR_CLEAR" % (self.chain_error,)))

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
                self._heat(gcmd, self._change_temp(gcmd))

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
            self._go_extrude_pos(gcmd)
            self._heat(gcmd, self._change_temp(gcmd))
            grab = gcode_extruder_move(c["buffer_empty_len"],
                                       c["tn_extrude_velocity"])
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

    def cmd_flush(self, gcmd):
        def body(gcmd):
            c = self.cfg
            length = gcmd.get_float("LENGTH", None, minval=0.)
            vel = gcmd.get_float("VELOCITY", c["tn_extrude_velocity"], above=0.)
            segs = flush_segments(c, length)
            self._go_extrude_pos(gcmd)
            self._heat(gcmd, self._change_temp(gcmd))
            old = self._max_accel()
            for i, seg in enumerate(segs):
                self._move(gcmd, "Spülen %d/%d (%s mm)" % (i + 1, len(segs), _n(seg)),
                           gcode_flush_segment(seg, vel) + ["M400"])
                self._move(gcmd, "Abstreifen %d/%d" % (i + 1, len(segs)),
                           gcode_wipe(c, old))
            gcmd.respond_info("[k2_box] FLUSH %s mm (%s) mit F%s ok%s" % (
                _n(sum(segs)), " + ".join(_n(x) for x in segs), _n(vel),
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
            return
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

    def _runout_temp(self, gcmd):
        """Lade-/Spültemperatur: TEMP= oder max(Drucktemperatur vor der Pause
        (_PI_PARAM.hotend_temp, macros.cfg PAUSE), sonst Ziel, Tn_extrude_temp).
        ANNAHME: Stock nimmt die Materialtemperatur aus RFID/Datenbank."""
        t = gcmd.get_float("TEMP", None, above=0.)
        if t is not None:
            return float(t)
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
        return max(base, float(self.cfg["tn_extrude_temp"]))

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
        temp = self._runout_temp(gcmd)
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
            "KTWO_BOX_EXTRUDE TNN=%s TEMP=%s" % (tnn, _n(temp)),
            "KTWO_BOX_WASTE",
            "KTWO_BOX_FLUSH TNN=%s TEMP=%s" % (tnn, _n(temp)),
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
