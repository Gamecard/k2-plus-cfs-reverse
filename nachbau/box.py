#!/usr/bin/env python3
"""box.py – Python-Nachbau von box_wrapper ParseData/BoxState/BoxAction (CFS-Box, K2 Plus).

Erweiterung von boxmini.py (NICHT ändern): boxmini.py deckt RFID + Restmenge
als BoxMini-Klasse ab; diese Datei ergänzt sinngemäß nach API-box_wrapper.md:

1) ParseData (reine Funktionen, keine Hardware):
   get_rfid / get_remain_len / get_measuring_wheel (BE-Float via struct) /
   get_box_status, dazu belegt: parse_version_sn (ASCII "153"+SN),
   parse_buffer_state / parse_hardware_status (je 1 Byte).
2) BoxState: Tn-Datenmodell im Speicher (tn_map/tnn_map/save_data als dicts,
   generate/get/modify/update-Methoden sinngemäß nach API-Doku).
3) BoxAction: belegte Einzel-Step-Sequenz-Builder (Preload arm/disarm/rearm,
   Feed-Mode enter, Engage/Release, Print-Mode) + bus-gebundene Klasse.

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
FUNC_MOTOR_ACTION = 0x0F
FUNC_GET_VERSION_SN = 0x14
# 0x10/0x11 (Extrude/Retrude): siehe cfsseq.py, hier nur als Konstanten zum
# Referenzieren (keine eigenen Builder – kein Duplikat).
FUNC_EXTRUDE = 0x10
FUNC_RETRUDE = 0x11

# Preload-Phasen (Beleg: Referenzimplementierung + PROTOKOLL-485.md).
PRELOAD_ARM = 0x00
PRELOAD_DISARM = 0x01
PRELOAD_REARM = 0x02


def _mask(slot):
    """Slot 0..3 oder 1-hot-Maske -> Maske (vgl. cfsseq._slot)."""
    if slot in range(4):
        return 1 << slot
    if slot in SLOT_MASKS or slot == MASK_ALL or slot == 0x00:
        return slot
    raise ValueError("Slot muss 0..3 oder 1-hot-Maske sein: %r" % (slot,))


def _response_data(msg, expect_fun):
    """Hilfsfunktion: Voll-Frame oder Rohdaten -> Antwort-Daten (bytes).

    Erkannt wird ein Voll-Frame an Head 0xF7 + Mindestlänge; dann wird per
    serial485.parse_frame CRC-geprüft und der fun-Code verglichen.
    Sonst gilt msg als bereits extrahierte Rohdaten (z.B. aus Tests).
    """
    raw = bytes(msg)
    if len(raw) >= 6 and raw[0] == serial485.PACK_HEAD:
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
    TODO (.so-offen): Felder nach dem vender-Hex (color/material/remain)
      sind weitere ';'-Felder – hier bewusst generisch als String belassen.
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


def get_remain_len(data_send, msg):
    """Restmengen-Antwort parsen: 4 Bytes positional A,B,C,D (0-100 %).

    Belegt: Live-Frame f7 01 07 00 03 1c ff 00 00 11 -> A=28 %, B=n/a, C/D=0;
    0xff = nicht ausgewählt, 0x00 = ausgewählt und leer.
    data_send = Request-Kontext (Slotmaske, vgl. .so-Signatur) – wird hier
      nur entgegengenommen/dokumentiert, nicht zur Filterung benutzt
      (TODO: ob das .so danach filtert, ist unbelegt).
    msg = Voll-Frame (fun 0x03) oder 4 Rohbytes.
    Gibt dict {Slot-Key: Prozent oder None} zurück.
    """
    data = _response_data(msg, FUNC_GET_REMAIN_LEN)
    raw = list(data)
    out = {}
    for i, key in enumerate(SLOT_KEYS):
        out[key] = None if (i >= len(raw) or raw[i] == NO_DATA) else raw[i]
    return out


def get_measuring_wheel(data_send, msg):
    """Messrad-Antwort parsen: 4 Bytes Big-Endian IEEE-754 Float, NEGATIV.

    Belegt/KORREKTUR 13.10.: c4e6fe0b -> -1847,94; c5019149 -> -2073,08;
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
    Die Status-/Datenbytes sind firmwareabhängige Zustandswerte – Semantik
    der Einzelbytes ist UNBELEGT und wird hier NICHT interpretiert (TODO).
    Gibt dict {raw, len, frame_status (None bei Rohdaten), b0..bN} zurück.
    """
    raw = bytes(msg)
    frame_status = None
    if len(raw) >= 6 and raw[0] == serial485.PACK_HEAD:
        frame_status = raw[3]
        data = _response_data(raw, FUNC_GET_BOX_STATE)
    else:
        data = raw
    out = {"raw": bytes(data), "len": len(data), "frame_status": frame_status}
    for i, byte in enumerate(bytes(data)):
        out["b%d" % i] = byte
    return out


def parse_version_sn(msg):
    """Version/SN-Antwort parsen: ASCII "153"+SN (Func 0x14).

    Belegt (K2 13.10.): Voll-Frame f7 01 19 00 14 ... 8d, Daten ASCII
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

def build_preload(mask, phase):
    """SET_PRE_LOADING (0x0d): [Slotmaske][Phase].

    Belegt: Referenz-Request 0x0d [Maske][Phase], Phase 00=Arm / 01=Disarm /
    02=Slot-Rearm (PROTOKOLL-485.md). STATUS 0xff.
    TODO: .so-Parameter force (communication_set_pre_loading force=False)
      – Wire-Abbild unbelegt, hier kein Byte dafür erfunden.
    """
    mask = _mask(mask)
    if phase not in (PRELOAD_ARM, PRELOAD_DISARM, PRELOAD_REARM):
        raise ValueError("Phase muss 0x00/0x01/0x02 sein")
    return serial485.build_frame(BOX_ADDR, FUNC_SET_PRE_LOADING, [mask, phase],
                                 status=STATUS_OPERATIONAL)


def build_preload_arm(mask):
    """Preload-Arm (0x0d [Maske 00]) – belegt, s. build_preload."""
    return build_preload(mask, PRELOAD_ARM)


def build_preload_disarm(mask):
    """Preload-Disarm (0x0d [Maske 01]) – belegt, s. build_preload."""
    return build_preload(mask, PRELOAD_DISARM)


def build_preload_rearm(mask):
    """Slot-Rearm (0x0d [Maske 02]) – belegt, s. build_preload."""
    return build_preload(mask, PRELOAD_REARM)


def build_feed_mode_enter():
    """Feed-Mode betreten (0x04 [00 01]) – K2-belegt (PROTOKOLL-485.md)."""
    return serial485.build_frame(BOX_ADDR, FUNC_SET_BOX_MODE, [0x00, 0x01],
                                 status=STATUS_OPERATIONAL)


def build_print_mode(mask):
    """Print-Mode (0x04 [Slotmaske 00]) – K2-belegt (PROTOKOLL-485.md)."""
    return serial485.build_frame(BOX_ADDR, FUNC_SET_BOX_MODE,
                                 [_mask(mask), 0x00],
                                 status=STATUS_OPERATIONAL)


def build_engage():
    """Motor-Engage (0x0f [01]) – Hi-belegt, K2-plausibel (PROTOKOLL-485.md).

    VORSICHT K2: Func-Zuordnung 0x0f auf K2 einzeln unverifiziert
    (CAN-Remap-Warnung), passt aber zum Hi-Map + K2-Logflanke.
    """
    return serial485.build_frame(BOX_ADDR, FUNC_MOTOR_ACTION, [0x01],
                                 status=STATUS_OPERATIONAL)


def build_release():
    """Motor-Release (0x0f [00]) – wie build_engage (VORSICHT s. dort)."""
    return serial485.build_frame(BOX_ADDR, FUNC_MOTOR_ACTION, [0x00],
                                 status=STATUS_OPERATIONAL)


def build_get_rfid(mask=0x0F):
    """GET_RFID-Request ([Maske], 0x02) – Live-Introspektion belegt
    (communication_get_rfid data=b'\\x01'/b'\\x0f', STATUS 0xff, Timeout 2 s)."""
    return serial485.build_frame(BOX_ADDR, FUNC_GET_RFID, [_mask(mask)],
                                 status=STATUS_OPERATIONAL)


def build_get_remain(mask=0x0F):
    """GET_REMAIN_LEN-Request ([Maske], 0x03) – wie build_get_rfid belegt."""
    return serial485.build_frame(BOX_ADDR, FUNC_GET_REMAIN_LEN, [_mask(mask)],
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
        """RFID lesen (num = Slotmaske, Default alle). Gibt Rohdaten zurück;
        parsen per get_rfid()."""
        return self.bus.transact(self.addr, FUNC_GET_RFID, [_mask(num)],
                                 status=STATUS_OPERATIONAL,
                                 timeout=TIMEOUT_OPERATIONAL)

    def communication_get_remain_len(self, num=0x0F):
        """Restmengen lesen (num = Slotmaske, Default alle)."""
        return self.bus.transact(self.addr, FUNC_GET_REMAIN_LEN, [_mask(num)],
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
    def communication_set_pre_loading(self, mask, phase):
        """Preload setzen (0x0d [Maske][Phase 00/01/02]).

        TODO: .so-Parameter action/num/force-Abbild (num=15 Default,
        force=False) – hier explizit (mask, phase), nichts erfunden.
        Hinweis: .so-Timeout SET_PRE_LOADING=300 s (lange Mechanik) –
        hier gilt der übergebene timeout, Default operational 2 s nur für
        den Frame-ACK; TODO bei echtem Preload ggf. länger wählen.
        """
        return self.bus.transact(self.addr, FUNC_SET_PRE_LOADING,
                                 [_mask(mask), phase],
                                 status=STATUS_OPERATIONAL,
                                 timeout=TIMEOUT_OPERATIONAL)

    def communication_set_box_mode_feed(self):
        """Feed-Mode betreten (0x04 [00 01])."""
        return self.bus.transact(self.addr, FUNC_SET_BOX_MODE, [0x00, 0x01],
                                 status=STATUS_OPERATIONAL,
                                 timeout=TIMEOUT_OPERATIONAL)

    def communication_set_box_mode_print(self, mask):
        """Print-Mode (0x04 [Maske 00])."""
        return self.bus.transact(self.addr, FUNC_SET_BOX_MODE,
                                 [_mask(mask), 0x00],
                                 status=STATUS_OPERATIONAL,
                                 timeout=TIMEOUT_OPERATIONAL)

    def communication_ctrl_connection_motor_action(self, act):
        """Motor-Engage (0x01) / Release (0x00) – 0x0f."""
        if act not in (0x00, 0x01):
            raise ValueError("act muss 0x00 (release) / 0x01 (engage) sein")
        return self.bus.transact(self.addr, FUNC_MOTOR_ACTION, [act],
                                 status=STATUS_OPERATIONAL,
                                 timeout=TIMEOUT_OPERATIONAL)

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
          get_rfid(b"A:AAAAAA;B:unknown;") == {"A": "AAAAAA", "B": "unknown"})
    check("rfid leer", get_rfid(b"") == {})
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
    check("builder preload-arm",
          build_preload_arm(0) == F("f7 01 05 ff 0d 01 00 b8"))
    check("builder preload-disarm",
          build_preload_disarm(0) == F("f7 01 05 ff 0d 01 01 bf"))
    check("builder preload-rearm",
          build_preload(0, 0x02) == F("f7 01 05 ff 0d 01 02 b6"))
    check("builder feed-enter",
          build_feed_mode_enter() == F("f7 01 05 ff 04 00 01 90"))
    check("builder print-mode",
          build_print_mode(0) == F("f7 01 05 ff 04 01 00 82"))
    check("builder engage", build_engage() == F("f7 01 04 ff 0f 01 b7"))
    check("builder release", build_release() == F("f7 01 04 ff 0f 00 b0"))
    check("builder get-rfid", build_get_rfid(0x0F) == F("f7 01 04 ff 02 0f 74"))
    check("builder get-remain", build_get_remain(0x0F) == F("f7 01 04 ff 03 0f 61"))
    check("builder get-wheel", build_get_wheel() == F("f7 01 04 ff 0e 01 a2"))
    check("builder get-buffer", build_get_buffer() == F("f7 01 03 ff 05 71"))
    check("builder get-hw", build_get_hardware(0) == F("f7 01 04 ff 08 00 db"))
    check("builder get-version", build_get_version() == F("f7 01 03 ff 14 06"))
    try:
        build_preload(0, 0x03)
        check("preload phasen-guard", False)
    except ValueError:
        check("preload phasen-guard", True)

    # -- 7. BoxState: Tn/Tnn-Roundtrip (Modell, kein Bus) --
    bs = BoxState()
    bs.modify_Tn_data(1, 0, 0, subpart=1, uppart=0)
    bs.modify_Tn_inner_data(1, 0, 0, "filament", "A")
    check("tn get", bs.get_Tn_data(1, 0, 0)["subpart"] == 1)
    check("tn inner", bs.get_Tn_inner_data(1, 0, 0, "filament") == "A")
    check("tn map", (1, 0, 0) in bs.generate_Tn_map())
    tnn = BoxState.tnn_key(1, 0)
    check("tnn key", tnn == "T1A")
    bs.update_Tnn_map(tnn, bs.generate_Tnn_content(tnn, rfid="AAAAAA", remain=28))
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
    ])
    act = BoxAction(bus)
    check("action rfid", get_rfid(act.communication_get_rfid(1))
          == {"B": "unknown"})
    check("action remain", get_remain_len(
        b"\x0f", act.communication_get_remain_len(0x0F))["A"] == 28)
    check("action wheel",
          abs(act.communication_measuring_wheel() - (-1847.94)) < 0.02)
    act.communication_set_pre_loading(0, PRELOAD_ARM)
    act.communication_set_box_mode_feed()
    act.communication_ctrl_connection_motor_action(0x01)
    check("action preload-payload", bus.calls[3][2] == [0x01, 0x00])
    check("action feed-payload", bus.calls[4][2] == [0x00, 0x01])
    check("action engage-payload", bus.calls[5][2] == [0x01])
    check("action status/timeout",
          all(c[3].get("status") == STATUS_OPERATIONAL
              and c[3].get("timeout") == TIMEOUT_OPERATIONAL
              for c in bus.calls))
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
