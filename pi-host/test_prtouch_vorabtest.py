#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_prtouch_vorabtest.py – Offline-Tests für prtouch_vorabtest.py.

Fake-Düsen-MCU auf Befehlstext-Ebene (wie serialhdl: „name k=v …“) mit
64er-Schieberegister, Encoder-Nachbau 0x08006524 und 41-Byte-Grenze aus
test_k2_prtouch.py. Geprüft: Befehlsreihenfolge (Stock-Reihenfolge wie
core.arm, ohne Main-MCU), Antast-Parameter, Wiederholungen, Entschärfen bei
JEDEM Fehler, Config-Fehler ohne MCU-Zugriff, Notabbruch (Signal), Gleichheit
der eingebauten Defaults mit config-pi/printer.cfg, Python-3.9-Syntax.

Aufruf: python3 test_prtouch_vorabtest.py
"""
import ast
import json
import os
import signal
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import k2_prtouch as K            # noqa: E402
import prtouch_vorabtest as V     # noqa: E402
from test_k2_prtouch import _McuEncoder  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s %s" % ("PASS" if cond else "FAIL", name,
                        "" if cond else detail))


def _reset_out():
    V.OUT.clear()
    V.OUT.update({"skript": "prtouch_vorabtest.py", "befehle": [],
                  "durchlaeufe": [], "log": [], "ausgelassen": []})
    V.STATE["ser"] = None
    V.STATE["ende"] = False


class Abbruch(Exception):
    pass


class FakeDuese:
    """Düsen-MCU auf Befehlsebene. Register leer bis start_prtouch_pres
    (B Memset), danach Ruhe-Rohwerte (−1 428 000 ± 400), gefiltert ± 300,
    Tickabstand 94 900 Takte (0,7908 ms live); jede Abfrage schiebt 3
    Samples nach (laufendes Register)."""

    ALLOWED = ("start_prtouch_pres", "read_prtouch_pres", "stop_prtouch_pres")

    def __init__(self, fehler=None, fehler_bei=None):
        self.fehler = fehler        # sensor_ab / tri / ack_err / timeout /
        self.fehler_bei = fehler_bei  # stop_fail / kbint / noisy
        self.events = []
        self.cmds = []
        self.armed = False
        self.tri = 0
        self.n_query = 0
        self._k = 0
        self._leer()

    def _leer(self):
        self.ticks = [0] * 64
        self.raw = [0] * 64
        self.filt = [0] * 64

    def _sample(self, k):
        if self.fehler == "sensor_ab":
            return 120000000 // 200 * k, -1, -1  # alle 5 ms, Wert -1
        if self.fehler == "noisy":
            return 94900 * k, -1428000 + (6000 if k % 2 else -6000), 0
        return (94900 * k, -1428000 + ((k * 7919) % 801) - 400,
                ((k * 104729) % 601) - 300)

    def _fill(self):
        self.ticks, self.raw, self.filt = [], [], []
        for _ in range(64):
            self._push()

    def _push(self):
        self._k += 1
        t, r, f = self._sample(self._k)
        self.ticks = (self.ticks + [(146176342 + t) & 0xFFFFFFFF])[-64:]
        self.raw = (self.raw + [r])[-64:]
        self.filt = (self.filt + [f])[-64:]

    def query(self, cmd, rname, oid):
        self.n_query += 1
        name = cmd.split()[0]
        args = dict(p.split("=") for p in cmd.split()[1:])
        args = {k: int(v) for k, v in args.items()}
        self.cmds.append(cmd)
        self.events.append(name if name != "read_prtouch_pres"
                           else "read%d" % args["is_src"])
        if name not in self.ALLOWED:
            raise AssertionError("unerlaubter Befehl %s" % cmd)
        if (self.fehler_bei is not None and self.n_query == self.fehler_bei):
            if self.fehler == "timeout":
                raise RuntimeError("Timeout on wait for 'resault' (Test)")
            if self.fehler == "kbint":
                raise KeyboardInterrupt()
        if name == "start_prtouch_pres":
            assert rname == "ack_prtouch"
            self.start_args = args
            if self.fehler == "ack_err":
                return {"oid": 0, "err": 1, "expar0": 0, "expar1": 0}
            self.armed = True
            self.tri = 1 if self.fehler == "tri" else 0
            self._fill()
            return {"oid": 0, "err": 0, "expar0": 64, "expar1": 0}
        if name == "stop_prtouch_pres":
            assert rname == "ack_prtouch"
            self.stop_args = args
            if self.fehler == "stop_fail":
                raise RuntimeError("Timeout stop_prtouch_pres (Test)")
            self.armed = False
            return {"oid": 0, "err": 0, "expar0": 0, "expar1": 71}
        # read_prtouch_pres
        assert rname == "resault_prtouch_pres"
        if args["is_src"] >= 2:
            return {"oid": 0, "tri_chxs": self.tri, "buf_len": 64, "ch": 0,
                    "idx": 0, "len": 0, "ticks": b"", "datas": b""}
        src = self.raw if args["is_src"] == 1 else self.filt
        idx, ln = args["idx"], args["len"]
        end = min(64, idx + ln)
        et, ed = _McuEncoder(), _McuEncoder()
        k = idx
        while k < end:
            if et.add(self.ticks[k]) + ed.add(src[k]) > 41:
                break
            k += 1
        res = {"oid": 0, "tri_chxs": self.tri, "buf_len": 64, "ch": 0,
               "idx": idx, "len": (k - idx) + 1, "ticks": et.finish(),
               "datas": ed.finish()}
        if self.armed:
            for _ in range(3):
                self._push()
        return res


def _lauf(fehler=None, fehler_bei=None, n=1, over=None):
    _reset_out()
    cfg = V.build_cfg(over or {})
    d = FakeDuese(fehler, fehler_bei)
    pauses = []

    def pause(s):
        d.events.append("pause%.3f" % s)
        pauses.append(s)
    exc = None
    res = None
    try:
        res = V.vorabtest(d.query, cfg, n, pause=pause)
    except BaseException as e:  # KeyboardInterrupt-Test
        exc = e
    return cfg, d, res, exc, pauses


def _compress(ev):
    out = []
    for e in ev:
        if not out or out[-1] != e:
            out.append(e)
    return out


def test_reihenfolge():
    cfg, d, res, exc, pauses = _lauf()
    seq = _compress(d.events)
    check("(vorab) Reihenfolge start -> Totzeit -> roh -> gefiltert -> Status "
          "-> stop", seq == ["start_prtouch_pres", "pause0.200", "read1",
                             "read0", "read2", "stop_prtouch_pres"], str(seq))
    check("(vorab) Antast-Parameter = Default-Config (Stock)",
          d.start_args == {"oid": 0, "cfg_regs": 60, "acq_tick": 93750,
                           "ned_tftr": 5, "ned_hftr": 1000, "ned_lftr": 800,
                           "min_hold": 4000, "max_hold": 10000,
                           "add_hold": 500, "lmt_dead": 128},
          str(d.start_args))
    check("(vorab) Entschärfen mit sta_swap=1, kein sta_swap=0",
          d.stop_args == {"oid": 0, "sta_swap": 1}
          and not any("sta_swap=0" in c for c in d.cmds), str(d.cmds))
    reads1 = [c for c in d.cmds if "is_src=1" in c]
    idxs = [int(c.split("idx=")[1].split()[0]) for c in reads1]
    lens = [int(c.split("len=")[1].split()[0]) for c in reads1]
    check("(vorab) Blocklesen idx += n, len = 64 - idx (echter Kern-Code)",
          idxs[0] == 0 and all(b > a for a, b in zip(idxs, idxs[1:]))
          and all(l == 64 - i for i, l in zip(idxs, lens))
          and 6 <= len(reads1) <= 10, str(list(zip(idxs, lens))))
    r = res[0]
    rest = r["rest"]
    check("(vorab) Ruhe OK: tick_median_ms ≈ 0,7908, filt ≤ 300, dif ≤ 800",
          r["ok"] and abs(rest["tick_median_ms"] - 0.79083) < 0.001
          and rest["filt_max_abs"] <= 300 and 0 < rest["dif"] <= 800
          and rest["ok"] and rest["errors"] == [], str(rest))
    check("(vorab) Main-MCU nicht angesprochen: nur Düsen-Befehle, Kern-"
          "Aufrufe cont/start/stop_step nur protokolliert",
          all(c.split()[0] in FakeDuese.ALLOWED for c in d.cmds)
          and V.OUT["ausgelassen"] == ["cont_prtouch_step",
                                       "start_prtouch_step",
                                       "stop_prtouch_step"],
          str(V.OUT["ausgelassen"]))
    check("(vorab) swap/pc7-Phase dokumentiert, tri_chxs=0, phase armed, "
          "disarms 1", "ausgelassen" in r["swap_phase"]
          and r["pc7_phase"].endswith("tri_chxs=0") and r["tri_chxs"] == 0
          and r["phase"] == "armed" and r["disarms"] == 1, str(r))
    check("(vorab) Befehlsliste im Ergebnis = gesendete Befehle",
          r["befehle"] == d.cmds and V.OUT["befehle"] == d.cmds)
    line = V.zeile(r)
    check("(vorab) Konsolenzeile enthält tick_median_ms/filt_max_abs/raw dif",
          "tick_median_ms=0.79" in line and "filt_max_abs=" in line
          and "raw dif=" in line and "OK" in line, line)
    js = json.loads(json.dumps(V.OUT, default=str))
    check("(vorab) Ergebnis JSON-serialisierbar", js["durchlaeufe"][0]["ok"])


def test_wiederholungen():
    cfg, d, res, exc, pauses = _lauf(n=3)
    seq = _compress(d.events)
    starts = d.events.count("start_prtouch_pres")
    stops = d.events.count("stop_prtouch_pres")
    alt = [e for e in d.events if e in ("start_prtouch_pres",
                                        "stop_prtouch_pres")]
    check("(vorab) 3 Wiederholungen: je arm -> Ruhe -> disarm, abwechselnd",
          starts == 3 and stops == 3
          and alt == ["start_prtouch_pres", "stop_prtouch_pres"] * 3
          and all(x["ok"] for x in res) and res[-1]["disarms"] == 3, str(seq))
    check("(vorab) Pause zwischen Durchläufen, Totzeit je Durchlauf",
          pauses == [0.2, V.PAUSE_ZWISCHEN_S, 0.2, V.PAUSE_ZWISCHEN_S, 0.2],
          str(pauses))
    s = V.zusammenfassung(res)
    check("(vorab) Zusammenfassung 3/3", s["ok"] == 3 and len(
        s["tick_median_ms"]) == 3 and s["disarms"] == 3, str(s))


def _entschaerft(d):
    return d.events and d.events[-1] == "stop_prtouch_pres"


def test_fehler_entschaerft():
    cfg, d, res, exc, _ = _lauf("sensor_ab")
    r = res[0]
    check("(vorab) Sensor ab (-1, 5 ms): Ruhetest-Fehler, entschärft",
          not r["ok"] and r["phase"] == "rest" and _entschaerft(d)
          and "-1" in r["fehler"] and "Tickabstand" in r["fehler"]
          and r["rest"]["ok"] is False, str(r["fehler"]))
    cfg, d, res, exc, _ = _lauf("noisy")
    check("(vorab) Ruhe zu unruhig (dif 12000): Fehler, entschärft",
          not res[0]["ok"] and "dif=" in res[0]["fehler"] and _entschaerft(d)
          and res[0]["rest"]["dif"] == 12000, str(res[0]["fehler"]))
    cfg, d, res, exc, _ = _lauf("tri")
    check("(vorab) Kontakt vor der Fahrt (tri_chxs=1): Fehler, entschärft",
          not res[0]["ok"] and res[0]["phase"] == "pc7" and _entschaerft(d)
          and res[0]["tri_chxs"] == 1, str(res[0]))
    cfg, d, res, exc, _ = _lauf("ack_err")
    check("(vorab) ack err≠0: Abbruch vor Totzeit, entschärft",
          not res[0]["ok"] and res[0]["phase"] == "start"
          and "read1" not in d.events and _entschaerft(d), str(d.events))
    cfg, d, res, exc, _ = _lauf("timeout", fehler_bei=4)
    check("(vorab) Kommunikationsfehler beim Lesen: entschärft, Fehlertext",
          not res[0]["ok"] and "Timeout" in res[0]["fehler"]
          and _entschaerft(d), str(res[0]["fehler"]))
    cfg, d, res, exc, _ = _lauf("kbint", fehler_bei=3)
    check("(vorab) KeyboardInterrupt: weitergereicht, vorher entschärft",
          isinstance(exc, KeyboardInterrupt) and _entschaerft(d),
          str(d.events))
    cfg, d, res, exc, _ = _lauf("stop_fail")
    check("(vorab) Entschärfen schlägt fehl: Durchlauf FEHLER mit Meldung",
          not res[0]["ok"] and "Entschärfen fehlgeschlagen" in res[0]["fehler"],
          str(res[0]["fehler"]))
    cfg, d, res, exc, _ = _lauf("timeout", fehler_bei=4, n=2)
    check("(vorab) nach Fehler im 1. Durchlauf läuft der 2. sauber",
          not res[0]["ok"] and res[1]["ok"]
          and d.events.count("stop_prtouch_pres") == 2, str(res[1]["fehler"]))


def test_config():
    cfg = V.build_cfg({"swap_check": "True"})
    check("(vorab) swap_check wird erzwungen aus (Main-MCU)",
          cfg["swap_check"] is False)
    _reset_out()
    d = FakeDuese()
    try:
        V.vorabtest(d.query, V.build_cfg({"rest_check": "False"}), 1,
                    pause=lambda s: None)
        ok = False
    except ValueError:
        ok = True
    check("(vorab) rest_check False -> abgelehnt ohne MCU-Befehl",
          ok and d.cmds == [])
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "e.json")
    _reset_out()
    rc = V.run(["--set", "pres_tri_hold=4000,4000,500", "--ausgabe", out])
    js = json.load(open(out))
    check("(vorab) Config-Fehler (min==max_hold): rc 1, kein MCU-Befehl, "
          "JSON geschrieben", rc == 1 and js["befehle"] == []
          and any("Stream" in s for s in js["log"]), str(js["log"]))
    _reset_out()
    rc = V.run(["--wiederholungen", "0", "--ausgabe", out])
    rc2 = V.run(["--wiederholungen", str(V.WIEDERHOLUNGEN_MAX + 1),
                 "--ausgabe", out])
    check("(vorab) --wiederholungen außerhalb 1..20 -> rc 1", rc == rc2 == 1)
    rc = V.run(["--set", "ohne_gleich", "--ausgabe", out])
    check("(vorab) --set ohne '=' -> rc 1", rc == 1)
    pcfg = os.path.join(_HERE, "config-pi", "printer.cfg")
    sec = V.lies_cfg_abschnitt(pcfg)
    c_file = V.build_cfg({}, pcfg)
    c_def = V.build_cfg({})
    keys = ("cfg_regs", "acq_tick", "ned_tftr", "ned_hftr", "ned_lftr",
            "min_hold", "max_hold", "add_hold", "lmt_dead", "arm_settle_s",
            "rest_max_dif", "rest_filt_max_frac", "rest_check",
            "pres_swap_pin", "pres_clk_pin", "pres_sdo_pin", "pres_idx",
            "contact_criterion")
    diff = [k for k in keys if c_file.get(k) != c_def.get(k)]
    check("(vorab) eingebaute Defaults == [k2_prtouch] in config-pi/"
          "printer.cfg", not diff and sec.get("mode") == "probe",
          "Unterschiede: %s" % diff)
    check("(vorab) --set übersteuert (arm_settle_s 0.3)",
          V.build_cfg({"arm_settle_s": "0.3"})["arm_settle_s"] == 0.3)


class _Ser:
    def __init__(self):
        self.sent = []

    def send(self, c):
        self.sent.append(c)


def test_notabbruch():
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "n.json")
    _reset_out()
    codes = []

    def fake_exit(rc):
        codes.append(rc)
        raise Abbruch()
    old_exit = V.STATE["exit"]
    V.STATE["exit"] = fake_exit
    V.STATE["ausgabe"] = out
    ser = _Ser()
    V.STATE["ser"] = ser
    try:
        try:
            V.notabbruch("Test")
        except Abbruch:
            pass
        js = json.load(open(out))
        check("(vorab) Notabbruch: stop_prtouch_pres sta_swap=1, dann reset, "
              "JSON, Exit 3", ser.sent == ["stop_prtouch_pres oid=0 "
                                           "sta_swap=1", "reset"]
              and codes == [3] and js["notabbruch"] == "Test", str(ser.sent))
        # Signalweg: echter SIGALRM nach 1 s
        _reset_out()
        ser2 = _Ser()
        V.STATE["ser"] = ser2
        codes[:] = []
        tm = V.installiere_notabbruch(1)
        t0 = time.monotonic()
        got = False
        try:
            while time.monotonic() - t0 < 3.0:
                time.sleep(0.05)
        except Abbruch:
            got = True
        tm.cancel()
        dt = time.monotonic() - t0
        check("(vorab) Notabbruch per SIGALRM nach ~1 s: entschärft + reset",
              got and codes == [3] and 0.8 < dt < 2.5
              and ser2.sent == ["stop_prtouch_pres oid=0 sta_swap=1",
                                "reset"], "dt=%.2f %s" % (dt, ser2.sent))
        try:
            V.notabbruch("zweimal")
        except Abbruch:
            pass
        check("(vorab) Notabbruch nur einmal (keine Doppelsendung)",
              ser2.sent.count("reset") == 1)
    finally:
        signal.alarm(0)
        for s in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT,
                  signal.SIGHUP):
            signal.signal(s, signal.SIG_DFL if s != signal.SIGINT
                          else signal.default_int_handler)
        V.STATE["exit"] = old_exit
        _reset_out()


class _FakeMsgParser:
    def get_version_info(self):
        return ("noz0_130-fake", "")

    def get_constant_float(self, name):
        assert name == "CLOCK_FREQ"
        return 120000000.0

    def create_command(self, cmd):
        return cmd


class _FakeSerialModule:
    """Fake serialhdl + clocksync + reactor für run()/_geraet()."""

    def __init__(self, duese, is_config_first=False):
        self.duese = duese
        self.is_config_first = is_config_first
        self.wire = []       # alles, was „über die Leitung“ ging
        self.connects = 0
        mod = self

        class SerialReader:
            def __init__(self, reactor, warn_prefix=""):
                self.default_cmd_queue = object()

            def connect_uart(self, port, baud):
                assert (port, baud) == ("/dev/ttyS3", 230400)
                mod.connects += 1
                mod.wire.append("CONNECT")

            def get_msgparser(self):
                return _FakeMsgParser()

            def send_with_response(self, cmd, resp):
                mod.wire.append(cmd)
                first = mod.connects == 1 and mod.is_config_first
                return {"#name": "config", "is_config": 1 if first else 0,
                        "is_shutdown": 0, "crc": 0}

            def send(self, cmd):
                mod.wire.append(cmd)

            def disconnect(self):
                mod.wire.append("DISCONNECT")

        class SerialRetryCommand:
            def __init__(self, ser, name, oid=None):
                self.name, self.oid = name, oid

            def get_response(self, cmds, queue):
                mod.wire.append(cmds[0])
                r = dict(mod.duese.query(cmds[0], self.name, self.oid))
                r["#name"] = self.name
                return r

        class ClockSync:
            def __init__(self, reactor):
                pass

            def connect(self, ser):
                mod.wire.append("CLOCKSYNC")

        class Reactor:
            def __init__(self):
                self.cbs = []
                self.t = 0.0

            def register_callback(self, cb):
                self.cbs.append(cb)

            def run(self):
                while self.cbs:
                    self.cbs.pop(0)(self.t)

            def end(self):
                pass

            def monotonic(self):
                return self.t

            def pause(self, waketime):
                self.t = max(self.t, waketime)

        import types
        self.mods = {
            "serialhdl": types.SimpleNamespace(
                SerialReader=SerialReader,
                SerialRetryCommand=SerialRetryCommand),
            "clocksync": types.SimpleNamespace(ClockSync=ClockSync),
            "reactor": types.SimpleNamespace(Reactor=Reactor)}

    def __enter__(self):
        self.saved = {k: sys.modules.get(k) for k in self.mods}
        sys.modules.update(self.mods)
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        signal.alarm(0)
        for s in (signal.SIGALRM, signal.SIGTERM, signal.SIGHUP):
            signal.signal(s, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.default_int_handler)


def test_geraet_ablauf():
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "g.json")
    _reset_out()
    d = FakeDuese()
    with _FakeSerialModule(d) as fm:
        rc = V.run(["--wiederholungen", "2", "--ausgabe", out])
    js = json.load(open(out))
    w = fm.wire
    cfg_cmds = ["allocate_oids count=1",
                "config_prtouch_pres oid=0 idx=0 swp_pin=PA15 clk_pin=PB13 "
                "sdo_pin=PB14", "finalize_config crc=4711"]
    i_cfg = w.index(cfg_cmds[0])
    i_start = w.index(next(c for c in w if str(c).startswith(
        "start_prtouch_pres")))
    check("(vorab-gerät) rc 0, 2 Durchläufe ok, JSON mit Befehlsliste",
          rc == 0 and js["rc"] == 0 and len(js["durchlaeufe"]) == 2
          and js["zusammenfassung"]["ok"] == 2, str(js.get("log")[-3:]))
    check("(vorab-gerät) connect -> get_config -> CLOCKSYNC -> Konfig wie "
          "Stock (nur config_prtouch_pres) -> get_config -> Durchläufe",
          w[:3] == ["CONNECT", "get_config", "CLOCKSYNC"]
          and w[i_cfg:i_cfg + 4] == cfg_cmds + ["get_config"]
          and i_start > i_cfg, str(w[:8]))
    check("(vorab-gerät) Ende: stop_prtouch_pres sta_swap=1 -> reset -> "
          "DISCONNECT (finally)",
          w[-3:] == ["stop_prtouch_pres oid=0 sta_swap=1", "reset",
                     "DISCONNECT"], str(w[-4:]))
    names = set(str(c).split()[0] for c in w)
    check("(vorab-gerät) nur Düsen-/Basisbefehle, nichts für Main/Stepper/"
          "Heizung", names <= {"CONNECT", "get_config", "CLOCKSYNC",
                               "allocate_oids", "config_prtouch_pres",
                               "finalize_config", "start_prtouch_pres",
                               "read_prtouch_pres", "stop_prtouch_pres",
                               "reset", "DISCONNECT"}, str(names))
    # MCU war noch konfiguriert (Klipper nicht sauber beendet) -> reset
    _reset_out()
    d = FakeDuese()
    with _FakeSerialModule(d, is_config_first=True) as fm:
        rc = V.run(["--wiederholungen", "1", "--ausgabe", out])
    w = fm.wire
    check("(vorab-gerät) konfigurierte MCU: reset + Neuverbindung vor der "
          "Konfiguration", rc == 0 and w[:6] == ["CONNECT", "get_config",
                                               "reset", "DISCONNECT",
                                               "CONNECT", "get_config"],
          str(w[:7]))
    # Fehler im Ablauf: rc 2, trotzdem entschärft + reset
    _reset_out()
    d = FakeDuese("sensor_ab")
    with _FakeSerialModule(d) as fm:
        rc = V.run(["--wiederholungen", "1", "--ausgabe", out])
    check("(vorab-gerät) Ruhetest-Fehler: rc 2, entschärft + reset",
          rc == 2 and fm.wire[-3:] == ["stop_prtouch_pres oid=0 sta_swap=1",
                                       "reset", "DISCONNECT"],
          str(fm.wire[-3:]))
    _reset_out()


def test_py39():
    ok = True
    for f in ("prtouch_vorabtest.py", "test_prtouch_vorabtest.py"):
        src = open(os.path.join(_HERE, f), encoding="utf-8").read()
        try:
            ast.parse(src, feature_version=(3, 9))
        except SyntaxError as exc:
            ok = False
            print(exc)
    check("(vorab) Python-3.9-Syntax (klippy-env am Drucker)", ok)
    src = open(os.path.join(_HERE, "prtouch_vorabtest.py"),
               encoding="utf-8").read()
    head = src.split("def _geraet")[0]
    check("(vorab) Klipper-Module (serialhdl/reactor/clocksync) nur im "
          "Geräteteil importiert", "import serialhdl" not in head
          and "import reactor" not in head)


def main():
    test_reihenfolge()
    test_wiederholungen()
    test_fehler_entschaerft()
    test_config()
    test_notabbruch()
    test_geraet_ablauf()
    test_py39()
    print("\n%d bestanden, %d fehlgeschlagen" % (len(PASS), len(FAIL)))
    if FAIL:
        print("FEHLER:", FAIL)
        sys.exit(1)
    print("ALLE TESTS BESTANDEN")


if __name__ == "__main__":
    main()
