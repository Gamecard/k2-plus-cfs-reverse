#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prtouch_vorabtest.py – Vorabprüfung von [k2_prtouch] OHNE Klipper nachbilden.

Läuft AUF DEM DRUCKER mit /usr/share/klippy-env/bin/python, während der
Klipper-Dienst GESTOPPT ist (ttyS3 frei). Mechanik wie prtouch_lesetest.py:
Klippys serialhdl/clocksync direkt, Düsen-MCU /dev/ttyS3 230400, reset im
finally, os._exit, 60-s-Notabbruch (SIGALRM + Reserve-Timer).

Bildet nach, was KTWO_PRTOUCH_READ unter Klipper (mode: probe) täte – mit dem
ECHTEN Code aus k2_prtouch.py (K2PrtouchProbeCore.preflight -> arm/disarm,
KlippyPresMcu, check_rest_ok), nur die Klipper-Anbindung ist durch Shims
ersetzt:

  scharf (start_prtouch_pres, Antast-Parameter aus unserer Default-Config =
  [k2_prtouch] in config-pi/printer.cfg) -> arm_settle_s -> Ruhetest roh
  (is_src=1) + gefiltert (is_src=0) + Tickabstand + -1/24-Bit -> Kontakt-
  Abfrage tri_chxs -> entschärfen (stop_prtouch_pres sta_swap=1) IMMER im
  finally (core.preflight).

AUSGELASSEN (Main-MCU wird NICHT angesprochen):
  - Signalweg-Test PA15 -> PC7 (swap_check): braucht das Lesen von PC7 an der
    Main-MCU (Endstop bzw. stop_prtouch_step) -> swap_check hier immer aus.
    Auch sta_swap=0 wird NICHT gesendet (PA15 bleibt HIGH = Ruhe).
  - cont_prtouch_step / start_prtouch_step / stop_prtouch_step (Main): der
    Kern ruft sie auf, der Shim sendet nichts und meldet „ausgelassen“.
  - PC7-Prüfung „noch kein Kontakt“: ersetzt durch den Düsen-Status
    read_prtouch_pres [oid, 2, 0, 0, 0] -> tri_chxs = 0 (wie mode: steps,
    K2PrtouchController._armed_triggered; B MCU-Befund §2/§3.3).
Keine Bewegung, keine Heizung, keine Stepper.

Aufruf (Klipper vorher stoppen, danach starten + firmware_restart, s.
README-k2_prtouch.md Abschnitt „Vorabtest am Gerät“):
  /usr/share/klippy-env/bin/python prtouch_vorabtest.py [--wiederholungen 3]
      [--cfg printer.cfg] [--set key=value ...] [--ausgabe datei.json]
k2_prtouch.py muss neben diesem Skript (oder in ./pi-host/) liegen.
Exit-Code: 0 alle Durchläufe ok, 2 Vorabprüfung fehlgeschlagen, 1 Fehler
(Verbindung/Config), 3 Notabbruch.
"""
import argparse
import json
import os
import signal
import sys
import threading
import time
import traceback

sys.path.insert(0, "/usr/share/klipper/klippy")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "pi-host"))
sys.path.insert(0, HERE)

import k2_prtouch as KP  # noqa: E402  (rein, keine Klipper-Importe)

PORT, BAUD = "/dev/ttyS3", 230400
NOTABBRUCH_S = 60.0       # harte Laufzeitgrenze (Auftrag)
NOTABBRUCH_RESERVE_S = 10.0  # Reserve-Timer, falls SIGALRM nicht greift
PAUSE_ZWISCHEN_S = 0.5    # Ruhe zwischen zwei Durchläufen
WIEDERHOLUNGEN_MAX = 20   # 20 × ~1 s bleibt sicher unter 60 s
PRES_OID = 0
CONFIG_CRC = 4711         # wie prtouch_lesetest.py

# Antast-/Ruhetest-Optionen wie [k2_prtouch] in pi-host/config-pi/printer.cfg
# (test_prtouch_vorabtest.py prüft die Gleichheit mit der Datei).
DEFAULT_OPTS = {
    "mode": "probe",
    "speed": "5.0",
    "lift_speed": "5.0",
    "z_offset": "0.0",
    "probe_max_travel_mm": "8.0",
    "arm_settle_s": "0.2",
    "swap_check": "True",
    "rest_check": "True",
    "rest_max_dif": "3000",
    "main_mcu": "mcu",
    "nozzle_mcu": "nozzle_mcu",
    "step_swap_pin": "^!PC7",
    "pres_swap_pin": "nozzle_mcu:PA15",
    "pres_cs0_pin": "nozzle_mcu:PB13, nozzle_mcu:PB14",
    "pres_cfg_regs": "60",
    "pres_acq_tkms": "0.78125",
    "pres_tri_fter": "5, 1, 0.8",
    "pres_tri_hold": "4000, 10000, 500",
    "pres_ded_tkms": "128.0",
    "step_acq_tkms": "0.390625",
}

OUT = {"skript": "prtouch_vorabtest.py", "befehle": [], "durchlaeufe": [],
       "log": [], "ausgelassen": []}
STATE = {"ser": None, "ausgabe": None, "ende": False, "lock": threading.Lock(),
         "exit": os._exit}


def log(*a):
    s = " ".join(str(x) for x in a)
    OUT["log"].append(s)
    try:
        print(s, flush=True)
    except Exception:  # SSH weg (EPIPE/EIO) -> nur noch JSON
        pass


def _clean(v):
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return v


def schreibe_json(path=None):
    path = path or STATE["ausgabe"]
    if not path:
        return
    try:
        with open(path, "w") as f:
            json.dump(OUT, f, indent=1, default=str)
    except Exception as exc:
        try:
            print("JSON nicht geschrieben: %s" % exc, flush=True)
        except Exception:
            pass


# ---------------------------------------------------------------------
# Config: unsere Default-Config (+ optional printer.cfg, --set)
# ---------------------------------------------------------------------
def lies_cfg_abschnitt(path, section="k2_prtouch"):
    """[k2_prtouch] aus einer Klipper-printer.cfg (ohne Includes) als Dict."""
    import configparser
    cp = configparser.RawConfigParser(strict=False,
                                      inline_comment_prefixes=("#", ";"))
    with open(path, encoding="utf-8") as f:
        text = f.read()
    # SAVE_CONFIG-Block (#*# ...) und Includes ignorieren
    text = text.split("#*# <---------------------- SAVE_CONFIG")[0]
    cp.read_string(text)
    if not cp.has_section(section):
        raise ValueError("Abschnitt [%s] fehlt in %s" % (section, path))
    return {k: v for k, v in cp.items(section)}


def build_cfg(overrides=None, cfg_file=None):
    """parse_options(Default-Config [+ Datei] [+ --set]) mit swap_check aus.

    swap_check wird ERZWUNGEN aus: der Signalweg-Test braucht die Main-MCU
    (PC7 lesen), die dieses Skript nicht anspricht."""
    opts = dict(DEFAULT_OPTS)
    if cfg_file:
        opts.update(lies_cfg_abschnitt(cfg_file))
    opts.update(dict(overrides or {}))
    cfg = dict(KP.parse_options(opts))
    cfg["swap_check"] = False
    return cfg


# ---------------------------------------------------------------------
# Shims: Klipper-API -> direkte Düsen-MCU-Abfragen
# ---------------------------------------------------------------------
class QueryShim:
    """Ersatz für mcu.lookup_query_command (wie prtouch_lesetest.py).
    query_fn(cmd_text, resp_name, oid) -> Antwort-Dict."""

    def __init__(self, query_fn, fmt, resp, oid):
        self.query_fn = query_fn
        self.fmt, self.resp, self.oid = fmt, resp, oid
        self.names = [p.split("=")[0] for p in fmt.split()[1:]]
        self.rname = resp.split()[0]

    def send(self, params):
        if len(params) != len(self.names):
            raise ValueError("%s: %d Parameter statt %d" % (
                self.fmt.split()[0], len(params), len(self.names)))
        cmd = self.fmt.split()[0] + " " + " ".join(
            "%s=%d" % (n, int(v)) for n, v in zip(self.names, params))
        OUT["befehle"].append(cmd)
        r = self.query_fn(cmd, self.rname, self.oid)
        return r


class McuShim:
    def __init__(self, query_fn):
        self.query_fn = query_fn

    def lookup_query_command(self, fmt, resp, oid=None, **kw):
        return QueryShim(self.query_fn, fmt, resp, oid)


class AufzeichnPres(KP.KlippyPresMcu):
    """Echte KlippyPresMcu, zeichnet zusätzlich die gelesenen Puffer auf."""

    def __init__(self, *a, **kw):
        KP.KlippyPresMcu.__init__(self, *a, **kw)
        self.neu()

    def neu(self):
        self.rec = {"roh": None, "gefiltert": None, "status": None,
                    "ack_start": None, "ack_stop": None}

    def start_sampling(self):
        r = KP.KlippyPresMcu.start_sampling(self)
        self.rec["ack_start"] = dict(r)
        return r

    def read_pres(self):
        r = KP.KlippyPresMcu.read_pres(self)
        self.rec["roh"] = (list(r[0]), list(r[1]))
        return r

    def read_pres_filtered(self):
        r = KP.KlippyPresMcu.read_pres_filtered(self)
        self.rec["gefiltert"] = (list(r[0]), list(r[1]))
        return r

    def read_status(self):
        r = KP.KlippyPresMcu.read_status(self)
        self.rec["status"] = dict(r)
        return r

    def disarm(self):
        r = KP.KlippyPresMcu.disarm(self)
        self.rec["ack_stop"] = dict(r)
        return r


class MainNichtAngesprochen:
    """Platzhalter für die Main-MCU: sendet NICHTS (Auftrag). Der Kern ruft
    cont_step/start_sampling/disarm auf – hier nur protokolliert."""

    def __init__(self):
        self.aufrufe = []

    def _skip(self, name):
        self.aufrufe.append(name)
        msg = "Main-MCU %s: AUSGELASSEN (nicht angesprochen)" % name
        OUT["ausgelassen"].append(name)
        return msg

    def cont_step(self):
        self._skip("cont_prtouch_step")
        return {"ausgelassen": True}

    def start_sampling(self):
        self._skip("start_prtouch_step")
        return {"err": 0, "expar0": 0, "expar1": 0, "ausgelassen": True}

    def disarm(self):
        self._skip("stop_prtouch_step")
        return {"err": 0, "expar0": 0, "expar1": 0, "ausgelassen": True}

    def read_swap(self):  # darf nie gebraucht werden (swap_check aus)
        raise RuntimeError("PC7 lesen braucht die Main-MCU – im Vorabtest "
                           "ausgelassen")


class VorabFehler(RuntimeError):
    pass


def _pc7_nicht_verfuegbar():
    raise VorabFehler("PC7/Endstop-Abfrage braucht die Main-MCU – im "
                      "Vorabtest ausgelassen (swap_check muss aus sein)")


# ---------------------------------------------------------------------
# Ablauf (rein, mit Fakes testbar)
# ---------------------------------------------------------------------
def _ruhe_auswertung(cfg, rec):
    """Gleiche Prüfung wie core.arm (check_rest_ok auf drop_empty-Daten) –
    hier auch dann, wenn der Kern abbrach (Werte für den Bericht)."""
    if rec.get("roh") is None:
        return None
    t, d = KP.drop_empty(*rec["roh"])
    filt = None
    if rec.get("gefiltert") is not None:
        _ft, filt = KP.drop_empty(*rec["gefiltert"])
    ok, info = KP.check_rest_ok(d, cfg, ticks=t, filt=filt)
    info = dict(info)
    info["ok"] = ok
    info["raw_spannweite"] = info.get("dif")
    return info


def vorabtest(query_fn, cfg, wiederholungen=3, pause=None, oid=PRES_OID):
    """N × K2PrtouchProbeCore.preflight() (arm -> Ruhe -> disarm im finally).

    query_fn(cmd_text, resp_name, oid) -> Dict: Düsen-MCU-Abfrage.
    pause(sekunden): Wartefunktion (Gerät: reactor.pause).
    Gibt die Liste der Durchlauf-Dicts zurück (auch in OUT['durchlaeufe']).
    """
    if not cfg.get("rest_check", True):
        raise ValueError("rest_check: False – der Vorabtest IST der Ruhetest")
    if cfg.get("swap_check"):
        raise ValueError("swap_check muss aus sein (braucht Main-MCU)")
    pause = pause if pause is not None else time.sleep
    pres = AufzeichnPres(None, McuShim(query_fn), cfg, oid, {})
    pres.handle_connect()
    main = MainNichtAngesprochen()

    def _kontakt_scharf():
        # Ersatz der PC7-Abfrage (wie mode steps): Düsen-Status tri_chxs
        return int(pres.read_status().get("tri_chxs", 0) or 0) != 0

    core = KP.K2PrtouchProbeCore(
        cfg, None, None, _pc7_nicht_verfuegbar, lambda: None, pause,
        error=VorabFehler, logger=None,
        query_triggered_armed=_kontakt_scharf)
    core.attach(pres, main)
    out = []
    for k in range(int(wiederholungen)):
        pres.neu()
        n_cmd0 = len(OUT["befehle"])
        t0 = time.monotonic()
        res = {"durchlauf": k + 1, "ok": False, "fehler": None}
        try:
            info = core.preflight()
            res["ok"] = True
            res["ack_pres"] = (info or {}).get("ack_pres")
        except Exception as exc:
            res["fehler"] = str(exc)
        res["phase"] = core.arm_phase
        res["disarms"] = core.disarm_count
        res["arms"] = core.arm_count
        res["dauer_s"] = round(time.monotonic() - t0, 3)
        rest = _ruhe_auswertung(cfg, pres.rec)
        res["rest"] = rest
        st = pres.rec.get("status")
        res["tri_chxs"] = None if st is None else st.get("tri_chxs")
        res["ack_start"] = pres.rec.get("ack_start")
        res["ack_stop"] = pres.rec.get("ack_stop")
        res["swap_phase"] = ("ausgelassen (Signalweg PA15->PC7 braucht die "
                             "Main-MCU)")
        res["pc7_phase"] = ("ersetzt durch Düsen-Status tri_chxs=%s"
                            % res["tri_chxs"] if st is not None
                            else "nicht erreicht")
        res["roh"] = pres.rec.get("roh")
        res["gefiltert"] = pres.rec.get("gefiltert")
        res["befehle"] = OUT["befehle"][n_cmd0:]
        out.append(res)
        OUT["durchlaeufe"].append(res)
        log(zeile(res))
        if k + 1 < int(wiederholungen):
            pause(PAUSE_ZWISCHEN_S)
    return out


def _fmt(v, f):
    return "-" if v is None else (f % v)


def zeile(res):
    r = res.get("rest") or {}
    return ("Durchlauf %d: %s | tick_median_ms=%s filt_max_abs=%s "
            "(Grenze %s) raw dif=%s (min %s max %s, n=%s) rest_ok=%s | "
            "swap: ausgelassen | pc7: %s | phase=%s disarms=%d%s" % (
                res["durchlauf"], "OK" if res["ok"] else "FEHLER",
                _fmt(r.get("tick_median_ms"), "%.4f"),
                _fmt(r.get("filt_max_abs"), "%d"),
                _fmt(r.get("filt_limit"), "%.0f"),
                _fmt(r.get("dif"), "%d"), _fmt(r.get("min"), "%d"),
                _fmt(r.get("max"), "%d"), _fmt(r.get("len"), "%d"),
                r.get("ok"), res.get("pc7_phase"), res.get("phase"),
                res.get("disarms", 0),
                "" if res["ok"] else " | %s" % res.get("fehler")))


def zusammenfassung(durchl):
    ok = [d for d in durchl if d["ok"]]
    def _vals(key):
        return [d["rest"][key] for d in durchl
                if d.get("rest") and d["rest"].get(key) is not None]
    s = {"durchlaeufe": len(durchl), "ok": len(ok),
         "tick_median_ms": _vals("tick_median_ms"),
         "filt_max_abs": _vals("filt_max_abs"),
         "raw_dif": _vals("dif"),
         "disarms": durchl[-1]["disarms"] if durchl else 0}
    return s


# ---------------------------------------------------------------------
# Notabbruch (60 s): entschärfen + reset + JSON + os._exit(3)
# ---------------------------------------------------------------------
def notabbruch(grund):
    lock = STATE["lock"]
    if not lock.acquire(False):
        return
    try:
        if STATE["ende"]:
            return
        STATE["ende"] = True
    finally:
        lock.release()
    try:
        OUT["notabbruch"] = str(grund)
        log("NOTABBRUCH: %s -> stop_prtouch_pres sta_swap=1 + reset" % grund)
        ser = STATE["ser"]
        if ser is not None:
            for c in ("stop_prtouch_pres oid=%d sta_swap=1" % PRES_OID,
                      "reset"):
                try:
                    OUT["befehle"].append(c + " (Notabbruch)")
                    ser.send(c)
                except Exception as exc:
                    log("Notabbruch %s: %s" % (c, exc))
            try:
                time.sleep(0.3)
            except Exception:
                pass
        schreibe_json()
    finally:
        STATE["exit"](3)


def installiere_notabbruch(sekunden=NOTABBRUCH_S):
    def _sig(signum, frame):
        notabbruch("Signal %d" % signum)
    signal.signal(signal.SIGALRM, _sig)
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    try:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)  # SSH-Abbruch überleben
    except (AttributeError, ValueError):
        pass
    signal.alarm(int(sekunden))
    tm = threading.Timer(float(sekunden) + NOTABBRUCH_RESERVE_S, notabbruch,
                         args=("Reserve-Timer %.0f s" % (
                             float(sekunden) + NOTABBRUCH_RESERVE_S),))
    tm.daemon = True
    tm.start()
    return tm


# ---------------------------------------------------------------------
# Gerät: serialhdl (nur hier Klipper-Importe)
# ---------------------------------------------------------------------
def _geraet(reactor, cfg, wiederholungen, port, baud):
    import serialhdl
    import clocksync

    def connect():
        ser = serialhdl.SerialReader(reactor, warn_prefix="nozzle: ")
        ser.connect_uart(port, baud)
        return ser

    def get_config(ser):
        OUT["befehle"].append("get_config")
        r = ser.send_with_response("get_config", "config")
        return {k: v for k, v in r.items() if not k.startswith("#")}

    def send(ser, c):
        OUT["befehle"].append(c)
        ser.send(c)

    ser = connect()
    STATE["ser"] = ser
    mp = ser.get_msgparser()
    log("verbunden:", mp.get_version_info()[0])
    cfgm = get_config(ser)
    log("get_config:", cfgm)
    if cfgm.get("is_config") or cfgm.get("is_shutdown"):
        log("Düsen-MCU konfiguriert/shutdown -> reset")
        try:
            send(ser, "reset")
        except Exception as exc:
            log("reset-send:", exc)
        reactor.pause(reactor.monotonic() + 2.0)
        ser.disconnect()
        STATE["ser"] = None
        reactor.pause(reactor.monotonic() + 1.0)
        ser = connect()
        STATE["ser"] = ser
        mp = ser.get_msgparser()
        cfgm = get_config(ser)
        log("get_config nach reset:", cfgm)
    if cfgm.get("is_config"):
        raise RuntimeError("Düsen-MCU nach reset noch konfiguriert – Abbruch")
    cs = clocksync.ClockSync(reactor)
    cs.connect(ser)
    freq = mp.get_constant_float("CLOCK_FREQ")
    OUT["clock_freq_mcu"] = freq
    log("CLOCK_FREQ", freq)
    if abs(freq - float(cfg["clock_freq"])) > 0.01 * float(cfg["clock_freq"]):
        raise RuntimeError("CLOCK_FREQ %.0f != cfg %.0f" % (
            freq, cfg["clock_freq"]))
    swp = KP._clean_pin_name(cfg["pres_swap_pin"])
    clk = KP._clean_pin_name(cfg["pres_clk_pin"])
    sdo = KP._clean_pin_name(cfg["pres_sdo_pin"])
    for c in ("allocate_oids count=1",
              "config_prtouch_pres oid=%d idx=%d swp_pin=%s clk_pin=%s "
              "sdo_pin=%s" % (PRES_OID, int(cfg["pres_idx"]), swp, clk, sdo),
              "finalize_config crc=%d" % CONFIG_CRC):
        log("CFG", c)
        send(ser, c)
    reactor.pause(reactor.monotonic() + 0.3)
    log("get_config:", get_config(ser))

    def query_fn(cmd, rname, oid):
        src = serialhdl.SerialRetryCommand(ser, rname, oid=oid)
        r = src.get_response([mp.create_command(cmd)], ser.default_cmd_queue)
        return {k: v for k, v in r.items() if not k.startswith("#")}

    def pause(sec):
        reactor.pause(reactor.monotonic() + float(sec))

    return vorabtest(query_fn, cfg, wiederholungen, pause=pause)


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--wiederholungen", type=int, default=3,
                    help="Anzahl arm->Ruhe->disarm (Default 3, max %d)"
                    % WIEDERHOLUNGEN_MAX)
    ap.add_argument("--cfg", default=None,
                    help="printer.cfg mit [k2_prtouch] (Default: eingebaute "
                         "Werte = config-pi/printer.cfg)")
    ap.add_argument("--set", action="append", default=[],
                    metavar="KEY=VALUE", help="Option übersteuern")
    ap.add_argument("--port", default=PORT)
    ap.add_argument("--baud", type=int, default=BAUD)
    ap.add_argument("--ausgabe",
                    default=os.path.join(HERE, "ergebnis_vorabtest.json"))
    a = ap.parse_args(argv)
    STATE["ausgabe"] = a.ausgabe
    if not 1 <= a.wiederholungen <= WIEDERHOLUNGEN_MAX:
        log("--wiederholungen muss 1..%d sein" % WIEDERHOLUNGEN_MAX)
        return 1
    over = {}
    for kv in a.set:
        if "=" not in kv:
            log("--set erwartet KEY=VALUE: %r" % kv)
            return 1
        k, v = kv.split("=", 1)
        over[k.strip().lower()] = v.strip()
    try:
        cfg = build_cfg(over, a.cfg)  # Config-Fehler -> kein MCU-Zugriff
    except Exception as exc:
        log("Config-Fehler (keine MCU angesprochen): %s" % exc)
        schreibe_json()
        return 1
    OUT["parameter"] = {
        "start_prtouch_pres": list(KP.build_start_pres_tuple(cfg, PRES_OID)),
        "arm_settle_s": cfg["arm_settle_s"],
        "arm_settle_min_s": cfg["arm_settle_min_s"],
        "rest_max_dif": cfg["rest_max_dif"],
        "rest_filt_max_frac": cfg["rest_filt_max_frac"],
        "pres_period_ms": cfg["pres_period_s"] * 1e3,
        "wiederholungen": a.wiederholungen,
        "swap_check": "aus (Main-MCU nicht angesprochen)",
    }
    log("Parameter:", OUT["parameter"])
    STATE["timer"] = installiere_notabbruch(NOTABBRUCH_S)

    import reactor as R
    reactor = R.Reactor()
    res = {"rc": 1}

    def cb(eventtime):
        try:
            d = _geraet(reactor, cfg, a.wiederholungen, a.port, a.baud)
            OUT["zusammenfassung"] = zusammenfassung(d)
            res["rc"] = 0 if d and all(x["ok"] for x in d) else 2
        except Exception:
            log("FEHLER:\n" + traceback.format_exc())
        finally:
            ser = STATE["ser"]
            if ser is not None:
                log("== reset der Düsen-MCU (frisch für Klipper)")
                try:
                    OUT["befehle"].append("reset")
                    ser.send("reset")
                except Exception as exc:
                    log("reset:", exc)
                reactor.pause(reactor.monotonic() + 0.5)
                try:
                    ser.disconnect()
                except Exception:
                    pass
                STATE["ser"] = None
        reactor.end()

    reactor.register_callback(cb)
    reactor.run()
    signal.alarm(0)
    with STATE["lock"]:
        STATE["ende"] = True
    try:
        STATE["timer"].cancel()
    except Exception:
        pass
    OUT["rc"] = res["rc"]
    log("Zusammenfassung:", OUT.get("zusammenfassung"), "rc =", res["rc"])
    schreibe_json()
    return res["rc"]


if __name__ == "__main__":
    rc = run()
    try:
        sys.stdout.flush()
    except Exception:
        pass
    os._exit(rc)
