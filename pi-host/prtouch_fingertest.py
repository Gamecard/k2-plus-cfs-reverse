#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prtouch_fingertest.py – schaltet die Düsen-MCU bei Kontakt die Swap-Leitung?

Läuft auf dem Drucker (klippy-env-Python), Klipper-Dienst GESTOPPT.
- Main-MCU (ttyS2): reset, NUR config_endstop auf PC7 (Eingang, wie Stock
  pull_up=0), Pegel per endstop_query_state abfragen.
- Düsen-MCU (ttyS3): reset, NUR config_prtouch_pres (wie Stock), Antast-
  Modus mit unseren Parametern scharf schalten, Rohwerte blockweise lesen.
Messfenster WINDOW_S: Benutzer drückt die KALTE Düse mehrmals leicht von unten.
Keine Bewegung, keine Heizung, keine Stepper konfiguriert. Ende: beide reset.
"""
import json
import os
import sys
import traceback

sys.path.insert(0, "/usr/share/klipper/klippy")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "pi-host"))
sys.path.insert(0, os.path.join(HERE, "nachbau"))

import reactor as R          # noqa: E402
import serialhdl              # noqa: E402
import clocksync              # noqa: E402
import k2_prtouch as KP       # noqa: E402

WINDOW_S = 100.0
OUT = {"log": [], "pc7": [], "pres": [], "unsolicited": [], "events": []}
SERS = []


def log(*a):
    s = " ".join(str(x) for x in a)
    OUT["log"].append(s)
    print(s, flush=True)


def q(ser, cmd, resp, oid=0):
    mp = ser.get_msgparser()
    src = serialhdl.SerialRetryCommand(ser, resp, oid=oid)
    return src.get_response([mp.create_command(cmd)], ser.default_cmd_queue)


def get_config(ser):
    r = ser.send_with_response("get_config", "config")
    return {k: v for k, v in r.items() if not k.startswith("#")}


def open_fresh(reactor, port, name):
    ser = serialhdl.SerialReader(reactor, warn_prefix=name + ": ")
    ser.connect_uart(port, 230400)
    SERS.append(ser)
    cfg = get_config(ser)
    log(name, "get_config:", cfg)
    if cfg.get("is_config") or cfg.get("is_shutdown"):
        log(name, "-> reset")
        try:
            ser.send("reset")
        except Exception as e:
            log("reset-send:", e)
        reactor.pause(reactor.monotonic() + 2.0)
        ser.disconnect()
        SERS.remove(ser)
        reactor.pause(reactor.monotonic() + 1.0)
        ser = serialhdl.SerialReader(reactor, warn_prefix=name + ": ")
        ser.connect_uart(port, 230400)
        SERS.append(ser)
        cfg = get_config(ser)
        log(name, "get_config nach reset:", cfg)
    if cfg.get("is_config"):
        raise RuntimeError("%s nach reset noch konfiguriert" % name)
    clocksync.ClockSync(reactor).connect(ser)
    return ser


def _main(reactor):
    main = open_fresh(reactor, "/dev/ttyS2", "main")
    noz = open_fresh(reactor, "/dev/ttyS3", "nozzle")
    t0 = reactor.monotonic()

    def unsol(name):
        def h(params):
            OUT["unsolicited"].append({"mcu": name, "t": round(reactor.monotonic() - t0, 3),
                                       "p": {k: (v.hex() if isinstance(v, (bytes, bytearray)) else v)
                                             for k, v in params.items() if k not in ("#sent_time",)}})
        return h
    noz.register_response(unsol("nozzle"), "resault_prtouch_pres", 0)

    for c in ("allocate_oids count=1", "config_endstop oid=0 pin=PC7 pull_up=0",
              "finalize_config crc=4712"):
        log("main CFG", c)
        main.send(c)
    for c in ("allocate_oids count=1",
              "config_prtouch_pres oid=0 idx=0 swp_pin=PA15 clk_pin=PB13 sdo_pin=PB14",
              "finalize_config crc=4711"):
        log("nozzle CFG", c)
        noz.send(c)
    reactor.pause(reactor.monotonic() + 0.3)
    log("main", get_config(main), "| nozzle", get_config(noz))

    def pc7():
        r = q(main, "endstop_query_state oid=0", "endstop_state")
        return int(r["pin_value"])

    log("PC7 vor scharf:", pc7())
    c = KP.parse_options({})
    start = KP.build_start_pres_tuple(c, 0)
    names = ["oid", "cfg_regs", "acq_tick", "ned_tftr", "ned_hftr", "ned_lftr",
             "min_hold", "max_hold", "add_hold", "lmt_dead"]
    start_cmd = "start_prtouch_pres " + " ".join("%s=%d" % kv for kv in zip(names, start))

    def arm():
        r = q(noz, start_cmd, "ack_prtouch")
        OUT["events"].append({"t": round(reactor.monotonic() - t0, 3), "ev": "arm",
                              "ack": {k: v for k, v in r.items() if not k.startswith("#")}})
        log("  scharf:", start_cmd, "->", {k: v for k, v in r.items() if not k.startswith("#")})

    def disarm(level=1):
        r = q(noz, "stop_prtouch_pres oid=0 sta_swap=%d" % level, "ack_prtouch")
        OUT["events"].append({"t": round(reactor.monotonic() - t0, 3), "ev": "disarm",
                              "ack": {k: v for k, v in r.items() if not k.startswith("#")}})

    arm()
    reactor.pause(reactor.monotonic() + 0.3)
    base = None
    last_pc7 = None
    rearm_at = None
    k = 0
    log("== MESSFENSTER %.0f s: jetzt Düse mehrmals leicht von unten drücken" % WINDOW_S)
    end = reactor.monotonic() + WINDOW_S
    while reactor.monotonic() < end:
        now = reactor.monotonic() - t0
        v = pc7()
        OUT["pc7"].append((round(now, 3), v))
        if v != last_pc7:
            OUT["events"].append({"t": round(now, 3), "ev": "pc7", "v": v})
            log("  t=%.2f PC7 = %d" % (now, v))
            if last_pc7 is not None and rearm_at is None:
                rearm_at = reactor.monotonic() + 2.0
            last_pc7 = v
        if k % 4 == 0:
            r = q(noz, "read_prtouch_pres oid=0 is_src=1 ch=0 idx=%d len=8" % ((k // 4 * 8) % 64),
                  "resault_prtouch_pres")
            d = KP.decode_pres_datas(bytes(r["datas"]), c)
            if d:
                if base is None:
                    base = sorted(d)[len(d) // 2]
                dev = max(abs(x - base) for x in d)
                OUT["pres"].append((round(now, 3), int(r["tri_chxs"]), dev, d[-1]))
                if dev > 3000 or r["tri_chxs"]:
                    log("  t=%.2f Kraft: max|Δ|=%d tri=%d" % (now, dev, r["tri_chxs"]))
        if rearm_at is not None and reactor.monotonic() >= rearm_at:
            log("  t=%.2f neu scharf schalten" % (reactor.monotonic() - t0))
            disarm(1)
            reactor.pause(reactor.monotonic() + 0.1)
            arm()
            rearm_at = None
        k += 1
        reactor.pause(reactor.monotonic() + 0.05)
    disarm(1)
    log("== Fenster zu. PC7-Wechsel:", sum(1 for e in OUT["events"] if e["ev"] == "pc7") - 1,
        "| max Kraftausschlag:", max((p[2] for p in OUT["pres"]), default=None),
        "| unaufgeforderte Pakete:", len(OUT["unsolicited"]))


def run():
    reactor = R.Reactor()
    res = {"rc": 1}

    def cb(eventtime):
        try:
            _main(reactor)
            res["rc"] = 0
        except Exception:
            log("FEHLER:\n" + traceback.format_exc())
        finally:
            for ser in list(SERS):
                try:
                    ser.send("reset")
                except Exception:
                    pass
            reactor.pause(reactor.monotonic() + 0.5)
            for ser in list(SERS):
                try:
                    ser.disconnect()
                except Exception:
                    pass
            log("beide MCUs reset")
        reactor.end()
    reactor.register_callback(cb)
    reactor.run()
    with open(os.path.join(HERE, "ergebnis_finger.json"), "w") as f:
        json.dump(OUT, f, indent=1, default=str)
    return res["rc"]


if __name__ == "__main__":
    rc = run()
    sys.stdout.flush()
    os._exit(rc)
