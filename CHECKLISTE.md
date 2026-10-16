# K2-Protokoll-Checkliste

Audit und Änderungsbegründungen: `<projekt>/AUDIT-UND-AENDERUNGEN.md`.

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
- [x] `printer-live.cfg` + `box-live.cfg` gesichert in `<projekt>/live-1.1.7.0-configs/`
- [x] Live-FW 1.1.7.0-Dump nach `<projekt>/` (`fw/`, `config/`,
  `klippy/extras/belt_mdl.py`, `rootfs-live/`-Subset)

## Offen – Mitschnitt
- [x] `klippy.log` loggt ALLE 485-Frames selbst (`cmd_485_send_data_with_response params:`) – kein Hardware-Mitschnitt nötig
- [x] CRC geknackt: CRC-8/ATM (poly 0x07, init 0x00) über `[len..data]`, head/addr NICHT enthalten
- [x] Belt-Frames live verifiziert (Version/Flash/ADC), UUID `a0 a1 … (12 B UUID, Platzhalter)`
- [x] Box-Func-Codes live (Stand: 0x02 RFID, 0x03 Remain, 0x04 Box-Mode, 0x05 Buffer,
  0x08 HW-Status, 0x0a Box-State/Event, 0x0d SET_PRE_LOADING/RFID-Control (live nur ACK len 3; Aktionen 00/01/02 =
  disable/enable/Force-Read, KORRIGIERT 15.10.2026), 0x0e Messrad-Float,
  0x0f ~~Motor-Action~~ Auto-Preload-Sperre (KORRIGIERT 15.10.2026, Ghidra), 0x10/0x11 Extrude/Retrude, 0x14 Version/SN, 0xa2 Auto-Addr;
  Details in PROTOKOLL-485.md – Zeile 25 oben war ein veralteter Zwischenstand)
- [x] Falsche-Syntax-Falle dokumentiert: `NUM=A` → Shutdown, `NUM=0..3` korrekt
- [x] CFS-Antworten eingefangen: Slot 1 mit Spule → `remain A=26%` (`f7 01 07 00 03 1a 00 ff 00 99`), RFID-Tag-Daten via Query (SN `SN-DEMO-…(SN gekürzt)`, Tag 1.5.3, vender `1012…(gekürzt, mat 101001/col 0FFFFFF)…`, color `0FFFFFF/0ce58f8/026eeee`, material `101001/000001/000001`)
- [x] RFID-Vollantwort dekodiert: `A:1012…(gekürzt, mat 101001/col 0FFFFFF)…;...` (ASCII, `:`/`;`-Separatoren, Key=Slot-Buchstabe)
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
  Kompletter Inhalt: `A:1012…(gekürzt, mat 101001/col 0FFFFFF)…;` (43 Datenbytes, endet mit `;`).
  Farbe/Material/Restmenge kommen aus SEPARATEN Abfragen, nicht aus diesem Frame.

## Offen – Pi-Ersatz (VOLL – Belt + PrTouch + CFS müssen laufen)
- [~] 32-bit Pi OS (armhf) + Python-3.9-Venv aufsetzen (nur so laden die Creality-`.so`)  **ÜBERHOLT 16.10.2026: Weg = Upstream-Klipper + eigene Module, s. PI-ERSATZ-PLAN.md**
- [~] Original-Klippy (`klippy/`-Tree + `.so` aus `rootfs-live/`, venv auf Pi neu  **ÜBERHOLT 16.10.2026: Weg = Upstream-Klipper + eigene Module, s. PI-ERSATZ-PLAN.md**
  aufsetzen) auf den Pi kopieren
- [~] `printer.cfg` voll übernehmen, nur Serials auf `/dev/ttyUSB*` umbiegen (live: ttyS2/ttyS3/ttyS5 (Dump-F008: ttyS7/ttyS1/ttyS6))  **ÜBERHOLT 16.10.2026: Weg = Upstream-Klipper + eigene Module, s. PI-ERSATZ-PLAN.md**
- [~] Pi mit 2x USB-Serial (Mainboard/Toolhead) + 1x USB-RS485 (CFS/Belt), 24V→5V-Buck  **ÜBERHOLT 16.10.2026: Weg = Upstream-Klipper + eigene Module, s. PI-ERSATZ-PLAN.md**
- [~] Testreihe: Belt-CALI/TEST, PrTouch `BED_MESH_CALIBRATE`, CFS Load/Unload mit RFID  **ÜBERHOLT 16.10.2026: Weg = Upstream-Klipper + eigene Module, s. PI-ERSATZ-PLAN.md**
- [ ] CFS-Bus-Anschluss (Elektrik, Quelle fake-name + Deutung, s. PROTOKOLL-485.md
  „CFS-Bus physisch“): A/B-Polarität am Gerät MESSEN (Ruhe: A hoch, B tief – Benennung in
  den Quellen widersprüchlich); USB-RS485 mit kräftigem Treiber + Auto-DE (z.B. FTDI +
  MAX3485/SP485; CH340+MAX485 evtl. zu schwach bei mehreren Boxen – Vermutung wegen
  300-Ω-Bias je Box); GND mitführen; zunächst OHNE 120 Ω (in der Box unbestückt);
  24 V aus dem Netzteil; Buffer-Kabelbaum (2 Open-Collector-Leitungen) unverändert
  durchführen – läuft nicht über RS485; Steckertyp am Gerät verifizieren (extern
  „guesswork“, Micro-Fit 3.0 vermutet). Transparent-Pfad s. Review-Punkt unten.

## Offen – Nachbau VOLLUMFANG (alle Funktionen; Module in `<projekt>/nachbau/`)
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
- [x] Kanal-A-Diagnose (13.10. spät): Slot A zieht seit Klemm-Episode (key865) nicht mehr ein,  **ERLEDIGT 16.10.2026: Filamentrest im Hub entfernt, live geprüft (Abschnitt „Kanal A – GELÖST“)**
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
**Stand 15.10.2026 nach Live-Log-Einarbeitung:** serial485 81 · box 126 · boxmini 17 · cfsseq 28 ·
motor 98 · motor_mini 30 · auto_addr 31 – alle grün.

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
- [x] Retry-Zählung am klippy.log nachzählen: meint Original `retries = 4` 4 oder 1+4
  Sendungen? (Nachbau: 5 Sendungen) – **ERLEDIGT, LIVE BELEGT 15.10.2026:** 1+4 = 5
  Sendungen, je ~2 s (`klippy.log.2026-10-05:63472–63521`); retries=0 für 0x0a-Poll,
  Auto-Addr, Motoren, Broadcasts → `box.FUNC_RETRIES` (PROTOKOLL-485.md §6.2)
- [x] 0x0d-Semantik (ERLEDIGT 15.10.2026, Ghidra `FUN_0800f0da`): op 0/1 = disable/enable
  global (Maske ignoriert), op 2 = Forced Preload mit RFID, op 3 = ohne RFID; Status
  0/1/3/0x16 (+ Pull-back-Fehler); Antwort bei op 2/3 erst nach Vorgang (~3 min pro Slot)
- [~] Preload/Force-Read **live mitschneiden – bleibt sinnvoll als Gegenprobe** zum
  Ghidra-Befund (bisher nur leeres ACK len 3; Request-Bytes des Hosts, Laufzeit bis zur
  Antwort, Status-Byte, Ablauf op 2 vs. 3) – **TEILWEISE ERLEDIGT 15.10.2026 (Live-Logs):**
  op 0/1/2 live (55 Requests; [0f 01]/[0f 00] sofort, [01 02] nach 31–37 s, Status 0);
  **op 3 nie live**; ~~[00 02]-Bursts ohne Antwort offen~~ (PROTOKOLL-485.md §6.4) – Bursts
  16.10.2026 geklärt (BEFUND2 §5: Box verweigert nicht, Ursache Host-seitig vermutet)
- [x] Status-Byte-Werte belegt (15.10.2026, Ghidra): 0 ok, 1 ungültig, 2 CRC, 3 busy/
  Zustand, Ablauf-Fehler 0x05–0x19, 0x30/0x50 nur bei 0x0a – `box.STATUS_NAMES`
- [x] Status-Byte (`raw[3]`) der Antworten auswerten (Fehler/Busy) –
  ~~`box._response_data` ignoriert es noch (TODO im Code; Werte jetzt als `box.STATUS_*`
  bereitgestellt, Logik bewusst unverändert)~~ **ERLEDIGT 16.10.2026 (Review 2, M2):**
  `serial485.transact_full` + `box.CfsStatusError`/`StatusBytes`/`raise_on_status`
- [x] **Timeout 0x0d op 2/3** (Bezug Retry-TODO oben): FW antwortet erst nach bis zu ~3 min
  pro Slot – Nachbau-Default 2 s + 4 Retries hätte den Force-Preload bis zu 5× gesendet.
  ERLEDIGT 15.10.2026: `box.communication_set_pre_loading` nutzt bei op 2/3 langen Timeout
  (`preload_force_timeout(mask)`, 180 s + 200 s je Slot) und `retries=0`
- [ ] `serial485.Wrapper._done` wächst unbegrenzt (nie abgeholte Antworten) – Aufräumen/Limit
- [ ] Hintergrund-Thread kann `get_response` Antworten wegschnappen, wenn zwischen
  `raw_send` und `get_response` eine Lücke ist – Registry/Zuordnung klären
- [x] Motor 0x06: Position der ID 70 im Frame klären – getrennter TX/RX-Mitschnitt
  (Live-Frame `f7 81 07 00 06 00 00 96 43 09` trägt nur den Float) – **ERLEDIGT 15.10.2026:**
  ID im Request (`[02,id]` lesen, `[01,id]+<f>` schreiben, `[03]` übernehmen); der Frame
  war die Antwort; Werte flüchtig (PROTOKOLL-485.md §3.1, umgesetzt in motor.py)
- [ ] Erase (0xF0/0x06) belegen: nur CFS-Typ 1/10 dekompiliert, für Motoren unbelegt
  (im Nachbau nur mit `erase=True`)
- [x] Motor 0x08 (laut grant0013 „get feedback“) live prüfen – Kandidat fürs echte
  Encoder-Lesen; `read_encoder` bleibt bis dahin gesperrt – **TEILWEISE ERLEDIGT 16.10.2026
  (live, Motor X, Standby):** 0x08 = Variablen-Lesepfad `[idx]` → 4 B LE, idx 1..12 gescannt
  (9 = 0.0016113, 10 = 0.0008057, 11 = 1106, Rest 0), Bedeutung je Index **offen**
  (`motor.read_variable`, `VARIABLE_TYPES`); `read_encoder` bleibt gesperrt
- [x] Motor 0x0F (version) live prüfen – **ERLEDIGT 16.10.2026:** `[00]` → 12 B
  (fireware_ver 1.9.7.81, cmd_system 1.3, protection 1.3, flash_param 2.75, logger 1.0;
  `motor.query_motor_version`, ersetzt Stub `motor_version_normal`)
- [x] **NEU 16.10.2026:** Bedeutung der 0x08-Indizes 1..12 klären – **TEILWEISE ERLEDIGT  **ERLEDIGT 16.10.2026: idx 1–12 per Motor-FW + Live (MOTOR-FW-BEFUND, LIVE-ABFRAGEN)**
  16.10.2026 (REA, `REA-MOTOR-BEFUND-2026-10-16.md`):** idx 5 `pos_fdb`, 6 `spd_fdb`, 7 `id_fdb`,
  8 `iq_fdb` BELEGT (Host-Dict `NO_ERR_CODE_TO_GET`), Typkette fest (1–10/17/18 `<f`, 11–16 `<I`);
  `DATA=7/8 EXTRUDER=1` = Stats-Hook Extruder-Überstrom, `DATA=1` nur Hilfetext. Nachbau:
  `VARIABLE_NAMES`, `read_position_feedback`/`read_speed_feedback`/`read_current_feedback`.
  **idx 11 ERLEDIGT 16.10.2026 (live, Messfahrt X nach G28):** Single-Turn-Position
  0..3199 Counts/Umdrehung (80 Counts/mm bei rot.dist. 40; Nachbau-Name `pos_counts`,
  Host-Name unbekannt; `read_position_counts`). ~~**Offen bleiben:** idx 1–4, 9, 10, 12 (keine
  Host-Namen; Hypothese 9/10 = ADC-LSB) sowie die Einheit von `spd_fdb` (idx 6).~~
  **ERLEDIGT 16.10.2026 (Motor-FW-Befund + Live):** 1–4 pos/spd/id/iq_ref, 9 Ia, 10 −Ib [A],
  11 Schrittpuls-Zähler, 12 DIR-Eingang (Gegenprobe −X 0 / +X 1), spd_fdb rad/s – s. Punkt
  „Motor-FW-Befund“ unten. Offen nur noch idx 15/16.
  `read_encoder` bleibt gesperrt (0x06-Altfalle, Ersatz = `read_position_feedback(with_counts=True)`).
  NIE `MOTOR_GET DATA=0` oder `DATA=13` senden (Host-Crash)
- [x] **NEU 16.10.2026 (REA):** Skalierung von `pos_fdb` (idx 5) klären – **ERLEDIGT 16.10.2026
  (live, Messfahrt X nach G28, Bett leer, User-Freigabe, `LIVE-ABFRAGEN-2026-10-16.md`):**
  pos_fdb = idx11·2π/3200 **rad**, single-turn (Wrap alle 40 mm); Nachbau `POS_COUNTS_PER_REV`,
  `counts_to_mm`/`counts_delta`, `read_position_feedback(with_counts=True)`. Absolute Position
  nur über Klipper-Schrittzähler. Hinweis für künftige Messfahrten: Creality-Klipper verlangt
  für `G1` **alle** Achsen referenziert (`G28 X Y` reicht nicht → „Must home axis first“).
- [x] **NEU 16.10.2026 (REA):** Einheit von `spd_fdb` (idx 6) klären – **ERLEDIGT 16.10.2026:**
  **rad/s** (FW: LESO-Drehzahl `spd_fdb_ctrl`, Parameter `*_rad_s`; live 1,53–1,57 bei
  10 mm/s = 1,571 rad/s Soll, −1,81 bei Fahrt −X)
- [x] **NEU 16.10.2026 (REA):** Detail-/Sample-Kommandos 0x13–0x16 und 0x0C-Byte-Layout –
  **ERLEDIGT (statisch) 16.10.2026, Motor-FW-Befund:** 0x13–0x16 lesend **ohne** Request-Payload
  (0x13/0x14 je 74 B Snapshot Fehler/Stall, 0x15/0x16 `0A` + 10 × `<ffff`); Nachbau
  `read_err_detail`/`read_stall_detail`/`read_pos_samples`/`read_spd_samples` ~~(live NICHT geprüft)~~
  – **LIVE ERLEDIGT 16.10.2026** (s. Punkt „0x13–0x16 live“ unten).
  0x0C [0b]: Byte 0–3 Fehler u32 LE (key781+n), Byte 4–7 Warnung u32 LE (key795+n) →
  `decode_protection`, `check_protection()` dekodiert
- [x] **Motor-FW-Befund 16.10.2026** (`MOTOR-FW-BEFUND-2026-10-16.md`, PROTOKOLL-485.md §3.1
  NACHTRAG (4)): Motor-FW `fw/F008|F012/mot*_022_C30-mot2_002_081.bin` = WCH RISC-V (CH32V30x-
  Klasse), Basis 0x3000 (12-KB-Bootloader), Befehlsregistrierung 0x6100–0x63d8. Erledigt:
  idx 1–4 (Sollwerte), 9/10 (Ia/−Ib), 12 (DIR-Eingang, live bestätigt), spd_fdb-Einheit,
  0x0C-Layout + Sub-Codes (01–05/07/09 nur Konstanten), STATUS-Bitfeld (`decode_motor_status`),
  0x13–0x16 statisch; Nachbau `VARIABLE_TABLE` (idx 0..18, Einheiten, Belegstatus),
  `read_reference_values`, `read_phase_currents`, `read_dir_input`, `read_variable_raw_fw`
  (idx 0..18, nur `confirm=True`), cfswatch zeigt Namen/Einheiten, 0x0C-Bits und Motor-STATUS
- [x] **0x13–0x16 live (16.10.2026 17:26 Log-Uhr, Motor X 0x81, Stillstand nach M84,
  `LIVE-ABFRAGEN-2026-10-16.md` „0x13–0x16 live“, PROTOKOLL-485.md §3.1 NACHTRAG (5)):** über die
  Klipper-Webhooks `motor_err_detail_data` (0x13 → 0x15 → 0x16) und `motor_cut_detail_data` (0x14 →
  MOTOR_GET idx 5–8), `{"num": 1}`. Requests `81 04 00 1x 02` (Host-Payload `[02]`, FW ignoriert),
  Antworten STATUS 00, 74 B bzw. 161 B (Zählbyte 0x0A); Feldreihenfolge/Format = Host-JSON bestätigt,
  0x14-Zusatzfloat = `param_peak_overcurrent_A`. Nachbau: Payload `[02]`, Feldname angeglichen,
  Selbsttest 7e mit Live-Frames (UUID-Platzhalter, CRC neu). `motor_param_modifi_data` (vermutl.
  schreibend) NICHT angefasst. Rest offen: Union-Inhalte bei err_code ≠ 0, Bedeutung
  `param_peak_overcurrent_A` (live 0,012), Host-Timeout.
- [ ] **Offen aus dem Motor-FW-Befund:** idx 15/16 (obj->[0xc]+0x24/+0x2c); ~~0x13–0x16 live
  gegenprüfen (Längen 74/161 B, Feldreihenfolge der Proben vermutet)~~ (erledigt 16.10., s. oben); Setzer von Fehlerbit 4
  (HW-Überstrom) und Warnbit 0 (Kommunikation) nicht lokalisiert (Fehlerbit 11 ohne Beleg);
  Gain·R der Strommessung (1 Ω vermutet → Flash-Parameter `driver_board_amp_gain` /
  `driver_board_R_of_current_sample_ohm` lesen); idx 13 (EN) live gegenprüfen
- [ ] **NEU 16.10.2026:** Offen, ob der Motor selbst 0x08 mit idx ≥ 13 verträgt – ~~(Crash bei
  DATA=13 liegt im Host; nicht geprüft, ob ein Frame rausging)~~ Host kennt 13–18 (Typkette),
  Crash ist belegt host-seitig ohne Frame (s. unten); am Motor weiter ungeprüft – nur mit
  eigenem Sender, nicht über Stock-G-Code. **16.10. statisch geklärt (Motor-FW):** FW behandelt
  idx 0..18 lesend, > 18 → veralteter Wert, kein Crash; Nachbau `read_variable_raw_fw(idx,
  confirm=True)`. Live-Gegenprobe bleibt offen
- [x] Motor 0x0C: Protection-Semantik (grant0013/k2-vanilla) gegen Plus-Frame `00*8` prüfen
  – **ERLEDIGT 15.10.2026:** Request `[0b]` → 8 B Status (00 = ok), `[05]` ohne Antwort;
  „8-Byte-Parameter“ WIDERLEGT (`motor.check_protection`)
- [x] Motor 0x12 (dev_uuid) und 0x0E (read/set_addr) am Plus mitschneiden
  (Stock-Bootsequenz laut grant0013: `FF 0E 01`, `8x 12 02`, `8x 0E 02`) – **ERLEDIGT
  15.10.2026 (Live-Logs):** beide live, Bootsequenz Plus in PROTOKOLL-485.md §3.1
  (`motor.read_485_addr`/`read_uuid`). **16.10.2026:** `12 02` gehört nicht zur Init
  (Webhook-Poll `motor_dev_uuid`), s. `MOTOR-INIT-BEFUND-2026-10-16.md`
- [x] Transparent-Pfad über Main-MCU (`transparent_send`/`transparent_response`) für
  Pi-Ersatz klären: Motoren am Plus direkt am Bus oder nur via MCU erreichbar? –
  **GEKLÄRT 15.10.2026:** X/Y/Z/Z1 direkt am ttyS5, **nur der Extruder** über transparent
  (Adresse 0x81; 0x85 auf ttyS5 antwortet nie)
- [x] 226-Param-Map (grant0013 `motor_params_map.md`) mit Plus-klippy.log abgleichen
  (flash_param-Enumeration), ID 70 = Kp bestätigen – **ERLEDIGT 15.10.2026:** **227** IDs
  (0–226), Namen identisch; ID 70 = Kp bestätigt; Log-Falle `value:N` = ID
  (`motor.SYS_PARAM_NAMES`)
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
- [x] **0xEA/0xEB-Semantik der App** – ERLEDIGT 16.10.2026 (BEFUND2 §6): „halbe Broadcasts“ –
  0xEA wird bei normalen Kommandos mit eigener Adresse beantwortet (nur CRC-Fehler/
  unbekannte fun/0x50 still), 0xEB nur 0x50/0x51; 0xFE/0xFF nur ohne eigene Adresse
  (PROTOKOLL-485.md §6.8)
- [ ] Längengrenze Bootloader len ≤ 0xFC vs. `serial485`-Prüfung bis 255 – nur notiert,
  Code NICHT geändert (App-Grenze unbekannt)
- [x] Hypothese 0x10 `[mask 07 03]` – **BESTÄTIGT 15.10.2026 (Ghidra):** mode 7 mit
  p = 3 mm = Extruder-Check (Odometer muss in 300 ms > p mm laufen, sonst 0x0e)
- [x] 0x0a-Zustandsbyte (ERLEDIGT 15.10.2026, Ghidra): Byte 3 = IDLE 0, PRELOAD 1,
  PRINT 2, RELOAD 3, ERROR 4, TEST 5; CHECK_HUB = Preload-Statemachine, kein Box-Zustand
  (`box.BOX_STATE_*`, `box.parse_box_state`)

## Offen – aus Ghidra-Neuanalyse CFS-FW (15.10.2026)

Details: `GHIDRA-CFS-BEFUND-2026-10-15.md` §9/§10.
- [x] Quelle von 0x0a Byte 0/1 (S[1]/S[0], live 0x13 0x34; = 0x52-Antwort) – LIVE 15.10.2026:
  langsam gegenläufig (b0 0x11–0x1c, b1 0x2d–0x39) → Vermutung Temp °C / rel. Feuchte;
  mit Hygrometer gegenprüfen – **ERLEDIGT 16.10.2026 (BEFUND2 §1, B+L):** AHT30, Temp °C int8 /
  Feuchte % uint8, abgeschnitten, ~1,2 s (`box.parse_box_state` → `temp_c`/`rh`)
- [x] R[3]-Quelle (0x0a Byte 2, Flag 0x80 – Motorblock?) – **ERLEDIGT 16.10.2026 (BEFUND2 §8):**
  Motorblock > 10 s (Bit 4 Hauptmotor, Bit 0–3 Feeder), R[5] = Blockmaske sofort (`block_*`)
- [x] 0x0a Byte 5 – **ERLEDIGT 16.10.2026 (BEFUND2 §2):** Typ-7-Tag-Sperrbit, nicht „RFID gültig“
  (`inlet_tag_mask`; live immer 0 erklärt)
- [ ] Bedeutung Config #6 (0x0c lesen / 0x50 schreiben + Reset) – NICHT senden (0x50 resettet)
- [x] Inhalt 0x15 (6 Einträge `((i+1)<<4)|X[i]`) – live `10 20 30 40 50 60 00…` (alle X = 0),
  Host-Name GET_HARDWARE_STATUS; ~~Bedeutung von X weiter offen~~ **ERLEDIGT 16.10.2026
  (BEFUND2 §3):** Init-Flags EEPROM/AHT30/Odometer/Buffer/RFID0/RFID1, 0 = OK
  (`box.parse_get_hardware_status` → `components`/`all_ok`; cfswatch dekodiert)
- [x] Init der Auto-Addr-Tabelle (0xA0–0xA3 in der App) – **ERLEDIGT 16.10.2026 (BEFUND2 §7):**
  Tabelle aus entpackter `.data`; A0 3× beantwortet, A1 0–1000 ms verzögert, adressierte Box
  ignoriert A0/A1, A2 = A3. `auto_addr.py` kompatibel (Selbsttest 13 `FwSerial`)
- [x] Physische Zuordnung Sensor A/B (Pin-Tabelle @0x20000218; Deutung A = Einlauf, B = Hub) –
  **ERLEDIGT 16.10.2026 (BEFUND2 §8):** Einlauf PD7–PD4, Hub PE10–PE7, Buffer PD8/PD9; ein
  gemeinsames Odometer MT6826
- [ ] cfs6-Handler 0x16–0x19/0x57 (Trockner?) im Detail
- [~] Weitere Fehlercodes nur im LED-Mapping (~~0x1a~~, 0x20, 0x22, ~~0x51~~, ~~0x52~~, ~~0x53~~) – Quellen
  (0x51 LIVE 15.10.2026 als SPEED_ERR belegt, key846) – **16.10.2026 (BEFUND2 §4/§8):** 0x50 Runout
  im Druck-Slot, 0x51/0x52 aus print_check, 0x1a Pull-back 40 s, 0x53 nie erzeugt; offen 0x20/0x22
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
- [x] Live-`.so` von FW 1.1.7.0 gesichert: `<projekt>/rootfs-live/` (box 2,3M / motor 1,1M / prtouch_v3 1,3M / serial_485 141K / libcrealitycloud 222K / mcu_util* )
- [x] `mcu_util_485` dekompiliert: Auto-Addr (0xa1: 0xfd/0xfc/0xfb/0xfe + 0xa0-Zuweisung) + 485-Update (Func 0xf0-Subs)
- [x] CFS-FW `cfs6_222_000.bin` geladen (Basis ~~0x08000000~~ – KORRIGIERT 15.10.2026: richtig 0x08004000, cfs0-App 0x08003000; 798 Funktionen, Rezept in PROTOKOLL-GESAMT.md)
- [x] CFS-FW R153 analysiert (942 Funktionen, RT-Thread/MS3791/at8236/AHT30, Details in PROTOKOLL-GESAMT.md)
- [x] `raw_send_wait_ack`: Dispatcher dekompiliert; CRC-Algorithmus als Source (`auto_addr_wrapper.py`) + 10 Live-Frames belegt – für Nachbau abgeschlossen
- [x] CFS-Details: 942 Funktionen, RT-Thread/MS3791/at8236/AHT30, finsh-Shell, RFID-Frame vollständig verifiziert (unpädded `%x`-Format, kein Abschneiden)

## Live-Logs 15.10.2026 – Pi-Ersatz, Kanal A, offene Punkte

Quelle: `live-logs-2026-10-15/` (lokal, nicht fürs Repo), Auswertung PROTOKOLL-485.md §3.1/§6.

### Pi-Ersatz (Folgerungen aus der Live-Bootsequenz)
- [ ] **Ein** RS485-Port (Stock ttyS5, 230400) reicht für X/Y/Z/Z1 + CFS + Belt – eine
  gemeinsame Queue, kein paralleler Zugriff (Motor-, CFS- und Auto-Addr-Frames verschränkt)
- [x] Pi muss die **Motor-Init** übernehmen (Broadcast `FF 0E 01`, `0E 02`~~/`12 02`~~, Stall-Mode,  **ANALYSIERT 16.10.2026 (GPIO-PHASE-/MOTOR-INIT-BEFUND), im Entwurf pi-host/ umgesetzt – Gerätetest offen**
  Protection `0C [0b]`, flash_param-Lesen) und die **Sys-Params nach jedem Kaltstart neu
  schreiben** (0x06 Sub 01/03 flüchtig, sonst Board-Defaults Kp 300 / err_time 0,01 / …)
  – **KORREKTUR 16.10.2026:** `12 02` (UUID) ist kein Init-Schritt, sondern Webhook-Poll
  `motor_dev_uuid` (Client/Display)
- [x] **Init-Logik des Stock-Hosts rekonstruiert – ERLEDIGT 16.10.2026** (`MOTOR-INIT-BEFUND-2026-10-16.md`,
  statisch + Live-Log-Gegenprobe, abgeglichen mit einer Zweitanalyse): `set_motor_pin` @0xf87d8
  (Pseudocode), Bedingungen/Fehlerzweige key798–803, Config-Steuerung (`<motor>_<param>`, nur
  `configurable` ∩ Config, 61 von 227; IDs 9/10/25 immer gelesen; `isclose` abs_tol 1e-6; lesen/schreiben
  je ≤ 3 Versuche; `06 [03]` je Motor; ID 226 nur Host), **Adress-Strapping belegt** (Motor-FW 0x0E Sub 01:
  Adresse = 0x81 + 2·STEP + DIR), Zeiten 0,1/0,2/0,05/1,0/5 s, Stock-Bug Extruder-Param-Abgleich (0x85 auf ttyS5)
- [x] **`motor_init.py` – ERLEDIGT 16.10.2026** (Nachbau der Init nach `MOTOR-INIT-BEFUND-2026-10-16.md` §9,
  Ergebnis §10): Config-Parser `[motor_control]`, Plan P0–P9 (167 Schritte, Strapping-Prüfung), Trockenlauf,
  Boot-Log-Abgleich (alle 10 Live-Boots: 0 Abweichungen, 0 fehlend, Konsistenz `isclose` ↔ Write 48/48),
  geschützter `run()` (confirm / allow_write / strapping_ok), Selbsttest mit FakeBus und anonymisiertem
  Log-Auszug. Kein Senden ohne Freigabe.
- [x] **GPIO-Phase über die Klipper-MCU** (P1/P7 von `motor_init.py`: Dir/Step-Pegel setzen + `query_gpio_status`  **ANALYSIERT 16.10.2026 (GPIO-PHASE-/MOTOR-INIT-BEFUND), im Entwurf pi-host/ umgesetzt – Gerätetest offen**
  zurücklesen, Resend/key799/key800) – fehlt; ohne sie darf der Broadcast `FF 0E 01` nicht gesendet werden
  (`run(strapping_ok=…)`)
- [ ] **Echter Lauf `motor_init.py`** am Drucker (erst nach GPIO-Phase und mit Freigabe; zuerst lesend
  `run(confirm=True)` ohne `allow_write`)
- [x] **Extruder-Transparent-Pfad** für den Param-Abgleich nachbauen (E über nozzle_mcu `transparent_send`,  **ENTWURF 16.10.2026 in pi-host/ (TransparentTransport, [k2_motor]) – Gerätetest offen**
  Adresse 0x81 – Stock-Bug 0x85 auf ttyS5 nicht übernehmen; Vorbild ungenutzter `motor_extruder_sys_param_init`)
  – in `motor_init.py` als transparent-Schritte markiert („transparent – Nachbau fehlt“: Adresse, Stall-Mode,
  `0C [0b]/[05]`, Param-Abgleich E)
- [x] Konstanten-Gegenprüfung: 13 falsch aufgelöste Float-Konstanten der Vorarbeit korrigiert
  (`MOTOR-INIT-BEFUND-2026-10-16.md` §7); `REA-MOTOR-BEFUND`/`MOTOR-FW-BEFUND` geprüft – keine Aussage beruht
  darauf (Prüfvermerk in beiden Dateien). Offen nur, falls künftig weitere Float-Konstanten aus der alten
  Tabelle zitiert werden: dann die korrigierte Tabelle verwenden
- [x] GPIO-Phase („initial/restore set_pin_io“ der Motor-dir/step-Pins) vor/nach der  **ANALYSIERT 16.10.2026 (GPIO-PHASE-/MOTOR-INIT-BEFUND), im Entwurf pi-host/ umgesetzt – Gerätetest offen**
  Adressabfrage braucht die **Main-MCU** – **BESTÄTIGT 16.10.2026:** `MOTOR_READ_ALL_PIN_IO`
  ist eine Klipper-GPIO-Abfrage (`gpio_status`, oid 11) an der Main-MCU (PB9, PB10, !PB7,
  PB8, PB5, PB6, PA1, PB15), kein RS485
- [ ] **HINWEIS 16.10.2026 (Host-Crash):** Eine eigene MOTOR_GET-/0x08-Implementierung muss
  den Variablen-Index **vor** dem Senden validieren (belegt: 1..12). Der Stock-Host
  (`motor_control_wrapper.so`) geht bei `DATA=0`/`DATA=13` in Klipper-SHUTDOWN (key60);
  Nachbau `read_variable` lehnt alles außerhalb 1..12 mit `ValueError` ab
- [x] **Crash-Ursache MOTOR_GET DATA=0/13 – ERLEDIGT 16.10.2026 (Traceback + REA):**
  `motor_control_wrapper.py` Z. 1982 `Motor_Control.motor_get` → `box_wrapper.py` Z. 384
  `ParseData.parse_num_to_byte` → `TypeError: 'NoneType' object cannot be interpreted as an
  integer`. Creality-`gcode.py` lehnt den Bereich (minval=1/maxval=12) nur mit Warnung ab
  (`get_int` → None), `motor_get` rechnet weiter → Absturz vor dem Senden, **kein Frame**
- [x] Extruder-Motor nur über `transparent_send` (nozzle_mcu, Adresse 0x81) – Creality-  **ENTWURF 16.10.2026 in pi-host/ (TransparentTransport, [k2_motor]) – Gerätetest offen**
  Klipper-MCU-FW mit `config_transparent` oder eigene Lösung nötig

### Kanal-A-Diagnose (Slot A zieht nicht ein) – Live-Befund 13.–16.10.
- Einzug bricht **reproduzierbar nach ≈ 240–250 mm** ab: Status **0x0c** (Odometer-Stillstand),
  Odometer steht (`klippy.log.2026-10-13:104571→104608`, 128278, 128451) → key836
  „blockage between the connections and the filament sensor“ (128530), Box ERROR 0x0c
- **0x19 Motorblock beim Rückzug** (0x11 [01 01], 2×, key865, Flags 0x91; 13:95746/95885);
  Pull-back per 0x0d [00 02] mit A im Hub: 11 s Blockade, Hub bleibt belegt
- Vorgeschichte 12.10.: **0x51 SPEED_ERR** mit A im Hub während Druck (key846), key845 Düse blockiert
- **Aktuell (14./16.10.):** Box IDLE, **Hub-Sensor meldet weiterhin Slot A** (0x0a Byte 4 = 01)
  → Verdacht **Filamentrest im Hub / Förderrolle bzw. Pfad A** (mechanisch, nicht Bus –
  alle A-Kommandos sauber quittiert); **B/C/D fehlerfrei**
- RFID A: Spule ohne Tag (A:unknown, remain 0xFF) – kein Defektbeleg
- **KONSEQUENZ (BEFUND2 §8, 16.10.2026):** Ein dauerhaft aktiver **Hub-Sensor A blockiert auch
  B/C/D** – jedes 0x0d op 2/3 (Force-Preload) in IDLE und jedes 0x11 Maske 0 zieht zuerst alle
  Slots mit aktivem Hub-Sensor zurück (`FUN_08004cd0(4,1)`) → A läuft in ≥ 10 s Motorblock
  (0x19, Flags 0x90/0x91) → STATE_ERROR, Abbruch, kein Preload der anderen Slots. Der Einzug-
  Abbruch nach ≈ 240 mm (0x0c) ist reine Odometer-Logik (mode 4 prüft keinen Sensor; gemeinsames
  Odometer → B/C/D können bei mechanisch blockiertem Pfad ebenfalls 0x09/0x0c/0x52 bekommen).
- [x] Hub/Pfad A mechanisch prüfen (Filamentrest entfernen), danach 0x0a Byte 4 = 00 kontrollieren.  **ERLEDIGT 16.10.2026: Filamentrest im Hub entfernt, live geprüft (Abschnitt „Kanal A – GELÖST“)**
  Konkrete Schritte:
  1. Drucker aus, Hub öffnen, **Filamentrest von Slot A** aus Hub/Pfad entfernen (auch
     abgebrochene Stücke an der Förderrolle).
  2. **Hub-Sensor A** prüfen (PE10, optische Gabellichtschranke; Indizes PE10..PE7 = A..D):
     Staub/Abrieb entfernen, Fahne frei beweglich, Stecker J6 auf Sitz.
  3. Drucker an, **live gegenprüfen mit cfswatch**:
     `ssh root@<drucker> 'tail -F /mnt/UDISK/printer_data/logs/klippy.log' | python3 nachbau/cfswatch.py --local --all`
     → in den 0x0a-Zeilen muss **„Hub-Maske leer“** stehen (Byte 4 = 00) und darf ohne
     Filament in A nicht wieder „Hub-Maske A“ werden. Zusätzlich 0x15 = „HW-Init alles ok“.
  4. Erst danach Force-Preload/Einzug B/C/D bzw. A erneut testen (Flags-Spalte auf
     „Motorblock > 10 s“ beobachten).

### Offen nach den Live-Logs
- [ ] 0x0d op 3 ~~und Status 0x50~~ – live nie gesehen (0x50-Semantik per BEFUND2 §4 geklärt: Runout)
- [x] 0x0d [00 02]-Bursts (5 Sendungen in ~20 ms) – warum keine Antwort? – **16.10.2026
  (BEFUND2 §5):** Box verweigert nicht (RX synchron, keine Busy-Antwort, keine Duplikat-
  erkennung, mind. ein Burst-Frame live ausgeführt); **Ursache Host-seitig vermutet**
  (Burst-Timeout 2–3 ms < Box-Latenz, Halbduplex-Kollision, späte Antworten verworfen).
  Folge für den Nachbau: keine Bursts, retries=0 bei Mechanik-Kommandos (so umgesetzt)
- [x] 0x0a Byte 0/1 = Temp/Feuchte gegenprüfen (Hygrometer) – **16.10.2026 per Disassembly
  belegt** (AHT30); Hygrometer-Vergleich nur noch optional (Genauigkeit)
- [ ] Motor 0x05 (`[01]/[02]` bei BELT_TENSION, nie Antwort) – Semantik

## Review 2 (16.10.2026) – Fixes (abgehakt) + BEFUND2

Details: `AUDIT-UND-AENDERUNGEN.md` „Review 2 (16.10.2026)“, PROTOKOLL-485.md §6.9.
Selbsttests 16.10.2026 (in `nachbau/`, `ok`-Zeilen gezählt, alle grün): serial485 93/93 ·
box 179 · boxmini 17 · cfsseq 28 · motor 134 · motor_mini 43 · auto_addr 33 · cfswatch `--selftest` OK.

- [x] serial485: Absenderprüfung `expect_addr` / `BROADCAST_ADDRS` / `reply_addr_for` (M1)
- [x] serial485: `transact_full` mit Status-Byte (M2)
- [x] serial485: Bus-Hold-Grenze `MAX_BUS_HOLD_S = 1200` s; Lock bleibt bewusst über die
  Transaktion (Halbduplex, Original identisch) (M6)
- [x] box: `CfsStatusError`/`StatusBytes`/`raise_on_status` (M2)
- [x] box: `FUNC_RETRIES` 0x10/0x11 = 0 (bewusste Abweichung, `FUNC_RETRIES_ORIGINAL` = 4),
  `communication_extrude`/`communication_retrude` (M3)
- [x] box: Messrad op 0 nur mit `reset=True` (G1); Maske 0 bei 0x0d op 2/3 = 4 Slots, 980 s (G2);
  `_box_fw_num` Host-Formate „1.5.3“ (G3); `_is_full_frame` robust (G4);
  DeprecationWarning bei `get_hardware_status(channel)` (G6)
- [x] cfsseq: `EXTRUDE_ACK`/`RETRUDE_ACK`, `check_one_hot`, einziges `slot_to_mask` (G5/G6)
- [x] motor: F0-Sender nur mit `confirm=True`; `write_sys_param` `allow_locked` für 166
  gesperrte IDs; float32-Plausibilität
- [x] motor_mini: `MINI_WRITABLE_IDS` (13 Stock-IDs) + `update_sys_params` (confirm)
- [x] neues Werkzeug `nachbau/cfswatch.py` (nur lesender Live-Monitor, Bedienung im Docstring
  und PROTOKOLL-485.md §6.9)
- [x] BEFUND2 eingearbeitet (`GHIDRA-CFS-BEFUND2-2026-10-16.md`): `parse_box_state`
  (`temp_c`/`rh`, `inlet_tag_mask`, `block_*`), `parse_get_hardware_status` (`components`),
  `STATUS_NAMES` (0x05/0x0a/0x0c/0x14/0x17/0x19/0x1a/0x50/0x51/0x52), cfswatch-Texte,
  auto_addr-Selbsttest 13 (A0 3×, adressierte Box ignoriert A0)
- [ ] Offen aus BEFUND2: Config #6, Fehlercodes 0x20/0x22, Zweck Typ-7-Tag-Sperrbit (0x5c-Auth?),
  Host-Burst-Timing im `.so`, Treiber-RX-Puffergröße der Box

### GPIO-Phase / MCU-Schnittstelle (16.10.2026, GPIO-PHASE-BEFUND-2026-10-16.md)
- [x] MCU-Data-Dictionaries (Main + Düse) extrahiert; Creality-Befehle `query_gpio_status`/`gpio_status`,
  `transparent_*`, `prtouch_*`, `fancheck` identifiziert; Strapping mit Upstream-`set_digital_out` möglich
- [x] Strapping-Mechanismus: `[duplicate_pin_override]` + Zeitmultiplex, kein Spezialbefehl
- [x] Pi-Host-Modul: Strapping über digital_out vor Broadcast + Extruder über transparent_send nachbauen  **ENTWURF 16.10.2026 in pi-host/ (TransparentTransport, [k2_motor]) – Gerätetest offen**
- [ ] `mcu0_130`-Dictionary ohne `transparent_*` prüfen (vermutl. Extraktionslücke)
- [x] Pi-Host-Modul ENTWURF `pi-host/` (16.10.2026): k2_transport.py (RS485 + transparent_send), k2_motor_init.py
  ([k2_motor] für Upstream-Klipper: Strapping, Init P0–P9, Param-Abgleich, Extruder via transparent 0x81),
  test_k2_motor.py 9/9 offline. Review-Fixes: Pins vor Connect, gültige print_time, query_gpio_status
- [ ] pi-host: Test mit echtem Upstream-Klipper (Pi) + Creality-MCU-FW; Rs485Transport an Klipper-Reactor anbinden

### Kanal A – GELÖST 16.10.2026
- [x] Ursache: gebrochener Filamentrest von Slot A im Hub bis zum Extruder → Hub-Sensor A dauerhaft aktiv
  (blockierte laut CFS-FW auch B/C/D; Einzug stoppte bei ~245 mm, Status 0x0c). Rest entfernt (User).
- [x] Live-Gegenprobe (cfswatch): Ereignis „Herausziehen A“, Hub-Maske leer → Einstecken A → Auto-Preload fertig
  → Force-Preload 0x0d 31 s ok → Extrude: Modus 0 (4,4 s ok), Modus 4/5 Odometer 1313 mm ohne Stillstand,
  Modus 6 ok, Modus 7 Extruder-Check ok, Box-Modus PRINT. Kein Fehlercode.
- [x] PI-ERSATZ-PLAN.md erstellt (Bestandsaufnahme, Hardware, Verkabelung, Inbetriebnahme, offene Punkte;
  größter Blocker: PrTouch-Ersatz)

### PrTouch v3 (16.10.2026, PRTOUCH-BEFUND-2026-10-16.md)
- [x] Architektur: MCU zeichnet nur auf (pres Düse, step Main), kein Hardwarestopp; Host berechnet Kontakt
- [x] Live-Mitschnitt Auto-Nivellierung ausgewertet (99 Tastungen, Mesh 9×9, Rechenweg zoft belegt)
- [x] Cut-Kalibrierung live (Stall-Mode 3 für Suche, Kp 300/100) – LIVE-ABFRAGEN-2026-10-16.md
- [x] Entscheidung Pi-Weg: PrTouch-Ersatz (eigenes Probe-Modul) oder Eddy-Sensor  **ENTSCHIEDEN 16.10.2026: eigenes Modul für den eingebauten Kraftsensor (Weg A); Eddy optional später**
- [ ] (für A) MCU-Bit-Ebene der prtouch-Frames: Schwellen/Filter/Register

### 16.10.2026 abends
- [x] `serial485.py`: `_done` begrenzt (MAX 128 + cleanup) und erwartete Antworten werden vom Hintergrund-Thread geparkt statt
  dispatcht (get_response findet sie) – Selbsttest 100/100 (93 + 7 neu)
- [x] Entwurf `pi-host/k2_box.py` ([k2_box]: Auto-Addr, Poll, Preload, Load/Unload, Moonraker-Status, gemeinsamer Bus-Lock
  mit [k2_motor]) – offline 9/9, Gerätetest offen
- [x] KORREKTUR: Klipper-MCU-Basis 0x08003000 (Main + Düse); PrTouch „stoppt nicht selbst“ mit korrekter Basis bestätigt
- [ ] PrTouch-Stopp-Weg über Host-trsync am Gerät/Log bestätigen; dann PrTouch-Modul schreiben
- [x] RE-Reste-Sammelbefund (RE-RESTE-BEFUND-2026-10-16.md): überwiegend TEILWEISE geklärt, Restpunkte brauchen Live-Tests
