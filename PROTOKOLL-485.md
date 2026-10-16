# K2 485-Protokoll (Basis: V1.1.3.13-Dump + Live 1.1.7.0, Doku in `<projekt>/`)

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
  - `f7 01 11 00 a2 01 00 a0 a1 … ab f0` (12 B UUID als Platzhalter, CRC neu) (Auto-Addr)
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
- `auto_addr` UUID (mb_addr_table_uniids): `a0 a1 … (12 B UUID, Platzhalter)`
  = exakt die 12 Bytes im 0xa2-Frame nach `01 00`
- Bus-Polling idle: alle ~2s `0xa2`-Frame an addr 0x01, ~~4 Retries, dann Timeout~~ (CFS aus)
  **KORRIGIERT – LIVE BELEGT 15.10.2026:** 0xa2 läuft mit `retries = 0` (eine Sendung je ~2 s,
  19 684× in den Logs, z.B. `klippy.log.2026-10-12:2106`), passt zu
  Klartextquelle `auto_addr_wrapper.py`:468 (lokal, nicht im Repo) (`retransmit=False`). Details §6.2.
- Box-Live-State: `disconnect` (CFS nicht angeschlossen), T1 hat aber gespeicherte
  RFID-Daten (vender `1012…(gekürzt, mat 101001/col 0FFFFFF)…`, remain 32/0/100/0)

## 2. CFS (Filament-Box) – RS485-Bus

FW (live 1.1.7.0): `fw/cfs/cfs0_050_G30-cfs0_000_150.bin`,
`fw/cfs/cfs0_050_G32-cfs0_000_153.bin`, `fw/cfs/cfs6_100_G31-cfs6_222_000.bin`
(Cortex-M; ~~Basis 0x08000000~~ **KORRIGIERT 15.10.2026:** die `.bin` sind
reine App-Images – cfs0-App @0x08003000, cfs6-App @0x08004000; Flash-Map s.
„CFS-Firmware: Flash-Map, Bootloader, Shell“ unten).
`fw/cfs/version.json`: boot `cfs0_050_G30/G32/cfs6_100_G31`,
app `cfs0_000_150/153`, `cfs6_222_000`.

Bus-Logik aus `mcu_util_485` + FW-Strings:
- Auto-Addr (`RS485_AUTO_ADDR`), pro Board `uuid` + `slave [0x..]` + `fw.cur_ver`
- States `DONE/FAIL/NACK`, Update analog zu mcu_util (sector-size, checksum, retry)
- Kommandos (FW-Shell + Log): `heartbeat`, `sn_information=%s`, `current mode = %d`,
  `485 uid = %d`, `slot%d insert/pullout/start preloading/read rfid`,
  `preloading enable/disable/aging/read [slot]`, `has filament`, `filament percent=%d%%`,
  `feed: ch / pull_back_hub / motor block state=%02x`, Treiber `MS3791/at8236` (FW-Namen; bestückt laut fake-name: BLDC-Treiber MS8828 + 4× HTD8236, s. „CFS-Hardware“ unten),
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
~~**Kein Live-0x0d-Request auf dem K2 Plus belegt** – live nur ein leeres ACK
(len 3, nach GET_RFID); Byte-Form `f7 01 03 00 0d 9e` = ref-hi-ACK (CRC 0x9e ✓).
Offen: ob das 1. Byte bei 0x00/0x01 die Maske
ist (ref-hi: 0x0F) oder ignoriert wird (vanilla: 0).~~ **ÜBERHOLT – LIVE BELEGT
15.10.2026:** 55 0x0d-Requests in den klippy.logs (Host sendet `[0f 01]` 22×, `[0f 00]`
3×, `[01 02]` 4×, `[00 02]` 26 Sendungen in Bursts); der Host schickt bei op 0/1 die
Maske 0x0F. Latenzen und offene Punkte s. §6.4. Nachbau:
`box.build_preload(mask, action)` / `cfsseq.build_preload(mask, action)`,
Konstanten `PRELOAD_DISABLE/ENABLE/FORCE_READ = 0x00/0x01/0x02`
(+ `PRELOAD_FORCE_TENSION_ONLY = 0x03` seit 15.10.2026, s. unten).
Alle K2-Frames CRC-verifiziert; der Versuch blieb ohne Mechanik, weil kein
neuer Insert-/Feed-Zustand vorlag.

**BELEGT 15.10.2026 (Ghidra, CFS-App R153, `FUN_0800f0da`; Details
`GHIDRA-CFS-BEFUND-2026-10-15.md` §3/§10):**
- op **0x00 = disable** (`FUN_08005376`, = finsh `preloading disable`), op **0x01 = enable**
  (`FUN_0800536e`) – **global**, das Masken-Byte wird bei op 0/1 **ignoriert** (damit ist
  die Frage „ref-hi 0x0F vs. vanilla 0“ gegenstandslos: beide wirken gleich).
- op **0x02 = Forced Preload MIT RFID** (`FUN_08004ec4(mask,1)`: pretension →
  STATE_CHECK_HUB → „read rfid and margin estimation“ = enthält den Force-Read).
- op **0x03 = Forced Preload OHNE RFID** (`FUN_08004ec4(mask,0)`: nur pretension) – NEU,
  im Nachbau bisher nicht vorhanden (jetzt `PRELOAD_FORCE_TENSION_ONLY`).
- op > 3 → Status 1. Status 3 = Box nicht IDLE/PRELOAD; 0x16 = kein Slot mit Filament
  (setzt STATE_ERROR); sonst Fehlercode eines vorgeschalteten Pull-backs (in IDLE wird
  vor dem Force `FUN_08004cd0(4,1)` = Rückzug aller Slots am Hub ausgeführt).
- **Antwort bei op 2/3 erst NACH dem Vorgang:** je Slot der Maske nacheinander 5 s +
  bis 180 s, ggf. vorher bis 179 s Warten auf laufendes Preload → **~3 min pro Slot**.
  Ein Host-Timeout von 2 s + Retries würde den Befehl mehrfach senden (Nachbau:
  langer Timeout, retries=0, s. `box.py`).
- Abweichung cfs6_222 (`FUN_0800e674`): op 0/1 wirkt dort **pro Maske** (0 = alle),
  op 2 braucht mask≠0, op 3 nur ein Slot, Antwort nur bei op 0/1 sofort.

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

`f7 01 2e 00 02 41 3a 31 30 31 32 … (Vendor-ASCII, Platzhalter, gekürzt) … 3b ...`

- Func `0x02` = GET_RFID-Antwort, Daten als **ASCII**: `A:<hex>;...`
- `A:` = Slot-Key, dann Vendor-Hex `1012…(gekürzt, mat 101001/col 0FFFFFF)…`, dann `;` –
  VOLLSTÄNDIG (13.10. nachverifiziert: 49 Tokens, len=0x2e → 46+3=49 ✓, CRC=0xd7 ✓
  über dem anonymisierten Frame;
  das `0x2` am Ende ist unpadded `%x`-Format, KEIN Abschneiden).
  Farbe/Material/Restmenge kommen aus separaten Abfragen.
- **Kurzform:** `f7 01 0d 00 02 42 3a 75 6e 6b 6e 6f 77 6e 3b 8e` = `B:unknown;` (len 0x0d, Slot ohne erkannten Tag → ASCII "unknown")
- **Weitere Zustände:** `f7 01 0a 00 02 43 3a 62 75 73 79 3b 54` = `C:busy;` (len 0x0a, Slot dreht/ließt gerade), später `C:unknown;` (Tag noch nicht gelesen)
- Passt exakt zum Query: `vender=1012…(gekürzt, mat 101001/col 0FFFFFF)…`; ~~danach folgen vermutlich color / material_type / remain als weitere `;`-Felder~~
  **KORRIGIERT 15.10.2026:** keine weiteren Felder – Farbe und Material stecken IM
  40-Zeichen-vender (20 Byte als Hex/ASCII). Feldlayout (Quelle fake-name `firmware.md:340-352`
  printf-Feldnamen, `firmware.md:521-523` Beispiel 11+13+16 = 40 Zeichen; Belegstärke
  mittel-stark): `month(1) day(2) year(2) supplier(4) batch(2) · mat_id(6) col(7) ·
  len(4) number(6) reserve(6)`. Unser Live-Vendor zerfällt sauber:
  `1|01|…(Platzhalter, gekürzt)… · 101001|0FFFFFF · …` → mat_id `101001` (= Hyper PLA,
  deckt sich mit Query `material_type`), col `0FFFFFF` (= Query `color_value`, weiß).
  Offen: Datumskodierung (der echte day-Wert ist nicht dezimal), Einheit von `len`.
  Nachbau: `nachbau/box.py` `split_rfid_vendor()` (Selbsttest feldweise).
- **preloading_mask ist Bitmaske pro Slot:** `0x1`=Slot A, `0x2`=Slot B (live beobachtet beim Umstecken: `preloading_slots ['A']→['B']`)
- Frame `f7 01 07 30 0a 03 00 00 00 51`: star kann `0x30` ('0') sein statt `0x00` – Statusfeld, CRC-8/ATM bestätigt (0x51)
- GET_BOX_STATE/Eventkanal `0x0a`: `f7 01 09 00 0a X Y 00 00 00 00 crc`,
  ~~Status-/Datenbytes sind firmwareabhängige Zustandswerte; `0x30`/`0x16` sind
  in der unabhängigen Referenz als Insert-/Busy-Events beschrieben.~~
  **BELEGT 15.10.2026 (Ghidra `FUN_0800ed26`):** Status 0x30 = Preload-Ereignis
  anstehend (4 Byte je Slot 1/2/3), 0x16 ist KEIN 0x0a-Event, sondern Fehlercode
  „Slot ohne Filament“ (0x0d/0x11). Layout s. Abschnitt „0x0a Box-State“ unten.
- REMAIN-Len-Frame bei Umstecken: `f7 01 07 00 03 1c ff 00 00 11` = A=28%, B=0xff ~~(n/a, Spule raus)~~, C/D=0
  **KORRIGIERT 15.10.2026 (Ghidra `FUN_0800ef66`):** 0xFF = nicht in Maske / Slot preloadet
  gerade / **Filament da, aber keine RFID-Daten** (B war `unknown` → passt); 0x00 =
  Sensor A meldet **kein Filament** (oder gespeicherter Prozentwert 0).

### Box-Func-Codes (LIVE, aus klippy.log extrahiert)

```
0x02 = GET_RFID        (Antwort ASCII "<Slot>:<hex|unknown|busy>;")
0x03 = GET_REMAIN_LEN  (Request [Slotmaske], Antwort positional A..D;
                         0xff=nicht ausgewählt/keine Daten,
                         ~~0=ausgewählt+leer~~ KORRIGIERT 15.10.2026:
                         0 = kein RFID-Restwert (Slot B ist mit remain 0
                         gefahren, 160 mm, s. T1B) – nicht „leer“)
                         KORRIGIERT/BELEGT 15.10.2026 (Ghidra): Wire-0 = kein
                         Filament (Sensor A) oder 0 %; 0xff auch = Filament
                         ohne RFID-Daten; „remain 0“ bei T1B war Host-Zustand
0x04 = SET_BOX_MODE (Request [00 01] = Feed-Modus; [Slotmaske 00] = Print-Modus)
       BELEGT 15.10.2026: [mask, mode]; mode 0 = Slot → STATE_PRINT (mask≠0),
       mode 1 = Print verlassen → STATE_IDLE („Feed-Modus“ = IDLE); Status 3 in
       PRELOAD/RELOAD
0x05 = GET_BUFFER_STATE (Box-Adresse 0x01, Request leer; 1 Byte Buffer-State;
                         die frühere Bezeichnung CUT_STATE war falsch)
0x08 = ~~GET_HARDWARE_STATUS (Request [Kanal], Toolhead-/Hardware-Sensorstatus)~~
       **KORRIGIERT – LIVE BELEGT 15.10.2026 (Host-Name im klippy.log):
       GET_FILAMENT_SENSOR_STATE** (`.so` `communication_get_filament_sensor_state(addr,
       pos)`): [00] → Bitmaske Sensor A (live `07` = A,B,C belegt), [01] → Bitmaske
       Sensor B/Hub (= 0x0a Byte 4). GET_HARDWARE_STATUS ist **0x15** (s. unten)
0x0a = Herzschlag/Poll (6 Datenbytes, Zustand; alle ~4s; Host-Name GET_BOX_STATE;
       **LIVE 15.10.2026:** Poll mit `retries = 0`; Box-FW 113 liefert nur 4 Datenbytes)
       ~~Kandidaten für das Zustandsbyte (SCHWACH, nur FW-Strings ohne Zahlenwerte,
       fake-name firmware.md:322/562-567): STATE_IDLE/PRELOAD/PRINT/RELOAD/ERROR/
       TEST/CHECK_HUB – Zuordnung zu b0/b1 unbelegt.~~
       BELEGT 15.10.2026 (Ghidra): Zustand steht in **b3**: IDLE=0 PRELOAD=1
       PRINT=2 RELOAD=3 ERROR=4 TEST=5; CHECK_HUB ist KEIN Box-Zustand (Preload-
       Statemachine). Details Abschnitt „0x0a Box-State“ unten.
0x0d = ~~?~~ SET_PRE_LOADING / RFID-Control (KORRIGIERT 15.10.2026; Request
       [Maske][00 disable|01 enable|02 Force-Read], s. Preload-Ablauf oben;
       BELEGT 15.10.2026 (Ghidra): op 2 = Forced Preload mit RFID, op 3 = ohne
       RFID (nur Pretension), Maske nur bei op 2/3;
       live nur leeres ACK len 3 nach GET_RFID; Form wie ref-hi `f7 01 03 00 0d 9e`;
       FW-Strings `forced_preloading %d` / ` forced_preloading!` (fake-name
       firmware.md:276-277) stützen SCHWACH Aktion 0x02 = Force-Preload)
0x0e = MEASURING_WHEEL (Request [01], Antwort 4 Bytes Big-Endian IEEE-754 Float,
       NEGATIV – kein Zähler!
       KORREKTUR 13.10.: c4e6fe0b→-1847,94, ~~c5019149→-2073,08~~ (Tippfehler,
       KORRIGIERT 15.10.2026: CRC-gültiger Live-Frame `f7 01 07 00 0e c5 00 91 49 a9`
       → c5009149 = −2057,08); Betrag wächst beim Fördern.
       Gilt auch für 0x10-Push-Replies. Unabhängig bestätigt: gitstonelabs.)
0x14 = GET_VERSION_SN (Hi- wie K2-BELEGT: Hi-Request `f7 01 03 ff 14 06`;
       K2-Response 13.10.: `f7 01 19 00 14 ...`, Daten ASCII "153"+
       SN (19 Zeichen, `SN-DEMO-…`, gekürzt) = CFS-App-Version + SN, CRC ok;
       LIVE 15.10.2026: bis 12.10. 00:23 „113“+SN, danach „153“+SN)
0x15 = GET_HARDWARE_STATUS (Host-Name, LIVE BELEGT 15.10.2026; Request leer,
       Antwort 16 B `10 20 30 40 50 60` + 10×`00`, 10 Requests)
0x0f = ~~CTRL_CONNECTION_MOTOR_ACTION (Request [01] Engage / [00] Release)~~
       WIDERLEGT 15.10.2026 (Ghidra, inline im Dispatcher): **Auto-Preload-Sperr-
       Flag** S[8] (0x200037d8): [01] = automatisches Preload gesperrt, [00] =
       freigegeben; KEINE Motor-Aktion. Bytes/Live-Flanke unverändert gültig
       (Host sperrt Auto-Preload während Load). Motor-Kommandos: 0x06/0x07/0x09
       **Host-Name (LIVE 15.10.2026): TIGHTEN_UP_ENABLE** (`.so`
       `communication_tighten_up_enable(addr, enable)`); [01] vor dem Load, [00] nach
       0x04 [m 00]. Unsere Ghidra-Semantik „Auto-Preload-Sperre“ bleibt als Deutung.
0x10 = EXTRUDE_PROCESS (Request [Slotmaske, Stufe, Parameter]:
       [slot 00 00] init, [slot 04 00] engage, [slot 05 00] push,
       [slot 06 00] settle, [slot 07 03] finalize; 0x05-Push wiederholt
       sich sensor-/Messrad-gesteuert)
       KORRIGIERT/BELEGT 15.10.2026 (Ghidra `FUN_080045c6`, 8 Modi 0..7):
       00 = Stage1 (Pretension → feed_top_to_hub → fördern), 04 = Dauerfördern
       starten (sofortige Antwort), 05 = **Odometer-Abfrage** (keine Push-
       Aktion; Antwort 4 B Float BE = die `c4 …`-Replies), 06 = Fördern beenden
       + Buffer-Check, 07 03 = **Extruder-Check mit p = 3 mm**. Tabelle unten.
0x11 = RETRUDE_PROCESS (Request START [Slotmaske 00], FINISH [Slotmaske 01];
       K2-Logs zeigen die dazugehörigen kurzen ACKs `f7 01 03 00 11 ca`)
       **LIVE 15.10.2026:** Folge `04[00 01] 08[00] 11[m 00]`×(meist 4) `11[m 01]`,
       KEIN `08[01]` dazwischen (0/23) – s. §6.5
       BELEGT 15.10.2026 (Ghidra `FUN_08004d90`): p=0 = Feeder+Hauptmotor
       rückwärts bis Buffer-Sensor (≤5 s, sonst 0x13); p=1 = rfid_mgr_fila_back
       + pull_back_hub (0x17/0x19/0x14); sonst Status 1; mask 0 = alle Slots
0xa2 = Auto-Addr/SN-Query (f7 01 11 00 a2 01 00 <12B uuid> <crc>, Poll alle ~2s,
       retries = 0 – LIVE 15.10.2026)
```

STATUS-Byte: Die unabhängige Referenz nutzt `0xFF` für CFS-Operational-Requests
und `0x00` für Addressing/Box-Replies; `0x30` = Insert-Push, `0x16` = busy.
K2 nutzt zusätzlich `0x00` bei Belt-/Motor-Requests (`belt_mdl.py`), während
die CFS-Box-Kommandos über den live introspektierten `send_data`-Pfad ebenfalls
den Operational-Kontext verwenden. Frühere `params`-Logs wurden teils als
Requests fehlinterpretiert, obwohl sie Antworten waren.
~~`0x30` = Insert-Push, `0x16` = busy~~ → **KORRIGIERT/BELEGT 15.10.2026 (Ghidra):**
Das Status-Byte im **Request wird von der CFS-App ignoriert** (Dispatcher liest
`frame[2]` nie) – 0xFF/0x00 sind für die Box gleichwertig. Status-Werte in
**Antworten** s. Tabelle „Status-Byte (Antworten)“ unten (0x16 = Slot ohne Filament,
0x30 nur bei 0x0a).
VORSICHT K2: Hi-Warnung, dass K1/K2-Firmware Func-Codes remappen könnte (CAN-Build) –
auf unserem K2 Plus (1.1.7.0, UART+RS485, kein CAN) passen 0x02/0x03/0x0a/0x0e/0x10/0x11
exakt zum Hi-Map. Zouordnung 0x04/0x05/0x08/0x0f/0x14 auf K2 daher plausibel,
aber noch nicht einzeln verifiziert. **NACHTRAG 15.10.2026:** In der Box-FW R153
(= Firmware der Box des Users, App 153 live per 0x14) sind alle diese Codes per Ghidra
belegt; 0x0f hat dort aber eine andere Semantik (Auto-Preload-Sperre).

Alle Frames bestätigen CRC-8/ATM über `[len..data]`. Box-Operational-Requests
verwenden eine 1-hot-Slotmaske (A=0x01, B=0x02, C=0x04, D=0x08);
der GCode-Parameter `NUM=0..3` wird intern in diese Maske übersetzt.
GCode: `BOX_GET_RFID ADDR=<1..4> NUM=<0..3>`,
`BOX_GET_REMAIN_LEN ADDR=<1..4> NUM=<0..3>`, `BOX_SET_PRE_LOADING ADDR=.. NUM=.. ACTION=RUN`.
ACHTUNG: `NUM=A/B/C/D` ist FALSCH → `key60 Internal error` + Klippy-Shutdown (beobachtet
12.10.2026); `NUM=0..3` numerisch verwenden.

### Func-Tabelle CFS-App R153 (BELEGT 15.10.2026, Ghidra)

Quelle: Ghidra-Neuanalyse `fw/cfs/cfs0_050_G32-cfs0_000_153.bin` @0x08003000 (= Firmware
in der Box des Users, App-Version 153 live per 0x14 bestätigt); Bericht
`GHIDRA-CFS-BEFUND-2026-10-15.md` (B = im Code belegt, B+L = zusätzlich Live-Frame,
V = Deutung). Gegenprobe R150: identisch außer 0x12.

**Rahmen (App):**
- Empfang `FUN_0800f880`: wartet auf `0xF7`, akzeptiert `addr ∈ {eigene, 0xEA, 0xEB}`
  (Bootloader dagegen 0xFF/0xFE/eigene/0xF0 – getrennter Parser, s. „Flash-Map“ oben),
  `len > 2`, dann status, fun, `len-3` Daten, CRC; Byte-Timeout 0x78 Ticks.
- **CRC-Scope `len..data` BESTÄTIGT** (`FUN_0800e9ec`, CRC-8 poly 0x07 init 0, Bitschleife;
  B+L, 6 Live-Frames nachgerechnet).
- **Status-Byte im Request wird ignoriert** (B): Dispatcher `FUN_0800f27c` liest `frame[2]` nie.
- CRC-Fehler → Antwort `fun`, Status 2, ohne Daten (nicht bei addr 0xEA).
- ~~0xEA = Broadcast ohne Antwort~~; 0xEB = Antwort mit Absender 0xEB (nur 0x50/0x51 relevant).
  **KORRIGIERT 16.10.2026 (BEFUND2 §6, B):** 0xEA ist nur ein „halber Broadcast“ – bei normalen
  Kommandos (0x0a, 0x10 …) antwortet die Box trotzdem, mit **eigener** Adresse; unterdrückt
  werden nur CRC-Fehler-Antwort, unbekannte fun und 0x50. 0xFE/0xFF nimmt die App **nur ohne
  eigene Adresse** an (0xFF praktisch Bootloader-only). Details: „Adressen-Semantik“ in §6.8.
- Antwort-Builder `FUN_0800eaf2`: `F7, own_addr, n+3, status, fun, data, crc`.
- Längenprüfung `FUN_0800ebc2`: falsches len → Status 1. Maskenprüfung `FUN_0800eb88`:
  nur 1-hot 0x01/0x02/0x04/0x08; 0x00 = „alle“ nur wo erlaubt; sonst Status 1.

| fun | Daten (Request) | Handler | Semantik (belegt) |
|---|---|---|---|
| 0x01 | – | inline + `FUN_08010c60` | Status 0, dann LED-Status aus Slot-Sensoren neu (NEU) |
| 0x02 | mask | `FUN_0800ef00` | RFID `"X:<40 Hex>;"` / `busy` (Preload läuft) / `unknown` (Filament, kein Tag) (B+L) |
| 0x03 | mask | `FUN_0800ef66` | 4 B Restmenge %; 0xFF = nicht in Maske/preloadet/Filament ohne RFID; 0 = kein Filament (B+L) |
| 0x04 | mask, mode | `FUN_0800f014` | mode 0 = STATE_PRINT (mask≠0), mode 1 = IDLE; Status 3 in PRELOAD/RELOAD (B) |
| 0x05 | – | `FUN_0800f0c2` | 1 B: 2 Bit Buffer-Sensoren (invertiert) (B; „Buffer“ V) |
| 0x06 | mask, op | `FUN_0800ebda` | Feeder: 0 stop, 1 out (P[1]), 2 back (P[6]); Status 3 ≠ IDLE. **FW-BUG** s. unten (NEU) |
| 0x07 | op | `FUN_0800ec26` | Hauptmotor: 0 stop, 1 vor (P[2]), 2 zurück (P[7]); Status 3 ≠ IDLE (NEU; Motorzuordnung V) |
| 0x08 | sel | `FUN_0800ec70` | 1 B Bitmaske: sel 0 = Sensor A, sel 1 = Sensor B (1 = aktiv) (B; A=Einlauf/B=Hub V) |
| 0x09 | a, b, wert | `FUN_0800ecd2` | Speed-Parameter: (0,0)→P[1] (0,1)→P[6] (1,0)→P[2] (1,1)→P[7] (NEU) |
| 0x0a | – | `FUN_0800ed26` | Box-State, s. unten (B+L) |
| 0x0b | mask, x | `FUN_0800eee8` | LED-Code x für Slot (NEU; LED V). Achtung: Bootloader-0x0B [01] = Sprung in App |
| 0x0c | – | `FUN_0800f124` | 1 B Config-Wert #6 (NEU; Bedeutung offen) |
| 0x0d | mask, op | `FUN_0800f0da` | Preload-Steuerung op 0–3, s. „Preload-Ablauf“ (B) |
| 0x0e | op | `FUN_0800efc6` | op 0 = Odometer nullen, op 1 = 4 B Float BE (mm) (B+L) |
| 0x0f | x∈{0,1} | inline | **Auto-Preload-Sperr-Flag** S[8], Status 0; sonst 1. KEINE Motor-Aktion (B) |
| 0x10 | mask, mode, p | `FUN_080045c6` | Extrude-Prozess, 8 Modi, s. unten (B) |
| 0x11 | mask, p | `FUN_08004d90` | Retract p 0/1, s. unten (B) |
| 0x12 | mask, mode, s, dist_hi, dist_lo | `FUN_080047f6` | mode 0 feed_top_to_hub (+1 B Zustand), mode 1 fördern bis dist (u16 BE, ≤3000 mm) (NEU; nur R153) |
| 0x13 | mask | `FUN_08004dc2` | Pretension → feed_top_to_hub → fördern bis Stillstand; Status = Fehlercode (NEU) |
| 0x14 | – | inline | 22 B: `"153"` + 19 B SN (B+L) |
| 0x15 | – | `FUN_0800f16e` | 16 B: 6 Einträge `((i+1)<<4)|X[i]` (NEU; ~~Bedeutung offen~~ **BEFUND2: Init-Flags EEPROM/AHT30/Odometer/Buffer/RFID0/RFID1, 0 = OK**, s. §6.8) |
| 0x30 | x | `FUN_0800f19c` | GPIO 0x10 = (x==1), Status 0 (NEU) |
| 0x50 | wert | inline | Config #6 schreiben+speichern, dann vermutl. Reset; gleicher Wert → Status 3 (NEU) |
| 0x51 | x | inline | nur addr 0xEB: G[3]=x, keine Antwort (NEU) |
| 0x52 | – | inline | Antwort mit **fun 0x53**: `[S[1], S[0]]` (= 0x0a Byte 0/1) (NEU) |
| 0x53 | a, b | inline | schreibt S-Bytes, keine Antwort (Längenfehler meldet fun 0x52 – FW-Copy-Paste) (NEU) |
| 0x55 | x | `FUN_0800f14a` | Echo-Test: Antwort x+1 (NEU) |
| 0x56 | – | inline | Status 0, dann Reset (V) (NEU) |
| 0x5b | – | `FUN_0800f1ba` | 8 B GD32-Unique-ID (NEU) |
| 0x5c | 16 B | `FUN_0800f1e8` | Block an I2C-Gerät 0x60, 16 B Ergebnis (Auth/Krypto V) (NEU) |
| 0x5d | – | `FUN_08007c0e` | `main_pcb_test_mode` (NEU) |
| 0xA0–0xA3 | – | Modul `RS485_AUTO_ADDR` | nicht im Dispatcher (eigene Tabelle @0x200000c4, BEFUND2 §7); Box-Seite der Auto-Adressierung, s. §6.8 |
| sonst | – | `LAB_0800f7f8` | Status 1 ohne Daten |

**FW-Bug 0x06 (B, auch R150):** Die Maske für `eb88` kommt aus Register `r7`
(= `rt_tick_get()`, 0x0800f8a6), nicht aus dem Datenbyte (Disassembly 0x0800f470
`mov r0,r7`) → praktisch **immer Status 1**. 0x06 ist damit am Bus faktisch unbenutzbar.

**0x10 Extrude-Modi** (`FUN_080045c6`, mask 1-hot Pflicht, Speed-Param P[i]):

| mode | Ablauf | Antwort |
|---|---|---|
| 0 | Stage1: Pretension → feed_top_to_hub → Hauptmotor P[3] bis Bedingung | Status + 1 B Box-Zustand |
| 1 | RELOAD, Hauptmotor P[3] bis Odometer-Stillstand oder >3000 mm (0x0b) | Status |
| 2 | RELOAD, P[2], ≥5 mm in 3 s nötig (sonst 0x0a), dann wie 1 | Status |
| 3 | **Fehler löschen**: Zustand IDLE, S[3]=0, LED neu | Status 0 |
| 4 | RELOAD, **sofort Status 0**, dann Dauerfördern P[3]; FW liest den Bus selbst (mode 5/6); Timeouts 25 s → 0x0a, >3000 mm → 0x0b, Stillstand 500 ms → 0x0c | Status 0 (sofort) |
| 5 | Abfrage: Status = gespeicherter Fehler aus mode 4 (danach gelöscht) + 4 B Odometer Float BE | Status + 4 B |
| 6 | Fördern beenden / Buffer füllen bis voll (≤3 s, sonst 0x0d) | Status |
| 7 | Extruder-Check: P[4], 300 ms, Odometer muss > p mm laufen; < p·0,5 → 0x0e | Status |
| >7 | `illegal data`, **keine Antwort** | – |

Live `[mask 07 03]` = **mode 7 mit p = 3 mm** (Extruder-Greif-Check; Mechanik B, Deutung V) –
Hypothese aus fake-name/CHECKLISTE **bestätigt**. Live-Ablauf 00/04/05…/06/07 03 passt
damit: Stage1, Dauerfördern, Odometer-Polls, Stopp + Buffer, Extruder-Check.

**0x11 Retract** (`FUN_08004d90`, mask 0 = alle Slots mit Sensor B aktiv):
p=1 zuerst `rfid_mgr_fila_back`; Zustand muss IDLE/PRELOAD sein (sonst 3), Slot ohne
Filament → 0x16 + ERROR. p=0: Feeder + Hauptmotor rückwärts P[7] bis Buffer-Sensor
(≤5 s, sonst 0x13). p=1: pull_back_hub (Sensor A muss 0 sein, sonst 0x17; Motorblock 0x19;
„pull back hub PE“-Timeout 0x14). Anderes p → 1. Danach IDLE bzw. ERROR mit Code.
**Schwellen (BEFUND2 §8, B+L):** p=1 Takt 20 ms, Ziel Hub-Sensor 5× in Folge frei, max. 2000
Runden = 40 s → **0x1a**; Odometer < 0,01 mm/20 ms **und** R[3] (Motorblock > 10 s) → **0x19**
(passt zu live 11,6/12,2 s); Stufe 2: Hub nach 7 s noch belegt → **0x14**; vorab Einlauf-Sensor
leer → **0x17**. Maske 0 wählt alle Slots mit aktivem Hub-Sensor.

**Status-Byte (Antworten):**

| Wert | Bedeutung |
|---|---|
| 0x00 | OK |
| 0x01 | ungültig: len falsch, Maske ungültig, Subcode ungültig, fun unbekannt |
| 0x02 | CRC-Fehler im Request |
| 0x03 | busy / falscher Zustand (z.B. 0x04 in PRELOAD/RELOAD, 0x06/0x07 ≠ IDLE); bei 0x50 „Wert unverändert“ |
| 0x05 | Feeder-Out-Fehler (`cmd_parse_out`); **BEFUND2:** auch 0x10 mode 0 – Hub-Sensor nicht innerhalb 7 s (700×10 ms) erreicht (`FUN_080042a8`, B+L) |
| 0x06 | `buffer_fill_timeout` |
| 0x08 | Pretension-Warten erfolglos |
| 0x09 | feed_top_to_hub: Odometer bewegt sich nicht |
| 0x0a | 25-s-Timeout (mode 4: 1250×20 ms) bzw. < 5 mm in 3 s (mode 2) |
| 0x0b | Fördern > 3000 mm |
| 0x0c | Odometer-Stillstand 500 ms (mode 4: 25×20 ms in Folge < 0,01 mm vorwärts; Rückfedern zählt als Stillstand; nur gemeinsames Odometer, kein Sensor) |
| 0x0d | Buffer nicht voll |
| 0x0e | feed extruder timeout (0x10 mode 7) |
| 0x13 | Rückzug: Buffer-Sensor-Timeout 5 s |
| 0x14 | pull back hub PE timeout (Pull-back Stufe 2: Hub nach 7 s noch belegt) |
| 0x16 | Slot ohne Filament (setzt STATE_ERROR) |
| 0x17 | pull_back_hub: Sensor A nicht aktiv (= Einlauf meldet kein Filament, `FUN_08004c5a`) |
| 0x18 | intern „kein Slot belegt“ → auf 0x16 abgebildet |
| 0x19 | pull_back_hub motor block (LIVE 15.10.2026: 2× bei 0x11 [01 01], key865); **BEFUND2:** Odometer steht und Motorblock > 10 s (R[3]) |
| 0x1a | **BEFUND2 (B):** Pull-back p=1 Timeout 40 s (2000×20 ms, Hub nicht frei) – live nie |
| 0x30 | **nur 0x0a**: Preload-Ereignis anstehend (4 B je Slot 1/2/3) (B+L); Host-Name **UPDATE_STATE** (LIVE 15.10.2026) |
| 0x50 | **nur 0x0a** in STATE_PRINT: Sensorprüfung ≠ 0 (V: Runout); live nie gesehen. **BEFUND2 §4 (B):** aktueller Druck-Slot, `(Einlauf frei) | (Hub frei)<<1` ≠ 0 → Runout, setzt R[8]=1 |
| 0x51 | **LIVE BELEGT 15.10.2026:** Fehlercode in STATE_ERROR, Host-Name **SPEED_ERR**, key846 „empty printing, box speed is smaller than extruder“ (452× am 12.10.); vorher nur aus LED-Mapping bekannt. **BEFUND2 §4 (B):** Thread print_check – ≈10 s ohne „buffer full“ und Odometer ≥ 40 mm nachgeschoben **oder** ≥ 3 Buffer-leer-Meldungen = Buffer-Nachschub zu langsam (Schlupf/Reibung, V) |
| 0x52 | **BEFUND2 §4 (B):** print_check – ≈10 s ohne „buffer full“ und Odometer < 5 mm = kein Vorschub (Blockade, V); nicht, wenn vorher 0x50. Live nie |
| 0x53 | **BEFUND2 (B):** wird nie erzeugt (nur LED-Mapping; Zweig in `FUN_0800ed26` toter Code) |
| S[3] | nur 0x0a in STATE_ERROR: gespeicherter Fehlercode |

Fehler ∉ {1,3} setzen STATE_ERROR mit S[3]=Code (`FUN_08003e3a`); löschen per 0x10 mode 3.
**LIVE 15.10.2026:** der Host löscht Fehler stattdessen per 0x04 [00 01] (ERROR → IDLE),
0x10 mode 3 wird nie gesendet.
Weitere Codes nur im LED-Mapping (~~0x1a~~, 0x20, 0x22, ~~0x51~~, ~~0x52~~, 0x53), Quellen offen
(0x51 inzwischen live als SPEED_ERR belegt, s. oben; **BEFUND2:** 0x1a/0x51/0x52 Quellen gefunden,
0x53 nie erzeugt; offen nur noch 0x20/0x22).

**0x0a Box-State** (`FUN_0800ed26`, Antwort 6 Datenbytes, B+L):

| Byte | Inhalt |
|---|---|
| 0 | S[1] (live 0x13; ~~Quelle offen~~, = 0x52-Antwort Byte 0). ~~LIVE 15.10.2026 (V): Kandidat~~ **BELEGT (BEFUND2 §1, B+L): Temperatur °C**, AHT30 @I2C 0x38, int8 (Zweierkomplement), abgeschnitten, Takt ~1,2 s; bei AHT-Initfehler dauerhaft 0 |
| 1 | S[0] (live 0x34). **BELEGT (BEFUND2 §1, B+L): rel. Feuchte %**, uint8 0..100, abgeschnitten |
| 2 | Flags: `R[3] | 0x80` einmalig wenn R[3]≠0 (~~V: Motorblock~~ **BEFUND2 §8, B:** R[3] = Motorblock > 10 s aus Timer motor_blocked_detect_timer), sonst R[5] = Blockmaske sofort (wird zurückgesetzt). **Bit 4 = Hauptmotor, Bit 0–3 = Feeder A–D**; live 0x90 = Hauptmotor > 10 s, 0x91 = + Feeder A, 01/02/04/08 = Feeder-Block (V: Pretension-Endanschlag) |
| 3 | **Box-Zustand** S[2]: IDLE=0, PRELOAD=1, PRINT=2, RELOAD=3, ERROR=4, TEST=5 (≥6 „unknown“) |
| 4 | Bitmaske Sensor B (aktiv = 1); LIVE 15.10.2026: = Slot im Hub, deckungsgleich mit 0x08 [01] |
| 5 | ~~Bitmaske „Slot hat gültige RFID-Daten“ (V)~~ ~~UNBELEGT~~ – live in 8 268 R153-Frames immer 0, auch mit RFID-Spule (WIDERSPRICHT der Deutung, LIVE 15.10.2026). **BEFUND2 §2 (B):** Bit i = Einlauf-Flag && Tag geladen && **Tag-Typ 7** && Tag-Feld+0x10 == 0 – Sperrbit „Typ-7-Tag ohne Datenfreigabe“; schließt 0x02-Daten/0x03-% des Slots aus (Nachbau `inlet_tag_mask`) |

**STATE_CHECK_HUB ist KEIN Box-Zustand**, sondern Zustand 2 der Preload-Statemachine
`FUN_080064b8` (0 forced-Start, 1 idle/Queue, 2 CHECK_HUB, 3 pretension, 4 read rfid +
margin estimation) – taucht in 0x0a nicht auf. Status der 0x0a-Antwort: ERROR → S[3];
PRINT → 0x50 oder 0; Preload-Ereignis → 0x30 mit 4 Byte (dann anderes Layout!); sonst 0.
Live `09 00 0a 13 34 00 00 00 00` = Zustand IDLE, keine Sensoren/RFID-Bits ✓;
`07 30 0a 03 00 00 00` = Ereignis Slot A Wert 3 ✓.
**Gilt erst ab Box-FW 153** – FW 113 (bis 12.10.2026) liefert 4 Datenbytes `b0 b1 ?? Zustand`
mit Status 0 (`f7 01 07 00 0a 17 34 00 00 81`, `klippy.log.2026-10-05:4452`); ein 4-Byte-
Frame ist also nur mit Status 0x30 sicher ein Ereignis (Nachbau `parse_box_state(msg,
box_fw)`). Ereigniswerte (Deutung, LIVE 15.10.2026 gestützt): **1 = Einstecken/Preload-
Start, 2 = Herausziehen, 3 = Preload fertig** (Details §6.6).

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
2. High-Level-Load flankiert 0x10 mit Feed-Mode (0x04), ~~Motor-Engage/Release~~
   Auto-Preload-Sperre an/aus (0x0f, KORRIGIERT 15.10.2026 Ghidra),
   Hardware-Ping (0x08), Buffer-Read (0x05) und Print-Mode (0x04).
3. `MEASURING_WHEEL` (0x0e) alle ~3 s während/nach dem Extrudieren.
4. Resultat: `change_color_num` T1A → 1, `extrude = 229.17 mm`

### Zweiter Slot T1B (12.10.2026, 160 mm @ 350 mm/min, 220 °C)

- Gleicher Ablauf, `tnn: T1B`, `extrude_process_stage7_ret: OK`
- 0x10-Frames: `f7 01 07 00 10 c4 xx xx xx xx` – Byte1 konstant `0xc4`, 4 variable Bytes
  (Encoder/Position); der Slot steckt NICHT im Frame – die Box kennt ihn kontextuell
  **BELEGT 15.10.2026 (Ghidra):** das sind Antworten auf 0x10 **mode 5** = Status 0 +
  4 Byte Odometer-Float BE (`c4 8d ad 5a` ≈ −1133 mm); `0xc4` = Vorzeichen/Exponent,
  kein Konstantbyte. Slot im Antwortframe ist im Format nicht vorgesehen.
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

### CFS-Firmware: Flash-Map, Bootloader, Shell (fake-name + eigene Prüfung, 15.10.2026)

Quelle: fake-name/cfs-reverse-engineering (Connor Wolf, Stand 2025-04, ältere FW
App 108/113; keine Lizenz für Texte/Dumps → nur Fakten übernommen) + eigene Prüfung.

- **MCU:** GD32F303VET6 (512 KiB Flash), RTOS **RT-Thread 5.0.2** (fake-name readme.md:17,
  firmware.md:13-28). Stark.
- **Flash-Map (stark, selbst verifiziert per String-Zeiger-Test: zählt Wörter im Image,
  die bei angenommener Basis auf String-Anfänge zeigen):**
  - Bootloader `cfs0_050_G32` 12 KiB @`0x08000000`
  - cfs0-App @`0x08003000` (Versionsstring bei App+0x200) – `cfs0_000_153`: Basis
    0x08003000 = **179 Treffer** vs. 1 bei 0x08000000
  - cfs6-App @`0x08004000` – `cfs6_222_000`: 0x08004000 = **55** vs. 0 (passt zum
    Reset-Vektor 0x08004144 aus dem alten Import)
  - DB-/Parameter-Page 2 KiB @`0x0807F800` (letzte Flash-Page)
  - Folge: unsere Ghidra-Importe @0x08000000 waren um 0x3000/0x4000 verschoben –
    erklärt die „keinen direkten Code-XRefs“ (PROTOKOLL-GESAMT.md §5).
    ~~Neuanalyse 15.10.2026 ausstehend~~ **ERLEDIGT 15.10.2026 (Ghidra):** cfs0_000_153
    @0x08003000 → 1063 Funktionen (alte Basis 927), Strings mit XRef 538 → 930, XRefs
    812 → 1464; cfs6_222 @0x08004000 → 936 Funktionen. Dispatcher, Handler und Semantik:
    `GHIDRA-CFS-BEFUND-2026-10-15.md` und Abschnitt „Func-Tabelle CFS-App R153“ unten.
- **Bootloader-Protokoll (stark, Disassembly des Bootloader-Dumps durch unseren Agent):**
  - spricht selbst F7-Frames mit CRC-8 (poly 0x07, init 0, über [len..data]) – wie die App
  - akzeptiert Zieladressen `0xFF`, `0xFE`, eigene Adresse und `0xF0` (Bedeutung von
    0xF0 unklar); Längengrenze **len ≤ 0xFC** (serial485 prüft bis 255 – nur notiert)
  - **UNTERSCHIED Bootloader ↔ App (BELEGT 15.10.2026, Ghidra):** die App-Empfangs-
    Statemachine `FUN_0800f880` akzeptiert NUR `eigene`, `0xEA`, `0xEB` (kein 0xFF/0xFE/0xF0);
    0xFF/0xFE kommen in der App nur im Modul `RS485_AUTO_ADDR` vor (`FUN_0800a92c`:
    0xFF/0xFE/eigene für 0xA0–0xA3). Kein Widerspruch, sondern zwei getrennte Parser –
    aber: ein App-Kommando an 0xFF wird von der App (außer Auto-Addr) NICHT bearbeitet.
  - `0xA1` (Discovery): Antwort nur wenn `data[0] ≤ eigene Gruppe ≤ data[1]`, dann
    Zufalls-Backoff (LCG `x*0x41C64E6D+12345`, `% 1000`, ≠ 0 – vermutlich ms) → erklärt
    den Host-Timeout von 1 s (Antworten streuen über bis zu ~1 s)
  - `0xA0` (Zuweisung): data = `[addr][12 B UID]`, UID-Vergleich, `addr ≠ 0` wird
    übernommen; unadressiert gilt uid/Adresse `0xFE`
  - Broadcast `0x0B [01]` → Sprung in die App (bestätigt unseren Loader-Check in
    `auto_addr.py`)
  - RS485 = USART1 auf PA2/PA3, 8N1, ≈230400 Bd (mittel; fake-name PCBs.md:64-68
    nennt PA2 als TX-Enable-Pfad, PA3 RX)
- **finsh-Shell (stark):** nur über **SEGGER RTT/SWD** erreichbar (Konsole `jlinkRtt`,
  fake-name firmware.md:985; String auch in unserer R153), **NICHT über RS485**. Die
  Shell-Kommandos (`preloading …`, `rfid …`, `MS3791_test` …) sind also kein Buspfad.

### CFS-Hardware (fake-name PCBs.md + Mitschnitte `probing/*.dsl`, 15.10.2026)

- **Hauptantrieb = BLDC**, bestückt ist **MS8828** (Relmon/Ruimeng); die FW nennt ihn
  `MS3791` (PCBs.md:22). Pins: SS (Bremse) PE4, FG (Tacho) PE11, F/R PE3, PWM PE13
  (PCBs.md:207-216). ~~„MS3791 (Stepper)“~~ korrigiert. Stark.
- **4× H-Brücke HTD8236** (AT8236-kompatibel, FW-Name `at8236`) + LM358 + 0,5-Ω-Shunts
  zur Strommessung (PCBs.md:10-11, 128-144). Slot A = M1: IN PD3/PD2, Strom PC5. Stark.
- **2× RFID-Leser FM17622** (MFRC522-kompatibel), I2C-Adresse 0x28, J8 = PB10/PB11,
  J19 = PB8/PB9 (PCBs.md:171-182, 219-227); Mitschnitt zeigt nur Init (SoftReset,
  Reg 0x01 → 0x20). Stark.
- **AHT30** (Temp/Feuchte) I2C 0x38: Trigger `AC 33 00`, 6 Byte nach 200 ms, Periode
  ~1,2 s (Mitschnitt; Pins PC11/PC12, firmware.md:404-405). Stark.
- **Magnet-Encoder MT6826S** (Board `MF003_ENCODER_V12`, Feeder-Baugruppe, PCBs.md:252-256),
  SPI PB12-PB15, Mode 3, liest Reg 0x003 mit 50 Hz. Stark. **Offen:** misst er das
  Messrad oder den BLDC? → mit 0x0e MEASURING_WHEEL korrelieren.
- EEPROM 24C16, LCD-Treiber TM1621B – für den Bus irrelevant.

### CFS-Bus physisch (15.10.2026, Quellen fake-name + eigene Deutung)

- **Interner Stecker J4, 8-polig** (2 mm, vermutlich Yeonho SMW200-08; PCBs.md:52-78;
  Belegstärke mittel-hoch):

  | Pin | Signal |
  |---|---|
  | 1, 2 | +24 V |
  | 3, 4 | GND |
  | 5 | RS485-B |
  | 6 | RS485-A |
  | 7 | Buffer-SW1 (PD8) |
  | 8 | Buffer-SW2 (PD9) |

- **Rückwand-Board `MF003_INTERFACE_V20`** mit zwei 2×3-Buchsen → Daisy-Chain zur
  nächsten Box (Deutung). Externer Bus 6-polig laut Netzliste des fake-name-Adapters:
  1 B, 2 GND, 3 24 V, 4/5 Buffer, 6 A – **schwach**: Steckertyp ist dort ausdrücklich
  „guesswork“ (readme.md:37), vermutet Molex Micro-Fit 3.0; Kabelfarben PCBs.md:238-245.
- **A/B-Benennung ist in den Quellen widersprüchlich** → am Gerät messen: in Ruhe
  liegt **A hoch, B tief** (Bias).
- **Transceiver TPT75176H** (3Peak), Bias je 300 Ω A→5 V / B→GND, 120-Ω-Terminierung
  vorgesehen, aber **NICHT bestückt** (PCBs.md:7, 72-74). Laut fake-name
  Pseudo-Open-Collector-Betrieb (DI fest auf GND, DE wird per TX geschaltet) –
  unsicher, begrenzt aber in jedem Fall Flankensteilheit/Baudrate.
- **Buffer-Signale laufen NICHT über RS485:** der 4-in-1-Buffer hat zwei
  Open-Collector-Ausgänge (Lichtschranken je Endlage), die direkt an jede Box gehen
  (J4 Pin 7/8 → PD8/PD9); die fördernde Box regelt den Buffer selbst
  (PCBs.md:263-271). `0x05 GET_BUFFER_STATE` ist nur die Abfrage dieses Zustands.
  Passt zu `box-live.cfg` ohne Buffer-Pin.

## 3. Motor/Toolhead-Boards

FW: `fw/F008|F012/mot*_022_C30-mot2_002_081.bin` (123K, Klartext: `mot2_002_081`, `flash_param_version`, `motor_R/L/Ke`, `boot_key`, `Rebooting system...`),
`mcu0*/noz0*` (29-31K, `CanBoot`, `mcu0_022_000`), `belt/*.bin` (6,5K, `belt_mdl start!`),
`rfid/*.bin` (20K, Cortex-M), `K1/*`, `F038/*`.

Host: `motor_control_wrapper`: `motor_version/sys_param/flash_param`, `motor_read485_addr`, `motor_encoder_calibrate`, `motor_elec_offset_calibrate`, `set_e/z_stall_mode`, `check_protection_code`, `data_pack_extruder`.

### LIVE: Motor-Boards am 485-Bus (Cut-Kalibrierung 12.10.2026, 23:51)

- Adressen `0x81..0x84` = closed-loop Motor-Boards (X/Y/Z/Z1, F008 mot-Boards)
- `0x11` = Modus-Umschaltung (1-2 Datenbytes: `01`, `02 00`, `03`)
  **PRÄZISIERT 15.10.2026 (live):** `[01]`/`[02]`/`[03]` = Stall-Mode setzen (Echo),
  `[04]` = Modus lesen → Antwort `[mode, 00]`; `02 00` war eine **Antwort**, kein Request.
- `0x06` = **Sys-Param (sehr wahrscheinlich Schreiben)** – KORREKTUR 15.10.2026:
  Frames trugen `00 00 96 43` → `00 00 c8 42`. Das sind **Little-Endian-Float
  300.0 → 100.0** = exakt `MOTOR_SYS_PARAM NUM=1 DATA=1 ID=70 PARAMS=300|100
  PARAMS_TYPE=float` (`config/F008_CR0CN240319C13/gcode_macro.cfg:796/799`,
  `sensorless.cfg:145/211`, Makro CALIBRATE_CUT_POS). Passt zu k2-vanilla
  (PROTOKOLL-GESAMT.md 1.9: 0x06 = Sys-Param).
  ~~Big-Endian dekodiert 38467 → 51266 (Diff 12799)~~ – **WIDERLEGT 15.10.2026**
  (Artefakt der falschen Deutung; ebenso die ältere 0x4396/0x42c8-Lesart).
  ~~OFFEN: Position der ID 70 (0x46).~~ **GEKLÄRT – LIVE BELEGT 15.10.2026:** die ID
  steht im **Request**: Schreiben `[01, id] + <f LE>`, Lesen `[02, id]`, Übernahme `[03]`;
  der unten zitierte Frame ist die **Antwort** (Echo des Werts). S. §3.1. Der dokumentierte Frame
  `f7 81 07 00 06 00 00 96 43 09` hat len=`07` (= 3 + 4 Datenbytes) und gültige
  CRC – im Frame fehlt also kein Byte, er trägt NUR den Float. Vanilla-Form wäre
  `[01, id] + <f` (→ hypothetisch `f7 81 09 00 06 01 46 00 00 96 43 8f`), ~~auf dem
  Plus NICHT live gesehen~~ **LIVE BELEGT 15.10.2026** (exakt dieser Frame, per
  `build_frame` verifiziert). `read_encoder` in motor*.py bleibt gesperrt (0x08 nie live;
  **NACHTRAG 16.10.2026:** 0x08 ist live der Variablen-Lesepfad, Encoder-Index offen, §3.1).
  NACHTRAG 15.10.2026: **ID 70 = `controller_pos_loop_pid_param_kp`
  (Positionsregler-Kp, CONFIGURABLE)** laut grant0013/k2-reverse-engineering
  `docs/motor_params_map.md:103` (Fremdquelle K2, Namen aus klippy.log) →
  CALIBRATE_CUT_POS setzt Kp auf 300 und danach zurück auf 100.
- ~~`0x0c` = Parameter (8 Datenbytes `00*8`) an alle 4 Boards gleichzeitig~~
  **WIDERLEGT – LIVE BELEGT 15.10.2026:** 0x0C ist eine **Schutz-/Protection-Abfrage**:
  Request 1 Byte `[0b]` (= `MOTOR_CHECK_PROTECTION_AFTER_HOME DATA=11`), Antwort 8 Byte
  Status (live immer 8×`00` = kein Fehler); die `00*8` waren die Antwort. S. §3.1.
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
| `0x05` | control | **live 15.10.2026**: `[01]`/`[02]` an X/Y bei BELT_TENSION, nie Antwort; Semantik offen |
| `0x06` | sys_param read/write | **live**: `[02,id]` lesen / `[01,id]+<f>` schreiben / `[03]` übernehmen; flüchtig (§3.1) |
| `0x07` | flash_param read/write | unbelegt (Stubs `get/set_flash_param`) |
| `0x08` | get feedback | **live 16.10.2026** (Motor X): `[idx]` → 4 B LE = „aktueller Wert einer vom System unterstützten Variable“, idx 1..12; Bedeutung je Index offen (§3.1) |
| `0x0B` | boot | unbelegt (Motoren); bei CFS = Loader-Check an 0xFF |
| `0x0C` | protection status | **live**: Request `[0b]` → 8 B Status (8×`00` = ok); `[05]` ohne Antwort – „protection status“ bestätigt |
| `0x0D` | systemid | unbelegt |
| `0x0E` | read/set_addr | **live 15.10.2026**: `addr 0E [02]` → `[addr]`; `FF 0E [01]` Broadcast ohne Antwort |
| `0x0F` | version | **live 16.10.2026** (Motor X): `[00]` → 12 B Versionsblock (fw 1.9.7.81 …, §3.1) |
| `0x11` | stall_mode | **live**: `[01]/[02]/[03]` setzen, `[04]` lesen → `[mode,00]` |
| `0x12` | dev_uuid | **live 15.10.2026**: `addr 12 [02]` → 8 B, Präfix `cd ab` + 6 B individuell |

Adressen dort: `0x81..0x84` = X/Y/Z/Z1 über Main-MCU-Transparent-Pfad
(`transparent_send`/`transparent_response`), `0x85` = Extruder über Nozzle-MCU
transparent. **LIVE 15.10.2026: gilt für den K2, NICHT für den K2 Plus** – dort laufen
X/Y/Z/Z1 ausschließlich über serial_485 (`/dev/ttyS5`), nur der Extruder über
`transparent_send` (Adresse 0x81 auf dessen eigenem Bus); 0x85 auf ttyS5 antwortet nie (§3.1). Stock-Bootsequenz (aus Logs): Broadcast-Addr-Set `FF 0E 01`, UUID-Abfrage
`81/82 12 02`, Adress-Lesen `81/82 0E 02`, dann Stall-Mode und Protection-Check.

**Bewertung (Stand vor 15.10.2026):** Die drei auf dem Plus live gesehenen Bytes (0x06/0x0C/0x11) passen
widerspruchsfrei zur Fremdtabelle, ID 70 erklärt die 300/100-Werte. Alles Übrige sind
**Hypothesen (Fremdquelle K2, auf dem Plus unbelegt)** – kein sendender Code im Nachbau,
Stubs bleiben gesperrt, bis je Kommando ein Plus-Mitschnitt vorliegt (CHECKLISTE.md).
**NACHTRAG 15.10.2026:** live belegt sind jetzt zusätzlich 0x05, 0x0E, 0x12 (s. Tabelle);
weiterhin nie gesehen: 0x01, 0x03, 0x04, 0x07, 0x08, 0x0B (an Motoren), 0x0D, 0x0F, 0xF0.
**NACHTRAG 16.10.2026:** 0x08 und 0x0F live belegt (gezielte Abfrage Motor X, §3.1);
weiterhin nie gesehen: 0x01, 0x03, 0x04, 0x07, 0x0B (an Motoren), 0x0D, 0xF0.

### 3.1 LIVE BELEGT 15.10.2026: Motor-Kommunikation K2 Plus (klippy.log 05.–16.10.)

Quelle: Live-Logs `live-logs-2026-10-15/` (lokal, nicht im Repo; 10 vollständige Boot-Enumerationen; Zeilen ohne
Suffix = `klippy.log`, Boot 16.10. 01:15 Druckerzeit). Alle Motor-RX-Frames + 88/61
transparent-TX/RX CRC-OK; `serial485.build_frame` reproduziert die Stock-Frames
byte-genau (z.B. `f7 81 04 00 0c 0b 95`, `f7 ff 04 00 0e 01 89`).

- **0x06 Sys-Param** – ID im Request, Antwort nur der Wert:
  - Lesen `addr 05 00 06 02 <id>` → `f7 addr 07 00 06 <f LE> crc` (ID 9 → 123,58).
    ID 10 antwortet mit 2 Byte, ID 226 mit 0 Byte (Motor kennt ID nicht).
  - Schreiben `addr 09 00 06 01 <id> <f LE>` → Echo des Werts
    (`klippy.log.2026-10-12:10700-10707`); Vollframe `f7 81 09 00 06 01 46 00 00 96 43 8f`
    (ID 70 = 300.0) verifiziert. Der frühere „einzige Live-Frame“
    `f7 81 07 00 06 00 00 96 43 09` ist die **Antwort**.
  - Übernahme `addr 04 00 06 03` → `[01]` = Erfolg, einmal je Motor am Enumerationsende.
  - `MOTOR_SYS_PARAM NUM=1 DATA=1 ID=70 …`: NUM = Motorindex (1 → 0x81), DATA=1 = Sub 01.
  - **Flüchtig:** nach Soft-Restart liest der Host die geschriebenen Werte, nach Kaltstart
    wieder Board-Defaults (Kp 300, err_time 0,01, spd_kp 0,028, spd_ki 6,0) → Sub 01/03
    schreibt NICHT dauerhaft; der Host muss nach jedem Power-on neu setzen (Stock tut das).
- **0x0C** Request `[0b]` → 8 B Status (live 8×00); `[05]` nie beantwortet. ~~8-Byte-Parameter
  an alle~~ WIDERLEGT.
- **0x11** `[01]/[02]/[03]` setzen (Echo), `[04]` lesen → `[mode, 00]`
  (`klippy.log.2026-10-12:31417-31455`). Homing: `DATA=1` → `[01]` nur X/Y, `DATA=2` → `[02]` an alle.
- **0x0E** `addr 0e 02` → `[addr]`; `FF 0e 01` Broadcast ohne Antwort.
- **0x12** `addr 12 02` → 8 B UUID, Präfix `cd ab` (Rest individuell, hier nicht wiedergegeben).
- **0x05** `[01]`/`[02]` an X/Y während BELT_TENSION, **nie Antwort**
  (`klippy.log.2026-10-12:28621-28636`); Semantik offen (grant: „control“).
- **Extruder:** 0x85 über serial_485 antwortet nie (≈10 s Timeouts je Boot,
  `klippy.log.2026-10-05:4013-4280`); erreichbar nur über `transparent_send` (oid 1,
  sehr wahrscheinlich nozzle_mcu) unter **Adresse 0x81**. Stock-Mapping stepper 5 → 0x85
  auf ttyS5 ist ein Stock-Fehlgriff.
- **Retries:** alle Motor-, Broadcast- und Belt-Anfragen laufen mit `retries = 0`
  (Belt 0x21/0x22: `retries = 4`); Motoren antworten in < 20 ms.
- **Pfade:** X/Y/Z/Z1 + CFS 0x01 + Belt 0x21/0x22 teilen sich **einen** RS485-Port
  (ttyS5, 230400, eine Queue; Motor-, CFS- und Auto-Addr-Frames verschränkt, `klippy.log:4315-4373`).

**Stock-Bootsequenz K2 Plus** (`klippy.log`, Boot 16.10.):
1. 2127–2150 MCU-Connect (mcu ttyS2, nozzle_mcu ttyS3, rpi); 2167 serial_485 ttyS5.
2. 2259–2267 „initial set_pin_io“: dir/step-Pins der 4 Motoren über die Main-MCU auf
   Config-Werte (vermutlich Strapping/Adresswahl der Motorboards).
3. 2269 S485 `FF 0E 01` (Timeout) → 2313 transparent `FF 0E 01` (leer).
4. 2317–2371 je Motor `0E 02` + `12 02` (erste Runde antwortet nur Z).
5. 2376 transparent `81 0E 02` → Extruder meldet 0x81.
6. 2379–2420 zweite Runde, alle antworten → 2422 „All motors are normal“.
7. 2424–2439 Stall-Mode `[02]` an Z, Z1 (S485) und E (transparent); X/Y erst beim Homing.
8. 2442–2469 `0C [0b]` an 0x81–0x84 + E → 8×00; 2471–2491 `0C [05]` → keine Antwort.
9. 2492–2500 „restore set_pin_io“ (alle Pins 0).
10. 2501–4303 flash_param-Enumeration stepper 1–5 (0x06 Sub 02/01/03), stepper 5 = 0x85 Timeouts.
11. 4315–4373 `12 02` UUID erneut, verschränkt mit CFS-Auto-Addr (0xA2/0xA3).
Abweichungen zu grant (K2): alle vier Achsen antworten (grant: Z/Z1 stumm), Stall/Protection
an X–Z1 über serial_485 statt transparent, GPIO-Setzen vor **und** nach der Abfrage.

**flash_param-Enumeration: 227 IDs (0–226), nicht 226** – Namen identisch zu grant (inkl.
Tippfehler). **Log-Falle:** in `update_flash_param key:… value:N` ist N die **Param-ID**,
nicht der Wert. Gelesen werden nur IDs 9/10/25 + in `[motor_control]` konfigurierte;
grants „result is None“ für 9/10/25 ist ein Artefakt des Extruders (0x85-Timeout).
Volle Tabelle (lokal, nicht im Repo): `/tmp/claude-0/livelog-motor/params.csv`.

**Nachbau (parallel umgesetzt 15.10.2026, `nachbau/motor.py` / `motor_mini.py`):**
`read_sys_param` ([02,id], 4/2/0-Byte-Antwort), `build_sys_param_write_payload`
([01,id]+<f>) / `build_sys_param_update_payload` ([03]), `write_sys_param` nur mit
`confirm=True`, `check_protection` (0x0C [0b] → 8 B), `get_stall_mode` (0x11 [04]),
`read_485_addr` (0x0E [02]), `read_uuid` (0x12 [02]), `SYS_PARAM_NAMES` (227 Einträge),
`MOTOR_RETRIES = 0`, Timeouts 0x06/0x12 1,0 s (live 0x85-Timeouts 1,002–1,009 s),
0x0E/0x0C 0,05 s (live 0,052–0,066 s); `read_encoder` bleibt gesperrt.

#### NACHTRAG 16.10.2026: Live-Abfragen Motor X (0x81) – 0x0F, 0x08, Pin-IO

Quelle: `LIVE-ABFRAGEN-2026-10-16.md` (Rohdaten Scan: `arbeitsdateien-lokal/motor_get_scan.txt`,
lokal, nicht im Repo). Gesendet über Moonraker mit Creality-G-Codes (lokales Whitelist-Wrapper-
Skript, lässt nur lesende Motor-G-Codes durch), Drucker im Standby, **nicht referenziert** (Motoren stromlos/inaktiv).

> **WARNUNG – Creality-Host-Bug (zweimal reproduziert):** `MOTOR_GET … DATA=0` und
> `MOTOR_GET … DATA=13` lösen in `motor_control_wrapper.so` einen internen Fehler aus
> (`key60: Internal error on command:MOTOR_GET`) → **Klipper geht in SHUTDOWN**. DATA=0 wird
> schon vom G-Code-Parser abgelehnt („minimum of 1“) und crasht trotzdem. Gültig: **DATA=1..12**.
> Recovery: `FIRMWARE_RESTART` (`POST /printer/firmware_restart`), danach ~60 s bis Motoren/Box
> bereit. Ob der **Motor** Index ≥ 13 verträgt, ist offen (bei 13 nicht geprüft, ob ein Frame
> rausging). Nachbau `read_variable` akzeptiert deshalb nur 1..12; eine eigene Implementierung
> muss den Index **vor** dem Senden validieren.
> **→ Ursache seit 16.10. BELEGT (Traceback, s. „NACHTRAG 16.10.2026 (2)“ unten):** host-seitiger
> TypeError in `parse_num_to_byte(None)`, **kein Frame** ging auf den Bus.

- **0x0F Version** (`MOTOR_VERSION NUM=1 DATA=0`): TX `81 04 00 0f 00` → RX
  `f7 81 0f 00 0f 01 09 07 51 01 03 01 03 02 4b 01 00 02` (CRC ok). 12 B Nutzdaten, Feldnamen
  aus den Wrapper-Strings:

  | Bytes | Feld | Wert |
  |---|---|---|
  | 0–3 | `fireware_ver` | `01 09 07 51` = 1.9.7.81 |
  | 4–5 | `cmd_system_ver` | `01 03` = 1.3 |
  | 6–7 | `protection_ver` | `01 03` = 1.3 |
  | 8–9 | `flash_param_ver` | `02 4b` = 2.75 (minor dezimal) |
  | 10–11 | `logger_ver` | `01 00` = 1.0 |

  Nicht verwechseln mit dem Update-Pfad F0/00 (`query_version`, live nie beobachtet).
- **0x08 Variablen-Lesepfad** (`MOTOR_GET NUM=1 DATA=idx`, Host-Text „系统所支持变量当前值“ =
  aktueller Wert einer vom System unterstützten Variable): TX `81 04 00 08 <idx>` → RX
  `f7 81 07 00 08 <4 B LE> crc`. Der Host dekodiert je Index als float oder int, gibt aber
  **keine Namen** aus. Scan Motor X (Standby):

  | idx | Rohbytes | Host-Wert | Typ (Host-Dekodierung) | RX-Frame |
  |---|---|---|---|---|
  | 1–8 | `00 00 00 00` | 0.0 | float | `f7 81 07 00 08 00 00 00 00 0a` |
  | 9 | `33 33 d3 3a` | 0.0016113 | float | `f7 81 07 00 08 33 33 d3 3a e6` |
  | 10 | `33 33 53 3a` | 0.0008057 | float | `f7 81 07 00 08 33 33 53 3a 50` |
  | 11 | `52 04 00 00` | 1106 | int | `f7 81 07 00 08 52 04 00 00 71` |
  | 12 | `00 00 00 00` | 0 | int | `f7 81 07 00 08 00 00 00 00 0a` |

  Welcher Index Encoder/Position/Strom ist: ~~offen~~ **5–8 seit 16.10. REA-belegt**
  (pos_fdb/spd_fdb/id_fdb/iq_fdb, s. NACHTRAG (2)); 1–4/9–12 weiter ohne Namen. Hinweis aus
  den Wrapper-Strings: Stock nutzt selbst `MOTOR_GET NUM=1 DATA=7 EXTRUDER=1`,
  `… DATA=8 EXTRUDER=1` und `MOTOR_GET NUM=1 DATA=1` (`_stats_motor_get_value`) – Kontext
  unbelegt. Nächster Schritt: Scan nach Homing/in Bewegung, Namen aus `motor_control_wrapper.so`.
- **0x0E** `[02]` → `f7 81 04 00 0e 81 00` (eigene Adresse 0x81, bestätigt §3.1).
- **0x0C** `[0b]` (`MOTOR_AUTO_CHECK_PROTECTION NUM=1 DATA=11`) → `f7 81 0b 00 0c 00×8 fb`
  (keine Fehler, bestätigt §3.1).
- **Pin-IO** (`MOTOR_READ_ALL_PIN_IO`): **kein RS485** – Klipper-GPIO-Abfrage `gpio_status`
  (oid 11) an der **Main-MCU**: PB9, PB10, !PB7, PB8, PB5, PB6, PA1, PB15 = 0 (dir/step-Pins der
  4 Motoren). Für einen Pi-Ersatz heißt das: die GPIO-Phase (initial/restore `set_pin_io`)
  braucht weiter die Main-MCU.
- **Nachbau** (`nachbau/motor.py` / `motor_mini.py`): `query_motor_version()` (0x0F [00] → dict
  `fireware_ver`/`cmd_system_ver`/`protection_ver`/`flash_param_ver`/`logger_ver` + `raw`;
  Alias `motor_version`, in motor.py zusätzlich `motor_version_normal` = früherer Stub),
  `read_variable(idx)` (nur 1..12, sonst `ValueError` ohne Senden; Rückgabe `raw`, `float` (<f),
  `int` (~~<i, Vorzeichen unbelegt~~ → seit REA 16.10. `<I` unsigned wie der Host), `type`/`value`
  nach `VARIABLE_TYPES` = Host-Dekodierung live 16.10.). `retries=0`; Timeout 0x0F/0x08 **unbelegt** (live kein Timeout gemessen) → 1,0 s.
  `cfswatch.py` dekodiert 0x0F-/0x08-Motorantworten. Selbsttests mit den Live-Frames.

#### NACHTRAG 16.10.2026 (2): REA-Analyse `motor_control_wrapper.so` + Crash-Ursache belegt

Quelle: statische Analyse (REA/Ghidra + eigene `llvm-objdump`-Auflöser für Cython-Stringtabelle,
Konstanten, Tupel; nichts gesendet), Details und Adressen: `REA-MOTOR-BEFUND-2026-10-16.md`.
Binary `rootfs-live/usr/share/klipper/klippy/extras/motor_control_wrapper.cpython-39.so`
(armhf, Cython 0.29.21), SHA256 `d8191b77…b4b6e0f`. Adressen = ELF-vaddr (Ghidra +0x10000).

**Motor_cmd (Host-Tabelle, Modul-Init @0x10168/0x10a9c, BELEGT):**

| Cmd | Host-Name | Live | Nachbau |
|---|---|---|---|
| 0x01 | reboot | nie | Stub `reboot()` |
| 0x03 | encoder_calibrate_official | nie | Stub `encoder_calibrate()` |
| 0x04 | elec_offset_calibrate | nie | Stub `elec_offset_calibrate()` |
| 0x05 | control | unbeantwortet | nicht gebaut |
| 0x06 | sys_param | belegt | `read/write_sys_param` |
| 0x07 | flash_param | nie | Stubs `get/set_flash_param` |
| 0x08 | get | belegt | `read_variable` + Feedback-Helfer |
| 0x0B | boot | nie (NIE senden) | Sperre `boot()` |
| 0x0C | protection | belegt | `check_protection` (seit NACHTRAG (4) dekodiert) |
| 0x0D | systemid | nie | Stub `systemid()` |
| 0x0E | read485_addr / set485_addr | belegt (Lesen) | `read_485_addr` |
| 0x0F | version | belegt | `query_motor_version` |
| 0x11 | stall_mode | belegt | `set_mode` / `get_stall_mode` |
| 0x12 | dev_uuid | belegt | `read_uuid` |
| 0x13 | err_detail_data | nie | ~~Stub~~ `read_err_detail()` (FW-statisch, NACHTRAG (4)) |
| 0x14 | cut_detail_data | nie | ~~Stub~~ `read_stall_detail()` (FW-statisch) |
| 0x15 | pos_data_sample | nie | ~~Stub~~ `read_pos_samples()` (FW-statisch) |
| 0x16 | spd_data_sample | nie | ~~Stub~~ `read_spd_samples()` (FW-statisch) |

`Motor_Addr`: NUM-String '0'..'8' → 0x80..0x88, '9' → 0xFF (Broadcast); `MOTOR_NUM_TO_NAME`
1..5 → x, y, z, z1, e. Frame = `Motor_Addr[NUM]` + Länge + `00` + Cmd + Payload + CRC.

**0x08-Indexnamen (BELEGT 5–8):** Modul-Dict `NO_ERR_CODE_TO_GET` = {'pos_fdb':5, 'spd_fdb':6,
'id_fdb':7, 'iq_fdb':8} (@0x10cf8–0x10d60), gelesen von `_motor_no_cut_detail_data` (iteriert
`.items()`, je Index ein 0x08-Request). = Positions-, Drehzahl-, d- und q-Strom-Rückmeldung der
FOC-Regelung. Für 1–4 und 9–12 gibt es im Host **keine** Namen (keine weitere Tabelle;
`MOTOR_DATAS` ist nur die lokale Prüfliste [0..15] in `validate_data_params`).

**Typkette (BELEGT, fest verdrahtet):** `motor_get` (@0x51524, Py-Z. 1987/1989) prüft
`data in (1..10, 17, 18)` → `struct.unpack('<f', …)`, `data in (11..16)` → `struct.unpack('<I', …)`
(**unsigned**). Dieselbe Kette in `motor_extruder_get` und `_motor_no_cut_detail_data`. Der Motor
liefert keine Typinformation. Wertbytes = `result[5 : result[2]+2]` (= `[5:9]` bei Länge 7).
Der Host kennt 13–18, per G-Code (`maxval=12`) sind sie aber unerreichbar; am Motor live nicht
geprüft → Nachbau sperrt sie bewusst.

**Crash-Mechanismus DATA=0/13 – BELEGT** (Drucker-`klippy.log` 16.10. 13:12:27, Traceback):

```
File ".../motor_control_wrapper.py", line 1982, in Motor_Control.motor_get
File ".../box_wrapper.py", line 384, in ParseData.parse_num_to_byte
TypeError: 'NoneType' object cannot be interpreted as an integer
```

Ablauf: `validate_data_params` (`get_int('DATA', 0, minval=0, maxval=15)`) lässt 0 und 13 durch;
in `motor_get` folgt `data = gcmd.get_int('DATA', 0, minval=1, maxval=12)`. Creality's `gcode.py`
wirft bei Bereichsverletzung **keinen** CommandError, sondern gibt nur eine Warnung aus und liefert
`None`; `motor_get` rechnet weiter, `self.parse.parse_num_to_byte(None)` scheitert → `key60` →
SHUTDOWN. `motor_send_data` wird **nie erreicht – kein Frame auf dem Bus** (REA-Variante A
bestätigt, Variante B „UnboundLocalError 'value'“ widerlegt). Dieselbe Klasse droht vermutlich bei
NUM=0 oder NUM≥11 (VERMUTET, nicht getestet).

**Interne Abfragen DATA=7/8 EXTRUDER=1 (BELEGT):** `Motor_Control.stats(eventtime)` (Klipper-
Stats-Timer, nur bei `overcurrent_switch` und `motor_params_init`) führt
`MOTOR_GET NUM=1 DATA=7 EXTRUDER=1` / `DATA=8 EXTRUDER=1` aus, liest `_stats_motor_get_value`
und schreibt `motor_control: <id> <iq> overcurrent=<hypot(id,iq)>` ins Log → **Extruder-
Überstrom-Monitoring** |I| = √(id²+iq²), kein Zusammenhang mit der Cut-Erkennung. Transport über
`transparent_send` der Düsen-MCU, nicht über ttyS5. `MOTOR_GET NUM=1 DATA=1` ist nur der
Hilfetext („打印系统所支持变量“), kein interner Aufruf.

**Fehler-/Warnbits (BELEGT als Bit→key):** `motor_error_dict` Bit 0..13 → key781..794
(Encoder-Sprung, Encoder-Lesefehler, SW-Spitzen-, SW-Dauer-, HW-Überstrom, Drehzahl,
Positionsgrenze, Positions-Sollwertsprung, Schleppfehler, R/L, instabil, Phase fehlt,
Phasen-R-Differenz, Sonstige); `motor_warning_dict` Bit 0..2 → key795..797 (Kommunikation,
Unterspannung, MCU-Übertemperatur). `ERR_CODE_TO_UNION` (Bitwert → Detailformat): 1 `<ii`
lower/upper_encoder_pulse, 4 `<f` peak_overcurrent_A, 8 `<ff` continuous_overcurrent_A/time_s,
32 speed_over_limit, 64 pos_over_limit_min/max, 128 pos_cmd_mutation_turns, 256 (vermutet)
tracking_error. ~~**Offen:** welche Bytes der 8-B-Antwort von 0x0C [0b] err_code bzw. warning_code
tragen → Nachbau nur Bit-Decoder (`decode_error_bits`/`decode_warning_bits`), kein
`decode_protection(8 B)`.~~ → **BELEGT 16.10.** (Motor-FW + Host-Parser): Byte 0–3 Fehler, Byte 4–7
Warnung, je u32 LE → `decode_protection`, s. NACHTRAG (4).

**Hypothesen (VERMUTET, nicht gebaut):**

| G-Code / Index | Cmd | Hypothese |
|---|---|---|
| ~~idx 9 / 10~~ | 0x08 | ~~0.0016113 = 3,3 V/2048, 0.0008057 = 3,3 V/4096 → 2 bzw. 1 LSB eines 12-Bit-ADC (Strommess-Offset ia/ib?)~~ → **BELEGT 16.10.** (Motor-FW): Phasenströme Ia / −Ib in A, Stillstand = 2/1 LSB, s. NACHTRAG (4) |
| ~~idx 11~~ | 0x08 | ~~1106 = Roh-Encoderwert?~~ → **BELEGT 16.10.** als Single-Turn-Position 0..3199 Counts/Umdrehung, s. NACHTRAG (3) |
| MOTOR_FLASH_PARAM | 0x07 | `[DATA]` (+`00` bei 3/4, +ID bei 5/6); DATA=5 Schreiben (float `f`, uint16 `<H`, uint32 **`>I` Big-Endian**, int16 `<h`, sonst `>I`; Antwort Status 0 = OK), DATA=6 Lesen |
| MOTOR_CONTROL | 0x05 | `[DATA]` bis 12, Antwort 3 Statusbytes (Enable/EN-Pin/Controller/PWM); DATA-Bedeutung offen |
| MOTOR_STALL_MODE | 0x11 | 1 = Endschalter-/Blockiersignal, 2 = Notfall-Schutzausgang (Hilfetext, belegt); 4 = Messmodus für Cut-Positionierung (vermutet, `MOTOR_CHECK_CUT_POS`) |
| MOTOR_CLEAR_ERR_WARN_CODE | 0x0C | `[05]` = Fehler/Warnungen löschen (Hilfetext belegt), `[0b]` = abfragen |
| MOTOR_EXTRUDER_RETRY_PROCESS | – | reine G-Code-Folge, keine eigenen Frames (belegt) |

**Nachbau** (`motor.py`, knapp `motor_mini.py`): `VARIABLE_NAMES` (5–8), `VARIABLE_HOST_FORMATS`
(1..18, Host-Kette), `VARIABLE_TYPES` (1..12, `int` jetzt `<I`), `decode_variable` liefert
zusätzlich `name`/`fmt`; `read_position_feedback()` (idx 5), `read_speed_feedback()` (6),
`read_current_feedback()` (7+8 → id, iq, |i| = hypot); `MOTOR_CMD`, `FUNC_*` für 0x01–0x16,
`MOTOR_ADDR_BY_NUM`, `MOTOR_NUM_TO_NAME`, `ERR_DETAIL_FORMAT`/`FIELDS`, `MOTOR_ERROR_BITS`,
`MOTOR_WARNING_BITS`, `ERR_CODE_TO_UNION`; Stubs 0x01/0x0B/0x0D/0x13–0x16 (NotImplementedError,
senden nichts). `read_encoder` bleibt gesperrt (0x06-Altfalle) und verweist auf
`read_position_feedback` (Einheit/Skalierung von pos_fdb offen). `cfswatch.py` zeigt die Namen
für idx 5–8.

#### NACHTRAG 16.10.2026 (3): Skalierung pos_fdb (idx 5) / idx 11 – LIVE BELEGT

Messfahrt Motor X (0x81) nach `G28` (Bett leer, User-Freigabe), Y=175, Z=10, F3000–6000, je
Position `M400` + `MOTOR_GET` idx 5/11; `[stepper_x]` rotation_distance 40, microsteps 16; am Ende
Park X175 + `M84`, Klipper durchgehend ready. Rohdaten: `LIVE-ABFRAGEN-2026-10-16.md`.

| X [mm] | 50 | 100 | 150 | 160 | 170 | 185 | 200 | 250 |
|---|---|---|---|---|---|---|---|---|
| idx 11 | 690 | 1490 | 2290 | 3090 | 690 | 1890 | 3090 | 690 |
| idx 5 [rad] | 1.35489 | 2.92568 | 4.49648 | 6.06728 | 1.35489 | 3.71108 | 6.06728 | 1.35470 |

- **idx 11 = Single-Turn-Position, 0..3199 Counts/Umdrehung** (3200 = 200 Vollschritte × 16
  Microsteps; bei rotation_distance 40 → **80 Counts/mm**). Live: Counts = (80·X + 3090) mod 3200.
  Name im Nachbau `pos_counts` – **eigener Name** (live abgeleitet, Host-Name unbekannt).
- **idx 5 pos_fdb = idx11 · 2π/3200 [rad]** (Rest ≈ +8·10⁻⁵ rad ≈ 0,04 Counts, Ursache offen).
- Beide **wrappen alle 40 mm** (eine Umdrehung); 50-mm-Schritte zeigen deshalb nur +π/2
  (Aliasing). Eine **absolute** Achsposition liefern sie nicht – die hat nur Klipper
  (Schrittzähler/Homing).
- idx 6 spd_fdb im Stillstand ≈ 0 (±0,006; Einheit weiter offen). Vor G28 (stromlos):
  idx 5 = 0.0, idx 11 = 1106.
- Ablauf-Hinweis: `G28 X Y` + `G1` → „Must home axis first“ – Creality-Klipper verlangt für G1
  **alle** Achsen referenziert (auch Z) → `G28` komplett.

Nachbau: `POS_COUNTS_PER_REV = 3200`, `VARIABLE_NAMES[11] = 'pos_counts'`
(`VARIABLE_NAMES_DERIVED`), `counts_to_rad`/`pos_rad_to_counts`/`counts_to_mm`/`counts_delta`
(wrap-korrigiert, nur < ½ Umdrehung eindeutig), `read_position_counts()` (idx 11),
`read_position_feedback(with_counts=True)` → {pos_fdb, counts, counts_from_rad, mm_in_rev}.
`read_encoder` bleibt gesperrt („Encoder“ = idx 5/11 single-turn). `cfswatch.py` zeigt idx 11
als `pos_counts`. Selbsttests mit den Live-Werten (690 → 1.35481 rad, 800 Counts = 10 mm,
3090 → 690 = +800 über den Wrap).

#### NACHTRAG 16.10.2026 (4): Motor-FW-Befund – Variablen-Tabelle, 0x0C-Layout, 0x13–0x16, STATUS-Bitfeld

Quelle: statische Analyse der **Motor-Firmware** selbst (`MOTOR-FW-BEFUND-2026-10-16.md`; kein
Druckerzugriff) + Live-Werte 16.10. (Fahrt X F600/F1200, DIR-Gegenprobe; `LIVE-ABFRAGEN-2026-10-16.md`).

**Motor-FW:** `fw/F008|F012/mot{0,1,2}_022_C30-mot2_002_081.bin` (116 412 B, untersucht; die
`motor/mot*_02{1,3}_…`-Varianten haben denselben String-Satz), Kennung `mot2_002_081` @0x200,
Version über `fireware ver: %d.%d.%d.%d` (live 1.9.7.81). **CPU WCH QingKe RISC-V (CH32V30x-Klasse,
rv32imafc + Xwchc)**, **Basis 0x00003000** hinter einem 12-KB-Bootloader (303 lui/addi-Treffer auf
String-Anfänge bei 0x3000, sonst ≤ 7). Text-Konsole (`help` …) + Binärmodus. **Befehlsregistrierung
@0x6100–0x63d8** `reg(id, cmd, flag, name, handler)`: 00 help, 01 reboot, 02 encoder_calibrate,
03 encoder_calibrate_official, 04 elec_offset_calibrate, 05 control, 06 sys_param, 07 flash_param,
08 get, 0b boot, 0c protection, 0e rs485_addr, 0f version, **11 emergency_pin_mode** (Host:
stall_mode), 12 dev_uuid, 13 err_detail_data, 14 cut_detail_data, 15 pos_data, 16 spd_data
(`motor.MOTOR_FW_CMD_NAMES`).

**Antwortrahmen** (Builder @0xdd6e): `F7 | addr | len = n+3 | STATUS | cmd | payload[n] | CRC`.
**STATUS-Byte = Bitfeld** (im Request `00`; live bisher immer `00`):

| Bit | Wert | Bedeutung |
|---|---|---|
| 0 | 0x01 | Stall-Flag (obj+0x40 = 0x08 idx 14) |
| 1 | 0x02 | latched Fehlercode ≠ 0 |
| 2 | 0x04 | latched Warncode ≠ 0 |
| 3–7 | – | unbelegt |

Nachbau `decode_motor_status(byte)`; `cfswatch.py` meldet Motor-STATUS ≠ 0 als Warnung mit Klartext.

**0x08 MOTOR_GET – vollständige Variablen-Tabelle** (FW-Sprungtabelle @0x1bce4, idx 0..18;
`motor.VARIABLE_TABLE`, Namen außer 5–8 = eigene Nachbau-Namen nach FW-Deutung):

| idx | Name (Nachbau) | Einheit | FW-Feld / Deutung | Beleg |
|---|---|---|---|---|
| 0 | `const_zero` | – | konstant 0 | FW |
| 1 | `pos_ref` | rad | Positions-Soll (ctrl+0x118 = target_pos + 0x84, begrenzt), single-turn | FW + live |
| 2 | `spd_ref` | rad/s | Drehzahl-Soll (Pos-PI-Ausgang + Vorsteuerung); live 1,633 bei 1,571 Soll | FW + live |
| 3 | `id_ref` | A | d-Strom-Soll; FOC ohne Feldschwächung → live 0 | FW + live |
| 4 | `iq_ref` | A | q-Strom-Soll; live ≈ iq_fdb | FW + live |
| 5 | `pos_fdb` (Host) | rad | LESO-Positionsbeobachter `leso_pos`, single-turn = idx11·2π/3200 | Host + FW + live |
| 6 | `spd_fdb` (Host) | **rad/s** | LESO-Drehzahl `leso_spd` = `spd_fdb_ctrl`; live 1,53–1,57 bei 1,571 Soll | Host + FW + live |
| 7 / 8 | `id_fdb` / `iq_fdb` (Host) | A | d/q-Strom-Ist (ctrl+0x60c/0x610) | Host + FW |
| 9 | `ia` | A | Phasenstrom Ia (ADC-Kanal 0 − Offset); Stillstand 0,00161 = 2 LSB | FW + live |
| 10 | `minus_ib` | A | **−Ib** (ADC-Kanal 1 × −1,0); Stillstand 0,00081 = 1 LSB | FW + live |
| 11 | `pos_counts` | counts | **Schrittpuls-Zähler** (STEP-Eingang, u32), 3200/Umdr.; ×2π/3200 = Soll-Winkel | FW + live |
| 12 | `dir_input` | 0/1 | **DIR-Eingangspegel** (Flanke schaltet Zählrichtung); live −X 0 / +X 1, hält im Stillstand | FW + **live bestätigt** |
| 13 | `en_input` | 0/1 | vermutlich EN-Eingang (1 → Regelung aus) | FW, vermutet |
| 14 | `stall_flag` | 0/1 | Stall-Flag = STATUS bit0 | FW |
| 15 / 16 | – | – | obj->[0xc]+0x24 / +0x2c (u32) | **offen** |
| 17 | `board_detect_temperature_C` | °C | MCU-Temperatursensor (Kalibrierung 0x1FFFF720) | FW |
| 18 | `board_detect_power_supply_V` | V | Versorgung (ADC × Vref × `power_voltage_sample_gain`) | FW |
| > 18 | – | – | FW sendet den veralteten Scratch-Wert (kein Fehler, kein Crash) | FW |

Strom-Skala raw·Vref/4096/(Gain·R); **Gain·R = 1 Ω ist vermutet** (prüfbar über die Flash-Parameter
`driver_board_amp_gain` / `driver_board_R_of_current_sample_ohm`). Eine ud/uq-Spannung gibt es im GET
nicht. Die Host-Typkette (float 1–10/17/18, int 11–16) passt zu den FW-Feldern.
**Host-Crash bleibt:** `MOTOR_GET DATA=0/13` über Creality-G-Code → SHUTDOWN (host-seitig). Deshalb bleibt
`read_variable` bei 1..12; idx 0/13..18 nur über den eigenen Bus-Zugriff `read_variable_raw_fw(idx,
confirm=True)` (FW-statisch lesend, live für 0/13..18 nie gesendet → ohne `confirm=True`
`PermissionError`, nichts gesendet).

**0x0C protection – Byte-Layout BELEGT** (FW + Host-Parser `motor_auto_check_protection` @0x56788):
Antwort auf `[0b]` = **Byte 0–3 Fehler latched (u32 LE), Byte 4–7 Warnung latched (u32 LE)**; Host-Index
i = 8·Byte + Bit = LE-Bitnummer → Fehlerbit n → key781+n (0..13), Warnbit n → key795+n (0..2;
Bit 1 Unterspannung, Bit 2 MCU-Übertemperatur). Latched = roh & Maske (Fehler 0x400, Warnung 0x404).
Sub-Kommando = Payload-Byte 0 (Sprungtabelle @0x1bd30):

| Sub | Wirkung | Antwort | Nachbau |
|---|---|---|---|
| 00 | nichts | – | Konstante |
| 01 / 02 | protect_en = 0 / 1 | – | **nur Konstante** (zustandsändernd) |
| 03 / 04 | protect_report = 0 / 1 | – | **nur Konstante** |
| 05 | löscht roh/latched/Vorzyklus (Backups bleiben); live nie beantwortet | – | **nur Konstante** |
| 06 / 08 | Fehler- / Warn-Backup | 4 B | Konstante |
| 07 / 09 | Fehler / Warnung latched (*) | 4 B | **nur Konstante** |
| 0A | Fehler-Backup + Warn-Backup | 8 B | Konstante |
| 0B | Fehler latched + Warnung latched (*) | 8 B | `check_protection()` (wie Stock) |

(*) Seiteneffekt @0x10616: steht `emergency_pin_mode`/stall_mode auf **2** (Notfall-Ausgang), gibt
die FW nach der Antwort den Notfall-/Stall-Pin frei (quittiert die Leitung, Codes bleiben). Stock sendet
`[0b]` trotzdem (Boot, nach Homing). Nachbau: `check_protection()` → `decode_protection()` (dict
`err_code`, `warning_code`, `errors`, `warnings`, `ok`, `raw`), `check_protection_raw()` → 8 B;
`check_protection_code` = Alias (Host-Parser @0xd6cbc identisch).

**0x13–0x16 – LESEND, ~~ohne Request-Payload~~ Payload egal** (FW liest Payload-Byte 0 nicht; der Host
sendet `[02]`, live 16.10.; Host stellt sie als Webhook-Endpoints bereit, kein G-Code). ~~FW-statisch
belegt, live NICHT geprüft.~~ **FW-statisch belegt UND LIVE bestätigt 16.10.2026** (→ NACHTRAG (5)).

| Cmd | FW-Name | Antwort | Inhalt | Nachbau |
|---|---|---|---|---|
| 0x13 | err_detail_data | 74 B = `<I8BfHfiiffffffffi` (66 B) + 8 B Union | Snapshot zum **letzten Fehlerereignis**: err_code (= 1<<n), dev_uuid, elec_offset, phase_order_invert, elec_offset_err_deg, encoder_fdb, encoder_fdb_last, pos_fdb_encoder, spd_fdb_encoder, spd_fdb_ctrl, id, iq, V, °C, pos_loop_pid_err, step_increment; Union je Fehlertyp (`ERR_CODE_TO_UNION`) | `read_err_detail()` |
| 0x14 | cut_detail_data | 74 B, gleiches Layout | Snapshot beim **Stall-Ereignis** (Messpunkt von MOTOR_CHECK_CUT_POS); Host entpackt 70 B (+1 `f`, ~~Name unbelegt → `extra_f`~~ Host-Name live: `param_peak_overcurrent_A`) | `read_stall_detail()` |
| 0x15 | pos_data | `0A` + 10 × `<ffff` = 161 B | Ring, älteste zuerst: fdb_pos, leso_pos, target_pos, leso_err | `read_pos_samples()` |
| 0x16 | spd_data | `0A` + 10 × `<ffff` = 161 B | eigener Ring: fdb_spd, leso_spd, target_spd, leso_in_u | `read_spd_samples()` |

Snapshots bleiben bis zum nächsten Ereignis (auch über `protection [05]`); die Ringe werden nur bei
ctrl+4 == 1 geschrieben, kein Start/Löschen. Feldnamen = Host-Strings; „Namensreihenfolge =
Byte-Reihenfolge“ ist ~~**vermutet**~~ live = Host-Dekodierung (NACHTRAG (5)). Nachbau: Längen- und
Zählbyte-Prüfung (`ValueError`), `retries=0`, Timeout 1 s (Host-Timeout **unbelegt**), Rückgabe inkl.
`status` (decode_motor_status, über `transact_full`); Host-Namen `err_detail_data`/`cut_detail_data`/
`pos_data_sample`/`spd_data_sample` sind Aliase. ~~Selbsttests nur mit synthetischen Frames
(Platzhalter-UUID).~~ Selbsttests seit 16.10. mit den Live-Frames (UUID-Platzhalter, CRC neu) +
synthetischen Frames für die Union-Dekodierung (live nur err_code 0).

**Einstufung:** lesend/sicher = 0x08 (alle idx), 0x13–0x16, 0x0C 06/08/0A; 0x0C 07/09/0B lesend mit
Seiteneffekt (nur stall_mode 2); zustandsändernd = 0x0C 01–05 und 0x11.

**Noch offen:** idx 15/16; ~~0x13–0x16 live gegenprüfen~~ (erledigt 16.10., NACHTRAG (5)); Setzer von
Fehlerbit 4 (HW-Überstrom) und Warnbit 0 (Kommunikation); Gain·R (Stromskala); Fehlerbit 11 ohne
FW-Beleg; Union-Inhalte bei err_code ≠ 0 (live nur 0); Bedeutung `param_peak_overcurrent_A` (live 0,012).

#### NACHTRAG 16.10.2026 (5): 0x13–0x16 LIVE BESTÄTIGT (Motor X, Klipper-Webhooks)

Quelle: `LIVE-ABFRAGEN-2026-10-16.md` Abschnitt „0x13–0x16 live“ (klippy.log-Auszug 16.10. 17:26:57–58
Log-Uhr = UTC+8; Rohframes nur lokal, da mit echter Motor-UUID). Motor X `0x81`, Stillstand nach `M84`,
Klipper durchgehend `ready`, kein Druck.

**Weg (kein G-Code):** Die Kommandos sind nur als **Klipper-Webhook-Endpunkte** erreichbar (Unix-Socket
von Klipper, z. B. `/tmp/klippy_uds`, JSON-Request `{"id": …, "method": "<endpunkt>", "params": {"num": 1}}`;
Liste per `list_endpoints`):

| Endpunkt | gesendete Frames (in dieser Reihenfolge) | Rückgabe |
|---|---|---|
| `motor_err_detail_data` | `0x13`, danach `0x15` und `0x16` | JSON: err_detail + pos_data_sample + spd_data_sample |
| `motor_cut_detail_data` | `0x14`, danach `MOTOR_GET` idx 5–8 (0x08 = pos/spd/id/iq_fdb) | JSON: cut_detail-Felder (s. Tabelle) |
| `motor_dev_uuid` | (0x12, hier nicht benutzt) | – |
| `motor_param_modifi_data` | **vermutlich schreibend – NICHT angefasst** | – |

`num` = Motor-NUM (1 = X → `0x81`, Host-Tabelle `MOTOR_ADDR_BY_NUM`).

**Requests:** `81 04 00 13 02`, `81 04 00 14 02`, `81 04 00 15 02`, `81 04 00 16 02` – der Host hängt
`[02]` an (die FW ignoriert die Payload; Nachbau sendet trotzdem byte-gleich `DETAIL_REQUEST_PAYLOAD`).
Je Request genau eine Antwort (kein Retry im Auszug); Antwortzeit laut Log-Stempeln 0,014–0,174 s
(inkl. Logging; Host-Timeout unbelegt).

**Antworten:** STATUS `00` (kein Stall/Fehler/Warnung latched). 0x13/0x14: len `0x4d` → **74 B** Daten;
0x15/0x16: len `0xa4` → **161 B** (Zählbyte `0x0A` + 10 × 16 B `<ffff`). Geräte-CRC (CRC-8/ATM) stimmt.

**Feldtabelle 0x13/0x14** (74 B, `ERR_DETAIL_FORMAT = "<I8BfHfiiffffffffi"` + 8 B Union; Feldnamen = Host-
JSON-Schlüssel, Werte live, Dekodierung des Nachbaus = Host-JSON):

| Byte | Format | Host-Name | 0x13 live | 0x14 live |
|---|---|---|---|---|
| 0–3 | `<I` | `err_code` | 0 | 0 |
| 4–11 | 8 × `B` | `dev_uuid` | `cd ab …` (UUID, nicht dokumentiert) | gleich |
| 12–15 | `<f` | `elec_offset` | 123,58 | 123,58 |
| 16–17 | `<H` | `phase_order_invert` | 0 | 0 |
| 18–21 | `<f` | `elec_offset_err_deg` | 3,867 | 3,867 |
| 22–25 | `<i` | `encoder_fdb` | 0 | 4421 |
| 26–29 | `<i` | `encoder_fdb_last` | 0 | 4421 |
| 30–33 | `<f` | `pos_fdb_encoder` | 0 | 0,8477 |
| 34–37 | `<f` | `spd_fdb_encoder` | 0 | 1,0957 |
| 38–41 | `<f` | `spd_fdb_ctrl` | 0 | 1,5318 |
| 42–45 | `<f` | `id` | 0 | 0,00887 |
| 46–49 | `<f` | `iq` | 0 | 0,5157 |
| 50–53 | `<f` | `board_detect_power_supply_V` | 0 | 24,365 |
| 54–57 | `<f` | `board_detect_temperature_C` | 0 | 50,32 |
| 58–61 | `<f` | `pos_loop_pid_err` | 0 | 0,01211 |
| 62–65 | `<i` | `step_increment` | 0 | 0 |
| 66–69 | `<f` (nur 0x14, Host 70 B) | `param_peak_overcurrent_A` | – (Host entpackt 66 B) | 0,012 |
| 66–73 | Union (je err_code) | – | 8 × 00 | `a6 9b 44 3c` + 4 × 00 |

Lesart: 0x13 = kein Fehlerereignis seit Boot → nur die statischen Felder (UUID, elec_offset,
phase_order_invert, elec_offset_err_deg) sind gefüllt (deckt sich mit „Handler kopiert statische Felder
nach“, NACHTRAG (4)). 0x14 = Snapshot des letzten Stall-Ereignisses (vermutlich Homing davor) mit
plausiblen Bordwerten (24,4 V, 50 °C). `param_peak_overcurrent_A` = 0,012 trotz err_code 0 – Host-Name
übernommen, Bedeutung/Einheit offen (für einen Spitzenstrom-Grenzwert unplausibel klein).

**0x15/0x16** (10 Proben, Stillstand → alle 10 gleich): pos `fdb_pos` 2,15985 / `leso_pos` 2,15985 /
`target_pos` 2,159847 / `leso_err` 0 (Feld 3 und 4 damit eindeutig zugeordnet, Feld 1/2 identisch);
spd `fdb_spd` 0 / `leso_spd` −0,00117 / `target_spd` −0,00136 / `leso_in_u` −0,00163 (Host: „≈ 0“; Reihen-
folge konsistent, aber nicht unterscheidbar).

**Nachbau:** `motor.py` sendet `[02]` (`DETAIL_REQUEST_PAYLOAD`), `CUT_DETAIL_EXTRA_FIELD =
"param_peak_overcurrent_A"` (vorher `extra_f`), Docstrings „LIVE bestätigt 16.10.“, Selbsttest 7e mit den
Live-Frames (UUID-Bytes 3–8 → `00`, CRC neu berechnet: 0x13 `a2`, 0x14 `c0`; 0x15/0x16 Original-CRC `6f`/`1e`)
gegen alle Host-JSON-Werte. motor 208 ok.

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

## 6. Live-Gegenprobe CFS (LIVE BELEGT 15.10.2026, klippy.log 05.–16.10.)

Quelle: `live-logs-2026-10-15/klippy.log*` (6 Dateien, Drucker-Zugriff nur lesend; lokal,
**nicht fürs Repo**). Zeilenangaben `05:`/`12:`/`13:`/`14:` = `klippy.log.2026-10-<TT>`,
`cur:` = `klippy.log`. 58 567 TX, 36 626 RX, 21 684 Timeouts (fast alle 0xa1-Discovery
by design), 253 × „do not get response“, 27 × „Discard bytes“. Alle 36 626 RX-Frames
CRC-8/ATM über `len..data` gültig; nie Status 0x01/0x02 von der Box (Host-CRC/Länge stets
korrekt). Datenschutz: SN/UUID/Vendor hier nur gekürzt.

### 6.1 Logformat (wichtig zum Auswerten)

- **Requests** stehen nur als `reactor:_check_timers … retries = N, cmd = <addr len status
  fun data…>` – **ohne F7, ohne CRC** (CRC selbst berechnen, z.B. `cfsseq.requests_from_log_cmds`).
  Eine solche Zeile = **eine Sendung**. `data_send:` ist die Anforderung aus box_wrapper vor der Queue.
- **Antworten:** `Serial_485: got {'#msgid': <len>, '#msg': b'…'}` (Rohframe, Empfangsthread)
  und `cmd_485_send_data_with_response params: 0xf7 …` (zugestellte Antwort, Duplikat) –
  `params:` ist also **immer RX** (frühere Fehlinterpretationen als Request, s. §2).
- Fehlschlag je Versuch: `do not get response, retries = N, cmd = b'…'`; endgültig:
  `cmd_485_send_data_with_response timeout` + `key831` „serial_485 communication timeout“.
- **Log-Uhr = UTC+8** (letzter Eintrag 16.10. 01:27:39 ↔ Datei-mtime 15.10. 19:27 MESZ, +6 h);
  Boots beginnen mit `2020-01-01 08:00` (keine RTC).
- **Box-FW-Wechsel 113 → 153 am 12.10.2026** (`12:7053` „version: 1.1.3“, `12:12485`
  „version: 1.5.3“, dazwischen Bootloader-Frames von 0xFE, `12:6973`). Alles vor 12.10.
  00:23 Log-Uhr (inkl. ganz 05.10.) ist **FW 113**, nicht R153.

### 6.2 Retry-Semantik – `retries = 4` ⇒ 5 Sendungen

Beleg `05:63472–63521` (0x0e [01]): TX `retries = 4` → „do not get response, retries = 4“ →
TX `retries = 3` … TX `retries = 0` (63518) → timeout + key831 (63521); Abstand 2,00–2,12 s,
gesamt ≈ 10 s. 47 weitere vollständige Ketten 4→0; Antworten beim 2.–5. Versuch kommen vor
(`05:70721`, `05:67122`). **Nie mehr als 5 Sendungen.** Der Zähler zählt herunter, die Zeile
steht vor jeder Sendung ⇒ `serial485.py` (1 + 4) bestätigt.

Startwert je fun (Box 0x01; Nachbau `box.FUNC_RETRIES`/`func_retries`):

| fun | Host-Name | retries | n (Start) | Timeout (.so-Konstante) / Live |
|---|---|---|---|---|
| 0x02 | GET_RFID | 4 | 18 | 2 s |
| 0x03 | GET_REMAIN_LEN | 4 | 844 | 2 s |
| 0x04 | SET_BOX_MODE | 4 | 154 | 2 s (Retry-Abstand 2,00–2,06 s) |
| 0x05 | GET_BUFFER_STATE | 4 | 16 | 2 s |
| 0x08 | GET_FILAMENT_SENSOR_STATE | 4 | 96 | 2 s |
| **0x0a** | GET_BOX_STATE | **0** | 10 560 | Poll alle ~4 s, Antwort 3 ms…0,39 s |
| 0x0d | SET_PRE_LOADING | 4 | 55 | 300 s (op 2: Antwort nach 31–37 s, 1 Sendung) |
| 0x0e | MEASURING_WHEEL | 4 | 5 415 | 2 s |
| 0x0f | TIGHTEN_UP_ENABLE | 4 | 35 | 2 s |
| 0x10 | EXTRUDE_PROCESS | 4 | 191 | 15 s (mode 0: ≤ 7,25 s) |
| 0x11 | RETRUDE_PROCESS | 4 | 90 | 150 s (p1: ≤ 23,0 s) |
| 0x14 | GET_VERSION_SN | 4 | 78 | 2 s |
| 0x15 | GET_HARDWARE_STATUS | 4 | 10 | 2 s |

**`retries = 0` (eine Sendung, kein Retransmit):** 0x0a-Poll, Auto-Addr 0xa0/0xa1/0xa2/0xa3
(z.B. `12:2106`), alle Motor-Kommandos 0x81–0x85, Broadcasts 0xff/0x0b, 0xff/0x0e. Belt 0x21/0x22: 4.
Nachbau-Abweichung (bewusst): `box.communication_set_pre_loading` op 2/3 mit retries=0 und
Ghidra-Timeout ≥ 380 s statt 4 × 300 s (ein Retransmit würde den Force-Preload neu starten).

Sonderfall: 0x0d **[00 02]** aus dem G-Code-Pfad („filament_sensor false“) – 5 Sendungen in
≈ 16–23 ms, je Versuch „get response or timeout“ nach 2–3 ms (`13:69366–69385`,
`13:71509–71529`, `13:130620–130635`), **nie beantwortet** → erzeugt key831-Meldungen
ohne echten Box-Ausfall (0x0a-Polls davor/danach beantwortet). Ursache offen (Host-Timeout
kürzer als Box-Latenz 3–5 ms, oder Busy/Kollision).

### 6.3 Host-Namen (aus `cmd: …`-Zeilen)

02 GET_RFID, 03 GET_REMAIN_LEN, 04 SET_BOX_MODE, 05 GET_BUFFER_STATE,
**08 GET_FILAMENT_SENSOR_STATE** (bisher bei uns GET_HARDWARE_STATUS), 0a GET_BOX_STATE,
0d SET_PRE_LOADING, 0e MEASURING_WHEEL, **0f TIGHTEN_UP_ENABLE** (bei uns PRELOAD_INHIBIT/
MOTOR_ACTION), 10 EXTRUDE_PROCESS, 11 RETRUDE_PROCESS, 14 GET_VERSION_SN,
**15 GET_HARDWARE_STATUS**; Status `OK[0x0]`, **`UPDATE_STATE[0x30]`**, **`SPEED_ERR[0x51]`**.
Im Nachbau umbenannt, alte Namen als Aliase (`box.py`, `cfsseq.py`).
An die Box gesendet werden nur 02/03/04/05/08/0a/0d/0e/0f/10/11/14/15/a2/a3 – alle in der
Ghidra-Tabelle. **0x06/0x07/0x09 nie an die Box** (0x06 nur an Motoren) → FW-Bug 0x06 nicht
prüfbar. **Adressen 0xEA/0xEB kommen nie vor**; 0xFE nur bei a1/a0 und Bootloader-Antworten.

### 6.4 0x0d SET_PRE_LOADING live (55 Requests)

| Request | n | Antwort | Bewertung |
|---|---|---|---|
| `[0f 01]` (enable) | 22 (+9 Sendungen) | st=0 nach 3–10 ms (`13:69418/69421`, `12:65812`) | bestätigt: op 0/1 sofort, Host sendet Maske 0x0F |
| `[0f 00]` (disable) | 3 | st=0 sofort | bestätigt |
| `[01 02]` (Force A) | 4 | st=0 nach **31,0 / 37,0 / 35,0 / 31,0 s**, 1 Sendung (`13:90197→90211`, `13:134512→134526`, `13:135603`, `13:137449`); Host pollt solange nicht | bestätigt (≈ 30–37 s je Slot, < 3 min) |
| `[00 02]` (Force alle) | 26 Sendungen in Bursts | leerer Hub: st=0 nach 5 ms (`13:48753/48755`); Hub belegt: Box ≈ 11 s stumm (0x04 5× unbeantwortet `13:130637–130652`), danach 0x0a Flags 0x90 (`13:130760`), Hub bleibt belegt; Bursts sonst unbeantwortet | Blockade bestätigt; Burst-Antwort ~~offen~~ **BEFUND2 §5: Box verweigert nicht; Ursache Host-seitig vermutet** (s. §6.8) |
| op 3 (`[m 03]`) | 0 | – | **offen** (nie live) |

### 6.5 0x10 / 0x11 / 0x04 / 0x08 live

- **0x10:** Modi live nur 0, 4, 5, 6, 7 (p=3); nie 1–3. mode 0: `00` nach 4,3–5,4 s;
  Fehler st=05 mit Daten `04`=ERROR nach 7,24 s (`12:84996`, key835). mode 4 sofort (0–10 ms).
  mode 5: 104 × Float BE (≈ −1,1…−3,0 m); st=0c genau einmal je mode-4-Lauf, Odometer dabei
  unverändert (`12:80299/80319`). mode 6/7: 20–101 ms / 44–125 ms. Ghidra-Tabelle bestätigt.
  Extrude-Request-Folge identisch mit `cfsseq.build_extrude_sequence` (Selbsttest mit
  `13:` 03:01, Slot B).
- **0x11:** p0 64× (Antwort 0,24–0,72 s), p1 26× (0,8–21,5 s); 0x14 nach 23,0 s (`12:82188`,
  key849), **0x19 nach 11,6/12,2 s** (`13:95746`, `13:95885`, key865); Maske nie 0.
  **Retrude-Folge live:** `04[00 01] 08[00] 11[m 00]`×n `11[m 01]` mit n meist 4 (16×; ~2,5 s
  Abstand, dazwischen 0x0e-/0x03-Abfragen), Kurzform `04[00 01] 11[m 01]` (10×, nach
  Extrude-Abbruch/Entladen). **`08[01]` zwischen p0 und p1: 0 von 23** – ~~frühere
  cfsseq-Sequenz mit 08[01]~~ WIDERLEGT, korrigiert.
- **0x04:** `[m 00]` ⇒ 0x0a PRINT; `[00 01]` ⇒ IDLE, **auch aus ERROR** (`13:96151→96184`) –
  der Host löscht Fehler so (0x10 mode 3 nie gesendet). Status 3 nie gesehen.
- **0x08:** `[00]` → `07` (A,B,C Filament; D leer = remain D 00), `[01]` → Hub-Maske
  (deckungsgleich mit 0x0a Byte 4, `13:72763`). **0x02** `[00]` → st=0 ohne Daten (`13:48567`).
- **0x03:** `ff ff 1d 00`; nach Herausziehen A `00 ff 1d 00` (`13:132971`) – 0xFF = Filament
  ohne RFID, 0x00 = kein Filament bestätigt. **0x15:** `10 20 30 40 50 60 00…` (`13:80094`).

### 6.6 0x0a GET_BOX_STATE live

- R153: immer 6 Byte; Zustände 0, 1, 2, 4 beobachtet (RELOAD 3/TEST 5 nie); in ERROR ist der
  Status = S[3] (live 05/0c/14/19/51). Status-Verteilung: 00 9 826, 05 11, 0c 116, 14 56,
  19 8, 30 63, **51 452**.
- Byte 2 Flags: `01/02/04/08` einmalig nach 0x11 p1 bzw. Preload (= Slotmaske);
  `0x90` (`13:130760`), `0x91` bei Fehler 0x19 (`13:95774`) – R[3]-Deutung „Motorblock“ gestützt.
- Byte 4 = Slot am Hub (1-hot), deckungsgleich mit 0x08 [01].
- **Byte 5: immer 0** (8 268 Frames, auch mit RFID-Spule in C, remain 29 %) → Deutung
  „gültige RFID-Daten“ **WIDERSPRICHT/unbelegt**. → **BEFUND2 §2:** Typ-7-Tag-Sperrbit; live
  steckte nie ein gesperrter Typ-7-Tag.
- **Byte 0/1:** variieren langsam und gegenläufig (b0 0x11–0x1c, b1 0x2d–0x39; z.B. `13:7924`
  16/2f … `14:19447` 14/37) → ~~Vermutung~~ **BELEGT (BEFUND2 §1): Temperatur °C / rel. Feuchte % (AHT30)**;
  Hygrometer-Abgleich nur noch optional (Genauigkeit, keine Semantikfrage).
- **FW 113:** nur 4 Datenbytes `b0 b1 ?? Zustand` mit Status 0 (`05:4452` `17 34 00 00`, `18 33 00 02`).
- **0x30 UPDATE_STATE** (nur 12./13.10., 63×): **2 = Herausziehen** (remain danach 00,
  `13:132699→132971`), **1 = Einstecken/Preload-Start** (`13:133324`), **3 = Preload fertig**
  (danach GET_RFID, `12:42031→42107`) – Deutung, gestützt (Nachbau `box.EVENT_*`).
- **0x51 SPEED_ERR:** S[3] in ERROR, key846 „empty printing, box speed is smaller than
  extruder“ (`12:15733/15748`, 452× am 12.10. 00:28–01:11 Log-Uhr, Hub = Slot A).
- Status 0x50 (PRINT-Sensorprüfung): nie gesehen – ~~offen~~ Semantik per BEFUND2 §4 geklärt (Runout im Druck-Slot), live weiter nie.

### 6.7 Offen nach der Live-Gegenprobe

0x0d op 3; ~~Status 0x50~~; Zustände RELOAD/TEST im 0x0a; ~~warum die [00 02]-Bursts nie beantwortet
werden~~ (BEFUND2 §5: Host-seitig vermutet); 0x06/0x07/0x09 an die Box; ~~Byte 0/1 = Temp/Feuchte
gegenprüfen~~ (belegt); ~~Bedeutung 0x15-Nibbles~~ (belegt). Neu offen: Host-Burst-Timing im
`.so` (2–3 ms-Timeout?), Treiber-RX-Puffergröße der Box, 0x20/0x22-Quellen.

### 6.8 BEFUND2 16.10.2026 – Ghidra-Nachanalyse R153 (Details `GHIDRA-CFS-BEFUND2-2026-10-16.md`)

Belegstärke wie oben (B / B+L / V). Takt: 1 RT-Tick = 1 ms (`rt_tick_from_millisecond`). Die
`.data`-Sektion ist Keil-komprimiert (Entpacker `0x0800316c`) – erst entpackt waren Auto-Addr-
und Pin-Tabelle lesbar.

**0x0a – vollständiges Layout (R153, 6 Byte):**

| Byte | Inhalt | Beleg |
|---|---|---|
| 0 | Temperatur °C, int8, abgeschnitten (AHT30 @I2C 0x38, ~1,2 s) | B+L |
| 1 | rel. Feuchte %, uint8 0..100, abgeschnitten | B+L |
| 2 | Motorblock-Flags: Bit 7 = Block > 10 s (R[3], einmalig), Bit 4 Hauptmotor, Bit 0–3 Feeder A–D (sonst R[5] sofort) | B (+L 0x90/0x91) |
| 3 | Box-Zustand S[2] | B+L |
| 4 | Hub-Sensor-Maske (Sensor B, PE10..PE7) | B+L |
| 5 | Typ-7-Tag-Sperrbit (Einlauf-Flag && Tag geladen && Typ 7 && Feld+0x10 == 0) | B (Zweck V) |

AHT-Fehler: Messrückgabe ignoriert, letzte Werte bleiben stehen; schlug die Init fehl, bleiben
0/0 (dann 0x15 Byte 1 = 0x21). Debug-Override per finsh `temp_humi`. 0x52 liefert dasselbe Paar.

**0x15 GET_HARDWARE_STATUS:** Byte i = `((i+1)<<4) | Flag[i]`, Flag 0 = OK:

| Byte | Komponente | Flag ≠ 0 |
|---|---|---|
| 0 (0x1_) | EEPROM | 1 fehlt, 2 Verify @0x7f0 fehlgeschlagen |
| 1 (0x2_) | AHT30 | 1 nicht gefunden / 3× Lesefehler |
| 2 (0x3_) | Odometer MT6826 | 1 Timer-/Kommunikationsfehler |
| 3 (0x4_) | Buffer | 1 PD8 **und** PD9 aktiv (unplausibel) |
| 4/5 (0x5_/0x6_) | RFID-Leser 0/1 | 1 Fehler (Leser 1: Flag schon beim ersten Fehlversuch) |

Live `10 20 30 40 50 60 00…` = alles OK. Nachbau: `box.parse_get_hardware_status` → `components`/`all_ok`.

**Adressen-Semantik (App, RX-Zustand 1):** angenommen werden `{eigene, 0xEA, 0xEB}`; 0xFE/0xFF
nur, wenn die eigene Adresse 0xFE ist (Box ohne Adresse). 0xEA: normale Kommandos werden mit
eigener Adresse beantwortet (nur CRC-Fehler/unbekannte fun/0x50 still). 0xEB: nur 0x50
(Antwort mit Absender 0xEB) und 0x51 (Debug-Schalter, keine Antwort). Für Abfragen bei mehreren
Boxen ungeeignet (Kollision); live nie genutzt. `serial485.BROADCAST_ADDRS` enthält 0xEA/0xEB/
0xFC–0xFF → Absender der Antwort „beliebig“.

**Auto-Addr – Box-Seite (Tabelle @0x200000c4, B):**

| fun | Bedingung | Antwort |
|---|---|---|
| 0xA1 get_uniid | eigene Adr = 0xFE, Daten `[FE FE]` | 1×, an 0xFE, **Zufallsverzögerung 0–1000 ms** |
| 0xA0 set_addr | eigene Adr = 0xFE, `[neue_addr]+UID12` passt | Adresse übernehmen, **3×** Antwort von der neuen Adresse |
| 0xA2 online_check / 0xA3 get_addr_table | eigene Adresse | 1×, identisch |

Antwortdaten immer `[01, 00] + UID12` (dev_type 1, mode 0 = APP). Eine adressierte Box ignoriert
A0/A1 an 0xFE. Abgleich mit `nachbau/auto_addr.py`: kompatibel – A0-Duplikate werden
toleriert (Eintrag schon `acked`, `function_code_cb` ignoriert sie; im Nachbau-Transport verwirft
`reset_input_buffer` bzw. der fun-Filter sie), eine adressierte Box wird nach Host-Neustart über
0xA3 wieder ONLINE (Selbsttest 13, `FwSerial`). Rest-Risiko: A1-Antworten mit Verzögerung nahe
1000 ms können das 1-s-Timeout knapp verfehlen → nächster Zyklus holt sie (kein Fehler).

**Kanal-A-Schwellen (BEFUND2 §8):**

| Ablauf | Schwelle | Code |
|---|---|---|
| 0x10 mode 0 feed_top_to_hub | Hub-Sensor nicht in 7 s (700×10 ms, 10× stabil) | 0x05 |
| 0x10 mode 4 | 25×20 ms < 0,01 mm (500 ms) | 0x0c |
| 0x10 mode 4 | > 3000 mm | 0x0b |
| 0x10 mode 4 | 1250×20 ms = 25 s | 0x0a |
| 0x11 p=1 vorab | Einlauf-Sensor leer | 0x17 |
| 0x11 p=1 | Odometer steht + Motorblock > 10 s | 0x19 |
| 0x11 p=1 | 2000×20 ms = 40 s | 0x1a |
| 0x11 p=1 Stufe 2 | Hub nach 7 s belegt | 0x14 |
| PRINT print_check | ~10 s ohne „buffer full“: ≥ 40 mm/≥ 3× leer → 0x51; < 5 mm → 0x52 | 0x51/0x52 |

Mode 4 prüft nur das **gemeinsame** Odometer (MT6826, eines für alle Slots), keinen Sensor.
**Folgefehler bei dauerhaft aktivem Hub-Sensor A:** jedes 0x0d op 2/3 in IDLE und jedes 0x11
Maske 0 zieht A zuerst zurück (`FUN_08004cd0(4,1)`) → ≥10 s Motorblock, STATE_ERROR, Abbruch →
**blockiert B/C/D**. 0x10 mode 0 für A sieht den Hub sofort „erreicht“. Im Druck meldet der
Hub-Sensor nur 0x50, wenn er frei wird.

**Bursts `0x0d [00 02]` (BEFUND2 §5):** Der RX-Thread ruft den Dispatcher **synchron** auf – ein
blockierender Handler hält den Empfang an, Folge-Bytes landen im Treiberpuffer; **keine
Busy-Antwort, keine Duplikaterkennung** (identische Wiederholungen werden erneut ausgeführt).
Jeder vollständig empfangene 0x0d-Frame wird beantwortet. Live kamen Burst-Frames an und wurden
ausgeführt; die Antworten fehlen im Host-Log → Ursache **Host-seitig vermutet** (Burst-Timeout
2–3 ms unter Box-Latenz, Kollision auf dem Halbduplex-Bus, späte Antworten verworfen).

### 6.9 Code-Fixes / Schutzmechanismen im Nachbau (Review 2, 16.10.2026) und cfswatch

| Modul | Fix / Schutz |
|---|---|
| serial485 | Absenderprüfung `expect_addr` (Default `reply_addr_for(ziel)`; Broadcast-Ziele `BROADCAST_ADDRS` = beliebig) – fremde Frames mit gleichem fun (z.B. Box 0x0e vs. Motor) werden übersprungen (M1) |
| serial485 | `transact_full` liefert Status-Byte + Rohframe (M2) |
| serial485 | `MAX_BUS_HOLD_S = 1200` s: timeout × (retries+1) darüber → ValueError. Der Bus-Lock bleibt **bewusst** über die ganze Transaktion (Halbduplex, Original identisch) (M6) |
| box | `CfsStatusError` / `StatusBytes` / `raise_on_status`: Status ≠ 0 wird gemeldet statt verschluckt (M2) |
| box | `FUNC_RETRIES` 0x10/0x11 = 0 (**bewusste Abweichung**, `FUNC_RETRIES_ORIGINAL` = 4): Retransmit würde Mechanik doppelt auslösen – passt zu BEFUND2 §5 (Box hat keine Duplikaterkennung); `communication_extrude`/`communication_retrude` mit langen Timeouts (M3) |
| box | Messrad 0x0e op 0 (Nullen) nur mit `reset=True` (G1); Maske 0 bei 0x0d op 2/3 = 4 Slots (980 s) (G2); `_box_fw_num` versteht Host-Form „1.5.3“ (G3); `_is_full_frame` robust gegen Anhang (G4); DeprecationWarning bei `get_hardware_status(channel)` (sendet 0x08) (G6) |
| cfsseq | `EXTRUDE_ACK`/`RETRUDE_ACK`, `check_one_hot`, einziges `slot_to_mask` (G5/G6) |
| motor | F0-Update-Sender nur mit `confirm=True`; `write_sys_param` für die 166 gesperrten IDs nur mit `allow_locked=True`; float32-Plausibilität |
| motor_mini | `MINI_WRITABLE_IDS` (13 Stock-IDs), `update_sys_params` nur mit `confirm=True` |
| BEFUND2 | `parse_box_state`: `temp_c`/`rh` (belegt; `*_guess` Aliase), `inlet_tag_mask` (Alias `b5`/`rfid_valid_mask`), `block_long`/`block_main_motor`/`block_feeder_mask`; `parse_get_hardware_status`: `components`/`all_ok`; `STATUS_NAMES` 0x05/0x0a/0x0c/0x14/0x17/0x19/0x1a/0x50/0x51/0x52 |

**cfswatch.py** (`nachbau/cfswatch.py`, nur lesend, sendet nie, öffnet keinen Port): parst
klippy.log-Zeilen (stdin/Dateien) zu einer deutschen Zeitleiste – Request → Antwort mit Latenz,
fun-Name, Status-Klartext, 0x0a (Zustand, Hub-Maske, Motorblock-Flags, Temp/Feuchte), 0x15
(Komponenten), Odometer seit Förderstart, Retries/Timeouts, key8xx. SN/UUID/Vendor maskiert
(`--unmask`). Bedienung:

```
ssh root@<drucker> 'tail -F /mnt/UDISK/printer_data/logs/klippy.log' | python3 nachbau/cfswatch.py --local
python3 nachbau/cfswatch.py --slot A --only-errors klippy.log.2026-10-13   # lokale Log-Kopie
python3 nachbau/cfswatch.py --selftest
```

Optionen: `--slot A..D`, `--only-errors`, `--since HH:MM`, `--raw`, `--local` (UTC+8 → Europe/Berlin),
`--all`, `--no-motor`, `--unmask`, `--no-color`.
