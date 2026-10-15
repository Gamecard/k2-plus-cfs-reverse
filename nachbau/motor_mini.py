#!/usr/bin/env python3
"""motor_mini.py – Python-Nachbau von motor_control (K2 Closed-loop-Motoren, Kernfunktionen).

Ersetzt: motor_control_wrapper.cpython-39.so (armhf, proprietär).

Umfang:
- read_encoder: GESPERRT (NotImplementedError, sendet nichts) – KORREKTUR
  15.10.2026: Func 0x06 ist KEIN Encoder-Lesen, sondern sehr wahrscheinlich
  Sys-Param-Schreiben (Live-Payload = LE-Float 300.0/100.0 = MOTOR_SYS_PARAM
  ID=70, s. motor.py FUNC_SYS_PARAM)
- decode_sys_param_float: reine Dekodier-Hilfe 0x06-Payload (<f, ohne Bus)
- set_mode: Modus-Umschaltung (Func 0x11, live belegt, Pflichtparameter)
- query_version: Versionsabfrage über Update-Pfad (Func 0xf0/Sub 0x00,
  belegt aus mcu_util_485-Decompile, FUN_00014a08)

Belege: PROTOKOLL-GESAMT.md Abschnitt 1.5 + 1.7, Live-Frames Cut-Kalibrierung
12.10.2026 (Motor-Boards 0x81-0x84 = X/Y/Z/Z1).

OFFEN (nicht erfunden): welcher Func-Code dem Wrapper-Kommando `motor_version`
im Normalbetrieb entspricht – implementiert ist der belegte Update-Pfad.
"""

import struct

from serial485 import Serial485  # noqa: F401 (für echten Betrieb)

# Func-Codes Motor-Boards (Beleg: Cut-Kalibrierung 12.10.2026, 23:51)
# 0x06 = vermutlich Sys-Param-Schreiben: 4 Bytes LE-Float (00 00 96 43 =
# 300.0, 00 00 c8 42 = 100.0 = MOTOR_SYS_PARAM ID=70 PARAMS=300/100 aus
# gcode_macro.cfg:796/799). WIDERLEGT 15.10.2026: "Encoder lesen, BE 38467".
FUNC_SYS_PARAM = 0x06
FUNC_READ_ENCODER = FUNC_SYS_PARAM  # Alt-Name, nur Kompatibilität – nicht senden!
FUNC_SET_MODE = 0x11       # 1-2 Datenbytes (01 / 02 00 / 03 gesehen)
FUNC_FW_UPDATE = 0xF0      # Update-Pfad, Sub-Codes aus Decompile
SUB_GET_VERSION = 0x00
SUB_GET_SECTOR = 0x03
SUB_REQUEST_UPDATE = 0x01
# Retries wie motor.py: Binary bricht nach 3 Fehlversuchen ab (mcu_util_485
# `cmp r6,#3` @0x1562c), serial485 macht retries+1 Versuche -> 2.
UPDATE_RETRIES = 2

MOTOR_ADDRS = (0x81, 0x82, 0x83, 0x84)  # X/Y/Z/Z1 (Beleg: Cut-Kalibrierung)


def decode_sys_param_float(data):
    """4 Bytes 0x06-Payload -> Float (`<f`), ohne Bus-Zugriff.
    Beleg: 00 00 96 43 -> 300.0, 00 00 c8 42 -> 100.0 (MOTOR_SYS_PARAM ID=70)."""
    raw = bytes(data)
    if len(raw) != 4:
        raise ValueError("0x06-Payload muss 4 Bytes haben: %r" % (raw,))
    return struct.unpack("<f", raw)[0]


class MotorMini:
    """Closed-loop-Motor (Kernfunktionen)."""

    def __init__(self, bus, addr):
        if addr not in MOTOR_ADDRS:
            raise ValueError("unbekannte Motor-Adresse: 0x%02x" % addr)
        self.bus = bus
        self.addr = addr

    def read_encoder(self, *args, **kwargs):
        """GESPERRT (15.10.2026) – sendet KEINEN Frame (s. motor.py)."""
        raise NotImplementedError(
            "read_encoder gesperrt: Func 0x06 ist vermutlich Sys-Param-Schreiben "
            "(Live-Payload = LE-Float 300.0/100.0 = MOTOR_SYS_PARAM ID=70), "
            "kein Encoder-Lesen; Encoder-Lesen ist unbelegt, Senden könnte "
            "Motorparameter ändern")

    def set_mode(self, mode, *extra):
        """Modus setzen (Func 0x11, z.B. (0x01,) / (0x02, 0x00)).
        Mind. 1 Byte, jedes int 0..255 (kein bool) – sonst ValueError."""
        raw = [mode] + list(extra)
        for b in raw:
            if isinstance(b, bool) or not isinstance(b, int) or not 0 <= b <= 0xFF:
                raise ValueError("Modus-Byte muss int 0..255 sein: %r" % (b,))
        self.bus.transact(self.addr, FUNC_SET_MODE, raw)

    def query_version(self):
        """Version über Update-Pfad abfragen (Func 0xf0/Sub 0x00). Gibt Rohdaten zurück."""
        return self.bus.transact(self.addr, FUNC_FW_UPDATE, [SUB_GET_VERSION],
                                 retries=UPDATE_RETRIES)


# --- Selbsttest mit Live-Frames (keine Hardware nötig) ---
def selftest():
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(("ok " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    from serial485 import parse_frame, build_frame

    # 1. 0x06-Payload = LE-Float (Beleg gegen Config MOTOR_SYS_PARAM ID=70)
    check("0x06 = 300.0", decode_sys_param_float(bytes.fromhex("00 00 96 43")) == 300.0)
    check("0x06 = 100.0", decode_sys_param_float(bytes.fromhex("00 00 c8 42")) == 100.0)

    # 2. Live-Frames parsen + CRC prüfen
    for hexframe, label in [
        ("f7 81 07 00 06 00 00 96 43 09", "sys-param 0x06 (300.0)"),
        ("f7 81 04 00 11 01 1d", "mode-set 01"),
        ("f7 81 04 00 11 02 14", "mode-set 02"),
        ("f7 81 0b 00 0c 00 00 00 00 00 00 00 00 fb", "params 0x0c"),
    ]:
        raw = bytes.fromhex(hexframe)
        fun, data = parse_frame(raw)
        check("parse %s" % label, True)
        check("rebuild %s" % label,
              build_frame(raw[1], fun, data, status=raw[3]) == raw)

    # 3. Adress-Guard
    try:
        MotorMini(None, 0x05)
        check("addr-guard", False)
    except ValueError:
        check("addr-guard", True)

    # 4. read_encoder gesperrt + sendet nichts; set_mode/query_version
    class _Bus:
        def __init__(self):
            self.calls = []

        def transact(self, addr, fun, data=(), **kw):
            self.calls.append((addr, fun, list(data), kw))
            return b""

    bus = _Bus()
    mm = MotorMini(bus, 0x81)
    for args in [(), (0x9643,)]:
        try:
            mm.read_encoder(*args)
            check("read_encoder%r wirft" % (args,), False)
        except NotImplementedError:
            check("read_encoder%r wirft" % (args,), True)
    check("read_encoder sendet nichts", bus.calls == [])
    for bad in [(), (256,), (True,)]:
        try:
            mm.set_mode(*bad)
            check("set_mode guard %r" % (bad,), False)
        except (TypeError, ValueError):
            check("set_mode guard %r" % (bad,), True)
    check("set_mode guards senden nichts", bus.calls == [])
    mm.set_mode(0x02, 0x00)
    mm.query_version()
    check("set_mode [02 00]", bus.calls[0][1:3] == (FUNC_SET_MODE, [0x02, 0x00]))
    check("query_version retries=2 (3 Versuche)",
          bus.calls[1][2] == [SUB_GET_VERSION]
          and bus.calls[1][3].get("retries") == UPDATE_RETRIES == 2)

    print("SELBSTTEST: %s" % ("ok" if fails == 0 else "%d FEHLER" % fails))
    return fails


if __name__ == "__main__":
    raise SystemExit(selftest())
