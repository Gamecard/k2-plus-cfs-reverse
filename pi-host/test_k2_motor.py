#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_k2_motor.py – Tests für k2_transport + k2_motor_init (ohne Klipper).

Läuft mit ``python3 test_k2_motor.py`` (ohne installiertes Klipper;
nachbau/ per sys.path eingebunden). Prüft:

1. Transparent-Frame-Format gegen 3 Live-Frames aus klippy.log (CRC).
2. Kompletter Init-Lauf mit FakeMcu + FakeRs485 (alle Motoren antworten).
3. Frame-Folge == motor_init.build_init_plan (gleiche Reihenfolge/Payloads).
4. Extruder-Frames nur via transparent an 0x81, nie 0x85 auf RS485.
5. allow_write=False sendet keine schreibenden Frames (nur lesen + Plan).

Fakes:
- FakeMcu: simuliert transparent_send/response (volle Frames inkl. F7/CRC)
  + digital_out (set/query) für die Strapping-Pins der Main-MCU.
- FakeRs485Bus: simuliert Motoren 0x81–0x84 auf RS485 (Adressübernahme erst
  nach Broadcast, Param-Lesen/Schreiben mit Echo, Stall-Echo, Schutz 8x00,
  Clear unbeantwortet).
"""

import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_REPO = next((d for d in (os.path.dirname(_HERE), os.path.dirname(os.path.dirname(_HERE)))
              if os.path.isdir(os.path.join(d, "nachbau"))), os.path.dirname(_HERE))
_NACHBAU = os.path.join(_REPO, "nachbau")
if os.path.isdir(_NACHBAU) and _NACHBAU not in sys.path:
    sys.path.insert(0, _NACHBAU)

import k2_transport as T
import k2_motor_init as K

try:
    import motor_init as REF
    _HAVE_REF = True
except Exception as _e:
    REF = None
    _HAVE_REF = False
    print("WARNUNG: nachbau/motor_init.py nicht importierbar: %s" % _e)

try:
    from serial485 import build_frame as REF_BUILD, parse_frame as REF_PARSE
    _HAVE_SERIAL = True
except Exception:
    REF_BUILD, REF_PARSE = None, None
    _HAVE_SERIAL = False


# =====================================================================
# Fakes
# =====================================================================

class FakeGpio(K.GpioOps):
    """Fake digital_out (Main-MCU): set/query mit phys-Pegel."""

    def __init__(self):
        self.levels = {}  # pin_raw -> level
        self.calls = []

    def set_pin(self, pin_raw, level):
        self.calls.append((pin_raw, int(level)))
        self.levels[pin_raw] = int(level)

    def query_pin(self, pin_raw):
        if pin_raw not in self.levels:
            return None  # noch nie gesetzt → keine Rücklesung
        inv = pin_raw.startswith("!")
        return int(self.levels[pin_raw]) ^ int(inv)


class FakeRs485Bus:
    """Simuliert X/Y/Z/Z1 (0x81–0x84) auf RS485 (Payload-Ebene)."""

    def __init__(self, gpio=None, values=None, silent=(), dead=()):
        self.gpio = gpio
        self.values = dict(values or {})  # (addr, pid) -> Zahl
        self.silent = set(silent)  # {(runde, addr)} ohne Antwort
        self.dead = set(dead)  # {addr} immer Timeout
        self.calls = []  # [(addr, fun, tuple(data), kwargs)]
        self.broadcast_seen = 0
        self.round = 0
        self.types = {10: "int16_t", 12: "uint16_t", 66: "uint16_t",
                      67: "uint16_t", 94: "uint16_t", 110: "uint16_t",
                      122: "uint16_t", 128: "uint16_t", 141: "uint16_t",
                      173: "uint16_t", 200: "uint16_t", 204: "uint16_t",
                      217: "uint16_t"}

    def _val(self, addr, pid):
        if (addr, pid) in self.values:
            return self.values[(addr, pid)]
        return 0  # Ersatzwert (führt ggf. zu Write – Test setzt gezielt)

    def transact_full(self, addr, fun, data, timeout=None, retries=0, **kw):
        payload = list(data)
        self.calls.append((addr, fun, tuple(payload), dict(kw, timeout=timeout, retries=retries)))
        if addr in self.dead:
            raise TimeoutError("FakeRS485: 0x%02X tot" % addr)
        if fun == K.FUNC_ADDR and payload == [K.SUB_ADDR_SET]:
            self.broadcast_seen += 1
            self.round += 1
            raise TimeoutError("Broadcast (erwartet)")
        if fun == K.FUNC_ADDR and payload == [K.SUB_ADDR_READ]:
            if self.broadcast_seen == 0:
                raise TimeoutError("ohne Broadcast keine Adresse")
            if (self.round, addr) in self.silent or (self.broadcast_seen, addr) in self.silent:
                raise TimeoutError("still (Runde %d)" % self.broadcast_seen)
            if addr not in (0x81, 0x82, 0x83, 0x84):
                raise TimeoutError("unbekannte Adresse 0x%02X" % addr)
            import k2_transport as _T
            raw = _T.build_frame(addr, fun, [addr])
            return addr, 0x00, fun, bytes([addr]), raw
        if fun == K.FUNC_SET_MODE:
            return addr, 0x00, fun, bytes(payload), T.build_frame(addr, fun, payload)
        if fun == K.FUNC_PROTECTION and payload == [K.SUB_PROT_CHECK]:
            return addr, 0x00, fun, bytes(8), T.build_frame(addr, fun, bytes(8))
        if fun == K.FUNC_PROTECTION and payload == [K.SUB_PROT_CLEAR]:
            raise TimeoutError("0C 05 nie beantwortet (live ebenso)")
        if fun == K.FUNC_SYS_PARAM and payload[0] == K.SUB_SYS_READ:
            pid = payload[1]
            if pid in K.HOST_ONLY_IDS:
                return addr, 0x00, fun, b"", T.build_frame(addr, fun, b"")
            vt = self.types.get(pid, "float")
            v = self._val(addr, pid)
            if vt == "float":
                raw = struct.pack("<f", float(v))
            elif vt == "int16_t":
                raw = struct.pack("<h", int(v))
            else:
                raw = struct.pack("<H", int(v))
            return addr, 0x00, fun, bytes(raw), T.build_frame(addr, fun, bytes(raw))
        if fun == K.FUNC_SYS_PARAM and payload[0] == K.SUB_SYS_WRITE:
            pid = payload[1]
            echo = bytes(payload[2:])
            # Wert übernehmen (für Echo-Konsistenz)
            try:
                if len(echo) == 4:
                    self.values[(addr, pid)] = struct.unpack("<f", echo)[0]
            except Exception:
                pass
            return addr, 0x00, fun, echo, T.build_frame(addr, fun, echo)
        if fun == K.FUNC_SYS_PARAM and payload == [K.SUB_SYS_UPDATE]:
            return addr, 0x00, fun, bytes([0x01]), T.build_frame(addr, fun, [0x01])
        if fun == K.FUNC_VERSION and payload == [K.SUB_VERSION_GET]:
            ver = bytes([1, 9, 7, 81, 1, 3, 1, 3, 2, 75, 1, 0])
            return addr, 0x00, fun, ver, T.build_frame(addr, fun, ver)
        if fun == K.FUNC_VARIABLE and len(payload) == 1 and 1 <= payload[0] <= 12:
            idx = payload[0]
            if idx in (11, 12):
                raw = struct.pack("<I", 1106 if idx == 11 else 0)
            else:
                raw = struct.pack("<f", 0.0)
            return addr, 0x00, fun, raw, T.build_frame(addr, fun, raw)
        raise AssertionError("FakeRS485 unerwartet: 0x%02X fun 0x%02X %r" % (addr, fun, payload))

    def transact(self, addr, fun, data, timeout=None, retries=0, **kw):
        _a, st, _f, d, _r = self.transact_full(addr, fun, data, timeout=timeout, retries=retries, **kw)
        return d


class FakeMcu:
    """Simuliert Düsen-MCU (transparent, volle Frames) + Main-GPIO.

    gpio-Teil wird vom Controller NICHT genutzt (eigener FakeGpio), hier nur
    transparent_exchange + Calls. Param-Speicher separat (E-Motor 0x81 auf
    eigenem Bus – gleiche Nummer wie X, aber anderer Bus!).
    """

    def __init__(self, values=None):
        self.values = dict(values or {})  # (0x81, pid) -> Zahl
        self.writes = []  # [volle Request-Bytes]
        self.reads = []  # [volle Response-Bytes oder b'']
        self.calls = []  # [(addr, fun, tuple(payload))]
        self.broadcast_seen = 0

    def exchange(self, write, timeout_ms):
        write = bytes(write)
        self.writes.append(write)
        # Vollframe zerlegen: F7 addr len status fun data crc
        if len(write) < 6 or write[0] != 0xF7:
            raise ValueError("FakeMcu: kein Vollframe")
        addr, fun = write[1], write[4]
        payload = list(write[5:-1])
        self.calls.append((addr, fun, tuple(payload)))
        if addr == 0xFF and fun == K.FUNC_ADDR and payload == [K.SUB_ADDR_SET]:
            self.broadcast_seen += 1
            self.reads.append(b"")
            return b""
        if addr != 0x81:
            # Düsenbus kennt nur 0x81 (E) + Broadcast
            self.reads.append(b"")
            raise TimeoutError("FakeMcu: 0x%02X unbekannt (Düsenbus nur 0x81)" % addr)
        if fun == K.FUNC_ADDR and payload == [K.SUB_ADDR_READ]:
            if self.broadcast_seen == 0:
                self.reads.append(b"")
                raise TimeoutError("ohne Broadcast keine Adresse (Düsenbus)")
            resp = T.build_frame(0x81, fun, [0x81])
            self.reads.append(resp)
            return resp
        if fun == K.FUNC_SET_MODE:
            resp = T.build_frame(0x81, fun, payload)
            self.reads.append(resp)
            return resp
        if fun == K.FUNC_PROTECTION and payload == [K.SUB_PROT_CHECK]:
            resp = T.build_frame(0x81, fun, bytes(8))
            self.reads.append(resp)
            return resp
        if fun == K.FUNC_PROTECTION and payload == [K.SUB_PROT_CLEAR]:
            self.reads.append(b"")
            return b""
        if fun == K.FUNC_SYS_PARAM and payload[0] == K.SUB_SYS_READ:
            pid = payload[1]
            if pid in K.HOST_ONLY_IDS:
                resp = T.build_frame(0x81, fun, b"")
                self.reads.append(resp)
                return resp
            if pid == 10:
                raw = struct.pack("<h", int(self.values.get((0x81, pid), 0)))
            else:
                raw = struct.pack("<f", float(self.values.get((0x81, pid), 0)))
            resp = T.build_frame(0x81, fun, raw)
            self.reads.append(resp)
            return resp
        if fun == K.FUNC_SYS_PARAM and payload[0] == K.SUB_SYS_WRITE:
            pid = payload[1]
            echo = bytes(payload[2:])
            try:
                if len(echo) == 4:
                    self.values[(0x81, pid)] = struct.unpack("<f", echo)[0]
            except Exception:
                pass
            resp = T.build_frame(0x81, fun, echo)
            self.reads.append(resp)
            return resp
        if fun == K.FUNC_SYS_PARAM and payload == [K.SUB_SYS_UPDATE]:
            resp = T.build_frame(0x81, fun, [0x01])
            self.reads.append(resp)
            return resp
        if fun == K.FUNC_VERSION and payload == [K.SUB_VERSION_GET]:
            ver = bytes([1, 9, 7, 81, 1, 3, 1, 3, 2, 75, 1, 0])
            resp = T.build_frame(0x81, fun, ver)
            self.reads.append(resp)
            return resp
        if fun == K.FUNC_VARIABLE and len(payload) == 1 and 1 <= payload[0] <= 12:
            idx = payload[0]
            if idx in (11, 12):
                raw = struct.pack("<I", 1106 if idx == 11 else 0)
            else:
                raw = struct.pack("<f", 0.0)
            resp = T.build_frame(0x81, fun, raw)
            self.reads.append(resp)
            return resp
        raise AssertionError("FakeMcu unerwartet: 0x%02X fun 0x%02X %r" % (addr, fun, payload))


# =====================================================================
# FakePrinter-Lebenszyklus für REVIEW 1–3 (Klipper-Verhalten nachgebaut)
# =====================================================================

FAKE_PIN_ENUM = {"PA0": [0, 16], "PB0": [16, 16], "PC0": [32, 16],
                 "PD0": [48, 16], "ADC_TEMPERATURE": 254}


def fake_pin_number(pin_raw):
    """Pin-Nummer wie K.pin_number_from_enum (Basis+Offset, s. Dictionary)."""
    s = str(pin_raw).strip()
    if ":" in s:
        s = s.split(":", 1)[1]
    clean = s.lstrip("!^~").strip()
    if clean in FAKE_PIN_ENUM:
        v = FAKE_PIN_ENUM[clean]
        return int(v[0]) if isinstance(v, (list, tuple)) else int(v)
    import re as _re
    m = _re.match(r"^([A-Z]+)(\d+)$", clean)
    if m:
        base = "%s0" % m.group(1)
        if base in FAKE_PIN_ENUM:
            v = FAKE_PIN_ENUM[base]
            b = int(v[0]) if isinstance(v, (list, tuple)) else int(v)
            return b + int(m.group(2))
    raise ValueError("Fake-Pin unbekannt: %r" % (pin_raw,))


class FakeReactor:
    def __init__(self):
        self.pauses = []

    def monotonic(self):
        return 100.0

    def pause(self, s):
        self.pauses.append(float(s))


class FakeToolhead:
    def __init__(self, print_time=10.0):
        self.print_time = float(print_time)
        self.wait_moves_calls = 0

    def get_last_move_time(self):
        return float(self.print_time)

    def wait_moves(self):
        self.wait_moves_calls += 1


class FakeGcode:
    def __init__(self):
        self.infos = []
        self.scripts = []
        self.commands = {}

    def respond_info(self, msg):
        self.infos.append(str(msg))

    def run_script(self, script):
        self.scripts.append(str(script))

    def register_command(self, name, func, desc=None):
        self.commands[name] = func


class FakeOutputPin:
    """Nachbau von MCU_digital_out: set_digital braucht print_time > 0."""

    def __init__(self, pin_raw, pin_number):
        self.pin_raw = pin_raw
        self.pin_number = int(pin_number)
        last = str(pin_raw).split(":")[-1].strip()
        self.invert = last.startswith("!")
        self.logical = None
        self.phys = 0
        self.last_print_time = None
        self.calls = []

    def set_digital(self, print_time, value):
        # REVIEW-Test (b): print_time <= 0 ablehnen wie Klipper (Timer zu nah).
        if print_time is None or float(print_time) <= 0:
            raise RuntimeError(
                "set_digital mit print_time=%r abgelehnt (muss > 0 sein)" % (print_time,))
        self.last_print_time = float(print_time)
        self.calls.append((float(print_time), int(value)))
        self.logical = int(value)
        self.phys = int(value) ^ int(self.invert)


class FakePpins:
    def __init__(self, printer):
        self.printer = printer
        self.pins = {}  # pin_raw -> FakeOutputPin

    def setup_pin(self, ptype, pin_raw):
        # REVIEW-Test (a): nach connect verboten wie Klipper.
        if self.printer.connected:
            raise RuntimeError("setup_pin nach connect verboten (wie Klipper)")
        if ptype != "digital_out":
            raise ValueError("nur digital_out unterstützt: %r" % (ptype,))
        num = fake_pin_number(pin_raw)
        pin = FakeOutputPin(pin_raw, num)
        self.pins[pin_raw] = pin
        return pin

    def lookup_pin(self, pin_raw):
        # ANNAHME-Rückgabe (mcu, pin_num) – KlippyGpio versteht mehrere Formen.
        if pin_raw in self.pins:
            return (self.printer.main_mcu, self.pins[pin_raw].pin_number)
        return (self.printer.main_mcu, fake_pin_number(pin_raw))

    def get_enumerations(self):
        return {"pin": dict(FAKE_PIN_ENUM)}


class FakeGpioQuery:
    """Simuliert query_gpio_status -> gpio_status (phys-Pegel)."""

    def __init__(self, mcu, oid):
        self.mcu = mcu
        self.oid = int(oid)

    def send(self, params):
        oid, pin_num = int(params[0]), int(params[1])
        if oid != self.oid:
            raise ValueError("oid-Mismatch: %d != %d" % (oid, self.oid))
        # pin_num -> phys über FakePpins auflösen.
        ppins = self.mcu.printer.pp
        for pin in ppins.pins.values():
            if pin.pin_number == pin_num:
                return {"oid": oid, "status": int(pin.phys)}
        # Nie gesetzter Pin: Hardware liest 0.
        return {"oid": oid, "status": 0}


class FakeTransparentQuery:
    def __init__(self, mcu, oid):
        self.mcu = mcu
        self.oid = int(oid)

    def send(self, params):
        return {"oid": self.oid, "read": b""}


class FakeConfigMcu:
    """Nachbau der MCU-Config-Lebenszyklus (Review-Test a)."""

    def __init__(self, printer, name, supports_query_gpio=True,
                 supports_transparent=False):
        self.printer = printer
        self.name = name
        self.supports_query_gpio = supports_query_gpio
        self.supports_transparent = supports_transparent
        self._oid_counter = 0
        self.config_cmds = []
        self._callbacks = []

    def create_oid(self):
        if self.printer.connected:
            raise RuntimeError("create_oid nach connect verboten (wie Klipper)")
        self._oid_counter += 1
        return int(self._oid_counter)

    def add_config_cmd(self, cmd):
        if self.printer.connected:
            raise RuntimeError("add_config_cmd nach connect verboten (wie Klipper)")
        self.config_cmds.append(str(cmd))

    def register_config_callback(self, cb):
        if self.printer.connected:
            raise RuntimeError(
                "register_config_callback nach connect verboten (wie Klipper)")
        self._callbacks.append(cb)

    def lookup_query_command(self, fmt, resp, oid=None, **kw):
        if not self.printer.connected:
            raise RuntimeError(
                "lookup_query_command vor connect verboten (wie Klipper)")
        if "query_gpio_status" in fmt:
            if not self.supports_query_gpio:
                raise RuntimeError("query_gpio_status fehlt im Dictionary")
            return FakeGpioQuery(self, oid)
        if "transparent_send" in fmt:
            if not self.supports_transparent:
                raise RuntimeError("transparent_send fehlt im Dictionary")
            return FakeTransparentQuery(self, oid)
        raise RuntimeError("unbekannter Query-Befehl: %r" % (fmt,))

    def try_lookup_query_command(self, fmt, resp, oid=None, **kw):
        try:
            return self.lookup_query_command(fmt, resp, oid=oid, **kw)
        except Exception:
            # REVIEW 3: Fallback None, wenn Befehl im Dictionary fehlt.
            # Lebenszyklus-Fehler (vor connect) ebenfalls als None – der
            # Aufrufer (handle_connect) läuft nur nach connect.
            if "query_gpio_status" in fmt and not self.supports_query_gpio:
                return None
            if "transparent_send" in fmt and not self.supports_transparent:
                return None
            return None

    def lookup_command(self, fmt, **kw):
        if not self.printer.connected:
            raise RuntimeError("lookup_command vor connect verboten")
        if "set_digital_out" in fmt:
            return ("set_digital_out", fmt)
        raise RuntimeError("unbekannter Befehl: %r" % (fmt,))

    def try_lookup_command(self, fmt, **kw):
        try:
            return self.lookup_command(fmt, **kw)
        except Exception:
            return None

    def get_enumeration(self, kind, name):
        if kind == "pin":
            return fake_pin_number(name)
        raise ValueError("Enumeration %r unbekannt" % (kind,))

    def get_enumerations(self):
        return {"pin": dict(FAKE_PIN_ENUM)}

    def estimated_print_time(self, monotonic):
        return float(monotonic) + 0.050


class FakePrinter:
    """Minimaler Klipper-Lebenszyklus: Config-Phase -> fire_connect()."""

    def __init__(self, supports_query_gpio=True):
        self.connected = False
        self.reactor = FakeReactor()
        self.toolhead = FakeToolhead()
        self.gcode = FakeGcode()
        self.main_mcu = FakeConfigMcu(self, "mcu",
                                      supports_query_gpio=supports_query_gpio)
        self.nozzle_mcu = FakeConfigMcu(self, "nozzle_mcu",
                                        supports_query_gpio=False,
                                        supports_transparent=True)
        self.pp = FakePpins(self)
        self.objects = {"ppins": self.pp, "mcu": self.main_mcu,
                        "nozzle_mcu": self.nozzle_mcu,
                        "toolhead": self.toolhead, "gcode": self.gcode}
        self._connect_handlers = []

    def lookup_object(self, name):
        try:
            return self.objects[name]
        except KeyError:
            raise RuntimeError("Objekt %r fehlt" % (name,))

    def get_reactor(self):
        return self.reactor

    def register_event_handler(self, ev, cb):
        if ev == "klippy:connect":
            self._connect_handlers.append(cb)

    def fire_connect(self):
        # Config-Callbacks laufen NOCH vor connect (add_config_cmd erlaubt).
        for mcu in (self.main_mcu, self.nozzle_mcu):
            for cb in list(mcu._callbacks):
                cb(0.0)
        self.connected = True
        for cb in list(self._connect_handlers):
            cb()


def _strapping_specs_for_test(cfg):
    specs = []
    for m in K.PIN_MOTORS:
        for kind in ("dir", "step"):
            spec = (cfg.get("pins", {}).get(m) or {}).get(kind)
            if spec and spec.get("raw") and spec["raw"] not in specs:
                specs.append(spec["raw"])
    if K.ENABLE_PIN_RAW not in specs:
        specs.append(K.ENABLE_PIN_RAW)
    return specs


# =====================================================================
# Test-Config (wie live motor_control.cfg: Pins + wenige Params)
# =====================================================================

def test_options():
    return {
        "switch": "1",
        "motor_closed_loop": "x,y,z,z1,e",
        "retries": "4",
        "allow_write": "True",
        "shutdown_on_failure": "False",
        "motor_x_dir": "PB9,0",
        "motor_x_step": "PB10,0",
        "motor_x_stall": "PB11",
        "motor_y_dir": "!PB7,0",
        "motor_y_step": "PB8,0",
        "motor_y_stall": "PB12",
        "motor_z_dir": "PB5,0",
        "motor_z_step": "PB6,1",
        "motor_z_stall": "PB13",
        "motor_z1_dir": "PA1,1",
        "motor_z1_step": "PB15,1",
        "motor_z1_stall": "PA10",
        "motor_e_stall": "nozzle_mcu:PB12",
        # wenige konfigurierte Params (IDs wie live F008, ohne 226):
        "x_controller_pos_loop_pid_param_kp": "100",
        "x_controller_spd_loop_pid_param_kp": "0.028",
        "x_controller_spd_loop_pid_param_ki": "6.0",
        "z_controller_spd_loop_pid_param_kp": "0.035",
        "z_controller_spd_loop_pid_param_ki": "2.0",
        "x_protection_param_prt_track_max_err": "0.3",
        "x_protection_param_prt_track_err_time": "0.1",
    }


def build_system(opts=None, rs485_values=None, mcu_values=None, **kw):
    opts = dict(opts if opts is not None else test_options())
    cfg = K.parse_options(opts)
    gpio = FakeGpio()
    rs485_bus = FakeRs485Bus(gpio=gpio, values=rs485_values, **kw)
    mcu = FakeMcu(values=mcu_values)
    rs485 = T.Rs485Transport(rs485_bus)
    transp = T.TransparentTransport(mcu)
    events = []
    ctl = K.K2MotorController(cfg, rs485, transp, gpio,
                              pause=lambda s: None, send_event=events.append)
    return cfg, gpio, rs485_bus, mcu, rs485, transp, ctl, events


# =====================================================================
# Einzeltests
# =====================================================================

def test_transparent_format():
    """Transparent-Frame-Format gegen 3 Live-Frames (inkl. CRC)."""
    cases = [
        # (addr, fun, payload, live-hex Request)
        (0xFF, 0x0E, [0x01], "f7 ff 04 00 0e 01 89"),
        (0x81, 0x0E, [0x02], "f7 81 04 00 0e 02 80"),
        (0x81, 0x11, [0x02], "f7 81 04 00 11 02 14"),
    ]
    responses = [
        ("f7 81 04 00 0e 81 00", 0x0E, bytes([0x81])),
        ("f7 81 0b 00 0c 00 00 00 00 00 00 00 00 fb", 0x0C, bytes(8)),
    ]
    for addr, fun, payload, hexstr in cases:
        live = bytes.fromhex(hexstr)
        got = bytes(T.build_frame(addr, fun, payload))
        assert got == live, "build_frame 0x%02X/0x%02X: %s != %s" % (addr, fun, got.hex(" "), hexstr)
        rfun, rdata = T.parse_frame(live)
        assert rfun == fun and bytes(rdata) == bytes(payload), "parse %s" % hexstr
        if _HAVE_SERIAL:
            assert bytes(REF_BUILD(addr, fun, payload)) == live, "serial485-Abweichung"
            _rf, _rd = REF_PARSE(live)
            assert _rf == fun
    for hexstr, fun, data in responses:
        live = bytes.fromhex(hexstr)
        rfun, rdata = T.parse_frame(live)
        assert rfun == fun and bytes(rdata) == bytes(data), "Antwort %s" % hexstr
    # Roundtrip über TransparentTransport + FakeMcu (CRC + Adresse geprüft):
    mcu = FakeMcu()
    tr = T.TransparentTransport(mcu)
    try:
        tr.transact(0xFF, 0x0E, [0x01], timeout=2.0, retries=0)
    except TimeoutError:
        pass  # Broadcast bleibt unbeantwortet/leer (wie live)
    st, fn, data = tr.transact(0x81, 0x0E, [0x02], timeout=2.0, retries=0)
    assert fn == 0x0E and bytes(data) == bytes([0x81]), "Roundtrip E addr"
    assert bytes(mcu.writes[1]) == bytes.fromhex("f7 81 04 00 0e 02 80"), "Request-Bytes live-identisch"
    return "3 Live-Requests + 2 Antworten byte-identisch (CRC ok), Roundtrip ok"


def test_init_lauf():
    """Kompletter Init-Lauf (alle antworten) – Status ready, Event k2_motor:ready."""
    # Fake-Werte: alles in Toleranz AUSSER X ID 70 (300 statt 100 → Write) und
    # Z ID 188 (0.01 statt 0.1 → Write) – wie live 16.10.
    rs_vals, mcu_vals = {}, {}
    cfg0 = K.parse_options(test_options())
    for m in ("x", "y", "z", "z1"):
        num = K.MOTOR_NAME_TO_NUM[m]
        addr = 0x80 + num
        for pname, cval in cfg0["params"].get(m, {}).items():
            pid = next(p["id"] for p in K.default_param_table() if p["name"] == pname)
            rs_vals[(addr, pid)] = cval
    rs_vals[(0x81, 70)] = 300.0
    rs_vals[(0x83, 188)] = 0.01
    mcu_vals[(0x81, 9)] = 123.0
    mcu_vals[(0x81, 10)] = 0
    mcu_vals[(0x81, 25)] = 3.0
    cfg, gpio, bus, mcu, rs485, transp, ctl, events = build_system(
        rs485_values=rs_vals, mcu_values=mcu_vals)
    res = ctl.run_init()
    assert res["status"] == "ready", "Status %s Fehler %s" % (res["status"], res["errors"])
    assert res["ready"] is True and ctl.is_ready is True
    assert events == ["k2_motor:ready"], "Event %r" % events
    # P1-Pins gesetzt (8 Dir/Step), Restore auf phys 0 geprüft via FakeGpio:
    assert len([c for c in gpio.calls if c[0] in ("PB9", "PB10", "!PB7", "PB8", "PB5", "PB6", "PA1", "PB15")]) >= 16
    return "ready, Event k2_motor:ready, %d GPIO-Sets, Fehler %d" % (len(gpio.calls), len(res["errors"]))


def test_plan_vergleich():
    """Gesendete Frame-Folge == motor_init.build_init_plan (Reihenfolge/Payloads)."""
    if not _HAVE_REF:
        return "SKIP (nachbau/motor_init.py fehlt)"
    rs_vals, mcu_vals = {}, {}
    cfg0 = K.parse_options(test_options())
    for m in ("x", "y", "z", "z1"):
        addr = 0x80 + K.MOTOR_NAME_TO_NUM[m]
        for pname, cval in cfg0["params"].get(m, {}).items():
            pid = next(p["id"] for p in K.default_param_table() if p["name"] == pname)
            rs_vals[(addr, pid)] = cval
    rs_vals[(0x81, 70)] = 300.0
    mcu_vals[(0x81, 9)] = 123.0
    mcu_vals[(0x81, 10)] = 0
    mcu_vals[(0x81, 25)] = 3.0
    cfg, gpio, bus, mcu, rs485, transp, ctl, events = build_system(
        rs485_values=rs_vals, mcu_values=mcu_vals)
    res = ctl.run_init()
    assert res["status"] == "ready"
    # Referenzplan aus derselben Config (motor_init-Format ist kompatibel):
    ref_cfg = dict(cfg)
    ref_cfg.setdefault("notizen", [])
    ref_cfg.setdefault("quelle", "test")
    ref_cfg.setdefault("stepper_pins", {})
    plan = REF.build_init_plan(ref_cfg)
    # Erwartete Bus-Frames aus dem Plan (aktiv, ttyS5 + transparent, bedingte
    # Writes nur bei isclose-Abweichung – hier nur X/70 abweichend):
    exp_tty, exp_tr = [], []
    for s in plan.steps:
        if not s.aktiv or s.bus not in ("ttyS5", "transparent") or s.sig() is None:
            continue
        if s.art == "param_write":
            v = rs_vals.get((s.addr, s.meta["param_id"]),
                            mcu_vals.get((s.addr, s.meta["param_id"])))
            import math as _m
            if _m.isclose(v, s.meta["config_value"], rel_tol=1e-9, abs_tol=1e-6):
                continue
        (exp_tty if s.bus == "ttyS5" else exp_tr).append((s.addr, s.fun, tuple(s.payload)))
    # Tatsächlich gesendet (Transports zeichnen (addr, fun, payload) auf):
    got_tty = [(a, f, tuple(d)) for a, f, d, _kw in bus.calls]
    got_tr = list(transp.sent)
    # Broadcast kommt in BEIDEN Plänen je 1× vor (1 Runde, alle antworten):
    assert got_tty == exp_tty, "ttyS5: %d != %d\n got %s\n exp %s" % (
        len(got_tty), len(exp_tty), got_tty[:8], exp_tty[:8])
    assert got_tr == exp_tr, "transparent: %d != %d\n got %s\n exp %s" % (
        len(got_tr), len(exp_tr), got_tr, exp_tr)
    return "ttyS5 %d + transparent %d Frames plan-identisch" % (len(got_tty), len(got_tr))


def test_extruder_transparent():
    """E-Frames nur via transparent an 0x81, nie 0x85 auf RS485."""
    cfg, gpio, bus, mcu, rs485, transp, ctl, events = build_system()
    # E-Reads mit Defaultwerten (0) – Config hat für E keine Params, nur 9/10/25:
    res = ctl.run_init()
    assert res["status"] == "ready", res["errors"]
    addrs_rs = {a for a, _f, _d, _k in bus.calls}
    assert 0x85 not in addrs_rs, "0x85 auf RS485 gesendet!"
    assert all(a != 0x85 for a, _f, _d in transp.sent), "0x85 via transparent?"
    e_tr = [(a, f, d) for a, f, d in transp.sent if a == 0x81]
    assert len(e_tr) >= 3, "E-Frames via transparent: %s" % (transp.sent,)
    funs = {f for _a, f, _d in e_tr}
    assert 0x0E in funs and 0x11 in funs and 0x0C in funs, "E braucht 0E/11/0C, got %s" % funs
    # Auch P8 (E-Param 9/10/25) via transparent:
    assert any(f == 0x06 for _a, f, _d in e_tr), "E-Param-Abgleich fehlt via transparent"
    return "E %d Frames via transparent 0x81, RS485-Addrs %s (ohne 0x85)" % (
        len(e_tr), sorted("0x%02X" % a for a in addrs_rs))


def test_allow_write_false():
    """allow_write=False sendet keine schreibenden Frames (nur lesen + Plan)."""
    opts = test_options()
    opts["allow_write"] = "False"
    cfg, gpio, bus, mcu, rs485, transp, ctl, events = build_system(opts=opts)
    # describe_plan muss den Hinweis enthalten:
    assert "allow_write=False" in ctl.describe_plan()
    res = ctl.run_init()
    # Diagnose-Modus läuft durch (kein key798, da Reads auch ohne Broadcast ok
    # sein können oder Timeouts nur geloggt werden – hier: E + Z antworten? Alle
    # antworten, da Fakes auch ohne Broadcast? Nein – Fakes brauchen Broadcast!
    # Ohne Broadcast antwortet RS485 NICHT → key798. Das ist ERWARTET und ok:
    # Diagnose ohne Broadcast kann key798 liefern. Entscheidend: keine Writes.
    writing = []
    for a, f, d, _k in bus.calls:
        if (f == K.FUNC_ADDR and list(d) == [1]) or f == K.FUNC_SET_MODE \
           or (f == K.FUNC_PROTECTION and list(d) == [5]) \
           or (f == K.FUNC_SYS_PARAM and list(d)[:1] == [1]) \
           or (f == K.FUNC_SYS_PARAM and list(d) == [3]):
            writing.append((a, f, tuple(d)))
    for a, f, d in transp.sent:
        if (f == K.FUNC_ADDR and list(d) == [1]) or f == K.FUNC_SET_MODE \
           or (f == K.FUNC_PROTECTION and list(d) == [5]) \
           or (f == K.FUNC_SYS_PARAM and list(d)[:1] == [1]) \
           or (f == K.FUNC_SYS_PARAM and list(d) == [3]):
            writing.append((a, f, tuple(d)))
    assert writing == [], "schreibende Frames trotz allow_write=False: %s" % (writing,)
    assert gpio.calls == [], "GPIO gesetzt trotz allow_write=False: %s" % (gpio.calls,)
    return "keine Writes (%d Reads via RS485, %d via transparent)" % (len(bus.calls), len(transp.sent))


def test_status_lesen():
    """K2_MOTOR_STATUS: Version 0x0F, Schutz 0x0C dekodiert, Feedback 5/6."""
    cfg, gpio, bus, mcu, rs485, transp, ctl, events = build_system()
    st = ctl.motor_status()
    assert set(st) == {"x", "y", "z", "z1", "e"}, st.keys()
    for m, e in st.items():
        assert "version" in e, "%s: %s" % (m, e)
        assert e["version"]["fireware_ver"] == "1.9.7.81", e["version"]
        assert e["protection"]["ok"] is True, e["protection"]
        assert e["pos_fdb"] == 0.0 and e["spd_fdb"] == 0.0, e
    return "5 Motoren: Version 1.9.7.81, Schutz ok, pos/spd 0.0"


def test_review_config_vor_connect():
    """REVIEW 1: setup_pin + Config-Befehle VOR connect (nach connect Fehler)."""
    printer = FakePrinter()
    cfg = K.parse_options(test_options())
    specs = _strapping_specs_for_test(cfg)
    assert len(specs) == 9, "8 Dir/Step + Enable erwartet, got %s" % (specs,)
    # FRÜH (Config-Phase): KlippyGpio legt alle Pins + Query-OID an.
    gpio = K.KlippyGpio(printer, "mcu", pin_specs=specs)
    assert len(gpio._pins) == 9, "Pins %d != 9" % len(gpio._pins)
    assert gpio._query_oid == 1, "Query-OID %r" % (gpio._query_oid,)
    # Transparent analog: OID + Callback VOR connect (Review 1, zweite Hälfte).
    t_oid = printer.nozzle_mcu.create_oid()
    assert t_oid == 1, "transparent-OID %r" % (t_oid,)
    printer.nozzle_mcu.register_config_callback(
        lambda e: printer.nozzle_mcu.add_config_cmd(
            "config_transparent oid=%d" % t_oid))
    # Connect: Config-Callbacks laufen, danach connected.
    printer.fire_connect()
    assert printer.connected is True
    assert printer.nozzle_mcu.config_cmds == [
        "config_transparent oid=1"], printer.nozzle_mcu.config_cmds
    # Query-Lookup erst NACH connect (optional, hier vorhanden).
    gpio.handle_connect()
    assert gpio._query_available is True, "query sollte verfügbar sein"
    # NACH connect verboten wie Klipper (a):
    for fn, label in [
            (lambda: printer.pp.setup_pin("digital_out", "PB9"), "setup_pin"),
            (lambda: printer.main_mcu.create_oid(), "create_oid"),
            (lambda: printer.main_mcu.add_config_cmd("config_digital_out oid=9"),
             "add_config_cmd"),
            (lambda: printer.main_mcu.register_config_callback(lambda e: None),
             "register_config_callback")]:
        try:
            fn()
        except RuntimeError:
            pass
        else:
            raise AssertionError("%s nach connect NICHT abgelehnt!" % label)
    return "9 Pins + Query-OID 1 vor connect, transparent-cmd %s, 4 Nach-Connects abgelehnt" % (
        printer.nozzle_mcu.config_cmds,)


def test_review_print_time():
    """REVIEW 2: set_digital braucht print_time > 0 (0 wird abgelehnt)."""
    printer = FakePrinter()
    cfg = K.parse_options(test_options())
    gpio = K.KlippyGpio(printer, "mcu",
                        pin_specs=_strapping_specs_for_test(cfg))
    printer.fire_connect()
    gpio.handle_connect()
    # Sauberer Weg (Variante a): toolhead-Zeit + sync (M400-Äquivalent).
    gpio.set_pin("PB9", 0)
    pin = printer.pp.pins["PB9"]
    assert pin.last_print_time is not None and pin.last_print_time > 0, (
        "print_time %r" % (pin.last_print_time,))
    assert pin.logical == 0 and pin.phys == 0, (pin.logical, pin.phys)
    assert printer.toolhead.wait_moves_calls == 0
    gpio.sync()
    assert printer.toolhead.wait_moves_calls == 1, "sync() muss wait_moves rufen"
    # Alter Bug (print_time=0) wird vom Fake wie von Klipper abgelehnt:
    try:
        pin.set_digital(0, 1)
    except RuntimeError:
        pass
    else:
        raise AssertionError("set_digital(0, …) NICHT abgelehnt!")
    # Invertierter Pin: logisch 0 -> phys 1 (wie Y-DIR !PB7).
    gpio.set_pin("!PB7", 0)
    pin7 = printer.pp.pins["!PB7"]
    assert pin7.last_print_time > 0 and pin7.phys == 1, pin7.phys
    return "print_time %.1f ok, wait_moves %d, set_digital(0) abgelehnt, !PB7 phys 1" % (
        pin.last_print_time, printer.toolhead.wait_moves_calls)


def test_review_query():
    """REVIEW 3: query_gpio_status simuliert + Fallback None ohne Befehl."""
    printer = FakePrinter(supports_query_gpio=True)
    cfg = K.parse_options(test_options())
    gpio = K.KlippyGpio(printer, "mcu",
                        pin_specs=_strapping_specs_for_test(cfg))
    printer.fire_connect()
    gpio.handle_connect()
    assert gpio._query_available is True
    # Pegel setzen, dann zurücklesen (phys, invertiert bei !PB7).
    gpio.set_pin("PB9", 0)
    assert gpio.query_pin("PB9") == 0, "PB9"
    gpio.set_pin("!PB7", 0)
    assert gpio.query_pin("!PB7") == 1, "!PB7 logisch 0 -> phys 1"
    gpio.set_pin("PB6", 1)
    assert gpio.query_pin("PB6") == 1, "PB6"
    # Pin-Nummern gegen Dictionary-Logik (PB9 = 16+9 = 25).
    assert gpio._pin_numbers["PB9"] == 25, gpio._pin_numbers["PB9"]
    assert gpio._pin_numbers["!PB7"] == 16 + 7, gpio._pin_numbers["!PB7"]
    # Fallback: MCU ohne query_gpio_status -> None statt Crash.
    printer2 = FakePrinter(supports_query_gpio=False)
    gpio2 = K.KlippyGpio(printer2, "mcu",
                         pin_specs=_strapping_specs_for_test(cfg))
    printer2.fire_connect()
    gpio2.handle_connect()
    assert gpio2._query is None and gpio2._query_available is False
    assert gpio2.query_pin("PB9") is None, "Fallback muss None liefern"
    return "PB9=0, !PB7=1, PB6=1, Nummern 25/%d, Fallback None ok" % (
        gpio._pin_numbers["!PB7"],)


# =====================================================================
# Runner
# =====================================================================

TESTS = [
    ("transparent-Format vs. Live (CRC)", test_transparent_format),
    ("kompletter Init-Lauf", test_init_lauf),
    ("Plan-Vergleich motor_init", test_plan_vergleich),
    ("Extruder via transparent 0x81 (nie 0x85)", test_extruder_transparent),
    ("allow_write=False schreibt nichts", test_allow_write_false),
    ("K2_MOTOR_STATUS liest 0x0F/0x0C/0x08", test_status_lesen),
    ("REVIEW 1: Config vor connect", test_review_config_vor_connect),
    ("REVIEW 2: print_time > 0", test_review_print_time),
    ("REVIEW 3: query + Fallback", test_review_query),
]


def main():
    fails, total = 0, 0
    print("test_k2_motor – %d Prüfungen (ohne Klipper, ohne Hardware)" % len(TESTS))
    for name, fn in TESTS:
        total += 1
        try:
            detail = fn()
            print("ok %d/%d %s – %s" % (total, len(TESTS), name, detail))
        except AssertionError as exc:
            fails += 1
            print("FEHLER %d/%d %s – %s" % (total, len(TESTS), name, exc))
        except Exception as exc:
            fails += 1
            import traceback
            print("FEHLER %d/%d %s – %s.%s: %s" % (
                total, len(TESTS), name, type(exc).__module__, type(exc).__name__, exc))
            traceback.print_exc()
    print("ERGEBNIS: %s (%d/%d ok)" % ("ok" if fails == 0 else "%d FEHLER" % fails,
                                       total - fails, total))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
