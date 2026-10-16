#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_k2_box.py – Tests für k2_box (ohne Klipper, ohne Hardware).

Läuft mit ``python3 test_k2_box.py`` (nachbau/ per sys.path, Pfadsuche wie
in pi-host/k2_transport.py). Prüft:

1. Kompletter Load-Ablauf (Slot B, Erfolg).
2. Fehlerabbruch mit Meldung (Slot A, 0x0c bei ~245 mm wie Kanal-A-Defekt).
3. Unload (Slot B, Retrude-Folge).
4. allow_write=False sendet keine mechanischen Kommandos (nur lesen + Plan).
5. Frame-Folge == Live-Sequenzen aus cfsseq (EXTRUDE/RETRUDE_LIVE_CMDS).
6. Gemeinsamer Lock mit Motor-Transport (keine Überlappung).
7. Lesende Abfragen + 0x0a-Poll (retries=0) + Moonraker-get_status.
8. Preload OPs + Auto-Addr nach k2_motor:ready + Guards (nie 0x50/0x06,
   keine Bursts, Cut-Platzhalter).
9. G-Codes KTWO_BOX_STATUS/LOAD/UNLOAD/PRELOAD via FakePrinter.

Fakes:
- FakeBoxBus: simuliert Box-Zustände (IDLE/PRELOAD/PRINT/ERROR), Hub-Maske,
  Odometer-Fortschritt (+50 mm je mode-5-Poll), Fehlerfall 0x0c bei ~245 mm
  für Slot A (alter Kanal-A-Defekt, Beleg CHECKLISTE.md + PROTOKOLL-485.md
  §6.8: 25x20 ms <0,01 mm → 0x0c), RFID/Remain/Version/HW-Status, Auto-Addr
  0xA0-0xA3. Zeichnet alle Calls + Intervalle (Lock-Test) auf.
- FakeMotorBus: minimaler Motor-Transport mit gleichem Lock (für Test 6).
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

import k2_box as K

try:
    import box as BOX
    _HAVE_BOX = True
except Exception as _e:
    BOX = None
    _HAVE_BOX = False
    print("WARNUNG: nachbau/box.py nicht importierbar: %s" % _e)

try:
    import cfsseq as SEQ
    _HAVE_SEQ = True
except Exception as _e:
    SEQ = None
    _HAVE_SEQ = False
    print("WARNUNG: nachbau/cfsseq.py nicht importierbar: %s" % _e)

try:
    from serial485 import build_frame as REF_BUILD
    _HAVE_S485 = True
except Exception:
    REF_BUILD = None
    _HAVE_S485 = False

UID12 = [0xB0, 0x5D, 0x3B, 0x8A, 0x0C, 0x93, 0x30, 0x10, 0x4C, 0x37, 0x36, 0x31]
SN19 = b"10000000000X000TEST"  # 19 Zeichen wie echte SN (Beleg box-Selbsttest)


# =====================================================================
# FakeBoxBus
# =====================================================================

class FakeBoxBus:
    """Simuliert CFS-Box 0x01 (BoxAction-kompatibel: transact_full)."""

    def __init__(self, lock=None, fail_after_mm=None, odo_step=50.0,
                 temp_c=22, rh=46, uid=None, addr=0x01):
        self._lock = lock if lock is not None else threading.Lock()
        self.fail_after_mm = fail_after_mm  # z.B. 245.0 nur für Slot A (0x01)
        self.odo_step = float(odo_step)
        self.odo = 0.0
        self.temp_c = int(temp_c)
        self.rh = int(rh)
        self.uid = list(uid if uid is not None else UID12)
        self.box_addr = int(addr)
        self.state = 0  # IDLE
        self.hub_mask = 0x00
        self.stored_error = 0x00
        self.feeding = False
        self.inhibit = 0
        self.preload_enable = 1
        # Slots: alle belegt mit RFID-Tag (B für Erfolg, A für Fehler – beide
        # mit Filament, damit mode 0 den Hub erreicht und erst das Odometer
        # stoppt; Beleg BEFUND2 §8 Folgefehler 3).
        self.slots = {}
        for i, letter in enumerate("ABCD"):
            self.slots[letter] = {"filament": True,
                                  "rfid": "TAG%s1234567890ABCDEF12345678" % letter,
                                  "remain": [28, 29, 30, 31][i]}
        self.calls = []  # [(addr, fun, tuple(data), kwargs)]
        self.intervals = []  # [(t0, t1)] für Lock-Test

    # -- Bus-API (BoxAction-kompatibel) ---------------------------------
    def transact_full(self, addr, fun, data=(), timeout=None, retries=0,
                      status=0xFF, expect_fun=None, expect_addr=None, **kw):
        if fun in K.FORBIDDEN_FUNS:
            raise AssertionError("FakeBox: verbotenes fun 0x%02x gesendet!" % fun)
        payload = list(data)
        with self._lock:
            t0 = time.monotonic()
            time.sleep(0.015)  # Arbeit simulieren (Lock-Test braucht Dauer)
            self.calls.append((addr, fun, tuple(payload),
                               dict(timeout=timeout, retries=retries,
                                    status=status)))
            raddr, rstatus, rfun, rdata = self._handle(addr, fun, payload)
            t1 = time.monotonic()
            self.intervals.append((t0, t1))
        if _HAVE_S485 and REF_BUILD is not None:
            rraw = bytes(REF_BUILD(raddr, rfun, rdata, status=rstatus))
        else:  # pragma: no cover
            rraw = b""
        return raddr, rstatus, rfun, bytes(rdata), rraw

    def transact(self, addr, fun, data=(), timeout=None, retries=0,
                 status=0xFF, **kw):
        _a, _st, _f, d, _r = self.transact_full(
            addr, fun, data, timeout=timeout, retries=retries, status=status)
        return d

    # -- Box-Verhalten ----------------------------------------------------
    def _handle(self, addr, fun, payload):
        A = self.box_addr
        if fun == 0xA1:  # Discovery an 0xFE
            return 0xFE, 0x00, 0xA1, bytes([0x01, 0x00] + self.uid)
        if fun == 0xA0:  # Adressvergabe [neu]+UID
            if len(payload) >= 13 and payload[1:] == self.uid:
                self.box_addr = int(payload[0])
                A = self.box_addr
            return A, 0x00, 0xA0, bytes([0x01, 0x00] + self.uid)
        if fun in (0xA2, 0xA3):  # Online-Check / Addr-Tabelle
            return A, 0x00, fun, bytes([0x01, 0x00] + self.uid)
        if fun == 0x0B:  # Loader-Check (keine Antwort, Beleg auto_addr)
            raise TimeoutError("0x0B bleibt unbeantwortet")
        if addr not in (A, 0xFE, 0xEA, 0xEB) and addr != A:
            raise TimeoutError("FakeBox: falsche Adresse 0x%02x" % addr)
        if fun == 0x0A:  # Status-Poll (6 B, Beleg GHIDRA §5 / BEFUND2 §1)
            t = self.temp_c & 0xFF
            data = bytes([t, self.rh & 0xFF, 0x00, self.state,
                          self.hub_mask & 0xFF, 0x00])
            st = self.stored_error if self.state == 4 else 0x00
            return A, st, 0x0A, data
        if fun == 0x02:  # RFID [Maske] -> ASCII
            mask = payload[0] if payload else 0x0F
            out = b""
            for i, letter in enumerate("ABCD"):
                if mask & (1 << i):
                    slot = self.slots[letter]
                    tag = slot["rfid"] if slot["filament"] else "unknown"
                    if tag is None:
                        tag = "unknown"
                    out += ("%s:%s;" % (letter, tag)).encode("ascii")
            return A, 0x00, 0x02, out
        if fun == 0x03:  # Remain [Maske] -> 4 B positional
            mask = payload[0] if payload else 0x0F
            out = []
            for i, letter in enumerate("ABCD"):
                if not (mask & (1 << i)):
                    out.append(0xFF)
                    continue
                slot = self.slots[letter]
                if not slot["filament"]:
                    out.append(0x00)
                elif slot["rfid"] is None:
                    out.append(0xFF)
                else:
                    out.append(int(slot["remain"]))
            return A, 0x00, 0x03, bytes(out)
        if fun == 0x04:  # Box-Mode [mask, mode]
            mask, mode = payload[0], payload[1]
            if mode == 1 and mask == 0x00:  # IDLE (löscht auch ERROR, live)
                self.state, self.stored_error, self.feeding = 0, 0x00, False
                return A, 0x00, 0x04, b""
            if mode == 0:
                if mask == 0:
                    return A, 0x01, 0x04, b""
                self.state = 2  # PRINT
                return A, 0x00, 0x04, b""
            return A, 0x01, 0x04, b""
        if fun == 0x0F:  # Inhibit [x]
            x = payload[0] if payload else 0xFF
            if x in (0, 1):
                self.inhibit = x
                return A, 0x00, 0x0F, b""
            return A, 0x01, 0x0F, b""
        if fun == 0x0D:  # Preload [mask, op]
            _mask, op = payload[0], payload[1]
            if op in (0, 1):
                self.preload_enable = op
                return A, 0x00, 0x0D, b""
            if op in (2, 3):
                self.state = 0
                return A, 0x00, 0x0D, b""
            return A, 0x01, 0x0D, b""
        if fun == 0x10:  # Extrude [mask, mode, p]
            mask, mode, p = payload[0], payload[1], payload[2]
            if mode == 0:  # Stage1: Hub erreichen (Sensor schon aktiv ok)
                if mask == 0 or mask not in (0x01, 0x02, 0x04, 0x08):
                    return A, 0x01, 0x10, b""
                letters = [l for i, l in enumerate("ABCD") if mask == (1 << i)]
                if not letters or not self.slots[letters[0]]["filament"]:
                    self.state, self.stored_error = 4, 0x16
                    return A, 0x16, 0x10, bytes([4])
                self.hub_mask |= mask
                self.state = 3  # RELOAD während Förderung
                return A, 0x00, 0x10, bytes([self.state])
            if mode == 4:  # Dauerfördern starten (sofort ok)
                self.feeding = True
                return A, 0x00, 0x10, b""
            if mode == 5:  # Odometer-Abfrage (Fehler aus mode 4)
                if mask == 0x01 and self.fail_after_mm is not None:
                    if self.odo >= self.fail_after_mm:
                        self.state, self.stored_error = 4, 0x0C
                        self.feeding = False
                        return A, 0x0C, 0x10, struct.pack(">f", -self.odo)
                    if self.odo + self.odo_step >= self.fail_after_mm:
                        # Nächster Schritt würde die Schwelle reißen → jetzt
                        # Stillstand melden (Odometer UNVERÄNDERT, live).
                        self.state, self.stored_error = 4, 0x0C
                        self.feeding = False
                        return A, 0x0C, 0x10, struct.pack(">f", -self.odo)
                self.odo += self.odo_step
                return A, 0x00, 0x10, struct.pack(">f", -self.odo)
            if mode == 6:  # Stopp/Buffer
                self.feeding = False
                return A, 0x00, 0x10, b""
            if mode == 7:  # Extruder-Check p mm
                return A, 0x00, 0x10, b""
            if mode == 3:  # Fehler löschen
                self.state, self.stored_error = 0, 0x00
                return A, 0x00, 0x10, b""
            return A, 0x01, 0x10, b""
        if fun == 0x11:  # Retrude [mask, p]
            mask, p = payload[0], payload[1]
            if p not in (0, 1):
                return A, 0x01, 0x11, b""
            if mask == 0:
                self.hub_mask = 0x00
            else:
                self.hub_mask &= (~mask) & 0xFF
            self.state = 0
            return A, 0x00, 0x11, b""
        if fun == 0x05:  # Buffer
            return A, 0x00, 0x05, b"\x00"
        if fun == 0x08:  # Sensor [pos]
            pos = payload[0] if payload else 0
            if pos == 0:
                m = 0
                for i, letter in enumerate("ABCD"):
                    if self.slots[letter]["filament"]:
                        m |= (1 << i)
                return A, 0x00, 0x08, bytes([m])
            return A, 0x00, 0x08, bytes([self.hub_mask & 0xFF])
        if fun == 0x0E:  # Messrad [op]
            op = payload[0] if payload else 1
            if op == 1:
                return A, 0x00, 0x0E, struct.pack(">f", -self.odo)
            self.odo = 0.0  # op 0 nullen (nur mit reset=True aufgerufen)
            return A, 0x00, 0x0E, b""
        if fun == 0x14:  # Version/SN
            return A, 0x00, 0x14, b"153" + SN19
        if fun == 0x15:  # HW-Status (alle ok)
            return A, 0x00, 0x15, bytes([0x10, 0x20, 0x30, 0x40, 0x50, 0x60]
                                        + [0] * 10)
        raise AssertionError("FakeBox unerwartet: fun 0x%02x %r" % (fun, payload))


class FakeMotorBus:
    """Minimaler Motor-Transport (nur für Lock-Test, gleiche Lock-Instanz)."""

    def __init__(self, lock):
        self._lock = lock
        self.calls = []
        self.intervals = []

    def transact(self, addr, fun, data, timeout=1.0, retries=0):
        payload = list(data)
        with self._lock:
            t0 = time.monotonic()
            time.sleep(0.015)
            self.calls.append((addr, fun, tuple(payload)))
            t1 = time.monotonic()
            self.intervals.append((t0, t1))
        return 0x00, fun, bytes(payload)


# =====================================================================
# FakePrinter (Klipper-Hülle, minimal)
# =====================================================================

class FakeReactor:
    NOW = 0

    def __init__(self):
        self.timers = []
        self.pauses = []

    def pause(self, s):
        self.pauses.append(float(s))

    def register_timer(self, cb, when=0):
        self.timers.append(cb)
        return cb


class FakeGcode:
    def __init__(self):
        self.infos = []
        self.commands = {}

    def respond_info(self, msg):
        self.infos.append(str(msg))

    def register_command(self, name, func, desc=None):
        if not upstream_gcode_name_ok(name):
            raise RuntimeError("Can't register '%s' as it is an invalid name" % name)
        self.commands[name] = func


def upstream_gcode_name_ok(cmd):
    """Nachbau der Namensprüfung aus Upstream gcode.py register_command
    (Buchstabe+Ziffer am Anfang ist ungültig, z. B. K2_…)."""
    try:
        c = cmd.upper().split()[0]
        float(c[1:])
        if c[0].isupper() and c[1].isdigit():
            return True
    except Exception:
        pass
    if (cmd.upper() != cmd or not cmd.replace('_', 'A').isalnum()
            or cmd[0].isdigit() or cmd[1:2].isdigit()):
        return False
    # Upstream-Parser (gcode.py _process_commands, args_r '([A-Z_]+|[A-Z*])')
    # zerlegt den Befehl: Ziffern im Namen kappen ihn (KTWO_485_X -> KTWO_485)
    # -> "Unknown command" trotz erfolgreicher Registrierung.
    import re as _re
    parts = _re.split('([A-Z_]+|[A-Z*])', cmd.upper())
    return ''.join(parts[:3]).strip() == cmd


class FakePrinter:
    def __init__(self, bus):
        self.reactor = FakeReactor()
        self.gcode = FakeGcode()
        self.bus = bus
        self.objects = {"gcode": self.gcode, "k2_485": bus}
        self.handlers = {}
        self.events = []

    def lookup_object(self, name):
        try:
            return self.objects[name]
        except KeyError:
            raise RuntimeError("Objekt %r fehlt" % (name,))

    def get_reactor(self):
        return self.reactor

    def register_event_handler(self, ev, cb):
        self.handlers.setdefault(ev, []).append(cb)

    def send_event(self, ev):
        self.events.append(ev)
        for cb in self.handlers.get(ev, []):
            cb()

    def fire(self, ev):
        for cb in self.handlers.get(ev, []):
            cb()


class FakeConfig:
    def __init__(self, printer, opts):
        self.printer = printer
        self._opts = dict(opts)

    def get_printer(self):
        return self.printer

    def get_options(self):
        return list(self._opts.keys())

    def get(self, name, default=None):
        return self._opts.get(name, default)

    def error(self, msg):
        raise ValueError(msg)


class FakeGcmd:
    def __init__(self, params=None):
        self.params = dict(params or {})
        self.infos = []

    def get(self, name, default=None):
        return self.params.get(name, self.params.get(name.lower(), default))

    def get_int(self, name, default=None):
        v = self.get(name, default)
        if v is None:
            return default
        return int(v)

    def respond_info(self, msg):
        self.infos.append(str(msg))

    def error(self, msg):
        raise ValueError(msg)


# =====================================================================
# Helfer
# =====================================================================

def test_options(**over):
    o = {"box_addr": "1", "allow_write": "True",
         "shutdown_on_failure": "False", "auto_init": "True",
         "poll_interval": "4.0"}
    o.update(over)
    return o


def build_system(opts=None, **bus_kw):
    opts = dict(opts if opts is not None else test_options())
    cfg = K.parse_options(opts)
    bus = FakeBoxBus(**bus_kw)
    ctl = K.K2BoxController(cfg, bus, pause=lambda s: None)
    return cfg, bus, ctl


def request_frames(bus):
    """Calls -> Vollframes (Status 0xFF, wie BoxAction sendet)."""
    if not _HAVE_S485:
        raise AssertionError("serial485 fehlt für Frame-Vergleich")
    return [bytes(REF_BUILD(a, f, list(d), status=0xFF))
            for a, f, d, _k in bus.calls]


# =====================================================================
# Einzeltests
# =====================================================================

def test_load_ok():
    cfg, bus, ctl = build_system()
    res = ctl.do_load("B")
    assert res["status"] == "ok" and res["slot"] == "B", res
    assert len(res["odo_mm"]) == 5, res["odo_mm"]
    assert res["odo_mm"][-1] > res["odo_mm"][0] > 0, res["odo_mm"]
    poll = ctl.poll_status()
    assert poll["hub_mask"] & 0x02, poll
    return "Load B ok, Odometer %s mm, Hub B belegt" % (
        round(res["odo_mm"][-1], 1),)


def _test_load_fehler_0c_impl():
    cfg, bus, ctl = build_system()
    bus.fail_after_mm = 245.0  # Kanal-A-Defekt (Beleg CHECKLISTE Kanal A)
    try:
        ctl.do_load("A")
    except BOX.CfsStatusError as exc:
        assert exc.status == 0x0C, "Status 0x%02x statt 0x0c" % exc.status
        assert "Stillstand" in exc.name or "0x0c" in str(exc).lower() \
            or "stillstand" in str(exc).lower(), exc.name
        poll = ctl.poll_status()
        assert poll["state_name"] == "STATE_ERROR", poll
        assert poll.get("error_code") == 0x0C, poll
        return "Abbruch 0x0c mit Meldung '%s' (Odometer %.0f mm)" % (
            exc.name, ctl.bus.odo)
    raise AssertionError("Load A hätte mit 0x0c abbrechen müssen")


def test_unload_ok():
    cfg, bus, ctl = build_system()
    ctl.do_load("B")
    assert bus.hub_mask & 0x02
    res = ctl.do_unload("B")
    assert res["status"] == "ok", res
    assert bus.hub_mask & 0x02 == 0, "Hub B müsste frei sein"
    return "Unload B ok, Hub frei"


def test_allow_write_false():
    opts = test_options(allow_write="False")
    cfg, bus, ctl = build_system(opts=opts)
    assert "allow_write=False" in ctl.describe_plan()
    for call, label in ((lambda: ctl.do_load("B"), "Load"),
                        (lambda: ctl.do_unload("B"), "Unload"),
                        (lambda: ctl.do_preload("B", 2), "Preload op2"),
                        (lambda: ctl.do_extrude("B"), "Extrude"),
                        (lambda: ctl.do_retrude("B"), "Retrude")):
        try:
            call()
        except PermissionError:
            pass
        else:
            raise AssertionError("%s hätte PermissionError werfen müssen" % label)
    mech = [c for c in bus.calls if c[1] in (0x0D, 0x04, 0x0F, 0x10, 0x11, 0xA0)]
    assert mech == [], "mechanische Frames trotz allow_write=False: %s" % (mech,)
    # Lesendes geht trotzdem (Poll + RFID + Remain + Version):
    poll = ctl.poll_status()
    assert poll["kind"] == "state", poll
    assert ctl.get_rfid("B") is not None
    assert isinstance(ctl.get_remain(), dict)
    assert ctl.get_version_sn()[0] == "153"
    try:
        ctl.do_cut("B")
    except NotImplementedError:
        pass
    else:
        raise AssertionError("Cut-Platzhalter müsste NotImplementedError werfen")
    assert all(c[1] != 0x50 and c[1] != 0x06 for c in bus.calls), "verbotenes fun!"
    return "keine mechanischen Frames (%d nur-lesend), Cut-Platzhalter ok" % (
        len(bus.calls),)


def test_frame_folge_live():
    if not (_HAVE_SEQ and _HAVE_S485 and _HAVE_BOX):
        return "SKIP (nachbau fehlt)"
    # Extrude Slot B (Maske 0x02, pushes=5) == EXTRUDE_LIVE_CMDS_B
    cfg, bus, ctl = build_system()
    ctl.do_load("B")
    got = request_frames(bus)
    exp = SEQ.build_extrude_sequence(0x02, pushes=5)
    assert got == exp, "Extrude-Folge weicht ab:\n got %d\n exp %d" % (len(got), len(exp))
    live = SEQ.requests_from_log_cmds(SEQ.EXTRUDE_LIVE_CMDS_B)
    assert got == live, "Extrude != EXTRUDE_LIVE_CMDS_B"
    # Retrude Slot A (Maske 0x01, 4x p0 + p1) == RETRUDE_LIVE_CMDS_A (gefiltert)
    cfg2, bus2, ctl2 = build_system()
    ctl2.do_unload("A")
    got2 = request_frames(bus2)
    exp2 = SEQ.build_retrude_sequence(0x01)
    assert got2 == exp2, "Retrude-Folge weicht ab"
    live2 = SEQ.requests_from_log_cmds(SEQ.RETRUDE_LIVE_CMDS_A)
    live2f = SEQ.filter_funs(live2, (0x04, 0x08, 0x11))
    assert got2 == live2f, "Retrude != RETRUDE_LIVE_CMDS_A (gefiltert 04/08/11)"
    return "Extrude %d + Retrude %d Frames live-identisch" % (len(got), len(got2))


def test_shared_lock():
    lock = threading.Lock()
    box_bus = FakeBoxBus(lock=lock)
    mot_bus = FakeMotorBus(lock)
    cfg = K.parse_options(test_options())
    ctl = K.K2BoxController(cfg, box_bus, pause=lambda s: None)
    errors = []

    def _box():
        try:
            for _ in range(4):
                box_bus.transact_full(0x01, 0x0A, [], timeout=0.5, retries=0)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def _mot():
        try:
            for _ in range(4):
                mot_bus.transact(0x81, 0x0C, [0x0B], timeout=0.5, retries=0)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=_box), threading.Thread(target=_mot)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5.0)
    assert not any(t.is_alive() for t in threads), "Threads hängen (Deadlock?)"
    assert not errors, errors
    spans = sorted(box_bus.intervals + mot_bus.intervals)
    for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
        assert not (b0 < a1 - 1e-9 and a0 < b1 - 1e-9), \
            "Überlappung trotz gemeinsamem Lock: %s vs %s" % ((a0, a1), (b0, b1))
    return "8 Transaktionen (4 Box + 4 Motor) ohne Überlappung (1 Lock)"


def test_lesen_poll_status():
    opts = test_options(allow_write="False")  # lesend geht auch ohne Write
    cfg, bus, ctl = build_system(opts=opts)
    poll = ctl.poll_status()
    call0 = bus.calls[0]
    assert call0[1] == 0x0A and call0[3].get("retries") == 0, \
        "0x0a-Poll muss retries=0 haben (Original): %r" % (call0,)
    assert poll["kind"] == "state" and poll["temp_c"] == 22 \
        and poll["rh"] == 46, poll
    rfid = ctl.get_rfid("B")
    assert rfid is not None and "TAGB" in rfid, rfid
    remain = ctl.get_remain()
    assert remain["B"] == 29, remain
    ver, sn = ctl.get_version_sn()
    assert ver == "153" and sn == SN19.decode("ascii"), (ver, sn)
    hw = ctl.get_hardware_status()
    assert hw["all_ok"] is True, hw
    assert ctl.get_sensor(0) & 0x0F == 0x0F
    assert ctl.get_buffer() == 0
    st = ctl.get_status()
    assert st["state_name"] == "STATE_IDLE" and st["hub_mask"] == 0 \
        and st["temp_c"] == 22 and st["rh"] == 46 \
        and st["slots"]["B"]["remain"] == 29, st
    return "Poll retries=0, Temp 22 °C/46 %, RFID/Remain/Version/HW ok"


def test_preload_addr_guards():
    cfg, bus, ctl = build_system()
    # Preload OPs (alle 0-3 sendbar, op 2/3 mit langem Timeout + retries=0)
    for op in (0, 1, 2, 3):
        n = len(bus.calls)
        ctl.do_preload("C", op)
        _a, _f, data, kw = bus.calls[n]
        assert tuple(data) == (0x04, op), data
        if op in (2, 3):
            assert kw.get("retries") == 0, kw
            assert kw.get("timeout") >= 380.0, kw
        else:
            assert kw.get("retries") == 4, kw  # wie Original (Beleg §6.2)
    # Mechanik-Retries: 0x10/0x11 immer 0 (keine Bursts, Beleg BEFUND2 §5)
    bus.calls.clear()
    ctl.do_load("D")
    for _a, f, _d, kw in bus.calls:
        if f in (0x10, 0x11):
            assert kw.get("retries") == 0, (f, kw)
    # Adressvergabe erst nach k2_motor:ready
    cfg2, bus2, ctl2 = build_system()
    n0 = len(bus2.calls)
    res = ctl2.ensure_addr(motor_ready=False)
    assert res["status"] == "wait_motor" and len(bus2.calls) == n0, res
    res = ctl2.on_motor_ready()
    assert res["status"] == "ready" and ctl2.is_ready, res
    funs = {c[1] for c in bus2.calls}
    assert 0xA1 in funs and 0xA2 in funs, funs
    # Guards: SLOT/OP-Fehler senden NICHTS
    n1 = len(bus.calls)
    for bad_call in (lambda: ctl.do_load("E"), lambda: ctl.do_load(9),
                     lambda: ctl.do_preload("B", 4),
                     lambda: K.check_not_forbidden(0x50),
                     lambda: K.check_not_forbidden(0x06)):
        try:
            bad_call()
        except (ValueError, PermissionError):
            pass
        else:
            raise AssertionError("Guard hätte werfen müssen: %r" % (bad_call,))
    assert len(bus.calls) == n1, "Guards haben gesendet!"
    return "Preload 0-3 (Timeouts/Retries ok), Auto-Addr nach ready, Guards ok"


def test_gcodes():
    bus = FakeBoxBus()
    printer = FakePrinter(bus)
    cfg = FakeConfig(printer, {"box_addr": "1", "allow_write": "True",
                               "shutdown_on_failure": "False",
                               "auto_init": "True", "poll_interval": "4.0"})
    mod = K.PrinterK2Box(cfg)
    printer.fire("klippy:connect")
    mod._bus = bus  # ANNAHME-Bus injizieren (Test)
    mod.handle_motor_ready()
    assert mod.controller is not None and mod.controller.is_ready
    assert "k2_box:ready" in printer.events
    g = FakeGcmd({})
    mod.cmd_status(g)
    assert any("Zustand" in m for m in g.infos), g.infos
    g2 = FakeGcmd({"SLOT": "B"})
    mod.cmd_load(g2)
    assert any("ok" in m for m in g2.infos), g2.infos
    g3 = FakeGcmd({"SLOT": "B"})
    mod.cmd_unload(g3)
    assert any("ok" in m for m in g3.infos), g3.infos
    g4 = FakeGcmd({"SLOT": "C", "OP": "1"})
    mod.cmd_preload(g4)
    assert any("ok" in m for m in g4.infos), g4.infos
    g5 = FakeGcmd({"SLOT": "B", "DRY_RUN": "1"})
    n = len(bus.calls)
    mod.cmd_load(g5)
    assert len(bus.calls) == n and any("Trockenlauf" in m for m in g5.infos)
    st = mod.get_status()
    assert "hub_mask" in st and "slots" in st, st
    # Bus da, Port aber weg ([k2_485] ohne Adapter): G-Code-Fehler statt
    # "Internal error" + Shutdown (Batch-Probe G 16.10.2026).
    class DeadBus:
        def transact_full(self, *a, **k):
            raise RuntimeError("[k2_485] Port /dev/x nicht zu öffnen (Test)")
        transact = transact_full
    mod.controller, mod._bus = None, DeadBus()  # Controller neu mit totem Bus
    for fn, params in ((mod.cmd_load, {"SLOT": "B"}), (mod.cmd_unload, {"SLOT": "B"}),
                       (mod.cmd_preload, {"SLOT": "C", "OP": "1"})):
        try:
            fn(FakeGcmd(params))
        except ValueError as exc:  # FakeGcmd.error
            assert "RS485-Bus-Fehler" in str(exc), exc
        else:
            raise AssertionError("%s: Bus-Fehler ohne G-Code-Fehler" % fn.__name__)
    return ("STATUS/LOAD/UNLOAD/PRELOAD + DRY_RUN + get_status ok, Bus-Fehler -> "
            "G-Code-Fehler")


# =====================================================================
# Runner
# =====================================================================

TESTS = [
    ("kompletter Load-Ablauf (B)", test_load_ok),
    ("Fehlerabbruch 0x0c mit Meldung (A ~245 mm)",
     _test_load_fehler_0c_impl),
    ("Unload (B)", test_unload_ok),
    ("allow_write=False schreibt nichts", test_allow_write_false),
    ("Frame-Folge == Live-Sequenzen", test_frame_folge_live),
    ("gemeinsamer Lock (keine Überlappung)", test_shared_lock),
    ("Lesen + Poll retries=0 + Moonraker-Status", test_lesen_poll_status),
    ("Preload/Addr/Guards (nie 0x50/0x06, keine Bursts)",
     test_preload_addr_guards),
    ("G-Codes via FakePrinter", test_gcodes),
]


def main():
    fails, total = 0, 0
    print("test_k2_box – %d Prüfungen (ohne Klipper, ohne Hardware)"
          % len(TESTS))
    for name, fn in TESTS:
        total += 1
        try:
            detail = fn()
        except AssertionError as exc:
            fails += 1
            print("FAIL %s: %s" % (name, exc))
        except Exception as exc:  # pragma: no cover
            fails += 1
            print("FAIL %s: %s: %s" % (name, type(exc).__name__, exc))
        else:
            print("ok %s – %s" % (name, detail))
    print("ERGEBNIS: %s (%d/%d ok)"
          % ("ok" if fails == 0 else "%d FEHLER" % fails, total - fails, total))
    return fails


if __name__ == "__main__":
    raise SystemExit(main())
