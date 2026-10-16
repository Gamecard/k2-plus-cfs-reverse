#!/usr/bin/env python3
"""serial485.py – Reiner-Python-Nachbau des Creality-RS485-Bustreibers (K2 Plus).

Ersetzt: serial_485_wrapper.cpython-39.so (armhf, CPython 3.9, proprietär).

Belege (alle in <projekt>/ dokumentiert):
- Frame-Format + Func-Codes: PROTOKOLL-485.md, PROTOKOLL-GESAMT.md
- CRC-Algorithmus: auto_addr_wrapper.py (Quelle, POLY=0x07)
  (Klartextquelle auto_addr_wrapper.py, crc8_cal/cal_pack_crc)
- Retry/Timeout-Verhalten: klippy.log-Mitschnitte (retries=4, Timeouts je Command)
- Empfangs-Validierung: belt_mdl.py recv_sensor_data (head/addr/len/fun-Prüfung)

Abhängigkeit für echte Hardware: pyserial (pip install pyserial).
Der Selbsttest unten läuft OHNE Hardware und OHNE pyserial.
"""

# --- Protokoll-Konstanten (Beleg: auto_addr_wrapper.py + Live-Frames) ---
PACK_HEAD = 0xF7
STATUS_OK = 0x00
STATUS_OPERATIONAL = 0xFF
POLY = 0x07

# Timeouts je Command (Beleg: auto_addr_wrapper.py cmd_timeout)
TIMEOUT_SHORT = 0.05    # SET_SLAVE_ADDR, GET_ADDR_TABLE, LOADER_TO_APP
TIMEOUT_MEDIUM = 0.10   # ONLINE_CHECK
TIMEOUT_LONG = 1.0      # Addressing-/Belt-Kommandos
TIMEOUT_OPERATIONAL = 2.0  # CFS-Operational-Kommandos (Live-Introspektion/Referenz)

# Auto-Addr-Commands (Beleg: auto_addr_wrapper.py)
CMD_GET_SLAVE_INFO = 0xA1
CMD_SET_SLAVE_ADDR = 0xA0
CMD_ONLINE_CHECK = 0xA2
CMD_GET_ADDR_TABLE = 0xA3
CMD_LOADER_TO_APP = 0x0B

# Broadcast-Adressen (Beleg: auto_addr_wrapper.py)
BROADCAST_MB = 0xFE      # CFS-Box
BROADCAST_CLM = 0xFD     # Closed-loop-Motoren
BROADCAST_BTM = 0xFC     # Belt-Module
BROADCAST_ALL = 0xFF     # Loader-Check (auto_addr.py BROADCAST_ADDR, 0x0B)
# 0xEA/0xEB: CFS-App-Sonderadressen (Ghidra, PROTOKOLL-485.md:321-328):
# ~~0xEA = Broadcast ohne Antwort~~ KORRIGIERT BEFUND2 §6: 0xEA wird bei
# normalen Kommandos TROTZDEM beantwortet (Absender = eigene Adresse; nur
# CRC-Fehler-/unbekannt-fun-/0x50-Antwort entfallen); 0xEB nur für 0x50
# (Antwort mit Absender 0xEB) / 0x51 (Debug, keine Antwort). 0xFE/0xFF nimmt
# die App nur ohne eigene Adresse an. Für Abfragen bei mehreren Boxen
# ungeeignet (Kollision) – Absenderprüfung bleibt hier "beliebig".
ADDR_EA = 0xEA
ADDR_EB = 0xEB
# Ziele, bei denen der Absender der Antwort NICHT gleich dem Ziel sein muss
# (Review M1, 16.10.2026): Broadcasts antworten mit Geräte- ODER Broadcast-
# Adresse (live: a1 an 0xFE -> Antwort von 0xFE, klippy.log.2026-10-12:6922;
# a2 an 0x01 -> Antwort von 0x01). Für alle anderen Ziele gilt: Antwort-
# Absender == Ziel (live: 0x01 und 0x81..0x84 antworten nur mit eigener
# Adresse). Ohne diese Prüfung würde z.B. ein Box-Frame (0x01, fun 0x0e) als
# Antwort für Motor 0x81 (gleicher fun auf ttyS5) akzeptiert.
BROADCAST_ADDRS = frozenset((BROADCAST_MB, BROADCAST_CLM, BROADCAST_BTM,
                             BROADCAST_ALL, ADDR_EA, ADDR_EB))
_AUTO = object()  # Sentinel: expect_addr aus Zieladresse ableiten


def reply_addr_for(addr):
    """Erwarteter Absender der Antwort auf einen Request an addr:
    addr selbst, bei BROADCAST_ADDRS None (= beliebig)."""
    return None if addr in BROADCAST_ADDRS else addr

MAX_RETRIES = 4          # Beleg: klippy.log "retries = 4"

# Obergrenze, wie lange EINE Transaktion den Bus-Lock halten darf
# (timeout * (retries + 1)), Review M6 16.10.2026. Längster belegter Fall:
# 0x0d op 2/3 Maske 0x0F = 980 s (box.preload_force_timeout, retries=0).
# Darüber -> ValueError statt stundenlanger Bus-Blockade (z.B. 300 s x 5).
MAX_BUS_HOLD_S = 1200.0
# Ab dieser Haltedauer wird der lange Lock im Log vermerkt (INFO).
LONG_HOLD_LOG_S = 5.0

# Serial_485_Wrapper._done-Begrenzung (Fix Teil B.1, 16.10.2026): nie abgeholte
# Antworten ließen _done unbegrenzt wachsen (Speicherleck bei langlebigen
# Sessions). MAX = harte Obergrenze (älteste IDs fliegen zuerst raus),
# TTL = Verfallszeit für verwaiste Einträge (Threads die nie abholen).
WRAPPER_DONE_MAX = 128
WRAPPER_DONE_TTL_S = 60.0
# Serial485 Bg-Race-Fix (Teil B.2, 16.10.2026): raw_send registriert eine
# erwartete Antwort, die der Hintergrund-Thread NICHT dispatcht, sondern in
# _resp_queue parkt; get_response holt dort zuerst ab. So kann der bg-Thread
# keine get_response-Antwort mehr in der Lücke zwischen raw_send und
# get_response wegschnappen. TTLs gegen Verwaisten bei Crashs.
BG_EXPECT_TTL_S = 2.0
RESP_QUEUE_TTL_S = 5.0
RESP_QUEUE_MAX = 32

# Plausibilitätsgrenzen für das Längenfeld (len = status+fun+daten... inkl. crc,
# vgl. build_frame: len = len(data)+3). Minimum 3 = Frame ohne Daten.
# Ein kleineres Maximum als das Byte-Maximum 255 ist nicht belegt (weder
# Wrapper-Quellen noch Mitschnitte) -> 255.
FRAME_LEN_MIN = 3
FRAME_LEN_MAX = 255

import logging
import threading
import time
from collections import deque

_log = logging.getLogger(__name__)


def crc8_cal(data):
    """CRC-8/ATM (MSB-first, poly 0x07, init 0x00, kein xorout).

    1:1-Port von auto_addr_wrapper.py::crc8_cal. An 10+ Live-Frames verifiziert.
    """
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ POLY) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def build_frame(addr, fun, data=(), status=STATUS_OK):
    """Baut einen kompletten Sende-Frame: [F7][addr][len][status][fun][data][crc].

    len = len(data) + 3 (Beleg: auto_addr_wrapper.py::data_pack).
    CRC über [len, status, fun] + data (Beleg: cal_pack_crc).
    """
    data = list(data)
    length = len(data) + 3
    body = [length, status, fun] + data
    return bytes([PACK_HEAD, addr] + body + [crc8_cal(body)])


def parse_frame(raw):
    """Prüft einen Empfangs-Frame, gibt (fun, daten) zurück (vgl. belt recv_sensor_data).

    Fehler -> ValueError mit Grund (head/länge/crc/fun).
    Erwartet mindestens [F7][addr][len][status][fun][crc].
    """
    if len(raw) < 6:
        raise ValueError("Frame zu kurz: %d Bytes" % len(raw))
    if raw[0] != PACK_HEAD:
        raise ValueError("head-Fehler: 0x%02x" % raw[0])
    length, status, fun = raw[2], raw[3], raw[4]
    if not FRAME_LEN_MIN <= length <= FRAME_LEN_MAX:
        raise ValueError("Längenfeld unplausibel: %d" % length)
    expect = length + 3  # head+addr+len (3) + length-Inhalt
    if len(raw) < expect:
        raise ValueError("unvollständig: %d < %d" % (len(raw), expect))
    body = list(raw[2:expect - 1])
    if crc8_cal(body) != raw[expect - 1]:
        raise ValueError("CRC-Fehler")
    return fun, bytes(raw[5:expect - 1])


def _scan_frame(buf):
    """Prüft den Pufferanfang auf EINEN Frame (Empfangs-Zustandsautomat).

    Gibt zurück:
      ("need", None)  – Frame-Anfang plausibel, aber noch unvollständig
                        -> weiterlesen (fragmentierte/lange Frames).
      ("bad", err)    – falscher Head, unplausibles Längenfeld oder
                        CRC-Fehler -> resynchronisieren.
      ("ok", (expect, addr, status, fun, daten)) – gültiger Frame der
                        Länge expect am Pufferanfang.
    """
    if not buf:
        return "need", None
    if buf[0] != PACK_HEAD:
        return "bad", ValueError("head-Fehler: 0x%02x" % buf[0])
    if len(buf) < 3:
        return "need", None
    length = buf[2]
    if not FRAME_LEN_MIN <= length <= FRAME_LEN_MAX:
        return "bad", ValueError("Längenfeld unplausibel: %d" % length)
    expect = length + 3
    if len(buf) < expect:
        return "need", None
    raw = bytes(buf[:expect])
    try:
        fun, data = parse_frame(raw)
    except ValueError as e:
        return "bad", e
    return "ok", (expect, raw[1], raw[3], fun, data)


def _resync(buf):
    """Verwirft Bytes bis zum nächsten PACK_HEAD (vgl. Review: Resync).

    Verhindert, dass Müll oder halbe Frames den Empfangspuffer bis zum
    Timeout verstopfen. Gibt den Restpuffer ab dem nächsten Head zurück.
    """
    for i in range(1, len(buf)):
        if buf[i] == PACK_HEAD:
            return buf[i:]
    return bytearray()


class Serial485:
    """RS485-Bus über pyserial. Entspricht Serialhdl_485/raw_send_wait_ack.

    Session-Schicht nach serial_485_wrapper.so-Symbolen (Beleg: strings-Dump
    vom 13.10.2026, vgl. PROTOKOLL-485.md Abschnitt 2):
    raw_send/raw_send_wait_ack/connect_uart/_start_session/_bg_thread/
    get_response/register_response/handle_default/disconnect/stats.

    Halbduplex: genau ein Frame gleichzeitig auf dem Bus, alles unter
    self._lock (threading.Lock, Typ unverändert für Alttests).
    Statistik: sent/received/timeouts/retries/crc_errors (vgl.
    serial_485_queue_get_stats im .so).
    """

    def __init__(self, port, baud=230400, timeout=TIMEOUT_LONG, ser=None):
        self.port = port
        self.baud = baud
        self.default_timeout = timeout
        self._lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._stats = {"sent": 0, "received": 0, "timeouts": 0,
                       "retries": 0, "crc_errors": 0}
        self._handlers = {}          # (addr, fun) -> callback; None = Wildcard
        self._default_handler = None
        self._connected = False
        self._session_active = False
        self._injected = ser is not None
        self._bg_stop = threading.Event()
        self._bg_thread_obj = None
        self._rest_buf = b""
        # Fix B.2: Antwort-Warteschlange + Erwartungs-Registry (alles unter
        # _lock verwaltet – kein extra Lock nötig, alle Zugriffe halten _lock).
        self._resp_queue = deque()      # [(addr,status,fun,data,raw,ts)]
        self._pending_expect = deque()  # [(addr|None, fun|None, deadline)]
        if ser is not None:
            self.ser = ser            # injiziert (Tests), kein pyserial nötig
            self._connected = True
        else:
            try:
                import serial
            except ImportError as e:
                raise SystemExit("pyserial fehlt: pip install pyserial") from e
            self.ser = serial.Serial(port, baud, timeout=0.02)
            self._connected = True

    # --- interne Helfer ---
    def _inc(self, key, n=1):
        with self._stats_lock:
            self._stats[key] = self._stats.get(key, 0) + n

    def _dispatch(self, addr, status, fun, data):
        """Ruft registrierten Handler für (addr, fun), sonst Default."""
        cb = self._handlers.get((addr, fun))
        if cb is None:
            cb = self._handlers.get((addr, None))
        if cb is None:
            cb = self._handlers.get((None, fun))
        if cb is None:
            cb = self._default_handler
        if cb is None:
            return None
        return cb(addr, status, fun, data)

    def _dispatch_safe(self, addr, status, fun, data):
        """_dispatch ohne Exception-Propagation (Handler-Fehler werden
        geloggt, der Busverkehr läuft weiter). Aufruf NUR ohne gehaltenes
        _lock (Handler dürfen selbst Bus-Funktionen aufrufen)."""
        try:
            self._dispatch(addr, status, fun, data)
        except Exception:
            _log.exception("Bus-Handler für addr 0x%02x fun 0x%02x fehlgeschlagen",
                           addr, fun)

    # --- Fix B.2-Helfer (alles NUR unter gehaltenem _lock aufrufen) --------
    @staticmethod
    def _match_expect(exp_addr, exp_fun, addr, fun):
        if exp_fun is not None and fun != exp_fun:
            return False
        if exp_addr is not None and addr != exp_addr:
            return False
        return True

    def _prune_expect_queue_locked(self, now):
        """Abgelaufene Erwartungen + alte Queue-Einträge verwerfen (unter _lock)."""
        while self._pending_expect and self._pending_expect[0][2] < now:
            self._pending_expect.popleft()
        # Nicht von vorne (deadlines monoton, aber sicherheitshalber filtern):
        self._pending_expect = deque(
            e for e in self._pending_expect if e[2] >= now)
        while len(self._resp_queue) > RESP_QUEUE_MAX:
            self._resp_queue.popleft()
        cutoff = now - RESP_QUEUE_TTL_S
        self._resp_queue = deque(
            e for e in self._resp_queue if e[5] >= cutoff)

    def _take_queued_locked(self, expect_fun, expect_addr):
        """Ersten passenden Queue-Eintrag entnehmen oder None (unter _lock)."""
        for i, (a, s, f, d, r, _ts) in enumerate(self._resp_queue):
            if self._match_expect(expect_addr, expect_fun, a, f):
                del self._resp_queue[i]
                return a, s, f, d, r
        return None

    def _recv_frame_locked(self, deadline, expect_fun=None, expect_addr=None):
        """EIN gültiger Frame unter gehaltenem _lock. Gibt
        (addr, status, fun, daten, rohframe) zurück. Zählt crc_errors.
        Wirft TimeoutError bei abgelaufener Deadline.

        Ablauf: Puffer (Überhang + gelesene Chunks) wird VOLLSTÄNDIG
        abgearbeitet, bevor neu gelesen wird:
          - unvollständiger Frame-Anfang -> weiterlesen (Fragmente, >64 Byte)
          - falscher Head/unplausibles len/CRC-Fehler -> _resync (nächster 0xF7)
          - gültiger, aber nicht gewollter Frame (fun/addr-Mismatch, z.B.
            Herzschlag) -> als GANZES überspringen (Daten dürfen 0xF7
            enthalten, z.B. Belt-Flash); nicht dispatcht (wie bisher)
        Bei Deadline mit unvollständigem Rest wird hinter dem vermeintlichen
        Head nach einem kompletten Frame gesucht (Müll-Head, der einen echten
        Frame "verschluckt"). Findet sich keiner, bleibt der Rest als
        Überhang erhalten (get_response/_bg_thread können ihn vervollständigen;
        transact leert ihn ohnehin vor jedem Senden).
        """
        buf = bytearray(getattr(self, "_rest_buf", b"") or b"")
        self._rest_buf = b""
        last_err = None
        final = False          # Deadline erreicht: nur noch Lookahead
        pending = b""          # unvollständiger Rest zum Deadline-Zeitpunkt
        found_any = False      # Lookahead hat einen gültigen Frame gefunden
        while True:
            while buf:
                state, val = _scan_frame(buf)
                if state == "need":
                    if not final:
                        break
                    buf = _resync(buf)
                    continue
                if state == "bad":
                    if not final:  # Lookahead-Treffer nicht als Busfehler zählen
                        if "CRC" in str(val):
                            self._inc("crc_errors")
                        last_err = val
                    buf = _resync(buf)
                    continue
                expect, addr, status, rfun, rdata = val
                raw = bytes(buf[:expect])
                if final:
                    found_any = True
                if expect_fun is not None and rfun != expect_fun:
                    last_err = ValueError("fun-Mismatch: 0x%02x" % rfun)
                    del buf[:expect]
                    continue
                if expect_addr is not None and addr != expect_addr:
                    last_err = ValueError("addr-Mismatch: 0x%02x" % addr)
                    del buf[:expect]
                    continue
                self._rest_buf = bytes(buf[expect:])  # Überhang (Folgeframe)
                return addr, status, rfun, rdata, raw
            if final:
                break
            if time.monotonic() >= deadline:
                final = True
                pending = bytes(buf)
                continue
            chunk = self.ser.read(64)
            if chunk:
                buf += chunk
        if not found_any and pending:
            self._rest_buf = pending
        raise TimeoutError(last_err or "keine Antwort")

    def _transact_locked(self, addr, fun, data, timeout, retries,
                         expect_fun, status, want_full=False,
                         expect_addr=_AUTO):
        """Kern unter gehaltenem _lock. want_full -> (addr,status,fun,daten,roh),
        sonst nur daten. Zählt sent/retries/received/timeouts.

        expect_addr (Review M1): Default = reply_addr_for(addr), d.h. nur
        Frames mit Absender == Ziel gelten als Antwort (Broadcast-Ziele:
        beliebig). Fremde Frames mit passendem fun werden übersprungen.
        Explizit None = beliebiger Absender, int = genau dieser Absender.

        Dispatcht NICHT selbst: der Aufrufer ruft _dispatch_safe erst nach
        Freigabe von _lock (sonst Deadlock, wenn ein Handler Bus-Funktionen
        aufruft – threading.Lock ist nicht reentrant)."""
        expect_fun = fun if expect_fun is None else expect_fun
        if expect_addr is _AUTO:
            expect_addr = reply_addr_for(addr)
        frame = build_frame(addr, fun, data, status=status)
        last_err = None
        # TODO: am klippy.log nachzählen, ob 4 Versuche oder 1+4
        # (aktuell: retries=4 -> 1 Erstsendung + 4 Wiederholungen = 5 Sendungen).
        for attempt in range(retries + 1):
            if attempt > 0:
                self._inc("retries")
            try:
                self.ser.reset_input_buffer()
            except Exception:
                pass
            self._rest_buf = b""  # reset verwirft stale Bytes (Live-Verhalten)
            self.ser.write(frame)
            self._inc("sent")
            deadline = time.monotonic() + timeout
            try:
                raddr, rstatus, rfun, rdata, rraw = self._recv_frame_locked(
                    deadline, expect_fun=expect_fun, expect_addr=expect_addr)
            except TimeoutError as e:
                last_err = e
                continue
            self._inc("received")
            if want_full:
                return raddr, rstatus, rfun, rdata, rraw
            return rdata
        self._inc("timeouts")
        raise TimeoutError("addr 0x%02x fun 0x%02x: %s" % (addr, fun, last_err))

    # --- Belegnamen Serialhdl_485 ---
    def connect_uart(self, port=None, baud=None, timeout=None):
        """Öffnet (erneut) den UART (Beleg: Serialhdl_485.connect_uart)."""
        with self._lock:
            if port is not None:
                self.port = port
                self._injected = False
            if baud is not None:
                self.baud = baud
            if timeout is not None:
                self.default_timeout = timeout
            if self._injected and port is None:
                self._connected = True  # injizierter Test-Port bleibt
                return True
            try:
                old = getattr(self, "ser", None)
                if old is not None and self._connected:
                    try:
                        if hasattr(old, "close"):
                            old.close()
                    except Exception:
                        pass
            finally:
                self._connected = False
            try:
                import serial
            except ImportError as e:
                raise SystemExit("pyserial fehlt: pip install pyserial") from e
            self.ser = serial.Serial(self.port, self.baud, timeout=0.02)
            self._injected = False
            self._connected = True
            return True

    def disconnect(self):
        """Schließt Port + stoppt Hintergrund-Leser
        (Beleg: Serialhdl_485.disconnect)."""
        self._stop_bg()
        with self._lock:
            try:
                if (getattr(self, "ser", None) is not None
                        and not getattr(self, "_injected", False)):
                    try:
                        if hasattr(self.ser, "close"):
                            self.ser.close()
                    except Exception:
                        pass
            finally:
                self._connected = False
                self._session_active = False

    def _start_session(self, start_bg=False, bg_timeout=0.2):
        """Explizite Session-Eröffnung (Beleg: Serialhdl_485._start_session).

        Stellt sicher, dass der Port offen ist, leert den Empfangspuffer
        (Port + interner Überhang _rest_buf) und markiert die Session aktiv. Idempotent. Mit start_bg=True wird der
        Hintergrund-Leser (_bg_thread) als Daemon gestartet.
        """
        if not getattr(self, "_connected", False) or getattr(self, "ser", None) is None:
            self.connect_uart()
        else:
            try:
                self.ser.reset_input_buffer()
            except Exception:
                pass
        self._rest_buf = b""
        self._session_active = True
        if start_bg:
            self._start_bg(timeout=bg_timeout)
        return True

    def _start_bg(self, timeout=0.2):
        if self._bg_thread_obj and self._bg_thread_obj.is_alive():
            return
        self._bg_stop.clear()
        self._bg_thread_obj = threading.Thread(
            target=self._bg_thread, kwargs={"timeout": timeout}, daemon=True)
        self._bg_thread_obj.start()

    def _stop_bg(self):
        self._bg_stop.set()
        obj = getattr(self, "_bg_thread_obj", None)
        if obj and obj.is_alive() and obj is not threading.current_thread():
            obj.join(timeout=0.5)
        self._bg_thread_obj = None

    def _bg_thread(self, timeout=0.2):
        """Hintergrund-Leser (Beleg: Serialhdl_485._bg_thread).

        Pollt unsolicited Frames (z.B. Herzschlag 0x0a, Events mit
        status 0x30/0x16) und dispatchet sie an die Registry. Synchronous
        transact-Verkehr läuft weiter über _lock (Halbduplex-Serialisierung).
        Nur aktiv nach _start_session(start_bg=True); disconnect() stoppt.

        Fix B.2: Passt ein Frame zu einer per raw_send registrierten
        Erwartung, wird er NICHT dispatcht, sondern in _resp_queue geparkt –
        get_response holt ihn dort ab (kein Wegschnappen mehr).
        """
        while not self._bg_stop.wait(0.02):
            if not getattr(self, "_connected", False):
                continue
            try:
                with self._lock:
                    deadline = time.monotonic() + timeout
                    try:
                        raddr, rstatus, rfun, rdata, rraw = \
                            self._recv_frame_locked(deadline)
                    except TimeoutError:
                        continue
                    self._inc("received")
                    now = time.monotonic()
                    self._prune_expect_queue_locked(now)
                    parked = False
                    for exp_addr, exp_fun, _dl in list(self._pending_expect):
                        if self._match_expect(exp_addr, exp_fun,
                                              raddr, rfun):
                            self._pending_expect.remove(
                                (exp_addr, exp_fun, _dl))
                            self._resp_queue.append(
                                (raddr, rstatus, rfun, rdata, rraw, now))
                            parked = True
                            break
                    if parked:
                        continue
                # außerhalb von _lock (Handler dürfen Bus-Funktionen rufen)
                self._dispatch_safe(raddr, rstatus, rfun, rdata)
            except Exception:
                _log.exception("_bg_thread: Fehler beim Empfang")
                continue

    def raw_send(self, addr, fun, data=(), status=STATUS_OK,
                 expect_response=True, expect_timeout=None, expect_fun=None,
                 expect_addr=_AUTO):
        """Fire-and-forget-Senden (Beleg: Serialhdl_485.raw_send).

        Fix B.2: Mit expect_response=True (Default) wird atomar mit dem
        Schreiben eine erwartete Antwort registriert – der Hintergrund-Thread
        parkt sie dann in _resp_queue statt sie zu dispatchen, sodass ein
        anschließendes get_response sie auch bei Lücke sicher findet.
        Wer wirklich ohne Antwort sendet, übergibt expect_response=False
        (dann Verhalten wie bisher). expect_fun/expect_addr schränken die
        Erwartung ein (Default: gleicher fun, Absender wie Ziel bzw.
        beliebig bei Broadcast-Zielen).
        """
        frame = build_frame(addr, fun, data, status=status)
        with self._lock:
            self.ser.write(frame)
            self._inc("sent")
            if expect_response:
                efun = fun if expect_fun is None else expect_fun
                if expect_addr is _AUTO:
                    eaddr = reply_addr_for(addr)
                else:
                    eaddr = expect_addr
                ttl = (self.default_timeout if expect_timeout is None
                       else expect_timeout)
                try:
                    ttl = float(ttl)
                except Exception:
                    ttl = BG_EXPECT_TTL_S
                if ttl <= 0:
                    ttl = BG_EXPECT_TTL_S
                now = time.monotonic()
                self._prune_expect_queue_locked(now)
                self._pending_expect.append((eaddr, efun, now + ttl))
        return frame

    def raw_send_wait_ack(self, addr, fun, data=(), timeout=None,
                          retries=MAX_RETRIES, expect_fun=None,
                          status=STATUS_OK, expect_addr=_AUTO):
        """Sendet, wartet auf Antwort mit passendem fun-Code UND passendem
        Absender (expect_addr, s. _transact_locked)
        (Beleg: Serialhdl_485.raw_send_wait_ack). Gibt Antwort-Daten zurück."""
        full = self._send_wait_full_nodispatch(addr, fun, data, timeout,
                                               retries, expect_fun, status,
                                               expect_addr=expect_addr)
        self._dispatch_safe(*full[:4])  # nach Freigabe von _lock
        return full[3]

    def raw_send_wait_ack_full(self, addr, fun, data=(), timeout=None,
                               retries=MAX_RETRIES, expect_fun=None,
                               status=STATUS_OK, expect_addr=_AUTO):
        """Wie raw_send_wait_ack, gibt aber (addr, status, fun, daten, rohframe)
        zurück (für Wrapper-Vollframes, vgl. auto_addr data_handler)."""
        full = self._send_wait_full_nodispatch(addr, fun, data, timeout,
                                               retries, expect_fun, status,
                                               expect_addr=expect_addr)
        self._dispatch_safe(*full[:4])  # nach Freigabe von _lock
        return full

    @staticmethod
    def _check_hold(timeout, retries):
        """Review M6: Haltedauer des Bus-Locks begrenzen (MAX_BUS_HOLD_S)."""
        if timeout < 0 or retries < 0:
            raise ValueError("timeout/retries dürfen nicht negativ sein: "
                             "%r/%r" % (timeout, retries))
        hold = timeout * (retries + 1)
        if hold > MAX_BUS_HOLD_S:
            raise ValueError(
                "Transaktion würde den Bus bis %.0f s blockieren "
                "(timeout %.1f s x %d Sendungen) > MAX_BUS_HOLD_S %.0f s"
                % (hold, timeout, retries + 1, MAX_BUS_HOLD_S))
        return hold

    def _send_wait_full_nodispatch(self, addr, fun, data=(), timeout=None,
                                   retries=MAX_RETRIES, expect_fun=None,
                                   status=STATUS_OK, expect_addr=_AUTO):
        """Intern: wie raw_send_wait_ack_full, aber OHNE Dispatch. Der
        Aufrufer muss _dispatch_safe(*full[:4]) selbst (ohne Locks) aufrufen
        (genutzt vom Wrapper, der unter _send_lock sendet).

        BUS-LOCK (Review M6, Entscheidung 16.10.2026): _lock bleibt für die
        GANZE Transaktion gehalten (Senden bis Antwort/Timeout), auch bei
        langen Kommandos (0x0d op 2/3 bis 980 s, 0x11 bis 150 s). Andere
        Threads (Motoren, 0x0a-Poll, _bg_thread) warten solange. Gründe:
        (1) RS485 halbduplex: der Slave antwortet asynchron am Ende seines
            Vorgangs; sendet der Host in dieser Zeit, kollidieren die Frames
            (keine Arbitrierung, beide Frames kaputt -> Antwort verloren,
            bei retries=0 nicht wiederholbar).
        (2) Original genauso: live sendet der Host während 0x0d [01 02]
            (31-37 s) nichts, auch keine 0x0a-Polls (PROTOKOLL-485.md 6.4,
            klippy.log.2026-10-13:90197-90211).
        (3) Box-FW bearbeitet 0x0d op 2/3 synchron und ist solange stumm
            (live 0x04 5x unbeantwortet, 13:130637-130652).
        Absicherung: Haltedauer timeout*(retries+1) <= MAX_BUS_HOLD_S, sonst
        ValueError; ab LONG_HOLD_LOG_S INFO-Logzeile.
        TODO (M6): Pending-Registry (Senden unter Lock, Warten ohne Lock)
          wäre nur für Geräte sinnvoll, die während einer laufenden Box-
          Operation sicher senden dürfen – dafür fehlt ein Kollisions-Beleg;
          0x10 mode 4 (Box liest den Bus selbst, Ghidra §4) zeigt, dass die
          Box kurze Fragen während des Förderns beantwortet, aber nicht,
          ob fremde Slaves dazwischen senden dürfen.
        """
        timeout = self.default_timeout if timeout is None else timeout
        hold = self._check_hold(timeout, retries)
        if hold >= LONG_HOLD_LOG_S:
            _log.info("Bus-Lock bis %.0f s für addr 0x%02x fun 0x%02x",
                      hold, addr, fun)
        with self._lock:
            return self._transact_locked(addr, fun, list(data), timeout,
                                         retries, expect_fun, status,
                                         want_full=True,
                                         expect_addr=expect_addr)

    def get_response(self, timeout=None, expect_fun=None, expect_addr=None):
        """Liest EINEN gültigen Frame (Beleg: Serialhdl_485.get_response).

        Gibt (addr, status, fun, daten) zurück (daten als bytes).
        Rohframe = build_frame(addr, fun, daten, status=status).
        Wirft TimeoutError nach timeout, zählt crc_errors/received.

        Fix B.2: Zuerst wird _resp_queue geprüft (vom Hintergrund-Thread
        geparkte erwartete Antworten aus der raw_send/get_response-Lücke) –
        erst danach vom seriellen Port gelesen.
        """
        timeout = self.default_timeout if timeout is None else timeout
        with self._lock:
            self._prune_expect_queue_locked(time.monotonic())
            queued = self._take_queued_locked(expect_fun, expect_addr)
            if queued is not None:
                raddr, rstatus, rfun, rdata, _rraw = queued
            else:
                deadline = time.monotonic() + timeout
                try:
                    raddr, rstatus, rfun, rdata, _ = self._recv_frame_locked(
                        deadline, expect_fun=expect_fun, expect_addr=expect_addr)
                except TimeoutError:
                    self._inc("timeouts")
                    raise
                self._inc("received")
        self._dispatch_safe(raddr, rstatus, rfun, rdata)  # ohne _lock
        return raddr, rstatus, rfun, rdata

    def register_response(self, addr, fun, callback):
        """Registriert Antwort-Handler für (addr, fun)
        (Beleg: Serialhdl_485.register_response). None = Wildcard.
        callback(addr, status, fun, daten). callback=None löscht.

        Handler laufen NACH Freigabe des Bus-Locks (im Thread des Aufrufers
        von transact/raw_send_wait_ack/get_response bzw. im _bg_thread) und
        dürfen daher selbst Bus-Funktionen aufrufen. Exceptions im Handler
        werden geloggt (logging), nicht propagiert. Nur die Antwort, auf die
        gewartet wurde, wird dispatcht; übersprungene Fremd-Frames nicht."""
        with self._lock:
            if callback is None:
                self._handlers.pop((addr, fun), None)
            else:
                self._handlers[(addr, fun)] = callback

    def handle_default(self, addr, status, fun, data):
        """Default-Handler (Beleg: Serialhdl_485.handle_default). No-op."""
        return None

    def stats(self):
        """Zählerstand (Beleg: Serialhdl_485.stats / serial_485_queue_get_stats):
        {sent, received, timeouts, retries, crc_errors}."""
        with self._stats_lock:
            return dict(self._stats)

    def transact(self, addr, fun, data=(), timeout=None, retries=MAX_RETRIES,
                 expect_fun=None, status=STATUS_OK, expect_addr=_AUTO):
        """Sendet Frame, wartet auf Antwort mit passendem fun-Code und
        Absender (vgl. raw_send_wait_ack; expect_addr s. _transact_locked).

        Gibt die Antwort-Daten (bytes) zurück, wirft TimeoutError nach retries.
        Das Status-Byte der Antwort geht hier verloren -> transact_full.
        Der Empfangspuffer wird auf PACK_HEAD resynchronisiert, damit Müll oder
        halbe Frames vom Bus den Parser nicht blockieren.
        Bus-Lock für die ganze Dauer, begrenzt durch MAX_BUS_HOLD_S
        (s. _send_wait_full_nodispatch).
        """
        return self.raw_send_wait_ack(addr, fun, data, timeout=timeout,
                                      retries=retries, expect_fun=expect_fun,
                                      status=status, expect_addr=expect_addr)

    def transact_full(self, addr, fun, data=(), timeout=None,
                      retries=MAX_RETRIES, expect_fun=None, status=STATUS_OK,
                      expect_addr=_AUTO):
        """Wie transact, gibt aber (addr, status, fun, daten, rohframe)
        zurück – mit dem Status-Byte der Antwort (Review M2, 16.10.2026;
        = raw_send_wait_ack_full mit transact-Signatur)."""
        return self.raw_send_wait_ack_full(addr, fun, data, timeout=timeout,
                                           retries=retries,
                                           expect_fun=expect_fun,
                                           status=status,
                                           expect_addr=expect_addr)


class Serial_485_Wrapper:
    """High-Level-Wrapper mit Sende-Queue + Response-Registry.

    Entspricht Serial_485_Wrapper (Beleg: serial_485_wrapper.so-Symbole,
    strings-Dump 13.10.2026):
    cmd_send_data_with_response/cmd_485_send_data/add_send_data/
    remove_send_data/send_queue_process/register_response/
    is_exist_in_send_list/handle_callback (+ _connect/_disconnect/stats).

    Wire-Format wie im Live-Betrieb (Beleg: auto_addr_wrapper.py
    send_package + belt_mdl.py send_data): data_send = [addr][len][status]
    [fun][daten...] OHNE Head und OHNE CRC (CRC ergänzt der Treiber beim
    Senden). Rückgabe von cmd_send_data_with_response ist der VOLLE
    Antwort-Frame [F7][addr][len][status][fun][daten][crc] (Beleg:
    auto_addr data_handler ret[0]=head ... ret[-1]=crc) oder None bei
    Timeout (Beleg: send_package "Error: no response").

    Halbduplex: send_queue_process arbeitet FIFO ab, ein Eintrag gleichzeitig;
    Queue-Zugriffe sind Lock-geschützt, der Bus selbst über Serial485._lock.
    """

    def __init__(self, port="/dev/ttyS5", baud=230400, timeout=TIMEOUT_LONG,
                 bus=None, ser=None):
        if bus is not None:
            self.bus = bus
        elif isinstance(port, Serial485):
            self.bus = port
        elif ser is not None:
            self.bus = Serial485(port, baud, timeout, ser=ser)
        else:
            self.bus = Serial485(port, baud, timeout)
        self._queue = deque()
        self._qlock = threading.Lock()
        self._send_lock = threading.Lock()  # genau ein Drain gleichzeitig
        self._handlers = {}          # (addr, fun) -> callback(data_send, msg)
        self._default_handler = None
        self._done = {}              # Eintrags-ID -> Antwort (für Neben-Threads)
        self._done_time = {}         # Eintrags-ID -> monotonic-ts (Fix B.1)
        self._seq = 0

    # --- Fix B.1-Helfer (NUR unter gehaltenem _qlock aufrufen) --------------
    def _store_done_locked(self, eid, resp):
        """Antwort ablegen + verwaiste/überzählige Einträge aufräumen."""
        now = time.monotonic()
        self._done[eid] = resp
        self._done_time[eid] = now
        cutoff = now - WRAPPER_DONE_TTL_S
        for old in [k for k, t in list(self._done_time.items())
                    if t < cutoff]:
            self._done.pop(old, None)
            self._done_time.pop(old, None)
        while len(self._done) > WRAPPER_DONE_MAX:
            oldest = min(self._done)
            self._done.pop(oldest, None)
            self._done_time.pop(oldest, None)

    def _take_done_locked(self, eid, default=None):
        """Antwort abholen (verfallene gilt als fehlend)."""
        now = time.monotonic()
        t = self._done_time.get(eid)
        if t is not None and now - t > WRAPPER_DONE_TTL_S:
            self._done.pop(eid, None)
            self._done_time.pop(eid, None)
            return default
        self._done_time.pop(eid, None)
        return self._done.pop(eid, default)

    def cleanup_done(self):
        """Verwaiste _done-Einträge aufräumen (öffentlich, z.B. zyklisch).

        Gibt (entfernt, übrig) zurück.
        """
        with self._qlock:
            now = time.monotonic()
            cutoff = now - WRAPPER_DONE_TTL_S
            old = [k for k, t in list(self._done_time.items()) if t < cutoff]
            for k in old:
                self._done.pop(k, None)
                self._done_time.pop(k, None)
            while len(self._done) > WRAPPER_DONE_MAX:
                oldest = min(self._done)
                self._done.pop(oldest, None)
                self._done_time.pop(oldest, None)
                old.append(oldest)
            return len(old), len(self._done)

    # --- Queue (Belegnamen) ---
    def add_send_data(self, data, timeout=TIMEOUT_LONG, retransmit=True):
        """Reiht Wire-Bytes ein, gibt Eintrags-ID zurück
        (Beleg: Serial_485_Wrapper.add_send_data)."""
        with self._qlock:
            self._seq += 1
            entry = {"id": self._seq, "data": bytes(data),
                     "timeout": timeout, "retransmit": bool(retransmit)}
            self._queue.append(entry)
            return entry["id"]

    def remove_send_data(self, ident):
        """Entfernt Eintrag per ID oder per Wire-Bytes. Gibt True/False
        (Beleg: Serial_485_Wrapper.remove_send_data)."""
        with self._qlock:
            if isinstance(ident, (bytes, bytearray, list)):
                want = bytes(ident)
                for i, e in enumerate(self._queue):
                    if e["data"] == want:
                        del self._queue[i]
                        return True
                return False
            for i, e in enumerate(self._queue):
                if e["id"] == ident:
                    del self._queue[i]
                    return True
            return False

    def is_exist_in_send_list(self, data):
        """True wenn Wire-Bytes bereits in der Queue
        (Beleg: Serial_485_Wrapper.is_exist_in_send_list)."""
        want = bytes(data)
        with self._qlock:
            return any(e["data"] == want for e in self._queue)

    @staticmethod
    def _split_wire(data_send):
        wire = bytes(data_send)
        if len(wire) < 4:
            raise ValueError("data_send zu kurz: %d" % len(wire))
        return wire[0], wire[1], wire[2], wire[3], wire[4:]

    def _send_entry(self, entry, deferred):
        """Sendet EINEN Queue-Eintrag, gibt Vollframe oder None (Timeout).

        Dispatch (Bus-Registry + handle_callback) wird NICHT hier ausgeführt,
        sondern als (eintrag, vollantwort) an deferred angehängt; der
        Aufrufer führt ihn nach Freigabe von _send_lock aus (_run_deferred),
        damit Handler selbst senden dürfen ohne Deadlock."""
        addr, _length, status, fun, payload = self._split_wire(entry["data"])
        retries = MAX_RETRIES if entry["retransmit"] else 0
        try:
            full = self.bus._send_wait_full_nodispatch(
                addr, fun, list(payload), timeout=entry["timeout"],
                retries=retries, expect_fun=fun, status=status)
        except TimeoutError:
            return None
        deferred.append((entry["data"], full))
        return full[4]

    def _run_deferred(self, deferred):
        """Führt gesammelte Dispatches aus (ohne gehaltene Locks), in
        Sende-Reihenfolge: erst Bus-Registry, dann Wrapper-Registry."""
        for data_send, full in deferred:
            self.bus._dispatch_safe(*full[:4])
            try:
                self.handle_callback(data_send, full[4])
            except Exception:
                _log.exception("Wrapper handle_callback fehlgeschlagen")

    def send_queue_process(self):
        """Arbeitet die Queue FIFO ab, ein Eintrag gleichzeitig
        (Beleg: Serial_485_Wrapper.send_queue_process). Gibt
        [(eintrags-id, antwort-vollframe-oder-None)] in Reihenfolge zurück."""
        results = []
        deferred = []
        with self._send_lock:
            while True:
                with self._qlock:
                    if not self._queue:
                        break
                    entry = self._queue.popleft()
                resp = self._send_entry(entry, deferred)
                with self._qlock:
                    self._store_done_locked(entry["id"], resp)
                results.append((entry["id"], resp))
        # Handler erst nach Freigabe von _send_lock (dürfen selbst senden)
        self._run_deferred(deferred)
        return results

    # --- Sende-Kommandos (Belegnamen) ---
    def cmd_send_data_with_response(self, data, timeout=1.0, retransmit=True):
        """Sendet Wire-Bytes, wartet auf Antwort-Vollframe
        (Beleg: Serial_485_Wrapper.cmd_send_data_with_response).

        Gibt den VOLLEN Antwort-Frame (bytes, inkl. Head+CRC) zurück oder
        None bei Timeout (vgl. auto_addr send_package). FIFO-fair: vorher
        eingereihte Einträge werden zuerst gesendet.
        """
        eid = self.add_send_data(data, timeout=timeout, retransmit=retransmit)
        deferred = []
        result = None
        with self._send_lock:
            with self._qlock:
                done = eid in self._done  # Neben-Thread hat ihn schon erledigt
                if done:
                    result = self._take_done_locked(eid)
            while not done:
                with self._qlock:
                    if not self._queue:
                        result = self._take_done_locked(eid, None)
                        break
                    entry = self._queue.popleft()
                resp = self._send_entry(entry, deferred)
                with self._qlock:
                    self._store_done_locked(entry["id"], resp)
                    if entry["id"] == eid:
                        result = self._take_done_locked(eid)
                        break
                    # fremde Antworten liegen für deren Threads in _done
        # Handler erst nach Freigabe von _send_lock (dürfen selbst senden)
        self._run_deferred(deferred)
        return result

    def cmd_485_send_data(self, data, timeout=1.0):
        """Fire-and-forget (Beleg: Serial_485_Wrapper.cmd_485_send_data).
        Gibt den gesendeten Frame zurück."""
        addr, _length, status, fun, payload = self._split_wire(data)
        return self.bus.raw_send(addr, fun, list(payload), status=status,
                                 expect_response=False)

    # --- Registry/Dispatch (Belegnamen) ---
    def register_response(self, addr, fun, callback):
        """Registriert Handler callback(data_send, msg) für (addr, fun)
        (Beleg: Serial_485_Wrapper.register_response). None = Wildcard,
        callback=None löscht."""
        with self._qlock:
            if callback is None:
                self._handlers.pop((addr, fun), None)
            else:
                self._handlers[(addr, fun)] = callback

    def handle_callback(self, data_send, msg):
        """Dispatchet Antwort-Vollframe msg an Registry
        (Beleg: Serial_485_Wrapper.handle_callback). Handler-Exceptions
        werden geloggt und nicht propagiert (Rückgabe dann None). Wird vom
        Wrapper ohne gehaltene Locks aufgerufen."""
        try:
            rfun, _rdata = parse_frame(bytes(msg))
        except ValueError:
            return None
        raw = bytes(msg)
        raddr = raw[1] if len(raw) > 1 else None
        for key in ((raddr, rfun), (raddr, None), (None, rfun), (None, None)):
            cb = self._handlers.get(key)
            if cb is not None:
                try:
                    return cb(bytes(data_send), bytes(msg))
                except Exception:
                    _log.exception("Wrapper-Handler %r fehlgeschlagen", key)
                    return None
        if self._default_handler is not None:
            try:
                return self._default_handler(bytes(data_send), bytes(msg))
            except Exception:
                _log.exception("Wrapper-Default-Handler fehlgeschlagen")
        return None

    # --- Lifecycle/Stats ---
    def _connect(self, *a, **k):
        return self.bus.connect_uart(*a, **k)

    def _disconnect(self):
        return self.bus.disconnect()

    def _shutdown(self):
        return self.bus.disconnect()

    def stats(self):
        """Bus-Zähler + queue_len/done_len (vgl. serial_485_queue_get_stats)."""
        d = self.bus.stats()
        with self._qlock:
            d["queue_len"] = len(self._queue)
            d["done_len"] = len(self._done)
        return d


# --- Selbsttest mit den mitgeschnittenen Live-Frames (keine Hardware nötig) ---
_LIVE_FRAMES = [
    # (hex-frame, Beschreibung)
    ("f7 21 07 00 00 01 02 03 04 f0", "belt Version-Resp"),
    ("f7 21 10 00 02 03 00 00 00 93 ff f7 b7 95 ff f3 51 4c 3d", "belt Flash-Resp"),
    ("f7 21 07 00 06 ff f8 c7 12 4c", "belt ADC-Resp"),
    ("f7 01 11 00 a2 01 00 a0 a1 a2 a3 a4 a5 a6 a7 a8 a9 aa ab f0", "Auto-Addr a2"),
    ("f7 01 07 00 03 ff ff ff 00 98", "GET_REMAIN_LEN leer"),
    ("f7 01 09 00 0a 13 34 00 00 00 00 d7", "Herzschlag 0x0a"),
    ("f7 01 03 00 02 b3", "GET_RFID leere Antwort (Status 00)"),
    ("f7 01 07 00 03 1a 00 ff 00 99", "Remain A=26%"),
    # Status 00 = ANTWORT ohne Daten (Requests tragen Status 0xff), Review G5
    ("f7 01 03 00 10 cd", "EXTRUDE leere Antwort (Status 00)"),
    ("f7 01 03 00 11 ca", "RETRUDE leere Antwort (Status 00)"),
    ("f7 22 10 00 04 03 00 00 00 8a ff f1 63 09 ff ed 12 bd 3c", "belt mdly write_flash"),
]


def selftest():
    fails = 0
    total = 0  # Anzahl aller Einzelchecks (Live-Frames, Asserts, check2)
    for hexframe, label in _LIVE_FRAMES:
        total += 1
        raw = bytes.fromhex(hexframe)
        # 1. CRC des geloggten Frames muss stimmen
        body = list(raw[2:-1])
        if crc8_cal(body) != raw[-1]:
            print("FAIL crc %s" % label)
            fails += 1
            continue
        # 2. Neu gebauter Frame (addr/fun/daten) muss identisch sein
        rebuilt = build_frame(raw[1], raw[4], raw[5:-1], status=raw[3])
        if rebuilt != raw:
            print("FAIL rebuild %s" % label)
            fails += 1
            continue
        # 3. Parsen muss fun + daten liefern
        fun, data = parse_frame(raw)
        assert fun == raw[4] and data == raw[5:-1], label
        print("ok %s" % label)
    # 4. Resync: Müll + halber Frame vor gültigem Frame werden verworfen
    noisy = (b"\x00\xff\x01\x02" +
             bytes.fromhex("f7 01 03 00 02 b3"))
    assert _resync(bytearray(noisy)) == bytes.fromhex("f7 01 03 00 02 b3")
    print("ok resync Müllpräfix")
    total += 1
    assert _resync(bytearray(b"\x00\x01\x02")) == bytearray()
    print("ok resync ohne Head")
    total += 1
    # 5. Transaktions-Lock existiert (Schutz bei parallelen Threads)
    import sys
    import types

    class _FakePort:
        def reset_input_buffer(self):
            pass

        def write(self, frame):
            self.written = frame

        def read(self, n):
            return b""

    fake_serial = types.ModuleType("serial")
    fake_serial.Serial = lambda *a, **k: _FakePort()
    sys.modules["serial"] = fake_serial
    try:
        inst = Serial485("/dev/ttyFAKE0")
        assert hasattr(inst, "_lock")
        import threading
        assert isinstance(inst._lock, type(threading.Lock()))
        print("ok transact-lock")
        total += 1
    finally:
        del sys.modules["serial"]

    # --- 6+. Session-Schicht (Belegnamen .so), hardwarelos mit FakeSerial ---
    class FakeSerial:
        """Simulierter Halbduplex-Port: spielt pro write() genau eine
        geskriptete Antwort ab (bytes-Vollframe oder None = Stille/Timeout).
        Zeichnet alle writes in Reihenfolge auf."""

        def __init__(self, script):
            self.script = list(script)
            self.written = []
            self._read_buf = bytearray()

        def reset_input_buffer(self):
            self._read_buf.clear()

        def write(self, frame):
            self.written.append(bytes(frame))
            self._read_buf.clear()
            if self.script:
                nxt = self.script.pop(0)
                if nxt is not None:
                    self._read_buf += bytes(nxt)

        def read(self, n):
            if self._read_buf:
                chunk = bytes(self._read_buf[:n])
                del self._read_buf[:n]
                return chunk
            time.sleep(0.002)
            return b""

        def close(self):
            pass

    def check2(name, cond):
        nonlocal fails, total
        total += 1
        print(("ok " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    # 6. connect/disconnect/_start_session/stats-Verwaltung
    bus0 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([]))
    check2("session start", bus0._start_session() is True
           and bus0._session_active is True)
    check2("session verbunden", bus0._connected is True)
    s0 = bus0.stats()
    check2("stats keys", set(s0) == {"sent", "received", "timeouts",
                                     "retries", "crc_errors"})
    check2("stats null", all(v == 0 for v in s0.values()))
    check2("methoden vorhanden",
           all(hasattr(bus0, m) for m in (
               "raw_send", "raw_send_wait_ack", "connect_uart",
               "_start_session", "_bg_thread", "get_response",
               "disconnect", "stats", "register_response")))
    bus0.disconnect()
    check2("disconnect", bus0._connected is False
           and bus0._session_active is False)
    bus0.connect_uart()  # injiziert: ohne pyserial/Hardware
    check2("reconnect", bus0._connected is True)
    bus0._start_session()

    # 7. Queue-Reihenfolge + Registry-Dispatch + Stats (Wrapper)
    resp1 = build_frame(0x01, 0x10, [0xA1])
    resp2 = build_frame(0x01, 0x10, [0xB2])
    resp3 = build_frame(0x01, 0x10, [0xC3])
    wire1 = bytes([0x01, 3 + 3, STATUS_OPERATIONAL, 0x10, 0x01, 0x05, 0x00])
    wire2 = bytes([0x01, 3 + 3, STATUS_OPERATIONAL, 0x10, 0x01, 0x06, 0x00])
    wire3 = bytes([0x01, 3 + 3, STATUS_OPERATIONAL, 0x10, 0x01, 0x07, 0x03])
    bus1 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([resp1, resp2, resp3]))
    bus1._start_session()
    wrap = Serial_485_Wrapper(bus=bus1)
    check2("wrapper methoden",
           all(hasattr(wrap, m) for m in (
               "cmd_send_data_with_response", "cmd_485_send_data",
               "add_send_data", "remove_send_data", "send_queue_process",
               "register_response", "is_exist_in_send_list")))
    hits = []
    wrap.register_response(0x01, 0x10, lambda ds, msg: hits.append((bytes(ds), bytes(msg))))
    bus_hits = []
    bus1.register_response(0x01, 0x10,
                           lambda a, s, f, d: bus_hits.append((a, s, f, bytes(d))))
    # remove/is_exist-Pfad (eigener Mini-Queue-Stand, stört Reihenfolge nicht)
    dummy = bytes([0x01, 3, STATUS_OK, 0xA2])
    did = wrap.add_send_data(dummy, timeout=0.05)
    check2("queue exist", wrap.is_exist_in_send_list(dummy) is True)
    check2("queue remove", wrap.remove_send_data(did) is True
           and wrap.is_exist_in_send_list(dummy) is False)
    check2("queue remove-miss", wrap.remove_send_data(999999) is False)
    id1 = wrap.add_send_data(wire1, timeout=0.5)
    id2 = wrap.add_send_data(wire2, timeout=0.5)
    id3 = wrap.add_send_data(wire3, timeout=0.5)
    check2("queue fifo-ids", (id1, id2, id3) == (id1, id1 + 1, id1 + 2))
    results = wrap.send_queue_process()
    check2("queue reihenfolge",
           [r for _, r in results] == [resp1, resp2, resp3]
           and [i for i, _ in results] == [id1, id2, id3])
    check2("queue wire-reihenfolge",
           [bytes(w)[5:-1] for w in bus1.ser.written] ==
           [bytes(wire1)[4:], bytes(wire2)[4:], bytes(wire3)[4:]]
           and [bytes(w)[4] for w in bus1.ser.written] == [0x10, 0x10, 0x10])
    check2("registry dispatch wrapper", len(hits) == 3
           and hits[0] == (wire1, resp1) and hits[2] == (wire3, resp3))
    check2("registry dispatch bus", len(bus_hits) == 3
           and bus_hits[1][3] == bytes([0xB2]))
    st1 = wrap.stats()
    check2("stats queue", st1["sent"] == 3 and st1["received"] == 3
           and st1["queue_len"] == 0)
    # Einzel-Call (auto_addr-Signatur: wire -> Vollframe oder None)
    bus1b = Serial485("/dev/ttyFAKE1", ser=FakeSerial([resp1]))
    bus1b._start_session()
    wrap1b = Serial_485_Wrapper(bus=bus1b)
    single = wrap1b.cmd_send_data_with_response(wire1, 0.5, False)
    check2("cmd_send_data_with_response", single == resp1)
    check2("cmd timeout->None",
           Serial_485_Wrapper(bus=Serial485(
               "/dev/ttyFAKE1", ser=FakeSerial([None]))).cmd_send_data_with_response(
                   wire1, 0.03, False) is None)

    # 8. Retry-Zählung: 1x Stille, dann Antwort
    good = build_frame(0x01, 0x03, [0x1A, 0x00, 0xFF, 0x00])
    bus2 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([None, good]))
    bus2._start_session()
    rdata = bus2.transact(0x01, 0x03, [0x0F], timeout=0.05, retries=2,
                          status=STATUS_OPERATIONAL)
    check2("retry antwort", rdata == bytes([0x1A, 0x00, 0xFF, 0x00]))
    st2 = bus2.stats()
    check2("retry zaehlung", st2["sent"] == 2 and st2["retries"] >= 1
           and st2["received"] == 1 and st2["timeouts"] == 0)

    # 9. Timeout- und CRC-Stats
    bus3 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([None, None]))
    bus3._start_session()
    try:
        bus3.transact(0x01, 0x03, [0x0F], timeout=0.03, retries=1)
        check2("timeout wirft", False)
    except TimeoutError:
        check2("timeout wirft", True)
    check2("timeout stats", bus3.stats()["timeouts"] == 1)
    bad = bytearray(build_frame(0x01, 0x03, [0x01]))
    bad[-1] ^= 0xFF  # CRC verderben
    bus4 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([bytes(bad), good]))
    bus4._start_session()
    rdata4 = bus4.transact(0x01, 0x03, [0x0F], timeout=0.2, retries=2)
    st4 = bus4.stats()
    check2("crc retry antwort", rdata4 == bytes([0x1A, 0x00, 0xFF, 0x00]))
    check2("crc stats", st4["crc_errors"] >= 1 and st4["retries"] >= 1)

    # 10. raw_send (ohne ACK) + get_response (ein Frame)
    bus5 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([resp2]))
    bus5._start_session()
    sent_frame = bus5.raw_send(0x01, 0x10, [0x01, 0x05, 0x00],
                               status=STATUS_OPERATIONAL)
    check2("raw_send frame", sent_frame == build_frame(
        0x01, 0x10, [0x01, 0x05, 0x00], status=STATUS_OPERATIONAL))
    check2("raw_send stats", bus5.stats()["sent"] == 1)
    bus5.ser._read_buf += bytearray(resp2)  # Antwort einlegen
    ga, gs, gf, gd = bus5.get_response(timeout=0.3)
    check2("get_response", (ga, gs, gf, gd) == (0x01, 0x00, 0x10, bytes([0xB2])))

    # 10b. Empfangs-Robustheit (Review K1-K3, G2, M1/M2)
    class ChunkSerial(FakeSerial):
        """Liefert den Empfangspuffer in festen Häppchen: chunks = Liste von
        Chunkgrößen je read() (letzte wiederholt sich)."""

        def __init__(self, script, chunks):
            FakeSerial.__init__(self, script)
            self.chunks = list(chunks)
            self._ci = 0

        def read(self, n):
            if self._read_buf:
                k = self.chunks[min(self._ci, len(self.chunks) - 1)]
                self._ci += 1
                return FakeSerial.read(self, min(n, k))
            return FakeSerial.read(self, n)

    gdata = bytes([0x1A, 0x00, 0xFF, 0x00])
    for name, chunks in (("3+rest", [3, 64]), ("4-byte", [4])):
        busf = Serial485("/dev/ttyFAKE1", ser=ChunkSerial([good], chunks))
        try:
            ok = busf.transact(0x01, 0x03, [0x0F], timeout=0.2, retries=0) == gdata
        except TimeoutError:
            ok = False
        check2("fragmentiert %s" % name, ok)
    bigdata = bytes(range(80))
    big = build_frame(0x01, 0x02, bigdata)  # 86 Byte > read(64)
    busb = Serial485("/dev/ttyFAKE1", ser=FakeSerial([big]))
    try:
        ok = busb.transact(0x01, 0x02, [0x01], timeout=0.2, retries=0) == bigdata
    except TimeoutError:
        ok = False
    check2("frame >64 byte", ok and busb.stats()["crc_errors"] == 0)
    hb = bytes.fromhex("f7 01 09 00 0a 13 34 00 00 00 00 d7")
    bush = Serial485("/dev/ttyFAKE1", ser=FakeSerial([hb + good]))
    t0 = time.monotonic()
    try:
        ok = bush.transact(0x01, 0x03, [0x0F], timeout=0.5, retries=0) == gdata
    except TimeoutError:
        ok = False
    check2("herzschlag+antwort ein chunk", ok and time.monotonic() - t0 < 0.4)
    flash = bytes.fromhex("f7 21 10 00 02 03 00 00 00 93 ff f7 b7 95 ff f3 51 4c 3d")
    adc = build_frame(0x21, 0x06, [0xFF, 0xF8, 0xC7, 0x12])
    busx = Serial485("/dev/ttyFAKE1", ser=FakeSerial([]))
    busx.ser._read_buf += flash + adc
    try:
        ok = busx.get_response(timeout=0.3, expect_fun=0x06) == (
            0x21, 0x00, 0x06, bytes([0xFF, 0xF8, 0xC7, 0x12]))
    except TimeoutError:
        ok = False
    check2("fremd-frame mit 0xF7 in daten", ok and busx.stats()["crc_errors"] == 0)
    junk = bytes.fromhex("f7 01 00 00 00 00")
    busj = Serial485("/dev/ttyFAKE1", ser=FakeSerial([junk + good]))
    try:
        ok = busj.transact(0x01, 0x03, [0x0F], timeout=0.2, retries=0) == gdata
    except Exception:  # vorher IndexError
        ok = False
    check2("müll len=0 vor antwort", ok)
    try:
        parse_frame(junk)
        ok = False
    except ValueError:
        ok = True
    check2("parse_frame len<3 -> ValueError", ok)
    busr = Serial485("/dev/ttyFAKE1", ser=FakeSerial([]))
    busr._rest_buf = good
    busr._start_session()
    try:
        busr.get_response(timeout=0.03)
        ok = False
    except TimeoutError:
        ok = True
    check2("_start_session leert _rest_buf", ok)
    # Handler ruft selbst Bus-/Wrapper-Funktionen (früher Deadlock)
    busd = Serial485("/dev/ttyFAKE1", ser=FakeSerial([good]))
    nested = []
    busd.register_response(0x01, 0x03,
                           lambda a, st, f, d: nested.append(busd.raw_send(0x01, 0x10)))
    thd = threading.Thread(target=lambda: busd.transact(
        0x01, 0x03, [0x0F], timeout=0.1, retries=0), daemon=True)
    thd.start()
    thd.join(1.5)
    check2("bus-handler ruft raw_send (kein deadlock)",
           not thd.is_alive() and len(nested) == 1)
    wbus = Serial485("/dev/ttyFAKE1", ser=FakeSerial([good, good]))
    wwrap = Serial_485_Wrapper(bus=wbus)
    wwire = bytes([0x01, 4, STATUS_OK, 0x03, 0x0F])
    wnested = []
    wflag = []

    def _wcb(ds, msg):
        if not wflag:  # nur einmal verschachtelt senden
            wflag.append(1)
            wnested.append(wwrap.cmd_send_data_with_response(wwire, 0.1, False))
    wwrap.register_response(0x01, 0x03, _wcb)
    thw = threading.Thread(target=lambda: wwrap.cmd_send_data_with_response(
        wwire, 0.1, False), daemon=True)
    thw.start()
    thw.join(1.5)
    check2("wrapper-handler sendet selbst (kein deadlock)",
           not thw.is_alive() and wnested == [good])
    # Handler-Exception wird geloggt, nicht propagiert
    buse = Serial485("/dev/ttyFAKE1", ser=FakeSerial([good]))

    def _boom(*a):
        raise RuntimeError("selbsttest: erwartete Handler-Exception")
    buse.register_response(0x01, 0x03, _boom)
    _lg = logging.getLogger(__name__)
    _old_disabled, _lg.disabled = _lg.disabled, True  # Traceback nicht ausgeben
    try:
        ok = buse.transact(0x01, 0x03, [0x0F], timeout=0.1, retries=0) == gdata
    except Exception:
        ok = False
    finally:
        _lg.disabled = _old_disabled
    check2("handler-exception nicht propagiert", ok)

    # 10c. Review M1 (16.10.2026): Absenderadresse muss zum Ziel passen
    box_wheel = bytes.fromhex("f7 01 07 00 0e c4 e6 fe 0b f3")  # Box, fun 0x0e
    mot_reply = build_frame(0x81, 0x0e, [0x11, 0x22])          # synthetisch
    busm = Serial485("/dev/ttyFAKE1", ser=FakeSerial([box_wheel + mot_reply]))
    try:
        ok = busm.transact(0x81, 0x0e, [0x02], timeout=0.2, retries=0) \
            == bytes([0x11, 0x22])
    except TimeoutError:
        ok = False
    check2("M1 fremder Frame (0x01) vor Motor-Antwort -> echte Antwort", ok)
    busm2 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([box_wheel]))
    try:
        busm2.transact(0x81, 0x0e, [0x02], timeout=0.05, retries=0)
        ok = False
    except TimeoutError as e:
        ok = "addr-Mismatch" in str(e)
    check2("M1 nur fremder Frame -> Timeout (addr-Mismatch)", ok)
    busm3 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([box_wheel]))
    try:
        ok = busm3.transact(0x81, 0x0e, [0x02], timeout=0.1, retries=0,
                            expect_addr=None) == box_wheel[5:-1]
    except TimeoutError:
        ok = False
    check2("M1 expect_addr=None -> beliebiger Absender (explizit)", ok)
    a1_fe = build_frame(0xFE, 0xA1, [0x01])
    a2_01 = build_frame(0x01, 0xA2, [0x01])
    busbc = Serial485("/dev/ttyFAKE1", ser=FakeSerial([a1_fe, a2_01]))
    try:
        ok = (busbc.transact(0xFE, 0xA1, [0xFE, 0xFE], timeout=0.1,
                             retries=0) == b"\x01"
              and busbc.transact(0xFF, 0xA2, [], timeout=0.1, retries=0)
              == b"\x01")
    except TimeoutError:
        ok = False
    check2("M1 Broadcast-Ziel 0xFE/0xFF: Absender beliebig", ok)
    check2("M1 reply_addr_for", reply_addr_for(0x81) == 0x81
           and all(reply_addr_for(a) is None
                   for a in (0xFE, 0xFF, 0xFD, 0xFC, 0xEA, 0xEB)))
    wrm = Serial_485_Wrapper(bus=Serial485(
        "/dev/ttyFAKE1", ser=FakeSerial([box_wheel + mot_reply])))
    check2("M1 Wrapper-Pfad überspringt fremden Absender",
           wrm.cmd_send_data_with_response(
               bytes([0x81, 4, STATUS_OK, 0x0e, 0x02]), 0.2, False)
           == mot_reply)

    # 10d. Review M2: transact_full liefert das Status-Byte (Live-Frame
    #      0x10 mode 5 mit Status 0x0c Odometer-Stillstand)
    st0c = bytes.fromhex("f7 01 07 0c 10 c3 30 40 af a4")
    bussf = Serial485("/dev/ttyFAKE1", ser=FakeSerial([st0c]))
    full = bussf.transact_full(0x01, 0x10, [0x02, 0x05, 0x00], timeout=0.1,
                               retries=0, status=STATUS_OPERATIONAL)
    check2("M2 transact_full Status 0x0c + Daten",
           full[:4] == (0x01, 0x0c, 0x10, bytes.fromhex("c3 30 40 af"))
           and full[4] == st0c)

    # 10e. Spätantwort nach Timeout bei retries=0: genau 1 Sendung (keine
    #      Mehrfachauslösung der Mechanik); die Spätantwort verwirft das
    #      reset_input_buffer der nächsten Transaktion, sofern sie VOR deren
    #      Senden eintrifft (sonst fun/addr-Filter, s. _recv_frame_locked)
    class LateSerial(FakeSerial):
        """Antwort auf die 1. Sendung erst nach delay s (nach dem Timeout)."""

        def __init__(self, late, delay, script=()):
            FakeSerial.__init__(self, script)
            self.late, self.delay, self.t_write = late, delay, None

        def write(self, frame):
            if self.t_write is None:
                self.t_write = time.monotonic()
                self.written.append(bytes(frame))
                return
            FakeSerial.write(self, frame)

        def read(self, n):
            if (self.late is not None and self.t_write is not None
                    and time.monotonic() - self.t_write >= self.delay):
                self._read_buf += self.late
                self.late = None
            return FakeSerial.read(self, n)

    late_ack = build_frame(0x01, 0x11, [])
    busl = Serial485("/dev/ttyFAKE1", ser=LateSerial(late_ack, 0.08))
    try:
        busl.transact(0x01, 0x11, [0x02, 0x01], timeout=0.04, retries=0,
                      status=STATUS_OPERATIONAL)
        ok = False
    except TimeoutError:
        ok = True
    check2("Spätantwort retries=0: Timeout, genau 1 Sendung",
           ok and len(busl.ser.written) == 1 and busl.stats()["sent"] == 1
           and busl.stats()["retries"] == 0)

    # 10f. Review M6: Bus-Lock-Obergrenze + kein Deadlock bei langem Halten
    busv = Serial485("/dev/ttyFAKE1", ser=FakeSerial([]))
    for bad_t, bad_r in ((MAX_BUS_HOLD_S + 1, 0), (300.0, 4), (-1.0, 0)):
        try:
            busv.transact(0x01, 0x0d, [0x0f, 0x02], timeout=bad_t,
                          retries=bad_r)
            ok = False
        except ValueError:
            ok = busv.stats()["sent"] == 0
        check2("M6 Haltedauer-Guard %.0f s x %d" % (bad_t, bad_r + 1), ok)

    class SlowSerial(FakeSerial):
        """1. Antwort kommt erst nach delay s (langes Kommando)."""

        def __init__(self, script, delay):
            FakeSerial.__init__(self, script)
            self.delay, self.t0, self.pending = delay, None, None

        def write(self, frame):
            FakeSerial.write(self, frame)
            if self.t0 is None:  # 1. Sendung: Antwort zurückhalten
                self.t0 = time.monotonic()
                self.pending, self._read_buf = bytes(self._read_buf), bytearray()

        def read(self, n):
            if self.pending and time.monotonic() - self.t0 >= self.delay:
                self._read_buf += self.pending
                self.pending = None
            return FakeSerial.read(self, n)

    ack0d = build_frame(0x01, 0x0d, [])
    busw = Serial485("/dev/ttyFAKE1", ser=SlowSerial([ack0d, good], 0.25))
    res = {}
    tl = threading.Thread(target=lambda: res.__setitem__("long", busw.transact(
        0x01, 0x0d, [0x01, 0x02], timeout=1.0, retries=0)), daemon=True)
    tl.start()
    time.sleep(0.05)
    t_b = time.monotonic()
    ts = threading.Thread(target=lambda: res.__setitem__("short", busw.transact(
        0x01, 0x03, [0x0F], timeout=0.3, retries=0)), daemon=True)
    ts.start()
    tl.join(2.0)
    ts.join(2.0)
    check2("M6 zweiter Thread wartet auf Lock, dann eigene Antwort",
           not tl.is_alive() and not ts.is_alive()
           and res.get("long") == b"" and res.get("short") == gdata
           and time.monotonic() - t_b >= 0.15
           and [bytes(w)[4] for w in busw.ser.written] == [0x0d, 0x03])

    # 11. Kreuzvalidierung gegen Fremd-Referenz ref-hi/ (GPL-3.0, nur lesen;
    #     Herkunft siehe ref-hi/HERKUNFT.txt). Hex-Strings enthalten teils
    #     Leerzeichen-Tippfehler -> vor bytes.fromhex alle Leerzeichen entfernen.
    import json
    import os

    def _hex(sv):
        return bytes.fromhex("".join(sv.split()))

    ref_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ref-hi")
    if not os.path.isdir(ref_dir):
        print("skip ref-hi (Ordner fehlt: %s)" % ref_dir)
    else:
        with open(os.path.join(ref_dir, "test_vectors.json"), encoding="utf-8") as fh:
            vectors = json.load(fh)
        for i, vec in enumerate(vectors):
            full = _hex(vec["full_frame_hex"])
            scope = _hex(vec["crc_scope_hex"])
            expected = int(vec["expected_crc"], 16)
            check2("ref-hi crc #%d %s" % (i, vec.get("description", "")),
                   crc8_cal(list(scope)) == expected
                   and full[2:-1] == scope and full[-1] == expected)
        with open(os.path.join(ref_dir, "valid_messages.json"), encoding="utf-8") as fh:
            messages = json.load(fh)
        for i, msg in enumerate(messages):
            raw = _hex(msg["hex"])
            func = int(msg["func"], 16)
            status = int(msg["status"], 16)
            data = _hex(msg["data_hex"])
            try:
                parsed = parse_frame(raw)
            except ValueError:
                parsed = None
            check2("ref-hi msg #%d %s" % (i, msg.get("description", "")),
                   build_frame(msg["addr"], func, data, status=status) == raw
                   and parsed == (func, data))

    # 12. Fix B.1: _done wächst nicht unbegrenzt (MAX + TTL + cleanup)
    wbus12 = Serial485("/dev/ttyFAKE1", ser=FakeSerial(
        [build_frame(0x01, 0x03, [0x00])] * 300))
    wbus12._start_session()
    wrap12 = Serial_485_Wrapper(bus=wbus12)
    wire12 = bytes([0x01, 4, STATUS_OK, 0x03, 0x0F])
    for _ in range(WRAPPER_DONE_MAX + 22):
        wrap12.add_send_data(wire12, timeout=0.05)
    res12 = wrap12.send_queue_process()
    check2("B.1 _done begrenzt (MAX %d)" % WRAPPER_DONE_MAX,
           len(res12) == WRAPPER_DONE_MAX + 22
           and len(wrap12._done) <= WRAPPER_DONE_MAX
           and wrap12.stats()["done_len"] <= WRAPPER_DONE_MAX)
    check2("B.1 älteste verdrängt, neueste da",
           min(wrap12._done) > 1 and max(wrap12._done) == wrap12._seq)
    # TTL: künstlich altern lassen -> cleanup räumt ab
    for k in list(wrap12._done_time):
        wrap12._done_time[k] -= (WRAPPER_DONE_TTL_S + 1.0)
    removed, left = wrap12.cleanup_done()
    check2("B.1 cleanup_done räumt Verfallene",
           removed >= 1 and left == 0 and len(wrap12._done) == 0)
    # Normalbetrieb nach Cleanup ungestört:
    wbus12b = Serial485("/dev/ttyFAKE1", ser=FakeSerial(
        [build_frame(0x01, 0x03, [0x07])]))
    wbus12b._start_session()
    wrap12b = Serial_485_Wrapper(bus=wbus12b)
    check2("B.1 Einzel-Call nach Cleanup",
           wrap12b.cmd_send_data_with_response(wire12, 0.2, False)
           == build_frame(0x01, 0x03, [0x07]))

    # 13. Fix B.2: bg-Thread schnappt get_response nichts mehr weg
    resp13 = build_frame(0x01, 0x08, [0x07])
    bus13 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([]))
    bus13._start_session(start_bg=True, bg_timeout=0.05)
    hits13 = []
    bus13.register_response(0x01, 0x08,
                            lambda a, s, f, d: hits13.append(bytes(d)))
    bus13.raw_send(0x01, 0x08, [0x00])  # registriert Erwartung (Default)
    time.sleep(0.12)  # Lücke: bg hätte früher die Antwort kassiert
    bus13.ser._read_buf += bytearray(resp13)  # Antwort trifft EIN
    time.sleep(0.12)  # bg liest zuerst – parkt statt zu dispatchen
    try:
        ga, gs, gf, gd = bus13.get_response(timeout=0.5, expect_fun=0x08)
        ok13 = (ga, gs, gf, gd) == (0x01, 0x00, 0x08, bytes([0x07]))
    except TimeoutError:
        ok13 = False
    check2("B.2 get_response findet geparkte Antwort (kein Timeout)", ok13)
    check2("B.2 geparkte Antwort NICHT doppelt dispatcht", hits13 == [bytes([0x07])])
    bus13.disconnect()
    # Ohne Erwartung: Verhalten wie bisher (Dispatch, kein Parken)
    resp13b = build_frame(0x01, 0x08, [0x05])
    bus13b = Serial485("/dev/ttyFAKE1", ser=FakeSerial([]))
    bus13b._start_session(start_bg=True, bg_timeout=0.05)
    hits13b = []
    bus13b.register_response(0x01, 0x08,
                             lambda a, s, f, d: hits13b.append(bytes(d)))
    bus13b.raw_send(0x01, 0x08, [0x00], expect_response=False)
    bus13b.ser._read_buf += bytearray(resp13b)
    time.sleep(0.25)
    try:
        bus13b.get_response(timeout=0.05, expect_fun=0x08)
        ok13b = False  # dürfte nichts mehr finden (bg hat dispatcht)
    except TimeoutError:
        ok13b = True
    check2("B.2 ohne Erwartung: bg dispatcht wie bisher", ok13b and hits13b == [bytes([0x05])])
    bus13b.disconnect()

    print("SELBSTTEST: %d/%d ok" % (total - fails, total))
    return fails


if __name__ == "__main__":
    raise SystemExit(selftest())
