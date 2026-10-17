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
Filamentwechsel (Ergänzung 16.10.2026, Belege README-k2_box.md §8):
10. Bausteine (Schnitt/Schacht/Abstreifen/Spülen/Rückzug) == box.cfg + Live,
    TNN-Mapping, Optionsgrenzen, Upstream-Namensregel.
11. Einzug bis Kopfsensor + Stage 7 (0x0e ohne Extruder, Timeout -> IDLE).
12. Rückzug mit Extruder-Haken je p0 (Frames == cfsseq).
13. Box-Zustand abwarten (PRELOAD/ERROR/Timeout).
14. Volle Kette T1B über die Klipper-Hülle (Reihenfolge Bus + G-Code).
15. Sperren (allow_write/allow_move/Druck ohne Freigabe -> Pause).
16. Fehler im Druck -> Pause, Quittung, RESUME_FIX.
17. Fehler ohne Druck -> G-Code-Fehler; Box 2/Hub belegt/kein Bus.
18. ReactorBusProxy.  19. Makros T0-T15/M8200/RESUME (Jinja2).
20.-24. Auto-Nachladen bei Runout (A7): Slot-Wahl, Ablauf, Pausenpfade,
    Befehle AUTO_REFILL/SAME_MATERIAL, Config (printer.cfg/k2-module.cfg).
25. Python-3.9-Syntax.

Fakes:
- FakeBoxBus: simuliert Box-Zustände (IDLE/PRELOAD/PRINT/ERROR), Hub-Maske,
  Odometer-Fortschritt (+50 mm je mode-5-Poll), Fehlerfall 0x0c bei ~245 mm
  für Slot A (alter Kanal-A-Defekt, Beleg CHECKLISTE.md + PROTOKOLL-485.md
  §6.8: 25x20 ms <0,01 mm → 0x0c), RFID/Remain/Version/HW-Status, Auto-Addr
  0xA0-0xA3. Zeichnet alle Calls + Intervalle (Lock-Test) auf.
- FakeMotorBus: minimaler Motor-Transport mit gleichem Lock (für Test 6).
"""

import math
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
import logging
logging.getLogger().setLevel(logging.CRITICAL)  # Fehlerpfad-Tests ohne Log-Rauschen

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

UID12 = [0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xAB]  # Platzhalter (Repo-Anonymisierung), Fake-Box
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
        # Wechsel-Simulation (Tests 10+): gemeinsamer Ereignis-Trace mit dem
        # FakeGcode, Kopfsensor (Filament am Kopf ab head_at_mm Odometer),
        # mode 7 nur ok, wenn der Extruder seit mode 6 gefördert hat.
        self.trace = None
        self.head = False
        self.head_at_mm = None
        self.need_extruder_for_mode7 = False
        self.extruder_mm = 0.0
        self.preload_polls = 0  # so viele 0x0a-Polls noch PRELOAD melden
        self.print_frame_status = 0x00  # 0x0a im PRINT: 0x50 = Runout (A7)

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
            if self.trace is not None:
                self.trace.append(("bus", fun, tuple(payload)))
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
            if self.preload_polls > 0:
                self.preload_polls -= 1
                data = bytes([t, self.rh & 0xFF, 0x00, 1,
                              self.hub_mask & 0xFF, 0x00])
                return A, 0x00, 0x0A, data
            data = bytes([t, self.rh & 0xFF, 0x00, self.state,
                          self.hub_mask & 0xFF, 0x00])
            st = self.stored_error if self.state == 4 else (
                self.print_frame_status if self.state == 2 else 0x00)
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
                if self.head_at_mm is not None and self.odo >= self.head_at_mm:
                    self.head = True
                return A, 0x00, 0x10, struct.pack(">f", -self.odo)
            if mode == 6:  # Stopp/Buffer
                self.feeding = False
                self.extruder_mm = 0.0
                return A, 0x00, 0x10, b""
            if mode == 7:  # Extruder-Check p mm (Odometer > p in 300 ms)
                if self.need_extruder_for_mode7 and self.extruder_mm <= 0:
                    self.state, self.stored_error = 4, 0x0E
                    return A, 0x0E, 0x10, b""
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
            if p == 1:
                self.head = False
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
    """Reactor mit virtueller Uhr: pause(waketime) springt dorthin (+1 ms
    echte Wartezeit, damit Hilfs-Threads des ReactorBusProxy laufen)."""
    NOW = 0

    def __init__(self):
        self.timers = []
        self.pauses = []
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def pause(self, waketime):
        self.pauses.append(float(waketime))
        time.sleep(0.001)
        self.now = max(self.now + 0.001, float(waketime))
        return self.now

    def register_timer(self, cb, when=0):
        self.timers.append(cb)
        return cb


class FakeGcode:
    def __init__(self):
        self.infos = []
        self.commands = {}
        self.scripts = []   # ausgeführte Zeilen (run_script_from_command)
        self.raw = []
        self.printer = None  # FakePrinter (für Zustands-Simulation)

    def respond_info(self, msg):
        self.infos.append(str(msg))

    def respond_raw(self, msg):
        self.raw.append(str(msg))

    def run_script_from_command(self, script):
        for line in script.split("\n"):
            line = line.strip()
            if not line:
                continue
            self.scripts.append(line)
            name = line.split()[0]
            if name in self.commands:  # z. B. KTWO_BOX_* aus KTWO_BOX_RUNOUT
                params = dict(t.split("=", 1) for t in line.split()[1:]
                              if "=" in t)
                g = FakeGcmd(params)
                try:
                    self.commands[name](g)
                finally:
                    self.infos.extend(g.infos)
                continue
            if self.printer is not None:
                self.printer.sim(line)

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


class _Obj:
    def __init__(self, status):
        self.status = status

    def get_status(self, eventtime=None):
        return dict(self.status)


class _SensorObj:
    def __init__(self, printer):
        self.printer = printer
        self.enabled = True

    def get_status(self, eventtime=None):
        bus = self.printer.bus
        return {"filament_detected": bool(getattr(bus, "head", False)),
                "enabled": self.enabled}


class FakePrinter:
    def __init__(self, bus, with_motion=False):
        self.reactor = FakeReactor()
        self.gcode = FakeGcode()
        self.gcode.printer = self
        self.bus = bus
        self.objects = {"gcode": self.gcode, "k2_485": bus}
        self.handlers = {}
        self.events = []
        # Drucker-Simulation für die Wechsel-Tests (Positionen in mm)
        self.pos = [200.0, 200.0, 20.0, 0.0]
        self.relative = False
        self.accel = 20000.0
        self.saved = {}
        self.fail_on = None  # Zeilenanfang -> gcode-Fehler simulieren
        if with_motion:
            self.print_state = "standby"
            self.paused = False
            self.target = 0.0
            self.sensor = _SensorObj(self)
            self.objects.update({
                "print_stats": _Obj({}), "pause_resume": _Obj({}),
                "toolhead": _Obj({}), "gcode_move": _Obj({}),
                "extruder": _Obj({}),
                "filament_switch_sensor filament_sensor": self.sensor})
            self._refresh()

    def _refresh(self):
        if "toolhead" not in self.objects:
            return
        self.objects["print_stats"].status = {"state": self.print_state}
        self.objects["pause_resume"].status = {"is_paused": self.paused}
        self.objects["toolhead"].status = {
            "homed_axes": "xyz", "max_accel": self.accel,
            "axis_maximum": [352.5, 400.0, 360.0, 0.0]}
        self.objects["gcode_move"].status = {"gcode_position": list(self.pos)}
        self.objects["extruder"].status = {"target": self.target}

    def sim(self, line):
        """Minimale G-Code-Simulation (nur was die Wechsel-Tests brauchen)."""
        if self.fail_on and line.startswith(self.fail_on):
            raise ValueError("Simulierter G-Code-Fehler bei %r" % line)
        if self.bus is not None and getattr(self.bus, "trace", None) is not None:
            self.bus.trace.append(("gcode", line))
        w = line.split()
        cmd = w[0]
        args = {}
        for tok in w[1:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                args[k] = v
            else:
                args[tok[0]] = tok[1:]
        if cmd == "G90":
            self.relative = False
        elif cmd == "G91":
            self.relative = True
        elif cmd in ("G0", "G1"):
            for i, ax in enumerate("XYZE"):
                if ax in args:
                    v = float(args[ax])
                    self.pos[i] = self.pos[i] + v if (self.relative or ax == "E") \
                        else v
                    if ax == "E" and v > 0 and self.bus is not None:
                        self.bus.extruder_mm += v
        elif cmd == "SET_VELOCITY_LIMIT" and "ACCEL" in args:
            self.accel = float(args["ACCEL"])
        elif cmd in ("M104", "M109"):
            self.target = float(args.get("S", 0))
        elif cmd == "SAVE_GCODE_STATE":
            self.saved[args["NAME"]] = self.relative
        elif cmd == "RESTORE_GCODE_STATE":
            self.relative = self.saved.get(args["NAME"], self.relative)
        elif cmd == "SET_FILAMENT_SENSOR":
            self.sensor.enabled = bool(int(args["ENABLE"]))
        elif cmd == "PAUSE":
            self.paused = True
            self.print_state = "paused"
        elif cmd == "RESUME":
            self.paused = False
            self.print_state = "printing"
        self._refresh()

    def set_printing(self, on=True):
        self.print_state = "printing" if on else "standby"
        self._refresh()

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

    def get_int(self, name, default=None, **kw):
        v = self.get(name, default)
        if v is None:
            return default
        return int(v)

    def get_float(self, name, default=None, **kw):
        v = self.get(name, default)
        if v is None:
            return default
        v = float(v)
        if "minval" in kw and v < kw["minval"]:
            raise ValueError("%s < minval" % name)
        if "maxval" in kw and v > kw["maxval"]:
            raise ValueError("%s > maxval" % name)
        if "above" in kw and v <= kw["above"]:
            raise ValueError("%s <= above" % name)
        return v

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
    # Cut = reine Kopfbewegung (16.10.2026): do_cut liefert nur den G-Code-
    # Plan und sendet KEIN Box-Frame (Beleg Live-Cut ohne Box-/Motor-Frame).
    n_cut = len(bus.calls)
    plan = ctl.do_cut("B")
    assert plan["status"] == "plan" and any("X-7.8" in l for l in plan["gcode"]), plan
    assert len(bus.calls) == n_cut, "do_cut hat gesendet!"
    assert all(c[1] != 0x50 and c[1] != 0x06 for c in bus.calls), "verbotenes fun!"
    return "keine mechanischen Frames (%d nur-lesend), Cut = Plan ohne Frame" % (
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
# Filamentwechsel (Ergänzung 16.10.2026) – Belege s. README-k2_box.md §8
# =====================================================================

class VClock:
    """Virtuelle Uhr für Controller-Tests (pause springt vor)."""

    def __init__(self):
        self.t = 0.0
        self.pauses = []

    def clock(self):
        return self.t

    def pause(self, s):
        self.pauses.append(float(s))
        self.t += float(s)


def build_vsystem(opts=None, **bus_kw):
    cfg, bus, ctl = build_system(opts, **bus_kw)
    vc = VClock()
    ctl.pause = vc.pause
    ctl.clock = vc.clock
    return cfg, bus, ctl, vc


def _frames(trace):
    return [(e[1], e[2]) for e in trace if e[0] == "bus"]


def test_wechsel_bausteine():
    c = K.parse_options(test_options())
    # Defaults = box.cfg + SAVE_CONFIG (cut_pos_x -7.80), Sperren aus
    assert c["cut_pos_x"] == -7.8 and c["pre_cut_pos_x"] == 10.0 \
        and c["pre_cut_pos_y"] == 200.0 and c["cut_pos_y"] == 200.0, c
    assert c["allow_move"] is False and c["pause_on_failure"] is True, c
    assert c["filament_sensor"] == "filament_sensor" and c["cut_sensor"] == ""
    # Stock-Schreibweise (Klipper schreibt Optionsnamen klein)
    c2 = K.parse_options(test_options(Tn_retrude="-12", cut_pos_x="-7.6"))
    assert c2["tn_retrude"] == -12.0 and c2["cut_pos_x"] == -7.6, c2
    for bad in ({"cut_pos_x": "-4.0"}, {"cut_pos_x": "-10"},
                {"tn_retrude": "10"}, {"cut_velocity": "0"},
                {"change_z_hop": "8"}, {"allow_move": "vielleicht"}):
        try:
            K.parse_options(test_options(**bad))
        except ValueError:
            pass
        else:
            raise AssertionError("Option hätte abgelehnt werden müssen: %r" % bad)
    # Schneidfahrt = T0-Beispiel Schritt 3-5 + CR_BOX_CUT (cut_pos_x -7.80)
    assert K.gcode_cut(c, 20000) == [
        "G90", "G0 X10 Y200 F12000", "SET_VELOCITY_LIMIT ACCEL=5000",
        "G0 X-7.8 Y200 F30000", "G4 P100", "G0 X10 F12000",
        "SET_VELOCITY_LIMIT ACCEL=20000", "M400"], K.gcode_cut(c, 20000)
    # Schacht (T0-Beispiel Schritt 6-6.3) / Verlassen (Schritt 11)
    assert K.gcode_go_extrude_pos(c) == [
        "G90", "G0 Y345 F12000", "G0 X139 F12000", "G0 Y378 F12000",
        "G0 X133 F12000"]
    assert K.gcode_safe_pos(c) == ["G90", "G0 X160 F12000", "G0 Y345 F12000"]
    # Abstreifen (10.1-10.3): Blech-Schnitt, 2 Kreise r=4, 3x Schütteln
    w = K.gcode_wipe(c, 20000)
    assert w[:5] == ["G90", "SET_VELOCITY_LIMIT ACCEL=5000", "G0 X160 F12000",
                     "G0 X135 F12000", "G0 X160 Y374 F12000"], w[:5]
    circ1, circ2 = w[5:21], w[22:38]
    assert w[21] == "G0 X170 Y374 F12000", w[21]

    def _pts(lines):
        out = []
        for l in lines:
            parts = dict((t[0], float(t[1:])) for t in l.split()[1:])
            out.append((parts["X"], parts["Y"], parts["F"]))
        return out
    for pts, cx, end in ((_pts(circ1), 164.0, (160.0, 374.0)),
                         (_pts(circ2), 166.0, (170.0, 374.0))):
        for x, y, f in pts:
            assert abs(math.hypot(x - cx, y - 374.0) - 4.0) < 0.01 and f == 10000
        assert abs(pts[-1][0] - end[0]) < 0.01 and abs(pts[-1][1] - end[1]) < 0.01
    # G2 = im Uhrzeigersinn: vom Startpunkt (160|374) zuerst nach oben (y+)
    assert _pts(circ1)[0][1] > 374.0 and _pts(circ2)[0][1] > 374.0
    assert w[38:44] == ["G0 X160 Y378 F12000", "G0 X133 Y378 F12000"] * 3, w[38:44]
    assert w[-1] == "SET_VELOCITY_LIMIT ACCEL=20000"
    # Spülen live: length [90, 70], check_and_extrude 18/72 und 14/56 mm
    assert K.flush_segments(c) == [90.0, 70.0]
    assert K.gcode_flush_segment(90, 360) == ["G91", "G1 E18 F360",
                                              "G1 E72 F360", "G90"]
    assert K.gcode_flush_segment(70, 360)[1:3] == ["G1 E14 F360", "G1 E56 F360"]
    assert K.flush_segments(c, 200) == [90.0, 90.0, 20.0]
    # Rückzug live 3x "extrude = -10.0" (= buffer_empty_len 30)
    assert K.retrude_extruder_steps(c) == [-10.0, -10.0, -10.0]
    assert K.retrude_extruder_steps(c, 25) == [-10.0, -10.0, -5.0]
    assert K.gcode_extruder_move(-10, 600) == ["G91", "G1 E-10 F600", "G90"]
    # TNN wie box.cfg M8200 L: addr = I/4+1, Slot = I%4
    for i, tnn in ((0, "T1A"), (1, "T1B"), (3, "T1D"), (4, "T2A"),
                   (9, "T3B"), (15, "T4D"), ("=2", "T1C")):
        assert K.tnn_from_index(i) == tnn, (i, K.tnn_from_index(i))
    assert K.parse_tnn("t1b") == (1, 1, "B", "T1B")
    for bad in (16, -1, "x", True):
        try:
            K.tnn_from_index(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("tnn_from_index(%r) hätte werfen müssen" % (bad,))
    for bad in ("T5A", "T1E", "1A", "T12"):
        try:
            K.parse_tnn(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("parse_tnn(%r) hätte werfen müssen" % (bad,))
    # Upstream-Namensregel für alle neuen G-Codes
    for name in ("KTWO_BOX_PRE_OPT", "KTWO_BOX_CUT", "KTWO_BOX_GO_EXTRUDE_POS",
                 "KTWO_BOX_SAFE_POS", "KTWO_BOX_RETRUDE", "KTWO_BOX_EXTRUDE",
                 "KTWO_BOX_FLUSH", "KTWO_BOX_WIPE", "KTWO_BOX_WASTE",
                 "KTWO_BOX_END_OPT", "KTWO_BOX_WAIT", "KTWO_BOX_ERROR_CLEAR",
                 "KTWO_BOX_RESUME_FIX"):
        assert upstream_gcode_name_ok(name), name
    return "Cut/Schacht/Abstreifen/Spülen/Rückzug == box.cfg + Live, TNN-Mapping ok"


def test_extrude_sensor_stage7():
    cfg, bus, ctl, vc = build_vsystem()
    bus.trace = []
    bus.head_at_mm = 200.0           # Kopfsensor nach 4 Polls (4 x 50 mm)
    bus.need_extruder_for_mode7 = True
    stage7 = []

    def _stage7():
        stage7.append(vc.t)
        bus.trace.append(("stage7",))
        bus.extruder_mm += 30.0      # Extruder greift (G1 E30 F360)

    res = ctl.do_extrude("B", sensor_fn=lambda: bus.head, sensor_timeout=90,
                         poll_interval=2.0, stage7_fn=_stage7, stage7_delay=2.0)
    assert res["status"] == "ok" and len(res["odo_mm"]) == 4, res
    assert ctl.loaded_slot == "B"
    seq = [e for e in bus.trace]
    names = []
    for e in seq:
        if e[0] == "stage7":
            names.append("S7")
        else:
            names.append("%02x:%s" % (e[1], ",".join("%02x" % b for b in e[2])))
    exp = (["04:00,01", "0f:01", "08:00", "10:02,00,00", "10:02,04,00"]
           + ["10:02,05,00"] * 4
           + ["10:02,06,00", "S7", "10:02,07,03", "05:", "04:02,00", "0f:00"])
    assert names == exp, names
    assert 2.0 in vc.pauses, vc.pauses  # stage7_delay vor mode 7
    # Ohne Stage 7 greift kein Extruder -> Box meldet 0x0e (mode 7)
    cfg2, bus2, ctl2, vc2 = build_vsystem()
    bus2.need_extruder_for_mode7 = True
    try:
        ctl2.do_extrude("C")
    except BOX.CfsStatusError as exc:
        assert exc.status == 0x0E, exc.status
    else:
        raise AssertionError("mode 7 ohne Extruder hätte 0x0e liefern müssen")
    # Kopfsensor nie erreicht -> Timeout, Box per 0x04 [00 01] gestoppt
    cfg3, bus3, ctl3, vc3 = build_vsystem()
    try:
        ctl3.do_extrude("D", sensor_fn=lambda: False, sensor_timeout=10,
                        poll_interval=2.0)
    except K.BoxChangeError as exc:
        assert "Kopf-Filamentsensor" in str(exc), exc
    else:
        raise AssertionError("Sensor-Timeout hätte BoxChangeError werfen müssen")
    last = bus3.calls[-1]
    assert last[1] == 0x04 and tuple(last[2]) == (0x00, 0x01), last
    polls = [c for c in bus3.calls if c[1] == 0x10 and c[2][1] == 0x05]
    assert 5 <= len(polls) <= 7, len(polls)  # ~alle 2 s in 10 s
    assert ctl3.loaded_slot is None
    return ("Einzug bis Kopfsensor (4 Polls), Stage 7 zwischen mode 6/7, "
            "0x0e ohne Extruder, Timeout -> IDLE")


def test_retrude_after_p0():
    cfg, bus, ctl, vc = build_vsystem()
    ctl.do_load("A")
    bus.trace = []
    marks = []

    def _after(i):
        marks.append(i)
        bus.trace.append(("E", i))

    ctl.do_retrude("A", after_p0=_after)
    names = [("E%d" % e[1]) if e[0] == "E" else
             "%02x:%s" % (e[1], ",".join("%02x" % b for b in e[2]))
             for e in bus.trace]
    assert names == ["04:00,01", "08:00", "11:01,00", "E0", "11:01,00", "E1",
                     "11:01,00", "E2", "11:01,00", "E3", "11:01,01"], names
    assert ctl.loaded_slot is None and bus.hub_mask & 0x01 == 0
    # Frames selbst unverändert == cfsseq-Live-Sequenz
    if _HAVE_SEQ and _HAVE_S485:
        got = [bytes(REF_BUILD(0x01, f, list(d), status=0xFF))
               for f, d in _frames(bus.trace)]
        assert got == SEQ.build_retrude_sequence(0x01), "Retrude-Frames weichen ab"
    return "Rückzug: p0 -> Extruder-Haken x4 -> p1, Frames live-identisch"


def test_wait_box_ready():
    cfg, bus, ctl, vc = build_vsystem()
    bus.preload_polls = 3
    st = ctl.wait_box_ready(timeout=60, interval=1.0)
    assert st["state_name"] == "STATE_IDLE", st
    assert len([c for c in bus.calls if c[1] == 0x0A]) == 4 and \
        vc.pauses == [1.0, 1.0, 1.0], vc.pauses
    bus.state, bus.stored_error = 4, 0x0C
    try:
        ctl.wait_box_ready(timeout=60)
    except BOX.CfsStatusError as exc:
        assert exc.status == 0x0C, exc.status
    else:
        raise AssertionError("ERROR-Zustand hätte CfsStatusError werfen müssen")
    bus.state, bus.stored_error = 0, 0
    bus.preload_polls = 10000
    try:
        ctl.wait_box_ready(timeout=5, interval=1.0)
    except K.BoxChangeError as exc:
        assert "nicht bereit" in str(exc), exc
    else:
        raise AssertionError("Timeout hätte BoxChangeError werfen müssen")
    return "PRELOAD -> warten -> IDLE, ERROR -> 0x0c, Timeout"


def build_hull(**opt_over):
    bus = FakeBoxBus()
    printer = FakePrinter(bus, with_motion=True)
    opts = test_options(allow_move="True")
    opts.update(opt_over)
    mod = K.PrinterK2Box(FakeConfig(printer, opts))
    printer.fire("klippy:connect")
    mod._bus = bus
    return bus, printer, mod


def run_chain(mod, tnn, ret=1):
    """Kette wie macros.cfg _KTWO_TOOL_CHANGE (T-Befehl) -> alle Antworten."""
    infos = []
    for fn, params in ((mod.cmd_pre_opt, {"TNN": tnn}), (mod.cmd_cut, {}),
                       (mod.cmd_retrude, {}), (mod.cmd_extrude, {"TNN": tnn}),
                       (mod.cmd_waste, {}), (mod.cmd_flush, {"TNN": tnn}),
                       (mod.cmd_end_opt, {"RETURN": str(ret)})):
        g = FakeGcmd(params)
        fn(g)
        infos.extend(g.infos)
    return infos


def _idx(trace, pred, start=0):
    for i in range(start, len(trace)):
        if pred(trace[i]):
            return i
    raise AssertionError("Eintrag fehlt ab %d" % start)


def _g(line):
    return lambda e: e[0] == "gcode" and e[1] == line


def _b(fun, data):
    return lambda e: e[0] == "bus" and e[1] == fun and tuple(e[2]) == tuple(data)


def test_wechsel_kette_voll():
    bus, printer, mod = build_hull(allow_write="True")
    bus.hub_mask, bus.head = 0x01, True   # A ist geladen
    bus.head_at_mm = 300.0
    bus.need_extruder_for_mode7 = True
    bus.trace = []
    run_chain(mod, "T1B")
    t = bus.trace
    i0 = _idx(t, _g("SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=0"))
    i1 = _idx(t, _g("SAVE_GCODE_STATE NAME=_ktwo_box_chain"), i0)
    i2 = _idx(t, _g("G1 Z0.4 F600"), i1)
    i3 = _idx(t, _g("G0 X-7.8 Y200 F30000"), i2)          # Schnitt
    i4 = _idx(t, _g("G0 X133 F12000"), i3)                # Schacht
    i5 = _idx(t, _g("M109 S220"), i4)
    i6 = _idx(t, _b(0x08, (0x01,)), i5)                   # Hub lesen
    p0 = i6
    for k in range(3):                                    # p0 + E-10 x3
        p0 = _idx(t, _b(0x11, (0x01, 0x00)), p0 + 1)
        _idx(t, _g("G1 E-10 F600"), p0)
        assert _idx(t, _g("G1 E-10 F600"), p0) < _idx(t, _b(0x11, (0x01, 0x00)), p0 + 1)
    i7 = _idx(t, _b(0x11, (0x01, 0x01)), p0)              # p1
    i8 = _idx(t, _b(0x10, (0x02, 0x06, 0x00)), i7)        # mode 6
    i9 = _idx(t, _g("G1 E30 F360"), i8)                   # Stage 7
    i10 = _idx(t, _b(0x10, (0x02, 0x07, 0x03)), i9)       # mode 7
    i11 = _idx(t, _b(0x04, (0x02, 0x00)), i10)            # PRINT
    i12 = _idx(t, _g("G1 E18 F360"), i11)
    i13 = _idx(t, _g("G1 E72 F360"), i12)
    i14 = _idx(t, _g("G0 X135 F12000"), i13)              # Abstreifen 1
    i15 = _idx(t, _g("G1 E14 F360"), i14)
    i16 = _idx(t, _g("G1 E56 F360"), i15)
    i17 = _idx(t, _g("G0 X135 F12000"), i16)              # Abstreifen 2
    i18 = _idx(t, _g("G0 Y345 F12000"), i17)              # Schacht verlassen
    i19 = _idx(t, _g("G0 X200 Y200 F12000"), i18)         # Rückfahrt
    i20 = _idx(t, _g("G0 Z20 F600"), i19)
    i21 = _idx(t, _g("M104 S0"), i20)
    _idx(t, _g("RESTORE_GCODE_STATE NAME=_ktwo_box_chain"), i21)
    _idx(t, _g("SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=1"), i21)
    assert [round(v, 3) for v in printer.pos[:3]] == [200.0, 200.0, 20.0], printer.pos
    st = mod.get_status()
    assert st["loaded_tnn"] == "T1B" and not st["chain_active"] \
        and st["chain_error"] == "" and st["change_enabled"], st
    assert printer.accel == 20000.0, printer.accel
    assert not [m for m in printer.gcode.raw if m.startswith("!!")], printer.gcode.raw
    # Gleicher Slot nochmal: kein Wechsel (Hub B + Kopfsensor)
    n_bus = len([e for e in t if e[0] == "bus" and e[1] in (0x10, 0x11, 0x04)])
    n_g = len([e for e in t if e[0] == "gcode" and e[1].startswith(("G0", "G1"))])
    infos = run_chain(mod, "T1B")
    assert len([e for e in t if e[0] == "bus" and e[1] in (0x10, 0x11, 0x04)]) == n_bus
    assert len([e for e in t if e[0] == "gcode" and e[1].startswith(("G0", "G1"))]) == n_g
    assert any("bereits geladen" in m for m in infos), infos
    return "T1B: Hop→Schnitt→Schacht→E-10x3/p0→p1→Einzug→E30→mode7→Spülen 90+70→Rückfahrt; T1B erneut = nichts"


def test_wechsel_sperren():
    # 1) beide gesperrt, kein Druck: nur Plan, keine Bewegung, keine Mechanik
    bus, printer, mod = build_hull(allow_write="False", allow_move="False")
    infos = run_chain(mod, "T1C")
    moves = [l for l in printer.gcode.scripts
             if l.split()[0] in ("G0", "G1", "M109", "M104", "PAUSE")]
    assert moves == [], moves
    mech = [c for c in bus.calls if c[1] in (0x04, 0x0D, 0x0F, 0x10, 0x11, 0xA0)]
    assert mech == [], mech
    plans = [m for m in infos if "PLAN" in m]
    assert any("X-7.8" in m for m in plans) and any("PLAN Box EXTRUDE" in m
                                                     for m in plans), plans
    assert mod.get_status()["change_enabled"] is False
    # 2) Trockenlauf mit Bewegung (allow_move, ohne Box): Kopf + Extruder ja, Box nein
    bus, printer, mod = build_hull(allow_write="False", allow_move="True")
    run_chain(mod, "T1C")
    sc = printer.gcode.scripts
    assert "G0 X-7.8 Y200 F30000" in sc and sc.count("G1 E-10 F600") == 3 \
        and "G1 E30 F360" in sc and "G1 E18 F360" in sc, sc
    mech = [c for c in bus.calls if c[1] in (0x04, 0x0D, 0x0F, 0x10, 0x11, 0xA0)]
    assert mech == [], mech
    # 3) Im Druck ohne Freigabe: PAUSE, Rest übersprungen, keine Bewegung
    bus, printer, mod = build_hull(allow_write="False", allow_move="True")
    printer.set_printing(True)
    infos = run_chain(mod, "T1B")
    sc = printer.gcode.scripts
    assert "PAUSE" in sc and not [l for l in sc if l.startswith(("G0", "G1"))], sc
    st = mod.get_status()
    assert "nicht freigegeben" in st["chain_error"] and not st["chain_active"], st
    assert any("übersprungen" in m for m in infos), infos
    assert printer.sensor.enabled, "Kopfsensor muss wieder aktiv sein"
    mod.cmd_error_clear(FakeGcmd({}))
    assert mod.get_status()["chain_error"] == ""
    return "gesperrt = nur Plan; allow_move ohne Box = Trockenlauf; Druck ohne Freigabe = PAUSE"


def test_wechsel_fehler_im_druck():
    bus, printer, mod = build_hull(allow_write="True")
    bus.fail_after_mm = 245.0          # Slot A: 0x0c wie Kanal-A-Defekt
    bus.head_at_mm = 1000.0
    printer.set_printing(True)
    bus.trace = []
    run_chain(mod, "T1A")
    sc = printer.gcode.scripts
    # seit 17.10.2026: PAUSE trägt das Rückfahrziel = Druckposition vor dem
    # Wechsel (200/200/20, Z ohne Hop) – Fehlerposition ist der Schacht
    want = "PAUSE CFS_RETURN_X=200 CFS_RETURN_Y=200 CFS_RETURN_Z=20"
    assert want in sc and "PAUSE" not in sc, sc
    st = mod.get_status()
    assert "0x0c" in st["chain_error"] and not st["chain_active"], st
    assert st["pending_resume_z"] == 20.0, st
    assert any(m.startswith("!!") and "PAUSIERT" in m for m in printer.gcode.raw)
    i_pause = sc.index(want)
    assert not [l for l in sc[i_pause:] if l.startswith(("G0", "G1"))], sc[i_pause:]
    assert printer.sensor.enabled and "RESTORE_GCODE_STATE NAME=_ktwo_box_chain" \
        in sc[i_pause:]
    # PRE_OPT mit offenem Fehler: nichts, keine zweite Pause-Bewegung
    n = len(sc)
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1A"}))
    mod.cmd_cut(FakeGcmd({}))
    assert not [l for l in sc[n:] if l.startswith(("G0", "G1"))], sc[n:]
    mod.cmd_end_opt(FakeGcmd({}))
    # Quittieren: Box 0x04 [00 01] (löscht ERROR) + RESUME_FIX senkt Z
    mod.cmd_error_clear(FakeGcmd({}))
    assert bus.calls[-1][1] == 0x04 and tuple(bus.calls[-1][2]) == (0, 1)
    mod.cmd_resume_fix(FakeGcmd({}))
    assert "G1 Z20 F600" in printer.gcode.scripts
    assert mod.get_status()["pending_resume_z"] is None
    return "0x0c im Druck -> PAUSE (kein Abbruch), Folgeschritte übersprungen, Quittung + Z-Fix"


def test_wechsel_fehler_ohne_druck():
    # Box-Fehler außerhalb des Drucks -> G-Code-Fehler, Kette aufgeräumt
    bus, printer, mod = build_hull(allow_write="True")
    bus.fail_after_mm = 245.0
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1A"}))
    try:
        mod.cmd_extrude(FakeGcmd({"TNN": "T1A"}))
    except ValueError as exc:
        assert "0x0c" in str(exc), exc
    else:
        raise AssertionError("Box-Fehler ohne Druck hätte G-Code-Fehler werfen müssen")
    assert not mod.get_status()["chain_active"] and printer.sensor.enabled
    assert "PAUSE" not in printer.gcode.scripts
    # Bewegungsfehler im Schnitt -> Beschleunigung zurück + Fehler
    bus, printer, mod = build_hull(allow_write="True")
    printer.fail_on = "G0 X-7.8"
    try:
        mod.cmd_cut(FakeGcmd({}))
    except ValueError as exc:
        assert "CUT fehlgeschlagen" in str(exc), exc
    else:
        raise AssertionError("Bewegungsfehler hätte G-Code-Fehler werfen müssen")
    assert printer.accel == 20000.0, printer.accel
    # Box 2 nicht konfiguriert / Hub durch anderen Slot belegt
    bus, printer, mod = build_hull(allow_write="True")
    for params, text in (({"TNN": "T2A"}, "Box 2"), ({"TNN": "T1B"}, "Hub belegt")):
        bus.hub_mask = 0x01 if params["TNN"] == "T1B" else 0
        try:
            mod.cmd_extrude(FakeGcmd(params))
        except ValueError as exc:
            assert text in str(exc), exc
        else:
            raise AssertionError("%s hätte abgelehnt werden müssen" % params)
    mech = [c for c in bus.calls if c[1] in (0x10, 0x11)]
    assert mech == [], mech
    # Ohne Bus (RS485 fehlt): klare Meldung, kein Absturz
    bus2 = FakeBoxBus()
    printer2 = FakePrinter(bus2, with_motion=True)
    del printer2.objects["k2_485"]
    mod2 = K.PrinterK2Box(FakeConfig(printer2, test_options(allow_move="True")))
    try:
        mod2.cmd_retrude(FakeGcmd({}))
    except ValueError as exc:
        assert "k2_485" in str(exc), exc
    else:
        raise AssertionError("ohne Bus hätte G-Code-Fehler kommen müssen")
    return "ohne Druck: G-Code-Fehler + Aufräumen; Box 2/Hub belegt/kein Bus abgelehnt"


# =====================================================================
# RESUME nach abgebrochenem CFS-Wechsel im Schneidbereich (17.10.2026,
# config-pi/NOTIZ-reinigung-resume.md §6a, Batch upstream-test-resumecut)
# =====================================================================

def _hull_t1b_ok(**over):
    """Hülle wie test_wechsel_kette_voll: Slot A geladen, T1B läuft durch."""
    bus, printer, mod = build_hull(allow_write="True", **over)
    bus.hub_mask, bus.head = 0x01, True
    bus.head_at_mm = 300.0
    bus.need_extruder_for_mode7 = True
    return bus, printer, mod


def _set_pos(printer, x, y, z):
    printer.pos[:3] = [float(x), float(y), float(z)]
    printer._refresh()


def test_resume_ziel_nach_abbruch():
    # 1) Baustein: Schneidbereich nur in X verlassen (box.cfg:13 pre_cut_pos_x)
    c = dict(K.CHANGE_DEFAULTS)
    assert K.gcode_leave_cut_zone(c, [10.0, 200.0, 5.0]) == []
    assert K.gcode_leave_cut_zone(c, [9.98, 200.0, 5.0]) == ["G0 X10 F12000", "M400"]
    assert K.gcode_leave_cut_zone(c, [-7.8, 200.0, 5.0]) == ["G0 X10 F12000", "M400"]
    assert K.gcode_leave_cut_zone(c, None) == []
    # 2) T-Kette im Druck, Schnitt bricht AM ANSCHLAG ab (nach G0 X-7.8,
    #    vor dem Rückweg G0 X10) -> PAUSE mit Rückfahrziel Druckposition
    bus, printer, mod = _hull_t1b_ok()
    printer.set_printing(True)
    _set_pos(printer, 110, 120, 10)
    printer.fail_on = "G4 P"
    run_chain(mod, "T1B")
    sc = printer.gcode.scripts
    assert [round(v, 3) for v in printer.pos[:3]] == [-7.8, 200.0, 10.4], \
        printer.pos
    want = "PAUSE CFS_RETURN_X=110 CFS_RETURN_Y=120 CFS_RETURN_Z=10"
    assert want in sc and "PAUSE" not in sc, sc
    i = sc.index(want)
    assert not [l for l in sc[i:] if l.startswith(("G0", "G1"))], sc[i:]
    st = mod.get_status()
    assert "CUT fehlgeschlagen" in st["chain_error"] and not st["chain_active"]
    assert st["pending_resume_z"] == 10.0, st   # Fallback ohne PAUSE-Makro
    assert any("Rückfahrziel für RESUME = Druckposition vor dem Wechsel "
               "X110 Y120 Z10" in m for m in printer.gcode.raw), printer.gcode.raw
    assert printer.accel == 20000.0 and printer.sensor.enabled
    assert mod._keep_start_pos == [110.0, 120.0, 10.0]
    # 3) Wiederholung IN DER PAUSE bricht erneut am Hebel ab: kein zweites
    #    PAUSE, kein neues Ziel/Z, Startposition = die des 1. Wechsels
    assert printer.paused
    mod.cmd_error_clear(FakeGcmd({}))
    n = len(sc)
    run_chain(mod, "T1B")
    assert not [l for l in sc[n:] if l.startswith("PAUSE")], sc[n:]
    assert sc[n:].count("G0 X10 Y200 F12000") == 1   # vom Hebel nur in X weg
    assert mod.get_status()["pending_resume_z"] == 10.0
    assert mod._keep_start_pos == [110.0, 120.0, 10.0]
    # 4) erfolgreiche Wiederholung in der Pause: END_OPT fährt X/Y zur
    #    Startposition, senkt Z aber NICHT (das macht RESUME)
    printer.fail_on = None
    mod.cmd_error_clear(FakeGcmd({}))
    bus.trace = []
    infos = run_chain(mod, "T1B")
    t = bus.trace
    i1 = _idx(t, _g("G0 X10 Y200 F12000"))
    assert not [e for e in t[:i1] if e[0] == "gcode" and e[1].startswith(("G0", "G1"))
                and "Y" in e[1] and "Z" not in e[1]], t[:i1]
    i2 = _idx(t, _g("G0 X110 Y120 F12000"), i1)
    assert not [e for e in t[i2:] if e[0] == "gcode" and e[1].startswith("G0 Z")], t[i2:]
    assert round(printer.pos[2], 3) > 10.0 and printer.pos[:2] == [110.0, 120.0]
    assert any("Z bleibt" in m for m in infos), infos
    st = mod.get_status()
    assert st["loaded_tnn"] == "T1B" and st["chain_error"] == "" \
        and mod._keep_start_pos is None, st
    # 5) Startposition eines früheren Abbruchs veraltet nicht: nach CANCEL
    #    (keine Pause mehr) nimmt ein neuer Wechsel die AKTUELLE Position
    bus, printer, mod = _hull_t1b_ok()
    printer.set_printing(True)
    _set_pos(printer, 110, 120, 10)
    printer.fail_on = "G4 P"
    run_chain(mod, "T1B")
    assert mod._keep_start_pos == [110.0, 120.0, 10.0]
    printer.paused, printer.fail_on = False, None      # CANCEL + neuer Druck
    printer.set_printing(True)
    mod.cmd_error_clear(FakeGcmd({}))
    _set_pos(printer, 50, 60, 5)
    bus.trace = []
    run_chain(mod, "T1B")
    t = bus.trace
    i3 = _idx(t, _g("G0 X50 Y60 F12000"))
    _idx(t, _g("G0 Z5 F600"), i3)
    assert not [e for e in t if e[0] == "gcode" and e[1].startswith("G0 X110 Y120")]
    assert [round(v, 3) for v in printer.pos[:3]] == [50.0, 60.0, 5.0]
    # 6) Einzelschritt (M8200 C ohne P) im Druck: Ziel = Position davor
    bus, printer, mod = _hull_t1b_ok()
    printer.set_printing(True)
    _set_pos(printer, 80, 90, 7)
    printer.fail_on = "G4 P"
    mod.cmd_cut(FakeGcmd({}))
    assert "PAUSE CFS_RETURN_X=80 CFS_RETURN_Y=90 CFS_RETURN_Z=7" \
        in printer.gcode.scripts, printer.gcode.scripts
    assert mod.get_status()["pending_resume_z"] is None and mod._step_start is None
    # 7) Fehler OHNE Bewegung (Druck ohne Freigabe): PAUSE ohne Ziel
    bus, printer, mod = build_hull(allow_write="False", allow_move="True")
    printer.set_printing(True)
    run_chain(mod, "T1B")
    assert "PAUSE" in printer.gcode.scripts and not [
        l for l in printer.gcode.scripts if l.startswith("PAUSE ")]
    return ("Hebel-Abbruch im Druck -> PAUSE CFS_RETURN=Druckpos (Z ohne Hop); "
            "Wiederholung in der Pause ohne neue Pause, END_OPT ohne Z-Senkung; "
            "alte Startposition verworfen; Einzelschritt; ohne Bewegung kein Ziel")


def test_schacht_aus_schneidbereich_und_resume_fix():
    # Kopf am Hebel (abgebrochener Schnitt außerhalb/innerhalb einer Pause,
    # Schritt von Hand wiederholt): erst NUR X auf pre_cut_pos_x, dann Y345
    bus, printer, mod = build_hull(allow_write="False", allow_move="True")
    _set_pos(printer, -7.8, 200, 20)
    bus.trace = []
    mod.cmd_go_extrude_pos(FakeGcmd({}))
    moves = [e[1] for e in bus.trace if e[0] == "gcode"
             and e[1].startswith(("G0", "G1", "M400"))]
    assert moves == ["G0 X10 F12000", "M400", "G0 Y345 F12000", "G0 X139 F12000",
                     "G0 Y378 F12000", "G0 X133 F12000"], moves
    # außerhalb des Schneidbereichs unverändert (Stock T0-Beispiel 6.)
    _set_pos(printer, 10, 200, 20)
    bus.trace = []
    mod.cmd_go_extrude_pos(FakeGcmd({}))
    moves = [e[1] for e in bus.trace if e[0] == "gcode"
             and e[1].startswith(("G0", "G1"))]
    assert moves[0] == "G0 Y345 F12000", moves
    # RESUME_FIX CLEAR=1 (aus PAUSE): verwirft die Z-Korrektur ohne Fahrt
    mod.pending_resume_z = 12.0
    n = len(printer.gcode.scripts)
    g = FakeGcmd({"CLEAR": "1"})
    mod.cmd_resume_fix(g)
    assert mod.get_status()["pending_resume_z"] is None
    assert any("verworfen" in m for m in g.infos), g.infos
    g = FakeGcmd({"CLEAR": "1"})
    mod.cmd_resume_fix(g)
    assert g.infos == [] and printer.gcode.scripts[n:] == []
    mod.cmd_resume_fix(FakeGcmd({}))           # nichts offen -> nichts
    assert printer.gcode.scripts[n:] == []
    return "Schacht-Einfahrt vom Hebel: X10 vor Y345; RESUME_FIX CLEAR=1 ohne Fahrt"


def _render_env():
    import jinja2
    path = os.path.join(_HERE, "config-pi", "macros.cfg")
    macros = _macro_sources(path)
    kcfg = {}
    sec = open(os.path.join(_HERE, "config-pi", "k2-module.cfg"),
               encoding="utf-8").read()
    a = sec.index("[k2_box]")
    for ln in sec[a:sec.index("\n[", a + 1)].splitlines()[1:]:
        if ":" in ln and not ln.startswith("#"):
            k, v = ln.split(":", 1)
            kcfg[k.strip().lower()] = v.strip()

    def pyval(v):
        if v in ("True", "False"):
            return v == "True"
        try:
            return float(v) if "." in v else int(v)
        except ValueError:
            return v
    param = {k: pyval(v) for k, v in macros["_PI_PARAM"][1].items()}
    return jinja2.Environment("{%", "%}", "{", "}"), macros, kcfg, param


class _RenderRaise(Exception):
    pass


def _rprinter(param, kcfg, **upd):
    pr = {
        "gcode_macro _PI_PARAM": dict(param, hotend_temp=220, pause_x=110.0,
                                      pause_y=120.0, pause_z=10.0,
                                      pause_pos_valid=True),
        "gcode_macro _PI_FREIGABE": {"allow_move": True},
        "k2_box": {"allow_move": True, "allow_write": True,
                   "change_enabled": True, "chain_active": False,
                   "chain_error": ""},
        "toolhead": {"homed_axes": "xyz", "max_accel": 20000.0,
                     "position": {"x": 342.5, "y": 200.0, "z": 50.0},
                     "axis_maximum": {"x": 352.5, "y": 400.0}},
        "gcode_move": {"gcode_position": {"x": 342.5, "y": 200.0, "z": 50.0,
                                          "e": 0.0}},
        "pause_resume": {"is_paused": True},
        "print_stats": {"state": "printing"},
        "extruder": {"temperature": 220.0, "target": 220.0, "can_extrude": True},
        "configfile": {"config": {"k2_box": dict(kcfg)},
                       "settings": {"extruder": {"min_extrude_temp": 170.0}}},
        "output_pin fan0": {"value": 0.0}, "output_pin fan2": {"value": 0.0},
    }
    for key, val in upd.items():
        pr[key].update(val)
    return pr


def _render(env, macros, name, printer, params=None):
    msgs = []

    def _info(m):
        msgs.append(m)
        return ""

    def _err(m):
        raise _RenderRaise(m)
    out = env.from_string(macros[name][0]).render(
        params=dict(params or {}), rawparams="", printer=printer,
        action_respond_info=_info, action_raise_error=_err)
    return [l.strip() for l in out.split("\n")
            if l.strip() and not l.strip().startswith("#")], msgs


def test_makros_resume_schneidbereich():
    try:
        env, macros, kcfg, param = _render_env()
    except ImportError:
        return "SKIP (jinja2 fehlt)"
    for n in ("PAUSE", "_PI_RESUME_PREPARE", "_PI_RESUME_RETURN",
              "_PI_RESUME_REBASE", "_PI_CLEAN_PATH"):
        assert n in macros, n
    assert param.get("pause_rebase") is False, param
    R = lambda name, pr, params=None: _render(env, macros, name, pr, params)  # noqa: E731
    pos = lambda x, y, z: {"gcode_position": {"x": x, "y": y, "z": z, "e": 0.0}}  # noqa: E731
    tpos = lambda x, y, z: {"position": {"x": x, "y": y, "z": z}}  # noqa: E731

    def raises(name, pr, txt, params=None):
        try:
            lines, _m = R(name, pr, params)
        except _RenderRaise as exc:
            assert all(t in str(exc) for t in txt), exc
            return str(exc)
        raise AssertionError("%s hätte verweigern müssen: %s" % (name, lines))

    # --- PAUSE normal: Ziel = Position, kein Rebase, Altlast-Z weg -------
    pr = _rprinter(param, kcfg, gcode_move=pos(110.0, 120.0, 10.0),
                   toolhead=tpos(110.0, 120.0, 10.0),
                   pause_resume={"is_paused": False})
    lines, msgs = R("PAUSE", pr)
    i = lines.index("PAUSE_BASE")
    assert lines[i + 1:i + 7] == [
        "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_x VALUE=110.0",
        "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_y VALUE=120.0",
        "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_z VALUE=10.0",
        "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_pos_valid VALUE=True",
        "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_rebase VALUE=False",
        "KTWO_BOX_RESUME_FIX CLEAR=1"], lines
    mv = [l for l in lines if l.startswith("G1 ")]
    assert mv[0] == "G1 E-1.0 F180" and "G1 X10.0 F12000.0" not in lines \
        and "G1 Y345.0 F12000.0" not in lines and msgs == [], (lines, msgs)
    # --- PAUSE nach Abbruch AM HEBEL (k2_box: CFS_RETURN = Druckposition) --
    pr = _rprinter(param, kcfg, gcode_move=pos(-7.8, 200.0, 10.4),
                   toolhead=tpos(-7.8, 200.0, 10.4),
                   pause_resume={"is_paused": False})
    cfs = {"CFS_RETURN_X": "110", "CFS_RETURN_Y": "120", "CFS_RETURN_Z": "10"}
    lines, msgs = R("PAUSE", pr, cfs)
    i = lines.index("PAUSE_BASE")
    assert lines[i + 1:i + 7] == [
        "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_x VALUE=110.0",
        "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_y VALUE=120.0",
        "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_z VALUE=10.0",
        "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_pos_valid VALUE=True",
        "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_rebase VALUE=True",
        "KTWO_BOX_RESUME_FIX CLEAR=1"], lines
    assert msgs and "Fehlerposition X-7.80 Y200.00 Z10.40" in msgs[0] \
        and "X110.00 Y120.00 Z10.00" in msgs[0], msgs
    mv = [l for l in lines[i:] if l.startswith(("G1 ", "G90", "G91"))]
    assert mv[:3] == ["G90", "G1 X10.0 F12000.0", "G91"], mv   # erst nur X raus
    assert lines.index("G1 X10.0 F12000.0") < lines.index("G1 Z39.6 F600") \
        < lines.index("G1 X342.5 Y200.0 F6000"), lines
    # --- PAUSE nach Abbruch IM SCHACHT: Z-Hub, X160, Y345, dann Park -------
    pr = _rprinter(param, kcfg, gcode_move=pos(133.0, 378.0, 10.4),
                   toolhead=tpos(133.0, 378.0, 10.4),
                   pause_resume={"is_paused": False})
    lines, _m = R("PAUSE", pr, cfs)
    mv = [l for l in lines if l.startswith("G1 ") and "E" not in l]
    assert mv == ["G1 Z39.6 F600", "G1 X160.0 F12000.0", "G1 Y345.0 F12000.0",
                  "G1 X342.5 Y200.0 F6000"], mv
    # --- PREPARE: Rebase nach der Rückfahrt -------------------------------
    pr = _rprinter(param, kcfg)
    pr["gcode_macro _PI_PARAM"]["pause_rebase"] = True
    lines, _m = R("_PI_RESUME_PREPARE", pr)
    assert lines == ["SAVE_GCODE_STATE NAME=_pi_resume_prep",
                     "_PI_NOZZLE_CLEAN ONLY=GO WHY=RESUME", "M109 S220",
                     "_PI_RESUME_PRIME", "_PI_NOZZLE_CLEAN WHY=RESUME",
                     "_PI_RESUME_RETURN",
                     "RESTORE_GCODE_STATE NAME=_pi_resume_prep",
                     "_PI_RESUME_REBASE"], lines
    # ohne Bewegungsfreigabe: verweigert (RESUME_BASE führe zur Fehlerpos.)
    pr["gcode_macro _PI_FREIGABE"]["allow_move"] = False
    raises("_PI_RESUME_PREPARE", pr, ["RESUME gesperrt", "abgebrochenem CFS",
                                      "_PI_FREIGABE allow_move=False"])
    pr = _rprinter(param, kcfg)
    pr["gcode_macro _PI_PARAM"]["pause_rebase"] = True
    pr["k2_box"]["allow_move"] = False
    raises("_PI_RESUME_PREPARE", pr, ["RESUME gesperrt", "[k2_box] allow_move"])
    # Kopf am Hebel (Abbruch in einer Pause): erst nur X, dann Schacht
    pr = _rprinter(param, kcfg, gcode_move=pos(-7.8, 200.0, 50.4))
    lines, msgs = R("_PI_RESUME_PREPARE", pr)
    assert lines[:5] == ["SAVE_GCODE_STATE NAME=_pi_resume_prep", "G90",
                         "G1 X10.0 F12000.0", "M400",
                         "_PI_NOZZLE_CLEAN ONLY=GO WHY=RESUME"], lines
    assert "_PI_RESUME_REBASE" not in lines and "Schneidbereich" in msgs[0]
    pr["k2_box"]["allow_move"] = False
    raises("_PI_RESUME_PREPARE", pr, ["RESUME gesperrt", "Schneidbereich X-7.80"])
    # Ziel am Hebel: immer verweigert, vor jeder Zeile
    pr = _rprinter(param, kcfg)
    pr["gcode_macro _PI_PARAM"].update(pause_x=-7.8, pause_y=200.0)
    raises("_PI_RESUME_PREPARE", pr, ["RESUME gesperrt", "Schneidhebel"])
    # nicht referenziert: alter Pfad (nur M109), kein Fehlalarm X0 < 10
    pr = _rprinter(param, kcfg, gcode_move=pos(0.0, 0.0, 0.0),
                   toolhead={"homed_axes": ""})
    lines, msgs = R("_PI_RESUME_PREPARE", pr)
    assert lines == ["M109 S220"] and "nicht referenziert" in msgs[0], (lines, msgs)
    # --- RETURN -----------------------------------------------------------
    pr = _rprinter(param, kcfg, gcode_move=pos(160.0, 345.0, 50.0))
    pr["gcode_macro _PI_PARAM"]["pause_rebase"] = True
    lines, msgs = R("_PI_RESUME_RETURN", pr)
    assert lines == ["G90", "G0 X110.0 Y120.0 F12000.0", "G1 Z10.0 F600.0"] \
        and "vor dem abgebrochenen CFS-Wechsel" in msgs[0], (lines, msgs)
    pr["gcode_macro _PI_PARAM"].update(pause_x=5.0)          # Bettkante
    lines, _m = R("_PI_RESUME_RETURN", pr)
    assert lines == ["G90", "G0 X10.0 Y120.0 F12000.0", "G0 X5.0 F12000.0",
                     "G1 Z10.0 F600.0"], lines
    pr = _rprinter(param, kcfg, gcode_move=pos(-7.8, 200.0, 50.0))
    lines, _m = R("_PI_RESUME_RETURN", pr)
    assert lines == ["G90", "G0 X10.0 F12000.0", "M400",
                     "G0 X110.0 Y120.0 F12000.0", "G1 Z10.0 F600.0"], lines
    pr["k2_box"]["chain_error"] = "[k2_box] GO_EXTRUDE_POS fehlgeschlagen: x"
    raises("_PI_RESUME_RETURN", pr, ["vor der Rückfahrt", "GO_EXTRUDE_POS"])
    # --- REBASE -----------------------------------------------------------
    pr = _rprinter(param, kcfg, gcode_move=pos(110.0, 120.0, 10.0))
    lines, msgs = R("_PI_RESUME_REBASE", pr)
    assert lines == ["RESTORE_GCODE_STATE NAME=PAUSE_STATE",
                     "SAVE_GCODE_STATE NAME=PAUSE_STATE",
                     "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_rebase "
                     "VALUE=False"], lines
    pr = _rprinter(param, kcfg, gcode_move=pos(110.0, 120.5, 10.0))
    raises("_PI_RESUME_REBASE", pr, ["nicht an der Druckposition",
                                     "PAUSE_STATE unverändert"])
    # --- _PI_CLEAN_PATH GO vom Hebel == k2_box _go_extrude_pos -------------
    pr = _rprinter(param, kcfg, gcode_move=pos(-7.8, 200.0, 20.0))
    lines, _m = R("_PI_CLEAN_PATH", pr, {"PART": "GO"})
    c = dict(K.CHANGE_DEFAULTS)
    exp = K.gcode_go_extrude_pos(c)
    exp = exp[:1] + K.gcode_leave_cut_zone(c, [-7.8, 200.0]) + exp[1:]
    norm = lambda l: [(w[0], float(w[1:])) if w[0] in "XYF" and len(w) > 1  # noqa: E731
                      else w for w in l.split()]
    assert [norm(l) for l in lines] == [norm(l) for l in exp], (lines, exp)
    return ("PAUSE (normal/Hebel/Schacht), PREPARE (Rebase, Sperren, Hebel), "
            "RETURN (Ziel Bettkante, vom Hebel, chain_error), REBASE, CLEAN_PATH")


def test_reactor_bus_proxy():
    bus = FakeBoxBus()
    seen = []
    real = bus.transact_full

    def _tf(*a, **k):
        seen.append(threading.current_thread() is threading.main_thread())
        return real(*a, **k)

    bus.transact_full = _tf
    r = FakeReactor()
    proxy = K.ReactorBusProxy(bus, r)
    out = proxy.transact_full(0x01, 0x0A, [], timeout=0.5, retries=0)
    assert out[2] == 0x0A and seen == [False] and r.pauses, (out, seen)

    class Boom:
        def transact_full(self, *a, **k):
            raise RuntimeError("Port weg")
    try:
        K.ReactorBusProxy(Boom(), r).transact_full(1, 0x0A, [])
    except RuntimeError as exc:
        assert "Port weg" in str(exc)
    else:
        raise AssertionError("Fehler im Hilfs-Thread muss durchgereicht werden")
    # Hülle nutzt den Proxy, wenn der Reactor pause+monotonic hat
    b2, p2, m2 = build_hull()
    ctl = m2._ensure_controller()
    assert isinstance(ctl.bus, K.ReactorBusProxy), type(ctl.bus)
    return "Bus-Aufruf im Hilfs-Thread, Reactor pausiert, Fehler durchgereicht"


def test_python39():
    import ast
    for fn in ("k2_box.py", "test_k2_box.py"):
        with open(os.path.join(_HERE, fn), encoding="utf-8") as fh:
            ast.parse(fh.read(), filename=fn, feature_version=(3, 9))
    return "k2_box.py + test_k2_box.py Python-3.9-Syntax ok"


def _macro_sources(path):
    """[gcode_macro NAME] -> (gcode-Text, variablen) aus einer cfg-Datei."""
    macros, cur, in_gcode, var = {}, None, False, {}
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if line.startswith("["):
                cur, in_gcode = None, False
                if line.startswith("[gcode_macro "):
                    cur = line[len("[gcode_macro "):-1].strip()
                    macros[cur] = {"gcode": [], "vars": {}}
                continue
            if cur is None:
                continue
            if in_gcode and (line.startswith((" ", "\t")) or not line.strip()):
                macros[cur]["gcode"].append(line.strip())
                continue
            in_gcode = False
            if line.startswith("gcode:"):
                in_gcode = True
            elif line.startswith("variable_"):
                k, v = line[len("variable_"):].split(":", 1)
                macros[cur]["vars"][k.strip()] = v.strip()
    return {k: ("\n".join(v["gcode"]), v["vars"]) for k, v in macros.items()}


def test_makros_cfg():
    try:
        import jinja2
    except Exception:
        return "SKIP (jinja2 fehlt)"
    path = os.path.join(_HERE, "config-pi", "macros.cfg")
    macros = _macro_sources(path)
    for n in ["T%d" % i for i in range(16)] + ["M8200", "_KTWO_TOOL_CHANGE",
                                               "RESUME"]:
        assert n in macros, "Makro %s fehlt" % n
    env = jinja2.Environment("{%", "%}", "{", "}")

    class _Raise(Exception):
        pass

    def render(name, params=None, k2=None, m8200_tnn=""):
        src, _vars = macros[name]
        printer = {"k2_box": dict({"change_enabled": True, "chain_error": ""},
                                  **(k2 or {})),
                   "gcode_macro M8200": {"tnn": m8200_tnn},
                   "gcode_macro _PI_PARAM": {"hotend_temp": 0, "fan0_speed": 0,
                                             "fan2_speed": 0},
                   "gcode_macro _KTWO_FAN_SAVE": {"fan2_value": 0.0},
                   "output_pin fan2": {"value": 0.0},
                   "pause_resume": {"is_paused": False}}
        msgs = []

        def _info(m):
            msgs.append(m)
            return ""

        def _err(m):
            raise _Raise(m)

        out = env.from_string(src).render(
            params=dict(params or {}), rawparams="", printer=printer,
            action_respond_info=_info, action_raise_error=_err)
        lines = [l.strip() for l in out.split("\n")
                 if l.strip() and not l.strip().startswith("#")]
        return lines, msgs

    # T-Befehle: T1..T15 -> Kette, T0 nur mit Freigabe (sonst Einfarbbetrieb)
    for i in range(1, 16):
        assert render("T%d" % i)[0] == ["_KTWO_TOOL_CHANGE T=%d" % i]
    assert render("T0")[0] == ["_KTWO_TOOL_CHANGE T=0"]
    lines, msgs = render("T0", k2={"change_enabled": False})
    assert lines == [] and "Einfarbbetrieb" in msgs[0], (lines, msgs)
    exp = {0: "T1A", 1: "T1B", 3: "T1D", 4: "T2A", 5: "T2B", 15: "T4D"}
    for i, tnn in exp.items():
        lines, _m = render("_KTWO_TOOL_CHANGE", {"T": str(i)})
        assert lines == ["_KTWO_FAN_SAVE", "KTWO_BOX_PRE_OPT TNN=%s" % tnn,
                         "KTWO_BOX_CUT", "KTWO_BOX_RETRUDE",
                         "KTWO_BOX_EXTRUDE TNN=%s" % tnn, "KTWO_BOX_WASTE",
                         "KTWO_BOX_FLUSH TNN=%s" % tnn,
                         "KTWO_BOX_END_OPT RETURN=1", "_KTWO_FAN_RESTORE"], lines
    # M8200-Kette (Stock box.cfg-Semantik)
    cases = [({"P": ""}, ["KTWO_BOX_PRE_OPT Z_HOP=0"]),
             ({"P": "", "S": "5"}, ["KTWO_BOX_PRE_OPT Z_HOP=0 TNN=T2B"]),
             ({"C": "", "S": "0"}, ["KTWO_BOX_CUT"]),
             ({"R": ""}, ["KTWO_BOX_RETRUDE"]),
             ({"R": "", "E": "20"}, ["KTWO_BOX_RETRUDE LENGTH=20.0"]),
             ({"L": "", "I": "0"}, ["KTWO_BOX_EXTRUDE TNN=T1A",
                                    "SET_GCODE_VARIABLE MACRO=M8200 VARIABLE=tnn VALUE='\"T1A\"'",
                                    "KTWO_BOX_WASTE"]),
             ({"L": "", "I": "=7"}, None),
             ({"W": ""}, ["KTWO_BOX_WASTE"]),
             ({"F": "", "S": "600", "L": "100"}, ["KTWO_BOX_FLUSH",
                                                 "SET_GCODE_VARIABLE MACRO=M8200 VARIABLE=tnn VALUE='\"\"'"]),
             ({"O": ""}, ["KTWO_BOX_END_OPT RETURN=0"])]
    for params, want in cases:
        lines, msgs = render("M8200", params)
        if want is None:
            assert lines[0] == "KTWO_BOX_EXTRUDE TNN=T2D" and msgs == ["tnn: T2D"], lines
        else:
            assert lines == want, (params, lines)
    lines, _m = render("M8200", {"F": ""}, m8200_tnn="T1C")
    assert lines[0] == "KTWO_BOX_FLUSH TNN=T1C", lines
    # RESUME gesperrt bei offenem Wechsel-Fehler, sonst mit RESUME_FIX
    try:
        render("RESUME", k2={"chain_error": "EXTRUDE fehlgeschlagen"})
    except _Raise as exc:
        assert "RESUME gesperrt" in str(exc)
    else:
        raise AssertionError("RESUME hätte gesperrt sein müssen")
    lines, _m = render("RESUME")
    assert lines[-2:] == ["RESUME_BASE", "KTWO_BOX_RESUME_FIX"], lines
    # Jeder KTWO_BOX_*-Befehl der Makros ist im Modul registriert
    bus = FakeBoxBus()
    printer = FakePrinter(bus, with_motion=True)
    K.PrinterK2Box(FakeConfig(printer, test_options()))
    import re as _re
    used = set()
    for src, _v in macros.values():
        used.update(_re.findall(r"KTWO_BOX_[A-Z_]*[A-Z]", src))
    missing = sorted(u for u in used if u not in printer.gcode.commands)
    assert not missing, missing
    assert "_PI_KEIN_CFS" not in open(path, encoding="utf-8").read()
    return "T0..T15/M8200/RESUME gerendert (Jinja2), TNN == box.cfg, %d KTWO_BOX_* registriert" % len(used)


# =====================================================================
# Auto-Nachladen bei Runout (A7)
# =====================================================================

# Synthetische 40-Zeichen-vender (Feldlayout box.RFID_VENDOR_FIELDS, KEINE
# echten Seriennummern): mat 101001 (Hyper PLA) + Farbe weiß/schwarz.
V_WEISS = "10126000000" + "101001" + "0FFFFFF" + "0330000000000000"
V_SCHWARZ = V_WEISS.replace("0FFFFFF", "0000000")
V_PETG = V_WEISS.replace("101001", "102001")


def test_runout_slotwahl():
    sel = K.select_runout_slot
    rf = {"A": V_WEISS, "B": V_SCHWARZ, "C": V_WEISS, "D": V_WEISS}
    assert K.rfid_material(V_WEISS) == ("101001", "0FFFFFF")
    for bad in ("unknown", "busy", "", None, V_WEISS[:-1],
                "10126000000" + "000000" + "0FFFFFF" + "0330000000000000"):
        assert K.rfid_material(bad) is None, bad
    # gleiches Material + Farbe, Reihenfolge ab dem Slot NACH dem leeren
    assert sel("A", 0x0E, rf)[0] == "C"          # B schwarz übersprungen
    assert sel("C", 0x0B, rf)[0] == "D"          # zyklisch C -> D
    assert sel("D", 0x07, rf)[0] == "A"          # D -> A
    assert sel("A", 0x0A, rf)[0] == "D"          # C leer -> D (weiß)
    assert sel("A", 0x03, rf)[0] is None         # C/D ohne Filament am Einlauf
    assert sel("A", 0x02, rf)[0] is None         # nur schwarz belegt
    assert sel("A", 0x02, rf, require_color=False)[0] == "B"
    t, why = sel("A", 0x0E, {"A": V_WEISS, "B": V_PETG, "C": "unknown",
                             "D": V_PETG}, require_color=False)
    assert t is None and "no tray" in why, why  # anderes Material / kein Tag
    t, why = sel("A", 0x0E, {"A": "unknown", "C": V_WEISS})
    assert t is None and "unbekannt" in why, why
    assert sel("A", 0x0F, rf)[0] != "A"           # nie der leere Slot
    # Gleich-Material-Gruppen (Stock same_material, manuell)
    g = K.parse_same_material("T1A,T1C/T1B,T1D")
    assert g == [["A", "C"], ["B", "D"]], g
    assert sel("A", 0x0F, {}, g)[0] == "C" and sel("D", 0x0F, {}, g)[0] == "B"
    assert sel("A", 0x0B, {}, g)[0] is None       # C leer
    assert sel("A", 0x0F, {}, [["B", "D"]])[0] is None
    assert K.parse_same_material("T1D,T1A,T1B") == [["D", "A", "B"]]
    assert sel("A", 0x0F, {}, [["D", "A", "B"]])[0] == "B"  # Listenfolge
    for bad in ("T1A", "T1A,T2B", "T1A,T1B/T1B,T1C", "", "T1A,T1E"):
        try:
            K.parse_same_material(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("LIST %r hätte abgelehnt werden müssen" % bad)
    # Optionen: Default AUS, Grenzen
    c = K.parse_options(test_options())
    assert c["runout_refill"] is False and c["runout_require_color"] is True \
        and c["runout_resume"] is True and c["runout_tail_length"] == 30.0 \
        and c["runout_tail_velocity"] == 600.0, c
    for bad in ({"runout_tail_length": "-1"}, {"runout_tail_length": "150"},
                {"runout_tail_velocity": "0"}, {"runout_refill": "vielleicht"}):
        try:
            K.parse_options(test_options(**bad))
        except ValueError:
            pass
        else:
            raise AssertionError("Option %r hätte abgelehnt werden müssen" % bad)
    return "RFID mat+Farbe zyklisch ab Nachbar, Einlauf Pflicht, Gruppen-Liste, Optionen Default AUS"


def build_runout(**opt_over):
    """Druck läuft mit T1A, Spule A leer: Hub/Kopf leer, Box PRINT + 0x50,
    Etikett A beim Laden gemerkt, B schwarz, C weiß (Ersatz), D weiß leer."""
    opts = dict(allow_write="True", runout_refill="True")
    opts.update(opt_over)
    bus, printer, mod = build_hull(**opts)
    for letter, tag in zip("ABCD", (V_WEISS, V_SCHWARZ, V_WEISS, V_WEISS)):
        bus.slots[letter]["rfid"] = tag
    ctl = mod._ensure_controller()
    ctl.get_rfid("A")                      # wie nach KTWO_BOX_EXTRUDE T1A
    ctl.loaded_slot = "A"
    bus.slots["A"]["filament"] = False     # Spule leer -> RFID "unknown"
    bus.slots["D"]["filament"] = False
    bus.state, bus.print_frame_status = 2, 0x50
    bus.hub_mask, bus.head = 0x00, False
    bus.head_at_mm = 100.0
    bus.need_extruder_for_mode7 = True
    printer.objects["gcode_macro _PI_PARAM"] = _Obj({"hotend_temp": 235.0})
    printer.set_printing(True)
    printer.gcode.run_script_from_command("PAUSE")  # pause_on_runout: True
    bus.calls[:] = []
    bus.trace = []
    return bus, printer, mod


def _mech(bus):
    return [c for c in bus.calls if c[1] in (0x04, 0x0D, 0x0F, 0x10, 0x11)]


def test_runout_ablauf():
    bus, printer, mod = build_runout()
    n0 = len(printer.gcode.scripts)
    g = FakeGcmd({})
    mod.cmd_runout(g)
    t = bus.trace
    i0 = _idx(t, _b(0x0A, ()))                              # Zustand PRINT/0x50
    i1 = _idx(t, _b(0x08, (0x01,)), i0)                     # Hub leer
    i2 = _idx(t, _b(0x08, (0x00,)), i1)                     # Einlauf
    i3 = _idx(t, _b(0x02, (0x0F,)), i2)                     # RFID alle
    i4 = _idx(t, _g("SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=0"), i3)
    i5 = _idx(t, _g("G0 X133 F12000"), i4)                  # Schacht
    i6 = _idx(t, _g("M109 S235"), i5)                       # Drucktemperatur
    i7 = _idx(t, _g("G1 E30 F600"), i6)                     # Stock E30 F600
    i8 = _idx(t, _b(0x10, (0x04, 0x00, 0x00)), i7)          # Einzug Slot C
    i9 = _idx(t, _b(0x10, (0x04, 0x06, 0x00)), i8)
    i10 = _idx(t, _g("G1 E30 F360"), i9)                    # Stage 7
    i11 = _idx(t, _b(0x10, (0x04, 0x07, 0x03)), i10)
    i12 = _idx(t, _b(0x04, (0x04, 0x00)), i11)              # PRINT Slot C
    i13 = _idx(t, _g("G1 E18 F360"), i12)                   # Spülen 90 + 70
    i14 = _idx(t, _g("G1 E56 F360"), i13)
    i15 = _idx(t, _g("G0 Y345 F12000"), i14)                # Schacht raus
    _idx(t, _g("SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=1"), i15)
    sc = printer.gcode.scripts[n0:]
    assert sc[-1] == "RESUME", sc[-5:]
    assert "KTWO_BOX_PRE_OPT TNN=T1C Z_HOP=0" in sc, sc[:3]
    assert not [l for l in sc if l.startswith("G1 Z")], "kein Z-Hop (PAUSE hob)"
    assert "G0 X-7.8 Y200 F30000" not in sc, "kein Schnitt (Kopf leer)"
    assert not [c for c in bus.calls if c[1] == 0x11], "kein Rückzug (Hub leer)"
    st = mod.get_status()
    assert st["loaded_tnn"] == "T1C" and st["chain_error"] == "" \
        and not st["chain_active"] and st["auto_refill"] == 1, st
    assert "T1A leer -> T1C nachgeladen" in st["runout_last"], st
    assert not printer.paused and printer.print_state == "printing"
    assert not [m for m in printer.gcode.raw if m.startswith("!!")], printer.gcode.raw
    # Rückfahrt zum Parkpunkt der PAUSE vor dem RESUME (start_pos)
    _idx(t, _g("G0 X200 Y200 F12000"), i15)
    # Box im ERROR 0x51 (Folge des Runouts) -> erst 0x04 [00 01], dann Kette
    bus, printer, mod = build_runout()
    bus.state, bus.stored_error = 4, 0x51
    mod.cmd_runout(FakeGcmd({}))
    k = [i for i, c in enumerate(bus.calls) if c[1] == 0x04]
    assert tuple(bus.calls[k[0]][2]) == (0x00, 0x01), bus.calls[k[0]]
    assert mod.get_status()["loaded_tnn"] == "T1C" and not printer.paused
    # runout_resume False: nachgeladen, Druck bleibt pausiert
    bus, printer, mod = build_runout(runout_resume="False")
    mod.cmd_runout(FakeGcmd({}))
    assert printer.paused and "RESUME" not in printer.gcode.scripts
    assert mod.get_status()["loaded_tnn"] == "T1C"
    return "T1A leer: Analyse 0a/08/08/02 -> Schacht, M109 235, E30 F600 -> Einzug C, E30 F360, mode7, PRINT -> Spülen -> Park -> RESUME; ERROR 0x51 quittiert"


def _assert_runout_pause(bus, printer, mod, text, n_scripts=None, bus_ok=True):
    st = mod.get_status()
    assert text in st["runout_last"], (text, st["runout_last"])
    assert printer.paused, "Druck muss pausiert bleiben"
    sc = printer.gcode.scripts[n_scripts or 0:]
    assert not [l for l in sc if l.split()[0] in ("G0", "G1", "M109", "RESUME")
                or l.startswith("KTWO_")], sc
    assert _mech(bus) == [], _mech(bus)
    if not bus_ok:
        assert bus.calls == [], bus.calls
    assert any(m.startswith("!!") and "PAUSIERT" in m for m in printer.gcode.raw)
    assert st["chain_error"] == "", "Vorprüfung sperrt RESUME nicht"


def test_runout_pausenpfade():
    # 1) Auslieferung: Auto-Nachladen aus -> nur Pause, kein Bus
    bus, printer, mod = build_runout(runout_refill="False")
    n = len(printer.gcode.scripts)
    mod.cmd_runout(FakeGcmd({}))
    _assert_runout_pause(bus, printer, mod, "Auto-Nachladen aus", n, bus_ok=False)
    # 2) Freigabe fehlt -> Pause, kein Bus
    bus, printer, mod = build_runout(allow_write="False")
    n = len(printer.gcode.scripts)
    mod.cmd_runout(FakeGcmd({}))
    _assert_runout_pause(bus, printer, mod, "nicht freigegeben", n, bus_ok=False)
    # 3) pause_on_runout False (Druck läuft noch): k2_box pausiert selbst
    bus, printer, mod = build_runout(allow_move="False")
    printer.paused, printer.print_state = False, "printing"
    printer._refresh()
    n = len(printer.gcode.scripts)
    mod.cmd_runout(FakeGcmd({}))
    assert printer.gcode.scripts[n:] == ["PAUSE"], printer.gcode.scripts[n:]
    _assert_runout_pause(bus, printer, mod, "nicht freigegeben", n + 1)
    # 4) Lesende Fälle: je eine Störung -> Pause mit Klartext
    cases = (
        ("Kopf", lambda b, m: setattr(b, "head", True), "Fehlauslösung"),
        ("IDLE", lambda b, m: setattr(b, "state", 0), "CFS druckt nicht"),
        ("Fehler", lambda b, m: (setattr(b, "state", 4),
                                 setattr(b, "stored_error", 0x0C)),
         "ERROR (0x0c"),
        ("Slot", lambda b, m: setattr(m.controller, "loaded_slot", None),
         "Slot unbekannt"),
        ("Hub", lambda b, m: setattr(b, "hub_mask", 0x01), "Hub meldet noch"),
        ("Einlauf", lambda b, m: b.slots["A"].update(filament=True),
         "Einlauf von Slot A"),
        ("kein Ersatz", lambda b, m: b.slots["C"].update(rfid=V_SCHWARZ),
         "no tray"),
        ("kein Etikett", lambda b, m: m.controller.tag_cache.clear(),
         "unbekannt (kein lesbares RFID"),
    )
    for name, setup, text in cases:
        bus, printer, mod = build_runout()
        setup(bus, mod)
        bus.calls[:] = []
        n = len(printer.gcode.scripts)
        mod.cmd_runout(FakeGcmd({}))
        try:
            _assert_runout_pause(bus, printer, mod, text, n)
        except AssertionError as exc:
            raise AssertionError("%s: %s" % (name, exc))
    # 5) Farbe egal -> B (schwarz) wird genommen (DRY_RUN: nichts bewegt)
    bus, printer, mod = build_runout(runout_require_color="False")
    bus.slots["C"]["rfid"] = V_SCHWARZ
    n = len(printer.gcode.scripts)
    g = FakeGcmd({"DRY_RUN": "1"})
    mod.cmd_runout(g)
    assert any("Ersatz T1B" in m and "DRY_RUN" in m for m in g.infos), g.infos
    assert printer.gcode.scripts[n:] == [] and _mech(bus) == []
    # 6) Kein RS485-Bus -> klare Meldung, Pause
    bus, printer, mod = build_runout()
    mod.controller, mod._bus = None, None
    del printer.objects["k2_485"]
    n = len(printer.gcode.scripts)
    mod.cmd_runout(FakeGcmd({}))
    _assert_runout_pause(bus, printer, mod, "k2_485", n, bus_ok=False)
    # 7) Fehler IN der Kette (Ersatz A läuft auf 0x0c) -> Pause, RESUME gesperrt
    bus, printer, mod = build_runout()
    ctl = mod.controller
    bus.slots["A"]["filament"] = True
    ctl.get_rfid("B")
    bus.slots["B"]["rfid"] = V_WEISS
    ctl.loaded_slot = "B"
    ctl.tag_cache["B"] = V_WEISS
    bus.slots["B"]["filament"] = False
    bus.slots["C"]["filament"] = False
    bus.fail_after_mm = 50.0
    mod.cmd_runout(FakeGcmd({}))
    st = mod.get_status()
    assert "0x0c" in st["chain_error"] and printer.paused, st
    assert "fehlgeschlagen" in st["runout_last"] and "T1A" in st["runout_last"]
    assert "RESUME" not in printer.gcode.scripts and printer.sensor.enabled
    # 8) Ohne Druck (Handaufruf) -> G-Code-Fehler statt Pause
    bus, printer, mod = build_runout(runout_refill="False")
    printer.paused, printer.print_state = False, "standby"
    printer._refresh()
    try:
        mod.cmd_runout(FakeGcmd({}))
    except ValueError as exc:
        assert "Auto-Nachladen aus" in str(exc), exc
    else:
        raise AssertionError("ohne Druck hätte ein G-Code-Fehler kommen müssen")
    return "aus/ohne Freigabe/Kopf/IDLE/0x0c/Slot/Hub/Einlauf/kein Ersatz/kein Etikett/kein Bus -> Pause ohne Bewegung; Kettenfehler -> Pause+RESUME gesperrt; ohne Druck -> Fehler"


def test_runout_befehle():
    bus, printer, mod = build_runout(runout_refill="False")
    for name in ("KTWO_BOX_RUNOUT", "KTWO_BOX_AUTO_REFILL",
                 "KTWO_BOX_SAME_MATERIAL"):
        assert name in printer.gcode.commands and upstream_gcode_name_ok(name)
    printer.gcode.run_script_from_command("KTWO_BOX_AUTO_REFILL ENABLE=1")
    assert mod.get_status()["auto_refill"] == 1
    printer.gcode.run_script_from_command("KTWO_BOX_SAME_MATERIAL LIST=T1A,T1D")
    assert mod.get_status()["same_material"] == [["T1A", "T1D"]]
    try:
        printer.gcode.run_script_from_command("KTWO_BOX_SAME_MATERIAL LIST=T1A,T2A")
    except ValueError as exc:
        assert "Box 2" in str(exc), exc
    else:
        raise AssertionError("T2A hätte abgelehnt werden müssen")
    # Gruppe A+D: D ohne Filament -> kein Ersatz, obwohl C per RFID passt
    g = FakeGcmd({"DRY_RUN": "1"})
    mod.cmd_runout(g)
    assert any("Gruppe A+D" in m for m in g.infos), g.infos
    bus.slots["D"]["filament"] = True
    bus.slots["D"]["rfid"] = "unknown"   # Gruppe gilt auch ohne Etikett
    g = FakeGcmd({"DRY_RUN": "1"})
    mod.cmd_runout(g)
    assert any("Ersatz T1D" in m for m in g.infos), g.infos
    printer.gcode.run_script_from_command("KTWO_BOX_SAME_MATERIAL CLEAR=1")
    printer.gcode.run_script_from_command("KTWO_BOX_AUTO_REFILL ENABLE=0")
    st = mod.get_status()
    assert st["same_material"] == [] and st["auto_refill"] == 0, st
    # print_slot aus dem 0x0a-Poll im PRINT (Hub-Maske) als Fallback
    bus2, printer2, mod2 = build_hull()
    ctl = mod2._ensure_controller()
    bus2.state, bus2.hub_mask = 2, 0x02
    ctl.poll_status()
    assert ctl.print_slot == "B", ctl.print_slot
    return "AUTO_REFILL/SAME_MATERIAL setzen Status, Gruppen ersetzen RFID-Regel, print_slot aus 0x0a"


def test_runout_config():
    """printer.cfg/k2-module.cfg (Auslieferung): runout_gcode = KTWO_BOX_RUNOUT,
    pause_on_runout True (Upstream pausiert VOR runout_gcode), Auto-Nachladen
    AUS, kein cut_pos_x in [k2_box] (SAVE_CONFIG-Konflikt, A6)."""
    import configparser
    base = os.path.join(_HERE, "config-pi")

    def section(fn, name):
        cp = configparser.RawConfigParser(strict=False,
                                          inline_comment_prefixes=("#",))
        txt = open(os.path.join(base, fn), encoding="utf-8").read()
        txt = txt.split("#*# <---------------------- SAVE_CONFIG")[0]
        cp.read_string(txt)
        return dict(cp.items(name))

    fs = section("printer.cfg", "filament_switch_sensor filament_sensor")
    assert fs["pause_on_runout"].strip() == "True", fs
    assert fs["runout_gcode"].strip() == "KTWO_BOX_RUNOUT", fs
    kb = section("k2-module.cfg", "k2_box")
    assert "cut_pos_x" not in kb, "cut_pos_x in [k2_box] -> SAVE_CONFIG-Konflikt"
    c = K.parse_options(kb)
    assert c["cut_pos_x"] == -7.8 and c["runout_refill"] is False \
        and c["allow_write"] is False and c["allow_move"] is False, c
    return "pause_on_runout True + runout_gcode KTWO_BOX_RUNOUT, runout_refill False, cut_pos_x nur Default -7.80"


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
    ("Wechsel-Bausteine == box.cfg/Live", test_wechsel_bausteine),
    ("Einzug mit Kopfsensor + Stage 7", test_extrude_sensor_stage7),
    ("Rückzug mit Extruder-Haken je p0", test_retrude_after_p0),
    ("Box-Zustand abwarten (BOX_MODE_WAIT)", test_wait_box_ready),
    ("Wechsel-Kette T1B komplett (Freigaben an)", test_wechsel_kette_voll),
    ("Wechsel-Sperren (allow_write/allow_move/Druck)", test_wechsel_sperren),
    ("Wechsel-Fehler im Druck -> Pause", test_wechsel_fehler_im_druck),
    ("Wechsel-Fehler ohne Druck -> G-Code-Fehler", test_wechsel_fehler_ohne_druck),
    ("RESUME-Ziel nach Abbruch am Schneidhebel (PAUSE CFS_RETURN)",
     test_resume_ziel_nach_abbruch),
    ("Schacht vom Hebel nur über X + RESUME_FIX CLEAR",
     test_schacht_aus_schneidbereich_und_resume_fix),
    ("Makros PAUSE/RESUME nach CFS-Abbruch (Schneidbereich/Schacht)",
     test_makros_resume_schneidbereich),
    ("ReactorBusProxy (kein Reactor-Blockieren)", test_reactor_bus_proxy),
    ("Makros T0-T15/M8200/RESUME (macros.cfg)", test_makros_cfg),
    ("Runout: Slot-Wahl-Regel + Optionen", test_runout_slotwahl),
    ("Runout: Nachladen T1A -> T1C + RESUME", test_runout_ablauf),
    ("Runout: Pausenpfade + Sperren", test_runout_pausenpfade),
    ("Runout: AUTO_REFILL/SAME_MATERIAL", test_runout_befehle),
    ("Runout: printer.cfg/k2-module.cfg", test_runout_config),
    ("Python-3.9-Syntax", test_python39),
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
