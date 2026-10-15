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
| RFID-Tag-SN (19 Zeichen) | `SN-DEMO-1234567890X` (auch als ASCII-Hex im 0x14-Frame) |
| Version/SN-Frame 0x14 | `f7 01 19 00 14 … 58 d4` (CRC neu: `0xd4`) |
| RFID-Vendor-Hex (40 Zeichen) | `AAAA…` (40×`A`, auch als ASCII-Hex `41 …` und gekürzte Präfixe) |
| RFID-Frame 0x02 `A:<vendor>;` | CRC neu: `0x78` (Original-CRC `0x02` bezog sich auf die echten Bytes) |

Nicht im Repo: Klartextquelle `auto_addr_wrapper.py` (Creality, 729 Zeilen; Zeilenangaben
„Orig :<zeile>“ bzw. „:604-607“ beziehen sich auf sie), Firmware-/Config-Tarball und
lokale Übergabenotizen.
