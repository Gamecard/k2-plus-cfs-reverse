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
Ablauf Retrude (Func 0x11): ~~Requests [Slotmaske,00] START und [Slotmaske,01]
FINISH, dazwischen einmaliger Toolhead-Pull + 0x08 [01]~~ KORRIGIERT
15.10.2026 (LIVE, klippy.log 12.-16.10.): 04 [00 01] -> 08 [00] ->
11 [m 00] meist 4x (16 von 26 Retrudes; je Antwort nach 0,24-0,72 s,
Abstand ~2,5 s, dazwischen 0x0e-/0x03-Abfragen) -> 11 [m 01]. In 10 Fällen
(nach Extrude-Abbruch/Entladen) direkt 04 [00 01] -> 11 [m 01] ohne p0.
0x08 [01] kommt zwischen p0 und p1 NIE vor (0 von 23), nur vor 0x04.
0x0e liefert den Float.

BELEGT 15.10.2026 (Ghidra CFS-FW R153, GHIDRA-CFS-BEFUND-2026-10-15.md §4):
die „Stufen“ sind die 8 Modi von cmd_feed_process_2 (FUN_080045c6), s.
EXTRUDE_MODE_*: 00 Stage1 (Pretension -> feed_top_to_hub), 04 Dauerfördern
starten (sofortige Antwort), 05 = Odometer-ABFRAGE (keine Push-Aktion; die
Wiederholung ist ein Poll, Antwort 4 B Float BE), 06 Fördern beenden +
Buffer-Check, 07 03 = Extruder-Check mit p = 3 mm. 0x0f ist NICHT Motor-
Engage/Release, sondern die Auto-Preload-Sperre ([01] sperren, [00] frei).
0x04 [00 01] = Box-Mode IDLE (Print verlassen), [mask 00] = STATE_PRINT.
0x08 [00]/[01] = Bitmaske Sensor A/B (Host-Name 0x08 = GET_FILAMENT_SENSOR_
STATE, nicht GET_HARDWARE_STATUS – das ist 0x15; 0x0f Host-Name
TIGHTEN_UP_ENABLE). 0x11 p=0 = Rückzug bis Buffer-Sensor,
p=1 = pull_back_hub (FUN_08004d90). Bytes der Sequenzen unverändert.
Messrad-Nutzdaten (0x0e + 0x10-Replies): 4-Byte Big-Endian IEEE-754 Float, negativ
(KORREKTUR 13.10., vgl. PROTOKOLL-485.md – kein Zähler).
"""

from serial485 import parse_frame, build_frame

BOX_ADDR = 0x01
STATUS_OPERATIONAL = 0xFF
SLOT_MASKS = (0x01, 0x02, 0x04, 0x08)
MASK_ALL = 0x0F
# 0x0d-Aktionen – identisch zu box.PRELOAD_* (Belege dort: ref-hi
# f70105ff0d0f0169 = mask 0x0F enable 0x01; vanilla (0,enabled)/(mask,2)).
# ERGÄNZT 15.10.2026 (Ghidra FUN_0800f0da): 0x03 = Forced Preload OHNE RFID.
PRELOAD_DISABLE, PRELOAD_ENABLE, PRELOAD_FORCE_READ = 0x00, 0x01, 0x02
PRELOAD_FORCE_TENSION_ONLY = 0x03
PRELOAD_ACTIONS = (PRELOAD_DISABLE, PRELOAD_ENABLE, PRELOAD_FORCE_READ,
                   PRELOAD_FORCE_TENSION_ONLY)

# 0x10-Modi (2. Datenbyte), BELEGT 15.10.2026 (Ghidra FUN_080045c6, CFS-FW
# R153; Mechanik B, Funktionsnamen teils Deutung). Werte > 7: keine Antwort.
EXTRUDE_MODE_STAGE1 = 0x00        # Pretension -> feed_top_to_hub -> fördern; Antwort +1 B Zustand
EXTRUDE_MODE_FEED_TO_STALL = 0x01  # RELOAD, fördern bis Odometer-Stillstand (>3000 mm -> 0x0b)
EXTRUDE_MODE_FEED_CHECKED = 0x02   # wie 1, vorher >=5 mm in 3 s nötig (sonst 0x0a)
EXTRUDE_MODE_CLEAR_ERROR = 0x03    # Fehler löschen: Zustand IDLE, Fehlercode 0
EXTRUDE_MODE_FEED_START = 0x04     # Dauerfördern, Antwort SOFORT Status 0
EXTRUDE_MODE_QUERY = 0x05          # Abfrage: Fehler aus mode 4 + 4 B Odometer-Float BE
EXTRUDE_MODE_STOP_BUFFER = 0x06    # Fördern beenden / Buffer füllen bis voll (sonst 0x0d)
EXTRUDE_MODE_EXTRUDER_CHECK = 0x07  # Odometer muss in 300 ms > p mm laufen (sonst 0x0e)
EXTRUDE_MODES = {
    EXTRUDE_MODE_STAGE1: "stage1 (pretension, feed_top_to_hub)",
    EXTRUDE_MODE_FEED_TO_STALL: "feed bis Stillstand",
    EXTRUDE_MODE_FEED_CHECKED: "feed mit 5-mm-Anlaufprüfung",
    EXTRUDE_MODE_CLEAR_ERROR: "Fehler löschen",
    EXTRUDE_MODE_FEED_START: "Dauerfördern starten (sofortige Antwort)",
    EXTRUDE_MODE_QUERY: "Odometer/Fehler abfragen",
    EXTRUDE_MODE_STOP_BUFFER: "Fördern beenden + Buffer-Check",
    EXTRUDE_MODE_EXTRUDER_CHECK: "Extruder-Check (p mm)",
}
EXTRUDER_CHECK_MM = 0x03  # Live-Parameter von [mask 07 03] = p 3 mm
# 0x11-Parameter p (BELEGT 15.10.2026, Ghidra FUN_08004d90)
RETRUDE_P_TO_BUFFER = 0x00  # Feeder+Hauptmotor rückwärts bis Buffer-Sensor (live „START“)
RETRUDE_P_PULL_HUB = 0x01   # rfid_mgr_fila_back + pull_back_hub (live „FINISH“)
FUNC_SET_BOX_MODE = 0x04
FUNC_SET_PRE_LOADING = 0x0D
FUNC_FILAMENT_SENSOR_STATE = 0x08  # Host: GET_FILAMENT_SENSOR_STATE (live 15.10.)
FUNC_HARDWARE_STATUS = FUNC_FILAMENT_SENSOR_STATE  # ALT-NAME (falsch), Alias
FUNC_TIGHTEN_UP_ENABLE = 0x0F  # Host: TIGHTEN_UP_ENABLE (live 15.10.)
FUNC_MOTOR_ACTION = FUNC_TIGHTEN_UP_ENABLE  # KORRIGIERT 15.10.2026: Auto-Preload-Sperre (s. oben)
FUNC_PRELOAD_INHIBIT = FUNC_MOTOR_ACTION
FUNC_EXTRUDE = 0x10
FUNC_RETRUDE = 0x11
FUNC_WHEEL = 0x0E
FUNC_05, FUNC_04, FUNC_0F = 0x05, 0x04, 0x0F

EXTRUDE_POLL = bytes.fromhex("f7 01 03 00 10 cd")
RETRUDE_POLL = bytes.fromhex("f7 01 03 00 11 ca")


def _check_mask(mask):
    """Slotmaske 0x00..0x0f (Mehrfachmasken erlaubt), keine Umdeutung."""
    if isinstance(mask, bool) or not isinstance(mask, int) \
            or not 0 <= mask <= MASK_ALL:
        raise ValueError("Maske muss int 0x00..0x0f sein: %r" % (mask,))
    return mask


def _one_hot(mask):
    """Genau EIN Slot als 1-hot-Maske (Extrude/Retrude sind nur
    einzelslotig belegt; Mehrfachmasken dort unbelegt -> ValueError)."""
    if isinstance(mask, bool) or mask not in SLOT_MASKS:
        raise ValueError("Maske muss 1-hot 0x01/0x02/0x04/0x08 sein: %r"
                         % (mask,))
    return mask


def slot_to_mask(index):
    """Slot-Index 0..3 (A..D) -> 1-hot-Maske (wie box.slot_to_mask)."""
    if isinstance(index, bool) or index not in range(4):
        raise ValueError("Slot-Index muss 0..3 sein: %r" % (index,))
    return SLOT_MASKS[index]


def build_preload(mask, action):
    """Baut SET_PRE_LOADING (0x0d): [Slotmaske][Aktion 00/01/02/03].

    Bytegleich mit box.build_preload (gleiche Prüfungen). Maske wirkt laut
    Ghidra (FUN_0800f0da) nur bei 02/03, bei 00/01 ignoriert.
    """
    if action not in PRELOAD_ACTIONS:
        raise ValueError("Aktion muss 0x00..0x03 sein")
    return build_frame(BOX_ADDR, FUNC_SET_PRE_LOADING,
                       [_check_mask(mask), action],
                       status=STATUS_OPERATIONAL)


def build_extrude_stage(mask, stage, parameter=0x00):
    """Baut einen echten 0x10-Request: [Slotmaske][Stufe][Parameter].

    mask = 1-hot-Slotmaske (0x01=A … 0x08=D), KEIN Slot-Index – dafür
    slot_to_mask() verwenden.
    Stufen gemäß unabhängiger, hardwarevalidierter Referenz:
    00 init, 04 engage, 05 push, 06 settle, 07 finalize mit Parameter 03.
    BELEGT 15.10.2026 (Ghidra): stage = 0x10-Modus 0..7 (EXTRUDE_MODE_*),
    parameter = p (nur mode 7 nutzt ihn: mm für den Extruder-Check).
    Keine Einschränkung des Wertebereichs (Verhalten unverändert).
    """
    mask = _one_hot(mask)
    return build_frame(BOX_ADDR, FUNC_EXTRUDE,
                       [mask, stage, parameter], status=STATUS_OPERATIONAL)


def build_extrude_sequence(mask, pushes=1):
    """Baut den CFS-Transportablauf ohne Sensor-/Zeitsteuerung.

    mask = 1-hot-Slotmaske (s. build_extrude_stage).
    LIVE BESTÄTIGT 15.10.2026: Request-Folge identisch mit klippy.log.2026-
    10-13 03:01 (Slot B, pushes=5), s. EXTRUDE_LIVE_REQ_B im Selbsttest.
    """
    if pushes < 1:
        raise ValueError("pushes muss >= 1 sein")
    mask = _one_hot(mask)
    frames = [
        build_frame(BOX_ADDR, FUNC_SET_BOX_MODE, [0x00, 0x01],
                    status=STATUS_OPERATIONAL),
        build_frame(BOX_ADDR, FUNC_MOTOR_ACTION, [0x01],
                    status=STATUS_OPERATIONAL),
        build_frame(BOX_ADDR, FUNC_FILAMENT_SENSOR_STATE, [0x00],
                    status=STATUS_OPERATIONAL),
        build_extrude_stage(mask, EXTRUDE_MODE_STAGE1),
        build_extrude_stage(mask, EXTRUDE_MODE_FEED_START),
    ]
    # „pushes“ = Odometer-Polls (mode 5), keine eigenen Förderbefehle
    frames.extend(build_extrude_stage(mask, EXTRUDE_MODE_QUERY)
                  for _ in range(pushes))
    frames.extend([
        build_extrude_stage(mask, EXTRUDE_MODE_STOP_BUFFER),
        build_extrude_stage(mask, EXTRUDE_MODE_EXTRUDER_CHECK,
                            EXTRUDER_CHECK_MM),
        build_frame(BOX_ADDR, FUNC_05, [], status=STATUS_OPERATIONAL),
        build_frame(BOX_ADDR, FUNC_SET_BOX_MODE, [mask, 0x00],
                    status=STATUS_OPERATIONAL),
        build_frame(BOX_ADDR, FUNC_MOTOR_ACTION, [0x00],
                    status=STATUS_OPERATIONAL),
    ])
    return frames


RETRUDE_TO_BUFFER_REPEATS = 4  # live: 11 [m 00] meist 4x vor [m 01]


def build_retrude_sequence(mask, to_buffer_repeats=RETRUDE_TO_BUFFER_REPEATS):
    """Baut den CFS-Retrude-Ablauf (mask = 1-hot-Slotmaske) wie LIVE.

    KORRIGIERT 15.10.2026 (klippy.log 12.-16.10., 26 Retrudes):
      04 [00 01], 08 [00], 11 [m 00] x to_buffer_repeats (live meist 4),
      11 [m 01].
    ~~08 [01] zwischen p0 und p1~~ WIDERLEGT (live 0 von 23).
    to_buffer_repeats=0: live belegte Kurzform (10x, nach Extrude-Abbruch/
      Entladen) 04 [00 01] -> 11 [m 01] (ohne 08 [00]).
    Dazwischen liegende Abfragen (0x0e Messrad, 0x03 Restmenge) und der
    Toolhead-Pull (G1 E-…) sind bewusst nicht Teil der Sequenz.
    """
    mask = _one_hot(mask)
    if isinstance(to_buffer_repeats, bool) or not isinstance(
            to_buffer_repeats, int) or to_buffer_repeats < 0:
        raise ValueError("to_buffer_repeats muss int >= 0 sein")
    frames = [build_frame(BOX_ADDR, FUNC_SET_BOX_MODE, [0x00, 0x01],
                          status=STATUS_OPERATIONAL)]
    if to_buffer_repeats:
        frames.append(build_frame(BOX_ADDR, FUNC_FILAMENT_SENSOR_STATE, [0x00],
                                  status=STATUS_OPERATIONAL))
        frames.extend(build_frame(BOX_ADDR, FUNC_RETRUDE,
                                  [mask, RETRUDE_P_TO_BUFFER],
                                  status=STATUS_OPERATIONAL)
                      for _ in range(to_buffer_repeats))
    frames.append(build_frame(BOX_ADDR, FUNC_RETRUDE, [mask, RETRUDE_P_PULL_HUB],
                              status=STATUS_OPERATIONAL))
    return frames


def requests_from_log_cmds(cmds):
    """Log-Request-Zeilen ("retries = N, cmd = 0x1 0x5 0xff 0x11 0x1 0x0",
    nur der cmd-Teil, OHNE F7/CRC) in Voll-Frames wandeln (CRC berechnet)."""
    out = []
    for c in cmds:
        b = [int(x, 16) for x in c.split()]
        addr, length, status, fun, data = b[0], b[1], b[2], b[3], b[4:]
        if length != len(data) + 3:
            raise ValueError("Längenfeld passt nicht: %r" % (c,))
        out.append(build_frame(addr, fun, data, status=status))
    return out


def filter_funs(frames, funs):
    """Nur Frames mit fun in funs behalten (z.B. Poll-Abfragen entfernen)."""
    return [bytes(f) for f in frames if parse_frame(bytes(f))[0] in funs]


def check_frames(frames):
    """Prüft Liste roher Frames einzeln (Head/Länge/CRC via parse_frame).

    KEINE Reihenfolgeprüfung – die erfolgt (bewusst locker, s. dort) in
    verify_extrude/verify_retrude. Gibt [(addr, fun, daten)] zurück;
    fehlerhafter Frame -> ValueError.
    """
    return [_parsed(bytes(f)) for f in frames]


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
# LIVE 15.10.2026 (klippy.log.2026-10-13, Request-Zeilen „retries = 4,
# cmd = …“ in Reihenfolge, nur Box-0x01, ohne 0x0a-/0xa2-Polls; keine SN/UUID).
RETRUDE_LIVE_CMDS_A = [  # 02:34:16-02:34:42, Slot A
    "0x1 0x5 0xff 0x4 0x0 0x1", "0x1 0x4 0xff 0x8 0x0",
    "0x1 0x5 0xff 0x11 0x1 0x0", "0x1 0x4 0xff 0xe 0x1",
    "0x1 0x5 0xff 0x11 0x1 0x0", "0x1 0x4 0xff 0xe 0x1",
    "0x1 0x5 0xff 0x11 0x1 0x0", "0x1 0x4 0xff 0xe 0x1",
    "0x1 0x5 0xff 0x11 0x1 0x0", "0x1 0x5 0xff 0x11 0x1 0x1",
    "0x1 0x4 0xff 0xe 0x1",
]
RETRUDE_LIVE_RX_A = [  # zugehörige Antworten (params:-Zeilen), Auszug
    "f7 01 03 00 04 a1", "f7 01 04 00 08 07 e5", "f7 01 03 00 11 ca",
    "f7 01 07 00 0e c5 1f d1 e4 5d", "f7 01 03 00 11 ca",
    "f7 01 07 00 0e c5 1f 36 90 3e", "f7 01 03 00 11 ca",
    "f7 01 07 00 0e c5 1f 36 9d 1d", "f7 01 03 00 11 ca",
    "f7 01 03 00 11 ca", "f7 01 07 00 0e c4 29 7e 91 40",
]
EXTRUDE_LIVE_CMDS_B = [  # 03:01, Slot B (0x0e-Polls entfernt)
    "0x1 0x5 0xff 0x4 0x0 0x1", "0x1 0x4 0xff 0xf 0x1", "0x1 0x4 0xff 0x8 0x0",
    "0x1 0x6 0xff 0x10 0x2 0x0 0x0", "0x1 0x6 0xff 0x10 0x2 0x4 0x0",
] + ["0x1 0x6 0xff 0x10 0x2 0x5 0x0"] * 5 + [
    "0x1 0x6 0xff 0x10 0x2 0x6 0x0", "0x1 0x6 0xff 0x10 0x2 0x7 0x3",
    "0x1 0x3 0xff 0x5", "0x1 0x5 0xff 0x4 0x2 0x0", "0x1 0x4 0xff 0xf 0x0",
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
          build_extrude_stage(slot_to_mask(0), 0x05) ==
          bytes.fromhex("f7 01 06 ff 10 01 05 00 82"))
    check("build retrude start",
          build_retrude_sequence(0x02)[2][5:-1] == bytes.fromhex("02 00"))
    # LIVE 15.10.2026: Retrude-Requests Slot A == build_retrude_sequence
    live_r = requests_from_log_cmds(RETRUDE_LIVE_CMDS_A)
    check("retrude live A (04,08[00],11p0 x4,11p1) == build",
          filter_funs(live_r, (FUNC_SET_BOX_MODE, FUNC_FILAMENT_SENSOR_STATE,
                               FUNC_RETRUDE)) == build_retrude_sequence(0x01))
    check("retrude live: kein 08 [01] zwischen p0 und p1",
          all(parse_frame(f) != (0x08, b"\x01") for f in live_r))
    mixed = []
    for tx, rx in zip(live_r, RETRUDE_LIVE_RX_A):
        mixed += [tx, bytes.fromhex(rx)]
    ok, msg = verify_retrude(mixed)
    check("retrude live TX+RX (%s)" % msg, ok)
    check("retrude kurzform (0 Wiederholungen)",
          [parse_frame(f) for f in build_retrude_sequence(0x02, 0)]
          == [(0x04, b"\x00\x01"), (0x11, b"\x02\x01")])
    live_e = requests_from_log_cmds(EXTRUDE_LIVE_CMDS_B)
    check("extrude live B == build_extrude_sequence(0x02, pushes=5)",
          live_e == build_extrude_sequence(0x02, pushes=5))
    check("namen 0x08/0x0f (Host) + Aliase",
          FUNC_FILAMENT_SENSOR_STATE == FUNC_HARDWARE_STATUS == 0x08
          and FUNC_TIGHTEN_UP_ENABLE == FUNC_MOTOR_ACTION == 0x0F)
    check("slot_to_mask(1) == 0x02", slot_to_mask(1) == 0x02)
    check("preload ref-hi f70105ff0d0f0169",
          build_preload(0x0F, PRELOAD_ENABLE) == bytes.fromhex("f70105ff0d0f0169"))
    check("preload mehrfachmaske 0x05", build_preload(0x05, 0x02)[5] == 0x05)
    # 15.10.2026 (Ghidra): op 3 = Forced Preload ohne RFID
    check("preload op3 tension-only",
          build_preload(0x01, PRELOAD_FORCE_TENSION_ONLY)[5:7] == b"\x01\x03")
    check("extrude-modi 0..7 benannt",
          sorted(EXTRUDE_MODES) == list(range(8)))
    # Benennung ändert die Live-Sequenz NICHT (Bytes wie vorher hart codiert)
    seq = build_extrude_sequence(0x01, pushes=2)
    check("extrude-sequenz bytegleich",
          [f[5:7] for f in seq[3:9]] == [b"\x01\x00", b"\x01\x04", b"\x01\x05",
                                          b"\x01\x05", b"\x01\x06", b"\x01\x07"]
          and seq[8][7] == 0x03)
    rs = build_retrude_sequence(0x04)
    check("retrude p 00/01",
          rs[-1][5:7] == b"\x04\x01" and rs[2][5:7] == b"\x04\x00"
          and len(rs) == 2 + RETRUDE_TO_BUFFER_REPEATS + 1)
    for name, call in (("extrude 0x03", lambda: build_extrude_stage(0x03, 0)),
                       ("extrude 0", lambda: build_extrude_stage(0, 0)),
                       ("retrude 0x0f", lambda: build_retrude_sequence(0x0F)),
                       ("preload 0x10", lambda: build_preload(0x10, 1)),
                       ("preload aktion 4", lambda: build_preload(0x01, 4)),
                       ("slot 4", lambda: slot_to_mask(4)),
                       ("retrude repeats -1",
                        lambda: build_retrude_sequence(0x01, -1))):
        try:
            call()
            check("guard " + name, False)
        except ValueError:
            check("guard " + name, True)

    print("SELBSTTEST: %s" % ("ok" if fails == 0 else "%d FEHLER" % fails))
    return fails


if __name__ == "__main__":
    raise SystemExit(selftest())
