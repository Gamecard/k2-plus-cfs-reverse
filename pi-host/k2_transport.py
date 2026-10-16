#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2_transport.py – RS485- und Transparent-Transporte für K2-Plus-Motoren.

Ziel: Upstream-Klipper-Zusatzmodul [k2_motor] (offener Ersatz für Crealitys
motor_control_wrapper.so). Diese Datei kapselt die zwei Bus-Wege mit
GLEICHER Schnittstelle::

    transact(addr, fun, data, timeout=..., retries=0) -> (status, fun, data)

- ``Rs485Transport``: X/Y/Z/Z1 (0x81–0x84) über den RS485-Port (/dev/ttyS5,
  230400). Wrapper um eine nachbau/serial485-artige Bus-API (erwartet
  ``transact_full`` oder ``transact``). In Upstream-Klipper ist das ein
  eigener Python-Seriell-Port – HINWEIS: Der Port wird mit CFS (0x01) und
  Belt (0x21/0x22) geteilt; Zugriffe müssen über EINE gemeinsame Queue /
  EINEN Lock serialisiert werden (Halbduplex, vgl. nachbau/serial485.py).
- ``TransparentTransport``: Extruder-Motor (0x81 auf dem Düsenbus) über die
  Düsen-MCU (``config_transparent`` / ``transparent_send`` /
  ``transparent_response``). Baut den VOLLEN RS485-Frame (F7 addr len status
  fun data crc) mit ``serial485.build_frame``, sendet per ``transparent_send``
  (``timeout_ms``), parst ``transparent_response`` mit ``parse_frame``
  (CRC prüfen, Absenderadresse prüfen). ``retries=0`` wie Stock (genau eine
  Sendung, nie ein Retry – live ~2200 Motor-Sendungen alle „retries = 0“).

Grundlagen (nur gelesen, nichts gesendet):
- MOTOR-INIT-BEFUND-2026-10-16.md (§2 Ablauf, §4 Param-Abgleich, §5
  Strapping, §6 Zeiten, §7 Stock-Bug 0x85)
- GPIO-PHASE-BEFUND-2026-10-16.md (MCU-Befehle transparent_*, set_digital_out,
  query_gpio_status, config_digital_out, [duplicate_pin_override])
- nachbau/serial485.py (build_frame, parse_frame, CRC-8/ATM Poly 0x07)
- nachbau/motor.py (Func-Codes, Timeouts, Adressen)
- Live-Log live-logs-2026-10-15/klippy.log (Boot ab Zeile 2259; transparent-
  Format: Host sendet VOLLE Frames inkl. F7 und CRC, z. B. ``f7 ff 04 00 0e
  01 89``, ``f7 81 04 00 0e 02 80``, ``f7 81 04 00 11 02 14``,
  ``f7 81 04 00 0c 0b 95``; Antwort z. B. ``f7 81 04 00 0e 81 00``,
  ``f7 81 0b 00 0c 00×8 fb``)

Upstream-Klipper-API (ohne vorliegenden Quellbaum, gegen die bekannte API
geschrieben – ANNAHME, s. BERICHT.md):
- ``mcu.create_oid()``, ``mcu.add_config_cmd("config_transparent oid=%d")``,
  ``mcu.lookup_query_command("transparent_send oid=%c write=%*s
  timeout_ms=%u", "transparent_response oid=%c read=%*s", oid=...)``.
- Alle MCU-Zugriffe laufen über die kleine Adapterklasse
  ``TransparentMcuAdapter`` (Protokoll: ``exchange(write, timeout_ms)``),
  damit Tests mit einer Fake-MCU laufen (s. test_k2_motor.py).

Kein Netzwerk, kein Druckerzugriff. Reine Frame-Logik, ohne Hardware lauffähig.
"""

import importlib
import os
import sys

# --- nachbau/ finden und ANHÄNGEN (Fix 16.10.2026, Import-Falle) -------------
# Früher: sys.path.insert(0, <eigener Ordner>) + insert(0, nachbau/). Unter
# Klipper ist der eigene Ordner klippy/extras/ – vorne in sys.path verdeckt er
# Standardmodule (k2_prtouch: "import statistics" -> extras/statistics.py,
# statistics.median fehlt). Regeln jetzt:
#   1. Der Modulordner kommt NIE in sys.path. Geschwister (k2_transport usw.)
#      laden die Module per "from . import X" (Klipper-Paket extras), Fallback
#      "import X" nur für die Offline-Tests (pi-host/ ist dort sys.path[0]).
#   2. nachbau/ wird nur ANGEHÄNGT (sys.path.append): Namen der Standard-
#      bibliothek, von klippy/ und site-packages haben Vorrang, nachbau kann
#      nichts verdecken. Die nachbau-Dateien importieren sich gegenseitig
#      absolut ("import serial485"), deshalb muss der Ordner in sys.path.
#   3. import_nachbau()/check_nachbau_module() prüfen, dass ein Modul wirklich
#      aus nachbau/ kommt (z. B. kein pip-Paket "box"), sonst klare Meldung.
# Suchreihenfolge (erster Ordner mit serial485.py gewinnt):
#   $K2_NACHBAU_DIR                 (Override, z. B. für Tests)
#   <modulordner>/k2_nachbau/       INSTALLATIONSWEG Pi: klippy/extras/k2_nachbau/
#   <modulordner>/../nachbau/       Repo: pi-host/../nachbau (Offline-Tests)
#   <modulordner>/../../nachbau/    alter Weg: <klipper>/nachbau/ (Stand 16.10. früh)
NACHBAU_ENV = "K2_NACHBAU_DIR"
NACHBAU_SUBDIR = "k2_nachbau"
_HERE = os.path.dirname(os.path.abspath(__file__))


def nachbau_candidates(here=_HERE):
    """Kandidaten-Ordner für nachbau/ in Suchreihenfolge."""
    out = []
    env = os.environ.get(NACHBAU_ENV, "").strip()
    if env:
        out.append(env)
    out.append(os.path.join(here, NACHBAU_SUBDIR))
    out.append(os.path.join(os.path.dirname(here), "nachbau"))
    out.append(os.path.join(os.path.dirname(os.path.dirname(here)), "nachbau"))
    return out


def find_nachbau_dir(here=_HERE):
    """Erster Kandidat mit serial485.py (realpath) oder None."""
    for cand in nachbau_candidates(here):
        if os.path.isfile(os.path.join(cand, "serial485.py")):
            return os.path.realpath(cand)
    return None


NACHBAU_DIR = find_nachbau_dir()


def ensure_nachbau_path():
    """nachbau/ ANHÄNGEN (nie vorne einfügen). -> Ordner oder None."""
    if NACHBAU_DIR is None:
        return None
    known = set(os.path.realpath(p) for p in sys.path if p)
    if NACHBAU_DIR not in known:
        sys.path.append(NACHBAU_DIR)
    return NACHBAU_DIR


def check_nachbau_module(mod):
    """ImportError, wenn ``mod`` nicht aus NACHBAU_DIR stammt (Namenskollision)."""
    name = getattr(mod, "__name__", "?")
    path = getattr(mod, "__file__", None)
    where = os.path.dirname(os.path.realpath(path)) if path else None
    if NACHBAU_DIR is None or where != NACHBAU_DIR:
        raise ImportError(
            "Modul %r kommt aus %s statt aus nachbau/ (%s) – Namenskollision "
            "(z. B. pip-Paket gleichen Namens) oder nachbau/ fehlt; Installation "
            "s. config-pi/README.md §3" % (name, where, NACHBAU_DIR))
    return mod


def import_nachbau(name):
    """nachbau-Modul laden (Pfad anhängen, Herkunft prüfen)."""
    if ensure_nachbau_path() is None:
        raise ImportError(
            "nachbau/ nicht gefunden (gesucht: %s) – Installation s. "
            "config-pi/README.md §3" % ", ".join(nachbau_candidates()))
    return check_nachbau_module(importlib.import_module(name))


try:
    # Bevorzugt: geprüfte Implementierung aus dem Nachbau verwenden.
    _nb_s485 = import_nachbau("serial485")
    _nb_build_frame = _nb_s485.build_frame
    _nb_parse_frame = _nb_s485.parse_frame
    _nb_crc8 = _nb_s485.crc8_cal
    _HAVE_NACHBAU = True
except Exception:  # pragma: no cover – Fallback für Klipper ohne nachbau/
    _HAVE_NACHBAU = False

# --- Lokale Frame-Helfer (byte-identisch zu nachbau/serial485.py) ------------
# Dienen als Fallback, wenn nachbau/serial485.py nicht importierbar ist
# (Upstream-Klipper-Deployment: nur k2_transport.py + k2_motor_init.py
# kopieren). In Tests wird die Byte-Gleichheit gegen serial485 geprüft.
PACK_HEAD = 0xF7
STATUS_OK = 0x00
POLY = 0x07
BROADCAST_ADDR = 0xFF


def _crc8_cal(data):
    crc = 0
    for byte in bytes(data):
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ POLY) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def _build_frame_local(addr, fun, data=(), status=STATUS_OK):
    data = list(data)
    length = len(data) + 3
    body = [length, status, fun] + data
    return bytes([PACK_HEAD, addr] + body + [_crc8_cal(body)])


def _parse_frame_local(raw):
    raw = bytes(raw)
    if len(raw) < 6:
        raise ValueError("Frame zu kurz: %d Bytes" % len(raw))
    if raw[0] != PACK_HEAD:
        raise ValueError("head-Fehler: 0x%02x" % raw[0])
    length = raw[2]
    if not 3 <= length <= 255:
        raise ValueError("Längenfeld unplausibel: %d" % length)
    expect = length + 3
    if len(raw) < expect:
        raise ValueError("unvollständig: %d < %d" % (len(raw), expect))
    body = list(raw[2:expect - 1])
    if _crc8_cal(body) != raw[expect - 1]:
        raise ValueError("CRC-Fehler")
    return raw[4], bytes(raw[5:expect - 1])


if _HAVE_NACHBAU:
    build_frame = _nb_build_frame
    parse_frame = _nb_parse_frame
    crc8_cal = _nb_crc8
else:  # pragma: no cover
    build_frame = _build_frame_local
    parse_frame = _parse_frame_local
    crc8_cal = _crc8_cal


def _check_addr_fun(addr, fun):
    if isinstance(addr, bool) or not isinstance(addr, int) or not 0 <= addr <= 0xFF:
        raise ValueError("addr muss int 0..255 sein: %r" % (addr,))
    if isinstance(fun, bool) or not isinstance(fun, int) or not 0 <= fun <= 0xFF:
        raise ValueError("fun muss int 0..255 sein: %r" % (fun,))


# =====================================================================
# Adapter-Protokoll für die Düsen-MCU
# =====================================================================

class TransparentMcuAdapter:
    """Protokoll für den transparent-Kanal (Düsen-MCU).

    Reale Implementierung (Upstream-Klipper, ANNAHME – gegen die bekannte
    API geschrieben, ohne vorliegenden Quellbaum)::

        oid = mcu.create_oid()
        mcu.add_config_cmd("config_transparent oid=%d" % oid)
        query = mcu.lookup_query_command(
            "transparent_send oid=%c write=%*s timeout_ms=%u",
            "transparent_response oid=%c read=%*s", oid=oid)
        read = query.send([oid, bytes(write), int(timeout_ms)])["read"]

    Tests nutzen eine Fake-Implementierung mit derselben Methode
    ``exchange(write, timeout_ms) -> read`` (s. test_k2_motor.py: FakeMcu).
    """

    def exchange(self, write, timeout_ms):
        """Vollen Frame senden, Antwort-Bytes empfangen (b'' = leer/Timeout)."""
        raise NotImplementedError


class KlippyTransparentAdapter(TransparentMcuAdapter):
    """Echte Anbindung an die Klipper-MCU (Upstream-API, ANNAHME).

    Lebenszyklus nach REVIEW 1 (wie Stock ``_build_config`` @0xa04fc + L3089):
    - VOR ``klippy:connect``: ``oid = mcu.create_oid()`` +
      ``mcu.register_config_callback(cb)``, im Callback
      ``mcu.add_config_cmd("config_transparent oid=%d" % oid)`` (s.
      ``PrinterK2Motor._build_transparent_config`` in k2_motor_init.py).
    - NACH ``klippy:connect``: ``setup()`` löst den Query-Befehl auf.
    Bis dahin wirft ``exchange`` einen RuntimeError – der Init-Lauf startet
    deshalb erst nach dem Connect.
    """

    def __init__(self, mcu, oid):
        self.mcu = mcu
        self.oid = oid
        self._query = None

    def setup(self):
        """Query-Befehl nach dem Connect auflösen (ANNAHME: Signatur wie Stock)."""
        try:
            self._query = self.mcu.lookup_query_command(
                "transparent_send oid=%c write=%*s timeout_ms=%u",
                "transparent_response oid=%c read=%*s", oid=self.oid)
        except Exception as exc:
            raise RuntimeError("transparent_send-Lookup fehlgeschlagen: %s" % exc)

    def exchange(self, write, timeout_ms):
        if self._query is None:
            raise RuntimeError("transparent-Kanal nicht eingerichtet (setup() fehlt)")
        res = self._query.send([self.oid, bytes(write), int(timeout_ms)])
        read = res.get("read", b"") if isinstance(res, dict) else b""
        return bytes(read) if read is not None else b""


# =====================================================================
# Rs485Transport – X/Y/Z/Z1 über den RS485-Port
# =====================================================================

class Rs485Transport:
    """Transport für X/Y/Z/Z1 (0x81–0x84) über RS485.

    ``bus`` ist eine nachbau/serial485-artige Bus-API. Bevorzugt wird
    ``bus.transact_full(addr, fun, data, timeout, retries, ...)`` genutzt
    (liefert ``(addr, status, fun, daten, roh)`` inkl. STATUS-Byte); falls
    nur ``bus.transact`` existiert (liefert nur Daten), wird ``status=0x00``
    angenommen (ANNAHME, im Test beides abgedeckt).

    HINWEIS Upstream-Klipper: Der RS485-Port (/dev/ttyS5, 230400, 8N1) wird
    mit CFS (0x01) und Belt (0x21/0x22) GETEILT. Es darf genau EIN offener
    Port mit EINER Sende-Queue existieren (Halbduplex-Lock über die ganze
    Transaktion, vgl. serial485.Serial485._lock). Dieses Modul öffnet den
    Port NICHT selbst, sondern bekommt ``bus`` injiziert – die Port-Verwaltung
    (open/close/queue) bleibt beim Host bzw. einem zentralen 485-Manager.
    """

    BUS_NAME = "ttyS5"

    def __init__(self, bus, name="ttyS5"):
        if bus is None:
            raise ValueError("Rs485Transport braucht ein bus-Objekt")
        self.bus = bus
        self.name = name
        self.sent = []  # [(addr, fun, tuple(data))] – Diagnose, kein Bus-Zugriff

    def transact(self, addr, fun, data, timeout=1.0, retries=0):
        """``(status, fun, data)`` – TimeoutError bei ausbleibender Antwort.

        ``retries``: Stock nutzt IMMER 0 (genau eine Sendung). Der Parameter
        bleibt für Schnittstellen-Gleichheit erhalten und wird an den Bus
        durchgereicht; Aufrufer übergeben 0 (wie Stock).
        """
        _check_addr_fun(addr, fun)
        payload = list(bytes(data) if isinstance(data, (bytes, bytearray)) else data)
        self.sent.append((addr, fun, tuple(payload)))
        full = getattr(self.bus, "transact_full", None)
        if full is not None:
            raddr, rstatus, rfun, rdata, _raw = full(
                addr, fun, payload, timeout=timeout, retries=retries)
            if raddr != addr and addr != BROADCAST_ADDR:
                raise ValueError("Absender 0x%02X statt 0x%02X" % (raddr, addr))
            if rfun != fun:
                raise ValueError("fun-Mismatch: 0x%02X statt 0x%02X" % (rfun, fun))
            return int(rstatus), int(rfun), bytes(rdata)
        # Fallback: bus.transact liefert nur Daten (STATUS unbekannt → 0x00).
        rdata = self.bus.transact(addr, fun, payload, timeout=timeout, retries=retries)
        return STATUS_OK, int(fun), bytes(rdata)


# =====================================================================
# TransparentTransport – Extruder über die Düsen-MCU
# =====================================================================

class TransparentTransport:
    """Transport für den Extruder-Motor (0x81 auf dem Düsenbus).

    Ablauf je Transaktion (ANNAHME zur Upstream-API, belegt durch Stock-Log):
    1. Vollen Frame bauen: ``build_frame(addr, fun, data, status=0x00)``
       = ``[F7][addr][len][status][fun][data][crc]`` (CRC über len..data).
    2. Per ``transparent_send oid write timeout_ms`` senden (``timeout_ms``
       = ``int(timeout * 1000)`` wie Stock ``TIMEOUT_LONG_TIME_MS`` ≈ 2 s
       für den Broadcast, sonst je Func).
    3. ``transparent_response`` lesen: ``b''`` = leer → TimeoutError (Stock:
       Broadcast und ``0C [05]`` bleiben unbeantwortet/leer – der Aufrufer
       wertet das als „erwartet“ aus).
    4. Vollframe mit ``parse_frame`` prüfen (CRC), Absenderadresse prüfen
       (Antwort-Absender == Anfrage-Adresse; Broadcast 0xFF akzeptiert
       beliebig, kommt live aber nie mit Antwort), fun prüfen, STATUS-Byte
       (Byte 3) zurückgeben.

    ``retries``: Stock nutzt 0 (Konstante in ``motor_read485_addr`` 0x615a0
    bzw. ``TIMEOUT_LONG_TIME_MS`` für transparent). Der Parameter wird
    ausgeführt (``retries+1`` Versuche), Aufrufer übergeben 0 wie Stock.
    """

    BUS_NAME = "transparent"

    def __init__(self, mcu_adapter, oid=1, name="transparent"):
        if mcu_adapter is None or not hasattr(mcu_adapter, "exchange"):
            raise ValueError("TransparentTransport braucht mcu_adapter.exchange()")
        self.adapter = mcu_adapter
        self.oid = oid
        self.name = name
        self.sent = []  # [(addr, fun, tuple(data))] – Diagnose

    def transact(self, addr, fun, data, timeout=2.0, retries=0):
        _check_addr_fun(addr, fun)
        payload = list(bytes(data) if isinstance(data, (bytes, bytearray)) else data)
        self.sent.append((addr, fun, tuple(payload)))
        if timeout is None or timeout < 0:
            raise ValueError("timeout muss >= 0 sein: %r" % (timeout,))
        if retries is None or retries < 0:
            raise ValueError("retries muss >= 0 sein: %r" % (retries,))
        frame = bytes(build_frame(addr, fun, payload, status=STATUS_OK))
        timeout_ms = int(round(float(timeout) * 1000))
        last_err = None
        for _ in range(int(retries) + 1):
            read = bytes(self.adapter.exchange(frame, timeout_ms) or b"")
            if not read:
                last_err = TimeoutError(
                    "transparent 0x%02X fun 0x%02X: keine Antwort (leer)" % (addr, fun))
                continue
            try:
                rfun, rdata = parse_frame(read)
            except ValueError as exc:
                last_err = exc
                continue
            if len(read) < 6 or read[0] != PACK_HEAD:
                last_err = ValueError("transparent: head-Fehler")
                continue
            raddr = read[1]
            rstatus = read[3]
            if addr != BROADCAST_ADDR and raddr != addr:
                last_err = ValueError(
                    "transparent: Absender 0x%02X statt 0x%02X" % (raddr, addr))
                continue
            if rfun != fun:
                last_err = ValueError(
                    "transparent: fun-Mismatch 0x%02X statt 0x%02X" % (rfun, fun))
                continue
            return int(rstatus), int(rfun), bytes(rdata)
        if isinstance(last_err, TimeoutError):
            raise last_err
        if last_err is not None:
            raise TimeoutError(
                "transparent 0x%02X fun 0x%02X: %s" % (addr, fun, last_err))
        raise TimeoutError("transparent 0x%02X fun 0x%02X: keine Antwort" % (addr, fun))
