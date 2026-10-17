#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline-Tests für k2_485.py ([k2_485], gemeinsamer RS485-Bus).

Läuft mit ``python3 test_k2_485.py`` – ohne Klipper, ohne Hardware, ohne
pyserial (Fake-Seriell wird über die echte nachbau/serial485.Serial485 mit
``ser=``-Injektion betrieben). Prüft:

1. Laden: Objekt da, KEIN Port geöffnet, KTWO_BUS_STATUS gültig (Upstream-Regel),
   Connect-/Disconnect-Handler registriert.
2. Connect öffnet GENAU EINE Serial485-Instanz; Motor-Frame byte-genau über
   transact_full und über k2_transport.Rs485Transport (Weg von [k2_motor]).
3. Gemeinsame Instanz: [k2_motor] (Rs485Transport), [k2_box]/[k2_belt]
   (lookup_object('k2_485')) bekommen dasselbe Objekt – ein Port, ein Lock.
4. Batch-Modus (debugoutput): Port wird nie geöffnet, klare Meldung,
   Zugriff -> RuntimeError.
5. Port fehlt (echter Serial485-Pfad, nicht existierender Pfad): Connect ohne
   Absturz, Meldung, get_status open=False; späterer Zugriff öffnet neu.
6. Port fällt im Betrieb aus (OSError) -> RuntimeError, geschlossen, Re-Open;
   TimeoutError (keine Antwort) lässt den Port offen.
7. Python-3.9-Syntax der Module.
8. Sendeschutz Motor-Kalibrierung (17.10.2026, MOTOR-KALIBRIERUNG-BEFUND §12):
   an Motor-Adressen 0x80-0x88/0xFD nie fun 0x03/0x04, fun 0x07 nur Sub 02/06
   – serial485 und lokaler k2_transport-Fallback gleich, über k2_485,
   Rs485Transport und TransparentTransport nichts gesendet; Box/Belt/Rack
   mit denselben fun-Codes unberührt.
"""

import ast
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import k2_485 as B  # noqa: E402
import k2_transport as T  # noqa: E402

S485 = T.import_nachbau("serial485")

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print("%s %s %s" % ("PASS" if cond else "FAIL", name, detail))


# ---------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------

class FakeSerial:
    """Halbduplex-Port: pro write() eine geskriptete Antwort (None = Stille)."""

    def __init__(self, script=(), fail_on_write=False):
        self.script = list(script)
        self.written = []
        self.buf = bytearray()
        self.fail_on_write = fail_on_write
        self.closed = False

    def reset_input_buffer(self):
        self.buf.clear()

    def write(self, frame):
        if self.fail_on_write:
            raise OSError(5, "Input/output error (Adapter gezogen)")
        self.written.append(bytes(frame))
        self.buf.clear()
        if self.script:
            nxt = self.script.pop(0)
            if nxt is not None:
                self.buf += bytes(nxt)
        return len(frame)

    def read(self, n=1):
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def close(self):
        self.closed = True


class Factory:
    def __init__(self, *serials):
        self.serials = list(serials)
        self.calls = []
        self.instances = []

    def __call__(self, port, baud, timeout):
        self.calls.append((port, baud, timeout))
        ser = self.serials.pop(0) if self.serials else FakeSerial()
        inst = S485.Serial485(port, baud, timeout=timeout, ser=ser)
        self.instances.append(inst)
        return inst


class FakeGcode:
    def __init__(self):
        self.commands = {}
        self.infos = []

    def register_command(self, name, func, desc=None):
        self.commands[name] = func

    def respond_info(self, msg):
        self.infos.append(str(msg))


class FakePrinter:
    def __init__(self, debugoutput=None):
        self.gcode = FakeGcode()
        self.objects = {"gcode": self.gcode}
        self.handlers = {}
        self.start_args = {}
        if debugoutput is not None:
            self.start_args["debugoutput"] = debugoutput

    def lookup_object(self, name, default=RuntimeError):
        if name in self.objects:
            return self.objects[name]
        if default is not RuntimeError:
            return default
        raise RuntimeError("Unknown config object '%s'" % name)

    def register_event_handler(self, ev, cb):
        self.handlers.setdefault(ev, []).append(cb)

    def get_start_args(self):
        return self.start_args

    def send(self, ev):
        for cb in self.handlers.get(ev, []):
            cb()

    def load_section(self, mod, config):
        # wie klippy.py load_object: Rückgabe unter dem Abschnittsnamen
        self.objects[config.get_name()] = mod.load_config(config)
        return self.objects[config.get_name()]


class FakeConfig:
    def __init__(self, printer, opts, name="k2_485"):
        self.printer = printer
        self.opts = dict(opts)
        self.name = name

    def get_printer(self):
        return self.printer

    def get_name(self):
        return self.name

    def get(self, key, default=RuntimeError):
        if key in self.opts:
            return self.opts[key]
        if default is RuntimeError:
            raise ValueError("Option '%s' in section '%s' must be specified"
                             % (key, self.name))
        return default

    def getint(self, key, default=None, minval=None, maxval=None):
        v = int(self.get(key, default))
        if minval is not None and v < minval:
            raise ValueError("minval")
        return v

    def getfloat(self, key, default=None, minval=None, maxval=None,
                 above=None, below=None):
        v = float(self.get(key, default))
        if above is not None and v <= above:
            raise ValueError("above")
        return v

    def error(self, msg):
        return ValueError(msg)


def upstream_gcode_name_ok(cmd):
    """Nachbau der Namensprüfung aus Upstream gcode.py register_command."""
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


PORT = "/dev/serial/by-id/usb-TEST_RS485-if00-port0"   # Platzhalter
# Motor X (0x81) Version 0x0F [0x01] -> Antwort mit 4 Datenbytes (Fake-Werte)
REQ = bytes(S485.build_frame(0x81, 0x0F, [0x01]))
RESP = bytes(S485.build_frame(0x81, 0x0F, [0x01, 0x09, 0x07, 0x51]))


def make(debugoutput=None, factory=None, opts=None):
    B.SERIAL_FACTORY = factory
    pr = FakePrinter(debugoutput=debugoutput)
    o = {"serial": PORT, "baud": "230400"}
    o.update(opts or {})
    obj = pr.load_section(B, FakeConfig(pr, o))
    return pr, obj


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------

def test_laden_und_connect():
    fac = Factory(FakeSerial([RESP]))
    pr, bus = make(factory=fac)
    check("Laden: Objekt 'k2_485' registriert", pr.lookup_object("k2_485") is bus)
    check("Laden: kein Port geöffnet", not bus.is_open() and fac.calls == [])
    check("Laden: KTWO_BUS_STATUS gültiger Upstream-Name",
          list(pr.gcode.commands) == ["KTWO_BUS_STATUS"]
          and upstream_gcode_name_ok("KTWO_BUS_STATUS")
          and not upstream_gcode_name_ok("K2_BUS_STATUS")
          and not upstream_gcode_name_ok("KTWO_485_STATUS"))
    check("Laden: connect/disconnect-Handler",
          "klippy:connect" in pr.handlers and "klippy:disconnect" in pr.handlers)
    pr.send("klippy:connect")
    check("Connect: genau eine Serial485-Instanz offen",
          bus.is_open() and len(fac.instances) == 1
          and fac.calls == [(PORT, 230400, 1.0)], fac.calls)
    check("Connect: Meldung 'offen'", any("offen" in i for i in pr.gcode.infos),
          pr.gcode.infos)
    addr, st, fun, data, _raw = bus.transact_full(0x81, 0x0F, [0x01], timeout=0.2,
                                                  retries=0)
    ser = fac.instances[0].ser
    check("transact_full: Request byte-genau", ser.written == [REQ],
          [w.hex(" ") for w in ser.written])
    check("transact_full: Antwort geparst",
          (addr, st, fun, bytes(data)) == (0x81, 0, 0x0F, bytes([1, 9, 7, 0x51])))
    # Weg von [k2_motor]: Rs485Transport über dasselbe Objekt
    ser.script.append(RESP)
    tr = T.Rs485Transport(pr.lookup_object("k2_485"), name="k2_485")
    res = tr.transact(0x81, 0x0F, [0x01], timeout=0.2, retries=0)
    check("Rs485Transport über k2_485", res == (0, 0x0F, bytes([1, 9, 7, 0x51])), res)
    check("eine Instanz für alle (kein zweites Öffnen)", len(fac.calls) == 1)
    st = bus.get_status()
    check("get_status/stats", st["open"] and st["stats"].get("sent") == 2, st)
    g = type("G", (), {"infos": [], "respond_info": lambda self, m: self.infos.append(m)})()
    pr.gcode.commands["KTWO_BUS_STATUS"](g)
    check("KTWO_BUS_STATUS ohne Bus-Verkehr", "offen=True" in g.infos[0]
          and len(ser.written) == 2, g.infos)
    pr.send("klippy:disconnect")
    check("Disconnect schließt", not bus.is_open())


def test_gemeinsam_mit_modulen():
    """[k2_motor]/[k2_box]/[k2_belt] holen dasselbe Objekt."""
    fac = Factory(FakeSerial())
    pr, bus = make(factory=fac)
    import k2_box as KBOX  # noqa: F401  (Import ohne sys.path-Manipulation)
    import k2_belt as KBELT
    import k2_motor_init as KM
    found = []
    for name in KBELT.BUS_OBJECT_NAMES[:1] + ("k2_485",):
        found.append(pr.lookup_object(name))
    check("Belt/Box-Suchname 'k2_485' liefert dieselbe Instanz",
          KBELT.BUS_OBJECT_NAMES[0] == "k2_485" and all(f is bus for f in found))
    check("k2_motor BUS_SECTION = 'k2_485'", KM.BUS_SECTION == "k2_485")
    ext = [p for p in sys.path if os.path.realpath(p) == os.path.realpath(_HERE)]
    check("Modulordner nicht von den Modulen in sys.path eingefügt",
          len(ext) == 1, ext)
    nb = [p for p in sys.path if p and os.path.realpath(p) == T.NACHBAU_DIR]
    check("nachbau/ angehängt (nicht vorne)",
          nb and sys.path.index(nb[0]) > 0, (T.NACHBAU_DIR, sys.path[:3]))
    # Belt am Bus ohne nutzbaren Port -> BeltError (G-Code-Fehler), kein
    # RuntimeError durch die Hülle (sonst Klipper-Shutdown, Batch-Probe G).
    B.SERIAL_FACTORY = None
    pr2, bus2 = make(debugoutput="/tmp/x.serial")
    ctl = KBELT.K2BeltController("mdlx", KBELT.parse_options({}, "mdlx"), bus=bus2,
                                 pause=lambda s: None, respond=lambda m: None,
                                 run_gcode=lambda l: None)
    try:
        ctl.read_version()
        err = None
    except Exception as exc:
        err = exc
    check("k2_belt: Bus-Fehler -> BeltError", isinstance(err, KBELT.BeltError)
          and "Batch-Modus" in str(err), repr(err))
    import statistics
    check("import statistics = Standardbibliothek",
          hasattr(statistics, "median")
          and "extras" not in os.path.dirname(statistics.__file__))


def test_batch():
    fac = Factory()
    pr, bus = make(debugoutput="/tmp/out.serial", factory=fac)
    pr.send("klippy:connect")
    check("Batch: Port nie geöffnet", not bus.is_open() and fac.calls == [])
    check("Batch: Meldung", any("Batch-Modus" in i for i in pr.gcode.infos),
          pr.gcode.infos)
    try:
        bus.transact_full(0x81, 0x0F, [1])
    except RuntimeError as exc:
        check("Batch: Zugriff -> RuntimeError mit Grund", "Batch-Modus" in str(exc),
              str(exc))
    else:
        check("Batch: Zugriff -> RuntimeError mit Grund", False)


def test_port_fehlt():
    pr, bus = make(factory=None,
                   opts={"serial": "/dev/serial/by-id/usb-GIBT-ES-NICHT-if00"})
    try:
        pr.send("klippy:connect")
        crashed = None
    except BaseException as exc:  # auch SystemExit (pyserial fehlt)
        crashed = exc
    check("Port fehlt: Connect ohne Absturz", crashed is None, repr(crashed))
    check("Port fehlt: Meldung", any("nicht zu öffnen" in i for i in pr.gcode.infos),
          pr.gcode.infos)
    st = bus.get_status()
    check("Port fehlt: get_status open=False + error",
          st["open"] is False and st["error"], st["error"])
    try:
        bus.transact(0x81, 0x0F, [1], timeout=0.05, retries=0)
    except RuntimeError as exc:
        check("Port fehlt: Zugriff -> RuntimeError", "nicht zu öffnen" in str(exc))
    else:
        check("Port fehlt: Zugriff -> RuntimeError", False)
    # Adapter nachträglich da -> nächster Zugriff öffnet
    fac = Factory(FakeSerial([RESP]))
    B.SERIAL_FACTORY = fac
    data = bus.transact(0x81, 0x0F, [1], timeout=0.2, retries=0)
    check("Port später da: Re-Open beim Zugriff",
          bytes(data) == bytes([1, 9, 7, 0x51]) and bus.is_open()
          and bus.last_error is None)


def test_ausfall_timeout():
    fac = Factory(FakeSerial([None]), FakeSerial(fail_on_write=True), FakeSerial([RESP]))
    pr, bus = make(factory=fac)
    pr.send("klippy:connect")
    try:
        bus.transact(0x81, 0x0F, [1], timeout=0.05, retries=0)
        ok = False
    except TimeoutError:
        ok = True
    check("TimeoutError durchgereicht, Port bleibt offen", ok and bus.is_open())
    bus.close()
    try:
        bus.transact(0x81, 0x0F, [1], timeout=0.05, retries=0)
        msg = ""
    except RuntimeError as exc:
        msg = str(exc)
    check("OSError -> RuntimeError 'ausgefallen', geschlossen",
          "ausgefallen" in msg and not bus.is_open(), msg)
    data = bus.transact(0x81, 0x0F, [1], timeout=0.2, retries=0)
    check("danach Re-Open", bytes(data) == bytes([1, 9, 7, 0x51])
          and len(fac.calls) == 3)


def test_config_fehler():
    B.SERIAL_FACTORY = Factory()
    pr = FakePrinter()
    try:
        pr.load_section(B, FakeConfig(pr, {"baud": "230400"}))
        msg = ""
    except ValueError as exc:
        msg = str(exc)
    check("serial: fehlt -> Config-Fehler", "serial" in msg, msg)


def test_py39():
    bad = []
    for f in ("k2_485.py", "k2_transport.py", "k2_motor_init.py", "k2_box.py",
              "k2_belt.py", "k2_prtouch.py", "test_k2_485.py"):
        src = open(os.path.join(_HERE, f), encoding="utf-8").read()
        try:
            ast.parse(src, filename=f, feature_version=(3, 9))
        except SyntaxError as exc:
            bad.append("%s: %s" % (f, exc))
    check("Python-3.9-Syntax", not bad, bad)


class _FakeTransparent(T.TransparentMcuAdapter):
    """Zeichnet transparent-Sendungen auf, antwortet nie (b'')."""

    def __init__(self):
        self.writes = []

    def exchange(self, write, timeout_ms):
        self.writes.append(bytes(write))
        return b""


def _motor_guard_cases():
    """(addr, fun, data, erlaubt) – Motor gesperrt/erlaubt, Box/Belt frei."""
    cases = []
    for a in list(range(0x80, 0x89)) + [0xFD]:
        cases += [(a, 0x03, [], False), (a, 0x03, [0x00], False),
                  (a, 0x04, [0x00], False), (a, 0x04, [], False),
                  (a, 0x07, [], False), (a, 0x07, [0x00], False),
                  (a, 0x07, [0x01], False), (a, 0x07, [0x03, 0x00], False),
                  (a, 0x07, [0x04, 0x00], False),
                  (a, 0x07, [0x05, 0x14, 0, 0, 0x80, 0x3F], False),
                  (a, 0x07, [0x07], False),
                  (a, 0x07, [0x02], True), (a, 0x07, [0x06, 0x14], True)]
    cases += [(0x81, 0x06, [0x02, 0x46], True), (0x81, 0x0F, [0x01], True),
              (0x81, 0x05, [0x02], True), (0x81, 0x0E, [0x02], True)]
    # Box (0x01-0x04, Gruppe 0xFE): 0x03 Restmenge, 0x04 Modus, 0x07 Hauptmotor
    for a in (0x01, 0x02, 0x03, 0x04, 0xFE):
        cases += [(a, 0x03, [0x0F], True), (a, 0x04, [0x00, 0x01], True),
                  (a, 0x04, [0x0F, 0x00], True), (a, 0x07, [0x01], True),
                  (a, 0x07, [0x02], True), (a, 0x07, [0x00], True)]
    # Belt (0x21/0x22): 0x04 WRITE_FLASH, 0x02 READ_FLASH; Rack 0x11; 0xFF/0xFC
    for a in (0x21, 0x22):
        cases += [(a, 0x04, [0x02, 0, 0, 0, 0x8C], True), (a, 0x02, [0x02], True)]
    cases += [(0x11, 0x03, [0x01], True), (0xFF, 0x03, [], True),
              (0xFF, 0x04, [0x00], True), (0xFF, 0x07, [0x05], True),
              (0xFC, 0x04, [0x00], True)]
    return cases


def test_motor_sendeschutz():
    cases = _motor_guard_cases()
    check("Sendeschutz: k2_transport nutzt serial485.check_tx_frame",
          T.check_tx_frame is S485.check_tx_frame
          and T.TxGuardError is S485.TxGuardError
          and hasattr(S485, "TX_MOTOR_GUARD_ADDRS"))
    check("Sendeschutz: Motor-Adressen identisch (serial485 = lokal)",
          S485.TX_MOTOR_GUARD_ADDRS == T.TX_MOTOR_GUARD_ADDRS
          == frozenset(list(range(0x80, 0x89)) + [0xFD])
          and S485.TX_FLASH_PARAM_READ_SUBS == T.TX_FLASH_PARAM_READ_SUBS
          == frozenset((0x02, 0x06)))
    for name, fn in (("serial485", S485.check_tx_frame),
                     ("lokal", T._check_tx_frame_local)):
        wrong = []
        for addr, fun, data, ok in cases:
            try:
                fn(addr, fun, data)
                got = True
            except ValueError:
                got = False
            if got != ok:
                wrong.append("0x%02X/0x%02X %s" % (addr, fun, data))
        check("Sendeschutz Matrix %s (%d Fälle)" % (name, len(cases)),
              not wrong, wrong[:5])
    try:
        S485.check_tx_frame(0x83, 0x04, [0x00])
        msg = ""
    except S485.TxGuardError as exc:
        msg = str(exc)
    check("Sendeschutz: Meldung nennt elec_offset + nichts gesendet",
          "elec_offset" in msg and "nichts gesendet" in msg, msg)

    # über [k2_485] (echte Serial485 + Fake-Port): gesperrt -> nichts auf dem Draht
    fac = Factory(FakeSerial([None] * 8))
    pr, bus = make(factory=fac)
    pr.send("klippy:connect")
    ser = fac.instances[0].ser
    blocked = [c[:3] for c in cases if not c[3]]
    nraise = 0
    for addr, fun, data in blocked:
        try:
            bus.transact_full(addr, fun, data, timeout=0.01, retries=0)
        except S485.TxGuardError:
            nraise += 1
        except Exception:
            pass
    check("k2_485: alle gesperrten Motor-Frames -> TxGuardError, Draht leer",
          nraise == len(blocked) and ser.written == [], (nraise, len(blocked)))
    # Box/Belt über denselben Bus: unverändert byte-genau gesendet
    import k2_belt as KBELT
    box_req = (0x01, 0x04, [0x00, 0x01])
    belt_req = (KBELT.ADDR_MDLX, KBELT.FUN_WRITE_FLASH, [0x02, 0, 0, 0, 0x8C])
    for addr, fun, data in (box_req, belt_req, (0x81, 0x07, [0x06, 0x14])):
        try:
            bus.transact_full(addr, fun, data, timeout=0.01, retries=0)
        except TimeoutError:
            pass
    check("k2_485: Box 0x04 / Belt WRITE_FLASH / Motor 0x07 [06] gesendet",
          ser.written[:1] == [bytes(S485.build_frame(*box_req))]
          and ser.written[1:2] == [bytes(S485.build_frame(*belt_req))]
          and ser.written[2:3] == [bytes(S485.build_frame(0x81, 0x07, [0x06, 0x14]))],
          [w.hex(" ") for w in ser.written])
    pr.send("klippy:disconnect")

    # Rs485Transport (Weg von [k2_motor]) und TransparentTransport (Extruder 0x81)
    class _RecBus:
        def __init__(self):
            self.calls = []

        def transact_full(self, addr, fun, data, timeout=None, retries=0):
            self.calls.append((addr, fun, list(data)))
            raise TimeoutError("still")
    rb = _RecBus()
    rs = T.Rs485Transport(rb)
    mcu = _FakeTransparent()
    tr = T.TransparentTransport(mcu)
    nr = nt = 0
    for addr, fun, data in blocked:
        try:
            rs.transact(addr, fun, data, timeout=0.01, retries=0)
        except T.TxGuardError:
            nr += 1
        except TimeoutError:
            pass
        try:
            tr.transact(addr, fun, data, timeout=0.01, retries=0)
        except T.TxGuardError:
            nt += 1
        except TimeoutError:
            pass
    check("Rs485Transport/TransparentTransport: gesperrt, nichts gesendet",
          nr == nt == len(blocked) and rb.calls == [] and rs.sent == []
          and mcu.writes == [] and tr.sent == [], (nr, nt, rb.calls, mcu.writes))
    for t in (rs, tr):
        try:
            t.transact(0x81, 0x07, [0x02], timeout=0.01, retries=0)
        except TimeoutError:
            pass
    check("Rs485/transparent: Motor 0x07 [02] (lesend) erlaubt",
          rb.calls == [(0x81, 0x07, [0x02])]
          and mcu.writes == [bytes(S485.build_frame(0x81, 0x07, [0x02]))],
          (rb.calls, mcu.writes))


def main():
    for fn in (test_laden_und_connect, test_gemeinsam_mit_modulen, test_batch,
               test_port_fehlt, test_ausfall_timeout, test_config_fehler,
               test_py39, test_motor_sendeschutz):
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            check(fn.__name__ + " (Ausnahme)", False, repr(exc))
    B.SERIAL_FACTORY = None
    fails = [n for n, ok in RESULTS if not ok]
    print("ERGEBNIS: %s (%d/%d Prüfungen ok)" % (
        "ok" if not fails else "%d FEHLER" % len(fails),
        len(RESULTS) - len(fails), len(RESULTS)))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
