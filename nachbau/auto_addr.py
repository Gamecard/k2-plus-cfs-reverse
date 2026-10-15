#!/usr/bin/env python3
"""auto_addr.py – Klipper-unabhängiger Port von auto_addr_wrapper.py (K2 Plus).

1:1-Port der Protokoll-Logik (Quelle, 729 Zeilen:
 der Klartextquelle auto_addr_wrapper.py (lokal, nicht im Repo)).
Klipper-Anbindung (config/printer/reactor/gcode/configfile) ist durch
 injizierbare Abhängigkeiten ersetzt:
 - serial: Objekt mit cmd_send_data_with_response(data, timeout, retransmit)
   (z.B. Adapter um serial485.Serial485.transact)
 - store: dict-ähnlich für Uniid-Persistenz (statt CXSAVE_CONFIG)
 - Hintergrund-Schleife per threading statt Klipper-Reactor

Beleg: Live-Verhalten (Discovery-Polls, 0xa2-Sequenz, UUID) in PROTOKOLL-485.md.
Selbsttest unten OHNE Hardware (FakeSerial mit gültigen Frames).
"""

import threading
import time

# --- Konstanten 1:1 aus der Quelle ---
MODE_APP = 0
MODE_LOADER = 1

DEV_TYPE_MB = 1       # CFS-Box
DEV_TYPE_CLM = 2      # Closed-loop-Motoren
DEV_TYPE_BTM = 3      # Belt-Module
DEV_TYPE_CFS_PRO = 10  # neue CFS-Generation

POLY = 0x07

STATUS_OK = 0x00

ONLINE_STATE_OFFLINE = 0
ONLINE_STATE_ONLINE = 1
ONLINE_STATE_INIT = 2
ONLINE_STATE_WAIT_FOR_ACK = 3

MAX_GET_TIMES = 2
MAX_SET_TIMES = 2
MAX_LOST_CNT = 3

PACK_HEAD = 0xF7
BROADCAST_ADDR_MB = 0xFE
BROADCAST_ADDR_CLM = 0xFD
BROADCAST_ADDR_BTM = 0xFC
BROADCAST_ADDR_CFS_PRO = 0xFE

CMD_GET_SLAVE_INFO = 0xA1
CMD_SET_SLAVE_ADDR = 0xA0
CMD_ONLINE_CHECK = 0xA2
CMD_GET_ADDR_TABLE = 0xA3
CMD_LOADER_TO_APP = 0x0B

TIMEOUT_SHORT_TIME = 0.05
TIMEOUT_MEDIUM_TIME = 0.1
TIMEOUT_LONG_TIME = 1.0

CMD_TIMEOUT = {
    CMD_GET_SLAVE_INFO: TIMEOUT_LONG_TIME,
    CMD_SET_SLAVE_ADDR: TIMEOUT_SHORT_TIME,
    CMD_GET_ADDR_TABLE: TIMEOUT_SHORT_TIME,
    CMD_ONLINE_CHECK: TIMEOUT_MEDIUM_TIME,
    CMD_LOADER_TO_APP: TIMEOUT_SHORT_TIME,
}

MAX_DATA_LEN = 100
MAX_UNIID_LEN = 12


class DataPackage:
    def __init__(self, head, slave_addr, length, status, function_code, data, crc):
        self.head = head
        self.slave_addr = slave_addr
        self.length = length
        self.status = status
        self.function_code = function_code
        self.data = list(data)
        self.crc = crc


class FcAckData:
    def __init__(self, dev_type, mode, uniid):
        self.dev_type = dev_type
        self.mode = mode
        self.uniid = list(uniid)


class AddrManager:
    def __init__(self, addr, uniid=None, mapped=0, online=0, acked=0,
                 lost_cnt=0, mode=0, type=0):
        self.addr = addr
        self.uniid = list(uniid) if uniid else [0x00]
        self.mapped = mapped
        self.online = online
        self.acked = acked
        self.lost_cnt = lost_cnt
        self.mode = mode
        self.type = type


def default_tables():
    """Adress-Tabellen 1:1 aus der Quelle (nur MB aktiv, Rest auskommentiert)."""
    return {
        DEV_TYPE_MB: [
            AddrManager(0x01), AddrManager(0x02),
            AddrManager(0x03), AddrManager(0x04),
        ],
    }

def broadcast_addr(dev_type):
    return {
        DEV_TYPE_MB: BROADCAST_ADDR_MB,
        DEV_TYPE_CLM: BROADCAST_ADDR_CLM,
        DEV_TYPE_BTM: BROADCAST_ADDR_BTM,
        DEV_TYPE_CFS_PRO: BROADCAST_ADDR_CFS_PRO,
    }[dev_type]


def crc8_cal(data):
    """1:1 aus der Quelle (verifiziert an 10+ Live-Frames, 16 Hi-Vektoren)."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ POLY) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def cal_pack_crc(length, status, function_code, data):
    return crc8_cal([length, status, function_code] + list(data))


class AutoAddr:
    """Auto-Adress-Vergabe + Online-Überwachung. Entspricht AutoAddrWrapper."""

    def __init__(self, serial, store=None, tables=None, debug=False):
        self._serial = serial
        self.store = store if store is not None else {}
        self.tables = tables if tables is not None else default_tables()
        self.debug = debug
        self.uniid_changed = False
        self._stop = threading.Event()
        self._thread = None
        for dev_type, table in self.tables.items():
            self._load_saved_uniids(dev_type, table)

    def dprintf(self, msg):
        if self.debug:
            print("[auto_addr]", msg)

    # --- Persistenz (statt configfile + CXSAVE_CONFIG) ---
    @staticmethod
    def _store_key(dev_type):
        return {DEV_TYPE_MB: "mb_addr_table_uniids",
                DEV_TYPE_CFS_PRO: "cfs_pro_addr_table_uniids"}.get(dev_type,
                "dev_%d_uniids" % dev_type)

    def _load_saved_uniids(self, dev_type, table):
        saved = self.store.get(self._store_key(dev_type))
        if not saved:
            return
        for i, uniid in enumerate(saved):
            if i >= len(table):
                break
            table[i].uniid = list(uniid)
            if uniid and uniid[0] != 0:
                table[i].mapped = 1
                table[i].online = ONLINE_STATE_INIT

    def _save_uniids(self, dev_type, table):
        self.store[self._store_key(dev_type)] = [list(e.uniid) for e in table]

    # --- Framing (1:1 data_pack/send_package, ohne Head/CRC auf die Leitung) ---
    def data_pack(self, slave_addr, cmd, data):
        length = len(data) + 3
        return DataPackage(PACK_HEAD, slave_addr, length, STATUS_OK,
                           cmd, list(data), 0x00)

    def send_package(self, package):
        wire = ([package.slave_addr, package.length, package.status,
                 package.function_code] + list(package.data))
        package.crc = cal_pack_crc(package.length, package.status,
                                   package.function_code, package.data)
        timeout = CMD_TIMEOUT[package.function_code]
        ret = self._serial.cmd_send_data_with_response(bytes(wire), timeout, False)
        if ret is None:
            self.dprintf("Error: no response")
            return
        self.data_handler(ret)

    def data_handler(self, ret):
        package = DataPackage(ret[0], ret[1], ret[2], ret[3], ret[4],
                              [b for b in ret[5:-1]], ret[-1])
        if package.status == STATUS_OK:
            self.function_code_cb(package)
        else:
            self.dprintf("Error: status %d" % package.status)

    # --- Antwort-Dispatch (1:1 function_code_cb, gekürzt um Klipper-Logs) ---
    def _table_for(self, dev_type):
        if dev_type == DEV_TYPE_CFS_PRO and DEV_TYPE_MB in self.tables:
            return self.tables[DEV_TYPE_MB]
        return self.tables.get(dev_type)

    def function_code_cb(self, package):
        fun = package.function_code
        if fun not in (CMD_SET_SLAVE_ADDR, CMD_GET_SLAVE_INFO,
                       CMD_ONLINE_CHECK, CMD_GET_ADDR_TABLE):
            self.dprintf("unknown function code: %d" % fun)
            return
        ack = FcAckData(package.data[0], package.data[1], package.data[2:])
        table = self._table_for(ack.dev_type)
        if table is None:
            return
        for entry in table:
            if entry.uniid == ack.uniid:
                entry.mode = ack.mode
                break
        if fun == CMD_SET_SLAVE_ADDR:
            for entry in table:
                if entry.online in (ONLINE_STATE_INIT, ONLINE_STATE_WAIT_FOR_ACK) \
                        and entry.addr == package.slave_addr \
                        and entry.uniid == ack.uniid and entry.acked == 0:
                    entry.acked = 1
                    entry.online = ONLINE_STATE_ONLINE
                    entry.lost_cnt = 0
                    entry.type = ack.dev_type
                    break
        elif fun == CMD_GET_SLAVE_INFO:
            self.addr_allocate(ack.uniid, table)
        elif fun == CMD_ONLINE_CHECK:
            for entry in table:
                if entry.addr == package.slave_addr \
                        and entry.uniid == ack.uniid:
                    entry.acked = 1
                    entry.online = ONLINE_STATE_ONLINE
                    entry.lost_cnt = 0
                    entry.type = ack.dev_type
                    break
        elif fun == CMD_GET_ADDR_TABLE:
            for i, entry in enumerate(table):
                if entry.addr == package.slave_addr:
                    if any(j != i and e.uniid == ack.uniid
                           for j, e in enumerate(table)):
                        return  # Duplikat: alte Adresse behalten
                    entry.uniid = ack.uniid
                    entry.mapped = 1
                    entry.acked = 1
                    entry.online = ONLINE_STATE_ONLINE
                    entry.lost_cnt = 0
                    entry.type = ack.dev_type
                    self.uniid_changed = True
                    break
        if self.uniid_changed:
            self.uniid_changed = False
            for dev_type, tab in self.tables.items():
                if tab is table:
                    self._save_uniids(dev_type, tab)

    # --- Adress-Vergabe (1:1 addr_allocate, 3 Methoden) ---
    def addr_allocate(self, uniid, table):
        for entry in table:  # Methode 1: gemappte, offline/init-Adresse, UUID passt
            if entry.mapped == 1 and entry.online in (ONLINE_STATE_OFFLINE,
                                                      ONLINE_STATE_INIT) \
                    and uniid == entry.uniid:
                entry.mapped = 1
                entry.online = ONLINE_STATE_WAIT_FOR_ACK
                return entry.addr
        for entry in table:  # Methode 2: ungemappte Adresse
            if entry.mapped == 0:
                entry.mapped = 1
                entry.online = ONLINE_STATE_WAIT_FOR_ACK
                entry.uniid = list(uniid)
                self.uniid_changed = True
                return entry.addr
        for entry in table:  # Methode 3: gemappte Offline-Adresse, UUID egal
            if entry.mapped == 1 and entry.online in (ONLINE_STATE_OFFLINE,
                                                      ONLINE_STATE_INIT) \
                    and entry.uniid != uniid:
                entry.uniid = list(uniid)
                entry.mapped = 1
                entry.online = ONLINE_STATE_WAIT_FOR_ACK
                self.uniid_changed = True
                return entry.addr
        return -1

    # --- Kommando-Schnittstelle (1:1 communication_*) ---
    def get_slave_info(self, dev_type, extra=None):
        bc = broadcast_addr(dev_type)
        self.send_package(self.data_pack(bc, CMD_GET_SLAVE_INFO, [bc, bc]))

    def set_slave_addr(self, dev_type, addr, uniid):
        self.send_package(self.data_pack(broadcast_addr(dev_type),
                                          CMD_SET_SLAVE_ADDR, [addr] + list(uniid)))

    def online_check(self, dev_type, addr):
        self.send_package(self.data_pack(addr, CMD_ONLINE_CHECK, []))

    def get_addr_table(self, dev_type, addr):
        self.send_package(self.data_pack(addr, CMD_GET_ADDR_TABLE, []))

    def loader_to_app(self, dev_type, addr):
        self.send_package(self.data_pack(addr, CMD_LOADER_TO_APP, [0x01]))

    # --- Hintergrund-Schleife (statt Reactor; 1 s, 10 s bei Druck) ---
    def online_once(self, printing=False):
        for dev_type, table in self.tables.items():
            self.get_slave_info(dev_type)
            for entry in table:
                if entry.mapped == 1:
                    entry.lost_cnt += 1
                    self.online_check(dev_type, entry.addr)
                    if entry.lost_cnt > MAX_LOST_CNT:
                        entry.online = ONLINE_STATE_OFFLINE
                        entry.acked = 0
        return self.status_text()

    def status_text(self):
        lines = []
        for dev_type, table in self.tables.items():
            for e in table:
                lines.append("dev %d addr 0x%02X mapped %d online %d acked %d lost %d mode %d uniid %s"
                             % (dev_type, e.addr, e.mapped, e.online, e.acked,
                                e.lost_cnt, e.mode,
                                ",".join("0x%02X" % b for b in e.uniid)))
        return "\n".join(lines)

    def start_background(self, interval=1.0):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.wait(interval):
                try:
                    self.online_once()
                except Exception:
                    pass
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop_background(self):
        self._stop.set()


# --- Selbsttest OHNE Hardware: Skript-Austausch mit gültigen Frames ---
class FakeSerial:
    """Beantwortet Discovery/Online-Check wie eine CFS (UUID fix)."""

    UUID = [0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xAB]

    def __init__(self):
        self.assigned = {}
        self.sent = []

    def cmd_send_data_with_response(self, data, timeout, retransmit):
        self.sent.append((list(data), timeout, retransmit))
        addr, length, status, fun = data[0], data[1], data[2], data[3]
        payload = list(data[4:])
        if fun == CMD_GET_SLAVE_INFO:  # Broadcast -> melde Typ MB als UUID-Träger
            return bytes(_frame(0xFE, CMD_GET_SLAVE_INFO, [DEV_TYPE_MB, MODE_APP] + self.UUID))
        if fun == CMD_SET_SLAVE_ADDR:  # [addr]+uuid -> ACK mit Typ/Modus/UUID
            new_addr, uuid = payload[0], payload[1:]
            self.assigned[new_addr] = uuid
            return bytes(_frame(new_addr, CMD_SET_SLAVE_ADDR, [DEV_TYPE_MB, MODE_APP] + uuid))
        if fun == CMD_ONLINE_CHECK:
            uuid = self.assigned.get(addr, self.UUID)
            return bytes(_frame(addr, CMD_ONLINE_CHECK, [DEV_TYPE_MB, MODE_APP] + uuid))
        if fun == CMD_GET_ADDR_TABLE:
            uuid = self.assigned.get(addr, self.UUID)
            return bytes(_frame(addr, CMD_GET_ADDR_TABLE, [DEV_TYPE_MB, MODE_APP] + uuid))
        return None


def _frame(addr, fun, data, status=STATUS_OK):
    length = len(data) + 3
    body = [length, status, fun] + list(data)
    return [PACK_HEAD, addr] + body + [crc8_cal(body)]


def selftest():
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(("ok " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    # 1. CRC-Identität mit Quelle + Live-Frame
    check("crc quell-identisch",
          crc8_cal([0x07, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04]) == 0xF0)
    live = bytes.fromhex("f7 01 11 00 a2 01 00 a0 a1 a2 a3 a4 a5 a6 a7 a8 a9 aa ab f0")
    check("crc live-a2", crc8_cal(list(live[2:-1])) == live[-1])

    # 2. Discovery -> Adresse 0x01 vergeben, online, UUID persistiert
    aa = AutoAddr(FakeSerial())
    aa.get_slave_info(DEV_TYPE_MB)
    t = aa.tables[DEV_TYPE_MB]
    check("alloc addr", t[0].addr == 0x01 and t[0].online == ONLINE_STATE_WAIT_FOR_ACK)
    # SET-Antwort aus FakeSerial wurde in function_code_cb verarbeitet:
    # simuliere den vom Decompile belegten Ablauf: set -> ack -> online
    aa.send_package(aa.data_pack(BROADCAST_ADDR_MB, CMD_SET_SLAVE_ADDR,
                                 [0x01] + FakeSerial.UUID))
    check("set ack online", t[0].online == ONLINE_STATE_ONLINE)
    aa.online_check(DEV_TYPE_MB, 0x01)
    check("online bleibt", t[0].online == ONLINE_STATE_ONLINE and t[0].lost_cnt == 0)
    # GET_ADDR_TABLE-Flow sichert UUIDs (uniid_changed-Pfad)
    aa.get_addr_table(DEV_TYPE_MB, 0x01)
    check("store geschrieben", aa.store.get("mb_addr_table_uniids")[0] == FakeSerial.UUID)

    # 3. Restart lädt gespeicherte UUID (mapped=1, INIT)
    aa2 = AutoAddr(FakeSerial(), store=dict(aa.store))
    check("reload mapped", aa2.tables[DEV_TYPE_MB][0].mapped == 1)
    check("reload init", aa2.tables[DEV_TYPE_MB][0].online == ONLINE_STATE_INIT)

    # 4. Offline nach 4 verpassten Checks (FakeSerial ohne Antwort)
    class Silent:
        def cmd_send_data_with_response(self, data, timeout, retransmit):
            return None
    aa3 = AutoAddr(Silent())
    aa3.tables[DEV_TYPE_MB][0].mapped = 1
    aa3.tables[DEV_TYPE_MB][0].online = ONLINE_STATE_ONLINE
    for _ in range(5):
        aa3.online_once()
    check("offline nach timeouts",
          aa3.tables[DEV_TYPE_MB][0].online == ONLINE_STATE_OFFLINE)

    print("SELBSTTEST: %s" % ("ok" if fails == 0 else "%d FEHLER" % fails))
    return fails


if __name__ == "__main__":
    raise SystemExit(selftest())
