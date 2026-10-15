# K2 485-Protokoll (Basis: V1.1.3.13-Dump + Live 1.1.7.0, Doku in `/home/pro/k2-fw-live-1.1.7.0/`)

Quelle: `klippy/extras/belt_mdl.py` (Klartext, 621 Zeilen, live vom Drucker),
`fw/cfs/cfs0_050_G32-cfs0_000_153.bin` (Strings, live 1.1.7.0),
`*.wrapper.cpython-39.so` (nur Symbolnamen, Cython),
`usr/bin/mcu_util*` (Strings).

## 1. Belt-Modul – einziges offenes 485-Beispiel

Frame (send_sensor_data / recv_sensor_data):

```
[head 0xF7][addr][len][star 0x00][fun][data...][crc]
len = len(data) + 3
```

- `addr`: `0x21` = mdlx, `0x22` = mdly (config_addr)
- **CRC (LIVE-BEFUND 12.10.2026, geknackt): CRC-8/ATM (poly 0x07, init 0x00) über alle Bytes AB `len` bis Ende der Daten – `head` und `addr` sind NICHT enthalten.**
  Python (`belt_mdl.py`) sendet crc=0x00 als Platzhalter; die echte CRC berechnet `serial_485_wrapper.so` beim Senden.
  Verifiziert an 6 Live-Frames aus klippy.log (alle OK):
  - `f7 21 07 00 00 01 02 03 04 f0` (Version-Resp: hal 0102 / soft 0304)
  - `f7 21 10 00 02 03 ... 3d` (Flash-Resp: place=147)
  - `f7 21 07 00 06 ff f8 c7 12 4c` (ADC-Resp -> 135,18 N)
  - `f7 01 11 00 a2 01 00 a0 a1 a2 a3 a4 a5 a6 a7 a8 a9 aa ab f0` (Auto-Addr)
  - `f7 01 07 00 03 ff ff ff 00 98` (GET_REMAIN_LEN: 255 = keine Daten)
  - `f7 01 09 00 0a 13 34 00 00 00 00 d7`
- Antwort: `[0]==0xF7`, `[1]==addr`, `[2]=len`, `[4]=fun`, Daten `rec[5 : len-3+5]`
- Senden: `hex_data[1:-1]` via `serial_485.cmd_send_data_with_response(buf, 1)`
- 32-bit-Werte big-endian (split_to_bytes / bytes_to_int)

Func-Codes (STA):

```
0 = Version lesen -> halversion[0:2], softversion[2:]
2 = Flash lesen (flash_num) -> [Anzahl][4B place][4B idl_adc][4B full_adc]
4 = Flash schreiben (flash_num + 12B)
6 = ADC lesen -> 4B current_place_adc
8 = Slider bewegen (dir + 4B Distanz)
```

GCode: `BELT_MDL_INFO/MOVE(POS)/SET(MDL_N)/CALI(DIR,TEN)/TEST`, Fehler `key712-key721`
(z.B. 712/713 = 485-Timeout, 714/715 = nicht kalibriert `0xffffffff`, 716/717 = Tension >600/<-300).

### LIVE-BEFUNDE 12.10.2026 (Drucker 192.168.1.100, FW 1.1.7.0)

- `belt_mdl.py` live ist IDENTISCH zum Image 1.1.3.13 → CRC kam schon immer aus dem `.so`
### PrTouch BED_MESH_CALIBRATE LIVE (13.10.2026, 00:27–00:31)

- Z-Homing über Drucksensor: `[PROBE_PRES_INFO] pres_bst_indx=57/60 pres_bst_time=-0.005..-0.002`
  (bester Sample-Index + Zeit bis Berührung), Z fährt in 0,01er-Schritten (5.00→1.03→1.01)
- Grid-Messung 9×9: pro Punkt `pos_x/pos_y, bst_z` (z.B. X=302.5 Y=217.5 → -0.0205)
- Finales Mesh `default` gespeichert: Ecken ~+0.15, Mitte ~-0.02 (typische Wölbung)
- Kein 485-Bus nötig – PrTouch läuft rein über nozzle_mcu-Pins (pres_cs0 + ADC)

- Live-Belt-Messung mdlx: `current_place=147`, `pull_num=135.18 N` (Ziel 140, OK)
- **Belt-Kalibrierung 13.10. 00:07:** mdlx **137,47 N**, mdly **137,30 N** (Ziel 140 ±15% erreicht)
- **Cut-Kalibrierung:** `motor_control.cut.pos_x` -7.5 → **-7.6**; Motor-Boards `0x81..0x84` am Bus
- **Input Shaper nach Kalibrierung:** X = mzv 42,0 Hz, Y = mzv 42,4 Hz (gespeichert via SAVE_CONFIG)
- MCUs: Mainboard `gd32f303xe`, Toolhead `gd32f303xb` (beide 120 MHz, 230400 Baud),
  Host-MCU `linux` K2_1.1.3.2; Mainboard-Klipper `1.1.0.48-293-g493f9a0f-dirty`
- Live-Serials: `[mcu]=/dev/ttyS2`, `[nozzle]=/dev/ttyS3`, `[485]=/dev/ttyS5 @230400`
  (Image 1.1.3.13 hatte ttyS7/ttyS1/ttyS6 @460800 → Board-Revision anders!)
- `auto_addr` UUID (mb_addr_table_uniids): `a0 a1 a2 a3 a4 a5 a6 a7 a8 a9 aa ab`
  = exakt die 12 Bytes im 0xa2-Frame nach `01 00`
- Bus-Polling idle: alle ~2s `0xa2`-Frame an addr 0x01, 4 Retries, dann Timeout (CFS aus)
- Box-Live-State: `disconnect` (CFS nicht angeschlossen), T1 hat aber gespeicherte
  RFID-Daten (vender `AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA`, remain 32/0/100/0)

## 2. CFS (Filament-Box) – RS485-Bus

FW (live 1.1.7.0): `fw/cfs/cfs0_050_G30-cfs0_000_150.bin`,
`fw/cfs/cfs0_050_G32-cfs0_000_153.bin`, `fw/cfs/cfs6_100_G31-cfs6_222_000.bin`
(Cortex-M, Basis 0x08000000).
`fw/cfs/version.json`: boot `cfs0_050_G30/G32/cfs6_100_G31`,
app `cfs0_000_150/153`, `cfs6_222_000`.

Bus-Logik aus `mcu_util_485` + FW-Strings:
- Auto-Addr (`RS485_AUTO_ADDR`), pro Board `uuid` + `slave [0x..]` + `fw.cur_ver`
- States `DONE/FAIL/NACK`, Update analog zu mcu_util (sector-size, checksum, retry)
- Kommandos (FW-Shell + Log): `heartbeat`, `sn_information=%s`, `current mode = %d`,
  `485 uid = %d`, `slot%d insert/pullout/start preloading/read rfid`,
  `preloading enable/disable/aging/read [slot]`, `has filament`, `filament percent=%d%%`,
  `feed: ch / pull_back_hub / motor block state=%02x`, Treiber `MS3791/at8236`,
  `rfid init/read/write <card id>`, `detect rfid`, `different rfid uid, retry`,
  `AHT10 temp/humi`, `feed filament exceed max length`

Host-Seite (nur `.so`, keine Quelle):
- `box_wrapper`: `BoxState.generate_Tn_map/generate_Tn_data`, `ParseData.get_rfid/get_remain_len/get_measuring_wheel/parse_num_to_byte`, `BoxAction.extrusion_all_materials/material_volume_to_length`
- `filament_rack_wrapper`: `cmd_filament_rack(_modify/_flush/_pre_flush/_set_temp)`, `check_rfid_valid`, `get_material_target_temp/speed`
- `serial_485_wrapper`: `Serialhdl_485.raw_send/raw_send_wait_ack/connect_uart/_bg_thread/get_response`, `Serial_485_Wrapper.cmd_485_send_data/cmd_send_data_with_response/register_response`

### Preload-Ablauf (BOX_SET_PRE_LOADING, K2-BELEGT 13.10.)

Die K2-High-Level-Kette löst vor `SET_PRE_LOADING` Abfragen aus: 0x04,
0x14 (Version+SN) und 0x0d. Die im Log sichtbaren Frames `0x03[ff ff 1d 00]`
waren Antworten/Statusframes, nicht sicher der Request. Unabhängig belegt ist
der eigentliche Wire-Request aus der Referenzimplementierung:
`0x0d [Slotmaske][Aktion]` ~~Phase 0x00=Arm, 0x01=Disarm, 0x02=Slot-Rearm~~
**KORRIGIERT 15.10.2026:** Aktion **0x00 = disable** (Insert-Reading/Preload aus),
**0x01 = enable**, **0x02 = Force-RFID-Read** (`SET_PRE_LOADING`/RFID-Control).
Belege: `nachbau/ref-hi/valid_messages.json` `f70105ff0d0f0169` =
„CMD_SET_PRE_LOADING box 1, mask=0x0F enable=0x01“; k2-vanilla
`files/box_protocol.py` (`CMD_RFID_CONTROL=0x0D`): `set_rfid_insert_reading(enabled)`
→ `(0, int(enabled))`, `force_rfid_read(mask)` → `(mask, 2)`. Die alte
Arm/Disarm-Belegung war durch keinen Frame gestützt und widerspricht beiden Referenzen.
**Kein Live-0x0d-Request auf dem K2 Plus belegt** – live nur ein leeres ACK
(len 3, nach GET_RFID); Byte-Form `f7 01 03 00 0d 9e` = ref-hi-ACK (CRC 0x9e ✓).
Offen: ob das 1. Byte bei 0x00/0x01 die Maske
ist (ref-hi: 0x0F) oder ignoriert wird (vanilla: 0). Nachbau:
`box.build_preload(mask, action)` / `cfsseq.build_preload(mask, action)`,
Konstanten `PRELOAD_DISABLE/ENABLE/FORCE_READ = 0x00/0x01/0x02`.
Alle K2-Frames CRC-verifiziert; der Versuch blieb ohne Mechanik, weil kein
neuer Insert-/Feed-Zustand vorlag.

### M8200-Sequenzen (13.10., belegt via helixscreen-Recherche + eigene Logs)

- Mapping bestätigt: `M8200 L I=n` mit n=0..15 → TnX (I=0→T1A, I=1→T1B …) –
  eigene Jinja-Rechnung und helixscreen-Doku stimmen überein.
- WURZEL des key60-Crashs bei direktem `BOX_LOAD_MATERIAL`: Das Makro lässt
  `CR_BOX_PRE_OPT` weg, das vor `CR_BOX_EXTRUDE` Pflicht ist.
- Korrekte Folgen (Display-äquivalent, per API fahrbar):
  Load: `M8200 P` → `M8200 L I=n` → `M8200 F` → `M8200 O`
  Unload: `M8200 P` → `M8200 C` → `M8200 R` → `M8200 O`
- Fehlercodes (Auswahl, aus .so-Strings belegt): key831 RS485-Timeout,
  key835-838 Extrude-Blockaden, key841 Cut-Sensor, key843 RFID, key845 Düse
  blockiert, key849-851 Retrude-Fehler, key855 Cut-Position.
- Materialcodes: `101001` = Hyper PLA (live bestätigt), Farben `0RRGGBB`.
- Hinweis: Stock-Display spricht die CFS teils DIREKT über RS485 an
  (Klipper-Bypass) – erklärt Display-Verhalten ohne Log-Spur.
- API-Grenze (13.10. verifiziert): Einzelbefehle (M104/M140/G1, Abfragen,
  BOX_ERROR_CLEAR, BOX_SET_PRE_LOADING) laufen per API; M8200-Ketten
  (P→L→F→O) feuern per blankem API-Call NICHT (weder ungehomt noch voll
  gehomt, weder mit noch ohne P zuerst) – keine Log-Aktivität, kein Fehler.
  Display-Loads umgehen Klippy-Makros teils per Direkt-RS485, daher nicht
  1:1 per API replizierbar.

Einzelne Chain-Schritte laufen per Moonraker-API verlässlich:
Heizen (`M104`/`M140`, verifiziert an Targets), Cut (`BOX_LOAD_MATERIAL_CUT_MATERIAL`,
Kopf fährt an Cutter-Pos), Retrude (`BOX_LOAD_MATERIAL_RETRUDE_MATERIAL`, Hub wird frei),
Error-Clear (`BOX_ERROR_CLEAR` löst EXTRUDE_ERR8), Abfragen aller Art.
Messrad-Float offiziell bestätigt: Klippy loggt `data_hex → Float` selbst
(z.B. `0xc52a4e54 → -2724.89`).
Volle Loads per API brauchen die Kette mit Pre-Opt: `M8200 P` zuerst, dann
`M8200 L I=n` (blankes L ohne P bleibt stehen – belegter Grund für die
früheren Fehlschläge). Display-Flow bleibt zusätzlich belegt.

### RFID-Frame LIVE DEKODIERT (12.10.2026, 22:52:19)

`f7 01 2e 00 02 41 3a 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 3b 78`
(Vendor-Bytes anonymisiert, gleiche Länge; CRC 0x78 neu berechnet und verifiziert)

- Func `0x02` = GET_RFID-Antwort, Daten als **ASCII**: `A:<hex>;...`
- `A:` = Slot-Key, dann Vendor-Hex `AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA`, dann `;` –
  VOLLSTÄNDIG (13.10. nachverifiziert: 49 Tokens, len=0x2e → 46+3=49 ✓, CRC=0x78 ✓
  über dem anonymisierten Frame;
  das `0x2` am Ende ist unpadded `%x`-Format, KEIN Abschneiden).
  Farbe/Material/Restmenge kommen aus separaten Abfragen.
- **Kurzform:** `f7 01 0d 00 02 42 3a 75 6e 6b 6e 6f 77 6e 3b 8e` = `B:unknown;` (len 0x0d, Slot ohne erkannten Tag → ASCII "unknown")
- **Weitere Zustände:** `f7 01 0a 00 02 43 3a 62 75 73 79 3b 54` = `C:busy;` (len 0x0a, Slot dreht/ließt gerade), später `C:unknown;` (Tag noch nicht gelesen)
- Passt exakt zum Query: `vender=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA`, danach folgen vermutlich color / material_type / remain als weitere `;`-Felder
- **preloading_mask ist Bitmaske pro Slot:** `0x1`=Slot A, `0x2`=Slot B (live beobachtet beim Umstecken: `preloading_slots ['A']→['B']`)
- Frame `f7 01 07 30 0a 03 00 00 00 51`: star kann `0x30` ('0') sein statt `0x00` – Statusfeld, CRC-8/ATM bestätigt (0x51)
- GET_BOX_STATE/Eventkanal `0x0a`: `f7 01 09 00 0a X Y 00 00 00 00 crc`,
  Status-/Datenbytes sind firmwareabhängige Zustandswerte; `0x30`/`0x16` sind
  in der unabhängigen Referenz als Insert-/Busy-Events beschrieben.
- REMAIN-Len-Frame bei Umstecken: `f7 01 07 00 03 1c ff 00 00 11` = A=28%, B=0xff (n/a, Spule raus), C/D=0

### Box-Func-Codes (LIVE, aus klippy.log extrahiert)

```
0x02 = GET_RFID        (Antwort ASCII "<Slot>:<hex|unknown|busy>;")
0x03 = GET_REMAIN_LEN  (Request [Slotmaske], Antwort positional A..D;
                         0xff=nicht ausgewählt/keine Daten,
                         ~~0=ausgewählt+leer~~ KORRIGIERT 15.10.2026:
                         0 = kein RFID-Restwert (Slot B ist mit remain 0
                         gefahren, 160 mm, s. T1B) – nicht „leer“)
0x04 = SET_BOX_MODE (Request [00 01] = Feed-Modus; [Slotmaske 00] = Print-Modus)
0x05 = GET_BUFFER_STATE (Box-Adresse 0x01, Request leer; 1 Byte Buffer-State;
                         die frühere Bezeichnung CUT_STATE war falsch)
0x08 = GET_HARDWARE_STATUS (Request [Kanal], Toolhead-/Hardware-Sensorstatus)
0x0a = Herzschlag/Poll (6 Datenbytes, Zustand; alle ~4s)
0x0d = ~~?~~ SET_PRE_LOADING / RFID-Control (KORRIGIERT 15.10.2026; Request
       [Maske][00 disable|01 enable|02 Force-Read], s. Preload-Ablauf oben;
       live nur leeres ACK len 3 nach GET_RFID; Form wie ref-hi `f7 01 03 00 0d 9e`)
0x0e = MEASURING_WHEEL (Request [01], Antwort 4 Bytes Big-Endian IEEE-754 Float,
       NEGATIV – kein Zähler!
       KORREKTUR 13.10.: c4e6fe0b→-1847,94, ~~c5019149→-2073,08~~ (Tippfehler,
       KORRIGIERT 15.10.2026: CRC-gültiger Live-Frame `f7 01 07 00 0e c5 00 91 49 a9`
       → c5009149 = −2057,08); Betrag wächst beim Fördern.
       Gilt auch für 0x10-Push-Replies. Unabhängig bestätigt: gitstonelabs.)
0x14 = GET_VERSION_SN (Hi- wie K2-BELEGT: Hi-Request `f7 01 03 ff 14 06`;
       K2-Response 13.10.: `f7 01 19 00 14 ... d4`, Daten ASCII "153"+
       "SN-DEMO-1234567890X" = CFS-App-Version + SN, CRC ok)
0x0f = CTRL_CONNECTION_MOTOR_ACTION (Request [01] Engage / [00] Release)
0x10 = EXTRUDE_PROCESS (Request [Slotmaske, Stufe, Parameter]:
       [slot 00 00] init, [slot 04 00] engage, [slot 05 00] push,
       [slot 06 00] settle, [slot 07 03] finalize; 0x05-Push wiederholt
       sich sensor-/Messrad-gesteuert)
0x11 = RETRUDE_PROCESS (Request START [Slotmaske 00], FINISH [Slotmaske 01];
       K2-Logs zeigen die dazugehörigen kurzen ACKs `f7 01 03 00 11 ca`)
0xa2 = Auto-Addr/SN-Query (f7 01 11 00 a2 01 00 <12B uuid> <crc>, Poll alle ~2s)
```

STATUS-Byte: Die unabhängige Referenz nutzt `0xFF` für CFS-Operational-Requests
und `0x00` für Addressing/Box-Replies; `0x30` = Insert-Push, `0x16` = busy.
K2 nutzt zusätzlich `0x00` bei Belt-/Motor-Requests (`belt_mdl.py`), während
die CFS-Box-Kommandos über den live introspektierten `send_data`-Pfad ebenfalls
den Operational-Kontext verwenden. Frühere `params`-Logs wurden teils als
Requests fehlinterpretiert, obwohl sie Antworten waren.
VORSICHT K2: Hi-Warnung, dass K1/K2-Firmware Func-Codes remappen könnte (CAN-Build) –
auf unserem K2 Plus (1.1.7.0, UART+RS485, kein CAN) passen 0x02/0x03/0x0a/0x0e/0x10/0x11
exakt zum Hi-Map. Zouordnung 0x04/0x05/0x08/0x0f/0x14 auf K2 daher plausibel,
aber noch nicht einzeln verifiziert.

Alle Frames bestätigen CRC-8/ATM über `[len..data]`. Box-Operational-Requests
verwenden eine 1-hot-Slotmaske (A=0x01, B=0x02, C=0x04, D=0x08);
der GCode-Parameter `NUM=0..3` wird intern in diese Maske übersetzt.
GCode: `BOX_GET_RFID ADDR=<1..4> NUM=<0..3>`,
`BOX_GET_REMAIN_LEN ADDR=<1..4> NUM=<0..3>`, `BOX_SET_PRE_LOADING ADDR=.. NUM=.. ACTION=RUN`.
ACHTUNG: `NUM=A/B/C/D` ist FALSCH → `key60 Internal error` + Klippy-Shutdown (beobachtet
12.10.2026); `NUM=0..3` numerisch verwenden.

### CRC-Quelle: auto_addr_wrapper.py im Klartext (13.10.2026)

Die maßgebliche Implementierung liegt als **Python-Quelle** vor
(lokale Forschungsablage, nicht im Repo, 729 Zeilen):

```python
POLY = 0x07
def crc8_cal(self, data, len):
    crc = 0
    for i in range(len):
        crc ^= data[i]
        for j in range(8):
            if crc & 0x80: crc = (crc << 1) ^ POLY
            else:          crc <<= 1
            crc &= 0xFF
    return crc
def cal_pack_crc(self, package):
    crc_buff = [package.length, package.status, package.function_code]
    crc_buff += package.data
    return self.crc8_cal(crc_buff, len(crc_buff))
```

Dazu das komplette Protokoll-Gerüst als `@dataclass DataPackage`
(head/slave_addr/length/status/function_code/data/crc), Broadcast-Adressen
(`0xFF` allgemein, `0xFE` MB/CFS-Pro, `0xFD` Motoren, `0xFC` Belt),
Device-Typen (MB=1 CFS-Box, CLM=2 Closed-loop, BTM=3 Belt, CFS_PRO=10),
Timeouts (kurz 0,05 s / mittel 0,1 s / lang 1,0 s je Command, kein Low-Level-Retransmit),
Adress-Tabellen (MB 0x01-0x04, Motoren 0x81-0x84, Belt 0x91-0x92),
Uniid-Persistenz in `printer.cfg` (`mb_addr_table_uniids`), Online-Logik
(~~3 verpasste Checks = offline~~ KORRIGIERT 15.10.2026: `lost_cnt > MAX_LOST_CNT=3`,
also offline erst beim 4. unbeantworteten Check – Klartextquelle `auto_addr_wrapper.py`:604-607, lokal, nicht im Repo;
Selbsttest `nachbau/auto_addr.py`). Nur MB-Auto-Addr ist aktiv (CLM/BTM/CFS_PRO
in `dev_table_map_table` auskommentiert).

### raw_send_wait_ack im .so (13.10.2026, ehrlicher Befund)

Dispatcher `FUN_0001b81c` dekompiliert (Argument-Parsing + 3-Arg-Serial-Call).
Die CRC-Schleife selbst wurde im `serial_485_wrapper.so` trotz erschöpfender
Suche NICHT lokalisiert: Voll-Disassembly von `.text` (8415 Instruktionen) enthält
keine Shift+XOR-Cluster, keine CRC-Tabelle, keine PyNumber-Bitops, keine
CRC-Callback-Strings. Für den Python-Nachbau irrelevant – Algorithmus steht oben
als Quelle und ist an 10+ Live-Frames verifiziert.

### Extrude-Sequenz LIVE (12.10.2026, T1A, 229 mm @ 220 °C)

1. `EXTRUDE_PROCESS` (0x10): echte Requests tragen `[Slotmaske, Stufe, Parameter]`;
   Stufen 00/04/05/06/07, 0x05-Push wiederholt sich sensor-/Messrad-gesteuert.
   Die früher als „Poll“ bezeichneten `f7 01 03 00 10 cd` sind ACK/Antwortframes.
2. High-Level-Load flankiert 0x10 mit Feed-Mode (0x04), Motor-Engage/Release (0x0f),
   Hardware-Ping (0x08), Buffer-Read (0x05) und Print-Mode (0x04).
3. `MEASURING_WHEEL` (0x0e) alle ~3 s während/nach dem Extrudieren.
4. Resultat: `change_color_num` T1A → 1, `extrude = 229.17 mm`

### Zweiter Slot T1B (12.10.2026, 160 mm @ 350 mm/min, 220 °C)

- Gleicher Ablauf, `tnn: T1B`, `extrude_process_stage7_ret: OK`
- 0x10-Frames: `f7 01 07 00 10 c4 xx xx xx xx` – Byte1 konstant `0xc4`, 4 variable Bytes
  (Encoder/Position); der Slot steckt NICHT im Frame – die Box kennt ihn kontextuell
- `change_color_num`: A=1, B=1 nach beiden Extrudes; remain B bleibt 0, vender B "unknown"
  (RFID-Tag von Slot 2 wurde bislang nicht eingelesen) → Beleg: remain 0 = kein
  RFID-Restwert, NICHT „leer“ (Nachbau: `has_filament` → None statt False, 15.10.2026)
- RETRUDE (0x11) zuvor: `extrude = -10.0` (10 mm Rückzug), Messrad rückwärts

### Slot-Wechsel T1A→T1B (13.10.2026, Display-Flow, live beobachtet)

- Ablauf: erst T1A-Retrude (Hub leeren), dann T1B-Load (`tnn: T1B`, stage7 OK, 160 mm)
- Hub-Wechsel sauber: `filament: A → None → B`, keine Fehler
- Beleg: Auto-Wechsel retrudet den belegten Slot selbsttätig vor dem neuen Load

### Dritter Slot T1D (12.10.2026, 247,08 mm @ 360 mm/min, 220 °C)

- `tnn: T1D`, `stage7_ret: OK` – identische Struktur, bestätigt: Slot steckt nie im Frame
- Slot-Matrix komplett: A/B/C/D extrudiert, jeweils gleiche 0x10/0x0e/0x11-Sequenz
  (T1C: 160 mm @ 360 mm/min, 220 °C, stage7 OK – 23:44)
- WICHTIG live gelernt: CFS blockiert alle Einzüge, wenn ein Slot nicht zurückgezogen ist
  (`T1.filament = B` → `hub has filament`; Fix: Slot B „Zurückziehen" → `filament: None`)

## 3. Motor/Toolhead-Boards

FW: `fw/F008|F012/mot*_022_C30-mot2_002_081.bin` (123K, Klartext: `mot2_002_081`, `flash_param_version`, `motor_R/L/Ke`, `boot_key`, `Rebooting system...`),
`mcu0*/noz0*` (29-31K, `CanBoot`, `mcu0_022_000`), `belt/*.bin` (6,5K, `belt_mdl start!`),
`rfid/*.bin` (20K, Cortex-M), `K1/*`, `F038/*`.

Host: `motor_control_wrapper`: `motor_version/sys_param/flash_param`, `motor_read485_addr`, `motor_encoder_calibrate`, `motor_elec_offset_calibrate`, `set_e/z_stall_mode`, `check_protection_code`, `data_pack_extruder`.

### LIVE: Motor-Boards am 485-Bus (Cut-Kalibrierung 12.10.2026, 23:51)

- Adressen `0x81..0x84` = closed-loop Motor-Boards (X/Y/Z/Z1, F008 mot-Boards)
- `0x11` = Modus-Umschaltung (1-2 Datenbytes: `01`, `02 00`, `03`)
- `0x06` = **Sys-Param (sehr wahrscheinlich Schreiben)** – KORREKTUR 15.10.2026:
  Frames trugen `00 00 96 43` → `00 00 c8 42`. Das sind **Little-Endian-Float
  300.0 → 100.0** = exakt `MOTOR_SYS_PARAM NUM=1 DATA=1 ID=70 PARAMS=300|100
  PARAMS_TYPE=float` (`config/F008_CR0CN240319C13/gcode_macro.cfg:796/799`,
  `sensorless.cfg:145/211`, Makro CALIBRATE_CUT_POS). Passt zu k2-vanilla
  (PROTOKOLL-GESAMT.md 1.9: 0x06 = Sys-Param).
  ~~Big-Endian dekodiert 38467 → 51266 (Diff 12799)~~ – **WIDERLEGT 15.10.2026**
  (Artefakt der falschen Deutung; ebenso die ältere 0x4396/0x42c8-Lesart).
  OFFEN: Position der ID 70 (0x46). Der dokumentierte Frame
  `f7 81 07 00 06 00 00 96 43 09` hat len=`07` (= 3 + 4 Datenbytes) und gültige
  CRC – im Frame fehlt also kein Byte, er trägt NUR den Float. Vanilla-Form wäre
  `[01, id] + <f` (→ hypothetisch `f7 81 09 00 06 01 46 00 00 96 43 8f`), auf dem
  Plus NICHT live gesehen. `read_encoder` in motor*.py ist deshalb gesperrt.
  NACHTRAG 15.10.2026: **ID 70 = `controller_pos_loop_pid_param_kp`
  (Positionsregler-Kp, CONFIGURABLE)** laut grant0013/k2-reverse-engineering
  `docs/motor_params_map.md:103` (Fremdquelle K2, Namen aus klippy.log) →
  CALIBRATE_CUT_POS setzt Kp auf 300 und danach zurück auf 100.
- `0x0c` = Parameter (8 Datenbytes `00*8`) an alle 4 Boards gleichzeitig
- Ablauf `CALIBRATE_CUT_POS`: Modus→~~ADC lesen~~ Sys-Param 300.0 (0x06)→Parameter
  →~~ADC lesen~~ Sys-Param 100.0 (0x06)→Modus zurück (KORREKTUR 15.10.2026)
  (MOTOR_SYS_PARAM ID=70 PARAMS=300/100 = Positionsregler-Kp hoch/zurück,
  Name laut grant0013; frühere Deutung „Cut-Kraft rampen“ damit präzisiert)

### Fremdquelle grant0013/k2-reverse-engineering (K2, 15.10.2026 gesichtet)

Upstream https://github.com/grant0013/k2-reverse-engineering (GPL-3.0, Stand
2026-04/05). Bezieht sich auf den **K2, nicht den K2 Plus**. Belege dort aus
klippy.log-Auswertung und Namen – **keine Frames, keine CRCs**; der Autor schreibt
selbst, dass sein eigener Code nur leere Motor-Antworten bekommt. Gesichtet:
`docs/motor_params_map.md` (226 Motor-Parameter-IDs), `docs/k2_architecture.md`,
`docs/rs485_protocol.md`. Keine Dateien übernommen.

| Byte | laut Fremdquelle (K2) | unser Stand (K2 Plus) |
|---|---|---|
| `0x01` | reboot | unbelegt |
| `0x03` | encoder_cal | unbelegt (Stub `encoder_calibrate`) |
| `0x04` | elec_offset_cal | unbelegt (Stub `elec_offset_calibrate`) |
| `0x05` | control | unbelegt |
| `0x06` | sys_param read/write | **live**: 4 B LE-Float 300.0/100.0 = ID 70 (Kp) – passt |
| `0x07` | flash_param read/write | unbelegt (Stubs `get/set_flash_param`) |
| `0x08` | get feedback | unbelegt – Kandidat fürs echte Encoder-Lesen |
| `0x0B` | boot | unbelegt (Motoren); bei CFS = Loader-Check an 0xFF |
| `0x0C` | protection status | **live**: 8× `00` an alle Boards; Semantik offen |
| `0x0D` | systemid | unbelegt |
| `0x0E` | read/set_addr | unbelegt (Stub `read_485_addr`) |
| `0x0F` | version | unbelegt (belegt nur F0/00 via `query_version`) |
| `0x11` | stall_mode | **live**: 1–2 B (`01`/`02 00`/`03`) – „Modus“ passt dazu |
| `0x12` | dev_uuid | unbelegt |

Adressen dort: `0x81..0x84` = X/Y/Z/Z1 über Main-MCU-Transparent-Pfad
(`transparent_send`/`transparent_response`), `0x85` = Extruder über Nozzle-MCU
transparent. Stock-Bootsequenz (aus Logs): Broadcast-Addr-Set `FF 0E 01`, UUID-Abfrage
`81/82 12 02`, Adress-Lesen `81/82 0E 02`, dann Stall-Mode und Protection-Check.

**Bewertung:** Die drei auf dem Plus live gesehenen Bytes (0x06/0x0C/0x11) passen
widerspruchsfrei zur Fremdtabelle, ID 70 erklärt die 300/100-Werte. Alles Übrige sind
**Hypothesen (Fremdquelle K2, auf dem Plus unbelegt)** – kein sendender Code im Nachbau,
Stubs bleiben gesperrt, bis je Kommando ein Plus-Mitschnitt vorliegt (CHECKLISTE.md).

## 4. Update-Werkzeuge

`usr/bin/mcu_util` (14K): `usart_send_Process/usart_rec_Process`, `enter_transparent`, `get_version/get_sector_size`, `update_request/app_len/app_data_confirm/version_confirm/sector_size_confirm/start_app/app_run`, `checksum fail/no ack/flash fail`.

### GHIDRA-DEKOMPILIERT (12.10.2026, via REA/Ghidra 12.1.4)

`FUN_00010e80` = usart_send_Process-Thread, komplette Update-State-Machine:

```
State 1→2:   Antwort 0x0075
State 3→4:   enter_transparent -> sendet 0x04FB
State 5→6:   exit_transparent  -> sendet 0x05FA
State 7→8:   get_version       -> sendet 0x00FF
State 9→A:   get_sector_size   -> sendet 0x03FC
State B→C:   update_request    -> sendet 0x01FE (Retry: lseek(fw,0))
State D→E:   app_len           -> sendet 4-Byte-App-Länge
State F→10:  Sektor lesen + Checksumme; EOF -> "wait for mcu update done"
State 11→12: sendet 0x02FD
State 13/14/15/16: Thread-Ende (exit 0/2/1)
```

`FUN_00010e54` = Checksumme: **Summe aller Bytes, dann bitweises Komplement** (`~sum`, 8 Bit) – klassische 1er-Komplement-Summe, wird als 1 Byte an die Daten angehängt.
(HINWEIS 15.10.2026: im Live-`rootfs-live/usr/bin/mcu_util` liegt diese Routine bei
**0x10e84** (`llvm-objdump`: add/ldrb-Schleife, `mvn`+`uxtb`) – Versatz +0x30 zum
Ghidra-Namen, evtl. anderes Binary/andere Basis bei der Ghidra-Analyse.)

`mcu_util_485`: zusätzlich JSON-Scan (`CFSs`, `uuid`, `/version.json`, `000_000`), `duplicate uuid, skip!`, `keep only one firmware file for %s board!`.

`usr/bin/klipper_mcu` (38K): PTY-Bridge (`openpty`, `ttyname`, `sched_setscheduler`).

## 5. Folgerung für Custom-FW

Kein Crypto, aber alles Board-spezifische steckt in den Cython-`.so` + 485-Frames.
Wer Tradrack o.ä. anbinden will, muss `serial_485_wrapper.cmd_send_data_with_response` mitschneiden (Logic-Analyzer an 485-A/B oder `socat`-Log zwischen Klippy und `/dev/tty*`) und `box_wrapper.ParseData` nachbauen. Belt-Modul oben ist die einzige offene Referenzimplementierung.

**UPDATE 12.10.2026:** Der Mitschnitt ist nicht mehr nötig – `klippy.log` loggt jeden 485-Frame
(`cmd_485_send_data_with_response params: ...`), und die CRC ist geknackt (CRC-8/ATM über `[len..data]`).
Damit ist ein reiner Python-Nachbau von `serial_485_wrapper` + `box_wrapper` ohne Hardware-Mitschnitt möglich:
Frames aus dem Log extrahieren, CRC-8/ATM verifizieren, `ParseData`-Semantik aus den Log-Kommandos
(`GET_BOX_STATE`, `GET_REMAIN_LEN`, `GET_RFID`, `BOX_SET_PRE_LOADING`) ableiten.
