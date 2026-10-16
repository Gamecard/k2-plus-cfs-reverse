#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2_motor_init.py – Klipper-Zusatzmodul [k2_motor] für K2-Plus-Motoren.

Offener Ersatz für Crealitys ``motor_control_wrapper.so`` (Stock, armhf,
proprietär, mit Bugs). Initialisiert die Closed-loop-Motoren X/Y/Z/Z1/E auf
einem Raspberry-Pi-Host unter UPSTREAM-Klipper – so wie Stock, aber ohne
dessen Bugs und mit sicherem Default (nur lesen).

Grundlagen (nur gelesen, nichts gesendet):
- MOTOR-INIT-BEFUND-2026-10-16.md (maßgeblich: Init-Ablauf P0–P9 aus
  ``set_motor_pin`` @0xf87d8, Zeiten, Fehler-Keys 798–803, Param-Abgleich
  ``verify_sys_param`` @0xc163c mit isclose abs_tol 1e-6, Strapping
  ``Adresse = 0x81 + 2·STEP + DIR``)
- GPIO-PHASE-BEFUND-2026-10-16.md (MCU-Befehle: ``set_digital_out``,
  ``config_digital_out``/``update_digital_out``, ``query_gpio_status``,
  ``config_transparent``/``transparent_send``/``transparent_response``,
  ``[duplicate_pin_override]``)
- nachbau/motor_init.py (Plan/Trockenlauf/compare – LOGIK-VORLAGE, hier als
  eigenständiger Port wiederverwendet statt neu erfunden; gleiche Phasen,
  gleiche Reihenfolge/Payloads, gleiche Pausen/Timeouts/Versuche)
- nachbau/motor.py (Frame-Builder/Decoder, Param-Tabelle 227 Einträge)
- nachbau/serial485.py (build_frame, parse_frame, CRC-8/ATM)
- Live-Log live-logs-2026-10-15/klippy.log (Boot ab Zeile 2259; transparent-
  Frames inkl. F7+CRC; RS485-Wire ohne Head/CRC; Extruder antwortet über die
  Düsen-MCU unter 0x81)

Ablauf (wie Stock, P0–P9):
  P0 transparent-Kanal zur Düsen-MCU (``config_transparent`` + Lookup
     ``transparent_send`` – erst nach dem Connect möglich)
  P1 GPIO-Strapping: Dir/Step-Pegel laut Config über die Main-MCU setzen
     (VOR dem Broadcast!), optional per ``query_gpio_status`` zurücklesen
     (wenn Befehl vorhanden), 0,2 s, sonst key799-analog
  P2 Adress-Runde: ``FF 0E [01]`` auf RS485 + transparent (keine Antwort
     erwartet), 0,1 s, je Motor ``0E [02]`` (je 0,05 s davor, E transparent
     0x81); bis ``retries`` Runden (Default 4), 1,0 s Pause; sonst
     key798-analog (Shutdown NUR per Option)
  P3 „All motors are normal“
  P4 Stall-Mode ``11 [02]`` an Z/Z1 (RS485) + E (transparent), je ≤3
     Versuche (key801/802-analog); X/Y erst beim Homing (wie Stock)
  P5 Schutzabfrage ``0C [0b]`` an alle (E transparent), 8 B dekodiert
  P6 Fehler löschen ``0C [05]`` an alle, UNBEDINGT (live nie beantwortet)
  P7 GPIO-Restore: alle Dir/Step physisch 0, 0,1 s, zurücklesen
     (key800-analog), 1,0 s – läuft auch im Fehlerfall
  P8 Param-Abgleich je Motor mit Stall-Pin: IDs 9/10/25 lesen; IDs
     ``configurable ∩ Config`` lesen ``06 [02,id]`` (≤3), Vergleich
     ``isclose(rel_tol=1e-9, abs_tol=1e-6)``, bei Abweichung ``06 [01,id]+<f``
     (≤3, Echo prüfen), am Ende ``06 [03]`` je Motor. ID 226 wird NIE
     gesendet (reiner Host-Parameter, Motor antwortet 0 B). Nicht-Float-IDs
     werden nur gelesen (Schreibformat unbelegt).
  P9 bereit: Event ``k2_motor:ready`` (statt Stock-``auto_addr:start``;
     CFS-Auto-Addr kann darauf warten – gleiche Reihenfolge wie Stock:
     gemeinsame ttyS5-Queue erst nach der Motor-Init)

Stock-Bug FIX (belegt, Befund §7): Der Extruder-Param-Abgleich läuft KORREKT
über TransparentTransport an 0x81 auf dem Düsenbus – NIE als 0x85 auf RS485
(Stock adressiert ``verify_sys_param`` für E als num=5 → 0x85 auf ttyS5, wo
niemand antwortet, ≈9 s Timeouts ohne Wirkung). Vorbild ist der ungenutzte
``motor_extruder_sys_param_init``. Dieses Modul sendet NIEMALS an 0x85
(Guard im Code + Test).

Sicherheit: Schreibende Bus-Schritte (Broadcast, Stall-Mode, 0C [05],
Param-Schreiben, 06 [03]) sowie das GPIO-Setzen laufen NUR mit
``allow_write: True`` (Default False → nur lesen + Plan ausgeben). Der
G-Code ``KTWO_MOTOR_INIT`` ohne ``ALLOW_WRITE=1`` gibt ebenfalls nur den Plan
aus. Der Broadcast läuft zusätzlich nur nach gesetzten Strapping-Pegeln
(P1) – ohne Pins bekämen alle Motoren dieselbe Adresse.

Klipper-Einbindung (Dateien kopieren, s. BERICHT.md): ``k2_transport.py`` +
``k2_motor_init.py`` nach ``klippy/extras/`` (als ``k2_motor.py`` bzw. als
Paket), ``[k2_motor]`` in printer.cfg. Alle MCU-Zugriffe sind in den kleinen
Adapterklassen gekapselt (``KlippyGpio``, ``KlippyTransparentAdapter`` aus
k2_transport), damit Tests mit Fake-MCU/Fake-RS485 laufen (s.
test_k2_motor.py). Ohne Klipper importierbar (reine Logik + Fakes).

G-Codes:
- ``KTWO_MOTOR_INIT [DRY_RUN=1] [ALLOW_WRITE=1]`` – Init-Lauf (Default:
  Plan ausgeben, nichts senden)
- ``KTWO_MOTOR_STATUS`` – liest je Motor Version (0x0F), Schutz (0x0C [0b]
  dekodiert) und Feedback idx 5/6 (0x08, pos_fdb/spd_fdb) – nur lesend.

Sensorless-Homing X/Y (16.10.2026 spät, config-pi/NOTIZ-stall.md; Frames
live belegt, s. Konstanten STALL_MODE_ENDSTOP ff.):
- ``KTWO_MOTOR_HOMING PHASE=BEGIN|END`` – Stock-Folge vor/nach G28 X/Y:
  BEGIN = 0x11 [01] an x, y + ID 70 (pos_kp) = ``homing_pos_kp`` (300) an x;
  END = 0x11 [02] an x, y + 0C [0b] an alle + ID 70 = Config-Kp (100) an x.
  BEGIN-Fehler -> Rückbau + G-Code-Fehler (G28 bricht ohne Bewegung ab);
  END läuft immer komplett, Fehler danach als G-Code-Fehler. Bricht ein
  G-Code zwischen BEGIN und END ab (Ereignis ``gcode:command_error``),
  setzt das Modul X/Y selbst zurück.
- ``KTWO_MOTOR_STALL_MODE [MOTOR=x|y|xy|z|z1] [DATA=1|2]`` – ohne DATA lesen
  (0x11 [04]), mit DATA setzen (nur x/y).
- ``KTWO_MOTOR_SYS_PARAM MOTOR=x ID=<n> [VALUE=<f>]`` – ohne VALUE lesen
  (0x06 [02,id]), mit VALUE flüchtig schreiben (0x06 [01,id]+<f, nur
  SYS_PARAM_WRITABLE_IDS, kein 06 [03]).
- ``KTWO_MOTOR_PROTECTION [STRICT=1]`` – 0C [0b] an alle (lesend).
Alle schreibenden Varianten NUR mit ``allow_write: True`` in der Config
(kein G-Code-Override), vorher ``toolhead.wait_moves()``; Fehler = G-Code-
Fehler, nie Shutdown. Status für Makros: ``printer.k2_motor`` mit
``allow_write``, ``homing_active``, ``stall_mode``, ``homing_pos_kp``,
``last_error``.
"""

import math
import struct
import time

# Import-Regel (Fix 16.10.2026, s. k2_transport.py): eigener Ordner NIE in
# sys.path (unter Klipper = klippy/extras/, verdeckte "statistics" u. a.);
# k2_transport relativ (Klipper-Paket extras), Fallback absolut (Tests).
try:
    from . import k2_transport as _K2T
except ImportError:
    try:
        import k2_transport as _K2T
    except ImportError as _e:  # pragma: no cover
        raise ImportError("k2_transport.py wird benötigt (gleicher Ordner): %s" % _e)
Rs485Transport = _K2T.Rs485Transport
TransparentTransport = _K2T.TransparentTransport
KlippyTransparentAdapter = _K2T.KlippyTransparentAdapter

# Nachbau-Helfer optional (für Decoder-Vergleich, kein Muss – unten vendored).
try:
    _M = _K2T.import_nachbau("motor")
    _HAVE_MOTOR = True
except Exception:
    _M = None
    _HAVE_MOTOR = False

# =====================================================================
# Konstanten (Port von nachbau/motor_init.py + motor.py, Belege dort)
# =====================================================================

NUM_BY_NAME_INIT = {"x": 1, "y": 2, "z": 3, "z1": 4, "e": 1}

# Gemeinsamer RS485-Bus (Abschnitt/Objekt aus k2_485.py)
BUS_SECTION = "k2_485"
MOTOR_NAME_TO_NUM = {"x": 1, "y": 2, "z": 3, "z1": 4, "e": 5}
VERIFY_ORDER = ("x", "y", "z", "z1", "e")
PIN_MOTORS = ("x", "y", "z", "z1")
ALWAYS_READ = (9, 10, 25)
HOST_ONLY_IDS = (226,)
ABS_TOL = 1e-6
REL_TOL = 1e-9

BROADCAST_ADDR = 0xFF
TRANSPARENT_ADDR = 0x81
STOCK_E_ADDR = 0x85  # Stock-Fehlgriff – wird NIE gesendet (Guard + Test).

FUNC_SYS_PARAM = 0x06
FUNC_PROTECTION = 0x0C
FUNC_ADDR = 0x0E
FUNC_VERSION = 0x0F
FUNC_SET_MODE = 0x11
FUNC_VARIABLE = 0x08

SUB_SYS_WRITE = 0x01
SUB_SYS_READ = 0x02
SUB_SYS_UPDATE = 0x03
SUB_PROT_CLEAR = 0x05
SUB_PROT_CHECK = 0x0B
SUB_ADDR_SET = 0x01
SUB_ADDR_READ = 0x02
SUB_VERSION_GET = 0x00
STALL_MODE_EMERGENCY = 0x02

# --- Sensorless-Homing X/Y (Stock sensorless.cfg [homing_override]) ----------
# Belege (live-logs-2026-10-16/klippy.log, G28 X Y 14:06:21–14:06:31,
# Zeilen 369155–369319; G28 14:08-14:09 / 14:21-14:22 identisch):
#   vor dem Homing  MOTOR_STALL_MODE DATA=1   -> 81 04 00 11 01, 82 04 00 11 01
#                   (Echo f7 8x 04 00 11 01 1d)
#                   MOTOR_SYS_PARAM NUM=1 DATA=1 ID=70 PARAMS=300 PARAMS_TYPE=float
#                   -> 81 09 00 06 01 46 00 00 96 43 (Echo f7 81 07 00 06 00 00 96 43 09)
#   nach dem Homing MOTOR_STALL_MODE DATA=2   -> 81/82 04 00 11 02 (Echo ... 02 14)
#                   MOTOR_CHECK_PROTECTION_AFTER_HOME DATA=11 -> 0C [0b] an
#                   81, 82, 83, 84 + E transparent (f7 81 04 00 0c 0b 95)
#                   MOTOR_SYS_PARAM NUM=1 ... PARAMS=100 -> 81 09 00 06 01 46 00 00 c8 42
# KEIN 06 [03] (Übernahme) und KEINE Pause zwischen den Frames (Stock-Abstand
# ~20 ms = Bus-Laufzeit). DATA=1/2 gehen NUR an X/Y (0x81/0x82), Z/Z1 bleiben
# auf ihrem Boot-Modus 2 (alle Logs 05.–16.10.). ID 70 =
# controller_pos_loop_pid_param_kp, NUM=1 -> nur Motor x (0x81); Motor y hat
# laut motor_control.cfg ohnehin Kp 300 -> beim Homing beide CoreXY-Motoren 300.
# Hilfetext Stock (REA-MOTOR-BEFUND §3): Stall-Mode 1 = Endschalter-/
# Blockiersignal (PB11/PB12), 2 = Notfall-Schutzausgang.
STALL_MODE_ENDSTOP = 0x01
STALL_MODES_SETTABLE = (STALL_MODE_ENDSTOP, STALL_MODE_EMERGENCY)  # 03 nur Cut-Check
SUB_STALL_GET = 0x04       # 0x11 [04] -> [mode, 00] (klippy.log.2026-10-12:31417)
STALL_SET_MOTORS = ("x", "y")      # Stock MOTOR_STALL_MODE: nur 0x81/0x82
STALL_READ_MOTORS = ("x", "y", "z", "z1")
SYS_PARAM_MOTORS = ("x", "y", "z", "z1")  # RS485; E (transparent) hier nicht
HOMING_PARAM_ID = 70       # controller_pos_loop_pid_param_kp
HOMING_PARAM_MOTORS = ("x",)  # Stock NUM=1
HOMING_POS_KP_DEFAULT = 300.0  # Stock PARAMS=300 (sensorless.cfg:145)
HOMING_POS_KP_RESTORE_DEFAULT = 100.0  # Stock PARAMS=100, sonst Config-Wert
# Schreibbar per KTWO_MOTOR_SYS_PARAM: nur die IDs, die Stock per
# [motor_control] setzt (SYS_PARAM_STOCK_CONFIGURED ohne Host-ID 226).
SYS_PARAM_WRITABLE_IDS = (70, 86, 87, 102, 103, 116, 118, 119, 120, 121, 187, 188)

TIMEOUT_ADDR = 0.05
TIMEOUT_STALL = 1.0
TIMEOUT_PROTECTION = 0.05
TIMEOUT_SYS_PARAM = 1.0
TIMEOUT_VERSION = 1.0
TIMEOUT_VARIABLE = 1.0
TIMEOUT_TRANSPARENT = 2.0

PAUSE_AFTER_GPIO = 0.2
PAUSE_AFTER_BROADCAST = 0.1
PAUSE_BEFORE_ADDR = 0.05
PAUSE_ROUND = 1.0
PAUSE_AFTER_RESTORE = 0.1
PAUSE_RESTORE_DONE = 1.0
PAUSE_STALL_RETRY = 0.05

STALL_TRIES = 3
PARAM_READ_TRIES = 3
PARAM_WRITE_TRIES = 3
GPIO_RESEND_TRIES = 3

WRITING_ARTS = ("broadcast", "stall", "clear", "param_write", "param_update")

# Fehlertexte analog key798–803 (Stock-Codes beibehalten für Nachvollzug;
# Shutdown NUR per Option shutdown_on_failure, sonst nur Fehlermeldung).
ERR_KEY798 = ("[key798] Motor-Verbindung fehlgeschlagen, max. Retry-Anzahl "
              "(retries=%d) überschritten. Fehlend: %s. "
              "Stall-/Param-Phase entfallen.")
ERR_KEY799 = ("[key799] GPIO-Prüfung (Strapping) fehlgeschlagen: Pin %s "
              "erwartet phys=%d, gelesen=%s (%d/%d Versuche).")
ERR_KEY800 = ("[key800] GPIO-Restore-Prüfung fehlgeschlagen: Pin %s "
              "erwartet phys=0, gelesen=%s.")
ERR_KEY801 = ("[key801] Stall-Mode (Z/Z1) fehlgeschlagen: Motor %s "
              "antwortet nicht auf 0x11 [02] (%d Versuche).")
ERR_KEY802 = ("[key802] Stall-Mode (E) fehlgeschlagen: Extruder antwortet "
              "nicht auf 0x11 [02] via transparent (%d Versuche).")
# key803 gehört zum NICHT genutzten zweiten Param-Pfad (nur Doku, kein Code).

# --- Param-Tabelle (vendored aus motor.py SYS_PARAM_NAMES, 227 IDs) -----------
# Namen identisch zu motor.SYS_PARAM_NAMES (inkl. Tippfehler standar/calibtate/
# parma); Index = Param-ID. Quelle: nachbau/motor.py (live flash_param-Enum).
_SYS_PARAM_NAMES = (
    "param_flash_param_version", "param_boot_key",
    "param_flash_key_write_retries_num", "param_system_startup_delay_ms",
    "param_pwm_period_s", "param_task_period_s", "param_use_default_param",
    "param_singleturn_control_mode",
    "param_elec_angle_calibration_standar_deg", "param_elec_offset",
    "param_phase_order_invert", "param_emergency_pin_mode", "param_stall_mode",
    "param_stall_cur_A", "param_stall_pos_err_rad", "param_calibration_dir",
    "param_encoder_cal_count_max", "param_encoder_read_count",
    "param_delay_ms_per_step", "param_delay_ms_per_read", "param_ud_cal_set",
    "param_motion_dir", "param_encoder_calibtate_official_cal_count_max",
    "param_encoder_calibtate_official_ud_cal_set",
    "param_encoder_calibration_round_count", "param_elec_offset_err_deg",
    "driver_board_param_power_supply_V", "driver_board_param_adc_ref_V",
    "driver_board_param_amp_gain",
    "driver_board_param_power_voltage_sample_gain",
    "driver_board_param_R_of_current_sample_ohm",
    "driver_board_param_adc_calibration_start_index",
    "driver_board_param_adc_calibration_buffer_size",
    "driver_board_param_led_toggle_period_s", "driver_board_param_current_dir",
    "driver_board_param_pwm_output_en", "driver_board_param_mos_Ron",
    "driver_board_param_mos_deadtime", "driver_board_param_mos_rise_fall_time",
    "driver_board_param_mos_max_peak_current",
    "driver_board_param_mos_max_continuous_current", "motor_param_R",
    "motor_param_L", "motor_param_Ke", "motor_param_Kt", "motor_param_mass",
    "motor_param_J", "motor_param_B", "motor_param_stepping_angle_degree",
    "motor_param_phase_nums", "motor_param_pole_pair",
    "motor_param_power_supply", "motor_param_rated_voltage",
    "motor_param_rated_current", "motor_param_maximum_holding_torque",
    "encoder_param_direction", "encoder_param_encoder_bit",
    "encoder_param_encoder_max_pulse", "encoder_param_fake_encoder_en",
    "encoder_param_fake_encoder_step", "mcu_param_sysclk_hz",
    "mcu_param_HSE_hz", "mcu_param_uart_baudrate",
    "mcu_param_input_pulse_counter_trigger_mode",
    "mcu_param_input_pulse_counter_filter_level",
    "controller_param_control_algorithm",
    "controller_param_en_feedforward_idiq",
    "controller_param_en_feedforward_uduq", "controller_pos_loop_pid_param_en",
    "controller_pos_loop_pid_param_bypass", "controller_pos_loop_pid_param_kp",
    "controller_pos_loop_pid_param_ki", "controller_pos_loop_pid_param_kd",
    "controller_pos_loop_pid_param_kc",
    "controller_pos_loop_pid_ud_filter_param_en",
    "controller_pos_loop_pid_ud_filter_param_fc",
    "controller_pos_loop_pid_param_limit_ui",
    "controller_pos_loop_pid_parma_limit_out",
    "controller_pos_loop_pid_fal_param_en",
    "controller_pos_loop_pid_fal_param_a",
    "controller_pos_loop_pid_fal_param_d",
    "controller_pos_loop_pid_fal_param_zoom",
    "controller_pos_loop_pid_fal_param_section_nums",
    "controller_pos_loop_pid_fal_param_inteval_gain",
    "controller_spd_loop_pid_param_en", "controller_spd_loop_pid_param_bypass",
    "controller_spd_loop_pid_param_kp", "controller_spd_loop_pid_param_ki",
    "controller_spd_loop_pid_param_kd", "controller_spd_loop_pid_param_kc",
    "controller_spd_loop_pid_ud_filter_param_en",
    "controller_spd_loop_pid_ud_filter_param_fc",
    "controller_spd_loop_pid_param_limit_ui",
    "controller_spd_loop_pid_parma_limit_out",
    "controller_spd_loop_pid_fal_param_en",
    "controller_spd_loop_pid_fal_param_a",
    "controller_spd_loop_pid_fal_param_d",
    "controller_spd_loop_pid_fal_param_zoom",
    "controller_spd_loop_pid_fal_param_section_nums",
    "controller_spd_loop_pid_fal_param_inteval_gain",
    "controller_cur_loop_pid_param_en", "controller_cur_loop_pid_param_bypass",
    "controller_cur_loop_pid_param_kp", "controller_cur_loop_pid_param_ki",
    "controller_cur_loop_pid_param_kd", "controller_cur_loop_pid_param_kc",
    "controller_cur_loop_pid_ud_filter_param_en",
    "controller_cur_loop_pid_ud_filter_param_fc",
    "controller_cur_loop_pid_param_limit_ui",
    "controller_cur_loop_pid_parma_limit_out",
    "controller_cur_loop_pid_fal_param_en",
    "controller_cur_loop_pid_fal_param_a",
    "controller_cur_loop_pid_fal_param_d",
    "controller_cur_loop_pid_fal_param_zoom",
    "controller_cur_loop_pid_fal_param_section_nums",
    "controller_cur_loop_pid_fal_param_inteval_gain",
    "controller_leso_param_b0k", "controller_leso_param_b0",
    "controller_leso_param_z3k", "controller_leso_param_wp",
    "controller_leso_param_ws", "controller_leso_param_wd",
    "controller_fwc_param_en", "controller_fwc_param_I_max",
    "controller_fwc_param_L", "controller_fwc_param_phi_e",
    "controller_fwc_param_pole_pair", "controller_fwc_param_wm_base",
    "controller_cur_filter_param_en", "controller_cur_filter_param_Ts",
    "controller_cur_filter_param_fc",
    "controller_z3_filter_binary_second_order_param_en",
    "controller_z3_filter_binary_second_order_param_fc",
    "controller_z3_filter_binary_second_order_param_depth",
    "controller_z3_filter_binary_second_order_param_zeta_0N",
    "controller_z3_filter_binary_second_order_param_zeta_0D",
    "controller_spd_out_filter_binary_second_order_param_en",
    "controller_spd_out_filter_binary_second_order_param_fc",
    "controller_spd_out_filter_binary_second_order_param_depth",
    "controller_spd_out_filter_binary_second_order_param_zeta_0N",
    "controller_spd_out_filter_binary_second_order_param_zeta_0D",
    "controller_td_param_en", "controller_td_param_h0_gain_h",
    "controller_td_fhan_param_f0", "controller_td_param_d_gain",
    "controller_td_param_dd_gain",
    "controller_spd_out_filter_notch_0_param_en",
    "controller_spd_out_filter_notch_0_param_f0",
    "controller_spd_out_filter_notch_0_param_f_width",
    "controller_spd_out_filter_notch_0_param_depth",
    "controller_spd_out_filter_notch_1_param_en",
    "controller_spd_out_filter_notch_1_param_f0",
    "controller_spd_out_filter_notch_1_param_f_width",
    "controller_spd_out_filter_notch_1_param_depth",
    "controller_spd_out_filter_notch_2_param_en",
    "controller_spd_out_filter_notch_2_param_f0",
    "controller_spd_out_filter_notch_2_param_f_width",
    "controller_spd_out_filter_notch_2_param_depth",
    "controller_motor_ripple_compensator_en",
    "controller_motor_ripple_compensator_sin_coeffs_0_term_A",
    "controller_motor_ripple_compensator_sin_coeffs_0_term_n",
    "controller_motor_ripple_compensator_sin_coeffs_0_term_phi",
    "controller_motor_ripple_compensator_sin_coeffs_1_term_A",
    "controller_motor_ripple_compensator_sin_coeffs_1_term_n",
    "controller_motor_ripple_compensator_sin_coeffs_1_term_phi",
    "controller_motor_ripple_compensator_sin_coeffs_2_term_A",
    "controller_motor_ripple_compensator_sin_coeffs_2_term_n",
    "controller_motor_ripple_compensator_sin_coeffs_2_term_phi",
    "flash_manager_param_checksum_restore_en", "logger_param_en",
    "logger_param_continuous_send", "logger_param_sample_interval_n",
    "logger_param_n_pack_to_send", "protection_param_protect_en",
    "protection_param_protect_report", "protection_param_err_code_mask",
    "protection_param_warning_code_mask",
    "protection_param_encoder_mutation_coefficient_upper",
    "protection_param_encoder_mutation_coefficient_lower",
    "protection_param_prt_peak_cur_A", "protection_param_prt_continuous_cur_A",
    "protection_param_prt_continuous_time_s",
    "protection_param_prt_over_speed_rad_s",
    "protection_param_prt_over_speed_time_s",
    "protection_param_pos_over_limit_max_rad",
    "protection_param_pos_over_limit_min_rad",
    "protection_param_prt_cmd_mutation_turn",
    "protection_param_prt_track_max_err",
    "protection_param_prt_track_err_time",
    "protection_param_power_voltage_min", "protection_param_mcu_temp_max",
    "protection_err_code_backup", "protection_warning_code_backup",
    "chopper_param_chopper_mode", "chopper_param_svpwm_mode",
    "chopper_param_base_pwm", "chopper_param_offset_pwm_en",
    "chopper_param_offset_pwm", "chopper_param_hybrid_decay_ratio",
    "step_controller_param_pulse_count_max",
    "step_controller_param_subdivision", "step_controller_param_pulse_trim_en",
    "step_controller_param_trim_action_period",
    "step_controller_param_extra_trim_pulse", "cmd_int_param_char_cmd_support",
    "system_id_RL_param_param_uin_startup",
    "system_id_RL_param_param_uin_calibrate",
    "system_id_RL_param_param_current_stable_err_condition",
    "system_id_RL_param_param_current_buff_size",
    "system_id_RL_param_param_current_err_size",
    "system_id_RL_param_param_prepare_time",
    "system_id_RL_param_param_normal_R_range_min",
    "system_id_RL_param_param_normal_R_range_max",
    "system_id_RL_param_param_normal_L_range_min",
    "system_id_RL_param_normal_L_range_max",
    "motor_phase_check_param_run_on_startup",
    "motor_phase_check_param_motor_resistance_tolerance_standar_percent",
    "zazen_param_zazen_en", "zazen_param_zazen_trigger_time_s",
    "zazen_param_zazen_gain_pos_kp", "zazen_param_zazen_gain_spd_kp",
    "zazen_param_zazen_gain_spd_ki", "zazen_param_zazen_gain_cur_kp",
    "zazen_param_zazen_gain_cur_ki", "param_flash_key", "param_flash_checksum",
    "stall_pos_err_rad_for_slicer",
)
SYS_PARAM_COUNT = 227
SYS_PARAM_CONFIGURABLE = frozenset((
    9, 10, 12, 13, 14, 20, 21, 23, 25, 41, 42, 43, 66, 67, 70, 86, 87, 89,
    94, 95, 97, 102, 103, 105, 110, 111, 113, 116, 118, 119, 120, 121, 122,
    123, 127, 128, 130, 141, 142, 143, 144, 145, 173, 179, 180, 181, 182,
    183, 187, 188, 189, 190, 200, 204, 217, 218, 220, 221, 222, 223, 226))
_PARAM_TYPES_NONFLOAT = {
    10: "int16_t", 12: "uint16_t", 66: "uint16_t", 67: "uint16_t",
    94: "uint16_t", 110: "uint16_t", 122: "uint16_t", 128: "uint16_t",
    141: "uint16_t", 173: "uint16_t", 200: "uint16_t", 204: "uint16_t",
    217: "uint16_t"}
_TYPE_FORMATS = {"float": "<f", "uint16_t": "<H", "int16_t": "<h",
                 "uint32_t": "<I", "int32_t": "<i"}

MOTOR_ERROR_BITS = {
    0: ("key781", "Encoder-Sprung"), 1: ("key782", "Encoder-Lesefehler"),
    2: ("key783", "SW-Spitzenüberstrom"), 3: ("key784", "SW-Dauerüberstrom"),
    4: ("key785", "HW-Überstrom"), 5: ("key786", "Drehzahl über Grenze"),
    6: ("key787", "Positionsgrenze"), 7: ("key788", "Positions-Sollwertsprung"),
    8: ("key789", "Schleppfehler"), 9: ("key790", "R/L-Fehler"),
    10: ("key791", "instabil"), 11: ("key792", "Phase fehlt"),
    12: ("key793", "Phasenwiderstand-Differenz"), 13: ("key794", "Sonstige"),
}
MOTOR_WARNING_BITS = {
    0: ("key795", "Kommunikation"), 1: ("key796", "Unterspannung"),
    2: ("key797", "MCU-Übertemperatur"),
}


def default_param_table():
    """227 Einträge {id, name, configurable, value_type} (Port aus Nachbau)."""
    table = []
    for pid, name in enumerate(_SYS_PARAM_NAMES):
        conf = pid in SYS_PARAM_CONFIGURABLE
        vt = _PARAM_TYPES_NONFLOAT.get(pid, "float") if conf else None
        table.append({"id": pid, "name": name, "configurable": conf,
                      "value_type": vt})
    return table


def parse_pin_level(spec):
    """'!PB7,0' -> {raw, pin, invert, level, phys} (phys = level ^ invert)."""
    if spec is None:
        return None
    parts = [p.strip() for p in str(spec).split(",")]
    if len(parts) != 2 or parts[1] not in ("0", "1"):
        raise ValueError("Pin-Angabe muss 'PIN,0|1' sein: %r" % (spec,))
    pin = parts[0]
    inv = pin.startswith("!")
    lvl = int(parts[1])
    return {"raw": pin, "pin": pin.lstrip("!^~"), "invert": inv,
            "level": lvl, "phys": lvl ^ int(inv)}


def strapping_address(dir_phys, step_phys):
    """Motor-FW 0x0E Sub 01: addr = 0x81 + 2·STEP + DIR (Byte)."""
    for v in (dir_phys, step_phys):
        if v not in (0, 1):
            raise ValueError("Pegel muss 0/1 sein: %r" % (v,))
    return 0x81 + 2 * int(step_phys) + int(dir_phys)


def _isclose(a, b):
    return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def _float_to_bytes(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Sys-Param-Wert muss Zahl sein: %r" % (value,))
    if not math.isfinite(value):
        raise ValueError("Sys-Param-Wert muss endlich sein: %r" % (value,))
    try:
        return struct.pack("<f", float(value))
    except Exception as exc:
        raise ValueError("passt nicht in float32: %r (%s)" % (value, exc))


def decode_protection_local(data):
    """8 B von 0x0C [0b] -> dict (vendored aus motor.py, dort belegt)."""
    raw = bytes(data)
    if len(raw) != 8:
        raise ValueError("0x0C: %d statt 8 Bytes" % len(raw))
    err, warn = struct.unpack("<II", raw)
    def _bits(code, table):
        out, bit = [], 0
        while code >> bit:
            if code >> bit & 1:
                key, text = table.get(bit, (None, "unbekannt"))
                out.append((bit, key, text))
            bit += 1
        return out
    return {"err_code": err, "warning_code": warn,
            "errors": _bits(err, MOTOR_ERROR_BITS),
            "warnings": _bits(warn, MOTOR_WARNING_BITS),
            "ok": err == 0 and warn == 0, "raw": raw}


def decode_version_local(data):
    """12 B von 0x0F [00] -> dict (Feldnamen = Stock-Strings)."""
    raw = bytes(data)
    if len(raw) != 12:
        raise ValueError("0x0F: %d statt 12 Bytes" % len(raw))
    fields = (("fireware_ver", 4), ("cmd_system_ver", 2),
              ("protection_ver", 2), ("flash_param_ver", 2), ("logger_ver", 2))
    out, pos = {}, 0
    for name, n in fields:
        out[name] = ".".join(str(b) for b in raw[pos:pos + n])
        pos += n
    out["raw"] = raw
    return out


# =====================================================================
# Config (rein + Klipper-Hülle)
# =====================================================================

def parse_options(opts):
    """Reines Dict (keys klein, Werte Strings) -> strukturierte Config.

    Unterstützt Stock-[motor_control] plus neue Optionen (ANNAHME, mit
    Stock-kompatiblem Default):
    - allow_write (Default False), shutdown_on_failure (Default False),
      auto_init (Default True), nozzle_mcu (Default 'nozzle_mcu'),
      main_mcu (Default 'mcu').
    """
    o = {str(k).strip().lower(): v for k, v in dict(opts).items()}
    if "switch" not in o:
        raise ValueError("[k2_motor] switch fehlt (Pflichtoption, wie Stock)")
    try:
        switch = int(str(o["switch"]).strip())
    except ValueError:
        raise ValueError("switch muss 0/1 sein: %r" % (o["switch"],))
    closed_raw = str(o.get("motor_closed_loop", "") or "")
    closed = [s.strip() for s in closed_raw.replace(";", ",").split(",") if s.strip()]
    for m in closed:
        if m not in MOTOR_NAME_TO_NUM:
            raise ValueError("motor_closed_loop: unbekannter Motor %r" % (m,))
    def _int(key, default):
        try:
            return int(str(o.get(key, default)).strip())
        except ValueError:
            raise ValueError("%s muss int sein: %r" % (key, o.get(key)))
    def _bool(key, default):
        v = str(o.get(key, str(default))).strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off"):
            return False
        raise ValueError("%s muss bool sein: %r" % (key, o.get(key)))
    cfg = {
        "switch": switch,
        "motor_closed_loop": closed,
        "retries": _int("retries", 4),
        "allow_write": _bool("allow_write", False),
        "shutdown_on_failure": _bool("shutdown_on_failure", False),
        "auto_init": _bool("auto_init", True),
        "nozzle_mcu": str(o.get("nozzle_mcu", "nozzle_mcu")).strip(),
        "main_mcu": str(o.get("main_mcu", "mcu")).strip(),
        "homing_pos_kp": HOMING_POS_KP_DEFAULT,
        "pins": {}, "params": {},
    }
    if cfg["retries"] < 1:
        raise ValueError("retries muss >= 1 sein")
    if o.get("homing_pos_kp") not in (None, ""):
        try:
            hk = float(str(o["homing_pos_kp"]).strip())
        except ValueError:
            raise ValueError("homing_pos_kp muss Zahl sein: %r" % (o["homing_pos_kp"],))
        if not math.isfinite(hk) or not 0.0 < hk <= 10000.0:
            raise ValueError("homing_pos_kp muss 0 < Wert <= 10000 sein: %r" % (hk,))
        cfg["homing_pos_kp"] = hk
    for m in VERIFY_ORDER:
        cfg["pins"][m] = {
            "dir": parse_pin_level(o.get("motor_%s_dir" % m)),
            "step": parse_pin_level(o.get("motor_%s_step" % m)),
            "stall": (str(o.get("motor_%s_stall" % m)).strip()
                      if o.get("motor_%s_stall" % m) not in (None, "") else None),
        }
        cfg["params"][m] = {}
    known = {"switch", "motor_closed_loop", "retries", "allow_write",
             "shutdown_on_failure", "auto_init", "nozzle_mcu", "main_mcu",
             "overcurrent_switch", "cut_pos_offset", "homing_pos_kp"}
    import re as _re
    by_name = {p["name"]: p for p in default_param_table()}
    for key, val in o.items():
        if key in known or _re.match(r"^motor_(x|y|z|z1|e)_(dir|step|stall)$", key):
            continue
        m = _re.match(r"^(z1|x|y|z|e)_(.+)$", key)
        if not m:
            continue  # unbekannte Option ignorieren (wie Stock stille Filter)
        motor, pname = m.group(1), m.group(2)
        if motor not in cfg["params"]:
            continue
        if pname not in by_name:
            continue  # unbekannter Param – ignorieren (Stock: still)
        if not by_name[pname]["configurable"]:
            continue  # nicht configurable – still ignorieren (wie Stock)
        try:
            import ast as _ast
            v = _ast.literal_eval(str(val).strip())
        except Exception:
            raise ValueError("Config-Wert nicht auswertbar: %s=%r" % (key, val))
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError("Config-Wert muss Zahl sein: %s=%r" % (key, val))
        cfg["params"][motor][pname] = v
    return cfg


def parse_klipper_config(config):
    """Klipper-ConfigWrapper -> parse_options (ANNAHME zur API, s. BERICHT).

    Versucht der Reihe nach: get_options() für freie <motor>_<param>-Keys,
    sonst getlist/getint/getfloat/getboolean/get. FakeConfig in Tests bietet
    dieselben Methoden.
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
    # Pflicht/Fix-Keys (Typen tolerant lesen – Klipper bietet getint etc.)
    def _gi(name, default):
        try:
            if hasattr(config, "getint"):
                return config.getint(name, default)
        except Exception:
            pass
        try:
            return int(str(_g(name, default)).strip())
        except Exception:
            return default
    def _gb(name, default):
        try:
            if hasattr(config, "getboolean"):
                return config.getboolean(name, default)
        except Exception:
            pass
        return _g(name, str(default))
    if not keys and isinstance(config, dict):
        return parse_options(config)
    for k in keys:
        opts[k.lower()] = _g(k, "")
    # Falls get_options leer war (manche Wrapper), Fix-Keys direkt holen:
    for k in ("switch", "motor_closed_loop", "retries", "allow_write",
              "shutdown_on_failure", "auto_init", "nozzle_mcu", "main_mcu",
              "overcurrent_switch", "cut_pos_offset", "homing_pos_kp"):
        if k not in opts:
            v = _g(k, None)
            if v is not None:
                opts[k] = v
    # Motor-Pins/Params, falls nicht in keys enthalten:
    import re as _re
    probe = []
    for m in VERIFY_ORDER:
        for tail in ("dir", "step", "stall"):
            probe.append("motor_%s_%s" % (m, tail))
    # Param-Keys aus der Tabelle (nur configurable, wie Stock relevant):
    for p in default_param_table():
        if p["configurable"]:
            for m in VERIFY_ORDER:
                probe.append("%s_%s" % (m, p["name"]))
    for k in probe:
        if k not in opts:
            v = _g(k, None)
            if v is not None and str(v) != "":
                opts[k] = v
    if "switch" not in opts:
        # getint-Variante (Klipper-Style, Pflichtoption)
        try:
            if hasattr(config, "getint"):
                opts["switch"] = config.getint("switch")
        except Exception as exc:
            raise ValueError("[k2_motor] switch fehlt (Pflichtoption): %s" % exc)
    if "motor_closed_loop" not in opts:
        try:
            if hasattr(config, "getlist"):
                opts["motor_closed_loop"] = ",".join(
                    config.getlist("motor_closed_loop", []))
        except Exception:
            pass
    _ = _gi; _ = _gb
    return parse_options(opts)


# =====================================================================
# GPIO-Adapter (klein, testbar) – REVIEW 1–3 eingearbeitet (16.10.2026)
# =====================================================================

# Enable-Pin, fest im Stock-Modul (GPIO-Befund (lokal) §0: Strings 'enable_pin_pa9',
# '!PA9', 'force stop,set !PA9 value=0'). Wird hier FRÜH mit angelegt
# (Review 1), vom Controller derzeit nicht aktiv getrieben (nur Doku;
# künftige Enable-Steuerung nutzt denselben Pfad).
ENABLE_PIN_RAW = "!PA9"


def _clean_pin_name(pin_raw):
    """'!PB7' / 'mcu:PB7' -> 'PB7' (Invert- und MCU-Präfix entfernen)."""
    s = str(pin_raw).strip()
    if ":" in s:
        s = s.split(":", 1)[1]
    return s.lstrip("!^~").strip()


def pin_number_from_enum(enum_pin, pin_name):
    """Klipper-Pinnummer aus der MCU-Enumeration 'pin' auflösen.

    Dictionary-Beleg (mcu-main-F008-GD32F303RET6.dict.json): nur Basis-Einträge
    ``PA0 [0,16], PB0 [16,16], PC0 [32,16], PD0 [48,16]`` – [Basis, Anzahl].
    ``PB9`` = Basis PB0 (16) + 9 = 25. Direkte Einträge (z.B. ADC_TEMPERATURE)
    werden direkt zurückgegeben. ANNAHME: Offset-Logik für PxN (Upstream-Muster).
    """
    clean = _clean_pin_name(pin_name)
    if not isinstance(enum_pin, dict):
        raise ValueError("Pin-Enumeration fehlt")
    if clean in enum_pin:
        v = enum_pin[clean]
        if isinstance(v, (list, tuple)):
            return int(v[0])
        return int(v)
    # Basis + Offset (z.B. PB9 über PB0 [16,16]):
    import re as _re
    m = _re.match(r"^([A-Z]+)(\d+)$", clean)
    if m:
        base_key = "%s0" % m.group(1)
        if base_key in enum_pin:
            v = enum_pin[base_key]
            base = int(v[0]) if isinstance(v, (list, tuple)) else int(v)
            return base + int(m.group(2))
    raise ValueError("Pin %r nicht in Enumeration" % (pin_name,))



def reactor_sleep(reactor):
    """Relative Pause für Klipper: reactor.pause() erwartet eine ABSOLUTE
    Weckzeit (upstream reactor.py: pause(waketime)). Ohne Reactor -> time.sleep."""
    if reactor is None or not hasattr(reactor, "pause"):
        return time.sleep
    return lambda s: reactor.pause(reactor.monotonic() + float(s))

def _mcu_chip_name(name):
    """Config-/Objektname -> Pin-Chip-Name ('mcu nozzle_mcu' -> 'nozzle_mcu')."""
    name = str(name).strip()
    return name[4:].strip() if name.startswith("mcu ") else name


def _lookup_mcu(printer, name):
    """MCU-Objekt nach Config-Namen (wie k2_prtouch._lookup_mcu, Fix 16.10.2026).

    Upstream registriert '[mcu]' als 'mcu' und '[mcu nozzle_mcu]' als
    'mcu nozzle_mcu' (mcu.py add_printer_objects/get_printer_mcu). Config-Wert
    'nozzle_mcu' wird daher zuerst als 'mcu nozzle_mcu' gesucht; der volle
    Objektname ('mcu nozzle_mcu') und 'mcu' gehen direkt. Fallback: wörtlich."""
    name = str(name).strip()
    if name != "mcu" and not name.startswith("mcu "):
        try:
            return printer.lookup_object("mcu " + name)
        except Exception:
            pass
    return printer.lookup_object(name)


class GpioOps:
    """Protokoll: Strapping-Pins setzen + optional zurücklesen."""

    def set_pin(self, pin_raw, level):
        raise NotImplementedError

    def sync(self):
        """Queue leeren (M400-Äquivalent) – Default: nichts zu tun (Fake)."""
        return None

    def query_pin(self, pin_raw):
        """-> phys-Pegel (0/1) oder None (wenn Rücklesung nicht vorhanden)."""
        return None


class KlippyGpio(GpioOps):
    """Echte GPIO-Anbindung (ANNAHMEN, s. BERICHT.md §Review-Korrekturen).

    REVIEW 1 (Pins zu spät – FIX): Alle Strapping-Pins werden im KONSTRUKTOR
    (load_config-Phase, vor klippy:connect) per ``pins.setup_pin("digital_out",
    pin)`` angelegt (Upstream-Objekt ``pins``). ``setup_pin`` registriert intern
    ``mcu.register_config_callback`` + ``create_oid`` + ``config_digital_out``
    (Upstream-Muster inkl. ``[duplicate_pin_override]`` für das Zeitmultiplex
    mit ``config_stepper``). Zur Laufzeit wird nur noch gesetzt – kein Lazy-
    Anlegen mehr (altes ``_output_pin`` entfernt).

    REVIEW 2 (print_time=0 – FIX, Entscheidung begründet): Variante (a)
    gequeue'tes ``digital_out`` mit gültiger print_time + M400 – NICHT Variante
    (b) direkter ``set_digital_out pin=%u value=%c``. Begründung: (a) entspricht
    dem Stock (GPIO-Befund (lokal) §1, B: ``update_digital_out`` via ``set_digital`` +
    Lookahead + ``M400``), respektiert das ``duplicate_pin_override``-
    Zeitmultiplex mit ``config_stepper`` (M400 synchronisiert Stepper-Queue und
    Strapping – sofortiges ``set_digital_out`` würde die Move-Queue umgehen),
    und ``set_digital_out`` ist im Stock-``.so`` negativ belegt (keine einzige
    Referenz, GPIO-Befund (lokal) §1). ``set_pin`` holt daher
    ``toolhead.get_last_move_time()`` (Fallback
    ``mcu.estimated_print_time(reactor.monotonic()) + 0,100 s``) und ruft
    ``pin.set_digital(print_time, level)``; ``sync()`` macht
    ``toolhead.wait_moves()`` (Fallback ``gcode.run_script("M400")``) – der
    Controller ruft ``sync()`` nach jedem Setzen vor Broadcast/Weiterlesen.
    Gekapselt hinter GpioOps (Tests mit FakePrinter prüfen print_time>0).

    REVIEW 3 (query_stub – FIX) + FIRMWARE-BEFUND 16.10.2026 (Abfrage-OID):
    ``query_gpio_status oid=%c gpio_pin=%c`` → ``gpio_status oid=%c status=%c``
    per ``mcu.lookup_query_command``. Dictionary (mcu-main-F008-
    GD32F303RET6.dict.json): Befehl 75 + Antwort -20 vorhanden, KEIN
    zugehöriger ``config_*``-Befehl. Disassembly des Main-MCU-Images
    (fw/F008/mcu0_140_G32-mcu0_022_000.bin, Basis 0x08003000, Befehlstabelle
    @0x0800743C, 12-Byte-Einträge; die Handler-Adresse zu einem Info-Wort
    (Byte0 = msgid) steht 12 Byte WEITER – die Liste HANDLER-TABELLE.txt
    ordnet deshalb um einen Eintrag verschoben zu; gegengeprüft an
    config_digital_out → 0x08006050 (gpio_out_setup + oid_alloc) und
    config_transparent → 0x080036AC (oid_alloc + USART-Init)):
    Handler query_gpio_status = 0x08003424: liest ``args[1]`` (Pin), Port =
    Pin>>4 aus der Tabelle 0x0800987C (GPIOA..E 0x40010800…), Bit = Pin&15,
    liest Register-Offset 0x0C (GD32 GPIOx_OCTL = Ausgangs-Latch, NICHT
    ISTAT) und antwortet ``gpio_status`` mit ``args[0]`` UNVERÄNDERT als oid.
    KEIN oid_lookup/oid_alloc → die OID ist reine Antwort-Kennung. Daher wird
    KEINE OID per ``create_oid`` verbraucht (vorher: eigenes OID ohne
    config-Befehl), sondern die Konstante ``GPIO_QUERY_OID`` (0) genutzt –
    das Upstream-Antwort-Routing filtert nur nach (``gpio_status``, oid), und
    nur dieses Modul sendet ``query_gpio_status``. Stock nutzte oid 11 für alle
    Pins (Log ``:2259–2266``) – ebenfalls nur Kennung. Folge der OCTL-Lesung:
    die Rücklesung bestätigt den von der MCU AUSGEGEBENEN Pegel (wie Stock
    key799/800), keinen Leitungsfehler. Fehlt der Befehl im Dictionary
    (Upstream-FW), liefert ``handle_connect`` keinen Query und ``query_pin``
    None (Prüfung übersprungen).

    STRAPPING-PINS ``max_duration`` (Fix 16.10.2026): Upstream-
    ``MCU_digital_out`` hat Default ``max_duration = 2 s`` – steht ein Pin
    länger auf Nicht-Default (z. B. Z-STEP 1, Z1 DIR/STEP 1, Y-DIR !PB7 phys 1
    während der ~10 s Init), schaltet die MCU ab. Stock ruft für JEDEN
    Dir/Step-Pin (``motor_x/y/z/z1_init``, 0x73ba8/0x73c54 …) und für
    ``enable_pin_pa9`` (0xce8bc) ``setup_max_duration(0.0)`` auf (BELEGT:
    PYOBJ 'setup_max_duration' + CONST float 0.0 direkt nach 'digital_out'/
    'motor_<m>_dir|step', arbeitsdateien-lokal/lokale Analyse-Dateien).
    Wir machen es genauso (= Upstream-Muster für statische Pins, vgl.
    output_pin.py ``setup_max_duration(0.)``, stepper_enable). Start- und
    Shutdown-Wert bleiben Upstream-Default (= Invertierung → phys. 0, bei
    ``!PA9`` phys. 1 = Treiber aus).
    """

    QUERY_FMT = "query_gpio_status oid=%c gpio_pin=%c"
    QUERY_RESP = "gpio_status oid=%c status=%c"
    # Firmware wertet die OID nicht aus (s. Docstring) -> keine create_oid.
    GPIO_QUERY_OID = 0
    # Stock: setup_max_duration(0.0) für alle Strapping-Pins (s. Docstring).
    STRAPPING_MAX_DURATION = 0.0

    def __init__(self, printer, main_mcu_name="mcu", pin_specs=None):
        self.printer = printer
        self.main_mcu_name = main_mcu_name
        self._pins = {}
        self._pin_numbers = {}
        self._query = None
        self._query_available = None
        self._query_oid = None
        specs = list(pin_specs or [])
        # Deduplizieren, Reihenfolge behalten; leere/None überspringen.
        seen, uniq = set(), []
        for raw in specs:
            if raw is None or str(raw) == "":
                continue
            if raw not in seen:
                seen.add(raw)
                uniq.append(raw)
        # FRÜH (vor connect): pins.setup_pin für jeden Main-MCU-Pin
        # (Upstream-Objekt 'pins', pins.py:138; Stock ebenso 'pins').
        # Pins anderer MCUs (z.B. 'nozzle_mcu:PB12' E-Stall) werden übersprungen
        # – sie gehören nicht zum Strapping und haben eigene MCU-Objekte.
        ppins = printer.lookup_object("pins")
        chip = _mcu_chip_name(main_mcu_name)
        for raw in uniq:
            if ":" in str(raw):
                prefix = str(raw).split(":", 1)[0].lstrip("!^~").strip()
                if prefix not in (main_mcu_name, chip):
                    continue
            # Upstream-Signatur: setup_pin('digital_out', pin_desc) -> MCU_digital_out
            pin = ppins.setup_pin("digital_out", raw)
            # Statischer Pin wie Stock: max_duration 0 (kein MCU-Shutdown, wenn
            # der Strapping-Pegel länger als 2 s ansteht, s. Docstring).
            if hasattr(pin, "setup_max_duration"):
                pin.setup_max_duration(self.STRAPPING_MAX_DURATION)
            self._pins[raw] = pin
        # Pin-Nummern (gpio_pin-Byte) – das Dictionary (Enumeration 'pin') ist
        # erst nach dem Connect da; hier nur versuchen, sonst lazy in query_pin.
        for raw in list(self._pins.keys()):
            try:
                self._pin_numbers[raw] = self._resolve_pin_number(raw)
            except Exception:
                pass
        # Antwort-Kennung ohne create_oid (Firmware wertet die OID nicht aus).
        self._query_oid = self.GPIO_QUERY_OID

    def _resolve_pin_number(self, pin_raw):
        # Upstream: pins.lookup_pin liefert nur den Pin-NAMEN (pin_params
        # ['pin'] = 'PB9') und registriert den Pin als belegt – daher NICHT
        # verwendet. Nummer aus der MCU-Enumeration 'pin' (Dictionary, erst
        # nach dem Connect verfügbar).
        # 1) mcu.get_enumeration('pin', name) / get_enumerations()['pin'].
        try:
            mcu = _lookup_mcu(self.printer, self.main_mcu_name)
            if hasattr(mcu, "get_enumeration"):
                return int(mcu.get_enumeration("pin", _clean_pin_name(pin_raw)))
            if hasattr(mcu, "get_enumerations"):
                enum = dict(mcu.get_enumerations()).get("pin", {})
                return pin_number_from_enum(enum, pin_raw)
        except Exception:
            pass
        # 2) Direkte Enumeration über das pins-Objekt (nur Test-Helper).
        try:
            ppins = self.printer.lookup_object("pins")
            if hasattr(ppins, "get_enumerations"):
                enum = dict(ppins.get_enumerations()).get("pin", {})
                return pin_number_from_enum(enum, pin_raw)
        except Exception:
            pass
        raise ValueError("Pin-Nummer für %r nicht auflösbar" % (pin_raw,))

    def handle_connect(self):
        """Query-Befehl nach klippy:connect auflösen (optional, sonst None)."""
        try:
            mcu = _lookup_mcu(self.printer, self.main_mcu_name)
        except Exception:
            self._query = None
            self._query_available = False
            return
        try:
            # Upstream: try_lookup_command(msgformat) -> None, wenn der Befehl
            # im Dictionary fehlt (mcu.py:1206); erst dann den Query anlegen.
            if hasattr(mcu, "try_lookup_command"):
                if mcu.try_lookup_command(self.QUERY_FMT) is None:
                    self._query = None
                    self._query_available = False
                    return
            self._query = mcu.lookup_query_command(
                self.QUERY_FMT, self.QUERY_RESP, oid=self._query_oid)
            self._query_available = True
        except Exception:
            self._query = None
            self._query_available = False

    def set_pin(self, pin_raw, level):
        if pin_raw not in self._pins:
            raise KeyError(
                "Pin %r nicht in load_config angelegt (setup_pin fehlt vor "
                "connect – Review 1)" % (pin_raw,))
        pin = self._pins[pin_raw]
        # Gültige print_time (Review 2, Variante a – s. Docstring).
        try:
            toolhead = self.printer.lookup_object("toolhead")
            print_time = toolhead.get_last_move_time()
        except Exception:
            mcu = _lookup_mcu(self.printer, self.main_mcu_name)
            reactor = self.printer.get_reactor()
            print_time = mcu.estimated_print_time(reactor.monotonic()) + 0.100
        if print_time is None or float(print_time) <= 0:
            raise RuntimeError(
                "Ungültige print_time %r für set_digital (Review 2)" % (print_time,))
        pin.set_digital(float(print_time), int(level))

    def sync(self):
        """M400-Äquivalent: Queue leeren, bevor Broadcast gesendet wird."""
        try:
            toolhead = self.printer.lookup_object("toolhead")
            if hasattr(toolhead, "wait_moves"):
                toolhead.wait_moves()
                return
        except Exception:
            pass
        try:
            gcode = self.printer.lookup_object("gcode")
            if hasattr(gcode, "run_script"):
                gcode.run_script("M400")
                return
        except Exception:
            pass

    def query_pin(self, pin_raw):
        if self._query is None or self._query_available is False:
            return None
        try:
            pin_num = self._pin_numbers.get(pin_raw)
            if pin_num is None:
                pin_num = self._resolve_pin_number(pin_raw)
                self._pin_numbers[pin_raw] = pin_num
            res = self._query.send([int(self._query_oid), int(pin_num)])
            if not isinstance(res, dict) or "status" not in res:
                return None
            return int(res["status"])
        except Exception:
            return None


# =====================================================================
# Controller – reine Init-Logik (ohne Klipper importierbar)
# =====================================================================

class K2MotorController:
    """Führt P0–P9 über Rs485Transport + TransparentTransport + GpioOps aus."""

    def __init__(self, cfg, rs485, transparent, gpio, pause=None,
                 logger=None, send_event=None):
        self.cfg = cfg
        self.rs485 = rs485
        self.transparent = transparent
        self.gpio = gpio
        self.pause = pause if pause is not None else time.sleep
        self.log = logger if logger is not None else self._print_log()
        self.send_event = send_event
        self.is_ready = False
        self.pin_levels = {}  # pin_raw -> level (für Diagnose/Tests)
        # Sensorless-Homing-Zustand (KTWO_MOTOR_HOMING / _STALL_MODE):
        # zuletzt per Echo bestätigter Stall-Mode je Motor (None = unbekannt)
        self.stall_mode = {m: None for m in STALL_READ_MOTORS}
        self.homing_active = False  # BEGIN ok, END noch nicht gelaufen
        self.last_error = None
        self._table = default_param_table()
        self._by_name = {p["name"]: p for p in self._table}

    class _print_log:
        def info(self, msg):
            print(msg)
        def error(self, msg):
            print("FEHLER: %s" % msg)

    # --- Helfer -------------------------------------------------------
    def _transport_for(self, motor):
        if motor == "e":
            return self.transparent, TRANSPARENT_ADDR
        num = NUM_BY_NAME_INIT[motor]
        addr = 0x80 + num
        if addr == STOCK_E_ADDR:
            raise RuntimeError("Guard: 0x85 darf nie gesendet werden (Stock-Bug)")
        return self.rs485, addr

    def _timeout_for(self, motor, default):
        if motor == "e":
            return TIMEOUT_TRANSPARENT if default in (TIMEOUT_ADDR,) else default
        return default

    def _transact(self, motor, fun, payload, timeout, retries=0):
        tr, addr = self._transport_for(motor)
        if addr == STOCK_E_ADDR:
            raise RuntimeError("Guard: 0x85 blockiert (Stock-Bug, nutze transparent 0x81)")
        return tr.transact(addr, fun, list(payload), timeout=timeout, retries=retries)

    def _set_strapping(self, phys_map):
        """phys_map: [(pin_raw, level)] – nur mit allow_write."""
        for pin_raw, level in phys_map:
            self.gpio.set_pin(pin_raw, int(level))
            self.pin_levels[pin_raw] = int(level)
        # M400-Äquivalent (Review 2): Queue leeren, bevor Broadcast folgt.
        try:
            self.gpio.sync()
        except Exception:
            pass

    def _query_all_pins(self, pin_list):
        """-> {pin_raw: phys oder None} (None = keine Rücklesung möglich)."""
        out = {}
        for _m, _kind, spec in pin_list:
            try:
                out[spec["raw"]] = self.gpio.query_pin(spec["raw"])
            except Exception:
                out[spec["raw"]] = None
        return out

    def _pin_list(self):
        lst = []
        for m in PIN_MOTORS:
            p = self.cfg["pins"].get(m) or {}
            for kind in ("dir", "step"):
                if p.get(kind):
                    lst.append((m, kind, p[kind]))
        return lst

    def _strapping_rows(self):
        rows = []
        for m in PIN_MOTORS:
            p = self.cfg["pins"].get(m) or {}
            if not p.get("dir") or not p.get("step"):
                continue
            addr = strapping_address(p["dir"]["phys"], p["step"]["phys"])
            exp = 0x80 + NUM_BY_NAME_INIT[m]
            rows.append((m, p["dir"], p["step"], addr, exp, addr == exp))
        return rows

    # --- Plan-Text (Trockenlauf, kein Bus) ------------------------------
    def describe_plan(self):
        cfg = self.cfg
        out = ["K2-Motor-Init-Plan (Trockenlauf, nichts gesendet)"]
        out.append("switch=%d retries=%d closed=%s allow_write=%s" % (
            cfg["switch"], cfg["retries"], ",".join(cfg["motor_closed_loop"]),
            cfg["allow_write"]))
        for m, d, s, addr, exp, ok in self._strapping_rows():
            out.append("Strapping %s DIR %s=%d STEP %s=%d -> 0x%02X (erwartet 0x%02X) %s" % (
                m, d["raw"], d["phys"], s["raw"], s["phys"], addr, exp,
                "ok" if ok else "FALSCH"))
        out.append("P0 transparent-Kanal (Düsen-MCU %s)" % cfg.get("nozzle_mcu"))
        out.append("P1 %d GPIO-Pegel + Rücklesen, 0,2 s" % len(self._pin_list()))
        out.append("P2 Broadcast FF 0E [01] (RS485 + transparent), 0,1 s; "
                   "0E [02] je Motor (E transparent 0x81); max. %d Runden" % cfg["retries"])
        out.append("P3 All motors are normal")
        out.append("P4 Stall-Mode 11 [02] an Z/Z1/E (je <=3)")
        out.append("P5 Schutz 0C [0b] an alle (8 B dekodiert)")
        out.append("P6 Clear 0C [05] unbedingt (live unbeantwortet)")
        out.append("P7 Restore alle Pins phys 0, 0,1 s + Rücklesen, 1,0 s")
        n_r = n_w = 0
        for m in VERIFY_ORDER:
            if not (self.cfg["pins"].get(m) or {}).get("stall"):
                continue
            for p in self._table:
                if not p["configurable"]:
                    continue
                configured = p["name"] in self.cfg["params"].get(m, {})
                if p["id"] not in ALWAYS_READ and not configured:
                    continue
                if p["id"] in HOST_ONLY_IDS:
                    continue
                n_r += 1
                if p["id"] not in ALWAYS_READ and p["value_type"] == "float":
                    n_w += 1
        out.append("P8 Param-Abgleich: %d Lesen, %d bedingte Writes, je Motor 06 [03]; "
                   "E via transparent 0x81 (nie 0x85); ID 226 nie" % (n_r, n_w))
        out.append("P9 is_ready + Event k2_motor:ready")
        if not cfg["allow_write"]:
            out.append("Hinweis: allow_write=False → nur lesen + Plan (keine "
                       "schreibenden Frames: Broadcast/Stall/Clear/Write/Update, kein GPIO-Setzen)")
        return "\n".join(out)

    # --- Hauptlauf ------------------------------------------------------
    def run_init(self, dry_run=False):
        """P0–P9. Rückgabe: dict(status, log[], errors[], ready)."""
        cfg = self.cfg
        logs, errors = [], []
        def info(msg):
            logs.append(("info", msg))
            try:
                self.log.info(msg)
            except Exception:
                pass
        def err(msg):
            errors.append(msg)
            logs.append(("error", msg))
            try:
                self.log.error(msg)
            except Exception:
                pass
        if dry_run or not cfg["allow_write"]:
            info(self.describe_plan())
            if dry_run:
                return {"status": "dry_run", "log": logs, "errors": errors,
                        "ready": False}
            info("allow_write=False: Diagnose-Modus – nur lesende Schritte "
                 "(kein Broadcast/Stall/Clear/Write/Update, kein GPIO-Setzen)")
        # Strapping-Guard (wie motor_init.build_init_plan: falsche Pegel → Abbruch)
        for m, d, s, addr, exp, ok in self._strapping_rows():
            if not ok:
                err("Strapping %s: DIR=%d STEP=%d -> 0x%02X, erwartet 0x%02X" % (
                    m, d["phys"], s["phys"], addr, exp))
                return {"status": "strapping_error", "log": logs, "errors": errors,
                        "ready": False}
        for m in cfg["motor_closed_loop"]:
            if m != "e" and (not (cfg["pins"].get(m) or {}).get("dir")
                             or not (cfg["pins"].get(m) or {}).get("step")):
                err("Motor %s ohne Dir/Step-Pins – keine Strapping-Adresse" % m)
                return {"status": "config_error", "log": logs, "errors": errors,
                        "ready": False}
        if cfg["switch"] == 0:
            info("switch=0: nur Pins normal (physisch 0), danach Ende")
            if cfg["allow_write"]:
                for _m, _kind, spec in self._pin_list():
                    lvl = 1 if spec["invert"] else 0
                    try:
                        self.gpio.set_pin(spec["raw"], lvl)
                        self.pin_levels[spec["raw"]] = lvl
                    except Exception as exc:
                        err("GPIO-Setzen %s: %s" % (spec["raw"], exc))
                try:
                    self.gpio.sync()  # M400-Äquivalent (Review 2)
                except Exception:
                    pass
                self.pause(PAUSE_AFTER_RESTORE)
            info("switch=0 set_pin_io 0")
            self.is_ready = False
            return {"status": "switch0", "log": logs, "errors": errors, "ready": False}

        can_write = bool(cfg["allow_write"] and not dry_run)
        pin_list = self._pin_list()

        # P0
        info("P0 transparent-Kanal: Düsen-MCU %s (Extruder 0x81)" % cfg.get("nozzle_mcu"))

        # P1
        if can_write:
            info("P1 Strapping-Pegel setzen (%d Pins)" % len(pin_list))
            for m, kind, spec in pin_list:
                try:
                    self.gpio.set_pin(spec["raw"], spec["level"])
                    self.pin_levels[spec["raw"]] = spec["level"]
                except Exception as exc:
                    err("GPIO-Setzen %s=%d: %s" % (spec["raw"], spec["level"], exc))
                    return {"status": "gpio_error", "log": logs, "errors": errors,
                            "ready": False}
            try:
                self.gpio.sync()  # M400-Äquivalent (Review 2): erst dann lesen/senden
            except Exception:
                pass
            self.pause(PAUSE_AFTER_GPIO)
            back = self._query_all_pins(pin_list)
            bad = [(spec["raw"], spec["phys"], back.get(spec["raw"]))
                   for _m, _k, spec in pin_list
                   if back.get(spec["raw"]) is not None and back.get(spec["raw"]) != spec["phys"]]
            for attempt in range(GPIO_RESEND_TRIES):
                if not bad:
                    break
                info("P1 Resend %d/%d" % (attempt + 1, GPIO_RESEND_TRIES))
                for raw, _phys, _got in bad:
                    lvl = next(s["level"] for _m, _k, s in pin_list if s["raw"] == raw)
                    try:
                        self.gpio.set_pin(raw, lvl)
                    except Exception:
                        pass
                try:
                    self.gpio.sync()  # M400-Äquivalent (Review 2)
                except Exception:
                    pass
                self.pause(0.2)
                back = self._query_all_pins(pin_list)
                bad = [(s["raw"], s["phys"], back.get(s["raw"]))
                       for _m, _k, s in pin_list
                       if back.get(s["raw"]) is not None and back.get(s["raw"]) != s["phys"]]
            if bad:
                for raw, phys, got in bad:
                    err(ERR_KEY799 % (raw, phys, got, GPIO_RESEND_TRIES, GPIO_RESEND_TRIES))
                self._restore_pins(pin_list, can_write, info, err)
                self._maybe_shutdown(info, err, "key799")
                return {"status": "key799", "log": logs, "errors": errors, "ready": False}
            info("initial set_pin_io done")
        else:
            info("P1 (Diagnose): Pins werden NICHT gesetzt (allow_write=False); "
                 "Rücklesung (falls vorhanden):")
            back = self._query_all_pins(pin_list)
            for _m, _k, spec in pin_list:
                got = back.get(spec["raw"])
                info("  %s erwartet phys %d, gelesen %s" % (
                    spec["raw"], spec["phys"], "–" if got is None else got))

        # P2 Runden
        closed = list(cfg["motor_closed_loop"])
        ok_all = False
        for rnd in range(1, cfg["retries"] + 1):
            if can_write:
                for bus_name in ("ttyS5", "transparent"):
                    try:
                        if bus_name == "ttyS5":
                            self.rs485.transact(BROADCAST_ADDR, FUNC_ADDR, [SUB_ADDR_SET],
                                                timeout=TIMEOUT_ADDR, retries=0)
                        else:
                            self.transparent.transact(BROADCAST_ADDR, FUNC_ADDR, [SUB_ADDR_SET],
                                                      timeout=TIMEOUT_TRANSPARENT, retries=0)
                        info("P2 Runde %d: Broadcast (%s) unerwartet beantwortet" % (rnd, bus_name))
                    except (TimeoutError, ValueError):
                        pass  # erwartet: keine Antwort / leer
                self.pause(PAUSE_AFTER_BROADCAST)
            else:
                info("P2 Runde %d (Diagnose): kein Broadcast (allow_write=False)" % rnd)
            ok, need = set(), set(closed)
            for m in closed:
                self.pause(PAUSE_BEFORE_ADDR)
                try:
                    _st, _fn, data = self._transact(
                        m, FUNC_ADDR, [SUB_ADDR_READ],
                        TIMEOUT_TRANSPARENT if m == "e" else TIMEOUT_ADDR, 0)
                    if len(bytes(data)) == 1:
                        ok.add(m)
                        info("P2 Runde %d: %s antwortet 0x%02X" % (rnd, m, bytes(data)[0]))
                    else:
                        info("P2 Runde %d: %s unerwartete Länge %d" % (rnd, m, len(bytes(data))))
                except (TimeoutError, ValueError) as exc:
                    info("P2 Runde %d: %s Timeout (%s)" % (rnd, m, exc))
            info("broadcast retries:%d" % rnd)
            if ok == need:
                ok_all = True
                if rnd > 1 or can_write:
                    pass
                break
            self.pause(PAUSE_ROUND)
        if not ok_all:
            missing = sorted(set(closed) - ok)
            err(ERR_KEY798 % (cfg["retries"], ",".join(missing) or "?"))
            self._restore_pins(pin_list, can_write, info, err)
            self._maybe_shutdown(info, err, "key798")
            return {"status": "key798", "log": logs, "errors": errors, "ready": False}

        # P3
        info("All motors are normal")

        # P4 Stall-Mode (nur Z/Z1/E, wie Stock; X/Y erst beim Homing)
        if can_write:
            for m in ("z", "z1", "e"):
                if m not in closed:
                    continue
                done = False
                for _t in range(STALL_TRIES):
                    try:
                        _st, _fn, data = self._transact(
                            m, FUNC_SET_MODE, [STALL_MODE_EMERGENCY],
                            TIMEOUT_TRANSPARENT if m == "e" else TIMEOUT_STALL, 0)
                        if bytes(data) == bytes([STALL_MODE_EMERGENCY]):
                            done = True
                            break
                    except (TimeoutError, RuntimeError, ValueError):
                        pass
                    self.pause(PAUSE_STALL_RETRY)
                if not done:
                    if m == "e":
                        err(ERR_KEY802 % STALL_TRIES)
                    else:
                        err(ERR_KEY801 % (m, STALL_TRIES))
                    self._restore_pins(pin_list, can_write, info, err)
                    self._maybe_shutdown(info, err, "key801/802")
                    return {"status": "key801_802", "log": logs, "errors": errors,
                            "ready": False}
                info("stall mode change motor_%s: 0x11 [02] ok" % m)
        else:
            info("P4 (Diagnose): kein Stall-Mode (allow_write=False)")

        # P5 Schutzabfrage (lesend – auch in Diagnose)
        for m in closed:
            try:
                _st, _fn, data = self._transact(m, FUNC_PROTECTION, [SUB_PROT_CHECK],
                                                TIMEOUT_TRANSPARENT if m == "e" else TIMEOUT_PROTECTION, 0)
                try:
                    dec = decode_protection_local(bytes(data))
                    if dec["ok"]:
                        info("P5 %s 0C [0b]: ok (8x00)" % m)
                    else:
                        info("P5 %s 0C [0b]: err=%s warn=%s" % (m, dec["errors"], dec["warnings"]))
                except ValueError as exc:
                    info("P5 %s 0C [0b]: %d B (%s)" % (m, len(bytes(data)), exc))
            except (TimeoutError, ValueError) as exc:
                info("P5 %s 0C [0b]: Timeout (%s)" % (m, exc))

        # P6 Clear (schreibend – nur mit allow_write)
        if can_write:
            for m in closed:
                try:
                    self._transact(m, FUNC_PROTECTION, [SUB_PROT_CLEAR],
                                   TIMEOUT_TRANSPARENT if m == "e" else TIMEOUT_PROTECTION, 0)
                    info("P6 %s 0C [05]: beantwortet (unerwartet, ok)" % m)
                except (TimeoutError, ValueError):
                    info("P6 %s 0C [05]: keine Antwort (live ebenso, ok)" % m)
        else:
            info("P6 (Diagnose): kein Clear 0C [05] (allow_write=False)")

        # P7 Restore (auch im Fehlerfall; Setzen nur mit allow_write)
        self._restore_pins(pin_list, can_write, info, err)

        # P8 Param-Abgleich
        p8 = self._param_phase(closed, can_write, info, err)
        if p8.get("fatal"):
            self._maybe_shutdown(info, err, "param")
            return {"status": p8.get("status", "param_error"), "log": logs,
                    "errors": errors, "ready": False}

        # P9
        self.is_ready = True
        info("is_ready=True; Event k2_motor:ready (CFS-Auto-Addr erst jetzt)")
        try:
            if self.send_event is not None:
                self.send_event("k2_motor:ready")
        except Exception as exc:
            info("Event k2_motor:ready nicht sendbar: %s" % exc)
        return {"status": "ready", "log": logs, "errors": errors, "ready": True}

    def _restore_pins(self, pin_list, can_write, info, err):
        if can_write:
            for m, kind, spec in pin_list:
                lvl = 1 if spec["invert"] else 0
                try:
                    self.gpio.set_pin(spec["raw"], lvl)
                    self.pin_levels[spec["raw"]] = lvl
                except Exception as exc:
                    err("GPIO-Restore %s: %s" % (spec["raw"], exc))
            try:
                self.gpio.sync()  # M400-Äquivalent (Review 2)
            except Exception:
                pass
            self.pause(PAUSE_AFTER_RESTORE)
            back = self._query_all_pins(pin_list)
            for _m, _k, spec in pin_list:
                got = back.get(spec["raw"])
                if got is not None and got != 0:
                    err(ERR_KEY800 % (spec["raw"], got))
            info("restore set_pin_io done")
            self.pause(PAUSE_RESTORE_DONE)
        else:
            info("P7 (Diagnose): kein Restore-Setzen (allow_write=False)")

    def _maybe_shutdown(self, info, err, key):
        if self.cfg.get("shutdown_on_failure"):
            msg = "SHUTDOWN (shutdown_on_failure=True, nach %s)" % key
            info(msg)
            raise RuntimeError(msg)
        info("Kein Shutdown (shutdown_on_failure=False) – nur Fehlermeldung")

    def _param_phase(self, closed, can_write, info, err):
        reads = {}
        for m in VERIFY_ORDER:
            if m not in closed:
                continue
            if not (self.cfg["pins"].get(m) or {}).get("stall"):
                continue
            info("update_flash_param stepper:%d (%s)" % (MOTOR_NAME_TO_NUM[m], m))
            for p in self._table:
                pid = p["id"]
                if not p["configurable"]:
                    continue
                configured = p["name"] in self.cfg["params"].get(m, {})
                if pid not in ALWAYS_READ and not configured:
                    continue
                if pid in HOST_ONLY_IDS:
                    continue  # ID 226: Host-Parameter, nie senden
                vt = p["value_type"] or "float"
                cval = self.cfg["params"][m].get(p["name"])
                # Lesen (≤3)
                raw = None
                for _t in range(PARAM_READ_TRIES):
                    try:
                        _st, _fn, data = self._transact(
                            m, FUNC_SYS_PARAM, [SUB_SYS_READ, pid], TIMEOUT_SYS_PARAM, 0)
                        raw = bytes(data)
                        break
                    except (TimeoutError, ValueError):
                        continue
                if raw is None:
                    info("  %s ID %d %s: result is None (Timeout, weiter)" % (m, pid, p["name"]))
                    reads[(m, pid)] = None
                    continue
                if pid in ALWAYS_READ:
                    info("  %s ID %d %s: read success (%d B)" % (m, pid, p["name"], len(raw)))
                    reads[(m, pid)] = self._decode_param(raw, vt)
                    continue
                val = self._decode_param(raw, vt)
                reads[(m, pid)] = val
                if val is None or isinstance(val, bool) or not isinstance(val, (int, float)):
                    info("  %s ID %d %s: kein Vergleichswert (%r)" % (m, pid, p["name"], val))
                    continue
                if cval is None:
                    continue
                if _isclose(val, cval):
                    continue
                info("  %s ID %d %s is not close: gelesen %r, config %r" % (m, pid, p["name"], val, cval))
                if not can_write:
                    info("    (Diagnose: kein Write, allow_write=False)")
                    continue
                if vt != "float":
                    info("    Schreibformat für %s unbelegt – nicht geschrieben" % vt)
                    continue
                payload = [SUB_SYS_WRITE, pid] + list(_float_to_bytes(cval))
                done = None
                for _t in range(PARAM_WRITE_TRIES):
                    try:
                        _st, _fn, data = self._transact(
                            m, FUNC_SYS_PARAM, payload, TIMEOUT_SYS_PARAM, 0)
                        if bytes(data) == bytes(payload[2:]):
                            done = float(cval)
                            break
                        # Echo prüfen (Antwort = 4 Float-Bytes)
                        try:
                            echo = struct.unpack("<f", bytes(data))[0]
                            if _isclose(echo, float(cval)):
                                done = echo
                                break
                        except Exception:
                            pass
                    except (TimeoutError, RuntimeError, ValueError):
                        continue
                if done is not None:
                    info("    %s ID %d write success (Echo ok)" % (m, pid))
                else:
                    info("    %s ID %d write failed" % (m, pid))
            if can_write:
                try:
                    _st, _fn, data = self._transact(m, FUNC_SYS_PARAM, [SUB_SYS_UPDATE],
                                                    TIMEOUT_SYS_PARAM, 0)
                    info("  %s Updata param 06 [03] ok" % m)
                except (TimeoutError, RuntimeError, ValueError) as exc:
                    info("  %s Updata param 06 [03]: %s (Stock ignoriert Ergebnis)" % (m, exc))
            else:
                info("  %s Updata param 06 [03]: übersprungen (allow_write=False)" % m)
        return {}

    def _decode_param(self, raw, vt):
        raw = bytes(raw)
        if len(raw) == 0:
            return "leer"
        fmt = _TYPE_FORMATS.get(vt or "float", "<f")
        if struct.calcsize(fmt) != len(raw):
            return raw.hex()
        return struct.unpack(fmt, raw)[0]

    # --- Status (nur lesend) ------------------------------------------------
    def motor_status(self):
        """Je Motor: Version 0x0F, Schutz 0x0C [0b] dekodiert, Feedback 0x08 idx 5/6."""
        out = {}
        for m in self.cfg["motor_closed_loop"]:
            entry = {"motor": m}
            try:
                _st, _fn, data = self._transact(m, FUNC_VERSION, [SUB_VERSION_GET],
                                                TIMEOUT_VERSION, 0)
                try:
                    entry["version"] = decode_version_local(bytes(data))
                except ValueError as exc:
                    entry["version_error"] = str(exc)
            except Exception as exc:
                entry["version_error"] = "%s" % exc
            try:
                _st, _fn, data = self._transact(m, FUNC_PROTECTION, [SUB_PROT_CHECK],
                                                TIMEOUT_PROTECTION, 0)
                try:
                    entry["protection"] = decode_protection_local(bytes(data))
                except ValueError as exc:
                    entry["protection_error"] = str(exc)
            except Exception as exc:
                entry["protection_error"] = "%s" % exc
            for idx, key in ((5, "pos_fdb"), (6, "spd_fdb")):
                try:
                    _st, _fn, data = self._transact(m, FUNC_VARIABLE, [idx],
                                                    TIMEOUT_VARIABLE, 0)
                    raw = bytes(data)
                    if len(raw) != 4:
                        entry[key + "_error"] = "%d statt 4 Bytes" % len(raw)
                    else:
                        entry[key] = struct.unpack("<f", raw)[0]
                except Exception as exc:
                    entry[key + "_error"] = "%s" % exc
            out[m] = entry
        return out

    # --- Sensorless-Homing X/Y (Stock sensorless.cfg, Belege s. Konstanten) --
    # Alle schreibenden Methoden prüfen allow_write ZUERST (K2MotorError ohne
    # Bus-Zugriff). Bus-/Port-Fehler (RuntimeError aus [k2_485], TimeoutError,
    # ValueError bei Absender/fun-Mismatch, OSError) werden als K2MotorError
    # gemeldet – die Klipper-Hülle macht daraus G-Code-Fehler (kein Shutdown).

    def _require_write(self, what):
        if not self.cfg.get("allow_write"):
            raise K2MotorError(
                "%s gesperrt: [k2_motor] allow_write=False – nichts gesendet "
                "(erst nach KTWO_MOTOR_INIT-Prüfung allow_write: True setzen)" % what)

    def _bus_call(self, motor, fun, payload, timeout, what):
        """Ein Frame, retries=0 wie Stock; Fehler -> K2MotorError."""
        try:
            return self._transact(motor, fun, payload, timeout, 0)
        except K2MotorError:
            raise
        except (RuntimeError, TimeoutError, ValueError, OSError) as exc:
            raise K2MotorError("%s: %s" % (what, exc))

    def set_stall_mode(self, motors, mode):
        """0x11 [mode] an ``motors`` (Reihenfolge wie übergeben, Stock x→y).

        Nur Modus 1 (Endschalter, vor dem Homing) und 2 (Notfall-Schutz,
        danach); nur x/y (Z/Z1 setzt der Init in P4). Antwort = Echo [mode]
        (live ``f7 81 04 00 11 01 1d``), sonst K2MotorError. Bricht beim
        ersten Fehler ab; ``self.stall_mode`` zeigt, was bestätigt ist.
        Rückgabe: Logzeilen (Stock-Wortlaut "stall mode change motor81")."""
        if isinstance(mode, bool) or mode not in STALL_MODES_SETTABLE:
            raise K2MotorError("Stall-Mode muss 1 (Endschalter) oder 2 (Notfall-"
                               "Schutz) sein: %r" % (mode,))
        motors = list(motors)
        for m in motors:
            if m not in STALL_SET_MOTORS:
                raise K2MotorError("Stall-Mode setzen nur für x/y (Stock); %r "
                                   "abgelehnt" % (m,))
        self._require_write("Stall-Mode setzen (0x11)")
        out = []
        for m in motors:
            _tr, addr = self._transport_for(m)
            try:
                _st, _fn, data = self._bus_call(
                    m, FUNC_SET_MODE, [mode], TIMEOUT_STALL,
                    "Stall-Mode %s (0x%02X) 0x11 [%02x]" % (m, addr, mode))
            except K2MotorError:
                self.stall_mode[m] = None  # Frame evtl. angekommen -> unbekannt
                raise
            if bytes(data) != bytes([mode]):
                self.stall_mode[m] = None
                raise K2MotorError("Stall-Mode %s (0x%02X): Echo %s statt %02x"
                                   % (m, addr, bytes(data).hex() or "leer", mode))
            self.stall_mode[m] = mode
            out.append("stall mode change motor%02x: send:%02x recv:%02x"
                       % (addr, mode, mode))
        return out

    def get_stall_mode(self, motor):
        """0x11 [04] -> int (Antwort [mode, 00], klippy.log.2026-10-12:31419). LESEND."""
        if motor not in STALL_READ_MOTORS:
            raise K2MotorError("Stall-Mode lesen nur für x/y/z/z1: %r" % (motor,))
        _tr, addr = self._transport_for(motor)
        _st, _fn, data = self._bus_call(motor, FUNC_SET_MODE, [SUB_STALL_GET],
                                        TIMEOUT_STALL,
                                        "Stall-Mode lesen %s (0x%02X)" % (motor, addr))
        raw = bytes(data)
        if len(raw) != 2:
            raise K2MotorError("Stall-Mode lesen %s: %d statt 2 Bytes (%s)"
                               % (motor, len(raw), raw.hex()))
        self.stall_mode[motor] = raw[0]
        return raw[0]

    def read_sys_param(self, motor, pid):
        """0x06 [02, id] -> Wert (float bzw. Typ laut Tabelle). LESEND."""
        if motor not in SYS_PARAM_MOTORS:
            raise K2MotorError("Sys-Param nur für x/y/z/z1: %r" % (motor,))
        if isinstance(pid, bool) or not isinstance(pid, int) or not 0 <= pid < len(self._table):
            raise K2MotorError("Param-ID muss 0..%d sein: %r" % (len(self._table) - 1, pid))
        if pid in HOST_ONLY_IDS:
            raise K2MotorError("ID %d ist ein reiner Host-Parameter (Motor antwortet 0 B)" % pid)
        p = self._table[pid]
        _st, _fn, data = self._bus_call(motor, FUNC_SYS_PARAM, [SUB_SYS_READ, pid],
                                        TIMEOUT_SYS_PARAM,
                                        "Sys-Param lesen %s ID %d" % (motor, pid))
        return self._decode_param(bytes(data), p["value_type"] or "float")

    def write_sys_param(self, motor, pid, value):
        """0x06 [01, id] + <f – FLÜCHTIG (bis Kaltstart), KEIN 06 [03] (wie
        Stock beim Homing). Nur IDs aus SYS_PARAM_WRITABLE_IDS, Echo = die 4
        Float-Bytes (live ``f7 81 07 00 06 00 00 96 43 09``). -> float32-Wert."""
        if motor not in SYS_PARAM_MOTORS:
            raise K2MotorError("Sys-Param schreiben nur für x/y/z/z1: %r" % (motor,))
        if isinstance(pid, bool) or pid not in SYS_PARAM_WRITABLE_IDS:
            raise K2MotorError("ID %r nicht freigegeben (schreibbar nur %s)"
                               % (pid, ",".join(str(i) for i in SYS_PARAM_WRITABLE_IDS)))
        try:
            raw = _float_to_bytes(value)
        except ValueError as exc:
            raise K2MotorError("Sys-Param ID %d: %s" % (pid, exc))
        f32 = struct.unpack("<f", raw)[0]
        if not math.isfinite(f32) or (value != 0 and f32 == 0.0):
            raise K2MotorError("Sys-Param ID %d: %r nicht als float32 darstellbar" % (pid, value))
        self._require_write("Sys-Param schreiben (0x06 [01])")
        _st, _fn, data = self._bus_call(motor, FUNC_SYS_PARAM,
                                        [SUB_SYS_WRITE, pid] + list(raw),
                                        TIMEOUT_SYS_PARAM,
                                        "Sys-Param schreiben %s ID %d" % (motor, pid))
        if bytes(data) != raw:
            raise K2MotorError("Sys-Param %s ID %d: Echo %s statt %s"
                               % (motor, pid, bytes(data).hex() or "leer", raw.hex()))
        return f32

    def check_protection(self, motors=None):
        """0C [0b] an alle (Stock MOTOR_CHECK_PROTECTION_AFTER_HOME DATA=11,
        E transparent). -> {motor: dict|str}; dict = decode_protection_local,
        str = Fehlertext (Timeout/Port). Seiteneffekt laut FW-Befund: bei
        Stall-Mode 2 quittiert [0b] die Notfall-Leitung (Codes bleiben) – so
        auch Stock direkt nach DATA=2."""
        out = {}
        for m in (motors if motors is not None else self.cfg["motor_closed_loop"]):
            try:
                _st, _fn, data = self._transact(
                    m, FUNC_PROTECTION, [SUB_PROT_CHECK],
                    TIMEOUT_TRANSPARENT if m == "e" else TIMEOUT_PROTECTION, 0)
                out[m] = decode_protection_local(bytes(data))
            except (RuntimeError, TimeoutError, ValueError, OSError) as exc:
                out[m] = "%s" % exc
        return out

    def _restore_kp_value(self):
        return float(self.cfg["params"].get("x", {}).get(
            "controller_pos_loop_pid_param_kp", HOMING_POS_KP_RESTORE_DEFAULT))

    def homing_begin(self):
        """Stock-Folge VOR dem X/Y-Homing: 0x11 [01] an x, y; dann ID 70 an x
        = homing_pos_kp (300). Fehler -> Rückbau (best effort: Stall-Mode 2,
        Kp zurück) und K2MotorError, damit das Homing OHNE Bewegung abbricht."""
        self._require_write("Homing-Vorbereitung (Stall-Mode 1 + Kp)")
        log = []
        self.homing_active = False
        kp = float(self.cfg.get("homing_pos_kp", HOMING_POS_KP_DEFAULT))
        stage = "stall"
        try:
            log.extend(self.set_stall_mode(STALL_SET_MOTORS, STALL_MODE_ENDSTOP))
            stage = "kp"
            for m in HOMING_PARAM_MOTORS:
                v = self.write_sys_param(m, HOMING_PARAM_ID, kp)
                log.append("sys param %s ID %d (pos_kp) = %g (Echo ok)"
                           % (m, HOMING_PARAM_ID, v))
        except K2MotorError as exc:
            rb = self._rollback(stage)
            self.last_error = "Homing-Vorbereitung: %s" % exc
            raise K2MotorError("Homing-Vorbereitung fehlgeschlagen: %s | Rückbau: %s"
                               " – keine Bewegung" % (exc, "; ".join(rb) or "nichts nötig"))
        self.homing_active = True
        self.last_error = None
        return log

    def _rollback(self, stage):
        """Best effort nach Fehler in homing_begin (Fehler nur gemeldet)."""
        out = []
        for m in STALL_SET_MOTORS:  # beide, auch den fehlgeschlagenen (Zustand unbekannt)
            try:
                self.set_stall_mode([m], STALL_MODE_EMERGENCY)
                out.append("%s Stall-Mode 2 ok" % m)
            except K2MotorError as exc:
                out.append("%s Stall-Mode 2: %s" % (m, exc))
        if stage == "kp":
            for m in HOMING_PARAM_MOTORS:
                try:
                    self.write_sys_param(m, HOMING_PARAM_ID, self._restore_kp_value())
                    out.append("%s Kp zurück ok" % m)
                except K2MotorError as exc:
                    out.append("%s Kp zurück: %s" % (m, exc))
        return out

    def homing_end(self, protection=True):
        """Stock-Folge NACH dem Homing: 0x11 [02] an x, y; 0C [0b] an alle
        (inkl. E transparent); ID 70 an x = Config-Kp (100). Läuft IMMER
        komplett durch (best effort), Rückgabe (log, errors). Schutzbits
        gesetzt oder Stall/Kp fehlgeschlagen -> errors; Timeout der
        Schutzabfrage nur als Hinweis im Log (lesend, Stock wertet es nicht)."""
        self._require_write("Homing-Abschluss (Stall-Mode 2 + Kp)")
        self.homing_active = False
        log, errors = [], []
        for m in STALL_SET_MOTORS:
            try:
                log.extend(self.set_stall_mode([m], STALL_MODE_EMERGENCY))
            except K2MotorError as exc:
                errors.append(str(exc))
        if protection:
            for m, res in self.check_protection().items():
                if isinstance(res, dict):
                    if res["ok"]:
                        log.append("Schutz %s 0C [0b]: ok (8x00)" % m)
                    else:
                        errors.append("Schutz %s: err=%s warn=%s" % (
                            m, [k for _b, k, _t in res["errors"]],
                            [k for _b, k, _t in res["warnings"]]))
                else:
                    log.append("Schutz %s 0C [0b]: keine Antwort (%s)" % (m, res))
        kp = self._restore_kp_value()
        for m in HOMING_PARAM_MOTORS:
            try:
                v = self.write_sys_param(m, HOMING_PARAM_ID, kp)
                log.append("sys param %s ID %d (pos_kp) = %g (Echo ok)"
                           % (m, HOMING_PARAM_ID, v))
            except K2MotorError as exc:
                errors.append(str(exc))
        self.last_error = "; ".join(errors) if errors else None
        return log, errors


class K2MotorError(RuntimeError):
    """Fehler der Homing-/Stall-/Param-Befehle (-> G-Code-Fehler, kein Shutdown)."""


# =====================================================================
# Klipper-Hülle [k2_motor] (ANNAHMEN zur Upstream-API, s. BERICHT.md)
# =====================================================================

class PrinterK2Motor:
    """Klipper-Objekt [k2_motor] – verdrahtet Controller mit Klipper."""

    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        try:
            self.cfg = parse_klipper_config(config)
        except Exception as exc:
            raise config.error("[k2_motor] Config-Fehler: %s" % exc)
        self.main_mcu_name = self.cfg.get("main_mcu", "mcu")
        self.nozzle_mcu_name = self.cfg.get("nozzle_mcu", "nozzle_mcu")
        # MCU-Namen robust auflösen (Fix 16.10.2026): 'nozzle_mcu' ->
        # Upstream-Objekt 'mcu nozzle_mcu'; voller Name geht weiterhin.
        try:
            self.mcu = _lookup_mcu(self.printer, self.main_mcu_name)
            self.nozzle_mcu = _lookup_mcu(self.printer, self.nozzle_mcu_name)
        except Exception as exc:
            raise config.error("[k2_motor] MCU nicht gefunden (main_mcu=%r, "
                               "nozzle_mcu=%r): %s" % (self.main_mcu_name,
                                                       self.nozzle_mcu_name, exc))
        # REVIEW 1 (FIX): Alles Konfigurierende VOR klippy:connect.
        # - Strapping-Pins (motor_<m>_dir/_step + Enable) sofort per
        #   pins.setup_pin anlegen (KlippyGpio-Konstruktor). Danach nur setzen.
        # - Transparent-OID früh per create_oid + register_config_callback
        #   (config_transparent läuft im Config-Callback vor finalize, wie
        #   Stock-_build_config @0xa04fc). Lookup erst nach connect.
        self._gpio = KlippyGpio(
            self.printer, self.main_mcu_name,
            pin_specs=self._strapping_pin_specs())
        self._oid = self.nozzle_mcu.create_oid()
        self.nozzle_mcu.register_config_callback(self._build_transparent_config)
        self._tadapter = KlippyTransparentAdapter(self.nozzle_mcu, self._oid)
        self._transparent = None
        # RS485-Bus X/Y/Z/Z1 = gemeinsames Objekt 'k2_485' (Modul k2_485.py,
        # EIN Port/Lock für Motoren + CFS + Belt). Steht [k2_485] in der Config,
        # wird es hier sofort geladen (printer.load_object) – die Reihenfolge
        # der Abschnitte ist damit egal; sonst Suche beim Connect.
        self._bus = None
        self._rs485 = None
        try:
            if hasattr(config, "has_section") and config.has_section(BUS_SECTION):
                self._bus = self.printer.load_object(config, BUS_SECTION)
        except Exception as exc:
            raise config.error("[k2_motor] [%s] nicht ladbar: %s" % (BUS_SECTION, exc))
        self.controller = None
        self.printer.register_event_handler("klippy:connect", self.handle_connect)
        # G-Code-Namen: Upstream verbietet Buchstabe+Ziffer am Anfang
        # (gcode.py register_command) -> KTWO_ statt K2_ (Fix 16.10.2026).
        self.gcode.register_command("KTWO_MOTOR_INIT", self.cmd_init,
                                    desc="K2-Motor-Init (DRY_RUN=1 nur Plan)")
        self.gcode.register_command("KTWO_MOTOR_STATUS", self.cmd_status,
                                    desc="K2-Motor-Status (Version/Schutz/Feedback)")
        # Sensorless-Homing X/Y (Stock MOTOR_STALL_MODE / MOTOR_SYS_PARAM /
        # MOTOR_CHECK_PROTECTION_AFTER_HOME) – Namen ohne Ziffern (Upstream-
        # Parser trennt an Ziffern).
        self.gcode.register_command("KTWO_MOTOR_STALL_MODE", self.cmd_stall_mode,
                                    desc="Stall-Mode X/Y lesen bzw. setzen (DATA=1|2)")
        self.gcode.register_command("KTWO_MOTOR_SYS_PARAM", self.cmd_sys_param,
                                    desc="Motor-Sys-Param lesen bzw. flüchtig schreiben")
        self.gcode.register_command("KTWO_MOTOR_PROTECTION", self.cmd_protection,
                                    desc="Schutzabfrage 0C [0b] an alle Motoren")
        self.gcode.register_command("KTWO_MOTOR_HOMING", self.cmd_homing,
                                    desc="Stock-Folge vor/nach X/Y-Homing (PHASE=BEGIN|END)")
        # Auto-Rückbau: bricht ein G-Code (z.B. G28 Y "No trigger") nach
        # PHASE=BEGIN ab, setzt das Modul X/Y zurück (Stall-Mode 2, Kp).
        self.printer.register_event_handler("gcode:command_error",
                                            self._handle_command_error)

    def _strapping_pin_specs(self):
        """Alle früh anzulegenden Pins: Dir/Step (x/y/z/z1) + Enable."""
        specs = []
        for m in PIN_MOTORS:
            for kind in ("dir", "step"):
                spec = (self.cfg.get("pins", {}).get(m) or {}).get(kind)
                if spec and spec.get("raw") and spec["raw"] not in specs:
                    specs.append(spec["raw"])
        if ENABLE_PIN_RAW not in specs:
            specs.append(ENABLE_PIN_RAW)
        return specs

    def _build_transparent_config(self):
        # Config-Callback (vor finalize): Düsen-Kaskade anmelden. Upstream ruft
        # Config-Callbacks OHNE Argument auf (mcu.py _send_config: cb()).
        # Creality-MCU-FW kennt config_transparent (Dictionary-ID 74).
        self.nozzle_mcu.add_config_cmd("config_transparent oid=%d" % self._oid)

    def _ensure_transports(self):
        """RS485-Transport für X/Y/Z/Z1 aus dem Objekt 'k2_485' bauen.

        Klare Fehlermeldung, wenn [k2_485] fehlt. Ob der Port wirklich offen
        ist, zeigt sich erst beim ersten Frame (k2_485 öffnet faul/beim
        Connect und meldet Port-Fehler selbst)."""
        if self._rs485 is not None:
            return self._rs485
        bus = self._bus
        if bus is None:
            try:
                bus = self.printer.lookup_object(BUS_SECTION)
            except Exception:
                bus = None
        if bus is None:
            raise RuntimeError(
                "[k2_motor] RS485-Bus fehlt: Abschnitt [%s] (serial: <by-id-Pfad>, "
                "baud: 230400) in printer.cfg anlegen – ohne ihn sind X/Y/Z/Z1 "
                "nicht erreichbar (nur KTWO_MOTOR_INIT DRY_RUN=1 geht)" % BUS_SECTION)
        self._bus = bus
        self._rs485 = Rs485Transport(bus, name=BUS_SECTION)
        return self._rs485

    def _ensure_controller(self):
        """Controller ohne Init-Lauf bauen (für lesende Befehle)."""
        if self.controller is None:
            self._ensure_transports()
            if self._transparent is None:
                self._transparent = TransparentTransport(self._tadapter, oid=self._oid)
            self.controller = K2MotorController(
                self.cfg, self._rs485, self._transparent, self._gpio,
                pause=reactor_sleep(self.reactor), logger=self.gcode,
                send_event=getattr(self.printer, "send_event", None))
        return self.controller

    def handle_connect(self):
        # Nur noch Lookups nach dem Connect (Stock L3089: transparent-Lookup
        # erst hier möglich; GPIO-Query ebenso optional). Config-Befehle
        # liefen bereits im Config-Callback (Review 1).
        try:
            self._tadapter.setup()
        except Exception as exc:
            self.gcode.respond_info("[k2_motor] transparent-Setup: %s" % exc)
            return
        try:
            self._gpio.handle_connect()
        except Exception as exc:
            self.gcode.respond_info("[k2_motor] gpio-query optional: %s" % exc)
        if self.cfg.get("auto_init"):
            try:
                self._ensure_transports()
            except Exception as exc:
                self.gcode.respond_info("[k2_motor] Auto-Init übersprungen: %s" % exc)
                return
            try:
                self._run(auto=True)
            except (RuntimeError, OSError) as exc:
                self.gcode.respond_info("[k2_motor] Auto-Init abgebrochen: %s" % exc)

    def _run(self, auto=False):
        self._ensure_transports()
        if self._transparent is None:
            self._transparent = TransparentTransport(self._tadapter, oid=self._oid)
        pause = reactor_sleep(self.reactor)  # relative Sekunden -> absolute Weckzeit
        send_event = getattr(self.printer, "send_event", None)
        old = self.controller
        self.controller = K2MotorController(
            self.cfg, self._rs485, self._transparent, self._gpio,
            pause=pause, logger=self.gcode, send_event=send_event)
        if old is not None:  # Homing-/Stall-Zustand nicht verlieren
            self.controller.stall_mode.update(old.stall_mode)
            self.controller.homing_active = old.homing_active
        res = self.controller.run_init()
        for _lvl, msg in res.get("log", []):
            try:
                self.gcode.respond_info("[k2_motor] %s" % msg)
            except Exception:
                pass
        return res

    def cmd_init(self, gcmd):
        dry = bool(gcmd.get_int("DRY_RUN", 0))
        allow = bool(gcmd.get_int("ALLOW_WRITE", 1 if self.cfg.get("allow_write") else 0))
        if dry:
            ctl = K2MotorController(self.cfg, self._rs485, self._transparent,
                                    self._gpio, pause=lambda s: None, logger=self.gcode)
            gcmd.respond_info(ctl.describe_plan())
            return
        if not allow:
            ctl = K2MotorController(self.cfg, self._rs485, self._transparent,
                                    self._gpio, pause=lambda s: None, logger=self.gcode)
            gcmd.respond_info(ctl.describe_plan())
            gcmd.respond_info("ALLOW_WRITE=0 (allow_write=False): nur gelesen, nichts geschrieben.")
            return
        try:
            self._ensure_transports()
            res = self._run()
        except (RuntimeError, OSError) as exc:
            # Bus/Port-Fehler ([k2_485]) als G-Code-Fehler, kein Shutdown.
            msg = str(exc)
            raise gcmd.error(msg if msg.startswith("[") else "[k2_motor] " + msg)
        gcmd.respond_info("[k2_motor] Status: %s (Fehler: %d)" % (
            res.get("status"), len(res.get("errors", []))))

    def cmd_status(self, gcmd):
        try:
            ctl = self._ensure_controller()
        except RuntimeError as exc:
            raise gcmd.error(str(exc))
        st = ctl.motor_status()
        for m, e in st.items():
            ver = e.get("version", e.get("version_error"))
            pro = e.get("protection", e.get("protection_error"))
            gcmd.respond_info("[k2_motor] %s: version=%s schutz=%s pos_fdb=%s spd_fdb=%s" % (
                m, ver, pro, e.get("pos_fdb", e.get("pos_fdb_error")),
                e.get("spd_fdb", e.get("spd_fdb_error"))))


    # --- Sensorless-Homing X/Y ------------------------------------------
    def _motors_arg(self, gcmd, default, allowed):
        raw = str(gcmd.get("MOTOR", default)).strip().lower()
        motors = ["x", "y"] if raw == "xy" else [s.strip() for s in raw.split(",") if s.strip()]
        for m in motors:
            if m not in allowed:
                raise gcmd.error("[k2_motor] MOTOR=%s ungültig (erlaubt: %s)"
                                 % (raw, ", ".join(allowed + (("xy",) if "y" in allowed else ()))))
        return motors

    def _wait_moves(self):
        """Bewegungen abwarten, bevor der Motor-Modus wechselt (Sicherheit;
        Stock-Wrapper wartet vermutlich nicht – ANNAHME, s. NOTIZ-stall.md)."""
        try:
            self.printer.lookup_object("toolhead").wait_moves()
        except Exception:
            pass

    def _ctl_or_error(self, gcmd):
        try:
            return self._ensure_controller()
        except RuntimeError as exc:
            msg = str(exc)
            raise gcmd.error(msg if msg.startswith("[") else "[k2_motor] " + msg)

    def cmd_stall_mode(self, gcmd):
        """KTWO_MOTOR_STALL_MODE [MOTOR=x|y|xy|z|z1] [DATA=1|2]
        Ohne DATA: lesen (0x11 [04]). Mit DATA: setzen (nur x/y, allow_write)."""
        data = gcmd.get_int("DATA", None)
        if data is None:
            ctl = self._ctl_or_error(gcmd)
            for m in self._motors_arg(gcmd, "xy", STALL_READ_MOTORS):
                try:
                    gcmd.respond_info("[k2_motor] %s: stall_mode=%d" % (m, ctl.get_stall_mode(m)))
                except K2MotorError as exc:
                    raise gcmd.error("[k2_motor] %s" % exc)
            return
        motors = self._motors_arg(gcmd, "xy", STALL_SET_MOTORS)
        if data not in STALL_MODES_SETTABLE:
            raise gcmd.error("[k2_motor] DATA=%d ungültig (1 = Endschalter, 2 = Notfall-Schutz)" % data)
        if not self.cfg.get("allow_write"):
            raise gcmd.error("[k2_motor] Stall-Mode setzen gesperrt: allow_write=False – nichts gesendet")
        ctl = self._ctl_or_error(gcmd)
        self._wait_moves()
        try:
            for line in ctl.set_stall_mode(motors, data):
                gcmd.respond_info("[k2_motor] %s" % line)
        except K2MotorError as exc:
            raise gcmd.error("[k2_motor] %s" % exc)

    def cmd_sys_param(self, gcmd):
        """KTWO_MOTOR_SYS_PARAM MOTOR=x ID=70 [VALUE=300]
        Ohne VALUE: lesen (0x06 [02,id]). Mit VALUE: flüchtig schreiben
        (0x06 [01,id]+<f, nur Stock-IDs, allow_write; kein 06 [03])."""
        motors = self._motors_arg(gcmd, "x", SYS_PARAM_MOTORS)
        pid = gcmd.get_int("ID", minval=0, maxval=len(_SYS_PARAM_NAMES) - 1)
        value = gcmd.get_float("VALUE", None)
        if value is not None and not self.cfg.get("allow_write"):
            raise gcmd.error("[k2_motor] Sys-Param schreiben gesperrt: allow_write=False – nichts gesendet")
        ctl = self._ctl_or_error(gcmd)
        name = _SYS_PARAM_NAMES[pid]
        try:
            for m in motors:
                if value is None:
                    gcmd.respond_info("[k2_motor] %s ID %d %s = %r" % (
                        m, pid, name, ctl.read_sys_param(m, pid)))
                else:
                    self._wait_moves()
                    v = ctl.write_sys_param(m, pid, value)
                    gcmd.respond_info("[k2_motor] %s ID %d %s = %g geschrieben (Echo ok, "
                                      "flüchtig)" % (m, pid, name, v))
        except K2MotorError as exc:
            raise gcmd.error("[k2_motor] %s" % exc)

    def cmd_protection(self, gcmd):
        """KTWO_MOTOR_PROTECTION [STRICT=1] – 0C [0b] an alle (lesend; bei
        Stall-Mode 2 quittiert die FW damit die Notfall-Leitung)."""
        strict = bool(gcmd.get_int("STRICT", 0))
        ctl = self._ctl_or_error(gcmd)
        bad = []
        for m, res in ctl.check_protection().items():
            if isinstance(res, dict):
                txt = "ok" if res["ok"] else "err=%s warn=%s" % (
                    [k for _b, k, _t in res["errors"]], [k for _b, k, _t in res["warnings"]])
                if not res["ok"]:
                    bad.append(m)
            else:
                txt = "keine Antwort (%s)" % res
                if strict:
                    bad.append(m)
            gcmd.respond_info("[k2_motor] Schutz %s: %s" % (m, txt))
        if strict and bad:
            raise gcmd.error("[k2_motor] Schutzabfrage: Problem bei %s" % ", ".join(bad))

    def cmd_homing(self, gcmd):
        """KTWO_MOTOR_HOMING PHASE=BEGIN|END – Stock-Folge rund um G28 X/Y."""
        phase = str(gcmd.get("PHASE")).strip().upper()
        if phase not in ("BEGIN", "END"):
            raise gcmd.error("[k2_motor] PHASE muss BEGIN oder END sein: %r" % phase)
        if not self.cfg.get("allow_write"):
            raise gcmd.error("[k2_motor] Homing-%s gesperrt: allow_write=False – nichts "
                             "gesendet, keine Bewegung" % phase)
        ctl = self._ctl_or_error(gcmd)
        self._wait_moves()
        if phase == "BEGIN":
            try:
                log = ctl.homing_begin()
            except K2MotorError as exc:
                raise gcmd.error("[k2_motor] %s" % exc)
            for line in log:
                gcmd.respond_info("[k2_motor] %s" % line)
            return
        log, errors = ctl.homing_end()
        for line in log:
            gcmd.respond_info("[k2_motor] %s" % line)
        if errors:
            raise gcmd.error("[k2_motor] Homing-Abschluss mit Fehlern: %s" % " | ".join(errors))

    def _handle_command_error(self):
        """gcode:command_error – lief BEGIN, aber END nicht (Homing-Abbruch),
        X/Y zurücksetzen (Stall-Mode 2, Kp). Fehler nur melden."""
        ctl = self.controller
        if ctl is None or not ctl.homing_active:
            return
        try:
            log, errors = ctl.homing_end(protection=False)
        except Exception as exc:  # nie aus dem Fehlerpfad heraus werfen
            log, errors = [], [str(exc)]
        try:
            self.gcode.respond_info(
                "[k2_motor] Homing abgebrochen – Rückbau X/Y: %s%s" % (
                    "; ".join(log) or "-",
                    (" | FEHLER: " + " | ".join(errors)) if errors else ""))
        except Exception:
            pass

    def get_status(self, eventtime=None):
        ctl = self.controller
        return {
            "allow_write": bool(self.cfg.get("allow_write")),
            "homing_pos_kp": float(self.cfg.get("homing_pos_kp", HOMING_POS_KP_DEFAULT)),
            "homing_active": bool(ctl.homing_active) if ctl is not None else False,
            "stall_mode": dict(ctl.stall_mode) if ctl is not None
            else {m: None for m in STALL_READ_MOTORS},
            "last_error": ctl.last_error if ctl is not None else None,
        }


def load_config(config):
    """Klipper-Einstieg [k2_motor] (Upstream-Muster)."""
    return PrinterK2Motor(config)
