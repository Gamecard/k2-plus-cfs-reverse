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
/home/pro/k2-fw-live-1.1.7.0/nachbau/
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

Nicht im Repo: Klartextquelle `auto_addr_wrapper.py` (Creality, 729 Zeilen; Zeilenangaben
„Orig :<zeile>“ bzw. „:604-607“ beziehen sich auf sie), Firmware-/Config-Tarball,
lokale Übergabenotizen, die Live-Logs `live-logs-2026-10-15/` (Zeilenangaben wie
`klippy.log:2512` beziehen sich auf sie) sowie Zugangsdaten/Login-Hinweise zum Drucker. Die Seriennummer aus den Dumps von `fake-name/cfs-reverse-engineering`
ist nicht übernommen.
