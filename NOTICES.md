# Herkunfts- und Lizenzhinweise (NOTICES)

## Eigene Arbeit

Alle Dateien unter `nachbau/` sowie die Dokumentation (`*.md`) sind eigene,
unabhängig erstellte Arbeit und stehen unter der **GNU General Public
License v3.0 or later** (siehe `LICENSE`).

## Quellen Dritter in diesem Repo

- `nachbau/ref-hi/test_vectors.json`, `nachbau/ref-hi/valid_messages.json`:
  Testvektoren aus `gitstonelabs/creality-cfs-klipper` (GPL-3.0), nur zur
  Kreuzvalidierung des CRC-/Frame-Parsers beigelegt.
- `belt_mdl-live.py`: Klartext-Python-Datei aus der Drucker-Firmware mit
  ausdrücklichem GPLv3-Header im Dateikopf.
- `auto_addr_wrapper.py` (Creality-Klartextquelle, 729 Zeilen) ist NICHT im Repo
  enthalten; der Port `nachbau/auto_addr.py` dokumentiert daraus nur
  Protokollfakten (Kommandos, Timeouts, Adressverfahren).
- `fw/`, `rootfs-live/`, `klippy/`, `config/`, `live-1.1.7.0-configs/`:
  Firmware-Artefakte und Konfigurationen vom Gerät (Creality), beigelegt als
  Forschungs-/Referenzmaterial für die Protokollanalyse.
- Ghidra-Decompile-Auszüge in der Doku (inkl. `GHIDRA-CFS-BEFUND-2026-10-15.md`): eigene
  Analyseergebnisse, auf Belegzeilen mit Adressen gekürzt, keine Creality-Quelltexte.
- Protokoll-Kenntnisse aus `gitstonelabs/creality-cfs-klipper` (GPL-3.0) und
  `prestonbrown/helixscreen`-Recherche-Dokumenten wurden zum Abgleich und zur
  Korrektur eigener Befunde verwendet; übernommene Fakten sind jeweils belegt.
- `grant0013/k2-reverse-engineering` (GPL-3.0, Creality K2): Motor-Parameter-IDs
  (z. B. ID 70 = Positionsregler-Kp) und Motor-Kommandobytes wurden zum Abgleich
  genutzt und als Fremd-Hypothesen gekennzeichnet; keine Dateien übernommen.
- `fake-name/cfs-reverse-engineering` (https://github.com/fake-name/cfs-reverse-engineering,
  Connor Wolf; keine Lizenz außer GPL3 für die Adapter-PCBs): CFS-Hardware-/Firmware-Fakten
  (Flash-Map/Bootloader, Bauteile, RFID-vender-Feldlayout) nur als Fakten mit Quellenangabe
  übernommen; keine Texte, Fotos oder Dumps, keine Seriennummern.

## Hinweis

Dieses Projekt ist eine unabhängige Reverse-Engineering-Dokumentation. Es
enthält keine Creality-Quelltexte jenseits der oben genannten, im
Firmware-Image als GPL gekennzeichneten Python-Datei (`belt_mdl-live.py`).
