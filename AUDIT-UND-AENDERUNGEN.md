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
  - `build_preload(mask, phase)` für 0x0D `[Maske, Phase]`
  - `build_extrude_stage(slot, stage, parameter)` für 0x10
  - `build_extrude_sequence(slot, pushes)`
  - `build_retrude_sequence(slot)` für 0x11 START `[slot,00]` und FINISH `[slot,01]`
- 0x10-Stufen dokumentiert: init 00, engage 04, push 05, settle 06,
  finalize 07/03.
- Der Trace-Prüfer erzwingt nicht mehr fälschlich 0x05/0x04/0x0F nach dem
  letzten 0x10-Frame. Logframes können Antworten statt Requests sein.
- Slot-Indizes 0..3 werden eindeutig in Masken 0x01/0x02/0x04/0x08 übersetzt.

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

Ausgeführt am lokalen Nachbau:

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
  Response-Registry und unsolicited-event routing.
- `motor_mini.py` ist nur ein geprüfter Ausschnitt; die normale Motor-Version-API
  und alle Closed-loop-/Protection-Funktionen sind nicht vollständig nachgebaut.
- `boxmini.has_filament()` nutzt Restmenge als einfachen Beleg; für vollständige
  Präsenzlogik müssen 0x02, 0x03, 0x08 und 0x0A gemeinsam ausgewertet werden.

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
3. `motor_mini.py`: `read_encoder(register)` ohne Default — ein stilles
   `00 00 00 00` wurde nie auf dem Bus beobachtet; Selbsttest prüft den
   TypeError bei fehlendem Argument.
