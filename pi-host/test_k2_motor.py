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
        self.modes = {}  # addr -> Stall-Mode (0x11), Default 2 wie nach Boot
        self.prot = {}  # addr -> 8 B Antwort auf 0C [0b] (Default 8x00)
        self.bad_echo = set()  # addr -> 0x11 antwortet mit falschem Echo
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
        if fun == K.FUNC_SET_MODE and payload == [K.SUB_STALL_GET]:
            # live: 81 04 00 11 04 -> f7 81 05 00 11 02 00 0e ([mode, 00])
            raw = bytes([self.modes.get(addr, 2), 0])
            return addr, 0x00, fun, raw, T.build_frame(addr, fun, raw)
        if fun == K.FUNC_SET_MODE:
            if len(payload) == 1:
                self.modes[addr] = payload[0]
            if addr in self.bad_echo:
                return addr, 0x00, fun, b"\x07", T.build_frame(addr, fun, [7])
            return addr, 0x00, fun, bytes(payload), T.build_frame(addr, fun, payload)
        if fun == K.FUNC_PROTECTION and payload == [K.SUB_PROT_CHECK]:
            raw = self.prot.get(addr, bytes(8))
            return addr, 0x00, fun, raw, T.build_frame(addr, fun, raw)
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
        # Upstream gcode.py: Buchstabe+Ziffer am Anfang = ungültig (K2_…).
        if not upstream_gcode_name_ok(name):
            raise RuntimeError("Can't register '%s' as it is an invalid name" % name)
        self.commands[name] = func


def upstream_gcode_name_ok(cmd):
    """Nachbau der Namensprüfung aus Upstream gcode.py register_command."""
    try:
        c = cmd.upper().split()[0]
        float(c[1:])
        if c[0].isupper() and c[1].isdigit():
            return True  # klassischer G-Code (G1, M400 …)
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
        self.max_duration = 2.0  # Upstream-Default MCU_digital_out

    def setup_max_duration(self, max_duration):
        self.max_duration = float(max_duration)

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

    def lookup_pin(self, pin_raw, *a, **kw):
        # Upstream: pin_params-Dict mit Pin-NAMEN (kein Zahlwert) – darf vom
        # Modul NICHT zur Nummernauflösung genutzt werden.
        raise AssertionError("pins.lookup_pin darf nicht aufgerufen werden")

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

    def lookup_command(self, fmt, cq=None):
        if not self.printer.connected:
            raise RuntimeError("lookup_command vor connect verboten")
        if "set_digital_out" in fmt:
            return ("set_digital_out", fmt)
        if "query_gpio_status" in fmt and self.supports_query_gpio:
            return ("query_gpio_status", fmt)
        if "transparent_send" in fmt and self.supports_transparent:
            return ("transparent_send", fmt)
        raise RuntimeError("unbekannter Befehl: %r" % (fmt,))

    def try_lookup_command(self, fmt):
        # Upstream mcu.py:1206 – genau EIN Argument, None wenn unbekannt.
        try:
            return self.lookup_command(fmt)
        except Exception:
            return None

    def get_enumerations(self):
        # Upstream: Dictionary erst nach dem Connect (identify) verfügbar.
        if not self.printer.connected:
            raise RuntimeError("get_enumerations vor connect (kein Dictionary)")
        return {"pin": dict(FAKE_PIN_ENUM)}

    def estimated_print_time(self, monotonic):
        return float(monotonic) + 0.050


class FakePrinter:
    """Minimaler Klipper-Lebenszyklus: Config-Phase -> fire_connect()."""

    def __init__(self, supports_query_gpio=True, bus=None):
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
        # Upstream-Objektnamen: 'pins' (pins.py), 'mcu', 'mcu nozzle_mcu'.
        self.objects = {"pins": self.pp, "mcu": self.main_mcu,
                        "mcu nozzle_mcu": self.nozzle_mcu,
                        "toolhead": self.toolhead, "gcode": self.gcode}
        if bus is not None:
            self.objects["k2_485"] = bus
        self._connect_handlers = []
        self.events = {}
        self.loaded = []

    def lookup_object(self, name, default=RuntimeError):
        try:
            return self.objects[name]
        except KeyError:
            if default is not RuntimeError:
                return default
            raise RuntimeError("Unknown config object '%s'" % (name,))

    def load_object(self, config, section, default=RuntimeError):
        self.loaded.append(section)
        return self.lookup_object(section, default)

    def get_reactor(self):
        return self.reactor

    def register_event_handler(self, ev, cb):
        if ev == "klippy:connect":
            self._connect_handlers.append(cb)
        self.events.setdefault(ev, []).append(cb)

    def send_event(self, ev, *args):
        for cb in list(self.events.get(ev, [])):
            cb(*args)

    def fire_connect(self):
        # Config-Callbacks laufen NOCH vor connect (add_config_cmd erlaubt).
        # Upstream mcu.py _send_config: Callbacks OHNE Argument.
        for mcu in (self.main_mcu, self.nozzle_mcu):
            for cb in list(mcu._callbacks):
                cb()
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


def test_transport_sendeschutz():
    """Sendeschutz 17.10.2026 in k2_transport: Länge (100 / fun 0xF0 0x80),
    Broadcast 0xFF/0xFD + 0x01/0x0B gesperrt (außer 0xFF 0x0B [01]); lokale
    Fallback-Prüfung == nachbau/serial485; nichts gesendet bei Verstoß."""
    cases = [
        (0x81, 0x06, [1, 70, 0, 0, 0x96, 0x43], True),   # längster Motor-Request
        (0xFE, 0xA0, list(range(13)), True),             # längster Live-Request
        (0x01, 0x10, [0] * 100, True), (0x01, 0x10, [0] * 101, False),
        (0x01, 0xF0, [0] * 0x80, True), (0x01, 0xF0, [0] * 0x81, False),
        (0xFF, 0x0E, [0x01], True), (0xFF, 0x0B, [0x01], True),
        (0x81, 0x0B, [0x00], True),  # Unicast: Sperre liegt in motor.py (Stub)
        (0xFF, 0x0B, [0x00], False), (0xFF, 0x0B, [], False),
        (0xFF, 0x0B, [0x01, 0x00], False), (0xFF, 0x01, [0x00], False),
        (0xFF, 0x01, [0x01], False), (0xFD, 0x0B, [0x01], False),
        (0xFD, 0x01, [], False), (0xFE, 0x0B, [0x00], True),
    ]
    impls = [("lokal", T._check_tx_frame_local)]
    if _HAVE_SERIAL:
        import serial485 as _S
        impls.append(("serial485", _S.check_tx_frame))
        assert T.check_tx_frame is _S.check_tx_frame, "nachbau-Prüfung nicht genutzt"
    for name, fn in impls:
        for addr, fun, data, ok in cases:
            try:
                fn(addr, fun, data)
                got = True
            except ValueError:
                got = False
            assert got == ok, "%s: 0x%02X fun 0x%02X %d B -> %s" % (
                name, addr, fun, len(data), got)
    # _build_frame_local byte-identisch, > len 255 klarer Fehler, RX bis 255
    for addr, fun, data in ((0xFF, 0x0E, [0x01]), (0x81, 0x0C, [0x0B]),
                            (0x81, 0x15, list(range(161)))):
        assert T._build_frame_local(addr, fun, data) == bytes(T.build_frame(addr, fun, data))
    long_rx = T._build_frame_local(0x81, 0x16, [0] * 252)
    assert long_rx[2] == 255 and T._parse_frame_local(long_rx)[0] == 0x16
    try:
        T._build_frame_local(0x81, 0x16, [0] * 253)
        raise AssertionError("len 256 nicht abgelehnt")
    except ValueError as exc:
        assert "zu lang" in str(exc)
    assert T._build_tx_frame_local(0xFF, 0x0B, [0x01]) == bytes(
        T.build_frame(0xFF, 0x0B, [0x01]))

    # Rs485Transport: gesperrt -> kein Bus-Aufruf, nichts in sent
    class _RecBus:
        def __init__(self):
            self.calls = []

        def transact_full(self, addr, fun, data, timeout=None, retries=0):
            self.calls.append((addr, fun, list(data)))
            raise TimeoutError("still")
    rb = _RecBus()
    rs = T.Rs485Transport(rb)
    for addr, fun, data in ((0xFF, 0x0B, [0x00]), (0xFF, 0x01, [0x00]),
                            (0x81, 0x06, [0] * 101)):
        try:
            rs.transact(addr, fun, data, timeout=0.05, retries=0)
            raise AssertionError("Rs485 nicht gesperrt: 0x%02X/0x%02X" % (addr, fun))
        except T.TxGuardError:
            pass
    assert rb.calls == [] and rs.sent == [], (rb.calls, rs.sent)
    try:
        rs.transact(0xFF, 0x0B, [0x01], timeout=0.05, retries=0)
    except TimeoutError:
        pass
    assert rb.calls == [(0xFF, 0x0B, [0x01])], rb.calls

    # TransparentTransport: gesperrt -> keine MCU-Sendung; erlaubt -> Live-Bytes
    mcu = FakeMcu()
    tr = T.TransparentTransport(mcu)
    for addr, fun, data in ((0xFF, 0x0B, [0x00]), (0xFF, 0x01, []),
                            (0x81, 0x11, [0] * 101)):
        try:
            tr.transact(addr, fun, data, timeout=2.0, retries=0)
            raise AssertionError("transparent nicht gesperrt: 0x%02X/0x%02X" % (addr, fun))
        except T.TxGuardError:
            pass
    assert mcu.writes == [] and tr.sent == [], (mcu.writes, tr.sent)
    try:
        tr.transact(0xFF, 0x0E, [0x01], timeout=2.0, retries=0)
    except TimeoutError:
        pass
    assert bytes(mcu.writes[0]) == bytes.fromhex("f7 ff 04 00 0e 01 89")
    return "%d Fälle x %d Implementierungen, Rs485 + transparent ohne Sendung" % (
        len(cases), len(impls))


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
    """KTWO_MOTOR_STATUS: Version 0x0F, Schutz 0x0C dekodiert, Feedback 5/6."""
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
    # Firmware wertet die Query-OID nicht aus -> keine OID verbraucht.
    assert gpio._query_oid == K.KlippyGpio.GPIO_QUERY_OID == 0, gpio._query_oid
    assert printer.main_mcu._oid_counter == 0, "Query darf keine OID belegen"
    # Strapping-Pins statisch wie Stock (setup_max_duration(0.0)):
    md = {r: p.max_duration for r, p in printer.pp.pins.items()}
    assert set(md.values()) == {0.0}, "max_duration %r" % (md,)
    # Transparent analog: OID + Callback VOR connect (Review 1, zweite Hälfte).
    t_oid = printer.nozzle_mcu.create_oid()
    assert t_oid == 1, "transparent-OID %r" % (t_oid,)
    printer.nozzle_mcu.register_config_callback(
        lambda: printer.nozzle_mcu.add_config_cmd(
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
            (lambda: printer.main_mcu.register_config_callback(lambda: None),
             "register_config_callback")]:
        try:
            fn()
        except RuntimeError:
            pass
        else:
            raise AssertionError("%s nach connect NICHT abgelehnt!" % label)
    return ("9 Pins (max_duration 0) vor connect, Query ohne OID-Verbrauch, "
            "transparent-cmd %s, 4 Nach-Connects abgelehnt" % (printer.nozzle_mcu.config_cmds,))


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


class FakeCmdError(Exception):
    pass


class FakeKGcmd:
    error = FakeCmdError

    def __init__(self, params=None):
        self.params = dict(params or {})
        self.infos = []

    def get_int(self, name, default=None, **kw):
        v = self.params.get(name, default)
        return None if v is None else int(v)

    def get_float(self, name, default=None, **kw):
        v = self.params.get(name, default)
        return None if v is None else float(v)

    def get(self, name, default=None):
        v = self.params.get(name, default)
        if v is None:
            raise FakeCmdError("Error on '%s': missing %s" % ("CMD", name))
        return str(v)

    def respond_info(self, msg):
        self.infos.append(str(msg))


class FakeKConfig:
    """ConfigWrapper-Nachbau für [k2_motor] (get/get_options/has_section)."""

    def __init__(self, printer, opts, sections=()):
        self.printer = printer
        self.opts = dict(opts)
        self.sections = set(sections)

    def get_printer(self):
        return self.printer

    def get_name(self):
        return "k2_motor"

    def get_options(self):
        return dict(self.opts)

    def get(self, key, default=None):
        return self.opts.get(key, default)

    def has_section(self, name):
        return name in self.sections

    def error(self, msg):
        return ValueError(msg)


def _huelle_opts(nozzle="nozzle_mcu"):
    opts = test_options()
    opts.update({"allow_write": "False", "auto_init": "False",
                 "main_mcu": "mcu", "nozzle_mcu": nozzle})
    return opts


def test_huelle_upstream():
    """Klipper-Hülle: KTWO_-Namen, 'pins', nozzle_mcu-Auflösung, Callback ohne
    Argument, max_duration 0, Bus 'k2_485' für X/Y/Z/Z1."""
    bus = FakeRs485Bus(gpio=FakeGpio())
    printer = FakePrinter(bus=bus)
    obj = K.load_config(FakeKConfig(printer, _huelle_opts(), sections=("k2_485",)))
    assert sorted(printer.gcode.commands) == [
        "KTWO_MOTOR_CUT_CAL", "KTWO_MOTOR_HOMING", "KTWO_MOTOR_INIT",
        "KTWO_MOTOR_PROTECTION", "KTWO_MOTOR_STALL_MODE", "KTWO_MOTOR_STATUS",
        "KTWO_MOTOR_SYS_PARAM"], \
        sorted(printer.gcode.commands)
    assert all(upstream_gcode_name_ok(c) for c in printer.gcode.commands)
    assert not upstream_gcode_name_ok("K2_MOTOR_INIT"), "Regel-Nachbau defekt"
    assert obj.nozzle_mcu is printer.nozzle_mcu, "nozzle_mcu -> 'mcu nozzle_mcu'"
    assert printer.loaded == ["k2_485"], "load_object(k2_485) fehlt: %r" % printer.loaded
    assert obj._bus is bus
    md = sorted(set(p.max_duration for p in printer.pp.pins.values()))
    assert len(printer.pp.pins) == 9 and md == [0.0], (len(printer.pp.pins), md)
    printer.fire_connect()  # ruft Config-Callbacks OHNE Argument
    assert printer.nozzle_mcu.config_cmds == ["config_transparent oid=1"], \
        printer.nozzle_mcu.config_cmds
    tr = obj._ensure_transports()
    assert isinstance(tr, T.Rs485Transport) and tr.bus is bus
    g = FakeKGcmd()
    obj.cmd_status(g)
    assert any(i.startswith("[k2_motor] x: version=") for i in g.infos), g.infos
    assert {a for a, _f, _d, _k in bus.calls} == {0x81, 0x82, 0x83, 0x84}, \
        "Status X/Y/Z/Z1 muss über k2_485 laufen"
    # voller Objektname geht weiterhin
    p2 = FakePrinter(bus=bus)
    o2 = K.load_config(FakeKConfig(p2, _huelle_opts("mcu nozzle_mcu")))
    assert o2.nozzle_mcu is p2.nozzle_mcu
    return "KTWO_MOTOR_INIT/STATUS, pins, 9 Pins max_duration 0, cb(), Bus k2_485 (%d Frames)" % (
        len(bus.calls),)


def test_huelle_ohne_bus():
    """Ohne [k2_485]: klare Fehlermeldung statt None-Absturz."""
    printer = FakePrinter()
    obj = K.load_config(FakeKConfig(printer, _huelle_opts()))
    printer.fire_connect()
    try:
        obj._ensure_transports()
    except RuntimeError as exc:
        msg = str(exc)
    else:
        raise AssertionError("ohne k2_485 kein Fehler")
    assert "[k2_485]" in msg and "serial:" in msg, msg
    for fn, params in ((obj.cmd_status, {}), (obj.cmd_init, {"ALLOW_WRITE": 1})):
        try:
            fn(FakeKGcmd(params))
        except FakeCmdError as exc:
            assert "[k2_485]" in str(exc), exc
        else:
            raise AssertionError("%s ohne Bus ohne Fehler" % fn.__name__)
    g = FakeKGcmd({"DRY_RUN": 1})
    obj.cmd_init(g)  # Trockenlauf geht ohne Bus
    assert g.infos and "P" in g.infos[0]
    return "Meldung: %s" % msg[:70]


# =====================================================================
# Sensorless-Homing X/Y (KTWO_MOTOR_HOMING / _STALL_MODE / _SYS_PARAM)
# =====================================================================

# Live-Belege live-logs-2026-10-16/klippy.log, G28 X Y 14:06:21–14:06:31
# (Requests ohne Kopf/CRC wie "retries = 0, cmd = …", Antworten als Vollframe)
LIVE_HOMING_BEGIN = [            # Zeilen 369155 / 369163 / 369172
    "81 04 00 11 01", "82 04 00 11 01", "81 09 00 06 01 46 00 00 96 43"]
LIVE_HOMING_END_RS485 = [        # Zeilen 369268 / 369281 / 369289–369310 / 369319
    "81 04 00 11 02", "82 04 00 11 02",
    "81 04 00 0c 0b", "82 04 00 0c 0b", "83 04 00 0c 0b", "84 04 00 0c 0b",
    "81 09 00 06 01 46 00 00 c8 42"]
LIVE_HOMING_END_E = "f7 81 04 00 0c 0b 95"   # transparent an E (Zeile 369316)
LIVE_ECHOS = [  # (addr, fun, payload, Live-Vollframe)
    (0x81, 0x11, [0x01], "f7 81 04 00 11 01 1d"),       # 369157
    (0x82, 0x11, [0x01], "f7 82 04 00 11 01 1d"),       # 369165
    (0x81, 0x06, [0x00, 0x00, 0x96, 0x43], "f7 81 07 00 06 00 00 96 43 09"),  # 369174
    (0x81, 0x11, [0x02], "f7 81 04 00 11 02 14"),       # 369270
    (0x81, 0x0C, [0] * 8, "f7 81 0b 00 0c 00 00 00 00 00 00 00 00 fb"),  # 369291
    (0x81, 0x06, [0x00, 0x00, 0xc8, 0x42], "f7 81 07 00 06 00 00 c8 42 d4"),  # 369321
    (0x81, 0x11, [0x02, 0x00], "f7 81 05 00 11 02 00 0e"),  # 0x11 [04]-Antwort (klippy.log.2026-10-12:31419)
]


def _wire(addr, fun, data):
    """Request wie im Stock-Log (addr len 00 fun data, ohne F7/CRC)."""
    d = list(data)
    return " ".join("%02x" % b for b in [addr, len(d) + 3, 0x00, fun] + d)


def _homing_opts(allow=True):
    opts = test_options()
    opts["allow_write"] = "True" if allow else "False"
    return opts


def test_homing_frames_live():
    """PHASE=BEGIN/END senden exakt die Stock-Frames (Reihenfolge + Bytes)."""
    for addr, fun, payload, live in LIVE_ECHOS:
        assert T.build_frame(addr, fun, payload).hex(" ") == live, (live, T.build_frame(addr, fun, payload).hex(" "))
    cfg, gpio, bus, mcu, rs485, transp, ctl, events = build_system(opts=_homing_opts())
    log = ctl.homing_begin()
    got = [_wire(a, f, d) for a, f, d, _k in bus.calls]
    assert got == LIVE_HOMING_BEGIN, got
    assert ctl.homing_active and ctl.stall_mode["x"] == 1 and ctl.stall_mode["y"] == 1
    assert log[0] == "stall mode change motor81: send:01 recv:01", log
    assert all(k["retries"] == 0 for _a, _f, _d, k in bus.calls), "Stock: retries=0"
    assert bus.values[(0x81, 70)] == 300.0
    n0 = len(bus.calls)
    log2, errors = ctl.homing_end()
    assert errors == [], errors
    got = [_wire(a, f, d) for a, f, d, _k in bus.calls[n0:]]
    assert got == LIVE_HOMING_END_RS485, got
    assert [w.hex(" ") for w in mcu.writes] == [LIVE_HOMING_END_E], mcu.writes
    assert not ctl.homing_active and ctl.stall_mode["x"] == 2 and ctl.stall_mode["y"] == 2
    assert bus.values[(0x81, 70)] == 100.0
    # kein 06 [03], kein Frame an 0x85, nichts an Z/Z1 außer 0C [0b]
    allf = [(a, f, tuple(d)) for a, f, d, _k in bus.calls]
    assert (0x81, 0x06, (3,)) not in allf and all(a != 0x85 for a, _f, _d in allf)
    assert not any(a in (0x83, 0x84) and f == 0x11 for a, f, _d in allf)
    return "BEGIN 3 + END 7 RS485 + 1 transparent Frame = Live 14:06:21/31 (Echo-CRCs ok)"


def test_homing_kp_config():
    """Restore-Kp aus x_controller_pos_loop_pid_param_kp, homing_pos_kp-Option."""
    opts = _homing_opts()
    opts["x_controller_pos_loop_pid_param_kp"] = "120"
    opts["homing_pos_kp"] = "250"
    cfg, gpio, bus, mcu, rs485, transp, ctl, events = build_system(opts=opts)
    ctl.homing_begin()
    assert bus.values[(0x81, 70)] == 250.0
    ctl.homing_end()
    assert bus.values[(0x81, 70)] == 120.0
    assert K.parse_options(test_options())["homing_pos_kp"] == 300.0
    for bad in ("0", "-5", "abc", "1e9", "nan"):
        o = test_options()
        o["homing_pos_kp"] = bad
        try:
            K.parse_options(o)
        except ValueError:
            continue
        raise AssertionError("homing_pos_kp=%s akzeptiert" % bad)
    return "Kp 250 beim Homing, 120 (Config) danach; Default 300; 5 ungültige Werte abgelehnt"


def test_homing_sperren():
    """allow_write=False / unzulässige Werte: K2MotorError OHNE Bus-Zugriff."""
    cfg, gpio, bus, mcu, rs485, transp, ctl, events = build_system(opts=_homing_opts(False))
    for fn in (ctl.homing_begin, ctl.homing_end,
               lambda: ctl.set_stall_mode(["x", "y"], 1),
               lambda: ctl.write_sys_param("x", 70, 300.0)):
        try:
            fn()
        except K.K2MotorError as exc:
            assert "allow_write=False" in str(exc), exc
        else:
            raise AssertionError("ohne allow_write kein Fehler")
    assert bus.calls == [] and mcu.writes == [], "Bus-Zugriff trotz Sperre"
    cfg, gpio, bus, mcu, rs485, transp, ctl, events = build_system(opts=_homing_opts())
    bad = [lambda: ctl.set_stall_mode(["z"], 1),       # Z/Z1 nur Init
           lambda: ctl.set_stall_mode(["e"], 2),
           lambda: ctl.set_stall_mode(["x"], 3),       # 03 = Cut-Check, nicht freigegeben
           lambda: ctl.set_stall_mode(["x"], 4),
           lambda: ctl.set_stall_mode(["x"], True),
           lambda: ctl.write_sys_param("x", 9, 1.0),   # Kalibrierwert
           lambda: ctl.write_sys_param("x", 226, 1.0),  # Host-ID
           lambda: ctl.write_sys_param("x", 70, float("inf")),
           lambda: ctl.write_sys_param("x", 70, 1e39),
           lambda: ctl.write_sys_param("x", 70, 1e-50),
           lambda: ctl.write_sys_param("e", 70, 100.0),
           lambda: ctl.read_sys_param("x", 226),
           lambda: ctl.get_stall_mode("e")]
    for fn in bad:
        try:
            fn()
        except K.K2MotorError:
            continue
        raise AssertionError("ungültiger Aufruf akzeptiert")
    assert bus.calls == [] and mcu.writes == [], bus.calls
    return "4 Schreibwege gesperrt ohne allow_write, %d ungültige Aufrufe ohne Frame" % len(bad)


def test_homing_rueckbau():
    """BEGIN-Fehler (Y tot) -> Rückbau X/Y auf 2, kein Kp-Write, homing_active False."""
    cfg, gpio, bus, mcu, rs485, transp, ctl, events = build_system(opts=_homing_opts(), dead={0x82})
    try:
        ctl.homing_begin()
    except K.K2MotorError as exc:
        msg = str(exc)
    else:
        raise AssertionError("BEGIN ohne Fehler trotz totem Y")
    got = [_wire(a, f, d) for a, f, d, _k in bus.calls]
    assert got == ["81 04 00 11 01", "82 04 00 11 01", "81 04 00 11 02", "82 04 00 11 02"], got
    assert "keine Bewegung" in msg and "Rückbau" in msg, msg
    assert not ctl.homing_active and ctl.stall_mode["x"] == 2 and ctl.stall_mode["y"] is None
    # Echo falsch bei X -> Fehler, Rückbau
    cfg, gpio, bus, mcu, rs485, transp, ctl, events = build_system(opts=_homing_opts())
    bus.bad_echo.add(0x81)
    try:
        ctl.homing_begin()
    except K.K2MotorError as exc:
        assert "Echo 07 statt 01" in str(exc), exc
    else:
        raise AssertionError("falsches Echo akzeptiert")
    return "Y tot: %d Frames (01,01,02,02), Meldung '%s…'" % (len(got), msg[:60])


def test_homing_end_fehler():
    """END läuft komplett durch: Schutzbit an Z -> Fehler, Kp trotzdem zurück."""
    cfg, gpio, bus, mcu, rs485, transp, ctl, events = build_system(opts=_homing_opts())
    ctl.homing_begin()
    bus.prot[0x83] = struct.pack("<II", 1 << 8, 0)  # key789 Schleppfehler
    log, errors = ctl.homing_end()
    assert len(errors) == 1 and "Schutz z" in errors[0] and "key789" in errors[0], errors
    assert bus.values[(0x81, 70)] == 100.0
    assert ctl.last_error == errors[0]
    # Schutz-Timeout (E) nur Hinweis:
    cfg, gpio, bus, mcu, rs485, transp, ctl, events = build_system(opts=_homing_opts())
    ctl.homing_begin()
    mcu_exchange = mcu.exchange
    mcu.exchange = lambda w, t: b""
    log, errors = ctl.homing_end()
    mcu.exchange = mcu_exchange
    assert errors == [] and any("Schutz e 0C [0b]: keine Antwort" in l for l in log), (log, errors)
    return "Schutzbit key789 -> Fehler, Kp 100 trotzdem; E-Timeout nur Hinweis"


class FakeBatchBus:
    """Wie [k2_485] im Batch-Modus: jeder Zugriff RuntimeError."""

    def __init__(self):
        self.calls = 0

    def transact_full(self, *a, **kw):
        self.calls += 1
        raise RuntimeError("[k2_485] Batch-Modus: RS485-Port /dev/serial/by-id/x wird nicht geöffnet")


def _huelle(allow=True, bus=None):
    bus = bus if bus is not None else FakeRs485Bus(gpio=FakeGpio())
    printer = FakePrinter(bus=bus)
    opts = _huelle_opts()
    opts["allow_write"] = "True" if allow else "False"
    obj = K.load_config(FakeKConfig(printer, opts, sections=("k2_485",)))
    printer.fire_connect()
    return printer, obj, bus


def _expect_cmd_error(fn, params, *needles):
    g = FakeKGcmd(params)
    try:
        fn(g)
    except FakeCmdError as exc:
        for n in needles:
            assert n in str(exc), (n, str(exc))
        return str(exc), g
    raise AssertionError("kein G-Code-Fehler: %s %r" % (fn.__name__, params))


def test_huelle_homing():
    """G-Codes: Sperre ohne allow_write, BEGIN/END, Lesen, Auto-Rückbau, Batch."""
    # 1. allow_write=False: alle Schreib-G-Codes -> G-Code-Fehler, KEIN Frame
    printer, obj, bus = _huelle(allow=False)
    for fn, params in ((obj.cmd_homing, {"PHASE": "BEGIN"}), (obj.cmd_homing, {"PHASE": "END"}),
                       (obj.cmd_stall_mode, {"DATA": 1}),
                       (obj.cmd_sys_param, {"MOTOR": "x", "ID": 70, "VALUE": 300})):
        _expect_cmd_error(fn, params, "allow_write=False", "nichts gesendet")
    assert bus.calls == [], bus.calls
    assert obj.get_status()["allow_write"] is False
    # Lesen geht ohne allow_write:
    g = FakeKGcmd({})
    obj.cmd_stall_mode(g)
    assert g.infos == ["[k2_motor] x: stall_mode=2", "[k2_motor] y: stall_mode=2"], g.infos
    g = FakeKGcmd({"MOTOR": "x", "ID": 70})
    obj.cmd_sys_param(g)
    assert g.infos and "controller_pos_loop_pid_param_kp" in g.infos[0], g.infos
    assert [_wire(a, f, d) for a, f, d, _k in bus.calls] == [
        "81 04 00 11 04", "82 04 00 11 04", "81 05 00 06 02 46"]
    # 2. allow_write=True: BEGIN/END über die Hülle
    printer, obj, bus = _huelle(allow=True)
    th = printer.toolhead
    g = FakeKGcmd({"PHASE": "BEGIN"})
    obj.cmd_homing(g)
    assert th.wait_moves_calls == 1, "vor dem Moduswechsel Bewegungen abwarten"
    st = obj.get_status()
    assert st["homing_active"] and st["stall_mode"]["x"] == 1 and st["homing_pos_kp"] == 300.0, st
    obj.cmd_homing(FakeKGcmd({"PHASE": "END"}))
    assert not obj.get_status()["homing_active"]
    _expect_cmd_error(obj.cmd_homing, {"PHASE": "MITTE"}, "BEGIN oder END")
    _expect_cmd_error(obj.cmd_stall_mode, {"DATA": 3}, "DATA=3")
    _expect_cmd_error(obj.cmd_stall_mode, {"DATA": 1, "MOTOR": "z"}, "MOTOR=z")
    _expect_cmd_error(obj.cmd_sys_param, {"MOTOR": "x", "ID": 9, "VALUE": 1}, "nicht freigegeben")
    # 3. Auto-Rückbau: BEGIN, dann G-Code-Fehler (z.B. G28 Y ohne Trigger)
    n0 = len(bus.calls)
    obj.cmd_homing(FakeKGcmd({"PHASE": "BEGIN"}))
    n1 = len(bus.calls)
    printer.send_event("gcode:command_error")
    got = [_wire(a, f, d) for a, f, d, _k in bus.calls[n1:]]
    assert got == ["81 04 00 11 02", "82 04 00 11 02", "81 09 00 06 01 46 00 00 c8 42"], got
    assert any("Homing abgebrochen" in i for i in printer.gcode.infos), printer.gcode.infos
    assert not obj.get_status()["homing_active"]
    printer.send_event("gcode:command_error")  # zweiter Fehler: nichts mehr senden
    assert len(bus.calls) == n1 + 3 and n1 - n0 == 3
    # 4. Batch-Modus ohne Port: klare Meldung, kein Absturz, nichts aktiv
    printer, obj, bus = _huelle(allow=True, bus=FakeBatchBus())
    msg, _g = _expect_cmd_error(obj.cmd_homing, {"PHASE": "BEGIN"}, "Batch-Modus",
                                "keine Bewegung", "Homing-Vorbereitung fehlgeschlagen")
    assert not obj.get_status()["homing_active"] and bus.calls == 3, bus.calls  # 01 x + Rückbau x,y
    _expect_cmd_error(obj.cmd_stall_mode, {}, "Batch-Modus")
    return "Sperre/Lesen/BEGIN-END/Auto-Rückbau/Batch ok (%s…)" % msg[:50]


# =====================================================================
# Schnittpositions-Kalibrierung (KTWO_MOTOR_CUT_CAL / CALIBRATE_CUT_POS)
# =====================================================================

# Live-Beleg arbeitsdateien-lokal/cutpos-live-20261016-1601.log (Display-
# Kalibrierung 16.10.2026 22:03:17–22:04:24): Requests "retries = 0, cmd = …"
LIVE_CUTCAL = [
    "81 09 00 06 01 46 00 00 96 43",   # Z.945  Kp 300 (Stock-Makro)
    "81 04 00 11 04",                  # Z.957  Stall-Mode lesen
    "81 04 00 11 03",                  # Z.981  Stall-Mode 3
    "81 04 00 11 02",                  # Z.1476 zurück auf 2
    "81 09 00 06 01 46 00 00 c8 42",   # Z.1489 Kp 100 (Stock-Makro)
]
LIVE_CUTCAL_ECHOS = [  # (addr, fun, payload, Live-Antwort-Vollframe)
    (0x81, 0x06, [0x00, 0x00, 0x96, 0x43], "f7 81 07 00 06 00 00 96 43 09"),  # Z.950
    (0x81, 0x11, [0x02, 0x00], "f7 81 05 00 11 02 00 0e"),                   # Z.959
    (0x81, 0x11, [0x03], "f7 81 04 00 11 03 13"),                            # Z.983
    (0x81, 0x11, [0x02], "f7 81 04 00 11 02 14"),                            # Z.1478
    (0x81, 0x06, [0x00, 0x00, 0xc8, 0x42], "f7 81 07 00 06 00 00 c8 42 d4"),  # Z.1492
]


class FakeCutMotion:
    """Bewegungs-Fake: Schneidhebel-Anschlag bei ``stop_x`` – ein Anstoß
    unter stop_x löst den Stall aus (Endstop bzw. erst im Nachlauf)."""

    def __init__(self, bus=None, stop_x=-8.05, homed="xyz", rail=(-10.0, 352.5),
                 max_accel=30000.0, late=False, pin_active=False, fail_at=None,
                 no_move_at=None, stop_list=None):
        self.bus = bus
        self.stop_x = stop_x
        self.stop_list = stop_list  # explizite Trefferliste (überstimmt stop_x)
        self.homed = homed
        self.rail = rail
        self.limits = (800.0, float(max_accel))
        self.late = late
        self.pin_active = pin_active
        self.fail_at = fail_at
        self.no_move_at = no_move_at
        self.pos = [100.0, 100.0, 10.0, 0.0]
        self.calls = []  # (art, …, n_bus_frames)

    def _n(self):
        return len(self.bus.calls) if self.bus is not None else 0

    def homed_axes(self):
        return self.homed

    def x_range(self):
        return self.rail

    def get_position(self):
        return list(self.pos)

    def get_limits(self):
        return self.limits

    def set_limits(self, v, a):
        self.calls.append(("limits", v, a, self._n()))
        self.limits = (self.limits[0] if v is None else v, self.limits[1] if a is None else a)

    def wait_moves(self):
        self.calls.append(("wait", self._n()))

    def move(self, x=None, y=None, speed=None):
        self.calls.append(("move", x, y, speed, self._n()))
        if x is not None:
            self.pos[0] = x
        if y is not None:
            self.pos[1] = y

    def stall_pin_active(self):
        return self.pin_active

    def probe_x(self, x, speed):
        self.calls.append(("probe", x, speed, self._n()))
        if self.fail_at is not None and abs(x - self.fail_at) < 1e-9:
            self.pos[0] = x + 0.3
            raise RuntimeError("Move out of range: %.3f" % x)
        if self.no_move_at is not None and abs(x - self.no_move_at) < 1e-9:
            return {"triggered": True, "late": False, "no_movement": True, "trig_x": self.pos[0]}
        if self.stop_list is not None:
            trig = any(abs(x - s) < 1e-9 for s in self.stop_list)
        else:
            trig = x < self.stop_x
        self.pos[0] = (self.stop_x if self.stop_list is None else x) if trig else x
        return {"triggered": trig and not self.late, "late": trig and self.late,
                "no_movement": False, "trig_x": self.pos[0] if trig and not self.late else None}

    def probes(self):
        return [c[1] for c in self.calls if c[0] == "probe"]


def _cutcal_params(**over):
    p = K.build_cut_cal_params(K.parse_options(test_options())["cut_cal"])
    p.update(over)
    return p


def _cutcal_run(motion_kw=None, p_over=None, opts=None, bad_echo=(), **bus_kw):
    cfg, gpio, bus, mcu, rs485, transp, ctl, events = build_system(
        opts=opts if opts is not None else _homing_opts(), **bus_kw)
    bus.bad_echo.update(bad_echo)
    motion = FakeCutMotion(bus=bus, **(motion_kw or {}))
    saved = []
    p = _cutcal_params(**(p_over or {}))
    try:
        res = ctl.cut_calibrate(motion, p, saved.append)
        err = None
    except K.K2MotorError as exc:
        res, err = None, str(exc)
    return ctl, bus, mcu, motion, saved, res, err


def test_cutcal_frames_live():
    """Erfolg: Frames = Live 22:03/22:04 (inkl. Echo-CRCs), Ablauf/Bewegung,
    Ergebnis -8,2 + 0,4 = -7,80, configfile-Wert "-7.80"."""
    for addr, fun, payload, live in LIVE_CUTCAL_ECHOS:
        got = T.build_frame(addr, fun, payload).hex(" ")
        assert got == live, (live, got)
    ctl, bus, mcu, motion, saved, res, err = _cutcal_run()
    assert err is None, err
    got = [_wire(a, f, d) for a, f, d, _k in bus.calls]
    assert got == LIVE_CUTCAL, got
    assert all(k["retries"] == 0 for _a, _f, _d, k in bus.calls), "Stock: retries=0"
    assert mcu.writes == [], "nichts an E (transparent)"
    assert res["first_hit"] == -8.1 and res["second_hit"] == -8.2, res
    assert res["result"] == -7.8 and saved == [-7.8], (res, saved)
    assert K.CUT_CAL_FORMAT % saved[0] == "-7.80"
    assert res["orig_mode"] == 2 and res["probes"] == 33, res   # -5.0 … -8.2 (live 33)
    xs = motion.probes()
    assert xs[0] == -5.0 and xs[-1] == -8.2 and len(xs) == 33, xs
    assert all(abs((xs[i] - xs[i + 1]) - 0.1) < 1e-9 for i in range(len(xs) - 1))
    assert all(c[2] == K.CUT_CAL_DEFAULTS["probe_speed"] for c in motion.calls if c[0] == "probe")
    # Reihenfolge Bus <-> Bewegung (n = Anzahl Frames zum Zeitpunkt)
    c = motion.calls
    assert c[0] == ("wait", 0) and c[1] == ("limits", None, 25000.0, 0), c[:2]
    park = [x for x in c if x[0] == "move" and x[2] == 200.0]
    assert park[0][1:] == (50.0, 200.0, 200.0, 1), park[0]      # nach Kp, vor 11 [04]
    assert all(x[3] == 3 for x in c if x[0] == "probe"), "Anstöße nur in Mode 3"
    # jeder Anstoß -> zurück auf Park X50
    for i, x in enumerate(c):
        if x[0] == "probe":
            assert c[i + 1][0] == "move" and c[i + 1][1] == 50.0, c[i + 1]
    assert park[-1][1:] == (200.0, 200.0, 200.0, 3), park[-1]   # Ausgang X200/Y200 (live)
    assert c[-1] == ("limits", 800.0, 30000.0, 5), c[-1]        # Beschleunigung zurück
    assert ctl.stall_mode["x"] == 2 and bus.values[(0x81, 70)] == 100.0
    assert not ctl.cut_cal_active and ctl.cut_cal_last["saved"]
    # Nachlauf-Treffer (Stall erst nach dem letzten Schritt) zählt genauso
    ctl, bus, mcu, motion, saved, res, err = _cutcal_run({"late": True})
    assert err is None and saved == [-7.8], (err, saved)
    assert any("(Nachlauf)" in l for l in res["log"]), res["log"]
    return "5 Frames = Live Z.945/957/981/1476/1489, 33 Anstöße -5.0…-8.2, cut_pos_x -7.80"


def test_cutcal_rueckbau():
    """Fehler an jeder Stelle -> Kopf weg, Mode 2, Kp 100, Beschleunigung
    zurück, NICHTS gespeichert, K2MotorError (kein Shutdown)."""
    rb = ["81 04 00 11 02", "81 09 00 06 01 46 00 00 c8 42"]
    head = LIVE_CUTCAL[:3]
    cases = []
    # 1. kein Stall bis x_min (Abbruch nach max. Weg)
    ctl, bus, mcu, motion, saved, res, err = _cutcal_run({"stop_x": -20.0})
    assert err and "kein Stall bis X=-9.50" in err and "Abbruch nach max. Weg" in err, err
    assert len(motion.probes()) == 46 and min(motion.probes()) == -9.5
    assert [_wire(a, f, d) for a, f, d, _k in bus.calls] == head + rb and saved == []
    cases.append("kein Stall")
    # 2. nur ein Stall (auf dem letzten Anstoß)
    ctl, bus, mcu, motion, saved, res, err = _cutcal_run({"stop_list": [-9.5]})
    assert err and "nur ein Stall (X=-9.50)" in err and saved == [], err
    cases.append("ein Stall")
    # 3. Ergebnis außerhalb: Treffer -5.5/-5.6 -> -5.2 > x_max -5.5
    ctl, bus, mcu, motion, saved, res, err = _cutcal_run({"stop_x": 0.0})
    assert err and "außerhalb check_cut_pos_x" in err and saved == [], err
    assert ctl.cut_cal_last["ignored"] == [-5.0, -5.1, -5.2, -5.3, -5.4], ctl.cut_cal_last
    assert [_wire(a, f, d) for a, f, d, _k in bus.calls] == head + rb
    cases.append("Ergebnis -5.2 außerhalb")
    # 4. Bewegungsfehler mitten im Anstoß -> Kopf zurück auf Park, dann Rückbau
    ctl, bus, mcu, motion, saved, res, err = _cutcal_run({"fail_at": -6.0})
    assert err and "Move out of range" in err and "Kopf auf Park X50.0" in err, err
    i = [k for k, c in enumerate(motion.calls) if c[0] == "probe" and c[1] == -6.0][0]
    assert motion.calls[i + 1][:2] == ("move", 50.0) and motion.calls[i + 1][4] == 3, \
        "Kopf weg vom Hebel VOR Mode 2"
    assert [_wire(a, f, d) for a, f, d, _k in bus.calls] == head + rb and saved == []
    assert motion.limits == (800.0, 30000.0)
    cases.append("Bewegungsfehler")
    # 5. Stall-Pin schon vor der ersten Fahrt aktiv -> keine Anstöße
    ctl, bus, mcu, motion, saved, res, err = _cutcal_run({"pin_active": True})
    assert err and "VOR der ersten Fahrt" in err and motion.probes() == [], err
    assert [_wire(a, f, d) for a, f, d, _k in bus.calls] == head + rb
    cases.append("Pin aktiv")
    # 6. no_movement bei einem Anstoß
    ctl, bus, mcu, motion, saved, res, err = _cutcal_run({"no_move_at": -5.3})
    assert err and "schon vor der Bewegung" in err and max(motion.probes()) == -5.0
    cases.append("no_movement")
    # 7. Echo falsch bei 0x11 [03] -> Rückbau (Mode 2 + Kp), keine Bewegung zum Hebel
    ctl, bus, mcu, motion, saved, res, err = _cutcal_run(bad_echo={0x81})
    assert err and "Echo 07 statt 03" in err and motion.probes() == [], err
    assert [_wire(a, f, d) for a, f, d, _k in bus.calls] == head + rb, bus.calls
    cases.append("Echo 0x11")
    # 8. Bus tot -> Kp-Write scheitert, Rückbau versucht Kp, kein Mode-Frame, keine Fahrt
    ctl, bus, mcu, motion, saved, res, err = _cutcal_run(dead={0x81})
    got = [_wire(a, f, d) for a, f, d, _k in bus.calls]
    assert got == [LIVE_CUTCAL[0], LIVE_CUTCAL[4]], got
    assert err and "Rückbau-FEHLER" in err and not any(c[0] == "move" for c in motion.calls), err
    assert motion.limits == (800.0, 30000.0) and not ctl.cut_cal_active
    cases.append("Bus tot")
    # 9. gelesener Mode 3 (Rest eines Abbruchs) -> trotzdem auf 2 zurück
    cfg, gpio, bus, mcu, rs485, transp, ctl, events = build_system(opts=_homing_opts())
    bus.modes[0x81] = 3
    motion = FakeCutMotion(bus=bus)
    res = ctl.cut_calibrate(motion, _cutcal_params(), lambda v: None)
    assert res["orig_mode"] == 3 and bus.modes[0x81] == 2 and ctl.stall_mode["x"] == 2
    cases.append("orig 3 -> 2")
    return "%d Fehlerfälle: %s" % (len(cases), ", ".join(cases))


def test_cutcal_grenzen():
    """Harte Grenzen: Optionen, [k2_box]-Vorrang, nie tiefer als x_min/-10,
    max. 100 Anstöße, Geschwindigkeit <= 200 mm/s, Beschleunigung <= max_accel."""
    d = K.parse_options(test_options())["cut_cal"]
    assert d == {k: K.CUT_CAL_DEFAULTS[k] for k in d} and len(d) == 10, d
    bad = [("cut_cal_probe_speed", "250"), ("cut_cal_probe_speed", "201"),
           ("cut_cal_probe_speed", "abc"), ("cut_cal_step", "0"), ("cut_cal_step", "0.01"),
           ("cut_cal_step", "1"), ("cut_cal_accel", "30000"), ("cut_cal_start_x", "-11"),
           ("cut_cal_start_x", "1"), ("cut_pos_offset", "-0.1"), ("cut_pos_offset", "3"),
           ("cut_cal_park_x", "150"), ("cut_cal_settle", "1"), ("cut_cal_pos_kp", "0"),
           ("cut_cal_exit_x", "350"), ("cut_cal_park_speed", "nan")]
    n_bad = 0
    for k, v in bad:
        o = test_options()
        o[k] = v
        try:
            K.parse_options(o)
        except ValueError:
            n_bad += 1
            continue
        raise AssertionError("%s=%s akzeptiert" % (k, v))
    o = test_options()
    o.update({"cut_cal_start_x": "-7.9", "cut_cal_probe_speed": "50", "cut_pos_offset": "0.5"})
    c = K.parse_options(o)["cut_cal"]
    assert c["start_x"] == -7.9 and c["probe_speed"] == 50.0 and c["offset"] == 0.5
    # [k2_box] hat Vorrang für Prüfbereich und Y
    p = K.build_cut_cal_params(c, {"check_cut_pos_x_min": -9.0, "check_cut_pos_x_max": -6.0,
                                   "cut_pos_y": 210.0, "cut_pos_x": -7.8})
    assert (p["x_min"], p["x_max"], p["y"]) == (-9.0, -6.0, 210.0), p
    xs = K.cut_cal_positions(p)
    assert xs[0] == -7.9 and xs[-1] == -9.0 and min(xs) >= -9.0, xs
    for box in ({"check_cut_pos_x_min": -10.5}, {"check_cut_pos_x_min": -5.0},
                {"cut_pos_y": 20.0}, {"check_cut_pos_x_max": "x"}):
        try:
            K.build_cut_cal_params(None, box)
        except K.K2MotorError:
            continue
        raise AssertionError("k2_box %r akzeptiert" % box)
    for over in ({"step": 0.05, "x_min": -10.0},          # 101 Anstöße > 100
                 {"park_x": -1.0}, {"exit_x": 10.0}, {"start_x": -9.6},
                 {"probe_speed": 200.1}, {"accel": 25001.0}, {"x_min": -10.01}):
        p = _cutcal_params()
        p.update(over)
        try:
            K.validate_cut_cal_params(p)
        except K.K2MotorError:
            continue
        raise AssertionError("Parameter %r akzeptiert" % over)
    assert len(K.cut_cal_positions(_cutcal_params())) == 46       # Stock -5.0…-9.5
    # Beschleunigung nie über max_accel des Druckers
    ctl, bus, mcu, motion, saved, res, err = _cutcal_run({"max_accel": 8000.0})
    assert err is None and ("limits", None, 8000.0, 0) in motion.calls, motion.calls[:3]
    # [stepper_x] position_min tiefer als x_min nötig -> Abbruch ohne Frame
    ctl, bus, mcu, motion, saved, res, err = _cutcal_run({"rail": (-9.0, 352.5)})
    assert err and "außerhalb [stepper_x]" in err and bus.calls == [] and motion.calls == [], err
    return "%d ungültige Optionen, k2_box-Vorrang, 46 Anstöße Stock, accel<=max_accel" % n_bad


def test_cutcal_sperren():
    """Ohne allow_write / bei aktivem Homing / ohne X/Y-Referenz: Fehler OHNE
    Frame und OHNE Bewegung."""
    ctl, bus, mcu, motion, saved, res, err = _cutcal_run(opts=_homing_opts(False))
    assert err and "allow_write=False" in err, err
    assert bus.calls == [] and motion.calls == [] and saved == []
    for homed in ("", "z", "x", "xz"):
        ctl, bus, mcu, motion, saved, res, err = _cutcal_run({"homed": homed})
        assert err and "nicht referenziert" in err and bus.calls == [] and motion.calls == [], err
    cfg, gpio, bus, mcu, rs485, transp, ctl, events = build_system(opts=_homing_opts())
    ctl.homing_begin()
    n0 = len(bus.calls)
    motion = FakeCutMotion(bus=bus)
    try:
        ctl.cut_calibrate(motion, _cutcal_params(), lambda v: None)
    except K.K2MotorError as exc:
        assert "Homing aktiv" in str(exc), exc
    else:
        raise AssertionError("Cut-Kalibrierung trotz aktivem Homing")
    assert len(bus.calls) == n0 and motion.calls == []
    # Mode 3 bleibt über die öffentlichen Wege gesperrt
    try:
        ctl.set_stall_mode(["x"], 3)
    except K.K2MotorError:
        pass
    else:
        raise AssertionError("KTWO_MOTOR_STALL_MODE DATA=3 akzeptiert")
    return "allow_write/Homing/4x homed_axes gesperrt, kein Frame, keine Bewegung"


class FakeConfigfile:
    def __init__(self):
        self.sets = []

    def set(self, section, option, value):
        self.sets.append((section, option, value))


class FakeMacro:
    def __init__(self, **variables):
        self.variables = dict(variables)

    def get_status(self, eventtime=None):
        return dict(self.variables)


class FakeBox:
    def __init__(self, cfg):
        self.cfg = dict(cfg)


def test_huelle_cutcal():
    """KTWO_MOTOR_CUT_CAL über die Hülle: DRY_RUN, Sperren (allow_write,
    _PI_FREIGABE fehlt/False), Erfolg mit configfile.set, Fehler = G-Code-
    Fehler, Status printer.k2_motor.cut_cal."""
    printer, obj, bus = _huelle(allow=True)
    cf = FakeConfigfile()
    printer.objects["configfile"] = cf
    printer.objects["k2_box"] = FakeBox({"check_cut_pos_x_min": -9.5,
                                         "check_cut_pos_x_max": -5.5, "cut_pos_y": 200.0})
    motions = []

    def _mk(p):
        m = FakeCutMotion(bus=bus)
        motions.append(m)
        return m
    obj._make_cut_motion = _mk
    # DRY_RUN: Plan, kein Frame, keine Bewegung (auch ohne Freigabe)
    g = FakeKGcmd({"DRY_RUN": 1})
    obj.cmd_cut_cal(g)
    plan = g.infos[0]
    for needle in ("81 09 00 06 01 46 00 00 96 43", "81 04 00 11 04", "81 04 00 11 03",
                   "81 04 00 11 02", "81 09 00 06 01 46 00 00 c8 42", "46 Anstöße",
                   "X-5.00…X-9.50", "configfile.set"):
        assert needle in plan, (needle, plan)
    assert bus.calls == [] and motions == []
    # Freigabe-Makro fehlt -> gesperrt
    _expect_cmd_error(obj.cmd_cut_cal, {}, "_PI_FREIGABE fehlt", "keine Bewegung")
    printer.objects["gcode_macro _PI_FREIGABE"] = FakeMacro(allow_move=False)
    _expect_cmd_error(obj.cmd_cut_cal, {}, "allow_move=False", "nichts gesendet")
    assert bus.calls == [] and motions == [] and cf.sets == []
    # Erfolg
    printer.objects["gcode_macro _PI_FREIGABE"].variables["allow_move"] = True
    g = FakeKGcmd({})
    obj.cmd_cut_cal(g)
    assert cf.sets == [("k2_box", "cut_pos_x", "-7.80")], cf.sets
    assert [_wire(a, f, d) for a, f, d, _k in bus.calls] == LIVE_CUTCAL
    assert any("cut_pos_x = -7.80 -> SAVE_CONFIG" in i for i in g.infos), g.infos
    st = obj.get_status()["cut_cal"]
    assert st["result"] == -7.8 and st["saved"] and st["probes"] == 33 and not st["active"], st
    # Fehler -> G-Code-Fehler mit Rückbau, Status zeigt Fehler, kein set
    obj._make_cut_motion = lambda p: FakeCutMotion(bus=bus, stop_x=-20.0)
    n0 = len(bus.calls)
    msg, g = _expect_cmd_error(obj.cmd_cut_cal, {}, "kein Stall", "Rückbau", "nichts gespeichert")
    assert [_wire(a, f, d) for a, f, d, _k in bus.calls[n0:]] == LIVE_CUTCAL, bus.calls[n0:]
    assert len(cf.sets) == 1 and obj.get_status()["cut_cal"]["error"]
    assert any("get_stall_mode" in i for i in g.infos), g.infos
    # allow_write=False -> gesperrt, auch mit Freigabe
    printer, obj, bus = _huelle(allow=False)
    printer.objects["gcode_macro _PI_FREIGABE"] = FakeMacro(allow_move=True)
    obj._make_cut_motion = _mk
    n_m = len(motions)
    _expect_cmd_error(obj.cmd_cut_cal, {}, "allow_write=False", "keine Bewegung")
    assert bus.calls == [] and len(motions) == n_m
    # Batch ohne Port -> Kp-Write scheitert, klare Meldung, kein Absturz
    printer, obj, bus = _huelle(allow=True, bus=FakeBatchBus())
    printer.objects["gcode_macro _PI_FREIGABE"] = FakeMacro(allow_move=True)
    printer.objects["configfile"] = FakeConfigfile()
    obj._make_cut_motion = lambda p: FakeCutMotion()
    _expect_cmd_error(obj.cmd_cut_cal, {}, "Batch-Modus", "nichts gespeichert")
    assert not obj.get_status()["cut_cal"]["active"]
    return "DRY_RUN/2 Freigabe-Sperren/Erfolg configfile -7.80/Fehler-Rückbau/allow_write/Batch"


class _FakeEs:
    def __init__(self, trig_time, pin=0):
        self.trig_time = trig_time
        self.pin = pin
        self.queries = []

    def get_steppers(self):
        return ["s"]

    def home_start(self, *a, **kw):
        return None

    def home_wait(self, t):
        return self.trig_time

    def query_endstop(self, pt):
        self.queries.append(pt)
        return self.pin


class _FakeHomingModule:
    """Ersatz für klippy/extras/homing.py (HomingMove-Schnittstelle)."""

    def __init__(self):
        self.moves = []
        self.no_move = None

    def HomingMove(self, printer, endstops, toolhead):
        mod = self

        class _HM:
            def homing_move(self_, pos, speed, probe_pos=False, triggered=True,
                            check_triggered=True):
                es, name = endstops[0]
                mod.moves.append((list(pos), speed, probe_pos, triggered, check_triggered, name))
                es.home_start(0.0, 0.000015, 4, 0.001, triggered=triggered)
                t = es.home_wait(1.0)
                return [pos[0] + (0.3 if t > 0 else 0.0)] + list(pos[1:])

            def check_no_movement(self_):
                return mod.no_move
        return _HM()


class _FakeRail:
    def __init__(self, es):
        self.es = es

    def get_endstops(self):
        return [(self.es, "stepper_x")]

    def get_range(self):
        return (-10.0, 352.5)


class _FakeKinToolhead:
    def __init__(self, es):
        self.kin = type("K", (), {"rails": [_FakeRail(es)]})()
        self.pos = [50.0, 200.0, 10.0, 0.0]
        self.dwells = []
        self.manual = []
        self.maxv = (800.0, 30000.0)

    def get_kinematics(self):
        return self.kin

    def get_position(self):
        return list(self.pos)

    def get_last_move_time(self):
        return 12.5

    def dwell(self, s):
        self.dwells.append(s)

    def manual_move(self, coord, speed):
        self.manual.append((list(coord), speed))

    def get_status(self, eventtime):
        return {"homed_axes": "xy"}

    def get_max_velocity(self):
        return self.maxv

    def set_max_velocities(self, v, a, scv, mcr):
        self.maxv = (self.maxv[0] if v is None else v, self.maxv[1] if a is None else a)

    def wait_moves(self):
        pass


def test_cutcal_klippy_adapter():
    """KlippyCutMotion: HomingMove mit probe_pos/check_triggered=False am
    X-Endstop, Trigger aus home_wait, Nachlauf = dwell + query_endstop."""
    hm = _FakeHomingModule()
    es = _FakeEs(trig_time=0.0, pin=0)
    th = _FakeKinToolhead(es)
    printer = FakePrinter()
    printer.objects["toolhead"] = th
    m = K.KlippyCutMotion(printer, settle=0.1, homing_module=hm)
    r = m.probe_x(-6.0, 166.0)
    assert r == {"triggered": False, "late": False, "no_movement": False, "trig_x": None}, r
    pos, speed, probe_pos, trig, check, name = hm.moves[0]
    assert pos[0] == -6.0 and pos[1] == 200.0 and speed == 166.0 and probe_pos is True \
        and trig is True and check is False and name == "stepper_x", hm.moves[0]
    assert th.dwells == [0.1] and es.queries == [12.5]
    es.pin = 1          # Stall erst im Nachlauf
    assert m.probe_x(-6.1, 166.0)["late"] is True
    es.trig_time = 3.2  # Endstop löst während der Fahrt aus
    r = m.probe_x(-8.1, 166.0)
    assert r["triggered"] and r["trig_x"] == -7.8 and not r["late"] and len(th.dwells) == 2, r
    hm.no_move = "stepper_x"
    assert m.probe_x(-8.2, 166.0)["no_movement"] is True
    assert m.homed_axes() == "xy" and m.x_range() == (-10.0, 352.5)
    m.set_limits(None, 25000.0)
    assert th.maxv == (800.0, 25000.0)
    m.move(x=50.0, speed=200.0)
    assert th.manual[-1] == ([50.0, None, None], 200.0)
    es.pin = 0
    assert m.stall_pin_active() is False

    def _boom(*a, **kw):
        raise RuntimeError("Must home axis first")
    th.manual_move = _boom
    try:
        m.move(x=1.0, speed=10.0)
    except K.K2MotorError as exc:
        assert "Must home axis first" in str(exc)
    else:
        raise AssertionError("Bewegungsfehler nicht als K2MotorError")
    return "probe_pos+check_triggered=False an stepper_x, Nachlauf dwell+query, Fehler->K2MotorError"


# =====================================================================
# Runner
# =====================================================================

TESTS = [
    ("transparent-Format vs. Live (CRC)", test_transparent_format),
    ("Sendeschutz Transport (Länge/Broadcast 0x01/0x0B)", test_transport_sendeschutz),
    ("kompletter Init-Lauf", test_init_lauf),
    ("Plan-Vergleich motor_init", test_plan_vergleich),
    ("Extruder via transparent 0x81 (nie 0x85)", test_extruder_transparent),
    ("allow_write=False schreibt nichts", test_allow_write_false),
    ("KTWO_MOTOR_STATUS liest 0x0F/0x0C/0x08", test_status_lesen),
    ("REVIEW 1: Config vor connect", test_review_config_vor_connect),
    ("REVIEW 2: print_time > 0", test_review_print_time),
    ("REVIEW 3: query + Fallback", test_review_query),
    ("Hülle Upstream (Namen/pins/MCU/cb/max_duration/k2_485)", test_huelle_upstream),
    ("Hülle ohne [k2_485] -> klare Meldung", test_huelle_ohne_bus),
    ("Homing: Frames = Live-Log (BEGIN/END)", test_homing_frames_live),
    ("Homing: Kp aus Config / homing_pos_kp", test_homing_kp_config),
    ("Homing: Sperren ohne Bus-Zugriff", test_homing_sperren),
    ("Homing: Rückbau bei BEGIN-Fehler", test_homing_rueckbau),
    ("Homing: END best effort", test_homing_end_fehler),
    ("Hülle: KTWO_MOTOR_HOMING/_STALL_MODE/_SYS_PARAM", test_huelle_homing),
    ("Cut-Kal.: Frames = Live-Log, Ablauf, -7.80", test_cutcal_frames_live),
    ("Cut-Kal.: Rückbau bei Fehlern", test_cutcal_rueckbau),
    ("Cut-Kal.: harte Grenzen", test_cutcal_grenzen),
    ("Cut-Kal.: Sperren ohne Frame/Bewegung", test_cutcal_sperren),
    ("Cut-Kal.: Hülle KTWO_MOTOR_CUT_CAL", test_huelle_cutcal),
    ("Cut-Kal.: Klipper-Adapter (HomingMove/Endstop)", test_cutcal_klippy_adapter),
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
