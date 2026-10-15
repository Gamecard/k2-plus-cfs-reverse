#!/usr/bin/env python3
"""motor.py – Python-Nachbau von motor_control (K2 Closed-loop-Motoren, erweitert).

Ersetzt (teilweise): motor_control_wrapper.cpython-39.so (armhf, proprietär).
Erweiterung von motor_mini.py – motor_mini.py bleibt unverändert.

Umfang (BELEGT):
- read_encoder: Encoder-Wert lesen (Func 0x06, 4-Byte-Parameter Big-Endian)
- set_mode: Modus-Umschaltung (Func 0x11, 1-2 Datenbytes)
- query_version: Version über Update-Pfad (Func 0xF0/Sub 0x00)
- write_params_raw: 8-Byte-Rohparameter (Func 0x0C, nur Länge belegt,
  Semantik UNBELEGT – kein funktionierendes Param-Schreiben)
- Update-Sequenz-Builder (Func 0xF0/Sub-Codes + 4B-Länge LE + Chunks)

Belege: PROTOKOLL-GESAMT.md Abschnitt 1.5 (Motor-Boards 0x81-0x84 = X/Y/Z/Z1,
Cut-Kalibrierung 12.10.2026) + 1.7 (485-Update-Ablauf, dekompiliert aus
mcu_util_485 FUN_00014a08) + 2.2 (1er-Komplement-Checksumme, FUN_00010e54).
Frame-Format/CRC: serial485.py (build_frame/parse_frame/crc8_cal).

OFFEN (nicht erfunden, s. TODO-Stubs unten): welcher Func-Code dem
Wrapper-Kommando `motor_version` im Normalbetrieb entspricht – implementiert
ist der belegte Update-Pfad (F0/00).
"""

from serial485 import Serial485  # noqa: F401 (für echten Betrieb)
from serial485 import build_frame, parse_frame, crc8_cal  # noqa: F401 (Selbsttest)

# Func-Codes Motor-Boards (Beleg: Cut-Kalibrierung 12.10.2026, 23:51)
FUNC_READ_ENCODER = 0x06   # 4 Bytes Big-Endian (00 00 96 43 = 38467)
FUNC_SET_PARAMS = 0x0C     # 8 Datenbytes (nur 8x 00 beobachtet, Semantik offen)
FUNC_SET_MODE = 0x11       # 1-2 Datenbytes ([01] / [02] / [02 00] / [03] gesehen)
FUNC_FW_UPDATE = 0xF0      # Update-Pfad, Sub-Codes aus Decompile (Abschnitt 1.7)

# Sub-Codes Update-Pfad (Beleg: PROTOKOLL-GESAMT.md 1.7, je 1 Byte, 3 Retries)
SUB_GET_VERSION = 0x00     # get version
SUB_REQUEST_UPDATE = 0x01  # request update
SUB_GET_SECTOR = 0x03      # get sector size
SUB_ERASE_SECTOR = 0x06    # erase private flash sector (nur CFS-Typ 1/10 belegt!)
UPDATE_RETRIES = 3         # Beleg: 1.7 "(3 Retries)"

PARAM_LEN_0C = 8           # Beleg: Live-Frame f7 81 0b 00 0c + 8 Nullbytes
FW_LEN_BYTES = 4           # Beleg: 1.7 "4 Bytes = send fw len (32-bit LE)"

MOTOR_ADDRS = (0x81, 0x82, 0x83, 0x84)  # X/Y/Z/Z1 (Beleg: Cut-Kalibrierung)
MOTOR_AXES = {0x81: "X", 0x82: "Y", 0x83: "Z", 0x84: "Z1"}


def decode_u32be(data):
    """4 Bytes Big-Endian -> int (vgl. belt_mdl.bytes_to_int).

    Beleg: Cut-Kalibrierung 00 00 96 43 -> 38467, 00 00 c8 42 -> 51266
    (KORREKTUR 13.10.: ältere Notiz las die Bytes vertauscht als 0x4396/0x42c8).
    """
    raw = bytes(data)
    if len(raw) != 4:
        raise ValueError("Antwort muss 4 Bytes haben: %r" % (raw,))
    return int.from_bytes(raw, "big")


def fw_checksum(data):
    """1er-Komplement-Summe über die FW-Daten: (~summe) & 0xFF, 1 Byte.

    Beleg: PROTOKOLL-GESAMT.md 2.2 (mcu_util FUN_00010e54: Summe aller
    Datenbytes, dann bitweises Komplement, 1 Byte angehängt).
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


def build_fw_len_payload(fw_len):
    """4 Bytes FW-Länge, 32-bit Little-Endian (Beleg: 1.7)."""
    if not 0 <= fw_len <= 0xFFFFFFFF:
        raise ValueError("FW-Länge muss 0..0xffffffff sein: %r" % (fw_len,))
    import struct
    return list(struct.pack("<I", fw_len))


def chunk_firmware(fw_bytes, sector_size):
    """Teilt die FW in Chunks der Sector-Größe (Beleg: 1.7 "Chunk-Größe aus
    sector size"). Letzter Chunk darf kürzer sein. sector_size <= 0 -> ValueError."""
    fw = bytes(fw_bytes)
    if sector_size <= 0:
        raise ValueError("sector_size muss > 0 sein: %r" % (sector_size,))
    return [fw[i:i + sector_size] for i in range(0, len(fw), sector_size)] or [b""]


def update_sequence(fw_bytes, sector_size):
    """Dokumentierte Update-Sequenz als (Schritt, Func, Payload)-Liste.

    Reihenfolge nach 1.7: version -> sector -> [erase: NUR CFS-Typ 1/10 belegt,
    für Motor-Boards UNBELEGT ob anwendbar] -> request -> len(LE) -> Daten-Chunks.
    Ende laut Decompile: "update end, wait to startup app" / "startup slave"
    (kein eigener Frame belegt – kein Builder dafür).
    """
    fw = bytes(fw_bytes)
    seq = [
        ("get-version", FUNC_FW_UPDATE, build_version_payload()),
        ("get-sector-size", FUNC_FW_UPDATE, build_sector_payload()),
        ("erase-sector (NUR CFS-Typ 1/10 belegt!)", FUNC_FW_UPDATE,
         build_erase_payload()),
        ("request-update", FUNC_FW_UPDATE, build_update_request_payload()),
        ("send-fw-len", FUNC_FW_UPDATE, build_fw_len_payload(len(fw))),
    ]
    for i, chunk in enumerate(chunk_firmware(fw, sector_size)):
        seq.append(("send-fw-chunk-%d" % i, FUNC_FW_UPDATE, list(chunk)))
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

    def read_encoder(self, register):
        """Encoder/Register lesen (Func 0x06, 4-Byte-Parameter Big-Endian).

        Beleg: Live-Requests trugen 00 00 96 43 bzw. 00 00 c8 42
        (Rolle der Bytes: vermutlich Register/Offset – Endpunkt unverifiziert).
        Der Parameter ist bewusst Pflicht: ein stiller Default (00 00 00 00)
        wurde nie auf dem Bus beobachtet.
        """
        import struct
        return decode_u32be(
            self.bus.transact(self.addr, FUNC_READ_ENCODER,
                              list(struct.pack(">I", register)),
                              retries=UPDATE_RETRIES))

    def set_mode(self, *mode_bytes):
        """Modus setzen (Func 0x11; beobachtet: [01] / [02] / [02 00] / [03]).

        Rollen der Werte UNBELEGT – reiner Passthrough, keine Validierung
        der Werte (erfundene Wertetabelle wäre geraten).
        """
        self.bus.transact(self.addr, FUNC_SET_MODE, list(mode_bytes))

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

    # --- TODO-Stubs: unbelegte Motor_Control-Methoden ---
    # Quelle der Namen: motor_control_wrapper-Strings (PROTOKOLL-GESAMT.md 4).
    # Jeder Stub nennt exakt den fehlenden Beleg. KEINER sendet Bus-Frames.

    def encoder_calibrate(self):
        """TODO: Func-Code + Payload von `motor_encoder_calibrate` UNBELEGT
        (nur Wrapper-Name bekannt, PROTOKOLL-GESAMT.md 4)."""
        raise NotImplementedError("TODO motor_encoder_calibrate: Func-Code/Payload "
                                  "unbelegt (PROTOKOLL-GESAMT.md 4)")

    def elec_offset_calibrate(self):
        """TODO: Func-Code + Payload von `motor_elec_offset_calibrate` UNBELEGT
        (nur Wrapper-Name bekannt, PROTOKOLL-GESAMT.md 4)."""
        raise NotImplementedError("TODO motor_elec_offset_calibrate: Func-Code/Payload "
                                  "unbelegt (PROTOKOLL-GESAMT.md 4)")

    def set_e_stall_mode(self, *args):
        """TODO: Abbildung von `set_e_stall_mode` auf Bus-Frames UNBELEGT
        (nur Wrapper-Name + Stall-Pins je Achse aus Config bekannt)."""
        raise NotImplementedError("TODO set_e_stall_mode: Func-Code/Payload "
                                  "unbelegt (PROTOKOLL-GESAMT.md 4)")

    def set_z_stall_mode(self, *args):
        """TODO: Abbildung von `set_z_stall_mode` auf Bus-Frames UNBELEGT
        (nur Wrapper-Name + Stall-Pins je Achse aus Config bekannt)."""
        raise NotImplementedError("TODO set_z_stall_mode: Func-Code/Payload "
                                  "unbelegt (PROTOKOLL-GESAMT.md 4)")

    def check_protection_code(self):
        """TODO: `check_protection_code`-Tabelle UNBELEGT (kein Code live
        gesehen, kein Decompile – PROTOKOLL-GESAMT.md 4)."""
        raise NotImplementedError("TODO check_protection_code: Code-Tabelle "
                                  "unbelegt (PROTOKOLL-GESAMT.md 4)")

    def get_sys_param(self, param_id):
        """TODO: `MOTOR_SYS_PARAM` (z.B. ID=70, CUT_POS 300->100 aus
        CALIBRATE_CUT_POS, 1.5) – Lese-Func-Code UNBELEGT."""
        raise NotImplementedError("TODO MOTOR_SYS_PARAM lesen: Func-Code für ID=%r "
                                  "unbelegt (PROTOKOLL-GESAMT.md 1.5/4)" % (param_id,))

    def set_sys_param(self, param_id, value):
        """TODO: `MOTOR_SYS_PARAM` – Schreib-Func-Code UNBELEGT (s. get_sys_param)."""
        raise NotImplementedError("TODO MOTOR_SYS_PARAM schreiben: Func-Code für ID=%r "
                                  "unbelegt (PROTOKOLL-GESAMT.md 1.5/4)" % (param_id,))

    def get_flash_param(self, param_id):
        """TODO: `MOTOR_FLASH_PARAM` (`motor_flash_param`) – Func-Code UNBELEGT
        (nur Wrapper-Name bekannt, PROTOKOLL-GESAMT.md 4)."""
        raise NotImplementedError("TODO MOTOR_FLASH_PARAM lesen: Func-Code für ID=%r "
                                  "unbelegt (PROTOKOLL-GESAMT.md 4)" % (param_id,))

    def set_flash_param(self, param_id, value):
        """TODO: `MOTOR_FLASH_PARAM` – Schreib-Func-Code UNBELEGT (s. get_flash_param)."""
        raise NotImplementedError("TODO MOTOR_FLASH_PARAM schreiben: Func-Code für ID=%r "
                                  "unbelegt (PROTOKOLL-GESAMT.md 4)" % (param_id,))

    def read_485_addr(self):
        """TODO: `motor_read485_addr` – Antwortformat UNBELEGT
        (nur Wrapper-Name bekannt, PROTOKOLL-GESAMT.md 4)."""
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
        belegt ist nur der Update-Pfad; nutze query_version()."""
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

    # 1. U32BE-Dekodierung (Live-Bytes, Big-Endian; Korrektur 13.10. beachtet)
    check("u32 0x9643=38467", decode_u32be(bytes.fromhex("00 00 96 43")) == 38467)
    check("u32 0xc842=51266", decode_u32be(bytes.fromhex("00 00 c8 42")) == 51266)
    check("u32 diff=12799 (Cut-Offset)", 51266 - 38467 == 12799)
    try:
        decode_u32be(b"\x00\x01\x02")
        check("u32 len-guard", False)
    except ValueError:
        check("u32 len-guard", True)

    # 2. Belegte Live-Frames aus motor_mini.py parsen + CRC + Rebuild
    for hexframe, label in [
        ("f7 81 07 00 06 00 00 96 43 09", "live 0x06-req"),
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

    # 3. Request-Builder erzeugt exakt den belegten Live-Request 0x06
    check("req 0x06 == live",
          build_frame(0x81, FUNC_READ_ENCODER, bytes.fromhex("00 00 96 43"))
          == bytes.fromhex("f7 81 07 00 06 00 00 96 43 09"))

    # 4. Motor gegen FakeBus (Antwort-Frames per build_frame erzeugt, keine
    #    erfundenen Live-CRCs – nur Request-Seite ist live belegt)
    bus = _FakeBus([
        build_frame(0x81, FUNC_READ_ENCODER, bytes.fromhex("00 00 c8 42")),
        build_frame(0x81, FUNC_SET_MODE, [0x01]),
        build_frame(0x81, FUNC_FW_UPDATE, [SUB_GET_VERSION, 0x02, 0x02]),
        build_frame(0x81, FUNC_FW_UPDATE, [SUB_GET_SECTOR, 0x00, 0x80]),
    ])
    m = Motor(bus, 0x81)
    check("axis X", m.axis == "X")
    check("read_encoder=51266", m.read_encoder(0x9643) == 51266)
    check("read_encoder req BE",
          bus.calls[0][1] == FUNC_READ_ENCODER
          and bus.calls[0][2] == [0x00, 0x00, 0x96, 0x43])
    m.set_mode(0x01)
    check("set_mode passthrough",
          bus.calls[1][1] == FUNC_SET_MODE and bus.calls[1][2] == [0x01])
    check("query_version sub 0x00",
          list(m.query_version()) == [SUB_GET_VERSION, 0x02, 0x02]
          and bus.calls[2][2] == [SUB_GET_VERSION])
    check("sector sub 0x03",
          list(m.query_sector_size()) == [SUB_GET_SECTOR, 0x00, 0x80]
          and bus.calls[3][2] == [SUB_GET_SECTOR])

    # 5. write_params_raw: nur Länge 8 belegt
    bus2 = _FakeBus([build_frame(0x82, FUNC_SET_PARAMS, [0] * 8)])
    Motor(bus2, 0x82).write_params_raw([0] * 8)
    check("params_raw 8B", bus2.calls[0][2] == [0] * 8)
    try:
        Motor(bus2, 0x82).write_params_raw([0] * 7)
        check("params_raw len-guard", False)
    except ValueError:
        check("params_raw len-guard", True)

    # 6. Update-Builder (Doku 1.7, keine Live-Frames dafür existent)
    check("payload version", build_version_payload() == [0x00])
    check("payload sector", build_sector_payload() == [0x03])
    check("payload erase", build_erase_payload() == [0x06])
    check("payload request", build_update_request_payload() == [0x01])
    check("fw_len LE", build_fw_len_payload(0x1E240) == [0x40, 0xE2, 0x01, 0x00])
    try:
        build_fw_len_payload(-1)
        check("fw_len guard", False)
    except ValueError:
        check("fw_len guard", True)
    check("checksum ~sum", fw_checksum([0x01, 0x02, 0x03]) == 0xF9)
    check("checksum leer=0xff", fw_checksum([]) == 0xFF)
    check("chunks", chunk_firmware(b"ABCDEFGHIJ", 4) == [b"ABCD", b"EFGH", b"IJ"])
    try:
        chunk_firmware(b"AB", 0)
        check("chunk guard", False)
    except ValueError:
        check("chunk guard", True)
    seq = update_sequence(b"ABCDEF", 4)
    check("seq reihenfolge",
          [s[0] for s in seq][:5] == ["get-version", "get-sector-size",
                                      "erase-sector (NUR CFS-Typ 1/10 belegt!)",
                                      "request-update", "send-fw-len"]
          and [s[0] for s in seq][5:] == ["send-fw-chunk-0", "send-fw-chunk-1"])
    check("seq len-payload", seq[4][2] == [0x06, 0x00, 0x00, 0x00])
    check("seq fun-codes",
          all(s[1] == FUNC_FW_UPDATE for s in seq))
    # Sequenz-Frames sind rundreisefähig (build -> parse)
    rt_ok = True
    for label, fun, payload in seq:
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
        Motor(None, 0x81).read_encoder()
        check("register-pflicht", False)
    except TypeError:
        check("register-pflicht", True)
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
