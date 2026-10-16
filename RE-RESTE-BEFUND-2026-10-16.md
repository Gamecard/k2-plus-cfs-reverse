# Befund: Offene Reverse-Engineering-Punkte (statisch, 16.10.2026)

> Sammelbefund (statisch, weiterer KI-Agent, von uns gesichtet). Pfade zu Arbeitsdateien: lokal, nicht im Repo.


Arbeitsordner: `<projekt>/lokale Arbeitsdateien `
Stand: 16.10.2026. NUR statische Analyse (kein Netzwerk, kein ssh/curl, kein Druckerzugriff).
Alles andere nur gelesen, geschrieben nur diese Datei.

Methoden: `python3` (Binär-Scans, zlib-Re-Extraktion), `/usr/bin/strings -a`,
`llvm-mc --disassemble` (Spot-Checks; ANMERKUNG: System-`objdump` ist x86-only,
`llvm-objdump` liest kein Raw-Binary — `llvm-mc` mit Hex-Input wurde als Ersatz
für die im Auftrag genannten `llvm-objdump`-Prüfungen verwendet), Ghidra-Vorarbeiten
nur gelesen (kein neuer Headless-Lauf nötig, Dispatcher-`.c`-Dateien vorhanden).

Belegstärken: **B** = im Dekompilat/Disassembly/String mit Adresse belegt,
**B+L** = zusätzlich live-gegengeprüft (zitiert, nicht selbst gemessen),
**V** = Deutung, **ANNAHME** = unbelegt.

## 1. CFS-Bootloader: Adresse 0xF0 und Längengrenze len ≤ 0xFC

Ergebnis:
- App-Seite GEKLÄRT: `FUN_0800f880` akzeptiert nur `{eigene, 0xEA, 0xEB}`
  (Ghidra-Befund `arbeitsdateien-lokal/ghidra-cfs/BEFUND.md` §1, Dispatcher
  `dispatcher_cfs0_153.c`; bestätigt in `GHIDRA-CFS-BEFUND2-2026-10-16.md` §6).
  `0xFF`/`0xFE`/`0xF0` kommen in der App nur im toten `FUN_0800a92c`
  bzw. gar nicht vor. Ein App-Kommando an `0xF0` wird verworfen.
- Bootloader-Seite OFFEN: Die Aussage „Bootloader akzeptiert `0xFF/0xFE/eigene/0xF0`,
  len ≤ 0xFC" stammt aus `PROTOKOLL-485.md` § „Flash-Map, Bootloader, Shell"
  („stark, Disassembly des Bootloader-Dumps durch unseren Agent"). Der Dump selbst
  liegt im Projekt NICHT vor (gesucht mit `find -iname *boot*/*dump*`: nur
  `lokale Arbeitsdateien tmp/scripts/PrtouchDump.java`; `fw/cfs/` enthält nur die Apps
  `cfs0_050_G32-cfs0_000_153.bin` ab `0x08003000` und `cfs6_…` ab `0x08004000`,
  kein 12-KiB-Bootloader `@0x08000000`). Eigene Prüfung: App-Binär enthält keinen
  `0xF0`-Adressvergleich (Spot-Disassembly RX `0x0800f880` via `llvm-mc` zeigt nur
  die bekannte Empfangs-State-Machine; Adresskonstanten `0xEA/0xEB` aus Dispatcher).
- WICHTIGE ENTFLECHTUNG (B): „0xF0" existiert ZWEIMAL mit verschiedener Bedeutung:
  (a) Bootloader-**Adresse** `0xF0` (oben, unbelegt), (b) **Func-Code** `0xF0`
  = 485-Firmware-Update (Host `mcu_util_485`, Subs `00/01/02/03/06`,
  `PROTOKOLL-GESAMT.md` §1.7, Strings in `rootfs-live/usr/bin/mcu_util_485`:
  `get_version/get_sector_size/erase_flash/update_request/update_end`,
  `erase private flash sector`). Beides vermischen die Quellen gern — trennen!
- len ≤ 0xFC: OFFEN. Nur notiert (`PROTOKOLL-485.md`: „serial485 prüft bis 255 –
  nur notiert"), kein Code-Beleg im App-Binär (RX nimmt `len > 2` an, keine
  0xFC-Konstante in `dispatcher_cfs0_153.c` gefunden).

Belege: `PROTOKOLL-485.md` Z. ~321f, ~547–575, ~1392–1397; `PROTOKOLL-GESAMT.md`
§1.7; `ghidra-cfs/BEFUND.md` §1; `ghidra-cfs2/BEFUND2.md` §6; `fw/cfs/`-Inhalt;
`rootfs-live/usr/bin/mcu_util_485` (ELF32 ARM, Strings s. o.).

Einstufung: **TEILWEISE** (App-Seite GEKLÄRT, Bootloader-Adresse 0xF0 + len 0xFC OFFEN).

Live-Test (falls je nötig): Bootloader-Bereich `0x08000000–0x08002FFF` per SWD/J-Link
auslesen (finsh nur via RTT/SWD, nicht RS485 — `PROTOKOLL-485.md` finsh-Abschnitt)
und Parser erneut disassemblieren; NICHT per Bus raten (0x50/0x56 resetten!).
Alternativ `mcu_util_485`-Update-Verkehr mitschneiden — betrifft aber Func-0xF0,
nicht Adresse-0xF0.

## 2. CFS Config #6, Fehlercodes 0x20/0x22, Typ-7-Sperrbit/0x5c, 0x0d-Mehrfachmaske

### 2a. Config #6 (0x0c lesen / 0x50 schreiben + Reset)

Ergebnis:
- Mechanik GEKLÄRT (B, `dispatcher_cfs0_153.c` Z. ~378–410): `0x0c` (len 3, keine
  Daten) → `FUN_0800f124` → `FUN_08011d82(6)` liefert 1 Byte (eigene Spot-Prüfung
  `0x08011d82` via `llvm-mc`: Tabellen-Zugriff `add r0,r1,r0,lsl#4` + indirekt —
  passt zu Config-Tabelle). `0x50` (len 4, 1 B Wert) → `FUN_08011d82(6)` lesen,
  bei gleichem Wert Status 3 ohne Schreiben, sonst `FUN_08011db4(6,…)` +
  `FUN_08011df0()` (speichern, Spot-Prüfung `0x08011df0`: Schleife mit EEPROM-Write-
  Mustern) + `FUN_08016388` (Reset-Verdacht, V) via Label `LAB_0800f6f8`.
  Adress-Sonderfall: bei `0xEA` keine Antwort auf 0x50, bei `0xEB` Antwort mit
  Absender `0xEB` (BEFUND2 §6).
- Bedeutung des Werts OFFEN: welche Geräte-Option #6 ist, steht nirgends
  (kein Name, kein EEPROM-Layout `@0x0807F800`/24C16 dekodiert, kein Live-Frame —
  zu Recht nie gesendet, da Reset auslösend).

Belege: `dispatcher_cfs0_153.c` 0x0c/0x50-Zeilen + Dekompilat-Auszug;
`ghidra-cfs/BEFUND.md` §2 (0x0c/0x50-Zeilen); `PROTOKOLL-485.md` Z. ~350/361.

Einstufung: **TEILWEISE** (Mechanik GEKLÄRT, Semantik OFFEN).
Live-Test: keiner (bewusst NICHT senden); allenfalls EEPROM-Page `0x0807F800`
per SWD lesen und Byte-Offset von #6 bestimmen.

### 2b. Fehlercodes 0x20/0x22

Ergebnis: OFFEN. Einziger Beleg ist das LED-Mapping `FUN_08010c8a`
(`ghidra-cfs/BEFUND.md` §5: „Weitere Fehlercodes … 0x1a, 0x20, 0x22, 0x51, 0x52,
0x53. Ihre Quellen sind nicht verfolgt"). Eigene String-Suche im cfs0-Binär nach
`led … err` findet nur `motor_id = %d, led set err %x @0x800de1b` (B), keine
0x20/0x22-Erzeuger. BEFUND2 §4 klärt 0x50/0x51/0x52 (Runout/print_check) und
0x1a (Pull-back 40 s), 0x53 als nie erzeugt — 0x20/0x22 bleiben die einzigen
LED-only-Codes ohne Erzeuger.

Belege: `ghidra-cfs/BEFUND.md` §5-Z. 210; `GHIDRA-CFS-BEFUND2-2026-10-16.md` §4;
eigener String-Scan (`0xde1b`-Fundstelle).

Einstufung: **OFFEN**. Live-Test: Fehler provozieren (Blockaden/Runout) und
`0x0a`-Status + LED-Code loggen; oder `FUN_08010c8a`-Caller per Ghidra-XRef
verfolgen (statisch, kein Bus nötig).

### 2c. Typ-7-Tag-Sperrbit (0x0a Byte 5) und 0x5c-Auth

Ergebnis:
- Prädikat GEKLÄRT (B, `BEFUND2.md` §2): Bit i = `FUN_080053f2(i)` genau bei
  Einlauf-Präsenzflag `[0x2000391e+i]` ≠ 0 UND `rec[0x17]` ≠ 0 UND
  `rec[0x63]==7` (Tag-Typ, Log-Byte „NUM") UND `u32 rec[0x28]==0` (Tag-Feld +0x10).
  Dasselbe Prädikat sperrt die 0x02/0x03-Datenausgabe (`FUN_0800545c/0800549c`) —
  Bit≠0 und RFID-Daten schließen sich gegenseitig aus. Live immer 0 = kein Slot
  in diesem Zustand (B+L).
- Zweck ANNAHME: „verschlüsselter/noch nicht autorisierter Typ-7-Tag" (V im
  Vorbefund). Eigene Stütze (B): cfs0-Strings `card %d auth block %d failed`,
  `RFID_authentication`, `Temporary.MFrfid_fm15l013_check`, `rfid_auth_check`,
  `rfid key read ok! 0x%02x 0x%02x @0x800dbd3`; `0x5c`-Handler `FUN_0800f1e8`
  (len 0x13 = 16 B Daten, `dispatcher_cfs0_153.c` Z. 33) schiebt per Spot-
  Disassembly 16-Byte-Blöcke (`movs r3,#16`) an I2C-Gerät `0x60`
  (`FUN_0800af7e(0x60,…)`, Vorbefund) und antwortet 16 B. Ob das Byte-5-Bit
  kausal an 0x5c hängt, ist NICHT belegt (kein gemeinsamer Caller gefunden).

Belege: `ghidra-cfs2/BEFUND2.md` §2; `dispatcher_cfs0_153.c` 0x5c-Zeile;
eigene cfs0-String-Fundstellen (s. o., VMA `0x8009883` ff.).

Einstufung: **TEILWEISE** (Prädikat GEKLÄRT, Auth-Zweck + 0x5c-Kausalität OFFEN).
Live-Test: Typ-7-Spule (falls vorhanden) einlegen, 0x02/0x03/0x0a-B5 beobachten;
0x5c NIEMALS blind senden (Auth-Schreibpfad, Tag-Risiko).

### 2d. 0x0d op 2 mit Mehrfachmaske (Laufzeit je Slot)

Ergebnis: statisch GEKLÄRT (B, `ghidra-cfs/BEFUND.md` §3 + `BEFUND2.md` §5):
op 0/1 = global disable/enable (Maske ignoriert), op 2 = Forced Preload MIT RFID,
op 3 = OHNE RFID (beide `FUN_08004ec4(mask,…)`); je Slot in Maske nacheinander
5 s + bis 180 s Poll, davor ggf. 179 s Warten auf laufendes Preload und
`FUN_08004cd0(4,1)`-Pull-back aller Hub-belegten Slots in IDLE → **~3 min/Slot**,
Antwort erst danach; `retries=0` + langer Timeout nötig (`nachbau/box.py`:
`preload_force_timeout`, Maske 0 = 4 Slots/980 s; `.so SET_PRE_LOADING=300 s`).
Live: op 0/1/2 belegt (55 Requests; `[01 02]` nach 31–37 s, Status 0),
op 3 nie live, Mehrfachmaske-Laufzeit nie live (`CHECKLISTE.md` Z. 155–160,
`PROTOKOLL-485.md` §6.4/Z. ~133–142).

Belege: `ghidra-cfs/BEFUND.md` §3 (Pseudocode `FUN_0800f0da`); `BEFUND2.md` §5;
`PROTOKOLL-485.md` Z. ~133–142; `nachbau/box.py` `FUNC_TIMEOUTS_SO`/G2.

Einstufung: **TEILWEISE** (Mechanik GEKLÄRT, Mehrfachmaske-Laufzeit + op 3 live OFFEN).
Live-Test: 0x0d op 2 mit 2er-/4er-Maske (Host-Timeout ≥ 980 s, retries=0) und
Zeit bis Antwort messen; op 3 nur mit Freigabe (ohne RFID, Verhalten beachten).

## 3. cfs6-Handler 0x16–0x19/0x57 (Trockner?)

Ergebnis:
- Trockner-Hypothese stark gestützt (B): cfs6-Binär (`cfs6_100_G31-cfs6_222_000.bin`,
  Basis `0x08004000` — Reset `0x08004145`, SP `0x20008030`, vgl. `ghidra-cfs/BEFUND.md`
  §0) enthält ~90 Dry/Heat/PTC/Fan-Strings (eigener Scan, VMA z. B. `0x800607d`
  `Chamber backside fan`, `0x8006539 ptc_power`, `0x80097cc PTC heating fan stop`,
  `0x800fe10 dry <start> <ch> <temperature> <minute>`, `0x8011fb8 ptc_out_event`,
  `0x8015928 PTC FAN VCC test`, finsh `dry/heat/fan_dry/ptc_start/…`), die im
  cfs0-Binär FEHLEN (dort nur BLDC/Feeder/AHT/MT6826). cfs0 kennt 0x16–0x19/0x57
  nicht (`sonst → Status 1`, `dispatcher_cfs0_153.c`), cfs6-Dispatcher
  (`dispatcher_cfs6_222.c` Z. 14–17/20) listet sie mit Längen:
  0x16 len 4 (1 B), 0x17 len 5 (2 B), 0x18 len 9 (6 B), 0x19 len 4 (1 B),
  0x57 len 8 (5 B).
- Eigene `llvm-mc`-Spot-Prüfung (VMA→File-Off via Basis 0x08004000) bestätigt die
  Längen aus dem Prolog (`movs r0,#22/#23/#24/#25/#87` + `movs r2,#4/#5/#9/#4/#8`
  + `bl <lencheck>` an `0x0800daa0/0x0800e62c/0x0800e5cc/0x0800de38/0x0800e7e8`):
  0x17 iteriert über 4 Slots (`cmp r6,#4`, Bit-Schleife — ANNAHME: Kanal-Auswahl),
  0x18 lädt `rev r0` (BE-u16, ANNAHME: Temp/Zeit) + 4 Einzelbytes (ANNAHME:
  ch/temp/minute). Callees (Heiz-/Lüfter-Funktionen) ohne Ghidra-XRef NICHT
  zugeordnet — pro-Handler-Semantik bleibt offen.

Belege: eigene String-Scans (cfs6 vs. cfs0); `dispatcher_cfs6_222.c`;
`ghidra-cfs/BEFUND.md` §6 (fun-Liste cfs6, „Kandidaten für Trockner/Heizung: V");
eigene `llvm-mc`-Prologe (s. o.).

Einstufung: **TEILWEISE** (Trockner-Zuordnung stark belegt, Einzel-Semantik OFFEN).
Live-Test: cfs6-Hardware (Trockner-Box) am Bus mitschneiden (Requests + 0x0a/0x15-
  Verhalten während dry/heat); statisch: Ghidra-XRefs der fünf Handler auf
  `api_heat_*`/`ptc_*`/`fan_*` verfolgen.

## 4. MT6826S-Encoder: Messrad oder BLDC?

Ergebnis: OFFEN, mit Tendenz Messrad (ANNAHME).
- B: MT6826 hängt an SPI PB12–15 (cfs0-Strings `SPI_CS:PB12 @0x8008080`,
  `SPI_CK:PB13`, `SPI_MISO:PB14`, `SPI_MOSI:PB15`; `odometer_magnetic_init
  communication fail`, `… mt6826_timer create fail`, `Bodometer_magnetic current
  position:%d.%02dmm @0x800b8c3`, finsh `odo_mag init/test/monitor/read step/read mm/reset`).
  Hardware-Quelle `PROTOKOLL-485.md` § „CFS-Hardware": Board `MF003_ENCODER_V12`,
  Feeder-Baugruppe, SPI Mode 3, Reg `0x003` mit 50 Hz.
- B: Es gibt genau EIN gemeinsames Odometer für alle Slots
  (`BEFUND2.md` §8: `FUN_0800b606` ohne Slot-Index; 0x0a-Odometer = Pfad hinter
  dem Hub; 0x0e liefert float32-BE mm, op 0 nullt). Mode-4-Förderung prüft nur
  dieses Odometer (keinen Sensor).
- V/ANNAHME: Board-Name „ENCODER" in der Feeder-Baugruppe + getrennter BLDC-Tacho
  (FW-Strings `MS3791_read_fg pluse=%d`, `pGMS3791_is_blocked`, PE11-FG-Pin in
  `PROTOKOLL-485.md`) sprechen für Messrad/Hub-Pfad, nicht BLDC-Welle. Aber:
  Der mechanische Anbau (sitzt der Magnet am Messrad oder an BLDC/Feeder-Rolle?)
  ist aus dem Binär nicht ableitbar; `fake-name PCBs.md:252-256` lag nicht zur
  Prüfung vor (nur zitiert).

Belege: eigene cfs0-String-Fundstellen (s. o.); `PROTOKOLL-485.md` Z. ~88,
~592–604, ~1386, ~1428; `ghidra-cfs2/BEFUND2.md` §8.

Einstufung: **OFFEN** (Tendenz Messrad als ANNAHME).
Live-Test: 0x0e-Verlauf gegen Förderweg (erwartete mm) UND gegen
BLDC-Laufzeit/Feeder-Drehzahl korrelieren (Checkliste: genau dieser Test);
bei blockiertem BLDC + laufendem Messrad (oder umgekehrt) trennt sich beides.

## 5. Motor-FW (WCH RISC-V, Basis 0x3000)

Basis-Befunde (B, übernommen + nachgeprüft): `mot{0,1,2}_022_C30-mot2_002_081.bin`
(116412 B, alle SHA `67b364a6…`; `motor/`-Variante 116396 B, gleicher String-Satz),
Kennung `mot2_002_081 @0x200`, CPU RISC-V WCH/QingKe, Befehlsregister
`0x6100–0x63d8` (u. a. `05 control @0xf880`, `08 get @0x102d0`, `0b boot @0x104e6`,
`0c protection @0x1053c`, `0e rs485_addr @0x107b4`, `0f version @0x109ba`,
`11 emergency_pin_mode @0x10c96`, `13–16 Details/Samples`). STATUS-Byte = Bitfeld
(Stall/lat.Err/lat.Warn). Quellen: `MOTOR-FW-BEFUND-2026-10-16.md` (= `REA-MOTOR-
BEFUND-2026-10-16.md`), `arbeitsdateien-lokal/rea-motor2/` (Skripte `luiref.py`/
`annot.py`: 303 lui/addi-Treffer auf `0x3000` = Basis-Beleg).

### 5a. MOTOR_GET idx 15/16

Ergebnis: OFFEN. FW liest idx 0..18 über Sprungtabelle `@0x1bce4`
(`MOTOR-FW-BEFUND` §3): 15 = `obj->[0xc]+0x24` (u32), 16 = `obj->[0xc]+0x2c`
(u32); Host-Typ `<I` (int) passt dazu. Was `obj->[0xc]` (dritter Objekt-Zeiger
neben ctrl `->[4]`/drv `->[8]`) zeigt, ist NICHT identifiziert (kein Name, kein
String-Treffer; ANNAHME: dritte Param-/Statusstruktur, evtl. Flash-/Sys-Param-
Block — reine ANNAHME). Eigener Binär-Check: GET-Handler-Bytes `@0xd2d0`
(= `0x102d0–0x3000`) enthalten die Tabellen-Dispatch-Muster, aber ohne Ghidra-
Symbole keine Feldnamen.

Belege: `MOTOR-FW-BEFUND-2026-10-16.md` §3-Z. 178; `rea-motor2/BEFUND.md` Z. 146.

Einstufung: **OFFEN**. Live-Test: nur mit eigenem Sender (NICHT Stock-G-Code,
s. 5b) idx 15/16 lesen + gegen Aktionen korrelieren (z. B. Stall/Protect-/Flash-
Param-Änderungen); statisch: `obj->[0xc]`-Schreiber per Ghidra/REA verfolgen.

### 5b. Ist idx ≥ 13 am Motor unkritisch?

Ergebnis: statisch JA (B), per Stock-G-Code trotzdem VERBOTEN.
- B (FW): idx 0..18 werden lesend behandelt, `> 18` sendet nur einen veralteten
  Scratch-Wert (`gp-0x7b4`), kein Crash (`MOTOR-FW-BEFUND` §3-Z. 181). idx 13 =
  EN-Eingang (`pins+0x118`), 14 = Stall-Flag — lesend harmlos (`§5`: „Lesend/
  sicher: 0x08 alle idx").
- B (Host): Der Crash bei `DATA=0/13` liegt VOR dem Senden: `motor_get` Z. 1982 →
  `parse_num_to_byte(None)` → `TypeError` (Creality-`gcode.py` lehnt `minval=1/
  maxval=12` nur per Warnung ab, rechnet mit None weiter) → key60-SHUTDOWN,
  KEIN Frame ging raus (`AUDIT-UND-AENDERUNGEN.md` Z. 555–561, `CHECKLISTE.md`
  Z. 379–383; Nachbau `read_variable` auf 1..12 begrenzt, `read_variable_raw_fw`
  nur mit `confirm=True`).
- Eigene Ergänzung: Host kennt Typkette bis 18 (float 1–10/17/18, int 11–16),
  sendet Stock aber nur 1..12 — `DATA=13` crasht daher im Parser, nicht am Motor.

Belege: `MOTOR-FW-BEFUND` §3/§5; `AUDIT-UND-AENDERUNGEN.md` Z. 508–561/651;
`CHECKLISTE.md` Z. 240–245; `nachbau/motor.py` (`VARIABLE_TABLE`,
`read_variable_raw_fw`).

Einstufung: **TEILWEISE** (FW-seitig GEKLÄRT unkritisch, Stock-Weg gesperrt).
Live-Test: nur mit eigenem Sender + `confirm`, nie über `MOTOR_GET DATA=13`
(Host-Shutdown); idx 13 (EN) ist per `MOTOR-FW-BEFUND`-Nachtrag live plausibel,
idx 14–18 am Motor noch offen.

### 5c. Motor-Kommando 0x05 ([01]/[02] bei BELT_TENSION, nie Antwort)

Ergebnis: Name bekannt, Semantik OFFEN.
- B: FW-Name `control @0xf880` (Befehlsregister, `rea-motor2/BEFUND.md` Z. 32);
  Host-Tabelle `Motor_cmd` kennt 0x05 (grant „control", `nachbau/motor.py`
  `FUNC_CONTROL`, `PROTOKOLL-485.md` Z. 695-Tabelle).
- B+L (zitiert): live `0x05 [01]/[02]` an X/Y während `BELT_TENSION`, NIE Antwort
  (Timeout ~0,05 s, retries 0; `klippy.log.2026-10-12:28621-28636/28949`,
  `livelog-motor/BEFUND-MOTOR.md` §2/§5, `nachbau/motor.py`-Docstring Z. 120–137).
  Deshalb im Nachbau NICHT gebaut (`FUNC_CONTROL` nur Konstante).
- Eigener FW-Check: `control`-Handler-Bytes `@0xc880` (= `0xf880–0x3000`)
  disassembliert sauber (RISC-V, `llvm-mc`), aber ohne Symbolik keine
  Parameter-Deutung. ANNAHME aus Fremdquelle/Kontext (grant, Belt-Ablauf):
  „Motor frei/fest / Enable/Regler an-aus für die Riemenmessung" — unbelegt.

Belege: `rea-motor2/BEFUND.md` Befehlstabelle; `MOTOR-FW-BEFUND` §0-Tabelle;
`PROTOKOLL-485.md` Z. 695/750; `livelog-motor/BEFUND-MOTOR.md` Z. 70/122;
`nachbau/motor.py` Z. 135 ff.

Einstufung: **OFFEN** (Rahmen B, Semantik offen).
Live-Test: 0x05 `[01]/[02]` an EINEM Motor mit eigenem Sender + Phasenstrom/
  Haltemoment/Drehbarkeit prüfen (Vorsicht: Belt-Ablauf setzt laufenden Host
  voraus; zuerst nur lesend beobachten).

### 5d. Erase (0xF0/0x06) für Motoren

Ergebnis:
- Host-Seite GEKLÄRT (B): Update-Pfad = Func `0xF0` mit Subs `00` version /
  `03` sector / `06` erase / `01` request / 4-B-Len / Chunks / `02` update_end
  (Timeout 500 ms, retries=2 = 3 Versuche; `PROTOKOLL-GESAMT.md` §1.7;
  `mcu_util_485`-Strings: `erase private flash sector`, `get_sector_size`,
  `update_end`, `get_version`; `nachbau/motor.py` Z. 499–512/1060–1134 mit
  `erase=True`-Gatter).
- Motor-Seite OFFEN: „Erase nur CFS-Typ 1/10 belegt" (`motor.py`-Docstring,
  PROTOKOLL-§1.7) — kein Beleg, dass Motor-Boards Sub `0x06` (oder überhaupt
  Func `0xF0`) bedienen. Eigener Motor-FW-String-Scan (645 Strings) findet
  `boot/boot_key/reboot/flash_param/flash_check…`, aber KEIN `erase/update/
  sector`-Kommando (nur `0b boot @0x104e6` als möglicher Update-Einstieg, V).
  Motor- und CFS-Update-Pfade dürfen nicht gleichgesetzt werden (ANNAHME-Warnung).

Belege: `PROTOKOLL-GESAMT.md` §1.7; `mcu_util_485`-Strings (s. o.);
`nachbau/motor.py` Z. 499 ff./1060 ff.; eigener Motor-FW-String-Scan.

Einstufung: **TEILWEISE** (Host-Mechanik GEKLÄRT, Motor-Unterstützung OFFEN).
Live-Test: KEINER an produktiven Motoren (Brick-Risiko); allenfalls an
Ersatz-Board `get version/sector` lesen, Erase nur mit Freigabe + Dump.

## 6. lokale Arbeitsdateien : mcu0_130-Dictionary ohne transparent_* — Lücke oder Variante?

Ergebnis: GEKLÄRT — echte FW-Variante, KEINE Extraktionslücke (B).
- Eigene Re-Extraktion (`vorarbeit/extract_dict.py`-Logik: `0x78` + FDC-Prüfung
  `%31==0`, `zlib.decompressobj` je Position, JSON-Prüfung) aus
  `fw/F008/mcu0_130_G32-mcu0_020_000.bin` (28812 B): genau EIN JSON-Treffer bei
  Offset 24812 (7327 B dekomprimiert), bytegleich zum Vorarbeit-JSON
  (`vorarbeit/dict/F008/mcu0_130_G32-mcu0_020_000.dict.json`, 72 Befehle) —
  kein zweites/übersehenes Dictionary im Image.
- Diff (B): 130 (Altstand `c48dd406-dirty-20240628`, `gd32f303xe`) = 72 Befehle /
  29 Antworten; 140 (`1.1.0.48-293-g493f9a0f-dirty-20241220`, 30948 B) = 75/32.
  Nur in 140: `config_transparent`, `transparent_send`,
  `query_finetuning…` (Vollform), `query_z_align…` (Vollform); nur in 130 die
  alte `query_z_align…`-Kurzform. Antworten nur in 140: `fakeshutdown`,
  `finetuning_status`, `transparent_response`. `query_gpio_status`/`gpio_status`
  sind in BEIDEN enthalten — es fehlt gezielt nur der Transparent-Pfad.
- Deutung (V, sparsam): Transparent (`config_transparent` 74/`transparent_send`
  73 → Extruder-Motor `0x81` via Düsen-MCU) kam erst mit dem 140-Stand
  (Dez. 2024); 130 (Juni 2024) kannte ihn noch nicht. Rohe `strings`-Suche nach
  `transparent` bleibt in beiden leer — erwartet, da das Dict zlib-komprimiert
  ist (kein Gegenbeleg).

Belege: `fw/F008/mcu0_130…bin` vs. `mcu0_140…bin` (Größe/Version s. o.);
`vorarbeit/dict/F008/*.dict.json` + `extract_dict.py`; `lokale Arbeitsdateien Agenten-Befund (lokal)`
§1–§4 (Befehls-IDs, Pins, Transparent-Pfad).

Einstufung: **GEKLÄRT**. Live-Test: keiner nötig (statisch geschlossen).
Folgerung: Für Strapping/Init den 140-Stand verwenden; 130-Dict nur als
Altstand-Referenz behalten.

## 7. filament_rack (Adresse 0x11, Kommandos) — grob

Ergebnis (grob, wie beauftragt):
- Umfang (B, `.so`-Strings): `filament_rack_wrapper.cpython-39.so` (206768 B,
  ARM/armhf) + Stub `filament_rack.py` (3 Zeilen, `load_config →
  FilamentRackWrapper`). Methoden u. a. `send_data`, `blow`, `beep`,
  `check_rfid_valid`, `get_material_target_temp/speed`, `get_material_type`,
  `is_different_type_material`, `quickly_wait_heating`, `enable_get_filament`,
  `find_objs`, `get_status`; G-Codes `FILAMENT_RACK(_MODIFY/_FLUSH/_PRE_FLUSH/
  _SET_TEMP/_SAVE_FAN/_RESTORE_FAN)`, `SET_COOL_TEMP`; Helfer `RemainMaterial
  (get_status/get_velocity/init/save)`; Material-DB-Dateien
  `creality/userdata/box/{remain_material_data,material_database}.json`;
  Lüfter via `SET_PIN PIN=fan0 …`, Piepser via `/usr/bin/beep.sh`,
  `not_pin`- missing/trigger-Texte, `serial_485`-Nutzung
  (`cmd_send_data_with_response`, `[filament rack] data_send/do not get respond`).
- Adresse 0x11 (B, RS485-Adresse, nicht Func-Code): `PROTOKOLL-GESAMT.md` §1.1-
  Tabelle („`0x11` Filament-Rack (Trockner), Config `filament_rack addr: 17` —
  noch NICHT live gesehen") + §1.6 (Start-Adressen `{0x01, 0x11, 0x23, 0x85}`:
  CFS/Rack/Belt/Motor; Klipper-Festadressen CFS `0x01`, Rack `0x11`, Belt
  `0x21/0x22`). In den vorliegenden Configs steht unter `[filament_rack]` nur
  `not_pin: !PA5` (`config/*/box.cfg`, `live-1.1.7.0-configs/box-live.cfg`) —
  kein `addr`-Parameter dort; die Adresse stammt aus der Auto-Addr-/Host-Tabelle
  (ANNAHME). Bus-Kommandos des Racks (Func-Bytes an `0x11`) sind aus dem `.so`
  per Strings NICHT ableitbar und live nie gesehen (OFFEN).
- Config (B): `[filament_rack] not_pin: !PA5` (alle Box-Cfgs + live); Makro-Kette
  in `gcode_macro.cfg` (`FILAMENT_RACK_SAVE_FAN → PRE_FLUSH → SET_TEMP/FLUSH →
  RESTORE_FAN`).

Belege: `.so`-Strings (sortierte Methoden-/G-Code-Liste s. o.);
`rootfs-live/…/extras/filament_rack.py`; `PROTOKOLL-GESAMT.md` §1.1/§1.6/§8-Z.1;
`config/*/box.cfg`, `box-live.cfg`, `gcode_macro.cfg` (Rack-Makros).

Einstufung: **TEILWEISE** (Umfang + Adresse 0x11 als RS485-Adresse GEKLÄRT,
Bus-Kommandos OFFEN — für „nur grob" ausreichend).
Live-Test (nur mit Rack-Hardware): `cmd_send_data_with_response` per
`socat`-Tee oder Logic-Analyzer an 485-A/B mitschneiden (`PROTOKOLL-485.md`
Z. ~1218), `ParseData`-Nachbau wie beim Belt-Modul; kein Senden ohne Gerät.
