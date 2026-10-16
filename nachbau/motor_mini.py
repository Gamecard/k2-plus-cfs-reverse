#!/usr/bin/env python3
"""motor_mini.py – Python-Nachbau von motor_control (K2 Closed-loop-Motoren, Kernfunktionen).

Ersetzt: motor_control_wrapper.cpython-39.so (armhf, proprietär).
Eigenständig (importiert motor.py NICHT); gleiche Regeln wie motor.py –
Details, Param-Namen-Tabelle und Update-Pfad dort.

LIVE-STAND 15.10.2026 (live-logs-2026-10-15/klippy.log*, lokal, nicht im Repo; s. motor.py):
- LESEND (senden erlaubt): read_sys_param (0x06 [02, id]),
  check_protection (0x0C [0b] -> 8 B), get_stall_mode (0x11 [04]),
  read_485_addr (0x0E [02]), read_uuid (0x12 [02] -> 8 B),
  query_motor_version (0x0F [00] -> 12 B) und read_variable (0x08 [idx],
  NUR idx 1..12 -> 4 B) – beide live 16.10.2026 an Motor X
  (LIVE-ABFRAGEN-2026-10-16.md)
- set_mode (0x11 [01]/[02]/[03], Antwort = Echo) – sendet, live belegt
- write_sys_param: 0x06 [01, id] + <f – sendet NUR mit confirm=True
  (sonst PermissionError); verändert Motorregelparameter, FLÜCHTIG bis
  Kaltstart. Nur IDs aus MINI_WRITABLE_IDS (= Stock-[motor_control]-IDs);
  alle anderen -> PermissionError, Verweis auf motor.py (Namen-Tabelle +
  allow_locked). Wert-Plausibilität wie motor.py (1e-50 -> ValueError).
- update_sys_params: 0x06 [03] – NUR mit confirm=True (wie motor.py)
- Reine Builder: build_sys_param_write_payload, build_sys_param_update_payload
- read_position_feedback / read_speed_feedback / read_current_feedback:
  0x08 idx 5 / 6 / 7+8 (pos_fdb/spd_fdb/id_fdb/iq_fdb, REA-belegt 16.10.;
  Skalierung offen)
- read_encoder: GESPERRT (0x06-Altfalle) -> read_position_feedback (idx 5)
- WARNUNG: Creality-Host MOTOR_GET DATA=0/13 -> Klipper-SHUTDOWN (live 16.10.,
  2x reproduziert; Ursache belegt: get_int -> None -> parse_num_to_byte
  TypeError VOR dem Senden) -> read_variable lehnt Index außerhalb 1..12 ab.
- query_version: Update-Pfad (0xF0/00, Decompile; live nie beobachtet) –
  FREI, reine "get version"-Abfrage (erster Schritt vor request-update).
  Die zustandsändernden F0-Sender (request_update/send_fw_len/update_end)
  gibt es hier bewusst NICHT – nur in motor.py, dort mit confirm-Sperre.

Adressen: X/Y/Z/Z1 = 0x81-0x84 nur über serial_485 (ttyS5). Der Extruder ist
NICHT 0x85 auf dem RS485 (antwortet nie, klippy.log.2026-10-05:4013-4280),
sondern nur transparent über die (vermutlich) Nozzle-MCU unter 0x81
(klippy.log:2375/2376) – nicht in diesem Modul.

Retries/Timeouts wie Stock: alle Motor-Kommandos retries=0 (1 Sendung);
Timeout 0x06/0x12 = 1.0 s, 0x0C/0x0E = 0.05 s, 0x11/0x0F/0x08 unbelegt (1.0 s).
"""

import math
import struct

from serial485 import Serial485  # noqa: F401 (für echten Betrieb)

FUNC_SYS_PARAM = 0x06
FUNC_READ_ENCODER = FUNC_SYS_PARAM  # Alt-Name, nur Kompatibilität – nicht senden!
FUNC_PROTECTION = 0x0C
FUNC_ADDR = 0x0E
FUNC_SET_MODE = 0x11
FUNC_UUID = 0x12
FUNC_FW_UPDATE = 0xF0      # Update-Pfad, Sub-Codes aus Decompile
FUNC_VARIABLE = 0x08       # live 16.10.: [idx] -> 4 B LE
FUNC_VERSION = 0x0F        # live 16.10.: [00] -> 12 B
SUB_VERSION_GET = 0x00
VERSION_FIELDS = (("fireware_ver", 4), ("cmd_system_ver", 2),
                  ("protection_ver", 2), ("flash_param_ver", 2),
                  ("logger_ver", 2))  # 12 B, Namen = Wrapper-Strings
VARIABLE_INDEX_MIN, VARIABLE_INDEX_MAX = 1, 12  # 0/13: Host-Crash (s. oben)
# Feste Host-Typkette (REA 16.10., motor_get Py-Z. 1987/1989): 1..10 '<f',
# 11/12 '<I' (UNSIGNED; 13..16 '<I', 17/18 '<f' per G-Code unerreichbar).
VARIABLE_TYPES = dict([(i, "float") for i in range(1, 11)]
                      + [(11, "int"), (12, "int")])
# Indexnamen (REA belegt, Host-Dict NO_ERR_CODE_TO_GET); 1-4/9-12 ohne Namen
VARIABLE_NAMES = {5: "pos_fdb", 6: "spd_fdb", 7: "id_fdb", 8: "iq_fdb"}
SUB_SYS_WRITE = 0x01
SUB_SYS_READ = 0x02
SUB_SYS_UPDATE = 0x03
SUB_PROT_CHECK = 0x0B      # [05] nie beantwortet -> wird nie gesendet
SUB_STALL_GET = 0x04
STALL_MODES = (0x01, 0x02, 0x03)
SUB_GET_VERSION = 0x00
SUB_GET_SECTOR = 0x03
SUB_REQUEST_UPDATE = 0x01
SYS_PARAM_COUNT = 227      # live IDs 0..226

MOTOR_RETRIES = 0          # live "retries = 0" bei allen Motor-Sendungen
TIMEOUT_SYS_PARAM = 1.0
TIMEOUT_PROTECTION = 0.05
TIMEOUT_ADDR = 0.05
TIMEOUT_UUID = 1.0
TIMEOUT_STALL = 1.0        # unbelegt (nie Timeout beobachtet)
TIMEOUT_VERSION = 1.0      # 0x0F unbelegt (live nur 1 Antwort, kein Timeout)
TIMEOUT_VARIABLE = 1.0     # 0x08 unbelegt
# Update-Pfad: Binary bricht nach 3 Fehlversuchen ab (mcu_util_485
# `cmp r6,#3` @0x1562c), serial485 macht retries+1 Versuche -> 2.
UPDATE_RETRIES = 2

MOTOR_ADDRS = (0x81, 0x82, 0x83, 0x84)  # X/Y/Z/Z1 über serial_485
SYS_UPDATE_OK = 0x01       # Antwort auf [03] (klippy.log:2875)
FLOAT32_MAX = 3.4028234663852886e38

# Schreibbare IDs (motor_mini hat keine Namen-/Configurable-Tabelle):
# genau die IDs, die die Stock-K2-Plus-Config [motor_control] nach jedem
# Kaltstart schreibt (= motor.SYS_PARAM_STOCK_CONFIGURED, Teilmenge von
# motor.SYS_PARAM_CONFIGURABLE). Live geschrieben beobachtet: 70 (Pos-Kp,
# klippy.log.2026-10-05:16596, 10-12:10700), 86, 87, 188 (Schleppfehler-
# Zeit). 226 nur X/Y, Motor-FW kennt sie nicht (0-B-Antwort). Weitere IDs
# (auch "not configurable" wie 1 param_boot_key) -> motor.py mit
# confirm=True (+ allow_locked=True). Abgleich im motor.py-Selbsttest.
MINI_WRITABLE_IDS = frozenset((70, 86, 87, 102, 103, 116, 118, 119, 120, 121,
                               187, 188, 226))


def decode_sys_param_float(data):
    """4 Bytes 0x06-Antwort -> Float (`<f`), ohne Bus-Zugriff.
    Beleg: 00 00 96 43 -> 300.0, 00 00 c8 42 -> 100.0 (ID 70)."""
    raw = bytes(data)
    if len(raw) != 4:
        raise ValueError("0x06-Payload muss 4 Bytes haben: %r" % (raw,))
    return struct.unpack("<f", raw)[0]


def _check_param_id(param_id):
    if isinstance(param_id, bool) or not isinstance(param_id, int) \
            or not 0 <= param_id < SYS_PARAM_COUNT:
        raise ValueError("Param-ID muss int 0..226 sein: %r" % (param_id,))


def build_sys_param_write_payload(param_id, value):
    """[01, id] + <f LE – REINER BUILDER, sendet nichts.
    Live: klippy.log.2026-10-05:16596 `81 09 00 06 01 46 00 00 96 43`
    (ID 70 = 300.0), Vollframe f7 81 09 00 06 01 46 00 00 96 43 8f.
    Wert: endlich, |Wert| <= float32-Max, != 0 darf nicht auf 0.0 runden
    (struct schreibt 1e-50 sonst still als 0.0) -> sonst ValueError."""
    _check_param_id(param_id)
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or abs(value) > FLOAT32_MAX:
        raise ValueError("Sys-Param-Wert muss endliche float32-Zahl sein: %r"
                         % (value,))
    try:
        raw = struct.pack("<f", value)
    except OverflowError:
        raise ValueError("Sys-Param-Wert passt nicht in float32: %r" % (value,))
    if value != 0 and struct.unpack("<f", raw)[0] == 0.0:
        raise ValueError("Sys-Param-Wert %r rundet in float32 auf 0.0" % (value,))
    return [SUB_SYS_WRITE, param_id] + list(raw)


def build_sys_param_update_payload():
    """[03] "Updata param" -> Antwort [01] (klippy.log:2869/2875). Builder."""
    return [SUB_SYS_UPDATE]


def decode_motor_version(data):
    """12 B 0x0F-Antwort -> dict (fireware_ver "a.b.c.d", Rest "major.minor",
    minor dezimal; 'raw'). Live: 01 09 07 51 01 03 01 03 02 4b 01 00 ->
    1.9.7.81 / 1.3 / 1.3 / 2.75 / 1.0."""
    raw = bytes(data)
    if len(raw) != 12:
        raise ValueError("0x0F: 12 Bytes erwartet: %r" % (raw,))
    out, pos = {}, 0
    for name, n in VERSION_FIELDS:
        out[name] = ".".join(str(b) for b in raw[pos:pos + n])
        pos += n
    out["raw"] = raw
    return out


def _check_variable_index(idx):
    if isinstance(idx, bool) or not isinstance(idx, int) \
            or not VARIABLE_INDEX_MIN <= idx <= VARIABLE_INDEX_MAX:
        raise ValueError("0x08-Index muss int 1..12 sein: %r – MOTOR_GET "
                         "DATA=0/13 lässt den Creality-Host in Klipper-SHUTDOWN "
                         "laufen; außerhalb 1..12 unbelegt" % (idx,))


def decode_variable(idx, data):
    """4 B 0x08-Antwort -> {'index','name','raw','float' (<f),'int' (<I wie
    Host),'type' (feste Host-Kette),'value'}. Namen nur 5..8, Skalierung offen."""
    _check_variable_index(idx)
    raw = bytes(data)
    if len(raw) != 4:
        raise ValueError("0x08 idx %d: 4 Bytes erwartet: %r" % (idx, raw))
    f, i = struct.unpack("<f", raw)[0], struct.unpack("<I", raw)[0]
    typ = VARIABLE_TYPES[idx]
    return {"index": idx, "name": VARIABLE_NAMES.get(idx), "raw": raw,
            "float": f, "int": i, "type": typ,
            "value": f if typ == "float" else i}


class MotorMini:
    """Closed-loop-Motor (Kernfunktionen)."""

    def __init__(self, bus, addr):
        if addr not in MOTOR_ADDRS:
            raise ValueError("unbekannte Motor-Adresse: 0x%02x" % addr)
        self.bus = bus
        self.addr = addr

    def _tx(self, fun, payload, timeout):
        return bytes(self.bus.transact(self.addr, fun, payload, timeout=timeout,
                                       retries=MOTOR_RETRIES))

    def read_sys_param(self, param_id):
        """0x06 [02, id] -> float; 0-Byte-Antwort -> None (ID unbekannt,
        live ID 226, klippy.log:2859/2865); andere Länge -> ValueError (live
        ID 10 mit 2 B, Format unbelegt). Live: klippy.log:2511/2517 ID 9 -> 123.58."""
        _check_param_id(param_id)
        raw = self._tx(FUNC_SYS_PARAM, [SUB_SYS_READ, param_id], TIMEOUT_SYS_PARAM)
        if not raw:
            return None
        if len(raw) != 4:
            raise ValueError("0x06 ID %d: %d-Byte-Antwort %r (Format unbelegt)"
                             % (param_id, len(raw), raw))
        return decode_sys_param_float(raw)

    def write_sys_param(self, param_id, value, confirm=False):
        """0x06 SCHREIBEN – nur mit confirm=True, sonst PermissionError ohne
        Senden. Verändert Motorregelparameter (flüchtig bis Kaltstart). Nur
        IDs aus MINI_WRITABLE_IDS, sonst PermissionError (-> motor.py).
        Live-Echo: klippy.log.2026-10-12:10700/10706. Echo-Fehler -> RuntimeError."""
        payload = build_sys_param_write_payload(param_id, value)
        if confirm is not True:
            raise PermissionError("write_sys_param gesperrt: verändert Motorregel"
                                  "parameter – nur mit confirm=True")
        if param_id not in MINI_WRITABLE_IDS:
            raise PermissionError(
                "write_sys_param(ID %d) in motor_mini gesperrt: nur Stock-"
                "konfigurierte IDs %s; andere IDs über motor.py (Namen-Tabelle, "
                "allow_locked)" % (param_id, sorted(MINI_WRITABLE_IDS)))
        echo = self._tx(FUNC_SYS_PARAM, payload, TIMEOUT_SYS_PARAM)
        if echo != bytes(payload[2:]):
            raise RuntimeError("0x06 Schreib-Echo passt nicht: %s" % echo.hex())
        return decode_sys_param_float(echo)

    def update_sys_params(self, confirm=False):
        """0x06 [03] "Updata param" – nur mit confirm=True, sonst
        PermissionError ohne Senden. Stock: einmal je Motor am Ende der
        Enumeration (klippy.log:2869 -> 2875 `f7 81 04 00 06 01 21`); Wirkung
        unbelegt (live NICHT dauerhaft). Antwort != [01] -> RuntimeError."""
        if confirm is not True:
            raise PermissionError("update_sys_params gesperrt: nur mit confirm=True")
        ans = self._tx(FUNC_SYS_PARAM, build_sys_param_update_payload(),
                       TIMEOUT_SYS_PARAM)
        if ans != bytes([SYS_UPDATE_OK]):
            raise RuntimeError("0x06 [03]: Antwort %s statt 01" % ans.hex())
        return True

    def check_protection(self):
        """0x0C [0b] -> 8 B Schutzstatus (live immer 8×00; klippy.log:2441/2447).
        [05] wird nie gesendet (nie beantwortet)."""
        ans = self._tx(FUNC_PROTECTION, [SUB_PROT_CHECK], TIMEOUT_PROTECTION)
        if len(ans) != 8:
            raise ValueError("0x0C: 8 Bytes erwartet: %r" % (ans,))
        return ans

    def set_mode(self, mode, *extra):
        """Stall-Mode setzen (0x11 [01]/[02]/[03]), Antwort = Echo.
        Live: klippy.log.2026-10-12:2864/2870, klippy.log:2439, 10-12:31436/31454.
        Genau 1 Byte – das frühere "[02 00]" war die Antwort auf [04]."""
        if extra or isinstance(mode, bool) or not isinstance(mode, int) \
                or mode not in STALL_MODES:
            raise ValueError("Stall-Mode muss genau ein Byte 1/2/3 sein: %r"
                             % ((mode,) + extra,))
        echo = self._tx(FUNC_SET_MODE, [mode], TIMEOUT_STALL)
        if echo != bytes([mode]):
            raise RuntimeError("0x11 Echo passt nicht: %s" % echo.hex())
        return mode

    def get_stall_mode(self):
        """0x11 [04] -> [mode, 00] (klippy.log.2026-10-12:31409/31432)."""
        ans = self._tx(FUNC_SET_MODE, [SUB_STALL_GET], TIMEOUT_STALL)
        if len(ans) != 2:
            raise ValueError("0x11 [04]: 2 Bytes erwartet: %r" % (ans,))
        return ans[0]

    def read_485_addr(self):
        """0x0E [02] -> eigene Adresse (klippy.log:2345/2356)."""
        ans = self._tx(FUNC_ADDR, [0x02], TIMEOUT_ADDR)
        if len(ans) != 1:
            raise ValueError("0x0E: 1 Byte erwartet: %r" % (ans,))
        return ans[0]

    def read_uuid(self):
        """0x12 [02] -> 8 B UUID, Präfix cd ab (klippy.log:2349/2362)."""
        ans = self._tx(FUNC_UUID, [0x02], TIMEOUT_UUID)
        if len(ans) != 8:
            raise ValueError("0x12: 8 Bytes erwartet: %r" % (ans,))
        return ans

    dev_uuid = read_uuid

    def query_motor_version(self):
        """0x0F [00] -> dict (MOTOR_VERSION DATA=0; live 16.10. Motor X
        `81 04 00 0f 00` -> fw 1.9.7.81 ...). retries=0, Timeout unbelegt."""
        return decode_motor_version(self._tx(FUNC_VERSION, [SUB_VERSION_GET],
                                             TIMEOUT_VERSION))

    motor_version = query_motor_version

    def read_variable(self, idx):
        """0x08 [idx] -> dict (MOTOR_GET DATA=idx), NUR idx 1..12, sonst
        ValueError ohne Senden (Host-Crash bei 0/13). Live-Scan 16.10. Motor X
        (Standby): 1-8 = 0, 9 = 0.0016113, 10 = 0.0008057, 11 = 1106, 12 = 0."""
        _check_variable_index(idx)
        return decode_variable(idx, self._tx(FUNC_VARIABLE, [idx],
                                             TIMEOUT_VARIABLE))

    def read_position_feedback(self):
        """pos_fdb (0x08 idx 5) -> float; Einheit/Skalierung offen."""
        return self.read_variable(5)["value"]

    def read_speed_feedback(self):
        """spd_fdb (0x08 idx 6) -> float; Einheit offen."""
        return self.read_variable(6)["value"]

    def read_current_feedback(self):
        """id_fdb/iq_fdb (idx 7, 8) -> {'id','iq','abs'=hypot} wie Host-Stats."""
        i_d, i_q = self.read_variable(7)["value"], self.read_variable(8)["value"]
        return {"id": i_d, "iq": i_q, "abs": math.hypot(i_d, i_q)}

    def read_encoder(self, *args, **kwargs):
        """GESPERRT – sendet KEINEN Frame (0x06-Altfalle: 0x06 = Sys-Param).
        Positionsrückmeldung = read_position_feedback (0x08 idx 5 pos_fdb,
        Einheit/Skalierung offen)."""
        raise NotImplementedError(
            "read_encoder gesperrt (0x06 = Sys-Param): Positionsrückmeldung = "
            "read_position_feedback() (0x08 idx 5 pos_fdb, Skalierung offen)")

    def query_version(self):
        """Version über Update-Pfad abfragen (Func 0xf0/Sub 0x00). Rohdaten.
        FREI: reine Abfrage ("get version", 1.7), ändert keinen Zustand."""
        return self.bus.transact(self.addr, FUNC_FW_UPDATE, [SUB_GET_VERSION],
                                 retries=UPDATE_RETRIES)


# --- Selbsttest mit Live-Frames (keine Hardware nötig) ---
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

    from serial485 import parse_frame, build_frame, crc8_cal as _crc8

    class _Bus:
        """Prüft TX gegen Log-Bytes (data_send-Form ohne f7/CRC) und spielt
        Log-Antworten (mit Geräte-CRC) ab."""

        def __init__(self, script):
            self.script = list(script)
            self.calls = []

        def transact(self, addr, fun, data=(), **kw):
            self.calls.append((addr, fun, list(data), kw))
            assert self.script, "unerwarteter Sendeversuch"
            want_tx, rx = self.script.pop(0)
            frame, want = build_frame(addr, fun, list(data)), bytes.fromhex(want_tx)
            assert (frame if want[0] == 0xF7 else frame[1:-1]) == want
            raw = bytes.fromhex(rx)
            rfun, rdata = parse_frame(raw)
            assert raw[1] == addr and rfun == fun
            return rdata

    # 1. Dekodierung / Builder gegen Log
    check("0x06 = 300.0", decode_sys_param_float(bytes.fromhex("00 00 96 43")) == 300.0)
    check("0x06 = 100.0", decode_sys_param_float(bytes.fromhex("00 00 c8 42")) == 100.0)
    check("write-builder = Log-TX + verifizierter Vollframe",
          build_frame(0x81, 6, build_sys_param_write_payload(70, 300.0))
          == bytes.fromhex("f7 81 09 00 06 01 46 00 00 96 43 8f")
          and bytes(build_sys_param_write_payload(70, 100.0))
          == bytes.fromhex("01 46 00 00 c8 42"))
    check("update-builder [03]", build_sys_param_update_payload() == [3])
    for bad in [(227, 1.0), (True, 1.0), (70, float("nan")), (70, True)]:
        check("builder guard %r" % (bad,),
              raises(ValueError, build_sys_param_write_payload, *bad))

    # 2. Lesende Kommandos gegen Live-Frames
    bus = _Bus([
        ("81 05 00 06 02 09", "f7 81 07 00 06 20 29 f7 42 59"),  # klippy.log:2511/2517
        ("81 05 00 06 02 0a", "f7 81 05 00 06 00 00 90"),        # :2520/2526
        ("81 05 00 06 02 e2", "f7 81 03 00 06 af"),              # :2859/2865
        ("81 04 00 0c 0b", "f7 81 0b 00 0c 00 00 00 00 00 00 00 00 fb"),  # :2441/2447
        ("81 04 00 11 04", "f7 81 05 00 11 02 00 0e"),           # 10-12:31409/31432
        ("81 04 00 11 03", "f7 81 04 00 11 03 13"),              # 10-12:31436/31454
        ("81 04 00 0e 02", "f7 81 04 00 0e 81 00"),              # :2387/2393
        # 0x12: TX klippy.log:2349-Form; Antwort PLATZHALTER-UUID, CRC neu
        ("81 04 00 12 02", "f7 81 0b 00 12 cd ab 00 00 00 00 00 00 ad"),
        ("81 09 00 06 01 46 00 00 c8 42", "f7 81 07 00 06 00 00 c8 42 d4"),  # 10-12:10700/10706
        ("81 04 00 06 03", "f7 81 04 00 06 01 21"),              # klippy.log:2869/2875
        # verifizierter Live-Vollframe (10-05:16596) -> Echo (10-14:25260)
        ("f7 81 09 00 06 01 46 00 00 96 43 8f", "f7 81 07 00 06 00 00 96 43 09"),
    ])
    mm = MotorMini(bus, 0x81)
    check("ID 9 = 123.58", abs(mm.read_sys_param(9) - 123.58) < 1e-3
          and bus.calls[0][3] == {"timeout": 1.0, "retries": 0})
    check("ID 10 (2 B) -> ValueError", raises(ValueError, mm.read_sys_param, 10))
    check("ID 226 (0 B) -> None", mm.read_sys_param(226) is None)
    check("check_protection 8x00", mm.check_protection() == bytes(8)
          and bus.calls[3][3] == {"timeout": 0.05, "retries": 0})
    check("get_stall_mode -> 2", mm.get_stall_mode() == 2)
    check("set_mode 3 Echo", mm.set_mode(3) == 3)
    check("read_485_addr -> 0x81", mm.read_485_addr() == 0x81)
    check("read_uuid 8 B cd ab", mm.read_uuid()[:2] == b"\xcd\xab")
    nobus = _Bus([])
    check("write ohne confirm gesperrt",
          raises(PermissionError, MotorMini(nobus, 0x81).write_sys_param, 70, 100.0)
          and nobus.calls == [])
    check("write confirm=True Echo", mm.write_sys_param(70, 100.0, confirm=True) == 100.0)
    check("update [03] -> [01]", mm.update_sys_params(confirm=True) is True)
    check("Live-Vollframe ID70=300", mm.write_sys_param(70, 300.0, confirm=True) == 300.0)
    check("alle Script-Frames verbraucht", not bus.script)
    m0 = MotorMini(nobus, 0x83)
    check("update ohne confirm gesperrt",
          raises(PermissionError, m0.update_sys_params)
          and raises(PermissionError, m0.update_sys_params, confirm="yes"))
    for pid in (1, 9, 224, 225, 0):
        check("ID %d nicht in MINI_WRITABLE_IDS gesperrt" % pid,
              raises(PermissionError, m0.write_sys_param, pid, 1.0, confirm=True))
    for bad in (1e-50, -1e-50, 3.5e38, float("inf")):
        check("Wert %r -> ValueError" % (bad,),
              raises(ValueError, m0.write_sys_param, 70, bad, confirm=True))
    check("Sperren senden nichts", nobus.calls == [])

    # 3. Guards / Sperren senden nichts
    check("addr-guard 0x85", raises(ValueError, MotorMini, None, 0x85))
    for args in [(), (0x9643,)]:
        check("read_encoder%r wirft" % (args,),
              raises(NotImplementedError, MotorMini(nobus, 0x81).read_encoder, *args))
    for bad in [(), (0,), (4,), (256,), (True,), (0x02, 0x00)]:
        check("set_mode guard %r" % (bad,),
              raises((TypeError, ValueError), MotorMini(nobus, 0x81).set_mode, *bad))
    check("Guards senden nichts", nobus.calls == [])

    # 3b. 0x0F / 0x08 gegen Live-Frames 16.10. (Motor X, Standby)
    bus = _Bus([
        ("81 04 00 0f 00",
         "f7 81 0f 00 0f 01 09 07 51 01 03 01 03 02 4b 01 00 02"),
        ("81 04 00 08 01", "f7 81 07 00 08 00 00 00 00 0a"),
        ("81 04 00 08 09", "f7 81 07 00 08 33 33 d3 3a e6"),
        ("81 04 00 08 0a", "f7 81 07 00 08 33 33 53 3a 50"),
        ("81 04 00 08 0b", "f7 81 07 00 08 52 04 00 00 71"),
        ("81 04 00 08 0c", "f7 81 07 00 08 00 00 00 00 0a"),
    ])
    mv = MotorMini(bus, 0x81)
    v = mv.query_motor_version()
    check("0x0F Version 1.9.7.81 / 1.3 / 1.3 / 2.75 / 1.0",
          (v["fireware_ver"], v["cmd_system_ver"], v["protection_ver"],
           v["flash_param_ver"], v["logger_ver"])
          == ("1.9.7.81", "1.3", "1.3", "2.75", "1.0")
          and len(v["raw"]) == 12
          and bus.calls[0][3] == {"timeout": TIMEOUT_VERSION, "retries": 0})
    r1, r9, r10, r11, r12 = [mv.read_variable(i) for i in (1, 9, 10, 11, 12)]
    check("0x08 idx 1 = 0.0", r1["value"] == 0.0 and r1["type"] == "float"
          and bus.calls[1][3] == {"timeout": TIMEOUT_VARIABLE, "retries": 0})
    check("0x08 idx 9 = 0.0016113", abs(r9["float"] - 0.0016113281) < 1e-9)
    check("0x08 idx 10 = 0.0008057", abs(r10["value"] - 0.0008056641) < 1e-9)
    check("0x08 idx 11 = 1106 (int)", r11["int"] == 1106 and r11["value"] == 1106)
    check("0x08 idx 12 = 0 (int)", r12["value"] == 0 and r12["type"] == "int")
    check("0x0F/0x08 Script verbraucht", not bus.script)
    check("0x08 Namen 5..8 + '<I' unsigned",
          decode_variable(5, bytes(4))["name"] == "pos_fdb" and r1["name"] is None
          and decode_variable(8, bytes(4))["name"] == "iq_fdb"
          and decode_variable(11, b"\xff\xff\xff\xff")["value"] == 0xFFFFFFFF)
    def _vrx(v):
        body = bytes([0x07, 0x00, FUNC_VARIABLE]) + struct.pack("<f", v)
        return "f7 81 %s %02x" % (body.hex(" "), _crc8(body))
    bus = _Bus([("81 04 00 08 %02x" % i, _vrx(v))
                for i, v in ((5, 0.5), (6, 1.0), (7, 3.0), (8, -4.0))])
    mf = MotorMini(bus, 0x81)
    check("Feedback pos/spd/id,iq (idx 5..8) + hypot",
          (mf.read_position_feedback(), mf.read_speed_feedback(),
           mf.read_current_feedback()) == (0.5, 1.0, {"id": 3.0, "iq": -4.0, "abs": 5.0})
          and not bus.script)
    for bad in (0, 13, -1, True, 1.0, None):
        check("0x08 idx-guard %r" % (bad,),
              raises(ValueError, MotorMini(nobus, 0x81).read_variable, bad))
    check("0x0F Längen-Guard", raises(ValueError, decode_motor_version, bytes(11)))
    check("0x08 Guards senden nichts", nobus.calls == [])

    # 4. query_version (Update-Pfad, retries=2 = 3 Versuche)
    class _Rec:
        calls = []

        def transact(self, addr, fun, data=(), **kw):
            self.calls.append((addr, fun, list(data), kw))
            return b""
    rec = _Rec()
    MotorMini(rec, 0x82).query_version()
    check("query_version retries=2 (3 Versuche)",
          rec.calls[0][1:3] == (FUNC_FW_UPDATE, [SUB_GET_VERSION])
          and rec.calls[0][3].get("retries") == UPDATE_RETRIES == 2)

    print("SELBSTTEST: %s" % ("ok" if fails == 0 else "%d FEHLER" % fails))
    return fails


if __name__ == "__main__":
    raise SystemExit(selftest())
