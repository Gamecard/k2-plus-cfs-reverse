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
  RFID-Daten (vender `101250000001010010FFFFFF0000000000000000`, remain 32/0/100/0)

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
**Kein Live-0x0d-Request auf dem K2 Plus belegt** – live nur ein leeres ACK
(len 3, nach GET_RFID); Byte-Form `f7 01 03 00 0d 9e` = ref-hi-ACK (CRC 0x9e ✓).
Offen: ob das 1. Byte bei 0x00/0x01 die Maske
ist (ref-hi: 0x0F) oder ignoriert wird (vanilla: 0). Nachbau:
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

`f7 01 2e 00 02 41 3a 31 30 31 32 35 30 30 30 30 30 30 31 30 31 30 30 31 30 46 46 46 46 46 46 30 30 30 30 30 30 30 30 30 30 30 30 30 30 30 30 3b d7`
(Vendor-Bytes anonymisiert, gleiche Länge und Feldstruktur; CRC 0xd7 neu berechnet und verifiziert)

- Func `0x02` = GET_RFID-Antwort, Daten als **ASCII**: `A:<hex>;...`
- `A:` = Slot-Key, dann Vendor-Hex `101250000001010010FFFFFF0000000000000000`, dann `;` –
  VOLLSTÄNDIG (13.10. nachverifiziert: 49 Tokens, len=0x2e → 46+3=49 ✓, CRC=0xd7 ✓
  über dem anonymisierten Frame;
  das `0x2` am Ende ist unpadded `%x`-Format, KEIN Abschneiden).
  Farbe/Material/Restmenge kommen aus separaten Abfragen.
- **Kurzform:** `f7 01 0d 00 02 42 3a 75 6e 6b 6e 6f 77 6e 3b 8e` = `B:unknown;` (len 0x0d, Slot ohne erkannten Tag → ASCII "unknown")
- **Weitere Zustände:** `f7 01 0a 00 02 43 3a 62 75 73 79 3b 54` = `C:busy;` (len 0x0a, Slot dreht/ließt gerade), später `C:unknown;` (Tag noch nicht gelesen)
- Passt exakt zum Query: `vender=101250000001010010FFFFFF0000000000000000`; ~~danach folgen vermutlich color / material_type / remain als weitere `;`-Felder~~
  **KORRIGIERT 15.10.2026:** keine weiteren Felder – Farbe und Material stecken IM
  40-Zeichen-vender (20 Byte als Hex/ASCII). Feldlayout (Quelle fake-name `firmware.md:340-352`
  printf-Feldnamen, `firmware.md:521-523` Beispiel 11+13+16 = 40 Zeichen; Belegstärke
  mittel-stark): `month(1) day(2) year(2) supplier(4) batch(2) · mat_id(6) col(7) ·
  len(4) number(6) reserve(6)`. Unser Live-Vendor zerfällt sauber (Repo: Platzhalter mit gleicher Struktur; echte
  day/year/supplier/batch/len/number entfernt):
  `1|01|25|0000|00 · 101001|0FFFFFF · 0000|000000|000000` → mat_id `101001` (= Hyper PLA,
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
0x08 = GET_HARDWARE_STATUS (Request [Kanal], Toolhead-/Hardware-Sensorstatus)
0x0a = Herzschlag/Poll (6 Datenbytes, Zustand; alle ~4s)
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
       K2-Response 13.10.: `f7 01 19 00 14 ... d4`, Daten ASCII "153"+
       "SN-DEMO-1234567890X" = CFS-App-Version + SN, CRC ok)
0x0f = ~~CTRL_CONNECTION_MOTOR_ACTION (Request [01] Engage / [00] Release)~~
       WIDERLEGT 15.10.2026 (Ghidra, inline im Dispatcher): **Auto-Preload-Sperr-
       Flag** S[8] (0x200037d8): [01] = automatisches Preload gesperrt, [00] =
       freigegeben; KEINE Motor-Aktion. Bytes/Live-Flanke unverändert gültig
       (Host sperrt Auto-Preload während Load). Motor-Kommandos: 0x06/0x07/0x09
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
       BELEGT 15.10.2026 (Ghidra `FUN_08004d90`): p=0 = Feeder+Hauptmotor
       rückwärts bis Buffer-Sensor (≤5 s, sonst 0x13); p=1 = rfid_mgr_fila_back
       + pull_back_hub (0x17/0x19/0x14); sonst Status 1; mask 0 = alle Slots
0xa2 = Auto-Addr/SN-Query (f7 01 11 00 a2 01 00 <12B uuid> <crc>, Poll alle ~2s)
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
- 0xEA = Broadcast ohne Antwort; 0xEB = Antwort mit Absender 0xEB (nur 0x50/0x51 relevant).
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
| 0x15 | – | `FUN_0800f16e` | 16 B: 6 Einträge `((i+1)<<4)|X[i]` (NEU; Bedeutung offen) |
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
| 0xA0–0xA3 | – | Modul `RS485_AUTO_ADDR` | nicht im Dispatcher; Antworten auf Box-eigene Anfragen |
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

**Status-Byte (Antworten):**

| Wert | Bedeutung |
|---|---|
| 0x00 | OK |
| 0x01 | ungültig: len falsch, Maske ungültig, Subcode ungültig, fun unbekannt |
| 0x02 | CRC-Fehler im Request |
| 0x03 | busy / falscher Zustand (z.B. 0x04 in PRELOAD/RELOAD, 0x06/0x07 ≠ IDLE); bei 0x50 „Wert unverändert“ |
| 0x05 | Feeder-Out-Fehler (`cmd_parse_out`) |
| 0x06 | `buffer_fill_timeout` |
| 0x08 | Pretension-Warten erfolglos |
| 0x09 | feed_top_to_hub: Odometer bewegt sich nicht |
| 0x0a | 25-s-Timeout bzw. < 5 mm in 3 s |
| 0x0b | Fördern > 3000 mm |
| 0x0c | Odometer-Stillstand 500 ms |
| 0x0d | Buffer nicht voll |
| 0x0e | feed extruder timeout (0x10 mode 7) |
| 0x13 | Rückzug: Buffer-Sensor-Timeout 5 s |
| 0x14 | pull back hub PE timeout |
| 0x16 | Slot ohne Filament (setzt STATE_ERROR) |
| 0x17 | pull_back_hub: Sensor A nicht aktiv |
| 0x18 | intern „kein Slot belegt“ → auf 0x16 abgebildet |
| 0x19 | pull_back_hub motor block |
| 0x30 | **nur 0x0a**: Preload-Ereignis anstehend (4 B je Slot 1/2/3) (B+L) |
| 0x50 | **nur 0x0a** in STATE_PRINT: Sensorprüfung ≠ 0 (V: Runout) |
| S[3] | nur 0x0a in STATE_ERROR: gespeicherter Fehlercode |

Fehler ∉ {1,3} setzen STATE_ERROR mit S[3]=Code (`FUN_08003e3a`); löschen per 0x10 mode 3.
Weitere Codes nur im LED-Mapping (0x1a, 0x20, 0x22, 0x51–0x53), Quellen offen.

**0x0a Box-State** (`FUN_0800ed26`, Antwort 6 Datenbytes, B+L):

| Byte | Inhalt |
|---|---|
| 0 | S[1] (live 0x13; Quelle offen, = 0x52-Antwort Byte 0) |
| 1 | S[0] (live 0x34) |
| 2 | Flags: `R[3] | 0x80` einmalig wenn R[3]≠0 (V: Motorblock), sonst R[5] (wird zurückgesetzt) |
| 3 | **Box-Zustand** S[2]: IDLE=0, PRELOAD=1, PRINT=2, RELOAD=3, ERROR=4, TEST=5 (≥6 „unknown“) |
| 4 | Bitmaske Sensor B (aktiv = 1) |
| 5 | Bitmaske „Slot hat gültige RFID-Daten“ (V) |

**STATE_CHECK_HUB ist KEIN Box-Zustand**, sondern Zustand 2 der Preload-Statemachine
`FUN_080064b8` (0 forced-Start, 1 idle/Queue, 2 CHECK_HUB, 3 pretension, 4 read rfid +
margin estimation) – taucht in 0x0a nicht auf. Status der 0x0a-Antwort: ERROR → S[3];
PRINT → 0x50 oder 0; Preload-Ereignis → 0x30 mit 4 Byte (dann anderes Layout!); sonst 0.
Live `09 00 0a 13 34 00 00 00 00` = Zustand IDLE, keine Sensoren/RFID-Bits ✓;
`07 30 0a 03 00 00 00` = Ereignis Slot A Wert 3 ✓.

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
