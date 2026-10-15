# K2-Protokoll-Checkliste

Audit und Änderungsbegründungen: `/home/pro/k2-fw-live-1.1.7.0/AUDIT-UND-AENDERUNGEN.md`.

## Erledigt
- [x] OTA-Image 1.1.3.13 entpackt (CPIO + Squashfs) – Dump-Verzeichnis gelöscht (486M),
  Original-Image liegt in `~/Downloads/`, alle Inhalte in Live-Version übernommen
- [x] Live-Artefakte gesichert: `fw/` (1.1.7.0), `config/`, `klippy/extras/` (inkl. `.so`),
  `rootfs-live/` (Subset), `belt_mdl-live.py` (GPLv3, aus Firmware)
  (Hinweis: `sw-description`/uboot/kernel-Parts existierten nur im gelöschten Dump;
  bei Bedarf aus `~/Downloads/`-Image re-extrahierbar)
- [x] Klipper-Configs (`F008/printer.cfg`, `box.cfg`) ausgewertet: live: ttyS2/ttyS3/ttyS5 (Dump-F008: ttyS7/ttyS1/ttyS6)
- [x] Blobs inventarisiert: `klipper_mcu`, `mcu_util*`, `libcrealitycloud.so`, `fw/*`, `*.wrapper.so`
- [x] `PROTOKOLL-485.md` geschrieben (Belt-Frame, CFS-Kommandos, Wrapper-APIs)
- [x] Festgestellt: kein Crypto, kein CAN – UART + RS485, `.so` nur armhf/cpython-39

## Offen – Drucker-Zugang
- [x] SSH aktiv, IP 192.168.1.100 (root, K2Plus-XXXX), Moonraker-API 7125 offen
- [x] `ls /dev/ttyS*` + `/proc/cmdline` ausgewertet
- [x] `klippy.log`-Auswertung laufend (alle 485-Frames werden selbst geloggt)
- [x] `printer-live.cfg` + `box-live.cfg` gesichert in `/home/pro/k2-fw-live-1.1.7.0/live-1.1.7.0-configs/`
- [x] Live-FW 1.1.7.0-Dump nach `/home/pro/k2-fw-live-1.1.7.0/` (`fw/`, `config/`,
  `klippy/extras/belt_mdl.py`, `rootfs-live/`-Subset)

## Offen – Mitschnitt
- [x] `klippy.log` loggt ALLE 485-Frames selbst (`cmd_485_send_data_with_response params:`) – kein Hardware-Mitschnitt nötig
- [x] CRC geknackt: CRC-8/ATM (poly 0x07, init 0x00) über `[len..data]`, head/addr NICHT enthalten
- [x] Belt-Frames live verifiziert (Version/Flash/ADC), UUID `a0 a1 a2 a3 a4 a5 a6 a7 a8 a9 aa ab`
- [x] Box-Func-Codes live (Stand: 0x02 RFID, 0x03 Remain, 0x04 Box-Mode, 0x05 Buffer,
  0x08 HW-Status, 0x0a Box-State/Event, 0x0d SET_PRE_LOADING/RFID-Control (live nur ACK len 3; Aktionen 00/01/02 =
  disable/enable/Force-Read, KORRIGIERT 15.10.2026), 0x0e Messrad-Float,
  0x0f ~~Motor-Action~~ Auto-Preload-Sperre (KORRIGIERT 15.10.2026, Ghidra), 0x10/0x11 Extrude/Retrude, 0x14 Version/SN, 0xa2 Auto-Addr;
  Details in PROTOKOLL-485.md – Zeile 25 oben war ein veralteter Zwischenstand)
- [x] Falsche-Syntax-Falle dokumentiert: `NUM=A` → Shutdown, `NUM=0..3` korrekt
- [x] CFS-Antworten eingefangen: Slot 1 mit Spule → `remain A=26%` (`f7 01 07 00 03 1a 00 ff 00 99`), RFID-Tag-Daten via Query (SN `SN-DEMO-1234567890X`, Tag 1.5.3, vender `101250000001010010FFFFFF0000000000000000`, color `0FFFFFF/0ce58f8/026eeee`, material `101001/000001/000001`)
- [x] RFID-Vollantwort dekodiert: `A:101250000001010010FFFFFF0000000000000000;...` (ASCII, `:`/`;`-Separatoren, Key=Slot-Buchstabe)
- [x] RFID-Zustände: `unknown` / `busy` / Hex-Daten; preloading_mask 0x1/0x2 = Slot-Bitmaske
- [x] Extrude-Sequenz T1A live: Func `0x10` State-Machine, `0x0e`=MEASURING_WHEEL, `0x05/0x04/0x0f` Aufräumen
- [x] CRC-Quelle als Python-Source gesichert (lokal, nicht im Repo; Beleg im
  Projekt-Root): POLY=0x07, Timeouts, Broadcast-Addrs, Device-Typen, Adress-Tabellen
- [x] CFS-FW R153 analysiert: 942 Funktionen (alte Basis; Neuanalyse 15.10.2026 @0x08003000: 1063), RT-Thread, MS3791/at8236/AHT30, 4 Feeder-PID, RFID-Manager, finsh-Shell-Kommandos
- [x] Ghidra-Skripte gesichert (`ghidra-scripts/`): SetCfsBase, CfsDetail, ListFuncs, FindCrc6
- [x] RFID-Antwort VOLLSTÄNDIG verifiziert (13.10.): Die `0x2e`-Frames waren NIE abgeschnitten –
  das Log-Format nutzt unpadded `%x` (`0x2` statt `0x02`), was wie Abschneiden aussah.
  Nachgezählt: 49 Tokens, len=0x2e=46 → 46+3=49 ✓, CRC-8/ATM über [len..data] = 0xd7 ✓
  (anonymisierter Frame; Original-CRC 0x02 bezog sich auf die echten Bytes).
  Kompletter Inhalt: `A:101250000001010010FFFFFF0000000000000000;` (43 Datenbytes, endet mit `;`).
  Farbe/Material/Restmenge kommen aus SEPARATEN Abfragen, nicht aus diesem Frame.

## Offen – Pi-Ersatz (VOLL – Belt + PrTouch + CFS müssen laufen)
- [ ] 32-bit Pi OS (armhf) + Python-3.9-Venv aufsetzen (nur so laden die Creality-`.so`)
- [ ] Original-Klippy (`klippy/`-Tree + `.so` aus `rootfs-live/`, venv auf Pi neu
  aufsetzen) auf den Pi kopieren
- [ ] `printer.cfg` voll übernehmen, nur Serials auf `/dev/ttyUSB*` umbiegen (live: ttyS2/ttyS3/ttyS5 (Dump-F008: ttyS7/ttyS1/ttyS6))
- [ ] Pi mit 2x USB-Serial (Mainboard/Toolhead) + 1x USB-RS485 (CFS/Belt), 24V→5V-Buck
- [ ] Testreihe: Belt-CALI/TEST, PrTouch `BED_MESH_CALIBRATE`, CFS Load/Unload mit RFID
- [ ] CFS-Bus-Anschluss (Elektrik, Quelle fake-name + Deutung, s. PROTOKOLL-485.md
  „CFS-Bus physisch“): A/B-Polarität am Gerät MESSEN (Ruhe: A hoch, B tief – Benennung in
  den Quellen widersprüchlich); USB-RS485 mit kräftigem Treiber + Auto-DE (z.B. FTDI +
  MAX3485/SP485; CH340+MAX485 evtl. zu schwach bei mehreren Boxen – Vermutung wegen
  300-Ω-Bias je Box); GND mitführen; zunächst OHNE 120 Ω (in der Box unbestückt);
  24 V aus dem Netzteil; Buffer-Kabelbaum (2 Open-Collector-Leitungen) unverändert
  durchführen – läuft nicht über RS485; Steckertyp am Gerät verifizieren (extern
  „guesswork“, Micro-Fit 3.0 vermutet). Transparent-Pfad s. Review-Punkt unten.

## Offen – Nachbau VOLLUMFANG (alle Funktionen; Module in `/home/pro/k2-fw-live-1.1.7.0/nachbau/`)
- [x] `serial485.py`: Frame-Bau, CRC-8/ATM, Retry, Timeouts – ~~Selbsttest 11/11 Live-Frames ok~~
  Selbsttest alle grün (~~Stand 15.10.2026: 70/70~~ **Stand 15.10.2026 nach Review: 81/81**
  inkl. ref-hi-Kreuzvalidierung, 16 CRC-Vektoren + 12 Nachrichten)
- [x] `serial485.py` vollständig (Subagent, verifiziert): Session (`connect_uart`,
  `_start_session`, `_bg_thread`), Sende-Queue (FIFO, Lock), Response-Registry,
  Stats, Disconnect/Shutdown, `raw_send`/`raw_send_wait_ack`/`get_response`,
  `Serial_485_Wrapper` (`cmd_send_data_with_response`, Queue-Draining);
  Altsignaturen unverändert, alle Regressionstests grün
- [x] `boxmini.py`: get_rfid, has_filament, remain percent – Selbsttest ~~7/7~~ **15/15 ok**
  (15.10.2026; `has_filament` → True bei remain>0, sonst None – nie False)
- [x] `box.py` (Subagent, verifiziert): ParseData-vollständig (rfid/remain/wheel/
  box-state/version-sn/buffer/hw-status, ~~50/50~~ ~~68/68~~ **101 Checks** Stand 15.10.2026 nach Ghidra-Einarbeitung), BoxState-Tn-Modell,
  BoxAction-Step-Builder; Unbelegtes (Dryer, force-Flag) als TODO markiert
- [ ] `filament_rack.py` vollständig: FilamentRackWrapper (send_data, blow, beep,
  check_rfid_valid, Material-Temps/Speeds, Heating-Waits) – braucht Rack-Hardware
- [x] `motor_mini.py`: ~~read_encoder (0x06)~~, set_mode (0x11), query_version (0xf0/0x00) –
  Selbsttest ok ~~(dabei Doku-Fehler repariert: Big-Endian 38467→51266)~~
  **WIDERLEGT 15.10.2026:** 0x06 ist kein Encoder-Lesen, sondern vermutlich
  Sys-Param-Schreiben (Payload = LE-Float 300.0/100.0 = MOTOR_SYS_PARAM ID=70);
  `read_encoder` gesperrt (NotImplementedError, sendet nichts), Float-Dekoder +
  Retry-Regel (retries=2 = 3 Versuche wie mcu_util_485) ergänzt
- [x] `motor.py` (Subagent, verifiziert): belegte Ops + Update-Sequenz-Builder
  (~~40/40~~ **65/65 Checks** Stand 15.10.2026); unbelegte Methoden als NotImplementedError-Stubs mit Beleg-Lücken
  (Kalibrierung, Stall-Modes, Protection, Sys-/Flash-Params, Pin-IO, motor_version normal)
  – REVIEW 15.10.2026: 0x06-Korrektur wie motor_mini (read_encoder gesperrt, Sys-Param-
  Schreib-Builder nur TODO, ID-Position offen); Update-Sequenz um Abschluss Sub 0x02
  (update_end, 500 ms) ergänzt, Erase nur mit erase=True, sector_size 1..127,
  leere FW → ValueError; set_mode Pflichtparameter 0..255
  – `motor_mini.py` Selbsttest **20/20 ok** (15.10.2026)
- [x] `cfsseq.py`: Extrude/Retrude als prüfbare Abläufe – Selbsttest ~~6/6~~ ~~17/17~~ **21/21 ok**
  (15.10.2026; Builder nehmen 1-hot-Maske statt Index, Preload-Aktionen 00/01/02/03,
  Ghidra-Benennung EXTRUDE_MODE_*/RETRUDE_P_*, Sequenzbytes unverändert)
- [x] CFS-Stufen-Semantik vollständig: 0x10/0x11-Datenbytes je Stufe belegen
  ~~(weitere Extrudes mitschneiden + mit Klippy-Stage-Logs korrelieren)~~
  **ERLEDIGT 15.10.2026 (Ghidra):** 0x10 = 8 Modi 0..7 (`FUN_080045c6`), 0x11 p 0/1
  (`FUN_08004d90`); Tabelle PROTOKOLL-485.md „Func-Tabelle CFS-App R153“, Konstanten
  `box.EXTRUDE_MODE_*`/`RETRUDE_P_*`. Live-Mitschnitt weiterer Extrudes bleibt als
  Gegenprobe sinnvoll (insb. Antwort mode 0 = Status + 1 B Zustand, bisher nicht im Log)
- [ ] Kanal-A-Diagnose (13.10. spät): Slot A zieht seit Klemm-Episode (key865) nicht mehr ein,
  kein Insert-Event im Log; Gegenbeweis RFID-Theorie: Insert ist mechanisch, B lief ohne Tag.
  PRÄZISIERT 15.10.2026 (fake-name PCBs.md): Insert-Erkennung ist eine **optische
  Gabellichtschranke** (Filament-Sensor-PCB, U1-U4), evtl. mit Fahne. Reihenfolge:
  (1) Lichtschranke Slot A reinigen/prüfen (Staub/Abrieb nach Klemm-Episode),
  (2) Stecker J5/J6 auf Sitz prüfen, (3) Slot-A-LED beim Einlegen beobachten,
  (4) Schwingarm/Einweg-Ratsche Slot A mechanisch prüfen, (5) dann Gegentest mit
  derselben Spule in Slot C – zieht C ein → Kanal A defekt. Optional Logic-Analyzer
  an PE7-PE10 (J6, RC-gefiltert, vermutlich Presence) bzw. PD4-PD7 (J5).
- Abend-Stand 13.10.: Hub hält A (API-Preload), Heizungen aus (M104/M140 S0),
  Drucker ready, CFS connect. API-Kette: Heizen/Cut/Retrude/Error-Clear ok,
  volle Loads nur per Display (s. PROTOKOLL-485.md „API-Kettensteuerung“).
- [x] `auto_addr.py`: Port der 729-zeiligen Klartextquelle (nur lokal vorhanden, nicht im Repo),
  Klipper-unabhängig – Selbsttest ~~9/9~~ **31/31 ok** (15.10.2026, nachgezählt: 31
  `ok`-Zeilen – die Review-Angabe „32/32“ stimmt nicht mit der Ausgabe überein);
  Zustandsmaschine komplett portiert, 2000 Zufallsszenarien Original vs. Port: 0 Abweichungen
- [ ] Optional: k2-vanilla-Pfad (Gamecard-Fork, offene Treiber) für Plus adaptieren –
  Pflichtliste in PROTOKOLL-GESAMT.md 1.9 (Geometrie, Dual-Z, Chamber, Lüfter,
  Thermistoren, Probe-Stack); motor.py-TODOs ggf. aus dessen motor_control.py
  übernehmen (nur mit Live-Gegenprobe je Methode wegen 0x06/0x0c-Formdifferenz)

## Review 15.10.2026 (Agents) – Fixes + offene Punkte

Details je Befund: `AUDIT-UND-AENDERUNGEN.md`, Abschnitt „Review 15.10.2026 (Agents) + Korrekturen“.
Selbsttests 15.10.2026 (ausgeführt in `nachbau/`, `ok`-Zeilen gezählt, alle grün):
serial485 81/81 · box 68 (nach fake-name-Einarbeitung 83, nach Ghidra-Einarbeitung 101) · boxmini 15 · cfsseq 17 (nach Ghidra 21) · motor 65 · motor_mini 20 · auto_addr 31.

- [x] `serial485.py`: Empfangspfad (fragmentierte/>64-B-Frames weitergelesen, Puffer
  komplett abgearbeitet, fremde gültige Frames als Ganzes übersprungen, len außerhalb
  3..255 abgelehnt), Handler-Exceptions geloggt, Dispatch nach Lock-Freigabe,
  `_start_session` leert `_rest_buf`, ref-hi-Kreuzvalidierung im Selbsttest
- [x] `box.py`/`boxmini.py`/`cfsseq.py`: ~~`_mask`~~ → `check_mask` + `slot_to_mask`;
  Preload 0x00=disable/0x01=enable/0x02=Force-Read; `has_filament` True/None;
  Remain-Antwort exakt 4 Bytes
- [x] `motor.py`/`motor_mini.py`: 0x06 = Sys-Param (LE-Float), `read_encoder` gesperrt,
  Update-Sequenz mit Abschluss 0xF0/0x02, Erase optional, Sector-Size 1..127,
  UPDATE_RETRIES=2 (= 3 Versuche)
- [x] `auto_addr.py`: Zustandsmaschine komplett portiert (0xA0 nach Broadcast,
  Reboot-Zweig, Startphase, Broadcast-Skip, Loader-Check 0x0B an 0xFF, 10-s-Druckintervall,
  Store im printer.cfg-Format)
- [x] Doku: Preload-Belegung, 0x0d, Remain-0-Semantik, Offline-Schwelle (`> 3` = 4. Check),
  Masken-Hinweis in API-box_wrapper.md korrigiert
- [ ] Retry-Zählung am klippy.log nachzählen: meint Original `retries = 4` 4 oder 1+4
  Sendungen? (Nachbau: 5 Sendungen)
- [x] 0x0d-Semantik (ERLEDIGT 15.10.2026, Ghidra `FUN_0800f0da`): op 0/1 = disable/enable
  global (Maske ignoriert), op 2 = Forced Preload mit RFID, op 3 = ohne RFID; Status
  0/1/3/0x16 (+ Pull-back-Fehler); Antwort bei op 2/3 erst nach Vorgang (~3 min pro Slot)
- [ ] Preload/Force-Read **live mitschneiden – bleibt sinnvoll als Gegenprobe** zum
  Ghidra-Befund (bisher nur leeres ACK len 3; Request-Bytes des Hosts, Laufzeit bis zur
  Antwort, Status-Byte, Ablauf op 2 vs. 3)
- [x] Status-Byte-Werte belegt (15.10.2026, Ghidra): 0 ok, 1 ungültig, 2 CRC, 3 busy/
  Zustand, Ablauf-Fehler 0x05–0x19, 0x30/0x50 nur bei 0x0a – `box.STATUS_NAMES`
- [ ] Status-Byte (`raw[3]`) der Antworten auswerten (Fehler/Busy) –
  `box._response_data` ignoriert es noch (TODO im Code; Werte jetzt als `box.STATUS_*`
  bereitgestellt, Logik bewusst unverändert)
- [x] **Timeout 0x0d op 2/3** (Bezug Retry-TODO oben): FW antwortet erst nach bis zu ~3 min
  pro Slot – Nachbau-Default 2 s + 4 Retries hätte den Force-Preload bis zu 5× gesendet.
  ERLEDIGT 15.10.2026: `box.communication_set_pre_loading` nutzt bei op 2/3 langen Timeout
  (`preload_force_timeout(mask)`, 180 s + 200 s je Slot) und `retries=0`
- [ ] `serial485.Wrapper._done` wächst unbegrenzt (nie abgeholte Antworten) – Aufräumen/Limit
- [ ] Hintergrund-Thread kann `get_response` Antworten wegschnappen, wenn zwischen
  `raw_send` und `get_response` eine Lücke ist – Registry/Zuordnung klären
- [ ] Motor 0x06: Position der ID 70 im Frame klären – getrennter TX/RX-Mitschnitt
  (Live-Frame `f7 81 07 00 06 00 00 96 43 09` trägt nur den Float)
- [ ] Erase (0xF0/0x06) belegen: nur CFS-Typ 1/10 dekompiliert, für Motoren unbelegt
  (im Nachbau nur mit `erase=True`)
- [ ] Motor 0x08 (laut grant0013 „get feedback“) live prüfen – Kandidat fürs echte
  Encoder-Lesen; `read_encoder` bleibt bis dahin gesperrt
- [ ] Motor 0x0C: Protection-Semantik (grant0013/k2-vanilla) gegen Plus-Frame `00*8` prüfen
- [ ] Motor 0x12 (dev_uuid) und 0x0E (read/set_addr) am Plus mitschneiden
  (Stock-Bootsequenz laut grant0013: `FF 0E 01`, `8x 12 02`, `8x 0E 02`)
- [ ] Transparent-Pfad über Main-MCU (`transparent_send`/`transparent_response`) für
  Pi-Ersatz klären: Motoren am Plus direkt am Bus oder nur via MCU erreichbar?
- [ ] 226-Param-Map (grant0013 `motor_params_map.md`) mit Plus-klippy.log abgleichen
  (flash_param-Enumeration), ID 70 = Kp bestätigen
- [x] Code-Restwiderspruch: `box.get_measuring_wheel`-Docstring nennt noch
  `c5019149 -> -2073,08` (Tippfehler, korrekt c5009149 = −2057,08) – Code-Agent
  (ERLEDIGT: Docstring nennt c5009149 und markiert den Tippfehler als KORREKTUR)

## Offen – aus fake-name/cfs-reverse-engineering (15.10.2026)

Details: PROTOKOLL-485.md §2 („CFS-Firmware“, „CFS-Hardware“, „CFS-Bus physisch“),
PROTOKOLL-GESAMT.md 1.9.
- [x] Ghidra-Neuanalyse cfs0 @0x08003000 / cfs6 @0x08004000 (Basis korrigiert) –
  ERLEDIGT 15.10.2026: `GHIDRA-CFS-BEFUND-2026-10-15.md` (cfs0 1063, cfs6 936 Funktionen)
- [ ] MT6826S-Encoder (SPI PB12-15): misst er das Messrad oder den BLDC? → Verlauf
  0x0e MEASURING_WHEEL gegen Förderweg/BLDC-Laufzeit korrelieren
- [ ] Bootloader-Parser: Bedeutung der akzeptierten Adresse 0xF0 klären (App akzeptiert
  dagegen nur eigene/0xEA/0xEB – Unterschied belegt, s. PROTOKOLL-485.md)
- [ ] Längengrenze Bootloader len ≤ 0xFC vs. `serial485`-Prüfung bis 255 – nur notiert,
  Code NICHT geändert (App-Grenze unbekannt)
- [x] Hypothese 0x10 `[mask 07 03]` – **BESTÄTIGT 15.10.2026 (Ghidra):** mode 7 mit
  p = 3 mm = Extruder-Check (Odometer muss in 300 ms > p mm laufen, sonst 0x0e)
- [x] 0x0a-Zustandsbyte (ERLEDIGT 15.10.2026, Ghidra): Byte 3 = IDLE 0, PRELOAD 1,
  PRINT 2, RELOAD 3, ERROR 4, TEST 5; CHECK_HUB = Preload-Statemachine, kein Box-Zustand
  (`box.BOX_STATE_*`, `box.parse_box_state`)

## Offen – aus Ghidra-Neuanalyse CFS-FW (15.10.2026)

Details: `GHIDRA-CFS-BEFUND-2026-10-15.md` §9/§10.
- [ ] Quelle von 0x0a Byte 0/1 (S[1]/S[0], live 0x13 0x34; = 0x52-Antwort)
- [ ] R[3]-Quelle (0x0a Byte 2, Flag 0x80 – Motorblock?)
- [ ] Bedeutung Config #6 (0x0c lesen / 0x50 schreiben + Reset) – NICHT senden (0x50 resettet)
- [ ] Inhalt 0x15 (6 Einträge `((i+1)<<4)|X[i]`)
- [ ] Init der Auto-Addr-Tabelle (0xA0–0xA3 in der App)
- [ ] Physische Zuordnung Sensor A/B (Pin-Tabelle @0x20000218; Deutung A = Einlauf, B = Hub)
- [ ] cfs6-Handler 0x16–0x19/0x57 (Trockner?) im Detail
- [ ] Weitere Fehlercodes nur im LED-Mapping (0x1a, 0x20, 0x22, 0x51–0x53) – Quellen
- [ ] 0x0d op 2 mit Mehrfachmaske: Laufzeit summiert sich je Slot (bis > 12 min bei 4 Slots)
  – live prüfen, ob der Host (box_wrapper) dafür eigene Timeouts hat (.so: SET_PRE_LOADING=300 s)
- [ ] 0x06 Feeder-Kommando: FW-Bug (Maske aus r7) → am Bus praktisch immer Status 1; nicht
  im Nachbau verwenden, bei neuer CFS-FW-Version erneut prüfen
- [x] RFID-vender-Feldlayout (40 Zeichen) → `box.split_rfid_vendor()` (Selbsttest)

## Offen – Tiefenanalyse (mit Ghidra + REA)
- [x] Ghidra 12.1.4 + JDK installiert (/opt/ghidra), REA 6.4.0 global, MCP für OpenCode registriert
- [x] `rea analyze mcu_util` läuft (116 Prozeduren, ARM-ELF)
- [x] Update-Protokoll dekompiliert: State-Machine + Commands `0x04FB/0x05FA/0x00FF/0x03FC/0x01FE/0x02FD`
- [x] Checksumme geknackt: 1er-Komplement-Summe über Datenbytes (`FUN_00010e54`;
  im Live-`mcu_util` bei 0x10e84 – Versatz +0x30, s. PROTOKOLL-GESAMT.md 2.2)
- [x] `box_wrapper.so` importiert (2,3 MB); `get_rfid`-Generator dekompiliert (Cython-Glue, echte Logik in Generator-Bodies)
- [x] `serial_485_wrapper.so` importiert (141K); Symbole liegen in Cython-`__pyx_mstate`-Tabellen → keine direkten String-XRefs
- [x] Live-`.so` von FW 1.1.7.0 gesichert: `/home/pro/k2-fw-live-1.1.7.0/rootfs-live/` (box 2,3M / motor 1,1M / prtouch_v3 1,3M / serial_485 141K / libcrealitycloud 222K / mcu_util* )
- [x] `mcu_util_485` dekompiliert: Auto-Addr (0xa1: 0xfd/0xfc/0xfb/0xfe + 0xa0-Zuweisung) + 485-Update (Func 0xf0-Subs)
- [x] CFS-FW `cfs6_222_000.bin` geladen (Basis ~~0x08000000~~ – KORRIGIERT 15.10.2026: richtig 0x08004000, cfs0-App 0x08003000; 798 Funktionen, Rezept in PROTOKOLL-GESAMT.md)
- [x] CFS-FW R153 analysiert (942 Funktionen, RT-Thread/MS3791/at8236/AHT30, Details in PROTOKOLL-GESAMT.md)
- [x] `raw_send_wait_ack`: Dispatcher dekompiliert; CRC-Algorithmus als Source (`auto_addr_wrapper.py`) + 10 Live-Frames belegt – für Nachbau abgeschlossen
- [x] CFS-Details: 942 Funktionen, RT-Thread/MS3791/at8236/AHT30, finsh-Shell, RFID-Frame vollständig verifiziert (unpädded `%x`-Format, kein Abschneiden)
