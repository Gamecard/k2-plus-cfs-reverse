#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bustest_lesend.py – erster Test unseres Protokollcodes am echten RS485-Bus.

Läuft AUF dem Drucker, während Klipper gestoppt ist (ttyS5 frei).
Live erfolgreich 16.10.2026: 78/78 Frames, 0 Timeouts/Retries/CRC-Fehler
(s. BUSTEST-BEFUND-2026-10-16.md). WICHTIG danach: Klipper starten UND
sofort FIRMWARE_RESTART (sonst antwortet der Extruder-Motor nicht, key798).
Nutzt nachbau/serial485 + pi-host/k2_box (K2BoxController) +
pi-host/k2_motor_init (K2MotorController.motor_status) über Rs485Transport.

Sicherheit: GuardBus lässt NUR eine feste Whitelist lesender Frames durch;
alles andere -> PermissionError, nichts gesendet. allow_write bleibt False.
"""
import json
import os
import sys
import time
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
# Layout A (Drucker-Testordner): BASE/{nachbau,pi-host}; Layout B (Repo): BASE = pi-host
for _d in (BASE, os.path.dirname(BASE)):
    for _sub in ("nachbau", "pi-host"):
        if os.path.isdir(os.path.join(_d, _sub)):
            sys.path.insert(0, os.path.join(_d, _sub))

import serial485                      # noqa: E402
import k2_transport                   # noqa: E402
import k2_box                         # noqa: E402
import k2_motor_init                  # noqa: E402

BOX = 0x01
MOTORS = (0x81, 0x82, 0x83, 0x84)


def allowed(addr, fun, data):
    d = list(data)
    if addr == BOX:
        if fun in (0x0A, 0x05, 0x14, 0x15) and d == []:
            return True
        if fun in (0x02, 0x03) and len(d) == 1 and 0 < d[0] <= 0x0F:
            return True
        if fun == 0x08 and d in ([0], [1]):
            return True
        if fun == 0x0E and d == [1]:          # Messrad LESEN (0 = nullen!)
            return True
        return False
    if addr in MOTORS:
        if fun == 0x0F and d == [0x00]:
            return True
        if fun == 0x0C and d == [0x0B]:
            return True
        if fun == 0x08 and len(d) == 1 and 1 <= d[0] <= 12:
            return True
        if fun == 0x12 and d == [0x02]:
            return True
        if fun == 0x06 and len(d) == 2 and d[0] == 0x02:
            return True
        return False
    return False


class GuardBus:
    def __init__(self, bus):
        self.bus = bus
        self.log = []

    def _check(self, addr, fun, data):
        if not allowed(addr, fun, data):
            raise PermissionError("GUARD: 0x%02X fun 0x%02X %r nicht erlaubt"
                                  % (addr, fun, list(data)))

    def transact_full(self, addr, fun, data=(), **kw):
        self._check(addr, fun, data)
        t0 = time.monotonic()
        try:
            r = self.bus.transact_full(addr, fun, data, **kw)
            self.log.append(("ok", addr, fun, list(data), r[1], bytes(r[3]).hex(),
                             round(time.monotonic() - t0, 3)))
            return r
        except Exception as e:
            self.log.append(("err", addr, fun, list(data), repr(e),
                             round(time.monotonic() - t0, 3)))
            raise

    def transact(self, addr, fun, data=(), **kw):
        return self.transact_full(addr, fun, data, **kw)[3]


def ttys5_holders():
    me = str(os.getpid())
    out = []
    for p in os.listdir("/proc"):
        if not p.isdigit() or p == me:
            continue
        try:
            for fd in os.listdir("/proc/%s/fd" % p):
                if os.readlink("/proc/%s/fd/%s" % (p, fd)) == "/dev/ttyS5":
                    out.append(p)
                    break
        except OSError:
            pass
    return out


def step(res, name, fn):
    try:
        v = fn()
        res[name] = v
        print("OK   %-22s %s" % (name, v))
    except Exception as e:
        res[name] = {"FEHLER": repr(e)}
        print("FEHL %-22s %r" % (name, e))


def main():
    h = ttys5_holders()
    if h:
        print("ABBRUCH: /dev/ttyS5 noch offen in PID %s" % h)
        return 3
    raw = serial485.Serial485("/dev/ttyS5")
    bus = GuardBus(raw)
    res = {}
    # Guard-Selbsttest (darf NICHT senden)
    try:
        bus.transact_full(0x81, 0x0E, [0x01])
        print("GUARD-FEHLER: Adress-Schreiben wurde durchgelassen!")
        return 4
    except PermissionError:
        print("OK   guard-selbsttest       Schreibframe blockiert")

    box = k2_box.K2BoxController({"allow_write": False}, bus)
    step(res, "box.version_sn", box.get_version_sn)
    for i in range(3):
        step(res, "box.poll_%d" % i, box.poll_status)
        time.sleep(0.5)
    step(res, "box.hardware", box.get_hardware_status)
    step(res, "box.sensor_A", lambda: box.get_sensor(0))
    step(res, "box.sensor_hub", lambda: box.get_sensor(1))
    step(res, "box.buffer", box.get_buffer)
    step(res, "box.wheel", box.get_wheel)
    step(res, "box.remain", box.get_remain)
    for s in "ABCD":
        step(res, "box.rfid_" + s, lambda s=s: box.get_rfid(s))

    tr = k2_transport.Rs485Transport(bus)
    mc = k2_motor_init.K2MotorController(
        {"motor_closed_loop": ["x", "y", "z", "z1"], "pins": {}},
        tr, None, None)
    step(res, "motor.status", mc.motor_status)
    for m in ("x", "y", "z", "z1"):
        def vars_(m=m):
            out = {}
            for idx in range(1, 13):
                _s, _f, d = mc._transact(m, 0x08, [idx], 1.0, 0)
                out[idx] = d.hex()
            return out
        step(res, "motor.%s.var1-12" % m, vars_)

    print("--- Bus-Statistik:", raw.stats())
    try:
        raw.disconnect()
    except Exception:
        pass
    with open(os.path.join(BASE, "ergebnis.json"), "w") as f:
        json.dump({"res": res, "frames": bus.log, "stats": raw.stats()}, f,
                  indent=1, default=str)
    print("Frames gesendet: %d (Fehler: %d)" % (
        len(bus.log), sum(1 for x in bus.log if x[0] == "err")))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
