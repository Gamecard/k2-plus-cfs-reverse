#!/usr/bin/env python3
"""motor.py – Python-Nachbau von motor_control (K2 Closed-loop-Motoren, erweitert).

Ersetzt (teilweise): motor_control_wrapper.cpython-39.so (armhf, proprietär).
Erweiterung von motor_mini.py (motor_mini.py bleibt eigenständig, gleiche
Sperren, gleiche Retry-/Timeout-Regeln).

LIVE-STAND 15.10.2026 (Auswertung live-logs-2026-10-15/klippy.log*, lokal, nicht im Repo, 6 Logs,
10 Boot-Enumerationen; Zeilenangaben "Datei:Zeile" beziehen sich auf diesen
Ordner, "klippy.log" = Boot 16.10. 01:15 Druckerzeit).

Umfang:
- LESEND (live belegt, senden erlaubt):
  read_sys_param / read_sys_param_raw (0x06 [02, id]),
  check_protection (0x0C [0b]), get_stall_mode (0x11 [04]),
  read_485_addr (0x0E [02]), read_uuid (0x12 [02])
- SETZEN (live belegt, sendet): set_mode (0x11 [01]/[02]/[03], Antwort = Echo)
- SCHREIBEN (live belegt, aber GESCHÜTZT): write_sys_param (0x06 [01, id]+<f)
  und update_sys_params (0x06 [03]) senden NUR mit confirm=True, sonst
  PermissionError ohne Bus-Zugriff. Sie verändern Motorregelparameter
  (Kp/Ki/Schutzgrenzen) – FLÜCHTIG bis zum nächsten Kaltstart.
- Reine Builder (ohne Senden): build_sys_param_read_payload,
  build_sys_param_write_payload, build_sys_param_update_payload
- decode_sys_param_float: Dekodier-Hilfe (<f, ohne Bus)
- read_encoder: GESPERRT (0x08 "get feedback" nie beobachtet)
- query_version + Update-Sequenz-Builder (Func 0xF0, aus mcu_util_485-
  Decompile; auf dem Motorbus live NIE beobachtet – unverändert)

Adressen / Pfade (live):
- X/Y/Z/Z1 = 0x81/0x82/0x83/0x84, AUSSCHLIESSLICH über serial_485
  (/dev/ttyS5, 230400, geteilt mit CFS 0x01 und Belt 0x21/0x22) – Guard
  MOTOR_ADDRS bleibt 0x81-0x84.
- EXTRUDER: NICHT 0x85 über RS485 – 0x85 antwortet dort nie (100 Sendungen,
  alle Timeout ~1 s, klippy.log.2026-10-05:4013-4280; Stock-Fehlgriff
  "stepper 5 -> 0x85"). Der Extruder-Motor antwortet nur über
  `transparent_send` der (vermutlich) Nozzle-MCU unter Adresse 0x81
  (klippy.log:2375/2376 `f7 81 04 00 0e 02 80` -> `f7 81 04 00 0e 81 00`
  "get motor extruder addr: 0x81"). Dafür braucht es einen eigenen
  Transport (Creality-MCU-FW mit config_transparent) – NICHT in diesem
  Modul; ein Motor(bus, 0x81) auf ttyS5 ist die X-Achse.

Retries/Timeouts (live, klippy.log* "retries = N, cmd = ..." + Zeitabstand
bis "get response or timeout"): ALLE ~2200 Motor-Sendungen laufen mit
"retries = 0" (genau 1 Sendung, nie ein Retry) -> MOTOR_RETRIES = 0.
Gemessene Timeouts je Func: 0x06 ~1.0 s (0x85: 1.002-1.009 s, 100x),
0x12 ~1.0 s (1.002-1.006 s, 19x), 0x0E ~0.05 s (0.052-0.061 s, 25x),
0x0C ~0.05 s ([05]: 0.052-0.066 s, 50x), 0x05 ~0.05 s. 0x11 lief nie in
einen Timeout -> Wert UNBELEGT, hier 1.0 s (serial485 TIMEOUT_LONG).
Antwortzeiten aller Motoren sonst <= 0.11 s (meist < 0.035 s).
Stock wiederholt nur beim E-Motor (0x85) auf Anwendungsebene (IDs 9/10/25
je 3x) – nicht nachgebaut (0x85 gesperrt).

Nie beobachtet (bleiben Stubs bzw. unverändert): 0x01, 0x03, 0x04, 0x07,
0x08, 0x0B, 0x0D, 0x0F, 0xF0. 0x05 ([01]/[02] an X/Y bei BELT_TENSION)
wurde nie beantwortet (klippy.log.2026-10-12:28621-28636) -> nicht gebaut.

Frühere Belege: PROTOKOLL-GESAMT.md 1.5/1.7/2.2 (Cut-Kalibrierung, Update-
Pfad, Checksumme). Frame-Format/CRC: serial485.py (build_frame/parse_frame/
crc8_cal, CRC über len..data; live gegen 36 626 RX-Frames geprüft).
"""

from serial485 import Serial485  # noqa: F401 (für echten Betrieb)
from serial485 import build_frame, parse_frame, crc8_cal  # noqa: F401 (Selbsttest)

import math
import struct

# --- Func-Codes Motor-Boards (live, s. Modul-Docstring) ---
# 0x06 Sys-Param (KORREKTUR 15.10.2026, live): Die Param-ID steht im REQUEST,
# die Antwort enthält NUR den Wert:
#   Lesen     [02, id]          -> <f LE (4 B); ID 10 -> 2 B; unbek. ID -> 0 B
#   Schreiben [01, id] + <f LE  -> Echo des Werts (4 B)
#   Update    [03]              -> [01] (= Erfolg)
# Der früher als "einziger Live-Frame" geführte f7 81 07 00 06 00 00 96 43 09
# ist die ANTWORT auf das Schreiben von ID 70 = 300.0
# (klippy.log.2026-10-05:16596, klippy.log.2026-10-14:25260).
#
# Fremd-Hypothesen grant0013 (K2) für live NIE beobachtete Codes – nur
# Abgleich, NICHT senden: 0x01 reboot, 0x03 encoder_cal, 0x04
# elec_offset_cal, 0x07 flash_param, 0x08 get feedback, 0x0B boot,
# 0x0D systemid, 0x0F version.
FUNC_CONTROL = 0x05        # [01]/[02] live nie beantwortet -> NICHT gebaut
FUNC_SYS_PARAM = 0x06
FUNC_READ_ENCODER = FUNC_SYS_PARAM  # Alt-Name, NUR Kompatibilität – nicht senden!
FUNC_PROTECTION = 0x0C     # Schutzstatus: Request 1 B ([0b]), Antwort 8 B
FUNC_SET_PARAMS = FUNC_PROTECTION  # Alt-Name; frühere Deutung WIDERLEGT
FUNC_ADDR = 0x0E           # [02] -> eigene Adresse; FF 0E 01 Broadcast stumm
FUNC_SET_MODE = 0x11       # Stall-Mode: [01]/[02]/[03] setzen, [04] lesen
FUNC_STALL_MODE = FUNC_SET_MODE
FUNC_UUID = 0x12           # [02] -> 8 B UUID (Präfix cd ab)
FUNC_FW_UPDATE = 0xF0      # Update-Pfad, Sub-Codes aus Decompile (Abschnitt 1.7)

# Sub-Codes 0x06 (live)
SUB_SYS_WRITE = 0x01       # "sys参数操作-写入成功" (klippy.log.2026-10-12:10707)
SUB_SYS_READ = 0x02        # "sys参数操作-读取" (klippy.log:2511-2517)
SUB_SYS_UPDATE = 0x03      # "Updata param / sys参数操作-更新" (klippy.log:2869-2875)
SYS_UPDATE_OK = 0x01       # Antwort auf [03]

# Sub-Codes 0x0C (live)
SUB_PROT_CHECK = 0x0B      # MOTOR_CHECK_PROTECTION_AFTER_HOME DATA=11 (klippy.log:201)
SUB_PROT_UNKNOWN_05 = 0x05  # nie beantwortet (klippy.log:2470-2491) -> nie senden
PROTECTION_LEN = 8         # Antwort 8 B (live immer 8x 00 = kein Fehler)

# Sub-Codes 0x11 (live)
STALL_MODES = (0x01, 0x02, 0x03)  # setzen, Antwort = Echo
SUB_STALL_GET = 0x04       # lesen -> [mode, 00] (klippy.log.2026-10-12:31409/31432)

SUB_ADDR_READ = 0x02       # 0x0E [02] -> [addr]
SUB_UUID_READ = 0x02       # 0x12 [02] -> 8 B
UUID_LEN = 8
UUID_PREFIX = b"\xcd\xab"  # live bei allen 4 Motoren

# Retries/Timeouts (live, s. Modul-Docstring)
MOTOR_RETRIES = 0          # "retries = 0" bei allen Motor-Sendungen
TIMEOUT_SYS_PARAM = 1.0    # 0x06 (0x85-Timeouts 1.002-1.009 s)
TIMEOUT_PROTECTION = 0.05  # 0x0C ([05]-Timeouts 0.052-0.066 s)
TIMEOUT_ADDR = 0.05        # 0x0E (0.052-0.061 s)
TIMEOUT_UUID = 1.0         # 0x12 (1.002-1.006 s)
TIMEOUT_STALL = 1.0        # 0x11: nie Timeout beobachtet -> UNBELEGT (TIMEOUT_LONG)

# Sub-Codes Update-Pfad (Beleg: PROTOKOLL-GESAMT.md 1.7, je 1 Byte, 3 Retries)
SUB_GET_VERSION = 0x00     # get version
SUB_REQUEST_UPDATE = 0x01  # request update
SUB_GET_SECTOR = 0x03      # get sector size
SUB_ERASE_SECTOR = 0x06    # erase private flash sector (nur CFS-Typ 1/10 belegt!)
SUB_UPDATE_END = 0x02      # update_end (Beleg: mcu_util_485 @0x15a50..0x15a8c:
                           # Zustand 10, Func 0xF0, 1 Byte = 0x02, Timeout 500 ms;
                           # String "update_end" + "update end, wait to startup app")
UPDATE_END_TIMEOUT = 0.5   # s, Beleg: mov r7,#500 -> [sp,#8] vor bl 0x148a4

# Retries Update-Pfad: mcu_util_485 bricht nach 3 Fehlversuchen ab (Zähler
# r6, `cmp r6,#3` bei 0x1562c bzw. 0x15b78). serial485.transact macht
# retries+1 Versuche -> retries=2 = 3 Versuche des Binarys. Gilt NUR für
# F0-Kommandos; alle Live-Motorkommandos nutzen MOTOR_RETRIES = 0.
UPDATE_RETRIES = 2

SECTOR_SIZE_MIN = 1        # Beleg: Sector-Size im Binary signed Byte (ldrsb
SECTOR_SIZE_MAX = 127      # [r4,#40] @0x15560, <=0 -> Fehlerpfad) -> 1..127;
                           # Längenbyte erlaubt ohnehin max. 252 Datenbytes.

PARAM_LEN_0C = PROTECTION_LEN  # Alt-Name
FW_LEN_BYTES = 4           # Beleg: 1.7 "4 Bytes = send fw len (32-bit LE)"

MOTOR_ADDRS = (0x81, 0x82, 0x83, 0x84)  # X/Y/Z/Z1 über serial_485 (ttyS5)
MOTOR_AXES = {0x81: "X", 0x82: "Y", 0x83: "Z", 0x84: "Z1"}
EXTRUDER_TRANSPARENT_ADDR = 0x81  # E-Motor NUR transparent (Nozzle-MCU), s. Docstring

# --- Sys-Param-Tabelle (live, flash_param-Enumeration klippy.log:2502 ff.) ---
# 227 IDs (0..226), Namen identisch zu grant0013 (inkl. Original-Tippfehler
# "standar"/"calibtate"/"parma"). Index = Param-ID. Achtung Log-Falle: in
# "update_flash_param key:... value:N" ist N die ID, nicht der Wert.
SYS_PARAM_NAMES = (
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
# Plus-Log: alles AUSSER "is not configurable" (166 IDs) – d.h. 45 "is not
# need override" + 13 per [motor_control] konfiguriert + 3 Kalibrierwerte.
SYS_PARAM_CONFIGURABLE = frozenset((
    9, 10, 12, 13, 14, 20, 21, 23, 25, 41, 42, 43, 66, 67, 70, 86, 87, 89,
    94, 95, 97, 102, 103, 105, 110, 111, 113, 116, 118, 119, 120, 121, 122,
    123, 127, 128, 130, 141, 142, 143, 144, 145, 173, 179, 180, 181, 182,
    183, 187, 188, 189, 190, 200, 204, 217, 218, 220, 221, 222, 223, 226))
# Stock liest immer (Kalibrierwerte, kein Write): 9/10/25
SYS_PARAM_STOCK_ALWAYS_READ = (9, 10, 25)
# Stock-K2-Plus-Config [motor_control] setzt (nach JEDEM Kaltstart neu, da
# 0x06/01 flüchtig): 226 nur X/Y und von der Motor-FW unbekannt (0-B-Antwort)
SYS_PARAM_STOCK_CONFIGURED = (70, 86, 87, 102, 103, 116, 118, 119, 120, 121,
                              187, 188, 226)


def _check_param_id(param_id):
    if isinstance(param_id, bool) or not isinstance(param_id, int):
        raise ValueError("Param-ID muss int sein: %r" % (param_id,))
    if not 0 <= param_id < SYS_PARAM_COUNT:
        raise ValueError("Param-ID muss 0..%d sein (live 227 IDs): %r"
                         % (SYS_PARAM_COUNT - 1, param_id))


def sys_param_name(param_id):
    """Param-ID -> Name (live-Tabelle SYS_PARAM_NAMES)."""
    _check_param_id(param_id)
    return SYS_PARAM_NAMES[param_id]


def sys_param_id(name):
    """Name -> Param-ID (ValueError, wenn unbekannt)."""
    try:
        return SYS_PARAM_NAMES.index(name)
    except ValueError:
        raise ValueError("unbekannter Sys-Param-Name: %r" % (name,))


def decode_sys_param_float(data):
    """4 Bytes 0x06-Antwort -> Float (Little-Endian, `<f`). KEIN Bus-Zugriff.

    Beleg live: f7 81 07 00 06 20 29 f7 42 59 -> 123.58 (ID 9 an X,
    klippy.log:2517); 00 00 96 43 -> 300.0 / 00 00 c8 42 -> 100.0 (ID 70,
    klippy.log.2026-10-14:25260 / klippy.log.2026-10-12:10706).
    """
    raw = bytes(data)
    if len(raw) != 4:
        raise ValueError("0x06-Payload muss 4 Bytes haben: %r" % (raw,))
    return struct.unpack("<f", raw)[0]


def _float_bytes(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Sys-Param-Wert muss int/float sein: %r" % (value,))
    if not math.isfinite(value):
        raise ValueError("Sys-Param-Wert muss endlich sein: %r" % (value,))
    try:
        return struct.pack("<f", value)
    except OverflowError:
        raise ValueError("Sys-Param-Wert passt nicht in float32: %r" % (value,))


def build_sys_param_read_payload(param_id):
    """0x06-Lese-Payload [02, id] (live: klippy.log:2511 `81 05 00 06 02 09`)."""
    _check_param_id(param_id)
    return [SUB_SYS_READ, param_id]


def build_sys_param_write_payload(param_id, value):
    """0x06-Schreib-Payload [01, id] + <f LE. REINER BUILDER, sendet nichts.

    Live belegt: `81 09 00 06 01 46 00 00 c8 42` (ID 70 = 100.0,
    klippy.log.2026-10-12:10700) und `81 09 00 06 01 46 00 00 96 43`
    (ID 70 = 300.0, klippy.log.2026-10-05:16596); Vollframe
    f7 81 09 00 06 01 46 00 00 96 43 8f. Antwort = Echo der 4 Float-Bytes.
    """
    _check_param_id(param_id)
    return [SUB_SYS_WRITE, param_id] + list(_float_bytes(value))


def build_sys_param_update_payload():
    """0x06-Update-Payload [03] ("Updata param"), Antwort [01]
    (klippy.log:2869/2875). REINER BUILDER, sendet nichts."""
    return [SUB_SYS_UPDATE]


def fw_checksum(data):
    """1er-Komplement-Summe über die FW-Daten: (~summe) & 0xFF, 1 Byte.

    Beleg: PROTOKOLL-GESAMT.md 2.2 (mcu_util FUN_00010e54 laut Ghidra; im
    Live-mcu_util bei 0x10e84: Summe aller Datenbytes, dann bitweises
    Komplement, 1 Byte angehängt).
    HINWEIS: Für 485-Chunks (1.7 "send fw data") ist NICHT separat belegt,
    ob/wie dieses Byte mitgesendet wird – Builder hängen es NICHT automatisch an.
    """
    return (~(sum(bytes(data)) & 0xFF)) & 0xFF


# --- Update-Sequenz-Builder (Beleg: PROTOKOLL-GESAMT.md 1.7) ---
# Jede Funktion liefert die Payload-Liste für Func 0xF0; Versand via
# bus.transact(addr, FUNC_FW_UPDATE, payload, retries=UPDATE_RETRIES).

def build_version_payload():
    """Sub 0x00 (1B) = get version."""
    return [SUB_GET_VERSION]


def build_sector_payload():
    """Sub 0x03 (1B) = get sector size (Antwort legt die Chunk-Größe fest)."""
    return [SUB_GET_SECTOR]


def build_erase_payload():
    """Sub 0x06 (1B) = erase private flash sector.

    WARNUNG: Nur für CFS-Typ 1/10 belegt (1.7) – ob Motor-Boards diesen
    Sub-Code unterstützen, ist UNBELEGT. Nicht blind an 0x81-0x84 senden.
    """
    return [SUB_ERASE_SECTOR]


def build_update_request_payload():
    """Sub 0x01 (1B) = request update."""
    return [SUB_REQUEST_UPDATE]


def build_update_end_payload():
    """Sub 0x02 (1B) = update_end (Beleg: mcu_util_485 Zustand 10 @0x15a64:
    mov r3,#2 -> strb [sp,#0x48], Länge 1, Func 0xF0, Timeout 500 ms)."""
    return [SUB_UPDATE_END]


def build_fw_len_payload(fw_len):
    """4 Bytes FW-Länge, 32-bit Little-Endian (Beleg: 1.7)."""
    if isinstance(fw_len, bool) or not isinstance(fw_len, int):
        raise ValueError("FW-Länge muss int sein: %r" % (fw_len,))
    if not 0 <= fw_len <= 0xFFFFFFFF:
        raise ValueError("FW-Länge muss 0..0xffffffff sein: %r" % (fw_len,))
    return list(struct.pack("<I", fw_len))


def _check_sector_size(sector_size):
    if isinstance(sector_size, bool) or not isinstance(sector_size, int):
        raise ValueError("sector_size muss int sein: %r" % (sector_size,))
    if not SECTOR_SIZE_MIN <= sector_size <= SECTOR_SIZE_MAX:
        raise ValueError("sector_size muss %d..%d sein (signed Byte im Binary): %r"
                         % (SECTOR_SIZE_MIN, SECTOR_SIZE_MAX, sector_size))


def chunk_firmware(fw_bytes, sector_size):
    """Teilt die FW in Chunks der Sector-Größe (Beleg: 1.7 "Chunk-Größe aus
    sector size"). Letzter Chunk darf kürzer sein.

    sector_size außerhalb 1..127 bzw. bool/float -> ValueError; leere FW ->
    ValueError (ein leerer 0xF0-Frame ist nicht belegt)."""
    _check_sector_size(sector_size)
    fw = bytes(fw_bytes)
    if not fw:
        raise ValueError("leere Firmware")
    return [fw[i:i + sector_size] for i in range(0, len(fw), sector_size)]


def update_sequence(fw_bytes, sector_size, erase=False):
    """Dokumentierte Update-Sequenz als (Schritt, Func, Payload)-Liste.

    Reihenfolge nach 1.7: version -> sector -> [erase, NUR mit erase=True;
    belegt nur für CFS-Typ 1/10, für Motor-Boards UNBELEGT] -> request ->
    len(LE) -> Daten-Chunks -> update-end (Sub 0x02, Beleg: mcu_util_485
    @0x15a64, Timeout UPDATE_END_TIMEOUT).
    """
    fw = bytes(fw_bytes)
    chunks = chunk_firmware(fw, sector_size)  # prüft leer + sector_size
    seq = [
        ("get-version", FUNC_FW_UPDATE, build_version_payload()),
        ("get-sector-size", FUNC_FW_UPDATE, build_sector_payload()),
    ]
    if erase:
        seq.append(("erase-sector (NUR CFS-Typ 1/10 belegt!)", FUNC_FW_UPDATE,
                    build_erase_payload()))
    seq += [
        ("request-update", FUNC_FW_UPDATE, build_update_request_payload()),
        ("send-fw-len", FUNC_FW_UPDATE, build_fw_len_payload(len(fw))),
    ]
    for i, chunk in enumerate(chunks):
        seq.append(("send-fw-chunk-%d" % i, FUNC_FW_UPDATE, list(chunk)))
    seq.append(("update-end", FUNC_FW_UPDATE, build_update_end_payload()))
    return seq


class Motor:
    """Closed-loop-Motor X/Y/Z/Z1 an serial_485 (live belegte Operationen,
    geschütztes Schreiben, Update-Pfad, TODO-Stubs).

    Alle live belegten Kommandos senden mit retries=MOTOR_RETRIES (0 = genau
    1 Sendung, wie Stock) und dem je Func gemessenen Timeout.
    """

    def __init__(self, bus, addr):
        if addr not in MOTOR_ADDRS:
            raise ValueError("unbekannte Motor-Adresse: 0x%02x (nur 0x81-0x84 "
                             "über serial_485; Extruder nur transparent)" % addr)
        self.bus = bus
        self.addr = addr

    @property
    def axis(self):
        """Achsname X/Y/Z/Z1."""
        return MOTOR_AXES[self.addr]

    def _tx(self, fun, payload, timeout):
        return bytes(self.bus.transact(self.addr, fun, payload, timeout=timeout,
                                       retries=MOTOR_RETRIES))

    # --- 0x06 Sys-Param ---

    def read_sys_param_raw(self, param_id):
        """0x06 [02, id] senden, Rohantwort (bytes) zurückgeben. LESEND.

        Live (klippy.log): ID 9 -> 4 B (2511/2517), ID 10 -> 2 B `00 00`
        (2520/2526), ID 226 -> 0 B (2859/2865, Motor-FW kennt ID nicht).
        retries=0, Timeout 1.0 s.
        """
        return self._tx(FUNC_SYS_PARAM, build_sys_param_read_payload(param_id),
                        TIMEOUT_SYS_PARAM)

    def read_sys_param(self, param_id):
        """Sys-Param lesen (0x06 [02, id]) -> float. LESEND.

        Rückgabe:
        - 4-Byte-Antwort -> float (`<f` LE), z.B. ID 9 an X = 123.58
          (klippy.log:2511 `81 05 00 06 02 09` -> 2517
          `f7 81 07 00 06 20 29 f7 42 59`)
        - 0-Byte-Antwort -> None = Motor-FW kennt die ID nicht (ID 226,
          klippy.log:2859/2865 `f7 81 03 00 06 af`)
        - andere Länge -> ValueError. Betrifft live ID 10
          (param_phase_order_invert, 2 B `00 00`, klippy.log:2526); deren
          Format (u16/i16/bool) ist UNBELEGT -> read_sys_param_raw nutzen.
        Timeout -> TimeoutError (serial485).
        """
        raw = self.read_sys_param_raw(param_id)
        if len(raw) == 0:
            return None
        if len(raw) != 4:
            raise ValueError("0x06 ID %d: %d-Byte-Antwort %r ist kein float "
                             "(Format unbelegt, read_sys_param_raw nutzen)"
                             % (param_id, len(raw), raw))
        return decode_sys_param_float(raw)

    def get_sys_param(self, param_id):
        """Alias für read_sys_param (Wrapper-Name MOTOR_SYS_PARAM lesen)."""
        return self.read_sys_param(param_id)

    def write_sys_param(self, param_id, value, confirm=False):
        """Sys-Param SCHREIBEN (0x06 [01, id] + <f) – GESCHÜTZT.

        ACHTUNG: verändert Motorregelparameter (z.B. ID 70 Positionsregler-Kp,
        188 Schleppfehler-Zeit) im laufenden Motor. Live FLÜCHTIG: nach
        Kaltstart wieder Werkswerte (Kp 300, err_time 0.01 …), nach Soft-
        Restart noch der geschriebene Wert (BEFUND-MOTOR.md 2.2).
        Ohne confirm=True -> PermissionError, es wird NICHTS gesendet.

        Live: klippy.log.2026-10-12:10700 `81 09 00 06 01 46 00 00 c8 42` ->
        10706 Echo `f7 81 07 00 06 00 00 c8 42 d4` ("写入成功", 100.0).
        retries=0, Timeout 1.0 s. Echo != gesendete Bytes -> RuntimeError.
        Rückgabe: geechoter Wert (float).
        """
        payload = build_sys_param_write_payload(param_id, value)  # prüft vorher
        if confirm is not True:
            raise PermissionError(
                "write_sys_param(ID %d=%r) gesperrt: verändert Motorregel"
                "parameter (flüchtig bis Kaltstart) – nur mit confirm=True"
                % (param_id, value))
        echo = self._tx(FUNC_SYS_PARAM, payload, TIMEOUT_SYS_PARAM)
        if echo != bytes(payload[2:]):
            raise RuntimeError("0x06 Schreib-Echo passt nicht: gesendet %s, "
                               "Antwort %s" % (bytes(payload[2:]).hex(), echo.hex()))
        return decode_sys_param_float(echo)

    def set_sys_param(self, param_id, value, confirm=False):
        """Alias für write_sys_param (gleicher Schutz, confirm=True nötig)."""
        return self.write_sys_param(param_id, value, confirm=confirm)

    def update_sys_params(self, confirm=False):
        """0x06 [03] "Updata param" – GESCHÜTZT (confirm=True nötig).

        Stock sendet es einmal pro Motor am Ende der flash_param-Enumeration
        (klippy.log:2869 `81 04 00 06 03` -> 2875 `f7 81 04 00 06 01 21`).
        Wirkung unbelegt (macht die Werte jedenfalls NICHT dauerhaft – live
        flüchtig). Antwort != [01] -> RuntimeError.
        """
        if confirm is not True:
            raise PermissionError("update_sys_params gesperrt: nur mit confirm=True")
        ans = self._tx(FUNC_SYS_PARAM, build_sys_param_update_payload(),
                       TIMEOUT_SYS_PARAM)
        if ans != bytes([SYS_UPDATE_OK]):
            raise RuntimeError("0x06 [03]: Antwort %s statt 01" % ans.hex())
        return True

    # --- 0x0C Schutzstatus ---

    def check_protection(self):
        """Schutzstatus abfragen (0x0C [0b]) -> 8 Byte (bytes). LESEND.

        = Stock MOTOR_CHECK_PROTECTION_AFTER_HOME DATA=11 (klippy.log:201).
        Live: klippy.log:2441 `81 04 00 0c 0b` -> 2447
        `f7 81 0b 00 0c 00×8 fb` (alle Motoren immer 8×00 = kein Fehler;
        Bedeutung einzelner Bytes != 0 UNBELEGT). retries=0, Timeout 0.05 s.
        Sub [05] wird NIE gesendet (nie beantwortet, klippy.log:2470-2491).
        """
        ans = self._tx(FUNC_PROTECTION, [SUB_PROT_CHECK], TIMEOUT_PROTECTION)
        if len(ans) != PROTECTION_LEN:
            raise ValueError("0x0C: %d statt 8 Bytes: %r" % (len(ans), ans))
        return ans

    def write_params_raw(self, data8=None):
        """VERALTET – frühere Deutung "8 Byte Parameter senden" WIDERLEGT.

        Live ist 0x0C ein 1-Byte-Request [0b] mit 8-Byte-Antwort (die 8×00
        waren die ANTWORT). Ohne Argument = Alias auf check_protection();
        mit Argument -> ValueError (es werden nie 8 Byte gesendet).
        """
        if data8 is not None:
            raise ValueError("write_params_raw: 8-Byte-0x0C-Senden widerlegt "
                             "(0x0C = Schutzabfrage [0b]); check_protection() nutzen")
        return self.check_protection()

    def check_protection_code(self):
        """TODO: Code-Tabelle der 8 Schutzstatus-Bytes UNBELEGT (live nur 8×00).
        Rohdaten: check_protection()."""
        raise NotImplementedError("TODO check_protection_code: Code-Tabelle "
                                  "unbelegt (live nur 8x 00), s. check_protection()")

    # --- 0x11 Stall-Mode ---

    def set_mode(self, mode, *extra):
        """Stall-Mode setzen (0x11 [mode], mode ∈ 01/02/03). SENDET.

        Live: Homing MOTOR_STALL_MODE DATA=1 -> [01] an X/Y, DATA=2 -> [02]
        an alle (klippy.log:135/200); klippy.log.2026-10-12:2864/2870
        `81 04 00 11 01` -> `f7 81 04 00 11 01 1d`; [03]:
        klippy.log.2026-10-12:31436/31454 -> `f7 81 04 00 11 03 13`.
        Antwort = Echo (Abweichung -> RuntimeError). Bedeutung der Modi
        UNBELEGT. Das frühere "[02 00]" ist die ANTWORT auf [04] (s.
        get_stall_mode) – 2-Byte-Requests werden abgelehnt.
        retries=0, Timeout 1.0 s (für 0x11 unbelegt).
        """
        if extra:
            raise ValueError("0x11 nimmt genau 1 Byte (live); [02 00] war die "
                             "Antwort auf [04]: %r" % ((mode,) + extra,))
        if isinstance(mode, bool) or not isinstance(mode, int) or mode not in STALL_MODES:
            raise ValueError("Stall-Mode muss 1, 2 oder 3 sein (live belegt): %r"
                             % (mode,))
        echo = self._tx(FUNC_SET_MODE, [mode], TIMEOUT_STALL)
        if echo != bytes([mode]):
            raise RuntimeError("0x11 Echo passt nicht: %s statt %02x"
                               % (echo.hex(), mode))
        return mode

    def get_stall_mode(self):
        """Stall-Mode lesen (0x11 [04]) -> int. LESEND.

        Live: klippy.log.2026-10-12:31409 `81 04 00 11 04` -> 31432
        `f7 81 05 00 11 02 00 0e` ("get_stall_mode … b'\\x02'"), Antwort
        [mode, 00]. retries=0, Timeout 1.0 s (unbelegt).
        """
        ans = self._tx(FUNC_STALL_MODE, [SUB_STALL_GET], TIMEOUT_STALL)
        if len(ans) != 2:
            raise ValueError("0x11 [04]: 2 Bytes erwartet: %r" % (ans,))
        return ans[0]

    # --- 0x0E / 0x12 ---

    def read_485_addr(self):
        """Eigene RS485-Adresse lesen (0x0E [02]) -> int. LESEND.

        Live: klippy.log:2345/2356 `83 04 00 0e 02` -> `f7 83 04 00 0e 83 0e`,
        klippy.log:2387/2393 (0x81). retries=0, Timeout 0.05 s. Der
        Broadcast `FF 0E 01` bleibt unbeantwortet (klippy.log:2268) und wird
        hier nicht gebaut.
        """
        ans = self._tx(FUNC_ADDR, [SUB_ADDR_READ], TIMEOUT_ADDR)
        if len(ans) != 1:
            raise ValueError("0x0E: 1 Byte erwartet: %r" % (ans,))
        return ans[0]

    def read_uuid(self):
        """Geräte-UUID lesen (0x12 [02]) -> 8 Byte (bytes). LESEND.

        Live: klippy.log:2349 `83 04 00 12 02` -> 2362 Antwort len 0b, 8 B
        mit Präfix cd ab (alle 4 Motoren). retries=0, Timeout 1.0 s.
        Präfix wird nicht erzwungen (nur Länge).
        """
        ans = self._tx(FUNC_UUID, [SUB_UUID_READ], TIMEOUT_UUID)
        if len(ans) != UUID_LEN:
            raise ValueError("0x12: 8 Bytes erwartet: %r" % (ans,))
        return ans

    dev_uuid = read_uuid  # grant0013-Name

    # --- Gesperrt ---

    def read_encoder(self, *args, **kwargs):
        """GESPERRT – sendet KEINEN Frame.

        0x06 ist live Sys-Param (Lesen/Schreiben von Regelparametern), kein
        Encoder. Fremd-Kandidat 0x08 "get feedback" (grant0013, K2) wurde in
        keinem Live-Log beobachtet -> bleibt gesperrt bis Live-Beleg.
        """
        raise NotImplementedError(
            "read_encoder gesperrt: 0x06 = Sys-Param (live), 0x08 'get feedback' "
            "nie beobachtet – Encoder-Lesen unbelegt")

    # --- Update-Pfad (Func 0xF0, live nie beobachtet – unverändert) ---

    def query_version(self):
        """Version über Update-Pfad abfragen (Func 0xF0/Sub 0x00). Rohdaten."""
        return self.bus.transact(self.addr, FUNC_FW_UPDATE,
                                 build_version_payload(),
                                 retries=UPDATE_RETRIES)

    def query_sector_size(self):
        """Sector-Größe abfragen (Func 0xF0/Sub 0x03). Rohdaten."""
        return self.bus.transact(self.addr, FUNC_FW_UPDATE,
                                 build_sector_payload(),
                                 retries=UPDATE_RETRIES)

    def request_update(self):
        """Update anmelden (Func 0xF0/Sub 0x01)."""
        self.bus.transact(self.addr, FUNC_FW_UPDATE,
                          build_update_request_payload(),
                          retries=UPDATE_RETRIES)

    def send_fw_len(self, fw_len):
        """FW-Länge senden (4B LE, Func 0xF0)."""
        self.bus.transact(self.addr, FUNC_FW_UPDATE,
                          build_fw_len_payload(fw_len),
                          retries=UPDATE_RETRIES)

    def update_end(self):
        """Update abschließen (Func 0xF0/Sub 0x02, Timeout 500 ms,
        Beleg: mcu_util_485 Zustand 10 @0x15a64)."""
        self.bus.transact(self.addr, FUNC_FW_UPDATE,
                          build_update_end_payload(),
                          timeout=UPDATE_END_TIMEOUT,
                          retries=UPDATE_RETRIES)

    # --- TODO-Stubs: live nie beobachtete Motor_Control-Methoden ---
    # Quelle der Namen: motor_control_wrapper-Strings (PROTOKOLL-GESAMT.md 4).
    # KEINER sendet Bus-Frames.

    def encoder_calibrate(self):
        """TODO: `motor_encoder_calibrate` – live nie beobachtet.
        Fremdquelle K2 (grant0013): 0x03 encoder_cal."""
        raise NotImplementedError("TODO motor_encoder_calibrate: 0x03 live nie "
                                  "beobachtet")

    def elec_offset_calibrate(self):
        """TODO: `motor_elec_offset_calibrate` – live nie beobachtet.
        Fremdquelle K2 (grant0013): 0x04 elec_offset_cal."""
        raise NotImplementedError("TODO motor_elec_offset_calibrate: 0x04 live "
                                  "nie beobachtet")

    def set_e_stall_mode(self, *args):
        """TODO: Extruder-Stall-Mode. Live: 0x11 [02] geht an den E-Motor
        NUR transparent unter 0x81 (klippy.log:2439) – dieser Transport ist
        hier nicht nachgebaut (ttyS5-0x81 = X-Achse)."""
        raise NotImplementedError("TODO set_e_stall_mode: E-Motor nur über "
                                  "transparent (Nozzle-MCU), nicht serial_485")

    def set_z_stall_mode(self, *args):
        """TODO: Wrapper-Name `set_z_stall_mode`; live = 0x11 [02] an
        0x83/0x84 (klippy.log:2424-2437) -> Motor(bus, 0x83).set_mode(2)
        nutzen. Weitere Logik des Wrappers unbelegt."""
        raise NotImplementedError("TODO set_z_stall_mode: Wrapper-Logik unbelegt; "
                                  "set_mode(2) an 0x83/0x84 nutzen")

    def get_flash_param(self, param_id):
        """TODO: `MOTOR_FLASH_PARAM` – 0x07 live nie beobachtet (die Stock-
        "flash_param"-Enumeration läuft live über 0x06, s. read_sys_param)."""
        raise NotImplementedError("TODO MOTOR_FLASH_PARAM lesen (ID=%r): 0x07 "
                                  "live nie beobachtet" % (param_id,))

    def set_flash_param(self, param_id, value):
        """TODO: `MOTOR_FLASH_PARAM` schreiben – 0x07 live nie beobachtet."""
        raise NotImplementedError("TODO MOTOR_FLASH_PARAM schreiben (ID=%r): "
                                  "0x07 live nie beobachtet" % (param_id,))

    def pin_io(self, *args):
        """TODO: Pin-IO – live über die Main-MCU (set_pin_io, klippy.log:
        2259-2267/2492-2500), NICHT über den Motorbus; hier nicht nachgebaut."""
        raise NotImplementedError("TODO Pin-IO: läuft über Main-MCU, nicht RS485")

    def motor_version_normal(self):
        """TODO: `motor_version` im Normalbetrieb – 0x0F live nie beobachtet;
        belegt ist nur der Update-Pfad (query_version, F0/00)."""
        raise NotImplementedError("TODO motor_version (Normalbetrieb): 0x0F nie "
                                  "beobachtet; belegt nur F0/00 via query_version()")


# --- Selbsttest (keine Hardware, kein Netzwerk nötig) ---
class _FakeBus:
    """Spielt Live-Mitschnitte ab und PRÜFT die gesendeten Bytes.

    script: Liste (erwartet_tx_hex, antwort_hex | None).
    erwartet_tx_hex = Log-Form "data_send" OHNE Kopf f7 und CRC
    (addr len status fun data) ODER Vollframe mit f7…crc (transparent-Log).
    antwort_hex = Log-Rohframe (mit Geräte-CRC) oder None (= Timeout).
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def transact(self, addr, fun, data=(), **kwargs):
        self.calls.append((addr, fun, list(data), dict(kwargs)))
        assert self.script, "FakeBus: unerwarteter Sendeversuch"
        want_tx, rx = self.script.pop(0)
        frame = build_frame(addr, fun, list(data))
        want = bytes.fromhex(want_tx)
        got = frame if want[0] == 0xF7 else frame[1:-1]
        assert got == want, "TX %s != Log %s" % (got.hex(" "), want.hex(" "))
        if rx is None:
            raise TimeoutError("FakeBus-Timeout")
        raw = bytes.fromhex(rx)
        rfun, rdata = parse_frame(raw)  # prüft Geräte-CRC
        assert raw[1] == addr and rfun == fun, (raw[1], rfun, addr, fun)
        return rdata


def selftest():
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(("ok " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    def raises(exc, fn, *a, **kw):
        try:
            fn(*a, **kw)
        except exc:
            return True
        except Exception:
            return False
        return False

    MR = {"timeout": TIMEOUT_SYS_PARAM, "retries": 0}

    # 1. Dekodierung + Builder gegen Live-Bytes (Erwartung aus dem Log)
    check("0x06 00 00 96 43 = 300.0",
          decode_sys_param_float(bytes.fromhex("00 00 96 43")) == 300.0)
    check("0x06 00 00 c8 42 = 100.0",
          decode_sys_param_float(bytes.fromhex("00 00 c8 42")) == 100.0)
    check("float len-guard", raises(ValueError, decode_sys_param_float, b"\x00\x01\x02"))
    check("write-builder ID70=300 = Log-TX (klippy.log.2026-10-05:16596)",
          bytes([0x81, 0x09, 0x00, FUNC_SYS_PARAM])
          + bytes(build_sys_param_write_payload(70, 300.0))
          == bytes.fromhex("81 09 00 06 01 46 00 00 96 43"))
    check("write-builder Vollframe = verifizierter Frame",
          build_frame(0x81, FUNC_SYS_PARAM, build_sys_param_write_payload(70, 300))
          == bytes.fromhex("f7 81 09 00 06 01 46 00 00 96 43 8f"))
    check("write-builder ID70=100 = Log-TX (klippy.log.2026-10-12:10700)",
          build_frame(0x81, 6, build_sys_param_write_payload(70, 100.0))[1:-1]
          == bytes.fromhex("81 09 00 06 01 46 00 00 c8 42"))
    check("update-builder [03]", build_sys_param_update_payload() == [0x03])
    check("read-builder [02 09]", build_sys_param_read_payload(9) == [0x02, 0x09])
    for bad in [(-1, 1.0), (227, 1.0), (True, 1.0), (70, True), (70, "1"),
                (70, float("nan")), (70, float("inf")), (70, 1e300)]:
        check("write-builder guard %r" % (bad,),
              raises(ValueError, build_sys_param_write_payload, *bad))

    # 2. Param-Tabelle (live 227 IDs, Namen = grant0013)
    check("227 Namen", len(SYS_PARAM_NAMES) == SYS_PARAM_COUNT == 227
          and len(set(SYS_PARAM_NAMES)) == 227)
    check("ID 70 = pos-loop kp",
          sys_param_name(70) == "controller_pos_loop_pid_param_kp"
          and sys_param_id("controller_pos_loop_pid_param_kp") == 70)
    check("ID 9/188/226 Namen", sys_param_name(9) == "param_elec_offset"
          and sys_param_name(188) == "protection_param_prt_track_err_time"
          and sys_param_name(226) == "stall_pos_err_rad_for_slicer")
    check("Stock-Config-IDs configurable",
          set(SYS_PARAM_STOCK_CONFIGURED) | set(SYS_PARAM_STOCK_ALWAYS_READ)
          <= SYS_PARAM_CONFIGURABLE and len(SYS_PARAM_CONFIGURABLE) == 61)
    check("name-guard", raises(ValueError, sys_param_id, "gibtsnicht"))

    # 3. read_sys_param gegen Live-Frames (klippy.log:2511-2550, 2859-2865)
    bus = _FakeBus([
        ("81 05 00 06 02 09", "f7 81 07 00 06 20 29 f7 42 59"),   # ID 9
        ("81 05 00 06 02 0a", "f7 81 05 00 06 00 00 90"),         # ID 10, 2 B
        ("81 05 00 06 02 0a", "f7 81 05 00 06 00 00 90"),         # ID 10 raw
        ("81 05 00 06 02 19", "f7 81 07 00 06 dc 7a 77 40 93"),   # ID 25
        ("81 05 00 06 02 e2", "f7 81 03 00 06 af"),               # ID 226, 0 B
        ("82 05 00 06 02 09", "f7 82 07 00 06 53 4a 18 43 ae"),   # Y ID 9
    ])
    mx = Motor(bus, 0x81)
    check("ID 9 X = 123.58", abs(mx.read_sys_param(9) - 123.58) < 1e-3
          and bus.calls[0][3] == MR)
    check("ID 10 (2 B) -> ValueError", raises(ValueError, mx.read_sys_param, 10))
    check("ID 10 raw = 00 00", mx.read_sys_param_raw(10) == b"\x00\x00")
    check("ID 25 X = 3.867", abs(mx.get_sys_param(25) - 3.867) < 1e-3)
    check("ID 226 (0 B) -> None", mx.read_sys_param(226) is None)
    check("ID 9 Y = 152.29", abs(Motor(bus, 0x82).read_sys_param(9) - 152.29) < 1e-2)
    check("read id-guard sendet nichts",
          raises(ValueError, mx.read_sys_param, 227) and len(bus.calls) == 6
          and not bus.script)

    # 4. write_sys_param / update: Schutz + Live-Echo
    nobus = _FakeBus([])
    m0 = Motor(nobus, 0x81)
    check("write ohne confirm -> PermissionError",
          raises(PermissionError, m0.write_sys_param, 70, 100.0))
    check("write confirm='yes' -> PermissionError",
          raises(PermissionError, m0.write_sys_param, 70, 100.0, confirm="yes"))
    check("set_sys_param ohne confirm gesperrt",
          raises(PermissionError, m0.set_sys_param, 70, 100.0))
    check("update ohne confirm gesperrt", raises(PermissionError, m0.update_sys_params))
    check("gesperrte Writes senden nichts", nobus.calls == [])
    bus = _FakeBus([
        ("81 09 00 06 01 46 00 00 c8 42", "f7 81 07 00 06 00 00 c8 42 d4"),
        ("f7 81 09 00 06 01 46 00 00 96 43 8f", "f7 81 07 00 06 00 00 96 43 09"),
        ("81 04 00 06 03", "f7 81 04 00 06 01 21"),
        ("81 09 00 06 01 46 00 00 c8 42", "f7 81 07 00 06 00 00 96 43 09"),
    ])
    mw = Motor(bus, 0x81)
    check("write ID70=100 Echo (10-12:10700/10706)",
          mw.write_sys_param(70, 100.0, confirm=True) == 100.0
          and bus.calls[0][3] == MR)
    check("write ID70=300 Echo (10-14:25260)",
          mw.set_sys_param(70, 300, confirm=True) == 300.0)
    check("update [03] -> [01] (klippy.log:2869/2875)",
          mw.update_sys_params(confirm=True) is True)
    check("falsches Echo -> RuntimeError",
          raises(RuntimeError, mw.write_sys_param, 70, 100.0, confirm=True))

    # 5. check_protection / write_params_raw ([0b] live, [05] nie)
    bus = _FakeBus([
        ("81 04 00 0c 0b", "f7 81 0b 00 0c 00 00 00 00 00 00 00 00 fb"),
        ("f7 81 04 00 0c 0b 95", "f7 81 0b 00 0c 00 00 00 00 00 00 00 00 fb"),
        ("84 04 00 0c 0b", "f7 84 0b 00 0c 00 00 00 00 00 00 00 00 fb"),
    ])
    check("protection X 8x00 (klippy.log:2441/2447)",
          Motor(bus, 0x81).check_protection() == bytes(8)
          and bus.calls[0][3] == {"timeout": TIMEOUT_PROTECTION, "retries": 0})
    check("write_params_raw() = Alias (transparent-Frame klippy.log:2469)",
          Motor(bus, 0x81).write_params_raw() == bytes(8))
    check("protection Z1", Motor(bus, 0x84).check_protection() == bytes(8))
    check("write_params_raw(8B) abgelehnt",
          raises(ValueError, Motor(nobus, 0x82).write_params_raw, [0] * 8))
    check("[05] nirgends sendbar", nobus.calls == [] and SUB_PROT_UNKNOWN_05 == 5
          and "05" not in [("%02x" % c[2][0]) for c in bus.calls])

    # 6. 0x11 set_mode / get_stall_mode, 0x0E, 0x12
    bus = _FakeBus([
        ("81 04 00 11 01", "f7 81 04 00 11 01 1d"),     # 10-12:2864/2870
        ("f7 81 04 00 11 02 14", "f7 81 04 00 11 02 14"),  # klippy.log:2439
        ("81 04 00 11 03", "f7 81 04 00 11 03 13"),     # 10-12:31436/31454
        ("81 04 00 11 04", "f7 81 05 00 11 02 00 0e"),  # 10-12:31409/31432
        ("83 04 00 0e 02", "f7 83 04 00 0e 83 0e"),     # klippy.log:2345/2356
        ("f7 81 04 00 0e 02 80", "f7 81 04 00 0e 81 00"),  # klippy.log:2375
        ("83 04 00 12 02", "f7 83 0b 00 12 cd ab 00 00 00 00 00 00 ad"),
        # ^ TX klippy.log:2349; Antwort = PLATZHALTER (echte UUID aus 2362
        #   entfernt), CRC neu berechnet
        ("81 04 00 11 01", "f7 81 04 00 11 03 13"),     # falsches Echo
        ("83 04 00 0e 02", None),                       # Timeout
    ])
    m = Motor(bus, 0x81)
    check("set_mode 1 Echo", m.set_mode(1) == 1
          and bus.calls[0][3] == {"timeout": TIMEOUT_STALL, "retries": 0})
    check("set_mode 2 Echo", m.set_mode(2) == 2)
    check("set_mode 3 Echo", m.set_mode(3) == 3)
    check("get_stall_mode -> 2", m.get_stall_mode() == 2)
    mz = Motor(bus, 0x83)
    check("read_485_addr Z = 0x83", mz.read_485_addr() == 0x83
          and bus.calls[4][3] == {"timeout": TIMEOUT_ADDR, "retries": 0})
    check("read_485_addr X = 0x81", m.read_485_addr() == 0x81)
    u = mz.dev_uuid()
    check("read_uuid 8 B Präfix cd ab", len(u) == 8 and u[:2] == UUID_PREFIX
          and bus.calls[6][3] == {"timeout": TIMEOUT_UUID, "retries": 0})
    check("set_mode falsches Echo -> RuntimeError", raises(RuntimeError, m.set_mode, 1))
    check("Timeout durchgereicht", raises(TimeoutError, mz.read_485_addr))
    n = len(bus.calls)
    for bad in [(), (0,), (4,), (256,), (True,), (1.0,), (0x02, 0x00)]:
        check("set_mode guard %r" % (bad,), raises((TypeError, ValueError),
                                                    m.set_mode, *bad))
    check("set_mode guards senden nichts", len(bus.calls) == n and not bus.script)

    # 7. Gesperrtes / Stubs / Adressen
    for args in [(), (0x9643,)]:
        check("read_encoder%r wirft" % (args,),
              raises(NotImplementedError, Motor(nobus, 0x81).read_encoder, *args))
    check("addr-guard 0x85 (Extruder nicht RS485)", raises(ValueError, Motor, None, 0x85))
    check("addr-guard 0x05", raises(ValueError, Motor, None, 0x05))
    mm = Motor(nobus, 0x84)
    stub_ok = all(raises(NotImplementedError, f) for f in [
        mm.encoder_calibrate, mm.elec_offset_calibrate, mm.check_protection_code,
        mm.pin_io, mm.motor_version_normal,
        lambda: mm.set_e_stall_mode(1), lambda: mm.set_z_stall_mode(1),
        lambda: mm.get_flash_param(0), lambda: mm.set_flash_param(0, 1)])
    check("stubs not-implemented", stub_ok)
    check("stubs/Sperren senden nichts", nobus.calls == [])
    check("kurz-frame", raises(ValueError, parse_frame, bytes.fromhex("f7 81")))
    corrupt = bytearray(bytes.fromhex("f7 81 07 00 06 00 00 96 43 09"))
    corrupt[-1] ^= 0xFF
    check("crc-fehler", raises(ValueError, parse_frame, bytes(corrupt)))

    # 8. Update-Pfad (Doku 1.7 + Disassembly, live nie beobachtet)
    bus = _FakeBus([
        ("81 04 00 f0 00", "f7 81 06 00 f0 00 02 02 %02x"
         % crc8_cal(bytes.fromhex("06 00 f0 00 02 02"))),
    ])
    check("query_version sub 0x00 retries=2",
          list(Motor(bus, 0x81).query_version()) == [0, 2, 2]
          and bus.calls[0][3].get("retries") == UPDATE_RETRIES)
    check("retries=2 -> 3 Versuche wie Binary", UPDATE_RETRIES + 1 == 3)
    check("payload version", build_version_payload() == [0x00])
    check("payload sector", build_sector_payload() == [0x03])
    check("payload erase", build_erase_payload() == [0x06])
    check("payload request", build_update_request_payload() == [0x01])
    check("payload update-end", build_update_end_payload() == [0x02])
    check("fw_len LE", build_fw_len_payload(0x1E240) == [0x40, 0xE2, 0x01, 0x00])
    for bad in [-1, 2 ** 32, True, 1.0]:
        check("fw_len guard %r" % (bad,), raises(ValueError, build_fw_len_payload, bad))
    check("checksum ~sum", fw_checksum([0x01, 0x02, 0x03]) == 0xF9)
    check("checksum leer=0xff", fw_checksum([]) == 0xFF)
    check("chunks", chunk_firmware(b"ABCDEFGHIJ", 4) == [b"ABCD", b"EFGH", b"IJ"])
    check("chunk 127 ok", len(chunk_firmware(bytes(300), 127)) == 3)
    for bad in [0, -1, 128, 300, True, 2.5, 4.0]:
        check("chunk guard %r" % (bad,), raises(ValueError, chunk_firmware, b"AB", bad))
    for fw in [b"", []]:
        check("leere FW guard %r" % (fw,), raises(ValueError, update_sequence, fw, 64))
    seq = update_sequence(b"ABCDEF", 4)
    check("seq reihenfolge (ohne erase)",
          [s[0] for s in seq] == ["get-version", "get-sector-size",
                                  "request-update", "send-fw-len",
                                  "send-fw-chunk-0", "send-fw-chunk-1",
                                  "update-end"])
    check("seq len-payload", seq[3][2] == [0x06, 0x00, 0x00, 0x00])
    check("seq ende sub 0x02", seq[-1][2] == [SUB_UPDATE_END])
    check("seq fun-codes", all(s[1] == FUNC_FW_UPDATE for s in seq))
    seq_e = update_sequence(b"ABCDEF", 4, erase=True)
    check("seq erase nur auf Wunsch",
          seq_e[2] == ("erase-sector (NUR CFS-Typ 1/10 belegt!)",
                       FUNC_FW_UPDATE, [SUB_ERASE_SECTOR])
          and len(seq_e) == len(seq) + 1
          and all(s[2] != [SUB_ERASE_SECTOR] for s in seq))
    rt_ok = True
    for label, fun, payload in seq + update_sequence(bytes(range(256)), 127):
        rfun, rdata = parse_frame(build_frame(0x83, fun, payload))
        rt_ok &= (rfun == fun and list(rdata) == list(payload))
    check("seq roundtrip", rt_ok)

    print("SELBSTTEST: %s" % ("ok" if fails == 0 else "%d FEHLER" % fails))
    return fails


if __name__ == "__main__":
    raise SystemExit(selftest())
