#!/usr/bin/env python3
"""motor.py – Python-Nachbau von motor_control (K2 Closed-loop-Motoren, erweitert).

Ersetzt (teilweise): motor_control_wrapper.cpython-39.so (armhf, proprietär).
Erweiterung von motor_mini.py (motor_mini.py bleibt eigenständig, gleiche
Sperren, gleiche Retry-/Timeout-Regeln).

LIVE-STAND 15.10.2026 (Auswertung live-logs-2026-10-15/klippy.log*, lokal, nicht im Repo, 6 Logs,
10 Boot-Enumerationen; Zeilenangaben "Datei:Zeile" beziehen sich auf diesen
Ordner, "klippy.log" = Boot 16.10. 01:15 Druckerzeit).
NACHTRAG 16.10.2026 (LIVE-ABFRAGEN-2026-10-16.md, Motor X 0x81, Standby,
nicht referenziert, gesendet über Creality-G-Codes MOTOR_VERSION/MOTOR_GET):
0x0F = Version ([00] -> 12 B) und 0x08 = Variablen-Lesepfad ([idx] -> 4 B LE,
idx 1..12) sind live belegt -> query_motor_version(), read_variable().
NACHTRAG 16.10.2026 (REA-MOTOR-BEFUND-2026-10-16.md, statische Analyse
motor_control_wrapper.so + Traceback klippy.log 13:12:27): 0x08-Index 5..8 =
pos_fdb/spd_fdb/id_fdb/iq_fdb (Host-Dict NO_ERR_CODE_TO_GET), Typkette des
Hosts fest (1..10,17,18 '<f', 11..16 '<I'), Motor_cmd-Tabelle komplett,
Crash-Ursache DATA=0/13 belegt (host-seitig, KEIN Frame auf dem Bus).
NACHTRAG 16.10.2026 (LIVE-ABFRAGEN-2026-10-16.md, Abschnitt „Skalierung
pos_fdb“, Messfahrt X nach G28, Bett leer, User-Freigabe): idx 11 =
Single-Turn-Position 0..3199 Counts/Umdrehung (POS_COUNTS_PER_REV = 3200 =
200 Vollschritte x 16 Microsteps; 80 Counts/mm bei rotation_distance 40),
idx 5 pos_fdb = idx11 * 2*pi/3200 [rad]. Beide wrappen alle 40 mm (eine
Umdrehung) -> KEINE absolute Achsposition (die hat nur Klipper über den
Schrittzähler). idx 6 spd_fdb im Stillstand ~0 (+-0,006, Einheit offen).

Umfang:
- LESEND (live belegt, senden erlaubt):
  read_sys_param / read_sys_param_raw (0x06 [02, id]),
  check_protection (0x0C [0b]), get_stall_mode (0x11 [04]),
  read_485_addr (0x0E [02]), read_uuid (0x12 [02]),
  query_motor_version (0x0F [00] -> 12 B, live 16.10.),
  read_variable (0x08 [idx], NUR idx 1..12 -> 4 B, live 16.10.),
  read_position_feedback / read_speed_feedback / read_current_feedback
  (0x08 idx 5 / 6 / 7+8, Namen REA-belegt; pos_fdb = rad single-turn,
  live 16.10. belegt; spd/id/iq Einheit OFFEN), read_position_counts
  (0x08 idx 11 = Counts 0..3199/Umdrehung, live 16.10. belegt)
- Umrechnung ohne Bus: pos_rad_to_counts, counts_to_rad, counts_to_mm,
  counts_delta (wrap-korrigierte Differenz, nur |Weg| < 1/2 Umdrehung)
- SETZEN (live belegt, sendet): set_mode (0x11 [01]/[02]/[03], Antwort = Echo)
- SCHREIBEN (live belegt, aber GESCHÜTZT): write_sys_param (0x06 [01, id]+<f)
  und update_sys_params (0x06 [03]) senden NUR mit confirm=True, sonst
  PermissionError ohne Bus-Zugriff. Sie verändern Motorregelparameter
  (Kp/Ki/Schutzgrenzen) – FLÜCHTIG bis zum nächsten Kaltstart.
- Reine Builder (ohne Senden): build_sys_param_read_payload,
  build_sys_param_write_payload, build_sys_param_update_payload
- decode_sys_param_float: Dekodier-Hilfe (<f, ohne Bus)
- read_encoder: GESPERRT (0x06-Altfalle) – "Encoder" = 0x08 idx 5/11
  (Single-Turn) -> read_position_feedback(with_counts=True)
- Detail-/Sample-Kommandos 0x13..0x16 (err_detail_data, cut_detail_data,
  pos_data_sample, spd_data_sample) und 0x01/0x03/0x04/0x07/0x0B/0x0D:
  nur Konstanten + NotImplementedError-Stubs (senden NICHTS)
- WARNUNG Host-Bug (live 16.10., zweimal reproduziert, Ursache BELEGT):
  Creality `MOTOR_GET … DATA=0` und `DATA=13` -> Klipper-SHUTDOWN.
  Traceback (klippy.log 16.10. 13:12:27): motor_control_wrapper.py:1982
  Motor_Control.motor_get -> box_wrapper.py:384 ParseData.parse_num_to_byte
  -> TypeError: 'NoneType' object cannot be interpreted as an integer.
  Creality-gcode.py lehnt DATA außerhalb minval=1/maxval=12 nur mit Warnung
  ab (get_int -> None), motor_get rechnet weiter -> Absturz VOR dem Senden.
  read_variable bleibt deshalb im G-Code-Bereich 1..12.
- Pin-IO (MOTOR_READ_ALL_PIN_IO): KEIN RS485 – dir/step-Pins der Motoren
  hängen an der Main-MCU (Klipper-GPIO PB9, PB10, !PB7, PB8, PB5, PB6, PA1,
  PB15) -> pin_io bleibt Stub (Main-MCU nötig)
- Update-Pfad (Func 0xF0, aus mcu_util_485-Decompile; auf dem Motorbus
  live NIE beobachtet): query_version/query_sector_size frei (Abfragen),
  request_update/send_fw_len/update_end NUR mit confirm=True (Bootloader-
  Risiko, Haltemoment weg); Builder + update_sequence senden nichts.
- write_sys_param: IDs außerhalb SYS_PARAM_CONFIGURABLE zusätzlich nur mit
  allow_locked=True; Werte mit float32-Plausibilitätsprüfung.

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
0x0B, 0x0D, 0x13-0x16, 0xF0 (0x08/0x0F seit 16.10. live belegt, s. oben;
Bedeutung der Cmd-Bytes seit 16.10. aus der Host-Tabelle Motor_cmd belegt). 0x05 ([01]/[02] an X/Y bei BELT_TENSION)
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
# elec_offset_cal, 0x07 flash_param, 0x0B boot, 0x0D systemid.
# (0x08 "get feedback" -> live 16.10. Variablen-Lesepfad; 0x0F "version"
# -> live 16.10. bestätigt.)
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
FUNC_VARIABLE = 0x08       # live 16.10.: [idx] -> 4 B LE (Variablen-Lesepfad)
FUNC_VERSION = 0x0F        # live 16.10.: [00] -> 12 B Versionsblock

# Motor_cmd – vollständige Host-Tabelle (REA 16.10.2026, Modul-Init
# motor_control_wrapper.so @0x10168, Dict-Aufbau @0x10a9c; BELEGT als
# Host-Zuordnung Name -> Cmd-Byte). Live gesehen: 0x05 (unbeantwortet),
# 0x06, 0x08, 0x0C, 0x0E, 0x0F, 0x11, 0x12. Alle anderen: NICHT senden,
# nur Stubs (NotImplementedError).
FUNC_REBOOT = 0x01
FUNC_ENCODER_CALIBRATE = 0x03      # encoder_calibrate_official
FUNC_ELEC_OFFSET_CALIBRATE = 0x04
FUNC_FLASH_PARAM = 0x07            # MOTOR_FLASH_PARAM (Payload-Logik vermutet)
FUNC_GET = FUNC_VARIABLE           # Host-Name "get"
FUNC_BOOT = 0x0B                   # MOTOR_BOOT – NIE senden (Bootloader)
FUNC_SYSTEMID = 0x0D
FUNC_ERR_DETAIL_DATA = 0x13        # Fehlerdetails (Format s. ERR_DETAIL_FORMAT)
FUNC_CUT_DETAIL_DATA = 0x14        # wie 0x13 + 1 float
FUNC_POS_DATA_SAMPLE = 0x15        # Positions-Sample (Inhalt unbelegt)
FUNC_SPD_DATA_SAMPLE = 0x16        # Drehzahl-Sample (Inhalt unbelegt)
MOTOR_CMD = {
    "reboot": FUNC_REBOOT, "encoder_calibrate_official": FUNC_ENCODER_CALIBRATE,
    "elec_offset_calibrate": FUNC_ELEC_OFFSET_CALIBRATE, "control": FUNC_CONTROL,
    "sys_param": FUNC_SYS_PARAM, "flash_param": FUNC_FLASH_PARAM,
    "get": FUNC_GET, "boot": FUNC_BOOT, "protection": FUNC_PROTECTION,
    "systemid": FUNC_SYSTEMID, "read485_addr": FUNC_ADDR,
    "set485_addr": FUNC_ADDR, "version": FUNC_VERSION,
    "stall_mode": FUNC_STALL_MODE, "dev_uuid": FUNC_UUID,
    "err_detail_data": FUNC_ERR_DETAIL_DATA,
    "cut_detail_data": FUNC_CUT_DETAIL_DATA,
    "pos_data_sample": FUNC_POS_DATA_SAMPLE,
    "spd_data_sample": FUNC_SPD_DATA_SAMPLE,
}
# Motor_Addr (Host, Schlüssel = NUM-String): '0'..'8' -> 0x80..0x88,
# '9' -> 0xFF (Broadcast). MOTOR_NUM_TO_NAME 1..5 -> x, y, z, z1, e.
# Hinweis: NUM 5 (e) -> 0x85 antwortet auf ttyS5 nie (Extruder transparent).
MOTOR_ADDR_BY_NUM = dict([(str(n), 0x80 + n) for n in range(9)] + [("9", 0xFF)])
MOTOR_NUM_TO_NAME = {1: "x", 2: "y", 3: "z", 4: "z1", 5: "e"}

# 0x13 err_detail_data: Host-Unpack-Format @0x3a3f0 (BELEGT als Host-
# Format; Request-Payload und Frame-Offset UNBELEGT, live nie gesehen).
# 0x14 cut_detail_data: dasselbe + 1 float (Feldname unbelegt).
ERR_DETAIL_FORMAT = "<I8BfHfiiffffffffi"
CUT_DETAIL_FORMAT = ERR_DETAIL_FORMAT + "f"
ERR_DETAIL_FIELDS = (
    "err_code", "dev_uuid", "elec_offset", "phase_order_invert",
    "elec_offset_err_deg", "encoder_fdb", "encoder_fdb_last",
    "pos_fdb_encoder", "spd_fdb_encoder", "spd_fdb_ctrl", "id", "iq",
    "board_detect_power_supply_V", "board_detect_temperature_C",
    "pos_loop_pid_err", "step_increment")  # dev_uuid = 8 B

# Fehler-/Warnbits (Host-Dicts motor_error_dict / motor_warning_dict,
# Modul-Init, BELEGT als Bit -> key-Zuordnung; Kurztexte = Übersetzung der
# key-Bedeutung laut REA-Befund). WELCHE Bytes der 8-B-Antwort von 0x0C [0b]
# err_code bzw. warning_code tragen, ist UNBELEGT (live nur 8x 00) – daher
# kein decode_protection(8 B), nur Bit-Decoder auf bereits bekannte Codes.
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
# ERR_CODE_TO_UNION (Host): Fehlerbit-WERT -> (Format, Felder) der Detail-
# daten. 256 -> tracking_error: Schlüssel VERMUTET (letztes Element).
ERR_CODE_TO_UNION = {
    1: ("<ii", ("lower_encoder_pulse", "upper_encoder_pulse")),
    4: ("<f", ("peak_overcurrent_A",)),
    8: ("<ff", ("continuous_overcurrent_A", "continuous_overcurrent_time_s")),
    32: (None, ("speed_over_limit",)),
    64: (None, ("pos_over_limit_min", "pos_over_limit_max")),
    128: (None, ("pos_cmd_mutation_turns",)),
    256: (None, ("tracking_error",)),
}

# 0x0F Version (live 16.10.2026, MOTOR_VERSION NUM=1 DATA=0):
#   TX data_send `81 04 00 0f 00`
#   RX `f7 81 0f 00 0f 01 09 07 51 01 03 01 03 02 4b 01 00 02`
#   12 B = fireware_ver 4 B (1.9.7.81) | cmd_system_ver 2 B (1.3) |
#          protection_ver 2 B (1.3) | flash_param_ver 2 B (2.75) |
#          logger_ver 2 B (1.0). Feldnamen = Strings motor_control_wrapper.so.
SUB_VERSION_GET = 0x00
VERSION_LEN = 12
VERSION_FIELDS = (("fireware_ver", 4), ("cmd_system_ver", 2),
                  ("protection_ver", 2), ("flash_param_ver", 2),
                  ("logger_ver", 2))

# 0x08 Variable (live 16.10.2026, MOTOR_GET NUM=1 DATA=idx, Scan 1..12,
# Rohdaten lokal, nicht im Repo): TX `81 04 00 08 <idx>`, RX 4 B LE.
# Host-Text "系统所支持变量当前值" (aktueller Wert einer vom System
# unterstützten Variable); Namen gibt der Host NICHT aus.
# Host-Bug: DATA=0 und DATA=13 -> Klipper-SHUTDOWN (key60) -> nur 1..12.
# Ursache BELEGT (Traceback 16.10. 13:12:27): G-Code-Grenze minval=1/
# maxval=12 -> get_int -> None (nur Warnung) -> parse_num_to_byte(None)
# -> TypeError, kein Frame geht raus.
VARIABLE_LEN = 4
VARIABLE_INDEX_MIN = 1
VARIABLE_INDEX_MAX = 12   # G-Code-Grenze des Hosts (maxval=12)
# Indexnamen (REA BELEGT: Modul-Dict NO_ERR_CODE_TO_GET @0x10cf8-0x10d60,
# genutzt von _motor_no_cut_detail_data). Index 1-4 und 9-12: KEIN Name im
# Host (bounded negativ). Vermutet (nur Kommentar, keine Host-Belege):
#   idx 9  = 0.0016113 = 3,3 V/2048 -> 2 LSB eines 12-Bit-ADC bei 3,3 V
#   idx 10 = 0.0008057 = 3,3 V/4096 -> 1 LSB (z. B. Strommess-Offset ia/ib)
# idx 11: BELEGT live 16.10. (Messfahrt X, LIVE-ABFRAGEN-2026-10-16.md):
#   Single-Turn-Position 0..3199 Counts/Umdrehung (690 @ X50, 1490 @ X100,
#   3090 @ X160, 690 @ X170 -> 80 Counts/mm, Wrap alle 40 mm). Name
#   "pos_counts" = EIGENER Name (live 16.10. abgeleitet, Host-Name
#   UNBEKANNT – nicht im Host-Dict NO_ERR_CODE_TO_GET).
VARIABLE_NAMES_HOST = {5: "pos_fdb", 6: "spd_fdb", 7: "id_fdb", 8: "iq_fdb"}
VARIABLE_NAMES_DERIVED = {11: "pos_counts"}   # live 16.10. abgeleitet
VARIABLE_NAMES = dict(list(VARIABLE_NAMES_HOST.items())
                      + list(VARIABLE_NAMES_DERIVED.items()))
IDX_POS_FDB, IDX_SPD_FDB, IDX_ID_FDB, IDX_IQ_FDB = 5, 6, 7, 8
IDX_POS_COUNTS = 11
# Skalierung (BELEGT live 16.10.): idx 5 pos_fdb [rad] = idx11 * 2*pi/3200.
# 3200 = 200 Vollschritte x 16 Microsteps ([stepper_x] microsteps 16);
# rotation_distance 40 -> 80 Counts/mm. Live-Rest pos_fdb - idx11*2pi/3200
# ~ +8e-5 rad (~0,04 Counts, Ursache offen) -> Toleranz POS_RAD_TOL.
POS_COUNTS_PER_REV = 3200
POS_ROTATION_DISTANCE = 40.0   # mm/Umdrehung, printer.cfg [stepper_x] (live)
POS_RAD_TOL = 2e-4
# Typkette des Hosts (REA BELEGT: motor_get @0x51524, Py-Zeile 1987/1989,
# Vergleiche @0x51e04-0x520e8 und @0x526bc-0x527c8; gleiche Kette in
# motor_extruder_get und _motor_no_cut_detail_data). Fest verdrahtet, der
# Motor liefert keinen Typ. Wertbytes = result[5:result[2]+2] (Vollframe,
# = [5:9] bei len 7 = die 4 Datenbytes). 13..18 kennt der Host, sie sind
# per G-Code aber nicht erreichbar und am Motor live NICHT geprüft ->
# read_variable sperrt sie bewusst.
VARIABLE_HOST_FORMATS = dict([(i, "<f") for i in list(range(1, 11)) + [17, 18]]
                             + [(i, "<I") for i in range(11, 17)])
# Typ je G-Code-erreichbarem Index (1..12): "float" = '<f', "int" = '<I'
# (UNSIGNED laut Host; bis 16.10. fälschlich '<i').
VARIABLE_TYPES = dict((i, "float" if VARIABLE_HOST_FORMATS[i] == "<f" else "int")
                      for i in range(VARIABLE_INDEX_MIN, VARIABLE_INDEX_MAX + 1))

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
# 0x0F/0x08: live 16.10. nur je 1 Antwort (Moonraker-G-Code), kein Timeout
# beobachtet -> Wert UNBELEGT, 1.0 s (serial485 TIMEOUT_LONG). retries=0
# wie alle Motor-Sendungen.
TIMEOUT_VERSION = 1.0      # unbelegt
TIMEOUT_VARIABLE = 1.0     # unbelegt

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


FLOAT32_MAX = 3.4028234663852886e38   # größter endlicher float32-Betrag


def _float_bytes(value):
    """Wert -> 4 Bytes `<f` mit Plausibilitätsprüfung (ValueError bei):
    nicht int/float bzw. bool, nicht endlich, |Wert| > float32-Max, oder
    Eingabe != 0 rundet in float32 auf 0 (z.B. 1e-50 – struct schreibt das
    sonst STILL als 0.0)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Sys-Param-Wert muss int/float sein: %r" % (value,))
    if not math.isfinite(value):
        raise ValueError("Sys-Param-Wert muss endlich sein: %r" % (value,))
    if abs(value) > FLOAT32_MAX:
        raise ValueError("Sys-Param-Wert |%r| > float32-Max %r" % (value, FLOAT32_MAX))
    try:
        raw = struct.pack("<f", value)
    except OverflowError:
        raise ValueError("Sys-Param-Wert passt nicht in float32: %r" % (value,))
    if value != 0 and struct.unpack("<f", raw)[0] == 0.0:
        raise ValueError("Sys-Param-Wert %r rundet in float32 auf 0.0 (Unterlauf)"
                         % (value,))
    return raw


def float32_rounding(value):
    """(float32-Wert, relative Abweichung) für einen Sys-Param-Wert, ohne Bus.
    Gleiche Prüfungen wie beim Schreiben (ValueError). Abweichung =
    |f32 - value| / |value| (0.0 bei value == 0); z.B. 0.1 -> ~1.5e-8,
    Subnormal 1e-45 -> ~0.4 (geschriebener Wert 1.4e-45)."""
    f32 = struct.unpack("<f", _float_bytes(value))[0]
    return f32, (abs(f32 - value) / abs(value) if value else 0.0)


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


def build_motor_version_payload():
    """0x0F-Payload [00] (live 16.10.: `81 04 00 0f 00`). Builder."""
    return [SUB_VERSION_GET]


def decode_motor_version(data):
    """12 B 0x0F-Antwort -> dict. KEIN Bus-Zugriff.

    fireware_ver "a.b.c.d" (4 Bytes dezimal), die übrigen "major.minor"
    (minor dezimal: 02 4b -> "2.75"); 'raw' = Rohbytes.
    Live 16.10.: 01 09 07 51 01 03 01 03 02 4b 01 00 -> fireware_ver
    1.9.7.81, cmd_system_ver 1.3, protection_ver 1.3, flash_param_ver 2.75,
    logger_ver 1.0.
    """
    raw = bytes(data)
    if len(raw) != VERSION_LEN:
        raise ValueError("0x0F: %d statt %d Bytes: %r" % (len(raw), VERSION_LEN, raw))
    out, pos = {}, 0
    for name, n in VERSION_FIELDS:
        out[name] = ".".join(str(b) for b in raw[pos:pos + n])
        pos += n
    out["raw"] = raw
    return out


def _check_variable_index(idx):
    if isinstance(idx, bool) or not isinstance(idx, int) \
            or not VARIABLE_INDEX_MIN <= idx <= VARIABLE_INDEX_MAX:
        raise ValueError(
            "0x08-Index muss int %d..%d sein (live belegt): %r – Index 0/13 "
            "lässt den Creality-Host (motor_control_wrapper.so, MOTOR_GET "
            "DATA=0/13) in Klipper-SHUTDOWN laufen; Verhalten des Motors "
            "außerhalb 1..12 unbelegt" % (VARIABLE_INDEX_MIN, VARIABLE_INDEX_MAX, idx))


def counts_to_rad(counts, counts_per_rev=POS_COUNTS_PER_REV):
    """idx-11-Counts -> rad (= pos_fdb-Skala). Live 16.10.: 690 -> 1.35481
    (pos_fdb live 1.35489). KEIN Bus-Zugriff."""
    return counts * 2.0 * math.pi / counts_per_rev


def pos_rad_to_counts(rad, counts_per_rev=POS_COUNTS_PER_REV):
    """pos_fdb [rad] -> Counts (float, nicht gerundet). Live 16.10.:
    1.35489 -> 690.04. Single-Turn (0..counts_per_rev). KEIN Bus-Zugriff."""
    return rad * counts_per_rev / (2.0 * math.pi)


def counts_to_mm(counts, rotation_distance=POS_ROTATION_DISTANCE,
                 counts_per_rev=POS_COUNTS_PER_REV):
    """Counts -> mm innerhalb EINER Umdrehung (bzw. Count-Differenz -> mm).
    Live 16.10.: 800 Counts = 10 mm (rotation_distance 40, 3200/rev).
    ACHTUNG Single-Turn: idx 11/pos_fdb wrappen alle rotation_distance mm;
    absolute Achsposition gibt es nur über den Klipper-Schrittzähler."""
    return counts * float(rotation_distance) / counts_per_rev


def counts_delta(new, old, counts_per_rev=POS_COUNTS_PER_REV):
    """Wrap-korrigierte Differenz new-old in (-rev/2, +rev/2]. Live 16.10.:
    3090 -> 690 = +800 (X160 -> X170). Nur eindeutig, wenn sich die Achse
    zwischen zwei Abfragen um < 1/2 Umdrehung (20 mm) bewegt hat; 40-mm-
    Vielfache sind unsichtbar (Aliasing). KEIN Bus-Zugriff."""
    d = (new - old) % counts_per_rev
    return d - counts_per_rev if d > counts_per_rev / 2 else d


def build_variable_payload(idx):
    """0x08-Payload [idx], idx 1..12 (live 16.10.: `81 04 00 08 09`). Builder."""
    _check_variable_index(idx)
    return [idx]


def decode_variable(idx, data):
    """4 B 0x08-Antwort -> {'index', 'name', 'raw', 'fmt', 'float', 'int',
    'type', 'value'}. KEIN Bus-Zugriff.

    'float' = <f, 'int' = <I (UNSIGNED wie der Host, REA 16.10.),
    'fmt'/'type' = feste Host-Typkette (VARIABLE_HOST_FORMATS), 'value' =
    Wert in diesem Typ, 'name' = VARIABLE_NAMES (5..8 Host, 11 abgeleitet)
    oder None. Skalierung: pos_fdb/idx 11 belegt (counts_to_rad), spd/id/iq
    OFFEN.
    Live: 33 33 d3 3a -> 0.0016113 (idx 9), 52 04 00 00 -> 1106 (idx 11).
    """
    _check_variable_index(idx)
    raw = bytes(data)
    if len(raw) != VARIABLE_LEN:
        raise ValueError("0x08 idx %d: %d statt 4 Bytes: %r" % (idx, len(raw), raw))
    f = struct.unpack("<f", raw)[0]
    i = struct.unpack("<I", raw)[0]
    typ = VARIABLE_TYPES[idx]
    return {"index": idx, "name": VARIABLE_NAMES.get(idx), "raw": raw,
            "fmt": VARIABLE_HOST_FORMATS[idx], "float": f, "int": i,
            "type": typ, "value": f if typ == "float" else i}


def decode_error_bits(code):
    """err_code (int-Bitmaske) -> Liste (bit, key, text). KEIN Bus-Zugriff.
    Bit-Zuordnung BELEGT (motor_error_dict); woher der Code im 0x0C-Frame
    kommt, ist UNBELEGT. Unbekannte Bits -> (bit, None, 'unbekannt')."""
    return _decode_bits(code, MOTOR_ERROR_BITS)


def decode_warning_bits(code):
    """warning_code (int-Bitmaske) -> Liste (bit, key, text), s.
    decode_error_bits (motor_warning_dict Bit 0..2)."""
    return _decode_bits(code, MOTOR_WARNING_BITS)


def _decode_bits(code, table):
    if isinstance(code, bool) or not isinstance(code, int) or code < 0:
        raise ValueError("Bitmaske muss int >= 0 sein: %r" % (code,))
    out, bit = [], 0
    while code >> bit:
        if code >> bit & 1:
            key, text = table.get(bit, (None, "unbekannt"))
            out.append((bit, key, text))
        bit += 1
    return out


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

    def write_sys_param(self, param_id, value, confirm=False, allow_locked=False):
        """Sys-Param SCHREIBEN (0x06 [01, id] + <f) – GESCHÜTZT.

        ACHTUNG: verändert Motorregelparameter (z.B. ID 70 Positionsregler-Kp,
        188 Schleppfehler-Zeit) im laufenden Motor. Live FLÜCHTIG: nach
        Kaltstart wieder Werkswerte (Kp 300, err_time 0.01 …), nach Soft-
        Restart noch der geschriebene Wert (BEFUND-MOTOR.md 2.2).
        Ohne confirm=True -> PermissionError, es wird NICHTS gesendet.

        Zweite Sperre: IDs AUSSERHALB SYS_PARAM_CONFIGURABLE (166 IDs, im
        Plus-Log "is not configurable", z.B. ID 1 param_boot_key, 224/225
        param_flash_key/param_flash_checksum, Kalibrier-/Phasenwerte) nur mit
        ZUSÄTZLICHEM allow_locked=True – sonst PermissionError mit ID-Namen.
        Wert-Plausibilität (ValueError, vor allen Sperren): endlich,
        |Wert| <= float32-Max, Eingabe != 0 darf nicht auf 0.0 runden (1e-50).
        Rundung vorab prüfbar: float32_rounding(value).

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
        if param_id not in SYS_PARAM_CONFIGURABLE and allow_locked is not True:
            raise PermissionError(
                "write_sys_param(ID %d %s) gesperrt: laut Stock 'not configurable' "
                "(Schlüssel/Checksumme/Kalibrierung) – nur mit confirm=True UND "
                "allow_locked=True" % (param_id, SYS_PARAM_NAMES[param_id]))
        echo = self._tx(FUNC_SYS_PARAM, payload, TIMEOUT_SYS_PARAM)
        if echo != bytes(payload[2:]):
            raise RuntimeError("0x06 Schreib-Echo passt nicht: gesendet %s, "
                               "Antwort %s" % (bytes(payload[2:]).hex(), echo.hex()))
        return decode_sys_param_float(echo)

    def set_sys_param(self, param_id, value, confirm=False, allow_locked=False):
        """Alias für write_sys_param (gleicher Schutz, confirm=True nötig)."""
        return self.write_sys_param(param_id, value, confirm=confirm,
                                    allow_locked=allow_locked)

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

    # --- 0x0F Version / 0x08 Variablen (live 16.10.2026) ---

    def query_motor_version(self):
        """Motor-Version im Normalbetrieb (0x0F [00]) -> dict. LESEND.

        = Stock MOTOR_VERSION NUM=n DATA=0. Live 16.10. (Motor X):
        `81 04 00 0f 00` -> `f7 81 0f 00 0f 01 09 07 51 01 03 01 03 02 4b 01
        00 02` = fireware_ver 1.9.7.81, cmd_system_ver 1.3, protection_ver
        1.3, flash_param_ver 2.75, logger_ver 1.0 (s. decode_motor_version).
        retries=0, Timeout 1.0 s (UNBELEGT). Nicht verwechseln mit
        query_version() (Update-Pfad F0/00, live nie beobachtet).
        """
        return decode_motor_version(self._tx(FUNC_VERSION,
                                             build_motor_version_payload(),
                                             TIMEOUT_VERSION))

    motor_version = query_motor_version         # Wrapper-Name
    motor_version_normal = query_motor_version  # früherer Stub-Name

    def read_variable(self, idx):
        """Variable lesen (0x08 [idx]) -> dict (s. decode_variable). LESEND.

        = Stock MOTOR_GET NUM=n DATA=idx. NUR idx 1..12 (live 16.10. Scan an
        Motor X, Standby: 1-8 = 0, 9 = 0.0016113, 10 = 0.0008057, 11 = 1106,
        12 = 0); sonst ValueError OHNE Senden.
        Begründung der Grenze: Der Creality-G-Code erlaubt nur DATA 1..12
        (get_int maxval=12); DATA=0/13 führte live zum Host-Crash (Traceback
        16.10. 13:12:27: motor_get:1982 -> parse_num_to_byte(None) ->
        TypeError, kein Frame gesendet). Die Host-Typkette kennt zwar 13..18
        (VARIABLE_HOST_FORMATS), am Motor ist das aber live NICHT geprüft –
        bewusst gesperrt. Typ = feste Host-Kette (1..10 '<f', 11/12 '<I').
        Namen: 5..8 = pos_fdb/spd_fdb/id_fdb/iq_fdb (Host), 11 = pos_counts
        (abgeleitet, VARIABLE_NAMES).
        retries=0, Timeout 1.0 s (UNBELEGT).
        """
        payload = build_variable_payload(idx)
        return decode_variable(idx, self._tx(FUNC_VARIABLE, payload,
                                             TIMEOUT_VARIABLE))

    motor_get = read_variable  # Host-Name

    def read_position_feedback(self, with_counts=False):
        """pos_fdb (0x08 idx 5) -> float [rad]. LESEND.
        Name REA-belegt (NO_ERR_CODE_TO_GET); Skalierung live 16.10. BELEGT:
        pos_fdb = idx11 * 2*pi/3200, SINGLE-TURN (Wrap alle 40 mm bei
        rotation_distance 40; stromlos vor G28: idx 5 = 0.0, idx 11 = 1106).
        with_counts=True -> 2 Frames (idx 5 + idx 11), Rückgabe dict
        {'pos_fdb' [rad], 'counts' (idx 11, 0..3199), 'counts_from_rad',
        'mm_in_rev' (counts_to_mm, nur innerhalb der Umdrehung)}.
        Absolute Achsposition NUR über den Klipper-Schrittzähler."""
        rad = self.read_variable(IDX_POS_FDB)["value"]
        if not with_counts:
            return rad
        counts = self.read_position_counts()
        return {"pos_fdb": rad, "counts": counts,
                "counts_from_rad": pos_rad_to_counts(rad),
                "mm_in_rev": counts_to_mm(counts)}

    def read_position_counts(self):
        """0x08 idx 11 (eigener Name pos_counts, Host-Name unbekannt) -> int
        0..3199 Counts/Umdrehung. LESEND. Live 16.10. belegt (80 Counts/mm,
        Wrap alle 40 mm). Umrechnung: counts_to_mm / counts_delta."""
        return self.read_variable(IDX_POS_COUNTS)["value"]

    def read_speed_feedback(self):
        """spd_fdb (0x08 idx 6) -> float. LESEND. Einheit OFFEN (vermutlich
        rad/s, vgl. protection_param_prt_over_speed_rad_s)."""
        return self.read_variable(IDX_SPD_FDB)["value"]

    def read_current_feedback(self):
        """id_fdb/iq_fdb (0x08 idx 7, 8) -> {'id', 'iq', 'abs'}. LESEND,
        2 Frames. 'abs' = hypot(id, iq) wie der Host-Stats-Hook
        (Motor_Control.stats, overcurrent=|I|). Einheit vermutlich A."""
        i_d = self.read_variable(IDX_ID_FDB)["value"]
        i_q = self.read_variable(IDX_IQ_FDB)["value"]
        return {"id": i_d, "iq": i_q, "abs": math.hypot(i_d, i_q)}

    def read_encoder(self, *args, **kwargs):
        """GESPERRT – sendet KEINEN Frame.

        Alt-Falle: früher als 0x06 gedeutet; 0x06 ist live Sys-Param
        (Lesen/Schreiben von Regelparametern), kein Encoder. "Encoder" =
        0x08 idx 5 (pos_fdb, rad) / idx 11 (Counts 0..3199), beide SINGLE-
        TURN (live 16.10. belegt) -> read_position_feedback(with_counts=True)
        bzw. read_position_counts() nutzen.
        """
        raise NotImplementedError(
            "read_encoder gesperrt (0x06 = Sys-Param): Positionsrückmeldung = "
            "read_position_feedback(with_counts=True) (0x08 idx 5 pos_fdb rad / "
            "idx 11 Counts, single-turn 3200/Umdrehung)")

    # --- Stubs aus der Host-Tabelle Motor_cmd (REA 16.10., live nie
    # gesehen) – senden NICHTS ---

    def err_detail_data(self, *args):
        """TODO 0x13 err_detail_data: Host entpackt '<I8BfHfiiffffffffi'
        (ERR_DETAIL_FIELDS, @0x3a3f0); Request-Payload unbelegt."""
        raise NotImplementedError("TODO 0x13 err_detail_data: Request unbelegt, "
                                  "live nie gesehen")

    def cut_detail_data(self, *args):
        """TODO 0x14 cut_detail_data: Format wie 0x13 + 1 float
        (CUT_DETAIL_FORMAT); Request-Payload unbelegt."""
        raise NotImplementedError("TODO 0x14 cut_detail_data: Request unbelegt, "
                                  "live nie gesehen")

    def pos_data_sample(self, *args):
        """TODO 0x15 pos_data_sample (Host-Tabelle Motor_cmd); Inhalt unbelegt."""
        raise NotImplementedError("TODO 0x15 pos_data_sample: unbelegt")

    def spd_data_sample(self, *args):
        """TODO 0x16 spd_data_sample (Host-Tabelle Motor_cmd); Inhalt unbelegt."""
        raise NotImplementedError("TODO 0x16 spd_data_sample: unbelegt")

    def reboot(self, *args):
        """TODO 0x01 reboot (Host-Tabelle) – live nie gesehen, nicht senden."""
        raise NotImplementedError("TODO 0x01 reboot: nicht nachgebaut")

    def boot(self, *args):
        """GESPERRT 0x0B boot (MOTOR_BOOT „使系统进入boot模式“) – Bootloader,
        Haltemoment weg. Nie senden."""
        raise NotImplementedError("0x0B boot gesperrt (Bootloader)")

    def systemid(self, *args):
        """TODO 0x0D systemid (Host-Tabelle) – Payload unbelegt."""
        raise NotImplementedError("TODO 0x0D systemid: unbelegt")

    # --- Update-Pfad (Func 0xF0, live NIE beobachtet) ---
    # FREI (lesend): query_version (Sub 00), query_sector_size (Sub 03) – im
    # Binary reine "get"-Abfragen VOR request-update (1.7, 3 Retries), sie
    # ändern laut Ablauf keinen Zustand. Restrisiko: wie die Motor-App-FW
    # auf F0 reagiert, ist live unbelegt.
    # GESCHÜTZT (confirm=True, sonst PermissionError ohne Bus-Zugriff):
    # request_update, send_fw_len, update_end. Es gibt bewusst KEINE Methode
    # für Daten-Chunks/Erase (nur Builder + update_sequence, senden nichts).

    @staticmethod
    def _check_update_confirm(name, confirm):
        if confirm is not True:
            raise PermissionError(
                "%s gesperrt: F0-Update-Pfad (live nie beobachtet) kann den Motor "
                "in den Bootloader schicken – Haltemoment weg, Z kann absacken; "
                "nur mit confirm=True" % name)

    def query_version(self):
        """Version über Update-Pfad abfragen (Func 0xF0/Sub 0x00). Rohdaten.
        FREI: reine Abfrage (1.7 "get version", erster Schritt vor
        request-update); live nie beobachtet."""
        return self.bus.transact(self.addr, FUNC_FW_UPDATE,
                                 build_version_payload(),
                                 retries=UPDATE_RETRIES)

    def query_sector_size(self):
        """Sector-Größe abfragen (Func 0xF0/Sub 0x03). Rohdaten.
        FREI: reine Abfrage (1.7 "get sector size", vor request-update)."""
        return self.bus.transact(self.addr, FUNC_FW_UPDATE,
                                 build_sector_payload(),
                                 retries=UPDATE_RETRIES)

    def request_update(self, confirm=False):
        """Update anmelden (Func 0xF0/Sub 0x01) – GESCHÜTZT (confirm=True).

        RISIKO: Schaltet den Slave vermutlich in den Bootloader/Update-Modus
        (danach im Binary nur noch len/Daten/update_end, "wait to startup
        app"). Ein Achsmotor verliert dabei die Regelung/das Haltemoment –
        bei Z (0x83/0x84) kann das Bett/Portal mechanisch absacken. Live nie
        beobachtet; ohne confirm=True PermissionError, nichts gesendet."""
        self._check_update_confirm("request_update", confirm)
        self.bus.transact(self.addr, FUNC_FW_UPDATE,
                          build_update_request_payload(),
                          retries=UPDATE_RETRIES)

    def send_fw_len(self, fw_len, confirm=False):
        """FW-Länge senden (4B LE, Func 0xF0) – GESCHÜTZT (confirm=True).
        Teil des Update-Ablaufs nach request_update (gleiches Risiko);
        Länge wird vor der Sperre geprüft (ValueError)."""
        payload = build_fw_len_payload(fw_len)
        self._check_update_confirm("send_fw_len", confirm)
        self.bus.transact(self.addr, FUNC_FW_UPDATE, payload,
                          retries=UPDATE_RETRIES)

    def update_end(self, confirm=False):
        """Update abschließen (Func 0xF0/Sub 0x02, Timeout 500 ms,
        Beleg: mcu_util_485 Zustand 10 @0x15a64) – GESCHÜTZT (confirm=True).
        RISIKO: startet laut Binary die (evtl. unvollständige) App neu
        ("update end, wait to startup app") – Motor kurz/dauerhaft ohne
        Regelung."""
        self._check_update_confirm("update_end", confirm)
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
        "flash_param"-Enumeration läuft live über 0x06, s. read_sys_param).

        Hypothese REA 16.10. (VERMUTET, Vergleiche @0x4bbbc-0x4bd08):
        Payload [DATA] (+ 00 bei DATA 3/4, + ID bei DATA 5/6); DATA=6 =
        Lesen (Wert nach PARAMS_TYPE entpackt), DATA=5 = Schreiben (Wert
        gepackt: float 'f' (nativ), uint16_t '<H', uint32_t '>I' BIG-ENDIAN,
        int16_t '<h', sonst '>I'; Antwort Statusbyte 0 = Erfolg).
        Parameter NUM 1..10, ID (0), PARAMS (''), PARAMS_TYPE ('float')."""
        raise NotImplementedError("TODO MOTOR_FLASH_PARAM lesen (ID=%r): 0x07 "
                                  "live nie beobachtet" % (param_id,))

    def set_flash_param(self, param_id, value):
        """TODO: `MOTOR_FLASH_PARAM` schreiben – 0x07 live nie beobachtet."""
        raise NotImplementedError("TODO MOTOR_FLASH_PARAM schreiben (ID=%r): "
                                  "0x07 live nie beobachtet" % (param_id,))

    def pin_io(self, *args):
        """TODO: Pin-IO – live über die Main-MCU (set_pin_io, klippy.log:
        2259-2267/2492-2500; MOTOR_READ_ALL_PIN_IO 16.10. = Klipper-GPIO
        gpio_status PB9, PB10, !PB7, PB8, PB5, PB6, PA1, PB15), NICHT über den
        Motorbus; hier nicht nachgebaut."""
        raise NotImplementedError("TODO Pin-IO: läuft über Main-MCU, nicht RS485")



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
    import motor_mini  # nur Abgleich; motor_mini importiert motor.py NICHT
    check("motor_mini-Schreibliste = Stock-Config-IDs (configurable)",
          motor_mini.MINI_WRITABLE_IDS == set(SYS_PARAM_STOCK_CONFIGURED)
          <= SYS_PARAM_CONFIGURABLE)
    wrong = _FakeBus([("81 05 00 06 02 09", "f7 82 07 00 06 53 4a 18 43 ae")])
    check("FakeBus prüft Absenderadresse (Antwort von 0x82 an 0x81)",
          raises(AssertionError, Motor(wrong, 0x81).read_sys_param, 9))

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
    check("alle Write-Frames verbraucht", not bus.script)

    # 4b. Gesperrte IDs (not configurable) + Wert-Plausibilität (nichts gesendet)
    for pid in (1, 224, 225, 0, 11):
        check("ID %d (%s) ohne allow_locked gesperrt" % (pid, sys_param_name(pid)),
              raises(PermissionError, m0.write_sys_param, pid, 1.0, confirm=True)
              and raises(PermissionError, m0.set_sys_param, pid, 1.0, confirm=True))
    check("allow_locked ohne confirm weiter gesperrt",
          raises(PermissionError, m0.write_sys_param, 1, 1.0, allow_locked=True))
    check("allow_locked='yes' zählt nicht",
          raises(PermissionError, m0.write_sys_param, 1, 1.0, confirm=True,
                 allow_locked="yes"))
    for bad in (1e-50, -1e-50, 3.5e38, -1e39, float("inf"), float("nan")):
        check("Wert %r -> ValueError (auch mit confirm)" % (bad,),
              raises(ValueError, m0.write_sys_param, 70, bad, confirm=True)
              and raises(ValueError, build_sys_param_write_payload, 70, bad))
    check("float32-Grenzen ok", build_sys_param_write_payload(70, FLOAT32_MAX)[2:]
          == list(struct.pack("<f", FLOAT32_MAX))
          and build_sys_param_write_payload(70, 0.0)[2:] == [0, 0, 0, 0]
          and build_sys_param_write_payload(70, -0.0)[2:] == [0, 0, 0, 0x80])
    f32, rel = float32_rounding(0.1)
    check("float32_rounding 0.1", f32 != 0.1 and 0 < rel < 1e-7
          and float32_rounding(300.0) == (300.0, 0.0))
    check("Sperren/Plausibilität senden nichts", nobus.calls == [])
    bus = _FakeBus([
        # verifizierter Live-Frame (klippy.log.2026-10-05:16596), Echo 10-14:25260
        ("f7 81 09 00 06 01 46 00 00 96 43 8f", "f7 81 07 00 06 00 00 96 43 09"),
        # ID 1 mit allow_locked: SYNTHETISCH (nie live geschrieben), CRC neu
        ("81 09 00 06 01 01 00 00 80 3f", "f7 81 07 00 06 00 00 80 3f %02x"
         % crc8_cal(bytes.fromhex("07 00 06 00 00 80 3f"))),
    ])
    mw = Motor(bus, 0x81)
    check("gültiger Write ID70=300 = Live-Frame",
          mw.write_sys_param(70, 300.0, confirm=True) == 300.0)
    check("ID 1 nur mit confirm+allow_locked",
          mw.write_sys_param(1, 1.0, confirm=True, allow_locked=True) == 1.0
          and not bus.script)

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
        mm.pin_io,
        lambda: mm.set_e_stall_mode(1), lambda: mm.set_z_stall_mode(1),
        lambda: mm.get_flash_param(0), lambda: mm.set_flash_param(0, 1),
        mm.err_detail_data, mm.cut_detail_data, mm.pos_data_sample,
        mm.spd_data_sample, mm.reboot, mm.boot, mm.systemid])
    check("stubs not-implemented", stub_ok)
    check("stubs/Sperren senden nichts", nobus.calls == [])
    check("kurz-frame", raises(ValueError, parse_frame, bytes.fromhex("f7 81")))
    corrupt = bytearray(bytes.fromhex("f7 81 07 00 06 00 00 96 43 09"))
    corrupt[-1] ^= 0xFF
    check("crc-fehler", raises(ValueError, parse_frame, bytes(corrupt)))

    # 7b. 0x0F Version + 0x08 Variablen (LIVE-ABFRAGEN-2026-10-16.md,
    #     Scan-Rohdaten lokal, nicht im Repo; Motor X, Standby)
    V_RX = "f7 81 0f 00 0f 01 09 07 51 01 03 01 03 02 4b 01 00 02"
    bus = _FakeBus([("81 04 00 0f 00", V_RX), ("81 04 00 0f 00", V_RX)])
    v = Motor(bus, 0x81).query_motor_version()
    check("0x0F Version live (fw 1.9.7.81 ...)",
          v == {"fireware_ver": "1.9.7.81", "cmd_system_ver": "1.3",
                "protection_ver": "1.3", "flash_param_ver": "2.75",
                "logger_ver": "1.0",
                "raw": bytes.fromhex("01 09 07 51 01 03 01 03 02 4b 01 00")}
          and bus.calls[0][3] == {"timeout": TIMEOUT_VERSION, "retries": 0})
    check("0x0F Alias motor_version_normal",
          Motor(bus, 0x81).motor_version_normal()["flash_param_ver"] == "2.75"
          and not bus.script)
    check("0x0F Vollframe TX", build_frame(0x81, FUNC_VERSION,
                                           build_motor_version_payload())[1:-1]
          == bytes.fromhex("81 04 00 0f 00"))
    check("0x0F Längen-Guard",
          raises(ValueError, decode_motor_version, bytes(11))
          and raises(ValueError, decode_motor_version, bytes(13)))
    scan = {i: "00 00 00 00" for i in range(1, 13)}
    scan.update({9: "33 33 d3 3a", 10: "33 33 53 3a", 11: "52 04 00 00"})
    crcs = {i: "0a" for i in range(1, 13)}
    crcs.update({9: "e6", 10: "50", 11: "71"})  # Geräte-CRC aus dem Scan
    bus = _FakeBus([("81 04 00 08 %02x" % i,
                     "f7 81 07 00 08 %s %s" % (scan[i], crcs[i]))
                    for i in range(1, 13)])
    mv = Motor(bus, 0x81)
    res = {i: mv.read_variable(i) for i in range(1, 13)}
    check("0x08 idx 1..12 gelesen, retries=0",
          not bus.script and len(bus.calls) == 12 and all(
              c[2] == [i + 1] and c[3] == {"timeout": TIMEOUT_VARIABLE, "retries": 0}
              for i, c in enumerate(bus.calls)))
    check("0x08 idx 1-8/12 = 0", all(res[i]["value"] == 0 and res[i]["raw"] == bytes(4)
                                     for i in list(range(1, 9)) + [12]))
    check("0x08 idx 9 float 0.0016113",
          abs(res[9]["float"] - 0.0016113281017169356) < 1e-12
          and res[9]["type"] == "float" and res[9]["value"] == res[9]["float"])
    check("0x08 idx 10 float 0.0008057",
          abs(res[10]["value"] - 0.0008056640508584678) < 1e-12)
    check("0x08 idx 11 int 1106", res[11]["int"] == 1106 and res[11]["value"] == 1106
          and res[11]["type"] == "int" and res[11]["raw"] == bytes.fromhex("52 04 00 00"))
    check("0x08 VARIABLE_TYPES 1..12", sorted(VARIABLE_TYPES) == list(range(1, 13))
          and [VARIABLE_TYPES[i] for i in (9, 10, 11, 12)] == ["float", "float", "int", "int"])
    # REA 16.10.: feste Host-Typkette + Indexnamen
    check("0x08 Host-Kette 1..10,17,18 '<f' / 11..16 '<I'",
          sorted(VARIABLE_HOST_FORMATS) == list(range(1, 19))
          and all(VARIABLE_HOST_FORMATS[i] == "<f" for i in list(range(1, 11)) + [17, 18])
          and all(VARIABLE_HOST_FORMATS[i] == "<I" for i in range(11, 17)))
    check("0x08 Namen 5..8 (Host) + 11 (abgeleitet)",
          VARIABLE_NAMES_HOST == {5: "pos_fdb", 6: "spd_fdb", 7: "id_fdb", 8: "iq_fdb"}
          and VARIABLE_NAMES == dict(list(VARIABLE_NAMES_HOST.items()) + [(11, "pos_counts")])
          and res[5]["name"] == "pos_fdb" and res[1]["name"] is None
          and res[11]["name"] == "pos_counts" and res[9]["name"] is None
          and res[11]["fmt"] == "<I" and res[9]["fmt"] == "<f")
    check("0x08 int unsigned '<I' (ff ff ff ff -> 4294967295)",
          decode_variable(11, b"\xff\xff\xff\xff")["value"] == 0xFFFFFFFF
          and decode_variable(12, b"\x00\x00\x00\x80")["int"] == 0x80000000)
    check("0x08 Hostgrenze 13..18 bekannt, aber gesperrt",
          all(raises(ValueError, decode_variable, i, bytes(4)) for i in range(13, 19)))
    # Feedback-Helfer: synthetische Antworten (Bytes = struct.pack('<f'))
    fb = {5: 1.5, 6: -2.25, 7: 3.0, 8: 4.0}
    def _vrx(v):
        body = bytes([0x07, 0x00, FUNC_VARIABLE]) + struct.pack("<f", v)
        return "f7 81 %s %02x" % (body.hex(" "), crc8_cal(body))
    bus = _FakeBus([("81 04 00 08 %02x" % i, _vrx(fb[i])) for i in (5, 6, 7, 8)])
    mf = Motor(bus, 0x81)
    pos, spd, cur = (mf.read_position_feedback(), mf.read_speed_feedback(),
                     mf.read_current_feedback())
    check("Feedback pos idx5 / spd idx6 / id,iq idx7,8 + hypot",
          pos == 1.5 and spd == -2.25 and cur == {"id": 3.0, "iq": 4.0, "abs": 5.0}
          and [c[2] for c in bus.calls] == [[5], [6], [7], [8]] and not bus.script)
    check("motor_get Alias", Motor.motor_get is Motor.read_variable)
    # Skalierung pos_fdb / idx 11 (live 16.10., Messfahrt X, rotation_distance 40)
    live = [(50, 1.35489, 690), (100, 2.92568, 1490), (150, 4.49648, 2290),
            (160, 6.06728, 3090), (170, 1.35489, 690), (180, 2.92568, 1490),
            (185, 3.71108, 1890), (200, 6.06728, 3090), (250, 1.35470, 690)]
    check("Skalierung 690 -> 1.35481 rad, 3200/rev",
          POS_COUNTS_PER_REV == 3200 and abs(counts_to_rad(690) - 1.35481) < 1e-5
          and abs(counts_to_rad(3200) - 2 * math.pi) < 1e-12)
    check("Skalierung Live-Tabelle idx5 = idx11*2pi/3200 (Tol 2e-4 rad)",
          all(abs(counts_to_rad(c) - r) < POS_RAD_TOL
              and abs(pos_rad_to_counts(r) - c) < 0.1 for _, r, c in live))
    check("Skalierung 800 Counts = 10 mm, 80 Counts/mm",
          counts_to_mm(1490 - 690) == 10.0 and counts_to_mm(80) == 1.0
          and counts_to_mm(3200) == POS_ROTATION_DISTANCE == 40.0)
    check("Skalierung Wrap X160->X170 (+800), X185<->X150 (+-400)",
          counts_delta(690, 3090) == 800 and counts_delta(2290, 1890) == 400
          and counts_delta(1890, 2290) == -400 and counts_delta(690, 690) == 0)
    check("Skalierung Single-Turn: Live-Counts = (80*X + 3090) mod 3200",
          all((80 * x + 3090) % 3200 == c for x, _, c in live))
    def _vrxb(i, raw):
        body = bytes([0x07, 0x00, FUNC_VARIABLE]) + raw
        return ("81 04 00 08 %02x" % i,
                "f7 81 %s %02x" % (body.hex(" "), crc8_cal(body)))
    bus = _FakeBus([_vrxb(5, struct.pack("<f", 1.35489)),
                    _vrxb(11, struct.pack("<I", 690))])
    pf = Motor(bus, 0x81).read_position_feedback(with_counts=True)
    check("read_position_feedback(with_counts) idx 5+11",
          [c[2] for c in bus.calls] == [[5], [11]] and not bus.script
          and pf["counts"] == 690 and abs(pf["pos_fdb"] - 1.35489) < 1e-6
          and abs(pf["counts_from_rad"] - 690) < 0.1
          and abs(pf["mm_in_rev"] - 8.625) < 1e-9)
    bus = _FakeBus([_vrxb(11, struct.pack("<I", 3090))])
    check("read_position_counts idx 11",
          Motor(bus, 0x81).read_position_counts() == 3090 and not bus.script)
    # Motor_cmd-Tabelle + Adressen + Fehlerbits (REA-Konstanten)
    check("MOTOR_CMD Host-Tabelle", MOTOR_CMD["get"] == 0x08 == FUNC_VARIABLE
          and MOTOR_CMD["version"] == 0x0F and MOTOR_CMD["protection"] == 0x0C
          and MOTOR_CMD["sys_param"] == 0x06 and MOTOR_CMD["stall_mode"] == 0x11
          and MOTOR_CMD["read485_addr"] == MOTOR_CMD["set485_addr"] == 0x0E
          and MOTOR_CMD["dev_uuid"] == 0x12 and MOTOR_CMD["boot"] == 0x0B
          and [MOTOR_CMD[k] for k in ("err_detail_data", "cut_detail_data",
                                      "pos_data_sample", "spd_data_sample")]
          == [0x13, 0x14, 0x15, 0x16] and len(MOTOR_CMD) == 19)
    check("MOTOR_ADDR_BY_NUM 1..4 = MOTOR_ADDRS, '9' = FF",
          tuple(MOTOR_ADDR_BY_NUM[str(n)] for n in range(1, 5)) == MOTOR_ADDRS
          and MOTOR_ADDR_BY_NUM["9"] == 0xFF and MOTOR_ADDR_BY_NUM["0"] == 0x80)
    check("ERR_DETAIL_FORMAT 66 B / 16 Felder",
          struct.calcsize(ERR_DETAIL_FORMAT) == 66
          and struct.calcsize(CUT_DETAIL_FORMAT) == 70 and len(ERR_DETAIL_FIELDS) == 16
          and len(struct.unpack(ERR_DETAIL_FORMAT, bytes(66))) == 15 + 8)
    check("Fehlerbits 0..13 -> key781..794, Warnbits 0..2 -> key795..797",
          [MOTOR_ERROR_BITS[b][0] for b in range(14)] == ["key%d" % k for k in range(781, 795)]
          and [MOTOR_WARNING_BITS[b][0] for b in range(3)] == ["key795", "key796", "key797"])
    check("decode_error_bits 0x105 / 0x4000",
          [b[:2] for b in decode_error_bits(0x105)] == [(0, "key781"), (2, "key783"), (8, "key789")]
          and decode_error_bits(0x4000) == [(14, None, "unbekannt")]
          and decode_error_bits(0) == [])
    check("decode_warning_bits 0x6", [b[1] for b in decode_warning_bits(6)] == ["key796", "key797"]
          and raises(ValueError, decode_warning_bits, -1)
          and raises(ValueError, decode_error_bits, True))
    check("ERR_CODE_TO_UNION Bitwerte", sorted(ERR_CODE_TO_UNION) == [1, 4, 8, 32, 64, 128, 256]
          and all(struct.calcsize(v[0]) == 4 * len(v[1])
                  for v in ERR_CODE_TO_UNION.values() if v[0]))
    for bad in (0, 13, -1, 255, True, 1.0, None, "9"):
        check("0x08 idx-guard %r (Host-Crash 0/13)" % (bad,),
              raises(ValueError, Motor(nobus, 0x81).read_variable, bad))
    check("0x08 Längen-Guard", raises(ValueError, decode_variable, 9, bytes(3)))
    check("0x08 Guards senden nichts", nobus.calls == [])

    # 8. Update-Pfad (Doku 1.7 + Disassembly, live nie beobachtet)
    bus = _FakeBus([
        ("81 04 00 f0 00", "f7 81 06 00 f0 00 02 02 %02x"
         % crc8_cal(bytes.fromhex("06 00 f0 00 02 02"))),
    ])
    check("query_version sub 0x00 retries=2",
          list(Motor(bus, 0x81).query_version()) == [0, 2, 2]
          and bus.calls[0][3].get("retries") == UPDATE_RETRIES)
    check("retries=2 -> 3 Versuche wie Binary", UPDATE_RETRIES + 1 == 3)
    # F0-Sender: ohne confirm KEIN Bus-Zugriff
    for addr in MOTOR_ADDRS:
        mu = Motor(nobus, addr)
        for name, fn in [("request_update", lambda c: mu.request_update(**c)),
                         ("send_fw_len", lambda c: mu.send_fw_len(6, **c)),
                         ("update_end", lambda c: mu.update_end(**c))]:
            check("%s 0x%02x ohne confirm gesperrt" % (name, addr),
                  raises(PermissionError, fn, {})
                  and raises(PermissionError, fn, {"confirm": "yes"})
                  and raises(PermissionError, fn, {"confirm": 1}))
    check("send_fw_len Längen-Guard vor Sperre",
          raises(ValueError, Motor(nobus, 0x83).send_fw_len, -1))
    check("F0-Sperren senden nichts", nobus.calls == [])

    def _syn(addr, data_hex):
        # SYNTHETISCHE Antwort (F0 live nie beobachtet), CRC neu berechnet
        body = bytes([len(bytes.fromhex(data_hex)) + 3, 0x00, 0xF0]) \
            + bytes.fromhex(data_hex)
        return "f7 %02x %s %02x" % (addr, body.hex(" "), crc8_cal(body))
    bus = _FakeBus([
        ("83 04 00 f0 01", _syn(0x83, "00")),
        ("83 07 00 f0 06 00 00 00", _syn(0x83, "00")),
        ("83 04 00 f0 02", _syn(0x83, "00")),
    ])
    mu = Motor(bus, 0x83)
    mu.request_update(confirm=True)
    mu.send_fw_len(6, confirm=True)
    mu.update_end(confirm=True)
    check("F0-Sender mit confirm=True senden korrekt",
          not bus.script and [c[2] for c in bus.calls] == [[1], [6, 0, 0, 0], [2]]
          and all(c[3].get("retries") == UPDATE_RETRIES for c in bus.calls)
          and bus.calls[2][3].get("timeout") == UPDATE_END_TIMEOUT)
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
