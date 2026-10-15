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
- Ghidra-Decompile-Auszüge in der Doku: eigene Analyseergebnisse, keine
  Creality-Quelltexte.
- Protokoll-Kenntnisse aus `gitstonelabs/creality-cfs-klipper` (GPL-3.0) und
  `prestonbrown/helixscreen`-Recherche-Dokumenten wurden zum Abgleich und zur
  Korrektur eigener Befunde verwendet; übernommene Fakten sind jeweils belegt.

## Hinweis

Dieses Projekt ist eine unabhängige Reverse-Engineering-Dokumentation. Es
enthält keine Creality-Quelltexte jenseits der oben genannten, im
Firmware-Image als GPL gekennzeichneten Python-Datei (`belt_mdl-live.py`).
