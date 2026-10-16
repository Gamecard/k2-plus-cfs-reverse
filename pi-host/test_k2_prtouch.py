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
            "max_travel_mm": 5.0}
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


def main():
    test_a_contact_accuracy()
    test_b_step_limits()
    test_c_emergency()
    test_d_no_move_without_allow()
    test_e_swap_rest_abort_before_move()
    test_f_formatstrings()
    test_g_stop_within_one_step()
    test_extra_can_brk_retry()
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
