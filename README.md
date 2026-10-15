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
| `GHIDRA-CFS-BEFUND-2026-10-15.md` | Ghidra-Neuanalyse der CFS-FW R153: Dispatcher, Func-/Status-Tabelle, 0x0a-Layout |
| `nachbau/` | Python-Module: `serial485`, `boxmini`, `box`, `motor_mini`, `motor`, `cfsseq`, `auto_addr` – alle mit Selbsttests |
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
```

Echte Hardware braucht `pyserial`; der Import erfolgt erst bei Benutzung
(`Serial485(port, baud)`), die Tests laufen ohne.

Stand der Selbsttests (15.10.2026 nach Live-Log-Einarbeitung, `ok`-Zeilen, 0 FAIL):
`serial485` 81/81 (inkl. ref-hi: 16 CRC-Vektoren + 12 Nachrichten), `box` 126,
`boxmini` 17, `cfsseq` 28, `motor` 98, `motor_mini` 30, `auto_addr` 31.

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

Status 15.10.2026: all self-tests green (serial485 81/81 incl. Hi reference
vectors, box 126, boxmini 17, cfsseq 28, motor 98, motor_mini 30, auto_addr 31).

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
