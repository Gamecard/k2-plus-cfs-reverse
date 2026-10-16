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


def test_live_ring_read():
    cfg = K.parse_options({})
    q = _RingQuery(LIVE_D1)
    pres = K.KlippyPresMcu(None, None, cfg, 0, {})
    pres._q_read = q
    t, d = pres.read_pres()
    check("(ring) 8 Abfragen idx 0..56, len 8, is_src 1",
          [p[3] for p in q.sent] == list(range(0, 64, 8))
          and all(p[1] == 1 and p[4] == 8 for p in q.sent))
    check("(ring) %d Samples zusammengeführt (<= 64)" % len(d), 56 <= len(d) <= 64)
    check("(ring) Ticks streng steigend", all(b > a for a, b in zip(t, t[1:])))
    check("(ring) Rohwerte im Ruhebereich", all(-1430000 < v < -1426000 for v in d))
    ok, info = K.check_rest_ok(d, cfg)
    check("(ring) Ruhetest ok (dif=%d)" % info["dif"], ok)
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
    cfg3 = K.parse_options({"lmt_dead": "77", "pres_cfg_regs": "61"})
    check("(map) direkte MCU-Option übersteuert",
          cfg3["lmt_dead"] == 77 and cfg3["cfg_regs"] == 61
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

    def read_pres(self):
        self.line.log.append("read_pres")
        base = -1428000
        d = [base + ((5000 if i % 2 else -5000) if self.line.rest_noisy
                     else det_noise(i, 300)) for i in range(64)]
        return list(range(64)), d

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
    base = {"allow_move": True, "arm_settle_s": 0.0}
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
    return core, line, desc, waits


EXPECT_ORDER = ["swap0", "query", "swap1", "query", "cont", "start_pres",
                "start_step", "read_pres", "query", "DESCEND", "stop_step",
                "stop_pres1", "query"]


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
                    t, v = pres.read_pres()
                    if d[3] != 0:  # 8 Blockabfragen = 1 Ruhetest im Log
                        line.log.pop()
                    return {"ticks": _RingQuery._pack(t[:8]),
                            "datas": _RingQuery._pack(v[d[3]:d[3] + 8])}
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
    opts = {"allow_move": "True", "arm_settle_s": "0"}
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
    check("(upstream) Endstop auf !PC7 (mcu, invert=1, pullup=0)",
          rec["endstop_pin"]["pin"] == "PC7"
          and rec["endstop_pin"]["invert"] == 1
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
