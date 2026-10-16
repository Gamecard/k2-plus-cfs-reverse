# PI-Ersatz-Plan: Upper-Board (T113, Creality-Klipper) → Raspberry Pi CM5 mit Upstream-Klipper

> Stand 16.10.2026. Erstellt mit einem weiteren KI-Agenten (statisch, aus den Projektbefunden), von uns durchgesehen. Alles mit ANNAHME Markierte ist nicht belegt.


Stand: 16.10.2026. Ziel: Unveränderte Creality-MCU-Firmware auf Main-MCU (GD32F303RET6) und
Düsen-MCU (GD32F303CBT6) weiterverwenden, Host (Upper-Board mit Allwinner T113) durch
Raspberry Pi CM5 mit Upstream-Klipper + eigenen Modulen ersetzen. Keine MCU-Firmware flashen.
ANNAHME = nicht durch Quellen belegt. Fundstellen in Klammern. Keine Seriennummern/UUIDs/IPs.

Quellen (alle nur gelesen): CHECKLISTE.md (Pi-Ersatz, CFS-Bus-Elektrik, offene Punkte),
PROTOKOLL-485.md (CFS-Bus physisch, Motoren §3.1, Live-Gegenprobe §6), PROTOKOLL-GESAMT.md,
GPIO-PHASE-BEFUND-2026-10-16.md, MOTOR-INIT-BEFUND-2026-10-16.md, pi-host/README.md ([k2_motor]-Entwurf),
MCU-Dictionaries oc-gpio-mcu/*.dict.json, live-1.1.7.0-configs/printer-live.cfg + box-live.cfg,
Werks-Configs config/F008*/ (printer.cfg, motor_control.cfg, box.cfg, gcode_macro.cfg, sensorless.cfg),
Creality-Klipper-Extras klippy/ und rootfs-live/.

Wichtige Vorbemerkung: CHECKLISTE.md „Pi-Ersatz (VOLL)" beschreibt den Weg mit
Original-Klippy + Creality-.so (32-bit armhf, Python 3.9, .so aus rootfs-live/). Dieser Plan hier
geht den anderen Weg (Auftrag): Upstream-Klipper, keine .so, dafür Nachbau-Module
(nachbau/*.py) und pi-host/[k2_motor]. Die .so (armhf/cpython-39, nur T113) laufen auf
einem CM5-Upstream-System nicht (PROTOKOLL-GESAMT.md §5).

## 1. Bestandsaufnahme: Creality-Module, MCU-Befehle, Upstream-Äquivalent

Legende Status: „Upstream ok" = Standard-Config übernehmbar. „Eigenes Modul nötig
(vorhanden)" = Nachbau/pi-host liegt vor, aber kein produktiver Klipper-Einsatz belegt.
„Eigenes Modul nötig (fehlt)" = muss erst geschrieben werden. „Unbekannt/optional" =
für Erstdruck entbehrlich.

| Funktion | Creality-Host-Modul + genutzte MCU-/Bus-Befehle | Upstream-Äquivalent? | Status |
|---|---|---|---|
| Closed-loop-Motoren X/Y/Z/Z1/E | `motor_control_wrapper.so` (rootfs-live, 1,1 MB) + `motor_control.cfg`/`sensorless.cfg`; RS485 ttyS5: 0x06 Sys-Param `[01/02/03,id]`, 0x0C Protection `[0b]/[05]`, 0x11 Stall-Mode, 0x0E Addr, 0x12 UUID, 0x08 Variablen idx 1..12, 0x0F Version, 0x13–0x16 Snapshots (PROTOKOLL-485.md §3.1); Extruder nur via `transparent_send`/`transparent_response` an Düsen-MCU (Adr. 0x81); GPIO-Phase via `query_gpio_status`→`gpio_status` (oid 11, PB9/PB10/!PB7/PB8/PB5/PB6/PA1/PB15), `config_digital_out`/`update_digital_out`, `[duplicate_pin_override]` (GPIO-PHASE-BEFUND, MOTOR-INIT-BEFUND) | Bewegung selbst Upstream ok (STEP/DIR über Main-MCU, Pins aus printer-live.cfg); RS485 nur Diagnose/Param, kein Upstream | Eigenes Modul nötig (vorhanden als Entwurf: pi-host/`[k2_motor]` + `k2_transport.py`, 9/9 offline, am Gerät UNGETESTET; Logik-Vorlage nachbau/`motor_init.py`+`motor.py`) |
| Bett-Abtastung/Strain (PrTouch v3) | `prtouch_v3_wrapper.so` (1,3 MB) + `[prtouch_v3]` + `[bed_mesh]` 9×9; MCU: `config_prtouch_pres/step/apax`, `start/stop/read/cont_prtouch_*` → `ack_prtouch`, `resault_prtouch_*`; Pins `pres_cs0 nozzle_mcu:PB13/PB14`, `pres_swap nozzle_mcu:PA15`, `step_swap !PC7` (printer-live.cfg; Dicts Befehle 38–49, Antworten 94/-30/-31/-32) | `[bed_mesh]`-Algorithmus ja; Stopp läuft über normalen Endstop auf `!PC7` (Swap-Leitung, trsync) → Upstream-`[probe]` nutzbar, wenn die Düsen-MCU vorher scharf geschaltet wird (BELEGT 16.10.2026, PRTOUCH-LESETEST-BEFUND §3/§6) | Entwurf pi-host/`[k2_prtouch]` (Lesepfad + Swap am Gerät getestet, Fingerdruck-Test 19/19); Umbau auf `[probe]` + Arm/Disarm-Hook offen |
| Belt-Spannmodule mdlx/mdly | `belt_mdl.py` (reines Python, offen, 621 Zeilen, Klartext in klippy/extras/) + `[belt_mdl mdlx/mdly]`; RS485 0x21/0x22: func 0/2/4/6/8, CRC-8/ATM, retries=4 (PROTOKOLL-485.md §1) | Keins (Riemenspannungsmessung in N) | Eigenes Modul nötig (vorhanden als Vorlage: `belt_mdl.py` 1:1 übernehmbar, braucht nur neuen RS485-Transport; Kalibrier-Makros aus gcode_macro.cfg prüfen) |
| Lüfterüberwachung | `config_fancheck`/`query_fancheck` → `fan_status` (Dict Befehle 18/19, Antwort 90); Config `[fan_feedback]` (printer_params.cfg): PTC PC6, Hotend PA12, Bauteil PC13 | Lüftersteuerung (`[heater_fan]`, `[output_pin]`, `[temperature_fan]`) Upstream ok; Drehzahl-Feedback nein | Upstream ok für Betrieb; Überwachung = eigenes Modul nötig (fehlt, optional, Druck geht ohne) |
| Kammerheizung/-lüfter | Nur Standard-Sektionen: `[heater_generic chamber_heater]` PC12 + NTC PC5 (watermark), `[temperature_fan chamber_fan]` PA0, `[heater_fan chamber_fan]` !PB14/enable PB2 (`is_ptc_fan`), `[temperature_sensor chamber_temp]` (printer-live.cfg) | Ja, alles Upstream-Standard | Upstream ok (Pins + watermark-Regelung 1:1 übernehmen) |
| Filamentsensor einfach | `[filament_switch_sensor filament_sensor]` `^!nozzle_mcu:PA11` + `BOX_CHECK_MATERIAL_REFILL` (printer-live.cfg) | Ja (`filament_switch_sensor`) | Upstream ok |
| CFS-Box (4 Slots) + Buffer | `box_wrapper.so` (2,3 MB) + `[box]`/`[serial_485 serial485]`/`[auto_addr]` + M8200-Makros (box.cfg); RS485 0x01: 0x02 RFID, 0x03 Remain, 0x04 Box-Mode, 0x05 Buffer, 0x08 Sensor-State, 0x0a Box-State, 0x0d Preload, 0x0e Messrad, 0x0f Sperr-Flag, 0x10/0x11 Extrude/Retrude, 0x14 Version/SN, 0x15 HW-Status, 0xa2 Auto-Addr; retries=4 außer 0x0a=0 (PROTOKOLL-485.md §2/§6) | Nein | Eigenes Modul nötig (teilweise vorhanden: nachbau/`serial485.py`+`box.py`+`cfsseq.py`+`auto_addr.py`+`cfswatch.py` als Protokoll-Bibliothek, alle Selbsttests grün; Klipper-Modul `[k2_box]` fehlt) |
| Filament-Rack/Trockner | `filament_rack_wrapper.so` + `filament_rack.py` (Loader) + `[filament_rack]` (Addr. 0x11, `not_pin !PA5`); live nie gesehen (PROTOKOLL-GESAMT.md §1.2) | Nein | Unbekannt/optional (ohne Rack-Hardware nicht testbar; für Erstdruck weglassen) |
| Cutter + Cut-Kalibrierung | `BOX_CUT_MATERIAL`, `CALIBRATE_CUT_POS` (gcode_macro.cfg: Kp ID 70 300→100 via `MOTOR_SYS_PARAM`, `MOTOR_CHECK_CUT_POS`, `motor_control.cut.pos_x`); Box-Koordinaten box.cfg | Bewegung Upstream ok; Kp-Umschaltung braucht Motor-Modul, Sequenz braucht Box-Modul | Eigenes Modul nötig (nutzt `[k2_motor]` + Box-Modul; ohne beide kein Cutter-Betrieb) |
| Input-Shaping/ADXL | `[lis2dw]` (nozzle_mcu:PA4 + Soft-SPI PA5/6/7) + `[resonance_tester]` + `[input_shaper]` mzv ~42 Hz (printer-live.cfg; live kalibriert, PROTOKOLL-485.md §1); MCU: `config_lis2dw`/`query_lis2dw` (Dict 61–63) | Ja, Upstream kennt Beschleunigungssensor + `[input_shaper]` (ANNAHME: `[lis2dw]`-Syntax am Pi-Quellbaum verifizieren) | Upstream ok (mit Vorbehalt, s. ANNAHME) |
| Dual-Z | `[z_tilt]` (Upstream-Standard) + `[z_align]` (Creality-eigen, Endstopps PA15/PA8, MCU `config_z_align`/`query_z_align`, Dict 13–17) | `z_tilt` ja, `z_align` nein | Upstream teilweise (z_tilt übernehmen; z_align-Ersatz fehlt → zunächst nur z_tilt oder manuell) |
| Filament-Wechsel/Makros | `gcode_macro.cfg` + `box.cfg`-Makros (M8200-Kette P→L→F→O, CR_BOX_*), `sensorless.cfg` (`homing_override` mit MOTOR_* + PRES_CHECK) | Makro-Sprache ja, Creality-G-Codes (BOX_*, MOTOR_*, PRES_CHECK, CR_BOX_*) nein | Eigenes Modul nötig (fehlt; M8200-Kette muss auf Box-Modul abgebildet werden) |
| Kamera | Display-App `webrtc_local` Port 8000 + `[load_ai]` + yolov5-Bins (PROTOKOLL-GESAMT.md §6); nur Strings, nicht analysiert | Basis-Kamera via Moonraker/Cam-Software ja (ANNAHME: crowsnest/o.ä.); AI-Abfall-Erkennung nein | Upstream ok für Bild; AI = Unbekannt/optional (weglassen) |
| Display/Touchscreen | Display-App `c440x` (Port 80, Moonraker-Webhooks wie `motor_dev_uuid`), spricht CFS teils direkt via RS485 am Klipper vorbei (PROTOKOLL-485.md M8200-Sequenzen) | Nein (KlipperScreen wäre Fremd-Display, ANNAHME, nicht an Stock-Display getestet) | Unbekannt/offen (für Erstdruck entbehrlich → Mainsail/Fluidd im Browser) |
| Power-Loss/EEPROM | `[mcu rpi]` + `[bl24c16f]` (i2c.1 via Host-MCU) + `print_stats.power_loss`-Zweige in sensorless.cfg | Host-MCU `rpi` entfällt auf Pi (dort läuft Klipper selbst); Power-Resume kein Upstream-Standard | Unbekannt/optional (weglassen; kein Ersatz vorhanden) |
| RS485-Transport + Auto-Addr | `serial_485_wrapper.so` + `auto_addr_wrapper.so` + Loader `serial_485.py`/`auto_addr.py` (klippy/extras/); Frame F7+CRC-8/ATM über len..data, 230400 8N1, eine Queue (PROTOKOLL-485.md §1.1/§1.6) | Nein (Upstream hat kein RS485-Multidrop) | Eigenes Modul nötig (vorhanden als Bibliothek: nachbau/`serial485.py` 93/93 + `auto_addr.py` 33/33; Klipper-Einbindung mit einer Queue fehlt) |

Creality-eigen-Zusammenfassung: echte Logik in `rootfs-live/usr/share/klipper/klippy/extras/*.so`
(box/motor_control/prtouch_v3/serial_485/filament_rack) + `usr/bin/mcu_util*` + `klipper_mcu`
(PTY-Bridge) + `libcrealitycloud.so` (Cloud/MQTT, nicht druckrelevant); `klippy/extras/*.py`
sind nur Loader (`from .box_wrapper import …`), Ausnahme `belt_mdl.py` (offen).

## 2. Hardware-Liste

- Raspberry Pi CM5 + Trägerboard (ANNAHME Typ: z.B. offizielles CM5-IO-Board oder
  Waveshare-Variante mit 2× USB-A, Ethernet, 5-V-Einspeisung; WLAN-Variante für
  Mainsail-Zugang). Speicher: microSD (≥32 GB, ANNAHME) oder NVMe/eMMC je Board
  (ANNAHME) – System + Klipper-Logs + Gcodes (virtual_sdcard-Pfad wird auf Pi gelegt).
- Netzteil: vorhandenes 24-V-Druckernetzteil + Step-Down 24 V → 5 V, ANNAHME ≥5 A
  (CM5 + 3× USB-Adapter + ggf. Display; CHECKLISTE.md fordert 24V→5V-Buck ohne Stromwert).
  Getrennte Absicherung/Schalter für Pi-Seite empfohlen (ANNAHME).
- 2× USB-UART für Main- und Düsen-MCU, 3,3-V-TTL, 230400 8N1 (live: ttyS2/ttyS3 @230400,
  printer-live.cfg; Dump-F008 abweichend ttyS7/ttyS1/ttyS6 @460800 – es gelten die
  Live-Werte). Physische TX/RX/GND-Punkte am Mainboard: aus Configs/Befunden NICHT
  ableitbar → ANNAHME: an den bisherigen T113-UART-Leitungen (TxD/RxD/GND) abgreifen,
  vorher mit Multimeter/Oszilloskop verifizieren (Idle 3,3 V, 230400). Kein 5-V-Adapter,
  kein RS232-Pegel, TX/RX nicht vertauschen (ANNAHME: Beschriftung am Board fehlt).
  Stabile Pfade `/dev/serial/by-id/*` verwenden (ANNAHME).
- 1× USB-RS485 mit Auto-DE, kräftiger Treiber, z.B. FTDI + MAX3485/SP485
  (CHECKLISTE.md Pi-Ersatz; CH340+MAX485 evtl. zu schwach bei mehreren Boxen –
  Vermutung wegen 300-Ω-Bias je Box). 230400 8N1, Halbduplex, EINE Queue im Host
  (Motoren + CFS + Belt teilen sich den Bus, PROTOKOLL-485.md §3.1/§6). Bias:
  je Box 300 Ω A→5 V / B→GND bereits bestückt; 120-Ω-Terminierung in Box
  unbestückt → zunächst OHNE 120 Ω, GND unbedingt mitführen, Buffer-Leitungen
  nicht auf RS485 legen (PROTOKOLL-485.md „CFS-Bus physisch").
- Kabel/Stecker CFS-Bus 6-polig extern: Belegung laut fremder Netzliste nur schwach
  belegt („guesswork", Micro-Fit 3.0 vermutet): 1 B, 2 GND, 3 24 V, 4/5 Buffer, 6 A;
  intern J4 8-polig: 1–2 +24 V, 3–4 GND, 5 B, 6 A, 7–8 Buffer-SW1/SW2 (PD8/PD9)
  (PROTOKOLL-485.md „CFS-Bus physisch"). A/B-Benennung widersprüchlich → am Gerät
  MESSEN (Ruhe: A hoch, B tief). Steckertyp am Gerät verifizieren (CHECKLISTE.md).
- Display-Anbindung: offen (ANNAHME: Stock-Touchscreen wird NICHT übernommen;
  Bedienung über Mainsail/Fluidd im Browser; KlipperScreen o.ä. nur optional).

## 3. Verkabelungsplan (Quelle → Ziel)

Alle Angaben 3,3-V-Logik wo UART; RS485 differentiell; 24 V nur an Power-Pins.
Vor dem ersten Strom: Durchgangs-/Spannungsprüfung, dann erst stecken.

| # | Quelle | Ziel | Signal / Bemerkung | Pegel |
|---|---|---|---|---|
| 1 | Drucker-24-V (Netzteil) | Buck-Eingang | 24-V-Versorgung Pi-Seite, eigene Sicherung (ANNAHME) | 24 V DC |
| 2 | Buck-Ausgang | CM5-Trägerboard 5-V-In | Pi-Versorgung, ANNAHME ≥5 A, dicke Leitungen, GND sternförmig | 5 V DC |
| 3 | Mainboard-UART TX/RX/GND (bisher T113 ttyS2) | USB-UART-Adapter 0 (RX/TX/GND) | Main-MCU GD32F303RET6, 230400 8N1; TX→RX kreuzen; exakte Punkte ANNAHME (messen!) | 3,3 V TTL |
| 4 | Toolhead-/Düsen-MCU UART TX/RX/GND (bisher T113 ttyS3) | USB-UART-Adapter 1 (RX/TX/GND) | Düsen-MCU GD32F303CBT6, 230400 8N1; TX→RX kreuzen; Leitungsweg ANNAHME (Kabelbaum Toolhead) | 3,3 V TTL |
| 5 | CFS-Bus Pin 6 (A) / Pin 1 (B) + Pin 2 GND (Belegung ANNAHME, s. §2) | USB-RS485 A/B/GND | RS485-Bus 230400, Auto-DE; A/B-Polarität MESSEN (Ruhe A hoch/B tief), ggf. tauschen | differentiell + GND |
| 6 | CFS-Bus Pin 3 | Drucker-24-V | Box-Versorgung (J4: 2× 24 V parallel); Stromtragfähigkeit beachten | 24 V DC |
| 7 | CFS-Bus Pin 4/5 (Buffer-SW1/SW2) | unverändert durchführen (Box↔Buffer) | 2× Open-Collector, laufen NICHT über RS485 (PROTOKOLL-485.md) | Open-Collector (ANNAHME 3,3/5 V, messen) |
| 8 | USB-UART 0/1 + USB-RS485 | CM5 USB-A-Ports | Stabile by-id-Pfade notieren; EIN RS485-Port für alles (eine Queue) | USB 5 V |

Warnhinweise (unbedingt beachten):

- 24 V tötet 5-V-/3,3-V-Eingänge sofort: 24-V-Pin (CFS Pin 3 / J4 Pin 1–2) nie mit
  A/B/GND/Buffer/UART vertauschen; zuerst GND verbinden, dann Signal, zuletzt 24 V.
  Buck-Ausgang vor Anschluss messen (5,0–5,2 V, ANNAHME).
- RS485-A/B-Polarität: Quellen widersprechen sich → MESSEN, nicht raten; bei
  vertauschten A/B bleibt der Bus stumm (kein Schaden, aber keine Antwort).
  120-Ω-Widerstand zunächst NICHT bestücken (Box hat keinen); GND immer mitführen.
- UART-Pegel: nur 3,3-V-TTL-Adapter; 5-V-TTL oder RS232 zerstört die GD32-Pins (ANNAHME:
  GD32 sind 3,3-V-Bausteine). TX/RX kreuzen (TX→RX), nicht TX→TX.
- Stecker: externer 6-pol-Typ nur vermutet (Micro-Fit 3.0, „guesswork") → vor dem
  Crimpen Gehäuse/Pinabstand/Raster am Gerät verifizieren (CHECKLISTE.md).
- Reihenfolge beim Test: erst Pi + USB-UARTs ohne 24 V/CFS prüfen (§4), CFS-24-V erst
  nach RS485-Grundfunktion. Buffer-Kabelbaum nie abklemmen/umlegen (direkte
  Open-Collector-Leitungen, PROTOKOLL-485.md).

## 4. Software-Schritte

### 4.1 Basis (ANNAHME: Standard-Pi-Weg, nicht aus Drucker-Quellen)

1. Raspberry Pi OS Lite (64-bit, ANNAHME) auf SD/NVMe, SSH + WLAN/LAN, Updates.
2. Upstream-Klipper + Moonraker + Mainsail/Fluidd nach offizieller Doku installieren
   (ANNAHME; KIAUH o.ä. nur als Hilfe). Kein Creality-Klippy-Tree, keine .so kopieren.
3. `printer.cfg`-Umbau (s. 4.2), dann `FIRMWARE_RESTART`, Logs (`klippy.log`,
   `moonraker.log`) beobachten.

### 4.2 printer.cfg-Umbau (von printer-live.cfg + F008-Werks-Configs)

- Behalten (Upstream ok, Werte 1:1): `[stepper_x/y/z/z1]` (Pins PB8/!PB7/PB10/PB9/
  PB6/PB5/PB15/PA1, enable !PA9, rotation_distance 40/40/8/8, endstops PB11/PB12),
  `[extruder]` (nozzle_mcu-Pins, my_thermistor 260 k, PID-Sätze), `[heater_bed]`
  (PC8/EPCOS), Kammer (`chamber_heater` PC12/watermark, `temperature_fan` PA0,
  `chamber_fan` !PB14/PB2, `chamber_temp` PC5), Lüfter (`hotend_fan`, `fan0/1/2`,
  `extruder_fan`, `power` PC9, `ptc_power` PB2), `[filament_switch_sensor]`,
  `[bed_mesh]`-Geometrie (345/9×9), `[z_tilt]`, `[input_shaper]`-Werte als Start,
  `[lis2dw]`-Pins (Syntax prüfen), `[printer]` corexy-Limits, `[pause_resume]`/
  `[exclude_object]`/`[display_status]`/`[virtual_sdcard]` (Pfad auf Pi anpassen),
  `[gcode_arcs]`, `[verify_heater]` (Fundstellen: printer-live.cfg komplett;
  Geometrie-Gegenprobe PROTOKOLL-GESAMT.md 1.9).
- Serials umbiegen (live ttyS2/ttyS3/ttyS5 → Pi by-id, ANNAHME-Namen):
  `[mcu] serial: /dev/serial/by-id/<usb-uart-main>`, `[mcu nozzle_mcu] serial:
  /dev/serial/by-id/<usb-uart-duese>`, jeweils `baud: 230400`, `restart_method:
  command` (CanBoot, unveränderte FW). RS485-Port zentral EINMAL öffnen
  (230400, eine Queue; ANNAHME: eigenes 485-Manager-Modul, da Upstream keins hat).
- Ersetzen: `[motor_control]` (+ sensorless-MOTOR_*-Makros) → `[k2_motor]` aus
  pi-host (s. README dort: `k2_transport.py` → extras, `k2_motor_init.py` →
  extras/`k2_motor.py`; Pins motor_*_dir/step/stall + `x/y/z/z1/e`-Params aus
  motor_control.cfg übernehmen; ID 226 darf stehenbleiben, wird nie gesendet;
  `allow_write: False`, `shutdown_on_failure: False`, `auto_init: True/False`
  nach §4.3). `[duplicate_pin_override]`-Zeile aus printer-live.cfg übernehmen
  (enthält bereits alle Strapping-Pins + PA9) – Pflicht für Zeitmultiplex
  (GPIO-PHASE-BEFUND §3–4).
- Deaktivieren/entfernen bis Ersatz vorhanden: `[box]`, `[serial_485 ...]`,
  `[auto_addr]`, `[filament_rack]`, `[belt_mdl ...]`, `[prtouch_v3]`,
  `[z_align]`, `[load_ai]`, `[bl24c16f]` + `[mcu rpi]`, `[fan_feedback]`/
  `fancheck`-Nutzung, alle `BOX_*`/`MOTOR_*`/`PRES_CHECK`/`CR_BOX_*`-Makros
  (box.cfg, gcode_macro.cfg, sensorless.cfg `homing_override`). Z-Homing-Endstop
  `probe:z_virtual_endstop` zunächst durch harmlosen Platzhalter ersetzen
  (ANNAHME: z.B. temporärer Schalter oder `homing_override` nur X/Y; ohne
  prtouch-Ersatz KEIN Z-Homing, KEIN Mesh – ehrliche Grenze).

### 4.3 Erste Inbetriebnahme (Reihenfolge + Abbruchkriterien)

Jeder Schritt nur bei bestandenem Vor-Schritt. Logs nach jedem Schritt prüfen
(`ring gfx`-analog gibt es hier nicht; relevant: Klipper-SHUTDOWN, key798–803-analoge,
Timeouts, STATUS≠0, `CfsStatusError`).

1. Nur MCU-Verbindung: `[k2_motor] auto_init: False`, CFS/Belt/prtouch auskommentiert.
   Erwartet: `Klipper ready`, `FIRMWARE_RESTART` sauber, Dictionaries melden je 75
   Befehle/32 Antworten (oc-gpio-mcu). Abbruch bei: kein Connect, falsche MCU-Version,
   `gpio_status`-Test fehlgeschlagen → UART-Verdrahtung/Baud prüfen, STOP.
2. Motor-Init DRY_RUN: `K2_MOTOR_INIT DRY_RUN=1`. Erwartet: Plan P0–P9 mit 167
   Schritten, Strapping X 0x81/Y 0x82/Z 0x83/Z1 0x84 (MOTOR-INIT-BEFUND §10).
   Abbruch bei: Plan weicht vom Befund ab (Pins/IDs) → Config korrigieren, STOP.
3. Motor-Init lesend (`allow_write: False`): `K2_MOTOR_INIT`. Erwartet: nur Reads,
   danach key798-analog („kein Broadcast im Diagnose-Modus", pi-host/README §4) –
   das ist KEIN Modulfehler. Abbruch bei: SHUTDOWN mit anderer Ursache, Framing-/CRC-Fehler → Bus prüfen, STOP.
4. Motor-Init schreibend (`allow_write: True`, Strapping gesetzt, `confirm`):
   `K2_MOTOR_INIT ALLOW_WRITE=1`. Erwartet: „All motors are normal", Stall Z/Z1/E,
   `0C [0b]` 8×00, Restore-Pegel 0, Param-Abgleich (flüchtige IDs, Toleranz 1e-6),
   Event `k2_motor:ready` (MOTOR-INIT-BEFUND §2/§4). Abbruch bei: key798 (Adress-Runde
   unvollständig → Strapping/Pegel prüfen), key799/800 (GPIO-Resend → Pins prüfen),
   key801/802 (Stall), STATUS-Bitfeld ≠0 (`decode_motor_status`), `0C`-Fehlerbits
   (key781+n) → STOP, kein Homing.
5. Homing ohne Z: nur X/Y (Stall-Mode wie sensorless.cfg, aber ohne MOTOR_*-Makros;
   ANNAHME: temporäre Upstream-Homing-Sequenz). Erwartet: sauberes Anfahren der
   Endstops PB11/PB12. Abbruch bei: Schrittverlust/Blockade (Motor-STATUS Stall-Bit,
   0x08 idx 5/11 prüfen) → STOP. Z/Mesh erst nach prtouch-Ersatz (gesperrt bis Modul
   vorhanden – ehrliche Grenze dieses Plans).
6. Erst danach (optional, je Modulreife): Belt (`BELT_TENSION`-Äquivalent, retries=4),
   dann CFS lesend (0x14 Version, 0x15 HW-Status, 0x0a Poll, 0x02/0x03 RFID/Remain –
   alle mit `retries` wie PROTOKOLL-485.md §6.2; 0x10/0x11 erst mit `retries=0`,
   ohne Bursts, nachbau/`cfsseq.py`), dann Extrude-Sequenzen nur mit freiem Hub
   (0x0a Byte 4 = 00, sonst Motorblock-Risiko 0x19, CHECKLISTE.md Kanal-A-Diagnose).

## 5. Risiken und Rückweg

- Keine MCU-Firmware flashen: kein `mcu_util`/`mcu_util_485`-Update (Func 0xF0/Codes
  0x04FB–0x02FD, PROTOKOLL-GESAMT.md §2), kein CanBoot-Flash, kein CFS-Update
  (0xF0-Subs), kein `0x50` Config-Schreiben (führt Reset aus), kein Bootloader-Sprung
  `0x0B [01]`, kein `0x0C 01–05` (zustandsändernd) außer Stock-Sequenz `0C [05]`
  nach `[0b]` (MOTOR-INIT-BEFUND). Sys-Params (0x06) sind flüchtig → Kaltstart stellt
  Board-Defaults wieder her (PROTOKOLL-485.md §3.1); ID 226 nie senden (Host-Parameter).
- Mechanik-Risiken: falsche CFS-Frames (0x10/0x11 mit Retries/Bursts) lösen
  Doppel-Förderung aus (Box hat keine Duplikaterkennung, PROTOKOLL-485.md §6.8) →
  nur `cfsseq.py`-Sequenzen mit retries=0; `MOTOR_GET DATA=0/13`-Absturz des
  Stock-Hosts nicht nachbauen (eigene Implementierung validiert 1..12,
  MOTOR-INIT-BEFUND/REA-Befund). Hub-Sensor belegt blockiert alle Slots
  (CHECKLISTE.md Kanal A) → vor jedem Load 0x0a-Byte-4 prüfen.
- Elektrische Risiken: 24 V auf Logik, RS485 ohne GND, UART mit 5 V, Verpolung –
  Folgen: zerstörte GD32/CM5/Box-Treiber. Gegenmittel: §3-Warnungen + Messen vor
  Stecken. Motor-Adressen ohne Strapping kollidieren (alle 0x81) → Broadcast nur
  mit `strapping_ok` (pi-host-Guard).
- Rückweg (jederzeit): Pi abklemmen, Original-Upper-Board wieder an ttyS2/ttyS3/ttyS5
  + 24 V + Buffer-Kabelbaum anschließen; MCUs behalten ihre FW (unverändert),
  Sys-Params setzen sich nach Kaltstart zurück; CFS-Auto-Addr startet stock-seitig
  erst nach `auto_addr:start` neu (MOTOR-INIT-BEFUND §2). Keine persistenten
  Pi-seitigen Änderungen an den MCUs außer flüchtigen RAM-Params. Werks-Configs
  (F008*) und Live-Configs als Backup behalten.

## 6. Offene Punkte (priorisiert, Aufwand S/M/L)

1. `[k2_motor]` am Gerät verifizieren (P0 Strapping→P9 ready, Zeiten, transparent-E
   0x81 statt 0x85, `query_gpio_status`-Rücklesung, Upstream-API-Signaturen,
   `duplicate_pin_override`-Syntax, RS485-Queue mit CFS/Belt) – Entwurf vorhanden
   (pi-host, 9/9 offline), Hardware-Test fehlt (pi-host/README §4/§6). Aufwand M.
2. Klipper-Modul `[k2_box]` + 485-Manager (eine Queue/ein Lock, `transact_full`
   mit STATUS, `FUNC_RETRIES` 0x10/0x11=0, lange Timeouts 0x0d-op2/3/0x10/0x11,
   keine Bursts) auf Basis nachbau/`serial485.py`+`box.py`+`cfsseq.py`+`auto_addr.py`
   schreiben; M8200/CR_BOX_*-Kette darauf abbilden. Aufwand L.
3. PrTouch-Ersatz (`config/start/read/stop_prtouch_*` + `bed_mesh`-Anbindung +
   Temperaturkompensation aus `[prtouch_v3]`-Params) – ohne ihn kein Z-Homing,
   kein Mesh, kein Druck. Aufwand L (größter Blocker nach Motor/Box).
4. Belt-Modul portieren (`belt_mdl.py` + Transport + G-Codes BELT_*; Vorsicht:
   `BELT_MDL_CALI` nur ohne Riemen). Aufwand S.
5. Homing/`homing_override` ohne Creality-MOTOR_* neu schreiben (Stall-Mode 0x11,
   Kp-Umschaltung ID 70, Protection-Check) auf Basis `[k2_motor]`; Cut-Kalibrierung
   danach. Aufwand M.
6. UART-Abgriff Main/Düse physisch lokalisieren (TX/RX/GND, 3,3 V, 230400) +
   CFS-Stecker/A/B-Polarität messen + stabile by-id-Pfade dokumentieren. Aufwand S
   (aber Voraussetzung für alles).
7. `[z_align]`-Ersatz oder bewusst nur `[z_tilt]`; `[lis2dw]`-Syntax am
   Upstream-Quellbaum gegenprüfen; `fancheck`-/Rack-/Power-Loss-/AI-/Display-Pfade
   als optional zurückstellen. Aufwand S–M.
8. Dauer-Themen aus Befunden: Motor idx 15/16, Fehlerbit-11-Quelle, Gain·R-Stromskala,
   0x13–0x16-Union bei err≠0, CFS-Config #6 (0x0c/0x50 nie senden), 0x20/0x22,
   Typ-7-Tag-Sperrbit-Zweck, Treiber-RX-Puffer, Host-Burst-Timing (CHECKLISTE.md
   Review 2 + BEFUND2-Reste). Aufwand S (Recherche) – nicht boot-blockierend.
