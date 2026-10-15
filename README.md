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
| `nachbau/` | Python-Module: `serial485`, `boxmini`, `box`, `motor_mini`, `motor`, `cfsseq`, `auto_addr` – alle mit Selbsttests |
| `nachbau/ref-hi/` | Unabhängige Hi-CFS-Testvektoren (CRC/Frames) zur Kreuzvalidierung |
| `fw/`, `klippy/`, `config/`, `rootfs-live/` | Live-Artefakte vom Drucker (1.1.7.0) |
| `ghidra-scripts/` | Ghidra-Skripte (CFS-Import, Funktionslisten, CRC-Suche) |
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

Stand der Selbsttests (Review 15.10.2026, `ok`-Zeilen, 0 FAIL): `serial485` 81/81
(inkl. ref-hi: 16 CRC-Vektoren + 12 Nachrichten), `box` 68, `boxmini` 15, `cfsseq` 17,
`motor` 65, `motor_mini` 20, `auto_addr` 31.

Wichtige Korrekturen aus dem Review 15.10.2026 (Details: `AUDIT-UND-AENDERUNGEN.md`):

- Motor-Func `0x06` ist **kein** Encoder-Lesen, sondern Sys-Param (LE-Float
  300.0/100.0 = `MOTOR_SYS_PARAM ID=70`); `read_encoder` ist gesperrt.
- CFS-Preload `0x0d [Maske][Aktion]`: `0x00` = disable, `0x01` = enable,
  `0x02` = Force-RFID-Read (die frühere Arm/Disarm/Rearm-Belegung ist widerlegt).
- Slot-Parameter sind Masken (`check_mask`, `slot_to_mask`), Remain `0` = kein
  RFID-Restwert (nicht „leer“).

## Anonymisierung

IP, Hostname, Geräte-SN, RFID-Vendor-Bytes und CFS-UUID sind anonymisiert
(syntaktisch gültige Platzhalter gleicher Länge, CRCs neu berechnet und
verifiziert). Details in `AUDIT-UND-AENDERUNGEN.md`.

## English summary

Open documentation and a working Python reimplementation of the proprietary
RS485 protocol between the Creality K2 Plus host board and its lower boards
(CFS filament box, closed-loop motors, belt modules). All findings are backed
by live captures and Ghidra analysis. License: GPL-3.0-or-later.

Review 15.10.2026: all self-tests green (serial485 81/81 incl. Hi reference
vectors, box 68, boxmini 15, cfsseq 17, motor 65, motor_mini 20, auto_addr 31).
Corrections: motor func `0x06` is a system-parameter write (LE float), not an
encoder read (`read_encoder` disabled); CFS preload `0x0d` actions are
`0x00` disable / `0x01` enable / `0x02` force RFID read; slot arguments are
bit masks; remain `0` means "no RFID remain value", not "empty".
