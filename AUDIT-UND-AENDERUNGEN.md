# Audit und Änderungen – K2-Nachbau / Protokoll

## Zweck

Vollständige Gegenprüfung des bisherigen K2-Plus-Nachbaus gegen:

- Live-Logs des K2 Plus (`192.168.1.100`, Firmware 1.1.7.0)
- Klartextquelle `auto_addr_wrapper.py` vom Drucker
- Live-Introspektion von `box_wrapper.cpython-39.so`
- unabhängiges Projekt `gitstonelabs/creality-cfs-klipper` (Hi-CFS, GPL-3.0)
- CRC-Vektoren und gültige Frames aus dessen Testdaten

Die Prüfung betraf insbesondere Frame-Richtung (Request vs. Antwort), STATUS-Byte,
Slot-Payloads, CFS-Stufen und die fünf Python-Module.

## Ergebnis vor den Änderungen

Die Selbsttests waren grün, aber „grün“ belegte nur CRC-/Parser-Konsistenz. Drei
inhaltliche Punkte waren zu großzügig oder falsch:

1. `boxmini.py` sendete bei RFID/Remain die falsche Payload und standardmäßig STATUS 0x00.
2. `cfsseq.py` behandelte beobachtete Antwortframes teilweise wie Requests und
   erzwang eine nicht allgemein belegte 0x05/0x04/0x0f-Reihenfolge.
3. Die Protokolldoku bezeichnete 0x05 als CUT_STATE und 0x11 als bloßen Poll.

## Durchgeführte Änderungen

### `nachbau/serial485.py`

- `STATUS_OPERATIONAL = 0xFF` ergänzt.
- `TIMEOUT_OPERATIONAL = 2.0` ergänzt.
- `Serial485.transact(..., status=...)` ergänzt, damit Operational-Requests
  ausdrücklich mit STATUS 0xFF gebaut werden können.
- Adressierungs-/Belt-Pfade behalten weiterhin STATUS 0x00.

### `nachbau/boxmini.py`

Korrigiert nach Live-Introspektion:

```text
communication_get_rfid(1, 1)       -> send_data(addr=1, cmd=0x02, timeout=2, data=01)
communication_get_rfid(1, 15)      -> send_data(addr=1, cmd=0x02, timeout=2, data=0f)
communication_get_remain_len(1, 1) -> send_data(addr=1, cmd=0x03, timeout=2, data=01)
communication_get_remain_len(1,15) -> send_data(addr=1, cmd=0x03, timeout=2, data=0f)
```

- Slot wird jetzt als 1-hot-Maske gesendet: A=0x01, B=0x02, C=0x04, D=0x08.
- `get_remain()` verwendet standardmäßig Maske 0x0F statt vier 0xFF-Bytes.
- Operational-STATUS 0xFF und 2-s-Timeout werden verwendet.
- FakeBus-Selbsttest prüft nun auch die gesendete Maske und den STATUS.

### `nachbau/cfsseq.py`

- Request-Builder ergänzt:
  - ~~`build_preload(mask, phase)` für 0x0D `[Maske, Phase]`~~ – KORRIGIERT 15.10.2026:
    `build_preload(mask, action)`, Aktion 0x00=disable/0x01=enable/0x02=Force-Read
    (s. Abschnitt „Review 15.10.2026“, Befund B2)
  - `build_extrude_stage(slot, stage, parameter)` für 0x10
  - `build_extrude_sequence(slot, pushes)`
  - `build_retrude_sequence(slot)` für 0x11 START `[slot,00]` und FINISH `[slot,01]`
- 0x10-Stufen dokumentiert: init 00, engage 04, push 05, settle 06,
  finalize 07/03.
- Der Trace-Prüfer erzwingt nicht mehr fälschlich 0x05/0x04/0x0F nach dem
  letzten 0x10-Frame. Logframes können Antworten statt Requests sein.
- ~~Slot-Indizes 0..3 werden eindeutig in Masken 0x01/0x02/0x04/0x08 übersetzt.~~
  KORRIGIERT 15.10.2026: Die automatische Übersetzung (`_mask`) deutete Masken
  0x01..0x03 als Index um → falscher Slot. Builder nehmen jetzt die Maske direkt,
  Index nur explizit über `slot_to_mask(index)` (Befund B1).

### Dokumentation

`PROTOKOLL-485.md`, `PROTOKOLL-GESAMT.md` und `API-box_wrapper.md` wurden angepasst:

- 0x05 = Box-Buffer-Status; CUT_STATE war eine Fehlzuordnung.
- 0x04 = SET_BOX_MODE.
- 0x08 = GET_HARDWARE_STATUS.
- 0x0F = CTRL_CONNECTION_MOTOR_ACTION.
- 0x10 = mehrstufiger Request mit Slotmaske/Stufe/Parameter.
- 0x11 = START/FINISH mit Slotmaske und Phase.
- 0x0E = negativer Big-Endian-IEEE-754-Messrad-Float.
- RFID-/Remain-Requests nutzen Slotmasken und STATUS 0xFF.
- Der Unterschied zwischen Log-Requests und Log-Antworten ist jetzt ausdrücklich markiert.

## Verifikation

Ausgeführt am lokalen Nachbau (historischer Stand; aktuelle Zahlen s. Abschnitt
„Review 15.10.2026“):

```text
serial485.py  : 11/11 Live-Frames
boxmini.py    : Selbsttest ok, inklusive Request-Maske/STATUS
motor_mini.py : Selbsttest ok
cfsseq.py     : Selbsttest ok, inklusive Request-Builder
auto_addr.py  : Selbsttest ok
compileall    : ok
```

Unabhängige Hi-CFS-Testdaten gegen `serial485.py`:

```text
16/16 CRC-Vektoren ok
12/12 gültige Hi-Frames ok
```

## Was weiterhin nicht behauptet wird

- Der Nachbau ist noch kein vollständiger Ersatz für alle Creality-Cython-Module.
- CFS-Stufen-/Timeout-Semantik wurde unabhängig für Hi-Hardware validiert; auf dem
  K2 Plus sind die Transportframes und viele Erfolgsfälle belegt, aber nicht jede
  Hi-Choreografie darf automatisch als K2-validiert gelten.
- `serial485.py` ist noch kein nichtblockierender Klipper-Reactor-Transport mit
  Response-Registry und unsolicited-event routing. (NACHTRAG 15.10.2026: Registry,
  Sende-Queue und Hintergrund-Thread sind inzwischen nachgebaut – Reactor-Anbindung
  weiterhin nicht; offene Nebenläufigkeitspunkte s. Review 15.10.2026, serial485 „Offen“.)
- `motor_mini.py` ist nur ein geprüfter Ausschnitt; die normale Motor-Version-API
  und alle Closed-loop-/Protection-Funktionen sind nicht vollständig nachgebaut.
- `boxmini.has_filament()` nutzt Restmenge als einfachen Beleg; für vollständige
  Präsenzlogik müssen 0x02, 0x03, 0x08 und 0x0A gemeinsam ausgewertet werden.
  KORRIGIERT 15.10.2026: ~~remain 0 → „leer“/False~~ – jetzt True nur bei remain>0,
  sonst None (remain 0 = kein RFID-Restwert; Slot B ist live mit remain 0 gefahren),
  Befund B3.

## Aktueller Projektstand

Die fünf offenen Pi-Aufbaupunkte bleiben in `./CHECKLISTE.md`.
Die Nachbau-Bausteine liegen vollständig unter:

```text
<projekt>/nachbau/
```

## Nachaudit

Nach Abgleich mit `gitstonelabs/creality-cfs-klipper/docs/protocol.md` wurden
zusätzlich korrigiert:

- 0x05 nicht mehr als Schnittbestätigung, sondern als Box-Buffer-Status
  dokumentiert.
- 0x10-Requests als echte Stufenframes `[Slotmaske, Stufe, Parameter]`
  dokumentiert; der bisherige Trace-Prüfer interpretiert Antwortframes nicht
  mehr fälschlich als vollständige Request-Choreografie.
- 0x11 als START/FINISH-Paar `[Slotmaske, 00]` / `[Slotmaske, 01]`
  dokumentiert.
- `boxmini.py` sendet RFID/Remain nun mit Slotmaske und STATUS `0xff`.
- `cfsseq.py` erzeugt jetzt Preload-, Extrude- und Retrude-Requestframes;
  die Selbsttests bleiben grün.

Letzte lokale Prüfung:

```text
serial485.py  11/11 ok
boxmini.py    ok
motor_mini.py ok
cfsseq.py     ok
auto_addr.py  ok
compileall    ok
Hi-CRC-Vektoren 16/16 ok
Hi-Frames      12/12 ok
```

## Code-Review-Fixes

Beim Review gefundene und behobene Mängel (alle mit Selbsttest abgedeckt):

1. `serial485.py`: `_resync()` verwirft Empfangs-Müll bis zum nächsten
   `PACK_HEAD`; `transact()` ruft es nach jedem Fehlschlag und
   Fun-Mismatch auf. Vorher wuchs der Puffer bis zum Timeout.
2. `serial485.py`: `threading.Lock` um die gesamte Transaktion in
   `transact()` (Schutz bei parallelen Threads, z. B. Hintergrund-Loop
   plus Haupt-Thread).
3. ~~`motor_mini.py`: `read_encoder(register)` ohne Default — ein stilles
   `00 00 00 00` wurde nie auf dem Bus beobachtet; Selbsttest prüft den
   TypeError bei fehlendem Argument.~~ **WIDERLEGT 15.10.2026:** 0x06 ist kein
   Encoder-Lesen, sondern Sys-Param (LE-Float 300.0/100.0, ~~Big-Endian 38467→51266~~
   war ein Artefakt). `read_encoder` ist in motor.py und motor_mini.py gesperrt
   (NotImplementedError, sendet nichts) – Befund M1.

## Review 15.10.2026 (Agents) + Korrekturen

Code-Review der Module in `nachbau/` durch Agents, Fixes im Code, danach Doku-Abgleich.
Maßgeblich ist der aktuelle Code. Selbsttests am 15.10.2026 ausgeführt (`python3 <modul>.py`
in `nachbau/`, `ok`-Zeilen gezählt, alle grün, 0 FAIL):

```text
serial485.py  : 81/81 ok (inkl. ref-hi: 16 CRC-Vektoren + 12 Nachrichten)
box.py        : 68 Checks ok
boxmini.py    : 15 Checks ok
cfsseq.py     : 17 Checks ok
motor.py      : 65 Checks ok
motor_mini.py : 20 Checks ok
auto_addr.py  : 31 Checks ok   (Review-Angabe „32/32“ nicht reproduzierbar – 31 ok-Zeilen)
```

### serial485.py

**S1 – Empfangspfad.** Fehler: fragmentierte Frames bzw. Frames >64 B wurden nicht
weitergelesen, ein Chunk mit mehreren Frames nicht vollständig abgearbeitet, gültige
Fremd-Frames (mit 0xF7 in den Daten) konnten falsch resynchronisiert werden; Längenbytes
<3 bzw. außerhalb 3..255 nicht abgelehnt. Beleg: Review + Live-Verhalten (Herzschlag 0x0a
und Antwort im selben Chunk). Korrektur: Weiterlesen bis `len+3`, Rest in `_rest_buf`,
Puffer komplett abarbeiten, Fremd-Frames als Ganzes überspringen, len-Grenzen 3..255.
Test: `fragmentiert 3+rest`, `fragmentiert 4-byte`, `frame >64 byte`,
`herzschlag+antwort ein chunk`, `fremd-frame mit 0xF7 in daten`, `müll len=0 vor antwort`,
`parse_frame len<3 -> ValueError`.

**S2 – Handler-Exceptions.** Fehler: Exceptions in Bus-/Wrapper-Handlern wurden
verschluckt. Korrektur: `_log.exception(...)`, Schleife läuft weiter.
Test: `handler-exception nicht propagiert`.

**S3 – Deadlock.** Fehler: Dispatch der Callbacks lief unter dem Lock; ein Handler, der
selbst sendet, blockierte. Korrektur: Dispatch erst nach Lock-Freigabe, Callbacks in
`send_queue_process` gesammelt und nach dem Drain ausgeführt.
Test: `bus-handler ruft raw_send (kein deadlock)`, `wrapper-handler sendet selbst (kein deadlock)`.

**S4 – Session-Start.** Fehler: `_start_session` übernahm stale Bytes aus `_rest_buf`.
Korrektur: `_rest_buf = b""` beim Start. Test: `_start_session leert _rest_buf`.

**S5 – ref-hi-Kreuzvalidierung.** Vorher nur manuell (s. „Unabhängige Hi-CFS-Testdaten“
oben), jetzt fester Teil des Selbsttests: 16 CRC-Vektoren + 12 Nachrichten aus
`nachbau/ref-hi/`. Test: `ref-hi crc #0..#15`, `ref-hi msg #0..#11`.

Offen aus dem Review (in CHECKLISTE.md als `- [ ]`):
- `retries=4` = 1 Erstsendung + 4 Wiederholungen = 5 Sendungen – ob das Original
  „retries = 4“ als 4 oder 1+4 meint, am klippy.log nachzählen.
- `Wrapper._done` wächst unbegrenzt (nie abgeholte Antworten).
- Hintergrund-Thread kann `get_response` eine Antwort wegschnappen, wenn zwischen
  `raw_send` und `get_response` eine Lücke ist.

### box.py / boxmini.py / cfsseq.py

**B1 – Maske vs. Index.** Fehler: Helfer `_mask` deutete Masken 0x01..0x03 als Slotindex
um → falscher Slot (Wire-Parameter `num` ist im Original eine Maske:
API-box_wrapper.md, `communication_get_rfid(1, 1)` → data `01`, `(1, 15)` → `0f`).
Korrektur: `check_mask` (0x00..0x0F, Mehrfachmasken, keine Umdeutung) + `slot_to_mask(index)`;
cfsseq-Builder nehmen eine 1-hot-Maske statt Index (Extrude/Retrude nur 1-hot,
Preload jede Maske). Test: box `mask 0x01/0x02/0x03 unverändert`, `mehrfachmaske 0x05/0x0C`,
`mask-guard *`, `slot_to_mask`; cfsseq `slot_to_mask(1) == 0x02`, `guard extrude 0x03`,
`guard retrude 0x0f`, `preload mehrfachmaske 0x05`; boxmini `slot_to_mask`.

**B2 – Preload-Aktionen (0x0d).** Fehler: Belegung 0x00=Arm/0x01=Disarm/0x02=Slot-Rearm
(PROTOKOLL-485.md „Preload-Ablauf“) ohne Frame-Beleg und im Widerspruch zu beiden
Referenzen. Beleg: ref-hi `f70105ff0d0f0169` = „CMD_SET_PRE_LOADING box 1, mask=0x0F
enable=0x01“; k2-vanilla `files/box_protocol.py`: `set_rfid_insert_reading(enabled)` →
`(0, int(enabled))`, `force_rfid_read(mask)` → `(mask, 2)`. KEIN Live-0x0d-Request belegt,
live nur leeres ACK (len 3). Korrektur: `PRELOAD_DISABLE=0x00`, `PRELOAD_ENABLE=0x01`,
`PRELOAD_FORCE_READ=0x02`, `build_preload(mask, action)` + `build_preload_enable/disable/
force_read`. Test: box `builder preload-enable (ref-hi f70105ff0d0f0169)`,
`builder preload-disable`, `builder preload-force-read`, `builder preload-enable vanilla (0,1)`,
`preload box == cfsseq (M1)`, `preload aktions-guard`; cfsseq `preload ref-hi f70105ff0d0f0169`,
`guard preload aktion 3`. Doku korrigiert: PROTOKOLL-485.md (Preload-Ablauf, 0x0d-Zeile),
PROTOKOLL-GESAMT.md (1.3, 1.9 als entschieden markiert).

**B3 – `has_filament` / Remain 0.** Fehler: remain 0 wurde als „ausgewählt + leer“ /
kein Filament gewertet. Beleg: T1B-Extrude 12.10.2026 (PROTOKOLL-485.md) – Slot B ist mit
remain 0 und Vendor „unknown“ 160 mm gefahren. Korrektur: `has_filament` → True bei
remain>0, sonst None (nie False), Bereichsprüfung des Slot-Index; Doku „0 = kein
RFID-Restwert“ (PROTOKOLL-485.md, PROTOKOLL-GESAMT.md, API-box_wrapper.md).
Test: boxmini `filament B remain 0 -> None`, `filament B 0xff -> None`,
`filament index-guard -1/4/True`.

**B4 – Remain-Länge.** Fehler: Antworten ≠ 4 Bytes wurden geparst. Korrektur: exakt
4 Bytes, sonst ValueError. Test: box `remain laengen-guard 1B/0B/5B`, boxmini `remain laengen-guard`.

Offen: Status-Byte `raw[3]` wird weiterhin NICHT ausgewertet (TODO in `box._response_data`);
ob Byte 1 bei Preload 00/01 die Maske trägt (ref-hi 0x0F) oder 0 (vanilla) ist unbelegt.

### motor.py / motor_mini.py

Doku dazu wurde bereits durch einen anderen Agent korrigiert (PROTOKOLL-485.md Abschnitt 3,
PROTOKOLL-GESAMT.md 1.5/1.7, CHECKLISTE.md); hier nur der Vollständigkeit halber:

**M1 – 0x06.** Fehler: als Encoder/ADC-Lesen gedeutet (~~Big-Endian 38467 → 51266~~).
Beleg: Payload `00 00 96 43` → `00 00 c8 42` = LE-Float 300.0 → 100.0 = `MOTOR_SYS_PARAM
ID=70 PARAMS=300|100` (gcode_macro.cfg:796/799, sensorless.cfg:145/211). Korrektur:
0x06 = Sys-Param; `read_encoder` gesperrt (NotImplementedError, sendet nichts).
Test: `0x06 00 00 96 43 = 300.0`, `0x06 00 00 c8 42 = 100.0`, `read_encoder() wirft`,
`read_encoder(38467,) wirft`, `read_encoder sendet nichts`. Offen: Position der ID 70 im
Frame (Live-Frame `f7 81 07 00 06 00 00 96 43 09` trägt nur den Float) → getrennter TX/RX-Mitschnitt.

**M2 – Update-Sequenz.** Korrektur: Abschluss Sub 0x02 (update_end, 500 ms), Erase nur mit
`erase=True`, Sector-Size 1..127 (signed Byte), `UPDATE_RETRIES=2` (= 3 Versuche wie
mcu_util_485 `cmp r6,#3`). Test: `update_end sub 0x02 / 500 ms`, `seq erase nur auf Wunsch`,
`chunk guard 0/128/300`, `chunk 127 ok`, `retries=2 -> 3 Versuche wie Binary`,
`query_version retries=2 (3 Versuche)`. Offen: Erase für Motoren unbelegt (nur CFS-Typ 1/10 dekompiliert).

### auto_addr.py

**A1 – Zustandsmaschine.** Fehler: nur Teilport. Korrektur: komplett portiert aus
der Klartextquelle `auto_addr_wrapper.py` (lokal, nicht im Repo) (0xA0-Zuweisung nach Broadcast, Reboot-Zweig, Startphase
set/get_addr_table, Broadcast-Skip, Loader-Check 0x0B an 0xFF, Druck-Intervall 10 s,
Store im Original-printer.cfg-Format). Beleg: Vergleich Original vs. Port über 2000
Zufallsszenarien, 0 Abweichungen. Bewusste Abweichungen: unbekannte Function-Codes/zu kurze
Frames werden ignoriert statt Crash; unlesbare Store-Werte verworfen. 1:1 inkl.
Original-Eigenheit: Antworten landen immer in der ersten (MB-)Tabelle.
Test: 31 Checks, u.a. `reboot: *`, `kalt: *`, `skip: 4 online, kein a1`, `loader: *`,
`store *`, `intervall 10 s bei Druck/Pause, sonst 1 s`, `offline erst nach 4 verpassten Checks`,
`unbekannter Code/Kurz-Frame ignoriert`.

**A2 – Doku-Offline-Schwelle.** Fehler: PROTOKOLL-485.md „3 verpasste Checks = offline“.
Beleg: Klartextquelle `auto_addr_wrapper.py`:604-607, lokal, nicht im Repo (`lost_cnt += 1` … `if lost_cnt > MAX_LOST_CNT`,
`MAX_LOST_CNT = 3`) → offline beim 4. unbeantworteten Check. Korrektur: Doku markiert.
Test: `offline erst nach 4 verpassten Checks`.

### Restwidersprüche (nicht in diesem Schritt behoben)

- `nachbau/box.py`, Docstring `get_measuring_wheel`: nennt noch `c5019149 -> -2073,08`
  (in der Doku bereits als Tippfehler korrigiert: Live-Frame c5009149 = −2057,08). Nur
  Kommentar, kein Funktionsfehler – Code-Änderung nicht Teil dieses Doku-Schritts.
- Die lokale Übergabenotiz (nicht im Repo) verweist auf `README.md`, `LICENSE`, `NOTICES.md`,
  die in der lokalen Projektablage fehlen – im Repo sind alle drei vorhanden.

## 15.10.2026 – Fremdquelle grant0013 eingearbeitet

Gesichtet: `grant0013/k2-reverse-engineering` (GPL-3.0, K2 – nicht K2 Plus; Belege aus
klippy.log/Namen, keine Frames/CRCs). Eingearbeitet nur als Kommentar/Doku, **kein neuer
sendender Code**, Stubs und `read_encoder` bleiben gesperrt:

- `nachbau/motor.py`, `nachbau/motor_mini.py`: ID 70 = `controller_pos_loop_pid_param_kp`
  (Positionsregler-Kp, `motor_params_map.md:103`) im FUNC_SYS_PARAM-Beleg; Fremd-Hypothesen
  der Kommandobytes (0x03/0x04/0x07/0x08/0x0C/0x0E/0x0F/0x11/0x12) an den passenden
  Stubs bzw. bei FUNC_SET_PARAMS, jeweils als „Fremdquelle K2, auf dem Plus unbelegt“.
- `PROTOKOLL-485.md` §3: ID-70-Nachtrag + Abschnitt mit Kommandotabelle und Bewertung.
- `PROTOKOLL-GESAMT.md` 1.5/1.9: ID-70-Hinweis, Projekteintrag inkl. Transparent-Pfad/
  Bootsequenz als Hinweis für den Pi-Ersatz.
- `CHECKLISTE.md`: 5 offene Prüfpunkte (0x08, 0x0C, 0x12/0x0E, Transparent-Pfad, Param-Map).
- Selbsttests danach unverändert grün (alle 7 Module).

## 15.10.2026 – Fremdquelle fake-name eingearbeitet + Ghidra-Basis korrigiert

Gesichtet: `fake-name/cfs-reverse-engineering` (Connor Wolf, Stand 2025-04, ältere CFS-FW
App 108/113; keine Lizenzdatei außer GPL3 für die Adapter-PCBs → nur Fakten mit
Quellenangabe übernommen, keine Texte/Fotos/Dumps, keine Seriennummer des Autors).
Belegstärke je Befund in den Zieldokumenten vermerkt.

- **Ghidra-Basis KORRIGIERT (stark, selbst verifiziert per String-Zeiger-Test):**
  Bootloader 12 KiB @0x08000000, cfs0-App @0x08003000 (179 vs. 1 Treffer), cfs6-App
  @0x08004000 (55 vs. 0), DB-Page @0x0807F800. Alte Angaben (0x08000000) in
  PROTOKOLL-485.md §2 und PROTOKOLL-GESAMT.md §5 als „KORRIGIERT 15.10.2026“ markiert;
  fehlende Code-XRefs vermutlich Folge der falschen Basis. Neuanalyse ausstehend.
  `ghidra-scripts/` NICHT geändert (anderer Agent).
- `PROTOKOLL-485.md` §2: neue Abschnitte „CFS-Firmware: Flash-Map, Bootloader, Shell“
  (Bootloader-Protokoll 0xA0/0xA1/0x0B, finsh nur via RTT), „CFS-Hardware“
  (MS8828-BLDC statt „Stepper“, HTD8236, FM17622, AHT30, MT6826S), „CFS-Bus physisch“
  (J4-Pinout, Bias/Terminierung, Buffer als Direktleitungen); RFID-vender-Feldlayout,
  0x0a-Zustandskandidaten, Force-Preload-Strings.
- `PROTOKOLL-GESAMT.md`: vender „24B“ → 40 Hex-Zeichen/20 Byte; „Tag-SN/Tag-FW“ →
  Box-SN + CFS-App-Version (0x14); MCU GD32F303VET6 + RT-Thread 5.0.2; MS3791-Korrektur;
  offener Punkt 8 erledigt, Punkt 9 (Neuanalyse) neu; §1.9 Projekteintrag fake-name.
- `nachbau/box.py`: reine Hilfsfunktion `split_rfid_vendor(s)` + `RFID_VENDOR_FIELDS`
  (40 Zeichen, sonst ValueError; kein Buszugriff), TODO in `get_rfid` aufgelöst,
  Selbsttest feldweise mit Live-Vendor + Längen-Guards (box 68 → 83 Checks).
- `CHECKLISTE.md`: Pi-Ersatz-Elektrik, Kanal-A-Diagnose (optische Lichtschranke),
  neuer Block „Offen – aus fake-name“ (MT6826S, Adresse 0xF0, len 0xFC vs. 255,
  0x10 `[mask 07 03]`, 0x0a-Enum, Neuanalyse).
- Selbsttests danach alle grün: serial485 81/81 · box 83 · boxmini 15 · cfsseq 17 ·
  motor 65 · motor_mini 20 · auto_addr 31.

## 15.10.2026 – Ghidra-Neuanalyse CFS-FW eingearbeitet

Eingabe: Ghidra-Neuanalyse `cfs0_000_153` (= FW in der Box des Users, App 153 live per 0x14)
mit korrigierter Basis 0x08003000 (+ Gegenprobe R150, cfs6_222 @0x08004000). Bericht als
`GHIDRA-CFS-BEFUND-2026-10-15.md` ins Projekt-Root übernommen; /tmp-Pfade als „Arbeitsdateien
lokal“ gekennzeichnet, Dekompilat-Auszug zu 0x0d auf Belegzeilen mit Adressen gekürzt (keine
größeren Creality-Code-Rekonstruktionen im Repo). 0x0d-Handler `FUN_0800f0da` vom Auftraggeber
selbst gegengeprüft; bei der Einarbeitung zusätzlich nachgeprüft (Abschnitt §10 im Befund):
0x0d-Laufzeit je Slot (5 s + bis 180 s, nacheinander), 0x03-Wire-Semantik (`FUN_0800ef66`),
0x10 mode 5 = Live-`c4…`-Replies.

- **Widerlegt/korrigiert:** 0x0f ist die Auto-Preload-Sperre (S[8]), KEIN Motor-Engage/
  Release (Bytes und Live-Flanke bleiben gültig). 0x10 „push“ (05) ist eine Odometer-
  Abfrage, 07 03 = Extruder-Check p=3 mm (Hypothese bestätigt). 0x0a-Zustand steht in
  Byte 3 (IDLE 0…TEST 5); CHECK_HUB ist kein Box-Zustand. Status 0x16 = Slot ohne Filament
  (nicht busy; busy = 0x03), 0x30 nur bei 0x0a. 0x03: Wire-0xFF auch „Filament ohne
  RFID-Daten“, Wire-0 = kein Filament (Sensor A) oder 0 % – „remain 0“ bei T1B war ein
  Host-Wert. Request-Status-Byte wird von der App ignoriert. App akzeptiert nur
  eigene/0xEA/0xEB (Bootloader: 0xFF/0xFE/eigene/0xF0) – Unterschied notiert.
- **Neu belegt:** 0x0d op 3 (Forced Preload ohne RFID), Maske bei op 0/1 ignoriert, Status
  0/1/3/0x16, Antwort erst nach dem Vorgang (~3 min je Slot); neue fun-Codes 0x01, 0x06
  (FW-Bug: Maske aus r7 → praktisch immer Status 1, auch R150), 0x07, 0x09, 0x0b, 0x0c, 0x12,
  0x13, 0x15, 0x30, 0x50–0x56, 0x5b–0x5d; vollständige Status-Tabelle.
- **Doku:** PROTOKOLL-485.md (neuer Abschnitt „Func-Tabelle CFS-App R153“ mit Rahmen,
  Handler-Tabelle, 0x10-Modi, 0x11, Status-Tabelle, 0x0a-Layout; Altbefunde durchgestrichen/
  markiert; Platzhalter „Neuanalyse ausstehend“ ersetzt), PROTOKOLL-GESAMT.md (1.3, 1.9,
  §5 neuer Abschnitt „CFS-FW Neuanalyse“ mit Funktionszahlen cfs0 1063 / cfs6 936 und
  XRef-Vergleich, cfs6 = Trockner-Variante; §8 offene Punkte), CHECKLISTE.md (abgehakt:
  0x0d-Semantik, Stufen-Semantik, Status-Werte, 0x0a-Enum, Neuanalyse, `[mask 07 03]`,
  0x0d-Timeout; neuer Block „Offen – aus Ghidra-Neuanalyse“).
- **Code (`nachbau/`):**
  - `box.py`: `PRELOAD_FORCE_TENSION_ONLY = 0x03` (+ Alias `PRELOAD_FORCE_RFID`),
    `build_preload_force_tension_only()`; `communication_set_pre_loading` bei op 2/3 mit
    `preload_force_timeout(mask)` (180 s + 200 s je Slot) und `retries=0` (vorher 2 s + 4
    Retries → hätte den Force-Preload bis zu 5× gesendet), optionaler `timeout`-Override;
    0x0f: `FUNC_PRELOAD_INHIBIT`, `build_preload_inhibit()`,
    `communication_set_preload_inhibit()` – alte Namen (`FUNC_MOTOR_ACTION`,
    `build_engage/release`, `communication_ctrl_connection_motor_action`) als
    kompatible Aliase; `STATUS_*` + `STATUS_NAMES` + `status_name()` (nur Bereitstellung,
    Status-Auswertung in `_response_data` bleibt TODO); `BOX_STATE_*`/`BOX_STATE_NAMES` +
    neuer `parse_box_state()` (benanntes 6-Byte-Layout bzw. 0x30-Ereignis);
    `get_box_status` unverändert (Bytes stimmten bereits); Docstrings 0x03/0x04.
    Re-Export der 0x10/0x11-Konstanten aus cfsseq.
  - `cfsseq.py`: `EXTRUDE_MODE_*` 0…7 + `EXTRUDE_MODES`, `EXTRUDER_CHECK_MM`,
    `RETRUDE_P_TO_BUFFER/PULL_HUB`, Preload op 3; Sequenzen nur umbenannt (Bytegleichheit
    im Selbsttest geprüft), Modul-Doku korrigiert.
  - `boxmini.py`: nur Docstrings (0x03-Semantik, 0x08 als möglicher Sensorpfad); Logik
    `has_filament` unverändert.
- **Widersprüche BEFUND ↔ Live-Frames:** keine harten. Präzisiert: BEFUND „bis zu ~3 min“
  gilt je Slot (Mehrfachmaske summiert sich); 0x03-Bedeutung von 0 gegenüber der bisherigen
  Doku verschoben (Host- vs. Wire-Wert), Live-Frames passen zur FW-Logik.
- Selbsttests danach alle grün: serial485 81/81 · box 101 · boxmini 15 · cfsseq 21 ·
  motor 65 · motor_mini 20 · auto_addr 31.

## 15.10.2026 – Live-Logs ausgewertet (Drucker-Zugriff nur lesend)

- **Quelle:** `live-logs-2026-10-15/` (6× klippy.log 05.–16.10., moonraker.log, versionen.txt;
  lokal, nicht fürs Repo). Auswertungen: PROTOKOLL-485.md §3.1 (Motoren) und §6 (CFS),
  GHIDRA-CFS-BEFUND-2026-10-15.md §11. Log-Uhr UTC+8; Box-FW 113 → 153 und Display-FW
  1.1.4.12 → 1.1.7.0 am 12.10.2026.
- **`nachbau/box.py`:**
  - `FUNC_RETRIES`/`func_retries()` + `FUNC_TIMEOUTS_SO` (Startwerte aus dem Log je fun:
    alle CFS-Operational 4, **0x0a-Poll 0**); alle `BoxAction.communication_*` übergeben
    `retries` explizit. Bewusste Abweichung: 0x0d op 2/3 bleibt retries=0 + Ghidra-Timeout
    (Original 4 × 300 s würde den Force-Preload neu auslösen).
  - Namen: `FUNC_GET_FILAMENT_SENSOR_STATE` = 0x08 (alt `FUNC_HARDWARE_STATUS_LEGACY_08`,
    `build_get_hardware`, `parse_hardware_status` als Aliase), `FUNC_GET_HARDWARE_STATUS` =
    **0x15** (neu `build_get_hardware_status`, `parse_get_hardware_status`;
    `communication_get_hardware_status()` sendet 0x15, mit altem `channel`-Argument wie
    früher 0x08), `FUNC_TIGHTEN_UP_ENABLE` = 0x0f (+ `build_tighten_up_enable`,
    `communication_tighten_up_enable`; PRELOAD_INHIBIT/MOTOR_ACTION bleiben Aliase),
    `STATUS_UPDATE_STATE` = 0x30, `STATUS_SPEED_ERR` = 0x51, `EVENT_*` (1/2/3).
  - `parse_box_state(msg, box_fw=None)`: Byte 5 als `b5` (unbelegt; `rfid_valid_mask` nur
    Alias), `temp_c_guess`/`rh_guess` (Vermutung), `hub_mask`; FW-113-Frames (4 Byte, Status 0)
    → `state_v113`; 4-Byte-**Rohdaten** ohne Status nicht mehr als Ereignis, sondern
    `ambiguous` (mit `box_fw` entscheidbar).
  - Selbsttest: echte Live-Frames (IDLE, SPEED_ERR, Motorblock 0x19/0x91, FW 113, 3 ×
    UPDATE_STATE, 0x08, 0x15); SN und RFID-vender im Test durch Platzhalter ersetzt (CRC neu).
- **`nachbau/cfsseq.py`:** `build_retrude_sequence(mask, to_buffer_repeats=4)` wie live
  (`04[00 01] 08[00] 11[m 00]×4 11[m 01]`, Kurzform bei 0); das frühere `08 [01]` zwischen
  p0 und p1 entfernt (live 0/23). Neu `requests_from_log_cmds`, `filter_funs`,
  Live-Sequenzen `RETRUDE_LIVE_CMDS_A`/`RX_A`, `EXTRUDE_LIVE_CMDS_B` als Tests (Extrude-
  Folge bytegleich mit `build_extrude_sequence`). Namen `FUNC_FILAMENT_SENSOR_STATE`,
  `FUNC_TIGHTEN_UP_ENABLE` (alte als Aliase).
- **`nachbau/boxmini.py`:** `RETRIES_OPERATIONAL = 4` explizit (wie Original), Live-Remain-
  Frame `00 ff 1d 00` als Test, Docstrings (0x08 live belegt).
- **Motor-Code:** parallel umgesetzt, siehe `motor.py`/`motor_mini.py` (read_sys_param,
  Schreib-/Update-Builder, write_sys_param mit confirm-Schutz, check_protection,
  get_stall_mode, read_485_addr, read_uuid, MOTOR_RETRIES=0, Timeouts 0x06/0x12 1,0 s,
  0x0E/0x0C 0,05 s, SYS_PARAM_NAMES 227).
- **Doku:** PROTOKOLL-485.md (Logformat, Retry-Tabelle, 0xa2 retries=0 statt „4 Retries“,
  0x0d live, Host-Namen, 0x51, Motor §3.1), PROTOKOLL-GESAMT.md (Ports, Versionen, §1.2/1.3/
  1.5/1.9), CHECKLISTE.md (6 Punkte geschlossen, Pi-Ersatz, Kanal A), lokale Übergabenotiz (nicht im Repo).
  Datenschutz: vollständige SN/UUID/RFID-vender in allen Markdown-Dateien gekürzt.
- Selbsttests danach alle grün: serial485 81 · box 126 · boxmini 17 · cfsseq 28 · motor 98 ·
  motor_mini 30 · auto_addr 31.

## 16.10.2026 – Review 2 (Code-Fixes) + Ghidra-BEFUND2 eingearbeitet

### Review 2 – Fixes im Code (Kennungen wie in den Code-Kommentaren)

| Kennung | Datei | Problem | Korrektur | Test |
|---|---|---|---|---|
| M1 | serial485.py | Antwort wurde nur über fun zugeordnet – ein Box-Frame (0x01, fun 0x0e) hätte als Antwort für Motor 0x81 gegolten | `expect_addr` (Default `reply_addr_for(ziel)`), `BROADCAST_ADDRS` (0xEA/0xEB/0xFC–0xFF) = beliebiger Absender | Selbsttest 10c |
| M2 | serial485.py, box.py | Status-Byte der Antworten wurde verschluckt | `transact_full`; `box.CfsStatusError`/`StatusBytes`/`raise_on_status` | serial485 10d, box 8c |
| M3 | box.py | 0x10/0x11 mit 4 Retries würden Mechanik bei Timeout erneut auslösen | `FUNC_RETRIES` 0x10/0x11 = 0 (**bewusste Abweichung**, `FUNC_RETRIES_ORIGINAL` = 4 hält das Original fest), `communication_extrude`/`communication_retrude` mit langen Timeouts | box 8c |
| M6 | serial485.py | Lock-Haltedauer unbegrenzt (z.B. 300 s × 5) | `MAX_BUS_HOLD_S = 1200` s → ValueError; Lock bleibt **bewusst** über die ganze Transaktion (Halbduplex, Original identisch) | serial485 10f |
| G1 | box.py | Messrad op 0 = Nullen (FW `FUN_0800efc6`) ohne Schutz | nur mit `reset=True` | box |
| G2 | box.py | Maske 0 bei 0x0d op 2/3 nicht in Timeout gerechnet | wie 4 Slots (980 s) | box |
| G3 | box.py | `_box_fw_num` kannte Host-Form „1.5.3“ nicht | Formate „153“/153/„1.5.3“ belegt | box |
| G4 | box.py | `_is_full_frame` scheiterte an angehängten Bytes | robust | box |
| G5/G6 | cfsseq.py, box.py | ACK-Konstanten irreführend benannt, `slot_to_mask` doppelt; alte `get_hardware_status(channel)`-Signatur still auf 0x08 | `EXTRUDE_ACK`/`RETRUDE_ACK` (Status 00), `check_one_hot`, einziges `slot_to_mask`; DeprecationWarning | cfsseq, box |
| – | motor.py | F0-Update/Schreiben ohne Schutz möglich | F0-Sender nur mit `confirm=True`; `write_sys_param` für 166 gesperrte IDs nur mit `allow_locked=True`; float32-Plausibilität | motor |
| – | motor_mini.py | Schreib-ID-Menge nicht begrenzt | `MINI_WRITABLE_IDS` (13 Stock-IDs), `update_sys_params` nur mit `confirm=True` | motor_mini |
| – | nachbau/cfswatch.py | (neu) | nur lesender Live-Monitor für klippy.log; sendet nie, maskiert SN/UUID/Vendor | `--selftest` |

### BEFUND2 (Ghidra, `GHIDRA-CFS-BEFUND2-2026-10-16.md`) – Folgen

- **Korrekturen an GHIDRA-CFS-BEFUND-2026-10-15.md** (dort mit „→ BEFUND2“ markiert, Altext
  bleibt): 0x0a Byte 5 ist **kein** „RFID gültig“, sondern Typ-7-Tag-Sperrbit; 0x51 ist **nicht**
  nur LED-Mapping, sondern print_check-Fehler; Byte-2-Flag R[3] = Motorblock > 10 s (Bit 4
  Hauptmotor, Bit 0–3 Feeder); 0xEA wird bei normalen Kommandos beantwortet.
- **Neu belegt:** 0x0a Byte 0/1 = Temp °C / Feuchte % (AHT30); 0x15 = Init-Flags von 6
  Komponenten; 0x50 = Runout im Druck-Slot, 0x52 = kein Vorschub, 0x53 nie erzeugt, 0x1a =
  Pull-back 40 s; Auto-Addr-Box-Seite (A0 3×, A1 0–1000 ms); Sensor-Pins PD7–PD4/PE10–PE7/
  PD8/PD9; ein gemeinsames Odometer; Keil-komprimierte `.data`; 1 Tick = 1 ms; RX synchron
  ohne Busy/Duplikaterkennung (Bursts: Ursache Host-seitig vermutet).
- **Code (minimal):**
  - `box.py`: `parse_box_state` → `temp_c` (int8)/`rh` belegt (`temp_c_guess`/`rh_guess`
    Aliase), `inlet_tag_mask` (`b5`/`rfid_valid_mask` Aliase), `block_long`/`block_main_motor`/
    `block_feeder_mask`; `HW_COMPONENTS` + `parse_get_hardware_status` → `components`/`all_ok`
    (`entries` unverändert); `STATUS_NAMES` präzisiert (0x05/0x0a/0x0c/0x14/0x17/0x19/0x50/0x51),
    neu `STATUS_PULL_HUB_ODO_TIMEOUT` 0x1a, `STATUS_NO_FEED` 0x52, Aliase `STATUS_RUNOUT`/
    `STATUS_BUFFER_SLOW`/`STATUS_HUB_TIMEOUT`; 0x53 bewusst ohne Konstante.
  - `cfswatch.py`: Temp/Feuchte ohne „Vermutung“, Motorblock-Flags im Klartext, 0x15
    „HW-Init alles ok“ bzw. Komponentenfehler (Warnung), Typ-7-Tag-Sperre; Selbsttest um
    Live-0x15-Frame, 0x91-Flags und 0x15-Fehlerfall erweitert.
  - `cfsseq.py`: nur Kommentare (mode-4-/p=1-Schwellen, Hub-Sensor-A-Folgefehler).
  - `serial485.py`: nur Kommentar zu 0xEA/0xEB korrigiert (Verhalten unverändert –
    `BROADCAST_ADDRS` bleibt „beliebiger Absender“).
  - `auto_addr.py`: **keine Logikänderung** – Abgleich ergab keinen echten Fehler: A0-Duplikate
    treffen auf `acked == 1` und werden ignoriert; adressierte Box (ignoriert A0) wird über
    0xA3 ONLINE. Neuer Selbsttest 13 mit FW-treuem `FwSerial` belegt beides; FakeSerial-
    Docstring nennt die Vereinfachung.
- Selbsttests 16.10.2026 alle grün: serial485 93/93 · box 179 · boxmini 17 · cfsseq 28 ·
  motor 134 · motor_mini 43 · auto_addr 33 · cfswatch `--selftest` OK.

## 16.10.2026 Live-Abfragen Motor X

- **Quelle:** `LIVE-ABFRAGEN-2026-10-16.md` (Belege: klippy.log + gcode_store des Druckers,
  13:10–13:15 Log-Uhr UTC+8; Scan-Rohdaten `arbeitsdateien-lokal/motor_get_scan.txt` – lokal, nicht im Repo).
  Gesendet wurden nur lesende Creality-G-Codes über Moonraker (lokales Whitelist-Wrapper-Skript,
  das nur lesende Motor-G-Codes durchlässt; nicht im Repo),
  Drucker im Standby, nicht referenziert.
- **Befunde:** 0x0F = Version (`[00]` → 12 B: fireware_ver 1.9.7.81, cmd_system 1.3,
  protection 1.3, flash_param 2.75, logger 1.0); 0x08 = Variablen-Lesepfad (`[idx]` → 4 B LE,
  idx 1..12; Host dekodiert 1–10 float, 11/12 int; Bedeutung offen); 0x0E `[02]` → 0x81 und
  0x0C `[0b]` → 8×00 bestätigt; `MOTOR_READ_ALL_PIN_IO` = GPIO-Abfrage der Main-MCU
  (PB9, PB10, !PB7, PB8, PB5, PB6, PA1, PB15), kein RS485.
- **Vorfall – zwei durch uns ausgelöste Klipper-Shutdowns:** `MOTOR_GET … DATA=0` und
  `MOTOR_GET … DATA=13` (Index-Grenzen beim Scan) lösten in `motor_control_wrapper.so` einen
  internen Fehler aus (`key60: Internal error on command:MOTOR_GET`) → Klipper SHUTDOWN.
  DATA=0 lehnt schon der G-Code-Parser ab („minimum of 1“) und crasht trotzdem. **Beide
  Male per `FIRMWARE_RESTART` (`POST /printer/firmware_restart`) behoben**, danach ~60 s bis
  Motoren/Box bereit (Motor-Init + Auto-Addr laufen neu); kein Druck lief, keine Bewegung.
  **Ursache:** Creality-Host-Bug (Index-Prüfung im Wrapper fehlt/fehlerhaft), nicht der
  Motor – ob der Motor idx ≥ 13 verträgt, ist offen. **Gegenmaßnahme:** Das lokale
  Whitelist-Wrapper-Skript lässt nur noch `DATA=1..12` (Regex) und `NUM=1..4` zu und sendet nur, wenn
  Druckerstatus standby/complete/cancelled/error **und** Klipper-Zustand `ready` ist
  (sonst Abbruch ohne Senden).
- **Code:**
  - `nachbau/motor.py`: `FUNC_VERSION` 0x0F, `FUNC_VARIABLE` 0x08, `VERSION_FIELDS`,
    `VARIABLE_TYPES` (Host-Dekodierung live 16.10.), `build_motor_version_payload`,
    `decode_motor_version`, `build_variable_payload`, `decode_variable`;
    `Motor.query_motor_version()` (Aliase `motor_version`, `motor_version_normal` – ersetzt
    den Stub) und `Motor.read_variable(idx)` (nur 1..12, sonst `ValueError` ohne Senden, mit
    Hinweis auf den Host-Crash; Rückgabe `raw`/`float`/`int`/`type`/`value`). retries=0,
    Timeout 0x0F/0x08 **unbelegt** → 1,0 s. `read_encoder` bleibt gesperrt (Docstring: 0x08
    ist der Variablen-Lesepfad, Encoder-Index offen); `pin_io`-Docstring nennt die GPIOs.
  - `nachbau/motor_mini.py`: dieselben Funktionen eigenständig (ohne Import von motor.py).
  - `nachbau/cfswatch.py`: Motor-Requests 0x08 („Variable lesen idx N“, Warnung außerhalb
    1..12) und 0x0F („Version abfragen“), Antworten dekodiert (Version im Klartext, Variable
    nach Host-Typ + float/int).
  - Selbsttests mit den Live-Frames (TX in data_send-Form, RX Vollframes mit Geräte-CRC;
    keine UUIDs).
- **Doku:** PROTOKOLL-485.md §3/§3.1 (Fremdtabelle 0x08/0x0F, Nachtrag mit Warnung,
  Versions- und Variablentabelle, Pin-IO), PROTOKOLL-GESAMT.md (§1.5, §1.9 Pi-Ersatz, §4, §5),
  CHECKLISTE.md (0x08 teilweise, 0x0F erledigt; neu: Index-Bedeutung, idx ≥ 13 am Motor,
  Host-Crash-Hinweis Pi-Ersatz), lokale Übergabenotiz (nicht im Repo).
- Selbsttests danach alle grün: serial485 93/93 · box 179 · boxmini 17 · cfsseq 28 ·
  motor 154 · motor_mini 58 · auto_addr 33 · cfswatch `--selftest` OK.

## 16.10.2026 REA-Analyse motor_control_wrapper.so + Crash-Traceback

- **Quelle:** statische Analyse `motor_control_wrapper.cpython-39.so` (REA/Ghidra 12.1.4 +
  eigene `llvm-objdump`-Auflöser; nichts gesendet, kein Druckerzugriff), Ergebnis
  `REA-MOTOR-BEFUND-2026-10-16.md` (Arbeitsdateien lokal, nicht im Repo).
  Zusätzlich vom Nutzer belegt: Drucker-`klippy.log` 16.10. 13:12:27, Traceback:
  `motor_control_wrapper.py line 1982 in Motor_Control.motor_get` →
  `box_wrapper.py line 384 in ParseData.parse_num_to_byte` →
  `TypeError: 'NoneType' object cannot be interpreted as an integer`.
- **Befunde:**
  - Crash-Ursache `MOTOR_GET DATA=0/13` **BELEGT**: Creality-`gcode.py` lehnt `DATA` außerhalb
    `minval=1/maxval=12` nur mit Warnung ab (`get_int` → None), `motor_get` rechnet weiter,
    `parse_num_to_byte(None)` → TypeError → key60 → SHUTDOWN. **Kein Frame ging auf den Bus**
    (REA-Variante A bestätigt). Korrigiert die frühere AUDIT-Aussage „Index-Prüfung im Wrapper
    fehlt/fehlerhaft“: die Prüfung existiert, ihr Fehlerpfad ist nur eine Warnung.
  - 0x08-Indexnamen 5–8 = `pos_fdb`/`spd_fdb`/`id_fdb`/`iq_fdb` (Dict `NO_ERR_CODE_TO_GET`);
    1–4/9–12 ohne Host-Namen (bounded negativ).
  - Typkette fest: 1–10/17/18 `<f`, 11–16 `<I` **unsigned** → Nachbau-Korrektur (bisher `<i`).
  - Vollständige `Motor_cmd`-Tabelle 0x01–0x16, `Motor_Addr`, `MOTOR_NUM_TO_NAME`,
    `motor_error_dict` (Bit 0..13 → key781..794), `motor_warning_dict` (Bit 0..2 → key795..797),
    `ERR_CODE_TO_UNION`, Unpack-Format `err_detail_data` `<I8BfHfiiffffffffi`.
  - Interne `MOTOR_GET NUM=1 DATA=7/8 EXTRUDER=1` = `Motor_Control.stats` (Extruder-Überstrom-
    Monitoring, |I| = hypot(id, iq), via transparent); `MOTOR_GET NUM=1 DATA=1` = Hilfetext.
- **Code:**
  - `nachbau/motor.py`: `VARIABLE_NAMES`, `IDX_*_FDB`, `VARIABLE_HOST_FORMATS` (1..18),
    `VARIABLE_TYPES` (1..12, „int“ = `<I`), `decode_variable` (+`name`, `fmt`, `int` unsigned),
    `Motor.read_position_feedback()` (idx 5), `read_speed_feedback()` (6),
    `read_current_feedback()` (7/8, `abs` = hypot), Alias `motor_get`; `read_variable` bleibt
    1..12 (Docstring: G-Code maxval=12 + Host-Crash belegt, 13..18 bewusst gesperrt);
    `read_encoder` gesperrt mit Verweis auf `read_position_feedback`. Konstanten `MOTOR_CMD`,
    `FUNC_REBOOT/ENCODER_CALIBRATE/ELEC_OFFSET_CALIBRATE/FLASH_PARAM/GET/BOOT/SYSTEMID/
    ERR_DETAIL_DATA/CUT_DETAIL_DATA/POS_DATA_SAMPLE/SPD_DATA_SAMPLE`, `MOTOR_ADDR_BY_NUM`,
    `MOTOR_NUM_TO_NAME`, `ERR_DETAIL_FORMAT/FIELDS`, `CUT_DETAIL_FORMAT`, `MOTOR_ERROR_BITS`,
    `MOTOR_WARNING_BITS`, `ERR_CODE_TO_UNION`; Bit-Decoder `decode_error_bits` /
    `decode_warning_bits` (kein `decode_protection(8 B)`: Byte-Layout der 0x0C-Antwort ist
    aus dem Befund nicht ableitbar). Stubs (NotImplementedError, senden nichts):
    `err_detail_data`, `cut_detail_data`, `pos_data_sample`, `spd_data_sample`, `reboot`,
    `boot`, `systemid`. MOTOR_FLASH_PARAM-Hypothesen (inkl. `>I` Big-Endian) nur im Docstring.
  - `nachbau/motor_mini.py`: `VARIABLE_NAMES`, `<I` unsigned, die drei Feedback-Helfer,
    `read_encoder`-Docstring.
  - `nachbau/cfswatch.py`: Namen für idx 5–8 in Request und Antwort
    („Variable lesen idx 8 (iq_fdb)“, „Variable idx 5 pos_fdb = …“).
- **Doku:** PROTOKOLL-485.md §3.1 „NACHTRAG 16.10.2026 (2)“ (Motor_cmd-Tabelle, Indexnamen,
  Typkette, Crash-Mechanismus mit Traceback, Stats-Hook, Fehlerbits, Hypothesen-Tabelle),
  PROTOKOLL-GESAMT.md (§1.5, §1.9 Pi-Ersatz: pos_fdb idx 5, §4, §5), CHECKLISTE.md (0x08
  teilweise 5–8, Crash-Ursache erledigt; neu offen: Skalierung pos_fdb, 0x13–0x16,
  0x0C-Byte-Layout), LIVE-ABFRAGEN-2026-10-16.md („Ursache belegt“), lokale Übergabenotiz (nicht im Repo).
- Selbsttests danach alle grün: serial485 93/93 · box 179 · boxmini 17 · cfsseq 28 ·
  motor 167 · motor_mini 60 · auto_addr 33 · cfswatch `--selftest` OK.

## 16.10.2026 Messfahrt Motor X – Skalierung pos_fdb (idx 5) / idx 11

- **Live-Zugriff (mit ausdrücklicher User-Freigabe, Bett leer):** `G28` komplett (zuvor
  `G28 X Y` + `G1` → „Must home axis first“: Creality-Klipper verlangt für G1 alle Achsen
  referenziert), Messfahrt X 50…250 mm bei Y=175, Z=10, F3000–6000, je Position `M400` +
  `MOTOR_GET` idx 5/11 (nur Index 1..12, kein DATA=0/13), danach Park X175 und **`M84`**
  (Motoren stromlos). Klipper durchgehend ready, kein SHUTDOWN. Rohdaten:
  `LIVE-ABFRAGEN-2026-10-16.md` (Abschnitt „Skalierung pos_fdb“).
- **Befund (BELEGT):** idx 11 = Single-Turn-Position 0..3199 Counts/Umdrehung (3200 =
  200 Vollschritte × 16, 80 Counts/mm bei rotation_distance 40); idx 5 pos_fdb =
  idx11·2π/3200 rad; beide Wrap alle 40 mm; idx 6 spd_fdb im Stillstand ≈ 0.
- **Code:**
  - `nachbau/motor.py`: `POS_COUNTS_PER_REV = 3200`, `POS_ROTATION_DISTANCE = 40.0`,
    `POS_RAD_TOL`, `IDX_POS_COUNTS = 11`, `VARIABLE_NAMES_HOST` (5–8) +
    `VARIABLE_NAMES_DERIVED` ({11: 'pos_counts'}, live abgeleitet, Host-Name unbekannt) →
    `VARIABLE_NAMES`; Helfer `counts_to_rad`, `pos_rad_to_counts`, `counts_to_mm`,
    `counts_delta` (wrap-korrigiert); `Motor.read_position_counts()` (idx 11),
    `read_position_feedback(with_counts=False)` (Default unverändert float, mit
    `with_counts=True` dict pos_fdb/counts/counts_from_rad/mm_in_rev, 2 Frames).
    `read_encoder` bleibt gesperrt (Docstring: „Encoder“ = idx 5/11 single-turn).
    7 neue Selbsttests mit den Live-Werten (690 → 1.35481 rad, Tabelle mit Tol 2e-4 rad,
    800 Counts = 10 mm, Wrap 3090 → 690 = +800, (80·X + 3090) mod 3200, Fake-Bus idx 5+11).
  - `nachbau/motor_mini.py`: `VARIABLE_NAMES[11]`, `POS_COUNTS_PER_REV`, `counts_to_mm`,
    `read_position_counts`, Docstrings; 1 Selbsttest + Name-Check idx 11.
  - `nachbau/cfswatch.py`: idx 11 erscheint als `pos_counts` (Request + Antwort; Tests angepasst).
- **Doku:** PROTOKOLL-485.md §3.1 „NACHTRAG 16.10.2026 (3)“ (Tabelle, Hypothese idx 11
  gestrichen), PROTOKOLL-GESAMT.md (§1.5 0x08, Pi-Ersatz: Position-Feedback single-turn
  3200/rev, Offen-Liste), CHECKLISTE.md (Skalierung pos_fdb + idx 11 erledigt; offen
  idx 1–4/9/10/12 und spd_fdb-Einheit; G1-Referenzierungs-Hinweis), lokale Übergabenotiz (nicht im Repo).
- Selbsttests danach alle grün: serial485 93/93 · box 179 · boxmini 17 · cfsseq 28 ·
  motor 174 · motor_mini 61 · auto_addr 33 · cfswatch `--selftest` OK.

## 16.10.2026 Motor-FW-Befund (statisch) + DIR-Gegenprobe

- **Quelle:** statische Analyse der Motor-Firmware `fw/F008|F012/mot*_022_C30-mot2_002_081.bin`
  (REA/llvm-objdump, kein Druckerzugriff) → `MOTOR-FW-BEFUND-2026-10-16.md` (Arbeitsdateien lokal);
  Live-Werte 16.10. (Fahrt X F600/F1200 sowie neue Gegenprobe idx 12, `LIVE-ABFRAGEN-2026-10-16.md`).
- **Befunde:** CPU WCH QingKe RISC-V (CH32V30x-Klasse), Basis 0x3000 hinter 12-KB-Bootloader,
  Befehlsregistrierung @0x6100–0x63d8 (0x11 heißt in der FW `emergency_pin_mode`); STATUS-Byte jeder
  Motorantwort = Bitfeld (bit0 Stall, bit1 Fehler latched, bit2 Warnung latched); 0x08-Tabelle
  idx 0..18 komplett (1–4 Sollwerte, 5/6 LESO, 9/10 Ia/−Ib, 11 Schrittpuls-Zähler, 12 DIR-Eingang,
  13 EN vermutet, 14 Stall-Flag, 17 °C, 18 V; 15/16 offen; > 18 veralteter Wert); spd_fdb = rad/s;
  0x0C [0b] = Byte 0–3 Fehler u32 LE, Byte 4–7 Warnung u32 LE (FW + Host-Parser), Sub-Codes 00..0B
  inkl. Seiteneffekt 07/09/0B (Notfall-Pin-Freigabe bei stall_mode 2); 0x13–0x16 lesend ohne
  Request-Payload (74 B Snapshots bzw. `0A` + 10 × `<ffff`).
- **Live-Gegenprobe idx 12:** Fahrt −X = 0 (spd_fdb −1,81 rad/s), +X = 1, Stillstand hält die letzte
  Richtung (0 nach Einschalten) → DIR-Eingang bestätigt; die Hypothese „Bewegungs-/Regler-aktiv-Flag“
  (LIVE-ABFRAGEN, Abschnitt Fahrt) ist widerlegt.
- **Korrekturen früherer Aussagen:** „Byte-Layout 0x0C unbelegt / kein `decode_protection`“ (REA-Eintrag)
  → belegt und gebaut; „idx 9/10 = ADC-LSB-Offset“ (Hypothese) → Phasenströme Ia/−Ib, die Stillstands-
  werte sind 2/1 LSB Rauschen/Offset; „idx 11 Name/Bedeutung abgeleitet“ → FW: Schrittpuls-Zähler
  (Name `pos_counts` beibehalten); „0x13–0x16 Request unbelegt“ → ohne Payload.
- **Entscheidung idx 0/13..18:** `read_variable` bleibt bei 1..12 (Creality-Host-Crash bei DATA=0/13
  bleibt relevant für alle, die über G-Code gehen). Neu `read_variable_raw_fw(idx, confirm=False)` für
  idx 0..18: sauber baubar, weil der eigene Bus-Zugriff den Host umgeht und die FW diese Indizes rein
  lesend behandelt; trotzdem `confirm=True`-Pflicht (sonst `PermissionError`, nichts gesendet), weil
  0/13..18 am Motor live nie gesendet wurden; idx > 18 → `ValueError`. In keinem Selbsttest am Bus.
- **Code:**
  - `nachbau/motor.py`: `VARIABLE_TABLE` (idx 0..18: Name, Einheit, Belegstatus, Deutung) →
    `VARIABLE_NAMES` (jetzt 0..14/17/18), `VARIABLE_UNITS`, `VARIABLE_STATUS`, `VARIABLE_FW_FORMATS`;
    `decode_variable` liefert zusätzlich `unit`/`status`; `decode_variable_fw`; Helfer
    `read_reference_values()` (idx 1–4), `read_phase_currents()` (9/10 → ia, ib = −idx10, minus_ib),
    `read_dir_input()` (12), `read_variable_raw_fw()`. 0x0C: `decode_protection()` (u32 LE + Bitnamen),
    `check_protection()` gibt jetzt das **dekodierte dict** zurück, `check_protection_raw()` die 8 B
    (`write_params_raw()` = raw-Alias), `check_protection_code` = Alias (vorher Stub); Konstanten
    `SUB_PROT_NOP … SUB_PROT_CHECK` (00..0B), `SUB_PROT_SIDE_EFFECT`, `SUB_PROT_STATE_CHANGING` –
    01..05/07/09 werden über keine öffentliche Methode gesendet, [0b] nur über `check_protection`
    (wie Stock, Seiteneffekt im Docstring). `decode_motor_status()` + `MOTOR_STATUS_*`.
    0x13–0x16: `decode_err_detail()` (74 B, `cut=True` für 0x14 inkl. `extra_f`),
    `decode_detail_union()`, `decode_samples()` (161 B, Zählbyte 10), Methoden `read_err_detail()`,
    `read_stall_detail()`, `read_pos_samples()`, `read_spd_samples()` (Request ohne Payload,
    retries 0, Timeout 1 s unbelegt, `status` über `transact_full`, Fallback None); Host-Namen
    `err_detail_data`/`cut_detail_data`/`pos_data_sample`/`spd_data_sample` jetzt Aliase statt Stubs.
    `MOTOR_FW_CMD_NAMES`. FakeBus kann `transact_full`.
    Neue Selbsttests: Tabelle/Einheiten, Helfer mit Live-Werten F600 + DIR-Gegenprobe (0/1/1),
    raw_fw-Sperre/Guards + synthetische 0/13/17/18, STATUS-Bits, `decode_protection` gegen eine
    Nachbildung des Host-Bitstring-Parsers (300 Zufallsframes), synthetische 0x0C-/0x13–0x16-Frames
    (Platzhalter-UUID), Längen-/Zählbyte-Guards.
  - `nachbau/motor_mini.py` (knapp): `VARIABLE_NAMES` 1..12 + `VARIABLE_UNITS`, `unit` in
    `decode_variable`, `read_reference_values`/`read_phase_currents`/`read_dir_input`,
    `decode_protection` (Bitnummern), `decode_motor_status`, `check_protection()` dekodiert +
    `check_protection_raw()`; 0x13–0x16 und raw_fw nur in motor.py.
  - `nachbau/cfswatch.py`: 0x08-Antworten mit Namen **und Einheit** („Variable idx 6 spd_fdb = 1.5
    rad/s …“), Request-Namen 0x13–0x16, 0x0C-Antwort ≠ 0 mit Fehler-/Warnbits im Klartext,
    0x13/0x14-Antworten kurz dekodiert, Motor-STATUS ≠ 0 als Warnung mit Bit-Klartext.
- **Doku:** PROTOKOLL-485.md §3.1 „NACHTRAG 16.10.2026 (4)“ (Motor-FW, STATUS-Bitfeld, vollständige
  Variablen-Tabelle, 0x0C-Layout + Subs, 0x13–0x16; Altstellen in NACHTRAG (2) gestrichen/verwiesen),
  PROTOKOLL-GESAMT.md (§1.5, §1.9 Pi-Ersatz, §4 Board-FW), CHECKLISTE.md, LIVE-ABFRAGEN-2026-10-16.md
  (Abschnitt Gegenprobe), neu `MOTOR-FW-BEFUND-2026-10-16.md`, lokale Übergabenotiz (nicht im Repo).
- Selbsttests danach alle grün: serial485 93/93 · box 179 · boxmini 17 · cfsseq 28 ·
  motor 202 · motor_mini 64 · auto_addr 33 · cfswatch `--selftest` OK.

## 16.10.2026 Live-Abfrage 0x13–0x16 (Motor X, Klipper-Webhooks)

- **Anlass:** 0x13–0x16 waren nur FW-statisch belegt (Motor-FW-Befund), Request-Payload und
  Feldreihenfolge der Proben „vermutet“, 0x14-Zusatzfloat ohne Host-Namen (`extra_f`).
- **Beleg:** klippy.log-Auszug 16.10. 17:26:57–58 Log-Uhr (lokal `arbeitsdateien-lokal/`, enthält echte
  UUID → nicht im Repo), Motor X `0x81`, Stillstand nach `M84`, Klipper durchgehend `ready`. Aufruf
  über die Klipper-Webhook-Endpunkte `motor_err_detail_data` (Bus 0x13 → 0x15 → 0x16) und
  `motor_cut_detail_data` (0x14 → MOTOR_GET idx 5–8), Parameter `{"num": 1}`; `motor_dev_uuid` nicht
  benutzt, `motor_param_modifi_data` (vermutlich schreibend) NICHT angefasst.
- **Befund:** Requests `81 04 00 1x 02` (Host-Payload `[02]`, FW ignoriert sie); Antworten STATUS 00,
  0x13/0x14 len 0x4d → 74 B, 0x15/0x16 len 0xa4 → 161 B (Zählbyte 0x0A + 10 × `<ffff`). Host-JSON =
  Nachbau-Dekodierung für alle Felder (0x13: err_code 0, elec_offset 123,58, err_deg 3,867, Rest 0;
  0x14: encoder_fdb 4421 … 24,365 V, 50,32 °C, param_peak_overcurrent_A 0,012; 0x15: 10 × 2,15985 /
  2,15985 / 2,159847 / 0; 0x16: 10 × ≈ 0).
- **Korrekturen `nachbau/motor.py`:**
  - `_read_detail()` sendet jetzt `DETAIL_REQUEST_PAYLOAD = (0x02,)` wie der Host (vorher leere
    Payload `81 03 00 1x`; funktional gleich, jetzt byte-gleich zum Stock-Verkehr).
  - `CUT_DETAIL_EXTRA_FIELD` `"extra_f"` → `"param_peak_overcurrent_A"` (Host-Name). Übrige 0x13/0x14-
    Feldnamen entsprachen bereits den Host-Namen (`board_detect_power_supply_V` usw.) – unverändert.
  - Docstrings `read_err_detail`/`read_stall_detail`/`read_pos_samples`/`read_spd_samples`,
    `decode_err_detail`/`decode_samples`, Modulkopf und Konstanten-Kommentare: „LIVE bestätigt 16.10.“;
    Reihenfolge der Probenfelder = Host (pos Feld 3/4 eindeutig, Feld 1/2 und spd im Stillstand nicht
    unterscheidbar – so dokumentiert). `TIMEOUT_DETAIL` bleibt 1 s (Host-Timeout unbelegt; live
    0,014–0,174 s laut Log-Stempeln).
  - Selbsttest 7e: Live-Frames 0x13–0x16 (UUID-Bytes 3–8 → `00` als Platzhalter, CRC-8/ATM neu: 0x13
    `a2`, 0x14 `c0`; 0x15/0x16 mit Original-Geräte-CRC `6f`/`1e`) gegen alle Host-JSON-Werte, Requests
    `81 04 00 1x 02` (FakeBus prüft die TX-Bytes), Reihenfolge wie die Webhooks; synthetische Frames
    bleiben für die Union-Dekodierung (live nur err_code 0) und die Guards.
- **Doku:** PROTOKOLL-485.md §3.1 NACHTRAG (5) (Endpunkt-Weg, Feldtabelle mit Host-Namen),
  CHECKLISTE.md, LIVE-ABFRAGEN-2026-10-16.md (Abschnitt „0x13–0x16 live“, ohne UUID),
  MOTOR-FW-BEFUND-2026-10-16.md (Live-Nachtrag), lokale Übergabenotiz (nicht im Repo).
- **Offen:** Union-Inhalte bei err_code ≠ 0; Bedeutung/Einheit von `param_peak_overcurrent_A` (0,012
  bei err_code 0); Host-Timeout.
- Selbsttests danach alle grün: serial485 93/93 · box 179 · boxmini 17 · cfsseq 28 ·
  motor 208 · motor_mini 64 · auto_addr 33 · cfswatch `--selftest` OK.

## Anonymisierung (Repo-Fassung)

Gegenüber der lokalen Forschungsablage sind im Repo folgende Werte durch syntaktisch
gültige Platzhalter gleicher Länge ersetzt; betroffene Frames haben neu berechnete,
verifizierte CRCs (CRC-8/ATM über `[len..data]`), die Selbsttests laufen damit grün:

| Wert | Platzhalter im Repo |
|---|---|
| Drucker-IP | `192.168.1.100` |
| Hostname | `K2Plus-XXXX` |
| CFS-UUID (12 Byte, `mb_addr_table_uniids`) | `a0 a1 … ab` (auch als `0xA0, …`-Liste/Store-String) |
| Auto-Addr-Frame 0xa2 mit UUID | `f7 01 11 00 a2 01 00 a0 … ab f0` (CRC neu: `0xf0`) |
| Tag-/Box-SN (19 Zeichen, 0x14) | `SN-DEMO-1234567890X` (auch als ASCII-Hex im 0x14-Frame) |
| Version/SN-Frame 0x14 | `f7 01 19 00 14 … 58 d4` (CRC neu: `0xd4`; Original-CRC `0x8d`) |
| RFID-Vendor (40 Zeichen) | `101250000001010010FFFFFF0000000000000000` – strukturierter, synthetischer Platzhalter (ersetzt den früheren 40×`A`), zerfällt per `box.split_rfid_vendor()` in month `1` · day `01` · year `25` · supplier `0000` · batch `00` · mat_id `101001` · col `0FFFFFF` · len `0000` · number `000000` · reserve `000000`. mat_id/col sind nicht identifizierend und wurden beibehalten; die echten day/year/supplier/batch/len/number sind entfernt. Auch als ASCII-Hex im 0x02-Frame und als gekürzte Präfixe (`10125`) |
| RFID-Frame 0x02 `A:<vendor>;` | `f7 01 2e 00 02 41 3a 31 30 … 30 3b d7` (CRC neu: `0xd7`; Original-CRC `0x02` bezog sich auf die echten Bytes) |
| Motor-UUID (0x12-Antwort, 8 Byte, Präfix `cd ab`) | `cd ab 00 00 00 00 00 00` (Antwortframe `f7 81 0b 00 12 cd ab 00×6 ad`, CRC neu: `0xad`) |
| Gekürzte Angaben (`…`) in der Doku | nur Platzhalter-Präfixe (`SN-DEMO-…`, `1012…`, Vendor-Felder `1`·`01`·…), nie echte Teilstücke |
| cfswatch-Selbsttest (`SELFTEST_LOG`) | RFID-Frame mit Platzhalter-Vendor (CRC `0xd7`), 0x14 mit `153SN-DEPLATZHALTER00` (CRC `0x42`), 0xa2 mit UUID-Präfix `a0 a1` (CRC `0x25`); Bedienbeispiel mit neutralem `ssh root@<drucker> …` statt lokalem Login-Alias |
| Zugangsdetails (lokale Wrapper-/Login-Pfade, SSH-Alias, Passwort-/Agent-Konfiguration, Passwörter) | neutral formuliert, z. B. „lokales Whitelist-Wrapper-Skript für lesende Motor-G-Codes über Moonraker“; die Whitelist-Idee und die Absturz-Warnung (`MOTOR_GET DATA=0/13` → Klipper-SHUTDOWN) sind inhaltlich vollständig erhalten |
| Motor-`dev_uuid` (z. B. aus Moonraker-Statusobjekten) | nicht übernommen; Live-Abfragen 16.10. (`LIVE-ABFRAGEN-2026-10-16.md`) enthalten keine UUIDs, Motor-UUID nur als Platzhalter `cd ab 00×6` |

Nicht im Repo: Klartextquelle `auto_addr_wrapper.py` (Creality, 729 Zeilen; Zeilenangaben
„Orig :<zeile>“ bzw. „:604-607“ beziehen sich auf sie), Firmware-/Config-Tarball,
lokale Übergabenotizen, die Rohdaten der Live-Abfragen 16.10. (`arbeitsdateien-lokal/`), das lokale Whitelist-Wrapper-Skript, die Live-Logs `live-logs-2026-10-15/` (Zeilenangaben wie
`klippy.log:2512` beziehen sich auf sie) sowie Zugangsdaten/Login-Hinweise zum Drucker (auch der lokale SSH-Alias aus den cfswatch-Beispielen). Die Seriennummer aus den Dumps von `fake-name/cfs-reverse-engineering`
ist nicht übernommen.
