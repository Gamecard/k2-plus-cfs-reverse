#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_k2_prtouch.py – Tests ohne Klipper (python3).

Fake-MCUs + Fake-Toolhead mit simulierter Kraftkurve (Kontakt bei bekannter
Z-Höhe, Rauschen, Drift) →
  (a) Kontakt korrekt, Z-Fehler < 0,02mm
  (b) kein Schritt > step_mm, Gesamtweg nie > max_travel_mm
  (c) Notgrenze bricht ab und hebt an
  (d) allow_move=False bewegt nie Z
  (e) defekter Swap/Sensor → Abbruch vor Bewegung
  (f) Formatstrings exakt wie Dictionary
  (g) Abbruch spätestens 1 Schritt nach Kontakt (Überfahrweg begrenzt)

Aufruf: python3 test_k2_prtouch.py
"""

import json
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import k2_prtouch as K

# prtouch-Befehle/-Antworten aus den MCU-Data-Dictionaries (Main + Düse, FW 1.1.0.48-293)
FORMATS = os.path.join(_HERE, "prtouch_mcu_formats.json")


PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("PASS %s %s" % (name, detail))
    else:
        FAIL.append(name)
        print("FAIL %s %s" % (name, detail))


class DummyLog:
    def __init__(self):
        self.infos = []
        self.errors = []

    def info(self, msg):
        self.infos.append(str(msg))

    def error(self, msg):
        self.errors.append(str(msg))


def det_noise(i, amp):
    """Deterministisches Rauschen ±amp (kein random → reproduzierbar)."""
    if amp <= 0:
        return 0
    span = 2 * int(amp) + 1
    return ((i * 7919 + 13) % span) - int(amp)


class FakeToolhead:
    """Simuliert Z (+XY), zeichnet alle Bewegungen auf."""

    def __init__(self, z_start=0.2, x=100.0, y=100.0, temps=(140.0, 50.0)):
        self.z = float(z_start)
        self.x = float(x)
        self.y = float(y)
        self.z_start = float(z_start)
        self.temps = tuple(temps)
        self.moves = []  # (kind, dz_or_xy, speed)
        self.waits = 0
        self.min_z = float(z_start)

    def get_z(self):
        return float(self.z)

    def get_xy(self):
        return (float(self.x), float(self.y))

    def move_down(self, step_mm, speed):
        self.moves.append(("down", float(step_mm), float(speed)))
        self.z -= float(step_mm)
        if self.z < self.min_z:
            self.min_z = float(self.z)

    def move_to_xy(self, x, y, speed):
        self.moves.append(("xy", (float(x), float(y)), float(speed)))
        self.x, self.y = float(x), float(y)

    def lift(self, dz_mm, speed):
        self.moves.append(("lift", float(dz_mm), float(speed)))
        self.z += float(dz_mm)

    def wait_moves(self):
        self.waits += 1

    def get_temps(self):
        return self.temps

    def down_steps(self):
        return [m[1] for m in self.moves if m[0] == "down"]

    def total_down(self):
        return sum(self.down_steps())


class FakePres:
    """Düsen-MCU: Kraftkurve mit Kontakt bei z_true."""

    def __init__(self, tool, cfg, z_true=0.0, baseline=-1450000,
                 noise_amp=300, swap_ok=True, rest_noisy=False,
                 emergency_always=False, spike=-9000, tick_base=10000000):
        self.tool = tool
        self.cfg = cfg
        self.z_true = float(z_true)
        self.baseline = int(baseline)
        self.noise_amp = int(noise_amp)
        self.swap_ok = bool(swap_ok)
        self.rest_noisy = bool(rest_noisy)
        self.emergency_always = bool(emergency_always)
        self.spike = int(spike)
        self.tick_base = int(tick_base)
        self.reads = 0
        self.starts = 0
        self.stops = []

    def test_swap(self):
        if not self.swap_ok:
            return {"swap_ok": False}
        return {"py_ver": 78, "pres_ver": 71, "step_ver": 71,
                "swap_ok": True}

    def start_sampling(self):
        self.starts += 1

    def stop_sampling(self, ok=True):
        self.stops.append(bool(ok))

    def _ticks(self):
        base = self.tick_base + self.reads * 100000
        step = 93750  # pres acq_tick
        last = base
        ticks = [last - (63 - i) * step for i in range(64)]
        return struct.pack("<64I", *[t & 0xFFFFFFFF for t in ticks])

    def _ruhe_datas(self, noisy=False):
        out = []
        for i in range(64):
            if noisy:
                # groß: abwechselnd ±5000 → dif 10000 > 3000
                v = self.baseline + (5000 if i % 2 == 0 else -5000)
            else:
                v = self.baseline + det_noise(i + self.reads * 131,
                                              self.noise_amp)
            out.append(int(v))
        return struct.pack("<64i", *out)

    def read_rest_pres(self):
        self.reads += 1
        return self._ticks(), self._ruhe_datas(noisy=self.rest_noisy)

    def read_pres(self):
        self.reads += 1
        if self.emergency_always:
            # Ruhe für baseline0 war ok (read_rest), hier Notfall:
            out = [self.baseline + 50000 + det_noise(i, 100)
                   for i in range(64)]
            return self._ticks(), struct.pack("<64i", *out)
        z = float(self.tool.get_z())
        if z > self.z_true + 1e-12:
            return self._ticks(), self._ruhe_datas(noisy=False)
        # Kontakt: erste 60 Ruhe, letzte 4 Spike (wie live FTR-Spitze):
        out = []
        for i in range(64):
            if i < 60:
                out.append(self.baseline + det_noise(i, self.noise_amp))
            else:
                out.append(self.baseline + self.spike
                           - (i - 60) * 500 + det_noise(i, 50))
        return self._ticks(), struct.pack("<64i", *out)


class FakeStep:
    """Main-MCU: Schrittzähler → counts, relativ zum Trigger."""

    def __init__(self, tool, cfg, swap_ok=True, tick_base=20000000):
        self.tool = tool
        self.cfg = cfg
        self.swap_ok = bool(swap_ok)
        self.tick_base = int(tick_base)
        self.reads = 0
        self.starts = 0
        self.stops = []

    def test_swap(self):
        if not self.swap_ok:
            return {"swap_ok": False}
        return {"py_ver": 78, "pres_ver": 71, "step_ver": 71,
                "swap_ok": True}

    def start_sampling(self):
        self.starts += 1

    def stop_sampling(self, ok=True):
        self.stops.append(bool(ok))

    def read_step(self):
        self.reads += 1
        speed = float(self.cfg["probe_speed"])
        stepd = float(self.cfg["step_dist_mm"])
        aqc = int(self.cfg["aqc_tick"])
        freq = float(self.cfg["clock_freq"])
        base = self.tick_base + self.reads * 50000
        ticks = [base - (63 - i) * aqc for i in range(64)]
        tb = struct.pack("<64I", *[t & 0xFFFFFFFF for t in ticks])
        # Zeiten relativ, Z_sample = current + (-t)*speed:
        zcur = float(self.tool.get_z())
        counts = []
        for i in range(64):
            t = (ticks[i] - ticks[-1]) / freq
            zsample = zcur + (-t) * speed
            counts.append(int(round((zsample - zcur) / stepd)))
        db = struct.pack("<64i", *counts)
        return tb, db


def make_ctl(z_start=0.2, z_true=0.0, cfg_over=None, fake_over=None):
    cfg_over = dict(cfg_over or {})
    fake_over = dict(fake_over or {})
    base = {"allow_move": True, "step_mm": 0.02, "probe_speed": 5.0,
            "max_travel_mm": 5.0, "packing": "struct"}  # Fakes liefern feste Breite
    base.update(cfg_over)
    cfg = K.parse_options(base)
    tool = FakeToolhead(z_start=z_start,
                        temps=fake_over.get("temps", (140.0, 50.0)))
    pres = FakePres(tool, cfg, z_true=z_true,
                    baseline=fake_over.get("baseline", -1450000),
                    noise_amp=fake_over.get("noise_amp", 300),
                    swap_ok=fake_over.get("swap_ok", True),
                    rest_noisy=fake_over.get("rest_noisy", False),
                    emergency_always=fake_over.get("emergency_always",
                                                   False))
    step = FakeStep(tool, cfg,
                    swap_ok=fake_over.get("step_swap_ok",
                                          fake_over.get("swap_ok", True)))
    log = DummyLog()
    ctl = K.K2PrtouchController(cfg, pres, step, tool,
                                pause=lambda s: None, logger=log)
    return cfg, tool, pres, step, ctl, log


def test_a_contact_accuracy():
    cfg, tool, pres, step, ctl, log = make_ctl(z_start=0.2, z_true=0.0)
    res = ctl.probe_point(100.0, 100.0)
    err = abs(res.get("z", 999.0) - 0.0) if res.get("status") == "ok" else 999
    check("(a) status ok", res.get("status") == "ok", str(res)[:200])
    check("(a) Z-Fehler < 0,02mm", err < 0.02, "err=%.4f z=%s" % (
        err, res.get("z")))
    # zoft im Live-Bereich 0.010-0.040 (Toleranz für diskrete Steps):
    zf = res.get("zoft", -1)
    check("(a) zoft 0.005-0.045", 0.005 <= zf <= 0.045, "zoft=%.4f" % zf)


def test_b_step_limits():
    cfg, tool, pres, step, ctl, log = make_ctl(z_start=0.2, z_true=0.0)
    res = ctl.probe_point()
    downs = tool.down_steps()
    mx = max(downs) if downs else 0.0
    tot = tool.total_down()
    check("(b) kein Schritt > step_mm",
          all(d <= cfg["step_mm"] + 1e-9 for d in downs),
          "max=%.4f step_mm=%.4f" % (mx, cfg["step_mm"]))
    check("(b) Gesamtweg <= max_travel",
          tot <= cfg["max_travel_mm"] + 1e-9,
          "total=%.4f max=%.2f" % (tot, cfg["max_travel_mm"]))
    # Negativ: Ziel außerhalb Hub → max_travel-Abbruch, trotzdem begrenzt:
    cfg2, tool2, _, _, ctl2, _ = make_ctl(z_start=0.5, z_true=0.0,
                                          cfg_over={"max_travel_mm": 0.1})
    res2 = ctl2.probe_point()
    check("(b) max_travel-Abbruch", res2.get("status") == "max_travel",
          str(res2.get("status")))
    check("(b) trotz Abbruch begrenzt",
          tool2.total_down() <= 0.1 + 1e-9, "total=%.4f" % tool2.total_down())
    # Config-Härten:
    try:
        K.parse_options({"step_mm": 0.06})
        check("(b) step_mm>0.05 abgelehnt", False, "kein Fehler")
    except ValueError:
        check("(b) step_mm>0.05 abgelehnt", True)
    try:
        K.parse_options({"probe_speed": 6.0})
        check("(b) speed>5 abgelehnt", False, "kein Fehler")
    except ValueError:
        check("(b) speed>5 abgelehnt", True)


def test_c_emergency():
    cfg, tool, pres, step, ctl, log = make_ctl(
        z_start=0.2, z_true=0.0, fake_over={"emergency_always": True})
    z0 = tool.get_z()
    res = ctl.probe_point()
    check("(c) Notgrenze bricht ab", res.get("status") == "emergency",
          str(res)[:200])
    check("(c) angehoben nach Abbruch", tool.get_z() > tool.min_z,
          "min=%.4f jetzt=%.4f" % (tool.min_z, tool.get_z()))
    check("(c) kein Weiterprobe (steps klein)",
          res.get("steps", 999) <= 2, "steps=%s" % res.get("steps"))


def test_d_no_move_without_allow():
    cfg, tool, pres, step, ctl, log = make_ctl(
        z_start=0.2, z_true=0.0, cfg_over={"allow_move": False})
    z0 = tool.get_z()
    res = ctl.probe_point(100.0, 100.0, dry_run=False)
    check("(d) allow_move=False → dry_run",
          res.get("status") == "dry_run", str(res.get("status")))
    check("(d) Z unbewegt", abs(tool.get_z() - z0) < 1e-12
          and len(tool.moves) == 0, "moves=%d" % len(tool.moves))
    # DRY_RUN=1 auch mit allow_move=True bewegt nichts:
    cfg2, tool2, _, _, ctl2, _ = make_ctl(z_start=0.2, z_true=0.0)
    res2 = ctl2.probe_point(dry_run=True)
    check("(d) DRY_RUN=1 bewegt nichts",
          res2.get("status") == "dry_run" and len(tool2.moves) == 0)


def test_e_swap_rest_abort_before_move():
    # Swap defekt:
    cfg, tool, pres, step, ctl, log = make_ctl(
        z_start=0.2, z_true=0.0, fake_over={"swap_ok": False})
    res = ctl.probe_point()
    check("(e) Swap defekt → swap_error", res.get("status") == "swap_error",
          str(res)[:200])
    check("(e) kein Schritt bei Swap-Fehler", len(tool.moves) == 0,
          "moves=%d" % len(tool.moves))
    # Sensor unruhig:
    cfg2, tool2, _, _, ctl2, _ = make_ctl(
        z_start=0.2, z_true=0.0, fake_over={"rest_noisy": True})
    res2 = ctl2.probe_point()
    check("(e) Sensor unruhig → rest_error",
          res2.get("status") == "rest_error", str(res2)[:200])
    check("(e) kein Schritt bei Ruhe-Fehler", len(tool2.moves) == 0)


def test_f_formatstrings():
    try:
        _f = json.load(open(FORMATS))
        main, noz = _f["main"], _f["nozzle"]
    except Exception as exc:
        check("(f) Dicts lesbar", False, str(exc))
        return
    for name, fmt in K.ALL_FORMATS.items():
        in_main = (fmt in main.get("commands", {})
                   or fmt in main.get("responses", {}))
        in_noz = (fmt in noz.get("commands", {})
                  or fmt in noz.get("responses", {}))
        check("(f) %s exakt" % name, in_main and in_noz,
              "" if (in_main and in_noz) else "fehlt")
    # Tippfehler exakt:
    check("(f) aqc_tick-Schreibweise", "aqc_tick" in K.CMD_START_STEP
          and "acq_tick" not in K.CMD_START_STEP)
    check("(f) resault-Schreibweise", K.RESP_PRES.startswith("resault_")
          and K.RESP_STEP.startswith("resault_"))


def test_g_stop_within_one_step():
    for zt in (0.0, 0.05):
        cfg, tool, pres, step, ctl, log = make_ctl(z_start=0.2, z_true=zt)
        res = ctl.probe_point()
        over = zt - tool.min_z  # >0 = zu tief
        check("(g) Kontakt z=%.2f: 1 Schritt genügt" % zt,
              res.get("status") == "ok" and over <= cfg["step_mm"] + 1e-9,
              "min_z=%.4f wahr=%.4f over=%.4f steps=%s" % (
                  tool.min_z, zt, over, res.get("steps")))
    # Wartet nach JEDEM Schritt (M400-Äquivalent):
    cfg, tool, pres, step, ctl, log = make_ctl(z_start=0.2, z_true=0.0)
    res = ctl.probe_point()
    downs = len(tool.down_steps())
    check("(g) wait nach jedem Schritt", tool.waits >= downs,
          "waits=%d downs=%d" % (tool.waits, downs))


def test_extra_can_brk_retry():
    # can_brk-Heuristik pur (T58-Werte → False, Normal → True):
    cfg = K.parse_options({})
    can, _ = K.check_can_brk(53, 42, 0.040, cfg)
    check("(x) T58 → can_brk False", can is False)
    can2, _ = K.check_can_brk(59, 54, 0.017, cfg)
    check("(x) normal → can_brk True", can2 is True)


# Echte Pakete (Live-Lesetest 16.10.2026, Düsen-MCU, Stock-READ-Parameter):
LIVE_PACKED = [('08aaab5679b6082c7301927201a27201927201927201927201a27201', '081012f034ea30c500bfdf359b002f', [146176342, 146271362, 146366228, 146461110, 146555976, 146650842, 146745708, 146840590], [-1428240, -1428192, -1427995, -1428060, -1428093, -1428040, -1427885, -1427838]), ('08aaabf20ec2089471019c7301d27101927201d87301a27201a27201', '0814166a37ea5effd8fe631cb100c100b1', [146935538, 147030150, 147125282, 147219956, 147314822, 147410014, 147504896, 147599778], [-1427606, -1427768, -1428064, -1427965, -1427937, -1427760, -1427567, -1427646]), ('0902aaab2a41a809d672014a72019e7201927201927201927201d87301927201', '090000025a35ea67232d27f6e4fcad', [162021674, 162116608, 162211402, 162306280, 162401146, 162496012, 162590878, 162686070, 162780936], [-1428134, -1428031, -1427996, -1427951, -1427912, -1427922, -1427950, -1427954, -1428037])]


def test_live_packed_decode():
    cfg = K.parse_options({})
    check("(live) Default packing = packed", cfg["packing"] == "packed")
    for tk, dt, exp_t, exp_d in LIVE_PACKED:
        t = K.decode_ticks(bytes.fromhex(tk), cfg)
        d = K.decode_pres_datas(bytes.fromhex(dt), cfg)
        check("(live) Paket n=%d ticks exakt" % len(t), t == exp_t)
        check("(live) Paket n=%d datas exakt" % len(d), d == exp_d)
        dts = [(b - a) & 0xFFFFFFFF for a, b in zip(t, t[1:])]
        check("(live) Abstand 0,78..0,80 ms", all(93500 <= x <= 96500 for x in dts))
        check("(live) Ruhewert um -1,428e6", all(-1430000 < v < -1426000 for v in d))
    try:
        K.decode_packed(bytes.fromhex(LIVE_PACKED[0][1])[:-1])
        check("(live) verkürzter Puffer -> Fehler", False)
    except ValueError:
        check("(live) verkürzter Puffer -> Fehler", True)


# Live-Test D1 (16.10.2026): 8 Blöcke à <=8 Samples aus dem 64er-Ring (dekodiert)
LIVE_D1 = [(0, [462289465, 462384385, 462479321, 462574299, 462668985, 462763905, 462858825], [-1427901, -1427746, -1427906, -1427725, -1427921, -1427938, -1427640]), (8, [463902365, 463997285, 464092205, 464187125, 464282061, 464376981, 464471917, 464566523], [-1427658, -1427741, -1427844, -1427841, -1428038, -1427964, -1427806, -1427800]), (16, [465515473, 465610409, 465705329, 465800249, 465895185, 465989791, 466084711, 466179647], [-1427644, -1427681, -1427749, -1427591, -1427509, -1427641, -1427408, -1427400]), (24, [467223517, 467318107, 467413027, 467507963, 467602899, 467697819, 467792739, 467887675], [-1427843, -1427994, -1427708, -1427409, -1427500, -1427741, -1427747, -1427628]), (32, [468836519, 468931125, 469026061, 469121181, 469215873, 469310805, 469405647, 469500739], [-1427628, -1427614, -1427658, -1427858, -1427873, -1427708, -1427378, -1427432]), (40, [470639049, 470733969, 470828889, 470923641, 471018557, 471113411, 471208271, 471303269], [-1427666, -1427966, -1427881, -1427963, -1427936, -1427618, -1427793, -1427419]), (48, [472441963, 472536569, 472631489, 472726409, 472821369, 472916147, 473011103, 473105957], [-1427565, -1427591, -1427742, -1427891, -1427936, -1427924, -1427698, -1427282]), (56, [474149623, 474244639, 474339229, 474434149, 474529069, 474623981, 474718901, 474813837], [-1427492, -1427511, -1427694, -1427781, -1427788, -1427840, -1427553, -1427606])]


class _RingQuery:
    """Fake read_prtouch_pres: liefert die Live-Blöcke als gepackte Puffer."""
    def __init__(self, blocks):
        self.blocks = {i: (t, d) for i, t, d in blocks}
        self.sent = []

    @staticmethod
    def _pack(vals):
        n = len(vals)
        mask = 0
        body = b""
        prev = None
        for i, v in enumerate(vals):
            x = v if prev is None else v - prev
            prev = v
            for sz in (1, 2, 3, 4):
                lo, hi = -(1 << (8 * sz - 1)), (1 << (8 * sz - 1)) - 1
                if lo <= x <= hi:
                    break
            mask |= (sz - 1) << (2 * i)
            body += int(x).to_bytes(sz, "little", signed=True)
        mb = (n + 3) // 4
        return bytes([n]) + mask.to_bytes(mb, "big") + body

    def send(self, params):
        self.sent.append(list(params))
        oid, is_src, ch, idx, ln = params
        t, d = self.blocks[idx]
        # Ticks > 2^31 als vorzeichenbehaftete Werte packen (kumuliert ok)
        tt = [x - (1 << 32) if x >= (1 << 31) else x for x in t]
        return {"ticks": self._pack(tt), "datas": self._pack(d), "idx": idx,
                "len": len(d)}


def _wrap32(x):
    return ((int(x) + (1 << 31)) & 0xFFFFFFFF) - (1 << 31)


def _delta_size(x):
    """Bytebreite je Delta wie Düsen-Encoder 0x08006524 (±2^7/2^15/2^23)."""
    if -0x80 <= x <= 0x7F:
        return 1
    if -0x8000 <= x <= 0x7FFF:
        return 2
    if -0x800000 <= x <= 0x7FFFFF:
        return 3
    return 4


class _McuEncoder:
    """Nachbau Encoder 0x08006524 + Abschluss 0x080064D8 (MCU-Befund §2):
    Delta zum Vorgänger (32 Bit), 0x80000000 übersprungen, Rückgabe =
    Datenbytes + (n_alt >> 2) + 2 (= Paketgröße)."""

    def __init__(self):
        self.prev = 0
        self.n = 0
        self.mask = 0
        self.body = b""

    def add(self, v):
        if int(v) & 0xFFFFFFFF == 0x80000000:
            return len(self.body) + (self.n >> 2) + 2
        n_old = self.n
        x = _wrap32(int(v) - self.prev)
        self.prev = int(v)
        sz = _delta_size(x)
        self.mask |= (sz - 1) << (2 * self.n)
        self.body += x.to_bytes(sz, "little", signed=True)
        self.n += 1
        return len(self.body) + (n_old >> 2) + 2

    def finish(self):
        if self.n == 0:
            return b""
        return (bytes([self.n]) + self.mask.to_bytes((self.n + 3) // 4, "big")
                + self.body)


class _McuRegister:
    """Nachbau read_prtouch_pres (Düse 0x08003F60) bzw. read_prtouch_step:
    64er-SCHIEBEREGISTER (Index 0 = ältestes), Antwort endet sobald
    ticks+datas > 41 Byte (Überlauf-Sample enthalten), len-Feld =
    Endindex - idx + 1 (bei vollständigem Bereich also n + 1, MCU-Befund
    §2). advance = Samples, die nach jeder Abfrage nachgeschoben werden
    (laufendes Register)."""

    def __init__(self, ticks, raw, filt=None, step=False, advance=0):
        self.ticks = [int(t) for t in ticks]
        self.raw = [int(v) for v in raw]
        self.filt = [int(v) for v in (filt if filt is not None else raw)]
        assert len(self.ticks) == len(self.raw) == len(self.filt) == 64
        self.step = step
        self.advance = int(advance)
        self.sent = []
        self.ns = []
        self.lens = []
        self._k = 0

    def _shift(self):
        self._k += 1
        t = (self.ticks[-1] + 94900) & 0xFFFFFFFF
        v = self.raw[-1] + (37 if self._k % 2 else -41)
        self.ticks = self.ticks[1:] + [t]
        self.raw = self.raw[1:] + [v]
        self.filt = self.filt[1:] + [(self._k * 13) % 200 - 100]

    def send(self, params):
        self.sent.append(list(params))
        if self.step:
            oid, idx, ln = params
            src = self.raw
        else:
            oid, is_src, ch, idx, ln = params
            if is_src >= 2:
                return {"tri_chxs": 0, "buf_len": 64, "idx": 0, "len": 0,
                        "ticks": b"", "datas": b""}
            src = self.raw if is_src == 1 else self.filt
        end = min(64, idx + ln)
        et, ed = _McuEncoder(), _McuEncoder()
        k = idx
        while k < end:
            if et.add(self.ticks[k]) + ed.add(src[k]) > 41:
                break
            k += 1
        res = {"ticks": et.finish(), "datas": ed.finish(), "idx": idx,
               "len": (k - idx) + 1, "buf_len": 64}
        self.ns.append(et.n)
        self.lens.append(res["len"])
        for _ in range(self.advance):
            self._shift()
        return res


def test_live_ring_read():
    """Live-Werte (Test D1) im Schieberegister-Nachbau: Index 0 leer (Tick 0,
    Wert 0 = Register noch nicht voll), danach 63 echte Samples."""
    cfg = K.parse_options({})
    t_live = [t for _, ts, _ in LIVE_D1 for t in ts]
    d_live = [v for _, _, ds in LIVE_D1 for v in ds]
    reg = _McuRegister([0] + t_live, [0] + d_live)
    pres = K.KlippyPresMcu(None, None, cfg, 0, {})
    pres._q_read = reg
    t, d = pres.read_pres()
    idxs = [p[3] for p in reg.sent]
    exp_idx = [sum(reg.ns[:i]) for i in range(len(reg.ns))]
    check("(ring) idx += n (n aus dem Paket), len = 64 - idx, is_src 1",
          idxs == exp_idx and all(p[1] == 1 and p[4] == 64 - p[3]
                                  for p in reg.sent), str(reg.sent))
    check("(ring) Blockgrößen 7-9 (41-Byte-Grenze) ns=%s" % reg.ns,
          all(7 <= n <= 9 for n in reg.ns[:-1]) and sum(reg.ns) == 64)
    check("(ring) leerer Eintrag (Tick 0/Wert 0) verworfen, 63 Samples",
          len(d) == 63 and 0 not in t and t == t_live and d == d_live)
    check("(ring) Ticks streng steigend", all(b > a for a, b in zip(t, t[1:])))
    check("(ring) Rohwerte im Ruhebereich", all(-1430000 < v < -1426000 for v in d))
    ok, info = K.check_rest_ok(d, cfg, ticks=t)
    check("(ring) Ruhetest inkl. Tickabstand ok (dif=%d, Median %.4f ms)"
          % (info["dif"], info.get("tick_median_ms") or 0), ok, str(info))
    t2, d2 = K.merge_ring([5, 3, 5, 4], [50, 30, 51, 40], 64)
    check("(ring) merge_ring sortiert + entfernt Duplikate", t2 == [3, 4, 5] and d2 == [30, 40, 51])
    t3, _ = K.merge_ring([0xFFFFFFF0, 0x10, 0xFFFFFFF8], [1, 3, 2], 64)
    check("(ring) merge_ring u32-Überlauf", t3 == [0xFFFFFFF0, 0xFFFFFFF8, 0x10])


class _SwapPres:
    def __init__(self):
        self.levels = []

    def set_swap(self, level):
        self.levels.append(level)
        return {"err": 0, "expar0": 0, "expar1": 71}


class _SwapStep:
    def __init__(self, answers):
        self.answers = list(answers)

    def read_swap(self):
        return {"err": 0, "expar0": self.answers.pop(0), "expar1": 71}


def test_swap_mechanism():
    cfg = K.parse_options({})
    # Erfolgstabelle aus der Emulation des Stock-Moduls (16.10.2026):
    for ans, exp in (((0, 1), True), ((1, 0), False), ((0, 0), False), ((1, 1), False)):
        pres, step = _SwapPres(), _SwapStep(ans)
        ctl = K.K2PrtouchController(cfg, pres, step, FakeToolhead(z_start=1.0),
                                    pause=lambda s: None)
        try:
            r = ctl.test_swap()
            got = True
            vers = (r["pres"]["pres_ver"], r["step"]["step_ver"])
        except K.K2PrtouchError:
            got = False
            vers = None
        check("(swap) Pegel %s -> %s" % (ans, "Success" if exp else "ERROR"), got is exp)
        check("(swap) Reihenfolge sta_swap 0, dann 1", pres.levels == [0, 1])
        if exp:
            check("(swap) Versionen 71/71", vers == (71, 71))



# =====================================================================
# Stock-Konfig -> MCU-Parameter (BELEGT per Emulation 16.10.2026)
# =====================================================================

def test_stock_mapping():
    cfg = K.parse_options({})
    tup = list(K.build_start_pres_tuple(cfg, 7))
    check("(map) Default start_prtouch_pres wie Stock",
          tup == [7, 60, 93750, 5, 1000, 800, 4000, 10000, 500, 128], str(tup))
    check("(map) Default aqc_tick 46875",
          list(K.build_start_step_tuple(cfg, 3)) == [3, 46875])
    cfg2 = K.parse_options({"pres_tri_fter": "7, 3, 0.25",
                            "pres_tri_hold": "4100, 10200, 530",
                            "pres_ded_tkms": "96.5",
                            "pres_acq_tkms": "0.5",
                            "step_acq_tkms": "0.5"})
    tup2 = list(K.build_start_pres_tuple(cfg2, 0))
    check("(map) Variation 7,3,0.25/4100,10200,530/96.5/0.5ms",
          tup2 == [0, 60, 60000, 7, 3000, 250, 4100, 10200, 530, 96], str(tup2))
    check("(map) Variation aqc_tick 60000", cfg2["aqc_tick"] == 60000)
    p = K.stock_to_mcu_params(60, 0.78125, (5, 1, 0.8), (4000, 10000, 500),
                              128.0, 0.390625)
    check("(map) lmt_dead abgeschnitten (96.9 -> 96)",
          K.stock_to_mcu_params(60, 0.78125, (5, 1, 0.8), (1, 2, 3), 96.9,
                                0.39)["lmt_dead"] == 96)
    check("(map) stock_to_mcu_params rein", p["acq_tick"] == 93750
          and p["aqc_tick"] == 46875 and p["ned_hftr"] == 1000)
    cfg3 = K.parse_options({"lmt_dead": "77", "pres_cfg_regs": "44",
                            "arm_settle_s": "0.3"})
    check("(map) direkte MCU-Option übersteuert",
          cfg3["lmt_dead"] == 77 and cfg3["cfg_regs"] == 44
          and cfg3["ned_lftr"] == 800)
    try:
        K.parse_options({"ned_lftr_scale": "10"})
        check("(map) ned_lftr_scale abgelehnt", False)
    except ValueError:
        check("(map) ned_lftr_scale abgelehnt", True)


# =====================================================================
# Probe-Hauptweg: K2PrtouchProbeCore mit Fakes (kein Klipper)
# =====================================================================

class ProbeLine:
    """Simuliert die Swap-Leitung PA15 (Düse) -> PC7 (Main)."""

    def __init__(self):
        self.level = 1
        self.armed = False
        self.stuck_low = False      # Leitung hängt auf 0 (ausgelöst)
        self.trigger_on_arm = False  # Düse löst sofort beim Scharfschalten aus
        self.ack_err = 0
        self.rest_noisy = False
        self.sensor_stuck = False   # Sensor ab: alle 5 ms Rohwert -1
        self.filt_noisy = False     # gefilterter Kanal zu nah an min_hold
        self.log = []

    def triggered(self):
        self.log.append("query")
        return self.stuck_low or self.level == 0


class FakeProbePres:
    def __init__(self, line):
        self.line = line

    def set_swap(self, level):
        self.line.log.append("swap%d" % level)
        self.line.level = int(level)
        return {"err": 0, "expar0": 0, "expar1": 71}

    def start_sampling(self):
        self.line.log.append("start_pres")
        if self.line.ack_err:
            return {"err": self.line.ack_err, "expar0": 0, "expar1": 0}
        self.line.armed = True
        self.line.level = 0 if self.line.trigger_on_arm else 1
        return {"err": 0, "expar0": 0, "expar1": 71}

    @staticmethod
    def _ticks():
        # 0,78125 ms = 93 750 Takte (1280 Hz, cfg_regs 60)
        return [10000000 + i * 93750 for i in range(64)]

    def read_pres(self):
        self.line.log.append("read_pres")
        if self.line.sensor_stuck:
            # MCU-Befund §1: DOUT hängt high -> alle 5 ms gelesen, Wert -1
            return [10000000 + i * 600000 for i in range(64)], [-1] * 64
        base = -1428000
        d = [base + ((5000 if i % 2 else -5000) if self.line.rest_noisy
                     else det_noise(i, 300)) for i in range(64)]
        return self._ticks(), d

    def read_pres_filtered(self):
        self.line.log.append("read_ftr")
        amp = 2500 if self.line.filt_noisy else 300
        return self._ticks(), [det_noise(3 * i + 1, amp) for i in range(64)]

    def disarm(self):
        self.line.log.append("stop_pres1")
        self.line.armed = False
        self.line.level = 1
        return {"err": 0, "expar0": 0, "expar1": 71}


class FakeProbeStep:
    def __init__(self, line, fail_disarm=False):
        self.line = line
        self.fail_disarm = fail_disarm

    def cont_step(self):
        self.line.log.append("cont")
        return {"cnt_x": 0, "cnt_y": 0, "cnt_z": 0}

    def start_sampling(self):
        self.line.log.append("start_step")
        return {"err": 0, "expar0": 0, "expar1": 71}

    def disarm(self):
        self.line.log.append("stop_step")
        if self.fail_disarm:
            raise RuntimeError("Timeout")
        return {"err": 0}


class FakeDescend:
    def __init__(self, line, raise_exc=None, z_true=0.0):
        self.line = line
        self.raise_exc = raise_exc
        self.z_true = z_true
        self.results = []
        self.calls = []

    def descend_until_trigger(self, gcmd, params):
        self.line.log.append("DESCEND")
        self.calls.append(dict(params))
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.line.armed:
            self.line.level = 0  # Kontakt zieht PA15/PC7 auf 0
        self.results.append(self.z_true)

    def pull_trigger_positions(self):
        r, self.results = self.results, []
        return r

    def clear_trigger_positions(self):
        self.results = []


class FakeGcmd:
    def __init__(self, params=None, command="PROBE"):
        self.params = dict(params or {})
        self.command = command

    def get_command(self):
        return self.command


def make_core(cfg_over=None, line=None, raise_exc=None, fail_disarm=False):
    # arm_settle_s 0.2 (Default; seit 17.10. >= 0,15 s Pflicht, die Fakes
    # pausieren nicht wirklich)
    base = {"allow_move": True, "arm_settle_s": 0.2}
    base.update(cfg_over or {})
    cfg = K.parse_options(base)
    line = line or ProbeLine()
    pauses = []
    waits = []

    def get_params(gcmd):
        return {"probe_speed": float(gcmd.params.get("PROBE_SPEED",
                                                     cfg["speed"])),
                "lift_speed": cfg["lift_speed"]}

    desc = FakeDescend(line, raise_exc=raise_exc)
    core = K.K2PrtouchProbeCore(cfg, get_params, desc, line.triggered,
                                lambda: waits.append(1), pauses.append)
    core.attach(FakeProbePres(line), FakeProbeStep(line, fail_disarm))
    core._test_pauses = pauses
    return core, line, desc, waits


EXPECT_ORDER = ["swap0", "query", "swap1", "query", "cont", "start_pres",
                "start_step", "read_pres", "read_ftr", "query", "DESCEND",
                "stop_step", "stop_pres1", "query"]


def _run(core, gcmd=None):
    gcmd = gcmd or FakeGcmd()
    try:
        sess = core.start_probe_session(gcmd)
    except K.K2PrtouchError as exc:
        return None, exc
    try:
        sess.run_probe(gcmd)
        return sess.pull_probed_results(), None
    except K.K2PrtouchError as exc:
        return None, exc
    finally:
        sess.end_probe_session()


def test_probe_arm_disarm_order():
    core, line, desc, waits = make_core()
    res, err = _run(core)
    check("(probe) Antastung ok", err is None and res == [0.0], str(err))
    check("(probe) Reihenfolge Arm vor Fahrt / Disarm danach (Stock)",
          line.log == EXPECT_ORDER, str(line.log))
    check("(probe) wait_moves vor dem Scharfschalten", len(waits) == 1)
    check("(probe) danach entschärft, PC7 wieder 1",
          not core.armed and line.level == 1 and core.arm_count == 1
          and core.disarm_count == 1)


def test_probe_disarm_on_exception():
    core, line, desc, _ = make_core(raise_exc=K.K2PrtouchError(
        "No trigger on probe after full movement"))
    res, err = _run(core)
    check("(probe) Ausnahme in der Fahrt wird weitergereicht",
          err is not None and "No trigger" in str(err), str(err))
    check("(probe) bei Ausnahme trotzdem entschärft",
          line.log[-3:] == ["stop_step", "stop_pres1", "query"]
          and not core.armed, str(line.log))
    # Auch fremde Ausnahmen (z.B. KeyboardInterrupt-ähnlich) -> entschärft:
    core2, line2, _, _ = make_core(raise_exc=ValueError("intern"))
    try:
        core2.run_probe(FakeGcmd())
        ok = False
    except ValueError:
        ok = True
    check("(probe) fremde Ausnahme -> entschärft + weitergereicht",
          ok and "stop_pres1" in line2.log and not core2.armed)
    # Disarm-Fehler nach erfolgreicher Fahrt -> Fehler, pres trotzdem versucht
    core3, line3, _, _ = make_core(fail_disarm=True)
    res3, err3 = _run(core3)
    check("(probe) Disarm-Fehler gemeldet, pres-Stop trotzdem gesendet",
          err3 is not None and "Entschärfen" in str(err3)
          and "stop_pres1" in line3.log, str(err3))


def test_probe_ack_error():
    line = ProbeLine()
    line.ack_err = 3
    core, line, desc, _ = make_core(line=line)
    res, err = _run(core)
    check("(probe) ack err!=0 -> Abbruch", err is not None
          and "err=3" in str(err), str(err))
    check("(probe) ack err -> keine Fahrt, entschärft",
          "DESCEND" not in line.log and "stop_pres1" in line.log)


def test_probe_pc7_already_triggered():
    # Ohne Signalweg-Test: Düse löst sofort beim Scharfschalten aus.
    line = ProbeLine()
    line.trigger_on_arm = True
    core, line, desc, _ = make_core({"swap_check": False}, line=line)
    res, err = _run(core)
    check("(probe) PC7 vor Fahrt ausgelöst -> Abbruch",
          err is not None and "VOR der Fahrt" in str(err), str(err))
    check("(probe) ausgelöst -> keine Fahrt, entschärft",
          "DESCEND" not in line.log and "stop_pres1" in line.log)
    # Mit Signalweg-Test: hängende Leitung fällt schon dort auf.
    line2 = ProbeLine()
    line2.stuck_low = True
    core2, line2, _, _ = make_core(line=line2)
    res2, err2 = _run(core2)
    check("(probe) hängende Leitung -> Signalweg-Test schlägt fehl",
          err2 is not None and "Signalweg" in str(err2)
          and "DESCEND" not in line2.log and "start_pres" not in line2.log,
          str(err2))
    # Ruhetest unruhig -> Abbruch vor Fahrt
    line3 = ProbeLine()
    line3.rest_noisy = True
    core3, line3, _, _ = make_core(line=line3)
    res3, err3 = _run(core3)
    check("(probe) Ruhetest unruhig -> Abbruch vor Fahrt",
          err3 is not None and "Ruhetest" in str(err3)
          and "DESCEND" not in line3.log and "stop_pres1" in line3.log)


def test_probe_allow_move_blocks():
    core, line, desc, waits = make_core({"allow_move": False})
    res, err = _run(core)
    check("(probe) allow_move False -> verständlicher Fehler",
          err is not None and "allow_move" in str(err), str(err))
    check("(probe) allow_move False -> kein MCU-Befehl, keine Fahrt",
          line.log == [] and not waits and not desc.calls, str(line.log))
    try:
        core.run_probe(FakeGcmd())
        ok = False
    except K.K2PrtouchError:
        ok = True
    check("(probe) auch run_probe direkt gesperrt", ok and line.log == [])
    core2, line2, _, _ = make_core()
    core2.attach(None, None)
    res2, err2 = _run(core2)
    check("(probe) ungebunden (vor connect) -> Abbruch ohne Fahrt",
          err2 is not None and "verbunden" in str(err2)
          and "DESCEND" not in line2.log)


def test_probe_speed_limit():
    core, line, desc, _ = make_core()
    res, err = _run(core, FakeGcmd({"PROBE_SPEED": "10"}))
    check("(probe) PROBE_SPEED=10 abgelehnt", err is not None
          and "5.0 mm/s" in str(err) and line.log == [], str(err))
    core2, line2, desc2, _ = make_core()
    gcmd = FakeGcmd({"PROBE_SPEED": "5"}, command="G28")
    res2, err2 = _run(core2, gcmd)
    check("(probe) PROBE_SPEED=5 erlaubt", err2 is None
          and desc2.calls[0]["probe_speed"] == 5.0, str(err2))
    for opt in ("speed", "probe_speed"):
        try:
            K.parse_options({opt: "5.5"})
            check("(probe) Config %s 5.5 abgelehnt" % opt, False)
        except ValueError:
            check("(probe) Config %s 5.5 abgelehnt" % opt, True)
    cfg = K.parse_options({"probe_speed": "3"})
    check("(probe) speed fällt auf probe_speed zurück", cfg["speed"] == 3.0)
    for v in ("10.5", "0"):
        try:
            K.parse_options({"probe_max_travel_mm": v})
            check("(probe) probe_max_travel_mm=%s abgelehnt" % v, False)
        except ValueError:
            check("(probe) probe_max_travel_mm=%s abgelehnt" % v, True)


def test_probe_target_z():
    f = K.compute_probe_target_z
    check("(probe) Ziel ohne G28 begrenzt auf max_travel",
          abs(f(5.0, -5.0, 8.0, False) - (-3.0)) < 1e-12)
    check("(probe) Ziel nie unter position_min",
          abs(f(2.0, -1.0, 8.0, False) - (-1.0)) < 1e-12)
    check("(probe) G28: bis position_min (Upstream)",
          abs(f(300.0, -5.0, 8.0, True) - (-5.0)) < 1e-12)
    cfg = K.parse_options({})
    check("(probe) Defaults: mode probe, allow_move False, z_offset 0, "
          "position_endstop = z_offset",
          cfg["mode"] == "probe" and cfg["allow_move"] is False
          and cfg["z_offset"] == 0.0 and cfg["position_endstop"] == 0.0)
    cfg2 = K.parse_options({"z_offset": "0.1"})
    check("(probe) position_endstop folgt z_offset",
          cfg2["position_endstop"] == 0.1)
    try:
        K.parse_options({"mode": "magic"})
        check("(probe) mode ungültig abgelehnt", False)
    except ValueError:
        check("(probe) mode ungültig abgelehnt", True)


class _Q:
    """Fake lookup_query_command-Objekt (zeichnet send-Daten auf)."""

    def __init__(self, name, fn):
        self.name, self.fn, self.sent = name, fn, []

    def send(self, data=(), minclock=0, reqclock=0, retry=True):
        self.sent.append(list(data))
        return self.fn(list(data))


def test_klippy_adapters_disarm_and_frozen():
    cfg = K.parse_options({})
    pres = K.KlippyPresMcu(None, None, cfg, 4, {})
    pres._q_stop = _Q("stop", lambda d: {"err": 0, "expar0": 0,
                                         "expar1": 71})
    pres.stop_sampling(ok=False)
    pres.disarm()
    check("(adapter) stop/disarm senden immer sta_swap=1",
          pres._q_stop.sent == [[4, 1], [4, 1]], str(pres._q_stop.sent))
    pres._q_start = _Q("start", lambda d: {"err": 2})
    check("(adapter) start_sampling liefert ack-Dict",
          pres.start_sampling()["err"] == 2
          and pres._q_start.sent[0] == [4, 60, 93750, 5, 1000, 800, 4000,
                                        10000, 500, 128])
    blk = _RingQuery._pack(list(range(100, 108)))
    pres._q_read = _Q("read", lambda d: {"ticks": b"" if d[4] == 0 else blk,
                                         "datas": b"" if d[4] == 0 else blk,
                                         "tri_chxs": 1})
    fr = pres.read_frozen()
    sent = pres._q_read.sent
    exp = ([[4, 2, 0, 0, 0]]
           + [[4, 0, 0, i, 64 - i] for i in range(0, 64, 8)]
           + [[4, 1, 0, i, 64 - i] for i in range(0, 64, 8)])
    check("(adapter) eingefrorener Ring: Stock-Abfragefolge", sent == exp,
          str(sent[:3]))
    check("(adapter) read_frozen dekodiert ftr/src", "ftr" in fr
          and "src" in fr and fr["status"].get("tri_chxs") == 1)
    step = K.KlippyStepMcu(None, None, cfg, 9)
    step._q_read = _Q("read", lambda d: {"ticks": b"" if d[2] == 0 else blk,
                                         "datas": b"" if d[2] == 0 else blk})
    step.read_frozen()
    exp2 = [[9, 0, 0]] + [[9, i, 64 - i] for i in range(0, 64, 8)]
    check("(adapter) step-Ring: Stock-Abfragefolge",
          step._q_read.sent == exp2)


# =====================================================================
# Integration gegen ECHTE Upstream-Dateien (optional, nur wenn die lokale
# Upstream-Kopie vorhanden ist): probe.py, homing.py (Homing-Klasse),
# manual_probe.py, gcode.py, pins.py – mit Fake-MCU/-Toolhead.
# =====================================================================

# Pfad zum klippy/-Ordner eines Upstream-Klipper-Checkouts: Umgebungsvariable
# KLIPPER_SRC (Checkout-Wurzel oder klippy/), sonst lokale Arbeitskopie.
_ks = os.environ.get("KLIPPER_SRC", "")
if _ks and os.path.isdir(os.path.join(_ks, "klippy")):
    _ks = os.path.join(_ks, "klippy")
UPSTREAM_KLIPPY = os.path.normpath(_ks or os.path.join(
    _HERE, "..", "arbeitsdateien-lokal", "upstream-klipper", "klippy"))


class _Ev:
    def __init__(self):
        self.handlers = {}

    def register(self, ev, cb):
        self.handlers.setdefault(ev, []).append(cb)

    def send(self, ev, *a):
        return [cb(*a) for cb in self.handlers.get(ev, [])]


def _integration_env(cfg_over=None, z_true=0.0, homing_speed=5.0):
    import collections
    if UPSTREAM_KLIPPY not in sys.path:
        sys.path.append(UPSTREAM_KLIPPY)
    import gcode as ugcode
    import pins as upins
    from extras import homing as uhoming

    line = ProbeLine()
    line.z_true = z_true
    rec = {"moves": [], "probing": [], "config": [], "homing_start": 0}

    class Reactor:
        def monotonic(self):
            return 100.0

        def pause(self, t):
            return t

    class Toolhead:
        def __init__(self):
            self.pos = [100.0, 100.0, 5.0, 0.0]
            self.homed = "xyz"

        def get_position(self):
            return list(self.pos)

        def set_position(self, pos, homing_axes=""):
            self.pos = list(pos)
            if homing_axes:
                self.homed = "".join(sorted(set(self.homed + homing_axes)))

        def manual_move(self, coord, speed):
            for i, v in enumerate(coord):
                if v is not None:
                    self.pos[i] = float(v)
            rec["moves"].append((list(self.pos), speed))

        def move(self, pos, speed):
            self.pos = list(pos)
            rec["moves"].append((list(self.pos), speed))

        def wait_moves(self):
            line.log.append("wait")

        def get_last_move_time(self):
            return 1.0

        def get_status(self, eventtime):
            return {"homed_axes": self.homed}

        def get_kinematics(self):
            return Kin()

    class Stepper:
        def __init__(self, name, oid):
            self.name, self.oid = name, oid

        def get_name(self, short=False):
            return self.name

        def get_oid(self):
            return self.oid

        def is_active_axis(self, a):
            return self.name.startswith("stepper_" + a)

    class Kin:
        def get_steppers(self):
            return [Stepper("stepper_x", 11), Stepper("stepper_y", 12),
                    Stepper("stepper_z", 13), Stepper("stepper_z1", 14)]

    class Endstop:
        def __init__(self, mcu, pin_params):
            self.mcu, self.pp = mcu, pin_params
            self.steppers = []

        def get_mcu(self):
            return self.mcu

        def add_stepper(self, s):
            self.steppers.append(s.get_name())

        def get_steppers(self):
            return list(self.steppers)

        def home_start(self, *a, **k):
            rec["homing_start"] += 1
            return None

        def home_wait(self, t):
            if line.armed:
                line.level = 0
                return 1.0
            return 0.0

        def query_endstop(self, print_time):
            # wie mcu.py:406-411: pin_value ^ invert
            line.log.append("query")
            return (0 if line.stuck_low else line.level) ^ self.pp["invert"]

    class Mcu:
        def __init__(self, name):
            self.name, self.oid, self.cbs = name, 0, []

        def register_config_callback(self, cb):
            self.cbs.append(cb)

        def create_oid(self):
            self.oid += 1
            return self.oid - 1

        def add_config_cmd(self, cmd, is_init=False, on_restart=False):
            rec["config"].append((self.name, cmd))

        def setup_pin(self, pin_type, pin_params):
            assert pin_type == "endstop"
            rec["endstop_pin"] = dict(pin_params)
            return Endstop(self, pin_params)

        def estimated_print_time(self, t):
            return 0.0

        def lookup_query_command(self, msgformat, respformat, oid=None,
                                 cq=None, is_async=False):
            name = msgformat.split()[0]
            pres = FakeProbePres(line)
            step = FakeProbeStep(line)
            regs = {}

            def fn(d):
                if name == "start_prtouch_pres":
                    rec["start_pres"] = list(d)
                    return pres.start_sampling()
                if name == "stop_prtouch_pres":
                    rec.setdefault("stop_pres", []).append(list(d))
                    if d[1] == 1 and line.armed:
                        return pres.disarm()
                    return pres.set_swap(d[1])
                if name == "read_prtouch_pres":
                    if d[1] >= 2:  # Status-Abfrage (is_src >= 2)
                        return {"tri_chxs": 0, "buf_len": 64, "idx": 0,
                                "len": 0, "ticks": b"", "datas": b""}
                    if d[3] == 0:  # neuer Ringlesevorgang = 1 Logeintrag
                        t, v = (pres.read_pres() if d[1] == 1
                                else pres.read_pres_filtered())
                        regs[d[1]] = _McuRegister(t, v)
                    return regs[d[1]].send(d)
                if name == "cont_prtouch_step":
                    return step.cont_step()
                if name == "start_prtouch_step":
                    rec["start_step"] = list(d)
                    return step.start_sampling()
                if name == "stop_prtouch_step":
                    step.disarm()
                    return {"err": 0, "expar0": line.level, "expar1": 71}
                return {}
            return _Q(name, fn)

    class Homing:
        """Fake PrinterHoming: probing_move simuliert HomingMove grob."""

        check_probe_first_home = uhoming.PrinterHoming.check_probe_first_home

        def probing_move(self, mcu_probe, pos, speed, check_movement=True):
            line.log.append("DESCEND")
            rec["probing"].append((list(pos), speed, check_movement))
            mcu_probe.home_start(0., 0., 0, 0.)
            if mcu_probe.home_wait(1.0) <= 0.:
                raise ugcode.CommandError("No trigger on probe after full "
                                          "movement")
            th.pos[2] = max(pos[2], th.pos[2] - (th.pos[2] - line.z_true))
            return list(th.pos)

    class Gcode:
        Coord = ugcode.Coord

        def __init__(self):
            self.cmds, self.out = {}, []
            # Echte Upstream-Namensprüfung (gcode.py register_command) ohne
            # GCodeDispatch.__init__ – fängt ungültige Namen wie K2_* ab.
            real = ugcode.GCodeDispatch.__new__(ugcode.GCodeDispatch)
            real.printer = Printer
            real.ready_gcode_handlers = real.gcode_handlers = {}
            real.base_gcode_handlers, real.gcode_help = {}, {}
            self._real = real

        def register_command(self, cmd, fn, when_not_ready=False, desc=None):
            assert cmd not in self.cmds, cmd
            self._real.register_command(cmd, fn, when_not_ready, desc)
            # Parser-Gegenprobe: Befehlszeile muss genau diesen Namen ergeben.
            parts = ugcode.GCodeDispatch.args_r.split(cmd.upper())
            assert ''.join(parts[:3]).strip() == cmd, "Parser zerlegt %r" % cmd
            self.cmds[cmd] = fn

        def respond_info(self, msg, log=True):
            self.out.append(msg)

        def respond_raw(self, msg):
            self.out.append(msg)

        def create_gcode_command(self, command, commandline, params):
            return ugcode.GCodeCommand(self, command, commandline, params,
                                       False)

    class Printer:
        command_error = ugcode.CommandError
        config_error = RuntimeError

        def __init__(self):
            self.objects, self.ev = {}, _Ev()

        def lookup_object(self, name, default=RuntimeError):
            if name in self.objects:
                return self.objects[name]
            if default is RuntimeError:
                raise RuntimeError("Unknown config object '%s'" % name)
            return default

        def add_object(self, name, obj):
            if name in self.objects:
                raise RuntimeError("Printer object '%s' already created"
                                   % name)
            self.objects[name] = obj

        def register_event_handler(self, ev, cb):
            self.ev.register(ev, cb)

        def send_event(self, ev, *a):
            return self.ev.send(ev, *a)

        def get_reactor(self):
            return reactor

        def get_start_args(self):
            return {}

        def is_shutdown(self):
            return False

    class Config:
        error = ValueError

        def __init__(self, name, opts, sections=None):
            self.name, self.opts = name, dict(opts)
            self.sections = sections or {}

        def get_printer(self):
            return printer

        def get_name(self):
            return self.name

        def get(self, o, default=None, note_valid=True):
            return self.opts.get(o, default)

        def getfloat(self, o, default=None, minval=None, maxval=None,
                     above=None, below=None, note_valid=True):
            return float(self.opts.get(o, default))

        def getint(self, o, default=None, minval=None, maxval=None,
                   note_valid=True):
            return int(self.opts.get(o, default))

        def getboolean(self, o, default=None, note_valid=True):
            return bool(self.opts.get(o, default))

        def getchoice(self, o, choices, default=None, note_valid=True):
            return self.opts.get(o, default)

        def has_section(self, s):
            return s in self.sections

        def getsection(self, s):
            return self.sections[s]

        def get_prefix_sections(self, p):
            return []

    reactor = Reactor()
    printer = Printer()
    th = Toolhead()
    gc = Gcode()
    pins = upins.PrinterPins()
    mcu, noz = Mcu("mcu"), Mcu("nozzle_mcu")
    pins.register_chip("mcu", mcu)
    pins.register_chip("nozzle_mcu", noz)
    for n, o in (("gcode", gc), ("toolhead", th), ("pins", pins),
                 ("mcu", mcu), ("mcu nozzle_mcu", noz),
                 ("homing", Homing())):
        printer.add_object(n, o)
    opts = {"allow_move": "True", "arm_settle_s": "0.2"}
    opts.update(cfg_over or {})
    zsec = Config("stepper_z", {"position_min": "-5"})
    config = Config("k2_prtouch", opts, {"stepper_z": zsec})
    obj = K.load_config(config)
    printer.add_object("k2_prtouch", obj)
    # Config-Phase + identify + connect wie Klipper:
    for m in (noz, mcu):
        for cb in m.cbs:
            cb()
    printer.send_event("klippy:mcu_identify")
    printer.send_event("klippy:connect")
    HomingInfo = collections.namedtuple(
        "homing_info", ["speed", "position_endstop", "retract_speed",
                        "retract_dist", "positive_dir",
                        "second_homing_speed"])
    hinfo = HomingInfo(homing_speed, 0.0, homing_speed, 0.0, False,
                       homing_speed / 2.0)
    return {"printer": printer, "obj": obj, "gcode": gc, "th": th,
            "line": line, "rec": rec, "pins": pins, "ugcode": ugcode,
            "uhoming": uhoming, "hinfo": hinfo}


def test_upstream_integration():
    if not os.path.isdir(os.path.join(UPSTREAM_KLIPPY, "extras")):
        print("SKIP (upstream) lokale Upstream-Kopie fehlt: %s"
              % UPSTREAM_KLIPPY)
        return
    env = _integration_env()
    gc, rec, line, th = env["gcode"], env["rec"], env["line"], env["th"]
    printer = env["printer"]
    check("(upstream) Objekt 'probe' registriert",
          printer.lookup_object("probe", None) is env["obj"].probe)
    check("(upstream) Endstop auf ^!PC7 (mcu, invert=1, pullup=1)",
          rec["endstop_pin"]["pin"] == "PC7"
          and rec["endstop_pin"]["invert"] == 1
          and rec["endstop_pin"]["pullup"] == 1
          and rec["endstop_pin"]["chip_name"] == "mcu")
    check("(upstream) Upstream-Befehle registriert",
          all(c in gc.cmds for c in ("PROBE", "QUERY_PROBE",
                                     "PROBE_ACCURACY", "PROBE_CALIBRATE",
                                     "Z_OFFSET_APPLY_PROBE",
                                     "KTWO_PRTOUCH_PROBE_STEPS",
                                     "KTWO_PRTOUCH_TEST_SWAP"))
          and "KTWO_PRTOUCH_HOME_Z" not in gc.cmds
          and "KTWO_PRTOUCH_PROBE" not in gc.cmds, str(sorted(gc.cmds)))
    cfgcmds = [c for _, c in rec["config"]]
    check("(upstream) config_prtouch_pres mit Pin-Namen wie Stock",
          "config_prtouch_pres oid=0 idx=0 swp_pin=PA15 clk_pin=PB13 "
          "sdo_pin=PB14" in cfgcmds, str(cfgcmds))
    check("(upstream) config_prtouch_step: X/Y/letzter Z-Stepper, PC7",
          "config_prtouch_step oid=0 oid_xstp=11 oid_ystp=12 oid_zstp=14 "
          "swp_pin=PC7" in cfgcmds, str(cfgcmds))
    ep = env["obj"].probe.mcu_endstop
    check("(upstream) Z-Stepper am Endstop (LookupZSteppers)",
          ep.get_steppers() == ["stepper_z", "stepper_z1"])
    # PROBE über die echte ProbeCommandHelper/SampleAveragingHelper-Kette:
    line.log.clear()
    g = gc.create_gcode_command("PROBE", "PROBE", {})
    gc.cmds["PROBE"](g)
    check("(upstream) PROBE ok + Ergebnis gemeldet",
          any("estimate contact at z=0.000000" in m for m in gc.out),
          str(gc.out[-2:]))
    check("(upstream) PROBE: Stock-Reihenfolge um die Fahrt",
          line.log == ["wait"] + EXPECT_ORDER, str(line.log))
    check("(upstream) start_prtouch_pres/step-Parameter wie Stock",
          rec["start_pres"][1:] == [60, 93750, 5, 1000, 800, 4000, 10000,
                                    500, 128]
          and rec["start_step"][1:] == [46875])
    pos, speed, chk = rec["probing"][-1]
    check("(upstream) PROBE: Ziel 5.0-8.0 = -3.0, 5 mm/s, check_movement",
          abs(pos[2] - (-3.0)) < 1e-9 and speed == 5.0 and chk is True,
          str(rec["probing"][-1]))
    # QUERY_PROBE: invertiert -> 'open' in Ruhe
    gc.cmds["QUERY_PROBE"](gc.create_gcode_command("QUERY_PROBE", "", {}))
    check("(upstream) QUERY_PROBE in Ruhe 'open'", gc.out[-1] == "probe: open")
    # PROBE_ACCURACY SAMPLES=3 -> 3 x Arm/Disarm
    n0 = env["obj"].probe.core.arm_count
    gc.cmds["PROBE_ACCURACY"](gc.create_gcode_command(
        "PROBE_ACCURACY", "PROBE_ACCURACY SAMPLES=3", {"SAMPLES": "3"}))
    core = env["obj"].probe.core
    check("(upstream) PROBE_ACCURACY: 3x scharf/entschärft",
          core.arm_count - n0 == 3 and core.disarm_count == core.arm_count
          and any("probe accuracy results" in m for m in gc.out))
    # PROBE_SPEED=10 -> Fehler, keine Fahrt
    nprob = len(rec["probing"])
    try:
        gc.cmds["PROBE"](gc.create_gcode_command(
            "PROBE", "PROBE PROBE_SPEED=10", {"PROBE_SPEED": "10"}))
        ok = False
    except env["ugcode"].CommandError as exc:
        ok = "5.0 mm/s" in str(exc)
    check("(upstream) PROBE PROBE_SPEED=10 -> CommandError, keine Fahrt",
          ok and len(rec["probing"]) == nprob)
    # G28 Z über die ECHTE Homing-Klasse (homing.py:265-294):
    hv = env["pins"].setup_pin("endstop", "probe:z_virtual_endstop")
    check("(upstream) probe:z_virtual_endstop position_endstop = z_offset",
          hv.get_position_endstop() == 0.0)

    class Rail:
        def get_endstops(self):
            return [(hv, "probe")]

        def get_homing_info(self):
            return env["hinfo"]

    th.pos = [175.0, 175.0, 120.0, 0.0]
    hs = env["uhoming"].Homing(printer)
    line.log.clear()
    hs.home_rails([Rail()], [None, None, 120.0 * 1.5, None],
                  [None, None, 0.0, None])
    pos, speed, chk = rec["probing"][-1]
    check("(upstream) G28 Z: Fahrt bis position_min, 5 mm/s, ohne "
          "check_movement (1. Versuch)",
          abs(pos[2] - (-5.0)) < 1e-9 and speed == 5.0 and chk is False,
          str(rec["probing"][-1]))
    check("(upstream) G28 Z: Z danach = 0 (Kontakt)", abs(th.pos[2]) < 1e-9,
          str(th.pos))
    check("(upstream) G28 Z: scharf vor / entschärft nach Fahrt",
          line.log[:2] == ["wait", "swap0"] and "stop_pres1" in line.log
          and not core.armed, str(line.log))


def test_upstream_integration_guards():
    if not os.path.isdir(os.path.join(UPSTREAM_KLIPPY, "extras")):
        print("SKIP (upstream) lokale Upstream-Kopie fehlt")
        return
    # allow_move False: G28 Z bricht vor jeder Bewegung ab.
    env = _integration_env({"allow_move": "False"})
    hv = env["pins"].setup_pin("endstop", "probe:z_virtual_endstop")

    class Rail:
        def get_endstops(self):
            return [(hv, "probe")]

        def get_homing_info(self):
            return env["hinfo"]

    env["th"].pos = [175.0, 175.0, 120.0, 0.0]
    env["line"].log.clear()
    try:
        env["uhoming"].Homing(env["printer"]).home_rails(
            [Rail()], [None, None, 180.0, None], [None, None, 0.0, None])
        ok = False
    except env["ugcode"].CommandError as exc:
        ok = "allow_move" in str(exc)
    check("(upstream) G28 Z mit allow_move False -> Fehler, keine Fahrt",
          ok and env["rec"]["probing"] == [] and env["line"].log == []
          and env["th"].pos[2] == 120.0, str(env["line"].log))
    # homing_speed 10 in [stepper_z] -> abgelehnt vor Bewegung
    env2 = _integration_env(homing_speed=10.0)
    hv2 = env2["pins"].setup_pin("endstop", "probe:z_virtual_endstop")

    class Rail2(Rail):
        def get_endstops(self):
            return [(hv2, "probe")]

        def get_homing_info(self):
            return env2["hinfo"]

    env2["line"].log.clear()
    try:
        env2["uhoming"].Homing(env2["printer"]).home_rails(
            [Rail2()], [None, None, 180.0, None], [None, None, 0.0, None])
        ok = False
    except env2["ugcode"].CommandError as exc:
        ok = "homing_speed" in str(exc)
    check("(upstream) G28 mit homing_speed 10 -> abgelehnt, keine Fahrt",
          ok and env2["rec"]["probing"] == [] and env2["line"].log == [])
    # Leitung hängt auf 0 -> PROBE bricht ab, Düse entschärft
    env3 = _integration_env()
    env3["line"].stuck_low = True
    env3["line"].log.clear()
    try:
        env3["gcode"].cmds["PROBE"](env3["gcode"].create_gcode_command(
            "PROBE", "PROBE", {}))
        ok = False
    except env3["ugcode"].CommandError as exc:
        ok = "Signalweg" in str(exc)
    check("(upstream) PROBE bei hängender Leitung -> CommandError, keine "
          "Fahrt, entschärft",
          ok and env3["rec"]["probing"] == []
          and env3["line"].log[-3:] == ["stop_step", "swap1", "query"]
          and env3["rec"]["stop_pres"][-1][1] == 1, str(env3["line"].log))
    # mode steps: kein 'probe'-Objekt, Rückfallbefehle registriert
    env4 = _integration_env({"mode": "steps"})
    check("(upstream) mode steps: kein 'probe', HOME_Z/MESH/PROBE_STEPS da",
          env4["printer"].lookup_object("probe", None) is None
          and all(c in env4["gcode"].cmds for c in (
              "KTWO_PRTOUCH_HOME_Z", "KTWO_PRTOUCH_MESH",
              "KTWO_PRTOUCH_PROBE_STEPS"))
          and "PROBE" not in env4["gcode"].cmds)

# =====================================================================
# Absicherung nach PRTOUCH-MCU-BEFUND-2026-10-17 (§7) – Punkte 1-6
# =====================================================================

def _raises(opts, needle=None):
    """parse_options muss ValueError werfen (optional mit Textteil)."""
    try:
        K.parse_options(opts)
    except ValueError as exc:
        return needle is None or needle in str(exc)
    return False


def test_1017_config_validation():
    """Punkt 1: Antast-Parameter beim Laden prüfen (kein Start)."""
    cfg = K.parse_options({})
    check("(1017-1) Stock-Defaults gültig, unverändert",
          list(K.build_start_pres_tuple(cfg, 1))
          == [1, 60, 93750, 5, 1000, 800, 4000, 10000, 500, 128])
    check("(1017-1) min_hold == max_hold (pres_tri_hold) -> Stream-Modus abgelehnt",
          _raises({"pres_tri_hold": "4000, 4000, 500"}, "Stream"))
    check("(1017-1) min_hold == max_hold (direkte MCU-Option) abgelehnt",
          _raises({"max_hold": "4000"}, "NIE"))
    check("(1017-1) max_hold != min_hold (auch < min_hold) erlaubt",
          not _raises({"max_hold": "3999"}))
    check("(1017-1) ned_hftr = 0 (pres_tri_fter[1] = 0) -> abgelehnt",
          _raises({"pres_tri_fter": "5, 0, 0.8"}, "Hochpass"))
    check("(1017-1) ned_hftr = 0 (direkt) abgelehnt",
          _raises({"ned_hftr": "0"}, "ned_hftr"))
    check("(1017-1) ned_hftr negativ abgelehnt", _raises({"ned_hftr": "-1"}))
    for v, ok in (("0", True), ("1", True), ("800", True), ("1000", True),
                  ("1001", False), ("5000", False)):
        check("(1017-1) ned_lftr=%s %s" % (v, "erlaubt" if ok else "abgelehnt"),
              _raises({"ned_lftr": v}, "ned_lftr") is (not ok))
    check("(1017-1) pres_tri_fter[2] = 1.2 (-> 1200) abgelehnt",
          _raises({"pres_tri_fter": "5, 1, 1.2"}, "schwingend"))
    check("(1017-1) lmt_dead 63 abgelehnt", _raises({"lmt_dead": "63"}, "SAMPLE"))
    check("(1017-1) pres_ded_tkms 50 (-> 50) abgelehnt",
          _raises({"pres_ded_tkms": "50"}, "lmt_dead"))
    check("(1017-1) lmt_dead 64 erlaubt", not _raises({"lmt_dead": "64"}))
    check("(1017-1) cfg_regs 61 unbekannt -> abgelehnt",
          _raises({"pres_cfg_regs": "61"}, "unbekannt"))
    check("(1017-1) cfg_regs 56 (1280 Hz/PGA64) erlaubt",
          not _raises({"pres_cfg_regs": "56"}))
    check("(1017-1) cfg_regs 44 (640 Hz) mit arm_settle_s 0.3 erlaubt",
          not _raises({"pres_cfg_regs": "44", "arm_settle_s": "0.3"}))
    check("(1017-1) min_hold 999 (< Ruherauschen) abgelehnt",
          _raises({"pres_tri_hold": "999, 10000, 500"}, "Ruherauschen"))
    check("(1017-1) min_hold 1000 erlaubt",
          not _raises({"pres_tri_hold": "1000, 10000, 500"}))
    check("(1017-1) add_hold < 0 abgelehnt",
          _raises({"pres_tri_hold": "4000, 10000, -1"}, "add_hold"))
    check("(1017-1) pres_idx 1 abgelehnt", _raises({"pres_idx": "1"}, "pres_idx"))
    check("(1017-1) acq_tick 0 abgelehnt", _raises({"acq_tick": "0"}))
    check("(1017-1) ned_tftr 0 (nur Schalter) erlaubt",
          not _raises({"ned_tftr": "0"}))
    # Klipper-Hülle: Config-Fehler -> config.error, kein Start
    class _Cfg(dict):
        class error(Exception):
            pass

        def get_printer(self):
            raise AssertionError("darf nicht erreicht werden")

        def get_options(self):
            return list(self.keys())

    class _P:
        def get_reactor(self):
            return None

        def lookup_object(self, name):
            return None

    class _C(_Cfg):
        def get_printer(self):
            return _P()

    try:
        K.PrinterK2Prtouch(_C({"pres_tri_hold": "4000, 4000, 500"}))
        ok = False
    except _Cfg.error as exc:
        ok = "Config-Fehler" in str(exc) and "Stream" in str(exc)
    check("(1017-1) Hülle: Fehler als config.error, kein Start", ok)


def test_1017_arm_settle():
    """Punkt 2: arm_settle_s an die Totzeit koppeln."""
    cfg = K.parse_options({})
    check("(1017-2) Abtastperiode aus cfg_regs 60 = 0,78125 ms",
          abs(K.pres_sample_period_s(cfg) - 0.00078125) < 1e-12)
    check("(1017-2) Totzeit Stock 128 Samples = 100 ms",
          abs(K.pres_dead_time_s(cfg) - 0.1) < 1e-9)
    check("(1017-2) Minimum Stock = 1,5 × 128 × 0,78125 ms = 0,15 s",
          abs(K.min_arm_settle_s(cfg) - 0.15) < 1e-9
          and abs(cfg["arm_settle_min_s"] - 0.15) < 1e-9)
    check("(1017-2) Default 0,2 s gültig", cfg["arm_settle_s"] == 0.2)
    check("(1017-2) arm_settle_s 0.149 abgelehnt",
          _raises({"arm_settle_s": "0.149"}, "Totzeit"))
    check("(1017-2) arm_settle_s 0 abgelehnt", _raises({"arm_settle_s": "0"}))
    check("(1017-2) arm_settle_s 0.15 erlaubt", not _raises({"arm_settle_s": "0.15"}))
    c64 = K.parse_options({"lmt_dead": "64"})
    check("(1017-2) lmt_dead 64: Minimum über Filter-Einschwingen 128 (0,15 s)",
          abs(c64["arm_settle_min_s"] - 0.15) < 1e-9
          and abs(c64["pres_dead_time_s"] - 0.05) < 1e-9)
    check("(1017-2) lmt_dead 256 braucht 0,3 s (0,2 abgelehnt)",
          _raises({"lmt_dead": "256"}, "arm_settle_s")
          and not _raises({"lmt_dead": "256", "arm_settle_s": "0.3"}))
    check("(1017-2) cfg_regs 44 (640 Hz) braucht 0,3 s (0,2 abgelehnt)",
          _raises({"pres_cfg_regs": "44"}, "arm_settle_s"))
    check("(1017-2) lmt_dead 2000 > Maximum 2 s -> abgelehnt",
          _raises({"lmt_dead": "2000", "arm_settle_s": "2.0"}, "Maximum"))
    check("(1017-2) acq_tick ändert die Periode NICHT (Bitbang)",
          abs(K.parse_options({"pres_acq_tkms": "2.0"})["arm_settle_min_s"]
              - 0.15) < 1e-9)
    # Laufzeit: Kern pausiert genau arm_settle_s, fail-safe bei zu kurz
    core, line, desc, _ = make_core({"arm_settle_s": "0.25"})
    res, err = _run(core)
    check("(1017-2) Kern pausiert arm_settle_s nach dem Scharfschalten",
          err is None and core._test_pauses == [0.25], str(core._test_pauses))
    core2, line2, desc2, _ = make_core()
    core2.cfg["arm_settle_s"] = 0.05  # an parse_options vorbei
    res2, err2 = _run(core2)
    check("(1017-2) Kern: zu kurze Wartezeit -> Abbruch vor Fahrt, entschärft",
          err2 is not None and "Totzeit" in str(err2)
          and "DESCEND" not in line2.log and "stop_pres1" in line2.log,
          str(err2))


def test_1017_min_speed():
    """Punkt 3: Mindestgeschwindigkeit (ANNAHME 2,5 mm/s bei Stock)."""
    cfg = K.parse_options({})
    check("(1017-3) Mindestgeschwindigkeit Stock 2,5 mm/s",
          abs(K.min_probe_speed(cfg) - 2.5) < 1e-12 and cfg["speed_min"] == 2.5)
    check("(1017-3) speed 2.4 abgelehnt", _raises({"speed": "2.4"}, "zu langsam"))
    check("(1017-3) speed 2.5 erlaubt", not _raises({"speed": "2.5"}))
    check("(1017-3) probe_speed 2 (speed fällt zurück) abgelehnt",
          _raises({"probe_speed": "2"}, "zu langsam"))
    check("(1017-3) mode steps: probe_speed 2 erlaubt (Host stoppt, nicht MCU)",
          not _raises({"mode": "steps", "probe_speed": "2"}))
    c = K.parse_options({"pres_tri_hold": "8000, 10000, 1000"})
    check("(1017-3) Schwellen 8000/1000 -> Minimum 5,0 mm/s",
          abs(c["speed_min"] - 5.0) < 1e-9
          and _raises({"pres_tri_hold": "8000, 10000, 1000", "speed": "4.9"},
                      "zu langsam"))
    check("(1017-3) Schwellen 9000/1000 -> Minimum > 5 -> unzulässig",
          _raises({"pres_tri_hold": "9000, 10000, 1000"}, "zu langsam"))
    check("(1017-3) cfg_regs 56 (PGA 64) -> Minimum 5,0 mm/s",
          abs(K.parse_options({"pres_cfg_regs": "56"})["speed_min"] - 5.0) < 1e-9)
    check("(1017-3) cfg_regs 44 (640 Hz) -> nie unter 2,5 mm/s",
          K.parse_options({"pres_cfg_regs": "44",
                           "arm_settle_s": "0.3"})["speed_min"] == 2.5)
    core, line, desc, _ = make_core()
    res, err = _run(core, FakeGcmd({"PROBE_SPEED": "2"}))
    check("(1017-3) PROBE_SPEED=2 -> Fehler 'zu langsam', kein MCU-Befehl",
          err is not None and "zu langsam" in str(err)
          and "second_homing_speed" in str(err) and line.log == []
          and not desc.calls, str(err))
    core2, line2, desc2, _ = make_core()
    res2, err2 = _run(core2, FakeGcmd({"PROBE_SPEED": "2.5"}))
    check("(1017-3) PROBE_SPEED=2.5 erlaubt", err2 is None
          and desc2.calls[0]["probe_speed"] == 2.5, str(err2))
    check("(1017-3) check_probe_speed: 0 / 5.1 / 2.49 abgelehnt, 3 ok",
          K.check_probe_speed(0, cfg) and K.check_probe_speed(5.1, cfg)
          and K.check_probe_speed(2.49, cfg)
          and K.check_probe_speed(3.0, cfg) is None)


def test_1017_rest_extended():
    """Punkt 4: Ruhetest um Tickabstand, -1/24 Bit, gefilterten Kanal."""
    cfg = K.parse_options({})
    per = 93750  # 0,78125 ms in Takten
    tk = [5000000 + i * per for i in range(64)]
    ok_d = [-1428000 + det_noise(i, 300) for i in range(64)]
    ok_f = [det_noise(i, 300) for i in range(64)]
    ok, info = K.check_rest_ok(ok_d, cfg, ticks=tk, filt=ok_f)
    check("(1017-4) gesunder Sensor: ok", ok and not info["errors"], str(info))
    stuck = [-1] * 64
    ok_old = K.rest_info(stuck)["dif"] <= cfg["rest_max_dif"]
    ok, info = K.check_rest_ok(stuck, cfg)
    check("(1017-4) Sensor ab (-1, dif 0): alter dif-Test bestünde, neuer nicht",
          ok_old and not ok and any("-1" in e for e in info["errors"])
          and any("konstant" in e for e in info["errors"]), str(info["errors"]))
    ok, info = K.check_rest_ok([-1428000] * 64, cfg)
    check("(1017-4) konstante Rohwerte (dif 0) abgelehnt",
          not ok and any("konstant" in e for e in info["errors"]))
    big = list(ok_d)
    big[5] = 1 << 23
    ok, info = K.check_rest_ok(big, cfg)
    check("(1017-4) Wert außerhalb 24 Bit abgelehnt",
          not ok and any("24 Bit" in e for e in info["errors"]))
    ok, info = K.check_rest_ok(ok_d[:10], cfg)
    check("(1017-4) zu wenige Samples (10) abgelehnt",
          not ok and any("gültige Rohwerte" in e for e in info["errors"]))
    ok, info = K.check_rest_ok([], cfg)
    check("(1017-4) leeres Register abgelehnt (kein Fehlerwurf)", not ok)
    for name, step, good in (("5 ms (Sensor hängt)", 600000, False),
                             ("55 µs (DOUT low)", 6600, False),
                             ("1,5625 ms (falsche Rate)", 187500, False),
                             ("0,7907 ms (live)", 94884, True),
                             ("0,85 ms (+8,8 %)", 102000, True),
                             ("0,87 ms (+11 %)", 104400, False)):
        t2 = [5000000 + i * step for i in range(64)]
        ok, info = K.check_rest_ok(ok_d, cfg, ticks=t2)
        check("(1017-4) Tickabstand %s -> %s" % (name, "ok" if good else "Abbruch"),
              ok is good, str(info["errors"]))
    # Jitter: ein Sample 60 µs zu spät gelesen (langer + kurzer Abstand) -> ok
    jit = list(tk)
    jit[20] += 7200
    ok, info = K.check_rest_ok(ok_d, cfg, ticks=jit)
    check("(1017-4) Jitter ±60 µs an einem Sample -> ok", ok, str(info["errors"]))
    # Einzelner viel zu kurzer Abstand (0,3 × Periode) -> Abbruch über Minimum
    short = list(tk)
    short[20] = short[19] + 28000
    short[21:] = [t + 28000 - per for t in short[21:]]
    ok, info = K.check_rest_ok(ok_d, cfg, ticks=sorted(short))
    check("(1017-4) Einzelabstand 0,23 ms (< 0,5 × Periode) -> Abbruch",
          not ok and any("Minimum" in e for e in info["errors"]),
          str(info["errors"]))
    # Laufendes Register (Lücken zwischen Blöcken): Median trotzdem ≈ Periode
    gap = [5000000 + (i + 11 * (i // 8)) * per for i in range(64)]
    ok, info = K.check_rest_ok(ok_d, cfg, ticks=gap)
    check("(1017-4) Blocklücken (laufendes Register) -> ok über Median", ok,
          str(info["errors"]))
    noisy_f = [2100 if i == 30 else v for i, v in enumerate(ok_f)]
    ok, info = K.check_rest_ok(ok_d, cfg, ticks=tk, filt=noisy_f)
    check("(1017-4) gefiltert max 2100 > 0,5 × min_hold 4000 -> Abbruch",
          not ok and any("gefilterter Kanal" in e for e in info["errors"])
          and info["filt_max_abs"] == 2100 and info["filt_limit"] == 2000.0)
    neg_f = [-2100 if i == 30 else v for i, v in enumerate(ok_f)]
    ok, info = K.check_rest_ok(ok_d, cfg, filt=neg_f)
    check("(1017-4) gefiltert -2100 (Betrag) -> Abbruch", not ok)
    cfg1 = K.parse_options({"rest_filt_max_frac": "1.0"})
    ok, info = K.check_rest_ok(ok_d, cfg1, filt=noisy_f)
    check("(1017-4) rest_filt_max_frac 1.0 -> 2100 < 4000 ok", ok)
    check("(1017-4) rest_filt_max_frac 0 / 1.5 abgelehnt",
          _raises({"rest_filt_max_frac": "0"}) and
          _raises({"rest_filt_max_frac": "1.5"}))
    ok, info = K.check_rest_ok(ok_d, cfg, filt=ok_f[:5])
    check("(1017-4) zu wenige gefilterte Werte abgelehnt", not ok)
    # Kern (mode probe): Abbruch vor Fahrt, entschärft
    for flag, needle in (("sensor_stuck", "-1"), ("filt_noisy", "gefilterter"),
                         ("rest_noisy", "dif=")):
        line = ProbeLine()
        setattr(line, flag, True)
        core, line, desc, _ = make_core(line=line)
        res, err = _run(core)
        check("(1017-4) Kern %s -> Ruhetest-Abbruch vor Fahrt" % flag,
              err is not None and "Ruhetest" in str(err) and needle in str(err)
              and "DESCEND" not in line.log and "stop_pres1" in line.log
              and not core.armed, str(err))
    core, line, desc, _ = make_core()
    res, err = _run(core)
    ri = core.last["arm"]["rest"]
    check("(1017-4) Kern liest roh + gefiltert, Info mit Tick/Filter",
          err is None and "read_ftr" in line.log
          and abs(ri["tick_median_ms"] - 0.78125) < 1e-6
          and ri["filt_max_abs"] <= 300, str(ri))
    # Rückfallweg: Tickprüfung greift auch dort
    cfg_s, tool, pres, step, ctl, log = make_ctl(z_start=0.2, z_true=0.0)
    pres._ticks = lambda: struct.pack("<64I", *[10000000 + i * 600000
                                               for i in range(64)])
    r = ctl.probe_point()
    check("(1017-4) Rückfallweg: Tickabstand 5 ms -> rest_error, keine Fahrt",
          r.get("status") == "rest_error" and "Tickabstand" in r.get("error", "")
          and not tool.moves, str(r)[:200])


def _varied_register():
    """64 Samples, deren Datendeltas Blöcke mit n = 9, 7, 8 … erzwingen."""
    ticks = [146176342 + i * 94900 for i in range(64)]
    vals, v = [], -1428000
    for i in range(64):
        if i == 0:
            d = 0
        elif i <= 8:
            d = 50 if i % 2 else -50      # 1-Byte-Deltas -> Block n = 9
        elif i <= 15:
            d = 300 if i % 2 else -300    # 2-Byte-Deltas -> Block n = 7
        elif i <= 23:
            d = 150 if i % 2 else -110    # gemischt 1/2 Byte -> n = 8
        else:
            d = 60 if i % 2 else -60      # 1 Byte -> 4 × 9, Rest 4 (vollständig)
        v += d
        vals.append(v)
    return ticks, vals


def test_1017_ring_blocks():
    """Punkt 5: Blocklesen idx += n (n aus dem Paket), len-Feld ignoriert."""
    cfg = K.parse_options({})
    ticks, vals = _varied_register()
    filt = [det_noise(i, 200) for i in range(64)]
    reg = _McuRegister(ticks, vals, filt=filt)
    pres = K.KlippyPresMcu(None, None, cfg, 3, {})
    pres._q_read = reg
    t, d = pres.read_pres()
    check("(1017-5) Blockgrößen 9/7/8 kommen vor (ns=%s)" % reg.ns,
          reg.ns[:3] == [9, 7, 8] and set(reg.ns[:-1]) <= {7, 8, 9}
          and reg.ns[-1] == 4)
    check("(1017-5) alle 64 Samples genau einmal, lückenlos",
          t == ticks and d == vals)
    idxs = [p[3] for p in reg.sent]
    check("(1017-5) nächster idx = idx + n, angefragt len = 64 - idx",
          idxs == [sum(reg.ns[:i]) for i in range(len(reg.ns))]
          and all(p[4] == 64 - p[3] for p in reg.sent), str(idxs))
    check("(1017-5) len-Feld der Antwort beim vollständigen Restblock n+1 "
          "(%d bei %d Samples) – nicht benutzt" % (reg.lens[-1], reg.ns[-1]),
          reg.lens[-1] == reg.ns[-1] + 1 and len(d) == 64)
    # Alte Methode (feste 8er-Schritte, len 8) hätte Samples verloren:
    reg_old = _McuRegister(ticks, vals)
    got = set()
    for idx in range(0, 64, 8):
        r = reg_old.send([3, 1, 0, idx, 8])
        got.update(K.decode_ticks(r["ticks"], cfg))
    check("(1017-5) Gegenprobe: feste 8er-Schritte verlieren %d Sample(s)"
          % (64 - len(got)), len(got) < 64)
    # Gefilterter Kanal (is_src=0) über denselben Weg:
    n_sent = len(reg.sent)
    tf, df = pres.read_pres_filtered()
    check("(1017-5) is_src=0 liefert den gefilterten Kanal vollständig",
          df == filt and tf == ticks
          and all(p[1] == 0 for p in reg.sent[n_sent:]))
    # Register noch nicht voll: leere Einträge werden verworfen
    reg2 = _McuRegister([0] * 20 + ticks[20:], [0] * 20 + vals[20:])
    pres._q_read = reg2
    t2, d2 = pres.read_pres()
    check("(1017-5) Tick-0/Wert-0-Einträge verworfen (44 von 64)",
          len(d2) == 44 and t2 == ticks[20:])
    # n = 0 vor Ende -> Abbruch, keine Endlosschleife
    class _Zero:
        def __init__(self):
            self.sent = []

        def send(self, p):
            self.sent.append(list(p))
            return {"ticks": b"", "datas": b"", "len": 1}
    z = _Zero()
    ts, ds, ix = K.read_ring_blocks(z.send, lambda i, ln: [0, 1, 0, i, ln],
                                    cfg, K.decode_pres_datas)
    check("(1017-5) n = 0 -> Abbruch nach 1 Abfrage", ts == [] and len(z.sent) == 1)
    # ticks/datas ungleich lang -> Fehler (fail-safe)
    blk8 = _RingQuery._pack(list(range(100, 108)))
    blk7 = _RingQuery._pack(list(range(100, 107)))
    try:
        K.read_ring_blocks(lambda p: {"ticks": blk8, "datas": blk7},
                           lambda i, ln: [0, 1, 0, i, ln], cfg,
                           K.decode_pres_datas)
        ok = False
    except ValueError:
        ok = True
    check("(1017-5) ticks/datas mit verschiedenem n -> ValueError", ok)
    # Laufendes Register (3 neue Samples je Abfrage): Ruhetest trotzdem ok
    calm = [-1428000 + det_noise(i, 300) for i in range(64)]
    reg3 = _McuRegister(ticks, calm, advance=3)
    pres._q_read = reg3
    t3, d3 = pres.read_pres()
    ok, info = K.check_rest_ok(d3, cfg, ticks=t3)
    check("(1017-5) laufendes Register: sortiert, Ruhetest ok (Median %.3f ms)"
          % (info.get("tick_median_ms") or 0),
          all(b > a for a, b in zip(t3, t3[1:])) and ok, str(info["errors"]))
    # Eingefrorener Ring (Diagnose): Status, dann is_src 0 und 1 mit idx += n
    reg4 = _McuRegister(ticks, vals, filt=filt)
    pres._q_read = reg4
    fr = pres.read_frozen()
    s0 = reg4.sent
    nf = len(reg4.ns) // 2
    check("(1017-5) read_frozen: Status [oid,2,0,0,0], dann FTR/SRC idx += n",
          s0[0] == [3, 2, 0, 0, 0] and fr["ftr"][1] == filt
          and fr["src"][1] == vals
          and [p[3] for p in s0[1:1 + nf]]
          == [sum(reg4.ns[:i]) for i in range(nf)], str(s0[:4]))
    # step-Seite (Main): gleiches Verfahren
    regs = _McuRegister(ticks, vals, step=True)
    stp = K.KlippyStepMcu(None, None, cfg, 9)
    stp._q_read = regs
    ts5, ds5 = stp.read_step()
    check("(1017-5) read_step: idx += n, alle 64",
          ts5 == ticks and ds5 == vals
          and [p[1] for p in regs.sent]
          == [sum(regs.ns[:i]) for i in range(len(regs.ns))])
    regs2 = _McuRegister(ticks, vals, step=True)
    stp._q_read = regs2
    st = stp.read_frozen()
    check("(1017-5) step read_frozen: [oid,0,0] zuerst, dann idx += n",
          regs2.sent[0] == [9, 0, 0] and st["step"][1] == vals)


def _creality_pc7_pull(cmds):
    """Endzustand des PC7-Pulls in der CREALITY-Firmware aus der Folge der
    MCU-Config-Befehle (MCU-Befund §4.5): wer zuletzt konfiguriert, gewinnt;
    config_endstop pull_up 0 = Pull-Down, 1 = Pull-Up, sonst schwebend;
    config_prtouch_step = immer Pull-Up."""
    pull = None
    for c in cmds:
        if c.startswith("config_endstop") and "pin=PC7" in c:
            v = int(c.split("pull_up=")[1].split()[0])
            pull = K.CREALITY_PULL.get(v, "schwebend")
        elif c.startswith("config_prtouch_step") and "swp_pin=PC7" in c:
            pull = "Pull-Up"
    return pull


def test_1017_pc7_pull():
    """Punkt 6: PC7-Pull bewusst Pull-Up (Stock-Endzustand) via '^!PC7'."""
    p = K.parse_pin_desc("^!PC7")
    check("(1017-6) parse_pin_desc ^!PC7 -> pullup 1, invert 1, mcu",
          p == {"pullup": 1, "invert": 1, "chip": "mcu", "pin": "PC7"})
    check("(1017-6) parse_pin_desc ~!PC7 -> pullup -1",
          K.parse_pin_desc("~!PC7")["pullup"] == -1)
    try:
        K.parse_pin_desc("!^PC7")
        ok = False
    except ValueError:
        ok = True
    check("(1017-6) '!^PC7' (falsche Reihenfolge, Upstream ungültig) abgelehnt", ok)
    cfg = K.parse_options({})
    check("(1017-6) Default step_swap_pin '^!PC7' = Pull-Up",
          cfg["step_swap_pin"] == "^!PC7" and cfg["step_swap_pull"] == "Pull-Up")
    check("(1017-6) '!PC7' (Creality: Pull-DOWN) im Probe-Modus abgelehnt",
          _raises({"step_swap_pin": "!PC7"}, "Pull-DOWN"))
    check("(1017-6) '~!PC7' (Creality: schwebend) abgelehnt",
          _raises({"step_swap_pin": "~!PC7"}, "schwebend"))
    check("(1017-6) '^PC7' ohne Invertierung abgelehnt",
          _raises({"step_swap_pin": "^PC7"}, "Invertierung"))
    check("(1017-6) '^!nozzle_mcu:PC7' (falsche MCU) abgelehnt",
          _raises({"step_swap_pin": "^!nozzle_mcu:PC7"}, "main_mcu"))
    check("(1017-6) mode steps: '!PC7' erlaubt (kein Endstop)",
          not _raises({"mode": "steps", "step_swap_pin": "!PC7"}))
    stock = ["config_endstop oid=1 pin=PC7 pull_up=0",
             "config_trsync oid=2",
             "config_prtouch_step oid=3 oid_xstp=4 oid_ystp=7 oid_zstp=9 "
             "swp_pin=PC7"]
    alt = [stock[2], "config_endstop oid=0 pin=PC7 pull_up=0"]
    neu = [stock[2], "config_endstop oid=0 pin=PC7 pull_up=1"]
    check("(1017-6) Creality-Modell: Stock-Folge -> Pull-Up",
          _creality_pc7_pull(stock) == "Pull-Up")
    check("(1017-6) Creality-Modell: alte k2-Folge mit !PC7 -> Pull-Down",
          _creality_pc7_pull(alt) == "Pull-Down")
    check("(1017-6) Creality-Modell: neue k2-Folge mit ^!PC7 -> Pull-Up "
          "(reihenfolgeunabhängig)",
          _creality_pc7_pull(neu) == "Pull-Up"
          and _creality_pc7_pull(neu[::-1]) == "Pull-Up")


def test_1017_upstream():
    """Integration gegen die echten Upstream-Dateien (sonst SKIP)."""
    if not os.path.isdir(os.path.join(UPSTREAM_KLIPPY, "extras")):
        print("SKIP (1017-upstream) lokale Upstream-Kopie fehlt")
        return
    # Config-Fehler beim Laden -> kein Start
    try:
        _integration_env({"pres_tri_hold": "4000, 4000, 500"})
        ok = False
    except ValueError as exc:
        ok = "Config-Fehler" in str(exc) and "Stream" in str(exc)
    check("(1017-upstream) min_hold == max_hold -> Config-Fehler beim Laden", ok)
    try:
        _integration_env({"step_swap_pin": "!PC7"})
        ok = False
    except ValueError as exc:
        ok = "Pull-DOWN" in str(exc)
    check("(1017-upstream) step_swap_pin '!PC7' -> Config-Fehler beim Laden", ok)
    # G28 Z: second_homing_speed 2 (< 2,5) -> 2. Antastung abgelehnt
    import collections
    env = _integration_env()
    hv = env["pins"].setup_pin("endstop", "probe:z_virtual_endstop")
    HI = collections.namedtuple(
        "homing_info", ["speed", "position_endstop", "retract_speed",
                        "retract_dist", "positive_dir", "second_homing_speed"])
    hinfo = HI(5.0, 0.0, 5.0, 5.0, False, 2.0)

    class Rail:
        def get_endstops(self):
            return [(hv, "probe")]

        def get_homing_info(self):
            return hinfo

    env["th"].pos = [175.0, 175.0, 120.0, 0.0]
    try:
        env["uhoming"].Homing(env["printer"]).home_rails(
            [Rail()], [None, None, 180.0, None], [None, None, 0.0, None])
        ok = False
    except env["ugcode"].CommandError as exc:
        ok = "zu langsam" in str(exc) and "second_homing_speed" in str(exc)
    core = env["obj"].probe.core
    check("(1017-upstream) G28 Z: second_homing_speed 2 -> 2. Fahrt abgelehnt",
          ok and len(env["rec"]["probing"]) == 1 and not core.armed,
          str(env["rec"]["probing"]))
    # KTWO_PRTOUCH_READ im Probe-Modus: Vorabprüfung ohne Bewegung
    env2 = _integration_env({"allow_move": "False"})
    env2["line"].log.clear()
    gc2 = env2["gcode"]
    gc2.cmds["KTWO_PRTOUCH_READ"](gc2.create_gcode_command(
        "KTWO_PRTOUCH_READ", "KTWO_PRTOUCH_READ", {}))
    core2 = env2["obj"].probe.core
    check("(1017-upstream) KTWO_PRTOUCH_READ: scharf -> Ruhetest -> entschärft, "
          "keine Fahrt (auch allow_move False)",
          any("READ ok" in m for m in gc2.out) and env2["rec"]["probing"] == []
          and "read_ftr" in env2["line"].log
          and env2["line"].log[0] == "wait"
          and env2["line"].log[-2:] == ["stop_step", "stop_pres1"]
          and not core2.armed and core2.arm_count == 1, str(gc2.out[-1:]))
    env3 = _integration_env()
    env3["line"].sensor_stuck = True
    gc3 = env3["gcode"]
    gc3.cmds["KTWO_PRTOUCH_READ"](gc3.create_gcode_command(
        "KTWO_PRTOUCH_READ", "KTWO_PRTOUCH_READ", {}))
    check("(1017-upstream) KTWO_PRTOUCH_READ bei abgezogenem Sensor -> Meldung, "
          "entschärft",
          any("Ruhetest" in m and "-1" in m for m in gc3.out)
          and not env3["obj"].probe.core.armed, str(gc3.out[-1:]))
    # PROBE bei abgezogenem Sensor: keine Fahrt
    env4 = _integration_env()
    env4["line"].sensor_stuck = True
    try:
        env4["gcode"].cmds["PROBE"](env4["gcode"].create_gcode_command(
            "PROBE", "PROBE", {}))
        ok = False
    except env4["ugcode"].CommandError as exc:
        ok = "Ruhetest" in str(exc)
    check("(1017-upstream) PROBE bei abgezogenem Sensor -> keine Fahrt",
          ok and env4["rec"]["probing"] == [])


def main():
    test_live_ring_read()
    test_swap_mechanism()
    test_live_packed_decode()
    test_a_contact_accuracy()
    test_b_step_limits()
    test_c_emergency()
    test_d_no_move_without_allow()
    test_e_swap_rest_abort_before_move()
    test_f_formatstrings()
    test_g_stop_within_one_step()
    test_extra_can_brk_retry()
    test_stock_mapping()
    test_probe_arm_disarm_order()
    test_probe_disarm_on_exception()
    test_probe_ack_error()
    test_probe_pc7_already_triggered()
    test_probe_allow_move_blocks()
    test_probe_speed_limit()
    test_probe_target_z()
    test_klippy_adapters_disarm_and_frozen()
    test_upstream_integration()
    test_upstream_integration_guards()
    test_1017_config_validation()
    test_1017_arm_settle()
    test_1017_min_speed()
    test_1017_rest_extended()
    test_1017_ring_blocks()
    test_1017_pc7_pull()
    test_1017_upstream()
    print("\n%d bestanden, %d fehlgeschlagen" % (len(PASS), len(FAIL)))
    if FAIL:
        print("FEHLER:", FAIL)
        sys.exit(1)
    print("ALLE TESTS BESTANDEN")


if __name__ == "__main__":
    main()


# --- Review 16.10.2026: harte Obergrenze für max_travel_mm ---
def _review_max_travel_cap():
    import k2_prtouch as _m
    try:
        _m.parse_options({"max_travel_mm": 50})
        print("FAIL (review) max_travel_mm=50 wurde akzeptiert")
        return False
    except ValueError:
        pass
    _m.parse_options({"max_travel_mm": 10})
    print("PASS (review) max_travel_mm > 10 wird abgelehnt, 10 erlaubt")
    return True


if __name__ == "__main__":
    import sys as _sys
    if not _review_max_travel_cap():
        _sys.exit(1)
