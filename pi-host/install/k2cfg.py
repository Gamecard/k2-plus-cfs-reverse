#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2cfg.py – Hilfswerkzeug für install.sh (läuft auf dem Pi, nur Standardbibliothek).

Unterbefehle:

  merge NEU AUS [--alt ALT] [--set ABSCHNITT=WERT ...] [--save-config-aus-paket]
      Schreibt NEU (Paket-Datei) nach AUS und übernimmt dabei
        * die ``serial:``-Zeilen der Abschnitte [mcu], [mcu nozzle_mcu], [k2_485]:
          Vorrang hat --set, sonst der Wert aus ALT (bereits installierte Datei),
          sofern er kein Platzhalter (``ANNAHME``) ist;
        * die Zeile ``material_database:`` im Abschnitt [k2_box] (Pfad der
          Creality-Materialdatenbank): steht sie in NEU und ALT aktiv und
          weicht ALT ab (von Hand angepasst), bleibt der Wert aus ALT;
          eine in ALT auskommentierte/fehlende Zeile übernimmt den Paketwert;
        * den SAVE_CONFIG-Block aus ALT (Kalibrierwerte), wenn ALT eine
          K2-Konfiguration ist und der Block abweicht – außer mit
          --save-config-aus-paket.
      Jede Übernahme wird auf stdout gemeldet.

  platzhalter DATEI ...
      Listet Abschnitte, deren ``serial:`` noch ein Platzhalter ist.
      Exit 0 = keine Platzhalter, 3 = Platzhalter vorhanden.
"""

import os
import re
import sys

SERIAL_SECTIONS = ("mcu", "mcu nozzle_mcu", "k2_485")
SAVE_MARKER = "#*# <---------------------- SAVE_CONFIG ---------------------->"
PLACEHOLDER = "ANNAHME"

_SEC_RE = re.compile(r"^\[([^\]]+)\]\s*$")
_SERIAL_RE = re.compile(r"^(serial\s*[:=]\s*)(\S.*?)\s*$")
# Weitere Optionen, deren Wert bei Wiederholung aus ALT erhalten bleibt
KEEP_OPTIONS = (("k2_box", "material_database"),)


def _opt_re(key):
    return re.compile(r"^(" + re.escape(key) + r"\s*[:=]\s*)(\S.*?)\s*$")


def options(text, wanted=KEEP_OPTIONS):
    """{(abschnitt, schlüssel): wert} der ersten aktiven Zeile (nur Hauptteil)."""
    body, _ = split_save_config(text)
    res = {}
    sec = None
    for line in body.splitlines():
        m = _SEC_RE.match(line)
        if m:
            sec = m.group(1).strip()
            continue
        for wsec, key in wanted:
            if sec == wsec and (wsec, key) not in res:
                m = _opt_re(key).match(line)
                if m:
                    res[(wsec, key)] = m.group(2)
    return res


def set_options(text, values):
    """Ersetzt je (abschnitt, schlüssel) die erste aktive Zeile; (text, geändert)."""
    body, save = split_save_config(text)
    out = []
    sec = None
    done = set()
    changed = []
    for line in body.splitlines(True):
        m = _SEC_RE.match(line.rstrip("\n"))
        if m:
            sec = m.group(1).strip()
        else:
            for (wsec, key), new in values.items():
                if sec != wsec or (wsec, key) in done:
                    continue
                m2 = _opt_re(key).match(line.rstrip("\n"))
                if m2:
                    done.add((wsec, key))
                    if m2.group(2) != new:
                        nl = "\n" if line.endswith("\n") else ""
                        line = m2.group(1) + new + nl
                        changed.append((wsec, key))
                    break
        out.append(line)
    return "".join(out) + save, changed


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def split_save_config(text):
    """(Hauptteil, SAVE_CONFIG-Block oder '')."""
    idx = text.find(SAVE_MARKER)
    if idx < 0:
        return text, ""
    return text[:idx], text[idx:]


def serials(text):
    """{abschnitt: wert} der aktiven serial-Zeilen (nur Hauptteil)."""
    body, _ = split_save_config(text)
    res = {}
    sec = None
    for line in body.splitlines():
        m = _SEC_RE.match(line)
        if m:
            sec = m.group(1).strip()
            continue
        if sec in SERIAL_SECTIONS:
            m = _SERIAL_RE.match(line)
            if m and sec not in res:
                res[sec] = m.group(2)
    return res


def set_serials(text, values):
    """Ersetzt die erste serial-Zeile je Abschnitt; gibt (text, geändert) zurück."""
    body, save = split_save_config(text)
    out = []
    sec = None
    done = set()
    changed = []
    for line in body.splitlines(True):
        m = _SEC_RE.match(line.rstrip("\n"))
        if m:
            sec = m.group(1).strip()
        elif sec in values and sec not in done:
            m2 = _SERIAL_RE.match(line.rstrip("\n"))
            if m2:
                done.add(sec)
                new = values[sec]
                if m2.group(2) != new:
                    nl = "\n" if line.endswith("\n") else ""
                    line = m2.group(1) + new + nl
                    changed.append(sec)
        out.append(line)
    return "".join(out) + save, changed


def is_k2_config(text):
    return "[k2_prtouch]" in text or "[k2_485]" in text


def cmd_merge(args):
    if len(args) < 2:
        raise SystemExit("merge: NEU AUS fehlen")
    neu, aus = args[0], args[1]
    alt = None
    sets = {}
    save_aus_paket = False
    i = 2
    while i < len(args):
        a = args[i]
        if a == "--alt":
            alt = args[i + 1]
            i += 2
        elif a == "--set":
            k, sep, v = args[i + 1].partition("=")
            if not sep or k not in SERIAL_SECTIONS or not v.strip():
                raise SystemExit("merge: --set ABSCHNITT=WERT ungültig: %r" % args[i + 1])
            sets[k] = v.strip()
            i += 2
        elif a == "--save-config-aus-paket":
            save_aus_paket = True
            i += 1
        else:
            raise SystemExit("merge: unbekannte Option %r" % a)

    text = _read(neu)
    alt_text = _read(alt) if alt and os.path.isfile(alt) else None
    have = serials(text)

    values = {}
    for sec in have:
        if sec in sets:
            values[sec] = sets[sec]
            src = "Option"
        elif alt_text is not None:
            old = serials(alt_text).get(sec)
            if old and PLACEHOLDER not in old:
                values[sec] = old
                src = "bisherige Datei"
            else:
                continue
        else:
            continue
        print("  serial [%s] <- %s (%s)" % (sec, values[sec], src))
    text, _changed = set_serials(text, values)

    if alt_text is not None:
        neu_opts = options(text)
        keep = {}
        for k, old in options(alt_text).items():
            if k in neu_opts and old != neu_opts[k] and PLACEHOLDER not in old:
                keep[k] = old
                print("  [%s] %s <- %s (bisherige Datei, Paket: %s)"
                      % (k[0], k[1], old, neu_opts[k]))
        text, _changed = set_options(text, keep)

    if alt_text is not None and is_k2_config(alt_text):
        _b, save_alt = split_save_config(alt_text)
        body, save_neu = split_save_config(text)
        if save_alt and save_alt != save_neu:
            if save_aus_paket:
                print("  SAVE_CONFIG: Block aus dem PAKET übernommen "
                      "(bisherige Kalibrierwerte verworfen, steht im Backup)")
            else:
                text = body + save_alt
                print("  SAVE_CONFIG: bisherigen Block (Kalibrierwerte) BEHALTEN")

    tmp = aus + ".k2tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, aus)
    return 0


def cmd_platzhalter(args):
    found = False
    for path in args:
        if not os.path.isfile(path):
            continue
        for sec, val in serials(_read(path)).items():
            if PLACEHOLDER in val:
                found = True
                print("  %s  [%s]  serial: %s" % (os.path.basename(path), sec, val))
    return 3 if found else 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd, args = argv[1], argv[2:]
    if cmd == "merge":
        return cmd_merge(args)
    if cmd == "platzhalter":
        return cmd_platzhalter(args)
    print("unbekannter Unterbefehl: %s" % cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
