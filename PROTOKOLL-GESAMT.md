# K2 Plus – Gesamt-Protokoll-Inventar (Stand 13.10.2026, Live-Log-Abgleich 15.10.2026)

Audit/Korrekturen der Nachbau-Module: `/home/pro/k2-fw-live-1.1.7.0/AUDIT-UND-AENDERUNGEN.md`.

Drucker: Creality K2 Plus (`K2Plus-XXXX`), Upper-Board T113 (Tina/OpenWrt 21.02, Kernel 5.4.61 armv7l)
FW live: **1.1.7.0** · Image-Dump 1.1.3.13 (entpackt, Verzeichnis gelöscht; Original in `~/Downloads/`)
Alle Angaben mit Belegquelle (Log-Frame, Decompile, Query, Config).

**LIVE-LOGS 15.10.2026** (`live-logs-2026-10-15/`, nur lesend vom Drucker, lokal, nicht
fürs Repo): Ports **mcu = `/dev/ttyS2`, nozzle_mcu = `/dev/ttyS3`, serial_485 = `/dev/ttyS5`**
(`klippy.log:1464/1719/1724`; grant/K2: Main-MCU ttyS7, Nozzle ttyS1 – gilt nicht für den Plus).
Versionen: OpenWrt 21.02-SNAPSHOT r0-4487dfa83, Board `t113_i-…`, Klipper `09faed31-dirty`,
MCU-FW mcu/nozzle `1.1.0.48-293-g493f9a0f-dirty-20241220` (GD32F303xE/xB), rpi `K2_1.1.3.2-…`.
**Display-FW (c440x) 1.1.4.12 → 1.1.7.0 am 12.10.2026 (~00:39 Log-Uhr)**; Box-App 113 → 153 am
selben Tag (s. 1.3). `.so`-md5 (box/filament_rack/motor_control/prtouch_v3/serial_485)
**identisch mit `rootfs-live/`**; `prtouch_v1/v2_wrapper.so` fehlen lokal (bei Bedarf
nachkopieren). Log-Uhr = UTC+8. Auswertung: PROTOKOLL-485.md §3.1 (Motoren) und §6 (CFS).

---

## 1. RS485-Bus (`/dev/ttyS5`, 230400 baud) – DAS proprietäre Herzstück

### 1.1 Frame-Format (allgemein)

```
[head 0xF7][addr][len][star][fun][data...][crc]
len = len(data) + 3
crc = CRC-8/ATM (poly 0x07, init 0x00) über [len..data]  ← GEKNACKT 12.10.2026,
      head/addr NICHT enthalten; an 9+ Live-Frames verifiziert
```

### 1.2 Busteilnehmer (Adressen)

| Adresse | Gerät | Belegt durch |
|---|---|---|
| `0x01` | CFS/Box (MultiColorMeterialBox) | Live-Frames |
| `0x11` | Filament-Rack (Trockner) | Config `filament_rack addr: 17` – **noch NICHT live gesehen** (auch nicht in den Logs 05.–16.10.) |
| `0x21` | Belt-Modul mdlx | Live-Frames |
| `0x22` | Belt-Modul mdly | Code (`config_addr`) |
| `0x81..0x84` | Closed-loop Motor-Boards (X/Y/Z/Z1) | Cut-Kalibrierung 23:51; LIVE 15.10.2026: alle 4 am ttyS5 (Boot-Enumeration) |
| `0x85` | Extruder-Motor (Stock-Mapping) | **LIVE 15.10.2026: antwortet auf ttyS5 nie**; Extruder nur via `transparent_send` (nozzle_mcu) unter 0x81 |
| `0xff` | Broadcast (`0E 01` Adresse setzen, `0B`) | live, ohne Antwort |
| `0xfe` | ADDR_CFS-Discovery (Func `0xa1`, Daten `fe 01`) | Dekompiliert |
| `0xfd` | ADDR_MOTOR-Discovery (Func `0xa1`) | Dekompiliert |
| `0xfc` | ADDR_BELT-Discovery (Func `0xa1`) | Dekompiliert |
| `0xfb` | ADDR_RFID-Discovery (Func `0xa1`) | Dekompiliert |
| `0xea`/`0xeb` | CFS-App-Broadcasts (Ghidra) | **LIVE 15.10.2026: nie gesehen** |

Retries (LIVE BELEGT 15.10.2026): `retries = 4` = 5 Sendungen (je ~2 s); CFS-Operational
retries=4 außer **0x0a-Poll retries=0**; Auto-Addr 0xa0–0xa3, Motoren 0x81–0x85,
Broadcasts: retries=0; Belt: 4. Tabelle PROTOKOLL-485.md §6.2.

### 1.3 Func-Codes – CFS/Box (`0x01`)

```
0x02 = GET_RFID        Antwort ASCII "<Slot>:<hex|unknown|busy>;"
0x03 = GET_REMAIN_LEN  Request [Slotmaske], Antwort positional A..D (4 Bytes);
       0xff = keine Daten, 0 = kein RFID-Restwert (NICHT „leer“ – Slot B ist
       mit remain 0 gefahren; KORRIGIERT 15.10.2026)
       BELEGT 15.10.2026 (Ghidra): Wire-0 = Sensor A ohne Filament (oder 0 %),
       0xff = nicht in Maske / preloadet / Filament ohne RFID-Daten
0x04 = SET_BOX_MODE    [00 01] Feed-Modus, [Slotmaske 00] Print-Modus
       (BELEGT 15.10.2026: [mask, mode] mode 0 = PRINT, mode 1 = IDLE)
0x05 = GET_BUFFER_STATE Box-Bufferstatus (früher fälschlich CUT_STATE)
0x08 = ~~GET_HARDWARE_STATUS Toolhead-/Hardware-Sensorstatus~~ KORRIGIERT 15.10.2026
       (Host-Name live): GET_FILAMENT_SENSOR_STATE [00] Sensor A / [01] Sensor B (Hub)
0x0a = Herzschlag/Poll 6 Datenbytes Zustand, alle ~4s (retries=0; FW 113: 4 Datenbytes;
       LIVE 15.10.2026: Byte 5 immer 0 → „RFID gültig“ unbelegt; Byte 0/1 V: Temp/Feuchte)
       ~~(Kandidaten STATE_IDLE/PRELOAD/PRINT/RELOAD/ERROR/TEST/CHECK_HUB – schwach,
       FW-Strings ohne Zahlenwerte, fake-name; s. PROTOKOLL-485.md)~~
       BELEGT 15.10.2026 (Ghidra): Byte 3 = Zustand IDLE=0…TEST=5, CHECK_HUB ist
       Preload-Statemachine, kein Box-Zustand
0x0d = ~~?~~ SET_PRE_LOADING/RFID-Control [Maske][00 disable|01 enable|02 Force-Read]
       (KORRIGIERT 15.10.2026, Belege ref-hi + k2-vanilla, s. PROTOKOLL-485.md
       „Preload-Ablauf“); ~~live nur leeres ACK (len 3, nach GET_RFID), kein Request~~
       LIVE BELEGT 15.10.2026: 55 Requests ([0f 01]/[0f 00]/[01 02]/[00 02]), op 2 Antwort
       nach 31–37 s; op 3 nie live
       BELEGT 15.10.2026 (Ghidra): op 0/1 global (Maske ignoriert), op 2 = Forced
       Preload mit RFID, op 3 = ohne RFID; Antwort bei 2/3 erst nach ~3 min/Slot
0x0e = MEASURING_WHEEL Request [01], Antwort 4B BE-IEEE754-Float negativ
0x0f = ~~CTRL_CONNECTION_MOTOR_ACTION [01] Engage / [00] Release~~
       WIDERLEGT 15.10.2026 (Ghidra): Auto-Preload-Sperr-Flag [01] sperren / [00] frei;
       Host-Name (live): TIGHTEN_UP_ENABLE
0x10 = EXTRUDE_PROCESS Requests [Slotmaske, Stufe, Parameter]: 00/04/05/06/07
       (BELEGT 15.10.2026: 8 Modi; 05 = Odometer-Abfrage, 07 03 = Extruder-Check 3 mm)
0x11 = RETRUDE_PROCESS Requests [Slotmaske, 00] START / [Slotmaske, 01] FINISH
       (LIVE 15.10.2026: [m 00] meist 4×, dann [m 01]; kein 0x08 [01] dazwischen)
0x14 = GET_VERSION_SN ASCII-Version+SN ("113" bis 12.10.2026, danach "153")
0x15 = GET_HARDWARE_STATUS (Host-Name live 15.10.2026) – 16 B `10 20 30 40 50 60 00…`
Status 0x30 = UPDATE_STATE (Ereignis 1 Einstecken / 2 Herausziehen / 3 Preload fertig, V);
       0x51 = SPEED_ERR (live echter Fehlercode, key846)
NEU 15.10.2026 (Ghidra, R153): 0x01 LED-Refresh, 0x06 Feeder (FW-Bug: immer Status 1),
       0x07 Hauptmotor, 0x09 Speeds, 0x0b LED, 0x0c Config#6, 0x12/0x13 Feed-Prozesse,
       0x15, 0x30 GPIO, 0x50–0x56, 0x5b UID, 0x5c I2C-Auth, 0x5d Testmodus –
       vollständige Tabelle PROTOKOLL-485.md „Func-Tabelle CFS-App R153“
0xa2 = Auto-Addr/SN-Query f7 01 11 00 a2 01 00 <12B uuid> crc (Poll ~2s)
```

- RFID-Daten: `vender` = ~~24B-Hex~~ **40 Hex-Zeichen / 20 Byte** (KORRIGIERT 15.10.2026)
  (`1012…(gekürzt, mat 101001/col 0FFFFFF)…`), `color_value` (0FFFFFF = weiß),
  `material_type` (101001), `remain_len` (%).
  Feldlayout des vender (mittel-stark, fake-name firmware.md:340-352/521-523):
  `month(1) day(2) year(2) supplier(4) batch(2) · mat_id(6) col(7) · len(4) number(6)
  reserve(6)` – color/material stecken IM vender (mat 101001 = Hyper PLA, col 0FFFFFF);
  Nachbau `box.split_rfid_vendor()`, Details PROTOKOLL-485.md „RFID-Frame“.
  ~~Tag-SN (`SN-DEMO-…(SN gekürzt)`), Tag-FW (`1.5.3`)~~ KORRIGIERT 15.10.2026: das sind
  **Box-SN + CFS-App-Version** (aus 0x14 GET_VERSION_SN, „153“), nicht Tag-Daten
  (PROTOKOLL-485.md Func-Tabelle 0x14 war korrekt)
- `preloading_mask` = Slot-Bitmaske: 0x1=A, 0x2=B, 0x4=C, 0x8=D
- FALLE: `BOX_GET_RFID NUM=A/B/C/D` → `key60 Internal error` + Shutdown;
  korrekt `NUM=0..3`. FALLE 2: nicht zurückgezogener Slot blockiert alle Einzüge
  (`T1.filament=B` → `hub has filament`; Fix: Slot zurückziehen)
- Extrude-Testmatrix live: T1A 229 mm, T1B 160 mm, T1C 160 mm, T1D 247 mm –
  alle @220 °C, identische High-Level-Sequenz. Die damals aus `params` gelesenen
  0x10/0x11-Frames waren überwiegend ACK/Antworten; die echten Request-Payloads
  sind in `nachbau/cfsseq.py` nach der unabhängigen Referenz als Builder hinterlegt.

### 1.4 Func-Codes – Belt-Module (`0x21/0x22`)

```
0 = Version lesen   -> halversion[0:2], softversion[2:]   (f7 21 07 00 00 .. f0)
2 = Flash lesen     -> [Anzahl][4B place][4B idl_adc][4B full_adc]
4 = Flash schreiben (flash_num + 12B)
6 = ADC lesen       -> 4B current_place_adc
8 = Slider bewegen  (dir + 4B Distanz)
```

- GCode: `BELT_MDL_INFO/MOVE(POS)/SET(MDL_N)/CALI(DIR,TEN)/TEST`, Fehler key712-key721
- ACHTUNG: `BELT_MDL_CALI` nur für Module OHNE Riemen (Code-Kommentar) – nie normal ausführen
- Live-Kalibrierung 13.10.: mdlx 137,47 N, mdly 137,30 N (Ziel 140 ±15%)
- Normale Spannung: `BELT_TENSION AXES=XY` (Set auf 140 N)

### 1.5 Func-Codes – Motor-Boards (`0x81..0x84`)

```
0x06 = Sys-Param, sehr wahrscheinlich SCHREIBEN (4 Bytes LE-Float, 00 00 96 43 = 300.0)
       [~~Encoder/ADC lesen~~ – WIDERLEGT 15.10.2026]
       LIVE BELEGT 15.10.2026: [02,id] lesen → 4 B <f (ID 10: 2 B, unbekannte ID: 0 B),
       [01,id]+<f> schreiben → Echo, [03] übernehmen → [01]; FLÜCHTIG (Kaltstart = Defaults)
0x0c = ~~Parameter (8 Datenbytes) an alle Boards gleichzeitig~~ WIDERLEGT 15.10.2026:
       Protection-Abfrage, Request [0b] → 8 B Status (00 = ok); [05] ohne Antwort
0x11 = Modus-Umschaltung (1-2 Datenbytes) – LIVE: [01]/[02]/[03] Stall-Mode setzen (Echo),
       [04] lesen → [mode, 00]
0x0e = (LIVE 15.10.2026) addr 0E [02] → [addr]; FF 0E [01] Broadcast ohne Antwort
0x12 = (LIVE 15.10.2026) addr 12 [02] → 8 B UUID (Präfix cd ab)
0x05 = (LIVE 15.10.2026) [01]/[02] an X/Y bei BELT_TENSION, nie Antwort (Semantik offen)
```

- **Pfad (LIVE 15.10.2026):** X/Y/Z/Z1 ausschließlich über serial_485 ttyS5 (geteilt mit
  CFS 0x01 + Belt), Extruder nur über `transparent_send`/0x81. Alle Motor-Requests
  `retries = 0`. Bootsequenz + 227 flash_params (IDs 0–226; Log-Falle `value:N` = ID):
  PROTOKOLL-485.md §3.1. Nachbau: `motor.py`/`motor_mini.py` (parallel umgesetzt:
  read_sys_param, check_protection, get_stall_mode, read_485_addr, read_uuid,
  write_sys_param nur mit confirm=True, MOTOR_RETRIES=0, SYS_PARAM_NAMES 227).

- Cut-Kalibrierung live: 0x06-Payload `00 00 96 43` → `00 00 c8 42`
  = **LE-Float 300.0 → 100.0** = `MOTOR_SYS_PARAM NUM=1 DATA=1 ID=70
  PARAMS=300|100 PARAMS_TYPE=float` (gcode_macro.cfg:796/799, sensorless.cfg:145/211)
  – KORREKTUR 15.10.2026, passt zu k2-vanilla (1.9: 0x06 = Sys-Param).
  ID 70 = `controller_pos_loop_pid_param_kp` (Positionsregler-Kp) laut
  grant0013 `motor_params_map.md:103` (Fremdquelle K2, s. 1.9).
  ~~(Big-Endian 38467 → 51266, Differenz 12799 Ticks = Cut-Offset)~~ **WIDERLEGT
  15.10.2026** (Artefakt; ebenso ältere 0x4396/0x42c8-Lesart).
  `motor_control.cut.pos_x` -7.5 → -7.6 (Ergebnis des Makros, nicht der 0x06-Bytes).
  ~~OFFEN: wo ID 70 steckt – Live-Frame `f7 81 07 00 06 00 00 96 43 09` hat len 07
  (3+4) + gültige CRC, enthält nur den Float.~~ GEKLÄRT 15.10.2026 (live): ID im
  Request `[01, 0x46] + <f>`; der zitierte Frame ist die Antwort (Echo).
- `CALIBRATE_CUT_POS`-Ablauf: MOTOR_SYS_PARAM ID=70 (300→100) + MOTOR_CHECK_CUT_POS

### 1.6 Auto-Addr / Discovery (DEKOMPILIERT aus mcu_util_485 1.1.7.0, 13.10.2026)

Der komplette Mechanismus steckt in `FUN_00014a08` (mcu_util_485-Init):

**Typ-spezifische Broadcast-Adressen (Func 0xa1, 2 Datenbytes `<adr> 01`):**

```
0xfd = ADDR_MOTOR  ("send ADDR_MOTOR", erwartet 4 Boards → "found all motors")
0xfc = ADDR_BELT   ("send ADDR_BELT", erwartet 2 → "found all belts")
0xfb = ADDR_RFID   ("send ADDR_RFID")
0xfe = ADDR_CFS    ("send ADDR_CFS", erwartet 1×Typ1 + 1×Typ10 = 4 → "found all CFS")
```

**Adress-Zuweisung (Func 0xa0, 13 Bytes = [Start-Adresse][12B UUID]):**
Host sendet die UUID des antwortenden Geräts an die Pseudo-Adresse → Gerät übernimmt
die zugewiesene Adresse ("set slave address [%02x] success"), nächste Adresse = +1.

**Start-Adressen je Typ** (local_138 = {0x01, 0x11, 0x23, 0x85}):
CFS ab `0x01`, RFID/Filament-Rack ab `0x11`, Belt ab `0x23`, Motor ab `0x85`.
(Klippy nutzt danach eigene feste Adressen: CFS 0x01, Rack 0x11, Belt 0x21/0x22 laut belt_mdl.py.)

**Idle-Poll im Log bestätigt das:** `cmd[0xfe 0x5 0x0 0xa1 0xfe 0xfe]` = ADDR_CFS-Discovery
alle ~1 s (Timeout wenn kein neues Gerät), `0xa2`-SN-Query an bekannte Adressen alle ~2 s.

### 1.7 485-Firmware-Update (DEKOMPILIERT, mcu_util_485 `FUN_00014a08`)

Ablauf in Phasen (param_1+8 = Zustand 1..9), alle Kommandos als **Func 0xf0** mit Sub-Code:

```
Sub 0x00 (1B)  = get version         (3 Retries)
Sub 0x03 (1B)  = get sector size     (3 Retries)
Sub 0x06 (1B)  = erase private flash sector (nur CFS-Typ 1/10; motor.py: nur mit erase=True)
Sub 0x01 (1B)  = request update      (3 Retries)
4 Bytes         = send fw len (32-bit LE, 3 Retries)
Sektor-Chunks   = send fw data (Chunk-Größe aus sector size, 3 Retries)
Sub 0x02 (1B)  = update_end (Zustand 10, Timeout 500 ms – ERGÄNZT 15.10.2026,
                  Disassembly mcu_util_485 @0x15a50..0x15a8c: mov r3,#2 → Payload,
                  Länge 1, Func 0xF0, mov r7,#500 → Timeout; String "update_end")
Ende            = "update end, wait to startup app" / "startup slave"
Retries         = max. 3 Versuche (`cmp r6,#3` @0x1562c/0x15b78) → serial485 retries=2
Sector-Size     = signed Byte (`ldrsb [r4,#40]` @0x15560, ≤0 → Fehlerpfad) → 1..127
```

Plus: `duplicate uuid, skip!` (UUID-Dedupe), `-f --force` (alle updaten),
`update cost time: %lld ms`, Log mit Timestamps + Slave-Adresse.

### 1.8 Unabhängige Bestätigung: gitstonelabs/creality-cfs-klipper (Hi, 13.10.2026)

### 1.9 Kreuzvergleich: k2-vanilla (Gamecard-Fork, K2-Base) – Subagenten-Abgleich

Stand: `/tmp/opencode/k2-vanilla` (flüchtig; bei Bedarf neu klonen).
Drei Subagenten-Berichte, Kernpunkte stichprobenweise verifiziert
(Geometrie 262/296.5/270 vs. live 352.5/400/360 ✓, Func-Konstanten
0x06/0x0C/0x11 ✓, motor_map-Achsen e/x/y/z/z1 ✓).

**Bestätigt (einig):** Frame-Format, CRC-8/ATM + Scope, STATUS FF/00-Grundsatz,
0x02/0x03 (Maske/positional), 0x04 Feed/Print-Formen, 0x05 Buffer,
0x0E BE-Float + Request [01], 0x10-Stufen 00/04/05/06/07 + finalize-Param 03,
Slot-Bitmasken, A2-Shape, Auto-Addr-Sequenz.

**Abweichend/offen (ehrlich markiert):**
- ~~0x0D `(mask,02)`: Vanilla Force-Read vs. unsere Phase-2-Rearm – gleiche Bytes,
  unentschiedene Semantik.~~ **ENTSCHIEDEN 15.10.2026:** Force-Read übernommen
  (0x00=disable, 0x01=enable, 0x02=Force-Read; ref-hi `f70105ff0d0f0169` =
  „mask=0x0F enable=0x01“ widerlegt die alte Arm/Disarm/Rearm-Belegung).
  ~~Offen bleibt nur, ob Byte 1 bei 00/01 die Maske trägt (ref-hi 0x0F) oder 0 (vanilla).~~
  **BELEGT 15.10.2026 (Ghidra):** Masken-Byte wird bei 00/01 ignoriert – beide Formen gleichwertig.
- 0x08: gleiche Bytes, Vanilla Masken-Abfrage vs. unsere Hardware-Status-Deutung.
  **BELEGT 15.10.2026 (Ghidra):** [sel] → 1 B Bitmaske Sensor A (sel 0) / B (sel 1) –
  Vanilla-Deutung „Masken-Abfrage“ trifft es; Box-Sensoren, nicht Toolhead.
- 0x11 Phase0: Vanilla `(0,0)` vs. unsere `(mask,00)`. **BELEGT 15.10.2026:** beides
  gültig – mask 0 = alle Slots mit Sensor B aktiv, 1-hot = ein Slot.
- Motor-Funcs: Vanilla 0x06 = Sys-Param `[01/02/03,id]`, 0x0C = Protection
  `[data]` – unsere K2-Plus-Live-Frames zeigen 0x06 mit 4 Datenbytes (NACHTRAG
  15.10.2026: = LE-Float 300.0/100.0 = Sys-Param ID=70 → stützt Vanilla-Deutung;
  nur die ID-Position fehlt im Plus-Frame) und 0x0C
  mit 8 Nullbytes. Keine der beiden Seiten ist damit widerlegt, aber blindes
  Übernehmen der Vanilla-Formen auf den Plus ist riskant (andere Board-Generation?).
  **LIVE BELEGT 15.10.2026:** Vanilla-Formen gelten auch am Plus – 0x06 `[01/02/03,id]`,
  0x0C `[0b]` → 8 B Status (die 8 Nullbytes waren die Antwort).
- Timeouts: Vanilla Default attempts=1 vs. unsere retries=4 (verschiedene Schichten).
  NACHTRAG 15.10.2026: im Nachbau retries=4 = 1+4 = 5 Sendungen; ~~ob das Original
  „retries = 4“ als 4 oder 1+4 Sendungen meint, ist am klippy.log nachzuzählen (offen).~~
  **LIVE BELEGT 15.10.2026:** 5 Sendungen (`klippy.log.2026-10-05:63472–63521`).
- STATUS/Event: Vanilla kennt zusätzlich `0x16`=UNLOAD_NO_FILAMENT (statt busy).
  **BELEGT 15.10.2026 (Ghidra):** 0x16 = Slot ohne Filament (Vanilla korrekt), busy = 0x03.

**Nutzen für motor.py-TODOs:** Vanilla implementiert encoder_calibrate (0x03),
elec_offset_calibrate (0x04), Sys-/Flash-Params (0x06/0x07), Protection (0x0C),
Stall-Modes (0x11), read_addr (0x0E), Pin-IO (lokal, kein Frame) – Fundstellen in
`files/motor_control.py` + `motor_map.json` (227 Params/Achse). Wegen der
0x06/0x0C-Formdifferenz nur mit Live-Gegenprobe je Methode übernehmen.

**K2-Plus-Adaption (falls k2-vanilla je auf dem Plus laufen soll):**
Pflicht: Geometrie X/Y/Z (352.5/400/360, Y-Enable `!PA9`, Z-Dir nicht invertiert),
`stepper_z1`+`z_tilt`+`z_align` statt `gear_ratio`/`tmc2208`/`screws_tilt`,
Chamber-Heizer + PTC-Lüfter (M141/M191-Stubs entfernen!), Lüfter-Mapping (PA0!),
Thermistor-Tabellen (Extruder 260k, Bett EPCOS), Box-Koordinaten (Y~374–378/200),
Mesh 345/9×9, `[prtouch_v3]`-Probe-Stack, `motor_closed_loop x,y,z,z1,e`.
Details siehe Subagenten-Bericht in der Session-Historie.

**Kreuzvergleich: grant0013/k2-reverse-engineering (K2, 15.10.2026 gesichtet)**

- Was: Recherche-Repo (GPL-3.0, Stand 2026-04/05) zum **K2 (nicht Plus)**, Upstream
  von `k2-adaptive-bedmesh`; Notizen zu Kommunikationspfaden, RS485-Frameformat,
  Motor-Kommandobytes und einer Map aller 226 Motor-Parameter-IDs (mit
  CONFIGURABLE-Flag), gewonnen aus klippy.log-Auswertung und Namen.
- Nützlich: ID 70 = `controller_pos_loop_pid_param_kp` (`motor_params_map.md:103`)
  erklärt unser live gesehenes 0x06 300.0→100.0 (CALIBRATE_CUT_POS hebt Kp an und
  setzt zurück). Kommandotabelle 0x01–0x12 (Details PROTOKOLL-485.md §3) deckt sich mit
  den Plus-Live-Bytes 0x06/0x0C/0x11 und mit k2-vanilla (0x0C = Protection) und liefert
  Kandidaten für die motor.py-Stubs (0x08 get feedback, 0x07 flash_param, 0x0E addr,
  0x0F version, 0x12 uuid).
- Einschränkungen: keine Frames/CRCs, keine Payload-Formen; der Autor bekommt mit
  eigenem Code nur leere Motor-Antworten. Alles nur Hypothese für den Plus.
- Hinweis Pi-Ersatz: Motoren hängen dort am **Transparent-Pfad der Main-MCU**
  (`config_transparent`, `transparent_send oid=%c write=%*s timeout_ms=%u` /
  `transparent_response`), Extruder 0x85 über Nozzle-MCU. Stock-Bootsequenz:
  Broadcast `FF 0E 01` (Timeout normal) → UUID `8x 12 02` → Adresse `8x 0E 02` →
  Stall-Mode → Protection-Check; Motoren antworten erst nach dem Stock-`_build_config`
  (UART-Init in der MCU-FW). Für einen Pi-Ersatz also zu klären, ob die Motoren am
  Plus direkt am `/dev/ttyS5`-Bus oder nur über die Main-MCU erreichbar sind.
  **GEKLÄRT 15.10.2026 (live):** am Plus direkt am ttyS5; nur der Extruder über
  transparent – die grant-Architektur gilt für den K2, nicht den Plus.

**Kreuzvergleich: fake-name/cfs-reverse-engineering (CFS-Hardware, 15.10.2026 gesichtet)**

- Was: Hardware-/Firmware-Recherche von Connor Wolf (Stand 2025-04) mit dem Ziel, die
  CFS als AMS an beliebigen Klipper-Druckern zu betreiben (Klipper auf dem GD32 bzw.
  RS485→CAN-Interposer). Inhalt: PCB-Fotos + Pinouts (PCBs.md), FW-String-Listen
  (firmware.md), Logic-Analyzer-Mitschnitte (probing/), Ghidra-Projekt, Adapter-PCBs.
- Nützlich: Flash-Map/Ghidra-Basis (App @0x08003000 bzw. cfs6 @0x08004000 – von uns
  verifiziert), MCU GD32F303VET6 + RT-Thread 5.0.2, Bauteile (BLDC MS8828 statt
  „Stepper“, 4× HTD8236, 2× FM17622, AHT30, MT6826S) mit Pins, J4-Pinout + Bus-Elektrik
  (TPT75176H, 300-Ω-Bias, 120 Ω unbestückt), Buffer-Signale als Direktleitungen,
  RFID-vender-Feldlayout, finsh nur via RTT. Eingearbeitet in PROTOKOLL-485.md §2
  („CFS-Firmware“, „CFS-Hardware“, „CFS-Bus physisch“) und `box.split_rfid_vendor()`.
- Einschränkungen: **ältere FW** (App 108/113 vs. unsere 150/153), **kein RS485-Nachbau**
  (Autor ersetzt die Original-FW), **keine Lizenzdatei** (nur Adapter-PCBs GPL3) → nur
  Fakten mit Quellenangabe übernommen, keine Texte/Fotos/Dumps. Steckertypen extern
  laut Autor „guesswork“; A/B-Benennung widersprüchlich.

**(Fortsetzung gitstonelabs/Hi, 1.8 – nicht fake-name):** Geklonter Stand in `/tmp/opencode/cfs-klipper-ref` (flüchtig – bei Bedarf neu klonen),
eigene Klipper-Module + Captures + Tests. **Kreuzvalidierung bestanden:**
16/16 Hi-CRC-Vektoren + 12/12 Hi-Frames laufen durch UNSEREN Parser
(`nachbau/serial485.py`) fehlerfrei – beide Projekte beschreiben dasselbe Protokoll.

Übernommene Korrekturen (Hi-belegt, K2-Status jeweils vermerkt):
- Messrad = BE-Float negativ (statt Zähler) – an K2-Frames nachverifiziert
- 0x05=GET_BUFFER_STATE (frühere CUT_STATE-Zuordnung korrigiert), 0x0F=Motor-Aktion
  (live-Host-Name: TIGHTEN_UP_ENABLE), ~~0x08=GET_HARDWARE_STATUS~~ (live: GET_FILAMENT_SENSOR_STATE;
  GET_HARDWARE_STATUS = 0x15), 0x14=Version/SN,
  0x0A=Box-State (0x08 ist separat) – K2 plausibel, einzeln unverifiziert
- STATUS-Semantik: Hi `0xFF`=Host-Kommandos / `0x00`=Addressing+Replies
  (+Event-Kanal 0x30/0x16); K2 nutzt `0x00` auch hostseitig
- Slot-Topologie: 1 Controller (Addr) + 4 Slots als Daten-Bitmaske (T0=0x01..T3=0x08)
- VORSICHT: Hi warnt vor Func-Remap in K1/K2-CAN-Builds – unser K2 Plus (1.1.7.0)
  zeigt KEIN CAN (nur UART+RS485) und passt zum Hi-Map; siehe PROTOKOLL-485.md
- Verwandte Projekte: fake-name/cfs-reverse-engineering (Hardware-Interposer),
  ityschenko/klipper-cfs (alternatives Klipper-Modul)
- GPL-Stand (laut Thread): formale Anfragen seit 02/2025 unbeantwortet (Hi+K2),
  Eskalationspfad Software Freedom Conservancy – für uns relevant als Beleg,
  dass `.so`-Sourcen nicht verfügbar sind (Nachbau bleibt nötig)

---

## 2. MCU-Update-Protokoll (UART zu Mainboard/Toolhead) – DEKOMPILIERT

Quelle: `mcu_util` (armhf ELF), Ghidra 12.1.4 via REA, 12.10.2026.

### 2.1 Commands (2 Bytes, 16-bit LE)

```
0x04FB = enter_transparent
0x05FA = exit_transparent
0x00FF = get_version
0x03FC = get_sector_size
0x01FE = update_request      (Retry: lseek(fw, 0))
0x02FD = nächste Phase (nach Daten)
app_len: 4-Byte-App-Länge vor den Daten
```

### 2.2 Checksumme

`FUN_00010e54`: Summe aller Datenbytes, dann **bitweises Komplement** (`~sum`, 8 Bit),
1 Byte an die Daten angehängt.
HINWEIS 15.10.2026: im Live-`rootfs-live/usr/bin/mcu_util` liegt die Routine bei
**0x10e84** (llvm-objdump verifiziert) – Versatz +0x30 zur Ghidra-Adresse, evtl.
anderes Binary/andere Basis bei der Ghidra-Analyse.

### 2.3 State-Machine (usart_send_Process, `FUN_00010e80`)

States 1→2 Antwort 0x0075; 3→4 transparent; 5→6 exit; 7→8 version; 9→A sector;
B→C update_request; D→E app_len; F→10 Daten+Checksumme (EOF = fertig); 11→12 0x02FD;
13/14/15/16 Thread-Ende (exit 0/2/1). Thread-Poll alle 100 ms.

### 2.4 485-Variante (`mcu_util_485`)

JSON-Scan (`CFSs`, `uuid`, `/version.json`, `000_000`), dedupe per uuid,
ein FW-File pro Board. Noch nicht dekompiliert.

---

## 3. PrTouch v3 – Drucksensor-Leveling (kein Bus, `.so`-Blob)

- Pins nozzle_mcu: `pres_cs0_pin PB13/PB14`, `pres_swap_pin PA15`, Software-SPI
- Z-Homing: `[PROBE_PRES_INFO] pres_bst_indx=N pres_bst_time=-0.00x`,
  Z-Schritte ~0,01 mm bis Druckschwelle
- `BED_MESH_CALIBRATE` live 13.10. 00:27-00:31: 9×9-Grid, pro Punkt `bst_z`
- Finales Mesh `default`: Ecken ~+0.15, Mitte ~-0.02
- Config-Parameter: `pres_tri_hold (4000,10000,500)`, `pres_acq_tkms 0.78125`,
  `slot_depth 2.5`, `prth_g28_prec 0.006`

---

## 4. Motor-Control (closed-loop, `.so`-Blob)

- `motor_control_wrapper` (1,1 MB): `motor_version/sys_param/flash_param`,
  `motor_encoder_calibrate`, `motor_elec_offset_calibrate`, `set_e/z_stall_mode`,
  `check_protection_code`, `motor_read485_addr`
- Config: `motor_closed_loop: x,y,z,z1,e`, LESO/PID-Parameter je Achse
  (Pos-/Spd-/Cur-Loop), Stall-Pins je Achse
- Board-FW: `fw/F008/mot*_022_C30-mot2_002_081.bin` (123K, Klartext-Strings)

---

## 5. Weitere proprietäre Blobs im Klippy-Fork

| Blob | Funktion | Status |
|---|---|---|
| `box_wrapper.so` (2,3M) | CFS-Host-Logik, ParseData, BoxAction | live belegt |
| `serial_485_wrapper.so` (141K) | Bus-Treiber, CRC, raw_send_wait_ack | live belegt (CRC empirisch) |
| `auto_addr_wrapper.so` | Auto-Adress-Vergabe | teilweise |
| `belt_mdl.py` | Belt-Logik (reines Python, offen!) | komplett dokumentiert |
| `filament_rack_wrapper.so` | Trockner/Regal (Addr 0x11) | Strings dokumentiert, Live offen |
| `prtouch_v1/v2/v3_wrapper.so` | Druck-Leveling | v3 live belegt |
| `motor_control_wrapper.so` | Closed-loop | live belegt (Cut) |
| `load_ai` + `usr/lib/yolov5/*.bin` | AI-Abfall-Erkennung (Kamera+Inference) | NICHT analysiert |
| `cfs6` | `cfs6_100_G31-cfs6_222_000.bin` (145K Cortex-M) | Geladen in Ghidra: Basis ~~`0x08000000`~~ (**KORRIGIERT 15.10.2026: richtig `0x08004000`**, App-Image ohne Bootloader – passt zum Reset `0x08004144`), SP `0x20008030`, ~~798 Funktionen (alte Basis; Neuanalyse 15.10.2026 ausstehend)~~ **936 Funktionen @0x08004000 (Neuanalyse 15.10.2026)**; andere Gerätevariante (Heizung/Lüfter, Bus RS485 oder CAN), s. „CFS-FW Neuanalyse“ unten | Import-Rezept unten |

### cfs6-Import-Rezept (analyzeHeadless, Java-Skript)

```
/opt/ghidra/ghidra_12.1.4_PUBLIC/support/analyzeHeadless <proj-dir> cfs6proj \
  -import cfs6_100_G31-cfs6_222_000.bin -processor "ARM:LE:32:Cortex" \
  -postScript SetCfsBase.java -scriptPath /home/pro/k2-fw-live-1.1.7.0/ghidra-scripts -deleteProject
```

Skript `SetCfsBase.java`: BinaryLoader legt Raw-Bin als Block `ram` @0x0 an →
PostScript erstellt `flash`-Block @0x08000000 aus den FileBytes (`getAllFileBytes()`),
(**KORRIGIERT 15.10.2026:** falsche Basis – cfs0-App gehört nach 0x08003000, cfs6-App
nach 0x08004000; Bootloader 12 KiB @0x08000000, DB-Page @0x0807F800; Beleg
String-Zeiger-Test 179 vs. 1 bzw. 55 vs. 0, s. PROTOKOLL-485.md „CFS-Firmware: Flash-Map“)
markiert Reset, ruft `analyzeAll()`. Ergebnis: 798 Funktionen.
Für Detail-Analysen: Ghidra-GUI öffnen (`ghidraRun`) mit Projekt, oder REA-Seeds
(`REA_GHIDRA_SEED_FILE`) für gezielte Adressen.
Skripte gesichert: `/home/pro/k2-fw-live-1.1.7.0/ghidra-scripts/` (SetCfsBase, CfsDetail,
ListFuncs, FindCrc6, SlotFuncs, SlotLoad – plus FindXor/FindNum/FindCrc2-5, CountIns,
DumpIns nur in der Session-Historie, nicht gesichert).

### CFS-Firmware-Details: cfs0_050_G32 R153 (Build 02.09.2026, analysiert 13.10.2026)

Import wie oben: **942 Funktionen** @ Basis ~~0x08000000~~ (**KORRIGIERT 15.10.2026:
richtig 0x08003000**, Versionsstring bei App+0x200), Heartbeat-Strings @
080004ec/0800beac/0800c318/0800c32c (Adressen relativ zur alten Basis; ~~keine direkten
Code-XRefs – RT-Thread-Tabellen~~ – die fehlenden XRefs lagen vermutlich an der um 0x3000
verschobenen Basis). ~~Neuanalyse 15.10.2026 ausstehend~~ → ERLEDIGT, s. nächster Abschnitt.

### CFS-FW Neuanalyse mit korrigierter Basis (Ghidra, 15.10.2026)

Bericht: `GHIDRA-CFS-BEFUND-2026-10-15.md` (Projekt-Root); Protokoll-Semantik eingearbeitet
in PROTOKOLL-485.md („Func-Tabelle CFS-App R153“, „Preload-Ablauf“). Skripte
`ghidra-scripts/SetCfsBase.java` (neu, Basis per Argument/Dateiname), `CfsDetail.java`,
`CfsDispatch.java` (neu: TBB-Tabellen-Reparatur, Dispatcher-Export).

| Image | Basis | Funktionen alt → neu | Strings mit XRef | XRefs auf Strings |
|---|---|---|---|---|
| cfs0_000_153 | 0x08003000 | 927 (Vergleichsimport @0x08000000; Doku nannte 942) → **1063** | 538 → **930** (von ~1180) | 812 → **1464** |
| cfs6_222_000 | 0x08004000 | Doku 798 → **936** | → 601 (von 806) | → 1019 |
| cfs0_000_150 | 0x08003000 | Dispatcher-Gegenprobe: identisch außer 0x12 | – | – |

XRef-Vergleich (B): Was schon bei der alten Basis XRefs hatte, wird PC-relativ (`adr`)
geladen und ist basisunabhängig; alle absoluten Literal-Pool-Zeiger (finsh-Tabellen,
`__FUNCTION__`-Namen, Hilfetexte) lösen erst mit 0x08003000 auf (z.B. `preloading enable`,
`STATE_CHECK_HUB`, `rfid_mgr_fila_*`: 0 → 1+ XRefs). Damit ist die alte Erklärung
„RT-Thread-Tabellen ohne XRefs“ endgültig ersetzt.

**cfs6_222 = andere Gerätevariante (B):** Strings `[D/HT] ptc_power`, `Chamber backside fan`,
`dry aging wait ac connect` → Heizung/Lüfter (Trockner-Box); Bus „RS485“ oder „CAN“
(`api_comm_cmd_parse` = `FUN_08005acc`). Eigene Implementierung (Message-Queue); 0x0d
wirkt dort **pro Maske** (op 0/1 mit mask&0xF, 0 = alle), 0x10 nur Modi 0/4/5/6/7.
Zusätzliche fun 0x16–0x19, 0x57 (Kandidaten Trockner, V, nicht dekompiliert).
cfs0_000_153 hat **kein** Dryer-Kommando (B).
String-Inventar (Beleg für Fähigkeiten/Zustände):

- **MCU/RTOS:** GD32F303VET6, RT-Thread **5.0.2** (fake-name, stark) (rt_thread_*, finsh-Shell
  – nur über SEGGER RTT/SWD `jlinkRtt`, NICHT über RS485 –, Log-Tags `[I/E/D/...]`), Threads u.a.
  `preload_mq`, `api_preload_thread_init`, RFID-Manager-Tasks
  (`rfid_mgr_fila_back/idle/print/save/task`)
- **Treiber:** MS3791 ~~(Stepper)~~ = FW-Name des **BLDC-Hauptantriebs** (bestückt MS8828,
  KORRIGIERT 15.10.2026, fake-name PCBs.md:22), at8236 ×4 (bestückt HTD8236, H-Brücken mit ADC-Strommessung + Timer),
  AHT30 Temp/Feuchte (SCL PC11, SDA PC12), 4 Feeder (feeder0-3) mit PID
  (`feeder_pid_output`, `feeder_target_speed`, `feeder_thrust_voltage`)
- **Feed-Überwachung:** Odometer (`feeding 500ms timeout, odometer not change`,
  `feed filament exceed max length`), Detooth-Logik (`reverse_detooth`,
  `feeder reverse detooth slot_id`), Buffer-voll-Stop
- **RFID:** `rfid init/read/write <card id>`, `rfid_auth_check`, `rfid_detect_tag`,
  `different rfid uid detected, retry`, `rfid continuous detect timeout`
- **Preload-Shell:** `preloading enable/disable/aging/read [slot_id]`,
  Slot-Events `insert/pullout/start preloading/read rfid and margin estimation/pretension`
- **Bus:** `RS485_AUTO_ADDR`, `heartbeat_wait`, `preload_check_flag`, `COMM ch`
- **Build:** `MF003 Vx.y.z build Sep 2 2026`
| `fan_feedback` | Lüfter-RPM-Feedback (Pins PC6/PA12/PC13) | NICHT analysiert |
| `bl24c16f` | EEPROM (I2C am rpi-MCU, addr 0x57) | Standard |
| `z_align` | Dual-Z mit Druck-Endstopps (PA15/PA8) | teilweise |

---

## 6. Cloud & Display (nicht druckrelevant)

- `libcrealitycloud.so` (222K): MQTT (paho), HTTPS (curl+openssl 1.1),
  Alibaba OSS/VOD – Base64 + `AES_cbc_encrypt` (Cloud-Auth, kein Bus)
- Exporte: `crealitycloud_get_jwttoken`, `cloudMqttServer::*`
- Display-App `c440x` (web-server Port 80, Moonraker-Webhooks), Kamera `webrtc_local`
  Port 8000, Fluidd Port 4408, Moonraker API Port 7125

---

## 7. Standard-Klipper (kein Proprietär – Pi läuft direkt)

- Heizen: extruder `nozzle_mcu:PB8` PID (kp 14.296/ki 1.271/kd 40.206, High-Temp-Satz),
  Bed `PC8` PID, Chamber `PC12` watermark
- Motion: corexy, Stepper-Pins, microsteps 16, Input Shaper mzv 42.0/42.4 Hz
- MCUs: Mainboard `gd32f303xe`, Toolhead `gd32f303xb` – Klipper-Serial-Protokoll
  Standard (1.1.0.48-293-g493f9a0f-dirty), Bootloader CanBoot (seriell)
- Heiztest live: M104 S200 → 45 s auf 200,9 °C, PID hält (Power 0.31)

---

## 8. Offene Punkte

1. ~~Filament-Rack (Addr 0x11)~~ → Strings dokumentiert (set_temp/flush/pre_flush/modify, Fan, RFID), Addr 0x11 bestätigt; Live-Test nur mit angeschlossenem Rack möglich
2. ~~Auto-Addr-Handshake~~ → DEKOMPILIERT: 0xa1-Discovery (0xfd/0xfc/0xfb/0xfe) + 0xa0-Zuweisung mit UUID
3. ~~mcu_util_485~~ → DEKOMPILIERT: Func 0xf0-Sub-Codes (version/sector/erase/request/len/data)
4. `raw_send_wait_ack`-Body dekompilieren (mstate-Tabellen/Seeds)
5. CFS-FW `cfs6_222_000.bin` → ERLEDIGT: Ghidra-Import-Rezept steht, ~~798~~ 936 Funktionen (Basis 0x08004000, 15.10.2026); offen: Handler 0x16–0x19/0x57 (Trockner?) im Detail
6. AI-Waste (load_ai + YOLOv5-Bins) – nur falls Pi-Ersatz das braucht
7. Langer RFID-Frame komplett (Log schneidet ~250 Zeichen ab → socat-Tee)
8. ~~RFID-Tag-Format vollständig (Felder nach dem vender-Hex)~~ → ERLEDIGT 15.10.2026:
   keine Felder nach dem vender; Layout des 40-Zeichen-vender aus fake-name übernommen
   (mittel-stark), `box.split_rfid_vendor()`; offen nur Datums-/len-Semantik
9. ~~Ghidra-Neuanalyse cfs0/cfs6 mit korrigierter Basis (0x08003000/0x08004000) – läuft~~
   → ERLEDIGT 15.10.2026 (`GHIDRA-CFS-BEFUND-2026-10-15.md`). Offen daraus: Quelle von
   0x0a Byte 0/1 (live 0x13 0x34) und R[3]; Bedeutung Config #6 (0x0c/0x50); Inhalt 0x15;
   Init der Auto-Addr-Tabelle (0xA0–0xA3); physische Zuordnung Sensor A/B (Pin-Tabelle
   @0x20000218); weitere Fehlercodes 0x1a/0x20/0x22/0x51–0x53.
