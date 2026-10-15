#!/usr/bin/env python3
"""box.py – Python-Nachbau von box_wrapper ParseData/BoxState/BoxAction (CFS-Box, K2 Plus).

Erweiterung von boxmini.py (NICHT ändern): boxmini.py deckt RFID + Restmenge
als BoxMini-Klasse ab; diese Datei ergänzt sinngemäß nach API-box_wrapper.md:

1) ParseData (reine Funktionen, keine Hardware):
   get_rfid / get_remain_len / get_measuring_wheel (BE-Float via struct) /
   get_box_status, dazu belegt: parse_version_sn (ASCII "153"+SN),
   parse_buffer_state / parse_hardware_status (je 1 Byte),
   split_rfid_vendor (40-Zeichen-vender -> Felder, ab 15.10.2026).
2) BoxState: Tn-Datenmodell im Speicher (tn_map/tnn_map/save_data als dicts,
   generate/get/modify/update-Methoden sinngemäß nach API-Doku).
3) BoxAction: belegte Einzel-Step-Sequenz-Builder (Preload enable/disable/
   force-read/force-tension-only,
   Feed-Mode enter, Auto-Preload-Sperre (0x0f, früher „Engage/Release“),
   Print-Mode) + bus-gebundene Klasse.

GHIDRA 15.10.2026: Semantik der CFS-App R153 (= FW in der Box des Users)
belegt – GHIDRA-CFS-BEFUND-2026-10-15.md, PROTOKOLL-485.md „Func-Tabelle
CFS-App R153“. Daraus hier: STATUS_* / STATUS_NAMES, BOX_STATE_*,
parse_box_state, Preload op 3 + langer Timeout, 0x0f-Umbenennung,
EXTRUDE_MODE_* (Re-Export aus cfsseq).

Bus-Anbindung: serial485.Serial485-Instanz wird übergeben (echte Hardware,
pyserial). Der Selbsttest läuft OHNE Hardware (FakeBus + Live-Frames).

Belege (alle in /home/pro/k2-fw-live-1.1.7.0/):
- Func-Codes + Frame-Format + CRC-8/SMBUS poly 0x07 über [len..daten]:
  PROTOKOLL-485.md, PROTOKOLL-GESAMT.md, API-box_wrapper.md.
- Operational-Requests: STATUS 0xFF, Timeout 2,0 s (Live-Introspektion
  send_data state=0xff, communication_get_rfid/remain payload [Maske]).
- Slots als 1-hot-Maske A=0x01/B=0x02/C=0x04/D=0x08, Default 0x0f = alle.
- Extrude/Retrude-Stufen-Sequenzen stehen bewusst NICHT hier, sondern in
  nachbau/cfsseq.py (dort belegt) – hier kein Duplikat, nur Verweis.

PRINZIP: Nichts erfinden. Was nicht auf dem Bus belegt ist, ist als TODO
markiert (insbesondere Tn-Innensemantik und .so-Entscheidungslogik).
Kein Netzwerk (Drucker-IP tabu), keine Hardware im Selbsttest.
"""

import struct

import serial485
from serial485 import Serial485  # noqa: F401 (für echten Betrieb)

# --- Protokoll-Konstanten (Beleg: PROTOKOLL-485.md + API-box_wrapper.md) ---
BOX_ADDR = 0x01
STATUS_OPERATIONAL = 0xFF
STATUS_REPLY = 0x00
TIMEOUT_OPERATIONAL = 2.0
NO_DATA = 0xFF
SLOT_KEYS = "ABCD"  # NUM 0..3 -> A/B/C/D
SLOT_MASKS = (0x01, 0x02, 0x04, 0x08)
MASK_ALL = 0x0F

FUNC_GET_RFID = 0x02
FUNC_GET_REMAIN_LEN = 0x03
FUNC_SET_BOX_MODE = 0x04
FUNC_GET_BUFFER_STATE = 0x05
FUNC_GET_HARDWARE_STATUS = 0x08
FUNC_GET_BOX_STATE = 0x0A
FUNC_SET_PRE_LOADING = 0x0D
FUNC_MEASURING_WHEEL = 0x0E
# 0x0f: ~~Motor-Engage/Release~~ WIDERLEGT 15.10.2026 (Ghidra, inline im
# Dispatcher FUN_0800f27c): Auto-Preload-Sperr-Flag S[8] (0x200037d8),
# x in {0,1} -> Status 0, sonst 1; S[8]!=0 sperrt das automatische Preload
# (FUN_080053b0). KEINE Motor-Aktion. Alter Name bleibt als Alias.
FUNC_PRELOAD_INHIBIT = 0x0F
FUNC_MOTOR_ACTION = FUNC_PRELOAD_INHIBIT  # Alias (alter Name, kompatibel)
FUNC_GET_VERSION_SN = 0x14
# 0x10/0x11 (Extrude/Retrude): siehe cfsseq.py, hier nur als Konstanten zum
# Referenzieren (keine eigenen Builder – kein Duplikat).
FUNC_EXTRUDE = 0x10
FUNC_RETRUDE = 0x11
# 0x10-Modi / 0x11-Parameter: definiert in cfsseq.py (belegt, Ghidra
# FUN_080045c6 / FUN_08004d90), hier nur re-exportiert – kein Duplikat.
from cfsseq import (  # noqa: E402,F401
    EXTRUDE_MODE_STAGE1, EXTRUDE_MODE_FEED_TO_STALL, EXTRUDE_MODE_FEED_CHECKED,
    EXTRUDE_MODE_CLEAR_ERROR, EXTRUDE_MODE_FEED_START, EXTRUDE_MODE_QUERY,
    EXTRUDE_MODE_STOP_BUFFER, EXTRUDE_MODE_EXTRUDER_CHECK, EXTRUDE_MODES,
    EXTRUDER_CHECK_MM, RETRUDE_P_TO_BUFFER, RETRUDE_P_PULL_HUB)

# --- Status-Byte der ANTWORTEN (BELEGT 15.10.2026, Ghidra CFS-FW R153;
# GHIDRA-CFS-BEFUND-2026-10-15.md §5). Nur Bereitstellung – die Auswertung
# in _response_data bleibt TODO (keine Logikänderung).
# Hinweis: das Status-Byte im REQUEST ignoriert die App (Dispatcher liest
# frame[2] nie) – STATUS_OPERATIONAL 0xFF bleibt aus Kompatibilität.
STATUS_OK = 0x00
STATUS_INVALID = 0x01        # len/Maske/Subcode ungültig, fun unbekannt (FUN_0800ebc2/eb88)
STATUS_CRC = 0x02            # CRC-Fehler im Request (FUN_0800f880)
STATUS_BUSY = 0x03           # busy / falscher Box-Zustand; 0x50: Wert unverändert
STATUS_FEED_OUT = 0x05       # cmd_parse_out: Feeder-Out-Fehler (FUN_08004df2)
STATUS_BUFFER_FILL_TIMEOUT = 0x06  # buffer_fill_timeout (FUN_08004df2)
STATUS_PRETENSION = 0x08     # Pretension-Warten erfolglos (FUN_08004460)
STATUS_HUB_NO_MOVE = 0x09    # feed_top_to_hub: Odometer bewegt sich nicht (FUN_08004190)
STATUS_FEED_TIMEOUT = 0x0A   # 25-s-Timeout bzw. <5 mm in 3 s (FUN_08025c4e/08003efa)
STATUS_FEED_MAX_LEN = 0x0B   # Fördern > 3000 mm
STATUS_ODOMETER_STALL = 0x0C  # Odometer-Stillstand 500 ms
STATUS_BUFFER_NOT_FULL = 0x0D  # Buffer nicht voll (FUN_08025d96)
STATUS_EXTRUDER_TIMEOUT = 0x0E  # feed extruder timeout, 0x10 mode 7 (FUN_08025dea)
STATUS_RETRACT_BUFFER_TIMEOUT = 0x13  # Rückzug: Buffer-Sensor 5 s (FUN_08004c9a)
STATUS_PULL_HUB_TIMEOUT = 0x14  # pull back hub PE timeout (FUN_08004884)
STATUS_NO_FILAMENT = 0x16    # Slot ohne Filament (FUN_08004d4a/0800e81a), setzt ERROR
STATUS_PULL_HUB_SENSOR = 0x17  # pull_back_hub: Sensor A nicht aktiv (FUN_08004c5a)
STATUS_NO_SLOT_INTERNAL = 0x18  # intern, wird auf 0x16 abgebildet
STATUS_PULL_HUB_BLOCK = 0x19  # pull_back_hub motor block (FUN_0800494e)
STATUS_PRELOAD_EVENT = 0x30  # NUR 0x0a: Preload-Ereignis anstehend (+4 B)
STATUS_PRINT_SENSOR = 0x50   # NUR 0x0a in STATE_PRINT: Sensorprüfung != 0 (Deutung offen)
STATUS_NAMES = {
    STATUS_OK: "ok",
    STATUS_INVALID: "ungültig (len/Maske/Subcode/fun)",
    STATUS_CRC: "CRC-Fehler im Request",
    STATUS_BUSY: "busy / falscher Zustand",
    STATUS_FEED_OUT: "Feeder-Out-Fehler",
    STATUS_BUFFER_FILL_TIMEOUT: "buffer_fill_timeout",
    STATUS_PRETENSION: "Pretension erfolglos",
    STATUS_HUB_NO_MOVE: "feed_top_to_hub: Odometer steht",
    STATUS_FEED_TIMEOUT: "Förder-Timeout (25 s / <5 mm in 3 s)",
    STATUS_FEED_MAX_LEN: "Fördern > 3000 mm",
    STATUS_ODOMETER_STALL: "Odometer-Stillstand 500 ms",
    STATUS_BUFFER_NOT_FULL: "Buffer nicht voll",
    STATUS_EXTRUDER_TIMEOUT: "feed extruder timeout",
    STATUS_RETRACT_BUFFER_TIMEOUT: "Rückzug: Buffer-Sensor-Timeout",
    STATUS_PULL_HUB_TIMEOUT: "pull back hub PE timeout",
    STATUS_NO_FILAMENT: "Slot ohne Filament",
    STATUS_PULL_HUB_SENSOR: "pull_back_hub: Sensor A nicht aktiv",
    STATUS_NO_SLOT_INTERNAL: "kein Slot belegt (intern)",
    STATUS_PULL_HUB_BLOCK: "pull_back_hub motor block",
    STATUS_PRELOAD_EVENT: "Preload-Ereignis (nur 0x0a)",
    STATUS_PRINT_SENSOR: "Sensorprüfung in PRINT (nur 0x0a)",
}


def status_name(code):
    """Klartext zu einem Antwort-Status-Byte (unbekannt -> 'unbekannt 0x..')."""
    return STATUS_NAMES.get(code, "unbekannt 0x%02x" % code)


# --- Box-Zustand (0x0a Byte 3), BELEGT 15.10.2026 (Ghidra FUN_0800fa0c/
# FUN_0800ed26). STATE_CHECK_HUB ist KEIN Box-Zustand, sondern Zustand 2 der
# Preload-Statemachine FUN_080064b8 (taucht in 0x0a nicht auf).
BOX_STATE_IDLE = 0
BOX_STATE_PRELOAD = 1
BOX_STATE_PRINT = 2
BOX_STATE_RELOAD = 3
BOX_STATE_ERROR = 4
BOX_STATE_TEST = 5
BOX_STATE_NAMES = {
    BOX_STATE_IDLE: "STATE_IDLE", BOX_STATE_PRELOAD: "STATE_PRELOAD",
    BOX_STATE_PRINT: "STATE_PRINT", BOX_STATE_RELOAD: "STATE_RELOAD",
    BOX_STATE_ERROR: "STATE_ERROR", BOX_STATE_TEST: "STATE_TEST",
}

# Preload-/RFID-Control-Aktion (0x0d, 2. Datenbyte). KORREKTUR 15.10.2026:
# Die frühere Belegung 00=Arm/01=Disarm/02=Rearm (PROTOKOLL-485.md:98) ist
# durch KEINEN Live-Frame gestützt (live nur "0x0d len 3" belegt; die ACK-Bytes f7 01 03 00 0d 9e stammen aus ref-hi
# und sind nicht live gesehen; kein 0x0d-Request mitgeschnitten). Beide unabhängigen
# Referenzen sagen übereinstimmend das Gegenteil:
# - nachbau/ref-hi/valid_messages.json: f70105ff0d0f0169 =
#   "CMD_SET_PRE_LOADING box 1, mask=0x0F enable=0x01"
# - k2-vanilla files/box_protocol.py (CMD_RFID_CONTROL=0x0D):
#   set_rfid_insert_reading(enabled) -> (0, int(enabled)),
#   force_rfid_read(mask) -> (mask, 2)
# Daher: 0x00 = Insert-Reading/Preload aus, 0x01 = an, 0x02 = Force-Read.
# ~~TODO (unbelegt): ob das 1. Byte bei 0x00/0x01 die Maske ist (ref-hi: 0x0F)
#   oder ignoriert wird (vanilla: 0)~~ BELEGT 15.10.2026 (Ghidra, Handler
#   FUN_0800f0da @CFS-App R153): bei op 0/1 wird die Maske IGNORIERT
#   (FUN_08005376 disable / FUN_0800536e enable wirken global); die
#   übergebene Maske wird trotzdem unverändert gesendet (ref-hi-Vektor).
# op 2 = FUN_08004ec4(mask, 1): Forced Preload MIT RFID (pretension ->
#   STATE_CHECK_HUB -> read rfid + margin estimation) = Force-Read.
# op 3 = FUN_08004ec4(mask, 0): Forced Preload OHNE RFID (nur pretension) – NEU.
# op > 3 -> Status 1. Antwort bei op 2/3 erst NACH dem Vorgang (s.
#   preload_force_timeout).
PRELOAD_DISABLE = 0x00
PRELOAD_ENABLE = 0x01
PRELOAD_FORCE_READ = 0x02
PRELOAD_FORCE_RFID = PRELOAD_FORCE_READ  # Alias (Ghidra-Name)
PRELOAD_FORCE_TENSION_ONLY = 0x03
PRELOAD_ACTIONS = (PRELOAD_DISABLE, PRELOAD_ENABLE, PRELOAD_FORCE_READ,
                   PRELOAD_FORCE_TENSION_ONLY)
PRELOAD_FORCE_ACTIONS = (PRELOAD_FORCE_READ, PRELOAD_FORCE_TENSION_ONLY)

# Laufzeit Forced Preload (BELEGT 15.10.2026, Ghidra): FUN_08004ec4 wartet in
# Zustand PRELOAD bis 179 s (0xb3 x 1 s) auf ein laufendes Preload bzw. zieht
# in IDLE erst alle Slots am Hub zurück (FUN_08004cd0(4,1)); dann je Slot der
# Maske NACHEINANDER FUN_08004e80: 5 s + bis 180 s Polling (0xb4 x 1 s).
# Die 0x0d-Antwort kommt erst danach. -> Timeout = Vorlauf + je Slot.
PRELOAD_FORCE_TIMEOUT_BASE = 180.0      # s, Vorlauf (Warten/Pull-back)
PRELOAD_FORCE_TIMEOUT_PER_SLOT = 200.0  # s, 5 s + 180 s + Reserve je Slot


def preload_force_timeout(mask):
    """Host-Timeout (s) für 0x0d op 2/3: Vorlauf + 200 s je Slot der Maske
    (mind. 1 Slot). Beispiel Maske 0x01 -> 380 s, 0x0F -> 980 s."""
    n = bin(check_mask(mask)).count("1")
    return PRELOAD_FORCE_TIMEOUT_BASE + PRELOAD_FORCE_TIMEOUT_PER_SLOT * max(1, n)


def check_mask(mask):
    """Slotmaske 0x00..0x0f prüfen und zurückgeben (Mehrfachmasken erlaubt).

    Wire-Parameter wie communication_get_rfid(addr, num) sind im Original
    eine MASKE (API-box_wrapper.md: num=1 -> data b'\\x01', num=15 -> b'\\x0f'),
    keine Slotnummer – es findet KEINE Umdeutung statt.
    """
    if isinstance(mask, bool) or not isinstance(mask, int) \
            or not 0 <= mask <= MASK_ALL:
        raise ValueError("Maske muss int 0x00..0x0f sein: %r" % (mask,))
    return mask


def slot_to_mask(index):
    """Slot-Index 0..3 (A..D) -> 1-hot-Maske 0x01/0x02/0x04/0x08."""
    if isinstance(index, bool) or index not in range(4):
        raise ValueError("Slot-Index muss 0..3 sein: %r" % (index,))
    return SLOT_MASKS[index]


def _is_full_frame(raw):
    """Voll-Frame erkennen: Head 0xF7, Mindestlänge 6 UND Längenfeld passt
    exakt zur Gesamtlänge (len + 3). Rohdaten, die zufällig mit 0xF7
    beginnen, gelten damit nur als Frame, wenn auch das Längenfeld stimmt;
    die CRC prüft danach parse_frame (Fehler -> ValueError).
    TODO: Restmehrdeutigkeit (Rohdaten mit 0xF7 + passendem Längenbyte) ist
      nur durch getrennte API (Frame vs. Rohdaten) restlos lösbar.
    """
    return (len(raw) >= 6 and raw[0] == serial485.PACK_HEAD
            and raw[2] + 3 == len(raw))


def _response_data(msg, expect_fun):
    """Hilfsfunktion: Voll-Frame oder Rohdaten -> Antwort-Daten (bytes).

    Voll-Frame (s. _is_full_frame) wird per serial485.parse_frame
    CRC-geprüft und der fun-Code verglichen. Sonst gilt msg als bereits
    extrahierte Rohdaten (z.B. Rückgabe von Serial485.transact).
    TODO (offen): Status-Byte raw[3] wird NICHT ausgewertet (Fehler-/Busy-
      Codes ≠ 0x00, vgl. vanilla STATUS_*), Antwort wird trotzdem geparst.
    """
    raw = bytes(msg)
    if _is_full_frame(raw):
        fun, data = serial485.parse_frame(raw)
        if fun != expect_fun:
            raise ValueError("fun-Mismatch: 0x%02x, erwartet 0x%02x"
                             % (fun, expect_fun))
        return data
    return raw


# =====================================================================
# 1) ParseData – reine Funktionen (Signaturen sinngemäß nach API-Doku:
#    get_rfid(msg); get_remain_len/get_measuring_wheel(data_send, msg);
#    get_box_status(msg)).
# =====================================================================

def get_rfid(msg):
    """RFID-Antwort parsen (ASCII, ';'-getrennt, Key=Slot-Buchstabe).

    Belegt (Live 12.10.2026): b"A:<hex>;" / b"B:unknown;" / b"C:busy;".
    msg = Voll-Frame (fun 0x02, CRC-geprüft) oder Rohdaten.
    Gibt dict {Slot-Key: Wert} zurück. Leere Antwort -> {}.
    Je Slot-Key wird der Wert bis zum nächsten ';' übernommen; weitere
    ';'-Felder ohne gültigen Slot-Key ("X:..." mit X in A..D) werden
    VERWORFEN, ebenso Felder ohne ':'.
    ERLEDIGT 15.10.2026 (vorher TODO): nach dem vender-Hex folgen KEINE
      weiteren Felder (Frame live vollständig, 49 Tokens/CRC ok). Farbe und
      Material stecken IM 40-Zeichen-vender selbst -> split_rfid_vendor();
      remain kommt separat über 0x03.
    """
    data = _response_data(msg, FUNC_GET_RFID)
    result = {}
    for part in data.decode("ascii", "replace").split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, _, value = part.partition(":")
        key, value = key.strip(), value.strip()
        if len(key) == 1 and key in SLOT_KEYS:
            result[key] = value
    return result


# Feldlayout des 40-Zeichen-RFID-vender-Strings (Reihenfolge + Breite).
# Quelle: fake-name/cfs-reverse-engineering firmware.md:340-352 (Feldnamen
# aus dem printf-Format der CFS-FW, month=%c -> 1 Zeichen, Rest %.*s) und
# firmware.md:521-523 (Beispiel-String, 11+13+16 Zeichen = 40). Breiten
# daraus abgeleitet + gegen unseren Live-Query geprüft (mat 101001 und
# col 0FFFFFF stehen exakt an Pos. 11..16 bzw. 17..23). Belegstärke:
# mittel-stark (Grenzen konsistent; Datums-/Supplier-Semantik unbelegt).
RFID_VENDOR_FIELDS = (
    ("month", 1), ("day", 2), ("year", 2), ("supplier", 4), ("batch", 2),
    ("mat_id", 6), ("col", 7),
    ("len", 4), ("number", 6), ("reserve", 6),
)
RFID_VENDOR_LEN = 40


def split_rfid_vendor(s):
    """40-Zeichen-RFID-vender-String in seine Felder zerlegen (rein, kein Bus).

    s = str oder bytes (ASCII), exakt 40 Zeichen, sonst ValueError.
    Gibt dict {month, day, year, supplier, batch, mat_id, col, len, number,
    reserve} mit den Roh-Teilstrings zurück – KEINE Interpretation (z.B.
    mat_id 101001 = Hyper PLA laut Live-Query/Display, col 0FFFFFF = weiß;
    Einheit von len und Datumskodierung sind unbelegt).
    """
    if isinstance(s, (bytes, bytearray)):
        s = bytes(s).decode("ascii")
    if not isinstance(s, str):
        raise ValueError("vender muss str/bytes sein: %r" % (s,))
    if len(s) != RFID_VENDOR_LEN:
        raise ValueError("vender muss %d Zeichen haben, hat %d: %r"
                         % (RFID_VENDOR_LEN, len(s), s))
    out, pos = {}, 0
    for name, width in RFID_VENDOR_FIELDS:
        out[name] = s[pos:pos + width]
        pos += width
    return out


def get_remain_len(data_send, msg):
    """Restmengen-Antwort parsen: 4 Bytes positional A,B,C,D (0-100 %).

    Belegt: Live-Frame f7 01 07 00 03 1c ff 00 00 11 -> A=28 %, B=n/a, C/D=0;
    0xff = kein Wert (nicht ausgewählt / keine Daten), 0x00 s. ACHTUNG.
    data_send = Request-Kontext (Slotmaske, vgl. .so-Signatur) – wird hier
      nur entgegengenommen/dokumentiert, nicht zur Filterung benutzt
      (TODO: ob das .so danach filtert, ist unbelegt).
    msg = Voll-Frame (fun 0x03) oder 4 Rohbytes; andere Länge -> ValueError.
    Gibt dict {Slot-Key: Prozent oder None} zurück.
    ACHTUNG Semantik: 0 heißt NICHT sicher „leer“ – live hat Slot B mit
      remain 0 (Vendor unknown, kein RFID-Restwert) 160 mm gefördert
      (PROTOKOLL-485.md, Abschnitt T1B). 0 = „kein RFID-Restwert“.
    PRÄZISIERT 15.10.2026 (Ghidra FUN_0800ef66): Wire-0xff = nicht in Maske /
      Slot preloadet / Filament da OHNE RFID-Daten (Live B:unknown -> 0xff ✓);
      Wire-0 = Sensor A meldet kein Filament ODER gespeicherter Wert 0 %.
      „remain 0“ bei T1B war ein Host-Zustand, kein Wire-Byte. Parsing
      unverändert (Werte sind mehrdeutig, None/0 bleiben wie geliefert).
    """
    data = _response_data(msg, FUNC_GET_REMAIN_LEN)
    if len(data) != 4:
        raise ValueError("Remain-Antwort muss 4 Bytes haben: %r" % (bytes(data),))
    return {key: (None if data[i] == NO_DATA else data[i])
            for i, key in enumerate(SLOT_KEYS)}


def get_measuring_wheel(data_send, msg):
    """Messrad-Antwort parsen: 4 Bytes Big-Endian IEEE-754 Float, NEGATIV.

    Belegt/KORREKTUR 13.10.: c4e6fe0b -> -1847,94; c5009149 -> -2057,08 (KORREKTUR 15.10.: vorher Tippfehler c5019149/-2073,08);
    c52a4e54 -> -2724,89 (Klippy loggt data_hex -> Float selbst). Der Betrag
    wächst beim Fördern; kein Zähler (vgl. PROTOKOLL-485.md, gitstonelabs).
    data_send = Request-Kontext (belegt [01]) – wie bei remain nur Kontext.
    msg = Voll-Frame (fun 0x0e) oder 4 Rohbytes. Sonst ValueError.
    """
    data = _response_data(msg, FUNC_MEASURING_WHEEL)
    if len(data) != 4:
        raise ValueError("Messrad-Antwort muss 4 Bytes haben: %r" % (bytes(data),))
    (value,) = struct.unpack(">f", bytes(data))
    return value


def get_box_status(msg):
    """Box-State/Herzschlag parsen (Func 0x0a, alle ~4 s).

    Belegt: f7 01 09 00 0a 13 34 00 00 00 00 d7 (6 Datenbytes);
    Variante f7 01 07 30 0a 03 00 00 00 51 (4 Datenbytes, Status 0x30).
    ~~Semantik der Einzelbytes ist UNBELEGT~~ BELEGT 15.10.2026 (Ghidra
    FUN_0800ed26) – benannte Felder liefert parse_box_state(); diese
    Funktion bleibt generisch (b0..bN, unverändert).
    Gibt dict {raw, len, frame_status (None bei Rohdaten), b0..bN} zurück.
    """
    raw = bytes(msg)
    frame_status = None
    if _is_full_frame(raw):
        frame_status = raw[3]
        data = _response_data(raw, FUNC_GET_BOX_STATE)
    else:
        data = raw
    out = {"raw": bytes(data), "len": len(data), "frame_status": frame_status}
    for i, byte in enumerate(bytes(data)):
        out["b%d" % i] = byte
    return out


def parse_box_state(msg):
    """0x0a-Antwort mit Namen (BELEGT 15.10.2026, Ghidra FUN_0800ed26).

    Normal (6 Datenbytes): b0 = S[1], b1 = S[0] (Quelle offen; live 0x13
    0x34, = 0x52-Antwort), b2 = Flags (R[3]|0x80 einmalig bzw. R[5]),
    b3 = Box-Zustand (BOX_STATE_*), b4 = Bitmaske Sensor B, b5 = Bitmaske
    „Slot hat gültige RFID-Daten“ (Deutung). Frame-Status: ERROR -> S[3]
    (gespeicherter Fehlercode), PRINT -> 0x50 oder 0, sonst 0.
    Ereignis (Status 0x30, 4 Datenbytes): je Slot A..D ein Ereigniswert
    (1/2/3 aus FUN_080065f4, Bedeutung offen) – dann KEIN Zustandsbyte.
    Rohdaten ohne Frame: Layout nach Länge (6 -> normal, 4 -> Ereignis).
    Gibt dict zurück: kind ('state'/'event'/'unknown'), frame_status,
    status_name, raw, und je nach kind die benannten Felder.
    """
    base = get_box_status(msg)
    data, fst = base["raw"], base["frame_status"]
    out = {"raw": data, "frame_status": fst,
           "status_name": None if fst is None else status_name(fst)}
    if (fst == STATUS_PRELOAD_EVENT or fst is None) and len(data) == 4:
        out["kind"] = "event"
        out["events"] = {k: data[i] for i, k in enumerate(SLOT_KEYS)}
    elif len(data) == 6 and fst != STATUS_PRELOAD_EVENT:
        out["kind"] = "state"
        out.update({"s1": data[0], "s0": data[1], "flags": data[2],
                    "state": data[3],
                    "state_name": BOX_STATE_NAMES.get(data[3], "unknown"),
                    "sensor_b_mask": data[4], "rfid_valid_mask": data[5]})
        if data[3] == BOX_STATE_ERROR and fst is not None:
            out["error_code"] = fst
    else:
        out["kind"] = "unknown"
    return out


def parse_version_sn(msg):
    """Version/SN-Antwort parsen: ASCII "153"+SN (Func 0x14).

    Belegt (K2 13.10.): Voll-Frame f7 01 19 00 14 ... d4, Daten ASCII
      "153"+"SN-DEMO-1234567890X" = CFS-App-Version + SN, CRC ok.
    Hi-Request belegt: f7 01 03 ff 14 06 (STATUS 0xff, leer).
    Gibt (app_version, sn) zurück, z.B. ("153", "SN-DEMO-1234567890X").
    TODO: ob "153" je CFS-Typ variiert (cfs6_222?) – unbelegt.
    """
    data = _response_data(msg, FUNC_GET_VERSION_SN)
    text = bytes(data).decode("ascii", "replace")
    if len(text) < 3:
        raise ValueError("Versions-Antwort zu kurz: %r" % (text,))
    return text[:3], text[3:]


def parse_buffer_state(msg):
    """Buffer-State-Antwort parsen (Func 0x05, Request leer, 1 Byte).

    Belegt: f7 01 04 00 05 00 19 (Daten 0x00). Gibt int 0..255 zurück.
    TODO: Wertesemantik (Buffer voll/leer/Position) ist unbelegt –
      frühere CUT_STATE-Bezeichnung war falsch (vgl. PROTOKOLL-485.md).
    """
    data = _response_data(msg, FUNC_GET_BUFFER_STATE)
    if len(data) != 1:
        raise ValueError("Buffer-Antwort muss 1 Byte haben: %r" % (bytes(data),))
    return data[0]


def parse_hardware_status(msg):
    """Hardware-Status-Antwort parsen (Func 0x08, Request [Kanal], 1 Byte).

    Request-Kanal belegt (High-Level flankiert 0x10 mit 0x08 [0x00]/[0x01],
    vgl. cfsseq.py); Antwort-EinByte belegt. Gibt int zurück.
    TODO: Kanal- und Bit-Semantik (Toolhead-Sensoren) unbelegt.
    """
    data = _response_data(msg, FUNC_GET_HARDWARE_STATUS)
    if len(data) != 1:
        raise ValueError("Hardware-Antwort muss 1 Byte haben: %r" % (bytes(data),))
    return data[0]


# =====================================================================
# 2) BoxState – Tn-Datenmodell (in-memory, sinngemäß nach API-Doku).
# =====================================================================

class BoxState:
    """Tn/Tnn-Zustand der CFS-Box (reines Datenmodell, kein Bus).

    Modell-Annahme (eigene, dokumentiert – .so-Innenleben ist TODO):
    - Tn = Box-Ebene, Schlüssel (addr, part, num).
    - Tnn = Box+Slot-Ebene, Schlüssel "T{addr}{Slotbuchstabe}" (live: T1A,
      T1B, T1D; vgl. PROTOKOLL-485.md Extrude-Matrix).
    - tn_map: {(addr, part, num): Tn-Dict}, tnn_map: {Tnn-Key: Content-Dict},
      save_data: {(addr, part, num): Save-Dict} (Save-Layer).
    Die Methoden heißen wie die .so-Fläche (generate/get/modify/update…);
    was das .so zusätzlich tut (Validierung, Events, Persistenz via BoxSave),
    ist TODO und hier bewusst nicht nachgebaut.
    """

    def __init__(self):
        self.state_init()

    # -- Lifecycle ----------------------------------------------------
    def state_init(self):
        """Zustand leeren (vgl. BoxState.state_init)."""
        self.tn_map = {}
        self.tnn_map = {}
        self.save_data = {}
        self.box_mode = None  # vgl. get_box_mode (TODO: Moduswerte)
        self.color_count = {}  # vgl. clear_color_count
        self.e_err = {}  # vgl. clear_e_err / e_err_set (TODO: Codes)

    # -- Tn-Ebene ------------------------------------------------------
    @staticmethod
    def _tn_key(addr, part, num):
        return (int(addr), int(part), int(num))

    def generate_Tn_data(self, addr, part, num, subpart=0, uppart=0, data=None):
        """Tn-Eintrag erzeugen (vgl. generate_Tn_data/inner_data).

        subpart/uppart stehen in der API-Signatur (modify_Tn_inner_data) –
        ihre .so-Semantik ist TODO; sie werden nur abgelegt.
        """
        return {"addr": int(addr), "part": int(part), "num": int(num),
                "subpart": int(subpart), "uppart": int(uppart),
                "data": dict(data) if data else {}}

    def get_Tn_data(self, addr, part, num):
        """Tn-Eintrag lesen (None wenn unbekannt)."""
        return self.tn_map.get(self._tn_key(addr, part, num))

    def modify_Tn_data(self, addr, part, num, **fields):
        """Tn-Eintrag anlegen/aktualisieren (vgl. modify_Tn_data)."""
        key = self._tn_key(addr, part, num)
        entry = self.tn_map.get(key)
        if entry is None:
            entry = self.generate_Tn_data(addr, part, num)
            self.tn_map[key] = entry
        entry.update(fields)
        return entry

    def get_Tn_inner_data(self, addr, part, num, subpart):
        """Tn-Innendaten lesen (vgl. get_Tn_inner_data; TODO-Semantik)."""
        entry = self.get_Tn_data(addr, part, num)
        if entry is None:
            return None
        return entry["data"].get(subpart)

    def modify_Tn_inner_data(self, addr, part, num, subpart, value, uppart=0):
        """Tn-Innendaten schreiben (vgl. modify_Tn_inner_data)."""
        key = self._tn_key(addr, part, num)
        entry = self.tn_map.get(key)
        if entry is None:
            entry = self.generate_Tn_data(addr, part, num, subpart=subpart,
                                          uppart=uppart)
            self.tn_map[key] = entry
        entry["data"][subpart] = value
        return entry

    def generate_Tn_map(self):
        """Kopie der Tn-Map (vgl. generate_Tn_map)."""
        return {k: {"addr": v["addr"], "part": v["part"], "num": v["num"],
                    "subpart": v["subpart"], "uppart": v["uppart"],
                    "data": dict(v["data"])}
                for k, v in self.tn_map.items()}

    # -- Tnn-Ebene ------------------------------------------------------
    @staticmethod
    def tnn_key(addr, slot):
        """Tnn-Schlüssel, z.B. (1, 0) -> 'T1A' (live-Format)."""
        if slot in range(4):
            letter = SLOT_KEYS[slot]
        elif isinstance(slot, str) and slot in SLOT_KEYS:
            letter = slot
        else:
            raise ValueError("Slot muss 0..3 oder A..D sein")
        return "T%d%s" % (int(addr), letter)

    def generate_Tnn_content(self, tnn, rfid=None, remain=None, extra=None):
        """Tnn-Content erzeugen (vgl. generate_Tnn_content; Felder TODO)."""
        content = {"tnn": tnn, "rfid": rfid, "remain": remain}
        if extra:
            content.update(extra)
        return content

    def get_Tnn_content(self, tnn):
        """Tnn-Content lesen (vgl. get_Tnn_content)."""
        return self.tnn_map.get(tnn)

    def update_Tnn_map(self, tnn, content):
        """Tnn-Content ablegen (vgl. update_Tnn_map/BoxAction.update_Tnn_map)."""
        self.tnn_map[tnn] = dict(content)
        return self.tnn_map[tnn]

    def generate_Tnn_map(self):
        """Kopie der Tnn-Map (vgl. generate_Tnn_map)."""
        return {k: dict(v) for k, v in self.tnn_map.items()}

    def get_Tnn_map(self):
        """Alias für generate_Tnn_map (vgl. get_Tnn_map)."""
        return self.generate_Tnn_map()

    # -- BoxMode / Save-Layer / Sync ------------------------------------
    def get_box_mode(self):
        """Box-Modus lesen (vgl. BoxState.get_box_mode; Werte TODO)."""
        return self.box_mode

    def get_tn_save_data(self, addr, part, num):
        """Save-Eintrag lesen (vgl. get_tn_save_data)."""
        return self.save_data.get(self._tn_key(addr, part, num))

    def modify_tn_save_data(self, addr, part, num, **fields):
        """Save-Eintrag schreiben (vgl. modify_tn_save_data)."""
        key = self._tn_key(addr, part, num)
        entry = self.save_data.setdefault(key, {})
        entry.update(fields)
        return entry

    def update_tn_save_data(self, addr, part, num):
        """Tn -> Save übernehmen (vgl. update_tn_save_data/sync_tn_data)."""
        entry = self.get_Tn_data(addr, part, num)
        if entry is None:
            return None
        return self.modify_tn_save_data(addr, part, num,
                                        subpart=entry["subpart"],
                                        uppart=entry["uppart"],
                                        data=dict(entry["data"]))

    def sync_tn_data(self):
        """Alle Tn-Einträge in den Save-Layer spiegeln (vgl. sync_tn_data)."""
        for (addr, part, num) in list(self.tn_map):
            self.update_tn_save_data(addr, part, num)
        return len(self.save_data)

    def update_same_material_list(self, material_list):
        """Gleich-Material-Liste ablegen (vgl. update_same_material_list).

        TODO: .so-Gruppierungslogik unbelegt – nur Ablage, keine Auswertung.
        """
        self.same_material_list = list(material_list)
        return self.same_material_list

    def clear_color_count(self):
        """Farbzähler löschen (vgl. clear_color_count)."""
        self.color_count = {}
        return self.color_count

    def clear_e_err(self):
        """Extrude-Fehlerflags löschen (vgl. clear_e_err; Codes TODO)."""
        self.e_err = {}
        return self.e_err

    def e_err_set(self, key, value=True):
        """Fehlerflag setzen (vgl. e_err_set; Codes TODO)."""
        self.e_err[key] = value
        return self.e_err


# =====================================================================
# 3) BoxAction – belegte Einzel-Step-Builder + bus-gebundene Klasse.
#    Gesamt-Reihenfolge/Steuerung (Sensor-/Messrad-gesteuert, Stufen 00/04/
#    05/06/07, Retrude START/FINISH): siehe cfsseq.py – hier NICHT dupliziert.
# =====================================================================

def build_preload(mask, action):
    """SET_PRE_LOADING / RFID-Control (0x0d): [Slotmaske][Aktion].

    Aktion 0x00=disable / 0x01=enable / 0x02=Force-Read (mit RFID) /
    0x03=Forced Preload ohne RFID (Belege s. PRELOAD_*-Konstanten, Ghidra
    FUN_0800f0da; Referenzvektor f70105ff0d0f0169 = Maske 0x0F, enable).
    Maske wirkt nur bei 02/03 (bei 00/01 von der FW ignoriert).
    STATUS 0xff. Identische Bytes wie cfsseq.build_preload.
    TODO: .so-Parameter force (communication_set_pre_loading force=False)
      – Wire-Abbild unbelegt; laut FW gibt es KEIN separates force-Byte,
      naheliegend ist force -> Aktion 0x02/0x03 (Host-Seite unbelegt).
    """
    mask = check_mask(mask)
    if action not in PRELOAD_ACTIONS:
        raise ValueError("Aktion muss 0x00..0x03 sein")
    return serial485.build_frame(BOX_ADDR, FUNC_SET_PRE_LOADING, [mask, action],
                                 status=STATUS_OPERATIONAL)


def build_preload_enable(mask=MASK_ALL):
    """Preload/Insert-Reading an (0x0d [Maske 01]) – Referenz-belegt."""
    return build_preload(mask, PRELOAD_ENABLE)


def build_preload_disable(mask=MASK_ALL):
    """Preload/Insert-Reading aus (0x0d [Maske 00]) – Referenz-belegt."""
    return build_preload(mask, PRELOAD_DISABLE)


def build_preload_force_read(mask=MASK_ALL):
    """RFID-Force-Read (0x0d [Maske 02]) – Referenz-belegt (vanilla);
    Ghidra 15.10.2026: Forced Preload MIT RFID (FUN_08004ec4(mask,1))."""
    return build_preload(mask, PRELOAD_FORCE_READ)


def build_preload_force_tension_only(mask=MASK_ALL):
    """Forced Preload OHNE RFID (0x0d [Maske 03]) – Ghidra-belegt 15.10.2026
    (FUN_0800f0da -> FUN_08004ec4(mask, 0): nur pretension, kein RFID-Lesen);
    nicht live gesehen. Antwort erst nach dem Vorgang (preload_force_timeout)."""
    return build_preload(mask, PRELOAD_FORCE_TENSION_ONLY)


def build_feed_mode_enter():
    """Feed-Mode betreten (0x04 [00 01]) – K2-belegt (PROTOKOLL-485.md).
    Ghidra 15.10.2026 (FUN_0800f014): mode 1 = Print verlassen -> STATE_IDLE."""
    return serial485.build_frame(BOX_ADDR, FUNC_SET_BOX_MODE, [0x00, 0x01],
                                 status=STATUS_OPERATIONAL)


def build_print_mode(mask):
    """Print-Mode (0x04 [Slotmaske 00]) – K2-belegt (PROTOKOLL-485.md).
    Ghidra 15.10.2026: mode 0 = Slot -> STATE_PRINT (mask != 0 nötig)."""
    return serial485.build_frame(BOX_ADDR, FUNC_SET_BOX_MODE,
                                 [check_mask(mask), 0x00],
                                 status=STATUS_OPERATIONAL)


def build_preload_inhibit(inhibit):
    """Auto-Preload-Sperre (0x0f [01] sperren / [00] freigeben).

    BELEGT 15.10.2026 (Ghidra, inline im Dispatcher FUN_0800f27c): setzt
    S[8] (0x200037d8); S[8] != 0 sperrt das automatische Preload
    (FUN_080053b0). KEINE Motor-Aktion. Live-Flanke: Host sperrt vor dem
    Load ([01]) und gibt danach frei ([00]).
    """
    return serial485.build_frame(BOX_ADDR, FUNC_PRELOAD_INHIBIT,
                                 [0x01 if inhibit else 0x00],
                                 status=STATUS_OPERATIONAL)


def build_engage():
    """Alias (alter Name): 0x0f [01] = Auto-Preload SPERREN.

    ~~Motor-Engage~~ WIDERLEGT 15.10.2026 (Ghidra) – s. build_preload_inhibit.
    Bytes unverändert.
    """
    return build_preload_inhibit(True)


def build_release():
    """Alias (alter Name): 0x0f [00] = Auto-Preload FREIGEBEN.

    ~~Motor-Release~~ WIDERLEGT 15.10.2026 (Ghidra) – s. build_preload_inhibit.
    """
    return build_preload_inhibit(False)


def build_get_rfid(mask=0x0F):
    """GET_RFID-Request ([Maske], 0x02) – Live-Introspektion belegt
    (communication_get_rfid data=b'\\x01'/b'\\x0f', STATUS 0xff, Timeout 2 s)."""
    return serial485.build_frame(BOX_ADDR, FUNC_GET_RFID, [check_mask(mask)],
                                 status=STATUS_OPERATIONAL)


def build_get_remain(mask=0x0F):
    """GET_REMAIN_LEN-Request ([Maske], 0x03) – wie build_get_rfid belegt."""
    return serial485.build_frame(BOX_ADDR, FUNC_GET_REMAIN_LEN, [check_mask(mask)],
                                 status=STATUS_OPERATIONAL)


def build_get_wheel():
    """MEASURING_WHEEL-Request ([01], 0x0e) – K2-belegt."""
    return serial485.build_frame(BOX_ADDR, FUNC_MEASURING_WHEEL, [0x01],
                                 status=STATUS_OPERATIONAL)


def build_get_box_state():
    """BOX_STATE-Poll (0x0a, leer) – Herzschlag alle ~4 s, K2-belegt.

    TODO: Poll-Payload des Hosts vs. 6-Byte-State-Antwort – die leere Form
    ist der belegte Host-Poll, Inhalt folgt per Antwort (get_box_status).
    """
    return serial485.build_frame(BOX_ADDR, FUNC_GET_BOX_STATE, [],
                                 status=STATUS_OPERATIONAL)


def build_get_buffer():
    """BUFFER_STATE-Request (0x05, leer) – K2-belegt."""
    return serial485.build_frame(BOX_ADDR, FUNC_GET_BUFFER_STATE, [],
                                 status=STATUS_OPERATIONAL)


def build_get_hardware(channel=0x00):
    """HARDWARE_STATUS-Request (0x08 [Kanal]) – Kanäle 0x00/0x01 in
    Extrude/Retrude-Flanke belegt (cfsseq.py); weitere Kanäle TODO."""
    if not 0 <= channel <= 0xFF:
        raise ValueError("Kanal muss 0x00..0xff sein")
    return serial485.build_frame(BOX_ADDR, FUNC_GET_HARDWARE_STATUS,
                                 [channel], status=STATUS_OPERATIONAL)


def build_get_version():
    """VERSION_SN-Request (0x14, leer, STATUS 0xff) – Hi-belegt
    (f7 01 03 ff 14 06); K2-Antwort s. parse_version_sn."""
    return serial485.build_frame(BOX_ADDR, FUNC_GET_VERSION_SN, [],
                                 status=STATUS_OPERATIONAL)


class BoxAction:
    """Box-Abläufe gegen den Bus (vgl. BoxAction.communication_*)."""

    def __init__(self, bus, addr=BOX_ADDR):
        self.bus = bus
        self.addr = addr

    # -- Abfragen (alle Timeout 2 s, STATUS 0xff; vgl. API-box_wrapper.md) --
    def communication_get_rfid(self, num=0x0F):
        """RFID lesen (num = Slotmaske 0x00..0x0f wie im Original, z.B.
        1 = Slot A, 15 = alle; KEINE Slotnummer). Gibt Rohdaten zurück;
        parsen per get_rfid()."""
        return self.bus.transact(self.addr, FUNC_GET_RFID, [check_mask(num)],
                                 status=STATUS_OPERATIONAL,
                                 timeout=TIMEOUT_OPERATIONAL)

    def communication_get_remain_len(self, num=0x0F):
        """Restmengen lesen (num = Slotmaske 0x00..0x0f, Default alle)."""
        return self.bus.transact(self.addr, FUNC_GET_REMAIN_LEN, [check_mask(num)],
                                 status=STATUS_OPERATIONAL,
                                 timeout=TIMEOUT_OPERATIONAL)

    def communication_measuring_wheel(self, action=0x01):
        """Messrad lesen (belegt action [01]; andere TODO). Gibt Float zurück."""
        raw = self.bus.transact(self.addr, FUNC_MEASURING_WHEEL, [action],
                                status=STATUS_OPERATIONAL,
                                timeout=TIMEOUT_OPERATIONAL)
        return get_measuring_wheel(bytes([action]), raw)

    def communication_get_box_state(self):
        """Box-State lesen (0x0a-Poll, Antwort via get_box_status)."""
        return self.bus.transact(self.addr, FUNC_GET_BOX_STATE, [],
                                 status=STATUS_OPERATIONAL,
                                 timeout=TIMEOUT_OPERATIONAL)

    def communication_get_buffer_state(self):
        """Buffer-State lesen (0x05, 1 Byte)."""
        return self.bus.transact(self.addr, FUNC_GET_BUFFER_STATE, [],
                                 status=STATUS_OPERATIONAL,
                                 timeout=TIMEOUT_OPERATIONAL)

    def communication_get_hardware_status(self, channel=0x00):
        """Hardware-Status lesen (0x08 [Kanal], 1 Byte)."""
        return self.bus.transact(self.addr, FUNC_GET_HARDWARE_STATUS,
                                 [channel], status=STATUS_OPERATIONAL,
                                 timeout=TIMEOUT_OPERATIONAL)

    def communication_get_version_sn(self):
        """Version/SN lesen (0x14, ASCII "153"+SN)."""
        return self.bus.transact(self.addr, FUNC_GET_VERSION_SN, [],
                                 status=STATUS_OPERATIONAL,
                                 timeout=TIMEOUT_OPERATIONAL)

    # -- Aktionen (belegte Einzel-Steps; Antwort = Box-ACK, kein Parsen) --
    def communication_set_pre_loading(self, mask, action, timeout=None):
        """Preload setzen (0x0d [Maske][Aktion 00 aus/01 an/02 Force-Read/
        03 Force ohne RFID]).

        mask = Slotmaske 0x00..0x0f, action = PRELOAD_DISABLE/ENABLE/
        FORCE_READ/FORCE_TENSION_ONLY (Belege s. Konstanten).
        TODO: .so-Parameter action/num/force-Abbild (num=15 Default,
        force=False) – hier explizit (mask, action), nichts erfunden.
        ~~TODO bei echtem Preload ggf. länger wählen~~ ERLEDIGT 15.10.2026
        (Ghidra FUN_0800f0da/FUN_08004ec4/FUN_08004e80): bei op 2/3 kommt die
        Antwort erst NACH dem Vorgang (~3 min je Slot, + Vorlauf). Daher
        Default-Timeout preload_force_timeout(mask) und retries=0 – ein
        2-s-Timeout mit Retries würde den Force-Preload mehrfach auslösen.
        op 0/1 antworten sofort: operational 2 s, Retries wie bisher.
        timeout = expliziter Override (s).
        """
        if action not in PRELOAD_ACTIONS:
            raise ValueError("Aktion muss 0x00..0x03 sein")
        mask = check_mask(mask)
        if action in PRELOAD_FORCE_ACTIONS:
            return self.bus.transact(
                self.addr, FUNC_SET_PRE_LOADING, [mask, action],
                status=STATUS_OPERATIONAL,
                timeout=(preload_force_timeout(mask) if timeout is None
                         else timeout),
                retries=0)
        return self.bus.transact(self.addr, FUNC_SET_PRE_LOADING,
                                 [mask, action],
                                 status=STATUS_OPERATIONAL,
                                 timeout=(TIMEOUT_OPERATIONAL if timeout is None
                                          else timeout))

    def communication_set_box_mode_feed(self):
        """Feed-Mode betreten (0x04 [00 01])."""
        return self.bus.transact(self.addr, FUNC_SET_BOX_MODE, [0x00, 0x01],
                                 status=STATUS_OPERATIONAL,
                                 timeout=TIMEOUT_OPERATIONAL)

    def communication_set_box_mode_print(self, mask):
        """Print-Mode (0x04 [Maske 00])."""
        return self.bus.transact(self.addr, FUNC_SET_BOX_MODE,
                                 [check_mask(mask), 0x00],
                                 status=STATUS_OPERATIONAL,
                                 timeout=TIMEOUT_OPERATIONAL)

    def communication_set_preload_inhibit(self, inhibit):
        """Auto-Preload-Sperre setzen (0x0f [01] sperren / [00] frei).

        BELEGT 15.10.2026 (Ghidra, s. build_preload_inhibit): FW akzeptiert
        nur 0/1 (sonst Status 1), Antwort sofort ohne Daten.
        """
        if isinstance(inhibit, bool):
            inhibit = int(inhibit)
        if inhibit not in (0x00, 0x01):
            raise ValueError("inhibit muss 0/1 bzw. False/True sein")
        return self.bus.transact(self.addr, FUNC_PRELOAD_INHIBIT, [inhibit],
                                 status=STATUS_OPERATIONAL,
                                 timeout=TIMEOUT_OPERATIONAL)

    def communication_ctrl_connection_motor_action(self, act):
        """Alias (.so-Name): 0x0f [act]. ~~Motor-Engage (0x01) / Release
        (0x00)~~ KORRIGIERT 15.10.2026 (Ghidra): 0x01 = Auto-Preload sperren,
        0x00 = freigeben – keine Motor-Aktion. Bytes/Guard unverändert."""
        if act not in (0x00, 0x01):
            raise ValueError("act muss 0x00 (release) / 0x01 (engage) sein")
        return self.communication_set_preload_inhibit(act)

    # -- TODO (unbelegt, bewusst NICHT implementiert) ---------------------
    # communication_extrude_process / retrude_process (Stufen + lange
    #   Timeouts 15/150 s, sensor-/messrad-gesteuert): siehe cfsseq.py.
    # DryRun/Dryer (get/set_dry_mode, pause/continue/auto_dry), Tighten-Up,
    # Filament-Sensor-States, create_connect, set_motor_speed, Test-Loop:
    #   Wire-Abbild unbelegt -> hier kein erfundenes Byte.


# --- Selbsttest (keine Hardware, keine Netzwerkzugriffe) ---
class _FakeBus:
    """Spielt Antwort-Daten ab, protokolliert Requests (wie boxmini._FakeBus)."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def transact(self, addr, fun, data=(), **kwargs):
        self.calls.append((addr, fun, list(data), dict(kwargs)))
        fun_exp, rdata = serial485.parse_frame(self.answers.pop(0))
        assert fun_exp == fun, (fun_exp, fun)
        return rdata


def selftest():
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(("ok " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    F = bytes.fromhex
    # -- 1. RFID: echte Live-Frames (12.10.2026) --
    check("rfid B:unknown",
          get_rfid(F("f7 01 0d 00 02 42 3a 75 6e 6b 6e 6f 77 6e 3b 8e"))
          == {"B": "unknown"})
    check("rfid C:busy",
          get_rfid(F("f7 01 0a 00 02 43 3a 62 75 73 79 3b 54"))
          == {"C": "busy"})
    check("rfid roh+multi",
          get_rfid(b"A:10125;B:unknown;") == {"A": "10125", "B": "unknown"})
    check("rfid leer", get_rfid(b"") == {})
    # Live-Vendor (PROTOKOLL-485.md, RFID-Frame 12.10.2026) feldweise –
    # Repo: anonymisierter Platzhalter gleicher Länge/Struktur (echte
    # day/year/supplier/batch/len/number entfernt; mat_id/col beibehalten):
    live_vendor = "101250000001010010FFFFFF0000000000000000"
    vf = split_rfid_vendor(live_vendor)
    for name, exp in (("month", "1"), ("day", "01"), ("year", "25"),
                      ("supplier", "0000"), ("batch", "00"),
                      ("mat_id", "101001"), ("col", "0FFFFFF"),
                      ("len", "0000"), ("number", "000000"),
                      ("reserve", "000000")):
        check("vendor %s=%s" % (name, exp), vf.get(name) == exp)
    check("vendor bytes == str",
          split_rfid_vendor(live_vendor.encode("ascii")) == vf)
    check("vendor felder lückenlos",
          "".join(vf[n] for n, _ in RFID_VENDOR_FIELDS) == live_vendor)
    for bad_v in (live_vendor[:-1], live_vendor + "0", ""):
        try:
            split_rfid_vendor(bad_v)
            check("vendor laengen-guard %d" % len(bad_v), False)
        except ValueError:
            check("vendor laengen-guard %d" % len(bad_v), True)
    try:
        get_rfid(F("f7 01 03 00 03 b3"))  # falscher fun -> muss scheitern
        check("rfid fun-guard", False)
    except ValueError:
        check("rfid fun-guard", True)

    # -- 2. Remain: echte Live-Frames --
    check("remain A=28",
          get_remain_len(b"\x0f", F("f7 01 07 00 03 1c ff 00 00 11"))
          == {"A": 28, "B": None, "C": 0, "D": 0})
    check("remain leer/none",
          get_remain_len(b"\x0f", F("f7 01 07 00 03 ff ff ff 00 98"))
          == {"A": None, "B": None, "C": None, "D": 0})
    check("remain roh", get_remain_len(b"\x01", bytes.fromhex("1a 00 ff 00"))
          == {"A": 26, "B": 0, "C": None, "D": 0})
    for bad_len in (b"\x1c", b"", bytes(5)):
        try:
            get_remain_len(b"\x0f", serial485.build_frame(
                BOX_ADDR, FUNC_GET_REMAIN_LEN, bad_len, status=STATUS_REPLY))
            check("remain laengen-guard %dB" % len(bad_len), False)
        except ValueError:
            check("remain laengen-guard %dB" % len(bad_len), True)

    # -- 3. Messrad: echte Live-Frames (KORREKTUR 13.10.: BE-Float negativ) --
    w1 = get_measuring_wheel(b"\x01", F("f7 01 07 00 0e c4 e6 fe 0b f3"))
    check("wheel -1847,94", abs(w1 - (-1847.94)) < 0.02)
    w2 = get_measuring_wheel(b"\x01", F("f7 01 07 00 0e c5 00 91 49 a9"))
    check("wheel negativ", w2 < 0)
    check("wheel c52a4e54",
          abs(get_measuring_wheel(b"\x01", bytes.fromhex("c5 2a 4e 54"))
              - (-2724.89)) < 0.02)
    try:
        get_measuring_wheel(b"\x01", bytes.fromhex("c4 e6 fe"))  # 3 B
        check("wheel laengen-guard", False)
    except ValueError:
        check("wheel laengen-guard", True)
    # synthetisch: struct-Roundtrip über echten Frame-Bau
    synth = struct.pack(">f", -123.456)
    check("wheel synthetisch",
          get_measuring_wheel(b"\x01", serial485.build_frame(
              BOX_ADDR, FUNC_MEASURING_WHEEL, synth,
              status=STATUS_REPLY)) == struct.unpack(">f", synth)[0])

    # -- 4. Box-State: echte Frames (6 B + 4 B/0x30-Variante) --
    st = get_box_status(F("f7 01 09 00 0a 13 34 00 00 00 00 d7"))
    check("boxstate 6B", st["len"] == 6 and st["b0"] == 0x13
          and st["b1"] == 0x34 and st["frame_status"] == 0x00)
    st30 = get_box_status(F("f7 01 07 30 0a 03 00 00 00 51"))
    check("boxstate 0x30/4B", st30["len"] == 4 and st30["frame_status"] == 0x30
          and st30["raw"] == bytes.fromhex("03 00 00 00"))
    # Rohdaten mit erstem Byte 0xF7, aber unpassendem Längenfeld -> Rohdaten
    st_raw = get_box_status(bytes([0xF7, 0x34, 0, 0, 0, 0]))
    check("boxstate roh mit 0xF7", st_raw["frame_status"] is None
          and st_raw["len"] == 6 and st_raw["b0"] == 0xF7)

    # 0x0a benannt (Ghidra 15.10.2026) – Live-Frames passen zum Layout
    nb = parse_box_state(F("f7 01 09 00 0a 13 34 00 00 00 00 d7"))
    check("parse_box_state live IDLE", nb["kind"] == "state"
          and nb["state"] == BOX_STATE_IDLE and nb["state_name"] == "STATE_IDLE"
          and nb["s1"] == 0x13 and nb["s0"] == 0x34 and nb["flags"] == 0
          and nb["sensor_b_mask"] == 0 and nb["rfid_valid_mask"] == 0
          and nb["status_name"] == "ok")
    ne = parse_box_state(F("f7 01 07 30 0a 03 00 00 00 51"))
    check("parse_box_state live 0x30-Ereignis", ne["kind"] == "event"
          and ne["events"] == {"A": 3, "B": 0, "C": 0, "D": 0}
          and "state" not in ne)
    nerr = parse_box_state(serial485.build_frame(
        BOX_ADDR, FUNC_GET_BOX_STATE, [0x13, 0x34, 0, BOX_STATE_ERROR, 0, 0],
        status=STATUS_NO_FILAMENT))
    check("parse_box_state ERROR -> Fehlercode", nerr["state_name"] == "STATE_ERROR"
          and nerr["error_code"] == 0x16)
    check("parse_box_state unbekannt", parse_box_state(b"\x01\x02")["kind"]
          == "unknown")
    check("box-state-enum 0..5", [BOX_STATE_NAMES[i] for i in range(6)] ==
          ["STATE_IDLE", "STATE_PRELOAD", "STATE_PRINT", "STATE_RELOAD",
           "STATE_ERROR", "STATE_TEST"])

    # -- 5. Version/SN: rekonstruierter K2-Voll-Frame (CRC 0xd4 verifiziert,
    #    SN anonymisiert, gleiche Länge) --
    ver, sn = parse_version_sn(
        F("f7 01 19 00 14 31 35 33 53 4e 2d 44 45 4d 4f 2d 31 32 33 34"
          " 35 36 37 38 39 30 58 d4"))
    check("version 153", ver == "153" and sn == "SN-DEMO-1234567890X")
    check("version-req Hi",
          serial485.parse_frame(F("f7 01 03 ff 14 06")) == (0x14, b""))

    # -- 6. Buffer/Hardware: echte Frames + synthetische Builder --
    check("buffer 0x00", parse_buffer_state(F("f7 01 04 00 05 00 19")) == 0)
    # Referenzvektor ref-hi/valid_messages.json: mask=0x0F enable=0x01
    check("builder preload-enable (ref-hi f70105ff0d0f0169)",
          build_preload_enable(0x0F) == F("f7 01 05 ff 0d 0f 01 69"))
    check("builder preload-disable",
          build_preload_disable(0x01) == F("f7 01 05 ff 0d 01 00 b8"))
    check("builder preload-force-read",
          build_preload_force_read(0x01) == F("f7 01 05 ff 0d 01 02 b6"))
    # vanilla set_rfid_insert_reading(True) -> (0, 1)
    check("builder preload-enable vanilla (0,1)",
          build_preload(0x00, PRELOAD_ENABLE)[5:7] == bytes([0x00, 0x01]))
    import cfsseq
    check("preload box == cfsseq (M1)",
          all(build_preload(m, a) == cfsseq.build_preload(m, a)
              for m in range(16) for a in PRELOAD_ACTIONS))
    # Masken-Semantik: num/mask ist MASKE, keine Slotnummer
    check("mask 0x01/0x02/0x03 unverändert",
          [check_mask(m) for m in (0x01, 0x02, 0x03)] == [0x01, 0x02, 0x03])
    check("mehrfachmaske 0x05/0x0C",
          build_get_rfid(0x05)[5] == 0x05 and build_get_remain(0x0C)[5] == 0x0C)
    check("print-mode maske 0x02", build_print_mode(0x02)[5:7] == b"\x02\x00")
    check("slot_to_mask", [slot_to_mask(i) for i in range(4)]
          == [0x01, 0x02, 0x04, 0x08])
    for bad in (0x10, -1, True, "A", 1.0):
        try:
            check_mask(bad)
            check("mask-guard %r" % (bad,), False)
        except ValueError:
            check("mask-guard %r" % (bad,), True)
    for bad in (4, -1, True):
        try:
            slot_to_mask(bad)
            check("slot-guard %r" % (bad,), False)
        except ValueError:
            check("slot-guard %r" % (bad,), True)
    check("builder feed-enter",
          build_feed_mode_enter() == F("f7 01 05 ff 04 00 01 90"))
    check("builder print-mode",
          build_print_mode(0x01) == F("f7 01 05 ff 04 01 00 82"))
    check("builder engage", build_engage() == F("f7 01 04 ff 0f 01 b7"))
    check("builder release", build_release() == F("f7 01 04 ff 0f 00 b0"))
    check("builder get-rfid", build_get_rfid(0x0F) == F("f7 01 04 ff 02 0f 74"))
    check("builder get-remain", build_get_remain(0x0F) == F("f7 01 04 ff 03 0f 61"))
    check("builder get-wheel", build_get_wheel() == F("f7 01 04 ff 0e 01 a2"))
    check("builder get-buffer", build_get_buffer() == F("f7 01 03 ff 05 71"))
    check("builder get-hw", build_get_hardware(0) == F("f7 01 04 ff 08 00 db"))
    check("builder get-version", build_get_version() == F("f7 01 03 ff 14 06"))
    try:
        build_preload(0, 0x04)
        check("preload aktions-guard", False)
    except ValueError:
        check("preload aktions-guard", True)
    # -- 6b. Ghidra 15.10.2026: op 3, 0x0f-Alias, Timeout-Formel --
    check("builder preload-op3 tension-only",
          build_preload_force_tension_only(0x01)[5:7] == b"\x01\x03"
          and serial485.parse_frame(build_preload_force_tension_only(0x01))
          == (FUNC_SET_PRE_LOADING, b"\x01\x03"))
    check("preload op3 box == cfsseq",
          build_preload(0x02, PRELOAD_FORCE_TENSION_ONLY)
          == cfsseq.build_preload(0x02, cfsseq.PRELOAD_FORCE_TENSION_ONLY))
    check("preload-inhibit == alter engage/release",
          build_preload_inhibit(True) == build_engage()
          == F("f7 01 04 ff 0f 01 b7")
          and build_preload_inhibit(False) == build_release())
    check("FUNC_MOTOR_ACTION alias 0x0f",
          FUNC_MOTOR_ACTION == FUNC_PRELOAD_INHIBIT == 0x0F)
    check("force-timeout 1 slot 380 s", preload_force_timeout(0x01) == 380.0)
    check("force-timeout 4 slots 980 s", preload_force_timeout(0x0F) == 980.0)
    check("force-timeout maske 0 -> 1 slot", preload_force_timeout(0x00) == 380.0)
    check("extrude-modi re-export",
          EXTRUDE_MODE_EXTRUDER_CHECK == 7 and EXTRUDE_MODE_QUERY == 5
          and EXTRUDER_CHECK_MM == 3 and RETRUDE_P_PULL_HUB == 1)
    check("status-namen", status_name(0x16) == "Slot ohne Filament"
          and status_name(STATUS_CRC) == "CRC-Fehler im Request"
          and status_name(0x7f) == "unbekannt 0x7f")

    # -- 7. BoxState: Tn/Tnn-Roundtrip (Modell, kein Bus) --
    bs = BoxState()
    bs.modify_Tn_data(1, 0, 0, subpart=1, uppart=0)
    bs.modify_Tn_inner_data(1, 0, 0, "filament", "A")
    check("tn get", bs.get_Tn_data(1, 0, 0)["subpart"] == 1)
    check("tn inner", bs.get_Tn_inner_data(1, 0, 0, "filament") == "A")
    check("tn map", (1, 0, 0) in bs.generate_Tn_map())
    tnn = BoxState.tnn_key(1, 0)
    check("tnn key", tnn == "T1A")
    bs.update_Tnn_map(tnn, bs.generate_Tnn_content(tnn, rfid="10125", remain=28))
    check("tnn content", bs.get_Tnn_content("T1A")["remain"] == 28
          and len(bs.get_Tnn_map()) == 1)
    check("save-layer", bs.update_tn_save_data(1, 0, 0) is not None
          and bs.get_tn_save_data(1, 0, 0) is not None
          and bs.sync_tn_data() >= 1)
    check("same-material", bs.update_same_material_list(["T1A"]) == ["T1A"])
    bs.e_err_set("key_test")
    check("e_err", bs.clear_e_err() == {})
    check("color-count", bs.clear_color_count() == {})

    # -- 8. BoxAction gegen FakeBus (Request-Payloads + STATUS/Timeout) --
    bus = _FakeBus([
        F("f7 01 0d 00 02 42 3a 75 6e 6b 6e 6f 77 6e 3b 8e"),  # rfid
        F("f7 01 07 00 03 1c ff 00 00 11"),  # remain
        F("f7 01 07 00 0e c4 e6 fe 0b f3"),  # wheel
        F("f7 01 03 00 0d 9e"),  # preload-ACK (synthetisch, CRC-echt)
        F("f7 01 03 00 04 a1"),  # feed-ACK (Live-Pollform)
        F("f7 01 03 00 0f 90"),  # engage-ACK (Live-Pollform)
        F("f7 01 03 00 0d 9e"),  # force-preload-ACK (synthetisch)
        F("f7 01 03 00 0d 9e"),  # force-tension-only-ACK (synthetisch)
        F("f7 01 03 00 0f 90"),  # preload-inhibit-ACK
    ])
    act = BoxAction(bus)
    # num ist MASKE wie im Original: 0x02 = Slot B -> Live-Antwort B:unknown
    check("action rfid", get_rfid(act.communication_get_rfid(0x02))
          == {"B": "unknown"} and bus.calls[0][2] == [0x02])
    check("action remain", get_remain_len(
        b"\x0f", act.communication_get_remain_len(0x0F))["A"] == 28)
    check("action wheel",
          abs(act.communication_measuring_wheel() - (-1847.94)) < 0.02)
    act.communication_set_pre_loading(0x01, PRELOAD_ENABLE)
    act.communication_set_box_mode_feed()
    act.communication_ctrl_connection_motor_action(0x01)
    check("action preload-payload", bus.calls[3][2] == [0x01, 0x01])
    check("action feed-payload", bus.calls[4][2] == [0x00, 0x01])
    check("action engage-payload", bus.calls[5][2] == [0x01])
    check("action status/timeout",
          all(c[3].get("status") == STATUS_OPERATIONAL
              and c[3].get("timeout") == TIMEOUT_OPERATIONAL
              for c in bus.calls))
    # 0x0d op 2/3: langer Timeout + retries=0 (Ghidra: Antwort nach Vorgang)
    act.communication_set_pre_loading(0x01, PRELOAD_FORCE_READ)
    act.communication_set_pre_loading(0x03, PRELOAD_FORCE_TENSION_ONLY,
                                      timeout=123.0)
    check("action force: timeout 380 s, retries 0",
          bus.calls[6][2] == [0x01, 0x02]
          and bus.calls[6][3].get("timeout") == 380.0
          and bus.calls[6][3].get("retries") == 0)
    check("action force op3: override-timeout, retries 0",
          bus.calls[7][2] == [0x03, 0x03]
          and bus.calls[7][3].get("timeout") == 123.0
          and bus.calls[7][3].get("retries") == 0)
    check("action enable: kein retries-Override",
          "retries" not in bus.calls[3][3])
    act.communication_set_preload_inhibit(True)
    check("action preload-inhibit payload", bus.calls[8][1] == 0x0F
          and bus.calls[8][2] == [0x01])
    try:
        act.communication_ctrl_connection_motor_action(0x02)
        check("motor act-guard", False)
    except ValueError:
        check("motor act-guard", True)

    # -- 9. CRC-Negativtest (verfälschtes Byte muss scheitern) --
    bad = bytearray(F("f7 01 07 00 03 1c ff 00 00 11"))
    bad[5] ^= 0x01
    try:
        serial485.parse_frame(bytes(bad))
        check("crc-guard", False)
    except ValueError:
        check("crc-guard", True)

    print("SELBSTTEST: %s" % ("ok" if fails == 0 else "%d FEHLER" % fails))
    return fails


if __name__ == "__main__":
    raise SystemExit(selftest())
