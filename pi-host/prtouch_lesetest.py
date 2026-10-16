#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prtouch_lesetest.py – PrTouch-Sensor lesen über die Düsen-MCU, OHNE Bewegung.

Läuft auf dem Drucker mit /usr/share/klippy-env/bin/python, während der
Klipper-Dienst GESTOPPT ist (ttyS3 frei). Nutzt Klippys serialhdl/clocksync
direkt (wie klippy/console.py) und unser pi-host/k2_prtouch.py
(KlippyPresMcu + Dekodierung/Ruhetest).

Ablauf: Düsen-MCU reset → nur config_prtouch_pres (exakt wie Stock, per
Emulation belegt) → Test A (Stock-READ_PRES-Parameter, asynchrone
resault-Pakete) → Test B (unser KlippyPresMcu: read_prtouch_pres-Abfrage) →
Test C (unsere Antast-Parameter, nur start/read/stop) → reset.
Keine Bewegung, keine Heizung, Main-MCU wird nicht angesprochen.
"""
import json
import logging
import os
import struct
import sys
import time
import traceback

sys.path.insert(0, "/usr/share/klipper/klippy")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "pi-host"))
sys.path.insert(0, os.path.join(HERE, "nachbau"))

import reactor as R          # noqa: E402
import serialhdl              # noqa: E402
import clocksync              # noqa: E402
import k2_prtouch as KP       # noqa: E402

PORT, BAUD = "/dev/ttyS3", 230400
OUT = {"frames_async": [], "tests": {}, "log": []}
logging.basicConfig(level=logging.WARNING)


def log(*a):
    s = " ".join(str(x) for x in a)
    OUT["log"].append(s)
    print(s, flush=True)


class QueryShim:
    """Ersatz für mcu.lookup_query_command (Klipper-API) über serialhdl."""
    def __init__(self, ser, fmt, resp, oid):
        self.ser, self.fmt, self.resp, self.oid = ser, fmt, resp, oid
        self.names = [p.split("=")[0] for p in fmt.split()[1:]]
        self.rname = resp.split()[0]

    def send(self, params):
        cmd = self.fmt.split()[0] + " " + " ".join(
            "%s=%d" % (n, int(v)) for n, v in zip(self.names, params))
        log("  > %s" % cmd)
        mp = self.ser.get_msgparser()
        src = serialhdl.SerialRetryCommand(self.ser, self.rname, oid=self.oid)
        r = src.get_response([mp.create_command(cmd)], self.ser.default_cmd_queue)
        log("  < %s" % {k: (v.hex() if isinstance(v, (bytes, bytearray)) else v)
                         for k, v in r.items() if not k.startswith("#")})
        return r


class McuShim:
    def __init__(self, ser):
        self.ser = ser

    def lookup_query_command(self, fmt, resp, oid=None, **kw):
        return QueryShim(self.ser, fmt, resp, oid)


def connect(reactor):
    ser = serialhdl.SerialReader(reactor, warn_prefix="nozzle: ")
    ser.connect_uart(PORT, BAUD)
    return ser


def get_config(ser):
    r = ser.send_with_response("get_config", "config")
    return {k: v for k, v in r.items() if not k.startswith("#")}


def stats(name, datas, ticks, freq):
    info = KP.rest_info(datas)
    dts = [(b - a) & 0xFFFFFFFF for a, b in zip(ticks, ticks[1:])]
    info["dt_ms_min"] = min(dts) / freq * 1000 if dts else None
    info["dt_ms_max"] = max(dts) / freq * 1000 if dts else None
    info["dt_ms_avg"] = (sum(dts) / len(dts)) / freq * 1000 if dts else None
    ok, _ = KP.check_rest_ok(datas, KP.parse_options({}))
    info["check_rest_ok"] = ok
    log("  %s: n=%d min=%d max=%d dif=%d avg=%.0f dt_ms=%.4f..%.4f (avg %.4f) rest_ok=%s" % (
        name, info["len"], info["min"], info["max"], info["dif"], info["avg"],
        info["dt_ms_min"] or 0, info["dt_ms_max"] or 0, info["dt_ms_avg"] or 0, ok))
    return info


STATE = {"ser": None}


def main(reactor):
    try:
        _main(reactor)
    finally:
        ser = STATE["ser"]
        if ser is not None:
            log("== reset der Düsen-MCU (frisch für Klipper)")
            try:
                ser.send("reset")
            except Exception as e:
                log("reset:", e)
            reactor.pause(reactor.monotonic() + 0.5)
            try:
                ser.disconnect()
            except Exception:
                pass


def _main(reactor):
    ser = connect(reactor)
    STATE["ser"] = ser
    mp = ser.get_msgparser()
    log("verbunden:", mp.get_version_info()[0])
    cfg = get_config(ser)
    log("get_config:", cfg)
    if cfg.get("is_config") or cfg.get("is_shutdown"):
        log("MCU ist konfiguriert/shutdown -> reset")
        try:
            ser.send("reset")
        except Exception as e:
            log("reset-send:", e)
        reactor.pause(reactor.monotonic() + 2.0)
        ser.disconnect()
        reactor.pause(reactor.monotonic() + 1.0)
        ser = connect(reactor)
        STATE["ser"] = ser
        mp = ser.get_msgparser()
        cfg = get_config(ser)
        log("get_config nach reset:", cfg)
    if cfg.get("is_config"):
        raise RuntimeError("MCU nach reset noch konfiguriert – Abbruch")
    cs = clocksync.ClockSync(reactor)
    cs.connect(ser)
    freq = mp.get_constant_float("CLOCK_FREQ")
    log("CLOCK_FREQ", freq)

    def on_res(params):
        OUT["frames_async"].append({k: (v.hex() if isinstance(v, (bytes, bytearray)) else v)
                                    for k, v in params.items() if k != "#name"})
    ser.register_response(on_res, "resault_prtouch_pres", 0)

    for c in ("allocate_oids count=1",
              "config_prtouch_pres oid=0 idx=0 swp_pin=PA15 clk_pin=PB13 sdo_pin=PB14",
              "finalize_config crc=4711"):
        log("CFG", c)
        ser.send(c)
    reactor.pause(reactor.monotonic() + 0.3)
    log("get_config:", get_config(ser))

    shim = McuShim(ser)
    q_start = shim.lookup_query_command(KP.CMD_START_PRES, KP.RESP_ACK, oid=0)
    q_stop = shim.lookup_query_command(KP.CMD_STOP_PRES, KP.RESP_ACK, oid=0)

    # --- Test A: Stock-READ_PRES-Parameter (per Emulation belegt) ---------
    log("== Test A: Stock-READ_PRES-Start, asynchrone Pakete 2 s")
    OUT["frames_async"].clear()
    q_start.send([0, 60, 93750, 0, 0, 0, 128, 128, 0, 128])
    reactor.pause(reactor.monotonic() + 2.0)
    q_stop.send([0, 0])
    fr = list(OUT["frames_async"])
    log("  async-Pakete:", len(fr), "len je Paket:", sorted(set(f.get("len") for f in fr)))
    ticks, datas = [], []
    OUT["tests"]["A_async_raw_first"] = fr[:3]
    c0 = KP.parse_options({})
    for f in fr:
        ticks += KP.decode_ticks(bytes.fromhex(f["ticks"]), c0)
        datas += KP.decode_pres_datas(bytes.fromhex(f["datas"]), c0)
    if datas:
        OUT["tests"]["A"] = stats("A (async, <I/<i)", datas, ticks, freq)

    # --- Test D: Antast-Modus, blockweises Lesen über idx ----------------
    c = KP.parse_options({})
    q_read = shim.lookup_query_command(KP.CMD_READ_PRES, KP.RESP_PRES, oid=0)
    rows = []
    q_start.send(list(KP.build_start_pres_tuple(c, 0)))
    reactor.pause(reactor.monotonic() + 0.5)
    log("== Test D1: idx 0..63 in 8er-Schritten, len=8, is_src=1")
    for idx in range(0, 64, 8):
        r = q_read.send([0, 1, 0, idx, 8])
        t = KP.decode_ticks(bytes(r["ticks"]), c)
        d = KP.decode_pres_datas(bytes(r["datas"]), c)
        rows.append({"test": "D1", "idx_req": idx, "idx": r["idx"], "len": r["len"],
                     "buf_len": r["buf_len"], "tri": r["tri_chxs"], "t": t, "d": d})
    log("== Test D2: gleiche Abfrage idx=0 len=8 dreimal (Ringpuffer fix oder laufend?)")
    for k in range(3):
        r = q_read.send([0, 1, 0, 0, 8])
        rows.append({"test": "D2", "k": k, "idx": r["idx"], "len": r["len"],
                     "t": KP.decode_ticks(bytes(r["ticks"]), c),
                     "d": KP.decode_pres_datas(bytes(r["datas"]), c)})
    log("== Test D3: is_src=0 vs 1 bei idx=0 len=8")
    for src in (0, 1):
        r = q_read.send([0, src, 0, 0, 8])
        rows.append({"test": "D3", "is_src": src, "idx": r["idx"], "len": r["len"],
                     "t": KP.decode_ticks(bytes(r["ticks"]), c),
                     "d": KP.decode_pres_datas(bytes(r["datas"]), c)})
    q_stop.send([0, 1])
    OUT["tests"]["D"] = rows
    for row in rows:
        t = row["t"]
        log("  %s %s idx=%s len=%s n=%d t0=%s t_last=%s d0=%s" % (
            row["test"], {k: row[k] for k in ("idx_req", "k", "is_src") if k in row},
            row["idx"], row["len"], len(t), t[0] if t else None, t[-1] if t else None,
            row["d"][0] if row["d"] else None))


def run():
    reactor = R.Reactor()
    res = {"rc": 1}

    def cb(eventtime):
        try:
            main(reactor)
            res["rc"] = 0
        except Exception:
            log("FEHLER:\n" + traceback.format_exc())
        reactor.end()
    reactor.register_callback(cb)
    reactor.run()
    with open(os.path.join(HERE, "ergebnis_prtouch.json"), "w") as f:
        json.dump(OUT, f, indent=1, default=str)
    return res["rc"]


if __name__ == "__main__":
    rc = run()
    sys.stdout.flush()
    os._exit(rc)
