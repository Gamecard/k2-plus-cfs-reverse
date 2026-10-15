#!/usr/bin/env python3
"""boxmini.py – Python-Nachbau von box_wrapper (CFS-Box, K2 Plus, Kernfunktionen).

Ersetzt: box_wrapper.cpython-39.so (armhf, CPython 3.9, proprietär).

Umfang:
- get_rfid: RFID-Tag eines Slots lesen (Func 0x02)
- has_filament: Belegung pro Slot (Func 0x03, Restmenge != 0/leer)
- remain percent: Restmenge in Prozent (Func 0x03)

Belege (alle in /home/pro/k2-fw-live-1.1.7.0/ dokumentiert):
- Func-Codes + ASCII-Format: PROTOKOLL-485.md, PROTOKOLL-GESAMT.md
- `A:<hex>;` / `B:unknown;` / `C:busy;`: Live-Frames 12.10.2026
- GET_REMAIN_LEN: Slotmaske im Request, Antwort positional A..D;
  0xff = nicht ausgewählt, 0 = ausgewählt+leer (Live: 1a 00 ff 00 = A 26%)
- NUM=0..3 numerisch (FALLE: NUM=A wirft key60-Shutdown)

Benutzung: Serial485-Instanz aus serial485.py übergeben (echte Hardware, pyserial).
Der Selbsttest unten läuft OHNE Hardware (Fake-Transport mit Live-Frames).
"""

from serial485 import Serial485  # noqa: F401 (für echten Betrieb)

# Func-Codes CFS/Box, Addr 0x01 (Beleg: PROTOKOLL-485.md)
FUNC_GET_RFID = 0x02
FUNC_GET_REMAIN_LEN = 0x03
BOX_ADDR = 0x01
STATUS_OPERATIONAL = 0xFF
TIMEOUT_OPERATIONAL = 2.0
NO_DATA = 0xFF
SLOT_KEYS = "ABCD"  # NUM 0..3 -> A/B/C/D (Beleg: Live-Frames)


def parse_rfid_response(data):
    """Parst GET_RFID-Antwort (ASCII, ';'-getrennt, Key=Slot-Buchstabe).

    Beispiele (anonymisiert): b"A:AAAAAA...;" / b"B:unknown;" / b"C:busy;"
    Gibt dict {Slot-Key: Wert} zurück. Leere Antwort -> {}.
    """
    result = {}
    for part in bytes(data).decode("ascii", "replace").split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, _, value = part.partition(":")
        key, value = key.strip(), value.strip()
        if len(key) == 1 and key in SLOT_KEYS:
            result[key] = value
    return result


def parse_remain_response(data):
    """Parst GET_REMAIN_LEN-Antwort: 4 Bytes A,B,C,D (0-100 %, 0xff = nicht ausgewählt).

    Beleg: Live-Frame f7 01 07 00 03 1a 00 ff 00 99 -> A=26 %.
    Gibt dict {Slot-Key: Prozent oder None} zurück.
    """
    raw = list(bytes(data))
    out = {}
    for i, key in enumerate(SLOT_KEYS):
        out[key] = None if (i >= len(raw) or raw[i] == NO_DATA) else raw[i]
    return out


class BoxMini:
    """CFS-Box (Kernfunktionen): RFID + Belegung + Restmenge."""

    def __init__(self, bus, addr=BOX_ADDR):
        self.bus = bus
        self.addr = addr

    def get_rfid(self, slot=0):
        """RFID-Tag von Slot 0..3 lesen.

        Der Request trägt eine 1-hot-Slotmaske, nicht die Slotnummer. Die
        Antwort ist ASCII (`A:...;`). STATUS=0xff ist der Operational-Modus.
        """
        if slot not in range(4):
            raise ValueError("Slot muss 0..3 sein")
        mask = 1 << slot
        raw = self.bus.transact(self.addr, FUNC_GET_RFID, [mask],
                                status=STATUS_OPERATIONAL,
                                timeout=TIMEOUT_OPERATIONAL)
        parsed = parse_rfid_response(raw)
        return parsed.get(SLOT_KEYS[slot])

    def get_remain(self, mask=0x0F):
        """Restmengen der ausgewählten Slots: {A/B/C/D: Prozent oder None}.

        Request: eine 1-hot-/Mehrfach-Slotmaske; Antwort positional A..D,
        0xff bedeutet „nicht ausgewählt“, 0 bedeutet ausgewählt und leer.
        """
        if not 0 <= mask <= 0x0F:
            raise ValueError("Maske muss 0x00..0x0f sein")
        raw = self.bus.transact(self.addr, FUNC_GET_REMAIN_LEN, [mask],
                                status=STATUS_OPERATIONAL,
                                timeout=TIMEOUT_OPERATIONAL)
        return parse_remain_response(raw)

    def has_filament(self, slot=0):
        """True wenn Slot belegt (Restmenge bekannt und > 0)."""
        remain = self.get_remain()[SLOT_KEYS[slot]]
        return remain is not None and remain > 0


# --- Selbsttest mit Live-Frames (keine Hardware nötig) ---
class _FakeBus:
    """Spielt aufgezeichnete Antworten ab (Framing wie Serial485.transact)."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def transact(self, addr, fun, data=(), **kwargs):
        self.calls.append((addr, fun, list(data), dict(kwargs)))
        from serial485 import parse_frame
        raw = self.answers.pop(0)
        rfun, rdata = parse_frame(raw)
        assert rfun == fun, (rfun, fun)
        return rdata


def selftest():
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(("ok " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    # 1. RFID-Parsing (Live-Frames 12.10.2026)
    check("rfid A", parse_rfid_response(
        bytes.fromhex("41 3a 41 41 41 41 41 41")) == {"A": "AAAAAA"})
    check("rfid unknown", parse_rfid_response(b"B:unknown;") == {"B": "unknown"})
    check("rfid busy", parse_rfid_response(b"C:busy;") == {"C": "busy"})
    check("rfid leer", parse_rfid_response(b"") == {})

    # 2. Remain-Parsing (Live: A=26 %, B=0, C/D=n/a)
    check("remain", parse_remain_response(bytes.fromhex("1a 00 ff 00")) ==
          {"A": 26, "B": 0, "C": None, "D": 0})

    # 3. BoxMini gegen FakeBus mit echten Live-Response-Frames
    bus = _FakeBus([
        bytes.fromhex("f7 01 07 00 03 1a 00 ff 00 99"),  # GET_REMAIN_LEN
        bytes.fromhex("f7 01 07 00 03 1c ff 00 00 11"),  # GET_REMAIN_LEN (A=28)
    ])
    box = BoxMini(bus)
    check("box remain", box.get_remain() == {"A": 26, "B": 0, "C": None, "D": 0})
    check("remain request mask", bus.calls[0][2] == [0x0F] and
          bus.calls[0][3]["status"] == STATUS_OPERATIONAL)
    check("box filament A", box.has_filament(0) is True)
    print("SELBSTTEST: %s" % ("ok" if fails == 0 else "%d FEHLER" % fails))
    return fails


if __name__ == "__main__":
    raise SystemExit(selftest())
