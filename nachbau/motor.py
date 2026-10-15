#!/usr/bin/env python3
"""motor.py – Python-Nachbau von motor_control (K2 Closed-loop-Motoren, erweitert).

Ersetzt (teilweise): motor_control_wrapper.cpython-39.so (armhf, proprietär).
Erweiterung von motor_mini.py (motor_mini.py bleibt eigenständig, gleiche
0x06-Sperre und gleiche Retry-Regel).

Umfang (BELEGT):
- decode_sys_param_float: reine Dekodier-Hilfe für die 0x06-Payload
  (4 Bytes Little-Endian-Float, KEIN Bus-Zugriff)
- read_encoder: GESPERRT (NotImplementedError, sendet nichts) – Func 0x06 ist
  KEIN Encoder-Lesen, sondern sehr wahrscheinlich Sys-Param-SCHREIBEN
  (KORREKTUR 15.10.2026, s. FUNC_SYS_PARAM)
- set_mode: Modus-Umschaltung (Func 0x11, 1-2 Datenbytes, Pflichtparameter)
- query_version: Version über Update-Pfad (Func 0xF0/Sub 0x00)
- write_params_raw: 8-Byte-Rohparameter (Func 0x0C, nur Länge belegt,
  Semantik UNBELEGT – kein funktionierendes Param-Schreiben)
- Update-Sequenz-Builder (Func 0xF0/Sub-Codes + 4B-Länge LE + Chunks +
  Abschluss Sub 0x02 "update_end")

Belege: PROTOKOLL-GESAMT.md Abschnitt 1.5 (Motor-Boards 0x81-0x84 = X/Y/Z/Z1,
Cut-Kalibrierung 12.10.2026) + 1.7 (485-Update-Ablauf, dekompiliert aus
mcu_util_485 FUN_00014a08) + 2.2 (1er-Komplement-Checksumme, Ghidra-Name
FUN_00010e54; im Live-mcu_util liegt die Routine bei 0x10e84 – evtl. andere
Basis/anderes Binary bei der Ghidra-Analyse).
Frame-Format/CRC: serial485.py (build_frame/parse_frame/crc8_cal).

OFFEN (nicht erfunden, s. TODO-Stubs unten): welcher Func-Code dem
Wrapper-Kommando `motor_version` im Normalbetrieb entspricht – implementiert
ist der belegte Update-Pfad (F0/00).
"""

from serial485 import Serial485  # noqa: F401 (für echten Betrieb)
from serial485 import build_frame, parse_frame, crc8_cal  # noqa: F401 (Selbsttest)

import struct

# Func-Codes Motor-Boards (Beleg: Cut-Kalibrierung 12.10.2026, 23:51)
# KORREKTUR 15.10.2026: 0x06 ist KEIN Encoder-Lesen. Die Live-Bytes
# 00 00 96 43 / 00 00 c8 42 sind Little-Endian-Float 300.0 / 100.0 = exakt
# `MOTOR_SYS_PARAM NUM=1 DATA=1 ID=70 PARAMS=300|100 PARAMS_TYPE=float` aus
# config/F008_CR0CN240319C13/gcode_macro.cfg:796/799 und sensorless.cfg:145/211
# (Makro CALIBRATE_CUT_POS). Passt zu k2-vanilla (PROTOKOLL-GESAMT.md 1.9:
# 0x06 = Sys-Param, Schreiben dort [01, id] + <f>). Die frühere BE-Deutung
# 38467 -> 51266 ("12799 Ticks Cut-Offset") war ein Artefakt.
# OFFEN: wo die ID 70 (0x46) steckt – der einzige dokumentierte Live-Frame
# f7 81 07 00 06 00 00 96 43 09 hat len=07 (=3+4) und gültige CRC, enthält
# also NUR die 4 Float-Bytes (kein übersehenes Byte im Frame).
# NACHTRAG 15.10.2026: ID 70 = `controller_pos_loop_pid_param_kp`
# (Positionsregler-Kp, CONFIGURABLE) laut Fremdquelle grant0013/
# k2-reverse-engineering docs/motor_params_map.md:103 (K2, nicht K2 Plus;
# Namen aus klippy.log-Auswertung) -> CALIBRATE_CUT_POS setzt Kp auf 300
# und danach zurück auf 100. Auf dem Plus belegt sind nur die Werte 300/100.
#
# Fremd-Hypothesen Motor-Kommandobytes (grant0013 docs/k2_architecture.md,
# Fremdquelle K2, auf dem Plus UNBELEGT – nur Abgleich, NICHT senden):
#   0x01 reboot, 0x03 encoder_cal, 0x04 elec_offset_cal, 0x05 control,
#   0x06 sys_param, 0x07 flash_param, 0x08 get feedback (Kandidat fürs
#   echte Encoder-Lesen), 0x0B boot, 0x0C protection status, 0x0D systemid,
#   0x0E read/set_addr, 0x0F version, 0x11 stall_mode, 0x12 dev_uuid.
FUNC_SYS_PARAM = 0x06      # Sys-Param (vermutlich Schreiben), Payload <f
FUNC_READ_ENCODER = FUNC_SYS_PARAM  # Alt-Name, NUR für Kompatibilität – nicht senden!
FUNC_SET_PARAMS = 0x0C     # 8 Datenbytes (nur 8x 00 beobachtet, Semantik offen)
                           # Fremdquelle K2 (grant0013): 0x0C = protection status
                           # – auf dem Plus unbelegt, Name bleibt bewusst neutral
FUNC_SET_MODE = 0x11       # 1-2 Datenbytes ([01] / [02] / [02 00] / [03] gesehen)
FUNC_FW_UPDATE = 0xF0      # Update-Pfad, Sub-Codes aus Decompile (Abschnitt 1.7)

# Sub-Codes Update-Pfad (Beleg: PROTOKOLL-GESAMT.md 1.7, je 1 Byte, 3 Retries)
SUB_GET_VERSION = 0x00     # get version
SUB_REQUEST_UPDATE = 0x01  # request update
SUB_GET_SECTOR = 0x03      # get sector size
SUB_ERASE_SECTOR = 0x06    # erase private flash sector (nur CFS-Typ 1/10 belegt!)
SUB_UPDATE_END = 0x02      # update_end (Beleg: mcu_util_485 @0x15a50..0x15a8c:
                           # Zustand 10, Func 0xF0, 1 Byte = 0x02, Timeout 500 ms;
                           # String "update_end" + "update end, wait to startup app")
UPDATE_END_TIMEOUT = 0.5   # s, Beleg: mov r7,#500 -> [sp,#8] vor bl 0x148a4

# Retries: mcu_util_485 bricht nach 3 Fehlversuchen ab (Zähler r6,
# `cmp r6,#3` bei 0x1562c bzw. 0x15b78). serial485.transact macht
# retries+1 Versuche (for attempt in range(retries + 1)) -> retries=2
# entspricht exakt den 3 Versuchen des Binarys. Gilt für ALLE F0-Kommandos
# (motor.py UND motor_mini.py). Nicht-Update-Kommandos (0x11/0x0C) nutzen
# den serial485-Default MAX_RETRIES=4 (Beleg: klippy.log "retries = 4").
UPDATE_RETRIES = 2

SECTOR_SIZE_MIN = 1        # Beleg: Sector-Size im Binary signed Byte (ldrsb
SECTOR_SIZE_MAX = 127      # [r4,#40] @0x15560, <=0 -> Fehlerpfad) -> 1..127;
                           # Längenbyte erlaubt ohnehin max. 252 Datenbytes.

PARAM_LEN_0C = 8           # Beleg: Live-Frame f7 81 0b 00 0c + 8 Nullbytes
FW_LEN_BYTES = 4           # Beleg: 1.7 "4 Bytes = send fw len (32-bit LE)"

MOTOR_ADDRS = (0x81, 0x82, 0x83, 0x84)  # X/Y/Z/Z1 (Beleg: Cut-Kalibrierung)
MOTOR_AXES = {0x81: "X", 0x82: "Y", 0x83: "Z", 0x84: "Z1"}


def decode_sys_param_float(data):
    """4 Bytes 0x06-Payload -> Float (Little-Endian, `<f`). KEIN Bus-Zugriff.

    Beleg: Live 00 00 96 43 -> 300.0, 00 00 c8 42 -> 100.0 = MOTOR_SYS_PARAM
    ID=70 PARAMS=300/100 PARAMS_TYPE=float (gcode_macro.cfg:796/799,
    sensorless.cfg:145/211). WIDERLEGT 15.10.2026: frühere BE-Deutung
    38467/51266 (decode_u32be entfernt).
    """
    raw = bytes(data)
    if len(raw) != 4:
        raise ValueError("0x06-Payload muss 4 Bytes haben: %r" % (raw,))
    return struct.unpack("<f", raw)[0]


def build_sys_param_write_payload(param_id, value):
    """TODO: Sys-Param-Schreiben (Func 0x06) – Frame-Struktur UNBELEGT.

    Belegt sind nur die 4 Float-Bytes; wo ID/NUM/DATA stehen, ist offen
    (k2-vanilla nutzt [01, id] + <f, auf dem K2 Plus aber nicht live gesehen).
    Kein Builder, damit nie ein geratener Schreib-Frame entsteht.
    """
    raise NotImplementedError("TODO Sys-Param-Schreiben (0x06): Position von "
                              "ID=%r im Frame unbelegt (nur 4 Float-Bytes live)"
                              % (param_id,))


def fw_checksum(data):
    """1er-Komplement-Summe über die FW-Daten: (~summe) & 0xFF, 1 Byte.

    Beleg: PROTOKOLL-GESAMT.md 2.2 (mcu_util FUN_00010e54 laut Ghidra; im
    Live-mcu_util bei 0x10e84: Summe aller Datenbytes, dann bitweises
    Komplement, 1 Byte angehängt).
    HINWEIS: Für 485-Chunks (1.7 "send fw data") ist NICHT separat belegt,
    ob/wie dieses Byte mitgesendet wird – Builder hängen es NICHT automatisch an.
    """
    return (~(sum(bytes(data)) & 0xFF)) & 0xFF


# --- Update-Sequenz-Builder (Beleg: PROTOKOLL-GESAMT.md 1.7) ---
# Jede Funktion liefert die Payload-Liste für Func 0xF0; Versand via
# bus.transact(addr, FUNC_FW_UPDATE, payload, retries=UPDATE_RETRIES).

def build_version_payload():
    """Sub 0x00 (1B) = get version."""
    return [SUB_GET_VERSION]


def build_sector_payload():
    """Sub 0x03 (1B) = get sector size (Antwort legt die Chunk-Größe fest)."""
    return [SUB_GET_SECTOR]


def build_erase_payload():
    """Sub 0x06 (1B) = erase private flash sector.

    WARNUNG: Nur für CFS-Typ 1/10 belegt (1.7) – ob Motor-Boards diesen
    Sub-Code unterstützen, ist UNBELEGT. Nicht blind an 0x81-0x84 senden.
    """
    return [SUB_ERASE_SECTOR]


def build_update_request_payload():
    """Sub 0x01 (1B) = request update."""
    return [SUB_REQUEST_UPDATE]


def build_update_end_payload():
    """Sub 0x02 (1B) = update_end (Beleg: mcu_util_485 Zustand 10 @0x15a64:
    mov r3,#2 -> strb [sp,#0x48], Länge 1, Func 0xF0, Timeout 500 ms)."""
    return [SUB_UPDATE_END]


def build_fw_len_payload(fw_len):
    """4 Bytes FW-Länge, 32-bit Little-Endian (Beleg: 1.7)."""
    if isinstance(fw_len, bool) or not isinstance(fw_len, int):
        raise ValueError("FW-Länge muss int sein: %r" % (fw_len,))
    if not 0 <= fw_len <= 0xFFFFFFFF:
        raise ValueError("FW-Länge muss 0..0xffffffff sein: %r" % (fw_len,))
    return list(struct.pack("<I", fw_len))


def _check_sector_size(sector_size):
    if isinstance(sector_size, bool) or not isinstance(sector_size, int):
        raise ValueError("sector_size muss int sein: %r" % (sector_size,))
    if not SECTOR_SIZE_MIN <= sector_size <= SECTOR_SIZE_MAX:
        raise ValueError("sector_size muss %d..%d sein (signed Byte im Binary): %r"
                         % (SECTOR_SIZE_MIN, SECTOR_SIZE_MAX, sector_size))


def chunk_firmware(fw_bytes, sector_size):
    """Teilt die FW in Chunks der Sector-Größe (Beleg: 1.7 "Chunk-Größe aus
    sector size"). Letzter Chunk darf kürzer sein.

    sector_size außerhalb 1..127 bzw. bool/float -> ValueError; leere FW ->
    ValueError (ein leerer 0xF0-Frame ist nicht belegt)."""
    _check_sector_size(sector_size)
    fw = bytes(fw_bytes)
    if not fw:
        raise ValueError("leere Firmware")
    return [fw[i:i + sector_size] for i in range(0, len(fw), sector_size)]


def update_sequence(fw_bytes, sector_size, erase=False):
    """Dokumentierte Update-Sequenz als (Schritt, Func, Payload)-Liste.

    Reihenfolge nach 1.7: version -> sector -> [erase, NUR mit erase=True;
    belegt nur für CFS-Typ 1/10, für Motor-Boards UNBELEGT] -> request ->
    len(LE) -> Daten-Chunks -> update-end (Sub 0x02, Beleg: mcu_util_485
    @0x15a64, Timeout UPDATE_END_TIMEOUT).
    """
    fw = bytes(fw_bytes)
    chunks = chunk_firmware(fw, sector_size)  # prüft leer + sector_size
    seq = [
        ("get-version", FUNC_FW_UPDATE, build_version_payload()),
        ("get-sector-size", FUNC_FW_UPDATE, build_sector_payload()),
    ]
    if erase:
        seq.append(("erase-sector (NUR CFS-Typ 1/10 belegt!)", FUNC_FW_UPDATE,
                    build_erase_payload()))
    seq += [
        ("request-update", FUNC_FW_UPDATE, build_update_request_payload()),
        ("send-fw-len", FUNC_FW_UPDATE, build_fw_len_payload(len(fw))),
    ]
    for i, chunk in enumerate(chunks):
        seq.append(("send-fw-chunk-%d" % i, FUNC_FW_UPDATE, list(chunk)))
    seq.append(("update-end", FUNC_FW_UPDATE, build_update_end_payload()))
    return seq


class Motor:
    """Closed-loop-Motor (belegte Operationen + Update-Pfad + TODO-Stubs)."""

    def __init__(self, bus, addr):
        if addr not in MOTOR_ADDRS:
            raise ValueError("unbekannte Motor-Adresse: 0x%02x" % addr)
        self.bus = bus
        self.addr = addr

    @property
    def axis(self):
        """Achsname X/Y/Z/Z1 (Beleg: Cut-Kalibrierung)."""
        return MOTOR_AXES[self.addr]

    # --- Belegte Operationen (aus motor_mini.py übernommen) ---

    def read_encoder(self, *args, **kwargs):
        """GESPERRT (15.10.2026) – sendet KEINEN Frame.

        Func 0x06 ist vermutlich Sys-Param-SCHREIBEN (Live-Bytes = Float
        300.0/100.0 = MOTOR_SYS_PARAM ID=70), Encoder-Lesen ist unbelegt.
        Senden könnte Motorparameter ändern.
        Fremdquelle K2 (grant0013): 0x08 = get feedback – Kandidat fürs echte
        Encoder-Lesen, auf dem Plus unbelegt (bleibt gesperrt bis Live-Beleg).
        """
        raise NotImplementedError(
            "read_encoder gesperrt: Func 0x06 ist vermutlich Sys-Param-Schreiben "
            "(Live-Payload = LE-Float 300.0/100.0 = MOTOR_SYS_PARAM ID=70), "
            "kein Encoder-Lesen; Encoder-Lesen ist unbelegt, Senden könnte "
            "Motorparameter ändern")

    def set_mode(self, mode, *extra):
        """Modus setzen (Func 0x11; beobachtet: [01] / [02] / [02 00] / [03]).

        Mindestens 1 Byte Pflicht (leerer 0x11-Frame nie beobachtet), jedes
        Byte 0..255 (int, kein bool). Rollen der Werte UNBELEGT – keine
        Wertetabelle (wäre geraten). Retries: serial485-Default.
        """
        raw = [mode] + list(extra)
        for b in raw:
            if isinstance(b, bool) or not isinstance(b, int) or not 0 <= b <= 0xFF:
                raise ValueError("Modus-Byte muss int 0..255 sein: %r" % (b,))
        self.bus.transact(self.addr, FUNC_SET_MODE, raw)

    def query_version(self):
        """Version über Update-Pfad abfragen (Func 0xF0/Sub 0x00). Rohdaten."""
        return self.bus.transact(self.addr, FUNC_FW_UPDATE,
                                 build_version_payload(),
                                 retries=UPDATE_RETRIES)

    def write_params_raw(self, data8):
        """8 Rohbytes senden (Func 0x0C). Belegt ist NUR die Länge 8
        (Live-Frame: 8 Nullbytes an alle Boards) – die Semantik der Bytes
        ist UNBELEGT, daher kein param-Namen/keine Werte-Deutung."""
        raw = list(data8)
        if len(raw) != PARAM_LEN_0C:
            raise ValueError("0x0c braucht genau 8 Bytes: %r" % (raw,))
        self.bus.transact(self.addr, FUNC_SET_PARAMS, raw)

    # --- Update-Pfad als Methoden (Sequenz s. update_sequence) ---

    def query_sector_size(self):
        """Sector-Größe abfragen (Func 0xF0/Sub 0x03). Rohdaten."""
        return self.bus.transact(self.addr, FUNC_FW_UPDATE,
                                 build_sector_payload(),
                                 retries=UPDATE_RETRIES)

    def request_update(self):
        """Update anmelden (Func 0xF0/Sub 0x01)."""
        self.bus.transact(self.addr, FUNC_FW_UPDATE,
                          build_update_request_payload(),
                          retries=UPDATE_RETRIES)

    def send_fw_len(self, fw_len):
        """FW-Länge senden (4B LE, Func 0xF0)."""
        self.bus.transact(self.addr, FUNC_FW_UPDATE,
                          build_fw_len_payload(fw_len),
                          retries=UPDATE_RETRIES)

    def update_end(self):
        """Update abschließen (Func 0xF0/Sub 0x02, Timeout 500 ms,
        Beleg: mcu_util_485 Zustand 10 @0x15a64)."""
        self.bus.transact(self.addr, FUNC_FW_UPDATE,
                          build_update_end_payload(),
                          timeout=UPDATE_END_TIMEOUT,
                          retries=UPDATE_RETRIES)

    # --- TODO-Stubs: unbelegte Motor_Control-Methoden ---
    # Quelle der Namen: motor_control_wrapper-Strings (PROTOKOLL-GESAMT.md 4).
    # Jeder Stub nennt exakt den fehlenden Beleg. KEINER sendet Bus-Frames.

    def encoder_calibrate(self):
        """TODO: Func-Code + Payload von `motor_encoder_calibrate` UNBELEGT
        (nur Wrapper-Name bekannt, PROTOKOLL-GESAMT.md 4).
        Fremdquelle K2 (grant0013): 0x03 encoder_cal – auf dem Plus unbelegt."""
        raise NotImplementedError("TODO motor_encoder_calibrate: Func-Code/Payload "
                                  "unbelegt (PROTOKOLL-GESAMT.md 4)")

    def elec_offset_calibrate(self):
        """TODO: Func-Code + Payload von `motor_elec_offset_calibrate` UNBELEGT
        (nur Wrapper-Name bekannt, PROTOKOLL-GESAMT.md 4).
        Fremdquelle K2 (grant0013): 0x04 elec_offset_cal – auf dem Plus unbelegt."""
        raise NotImplementedError("TODO motor_elec_offset_calibrate: Func-Code/Payload "
                                  "unbelegt (PROTOKOLL-GESAMT.md 4)")

    def set_e_stall_mode(self, *args):
        """TODO: Abbildung von `set_e_stall_mode` auf Bus-Frames UNBELEGT
        (nur Wrapper-Name + Stall-Pins je Achse aus Config bekannt).
        Fremdquelle K2 (grant0013): 0x11 stall_mode – auf dem Plus unbelegt
        (live gesehen nur 0x11 mit [01]/[02 00]/[03], s. set_mode)."""
        raise NotImplementedError("TODO set_e_stall_mode: Func-Code/Payload "
                                  "unbelegt (PROTOKOLL-GESAMT.md 4)")

    def set_z_stall_mode(self, *args):
        """TODO: Abbildung von `set_z_stall_mode` auf Bus-Frames UNBELEGT
        (nur Wrapper-Name + Stall-Pins je Achse aus Config bekannt).
        Fremdquelle K2 (grant0013): 0x11 stall_mode – auf dem Plus unbelegt."""
        raise NotImplementedError("TODO set_z_stall_mode: Func-Code/Payload "
                                  "unbelegt (PROTOKOLL-GESAMT.md 4)")

    def check_protection_code(self):
        """TODO: `check_protection_code`-Tabelle UNBELEGT (kein Code live
        gesehen, kein Decompile – PROTOKOLL-GESAMT.md 4).
        Fremdquelle K2 (grant0013): 0x0C protection status – auf dem Plus
        unbelegt (live nur 8x 00 an alle Boards, s. write_params_raw)."""
        raise NotImplementedError("TODO check_protection_code: Code-Tabelle "
                                  "unbelegt (PROTOKOLL-GESAMT.md 4)")

    def get_sys_param(self, param_id):
        """TODO: `MOTOR_SYS_PARAM` (z.B. ID=70, 300->100 aus
        CALIBRATE_CUT_POS, 1.5) – Func 0x06 vermutlich (Live-Payload = Float),
        Lese-Form auf dem K2 Plus UNBELEGT. ID 70 = controller_pos_loop_pid_param_kp
        (Positionsregler-Kp) laut grant0013 motor_params_map.md:103 (Fremdquelle K2)."""
        raise NotImplementedError("TODO MOTOR_SYS_PARAM lesen: Func-Code für ID=%r "
                                  "unbelegt (PROTOKOLL-GESAMT.md 1.5/4)" % (param_id,))

    def set_sys_param(self, param_id, value):
        """TODO: `MOTOR_SYS_PARAM` schreiben – Func 0x06 + <f-Wert belegt,
        Position der ID im Frame UNBELEGT (s. build_sys_param_write_payload)."""
        raise NotImplementedError("TODO MOTOR_SYS_PARAM schreiben: Func-Code für ID=%r "
                                  "unbelegt (PROTOKOLL-GESAMT.md 1.5/4)" % (param_id,))

    def get_flash_param(self, param_id):
        """TODO: `MOTOR_FLASH_PARAM` (`motor_flash_param`) – Func-Code UNBELEGT
        (nur Wrapper-Name bekannt, PROTOKOLL-GESAMT.md 4).
        Fremdquelle K2 (grant0013): 0x07 flash_param – auf dem Plus unbelegt."""
        raise NotImplementedError("TODO MOTOR_FLASH_PARAM lesen: Func-Code für ID=%r "
                                  "unbelegt (PROTOKOLL-GESAMT.md 4)" % (param_id,))

    def set_flash_param(self, param_id, value):
        """TODO: `MOTOR_FLASH_PARAM` – Schreib-Func-Code UNBELEGT (s. get_flash_param)."""
        raise NotImplementedError("TODO MOTOR_FLASH_PARAM schreiben: Func-Code für ID=%r "
                                  "unbelegt (PROTOKOLL-GESAMT.md 4)" % (param_id,))

    def read_485_addr(self):
        """TODO: `motor_read485_addr` – Antwortformat UNBELEGT
        (nur Wrapper-Name bekannt, PROTOKOLL-GESAMT.md 4).
        Fremdquelle K2 (grant0013): 0x0E read/set_addr, 0x12 dev_uuid – auf dem
        Plus unbelegt."""
        raise NotImplementedError("TODO motor_read485_addr: Antwortformat "
                                  "unbelegt (PROTOKOLL-GESAMT.md 4)")

    def pin_io(self, *args):
        """TODO: Pin-IO (Stall-Pins je Achse aus Config) – Bus-Abbildung
        UNBELEGT (kein Pin-Frame live gesehen, PROTOKOLL-GESAMT.md 4)."""
        raise NotImplementedError("TODO Pin-IO: Bus-Abbildung unbelegt "
                                  "(PROTOKOLL-GESAMT.md 4)")

    def motor_version_normal(self):
        """TODO: welcher Func dem Wrapper-Kommando `motor_version` im
        Normalbetrieb entspricht, ist OFFEN (motor_mini.py-Docstring) –
        belegt ist nur der Update-Pfad; nutze query_version().
        Fremdquelle K2 (grant0013): 0x0F version – auf dem Plus unbelegt."""
        raise NotImplementedError("TODO motor_version (Normalbetrieb): Func-Code offen; "
                                  "belegt nur F0/00 via query_version()")


# --- Selbsttest (keine Hardware, kein Netzwerk nötig) ---
class _FakeBus:
    """Spielt aufgezeichnete Antworten ab (Framing wie Serial485.transact)."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def transact(self, addr, fun, data=(), **kwargs):
        self.calls.append((addr, fun, list(data), dict(kwargs)))
        assert self.answers, "FakeBus: unerwarteter Sendeversuch"
        raw = self.answers.pop(0)
        rfun, rdata = parse_frame(raw)
        assert rfun == fun, (rfun, fun)
        return rdata


def selftest():
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(("ok " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    # 1. 0x06-Payload = LE-Float (echter Beleg gegen die Config:
    #    MOTOR_SYS_PARAM ID=70 PARAMS=300/100, gcode_macro.cfg:796/799)
    check("0x06 00 00 96 43 = 300.0",
          decode_sys_param_float(bytes.fromhex("00 00 96 43")) == 300.0)
    check("0x06 00 00 c8 42 = 100.0",
          decode_sys_param_float(bytes.fromhex("00 00 c8 42")) == 100.0)
    _, live_data = parse_frame(bytes.fromhex("f7 81 07 00 06 00 00 96 43 09"))
    check("live 0x06-Frame -> 300.0 (len 07 = nur 4 Datenbytes)",
          decode_sys_param_float(live_data) == 300.0 and len(live_data) == 4)
    try:
        decode_sys_param_float(b"\x00\x01\x02")
        check("float len-guard", False)
    except ValueError:
        check("float len-guard", True)
    try:
        build_sys_param_write_payload(70, 300.0)
        check("sys-param-write builder = TODO", False)
    except NotImplementedError:
        check("sys-param-write builder = TODO", True)

    # 2. Belegte Live-Frames aus motor_mini.py parsen + CRC + Rebuild
    for hexframe, label in [
        ("f7 81 07 00 06 00 00 96 43 09", "live 0x06 (sys-param 300.0)"),
        ("f7 81 04 00 11 01 1d", "live 0x11 [01]"),
        ("f7 81 04 00 11 02 14", "live 0x11 [02]"),
        ("f7 81 0b 00 0c 00 00 00 00 00 00 00 00 fb", "live 0x0c 8x00"),
    ]:
        raw = bytes.fromhex(hexframe)
        try:
            fun, data = parse_frame(raw)
            check("parse %s" % label, True)
            check("rebuild %s" % label,
                  build_frame(raw[1], fun, data, status=raw[3]) == raw)
        except ValueError:
            check("parse %s" % label, False)
            check("rebuild %s" % label, False)

    # 3. read_encoder gesperrt: wirft, OHNE etwas zu senden
    nobus = _FakeBus([])
    for args in [(), (0x9643,)]:
        try:
            Motor(nobus, 0x81).read_encoder(*args)
            check("read_encoder%r wirft" % (args,), False)
        except NotImplementedError:
            check("read_encoder%r wirft" % (args,), True)
    check("read_encoder sendet nichts", nobus.calls == [])

    # 4. Motor gegen FakeBus (Antwort-Frames per build_frame erzeugt, keine
    #    erfundenen Live-CRCs – nur Request-Seite ist live belegt)
    bus = _FakeBus([
        build_frame(0x81, FUNC_SET_MODE, [0x01]),
        build_frame(0x81, FUNC_FW_UPDATE, [SUB_GET_VERSION, 0x02, 0x02]),
        build_frame(0x81, FUNC_FW_UPDATE, [SUB_GET_SECTOR, 0x00, 0x80]),
        build_frame(0x81, FUNC_FW_UPDATE, [SUB_UPDATE_END]),
        build_frame(0x81, FUNC_SET_MODE, [0x02, 0x00]),
    ])
    m = Motor(bus, 0x81)
    check("axis X", m.axis == "X")
    m.set_mode(0x01)
    check("set_mode passthrough",
          bus.calls[0][1] == FUNC_SET_MODE and bus.calls[0][2] == [0x01]
          and "retries" not in bus.calls[0][3])
    check("query_version sub 0x00",
          list(m.query_version()) == [SUB_GET_VERSION, 0x02, 0x02]
          and bus.calls[1][2] == [SUB_GET_VERSION]
          and bus.calls[1][3].get("retries") == UPDATE_RETRIES)
    check("sector sub 0x03",
          list(m.query_sector_size()) == [SUB_GET_SECTOR, 0x00, 0x80]
          and bus.calls[2][2] == [SUB_GET_SECTOR])
    m.update_end()
    check("update_end sub 0x02 / 500 ms",
          bus.calls[3][2] == [SUB_UPDATE_END]
          and bus.calls[3][3].get("timeout") == 0.5)
    m.set_mode(0x02, 0x00)
    check("set_mode 2 Bytes", bus.calls[4][2] == [0x02, 0x00])
    for bad in [(), (256,), (-1,), (True,), (1.0,), (0x02, 300)]:
        try:
            m.set_mode(*bad)
            check("set_mode guard %r" % (bad,), False)
        except (TypeError, ValueError):
            check("set_mode guard %r" % (bad,), True)
    check("set_mode guards senden nichts", len(bus.calls) == 5)
    check("retries=2 -> 3 Versuche wie Binary", UPDATE_RETRIES + 1 == 3)

    # 5. write_params_raw: nur Länge 8 belegt
    bus2 = _FakeBus([build_frame(0x82, FUNC_SET_PARAMS, [0] * 8)])
    Motor(bus2, 0x82).write_params_raw([0] * 8)
    check("params_raw 8B", bus2.calls[0][2] == [0] * 8)
    try:
        Motor(bus2, 0x82).write_params_raw([0] * 7)
        check("params_raw len-guard", False)
    except ValueError:
        check("params_raw len-guard", True)

    # 6. Update-Builder (Doku 1.7 + Disassembly, keine Live-Frames dafür)
    check("payload version", build_version_payload() == [0x00])
    check("payload sector", build_sector_payload() == [0x03])
    check("payload erase", build_erase_payload() == [0x06])
    check("payload request", build_update_request_payload() == [0x01])
    check("payload update-end", build_update_end_payload() == [0x02])
    check("fw_len LE", build_fw_len_payload(0x1E240) == [0x40, 0xE2, 0x01, 0x00])
    for bad in [-1, 2 ** 32, True, 1.0]:
        try:
            build_fw_len_payload(bad)
            check("fw_len guard %r" % (bad,), False)
        except ValueError:
            check("fw_len guard %r" % (bad,), True)
    check("checksum ~sum", fw_checksum([0x01, 0x02, 0x03]) == 0xF9)
    check("checksum leer=0xff", fw_checksum([]) == 0xFF)
    check("chunks", chunk_firmware(b"ABCDEFGHIJ", 4) == [b"ABCD", b"EFGH", b"IJ"])
    check("chunk 127 ok", len(chunk_firmware(bytes(300), 127)) == 3)
    for bad in [0, -1, 128, 300, True, 2.5, 4.0]:
        try:
            chunk_firmware(b"AB", bad)
            check("chunk guard %r" % (bad,), False)
        except ValueError:
            check("chunk guard %r" % (bad,), True)
    for fw in [b"", []]:
        try:
            update_sequence(fw, 64)
            check("leere FW guard %r" % (fw,), False)
        except ValueError:
            check("leere FW guard %r" % (fw,), True)
    seq = update_sequence(b"ABCDEF", 4)
    check("seq reihenfolge (ohne erase)",
          [s[0] for s in seq] == ["get-version", "get-sector-size",
                                  "request-update", "send-fw-len",
                                  "send-fw-chunk-0", "send-fw-chunk-1",
                                  "update-end"])
    check("seq len-payload", seq[3][2] == [0x06, 0x00, 0x00, 0x00])
    check("seq ende sub 0x02", seq[-1][2] == [SUB_UPDATE_END])
    check("seq fun-codes", all(s[1] == FUNC_FW_UPDATE for s in seq))
    seq_e = update_sequence(b"ABCDEF", 4, erase=True)
    check("seq erase nur auf Wunsch",
          seq_e[2] == ("erase-sector (NUR CFS-Typ 1/10 belegt!)",
                       FUNC_FW_UPDATE, [SUB_ERASE_SECTOR])
          and len(seq_e) == len(seq) + 1
          and all(s[2] != [SUB_ERASE_SECTOR] for s in seq))
    # Sequenz-Frames sind rundreisefähig (build -> parse), auch bei 127er-Chunks
    rt_ok = True
    for label, fun, payload in seq + update_sequence(bytes(range(256)), 127):
        raw = build_frame(0x83, fun, payload)
        rfun, rdata = parse_frame(raw)
        rt_ok &= (rfun == fun and list(rdata) == list(payload))
    check("seq roundtrip", rt_ok)

    # 7. Negativtests
    try:
        Motor(None, 0x05)
        check("addr-guard", False)
    except ValueError:
        check("addr-guard", True)
    try:
        parse_frame(bytes.fromhex("f7 81"))
        check("kurz-frame", False)
    except ValueError:
        check("kurz-frame", True)
    corrupt = bytearray(bytes.fromhex("f7 81 07 00 06 00 00 96 43 09"))
    corrupt[-1] ^= 0xFF
    try:
        parse_frame(bytes(corrupt))
        check("crc-fehler", False)
    except ValueError:
        check("crc-fehler", True)

    # 8. TODO-Stubs werfen NotImplementedError (kein stilles Falsch-Verhalten)
    stub_ok = True
    mm = Motor(_FakeBus([]), 0x84)
    for call in [mm.encoder_calibrate, mm.elec_offset_calibrate,
                 mm.check_protection_code, mm.read_485_addr, mm.pin_io,
                 mm.motor_version_normal,
                 lambda: mm.set_e_stall_mode(1), lambda: mm.set_z_stall_mode(1),
                 lambda: mm.get_sys_param(70), lambda: mm.set_sys_param(70, 100),
                 lambda: mm.get_flash_param(0), lambda: mm.set_flash_param(0, 1)]:
        try:
            call()
            stub_ok = False
        except NotImplementedError:
            pass
    check("stubs not-implemented", stub_ok)

    print("SELBSTTEST: %s" % ("ok" if fails == 0 else "%d FEHLER" % fails))
    return fails


if __name__ == "__main__":
    raise SystemExit(selftest())
