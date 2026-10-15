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

## Anonymisierung

IP, Hostname, Geräte-SN, RFID-Vendor-Bytes und CFS-UUID sind anonymisiert
(syntaktisch gültige Platzhalter gleicher Länge, CRCs neu berechnet und
verifiziert). Details in `AUDIT-UND-AENDERUNGEN.md`.

## English summary

Open documentation and a working Python reimplementation of the proprietary
RS485 protocol between the Creality K2 Plus host board and its lower boards
(CFS filament box, closed-loop motors, belt modules). All findings are backed
by live captures and Ghidra analysis. License: GPL-3.0-or-later.
