#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""emu_prtouch_stock.py – lädt Crealitys prtouch_v3_wrapper.so OHNE Hardware.

Läuft auf dem Drucker mit /usr/share/klippy-env/bin/python, in einem eigenen
Prozess (das laufende Klipper bleibt unberührt). Alle Klipper-Objekte sind
Attrappen, die jeden Aufruf protokollieren. Ziel: die exakten MCU-
Konfigurations-/Startbefehle mitlesen (Pin-Reihenfolge clk/sdo, Skalierungen,
Antastablauf probe_prepare/home_start/probe_finish; s. PRTOUCH-LESETEST-BEFUND §1/§7).
Kein Zugriff auf Seriell-Ports, MCU oder Bewegung.
"""
import configparser
import sys
import traceback

sys.path.insert(0, "/usr/share/klipper/klippy")
LOG = []


def log(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s)
    print(s)


class Rec:
    """Universal-Attrappe: jeder Attributzugriff/Aufruf wird protokolliert."""
    def __init__(self, name):
        self._n = name

    def __getattr__(self, k):
        if k.startswith("__"):
            raise AttributeError(k)
        return Rec(self._n + "." + k)

    def __call__(self, *a, **kw):
        log("CALL", self._n, a, kw)
        return Rec(self._n + "()")

    def __int__(self):
        return 0
    __index__ = __int__

    def __float__(self):
        return 0.0

    def __bool__(self):
        return True

    def __iter__(self):
        return iter([])

    def __str__(self):
        return "<%s>" % self._n
    __repr__ = __str__

    def __lt__(self, o):
        return False
    __gt__ = __le__ = __ge__ = __lt__

    def __eq__(self, o):
        return o is self

    def __hash__(self):
        return id(self)

    def __add__(self, o):
        return 0.0
    __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = __truediv__ = __add__


import struct as _st


ACKS = []
RESP_CB = {}


class FakeQuery:
    def __init__(self, mcu, fmt, resp):
        self.mcu, self.fmt, self.resp = mcu, fmt, resp
        self.name = fmt.split()[0]

    def send(self, params=None, minclock=0, reqclock=0, retry=True):
        log("SEND", self.mcu, self.name, params)
        r = self.resp.split()[0] if self.resp else ""
        if r == "ack_prtouch":
            a = ACKS.pop(0) if ACKS else (0, 0, 0)
            return {"oid": 1, "err": a[0], "expar0": a[1], "expar1": a[2], "#name": r}
        if r == "resault_prtouch_pres":
            n = 8
            return {"oid": 1, "tri_chxs": 0, "buf_len": 64, "ch": 0, "idx": 0, "len": n,
                    "ticks": b"".join(_st.pack("<I", 1000 * i) for i in range(n)),
                    "datas": b"".join(_st.pack("<i", 50000 + i) for i in range(n)), "#name": r}
        if r == "resault_prtouch_step":
            n = 8
            return {"oid": 1, "buf_len": 64, "idx": 0, "len": n,
                    "ticks": b"".join(_st.pack("<I", 1000 * i) for i in range(n)),
                    "datas": b"".join(_st.pack("<i", i) for i in range(n)), "#name": r}
        if r == "resault_prtouch_step_cnt":
            return {"oid": 3, "cnt_x": 0, "cnt_y": 0, "cnt_z": 0, "#name": r}
        if r == "endstop_state":
            return {"oid": 1, "homing": 0, "next_clock": 0, "pin_value": 1, "#name": r}
        if r == "trsync_state":
            return {"oid": 2, "can_trigger": 1, "trigger_reason": 0, "clock": 0, "#name": r}
        log("   (keine Attrappen-Antwort fuer", r, ")")
        return {"#name": r}

    def send_with_preface(self, *a, **kw):
        return self.send(*a[1:2] if len(a) > 1 else None)

    def __getattr__(self, k):
        if k.startswith("__"):
            raise AttributeError(k)
        return Rec("q[%s].%s" % (self.name, k))


class FakeCmd:
    def __init__(self, mcu, fmt):
        self.mcu, self.name = mcu, fmt.split()[0]

    def send(self, params=None, minclock=0, reqclock=0):
        log("SEND", self.mcu, self.name, params)

    def send_wait_ack(self, params=None, minclock=0, reqclock=0):
        log("SEND+ACK", self.mcu, self.name, params)

    def get_command_tag(self):
        return 1


class FakeMcu:
    def __init__(self, name):
        self.name = name
        self.oid = 0
        self.cbs = []

    def get_name(self):
        return self.name

    def create_oid(self):
        self.oid += 1
        log("MCU", self.name, "create_oid ->", self.oid)
        return self.oid

    def add_config_cmd(self, cmd, is_init=False, on_restart=False):
        log("CFG", self.name, cmd, "is_init=%s" % is_init)

    def register_config_callback(self, cb):
        log("MCU", self.name, "register_config_callback", cb)
        self.cbs.append(cb)

    def lookup_query_command(self, fmt, resp, oid=None, cq=None, is_async=False):
        log("MCU", self.name, "lookup_query_command", fmt, "|", resp, "oid=", oid)
        return FakeQuery(self.name, fmt, resp)

    def lookup_command(self, fmt, cq=None):
        log("MCU", self.name, "lookup_command", fmt)
        return FakeCmd(self.name, fmt)

    def lookup_command_tag(self, fmt):
        return 7

    def get_query_slot(self, oid):
        return 0

    def is_fileoutput(self):
        return False

    def register_stepqueue(self, sq):
        pass

    def request_move_queue_slot(self):
        pass

    def try_lookup_command(self, fmt, cq=None):
        return self.lookup_command(fmt, cq)

    def register_response(self, cb, msg, oid=None):
        log("MCU", self.name, "register_response", msg, "oid=", oid)
        RESP_CB[(self.name, msg)] = cb

    def alloc_command_queue(self):
        return Rec("cq")

    def get_constants(self):
        return {"CLOCK_FREQ": 120000000}

    def get_constant_float(self, n):
        return float(self.get_constants().get(n, 0))

    def seconds_to_clock(self, t):
        return int(t * self.get_constants()["CLOCK_FREQ"])

    def print_time_to_clock(self, t):
        return self.seconds_to_clock(t)

    def estimated_print_time(self, t):
        return t

    def get_printer(self):
        return PRINTER

    def clock32_to_clock64(self, c):
        log("C32", self.name, c, hex(c))
        return c

    def clock_to_print_time(self, c):
        return c / float(self.get_constants()["CLOCK_FREQ"])

    def setup_pin(self, ptype, params):
        log("MCU", self.name, "setup_pin", ptype, params.get("pin"), "invert=%s" % params.get("invert"))
        return Rec("pin[%s:%s]" % (self.name, params.get("pin")))

    def __getattr__(self, k):
        if k.startswith("__"):
            raise AttributeError(k)
        return Rec("mcu[%s].%s" % (self.name, k))


MCUS = {"mcu": FakeMcu("mcu"), "nozzle_mcu": FakeMcu("nozzle_mcu")}


class FakePins:
    def lookup_pin(self, desc, can_invert=False, can_pullup=False, share_type=None):
        d = desc.strip()
        inv = d.startswith("!")
        d = d.lstrip("!^~ ")
        chip, _, pin = d.rpartition(":")
        chip = chip.strip() or "mcu"
        log("PINS lookup_pin", repr(desc), "->", chip, pin.strip(), "invert=%s" % inv)
        return {"chip": MCUS[chip], "chip_name": chip, "pin": pin.strip(),
                "invert": int(inv), "pullup": 0}

    def setup_pin(self, ptype, desc):
        log("PINS setup_pin", ptype, desc)
        return Rec("pin[%s]" % desc)

    def __getattr__(self, k):
        if k.startswith("__"):
            raise AttributeError(k)
        return Rec("pins." + k)


class FakeReactor(Rec):
    N = [0]

    def monotonic(self):
        return 100.0

    def pause(self, t):
        self.N[0] += 1
        n = self.N[0]
        cb = RESP_CB.get(("nozzle_mcu", "resault_prtouch_pres"))
        if cb and n <= 8:
            k = 16
            base = (n - 1) * k
            tb = bytes((base * 4 + j) & 0xFF for j in range(4 * k))
            db = bytes((0x80 + base * 4 + j) & 0xFF for j in range(4 * k))
            log("INJECT ticks", tb.hex())
            log("INJECT datas", db.hex())
            prm = {"oid": 1, "tri_chxs": 0, "buf_len": 64, "ch": 0, "idx": base % 256, "len": k,
                   "ticks": tb,
                   "datas": db,
                   "#name": "resault_prtouch_pres", "#sent_time": 1.0, "#receive_time": 1.0}
            log("INJECT resault_prtouch_pres idx=%d len=%d" % (base, k))
            cb(prm)
        if n > 60:
            raise RuntimeError("Abbruch: pause-Schleife")
        return t

    NOW = 0.0
    NEVER = 9e99


def _real_pins():
    import pins as P
    pp = P.PrinterPins()
    for n, m in MCUS.items():
        pp.register_chip(n, m)
    return pp


class FakePrinter:
    def __init__(self):
        self.objs = {"pins": _real_pins()}
        self.handlers = {}

    def lookup_object(self, name, default=configparser.Error):
        if name in self.objs:
            return self.objs[name]
        if name in MCUS:
            return MCUS[name]
        if name.startswith("mcu "):
            return MCUS[name[4:]]
        log("PRINTER lookup_object", name)
        o = Rec("obj[%s]" % name)
        self.objs[name] = o
        return o

    def add_object(self, name, obj):
        log("PRINTER add_object", name)
        self.objs[name] = obj

    def register_event_handler(self, ev, cb):
        log("PRINTER event_handler", ev, cb)
        self.handlers.setdefault(ev, []).append(cb)

    def get_reactor(self):
        return FakeReactor("reactor")

    def lookup_objects(self, module=None):
        return []

    SD = [0]

    def is_shutdown(self):
        self.SD[0] += 1
        n = self.SD[0]
        cb = None
        if cb and n <= 6:
            k = 32
            base = (n - 1) * k
            cb({"oid": 1, "tri_chxs": 0, "buf_len": 64, "ch": 0, "idx": base % 256, "len": k,
                "ticks": b"".join(_st.pack("<I", 1000 * (base + i)) for i in range(k)),
                "datas": b"".join(_st.pack("<i", -1427000 - (base + i)) for i in range(k)),
                "#name": "resault_prtouch_pres", "#sent_time": 1.0, "#receive_time": 1.0})
        return n > 400

    def __getattr__(self, k):
        if k.startswith("__"):
            raise AttributeError(k)
        return Rec("printer." + k)


class FakeStepper:
    def __init__(self, name, oid, sd):
        self.n, self.o, self.sd = name, oid, sd

    def get_name(self, short=False):
        return self.n

    def get_oid(self):
        return self.o

    def get_step_dist(self):
        return self.sd

    def is_active_axis(self, a):
        return a in self.n[-2:]

    def get_mcu(self):
        return MCUS["mcu"]

    def __getattr__(self, k):
        if k.startswith("__"):
            raise AttributeError(k)
        return Rec("stepper[%s].%s" % (self.n, k))


STEPPERS = [FakeStepper("stepper_x", 11, 0.0125), FakeStepper("stepper_y", 12, 0.0125),
            FakeStepper("stepper_z", 13, 0.0025), FakeStepper("stepper_z1", 14, 0.0025)]


class FakeKin(Rec):
    def get_steppers(self):
        return STEPPERS


class FakeToolhead(Rec):
    def get_kinematics(self):
        return FakeKin("kin")

    def get_position(self):
        return [175.0, 175.0, 10.0, 0.0]

    def get_last_move_time(self):
        return 1.0


class FakeGcmd:
    def __init__(self, cmd, params=None):
        self.cmd, self.p = cmd, dict(params or {})

    def get(self, k, default=None, **kw):
        return self.p.get(k, default)

    def get_int(self, k, default=None, **kw):
        v = self.p.get(k, default)
        return int(v) if v is not None else v

    def get_float(self, k, default=None, **kw):
        v = self.p.get(k, default)
        return float(v) if v is not None else v

    def get_command_parameters(self):
        return self.p

    def respond_info(self, msg, log_=True):
        log("GCMD-INFO", msg)

    def respond_raw(self, msg):
        log("GCMD-RAW", msg)

    def error(self, msg):
        return Exception(msg)

    def get_commandline(self):
        return self.cmd


PRINTER = FakePrinter()
PRINTER.objs["toolhead"] = FakeToolhead("toolhead")

CP = configparser.RawConfigParser(strict=False, inline_comment_prefixes=("#", ";"))
txt = open("/mnt/UDISK/printer_data/config/printer.cfg").read().split("#*# <----")[0]
CP.read_string(txt)


class FakeConfig:
    error = configparser.Error

    def __init__(self, sec):
        self.sec = sec

    def get_name(self):
        return self.sec

    def get_printer(self):
        return PRINTER

    def _raw(self, k, default):
        if CP.has_option(self.sec, k):
            v = CP.get(self.sec, k).strip()
            log("CONF", self.sec, k, "=", v)
            return v
        log("CONF", self.sec, k, "(default)", default)
        return default

    def get(self, k, default=configparser.Error, note_valid=True):
        v = self._raw(k, default)
        if v is configparser.Error:
            raise configparser.Error("fehlt: %s" % k)
        return v

    def getint(self, k, default=configparser.Error, minval=None, maxval=None, note_valid=True):
        v = self.get(k, default)
        return int(v) if isinstance(v, str) else v

    def getfloat(self, k, default=configparser.Error, minval=None, maxval=None,
                 above=None, below=None, note_valid=True):
        v = self.get(k, default)
        return float(v) if isinstance(v, str) else v

    def getboolean(self, k, default=configparser.Error, note_valid=True):
        v = self.get(k, default)
        return v.lower() in ("1", "true", "yes", "on") if isinstance(v, str) else v

    def getchoice(self, k, choices, default=configparser.Error, note_valid=True):
        v = self.get(k, default)
        return choices[v] if isinstance(choices, dict) and v in choices else v

    def getlists(self, k, default=configparser.Error, seps=(",",), count=None,
                 parser=str, note_valid=True):
        v = self.get(k, default)
        if not isinstance(v, str):
            return v
        out = [parser(x.strip()) for x in v.split(seps[-1]) if x.strip()]
        return tuple(out)

    def getlist(self, k, default=configparser.Error, sep=",", count=None, note_valid=True):
        return self.getlists(k, default, seps=(sep,), count=count)

    def getintlist(self, k, default=configparser.Error, sep=",", count=None, note_valid=True):
        return self.getlists(k, default, seps=(sep,), count=count, parser=int)

    def getfloatlist(self, k, default=configparser.Error, sep=",", count=None, note_valid=True):
        return self.getlists(k, default, seps=(sep,), count=count, parser=float)

    def getsection(self, s):
        return FakeConfig(s)

    def has_section(self, s):
        return CP.has_section(s)

    def get_prefix_sections(self, p):
        return [FakeConfig(s) for s in CP.sections() if s.startswith(p)]

    def get_prefix_options(self, p):
        return [o for o in CP.options(self.sec) if o.startswith(p)]

    def deprecate(self, *a):
        pass


def main():
    import extras.prtouch_v3_wrapper as W
    log("== import ok:", [n for n in dir(W) if not n.startswith("__")][:40])
    cfg = FakeConfig("prtouch_v3")
    try:
        obj = W.PRTouchEndstopWrapper(cfg)
        log("== Konstruktor ok:", type(obj))
    except Exception:
        log("== Konstruktor-FEHLER:\n" + traceback.format_exc())
        obj = None
    for name, m in MCUS.items():
        for cb in m.cbs:
            log("== config_callback", name, cb)
            try:
                cb()
            except Exception:
                log("   FEHLER:\n" + traceback.format_exc())
    for ev in ("klippy:mcu_identify", "klippy:connect", "klippy:ready"):
        for cb in PRINTER.handlers.get(ev, []):
            log("== event", ev, cb)
            try:
                cb()
            except Exception:
                log("   FEHLER:\n" + traceback.format_exc(limit=3))
    if obj is not None:
        pres = getattr(obj, "pres", None) or getattr(obj, "prtouch_pres", None)
        for an in sorted(a for a in dir(obj) if not a.startswith("__")):
            v = getattr(obj, an, None)
            if type(v).__name__ in ("PrtouchPres", "PrtouchStep", "PrtouchApax"):
                log("== Teilobjekt", an, type(v).__name__)
        for an in ("pres", "step"):
            o = getattr(obj, an)
            log("== Methoden", an, sorted(a for a in dir(o) if not a.startswith("__")))
        runs = []
        import mcu as _mcu
        _mcu.MCU_trsync.start = lambda self, *a, **k: log("TRSYNC start", a)
        _mcu.MCU_trsync.add_stepper = lambda self, *a, **k: log("TRSYNC add_stepper", a)
        class FakeHmove(Rec):
            def get_mcu_endstops(self):
                return [obj]
        hm = FakeHmove("hmove")
        ACKS[:] = [(0, 64, 0)] * 20
        for name, fn in (("multi_probe_begin", lambda: obj.multi_probe_begin()),
                         ("probe_prepare", lambda: obj.probe_prepare(hm)),
                         ("home_start", lambda: obj.home_start(1.0, 0.000015, 4, 0.0001, True)),
                         ("home_wait", lambda: obj.home_wait(2.0)),
                         ("probe_finish", lambda: obj.probe_finish(hm)),
                         ("multi_probe_end", lambda: obj.multi_probe_end()),
                         ("query_endstop", lambda: obj.query_endstop(1.0))):
            log("== RUN", name)
            try:
                r = fn()
                log("   -> Rueckgabe", repr(r)[:200])
            except Exception:
                log("   FEHLER:\n" + traceback.format_exc(limit=6))
        runs = []
        for target, cmd, params, acks in runs:
            ACKS[:] = list(acks)
            log("== ACKS", acks)
            o = obj
            if target == "pres":
                o = next((getattr(obj, a) for a in dir(obj)
                          if type(getattr(obj, a, None)).__name__ == "PrtouchPres"), None)
            if o is None or not hasattr(o, cmd):
                log("== kein", target, cmd)
                continue
            log("== RUN", cmd)
            try:
                getattr(o, cmd)(FakeGcmd(cmd.replace("cmd_", ""), params))
            except Exception:
                log("   FEHLER:\n" + traceback.format_exc(limit=4))
        log("== Attribute:", sorted(a for a in dir(obj) if not a.startswith("__"))[:200])
    open("/tmp/emu_prtouch.log", "w").write("\n".join(LOG))


main()
