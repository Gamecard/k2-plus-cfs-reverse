#!/usr/bin/env python3
"""motor_mini.py – Python-Nachbau von motor_control (K2 Closed-loop-Motoren, Kernfunktionen).

Ersetzt: motor_control_wrapper.cpython-39.so (armhf, proprietär).

Umfang:
- read_encoder: Encoder-Wert lesen (Func 0x06, live belegt)
- set_mode: Modus-Umschaltung (Func 0x11, live belegt)
- query_version: Versionsabfrage über Update-Pfad (Func 0xf0/Sub 0x00,
  belegt aus mcu_util_485-Decompile, FUN_00014a08)

Belege: PROTOKOLL-GESAMT.md Abschnitt 1.5 + 1.7, Live-Frames Cut-Kalibrierung
12.10.2026 (Motor-Boards 0x81-0x84 = X/Y/Z/Z1).

OFFEN (nicht erfunden): welcher Func-Code dem Wrapper-Kommando `motor_version`
im Normalbetrieb entspricht – implementiert ist der belegte Update-Pfad.
"""

from serial485 import Serial485  # noqa: F401 (für echten Betrieb)

# Func-Codes Motor-Boards (Beleg: Cut-Kalibrierung 12.10.2026, 23:51)
FUNC_READ_ENCODER = 0x06   # 4 Bytes Big-Endian (z.B. 00 00 96 43 = 0x4396)
FUNC_SET_MODE = 0x11       # 1-2 Datenbytes (01 / 02 00 / 03 gesehen)
FUNC_FW_UPDATE = 0xF0      # Update-Pfad, Sub-Codes aus Decompile
SUB_GET_VERSION = 0x00
SUB_GET_SECTOR = 0x03
SUB_REQUEST_UPDATE = 0x01

MOTOR_ADDRS = (0x81, 0x82, 0x83, 0x84)  # X/Y/Z/Z1 (Beleg: Cut-Kalibrierung)


def decode_u32be(data):
    """4 Bytes Big-Endian -> int (vgl. belt_mdl.bytes_to_int)."""
    raw = bytes(data)
    if len(raw) != 4:
        raise ValueError("Antwort muss 4 Bytes haben: %r" % (raw,))
    return int.from_bytes(raw, "big")


class MotorMini:
    """Closed-loop-Motor (Kernfunktionen)."""

    def __init__(self, bus, addr):
        if addr not in MOTOR_ADDRS:
            raise ValueError("unbekannte Motor-Adresse: 0x%02x" % addr)
        self.bus = bus
        self.addr = addr

    def read_encoder(self, register):
        """Encoder/Register lesen (Func 0x06, 4-Byte-Parameter Big-Endian).

        Beleg: Live-Requests trugen 00 00 96 43 bzw. 00 00 c8 42
        (Rolle der Bytes: vermutlich Register/Offset – Endpunkt unverifiziert).
        Der Parameter ist bewusst Pflicht: ein stiller Default (00 00 00 00)
        wurde nie auf dem Bus beobachtet.
        """
        import struct
        return decode_u32be(
            self.bus.transact(self.addr, FUNC_READ_ENCODER,
                              list(struct.pack(">I", register))))

    def set_mode(self, *mode_bytes):
        """Modus setzen (Func 0x11, z.B. (0x01,) / (0x02, 0x00))."""
        self.bus.transact(self.addr, FUNC_SET_MODE, list(mode_bytes))

    def query_version(self):
        """Version über Update-Pfad abfragen (Func 0xf0/Sub 0x00). Gibt Rohdaten zurück."""
        return self.bus.transact(self.addr, FUNC_FW_UPDATE, [SUB_GET_VERSION])


# --- Selbsttest mit Live-Frames (keine Hardware nötig) ---
def selftest():
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(("ok " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    from serial485 import parse_frame, build_frame

    # 1. U32BE-Dekodierung (Live-Bytes, Big-Endian wie belt_mdl.bytes_to_int)
    check("u32 0x9643", decode_u32be(bytes.fromhex("00 00 96 43")) == 38467)
    check("u32 0xc842", decode_u32be(bytes.fromhex("00 00 c8 42")) == 51266)

    # 2. Live-Frames parsen + CRC prüfen
    for hexframe, label in [
        ("f7 81 07 00 06 00 00 96 43 09", "mode-read 0x06"),
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

    # 4. Register ist Pflicht (kein stiller 00-00-00-00-Default)
    try:
        MotorMini(None, 0x81).read_encoder()
        check("register-pflicht", False)
    except TypeError:
        check("register-pflicht", True)

    print("SELBSTTEST: %s" % ("ok" if fails == 0 else "%d FEHLER" % fails))
    return fails


if __name__ == "__main__":
    raise SystemExit(selftest())
