#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2_box.py – Klipper-Zusatzmodul [k2_box] für die CFS-Box (K2 Plus).

Offener Ersatz für Crealitys ``box_wrapper.so`` (Stock, armhf, proprietär),
soweit der Box-Förderpfad betroffen ist. Gegenstück zu ``k2_motor_init.py``
(``[k2_motor]``): gleiche Stilregeln – reine Logik ohne Klipper testbar,
MCU-/Port-Zugriffe gekapselt, Schreiben nur mit ``allow_write``.

Grundlagen (nur gelesen, nichts gesendet):
- nachbau/box.py (ParseData/BoxState/BoxAction – WIEDERVERWENDET, nicht neu
  erfunden: RFID/Remain/Messrad/Box-State/Version/Buffer/Sensor/Hardware-
  Status, CfsStatusError/StatusBytes, FUNC_RETRIES, preload_force_timeout)
- nachbau/cfsseq.py (Extrude-/Retrude-Live-Sequenzen – WIEDERVERWENDET:
  build_extrude_sequence/build_retrude_sequence, EXTRUDE_LIVE_CMDS_B,
  RETRUDE_LIVE_CMDS_A, EXTRUDE_MODE_*/RETRUDE_P_*, slot_to_mask)
- nachbau/boxmini.py (has_filament-Semantik: remain 0 = unbekannt, nie False)
- nachbau/auto_addr.py (Adressvergabe – WIEDERVERWENDET via Adapter)
- nachbau/serial485.py (build_frame/parse_frame/CRC-8, Bus-Lock MAX_BUS_HOLD_S)
- PROTOKOLL-485.md (Func-Tabelle CFS-App R153, §6 Live-Gegenprobe: Retry-/
  Timeout-Tabelle §6.2, Extrude/Retrude live §6.5, 0x0a-Layout §6.6/§6.8,
  Schutzmechanismen §6.9; Sicherheitsregeln: nie 0x50/0x06, keine Bursts,
  ein gemeinsamer RS485-Transport/Lock für Motoren+CFS+Belt auf ttyS5)
- GHIDRA-CFS-BEFUND-2026-10-15.md + GHIDRA-CFS-BEFUND2-2026-10-16.md
  (0x0d op 0-3, 0x10 8 Modi, 0x11 p 0/1, 0x0a-Bytes Temp/Feuchte/Block/Hub,
  Status-Codes 0x0c/0x16/0x19/0x30/0x50/0x51/0x52, 0x06-FW-Bug, 0xEA/0xEB)
- LIVE-ABFRAGEN-2026-10-16.md (nur Motor – hier als Negativbeleg: Box braucht
  keinen Transparent-Pfad, nur RS485 0x01)
- CHECKLISTE.md Abschnitt „Kanal A“ (echter Live-Ablauf Preload/Extrude/
  Retrude + Fehler 0x0c bei ~245 mm und 0x19 beim Rückzug – als Testvorbild)
- pi-host/k2_transport.py (Rs485Transport-Schnittstelle + sys.path-Muster
  für nachbau/; Bus-Lock-Teilung: EIN Port, EINE Queue, EIN Lock)
- Original-Makros config/F008_CR0CN240319C13/box.cfg + gcode_macro.cfg
  (BOX_LOAD_MATERIAL-Kette HEATING→CUT→RETRUDE→EXTRUDE→FLUSH→END,
  BOX_QUIT_MATERIAL-Kette, M8200 P/C/R/L/W/F/O → CR_BOX_PRE_OPT/CUT/
  RETRUDE/EXTRUDE/WASTE/FLUSH/END_OPT, BOX_CUT_MATERIAL) sowie
  live-1.1.7.0-configs/box-live.cfg ([box] bus:serial485, Tn_retrude -10,
  Tn_extrude 140/220 °C/360, buffer_empty_len 30 – reine Doku, keine
  Toolhead-Bewegung hier)

Ablauf (wie Stock/Box-Live, nur Box-Seite – kein Toolhead/Motor):
  A0 Adressvergabe via AutoAddr (erst nach ``k2_motor:ready`` – gleiche
     Reihenfolge wie Stock: Motor-Init → auto_addr:start; hier Event)
  A1 zyklischer Status-Poll 0x0a (retries=0, alle ~4 s wie Original)
  A2 lesend jederzeit: RFID (0x02), Remain (0x03), Version/SN (0x14),
     Hardware-Status (0x15), Sensor (0x08), Buffer (0x05), Messrad (0x0e [01])
  A3 Preload 0x0d op 0-3 (op 2/3 lange Timeouts + retries=0, keine Bursts)
  A4 Extrude (Load, Box-Seite): 0x04 [00 01] IDLE → 0x0f [01] an →
     0x08 [00] Sensor → 0x10 [m 00 00] Stage1 → 0x10 [m 04 00] Start →
     0x10 [m 05 00] x5 Odometer-Polls → 0x10 [m 06 00] Stopp/Buffer →
     0x10 [m 07 03] Extruder-Check (p=3 mm) → 0x05 Buffer →
     0x04 [m 00] PRINT → 0x0f [00] aus. Byte-identisch zu
     cfsseq.build_extrude_sequence(mask, pushes=5) == EXTRUDE_LIVE_CMDS_B
     (Beleg PROTOKOLL-485.md §6.5, cfsseq-Selbsttest).
     Abbruch bei Status != 0 mit CfsStatusError-Text (z.B. 0x0c).
  A5 Retrude (Unload, Box-Seite): 0x04 [00 01] → 0x08 [00] →
     0x11 [m 00] x4 → 0x11 [m 01] (Beleg RETRUDE_LIVE_CMDS_A gefiltert,
     cfsseq.build_retrude_sequence; 0x0e-/0x03-Zwischenabfragen sind
     Diagnose und nicht Teil der Sequenz).
  A6 Cut als Platzhalter (braucht Motor/Toolhead – sendet NICHTS).

Sicherheit (PROTOKOLL-485 §6.8/§6.9, BEFUND2 §5-§8):
- Schreiben (0x0d/0x04/0x0f/0x10/0x11, Auto-Addr 0xA0, GPIO gibt es hier
  nicht) nur mit ``allow_write: True`` (Default False → nur lesen + Plan).
  Lesend (0x02/0x03/0x0a/0x0e-lesen/0x14/0x15/0x05/0x08) jederzeit.
- NIE 0x50 (Config #6 schreiben + Reset) und NIE 0x06-Feeder senden
  (0x50 resettet die Box, 0x06 hat FW-Bug Maske aus r7 → immer Status 1;
  Beleg Func-Tabelle). Guard im Code + Test.
- Keine Bursts: Mechanik-Kommandos (0x10/0x11, 0x0d op 2/3) mit retries=0
  und langen Timeouts (Box antwortet erst nach dem Vorgang; ein Retransmit
  würde ihn doppelt starten; Beleg BEFUND2 §5: keine Duplikaterkennung).
  Original-Retries für den Rest via box.func_retries (0x0a Poll retries=0).
- Bus-Lock mit [k2_motor] teilen: EIN gemeinsamer RS485-Transport/Lock
  (ttyS5, 230400, Halbduplex, Lock über die ganze Transaktion, vgl.
  serial485 MAX_BUS_HOLD_S). Dieses Modul öffnet den Port NICHT selbst,
  sondern bekommt ``bus`` injiziert – dieselbe bus-Instanz wie [k2_motor].

Klipper-Einbindung (Dateien kopieren, s. BERICHT.md): ``k2_box.py`` nach
``klippy/extras/`` (als ``k2_box.py``), ``[k2_box]`` in printer.cfg.
Alle Bus-Zugriffe laufen über BoxAction (gekapselt, Tests mit Fake-Bus).
Ohne Klipper importierbar (reine Logik + Fakes, s. test_k2_box.py).

G-Codes:
- ``K2_BOX_STATUS`` – frischer 0x0a-Poll (lesend) + Bericht.
- ``K2_BOX_LOAD SLOT=A..D [DRY_RUN=1]`` – Box-Load (Extrude-Ablauf A4).
  Default: Plan ausgeben, nichts senden (ohne allow_write bzw. mit DRY_RUN).
- ``K2_BOX_UNLOAD SLOT=A..D [DRY_RUN=1]`` – Box-Unload (Retrude-Ablauf A5).
- ``K2_BOX_PRELOAD SLOT=A..D OP=0..3 [DRY_RUN=1]`` – 0x0d op (0 disable,
  1 enable, 2 Force mit RFID, 3 Force ohne RFID).
"""

import os
import struct
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_REPO = next((d for d in (os.path.dirname(_HERE), os.path.dirname(os.path.dirname(_HERE)))
              if os.path.isdir(os.path.join(d, "nachbau"))), os.path.dirname(_HERE))
_NACHBAU = os.path.join(_REPO, "nachbau")
if os.path.isdir(_NACHBAU) and _NACHBAU not in sys.path:
    sys.path.insert(0, _NACHBAU)

try:
    import box as _BOX  # noqa: F401
    from box import (BoxAction, CfsStatusError, StatusBytes,  # noqa: F401
                     func_retries, preload_force_timeout, status_name,
                     parse_box_state, get_rfid, get_remain_len,
                     get_measuring_wheel, parse_version_sn,
                     parse_buffer_state, parse_filament_sensor_state,
                     parse_get_hardware_status, get_box_status,
                     BOX_STATE_NAMES, BOX_STATE_IDLE, BOX_STATE_ERROR,
                     PRELOAD_DISABLE, PRELOAD_ENABLE, PRELOAD_FORCE_READ,
                     PRELOAD_FORCE_TENSION_ONLY)
    _HAVE_BOX = True
except Exception as _e:  # pragma: no cover
    raise ImportError("nachbau/box.py wird benötigt (gleicher Stil wie "
                      "k2_motor_init.py -> k2_transport.py): %s" % _e)

try:
    import cfsseq as _SEQ  # noqa: F401
    from cfsseq import (slot_to_mask, RETRUDE_TO_BUFFER_REPEATS,  # noqa: F401
                        EXTRUDE_MODE_STAGE1, EXTRUDE_MODE_FEED_START,
                        EXTRUDE_MODE_QUERY, EXTRUDE_MODE_STOP_BUFFER,
                        EXTRUDE_MODE_EXTRUDER_CHECK, EXTRUDER_CHECK_MM,
                        RETRUDE_P_TO_BUFFER, RETRUDE_P_PULL_HUB,
                        EXTRUDE_LIVE_CMDS_B, RETRUDE_LIVE_CMDS_A,
                        build_extrude_sequence, build_retrude_sequence,
                        requests_from_log_cmds, filter_funs)
    _HAVE_SEQ = True
except Exception as _e:  # pragma: no cover
    raise ImportError("nachbau/cfsseq.py wird benötigt: %s" % _e)

try:
    import auto_addr as _AA  # noqa: F401
    _HAVE_AA = True
except Exception as _e:  # pragma: no cover
    raise ImportError("nachbau/auto_addr.py wird benötigt: %s" % _e)

try:
    import serial485 as _S485  # noqa: F401
    _HAVE_S485 = True
except Exception:  # pragma: no cover
    _S485 = None
    _HAVE_S485 = False

# =====================================================================
# Konstanten (Belege s. Docstring; Werte aus box.py/cfsseq.py wiederverwendet)
# =====================================================================

BOX_ADDR_DEFAULT = _BOX.BOX_ADDR  # 0x01 (Beleg PROTOKOLL-485.md §2)
SLOT_LETTERS = "ABCD"
SLOT_MASKS = (0x01, 0x02, 0x04, 0x08)
MASK_ALL = 0x0F

# NIE senden (Guard, Beleg PROTOKOLL-485.md Func-Tabelle + §6.9):
# - 0x50 schreibt Config #6 + speichert + resettet (gleicher Wert -> Status 3).
# - 0x06 Feeder hat FW-Bug (Maske aus r7 = Tick-Wert, praktisch immer Status 1).
FORBIDDEN_FUNS = (0x50, 0x06)

FUNC_GET_BOX_STATE = _BOX.FUNC_GET_BOX_STATE  # 0x0a
FUNC_SET_BOX_MODE = _BOX.FUNC_SET_BOX_MODE  # 0x04
FUNC_PRELOAD_INHIBIT = _BOX.FUNC_PRELOAD_INHIBIT  # 0x0f
FUNC_EXTRUDE = _BOX.FUNC_EXTRUDE  # 0x10
FUNC_RETRUDE = _BOX.FUNC_RETRUDE  # 0x11

POLL_INTERVAL_DEFAULT = 4.0  # s, Original-Poll alle ~4 s (Beleg §6.2, 10 560 Polls)
ODO_POLLS_DEFAULT = 5  # Auftrag A4: 5 Odometer-Polls (Beleg EXTRUDE_LIVE_CMDS_B)
RETRUDE_REPEATS_DEFAULT = RETRUDE_TO_BUFFER_REPEATS  # 4 (Beleg §6.5, 16 von 26)

PRELOAD_OP_NAMES = {
    PRELOAD_DISABLE: "disable (Insert-Reading/Preload aus)",
    PRELOAD_ENABLE: "enable (Insert-Reading/Preload an)",
    PRELOAD_FORCE_READ: "force-read (Forced Preload MIT RFID)",
    PRELOAD_FORCE_TENSION_ONLY: "tension-only (Forced Preload OHNE RFID)",
}


def check_not_forbidden(fun):
    """Guard: 0x50/0x06 dürfen nie gesendet werden (Beleg s. FORBIDDEN_FUNS)."""
    if fun in FORBIDDEN_FUNS:
        raise ValueError("fun 0x%02x ist verboten (0x50 resettet, 0x06 FW-Bug)" % fun)
    return fun


def parse_slot(slot):
    """SLOT A..D (Auftrag) -> (index 0..3, letter, mask).

    Akzeptiert Buchstaben A-D (case-insensitiv) und Indizes 0-3 (Komfort,
    ANNAHME – Original-G-Code NUM=0..3 numerisch, Beleg PROTOKOLL-485.md:
    NUM=A wirft key60). Masken (0x01/0x02/0x04/0x08) werden NICHT akzeptiert
    (Verwechslungsgefahr) – dafür slot_to_mask() verwenden.
    """
    if isinstance(slot, bool):
        raise ValueError("SLOT muss A..D oder 0..3 sein: %r" % (slot,))
    if isinstance(slot, int) and slot in range(4):
        idx = int(slot)
        return idx, SLOT_LETTERS[idx], SLOT_MASKS[idx]
    if isinstance(slot, str):
        s = slot.strip().upper()
        if len(s) == 1 and s in SLOT_LETTERS:
            idx = SLOT_LETTERS.index(s)
            return idx, s, SLOT_MASKS[idx]
        if s in ("0", "1", "2", "3"):
            idx = int(s)
            return idx, SLOT_LETTERS[idx], SLOT_MASKS[idx]
    raise ValueError("SLOT muss A..D (oder 0..3) sein: %r" % (slot,))


def parse_preload_op(op):
    """OP 0..3 -> int (s. PRELOAD_OP_NAMES)."""
    if isinstance(op, bool):
        raise ValueError("OP muss 0..3 sein: %r" % (op,))
    if isinstance(op, int) and op in PRELOAD_OP_NAMES:
        return int(op)
    if isinstance(op, str):
        s = op.strip()
        if s in ("0", "1", "2", "3"):
            return int(s)
        low = s.lower()
        for code, name in PRELOAD_OP_NAMES.items():
            if low == name.split(" ")[0]:
                return code
    raise ValueError("OP muss 0..3 sein (0 disable, 1 enable, 2 force-read, "
                     "3 tension-only): %r" % (op,))


# =====================================================================
# Config (rein + Klipper-Hülle, gleicher Stil wie k2_motor_init.py)
# =====================================================================

def parse_options(opts):
    """Reines Dict (keys klein, Werte Strings) -> strukturierte Config.

    Unterstützte Optionen (ANNAHME für neue Keys, Defaults sicher):
    - box_addr (Default 1), allow_write (Default False),
      shutdown_on_failure (Default False), auto_init (Default True),
      poll_interval (Default 4.0 s).
    """
    o = {str(k).strip().lower(): v for k, v in dict(opts).items()}

    def _int(key, default):
        try:
            return int(str(o.get(key, default)).strip())
        except ValueError:
            raise ValueError("%s muss int sein: %r" % (key, o.get(key)))

    def _float(key, default):
        try:
            return float(str(o.get(key, default)).strip())
        except ValueError:
            raise ValueError("%s muss Zahl sein: %r" % (key, o.get(key)))

    def _bool(key, default):
        v = str(o.get(key, str(default))).strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off"):
            return False
        raise ValueError("%s muss bool sein: %r" % (key, o.get(key)))

    cfg = {
        "box_addr": _int("box_addr", BOX_ADDR_DEFAULT),
        "allow_write": _bool("allow_write", False),
        "shutdown_on_failure": _bool("shutdown_on_failure", False),
        "auto_init": _bool("auto_init", True),
        "poll_interval": _float("poll_interval", POLL_INTERVAL_DEFAULT),
    }
    if not 0x01 <= cfg["box_addr"] <= 0x04:
        raise ValueError("box_addr muss 1..4 sein (Auto-Addr-Tabelle MB): %r"
                         % (cfg["box_addr"],))
    if not cfg["poll_interval"] > 0:
        raise ValueError("poll_interval muss > 0 sein: %r" % (cfg["poll_interval"],))
    return cfg


def parse_klipper_config(config):
    """Klipper-ConfigWrapper -> parse_options (ANNAHME zur API, s. BERICHT).

    Versucht get_options()/get wie in k2_motor_init.parse_klipper_config.
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
    for k in ("box_addr", "allow_write", "shutdown_on_failure", "auto_init",
              "poll_interval"):
        if k not in opts:
            v = _g(k, None)
            if v is not None:
                opts[k] = v
    return parse_options(opts)


# =====================================================================
# Auto-Addr-Adapter (Box-Seite, erst nach k2_motor:ready)
# =====================================================================

class BoxAutoAddrSerial:
    """Adapter: auto_addr-Serial-API um einen Box-Bus (serial485-artig).

    auto_addr.AutoAddr braucht ``cmd_send_data_with_response(data, timeout,
    retransmit)`` mit Wire-Bytes OHNE Head/CRC ([addr][len][status][fun]
    [daten...]) und liefert den VOLLEN Antwort-Frame ([F7]...[crc]) oder
    None bei Timeout (Beleg auto_addr.py send_package/data_handler).

    Umsetzung hier: Wire zerlegen -> bus.transact_full(addr, fun, payload,
    timeout, retries=0, status, expect_fun=fun, expect_addr=None) – mit
    expect_addr=None, weil Broadcasts (0xFE) mit Geräte- ODER neuer Adresse
    antworten (Beleg PROTOKOLL-485.md §6.8: A1 an 0xFE, A0-Antwort von neuer
    Adresse, 3x). retries=0 wie Original (Auto-Addr/Broadcasts, Beleg §6.2).
    """

    def __init__(self, bus, allow_write=True):
        if bus is None or not hasattr(bus, "transact_full"):
            raise ValueError("BoxAutoAddrSerial braucht bus.transact_full()")
        self.bus = bus
        self.allow_write = bool(allow_write)
        self.calls = []  # [(addr, fun, tuple(payload))] – Diagnose

    def cmd_send_data_with_response(self, data, timeout, retransmit):
        wire = bytes(data)
        if len(wire) < 4:
            return None
        addr, _length, status, fun = wire[0], wire[1], wire[2], wire[3]
        payload = list(wire[4:])
        self.calls.append((addr, fun, tuple(payload)))
        # 0xA0 (Adressvergabe) ist schreibend – ohne allow_write blocken.
        if fun == 0xA0 and not self.allow_write:
            return None
        check_not_forbidden(fun)
        try:
            _raddr, _rstatus, _rfun, _rdata, rraw = self.bus.transact_full(
                addr, fun, payload, timeout=timeout, retries=0,
                status=status, expect_fun=fun, expect_addr=None)
        except (TimeoutError, ValueError):
            return None
        return bytes(rraw)


# =====================================================================
# Controller – reine Box-Logik (ohne Klipper importierbar)
# =====================================================================

class K2BoxController:
    """Box-Abläufe gegen den Bus (vgl. BoxAction.communication_*).

    ``bus`` ist serial485-artig (transact/transact_full, z.B.
    serial485.Serial485 für /dev/ttyS5 – DIESELBE Instanz wie [k2_motor],
    damit EIN Lock den Halbduplex-Bus serialisiert). Alle BoxAction-Aufrufe
    laufen darüber (Statusprüfung via CfsStatusError, s. box.BoxAction).
    """

    def __init__(self, cfg, bus, auto_addr_obj=None, pause=None,
                 logger=None, send_event=None):
        self.cfg = dict(cfg)
        if bus is None or not hasattr(bus, "transact_full"):
            # Fallback auf transact (Status dann unbekannt – wie BoxAction).
            if bus is None or not hasattr(bus, "transact"):
                raise ValueError("K2BoxController braucht bus.transact_full() "
                                 "(serial485-artig, geteilt mit [k2_motor])")
        self.bus = bus
        self.action = BoxAction(bus, addr=self.cfg.get("box_addr", BOX_ADDR_DEFAULT))
        self.auto_addr = auto_addr_obj
        self._addr_store = {}
        self.pause = pause if pause is not None else time.sleep
        self.log = logger if logger is not None else self._print_log()
        self.send_event = send_event
        self.motor_ready = False
        self.is_ready = False
        self.last_poll = None  # parse_box_state-Dict (Cache für Moonraker)
        self.last_poll_raw = None  # StatusBytes
        self.box_fw = None  # z.B. "153" aus 0x14 (für parse_box_state)
        self.slot_cache = {}  # letter -> {rfid, remain}
        self._stop = threading.Event()
        self._thread = None

    class _print_log:
        def info(self, msg):
            print(msg)

        def error(self, msg):
            print("FEHLER: %s" % msg)

    # --- Helfer -------------------------------------------------------
    def _require_write(self, name):
        """Schreib-Guard: ohne allow_write -> PermissionError, NICHTS gesendet."""
        if not self.cfg.get("allow_write"):
            raise PermissionError(
                "%s braucht allow_write: True (Default False → nur lesen + "
                "Plan, nichts gesendet)" % name)
        return True

    def _odo_mm(self, raw4):
        """4 B Odometer-Float BE (0x10 mode 5) -> mm (Betrag, positiv)."""
        raw = bytes(raw4)
        if len(raw) != 4:
            raise ValueError("Odometer-Antwort muss 4 Bytes haben: %r" % (raw,))
        (neg,) = struct.unpack(">f", raw)
        return -float(neg)

    # --- Plan-Text (Trockenlauf, kein Bus) ------------------------------
    def describe_plan(self):
        out = ["K2-Box-Plan (Trockenlauf, nichts gesendet)"]
        out.append("box_addr=0x%02X allow_write=%s poll_interval=%.1f s" % (
            self.cfg.get("box_addr", BOX_ADDR_DEFAULT),
            self.cfg.get("allow_write"), self.cfg.get("poll_interval",
                                                      POLL_INTERVAL_DEFAULT)))
        out.append("A0 Adressvergabe (Auto-Addr 0xA0-0xA3, erst nach k2_motor:ready)")
        out.append("A1 Status-Poll 0x0a (retries=0, alle ~%.0f s wie Original)" % (
            self.cfg.get("poll_interval", POLL_INTERVAL_DEFAULT),))
        out.append("A2 lesend: 0x02 RFID / 0x03 Remain / 0x14 Version / 0x15 HW / "
                   "0x08 Sensor / 0x05 Buffer / 0x0e Messrad (retries wie Original)")
        out.append("A3 Preload 0x0d op 0 disable / 1 enable / 2 Force+RFID / "
                   "3 Force ohne RFID (op 2/3: Timeout >=380 s, retries=0, keine Bursts)")
        out.append("A4 Extrude (Load): 04 IDLE → 0f an → 08 Sensor → 10/00 → 10/04 → "
                   "10/05 x%d → 10/06 → 10/07 p=3 → 05 → 04 PRINT → 0f aus" % ODO_POLLS_DEFAULT)
        out.append("A5 Retrude (Unload): 04 IDLE → 08 Sensor → 11 p0 x%d → 11 p1" % (
            RETRUDE_REPEATS_DEFAULT,))
        out.append("A6 Cut: Platzhalter (braucht Motor/Toolhead – sendet NICHTS)")
        out.append("Verboten: nie 0x50 (Reset) / nie 0x06-Feeder (FW-Bug); "
                   "Bus-Lock mit [k2_motor] teilen (eine ttyS5-Instanz).")
        if not self.cfg.get("allow_write"):
            out.append("Hinweis: allow_write=False → nur lesen + Plan (keine "
                       "0x0d/0x04/0x0f/0x10/0x11, kein 0xA0).")
        return "\n".join(out)

    # --- A0 Adressvergabe ------------------------------------------------
    def on_motor_ready(self):
        """Event k2_motor:ready → Adressvergabe starten (s. PrinterK2Box)."""
        self.motor_ready = True
        return self.ensure_addr(motor_ready=True)

    def ensure_addr(self, motor_ready=None):
        """Auto-Addr-Ablauf (startup + ein Online-Zyklus).

        Erst nach k2_motor:ready (Beleg Stock-Bootsequenz PROTOKOLL-485.md
        §3.1 Schritt 11: Motor-Init → auto_addr:start). Ohne motor_ready →
        Status wait_motor, NICHTS gesendet. Ohne allow_write → nur lesende
        Anteile? Entwurf: ganz übersprungen (0xA0 ist schreibend), Status
        skipped – lesende Polls laufen trotzdem (Adresse meist schon vergeben).
        """
        ready = self.motor_ready if motor_ready is None else bool(motor_ready)
        if not ready:
            info = "wartet auf k2_motor:ready (nichts gesendet)"
            try:
                self.log.info(info)
            except Exception:
                pass
            return {"status": "wait_motor", "log": info, "ready": False}
        if not self.cfg.get("allow_write"):
            info = ("allow_write=False: keine Adressvergabe (0xA0 schreibend) – "
                    "nur lesende Polls")
            try:
                self.log.info(info)
            except Exception:
                pass
            return {"status": "skipped", "log": info, "ready": False}
        if self.auto_addr is None:
            serial = BoxAutoAddrSerial(self.bus, allow_write=True)
            self.auto_addr = _AA.AutoAddr(serial, store=self._addr_store)
        try:
            self.auto_addr.startup()
            txt = self.auto_addr.online_once()
        except Exception as exc:
            try:
                self.log.error("Auto-Addr: %s" % exc)
            except Exception:
                pass
            return {"status": "addr_error", "log": str(exc), "ready": False}
        self.is_ready = True
        try:
            if self.send_event is not None:
                self.send_event("k2_box:ready")
        except Exception as exc:
            try:
                self.log.info("Event k2_box:ready nicht sendbar: %s" % exc)
            except Exception:
                pass
        return {"status": "ready", "log": txt, "ready": True}

    # --- A1 Status-Poll (lesend, retries=0) -------------------------------
    def poll_status(self):
        """0x0a-Poll (retries=0 wie Original), Cache für Moonraker.

        Lesend – auch ohne allow_write erlaubt (Herzschlag alle ~4 s, Beleg
        PROTOKOLL-485.md §6.2: 10 560 Polls, retries=0). Gibt
        parse_box_state-Dict zurück (mit temp_c/rh/hub_mask/state).
        """
        raw = self.action.communication_get_box_state()
        self.last_poll_raw = raw
        try:
            parsed = parse_box_state(raw, box_fw=self.box_fw)
        except Exception:
            parsed = parse_box_state(raw)
        self.last_poll = parsed
        return parsed

    def start_background(self, interval=None):
        """Hintergrund-Poll (Threading, offline/Test) statt Reactor-Timer."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        wait = float(interval if interval is not None
                     else self.cfg.get("poll_interval", POLL_INTERVAL_DEFAULT))

        def loop():
            while not self._stop.is_set():
                try:
                    self.poll_status()
                except Exception:
                    pass
                if self._stop.wait(wait):
                    break

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop_background(self):
        self._stop.set()

    # --- A2 lesende Abfragen (auch ohne allow_write) ----------------------
    def get_rfid(self, slot):
        idx, letter, _mask = parse_slot(slot)
        mask = SLOT_MASKS[idx]
        raw = self.action.communication_get_rfid(mask)
        parsed = get_rfid(raw)
        val = parsed.get(letter)
        self.slot_cache.setdefault(letter, {})["rfid"] = val
        return val

    def get_remain(self, mask=MASK_ALL):
        if isinstance(mask, bool) or not isinstance(mask, int) \
                or not 0 <= mask <= MASK_ALL:
            raise ValueError("Maske muss 0x00..0x0f sein: %r" % (mask,))
        raw = self.action.communication_get_remain_len(mask)
        parsed = get_remain_len(bytes([mask]), raw)
        for letter, val in parsed.items():
            self.slot_cache.setdefault(letter, {})["remain"] = val
        return parsed

    def get_version_sn(self):
        raw = self.action.communication_get_version_sn()
        ver, sn = parse_version_sn(raw)
        self.box_fw = ver
        return ver, sn

    def get_hardware_status(self):
        raw = self.action.communication_get_hardware_status()
        return parse_get_hardware_status(raw)

    def get_sensor(self, pos=0x00):
        raw = self.action.communication_get_filament_sensor_state(pos)
        return parse_filament_sensor_state(raw)

    def get_buffer(self):
        raw = self.action.communication_get_buffer_state()
        return parse_buffer_state(raw)

    def get_wheel(self):
        return self.action.communication_measuring_wheel()

    # --- A3 Preload -------------------------------------------------------
    def do_preload(self, slot, op, timeout=None, dry_run=False):
        """0x0d [Maske][op] (op 0-3, Beleg GHIDRA §3).

        op 2/3: Antwort erst nach dem Vorgang → langer Timeout
        (preload_force_timeout) + retries=0 (kein Burst, Beleg BEFUND2 §5).
        Schreibend → allow_write nötig (auch op 0/1 ändern PRE[1] global).
        """
        idx, letter, mask = parse_slot(slot)
        op = parse_preload_op(op)
        if dry_run or not self.cfg.get("allow_write"):
            if dry_run:
                return {"status": "dry_run", "slot": letter, "op": op,
                        "plan": "0x0d [%02x %02x] (%s)" % (
                            mask, op, PRELOAD_OP_NAMES[op])}
            self._require_write("Preload SLOT=%s OP=%d" % (letter, op))
        check_not_forbidden(_BOX.FUNC_SET_PRE_LOADING)
        raw = self.action.communication_set_pre_loading(mask, op,
                                                       timeout=timeout)
        return {"status": "ok", "slot": letter, "op": op, "raw": bytes(raw)}

    # --- A4 Extrude (Load, Box-Seite) --------------------------------------
    def do_extrude(self, slot, odo_polls=ODO_POLLS_DEFAULT, dry_run=False):
        """Box-Extrude A4 (Byte-folge == cfsseq.build_extrude_sequence).

        Abbruch bei Status != 0: BoxAction wirft CfsStatusError (mit
        Status-Name, z.B. 0x0c Odometer-Stillstand) – hier mit Slot-Kontext
        angereichert und propagiert (Auftrag: „Abbruch mit CfsStatusError-Text“).
        """
        idx, letter, mask = parse_slot(slot)
        if isinstance(odo_polls, bool) or not isinstance(odo_polls, int) \
                or odo_polls < 1:
            raise ValueError("odo_polls muss int >= 1 sein: %r" % (odo_polls,))
        if dry_run or not self.cfg.get("allow_write"):
            if dry_run:
                return {"status": "dry_run", "slot": letter,
                        "plan": self.describe_plan()}
            self._require_write("Extrude SLOT=%s" % letter)
        for fun in (FUNC_SET_BOX_MODE, FUNC_PRELOAD_INHIBIT, FUNC_EXTRUDE):
            check_not_forbidden(fun)
        odo_trace = []

        def _run(mode, p, label):
            try:
                raw = self.action.communication_extrude(mask, mode, p)
            except CfsStatusError as exc:
                raise CfsStatusError(
                    exc.status,
                    "%s SLOT=%s: %s" % (label, letter, exc.name),
                    exc.data, exc.fun)
            if mode == _SEQ.EXTRUDE_MODE_QUERY:
                try:
                    odo_trace.append(self._odo_mm(bytes(raw)))
                except ValueError:
                    pass
            return raw

        try:
            self.action.communication_set_box_mode_feed()
            self.action.communication_set_preload_inhibit(True)
            self.action.communication_get_filament_sensor_state(0x00)
            _run(_SEQ.EXTRUDE_MODE_STAGE1, 0x00, "Stage1 (mode 0)")
            _run(_SEQ.EXTRUDE_MODE_FEED_START, 0x00, "Feed-Start (mode 4)")
            for _ in range(odo_polls):
                _run(_SEQ.EXTRUDE_MODE_QUERY, 0x00, "Odometer-Poll (mode 5)")
            _run(_SEQ.EXTRUDE_MODE_STOP_BUFFER, 0x00, "Stopp/Buffer (mode 6)")
            _run(_SEQ.EXTRUDE_MODE_EXTRUDER_CHECK, EXTRUDER_CHECK_MM,
                 "Extruder-Check (mode 7 p=3)")
            self.action.communication_get_buffer_state()
            self.action.communication_set_box_mode_print(mask)
            self.action.communication_set_preload_inhibit(False)
        except CfsStatusError:
            raise
        return {"status": "ok", "slot": letter, "odo_mm": list(odo_trace)}

    def do_load(self, slot, dry_run=False, odo_polls=ODO_POLLS_DEFAULT):
        """Box-Load = Extrude-Ablauf A4 (Toolhead-Heizen/Fahren macht der
        Aufrufer per Makro – hier nur Box-Förderung, ANNAHME s. BERICHT)."""
        if dry_run:
            idx, letter, _m = parse_slot(slot)
            return {"status": "dry_run", "slot": letter,
                    "plan": self.describe_plan()}
        return self.do_extrude(slot, odo_polls=odo_polls)

    # --- A5 Retrude (Unload, Box-Seite) ------------------------------------
    def do_retrude(self, slot,
                   to_buffer_repeats=RETRUDE_REPEATS_DEFAULT, dry_run=False):
        idx, letter, mask = parse_slot(slot)
        if isinstance(to_buffer_repeats, bool) or not isinstance(
                to_buffer_repeats, int) or to_buffer_repeats < 0:
            raise ValueError("to_buffer_repeats muss int >= 0 sein: %r"
                             % (to_buffer_repeats,))
        if dry_run or not self.cfg.get("allow_write"):
            if dry_run:
                return {"status": "dry_run", "slot": letter,
                        "plan": self.describe_plan()}
            self._require_write("Retrude SLOT=%s" % letter)
        for fun in (FUNC_SET_BOX_MODE, FUNC_RETRUDE):
            check_not_forbidden(fun)
        try:
            self.action.communication_set_box_mode_feed()
            if to_buffer_repeats:
                self.action.communication_get_filament_sensor_state(0x00)
                for _ in range(to_buffer_repeats):
                    self.action.communication_retrude(
                        mask, RETRUDE_P_TO_BUFFER)
            self.action.communication_retrude(mask, RETRUDE_P_PULL_HUB)
        except CfsStatusError as exc:
            raise CfsStatusError(
                exc.status, "Retrude SLOT=%s: %s" % (letter, exc.name),
                exc.data, exc.fun)
        return {"status": "ok", "slot": letter}

    def do_unload(self, slot, dry_run=False,
                  to_buffer_repeats=RETRUDE_REPEATS_DEFAULT):
        if dry_run:
            idx, letter, _m = parse_slot(slot)
            return {"status": "dry_run", "slot": letter,
                    "plan": self.describe_plan()}
        return self.do_retrude(slot, to_buffer_repeats=to_buffer_repeats,
                               dry_run=False)

    # --- A6 Cut (Platzhalter) ------------------------------------------------
    def do_cut(self, slot=None):
        """Cut-Ablauf als Platzhalter (braucht Motor/Toolhead).

        ANNAHME (Beleg CHECKLISTE/Box-Makros: BOX_CUT_MATERIAL fährt Kopf an
        Cutter-Pos und nutzt Motor-Boards): sendet bewusst NICHTS – hier nur
        Box-Seite verfügbar. Aufrufer nutzt Original-Makro M8200 C / CR_BOX_CUT.
        """
        raise NotImplementedError(
            "Cut braucht Motor/Toolhead (Fahrt an Cutter-Pos + Stall) – "
            "Box-Modul sendet nichts (Platzhalter)")

    # --- Status-Objekt für Moonraker (kein Bus) ------------------------------
    def get_status(self, eventtime=None):
        """Moonraker-Status (nur Cache, kein Bus – Klipper ruft oft auf).

        Keys: ready, box_addr, state, state_name, hub_mask, temp_c, rh,
        slots ({A..D: {rfid, remain}}), box_fw. Vor dem ersten Poll:
        state unknown.
        """
        _ = eventtime
        base = {"ready": bool(self.is_ready),
                "box_addr": self.cfg.get("box_addr", BOX_ADDR_DEFAULT),
                "box_fw": self.box_fw,
                "state": None, "state_name": "unknown",
                "hub_mask": None, "temp_c": None, "rh": None,
                "slots": {k: dict(v) for k, v in self.slot_cache.items()}}
        poll = self.last_poll or {}
        if poll.get("kind") == "state":
            base.update({"state": poll.get("state"),
                         "state_name": poll.get("state_name", "unknown"),
                         "hub_mask": poll.get("hub_mask"),
                         "temp_c": poll.get("temp_c"),
                         "rh": poll.get("rh")})
        elif poll.get("kind") == "event":
            base.update({"state": None, "state_name": "event",
                         "hub_mask": None,
                         "events": dict(poll.get("events", {}))})
        return base


# =====================================================================
# Klipper-Hülle [k2_box] (ANNAHMEN zur Upstream-API, s. BERICHT.md)
# =====================================================================

class PrinterK2Box:
    """Klipper-Objekt [k2_box] – verdrahtet Controller mit Klipper."""

    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        try:
            self.cfg = parse_klipper_config(config)
        except Exception as exc:
            raise config.error("[k2_box] Config-Fehler: %s" % exc)
        self._bus = None  # ANNAHME: geteilte ttyS5-Instanz (s. _ensure_bus)
        self.controller = None
        self.printer.register_event_handler("klippy:connect",
                                            self.handle_connect)
        # Adressvergabe erst nach Motor-Init (gleiche Reihenfolge wie Stock).
        try:
            self.printer.register_event_handler("k2_motor:ready",
                                                self.handle_motor_ready)
        except Exception:
            pass
        self.gcode.register_command("K2_BOX_STATUS", self.cmd_status,
                                    desc="K2-Box-Status (0x0a-Poll, lesend)")
        self.gcode.register_command("K2_BOX_LOAD", self.cmd_load,
                                    desc="K2-Box-Load SLOT=A..D [DRY_RUN=1]")
        self.gcode.register_command("K2_BOX_UNLOAD", self.cmd_unload,
                                    desc="K2-Box-Unload SLOT=A..D [DRY_RUN=1]")
        self.gcode.register_command("K2_BOX_PRELOAD", self.cmd_preload,
                                    desc="K2-Box-Preload SLOT=A..D OP=0..3")

    def _ensure_bus(self):
        # ANNAHME: EIN geteilter RS485-Bus (dieselbe Instanz wie [k2_motor],
        # Halbduplex-Lock über die ganze Transaktion). Betreiber stellt ihn
        # als printer-Objekt 'k2_485' bereit (Upstream hat keinen zentralen
        # 485-Manager); ohne Bus bleibt das Modul im Diagnose-Modus.
        if self._bus is not None:
            return self._bus
        for name in ("k2_485", "k2_rs485", "serial485"):
            try:
                self._bus = self.printer.lookup_object(name)
                return self._bus
            except Exception:
                continue
        raise RuntimeError("RS485-Bus nicht verdrahtet (ANNAHME: printer-Objekt "
                           "'k2_485' mit derselben ttyS5-Instanz wie [k2_motor])")

    def _ensure_controller(self):
        if self.controller is not None:
            return self.controller
        bus = self._ensure_bus()
        self.controller = K2BoxController(
            self.cfg, bus, pause=getattr(self.reactor, "pause", time.sleep),
            logger=self.gcode,
            send_event=getattr(self.printer, "send_event", None))
        return self.controller

    def handle_connect(self):
        try:
            self._ensure_controller()
        except Exception as exc:
            try:
                self.gcode.respond_info("[k2_box] Connect ohne Bus: %s" % exc)
            except Exception:
                pass
            return
        # Poll-Timer erst nach k2_motor:ready starten (Adresse!). ANNAHME
        # Upstream-API: reactor.register_timer(cb, reactor.NOW).
        if self.cfg.get("auto_init"):
            try:
                self.gcode.respond_info(
                    "[k2_box] bereit (warte auf k2_motor:ready für Auto-Addr)")
            except Exception:
                pass

    def handle_motor_ready(self, *args):
        try:
            ctl = self._ensure_controller()
        except Exception as exc:
            try:
                self.gcode.respond_info("[k2_box] kein Bus für Auto-Addr: %s"
                                        % exc)
            except Exception:
                pass
            return
        res = ctl.on_motor_ready()
        try:
            self.gcode.respond_info("[k2_box] Auto-Addr: %s" % (res,))
        except Exception:
            pass
        # Zyklischer 0x0a-Poll (ANNAHME Reactor-Timer, Intervall poll_interval).
        try:
            if hasattr(self.reactor, "register_timer"):
                interval = float(self.cfg.get("poll_interval",
                                              POLL_INTERVAL_DEFAULT))

                def _poll(eventtime):
                    try:
                        ctl.poll_status()
                    except Exception:
                        pass
                    return eventtime + interval

                self.reactor.register_timer(
                    _poll, getattr(self.reactor, "NOW", 0))
        except Exception:
            pass

    def get_status(self, eventtime=None):
        if self.controller is None:
            return {"ready": False, "state_name": "unknown"}
        return self.controller.get_status(eventtime)

    # -- G-Code-Helfer ---------------------------------------------------
    @staticmethod
    def _slot_param(gcmd):
        for key in ("SLOT", "slot"):
            try:
                val = gcmd.get(key, None)
            except Exception:
                val = None
            if val is not None:
                return val
        raise ValueError("SLOT=A..D fehlt (z.B. K2_BOX_LOAD SLOT=B)")

    def cmd_status(self, gcmd):
        try:
            ctl = self._ensure_controller()
        except Exception as exc:
            gcmd.respond_info("[k2_box] kein Bus: %s" % exc)
            return
        try:
            poll = ctl.poll_status()
        except Exception as exc:
            gcmd.respond_info("[k2_box] Poll-Fehler: %s" % exc)
            return
        st = ctl.get_status()
        gcmd.respond_info(
            "[k2_box] Zustand=%s Hub=0x%02x Temp=%s °C Feuchte=%s %% Slots=%s" % (
                st.get("state_name"), st.get("hub_mask") or 0,
                st.get("temp_c"), st.get("rh"), st.get("slots")))

    def cmd_load(self, gcmd):
        slot = self._slot_param(gcmd)
        try:
            dry = bool(gcmd.get_int("DRY_RUN", 0))
        except Exception:
            dry = False
        try:
            ctl = self._ensure_controller()
        except Exception as exc:
            gcmd.respond_info("[k2_box] kein Bus: %s" % exc)
            return
        if dry or not self.cfg.get("allow_write"):
            ctl2 = ctl
            try:
                res = ctl2.do_load(slot, dry_run=True)
                gcmd.respond_info("[k2_box] Trockenlauf (nichts gesendet): %s"
                                  % (res.get("plan"),))
            except Exception as exc:
                gcmd.respond_info("[k2_box] %s" % exc)
            if not dry:
                gcmd.respond_info("[k2_box] ALLOW_WRITE=0 (allow_write=False): "
                                  "nur gelesen, nichts geschrieben.")
            return
        try:
            res = ctl.do_load(slot)
        except CfsStatusError as exc:
            msg = ("[k2_box] Load SLOT=%s abgebrochen: Box-Status 0x%02x (%s)" % (
                slot, exc.status, exc.name))
            gcmd.respond_info(msg)
            if self.cfg.get("shutdown_on_failure"):
                raise gcmd.error(msg) if hasattr(gcmd, "error") else RuntimeError(msg)
            return
        except (ValueError, PermissionError) as exc:
            gcmd.respond_info("[k2_box] %s" % exc)
            return
        gcmd.respond_info("[k2_box] Load SLOT=%s ok (Odometer %s mm)" % (
            res.get("slot"), res.get("odo_mm")))

    def cmd_unload(self, gcmd):
        slot = self._slot_param(gcmd)
        try:
            dry = bool(gcmd.get_int("DRY_RUN", 0))
        except Exception:
            dry = False
        try:
            ctl = self._ensure_controller()
        except Exception as exc:
            gcmd.respond_info("[k2_box] kein Bus: %s" % exc)
            return
        if dry or not self.cfg.get("allow_write"):
            try:
                res = ctl.do_unload(slot, dry_run=True)
                gcmd.respond_info("[k2_box] Trockenlauf (nichts gesendet): %s"
                                  % (res.get("plan"),))
            except Exception as exc:
                gcmd.respond_info("[k2_box] %s" % exc)
            if not dry:
                gcmd.respond_info("[k2_box] ALLOW_WRITE=0 (allow_write=False): "
                                  "nur gelesen, nichts geschrieben.")
            return
        try:
            res = ctl.do_unload(slot)
        except CfsStatusError as exc:
            msg = ("[k2_box] Unload SLOT=%s abgebrochen: Box-Status 0x%02x (%s)"
                   % (slot, exc.status, exc.name))
            gcmd.respond_info(msg)
            if self.cfg.get("shutdown_on_failure"):
                raise gcmd.error(msg) if hasattr(gcmd, "error") else RuntimeError(msg)
            return
        except (ValueError, PermissionError) as exc:
            gcmd.respond_info("[k2_box] %s" % exc)
            return
        gcmd.respond_info("[k2_box] Unload SLOT=%s ok" % (res.get("slot"),))

    def cmd_preload(self, gcmd):
        slot = self._slot_param(gcmd)
        try:
            op = gcmd.get_int("OP", None)
        except Exception:
            op = None
        if op is None:
            try:
                op = gcmd.get("OP", None)
            except Exception:
                op = None
        try:
            dry = bool(gcmd.get_int("DRY_RUN", 0))
        except Exception:
            dry = False
        try:
            ctl = self._ensure_controller()
        except Exception as exc:
            gcmd.respond_info("[k2_box] kein Bus: %s" % exc)
            return
        if dry or not self.cfg.get("allow_write"):
            try:
                res = ctl.do_preload(slot, op if op is not None else 1,
                                     dry_run=True)
                gcmd.respond_info("[k2_box] Trockenlauf (nichts gesendet): %s"
                                  % (res.get("plan"),))
            except Exception as exc:
                gcmd.respond_info("[k2_box] %s" % exc)
            if not dry:
                gcmd.respond_info("[k2_box] ALLOW_WRITE=0 (allow_write=False): "
                                  "nur gelesen, nichts geschrieben.")
            return
        try:
            res = ctl.do_preload(slot, op)
        except CfsStatusError as exc:
            gcmd.respond_info("[k2_box] Preload abgebrochen: Box-Status 0x%02x "
                              "(%s)" % (exc.status, exc.name))
            return
        except (ValueError, PermissionError) as exc:
            gcmd.respond_info("[k2_box] %s" % exc)
            return
        gcmd.respond_info("[k2_box] Preload SLOT=%s OP=%s ok" % (
            res.get("slot"), res.get("op")))


def load_config(config):
    """Klipper-Einstieg [k2_box] (Upstream-Muster)."""
    return PrinterK2Box(config)
