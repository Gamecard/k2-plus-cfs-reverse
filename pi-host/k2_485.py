#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2_485.py – gemeinsamer RS485-Bus [k2_485] für Upstream-Klipper (K2 Plus).

Ersetzt die Stock-Abschnitte ``[serial_485 serial485]`` (serial_485_wrapper.so,
/dev/ttyS5, 230400 8N1). Am Stock-Bus hängen ALLE RS485-Teilnehmer:
Closed-loop-Motoren X/Y/Z/Z1 (0x81–0x84), CFS-Box (0x01 …) und die
Riemenspann-Module (0x21/0x22). Halbduplex → es darf genau EIN offener Port
mit EINEM Lock existieren (vgl. nachbau/serial485.Serial485._lock,
k2_transport.Rs485Transport-Docstring).

Dieses Modul stellt genau EINE ``nachbau/serial485.Serial485``-Instanz bereit:

- Objektname ``k2_485``: Klipper legt das von ``load_config`` gelieferte
  Objekt beim LADEN unter dem Abschnittsnamen ab (klippy.py ``load_object``:
  ``self.objects[section] = init_func(...)`` – wirkt wie
  ``printer.add_object('k2_485', …)``). ``[k2_motor]`` lädt es bei Bedarf
  selbst vor (``printer.load_object``), ``[k2_box]``/``[k2_belt]`` suchen es
  beim Connect (``lookup_object('k2_485')``) → Reihenfolge der Abschnitte in
  printer.cfg egal.
- Port öffnen erst bei ``klippy:connect`` (bzw. faul beim ersten Frame, falls
  das Öffnen beim Connect scheiterte). Beim Laden wird NICHTS geöffnet.

Verhalten OHNE Port (dokumentiert, im Test geprüft):
- Batch-/Debug-Modus (``klippy.py -o …``, Start-Argument ``debugoutput``):
  Port wird NIE geöffnet (kein Hardwarezugriff bei Trockenläufen). Meldung im
  Log: ``[k2_485] Batch-Modus: RS485-Port … wird nicht geöffnet``. Jeder
  Bus-Zugriff wirft ``RuntimeError`` mit dieser Begründung.
- Normalbetrieb, Port fehlt/belegt/pyserial fehlt: KEIN Absturz, KEIN
  Shutdown. Beim Connect eine Meldung ``[k2_485] Port … nicht zu öffnen: …``
  (Log + Konsole); ``get_status`` zeigt ``open: False`` + ``error``.
  Jeder Bus-Zugriff versucht das Öffnen erneut (USB-Adapter nachträglich
  eingesteckt) und wirft sonst ``RuntimeError("[k2_485] Port … nicht zu
  öffnen …")`` – die Nutzer-Module melden das als G-Code-Fehler bzw.
  Diagnose-Meldung.
- Bricht der Port im Betrieb weg (``OSError``/``SerialException``, nicht
  ``TimeoutError``), wird er geschlossen und beim nächsten Zugriff neu
  geöffnet.

Config::

    [k2_485]
    serial: /dev/serial/by-id/usb-…-if00-port0   # Pflicht
    baud: 230400                                  # Stock 230400 8N1
    #timeout: 1.0                                 # Default-Timeout je Frame (s)

G-Code: ``KTWO_BUS_STATUS`` (nur Zähler/Zustand, KEIN Bus-Verkehr).

Bus-API (an Serial485 durchgereicht): ``transact``, ``transact_full``,
``raw_send``, ``raw_send_wait_ack``, ``raw_send_wait_ack_full``,
``get_response``, ``register_response``, ``stats`` – dazu ``is_open()``,
``open()``, ``close()``.

Ohne Klipper importierbar (Tests: ``python3 test_k2_485.py``).
Python-3.9-kompatibel.
"""

import logging

# Import-Regel wie k2_transport.py: eigener Ordner NIE in sys.path,
# Geschwister relativ (Klipper-Paket extras), Fallback absolut (Tests).
try:
    from . import k2_transport as _K2T
except ImportError:
    import k2_transport as _K2T

SECTION = "k2_485"
DEFAULT_BAUD = 230400
DEFAULT_TIMEOUT = 1.0   # = serial485.TIMEOUT_LONG

# Test-Haken: callable(port, baud, timeout) -> Serial485-artiges Objekt.
# None = echte nachbau/serial485.Serial485 (pyserial).
SERIAL_FACTORY = None


def _load_serial485():
    """nachbau/serial485 laden (Herkunft geprüft) – ImportError mit Hinweis."""
    return _K2T.import_nachbau("serial485")


class K2Bus485:
    """Gemeinsamer RS485-Bus (genau eine Serial485-Instanz, faul geöffnet)."""

    PASSTHROUGH = ("transact", "transact_full", "raw_send", "raw_send_wait_ack",
                   "raw_send_wait_ack_full", "get_response", "register_response")

    def __init__(self, printer, port, baud=DEFAULT_BAUD, timeout=DEFAULT_TIMEOUT,
                 batch=False, factory=None, respond=None):
        if not port:
            raise ValueError("serial: fehlt")
        self.printer = printer
        self.port = str(port)
        self.baud = int(baud)
        self.timeout = float(timeout)
        self.batch = bool(batch)
        self._factory = factory
        self._respond = respond
        self._bus = None
        self.last_error = None
        self.open_count = 0

    # --- Zustand ------------------------------------------------------
    def is_open(self):
        return self._bus is not None

    def _msg(self, text):
        # gcode.respond_info schreibt selbst ins Log – sonst nur logging.
        if self._respond is not None:
            try:
                self._respond(text)
                return
            except Exception:
                pass
        logging.info(text)

    def open(self):
        """Port öffnen (idempotent). RuntimeError mit klarer Meldung."""
        if self._bus is not None:
            return self._bus
        if self.batch:
            self.last_error = ("Batch-Modus: RS485-Port %s wird nicht geöffnet"
                               % self.port)
            raise RuntimeError("[k2_485] " + self.last_error)
        factory = self._factory or SERIAL_FACTORY
        try:
            if factory is not None:
                bus = factory(self.port, self.baud, self.timeout)
            else:
                s485 = _load_serial485()
                bus = s485.Serial485(self.port, self.baud, timeout=self.timeout)
        except BaseException as exc:  # pyserial fehlt -> SystemExit im Nachbau
            if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
                raise
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            raise RuntimeError("[k2_485] Port %s nicht zu öffnen (%s) – serial: in "
                               "[k2_485] prüfen (ls -l /dev/serial/by-id/)"
                               % (self.port, self.last_error))
        self._bus = bus
        self.last_error = None
        self.open_count += 1
        return bus

    def close(self):
        bus, self._bus = self._bus, None
        if bus is not None and hasattr(bus, "disconnect"):
            try:
                bus.disconnect()
            except Exception:
                pass

    # --- Bus-API (durchgereicht, faul geöffnet) ------------------------
    def _call(self, name, *args, **kwargs):
        bus = self.open()
        try:
            return getattr(bus, name)(*args, **kwargs)
        except TimeoutError:
            raise  # normale Antwort-Zeitüberschreitung, Port bleibt offen
        except OSError as exc:  # SerialException (Adapter gezogen o. ä.)
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            self.close()
            raise RuntimeError("[k2_485] Port %s ausgefallen (%s) – wird beim "
                               "nächsten Zugriff neu geöffnet"
                               % (self.port, self.last_error))

    def transact(self, *args, **kwargs):
        return self._call("transact", *args, **kwargs)

    def transact_full(self, *args, **kwargs):
        return self._call("transact_full", *args, **kwargs)

    def raw_send(self, *args, **kwargs):
        return self._call("raw_send", *args, **kwargs)

    def raw_send_wait_ack(self, *args, **kwargs):
        return self._call("raw_send_wait_ack", *args, **kwargs)

    def raw_send_wait_ack_full(self, *args, **kwargs):
        return self._call("raw_send_wait_ack_full", *args, **kwargs)

    def get_response(self, *args, **kwargs):
        return self._call("get_response", *args, **kwargs)

    def register_response(self, *args, **kwargs):
        return self._call("register_response", *args, **kwargs)

    def stats(self):
        """Zähler der offenen Instanz (ohne Port: leer) – kein Bus-Verkehr."""
        if self._bus is None or not hasattr(self._bus, "stats"):
            return {}
        return dict(self._bus.stats())

    # --- Klipper-Ereignisse ---------------------------------------------
    def handle_connect(self):
        if self.batch:
            self.last_error = ("Batch-Modus: RS485-Port %s wird nicht geöffnet"
                               % self.port)
            self._msg("[k2_485] " + self.last_error)
            return
        try:
            self.open()
            self._msg("[k2_485] RS485-Port %s offen (%d Baud)" % (self.port, self.baud))
        except RuntimeError as exc:
            # KEIN Absturz: Nutzer-Module melden Fehler beim Zugriff.
            self._msg("%s – Motoren/CFS/Belt ohne Bus, Zugriffe melden Fehler"
                      % exc)

    def handle_disconnect(self):
        self.close()

    def get_status(self, eventtime=None):
        return {"serial": self.port, "baud": self.baud, "open": self.is_open(),
                "batch": self.batch, "error": self.last_error,
                "stats": self.stats()}

    def status_text(self):
        st = self.get_status()
        return ("[k2_485] serial=%s baud=%d offen=%s batch=%s fehler=%s stats=%s"
                % (st["serial"], st["baud"], st["open"], st["batch"],
                   st["error"], st["stats"]))


class PrinterK2Bus485(K2Bus485):
    """Klipper-Objekt [k2_485]."""

    def __init__(self, config):
        printer = config.get_printer()
        port = config.get("serial")
        baud = config.getint("baud", DEFAULT_BAUD, minval=1200)
        timeout = config.getfloat("timeout", DEFAULT_TIMEOUT, above=0.)
        batch = False
        try:
            batch = printer.get_start_args().get("debugoutput") is not None
        except Exception:
            pass
        # nachbau/serial485 muss importierbar sein (sonst Config-Fehler beim
        # Laden statt Überraschung beim Connect) – außer mit Test-Factory.
        if SERIAL_FACTORY is None:
            try:
                _load_serial485()
            except ImportError as exc:
                raise config.error("[k2_485] %s" % exc)
        self.gcode = printer.lookup_object("gcode")
        K2Bus485.__init__(self, printer, port, baud=baud, timeout=timeout,
                          batch=batch, respond=self._respond_console)
        printer.register_event_handler("klippy:connect", self.handle_connect)
        printer.register_event_handler("klippy:disconnect", self.handle_disconnect)
        self.gcode.register_command("KTWO_BUS_STATUS", self.cmd_status,
                                    desc="RS485-Bus [k2_485]: Zustand/Zähler "
                                         "(kein Bus-Verkehr)")

    def _respond_console(self, text):
        self.gcode.respond_info(text)

    def cmd_status(self, gcmd):
        gcmd.respond_info(self.status_text())


def load_config(config):
    """Klipper-Einstieg [k2_485]: Rückgabe = Objekt 'k2_485'."""
    return PrinterK2Bus485(config)
