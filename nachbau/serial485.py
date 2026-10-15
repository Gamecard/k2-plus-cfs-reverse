#!/usr/bin/env python3
"""serial485.py – Reiner-Python-Nachbau des Creality-RS485-Bustreibers (K2 Plus).

Ersetzt: serial_485_wrapper.cpython-39.so (armhf, CPython 3.9, proprietär).

Belege (alle in /home/pro/k2-fw-live-1.1.7.0/ dokumentiert):
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

MAX_RETRIES = 4          # Beleg: klippy.log "retries = 4"

import threading
import time
from collections import deque


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
    expect = length + 3  # head+addr+len (3) + length-Inhalt
    if len(raw) < expect:
        raise ValueError("unvollständig: %d < %d" % (len(raw), expect))
    body = list(raw[2:expect - 1])
    if crc8_cal(body) != raw[expect - 1]:
        raise ValueError("CRC-Fehler")
    return fun, bytes(raw[5:expect - 1])


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

    def _recv_frame_locked(self, deadline, expect_fun=None, expect_addr=None):
        """EIN gültiger Frame unter gehaltenem _lock. Gibt
        (addr, status, fun, daten, rohframe) zurück. Zählt crc_errors.
        Wirft TimeoutError bei abgelaufener Deadline."""
        buf = bytearray(getattr(self, "_rest_buf", b"") or b"")
        self._rest_buf = b""
        last_err = None
        # ggf. liegt bereits ein kompletter Frame im Überhang
        if len(buf) >= 6:
            try:
                rfun0, _ = parse_frame(bytes(buf))
                length0 = buf[2]
                expect0 = length0 + 3
                if len(buf) >= expect0:
                    raw0 = bytes(buf[:expect0])
                    addr0, status0 = raw0[1], raw0[3]
                    if ((expect_fun is None or rfun0 == expect_fun)
                            and (expect_addr is None or addr0 == expect_addr)):
                        _, rdata0 = parse_frame(bytes(buf[:expect0]))
                        self._rest_buf = bytes(buf[expect0:])
                        return addr0, status0, rfun0, rdata0, raw0
            except ValueError:
                pass
        while time.monotonic() < deadline:
            chunk = self.ser.read(64)
            if not chunk:
                continue
            buf += chunk
            try:
                rfun, rdata = parse_frame(bytes(buf))
            except ValueError as e:
                if "CRC" in str(e):
                    self._inc("crc_errors")
                last_err = e
                buf = _resync(buf)
                continue
            length = buf[2]
            expect = length + 3
            raw = bytes(buf[:expect])
            addr, status = raw[1], raw[3]
            if expect_fun is not None and rfun != expect_fun:
                last_err = ValueError("fun-Mismatch: 0x%02x" % rfun)
                buf = _resync(buf)
                continue
            if expect_addr is not None and addr != expect_addr:
                last_err = ValueError("addr-Mismatch: 0x%02x" % addr)
                buf = _resync(buf)
                continue
            rest = bytes(buf[expect:])
            self._rest_buf = rest  # Überhang (Folgeframe) merken
            return addr, status, rfun, rdata, raw
        raise TimeoutError(last_err or "keine Antwort")

    def _transact_locked(self, addr, fun, data, timeout, retries,
                         expect_fun, status, want_full=False):
        """Kern unter gehaltenem _lock. want_full -> (addr,status,fun,daten,roh),
        sonst nur daten. Zählt sent/retries/received/timeouts, dispatchet."""
        expect_fun = fun if expect_fun is None else expect_fun
        frame = build_frame(addr, fun, data, status=status)
        last_err = None
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
                    deadline, expect_fun=expect_fun)
            except TimeoutError as e:
                last_err = e
                continue
            self._inc("received")
            try:
                self._dispatch(raddr, rstatus, rfun, rdata)
            except Exception:
                pass
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

        Stellt sicher, dass der Port offen ist, leert den Empfangspuffer und
        markiert die Session aktiv. Idempotent. Mit start_bg=True wird der
        Hintergrund-Leser (_bg_thread) als Daemon gestartet.
        """
        if not getattr(self, "_connected", False) or getattr(self, "ser", None) is None:
            self.connect_uart()
        else:
            try:
                self.ser.reset_input_buffer()
            except Exception:
                pass
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
        """
        while not self._bg_stop.wait(0.02):
            if not getattr(self, "_connected", False):
                continue
            try:
                with self._lock:
                    deadline = time.monotonic() + timeout
                    try:
                        raddr, rstatus, rfun, rdata, _ = self._recv_frame_locked(deadline)
                    except TimeoutError:
                        continue
                    self._inc("received")
                try:
                    self._dispatch(raddr, rstatus, rfun, rdata)
                except Exception:
                    pass
            except Exception:
                continue

    def raw_send(self, addr, fun, data=(), status=STATUS_OK):
        """Fire-and-forget-Senden (Beleg: Serialhdl_485.raw_send)."""
        frame = build_frame(addr, fun, data, status=status)
        with self._lock:
            self.ser.write(frame)
            self._inc("sent")
        return frame

    def raw_send_wait_ack(self, addr, fun, data=(), timeout=None,
                          retries=MAX_RETRIES, expect_fun=None,
                          status=STATUS_OK):
        """Sendet, wartet auf Antwort mit passendem fun-Code
        (Beleg: Serialhdl_485.raw_send_wait_ack). Gibt Antwort-Daten zurück."""
        timeout = self.default_timeout if timeout is None else timeout
        with self._lock:
            return self._transact_locked(addr, fun, list(data), timeout,
                                         retries, expect_fun, status)

    def raw_send_wait_ack_full(self, addr, fun, data=(), timeout=None,
                               retries=MAX_RETRIES, expect_fun=None,
                               status=STATUS_OK):
        """Wie raw_send_wait_ack, gibt aber (addr, status, fun, daten, rohframe)
        zurück (für Wrapper-Vollframes, vgl. auto_addr data_handler)."""
        timeout = self.default_timeout if timeout is None else timeout
        with self._lock:
            return self._transact_locked(addr, fun, list(data), timeout,
                                         retries, expect_fun, status,
                                         want_full=True)

    def get_response(self, timeout=None, expect_fun=None, expect_addr=None):
        """Liest EINEN gültigen Frame (Beleg: Serialhdl_485.get_response).

        Gibt (addr, status, fun, daten) zurück (daten als bytes).
        Rohframe = build_frame(addr, fun, daten, status=status).
        Wirft TimeoutError nach timeout, zählt crc_errors/received.
        """
        timeout = self.default_timeout if timeout is None else timeout
        with self._lock:
            deadline = time.monotonic() + timeout
            try:
                raddr, rstatus, rfun, rdata, _ = self._recv_frame_locked(
                    deadline, expect_fun=expect_fun, expect_addr=expect_addr)
            except TimeoutError:
                self._inc("timeouts")
                raise
            self._inc("received")
            try:
                self._dispatch(raddr, rstatus, rfun, rdata)
            except Exception:
                pass
            return raddr, rstatus, rfun, rdata

    def register_response(self, addr, fun, callback):
        """Registriert Antwort-Handler für (addr, fun)
        (Beleg: Serialhdl_485.register_response). None = Wildcard.
        callback(addr, status, fun, daten). callback=None löscht."""
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
                 expect_fun=None, status=STATUS_OK):
        """Sendet Frame, wartet auf Antwort mit passendem fun-Code (vgl. raw_send_wait_ack).

        Gibt die Antwort-Daten (bytes) zurück, wirft TimeoutError nach retries.
        Der Empfangspuffer wird auf PACK_HEAD resynchronisiert, damit Müll oder
        halbe Frames vom Bus den Parser nicht blockieren.
        """
        return self.raw_send_wait_ack(addr, fun, data, timeout=timeout,
                                      retries=retries, expect_fun=expect_fun,
                                      status=status)


class Serial_485_Wrapper:
    """High-Level-Wrapper mit Sende-Queue + Response-Registry.

    Entspricht Serial_485_Wrapper (Beleg: serial_485_wrapper.so-Symbole,
    strings-Dump 13.10.2026):
    cmd_send_data_with_response/cmd_485_send_data/add_send_data/
    remove_send_data/send_queue_process/register_response/
    is_exist_in_send_list/handle_callback (+ _connect/_disconnect/stats).

    Wire-Format wie im Live-Betrieb (Beleg: auto_addr_wrapper-live.py
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
        self._seq = 0

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

    def _send_entry(self, entry):
        """Sendet EINEN Queue-Eintrag, gibt Vollframe oder None (Timeout)."""
        addr, _length, status, fun, payload = self._split_wire(entry["data"])
        retries = MAX_RETRIES if entry["retransmit"] else 0
        try:
            _raddr, _rstatus, _rfun, _rdata, rraw = \
                self.bus.raw_send_wait_ack_full(
                    addr, fun, list(payload), timeout=entry["timeout"],
                    retries=retries, expect_fun=fun, status=status)
        except TimeoutError:
            return None
        try:
            self.handle_callback(entry["data"], rraw)
        except Exception:
            pass
        return rraw

    def send_queue_process(self):
        """Arbeitet die Queue FIFO ab, ein Eintrag gleichzeitig
        (Beleg: Serial_485_Wrapper.send_queue_process). Gibt
        [(eintrags-id, antwort-vollframe-oder-None)] in Reihenfolge zurück."""
        results = []
        with self._send_lock:
            while True:
                with self._qlock:
                    if not self._queue:
                        break
                    entry = self._queue.popleft()
                resp = self._send_entry(entry)
                with self._qlock:
                    self._done[entry["id"]] = resp
                results.append((entry["id"], resp))
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
        with self._send_lock:
            with self._qlock:
                if eid in self._done:  # Neben-Thread hat ihn schon erledigt
                    return self._done.pop(eid)
            while True:
                with self._qlock:
                    if not self._queue:
                        return self._done.pop(eid, None)
                    entry = self._queue.popleft()
                resp = self._send_entry(entry)
                with self._qlock:
                    self._done[entry["id"]] = resp
                    if entry["id"] == eid:
                        return self._done.pop(eid)
                    # fremde Antworten liegen für deren Threads in _done

    def cmd_485_send_data(self, data, timeout=1.0):
        """Fire-and-forget (Beleg: Serial_485_Wrapper.cmd_485_send_data).
        Gibt den gesendeten Frame zurück."""
        addr, _length, status, fun, payload = self._split_wire(data)
        return self.bus.raw_send(addr, fun, list(payload), status=status)

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
        (Beleg: Serial_485_Wrapper.handle_callback)."""
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
                    return None
        if self._default_handler is not None:
            try:
                return self._default_handler(bytes(data_send), bytes(msg))
            except Exception:
                pass
        return None

    # --- Lifecycle/Stats ---
    def _connect(self, *a, **k):
        return self.bus.connect_uart(*a, **k)

    def _disconnect(self):
        return self.bus.disconnect()

    def _shutdown(self):
        return self.bus.disconnect()

    def stats(self):
        """Bus-Zähler + queue_len (vgl. serial_485_queue_get_stats)."""
        d = self.bus.stats()
        with self._qlock:
            d["queue_len"] = len(self._queue)
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
    ("f7 01 03 00 02 b3", "GET_RFID Req"),
    ("f7 01 07 00 03 1a 00 ff 00 99", "Remain A=26%"),
    ("f7 01 03 00 10 cd", "EXTRUDE Poll"),
    ("f7 01 03 00 11 ca", "RETRUDE Poll"),
    ("f7 22 10 00 04 03 00 00 00 8a ff f1 63 09 ff ed 12 bd 3c", "belt mdly write_flash"),
]


def selftest():
    fails = 0
    for hexframe, label in _LIVE_FRAMES:
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
    assert _resync(bytearray(b"\x00\x01\x02")) == bytearray()
    print("ok resync ohne Head")
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
        nonlocal fails
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

    print("SELBSTTEST: %d/%d ok" % (len(_LIVE_FRAMES) - fails, len(_LIVE_FRAMES)))
    return fails


if __name__ == "__main__":
    raise SystemExit(selftest())
