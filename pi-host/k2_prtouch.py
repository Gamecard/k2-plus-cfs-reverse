#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2_prtouch.py – Klipper-Zusatzmodul [k2_prtouch] für PrTouch v3 (K2 Plus).

Offener Ersatz für Crealitys ``prtouch_v3_wrapper.so`` (Stock, armhf,
proprietär, Cython) auf einem Pi-Host unter UPSTREAM-Klipper – mit der
UNVERÄNDERTEN Creality-MCU-Firmware (Main GD32F303RET6 + Düse GD32F303CBT6,
Basis 0x08003000). Nutzt den EINGEBAUTEN Kraftsensor (Düse als Taster).

Grundlagen (nur gelesen, nichts gesendet):
- PRTOUCH-BEFUND-2026-10-16.md (Architektur, Live-Mitschnitt 99 Tastungen,
  Host-Rechenweg best_indx→step_bst_indx→zoft, NACHTRAG Stopp-Weg + KORREKTUR
  Basis 0x08003000)
- PRTOUCH-STOPP-BEWEIS-2026-10-16.md (statischer Beweis: MCU stoppt NICHT
  selbst; Swap = nur Zeitsync Düse HIGH / Main liest einmal; Host bricht ab;
  kein trsync/endstop_home/stepper_stop in prtouch-Pfaden)
- Bit-Spezifikation (lokale Arbeitsdatei) (Befehle/Formate/Konstanten §§1-6,
  Minimal-Variante §Minimal; MCU-Adressen wegen falscher Basis ungültig,
  Befehlsformate/Dictionary-Inhalte/Live-Werte gültig)
- Live-Gegenprüfung (lokale Arbeitsdatei) (Live-Rechenweg, Pseudocode §5.3, Fenster
  PRES_N=64/STEP_N=64, SPEED=5mm/s, HUB=5mm, Lüfter 0.6, TEST_SWAP,
  can_brk-Ausreißer T58→T59 versetzt ±0.25mm)
- Host-Analyse (lokale Arbeitsdatei) (Host-Module, G-Codes, TEST_SWAP/PRES_CHECK,
  Wellen SRC/FTR/CAL, apax nicht im Normalpfad)
- MCU-Dictionaries mcu-main-F008-GD32F303RET6.dict.json +
  noz-duese-F008-GD32F303CBT6.dict.json (exakte Formatstrings inkl.
  Creality-Schreibweisen „aqc_tick“, „resault_*“)
- live-1.1.7.0-configs/printer-live.cfg ([prtouch_v3] Pins/Schwellen,
  [bed_mesh] 9x9, [stepper_z] 0.0025mm/Microstep)
- lokale Arbeitsdateien/mitschnitt-auto-nivellierung.log (99 Tastungen,
  PRES_TIME 64×-0.0498..0.0, STEP_TIME 64×-0.0244..0.0, best 53-60,
  zoft 0.010-0.040, tri_time immer 0.000)

Stil wie pi-host/k2_motor_init.py und k2_box.py:
- Reine Logik ohne Klipper testbar (K2PrtouchController + pure Helfer).
- MCU-/Toolhead-Zugriffe gekapselt (PrtouchMcuOps / ToolheadOps;
  Klippy-Adapter nur in der Hülle, Fakes nur in den Tests).
- Bewegung nur mit Freigabe (allow_move, Default False).

HAUPTWEG (mode: probe, Default, Stand 16.10.2026 abends):
  [k2_prtouch] registriert sich als Upstream-Probe (Objekt 'probe',
  Pseudo-Pin probe:z_virtual_endstop). Stopp über einen normalen
  MCU_endstop auf step_swap_pin (Default !PC7, Main-MCU) mit trsync – wie
  Stock (BELEGT: Emulation + Fingerdruck-Test 19/19). Vor jeder Fahrt:
  Signalweg-Test (sta_swap 0/1 über den Endstop), Stock-probe_prepare
  (cont_prtouch_step -> start_prtouch_pres mit ack-Prüfung ->
  start_prtouch_step), Ruhetest, PC7 nicht ausgelöst; nach jeder Fahrt
  (auch bei Ausnahme): stop_prtouch_step + stop_prtouch_pres sta_swap=1.
  Damit gehen PROBE, QUERY_PROBE, PROBE_ACCURACY, PROBE_CALIBRATE,
  G28 Z (endstop_pin: probe:z_virtual_endstop), BED_MESH_CALIBRATE,
  Z_TILT_ADJUST usw. Geprüft gegen Upstream-Klipper (Commit s. README).
  Klassen: K2PrtouchProbeCore (rein, testbar), K2PrtouchPrinterProbe /
  K2PrtouchEndstopWrapper / K2DescendHelper / K2ProbeOffsets (Hülle).

RÜCKFALL (mode: steps bzw. K2_PRTOUCH_PROBE_STEPS): zeitgesteuerte
Schrittfahrt K2PrtouchController.probe_point mit hostseitiger Auswertung.

Sicherheit (verbindlich, im Code durchgesetzt + getestet):
  S0 Probe-Hauptweg: Fahrt nur mit allow_move: True, Antastgeschwindigkeit
     hart <= 5 mm/s (auch PROBE_SPEED=/G28-homing_speed), Weg außerhalb G28
     <= probe_max_travel_mm (Default 8, max 10), Entschärfen bei jeder
     Ausnahme, Abbruch VOR der Fahrt bei ack err != 0, Signalweg-/Ruhetest-
     Fehler oder schon ausgelöstem Endstop.
  S1 MCU stoppt nicht selbst → Abwärtsfahrt NUR in kurzen Schritten
     (step_mm Default 0.02, Maximum 0.05) mit niedriger Geschwindigkeit
     (Default 5mm/s, Maximum 5mm/s), nach JEDEM Schritt wait_moves
     (M400-Äquivalent), dann lesen/auswerten, bei Kontakt SOFORT stoppen
     und anheben. (Gilt für den Rückfall-Schrittweg.)
  S2 Harte Grenzen: max_travel_mm pro Antastung (Default 5mm Stock-Hub),
     Kraft-Notgrenze (Roh-Abweichung vom Nullpunkt > emergency_thresh →
     sofort Abbruch + Fehler), Timeout; bei jedem Fehler: Sampling stoppen,
     Z anheben (z_lift_mm), klare Meldung, kein Weiterprobe (außer genau
     einmaliger can_brk-Wiederholung versetzt).
  S3 Bewegung nur mit allow_move: True (Default False → nur
     Selbsttest/TEST_SWAP/Sensor lesen, keine Z-Bewegung);
     G-Code K2_PRTOUCH_PROBE_STEPS [DRY_RUN=1].
  S4 Vor jeder Antastung TEST_SWAP-Äquivalent + Sensor-Ruhetest
     (Rauschen/Drift, analog PRES_CHECK); sonst Abbruch VOR Bewegung.
  S5 Temperaturen (Düse 140/Bett 50) nur als Empfehlung prüfen (Warnung),
     nicht selbst heizen.

G-Codes (PrinterK2Prtouch):
- mode probe: Upstream-Befehle (PROBE, QUERY_PROBE, PROBE_ACCURACY,
  PROBE_CALIBRATE, Z_OFFSET_APPLY_PROBE) + G28 Z/BED_MESH_CALIBRATE/… .
- K2_PRTOUCH_PROBE_STEPS [X=.. Y=.. DRY_RUN=1] – Rückfall-Einzelantastung
  (beide Modi).
- nur mode steps: K2_PRTOUCH_HOME_Z [DRY_RUN=1], K2_PRTOUCH_MESH
  [COUNT=.. DRY_RUN=1] (eigene Ersatzbefehle wie bisher).
- K2_PRTOUCH_TEST_SWAP – Sync-/Versionscheck ohne Bewegung.
- K2_PRTOUCH_READ – Sensor lesen ohne Bewegung.
- K2_PRTOUCH_STATUS – letzter Status (auch Moonraker via get_status).

ANNAHMEN sind im Code mit „ANNAHME:“ markiert und in BERICHT.md gelistet.
"""

import math
import os
import statistics
import struct
import sys
import time

# =====================================================================
# Exakte MCU-Formatstrings (BELEGT via Dictionaries, inkl. Tippfehler)
# =====================================================================
# Main-Dict mcu-main-F008-GD32F303RET6.dict.json + Düsen-Dict identisch:
#   commands: config_prtouch_* (49/45/40), start_* (48/44/39),
#             read_*/cont_*/stop_* (46/42/41/47/43/38)
#   responses: ack_prtouch (95), resault_prtouch_pres/step/step_cnt/apax
#              (-30/-31/-32/94)
# Schreibweisen „aqc_tick“ und „resault_*“ sind Firmware-Realität und
# werden exakt übernommen (SPEZIFIKATION §1.1, STOPP-BEWEIS §0).

CMD_CONFIG_PRES = ("config_prtouch_pres oid=%c idx=%c swp_pin=%u "
                   "clk_pin=%u sdo_pin=%u")
CMD_CONFIG_STEP = ("config_prtouch_step oid=%c oid_xstp=%u oid_ystp=%u "
                   "oid_zstp=%u swp_pin=%u")
CMD_CONFIG_APAX = "config_prtouch_apax oid=%c oid_estp=%c"
CMD_START_PRES = ("start_prtouch_pres oid=%c cfg_regs=%c acq_tick=%u "
                  "ned_tftr=%c ned_hftr=%c ned_lftr=%u "
                  "min_hold=%i max_hold=%i add_hold=%i lmt_dead=%u")
CMD_START_STEP = "start_prtouch_step oid=%c aqc_tick=%u"
CMD_START_APAX = "start_prtouch_apax oid=%c cfg_regs=%c acq_tick=%u"
CMD_READ_PRES = "read_prtouch_pres oid=%c is_src=%c ch=%c idx=%c len=%c"
CMD_READ_STEP = "read_prtouch_step oid=%c idx=%c len=%c"
CMD_CONT_STEP = "cont_prtouch_step oid=%c"
CMD_STOP_PRES = "stop_prtouch_pres oid=%c sta_swap=%c"
CMD_STOP_STEP = "stop_prtouch_step oid=%c"
CMD_STOP_APAX = "stop_prtouch_apax oid=%c"
RESP_ACK = "ack_prtouch oid=%c err=%c expar0=%u expar1=%u"
RESP_PRES = ("resault_prtouch_pres oid=%c tri_chxs=%c buf_len=%c ch=%c "
             "idx=%c len=%c ticks=%.*s datas=%.*s")
RESP_STEP = ("resault_prtouch_step oid=%c buf_len=%c idx=%c len=%c "
             "ticks=%.*s datas=%.*s")
RESP_STEP_CNT = "resault_prtouch_step_cnt oid=%c cnt_x=%i cnt_y=%i cnt_z=%i"
RESP_APAX = ("resault_prtouch_apax oid=%c ch=%c len=%c ticks=%.*s "
             "datas=%.*s espds=%.*s")

# Alle Befehle/Antworten als Dict zum Test (f) gegen die JSON-Dictionaries.
ALL_FORMATS = {
    "config_prtouch_pres": CMD_CONFIG_PRES,
    "config_prtouch_step": CMD_CONFIG_STEP,
    "config_prtouch_apax": CMD_CONFIG_APAX,
    "start_prtouch_pres": CMD_START_PRES,
    "start_prtouch_step": CMD_START_STEP,
    "start_prtouch_apax": CMD_START_APAX,
    "read_prtouch_pres": CMD_READ_PRES,
    "read_prtouch_step": CMD_READ_STEP,
    "cont_prtouch_step": CMD_CONT_STEP,
    "stop_prtouch_pres": CMD_STOP_PRES,
    "stop_prtouch_step": CMD_STOP_STEP,
    "stop_prtouch_apax": CMD_STOP_APAX,
    "ack_prtouch": RESP_ACK,
    "resault_prtouch_pres": RESP_PRES,
    "resault_prtouch_step": RESP_STEP,
    "resault_prtouch_step_cnt": RESP_STEP_CNT,
    "resault_prtouch_apax": RESP_APAX,
}

# =====================================================================
# Konstanten (BELEGT aus Live-Mitschnitt + Config; Lücken = ANNAHME)
# =====================================================================

CLOCK_FREQ = 120000000  # BELEGT Dict :82 + Stats freq=119998498/222
PRES_N = 64  # BELEGT PRES_TIME 64 Punkte
STEP_N = 64  # BELEGT STEP_TIME 64 Punkte
PRES_DT_S = 0.00078125  # BELEGT pres_acq_tkms 0.78125
# BELEGT 16.10.2026 (Emulation Stock-probe_prepare): step_acq_tkms
# 0.390625 ms -> aqc_tick 46875 (alte ANNAHME 0.0244s/64 bzw. 45700 FALSCH).
STEP_DT_S = 0.000390625

# Stock-Konfigwerte (printer-live.cfg [prtouch_v3]) und ihre Abbildung auf
# die MCU-Parameter – BELEGT 16.10.2026 per Emulation des Stock-Moduls
# (Stock-probe_prepare/home_start mitgelesen, Zuordnung durch gezielte
# Variation der Konfigwerte eindeutig). Umrechnung: stock_to_mcu_params().
STOCK_CFG_REGS = 60  # pres_cfg_regs
STOCK_PRES_ACQ_TKMS = 0.78125  # pres_acq_tkms [ms]
STOCK_STEP_ACQ_TKMS = 0.390625  # step_acq_tkms [ms]
STOCK_TRI_FTER = (5, 1, 0.8)  # pres_tri_fter
STOCK_TRI_HOLD = (4000, 10000, 500)  # pres_tri_hold
STOCK_DED_TKMS = 128.0  # pres_ded_tkms
STOCK_ACQ_TICK = 93750  # = round(0.78125/1000 * 120 MHz)
STOCK_AQC_TICK = 46875  # = round(0.390625/1000 * 120 MHz)
STOCK_NED = (5, 1000, 800)  # MCU-Werte ned_tftr/hftr/lftr (BELEGT)
STOCK_HOLDS = (4000, 10000, 500)  # MCU-Werte min/max/add_hold (BELEGT)
STOCK_LMT_DEAD = 128  # MCU-Wert lmt_dead = int(pres_ded_tkms) (BELEGT)
STOCK_SPEED = 5.0  # BELEGT STEP_DATA-Fall + liveconfig speed
STOCK_HUB = 5.0  # BELEGT horizontal_move_z + PRTOUCH_MOVE Z5.00
STOCK_FANS = 0.6  # BELEGT prth_min_fans + DISTUURB_CTL
STOCK_TEMP_NOZZLE = 140.0  # BELEGT G29-Ablauf
STOCK_TEMP_BED = 50.0  # BELEGT G29-Ablauf
STOCK_Z_STEP_MM = 0.0025  # BELEGT rotation 8/200/16 + halt_step_dist
STOCK_VERSIONS = {"PY": 78, "PRES": 71, "STEP": 71}  # BELEGT mitschnitt:1473
STOCK_CONTACT_THRESH = 3000  # ANNAHME: TEST_PRES_AMPLITUDE threshld 3000
# (FTR ±600 Ruhe vs -9489 Spitze) – als Kontakt-Schwelle plausibel.
STOCK_EMERGENCY_THRESH = 15000  # ANNAHME: pres_chk_hold 15000 als
# Notgrenze (BELEGT Existenz, ANNAHME Rolle); > normale Spitze (~9500),
# < defekter Sensor (50000+).
STOCK_REST_MAX_DIF = 3000  # ANNAHME: gleiche Schwelle für Ruhetest
# (analog PRES_CHECK „dynamic detection“; BELEGT pres_chk_hold Existenz).
STOCK_RETRY_DX = -0.25  # BELEGT T58→T59: 132.5→132.25
STOCK_RETRY_DY = 0.25  # BELEGT T58→T59: 47.5→47.75

# Harte Sicherheits-Maxima (verbindlich, S1):
STEP_MM_DEFAULT = 0.02
STEP_MM_MAX = 0.05
SPEED_DEFAULT = 5.0
SPEED_MAX = 5.0
MAX_TRAVEL_MAX = 10.0  # Review 16.10.: harte Obergrenze, Stock-Hub 5 mm
MAX_TRAVEL_DEFAULT = 5.0  # Stock-Hub
# Probe-Hauptweg (mode: probe): maximaler Abwärtsweg je Antastung außerhalb
# von G28 (PROBE, BED_MESH_CALIBRATE, Z_TILT_ADJUST …). Default 8 mm deckt
# horizontal_move_z 5 + Bettwelligkeit ab; harte Grenze wie MAX_TRAVEL_MAX.
PROBE_MAX_TRAVEL_DEFAULT = 8.0
ARM_SETTLE_DEFAULT_S = 0.2  # ANNAHME: Wartezeit nach dem Scharfschalten
ARM_SETTLE_MAX_S = 2.0
MODES = ("probe", "steps")
Z_LIFT_DEFAULT = 2.0  # wie sample_retract_dist 2.0
TIMEOUT_DEFAULT_S = 30.0  # wie z_align timeout 30s

READ_CHUNK = 8  # BELEGT 16.10.2026 live: read_prtouch_* liefert <= 8 Samples
# je Abfrage aus einem 64er-Ringpuffer (idx = Ringposition, laufend
# überschrieben) -> 8 Blöcke lesen, nach Tick sortieren (merge_ring).

HOST_PY_VER = 1  # eigene Host-Version (nicht Stock-78; ANNAHME: nur
# geloggt, kein exakter Match nötig – FW-Updates ändern PRES/STEP_VER).


# =====================================================================
# Pin-Helfer (wie k2_motor_init.py)
# =====================================================================

def _clean_pin_name(pin_raw):
    """'!PC7' / 'nozzle_mcu:PA15' -> 'PA15' (Invert- + MCU-Präfix weg)."""
    s = str(pin_raw).strip()
    if ":" in s:
        s = s.split(":", 1)[1]
    return s.lstrip("!^~").strip()


def pin_number_from_enum(enum_pin, pin_name):
    """Klipper-Pinnummer aus MCU-Enumeration 'pin' (ANNAHME Offset-Logik).

    BELEGT Dict-Einträge PA0 [0,16], PB0 [16,16], PC0 [32,16] …:
    PB9 = Basis PB0 (16) + 9 = 25.
    """
    clean = _clean_pin_name(pin_name)
    if not isinstance(enum_pin, dict):
        raise ValueError("Pin-Enumeration fehlt")
    if clean in enum_pin:
        v = enum_pin[clean]
        if isinstance(v, (list, tuple)):
            return int(v[0])
        return int(v)
    import re as _re
    m = _re.match(r"^([A-Z]+)(\d+)$", clean)
    if m:
        base_key = "%s0" % m.group(1)
        if base_key in enum_pin:
            v = enum_pin[base_key]
            base = int(v[0]) if isinstance(v, (list, tuple)) else int(v)
            return base + int(m.group(2))
    raise ValueError("Pin %r nicht in Enumeration" % (pin_name,))


def split_cs0_pin(spec):
    """'nozzle_mcu:PB13, nozzle_mcu:PB14' -> [pin1, pin2]."""
    parts = [p.strip() for p in str(spec).split(",") if p.strip()]
    return parts


# =====================================================================
# Config (rein + Klipper-Hülle)
# =====================================================================

def _num_list(raw, default, count, name):
    """'5, 1, 0.8' / [5, 1, 0.8] / None -> Tupel aus count Zahlen."""
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return tuple(float(v) for v in default)
    if isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        parts = [p for p in str(raw).replace(";", ",").split(",")
                 if p.strip() != ""]
    try:
        vals = tuple(float(str(p).strip()) for p in parts)
    except ValueError:
        raise ValueError("%s muss %d Zahlen enthalten: %r"
                         % (name, count, raw))
    if len(vals) != count:
        raise ValueError("%s muss %d Zahlen enthalten: %r"
                         % (name, count, raw))
    return vals


def stock_to_mcu_params(pres_cfg_regs, pres_acq_tkms, pres_tri_fter,
                        pres_tri_hold, pres_ded_tkms, step_acq_tkms,
                        clock_freq_pres=CLOCK_FREQ,
                        clock_freq_step=CLOCK_FREQ):
    """Stock-Konfigwerte -> MCU-Parameter für start_prtouch_pres/_step.

    BELEGT 16.10.2026 (Emulation des Stock-Moduls, Zuordnung durch
    Variation eindeutig; beide MCUs 120 MHz laut klippy.log):
      cfg_regs = pres_cfg_regs
      acq_tick = round(pres_acq_tkms / 1000 * CLOCK_FREQ Düse)
      ned_tftr = pres_tri_fter[0]
      ned_hftr = round(pres_tri_fter[1] * 1000)
      ned_lftr = round(pres_tri_fter[2] * 1000)
      min/max/add_hold = pres_tri_hold
      lmt_dead = int(pres_ded_tkms)   (abgeschnitten, nicht gerundet)
      aqc_tick = round(step_acq_tkms / 1000 * CLOCK_FREQ Main)
    Stock live: [60, 93750, 5, 1000, 800, 4000, 10000, 500, 128], 46875.
    ANNAHME (nicht prüfbar): ned_tftr/ned_hftr stehen im Dictionary als
    %c; Stock sendet trotzdem 1000 (VLQ-kodiert, Upstream-msgproto
    PT_byte kodiert ebenfalls ohne Begrenzung) – wir senden exakt wie Stock.
    """
    f = tuple(float(v) for v in pres_tri_fter)
    h = tuple(pres_tri_hold)
    return {
        "cfg_regs": int(pres_cfg_regs),
        "acq_tick": int(round(float(pres_acq_tkms) / 1000.0
                              * float(clock_freq_pres))),
        "ned_tftr": int(round(f[0])),
        "ned_hftr": int(round(f[1] * 1000.0)),
        "ned_lftr": int(round(f[2] * 1000.0)),
        "min_hold": int(round(float(h[0]))),
        "max_hold": int(round(float(h[1]))),
        "add_hold": int(round(float(h[2]))),
        "lmt_dead": int(float(pres_ded_tkms)),
        "aqc_tick": int(round(float(step_acq_tkms) / 1000.0
                              * float(clock_freq_step))),
    }


def parse_options(opts):
    """Reines Dict (keys klein) -> strukturierte Config.

    Pflicht: keine (alle mit sicheren Defaults); Pins/Stepper-OIDs mit
    Stock-Defaults aus printer-live.cfg. Harte Sicherheitsgrenzen werden
    hier durchgesetzt (ValueError bei Überschreitung).
    """
    o = {str(k).strip().lower(): v for k, v in dict(opts).items()}

    def _bool(key, default):
        v = str(o.get(key, str(default))).strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off"):
            return False
        raise ValueError("%s muss bool sein: %r" % (key, o.get(key)))

    def _float(key, default):
        try:
            return float(str(o.get(key, default)).strip())
        except ValueError:
            raise ValueError("%s muss Zahl sein: %r" % (key, o.get(key)))

    def _int(key, default):
        try:
            return int(str(o.get(key, default)).strip())
        except ValueError:
            raise ValueError("%s muss int sein: %r" % (key, o.get(key)))

    def _str(key, default):
        v = o.get(key, default)
        if v is None:
            return None
        s = str(v).strip()
        return s if s != "" else None

    cfg = {}
    # --- Sicherheit ---
    cfg["allow_move"] = _bool("allow_move", False)
    cfg["step_mm"] = _float("step_mm", STEP_MM_DEFAULT)
    cfg["probe_speed"] = _float("probe_speed", SPEED_DEFAULT)
    cfg["lift_speed"] = _float("lift_speed", SPEED_DEFAULT)
    cfg["max_travel_mm"] = _float("max_travel_mm", MAX_TRAVEL_DEFAULT)
    cfg["z_lift_mm"] = _float("z_lift_mm", Z_LIFT_DEFAULT)
    cfg["probe_timeout_s"] = _float("probe_timeout_s", TIMEOUT_DEFAULT_S)
    cfg["contact_thresh"] = _float("contact_thresh", STOCK_CONTACT_THRESH)
    cfg["emergency_thresh"] = _float("emergency_thresh",
                                     STOCK_EMERGENCY_THRESH)
    cfg["rest_max_dif"] = _float("rest_max_dif", STOCK_REST_MAX_DIF)
    cfg["rest_min_consec"] = _int("rest_min_consec", 2)
    if not 0.0 < cfg["step_mm"] <= STEP_MM_MAX + 1e-12:
        raise ValueError("step_mm muss in (0, %.2f] sein (S1): %r"
                         % (STEP_MM_MAX, o.get("step_mm")))
    if not 0.0 < cfg["probe_speed"] <= SPEED_MAX + 1e-12:
        raise ValueError("probe_speed muss in (0, %.1f] sein (S1): %r"
                         % (SPEED_MAX, o.get("probe_speed")))
    if not 0.0 < cfg["lift_speed"] <= 100.0 + 1e-12:
        raise ValueError("lift_speed muss in (0, 100] sein: %r"
                         % (o.get("lift_speed"),))
    if not 0.0 < cfg["max_travel_mm"] <= MAX_TRAVEL_MAX + 1e-12:
        raise ValueError("max_travel_mm muss in (0, %.1f] sein: %r"
                         % (MAX_TRAVEL_MAX, o.get("max_travel_mm")))
    if not 0.0 < cfg["z_lift_mm"]:
        raise ValueError("z_lift_mm muss > 0 sein")
    if cfg["contact_thresh"] <= 0:
        raise ValueError("contact_thresh muss > 0 sein")
    if cfg["emergency_thresh"] <= cfg["contact_thresh"]:
        raise ValueError("emergency_thresh muss > contact_thresh sein "
                         "(sonst löst jeder Kontakt die Notgrenze aus)")

    # --- Betriebsart + Probe-Hauptweg (Upstream-[probe]-Ersatz) ---
    cfg["mode"] = (_str("mode", "probe") or "probe").lower()
    if cfg["mode"] not in MODES:
        raise ValueError("mode muss 'probe' oder 'steps' sein: %r"
                         % (o.get("mode"),))
    # 'speed' = Upstream-Optionsname (ProbeParameterHelper); Default ist
    # probe_speed, damit beide Wege dieselbe Geschwindigkeit nutzen.
    cfg["speed"] = _float("speed", cfg["probe_speed"])
    if not 0.0 < cfg["speed"] <= SPEED_MAX + 1e-12:
        raise ValueError("speed muss in (0, %.1f] sein (S1): %r"
                         % (SPEED_MAX, o.get("speed")))
    cfg["z_offset"] = _float("z_offset", 0.0)  # wie Stock (Düse = Taster)
    pe = _str("position_endstop", None)
    cfg["position_endstop"] = (cfg["z_offset"] if pe is None
                               else _float("position_endstop", 0.0))
    cfg["probe_max_travel_mm"] = _float("probe_max_travel_mm",
                                        PROBE_MAX_TRAVEL_DEFAULT)
    if not 0.0 < cfg["probe_max_travel_mm"] <= MAX_TRAVEL_MAX + 1e-12:
        raise ValueError("probe_max_travel_mm muss in (0, %.1f] sein: %r"
                         % (MAX_TRAVEL_MAX, o.get("probe_max_travel_mm")))
    cfg["arm_settle_s"] = _float("arm_settle_s", ARM_SETTLE_DEFAULT_S)
    if not 0.0 <= cfg["arm_settle_s"] <= ARM_SETTLE_MAX_S + 1e-12:
        raise ValueError("arm_settle_s muss in [0, %.1f] sein: %r"
                         % (ARM_SETTLE_MAX_S, o.get("arm_settle_s")))
    # Signalweg-Test PA15->PC7 über den Endstop vor JEDER Fahrt (sta_swap
    # 0 -> Endstop ausgelöst, 1 -> frei), Ruhetest nach dem Scharfschalten,
    # optionale Diagnose-Auslesung des eingefrorenen Rings danach (Stock).
    cfg["swap_check"] = _bool("swap_check", True)
    cfg["rest_check"] = _bool("rest_check", True)
    cfg["diag_readout"] = _bool("diag_readout", False)

    # --- MCUs / Pins (Stock-Defaults aus printer-live.cfg) ---
    cfg["main_mcu"] = _str("main_mcu", "mcu") or "mcu"
    cfg["nozzle_mcu"] = _str("nozzle_mcu", "nozzle_mcu") or "nozzle_mcu"
    cfg["pres_idx"] = _int("pres_idx", 0)
    cfg["pres_swap_pin"] = _str("pres_swap_pin",
                                "nozzle_mcu:PA15") or "nozzle_mcu:PA15"
    cfg["step_swap_pin"] = _str("step_swap_pin", "!PC7") or "!PC7"
    cfg["pres_cs0_pin"] = _str("pres_cs0_pin",
                               "nozzle_mcu:PB13, nozzle_mcu:PB14")
    # ANNAHME clk/sdo: stehen NICHT in printer-live.cfg (Negativbeleg
    # SPEZIFIKATION §1.3). Auftrag verlangt Ableitung aus pres_cs0_pin
    # PB13/PB14 mit konfigurierbarer Zuordnung. Falls pres_clk_pin/
    # pres_sdo_pin gesetzt, haben sie Vorrang (für Ghidra/Live-Fund).
    cfg["pres_clk_pin"] = _str("pres_clk_pin", None)
    cfg["pres_sdo_pin"] = _str("pres_sdo_pin", None)
    cfg["pres_clk_sdo_swap"] = _bool("pres_clk_sdo_swap", False)
    cs0 = split_cs0_pin(cfg["pres_cs0_pin"] or "")
    if cfg["pres_clk_pin"] is None or cfg["pres_sdo_pin"] is None:
        if len(cs0) != 2:
            raise ValueError("pres_cs0_pin muss 2 Pins enthalten "
                             "(clk/sdo-Ableitung, ANNAHME): %r"
                             % (cfg["pres_cs0_pin"],))
        d_clk, d_sdo = cs0[0], cs0[1]
        if cfg["pres_clk_sdo_swap"]:
            d_clk, d_sdo = d_sdo, d_clk
        if cfg["pres_clk_pin"] is None:
            cfg["pres_clk_pin"] = d_clk
        if cfg["pres_sdo_pin"] is None:
            cfg["pres_sdo_pin"] = d_sdo

    # --- Sampling-Parameter (Stock-Stil, BELEGT 16.10.2026) ---
    # Eingabe wie printer-live.cfg [prtouch_v3]; MCU-Werte werden daraus
    # berechnet (stock_to_mcu_params). Direkte MCU-Optionen (cfg_regs,
    # acq_tick, aqc_tick, ned_*, *_hold, lmt_dead) übersteuern einzeln.
    if "ned_lftr_scale" in o:
        raise ValueError("ned_lftr_scale entfällt (alte ANNAHME widerlegt): "
                         "ned_lftr = round(pres_tri_fter[2] * 1000) – "
                         "pres_tri_fter oder ned_lftr (MCU-Wert) setzen")
    cfg["clock_freq"] = _float("clock_freq", CLOCK_FREQ)
    if cfg["clock_freq"] <= 0:
        raise ValueError("clock_freq muss > 0 sein")
    cfg["pres_cfg_regs"] = _int("pres_cfg_regs", STOCK_CFG_REGS)
    cfg["pres_acq_tkms"] = _float("pres_acq_tkms", STOCK_PRES_ACQ_TKMS)
    cfg["step_acq_tkms"] = _float("step_acq_tkms", STOCK_STEP_ACQ_TKMS)
    cfg["pres_ded_tkms"] = _float("pres_ded_tkms", STOCK_DED_TKMS)
    cfg["pres_tri_fter"] = _num_list(o.get("pres_tri_fter"), STOCK_TRI_FTER,
                                     3, "pres_tri_fter")
    cfg["pres_tri_hold"] = _num_list(o.get("pres_tri_hold"), STOCK_TRI_HOLD,
                                     3, "pres_tri_hold")
    mcu_p = stock_to_mcu_params(
        cfg["pres_cfg_regs"], cfg["pres_acq_tkms"], cfg["pres_tri_fter"],
        cfg["pres_tri_hold"], cfg["pres_ded_tkms"], cfg["step_acq_tkms"],
        cfg["clock_freq"], cfg["clock_freq"])
    for key, val in mcu_p.items():
        cfg[key] = _int(key, val) if key in o else int(val)
    if cfg["acq_tick"] <= 0 or cfg["aqc_tick"] <= 0:
        raise ValueError("acq_tick/aqc_tick müssen > 0 sein")

    # --- Datenformat (ANNAHME, zentral konfigurierbar) ---
    # BELEGT: ticks/datas sind Byte-Arrays (%.*s), 64 Samples, little/
    # signed/median-Strings in unzip_data. Byte-Breite statisch nicht
    # entscheidbar → Arbeitshypothese 4B u32-LE / 4B s32-LE (MCU-clock).
    # BELEGT 16.10.2026 (Live-Lesetest): MCU packt ticks/datas
    # (n + Größenmaske + Delta-Kodierung) -> packing "packed" (Default).
    # "struct" = alte Arbeitshypothese (feste Breite, tick/data_format).
    cfg["packing"] = _str("packing", "packed") or "packed"
    if cfg["packing"] not in ("packed", "struct"):
        raise ValueError("packing muss 'packed' oder 'struct' sein: %r"
                         % (cfg["packing"],))
    cfg["tick_format"] = _str("tick_format", "<I") or "<I"
    cfg["data_format"] = _str("data_format", "<i") or "<i"
    cfg["tick_size"] = _int("tick_size", 4)
    cfg["data_size"] = _int("data_size", 4)
    for k in ("tick_format", "data_format"):
        try:
            struct.calcsize(cfg[k])
        except struct.error:
            raise ValueError("%s ungültig: %r" % (k, cfg[k]))
    if cfg["tick_size"] <= 0 or cfg["data_size"] <= 0:
        raise ValueError("tick_size/data_size müssen > 0 sein")
    cfg["step_dist_mm"] = _float("step_dist_mm", STOCK_Z_STEP_MM)
    if cfg["step_dist_mm"] <= 0:
        raise ValueError("step_dist_mm muss > 0 sein")

    # --- can_brk / Retry ---
    cfg["retry_dx"] = _float("retry_dx", STOCK_RETRY_DX)
    cfg["retry_dy"] = _float("retry_dy", STOCK_RETRY_DY)
    cfg["retry_lift_mm"] = _float("retry_lift_mm", 1.0)
    # ANNAHME-Schranken aus genau 1 Beobachtung (T58): best<=53 UND
    # step<=42 UND zoft>=0.040 → can_brk False. Nur UND-Verknüpfung,
    # damit normale Randwerte (59/58/…) nicht fälschlich verwerfen.
    cfg["can_brk_min_best"] = _int("can_brk_min_best", 53)
    cfg["can_brk_min_step"] = _int("can_brk_min_step", 42)
    cfg["can_brk_max_zoft"] = _float("can_brk_max_zoft", 0.040)

    # --- Temperaturen (nur Warnung, S5) ---
    cfg["temp_nozzle_warn"] = _float("temp_nozzle_warn", STOCK_TEMP_NOZZLE)
    cfg["temp_bed_warn"] = _float("temp_bed_warn", STOCK_TEMP_BED)
    cfg["temp_tolerance"] = _float("temp_tolerance", 15.0)

    # --- Stepper-OIDs (ANNAHME Auto-Lookup, manuell übersteuerbar) ---
    cfg["stepper_x_oid"] = o.get("stepper_x_oid")
    cfg["stepper_y_oid"] = o.get("stepper_y_oid")
    cfg["stepper_z_oid"] = o.get("stepper_z_oid")
    for k in ("stepper_x_oid", "stepper_y_oid", "stepper_z_oid"):
        if cfg[k] is not None and str(cfg[k]).strip() != "":
            try:
                cfg[k] = int(str(cfg[k]).strip())
            except ValueError:
                raise ValueError("%s muss int sein: %r" % (k, cfg[k]))
        else:
            cfg[k] = None

    cfg["shutdown_on_failure"] = _bool("shutdown_on_failure", False)
    return cfg


def parse_klipper_config(config):
    """Klipper-ConfigWrapper -> parse_options (ANNAHME zur API).

    Wie k2_motor_init.parse_klipper_config: get_options()/get/getint/
    getfloat/getboolean tolerant lesen. FakeConfig in Tests bietet dieselben.
    """
    keys = []
    try:
        if hasattr(config, "get_options"):
            keys = list(config.get_options())
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
    # Fix-Keys nachholen, falls get_options leer war:
    for k in ("allow_move", "step_mm", "probe_speed", "lift_speed",
              "max_travel_mm", "z_lift_mm", "probe_timeout_s",
              "contact_thresh", "emergency_thresh", "rest_max_dif",
              "main_mcu", "nozzle_mcu", "pres_idx", "pres_swap_pin",
              "step_swap_pin", "pres_cs0_pin", "pres_clk_pin", "pres_sdo_pin",
              "pres_clk_sdo_swap", "cfg_regs", "acq_tick", "aqc_tick",
              "ned_tftr", "ned_hftr", "ned_lftr", "ned_lftr_scale",
              "min_hold", "max_hold", "add_hold", "lmt_dead",
              "packing", "tick_format", "data_format", "tick_size", "data_size",
              "clock_freq", "step_dist_mm", "retry_dx", "retry_dy",
              "retry_lift_mm", "temp_nozzle_warn", "temp_bed_warn",
              "temp_tolerance", "shutdown_on_failure",
              # Probe-Hauptweg + Stock-Stil-Sampling (16.10.2026):
              "mode", "speed", "z_offset", "position_endstop",
              "probe_max_travel_mm", "arm_settle_s", "swap_check",
              "rest_check", "diag_readout", "pres_cfg_regs",
              "pres_acq_tkms", "step_acq_tkms", "pres_tri_fter",
              "pres_tri_hold", "pres_ded_tkms", "rest_min_consec",
              "stepper_x_oid", "stepper_y_oid", "stepper_z_oid",
              "can_brk_min_best", "can_brk_min_step", "can_brk_max_zoft"):
        if k not in opts:
            v = _g(k, None)
            if v is not None and str(v) != "":
                opts[k] = v
    return parse_options(opts)


def ned_lftr_mcu_value(cfg):
    """MCU-Wert ned_lftr (BELEGT: round(pres_tri_fter[2] * 1000), 0.8 -> 800;
    schon in parse_options berechnet)."""
    return int(cfg["ned_lftr"])


def build_start_pres_tuple(cfg, oid):
    """(oid, cfg_regs, acq_tick, ned_tftr, ned_hftr, ned_lftr_u,
    min_hold, max_hold, add_hold, lmt_dead) für CMD_START_PRES."""
    return (int(oid), int(cfg["cfg_regs"]), int(cfg["acq_tick"]),
            int(cfg["ned_tftr"]), int(cfg["ned_hftr"]),
            int(ned_lftr_mcu_value(cfg)),
            int(cfg["min_hold"]), int(cfg["max_hold"]),
            int(cfg["add_hold"]), int(cfg["lmt_dead"]))


def build_start_step_tuple(cfg, oid):
    """(oid, aqc_tick) für CMD_START_STEP (Tippfehler aqc exakt)."""
    return (int(oid), int(cfg["aqc_tick"]))


# =====================================================================
# Dekodierung (ANNAHME u32-LE/s32-LE, zentral konfigurierbar)
# =====================================================================

def decode_int_list(raw, fmt, size, count=None):
    """Roh-Bytes -> int-Liste (struct, z.B. '<I'/4 oder '<i'/4)."""
    b = bytes(raw)
    if len(b) % int(size) != 0:
        raise ValueError("Byte-Länge %d nicht teilbar durch %d"
                         % (len(b), int(size)))
    n = len(b) // int(size)
    if count is not None and n != int(count):
        raise ValueError("erwartet %d Samples, erhalten %d"
                         % (int(count), n))
    out = []
    for i in range(n):
        (v,) = struct.unpack_from(str(fmt), b, i * int(size))
        out.append(int(v))
    return out


def decode_packed(raw):
    """Gepackter MCU-Puffer -> int-Liste (BELEGT live 16.10.2026).

    Format (ticks UND datas von resault_prtouch_pres, 315+3/318 Paketen
    exakt, s. PRTOUCH-LESETEST-BEFUND-2026-10-16.md):
      [n][Größenmaske, ceil(n/4) Byte, Big-Endian]
      [Wert 0 absolut][Wert 1..n-1 als Delta zum Vorgänger]
    Je Sample i liefert die Maske 2 Bit (LSB zuerst): Länge = Feld+1 Byte,
    jeder Wert little-endian mit Vorzeichen. Beispiel ticks:
    08 aaab | 4B Startzeit | 7× 3B Delta (~94 900 Ticks = 0,79 ms).
    """
    b = bytes(raw)
    if not b:
        return []
    n = b[0]
    mb = (n + 3) // 4
    if len(b) < 1 + mb:
        raise ValueError("gepackter Puffer zu kurz: %d Byte" % len(b))
    mask = int.from_bytes(b[1:1 + mb], "big")
    p = 1 + mb
    out = []
    for i in range(n):
        sz = ((mask >> (2 * i)) & 3) + 1
        if p + sz > len(b):
            raise ValueError("gepackter Puffer endet bei Sample %d" % i)
        v = int.from_bytes(b[p:p + sz], "little", signed=True)
        p += sz
        out.append(v if i == 0 else out[-1] + v)
    if p != len(b):
        raise ValueError("gepackter Puffer: %d Restbytes" % (len(b) - p))
    return out


def _decode_buf(raw, cfg, fmt, size):
    if isinstance(raw, (list, tuple)):
        return [int(x) for x in raw]  # schon dekodiert (merge_ring)
    if cfg.get("packing", "packed") == "packed":
        return decode_packed(raw)
    return decode_int_list(raw, fmt, size)


def decode_ticks(raw, cfg):
    """ticks-Bytes -> Tick-Liste (packed BELEGT; struct = alte Annahme).
    Gepackte Ticks sind kumuliert -> auf u32 begrenzen (MCU-Uhr läuft über)."""
    if isinstance(raw, (list, tuple)):
        return [int(x) & 0xFFFFFFFF for x in raw]  # schon dekodiert
    if cfg.get("packing", "packed") == "packed":
        return [t & 0xFFFFFFFF for t in decode_packed(raw)]
    return decode_int_list(raw, cfg["tick_format"], cfg["tick_size"],
                           count=PRES_N if len(bytes(raw)) // int(cfg["tick_size"]) == PRES_N else None)


def decode_pres_datas(raw, cfg):
    """datas-Bytes (pres) -> Rohwert-Liste (packed BELEGT)."""
    return _decode_buf(raw, cfg, cfg["data_format"], cfg["data_size"])


def decode_step_counts(raw, cfg):
    """datas-Bytes (step) -> Zählerliste (ANNAHME: gleiche Packung wie pres,
    Main-MCU-Seite live noch nicht mitgeschnitten)."""
    return _decode_buf(raw, cfg, cfg["data_format"], cfg["data_size"])


def merge_ring(ticks, datas, n=PRES_N):
    """Blöcke aus dem 64er-Ringpuffer zusammenführen (BELEGT live 16.10.):
    Duplikate (gleicher Tick) entfernen, nach Tick sortieren (u32-Überlauf
    relativ zum ersten Sample), die jüngsten n behalten."""
    if len(ticks) != len(datas):
        raise ValueError("ticks/datas ungleich lang: %d/%d"
                         % (len(ticks), len(datas)))
    if not ticks:
        return [], []
    ref = int(ticks[0])
    seen = {}
    for t, d in zip(ticks, datas):
        seen[int(t) & 0xFFFFFFFF] = int(d)
    keyed = sorted(seen.items(),
                   key=lambda kv: (kv[0] - ref + (1 << 31)) & 0xFFFFFFFF)
    keyed = keyed[-int(n):]
    return [k for k, _ in keyed], [v for _, v in keyed]


def ticks_to_times(ticks, freq=None):
    """MCU-ticks -> Sekunden relativ zum Fensterende (letzter = 0.0).

    BELEGT: PRES_TIME 64× -0.0498..0.0, STEP_TIME 64× -0.0244..0.0;
    Swap-synchronisiert (step_bst_time == pres_bst_time an allen 99).
    ANNAHME: beide Fenster teilen bereits die Swap-Nullpunkt-Basis
    (Düse HIGH / Main liest einmal); hier nur (t - t_last)/freq.
    """
    f = float(freq if freq else CLOCK_FREQ)
    if not ticks:
        raise ValueError("leere ticks")
    # 32-Bit-Wrap tolerant (ANNAHME clock32→clock64 wie Upstream):
    last = int(ticks[-1])
    out = []
    for t in ticks:
        d = int(t) - last
        # Wrap-Korrektur für 32-Bit-Überlauf:
        if d > 2 ** 31:
            d -= 2 ** 32
        elif d < -(2 ** 31):
            d += 2 ** 32
        out.append(float(d) / f)
    return out


def step_counts_to_mm(counts, cfg, relative=True):
    """Zähler -> mm. ANNAHME: counts * step_dist_mm; relativ zum Ende.

    BELEGT: STEP_DATA fällt z.B. 0.122→0.000 (Ende = tri_pose);
    0.122/0.0025 ≈ 49 Mikroschritte ≈ 24.4ms×5mm/s. relativ=True zieht
    den Endwert ab, sodass tri_pose ≈ 0 (wie live).
    """
    mms = [float(c) * float(cfg["step_dist_mm"]) for c in counts]
    if relative and mms:
        base = mms[-1]
        mms = [v - base for v in mms]
    return mms


def interp_linear(xs, ys, x):
    """Lineare Interpolation mit Clamp (für bst_pose)."""
    if len(xs) != len(ys) or not xs:
        raise ValueError("interp: leere Achsen")
    if x <= xs[0]:
        return float(ys[0])
    if x >= xs[-1]:
        return float(ys[-1])
    for i in range(1, len(xs)):
        if xs[i] >= x:
            x0, x1 = float(xs[i - 1]), float(xs[i])
            y0, y1 = float(ys[i - 1]), float(ys[i])
            if x1 == x0:
                return y0
            a = (x - x0) / (x1 - x0)
            return y0 + a * (y1 - y0)
    return float(ys[-1])


# =====================================================================
# Kontakterkennung nach Live-Rechenweg (BELEGT Verfahren, ANNAHME Details)
# =====================================================================

def median_int(data):
    return float(statistics.median(list(data)))


def rest_info(datas):
    """Min/max/avg/dif/len wie PRES_CHECK_INFO (BELEGT Format)."""
    vals = list(datas)
    if not vals:
        raise ValueError("leere datas")
    mn, mx = min(vals), max(vals)
    avg = sum(vals) / len(vals)
    return {"min": mn, "max": mx, "avg": avg, "dif": mx - mn,
            "len": len(vals)}


def check_rest_ok(datas, cfg):
    """Sensor-Ruhetest (analog PRES_CHECK). True/False + Info.

    ANNAHME Schranke: dif <= rest_max_dif (Default 3000). BELEGT: FTR
    ±600 Ruhe, PRES_CHECK SUCCESS bei dif=55285 über 7668 Samples
    (langes Fenster) – unser Kurzfenster (64) muss deutlich ruhiger sein.
    """
    info = rest_info(datas)
    ok = info["dif"] <= float(cfg["rest_max_dif"])
    return ok, info


def detect_contact(pres_datas, pres_times, step_mms, step_times, cfg):
    """Kontakt nach Live-Rechenweg: best_indx → step_bst_indx → zoft.

    BELEGT: CAL_TURN == best_indx, pres_bst == best, step_bst_time ==
    pres_bst_time, tri_time == 0.000, zoft = bst_pose - tri_pose
    (0.010-0.040), final = A + zoft.
    ANNAHME Filterdetails: hier Schwellwert auf Roh-/FTR-Daten
    (contact_thresh, min. rest_min_consec aufeinanderfolgende Samples),
    da exakte ned_*/CAL-Formel nur per Roh-Mitschnitt klärbar.
    Gibt dict(contact, best_indx, best_time, tri_time, step_bst_indx,
    tri_pose, bst_pose, zoft) zurück.
    """
    thresh = float(cfg["contact_thresh"])
    consec = max(1, int(cfg.get("rest_min_consec", 2)))
    datas = list(pres_datas)
    if len(datas) < 8:
        raise ValueError("zu wenig pres-Samples: %d" % len(datas))
    # Nullpunkt pro Fenster neu (BELEGT Drift: SRC-Offset -1.44Mio bis
    # -1.53Mio über Sitzung, pro Punkt neu kalibriert).
    base = median_int(datas[:32])
    devs = [abs(float(v) - base) for v in datas]
    best = None
    run = 0
    for i, d in enumerate(devs):
        if d > thresh:
            run += 1
            if run >= consec:
                best = i - consec + 1
                break
        else:
            run = 0
    if best is None:
        return {"contact": False, "baseline": base}
    best_time = float(pres_times[best]) - float(pres_times[-1])
    tri_time = 0.0
    # step_bst_indx: nächster Step-Index zu best_time (BELEGT Gleichheit
    # der Zeitbasen über Swap an allen 99 Punkten).
    bi = min(range(len(step_times)),
             key=lambda i: abs(float(step_times[i]) - float(best_time)))
    tri_pose = float(step_mms[-1])
    bst_pose = float(interp_linear(list(step_times), list(step_mms),
                                   float(best_time)))
    zoft = float(bst_pose - tri_pose)
    return {"contact": True, "baseline": base, "best_indx": int(best),
            "best_time": float(best_time), "tri_time": float(tri_time),
            "step_bst_indx": int(bi),
            "step_bst_time": float(step_times[bi]) - float(step_times[-1]),
            "tri_pose": float(tri_pose), "bst_pose": float(bst_pose),
            "zoft": float(zoft)}


def check_can_brk(best_indx, step_bst_indx, zoft, cfg):
    """can_brk-Plausibilität (ANNAHME aus genau 1 Beobachtung T58).

    T58 (einziges False in 99): best 53 (frühester), step 42 (frühester),
    zoft 0.040 (größter), final 0.088 deutlich über Nachbarschaft.
    Regel (UND, damit Randwerte nicht einzeln verwerfen):
      False genau wenn best<=53 UND step<=42 UND zoft>=0.040.
    Nachbarschafts-Vergleich (final deutlich über Umgebung) braucht
    Mesh-Kontext und läuft in probe_mesh (ANNAHME Schranke 0.05mm).
    """
    if (int(best_indx) <= int(cfg["can_brk_min_best"])
            and int(step_bst_indx) <= int(cfg["can_brk_min_step"])
            and float(zoft) >= float(cfg["can_brk_max_zoft"]) - 1e-12):
        return False, ("frühester Trigger (best=%d, step=%d) + größtes zoft "
                       "(%.3f) wie Ausreißer T58" % (best_indx,
                                                     step_bst_indx, zoft))
    return True, "plausibel"


def check_emergency(pres_datas, baseline0, cfg):
    """Notgrenze: max |roh - Nullpunkt| > emergency_thresh → True."""
    m = max(abs(float(v) - float(baseline0)) for v in pres_datas)
    return m > float(cfg["emergency_thresh"]), float(m)


# =====================================================================
# Gekapselte MCU-/Toolhead-Zugriffe (Logik testbar, vgl. k2_box.py)
# =====================================================================

class PrtouchMcuOps:
    """Protokoll pres-Seite (Düse) + step-Seite (Main)."""

    def test_swap(self):
        """TEST_SWAP-Äquivalent -> dict(py_ver, pres_ver, step_ver)."""
        raise NotImplementedError

    def start_sampling(self):
        raise NotImplementedError

    def read_pres(self):
        """-> (ticks_bytes, datas_bytes) je 64 Samples."""
        raise NotImplementedError

    def read_step(self):
        """-> (ticks_bytes, datas_bytes) je 64 Samples."""
        raise NotImplementedError

    def stop_sampling(self, ok=True):
        raise NotImplementedError

    def read_rest_pres(self):
        """Ruhefenster ohne Fahrt (für check_sensor_rest)."""
        return self.read_pres()


class ToolheadOps:
    """Protokoll Z-/XY-Bewegung + M400 + Temperatur (ANNAHME Upstream)."""

    def get_z(self):
        raise NotImplementedError

    def get_xy(self):
        raise NotImplementedError

    def move_down(self, step_mm, speed):
        raise NotImplementedError

    def move_to_xy(self, x, y, speed):
        raise NotImplementedError

    def lift(self, dz_mm, speed):
        raise NotImplementedError

    def wait_moves(self):
        """M400-Äquivalent nach JEDEM Schritt (S1)."""
        raise NotImplementedError

    def get_temps(self):
        """-> (nozzle_temp|None, bed_temp|None). Nur Warnung (S5)."""
        return (None, None)


class KlippyPresMcu(PrtouchMcuOps):
    """Echte pres-Anbindung (ANNAHMEN s. BERICHT §Annahmen).

    - oid via mcu.create_oid() in _build_config (vor connect).
    - config via mcu.add_config_cmd(CMD_CONFIG_PRES) wie Stock (is_init
      False, per Emulation des Stock-Moduls belegt 16.10.2026).
    - start/read/stop via mcu.lookup_query_command(cmd, resp, oid=...)
      nach klippy:connect (ANNAHME Query-Semantik wie Stock-Tupel-Slots).
    - Pins via ppins.lookup_pin/mcu.get_enumeration aufgelöst (wie
      KlippyGpio in k2_motor_init.py).
    """

    def __init__(self, printer, mcu, cfg, oid, pin_nums):
        self.printer = printer
        self.mcu = mcu
        self.cfg = cfg
        self.oid = int(oid)
        self.pin_nums = dict(pin_nums)
        self._q_start = self._q_read = self._q_stop = None

    def handle_connect(self):
        mcu = self.mcu
        self._q_start = mcu.lookup_query_command(
            CMD_START_PRES, RESP_ACK, oid=self.oid)
        self._q_read = mcu.lookup_query_command(
            CMD_READ_PRES, RESP_PRES, oid=self.oid)
        self._q_stop = mcu.lookup_query_command(
            CMD_STOP_PRES, RESP_ACK, oid=self.oid)

    def set_swap(self, level):
        """Swap-Leitung setzen: stop_prtouch_pres sta_swap=level (BELEGT per
        Emulation 16.10.: Stock-TEST_SWAP sendet sta_swap 0, dann 1;
        Antwort ack_prtouch expar1 = PRES_VER, live 71)."""
        r = self._q_stop.send([self.oid, 1 if level else 0])
        return {"err": int(r.get("err", 0)), "expar0": int(r.get("expar0", 0)),
                "expar1": int(r.get("expar1", 0))}

    def test_swap(self):
        # Echter Ablauf in K2PrtouchController.test_swap (set_swap/read_swap).
        return {"py_ver": HOST_PY_VER, "pres_ver": None, "step_ver": None,
                "note": "s. K2PrtouchController.test_swap"}

    def start_sampling(self):
        """start_prtouch_pres (Antast-Modus = Düse scharf) -> ack-Dict.
        Antwort ack_prtouch (BELEGT live 16.10.: err 0 bei Erfolg)."""
        args = build_start_pres_tuple(self.cfg, self.oid)
        return _ack_dict(self._q_start.send(list(args)))

    def disarm(self):
        """Düse entschärfen: stop_prtouch_pres sta_swap=1 (BELEGT
        Fingerdruck-Test 16.10.: setzt PA15/PC7 wieder auf 1). Wirft bei
        Kommunikationsfehler (Aufrufer entscheidet)."""
        return _ack_dict(self._q_stop.send([self.oid, 1]))

    def read_frozen(self):
        """Diagnose nach dem Entschärfen, Stock-Reihenfolge (BELEGT per
        Emulation 16.10.): read_prtouch_pres [oid, 2, 0, 0, 0] (Status,
        len 0), dann is_src=0 (FTR) und is_src=1 (SRC) je idx 0,8,…,56 mit
        len = 64 - idx (Antwort je Abfrage <= 8 Samples, gepackt)."""
        status = self._q_read.send([self.oid, 2, 0, 0, 0])
        out = {"status": {k: status.get(k) for k in
                          ("tri_chxs", "buf_len", "idx", "len")
                          if hasattr(status, "get")}}
        for name, is_src in (("ftr", 0), ("src", 1)):
            ts, ds = [], []
            for idx in range(0, PRES_N, READ_CHUNK):
                res = self._q_read.send([self.oid, is_src, 0, idx,
                                         PRES_N - idx])
                ts += decode_ticks(bytes(res["ticks"]), self.cfg)
                ds += decode_pres_datas(bytes(res["datas"]), self.cfg)
            out[name] = merge_ring(ts, ds, PRES_N)
        return out

    def read_pres(self):
        # BELEGT live 16.10.2026: is_src=1 = Rohwerte (Skala wie Stock-
        # READ_PRES, ~-1,428e6), is_src=0 = anderer (kleiner, gefilterter)
        # Kanal; ch=0; je Abfrage <= 8 Samples aus dem 64er-Ring (idx =
        # Ringposition) -> 8 Blöcke lesen, merge_ring. Rückgabe: Listen.
        ts, ds = [], []
        for idx in range(0, PRES_N, READ_CHUNK):
            res = self._q_read.send([self.oid, 1, 0, idx, READ_CHUNK])
            ts += decode_ticks(bytes(res["ticks"]), self.cfg)
            ds += decode_pres_datas(bytes(res["datas"]), self.cfg)
        return merge_ring(ts, ds, PRES_N)

    def stop_sampling(self, ok=True):
        # BELEGT 16.10.2026: sta_swap=1 = Leitung high (Ruhe, PC7=1);
        # sta_swap=0 zöge sie auf „ausgelöst“. Daher immer 1 (Stock-
        # probe_finish ebenso); ok bleibt nur als API-Parameter.
        _ = ok
        try:
            return self._q_stop.send([self.oid, 1])
        except Exception:
            return None


def _ack_dict(r):
    """ack_prtouch-Antwort -> dict(err, expar0, expar1) (fehlende = 0)."""
    get = r.get if hasattr(r, "get") else (lambda k, d=None: d)
    return {"err": int(get("err", 0) or 0),
            "expar0": int(get("expar0", 0) or 0),
            "expar1": int(get("expar1", 0) or 0)}


class KlippyStepMcu(PrtouchMcuOps):
    """Echte step-Anbindung (Main-MCU, ANNAHMEN wie pres)."""

    def __init__(self, printer, mcu, cfg, oid):
        self.printer = printer
        self.mcu = mcu
        self.cfg = cfg
        self.oid = int(oid)
        self._q_start = self._q_read = self._q_stop = self._q_cont = None

    def handle_connect(self):
        mcu = self.mcu
        # BELEGT (Emulation Stock-probe_prepare 16.10.2026): Antwort auf
        # cont_prtouch_step ist resault_prtouch_step_cnt (cnt_x/y/z).
        # ANNAHME: start/stop_prtouch_step antworten mit ack_prtouch.
        self._q_cont = mcu.lookup_query_command(
            CMD_CONT_STEP, RESP_STEP_CNT, oid=self.oid)
        self._q_start = mcu.lookup_query_command(
            CMD_START_STEP, RESP_ACK, oid=self.oid)
        self._q_read = mcu.lookup_query_command(
            CMD_READ_STEP, RESP_STEP, oid=self.oid)
        self._q_stop = mcu.lookup_query_command(
            CMD_STOP_STEP, RESP_ACK, oid=self.oid)

    def read_swap(self):
        """Swap-Pegel lesen: stop_prtouch_step (BELEGT per Emulation 16.10.:
        ack expar0 = gelesener Pegel, expar1 = STEP_VER, live 71)."""
        r = self._q_stop.send([self.oid])
        return {"err": int(r.get("err", 0)), "expar0": int(r.get("expar0", 0)),
                "expar1": int(r.get("expar1", 0))}

    def test_swap(self):
        return {"py_ver": HOST_PY_VER, "pres_ver": None, "step_ver": None,
                "note": "s. K2PrtouchController.test_swap"}

    def start_sampling(self):
        """start_prtouch_step [oid, aqc_tick] -> ack-Dict (ANNAHME Antwort
        ack_prtouch wie bei pres)."""
        return _ack_dict(self._q_start.send(
            list(build_start_step_tuple(self.cfg, self.oid))))

    def cont_step(self):
        """cont_prtouch_step (Stock-probe_prepare Schritt 1) -> Zähler."""
        r = self._q_cont.send([self.oid])
        get = r.get if hasattr(r, "get") else (lambda k, d=None: d)
        return {"cnt_x": get("cnt_x"), "cnt_y": get("cnt_y"),
                "cnt_z": get("cnt_z")}

    def disarm(self):
        """stop_prtouch_step (Stock-probe_finish Schritt 1)."""
        return _ack_dict(self._q_stop.send([self.oid]))

    def read_frozen(self):
        """Diagnose (Stock-Reihenfolge, BELEGT per Emulation): read_prtouch_
        step [oid, 0, 0], dann idx 0,8,…,56 mit len = 64 - idx."""
        self._q_read.send([self.oid, 0, 0])
        ts, ds = [], []
        for idx in range(0, STEP_N, READ_CHUNK):
            res = self._q_read.send([self.oid, idx, STEP_N - idx])
            ts += decode_ticks(bytes(res["ticks"]), self.cfg)
            ds += decode_step_counts(bytes(res["datas"]), self.cfg)
        return {"step": merge_ring(ts, ds, STEP_N)}

    def read_pres(self):
        raise NotImplementedError("step-MCU hat kein pres")

    def read_step(self):
        # ANNAHME (Main-MCU live noch nicht gelesen): wie pres – <= 8
        # Samples je Abfrage aus 64er-Ring, gleiche Packung.
        ts, ds = [], []
        for idx in range(0, STEP_N, READ_CHUNK):
            res = self._q_read.send([self.oid, idx, READ_CHUNK])
            ts += decode_ticks(bytes(res["ticks"]), self.cfg)
            ds += decode_step_counts(bytes(res["datas"]), self.cfg)
        return merge_ring(ts, ds, STEP_N)

    def stop_sampling(self, ok=True):
        try:
            return self._q_stop.send([self.oid])
        except Exception:
            return None


class KlippyToolhead(ToolheadOps):
    """Echte Toolhead-Anbindung (ANNAHME Upstream-API s. BERICHT).

    - manual_move(coord, speed) + wait_moves() + get_position().
      Fallback: gcode.run_script('G1 ...' + 'M400').
    - Temperaturen via extruder/heater_bed get_temp() (nur Warnung).
    """

    def __init__(self, printer):
        self.printer = printer

    def _th(self):
        return self.printer.lookup_object("toolhead")

    def get_z(self):
        try:
            return float(self._th().get_position()[2])
        except Exception:
            gcode = self.printer.lookup_object("gcode")
            return float(gcode.last_position[2])  # ANNAHME Fallback

    def get_xy(self):
        p = self._th().get_position()
        return float(p[0]), float(p[1])

    def _move(self, coord, speed):
        th = self._th()
        if hasattr(th, "manual_move"):
            th.manual_move(list(coord), float(speed))
            return
        # Fallback G-Code (ANNAHME):
        gcode = self.printer.lookup_object("gcode")
        gcode.run_script("G1 X%.3f Y%.3f Z%.4f F%.0f"
                         % (coord[0], coord[1], coord[2],
                            float(speed) * 60.0))

    def move_down(self, step_mm, speed):
        if float(step_mm) <= 0:
            raise ValueError("step_mm muss > 0 sein")
        p = list(self._th().get_position())
        p[2] = float(p[2]) - float(step_mm)
        self._move(p, speed)

    def move_to_xy(self, x, y, speed):
        p = list(self._th().get_position())
        self._move([float(x), float(y), float(p[2]), float(p[3])
                    if len(p) > 3 else 0.0][:3] if len(p) < 4 else
                    [float(x), float(y), float(p[2]), float(p[3])], speed)

    def lift(self, dz_mm, speed):
        p = list(self._th().get_position())
        p[2] = float(p[2]) + float(dz_mm)
        self._move(p, speed)

    def wait_moves(self):
        th = self._th()
        if hasattr(th, "wait_moves"):
            th.wait_moves()
            return
        self.printer.lookup_object("gcode").run_script("M400")

    def get_temps(self):
        nz, bd = None, None
        try:
            ex = self.printer.lookup_object("extruder")
            t, _tgt = ex.get_temp(time.time())
            nz = float(t)
        except Exception:
            pass
        try:
            hb = self.printer.lookup_object("heater_bed")
            t, _tgt = hb.get_temp(time.time())
            bd = float(t)
        except Exception:
            pass
        return nz, bd


# =====================================================================
# Controller – reine Antast-Logik (ohne Klipper importierbar)
# =====================================================================

class K2PrtouchError(RuntimeError):
    pass


class K2PrtouchController:
    """Schrittweise, host-seitig gestoppte Antastung (S1-S5).

    pres_mcu/step_mcu: PrtouchMcuOps; toolhead: ToolheadOps.
    """

    def __init__(self, cfg, pres_mcu, step_mcu, toolhead, pause=None,
                 logger=None):
        self.cfg = dict(cfg)
        self.pres = pres_mcu
        self.step = step_mcu
        self.tool = toolhead
        self.pause = pause if pause is not None else time.sleep
        self.log = logger if logger is not None else self._print_log()
        self.last_result = None
        self.waits = 0

    class _print_log:
        def info(self, msg):
            print(msg)

        def error(self, msg):
            print("FEHLER: %s" % msg)

    # --- Helfer -------------------------------------------------------
    def _require_move(self, name):
        if not self.cfg.get("allow_move"):
            raise PermissionError(
                "%s braucht allow_move: True (Default False → keine "
                "Z-Bewegung, nur Selbsttest/Lesen; DRY_RUN=1 für Plan)"
                % name)

    def describe_plan(self):
        c = self.cfg
        lines = ["K2-PrTouch-Plan (Trockenlauf, nichts bewegt/abgetastet)"]
        lines.append("allow_move=%s step_mm=%.3f (max %.2f) speed=%.1f "
                     "(max %.1f) max_travel=%.2f lift=%.2f timeout=%.0fs" % (
                         c["allow_move"], c["step_mm"], STEP_MM_MAX,
                         c["probe_speed"], SPEED_MAX, c["max_travel_mm"],
                         c["z_lift_mm"], c["probe_timeout_s"]))
        lines.append("pres: cfg_regs=%d acq_tick=%d ned=(%d,%d,%d/%d) "
                     "holds=(%d,%d,%d) lmt_dead=%d" % (
                         c["cfg_regs"], c["acq_tick"], c["ned_tftr"],
                         c["ned_hftr"], c["ned_lftr"],
                         ned_lftr_mcu_value(c), c["min_hold"],
                         c["max_hold"], c["add_hold"], c["lmt_dead"]))
        lines.append("step: aqc_tick=%d (Stock 46875 = 0,390625 ms)"
                     % c["aqc_tick"])
        lines.append("Pins: pres_swap=%s step_swap=%s clk=%s sdo=%s "
                     "(ANNAHME clk/sdo aus pres_cs0_pin)" % (
                         c["pres_swap_pin"], c["step_swap_pin"],
                         c["pres_clk_pin"], c["pres_sdo_pin"]))
        lines.append("Schwellen: contact=%.0f emergency=%.0f rest_dif=%.0f "
                     "(alle ANNAHME, s. BERICHT)" % (
                         c["contact_thresh"], c["emergency_thresh"],
                         c["rest_max_dif"]))
        lines.append("Ablauf pro Punkt: TEST_SWAP → Ruhetest → "
                     "start_pres/step → Schritte %.3fmm@%.1fmm/s + M400 + "
                     "read/detect → bei Kontakt Stopp + Lift → "
                     "Z = Pos + zoft → can_brk, ggf. 1× versetzt (%.2f,%.2f)"
                     % (c["step_mm"], c["probe_speed"], c["retry_dx"],
                        c["retry_dy"]))
        return "\n".join(lines)

    def test_swap(self):
        """TEST_SWAP-Äquivalent.

        BELEGT (Emulation des Stock-Moduls 16.10.2026, Stock live
        „Swap Test Success“): pres sta_swap=0 -> step lesen -> pres
        sta_swap=1 -> step lesen; Erfolg genau dann, wenn die Main-MCU
        erst 0, dann 1 meldet (expar0 der step-Antworten). PRES_VER/
        STEP_VER = expar1 der ersten Antworten (live 71/71).
        """
        if hasattr(self.pres, "set_swap") and hasattr(self.step, "read_swap"):
            try:
                p0 = self.pres.set_swap(0)
                s0 = self.step.read_swap()
                p1 = self.pres.set_swap(1)
                s1 = self.step.read_swap()
            except Exception as exc:
                raise K2PrtouchError("Swap-Test: MCU-Abfrage fehlgeschlagen: %s"
                                     % exc)
            ok = int(s0["expar0"]) == 0 and int(s1["expar0"]) == 1
            info = {"py_ver": HOST_PY_VER, "pres_ver": p0["expar1"],
                    "step_ver": s0["expar1"], "levels": (s0["expar0"],
                                                         s1["expar0"]),
                    "swap_ok": ok}
            if not ok:
                raise K2PrtouchError(
                    "PR_ERR_CODE_SWAP_PIN_DETECTI: The synchronization pin "
                    "test failed, pres_swap_pin=%s, step_swap_pin=%s "
                    "(gelesen %s statt (0, 1))."
                    % (self.cfg["pres_swap_pin"], self.cfg["step_swap_pin"],
                       info["levels"]))
            return {"pres": info, "step": info}
        try:
            p = self.pres.test_swap()
        except Exception as exc:
            raise K2PrtouchError("Swap-Test pres-Seite fehlgeschlagen: %s"
                                 % exc)
        try:
            s = self.step.test_swap()
        except Exception as exc:
            raise K2PrtouchError("Swap-Test step-Seite fehlgeschlagen: %s"
                                 % exc)
        # ANNAHME Prüfung: beide MCU-Seiten müssen antworten; exakte
        # Versionsnummern (live 78/71/71) nur geloggt, kein harter Match
        # (FW-Updates!). Fail nur bei Ausnahme/false.
        for info, name in ((p, "pres"), (s, "step")):
            if isinstance(info, dict) and info.get("swap_ok") is False:
                raise K2PrtouchError(
                    "PR_ERR_CODE_SWAP_PIN_DETECTI: The synchronization pin "
                    "test failed, pres_swap_pin=%s, step_swap_pin=%s."
                    % (self.cfg["pres_swap_pin"],
                       self.cfg["step_swap_pin"]))
        return {"pres": p, "step": s}

    def check_sensor_rest(self):
        """Ruhetest vor Bewegung (S4). Gibt (ok, info) oder wirft."""
        t_b, d_b = self.pres.read_rest_pres()
        datas = decode_pres_datas(d_b, self.cfg)
        ok, info = check_rest_ok(datas, self.cfg)
        if not ok:
            raise K2PrtouchError(
                "Sensor-Ruhetest fehlgeschlagen (analog PRES_CHECK): "
                "dif=%d > max %d (min=%d max=%d avg=%.0f len=%d) – "
                "keine Bewegung." % (info["dif"], self.cfg["rest_max_dif"],
                                     info["min"], info["max"], info["avg"],
                                     info["len"]))
        return info, datas

    def check_temps(self):
        """Temperatur-Empfehlung (S5): nur Warnungen, nie Abbruch/Heizen."""
        try:
            nz, bd = self.tool.get_temps()
        except Exception:
            return ["Temperaturen nicht lesbar (ANNAHME API) – weiter ohne "
                    "Prüfung (nur Empfehlung 140/50)."]
        warns = []
        tol = float(self.cfg["temp_tolerance"])
        if nz is not None and abs(nz - float(self.cfg["temp_nozzle_warn"])) > tol:
            warns.append("Düse %.0f°C weicht von Stock-Empfehlung %.0f°C ab "
                         "(±%.0f) – Messung ggf. ungenau, nicht geheizt."
                         % (nz, self.cfg["temp_nozzle_warn"], tol))
        if bd is not None and abs(bd - float(self.cfg["temp_bed_warn"])) > tol:
            warns.append("Bett %.0f°C weicht von Stock-Empfehlung %.0f°C ab "
                         "(±%.0f) – Messung ggf. ungenau, nicht geheizt."
                         % (bd, self.cfg["temp_bed_warn"], tol))
        return warns

    # --- Hauptablauf ---------------------------------------------------
    def probe_point(self, x=None, y=None, dry_run=False, _is_retry=False):
        """Eine Antastung. Gibt dict(status, z, zoft, details...).

        status: ok / dry_run / swap_error / rest_error / emergency /
                max_travel / timeout / no_contact / temp_warn_ok.
        Bei ok: z = A + zoft (A = Z bei Kontakt/tiefster Punkt).
        """
        c = self.cfg
        t0 = time.monotonic()
        warns = self.check_temps()
        for w in warns:
            try:
                self.log.info("[k2_prtouch] Warnung: %s" % w)
            except Exception:
                pass
        # S4: Swap + Ruhe VOR jeder Bewegung.
        try:
            swap = self.test_swap()
        except K2PrtouchError as exc:
            return {"status": "swap_error", "error": str(exc),
                    "warnings": warns}
        except Exception as exc:
            return {"status": "swap_error",
                    "error": "Swap-Test: %s" % exc, "warnings": warns}
        try:
            rest_info_d, rest_datas = self.check_sensor_rest()
            baseline0 = median_int(rest_datas)
        except K2PrtouchError as exc:
            return {"status": "rest_error", "error": str(exc),
                    "warnings": warns}
        except Exception as exc:
            return {"status": "rest_error",
                    "error": "Ruhetest: %s" % exc, "warnings": warns}

        if dry_run or not c.get("allow_move"):
            return {"status": "dry_run", "warnings": warns, "swap": swap,
                    "rest": rest_info_d, "baseline": baseline0,
                    "plan": self.describe_plan()}

        step_mm = float(c["step_mm"])
        speed = float(c["probe_speed"])
        # Harte Config-Grenzen (S1) doppelt prüfen (fail-safe):
        if step_mm <= 0 or step_mm > STEP_MM_MAX + 1e-12:
            return {"status": "config_error",
                    "error": "step_mm %.4f verletzt Maximum %.2f (S1)"
                             % (step_mm, STEP_MM_MAX), "warnings": warns}
        if speed <= 0 or speed > SPEED_MAX + 1e-12:
            return {"status": "config_error",
                    "error": "probe_speed %.2f verletzt Maximum %.1f (S1)"
                             % (speed, SPEED_MAX), "warnings": warns}

        # XY-Positionierung (mit Freigabe, S3):
        try:
            self._require_move("Positionierung")
            if x is not None and y is not None:
                self.tool.move_to_xy(float(x), float(y), 100.0)
                self.tool.wait_moves()
                self.waits += 1
        except PermissionError as exc:
            return {"status": "denied", "error": str(exc),
                    "warnings": warns}
        except Exception as exc:
            return {"status": "move_error", "error": "XY: %s" % exc,
                    "warnings": warns}

        # Sampling starten (Stock-Parameter):
        try:
            self.pres.start_sampling()
            self.step.start_sampling()
        except Exception as exc:
            return {"status": "start_error",
                    "error": "start_prtouch_*: %s" % exc, "warnings": warns}

        total = 0.0
        steps = 0
        try:
            while True:
                if total + step_mm > float(c["max_travel_mm"]) + 1e-9:
                    self._abort_lift("max_travel %.2fmm überschritten "
                                     "(%d Schritte)" % (c["max_travel_mm"],
                                                        steps))
                    try:
                        self.pres.stop_sampling(ok=False)
                    except Exception:
                        pass
                    try:
                        self.step.stop_sampling(ok=False)
                    except Exception:
                        pass
                    return {"status": "max_travel",
                            "error": "kein Kontakt innerhalb max_travel_mm="
                                     "%.2f (S2)" % c["max_travel_mm"],
                            "steps": steps, "travel_mm": total,
                            "warnings": warns}
                if time.monotonic() - t0 > float(c["probe_timeout_s"]):
                    self._abort_lift("Timeout %.0fs" % c["probe_timeout_s"])
                    for m in (self.pres, self.step):
                        try:
                            m.stop_sampling(ok=False)
                        except Exception:
                            pass
                    return {"status": "timeout",
                            "error": "Timeout %.0fs (S2)"
                                     % c["probe_timeout_s"],
                            "steps": steps, "travel_mm": total,
                            "warnings": warns}
                # S1: kurzer Schritt + M400:
                self.tool.move_down(step_mm, speed)
                self.tool.wait_moves()
                self.waits += 1
                total += step_mm
                steps += 1

                pt_b, pd_b = self.pres.read_pres()
                st_b, sd_b = self.step.read_step()
                pt = ticks_to_times(decode_ticks(pt_b, c),
                                    c["clock_freq"])
                pd = decode_pres_datas(pd_b, c)
                st = ticks_to_times(decode_ticks(st_b, c), c["clock_freq"])
                sd_counts = decode_step_counts(sd_b, c)
                sd_mm = step_counts_to_mm(sd_counts, c, relative=True)

                # S2: Notgrenze (Roh-Abweichung vom Nullpunkt):
                is_em, mag = check_emergency(pd, baseline0, c)
                if is_em:
                    self._abort_lift("Notgrenze %.0f überschritten "
                                     "(|dev|=%.0f)" % (c["emergency_thresh"],
                                                       mag))
                    for m in (self.pres, self.step):
                        try:
                            m.stop_sampling(ok=False)
                        except Exception:
                            pass
                    return {"status": "emergency",
                            "error": "Kraft-Notgrenze: Abweichung %.0f > %.0f "
                                     "(S2) – abgebrochen + angehoben."
                                     % (mag, c["emergency_thresh"]),
                            "steps": steps, "travel_mm": total,
                            "warnings": warns}

                det = detect_contact(pd, pt, sd_mm, st, c)
                if det.get("contact"):
                    A = float(self.tool.get_z())
                    zoft = float(det["zoft"])
                    final = float(A + zoft)
                    can, reason = check_can_brk(det["best_indx"],
                                                det["step_bst_indx"],
                                                zoft, c)
                    # S1/S2: SOFORT stoppen + anheben:
                    try:
                        self.pres.stop_sampling(ok=True)
                    except Exception:
                        pass
                    try:
                        self.step.stop_sampling(ok=True)
                    except Exception:
                        pass
                    try:
                        self.tool.lift(float(c["z_lift_mm"]),
                                       float(c["lift_speed"]))
                        self.tool.wait_moves()
                        self.waits += 1
                    except Exception as exc:
                        return {"status": "lift_error",
                                "error": "Anheben nach Kontakt: %s" % exc,
                                "z": final, "zoft": zoft,
                                "warnings": warns}
                    res = {"status": "ok", "z": final, "A": A,
                           "zoft": zoft, "best_indx": det["best_indx"],
                           "best_time": det["best_time"],
                           "step_bst_indx": det["step_bst_indx"],
                           "tri_pose": det["tri_pose"],
                           "bst_pose": det["bst_pose"],
                           "can_brk": bool(can), "can_reason": reason,
                           "steps": steps, "travel_mm": total,
                           "waits": self.waits, "warnings": warns,
                           "swap": swap, "rest": rest_info_d}
                    self.last_result = dict(res)
                    # can_brk False → genau EINMAL versetzt wiederholen
                    # (BELEGT T58→T59: +0.25/-0.25mm, 1mm hoch):
                    if not can and not _is_retry:
                        try:
                            self.tool.lift(float(c["retry_lift_mm"]),
                                           float(c["lift_speed"]))
                            self.tool.wait_moves()
                        except Exception:
                            pass
                        rx = (float(x) + float(c["retry_dx"])
                              if x is not None else None)
                        ry = (float(y) + float(c["retry_dy"])
                              if y is not None else None)
                        second = self.probe_point(rx, ry,
                                                  dry_run=False,
                                                  _is_retry=True)
                        second["first_try"] = {k: res[k] for k in
                                               ("z", "zoft", "best_indx",
                                                "step_bst_indx")
                                               if k in res}
                        second["retried"] = True
                        second["retry_dx"] = c["retry_dx"]
                        second["retry_dy"] = c["retry_dy"]
                        return second
                    return res
                # sonst: nächster kurzer Schritt (Überfahrweg wächst nur
                # um step_mm; Test (g) prüft Abbruch ≤1 Schritt nach Kontakt,
                # da Fake-Spike sofort im nächsten Fenster erscheint).
        except PermissionError as exc:
            try:
                self._abort_lift("deny")
            except Exception:
                pass
            return {"status": "denied", "error": str(exc),
                    "warnings": warns}
        except K2PrtouchError as exc:
            try:
                self._abort_lift("error")
            except Exception:
                pass
            return {"status": "error", "error": str(exc),
                    "warnings": warns}
        except Exception as exc:
            try:
                self._abort_lift("exception")
            except Exception:
                pass
            for m in (self.pres, self.step):
                try:
                    m.stop_sampling(ok=False)
                except Exception:
                    pass
            return {"status": "error", "error": "unerwartet: %s" % exc,
                    "warnings": warns}
        # Kein Kontakt im Hub → bereits oben als max_travel behandelt;
        # Falls Schleife je verlassen wird ohne return (sollte nicht):
        try:
            self._abort_lift("no_contact")
        except Exception:
            pass
        return {"status": "no_contact", "error": "kein Kontakt im Hub",
                "steps": steps, "travel_mm": total, "warnings": warns}

    def _abort_lift(self, why):
        """Bei jedem Fehler: Fahrt beenden + Z anheben (S2)."""
        _ = why
        try:
            self.tool.lift(float(self.cfg["z_lift_mm"]),
                           float(self.cfg["lift_speed"]))
            self.tool.wait_moves()
            self.waits += 1
        except Exception as exc:
            raise K2PrtouchError("Anheben nach Abbruch fehlgeschlagen: %s"
                                 % exc)


# =====================================================================
# Probe-Hauptweg (mode: probe) – reine Logik, ohne Klipper testbar
# =====================================================================
#
# Mechanismus (BELEGT 16.10.2026, PRTOUCH-LESETEST-BEFUND §3/§6 +
# Emulation Stock-probe_prepare/probe_finish):
#   Düse scharf (start_prtouch_pres, Antast-Parameter) -> Kontakt zieht
#   PA15 (Düse) und damit PC7 (Main) auf 0 -> normaler Upstream-
#   MCU_endstop auf !PC7 mit trsync stoppt Z IN DER MCU -> danach
#   stop_prtouch_pres sta_swap=1 (PC7 wieder 1).
# Stock-Reihenfolge, hier übernommen:
#   prepare: cont_prtouch_step -> start_prtouch_pres -> start_prtouch_step
#   Fahrt:   endstop_home (Upstream HomingMove/probing_move)
#   finish:  stop_prtouch_step -> stop_prtouch_pres sta_swap=1
#            (-> optional Diagnose: eingefrorenen Ring lesen)
# Zusätzlich (eigene Sicherheit, nicht Stock): Signalweg-Test über den
# Endstop, Ruhetest nach dem Scharfschalten, PC7-Prüfung vor der Fahrt,
# allow_move-Sperre, Geschwindigkeitsgrenze 5 mm/s, Wegbegrenzung außerhalb
# von G28, Entschärfen bei JEDER Ausnahme (try/finally).
#
# ANNAHMEN (offline bzw. gegen Upstream NICHT prüfbar, vor erster echter
# Fahrt am Gerät klären):
#   P1 Stopp unter Bewegung: PA15->PC7->endstop_home/trsync reagiert schnell
#      genug (Upstream-Abtastung 4x15 µs, homing.py:9-10); nur im Stand per
#      Fingerdruck belegt, nicht während einer Fahrt.
#   P2 arm_settle_s 0,2 s reicht, damit die Düsen-MCU ihren Ruhewert
#      übernimmt (Stock-Wartezeit nicht bekannt).
#   P3 Ruhetest (read_prtouch_pres) ist auch bei zusätzlich gestartetem
#      start_prtouch_step zulässig (live belegt nur ohne step-Seite, Test D).
#   P4 Signalweg-Test vor jeder Fahrt (sta_swap 0/1 ohne laufende Messung)
#      stört nichts – entspricht Stock-TEST_SWAP, dort aber ohne Endstop.
#   P5 start/stop_prtouch_step antworten mit ack_prtouch (err 0 = ok).
#   P6 Main-MCU-Endstop auf PC7 parallel zu config_prtouch_step swp_pin=PC7
#      (wie Stock, BELEGT per Emulation) – auf Upstream ohne Pin-Konflikt,
#      da config_prtouch_step den Pin nur als Namen im Config-Text trägt.

def compute_probe_target_z(cur_z, z_min, max_travel, is_homing):
    """Zielhöhe der Abwärtsfahrt.

    G28 (Homing via probe:z_virtual_endstop): Upstream-Verhalten, bis
    position_min (die Ausgangshöhe ist dort unbekannt/erzwungen).
    Sonst (PROBE, BED_MESH_CALIBRATE, …): höchstens max_travel unter der
    aktuellen Höhe, nie unter position_min.
    """
    if is_homing:
        return float(z_min)
    return max(float(z_min), float(cur_z) - float(max_travel))


class K2PrtouchProbeCore:
    """Antast-Sitzung für den Upstream-Probe-Weg (probe.py-Session-API).

    Abhängigkeiten (alle injiziert, Fakes in den Tests):
      get_params(gcmd) -> dict wie Upstream ProbeParameterHelper
      descend: descend_until_trigger(gcmd, params),
               pull_trigger_positions(), clear_trigger_positions()
      query_triggered() -> bool (Endstop step_swap_pin, True = ausgelöst)
      wait_moves(), pause(sekunden)
      pres/step (attach() nach klippy:connect): set_swap, start_sampling,
               read_pres, disarm, [read_frozen] bzw. cont_step,
               start_sampling, disarm, [read_frozen]
      error: Fehlerklasse (Klipper: printer.command_error – andere
             Ausnahmen würden in Klipper einen Shutdown auslösen).
    """

    def __init__(self, cfg, get_params, descend, query_triggered,
                 wait_moves, pause, error=K2PrtouchError, logger=None):
        self.cfg = dict(cfg)
        self.get_params = get_params
        self.descend = descend
        self.query_triggered = query_triggered
        self.wait_moves = wait_moves
        self.pause = pause
        self.error = error
        self.log = logger
        self.pres = self.step = None
        self.armed = False
        self.arm_count = 0
        self.disarm_count = 0
        self.last = None

    def attach(self, pres, step):
        self.pres, self.step = pres, step

    def _info(self, msg):
        try:
            if self.log is not None:
                if hasattr(self.log, "respond_info"):
                    self.log.respond_info(msg)
                else:
                    self.log.info(msg)
        except Exception:
            pass

    def _wrap(self, what, fn, *args):
        """MCU-Aufruf; fremde Ausnahmen -> self.error (kein Shutdown)."""
        try:
            return fn(*args)
        except Exception as exc:
            if isinstance(exc, self.error):
                raise
            raise self.error("[k2_prtouch] %s fehlgeschlagen: %s"
                             % (what, exc))

    # --- Freigabe / Grenzen -------------------------------------------
    def check_allowed(self, gcmd):
        """allow_move + Geschwindigkeitsgrenze; wirft VOR jeder Bewegung."""
        if not self.cfg.get("allow_move"):
            raise self.error(
                "[k2_prtouch] Antasten/Homing gesperrt: allow_move: False "
                "(Default). Erst K2_PRTOUCH_TEST_SWAP/K2_PRTOUCH_READ "
                "prüfen, dann allow_move: True setzen. Keine Bewegung.")
        params = self.get_params(gcmd)
        sp = float(params.get("probe_speed", 0.0))
        if not 0.0 < sp <= SPEED_MAX + 1e-12:
            raise self.error(
                "[k2_prtouch] Antastgeschwindigkeit %.2f mm/s unzulässig "
                "(hart <= %.1f mm/s). Bei G28: homing_speed und "
                "second_homing_speed in [stepper_z] <= %.1f setzen. "
                "Keine Bewegung." % (sp, SPEED_MAX, SPEED_MAX))
        return params

    # --- Scharf schalten / entschärfen ----------------------------------
    def arm(self):
        """Düse scharf schalten (mit Vorabprüfungen). Wirft self.error.

        Entschärft NICHT selbst – run_probe räumt per finally auf.
        """
        if self.pres is None or self.step is None:
            raise self.error("[k2_prtouch] noch nicht verbunden "
                             "(klippy:connect abwarten) – keine Bewegung.")
        c = self.cfg
        info = {}
        if c.get("swap_check", True):
            # Signalweg PA15 -> PC7 -> Endstop prüfen (wie Stock-TEST_SWAP,
            # aber direkt über den Stopp-Endstop): sta_swap=0 muss den
            # Endstop auslösen, sta_swap=1 muss ihn freigeben.
            a0 = self._wrap("stop_prtouch_pres sta_swap=0",
                            self.pres.set_swap, 0)
            t0 = bool(self._wrap("Endstop-Abfrage", self.query_triggered))
            a1 = self._wrap("stop_prtouch_pres sta_swap=1",
                            self.pres.set_swap, 1)
            t1 = bool(self._wrap("Endstop-Abfrage", self.query_triggered))
            info["swap"] = {"triggered": (t0, t1),
                            "err": (int(a0.get("err", 0)),
                                    int(a1.get("err", 0))),
                            "pres_ver": a0.get("expar1")}
            if not (t0 and not t1):
                raise self.error(
                    "[k2_prtouch] Signalweg-Test fehlgeschlagen: Endstop "
                    "%s meldete ausgelöst=%s bei sta_swap=0 und %s bei "
                    "sta_swap=1 (erwartet True/False). Verkabelung, "
                    "step_swap_pin-Invertierung (!PC7) oder pres_swap_pin "
                    "prüfen – keine Bewegung."
                    % (c.get("step_swap_pin"), t0, t1))
        # Stock-probe_prepare: cont_step -> start_pres -> start_step.
        info["cont"] = self._wrap("cont_prtouch_step", self.step.cont_step)
        ack = self._wrap("start_prtouch_pres", self.pres.start_sampling)
        info["ack_pres"] = ack
        if int(ack.get("err", 0)) != 0:
            raise self.error(
                "[k2_prtouch] Düse ließ sich nicht scharf schalten: "
                "start_prtouch_pres ack err=%d (expar0=%s expar1=%s) – "
                "keine Bewegung." % (int(ack.get("err", 0)),
                                     ack.get("expar0"), ack.get("expar1")))
        ack2 = self._wrap("start_prtouch_step", self.step.start_sampling)
        info["ack_step"] = ack2
        if int(ack2.get("err", 0)) != 0:
            raise self.error(
                "[k2_prtouch] start_prtouch_step ack err=%d – keine "
                "Bewegung." % int(ack2.get("err", 0)))
        self.armed = True
        self.arm_count += 1
        settle = float(c.get("arm_settle_s", ARM_SETTLE_DEFAULT_S))
        if settle > 0:
            self.pause(settle)
        if c.get("rest_check", True):
            _t, d = self._wrap("read_prtouch_pres (Ruhetest)",
                               self.pres.read_pres)
            datas = decode_pres_datas(d, c)
            ok, ri = check_rest_ok(datas, c)
            info["rest"] = ri
            if not ok:
                raise self.error(
                    "[k2_prtouch] Sensor-Ruhetest nach dem Scharfschalten "
                    "fehlgeschlagen: dif=%d > %d (len=%d) – keine Bewegung."
                    % (ri["dif"], c["rest_max_dif"], ri["len"]))
        if bool(self._wrap("Endstop-Abfrage", self.query_triggered)):
            raise self.error(
                "[k2_prtouch] Düse meldet bereits Kontakt (Endstop %s "
                "ausgelöst) VOR der Fahrt – keine Bewegung. Düse frei? "
                "Restkraft/Filament an der Düse?" % (c.get("step_swap_pin"),))
        return info

    def disarm(self):
        """Stock-probe_finish: stop_prtouch_step, dann stop_prtouch_pres
        sta_swap=1. Versucht IMMER beide; gibt Fehlerliste zurück."""
        errs = []
        for name, obj in (("stop_prtouch_step", self.step),
                          ("stop_prtouch_pres sta_swap=1", self.pres)):
            if obj is None:
                continue
            try:
                obj.disarm()
            except Exception as exc:
                errs.append("%s: %s" % (name, exc))
        self.armed = False
        self.disarm_count += 1
        return errs

    def _diag(self):
        out = {}
        for name, obj in (("pres", self.pres), ("step", self.step)):
            if obj is None or not hasattr(obj, "read_frozen"):
                continue
            try:
                r = obj.read_frozen()
                out[name] = {k: (len(v[1]) if isinstance(v, tuple) else v)
                             for k, v in r.items()}
            except Exception as exc:
                out[name + "_error"] = str(exc)
        return out

    # --- Upstream-Session-API (vgl. probe.py ProbeEndstopWrapper) ------
    def start_probe_session(self, gcmd):
        self.check_allowed(gcmd)
        self.descend.clear_trigger_positions()
        return self

    def run_probe(self, gcmd):
        params = self.check_allowed(gcmd)
        # Düse in Ruhe scharf schalten (Schwelle relativ zum Ruhewert beim
        # Scharfschalten, BELEGT Fingerdruck-Test §6):
        self.wait_moves()
        last = {"status": "arming"}
        self.last = last
        pending = None
        try:
            last["arm"] = self.arm()
            last["status"] = "armed"
            self.descend.descend_until_trigger(gcmd, params)
            last["status"] = "ok"
        except BaseException as exc:
            pending = exc
            last["status"] = "error"
            last["error"] = str(exc)
            raise
        finally:
            errs = self.disarm()
            if errs:
                last["disarm_errors"] = errs
            else:
                try:
                    if self.query_triggered():
                        last["warn"] = ("Endstop nach dem Entschärfen noch "
                                        "ausgelöst")
                except Exception:
                    pass
            if self.cfg.get("diag_readout"):
                last["diag"] = self._diag()
            if errs and pending is None:
                raise self.error("[k2_prtouch] Entschärfen fehlgeschlagen: "
                                 "%s" % "; ".join(errs))

    def pull_probed_results(self):
        return self.descend.pull_trigger_positions()

    def end_probe_session(self):
        self.descend.clear_trigger_positions()
        if self.armed:  # sollte nie vorkommen – trotzdem sicher entschärfen
            self.disarm()

    def get_status(self):
        return {"armed": self.armed, "arms": self.arm_count,
                "disarms": self.disarm_count, "last": self.last}


# =====================================================================
# Klipper-Hülle [k2_prtouch] (ANNAHMEN zur Upstream-API, s. BERICHT)
# =====================================================================

def _resolve_pin_number(printer, mcu_name, pin_raw):
    """Pin-Name -> Nummer (alt, seit 16.10.2026 NICHT mehr genutzt: die
    Hülle schickt Pin-NAMEN wie Stock, Upstream löst sie selbst auf)."""
    try:
        ppins = printer.lookup_object("ppins")
        if hasattr(ppins, "lookup_pin"):
            res = ppins.lookup_pin(pin_raw)
            if isinstance(res, (list, tuple)) and len(res) == 2:
                return int(res[1])
            if isinstance(res, int):
                return int(res)
    except Exception:
        pass
    try:
        mcu = printer.lookup_object(mcu_name)
        if hasattr(mcu, "get_enumeration"):
            return int(mcu.get_enumeration("pin",
                                           _clean_pin_name(pin_raw)))
        if hasattr(mcu, "get_enumerations"):
            return pin_number_from_enum(
                dict(mcu.get_enumerations()).get("pin", {}), pin_raw)
    except Exception:
        pass
    raise ValueError("Pin-Nummer für %r nicht auflösbar" % (pin_raw,))


def _upstream(name):
    """Upstream-Modul aus klippy/extras laden (im Paket relativ, in den
    Tests über einen 'extras'-Paketpfad). ANNAHME: Datei liegt als
    klippy/extras/k2_prtouch.py (wie jedes Zusatzmodul)."""
    import importlib
    if __package__:
        try:
            return importlib.import_module("." + name, __package__)
        except ImportError:
            pass
    return importlib.import_module("extras." + name)


def _lookup_mcu(printer, name):
    """MCU-Objekt nach Config-Namen. Upstream registriert '[mcu]' als 'mcu'
    und '[mcu nozzle_mcu]' als 'mcu nozzle_mcu' (mcu.py:1250-1260,
    get_printer_mcu). Fallback: Name wörtlich."""
    name = str(name).strip()
    if name != "mcu" and not name.startswith("mcu "):
        try:
            return printer.lookup_object("mcu " + name)
        except Exception:
            pass
    return printer.lookup_object(name)


class K2ProbeOffsets:
    """Wie Upstream ProbeOffsetsHelper (probe.py:410), aber z_offset mit
    Default 0 (Stock: Düse = Taster, keine XY-Versätze)."""

    def __init__(self, cfg):
        self.x_offset = 0.0
        self.y_offset = 0.0
        self.z_offset = float(cfg.get("z_offset", 0.0))

    def get_offsets(self, gcmd=None):
        return self.x_offset, self.y_offset, self.z_offset


class K2DescendHelper:
    """Abwärtsfahrt bis Endstop – nachgebaut nach Upstream
    DescendToEndstopHelper (probe.py:246-275), zusätzlich mit Wegbegrenzung
    außerhalb von G28 (compute_probe_target_z) und erneuter
    Geschwindigkeitsprüfung."""

    def __init__(self, config, mcu_probe, probe_offsets, cfg, probe_mod,
                 manual_probe_mod):
        self.printer = config.get_printer()
        self.mcu_probe = mcu_probe
        self.probe_offsets = probe_offsets
        self.cfg = cfg
        self.manual_probe = manual_probe_mod
        self.z_min_position = probe_mod.lookup_minimum_z(config)
        self.results = []
        # Z-Stepper an den Endstop hängen (trsync stoppt sie in der MCU):
        probe_mod.LookupZSteppers(config, self.mcu_probe.add_stepper)

    def descend_until_trigger(self, gcmd, params):
        toolhead = self.printer.lookup_object("toolhead")
        pos = toolhead.get_position()
        speed = float(params["probe_speed"])
        if not 0.0 < speed <= SPEED_MAX + 1e-12:
            raise self.printer.command_error(
                "[k2_prtouch] probe_speed %.2f > %.1f mm/s" % (speed,
                                                               SPEED_MAX))
        is_homing = gcmd.get_command() == "G28"
        pos[2] = compute_probe_target_z(
            pos[2], self.z_min_position, self.cfg["probe_max_travel_mm"],
            is_homing)
        phoming = self.printer.lookup_object("homing")
        check_movement = not phoming.check_probe_first_home(gcmd)
        ppos = phoming.probing_move(self.mcu_probe, pos, speed,
                                    check_movement=check_movement)
        offsets = self.probe_offsets.get_offsets()
        self.results.append(self.manual_probe.create_probe_result(ppos,
                                                                  offsets))

    def pull_trigger_positions(self):
        res = self.results
        self.results = []
        return res

    def clear_trigger_positions(self):
        self.pull_trigger_positions()


class K2PrtouchEndstopWrapper:
    """Endstop + Probe-Session (vgl. Upstream ProbeEndstopWrapper
    probe.py:545 / BLTouchProbe bltouch.py:26): MCU_endstop-Schnittstelle
    für HomingMove (get_mcu/add_stepper/get_steppers/home_start/home_wait/
    query_endstop, mcu.py:345-411) + Session-API an K2PrtouchProbeCore."""

    def __init__(self, mcu_endstop, core):
        self.mcu_endstop = mcu_endstop
        self.core = core
        self.get_mcu = mcu_endstop.get_mcu
        self.add_stepper = mcu_endstop.add_stepper
        self.get_steppers = mcu_endstop.get_steppers
        self.home_start = mcu_endstop.home_start
        self.home_wait = mcu_endstop.home_wait
        self.query_endstop = mcu_endstop.query_endstop

    def start_probe_session(self, gcmd):
        self.core.start_probe_session(gcmd)
        return self

    def run_probe(self, gcmd):
        self.core.run_probe(gcmd)

    def pull_probed_results(self):
        return self.core.pull_probed_results()

    def end_probe_session(self):
        self.core.end_probe_session()


class K2PrtouchPrinterProbe:
    """Das 'probe'-Objekt (vgl. Upstream PrinterProbe probe.py:608 und
    PrinterBLTouch bltouch.py:281): PROBE, QUERY_PROBE, PROBE_ACCURACY,
    PROBE_CALIBRATE, Z_OFFSET_APPLY_PROBE, probe:z_virtual_endstop sowie
    alle ProbePointsHelper-Nutzer (BED_MESH_CALIBRATE, Z_TILT_ADJUST,
    SCREWS_TILT_CALCULATE …)."""

    def __init__(self, config, cfg):
        probe_mod = _upstream("probe")
        manual_probe_mod = _upstream("manual_probe")
        self.printer = printer = config.get_printer()
        self.reactor = printer.get_reactor()
        self.cfg = cfg
        self.probe_offsets = K2ProbeOffsets(cfg)
        self.param_helper = probe_mod.ProbeParameterHelper(config)
        # Upstream liest 'speed' (Default 5.0); unser cfg['speed'] ist
        # identisch bzw. fällt auf probe_speed zurück und ist hart <= 5.
        self.param_helper.speed = float(cfg["speed"])
        ppins = printer.lookup_object("pins")
        self.mcu_endstop = ppins.setup_pin("endstop", cfg["step_swap_pin"])
        gcode = printer.lookup_object("gcode")
        self.core = K2PrtouchProbeCore(
            cfg, self.param_helper.get_probe_params, None,
            self._query_triggered, self._wait_moves, self._pause,
            error=printer.command_error, logger=gcode)
        self.mcu_probe = K2PrtouchEndstopWrapper(self.mcu_endstop, self.core)
        self.core.descend = K2DescendHelper(
            config, self.mcu_probe, self.probe_offsets, cfg, probe_mod,
            manual_probe_mod)
        self.probe_session = probe_mod.SampleAveragingHelper(
            config, self.param_helper, self.mcu_probe.start_probe_session)
        self.cmd_helper = probe_mod.ProbeCommandHelper(
            config, self, self.mcu_probe.query_endstop)
        probe_mod.HomingViaProbeHelper(config, float(cfg["position_endstop"]),
                                       self.mcu_probe.query_endstop)
        printer.add_object("probe", self)

    # Hilfen für den Kern (Klipper-APIs: mcu.py:1234, toolhead.py:422)
    def _query_triggered(self):
        mcu = self.mcu_endstop.get_mcu()
        pt = mcu.estimated_print_time(self.reactor.monotonic())
        return bool(self.mcu_endstop.query_endstop(pt))

    def _wait_moves(self):
        self.printer.lookup_object("toolhead").wait_moves()

    def _pause(self, seconds):
        self.reactor.pause(self.reactor.monotonic() + float(seconds))

    # Upstream-Probe-Schnittstelle (probe.py:621-628)
    def get_probe_params(self, gcmd=None):
        return self.param_helper.get_probe_params(gcmd)

    def get_offsets(self, gcmd=None):
        return self.probe_offsets.get_offsets(gcmd)

    def get_status(self, eventtime):
        st = self.cmd_helper.get_status(eventtime)
        st["k2_prtouch"] = self.core.get_status()
        return st

    def start_probe_session(self, gcmd):
        return self.probe_session.start_probe_session(gcmd)


class PrinterK2Prtouch:
    """Klipper-Objekt [k2_prtouch]."""

    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        try:
            self.cfg = parse_klipper_config(config)
        except Exception as exc:
            raise config.error("[k2_prtouch] Config-Fehler: %s" % exc)
        self.pres_oid = self.step_oid = None
        self.pres_mcu = self.step_mcu = None
        self.controller = None
        self.probe = None
        self._pin_nums = {}
        # MCU-Objekte (Upstream-Namen 'mcu' / 'mcu nozzle_mcu'):
        try:
            self.mcu_pres = _lookup_mcu(self.printer, self.cfg["nozzle_mcu"])
            self.mcu_step = _lookup_mcu(self.printer, self.cfg["main_mcu"])
        except Exception as exc:
            raise config.error("[k2_prtouch] MCU nicht gefunden "
                               "(nozzle_mcu/main_mcu): %s" % exc)
        # Upstream: register_config_callback vor connect (mcu.py:1131).
        self.mcu_pres.register_config_callback(self._build_pres_config)
        self.mcu_step.register_config_callback(self._build_step_config)
        self.printer.register_event_handler("klippy:connect",
                                            self.handle_connect)
        # Hauptweg: Upstream-Probe auf step_swap_pin (Default !PC7).
        if self.cfg["mode"] == "probe":
            try:
                self.probe = K2PrtouchPrinterProbe(config, self.cfg)
            except config.error:
                raise
            except Exception as exc:
                raise config.error("[k2_prtouch] Probe-Einrichtung: %s"
                                   % exc)
        cmds = [("K2_PRTOUCH_PROBE_STEPS", self.cmd_probe,
                 "Rückfall: zeitgesteuerte Schrittfahrt [X=.. Y=.. "
                 "DRY_RUN=1]"),
                ("K2_PRTOUCH_TEST_SWAP", self.cmd_swap,
                 "Sync-/Versionscheck ohne Bewegung"),
                ("K2_PRTOUCH_READ", self.cmd_read,
                 "Sensor lesen ohne Bewegung"),
                ("K2_PRTOUCH_STATUS", self.cmd_status, "Letzter Status")]
        if self.cfg["mode"] == "steps":
            # Nur im Rückfallmodus: eigene Ersatzbefehle für G28 Z / Mesh.
            cmds += [("K2_PRTOUCH_HOME_Z", self.cmd_home_z,
                      "Rückfall: Z-Homing Bettmitte [DRY_RUN=1]"),
                     ("K2_PRTOUCH_MESH", self.cmd_mesh,
                      "Rückfall: Raster-Mesh [COUNT=.. DRY_RUN=1]")]
        for cmd, fn, desc in cmds:
            self.gcode.register_command(cmd, fn, desc=desc)

    # --- Config-Phase (vor connect) ---
    def _build_pres_config(self):
        mcu = self.mcu_pres
        self.pres_oid = mcu.create_oid()
        # Pins als NAMEN wie Stock (BELEGT wörtlich: swp_pin=PA15
        # clk_pin=PB13 sdo_pin=PB14); Upstream löst '[ _]pin=NAME' über den
        # PinResolver der MCU auf (pins.py:16/41, mcu.py:1022-1025) und
        # msgproto kodiert *_pin über die 'pin'-Enumeration
        # (msgproto.py:125-135). Kein ppins.lookup_pin -> kein Konflikt mit
        # dem Endstop auf PC7 (Stock nutzt PC7 ebenso doppelt).
        swp = _clean_pin_name(self.cfg["pres_swap_pin"])
        clk = _clean_pin_name(self.cfg["pres_clk_pin"])
        sdo = _clean_pin_name(self.cfg["pres_sdo_pin"])
        self._pin_nums.update({"pres_swp": swp, "clk": clk, "sdo": sdo})
        mcu.add_config_cmd(
            "config_prtouch_pres oid=%d idx=%d swp_pin=%s clk_pin=%s "
            "sdo_pin=%s" % (self.pres_oid, int(self.cfg["pres_idx"]),
                            swp, clk, sdo))  # is_init False wie Stock

    def _build_step_config(self):
        mcu = self.mcu_step
        self.step_oid = mcu.create_oid()
        swp = _clean_pin_name(self.cfg["step_swap_pin"])
        self._pin_nums["step_swp"] = swp
        ox, oy, oz = self._stepper_oids()
        self._pin_nums.update({"ox": ox, "oy": oy, "oz": oz})
        mcu.add_config_cmd(
            "config_prtouch_step oid=%d oid_xstp=%d oid_ystp=%d "
            "oid_zstp=%d swp_pin=%s" % (self.step_oid, ox, oy, oz, swp))

    def _stepper_oids(self):
        c = self.cfg
        if (c["stepper_x_oid"] is not None
                and c["stepper_y_oid"] is not None
                and c["stepper_z_oid"] is not None):
            return (int(c["stepper_x_oid"]), int(c["stepper_y_oid"]),
                    int(c["stepper_z_oid"]))
        # Upstream: Stepper sind KEINE eigenen Printer-Objekte, sondern
        # über toolhead.get_kinematics().get_steppers() erreichbar
        # (probe.py:203-206); MCU_stepper.get_oid() (stepper.py:132), OID
        # schon im Konstruktor vergeben (stepper.py:33).
        # BELEGT (Emulation 16.10.): oid_zstp = LETZTER Z-Stepper (stepper_z1).
        steppers = {}
        try:
            kin = self.printer.lookup_object("toolhead").get_kinematics()
            for st in kin.get_steppers():
                steppers[st.get_name()] = st
        except Exception:
            pass
        names = ["stepper_x", "stepper_y",
                 "stepper_z1" if "stepper_z1" in steppers else "stepper_z"]
        out = []
        for name in names:
            st = steppers.get(name)
            if st is None:
                try:
                    st = self.printer.lookup_object(name)
                except Exception as exc:
                    raise RuntimeError(
                        "Stepper %r nicht gefunden – notfalls stepper_*_oid "
                        "in [k2_prtouch] setzen: %s" % (name, exc))
            for attr in ("get_oid", "_oid", "oid"):
                try:
                    v = getattr(st, attr)
                    out.append(int(v() if callable(v) else v))
                    break
                except Exception:
                    continue
            else:
                raise RuntimeError(
                    "Stepper-OID für %r nicht auflösbar – notfalls "
                    "stepper_*_oid manuell in [k2_prtouch] setzen." % name)
        return tuple(out)

    def handle_connect(self):
        pres = KlippyPresMcu(self.printer, self.mcu_pres, self.cfg,
                             self.pres_oid, self._pin_nums)
        step = KlippyStepMcu(self.printer, self.mcu_step, self.cfg,
                             self.step_oid)
        try:
            pres.handle_connect()
            step.handle_connect()
        except Exception as exc:
            # Ohne Query-Lookup bleibt der Kern ungebunden -> jede Probe-/
            # Homing-Fahrt bricht VOR der Bewegung mit klarer Meldung ab.
            try:
                self.gcode.respond_info("[k2_prtouch] Query-Lookup: %s" % exc)
            except Exception:
                pass
            return
        self.pres_mcu, self.step_mcu = pres, step
        if self.probe is not None:
            self.probe.core.attach(pres, step)
        self.controller = K2PrtouchController(
            self.cfg, pres, step, KlippyToolhead(self.printer),
            pause=getattr(self.reactor, "pause", time.sleep),
            logger=self.gcode)

    def _ctl(self):
        if self.controller is None:
            raise RuntimeError("noch nicht connected (klippy:connect "
                               "abwarten)")
        return self.controller

    def get_status(self, eventtime=None):
        _ = eventtime
        base = {"mode": self.cfg.get("mode"),
                "allow_move": bool(self.cfg.get("allow_move")),
                "step_mm": self.cfg.get("step_mm"),
                "last": self.controller.last_result
                if self.controller else None,
                "probe": self.probe.core.get_status()
                if self.probe is not None else None}
        return base

    # --- G-Code-Helfer ---
    @staticmethod
    def _dry(gcmd):
        try:
            return bool(gcmd.get_int("DRY_RUN", 0))
        except Exception:
            return False

    def cmd_probe(self, gcmd):
        try:
            ctl = self._ctl()
        except Exception as exc:
            gcmd.respond_info("[k2_prtouch] %s" % exc)
            return
        x = y = None
        try:
            x = gcmd.get_float("X", None)
        except Exception:
            pass
        try:
            y = gcmd.get_float("Y", None)
        except Exception:
            pass
        dry = self._dry(gcmd)
        if dry or not self.cfg.get("allow_move"):
            res = ctl.probe_point(x, y, dry_run=True)
            gcmd.respond_info("[k2_prtouch] Trockenlauf (keine Bewegung): "
                              "%s" % (res.get("plan", res),))
            if not dry:
                gcmd.respond_info("[k2_prtouch] ALLOW_WRITE-Hinweis: "
                                  "allow_move=False → DRY_RUN.")
            return
        res = ctl.probe_point(x, y, dry_run=False)
        if res.get("status") == "ok":
            gcmd.respond_info("[k2_prtouch] probe at %.3f,%.3f is z=%.4f "
                              "(A=%.4f zoft=%.4f best=%d step=%d can_brk=%s)"
                              % (x or 0.0, y or 0.0, res["z"], res["A"],
                                 res["zoft"], res["best_indx"],
                                 res["step_bst_indx"], res["can_brk"]))
            if self.cfg.get("shutdown_on_failure") and not res["can_brk"]:
                raise gcmd.error("[k2_prtouch] can_brk False") \
                    if hasattr(gcmd, "error") else RuntimeError("can_brk")
        else:
            msg = "[k2_prtouch] FEHLER (%s): %s" % (res.get("status"),
                                                    res.get("error"))
            gcmd.respond_info(msg)
            if self.cfg.get("shutdown_on_failure"):
                raise gcmd.error(msg) if hasattr(gcmd, "error") \
                    else RuntimeError(msg)

    def cmd_home_z(self, gcmd):
        """Z-Homing Bettmitte + SET_KINEMATIC_POSITION (ANNAHME API)."""
        try:
            ctl = self._ctl()
        except Exception as exc:
            gcmd.respond_info("[k2_prtouch] %s" % exc)
            return
        dry = self._dry(gcmd)
        # Bettmitte aus [bed_mesh] mesh_min/max (ANNAHME Config-Zugriff):
        cx = cy = 175.0
        try:
            bm = self.printer.lookup_object("bed_mesh")
            if hasattr(bm, "get_mesh_limits"):
                (x0, y0), (x1, y1) = bm.get_mesh_limits()
                cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        except Exception:
            pass
        res = ctl.probe_point(cx, cy, dry_run=dry or
                              not self.cfg.get("allow_move"))
        if res.get("status") == "dry_run":
            gcmd.respond_info("[k2_prtouch] HOME_Z Trockenlauf: %s"
                              % res.get("plan", ""))
            return
        if res.get("status") != "ok":
            gcmd.respond_info("[k2_prtouch] HOME_Z FEHLER: %s"
                              % res.get("error"))
            return
        # ANNAHME: SET_KINEMATIC_POSITION setzt Z ohne Fahrt (Upstream).
        try:
            self.gcode.run_script("SET_KINEMATIC_POSITION Z=%.4f" % res["z"])
            gcmd.respond_info("[k2_prtouch] HOME_Z ok: Z=%.4f" % res["z"])
        except Exception as exc:
            gcmd.respond_info("[k2_prtouch] SET_KINEMATIC_POSITION: %s "
                              "(ANNAHME – homing_override s. BERICHT)" % exc)

    def cmd_mesh(self, gcmd):
        """Eigenes Raster-Mesh (Fallback, ANNAHME bed_mesh-API)."""
        try:
            ctl = self._ctl()
        except Exception as exc:
            gcmd.respond_info("[k2_prtouch] %s" % exc)
            return
        dry = self._dry(gcmd)
        try:
            count = gcmd.get_int("COUNT", 3)
        except Exception:
            count = 3
        count = max(2, min(9, int(count)))
        # Raster aus [bed_mesh] oder Default 5..345 (ANNAHME):
        x0, y0, x1, y1 = 5.0, 5.0, 345.0, 345.0
        try:
            bm = self.printer.lookup_object("bed_mesh")
            if hasattr(bm, "get_mesh_limits"):
                (x0, y0), (x1, y1) = bm.get_mesh_limits()
        except Exception:
            pass
        if dry or not self.cfg.get("allow_move"):
            gcmd.respond_info("[k2_prtouch] MESH Trockenlauf %dx%d "
                              "(keine Bewegung)." % (count, count))
            return
        pts = []
        for iy in range(count):
            for ix in range(count):
                x = x0 + (x1 - x0) * ix / max(1, count - 1)
                y = y0 + (y1 - y0) * iy / max(1, count - 1)
                r = ctl.probe_point(x, y)
                if r.get("status") != "ok":
                    gcmd.respond_info("[k2_prtouch] MESH Abbruch bei "
                                      "(%.1f,%.1f): %s" % (x, y, r))
                    return
                pts.append((x, y, r["z"]))
        gcmd.respond_info("[k2_prtouch] MESH %dx%d ok, Spanne %.3f..%.3f "
                          "(ANNAHME: Übergabe an bed_mesh s. BERICHT)."
                          % (count, count, min(p[2] for p in pts),
                             max(p[2] for p in pts)))

    def cmd_swap(self, gcmd):
        try:
            ctl = self._ctl()
        except Exception as exc:
            gcmd.respond_info("[k2_prtouch] %s" % exc)
            return
        try:
            gcmd.respond_info("[k2_prtouch] TEST_SWAP: %s"
                              % (ctl.test_swap(),))
        except Exception as exc:
            gcmd.respond_info("[k2_prtouch] SWAP_FEHLER: %s" % exc)

    def cmd_read(self, gcmd):
        try:
            ctl = self._ctl()
        except Exception as exc:
            gcmd.respond_info("[k2_prtouch] %s" % exc)
            return
        try:
            info, _ = ctl.check_sensor_rest()
            gcmd.respond_info("[k2_prtouch] READ ok: %s" % (info,))
        except Exception as exc:
            gcmd.respond_info("[k2_prtouch] READ: %s" % exc)

    def cmd_status(self, gcmd):
        st = self.get_status()
        gcmd.respond_info("[k2_prtouch] %s" % (st,))


def load_config(config):
    """Klipper-Einstieg [k2_prtouch] (Upstream-Muster)."""
    return PrinterK2Prtouch(config)
