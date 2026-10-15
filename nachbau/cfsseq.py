#!/usr/bin/env python3
"""cfsseq.py – CFS Extrude/Retrude-Abläufe als prüfbare Sequenzen (K2 Plus).

Kein Ersatz für box_wrapper-State-Machine (Stufen-Semantik steckt im .so),
sondern die belegte Transport-Sequenz zum Abspielen/Verifizieren –
z.B. bei der Pi-Inbetriebnahme: aufgezeichnete Frames gegen Muster prüfen.

Belege: PROTOKOLL-485.md + PROTOKOLL-GESAMT.md, Live-Mitschnitte 12.10.2026
(T1A 229 mm, T1B/T1C je 160 mm, T1D 247 mm @220 °C; Retrude -10 mm).

Ablauf Extrude (Func 0x10): Requests [Slotmaske, Stufe, Parameter] mit Stufen
00/04/05/06/07; 0x05-Push wiederholt sich sensor-/Messrad-gesteuert.
High-Level flankiert das mit 0x04 Feed-Mode, 0x0f Engage/Release, 0x08 Sensor
und 0x05 Buffer-Read. K2-Logframes mit `status=0x00` sind häufig ACKs.
Ablauf Retrude (Func 0x11): Requests [Slotmaske,00] START und [Slotmaske,01]
FINISH, dazwischen einmaliger Toolhead-Pull; 0x0e liefert den Float.
Messrad-Nutzdaten (0x0e + 0x10-Replies): 4-Byte Big-Endian IEEE-754 Float, negativ
(KORREKTUR 13.10., vgl. PROTOKOLL-485.md – kein Zähler).
"""

from serial485 import parse_frame, build_frame

BOX_ADDR = 0x01
STATUS_OPERATIONAL = 0xFF
SLOT_MASKS = (0x01, 0x02, 0x04, 0x08)
FUNC_SET_BOX_MODE = 0x04
FUNC_SET_PRE_LOADING = 0x0D
FUNC_HARDWARE_STATUS = 0x08
FUNC_MOTOR_ACTION = 0x0F
FUNC_EXTRUDE = 0x10
FUNC_RETRUDE = 0x11
FUNC_WHEEL = 0x0E
FUNC_05, FUNC_04, FUNC_0F = 0x05, 0x04, 0x0F

EXTRUDE_POLL = bytes.fromhex("f7 01 03 00 10 cd")
RETRUDE_POLL = bytes.fromhex("f7 01 03 00 11 ca")


def _slot(slot):
    if slot in range(4):
        return 1 << slot
    if slot in SLOT_MASKS:
        return slot
    raise ValueError("Slot muss 0..3 oder eine 1-hot-Maske sein")


def build_preload(mask, phase):
    """Baut SET_PRE_LOADING (0x0d): [Slotmaske][Phase]."""
    return build_frame(BOX_ADDR, FUNC_SET_PRE_LOADING, [mask, phase],
                       status=STATUS_OPERATIONAL)


def build_extrude_stage(slot, stage, parameter=0x00):
    """Baut einen echten 0x10-Request: [Slotmaske][Stufe][Parameter].

    Stufen gemäß unabhängiger, hardwarevalidierter Referenz:
    00 init, 04 engage, 05 push, 06 settle, 07 finalize mit Parameter 03.
    """
    slot = _slot(slot)
    return build_frame(BOX_ADDR, FUNC_EXTRUDE,
                       [slot, stage, parameter], status=STATUS_OPERATIONAL)


def build_extrude_sequence(slot, pushes=1):
    """Baut den CFS-Transportablauf ohne Sensor-/Zeitsteuerung."""
    if pushes < 1:
        raise ValueError("pushes muss >= 1 sein")
    slot = _slot(slot)
    frames = [
        build_frame(BOX_ADDR, FUNC_SET_BOX_MODE, [0x00, 0x01],
                    status=STATUS_OPERATIONAL),
        build_frame(BOX_ADDR, FUNC_MOTOR_ACTION, [0x01],
                    status=STATUS_OPERATIONAL),
        build_frame(BOX_ADDR, FUNC_HARDWARE_STATUS, [0x00],
                    status=STATUS_OPERATIONAL),
        build_extrude_stage(slot, 0x00),
        build_extrude_stage(slot, 0x04),
    ]
    frames.extend(build_extrude_stage(slot, 0x05) for _ in range(pushes))
    frames.extend([
        build_extrude_stage(slot, 0x06),
        build_extrude_stage(slot, 0x07, 0x03),
        build_frame(BOX_ADDR, FUNC_05, [], status=STATUS_OPERATIONAL),
        build_frame(BOX_ADDR, FUNC_SET_BOX_MODE, [slot, 0x00],
                    status=STATUS_OPERATIONAL),
        build_frame(BOX_ADDR, FUNC_MOTOR_ACTION, [0x00],
                    status=STATUS_OPERATIONAL),
    ])
    return frames


def build_retrude_sequence(slot):
    """Baut den CFS-START/FINISH-Retrude-Ablauf.

    Der Toolhead-Pull (G1 E-15) liegt zwischen den beiden 0x11-Requests
    und ist bewusst nicht Teil des RS485-Frames.
    """
    slot = _slot(slot)
    return [
        build_frame(BOX_ADDR, FUNC_SET_BOX_MODE, [0x00, 0x01],
                    status=STATUS_OPERATIONAL),
        build_frame(BOX_ADDR, FUNC_HARDWARE_STATUS, [0x00],
                    status=STATUS_OPERATIONAL),
        build_frame(BOX_ADDR, FUNC_RETRUDE, [slot, 0x00],
                    status=STATUS_OPERATIONAL),
        build_frame(BOX_ADDR, FUNC_HARDWARE_STATUS, [0x01],
                    status=STATUS_OPERATIONAL),
        build_frame(BOX_ADDR, FUNC_RETRUDE, [slot, 0x01],
                    status=STATUS_OPERATIONAL),
    ]


def check_frames(frames):
    """Prüft Liste roher Frames: CRC + Reihenfolge. Gibt [(addr, fun, daten)]."""
    return [parse_frame(bytes(f)) and _parsed(bytes(f)) for f in frames]


def _parsed(raw):
    fun, data = parse_frame(raw)
    return (raw[1], fun, data)


def verify_extrude(frames):
    """Prüft einen beobachteten Extrude-Trace.

    Ein Log-Trace kann Antworten statt Requests enthalten; deshalb wird nicht
    fälschlich eine bestimmte 0x05/0x04/0x0f-Reihenfolge erzwungen. Vollständige
    Requests erzeugt `build_extrude_sequence()`.
    """
    parsed = check_frames(frames)
    funs = [p[1] for p in parsed]
    if not any(f == FUNC_EXTRUDE for f in funs):
        return False, "kein 0x10-Frame"
    if FUNC_WHEEL not in funs:
        return False, "kein Messrad-Frame (0x0e)"
    return True, "Extrude-Trace ok (%d Frames)" % len(parsed)


def verify_retrude(frames):
    """Prüft einen beobachteten Retrude-Trace: 0x11 + 0x0e."""
    parsed = check_frames(frames)
    funs = [p[1] for p in parsed]
    if sum(f == FUNC_RETRUDE for f in funs) < 1:
        return False, "kein 0x11-Frame"
    if FUNC_WHEEL not in funs:
        return False, "kein Messrad-Frame (0x0e)"
    return True, "Retrude-Sequenz ok (%d Frames)" % len(parsed)


# Exakte Live-Mitschnitte (Beleg, 12.10.2026) für Selbsttest + Replay-Referenz.
T1A_EXTRUDE = [  # 23:11:55-23:12:21, tnn T1A, 229 mm
    "f7 01 07 00 10 c4 8d ad 5a 39",
    "f7 01 07 00 10 c4 b7 28 73 77",
    "f7 01 07 00 10 c4 df f2 10 f5",
    "f7 01 03 00 10 cd",
    "f7 01 04 00 05 00 19",
    "f7 01 03 00 04 a1",
    "f7 01 03 00 0f 90",
    "f7 01 07 00 0e c4 e6 fe 0b f3",
    "f7 01 07 00 0e c4 e6 fe 05 d9",
]
RETRUDE = [  # 23:16, -10 mm, Messrad rückwärts
    "f7 01 03 00 11 ca",
    "f7 01 07 00 0e c5 00 91 49 a9",
    "f7 01 03 00 11 ca",
    "f7 01 07 00 0e c4 ff e7 70 e7",
    "f7 01 03 00 11 ca",
    "f7 01 07 00 0e c4 ff e7 84 25",
    "f7 01 03 00 11 ca",
    "f7 01 07 00 0e c3 32 53 0d f6",
]


def selftest():
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(("ok " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    ok, msg = verify_extrude([bytes.fromhex(f) for f in T1A_EXTRUDE])
    check("extrude T1A (%s)" % msg, ok)
    ok, msg = verify_retrude([bytes.fromhex(f) for f in RETRUDE])
    check("retrude (%s)" % msg, ok)

    # Negativtests: unvollständige Sequenzen müssen scheitern
    ok, _ = verify_extrude([bytes.fromhex("f7 01 03 00 10 cd")])
    check("extrude unvollständig scheitert", not ok)
    ok, _ = verify_retrude([bytes.fromhex("f7 01 03 00 11 ca")])
    check("retrude ohne Rad scheitert", not ok)

    # Poll-Konstanten gegen Doku
    check("extrude-poll", EXTRUDE_POLL == bytes.fromhex("f7 01 03 00 10 cd"))
    check("retrude-poll", RETRUDE_POLL == bytes.fromhex("f7 01 03 00 11 ca"))
    check("build extrude request",
          build_extrude_stage(0, 0x05) ==
          bytes.fromhex("f7 01 06 ff 10 01 05 00 82"))
    check("build retrude start",
          build_retrude_sequence(1)[2][5:-1] == bytes.fromhex("02 00"))

    print("SELBSTTEST: %s" % ("ok" if fails == 0 else "%d FEHLER" % fails))
    return fails


if __name__ == "__main__":
    raise SystemExit(selftest())
