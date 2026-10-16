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

Sicherheit (verbindlich, im Code durchgesetzt + getestet):
  S1 MCU stoppt nicht selbst → Abwärtsfahrt NUR in kurzen Schritten
     (step_mm Default 0.02, Maximum 0.05) mit niedriger Geschwindigkeit
     (Default 5mm/s, Maximum 5mm/s), nach JEDEM Schritt wait_moves
     (M400-Äquivalent), dann lesen/auswerten, bei Kontakt SOFORT stoppen
     und anheben.
  S2 Harte Grenzen: max_travel_mm pro Antastung (Default 5mm Stock-Hub),
     Kraft-Notgrenze (Roh-Abweichung vom Nullpunkt > emergency_thresh →
     sofort Abbruch + Fehler), Timeout; bei jedem Fehler: Sampling stoppen,
     Z anheben (z_lift_mm), klare Meldung, kein Weiterprobe (außer genau
     einmaliger can_brk-Wiederholung versetzt).
  S3 Bewegung nur mit allow_move: True (Default False → nur
     Selbsttest/TEST_SWAP/Sensor lesen, keine Z-Bewegung);
     G-Code K2_PRTOUCH_PROBE [DRY_RUN=1].
  S4 Vor jeder Antastung TEST_SWAP-Äquivalent + Sensor-Ruhetest
     (Rauschen/Drift, analog PRES_CHECK); sonst Abbruch VOR Bewegung.
  S5 Temperaturen (Düse 140/Bett 50) nur als Empfehlung prüfen (Warnung),
     nicht selbst heizen.

G-Codes (PrinterK2Prtouch):
- K2_PRTOUCH_PROBE [X=.. Y=.. DRY_RUN=1] – Einzelantastung.
- K2_PRTOUCH_HOME_Z [DRY_RUN=1] – Bettmitte antasten + SET_KINEMATIC_POSITION
  (homing_override-Vorschlag s. BERICHT).
- K2_PRTOUCH_MESH [COUNT=.. DRY_RUN=1] – eigenes Raster (Fallback, falls
  Upstream-Probe-API zu unsicher); erzeugt bed_mesh-kompatible Matrix.
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
# STEP_DT aus STEP_TIME-Fenster 0.0244s/64 ≈ 0.38ms (BELEGT Fenster,
# ANNAHME Faktor; SPEZIFIKATION §2.2: aqc_tick ≈ 45700).
STEP_DT_S = 0.0244 / 64.0

# Stock-Live-Werte (BELEGT liveconfig/printer-live.cfg/Mitschnitt):
STOCK_CFG_REGS = 60  # 1280/128 per Kommentar printer-live.cfg:343
STOCK_ACQ_TICK = int(round(PRES_DT_S * CLOCK_FREQ))  # 93750
STOCK_AQC_TICK = 45700  # ANNAHME Spanne 45600-46000 (§2.2)
STOCK_NED = (5, 1, 0.8)  # pres_tri_fter (BELEGT Werte, ANNAHME Mapping)
STOCK_HOLDS = (4000, 10000, 500)  # pres_tri_hold (BELEGT Werte)
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
Z_LIFT_DEFAULT = 2.0  # wie sample_retract_dist 2.0
TIMEOUT_DEFAULT_S = 30.0  # wie z_align timeout 30s

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

    # --- Stock-Sampling-Parameter ---
    cfg["cfg_regs"] = _int("cfg_regs", STOCK_CFG_REGS)
    cfg["acq_tick"] = _int("acq_tick", STOCK_ACQ_TICK)
    cfg["aqc_tick"] = _int("aqc_tick", STOCK_AQC_TICK)
    cfg["ned_tftr"] = _int("ned_tftr", STOCK_NED[0])
    cfg["ned_hftr"] = _int("ned_hftr", STOCK_NED[1])
    # ANNAHME ned_lftr: live 0.8 (float) vs Dict %u. Skalierung
    # konfigurierbar: MCU-Wert = round(ned_lftr * ned_lftr_scale).
    # Default scale 10 → 8. Exakter Faktor nur per Roh-Mitschnitt klärbar.
    cfg["ned_lftr"] = _float("ned_lftr", STOCK_NED[2])
    cfg["ned_lftr_scale"] = _int("ned_lftr_scale", 10)
    cfg["min_hold"] = _int("min_hold", STOCK_HOLDS[0])
    cfg["max_hold"] = _int("max_hold", STOCK_HOLDS[1])
    cfg["add_hold"] = _int("add_hold", STOCK_HOLDS[2])
    # ANNAHME lmt_dead: Herkunft offen (§2.1). Kandidat prth_err_dead 0.2
    # vs %u → Default 200 (= 0.2*1000). Vor Gerätetest per Debug prüfen.
    cfg["lmt_dead"] = _int("lmt_dead", 200)
    if cfg["ned_lftr_scale"] <= 0:
        raise ValueError("ned_lftr_scale muss > 0 sein")

    # --- Datenformat (ANNAHME, zentral konfigurierbar) ---
    # BELEGT: ticks/datas sind Byte-Arrays (%.*s), 64 Samples, little/
    # signed/median-Strings in unzip_data. Byte-Breite statisch nicht
    # entscheidbar → Arbeitshypothese 4B u32-LE / 4B s32-LE (MCU-clock).
    cfg["tick_format"] = _str("tick_format", "<I") or "<I"
    cfg["data_format"] = _str("data_format", "<i") or "<i"
    cfg["tick_size"] = _int("tick_size", 4)
    cfg["data_size"] = _int("data_size", 4)
    cfg["clock_freq"] = _float("clock_freq", CLOCK_FREQ)
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
              "tick_format", "data_format", "tick_size", "data_size",
              "clock_freq", "step_dist_mm", "retry_dx", "retry_dy",
              "retry_lift_mm", "temp_nozzle_warn", "temp_bed_warn",
              "temp_tolerance", "shutdown_on_failure"):
        if k not in opts:
            v = _g(k, None)
            if v is not None and str(v) != "":
                opts[k] = v
    return parse_options(opts)


def ned_lftr_mcu_value(cfg):
    """ned_lftr (float, z.B. 0.8) -> MCU-%u-Wert (ANNAHME Skalierung)."""
    return int(round(float(cfg["ned_lftr"]) * int(cfg["ned_lftr_scale"])))


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


def decode_ticks(raw, cfg):
    """ticks-Bytes -> u32-Liste (ANNAHME)."""
    return decode_int_list(raw, cfg["tick_format"], cfg["tick_size"],
                           count=PRES_N if len(bytes(raw)) // int(cfg["tick_size"]) == PRES_N else None)


def decode_pres_datas(raw, cfg):
    """datas-Bytes (pres) -> s32-Liste (ANNAHME)."""
    return decode_int_list(raw, cfg["data_format"], cfg["data_size"])


def decode_step_counts(raw, cfg):
    """datas-Bytes (step) -> s32-Zählerliste (ANNAHME)."""
    return decode_int_list(raw, cfg["data_format"], cfg["data_size"])


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
    - config via mcu.add_config_cmd(CMD_CONFIG_PRES, is_init=True).
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

    def test_swap(self):
        # ANNAHME: TEST_SWAP als get_status-Abfrage beider OIDs; hier
        # via ack-Prüfung (err==0) + Versions-Event. Exakte
        # PY/PRES/STEP_VER-Register nur per Roh-Mitschnitt beweisbar.
        return {"py_ver": HOST_PY_VER, "pres_ver": None, "step_ver": None,
                "note": "ANNAHME: Swap als Sync-Basis, kein Triggertest"}

    def start_sampling(self):
        args = build_start_pres_tuple(self.cfg, self.oid)
        return self._q_start.send(list(args))

    def read_pres(self):
        # ANNAHME is_src/ch/idx/len: ch=0 (alle Logs nur CH0), len=64,
        # is_src=1 (FTR, gefiltert für Trigger), idx=0 (Ringoffset 0).
        res = self._q_read.send([self.oid, 1, 0, 0, PRES_N])
        return bytes(res["ticks"]), bytes(res["datas"])

    def stop_sampling(self, ok=True):
        try:
            return self._q_stop.send([self.oid, 1 if ok else 0])
        except Exception:
            return None


class KlippyStepMcu(PrtouchMcuOps):
    """Echte step-Anbindung (Main-MCU, ANNAHMEN wie pres)."""

    def __init__(self, printer, mcu, cfg, oid):
        self.printer = printer
        self.mcu = mcu
        self.cfg = cfg
        self.oid = int(oid)
        self._q_start = self._q_read = self._q_stop = None

    def handle_connect(self):
        mcu = self.mcu
        self._q_start = mcu.lookup_query_command(
            CMD_START_STEP, RESP_ACK, oid=self.oid)
        self._q_read = mcu.lookup_query_command(
            CMD_READ_STEP, RESP_STEP, oid=self.oid)
        self._q_stop = mcu.lookup_query_command(
            CMD_STOP_STEP, RESP_ACK, oid=self.oid)

    def test_swap(self):
        return {"py_ver": HOST_PY_VER, "pres_ver": None, "step_ver": None,
                "note": "ANNAHME s. KlippyPresMcu"}

    def start_sampling(self):
        return self._q_start.send(list(build_start_step_tuple(self.cfg,
                                                              self.oid)))

    def read_pres(self):
        raise NotImplementedError("step-MCU hat kein pres")

    def read_step(self):
        res = self._q_read.send([self.oid, 0, STEP_N])
        return bytes(res["ticks"]), bytes(res["datas"])

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
        lines.append("pres: cfg_regs=%d acq_tick=%d ned=(%d,%d,%d->%d) "
                     "holds=(%d,%d,%d) lmt_dead=%d" % (
                         c["cfg_regs"], c["acq_tick"], c["ned_tftr"],
                         c["ned_hftr"], c["ned_lftr"],
                         ned_lftr_mcu_value(c), c["min_hold"],
                         c["max_hold"], c["add_hold"], c["lmt_dead"]))
        lines.append("step: aqc_tick=%d (ANNAHME ~45700)" % c["aqc_tick"])
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
        """TEST_SWAP-Äquivalent (BELEGT Versionen PY/PRES/STEP)."""
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
                st = ticks_to_times(
                    decode_int_list(st_b, c["tick_format"], c["tick_size"]),
                    c["clock_freq"])
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
# Klipper-Hülle [k2_prtouch] (ANNAHMEN zur Upstream-API, s. BERICHT)
# =====================================================================

def _resolve_pin_number(printer, mcu_name, pin_raw):
    """Pin-Name -> Nummer (wie KlippyGpio, ANNAHME API-Varianten)."""
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
        self._pin_nums = {}
        # MCU-Objekte (Namen aus Config):
        try:
            self.mcu_pres = self.printer.lookup_object(
                self.cfg["nozzle_mcu"])
            self.mcu_step = self.printer.lookup_object(
                self.cfg["main_mcu"])
        except Exception as exc:
            raise config.error("[k2_prtouch] MCU nicht gefunden "
                               "(nozzle_mcu/main_mcu): %s" % exc)
        # ANNAHME Upstream: register_config_callback vor connect.
        try:
            self.mcu_pres.register_config_callback(self._build_pres_config)
            self.mcu_step.register_config_callback(self._build_step_config)
        except Exception as exc:
            raise config.error("[k2_prtouch] register_config_callback: %s "
                               "(ANNAHME Upstream-API)" % exc)
        self.printer.register_event_handler("klippy:connect",
                                            self.handle_connect)
        for cmd, fn, desc in (
                ("K2_PRTOUCH_PROBE", self.cmd_probe,
                 "Einzelantastung [X=.. Y=.. DRY_RUN=1]"),
                ("K2_PRTOUCH_HOME_Z", self.cmd_home_z,
                 "Z-Homing Bettmitte [DRY_RUN=1]"),
                ("K2_PRTOUCH_MESH", self.cmd_mesh,
                 "Raster-Mesh [COUNT=.. DRY_RUN=1]"),
                ("K2_PRTOUCH_TEST_SWAP", self.cmd_swap,
                 "Sync-/Versionscheck ohne Bewegung"),
                ("K2_PRTOUCH_READ", self.cmd_read,
                 "Sensor lesen ohne Bewegung"),
                ("K2_PRTOUCH_STATUS", self.cmd_status,
                 "Letzter Status")):
            try:
                self.gcode.register_command(cmd, fn, desc=desc)
            except Exception:
                pass

    # --- Config-Phase (vor connect) ---
    def _build_pres_config(self):
        mcu = self.mcu_pres
        self.pres_oid = mcu.create_oid()
        swp = _resolve_pin_number(self.printer, self.cfg["nozzle_mcu"],
                                  self.cfg["pres_swap_pin"])
        clk = _resolve_pin_number(self.printer, self.cfg["nozzle_mcu"],
                                  self.cfg["pres_clk_pin"])
        sdo = _resolve_pin_number(self.printer, self.cfg["nozzle_mcu"],
                                  self.cfg["pres_sdo_pin"])
        self._pin_nums.update({"pres_swp": swp, "clk": clk, "sdo": sdo})
        # ANNAHME add_config_cmd-Signatur (Upstream-Muster, is_init).
        mcu.add_config_cmd(
            "config_prtouch_pres oid=%d idx=%d swp_pin=%d clk_pin=%d "
            "sdo_pin=%d" % (self.pres_oid, int(self.cfg["pres_idx"]),
                            swp, clk, sdo), is_init=True)

    def _build_step_config(self):
        mcu = self.mcu_step
        self.step_oid = mcu.create_oid()
        swp = _resolve_pin_number(self.printer, self.cfg["main_mcu"],
                                  self.cfg["step_swap_pin"])
        self._pin_nums["step_swp"] = swp
        # Stepper-OIDs: ANNAHME Auto-Lookup, sonst manuelle Config.
        ox, oy, oz = self._stepper_oids()
        self._pin_nums.update({"ox": ox, "oy": oy, "oz": oz})
        mcu.add_config_cmd(
            "config_prtouch_step oid=%d oid_xstp=%d oid_ystp=%d "
            "oid_zstp=%d swp_pin=%d" % (self.step_oid, ox, oy, oz, swp),
            is_init=True)

    def _stepper_oids(self):
        c = self.cfg
        if (c["stepper_x_oid"] is not None
                and c["stepper_y_oid"] is not None
                and c["stepper_z_oid"] is not None):
            return (int(c["stepper_x_oid"]), int(c["stepper_y_oid"]),
                    int(c["stepper_z_oid"]))
        # ANNAHME: Stepper-Objekte tragen ihre MCU-OID (je nach
        # Upstream-Version get_oid()/_oid/oid). Vor Gerätetest prüfen!
        out = []
        for name in ("stepper_x", "stepper_y", "stepper_z"):
            try:
                st = self.printer.lookup_object(name)
            except Exception as exc:
                raise RuntimeError("Stepper %r nicht gefunden (ANNAHME "
                                   "Upstream-Name): %s" % (name, exc))
            for attr in ("get_oid", "_oid", "oid"):
                try:
                    v = getattr(st, attr)
                    out.append(int(v() if callable(v) else v))
                    break
                except Exception:
                    continue
            else:
                raise RuntimeError(
                    "Stepper-OID für %r nicht auflösbar (ANNAHME "
                    "get_oid/_oid) – notfalls stepper_*_oid manuell in "
                    "[k2_prtouch] setzen." % name)
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
            try:
                self.gcode.respond_info("[k2_prtouch] Query-Lookup: %s "
                                        "(ANNAHME lookup_query_command)" % exc)
            except Exception:
                pass
            return
        self.pres_mcu, self.step_mcu = pres, step
        self.controller = K2PrtouchController(
            self.cfg, pres, step, KlippyToolhead(self.printer),
            pause=getattr(self.reactor, "pause", time.sleep),
            logger=self.gcode)

    def _ctl(self):
        if self.controller is None:
            # Noch nicht connected (z.B. direkt nach Restart): Controller
            # mit Klippy-Adaptern ohne Query-Cache ist trotzdem für
            # DRY_RUN/Swap brauchbar? Nein – klar melden.
            raise RuntimeError("noch nicht connected (klippy:connect "
                               "abwarten)")
        return self.controller

    def get_status(self, eventtime=None):
        _ = eventtime
        base = {"allow_move": bool(self.cfg.get("allow_move")),
                "step_mm": self.cfg.get("step_mm"),
                "last": self.controller.last_result
                if self.controller else None}
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
