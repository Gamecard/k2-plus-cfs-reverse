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
  0x0f Motor-Action, 0x10/0x11 Extrude/Retrude, 0x14 Version/SN, 0xa2 Auto-Addr;
  Details in PROTOKOLL-485.md – Zeile 25 oben war ein veralteter Zwischenstand)
- [x] Falsche-Syntax-Falle dokumentiert: `NUM=A` → Shutdown, `NUM=0..3` korrekt
- [x] CFS-Antworten eingefangen: Slot 1 mit Spule → `remain A=26%` (`f7 01 07 00 03 1a 00 ff 00 99`), RFID-Tag-Daten via Query (SN `SN-DEMO-1234567890X`, Tag 1.5.3, vender `AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA`, color `0FFFFFF/0ce58f8/026eeee`, material `101001/000001/000001`)
- [x] RFID-Vollantwort dekodiert: `A:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA;...` (ASCII, `:`/`;`-Separatoren, Key=Slot-Buchstabe)
- [x] RFID-Zustände: `unknown` / `busy` / Hex-Daten; preloading_mask 0x1/0x2 = Slot-Bitmaske
- [x] Extrude-Sequenz T1A live: Func `0x10` State-Machine, `0x0e`=MEASURING_WHEEL, `0x05/0x04/0x0f` Aufräumen
- [x] CRC-Quelle als Python-Source gesichert (lokal, nicht im Repo; Beleg im
  Projekt-Root): POLY=0x07, Timeouts, Broadcast-Addrs, Device-Typen, Adress-Tabellen
- [x] CFS-FW R153 analysiert: 942 Funktionen, RT-Thread, MS3791/at8236/AHT30, 4 Feeder-PID, RFID-Manager, finsh-Shell-Kommandos
- [x] Ghidra-Skripte gesichert (`ghidra-scripts/`): SetCfsBase, CfsDetail, ListFuncs, FindCrc6
- [x] RFID-Antwort VOLLSTÄNDIG verifiziert (13.10.): Die `0x2e`-Frames waren NIE abgeschnitten –
  das Log-Format nutzt unpadded `%x` (`0x2` statt `0x02`), was wie Abschneiden aussah.
  Nachgezählt: 49 Tokens, len=0x2e=46 → 46+3=49 ✓, CRC-8/ATM über [len..data] = 0x78 ✓
  (anonymisierter Frame; Original-CRC 0x02 bezog sich auf die echten Bytes).
  Kompletter Inhalt: `A:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA;` (43 Datenbytes, endet mit `;`).
  Farbe/Material/Restmenge kommen aus SEPARATEN Abfragen, nicht aus diesem Frame.

## Offen – Pi-Ersatz (VOLL – Belt + PrTouch + CFS müssen laufen)
- [ ] 32-bit Pi OS (armhf) + Python-3.9-Venv aufsetzen (nur so laden die Creality-`.so`)
- [ ] Original-Klippy (`klippy/`-Tree + `.so` aus `rootfs-live/`, venv auf Pi neu
  aufsetzen) auf den Pi kopieren
- [ ] `printer.cfg` voll übernehmen, nur Serials auf `/dev/ttyUSB*` umbiegen (live: ttyS2/ttyS3/ttyS5 (Dump-F008: ttyS7/ttyS1/ttyS6))
- [ ] Pi mit 2x USB-Serial (Mainboard/Toolhead) + 1x USB-RS485 (CFS/Belt), 24V→5V-Buck
- [ ] Testreihe: Belt-CALI/TEST, PrTouch `BED_MESH_CALIBRATE`, CFS Load/Unload mit RFID

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
  box-state/version-sn/buffer/hw-status, ~~50/50~~ **68/68 Checks** Stand 15.10.2026), BoxState-Tn-Modell,
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
- [x] `cfsseq.py`: Extrude/Retrude als prüfbare Abläufe – Selbsttest ~~6/6~~ **17/17 ok**
  (15.10.2026; Builder nehmen 1-hot-Maske statt Index, Preload-Aktionen 00/01/02)
- [ ] CFS-Stufen-Semantik vollständig: 0x10/0x11-Datenbytes je Stufe belegen
  (weitere Extrudes mitschneiden + mit Klippy-Stage-Logs korrelieren)
- [ ] Kanal-A-Diagnose (13.10. spät): Slot A zieht seit Klemm-Episode (key865) nicht mehr ein,
  kein Insert-Event im Log; Gegenbeweis RFID-Theorie: Insert ist mechanisch, B lief ohne Tag.
  Nächster Test: dieselbe Spule in Slot C – zieht C ein → Kanal A defekt
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
serial485 81/81 · box 68 · boxmini 15 · cfsseq 17 · motor 65 · motor_mini 20 · auto_addr 31.

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
- [ ] Preload-0x0d live mitschneiden (bisher nur leeres ACK len 3; Request-Bytes und
  ob Byte 1 bei 00/01 die Maske trägt – ref-hi 0x0F vs. vanilla 0 – unbelegt)
- [ ] Status-Byte (`raw[3]`) der Antworten auswerten (Fehler/Busy, vgl. vanilla STATUS_*) –
  `box._response_data` ignoriert es noch (TODO im Code)
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
- [ ] Code-Restwiderspruch: `box.get_measuring_wheel`-Docstring nennt noch
  `c5019149 -> -2073,08` (Tippfehler, korrekt c5009149 = −2057,08) – Code-Agent

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
- [x] CFS-FW `cfs6_222_000.bin` geladen (Basis 0x08000000, 798 Funktionen, Rezept in PROTOKOLL-GESAMT.md)
- [x] CFS-FW R153 analysiert (942 Funktionen, RT-Thread/MS3791/at8236/AHT30, Details in PROTOKOLL-GESAMT.md)
- [x] `raw_send_wait_ack`: Dispatcher dekompiliert; CRC-Algorithmus als Source (`auto_addr_wrapper.py`) + 10 Live-Frames belegt – für Nachbau abgeschlossen
- [x] CFS-Details: 942 Funktionen, RT-Thread/MS3791/at8236/AHT30, finsh-Shell, RFID-Frame vollständig verifiziert (unpädded `%x`-Format, kein Abschneiden)
