# K2 Plus CFS – Reverse Engineering & Python-Nachbau

Offene Dokumentation und lauffähiger Python-Nachbau des proprietären
RS485-Protokolls zwischen Creality K2 Plus (Upper-Board, Allwinner T113)
und seinen Lower-Boards (CFS-Filamentbox, Closed-loop-Motoren, Belt-Module).

Stand: Firmware 1.1.7.0, alles aus Live-Mitschnitten und Ghidra-Analysen belegt.
Lizenz: GPL-3.0-or-later (siehe `LICENSE`), Herkunft Dritter in `NOTICES.md`.

## Inhalt

| Pfad | Was |
|---|---|
| `PROTOKOLL-485.md` | RS485-Bus im Detail: Frames, CRC, Func-Codes, Abläufe |
| `PROTOKOLL-GESAMT.md` | Gesamt-Inventar: Bus, Updates, PrTouch, Motoren, Cloud |
| `API-box_wrapper.md` | Live-introspektierte API-Fläche der CFS-`.so` |
| `AUDIT-UND-AENDERUNGEN.md` | Audit-Protokoll aller Korrekturen mit Begründung |
| `CHECKLISTE.md` | Projektstatus (erledigt/offen) |
| `LIVE-ABFRAGEN-2026-10-16.md` | Live-Abfragen Motor X 16.10.2026: 0x0F Version, 0x08 Variablen-Scan, Pin-IO, Host-Crash-Warnung, Skalierung pos_fdb/idx 11 (Messfahrt) |
| `REA-MOTOR-BEFUND-2026-10-16.md` | Statische Analyse `motor_control_wrapper.so`: Motor_cmd-Tabelle, 0x08-Indexnamen, Typkette, Crash-Ursache, Fehlerbits |
| `MOTOR-FW-BEFUND-2026-10-16.md` | Statische Analyse der Motor-Firmware (WCH RISC-V): Befehlsregistrierung, STATUS-Bitfeld, 0x08-Tabelle idx 0..18, 0x0C-Layout, 0x13–0x16 |
| `MOTOR-INIT-BEFUND-2026-10-16.md` | Statische Rekonstruktion der Stock-Motor-Init (`set_motor_pin`): Ablauf, Fehlerzweige, Param-Abgleich, Adress-Strapping, Vorgaben für den Pi-Ersatz |
| `GHIDRA-CFS-BEFUND-2026-10-15.md` | Ghidra-Neuanalyse der CFS-FW R153: Dispatcher, Func-/Status-Tabelle, 0x0a-Layout |
| `GHIDRA-CFS-BEFUND2-2026-10-16.md` | Zweite Ghidra-Runde: Temp/Feuchte, 0x15, Statuscodes 0x50–0x52, Motorblock-Flags, Auto-Addr-Box-Seite, Sensor-Pins |
| `nachbau/` | Python-Module: `serial485`, `boxmini`, `box`, `motor_mini`, `motor`, `cfsseq`, `auto_addr` – alle mit Selbsttests; `cfswatch` = nur lesender Live-Monitor für `klippy.log`; `motor_init` = Plan/Trockenlauf/Log-Abgleich der Motor-Init |
| `nachbau/ref-hi/` | Unabhängige Hi-CFS-Testvektoren (CRC/Frames) zur Kreuzvalidierung |
| `fw/`, `klippy/`, `config/`, `rootfs-live/` | Live-Artefakte vom Drucker (1.1.7.0) |
| `ghidra-scripts/` | Ghidra-Skripte (CFS-Import mit korrigierter Basis, Dispatcher-/TBB-Reparatur `CfsDispatch`, Funktionslisten, CRC-Suche) |
| `live-1.1.7.0-configs/` | Live-`printer.cfg`/`box.cfg` (anonymisiert) |

## Schnellstart Nachbau

```sh
cd nachbau
for f in serial485 boxmini motor_mini cfsseq auto_addr box motor; do
  python3 "$f.py"   # jeder Selbsttest muss mit Exit-Code 0 enden
done
python3 cfswatch.py --selftest
python3 motor_init.py --selftest
```

Motor-Init (sendet nie): `python3 motor_init.py --dry-run` zeigt die Init-Sequenz aus der Live-Config,
`python3 motor_init.py --compare <klippy.log>` gleicht sie mit einem Boot-Log ab.

Live-Monitor (sendet nie, öffnet keinen Port, maskiert SN/UUID/Vendor):

```sh
ssh root@<drucker> 'tail -F /mnt/UDISK/printer_data/logs/klippy.log' | python3 nachbau/cfswatch.py --local
```

Echte Hardware braucht `pyserial`; der Import erfolgt erst bei Benutzung
(`Serial485(port, baud)`), die Tests laufen ohne.

Stand der Selbsttests (16.10.2026 nach der Live-Prüfung 0x13–0x16, `ok`-Zeilen, 0 FAIL):
`serial485` 93/93 (inkl. ref-hi: 16 CRC-Vektoren + 12 Nachrichten), `box` 179,
`boxmini` 17, `cfsseq` 28, `motor` 208, `motor_mini` 64, `auto_addr` 33,
`cfswatch --selftest` OK; `motor_init --selftest` 66 Prüfungen ok (16.10.2026).

> **WARNUNG – Creality-Host-Bug:** `MOTOR_GET … DATA=0` und `MOTOR_GET … DATA=13` lassen
> `motor_control_wrapper.so` intern scheitern (`key60: Internal error on command:MOTOR_GET`)
> → **Klipper geht in SHUTDOWN** (16.10.2026 zweimal reproduziert; DATA=0 crasht, obwohl der
> G-Code-Parser es ablehnt). Recovery: `FIRMWARE_RESTART`, danach ~60 s bis Motoren/Box bereit.
> Gültig sind nur **DATA=1..12**. Empfehlung: Motor-G-Codes nie frei über Moonraker senden,
> sondern über eine Whitelist, die nur lesende Befehle und belegte Index-Bereiche durchlässt.
> **Ursache belegt (Traceback):** `motor_get` Z. 1982 → `parse_num_to_byte(None)` → `TypeError` –
> Creality-`gcode.py` lehnt den Bereich nur mit Warnung ab (`get_int` → None); es geht **kein
> Frame** auf den Bus.

Skalierung Positionsrückmeldung – live belegt 16.10.2026 (Messfahrt X nach `G28`, Bett leer;
Details: `LIVE-ABFRAGEN-2026-10-16.md`, `PROTOKOLL-485.md` §3.1 „NACHTRAG (3)“):

- `0x08` idx 11 = **Single-Turn-Position 0..3199 Counts/Umdrehung** (3200 = 200 Vollschritte ×
  16 Microsteps; 80 Counts/mm bei rotation_distance 40) – Nachbau-Name `pos_counts` (eigener
  Name, Host-Name unbekannt) → `read_position_counts()`.
- idx 5 `pos_fdb` = idx11 · 2π/3200 **rad**. Beide wrappen alle 40 mm (eine Umdrehung) – eine
  absolute Achsposition liefert nur Klipper (Homing + Schrittzähler). Helfer `counts_to_mm`,
  `counts_delta` (wrap-korrigiert), `read_position_feedback(with_counts=True)`.
- `spd_fdb` (idx 6) im Stillstand ≈ 0; Einheit rad/s (seit Motor-FW-Befund). Hinweis: Creality-Klipper verlangt
  für `G1` alle Achsen referenziert (`G28 X Y` reicht nicht).

Motor-Init-Befund 16.10.2026 (statische Analyse der Stock-Startsequenz, mit Live-Log-Gegenprobe und
einer Zweitanalyse durch einen weiteren KI-Agenten; Details: `MOTOR-INIT-BEFUND-2026-10-16.md`):

- `Motor_Control.set_motor_pin` = gesamte Boot-Motorsequenz: Dir/Step-Pins setzen → Broadcast `FF 0E 01`
  → je Motor `0E 02` (max. 4 Runden) → Stall-Mode Z/Z1/E → `0C [0b]`/`0C [05]` → Pins zurück auf 0 →
  Sys-Param-Abgleich → `auto_addr:start` (CFS-Auto-Addr erst danach). Fehlschlag: key798 + SHUTDOWN.
- **Adress-Strapping belegt:** Die Motor-FW bildet die Adresse aus den DIR/STEP-Pegeln
  (0x81 + 2·STEP + DIR → X 0x81, Y 0x82, Z 0x83, Z1 0x84).
- Param-Abgleich (0x06): nur Config-Schlüssel `<motor>_<param>`, deren ID `configurable` ist (61 von 227),
  plus IDs 9/10/25 lesend; Toleranz `abs_tol=1e-6`; Werte flüchtig → nach jedem Kaltstart neu schreiben.
- `0x12 [02]` (UUID) ist **kein** Init-Schritt, sondern ein Webhook-Poll (`motor_dev_uuid`).
- Stock-Bug: Der Param-Abgleich spricht den Extruder als 0x85 auf ttyS5 an (nur Timeouts), statt über
  den transparent-Pfad der Düsen-MCU.
- Nachbau `nachbau/motor_init.py`: Plan, Trockenlauf und Boot-Log-Abgleich; gegen 10 Live-Boots ohne
  Abweichung. Offen: GPIO-Phase über die Klipper-MCU, transparent-Pfad (Extruder), echter Lauf.

Motor-FW-Befund 16.10.2026 (statische Analyse der Motor-Firmware selbst, Details:
`MOTOR-FW-BEFUND-2026-10-16.md`, `PROTOKOLL-485.md` §3.1 „NACHTRAG (4)“):

- Motor-FW `fw/F008|F012/mot*_022_C30-mot2_002_081.bin` gefunden: **WCH RISC-V (CH32V30x-Klasse)**,
  App-Basis 0x3000 hinter 12-KB-Bootloader, Befehlsregistrierung @0x6100–0x63d8 (0x11 heißt dort
  `emergency_pin_mode`). Das STATUS-Byte jeder Motorantwort ist ein Bitfeld: bit0 Stall, bit1
  Fehler latched, bit2 Warnung latched → `decode_motor_status()`.
- **0x08-Variablen-Tabelle idx 0..18** komplett (`motor.VARIABLE_TABLE` mit Einheit und Belegstatus):
  1–4 `pos_ref`/`spd_ref`/`id_ref`/`iq_ref`, 5/6 LESO-Beobachter (`pos_fdb` rad, `spd_fdb` **rad/s**),
  7/8 `id_fdb`/`iq_fdb`, 9/10 Phasenströme Ia/−Ib [A], 11 Schrittpuls-Zähler, 12 DIR-Eingang,
  13 EN (vermutet), 14 Stall-Flag, 17 °C, 18 V; 15/16 offen. Helfer `read_reference_values()`,
  `read_phase_currents()`, `read_dir_input()`. `read_variable` bleibt 1..12 (Host-Crash);
  0/13..18 nur `read_variable_raw_fw(idx, confirm=True)`.
- **DIR-Eingang live bestätigt:** idx 12 = 0 bei Fahrt −X, 1 bei +X, hält im Stillstand die
  letzte Richtung.
- **0x0C [0b]:** Byte 0–3 Fehler, Byte 4–7 Warnung, je u32 Little-Endian → `check_protection()`
  liefert jetzt Fehler-/Warnbits im Klartext. Sub-Codes 01–05/07/09 (zustandsändernd bzw.
  Seiteneffekt Notfall-Pin) nur als Konstanten.
- **0x13–0x16** lesend: Fehler-/Stall-Snapshot (74 B) und Probenringe (`0A` + 10 × 4 float) →
  `read_err_detail()`, `read_stall_detail()`, `read_pos_samples()`, `read_spd_samples()` –
  FW-statisch belegt und **live bestätigt 16.10.2026** (Motor X, über die Klipper-Webhook-Endpunkte
  `motor_err_detail_data`/`motor_cut_detail_data`; Request `81 04 00 1x 02`, Host-Dekodierung =
  Nachbau; 0x14-Zusatzfloat `param_peak_overcurrent_A`; Details `PROTOKOLL-485.md` §3.1 „NACHTRAG (5)“).

REA-Motor-Befund 16.10.2026 (statische Analyse `motor_control_wrapper.so`, Details:
`REA-MOTOR-BEFUND-2026-10-16.md`, `PROTOKOLL-485.md` §3.1 „NACHTRAG (2)“):

- `0x08`-Indizes 5–8 = `pos_fdb`, `spd_fdb`, `id_fdb`, `iq_fdb` (Positions-, Drehzahl-, d-/q-Strom-
  Rückmeldung) → `read_position_feedback()`, `read_speed_feedback()`, `read_current_feedback()`;
  1–4/9–12 ohne Host-Namen (Deutung seit dem Motor-FW-Befund belegt, s. oben).
  Typ fest im Host: 1–10 float, 11/12 `uint32`.
- Vollständige Host-Tabelle `Motor_cmd` (0x01–0x16) als Konstanten; ungeprüfte Kommandos
  (0x01, 0x0B, 0x0D u. a.) nur als Stubs, die nichts senden (0x13–0x16 s. Motor-FW-Befund).
- `MOTOR_GET … DATA=7/8 EXTRUDER=1` im Stock = Extruder-Überstrom-Monitoring (|I| = hypot(id, iq)).

Live-Abfragen 16.10.2026 (Motor X 0x81, Standby, nur lesende Creality-G-Codes über Moonraker;
Details: `LIVE-ABFRAGEN-2026-10-16.md`, `PROTOKOLL-485.md` §3.1):

- `0x0F [00]` = Motor-Version, 12 B: fireware 1.9.7.81, cmd_system 1.3, protection 1.3,
  flash_param 2.75, logger 1.0 → `query_motor_version()`.
- `0x08 [idx]` = Variablen-Lesepfad, 4 B LE, idx 1..12 (Host: 1–10 float, 11/12 int;
  Deutung je Index s. Motor-FW-Befund) → `read_variable(idx)` lehnt alles außerhalb 1..12 ohne Senden ab;
  `read_encoder` bleibt gesperrt. `cfswatch` dekodiert beide und warnt bei idx außerhalb 1..12.
- `MOTOR_READ_ALL_PIN_IO` läuft **nicht** über RS485, sondern als GPIO-Abfrage an der
  Main-MCU (PB9, PB10, !PB7, PB8, PB5, PB6, PA1, PB15) – ein Pi-Ersatz braucht dafür weiter
  die Main-MCU.

Ghidra-BEFUND2 16.10.2026 (Details: `GHIDRA-CFS-BEFUND2-2026-10-16.md`):

- 0x0a Byte 0/1 = Temperatur °C / rel. Feuchte % (AHT30) belegt; Byte 2 = Motorblock-Flags
  (Bit 7 > 10 s, Bit 4 Hauptmotor, Bit 0–3 Feeder); Byte 5 = Typ-7-Tag-Sperre (kein „RFID gültig“).
- 0x15 = Init-Flags der 6 Komponenten (EEPROM, AHT30, Odometer, Buffer-Sensor, 2 RFID-Leser).
- Statuscodes: 0x50 = Runout im Druck-Slot, 0x51 = SPEED_ERR (print_check), 0x52 = kein Vorschub.
- Kanal-A-Diagnose: dauerhafte „Hub-Maske A“ in 0x0a = Hub-Sensor A blockiert B/C/D
  (Filamentrest/Sensor prüfen, danach muss die Hub-Maske leer sein; `cfswatch` zeigt das).

Review 2 (16.10.2026, Details: `AUDIT-UND-AENDERUNGEN.md`):

- `serial485`: Antworten müssen vom angesprochenen Gerät kommen (Absenderprüfung, Broadcasts
  ausgenommen); `transact_full` liefert das Status-Byte; Bus-Lock-Haltedauer begrenzt.
- `box`: Status-Byte wird ausgewertet (`CfsStatusError`, `raise_on_status`); Extrude/Retrude
  (0x10/0x11) bewusst **ohne** Wiederholung, damit ein Timeout die Mechanik nicht erneut auslöst.
- `motor`/`motor_mini`: F0-Update und Parameter-Schreiben nur mit `confirm=True`
  (gesperrte IDs zusätzlich `allow_locked=True`).

Live-Gegenprobe 15.10.2026 (Drucker-Logs `klippy.log` 05.–16.10., nur lesend geholt;
die Logs selbst sind nicht im Repo, Details: `PROTOKOLL-485.md` §3.1 und §6):

- Retry-Semantik belegt: `retries = 4` ⇒ 1 + 4 = 5 Sendungen im 2-s-Abstand (Zähler
  zählt herunter); 0x0a-Box-Poll und 0xa2-Auto-Addr laufen mit `retries = 0`.
  Nachbau: `box.FUNC_RETRIES`/`func_retries`, Motoren durchgehend `retries = 0`.
- Motor-Lesebefehle (live belegt) freigegeben: Sys-Param lesen `0x06 [02, id]`,
  Schutzstatus `0x0C [0b]`, Stall-Mode `0x11 [04]`, Adresse `0x0E [02]`, UUID `0x12 [02]`.
- Schreibende Motor-Kommandos (`0x06 [01, id] + float`) senden **nur mit
  `confirm=True`** (sonst `PermissionError`, kein Frame); `read_encoder` bleibt gesperrt.
- Stock-Frames werden von `serial485.build_frame` byte-genau reproduziert, alle
  Motor-RX-Frames CRC-OK.

Ghidra-Neuanalyse 15.10.2026 (Details: `GHIDRA-CFS-BEFUND-2026-10-15.md`):

- Ghidra-Basis korrigiert: Bootloader 12 KiB @`0x08000000`, CFS-App `cfs0` @`0x08003000`,
  `cfs6` @`0x08004000` (vorher fälschlich `0x08000000` → fehlende XRefs).
- Func-Tabelle aus der CFS-FW R153 belegt (Dispatcher, Längen, Status-Werte, 0x0a-Box-State):
  `0x0f` = Auto-Preload-Sperre (kein Motor-Engage/Release), `0x0d` op 3 = Forced Preload
  ohne RFID (Antwort erst nach dem Vorgang, bis ~3 min je Slot), `0x10` mode 5 =
  Odometer-Abfrage, mode 7 = Extruder-Check.
- RFID-vender (40 Zeichen) per `box.split_rfid_vendor()` in Felder zerlegt.

Wichtige Korrekturen aus dem Review 15.10.2026 (Details: `AUDIT-UND-AENDERUNGEN.md`):

- Motor-Func `0x06` ist **kein** Encoder-Lesen, sondern Sys-Param (LE-Float
  300.0/100.0 = `MOTOR_SYS_PARAM ID=70`); `read_encoder` ist gesperrt.
- CFS-Preload `0x0d [Maske][Aktion]`: `0x00` = disable, `0x01` = enable,
  `0x02` = Force-RFID-Read (die frühere Arm/Disarm/Rearm-Belegung ist widerlegt).
- Slot-Parameter sind Masken (`check_mask`, `slot_to_mask`), Remain `0` = kein
  RFID-Restwert (nicht „leer“).

## Anonymisierung

IP, Hostname, Geräte-SN, RFID-Vendor-Bytes, CFS-UUID und Motor-UUIDs sind anonymisiert
(syntaktisch gültige Platzhalter gleicher Länge, CRCs neu berechnet und
verifiziert). Details in `AUDIT-UND-AENDERUNGEN.md`.

## English summary

Open documentation and a working Python reimplementation of the proprietary
RS485 protocol between the Creality K2 Plus host board and its lower boards
(CFS filament box, closed-loop motors, belt modules). All findings are backed
by live captures and Ghidra analysis. License: GPL-3.0-or-later.

Status 16.10.2026: all self-tests green (serial485 93/93 incl. Hi reference
vectors, box 179, boxmini 17, cfsseq 28, motor 208, motor_mini 64, auto_addr 33,
`cfswatch.py --selftest` OK).

Stock motor init reconstructed statically (16.10.2026, `MOTOR-INIT-BEFUND-2026-10-16.md`, German;
cross-checked against boot logs and a second analysis by another AI agent): `set_motor_pin` sets the
DIR/STEP pins, broadcasts `FF 0E 01`, reads `0E 02` per motor (up to 4 rounds), sets the stall mode for
Z/Z1/E, queries/clears protection (`0C [0b]`/`[05]`), restores the pins to 0, then syncs the volatile
system parameters (`0x06`, only configurable IDs set in the config plus IDs 9/10/25, tolerance
`abs_tol=1e-6`) and finally fires `auto_addr:start`. The motor firmware derives its bus address from the
pin levels (**address strapping**: 0x81 + 2·STEP + DIR). `0x12 [02]` (UUID) is a webhook poll, not part
of the init. Stock bug: the parameter sync addresses the extruder as 0x85 on ttyS5 instead of using the
nozzle-MCU transparent path. `nachbau/motor_init.py` rebuilds the sequence as a plan with dry run and
boot-log comparison (matches all 10 captured boots; never sends without explicit confirmation).

Motor firmware found and analysed statically (16.10.2026, `MOTOR-FW-BEFUND-2026-10-16.md`,
German): `fw/F008|F012/mot*_022_C30-mot2_002_081.bin` is a **WCH RISC-V (CH32V30x-class)**
image, app base 0x3000 behind a 12 KB bootloader, command registration at 0x6100–0x63d8. The
STATUS byte of every motor reply is a bit field (bit0 stall, bit1 latched error, bit2 latched
warning; `decode_motor_status()`). The full `0x08` variable table idx 0..18 is now known
(`motor.VARIABLE_TABLE` with units and evidence level): 1–4 position/speed/id/iq references,
5/6 LESO observer (`pos_fdb` rad, `spd_fdb` **rad/s**), 7/8 id/iq feedback, 9/10 phase currents
Ia/−Ib [A], 11 step-pulse counter, 12 DIR input level, 13 EN (assumed), 14 stall flag, 17 °C,
18 V; 15/16 unknown. **DIR input confirmed live:** idx 12 reads 0 when moving −X, 1 when moving
+X and keeps the last direction at standstill. `0x0C [0b]` returns the latched error code
(bytes 0–3) and warning code (bytes 4–7), each u32 little-endian; `check_protection()` now
decodes them. `0x13`–`0x16` are read-only without request payload (74-byte error/stall
snapshots, 10-sample position/speed rings) → `read_err_detail()`, `read_stall_detail()`,
`read_pos_samples()`, `read_spd_samples()` – statically derived and **verified live on
16.10.2026** (motor X via the Klipper webhook endpoints `motor_err_detail_data` /
`motor_cut_detail_data`; request `81 04 00 1x 02`, host JSON matches the decoder).
`read_variable()` stays limited to 1..12 (host crash); idx 0/13..18 only via
`read_variable_raw_fw(idx, confirm=True)`.

**WARNING – Creality host bug:** `MOTOR_GET … DATA=0` and `MOTOR_GET … DATA=13` make
`motor_control_wrapper.so` fail internally (`key60`) and put **Klipper into SHUTDOWN**
(reproduced twice on 16.10.2026; recover with `FIRMWARE_RESTART`). Only `DATA=1..12` is
valid. Never send motor G-codes freely via Moonraker – use a whitelist that only lets
read-only commands and known index ranges through.
Root cause confirmed by traceback: `motor_get` line 1982 → `parse_num_to_byte(None)` →
`TypeError`; Creality's `gcode.py` only warns on out-of-range values (`get_int` returns None),
so the host crashes **before** any frame is sent.

Position feedback scaling, measured live 16.10.2026 (axis X move after `G28`, empty bed;
`LIVE-ABFRAGEN-2026-10-16.md`, German): `0x08` idx 11 is a **single-turn position of
0..3199 counts per revolution** (3200 = 200 full steps × 16 microsteps, 80 counts/mm at
rotation_distance 40; our name `pos_counts`, the host name is unknown) and idx 5 `pos_fdb` =
idx11 · 2π/3200 **rad**. Both wrap every 40 mm, so the absolute axis position still comes only
from Klipper's step counter. Helpers: `read_position_counts()`, `counts_to_mm()`,
`counts_delta()`, `read_position_feedback(with_counts=True)`. The unit of `spd_fdb` is rad/s
(motor firmware analysis, see above).

Static analysis of `motor_control_wrapper.so` (16.10.2026, `REA-MOTOR-BEFUND-2026-10-16.md`,
German): `0x08` indices 5–8 are `pos_fdb`, `spd_fdb`, `id_fdb`, `iq_fdb` (FOC position, speed and
d/q current feedback; position scaling now measured, see above) → `read_position_feedback()`, `read_speed_feedback()`,
`read_current_feedback()`. The host decodes indices 1–10/17/18 as float and 11–16 as uint32.
The full host command table `Motor_cmd` (0x01–0x16) is included as constants; unverified
commands (0x01, 0x0B, 0x0D etc.) are stubs that never transmit (0x13–0x16: see the motor
firmware analysis above). The stock
`MOTOR_GET … DATA=7/8 EXTRUDER=1` calls are extruder over-current monitoring.

Live queries 16.10.2026 (motor X 0x81, idle, read-only stock G-codes via Moonraker;
`LIVE-ABFRAGEN-2026-10-16.md`, German): `0x0F [00]` returns the 12-byte motor version
block (firmware 1.9.7.81, cmd_system 1.3, protection 1.3, flash_param 2.75, logger 1.0,
`query_motor_version()`); `0x08 [idx]` reads a 4-byte LE system variable for idx 1..12
(meaning per index: see the motor firmware analysis; `read_variable()` rejects other indices without sending,
`read_encoder` stays disabled); `MOTOR_READ_ALL_PIN_IO` is a GPIO query on the main MCU,
not an RS485 command.

New: `nachbau/cfswatch.py`, a read-only live monitor for `klippy.log` (never
transmits, masks SN/UUID/vendor). Second Ghidra pass (`GHIDRA-CFS-BEFUND2-2026-10-16.md`):
box-state bytes 0/1 are temperature/humidity (AHT30), byte 2 holds motor-block flags,
0x15 reports the init flags of six components, status 0x50 = runout, 0x51 = speed
error, 0x52 = no feed; a persistent hub mask "A" means hub sensor A blocks channels
B–D. Review 2 hardening: replies are matched by sender address, the status byte is
evaluated, extrude/retrude are sent without retries, and motor F0 updates / parameter
writes require `confirm=True`.

Live cross-check 15.10.2026 against the printer's own logs (klippy.log, read-only;
logs not included in the repo): retry semantics confirmed (`retries = 4` means
1 + 4 = 5 transmissions 2 s apart; box poll 0x0a and auto-addr 0xa2 use
`retries = 0`; all motor commands use `retries = 0`). Motor read commands observed
live are now enabled (sys-param read `0x06 [02, id]`, protection `0x0C [0b]`,
stall mode `0x11 [04]`, address `0x0E [02]`, UUID `0x12 [02]`); the parameter write
`0x06 [01, id] + float` only transmits with `confirm=True`, otherwise it raises
`PermissionError` without sending.
Corrections: motor func `0x06` is a system-parameter write (LE float), not an
encoder read (`read_encoder` disabled); CFS preload `0x0d` actions are
`0x00` disable / `0x01` enable / `0x02` force RFID read; slot arguments are
bit masks; remain `0` means "no RFID remain value", not "empty".

Ghidra re-analysis 15.10.2026 (`GHIDRA-CFS-BEFUND-2026-10-15.md`, German): the
Ghidra load base was corrected (bootloader 12 KiB at `0x08000000`, CFS app at
`0x08003000`, `cfs6` at `0x08004000`); the function table is now backed by the
CFS firmware R153 dispatcher (lengths, status codes, box-state layout).
`0x0f` is the auto-preload inhibit flag (not motor engage/release), `0x0d` op 3
is a forced preload without RFID; the 40-char RFID vendor string is split into
its fields by `box.split_rfid_vendor()`.
