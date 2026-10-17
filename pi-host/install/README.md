# K2 Plus – Installationspaket für den Pi-Host

> **ENTWURF – Stand 17.10.2026, am Gerät UNGETESTET.** Nur in einer Attrappe (Fake-HOME, Fake-systemctl) Ende-zu-Ende geprüft.

Richtet einen Pi (CM5, MainsailOS/Debian 13, aarch64) in **einem Schritt** ein: Upstream-Klipper auf den geprüften Stand `9cb9975`, K2-Module nach `klippy/extras/`, Config nach `~/printer_data/config/`, Offline-Tests + Batch-Prüfung auf dem Pi. Bewegt nichts, startet Klipper nur auf Wunsch. Grundlage: `pi-host/config-pi/README.md` §3 (Installation) und §4 (Inbetriebnahme).

## 1. Dateien

| Datei | Läuft auf | Zweck |
|---|---|---|
| `pack.sh` | Arbeits-PC | baut `k2-pi-paket-<datum>.tar.gz` (+ `.sha256`) aus dem aktuellen Stand |
| `install.sh` | Pi, als `pi` | Installation in 7 Schritten (s. §4) |
| `uninstall.sh` | Pi, als `pi` | Rückbau aus `~/k2-pi-backup/` |
| `k2cfg.py` | Pi | Hilfswerkzeug: serial-Pfade/SAVE_CONFIG beim erneuten Installieren übernehmen, Platzhalter melden |

## 2. Paket bauen (Arbeits-PC)

```bash
pi-host/install/pack.sh            # Ausgabe: arbeitsdateien-lokal/pi-paket/
pi-host/install/pack.sh --tests    # vorher alle test_k2_*.py im Paket laufen lassen
pi-host/install/pack.sh -o /pfad   # anderer Ausgabeordner
```

Inhalt: `pi-host/k2_*.py`, `pi-host/test_k2_*.py` (+ `prtouch_mcu_formats.json`, Modul-READMEs), `pi-host/config-pi/` (cfg + Notizen), `nachbau/` (+ `ref-hi/`), `belt_mdl-live.py` (GPLv3-Original für den Belt-Vergleichstest), `mcu-dicts/` (F008-Dictionaries Main/Düse für Batch + lis2dw-Test), Skripte, `PAKET-INFO.txt`, `SHA256SUMS`.
**Nicht** enthalten: Geräteskripte fürs Stock-Gerät (`bustest_lesend.py`, `emu_prtouch_stock.py`, `prtouch_lesetest.py`, `prtouch_fingertest.py`, `prtouch_vorabtest.py`), Logs, Befunde, Übergabe-/Zugangsnotizen, **Creality-Materialdatenbank** (`material_database*.json`, s. §4a), **Düsen-Material-Zustand** (`remain_material*.json`, s. §4a). `pack.sh` bricht bei Zugangsdaten-/IP-Mustern im Inhalt ab, ebenso wenn eine Material-DB dem Namen nach (Paketordner und Tarball) oder dem Inhalt nach (JSON mit `meterialType`/`printerIntName`/`kvParam`) hineingeriete; dasselbe für `remain_material*.json` (Name bzw. JSON mit Schlüssel `"remain_material"`).

## 3. Auf den Pi kopieren und starten

```bash
scp arbeitsdateien-lokal/pi-paket/k2-pi-paket-<datum>.tar.gz pi@<pi-host>:~
ssh pi@<pi-host>
tar xzf k2-pi-paket-<datum>.tar.gz
cd k2-pi-paket-<datum>
./install.sh                     # Standard: printer.cfg bleibt, K2-Datei als printer-k2.cfg
```

Typischer Ablauf am Drucker (Lower-Board + Adapter angesteckt):

```bash
ls -l /dev/serial/by-id/                         # Namen ablesen
./install.sh --printer-cfg-ersetzen \
  --main-serial  /dev/serial/by-id/usb-...-if00-port0 \
  --duese-serial /dev/serial/by-id/usb-...-if00-port0 \
  --rs485-serial /dev/serial/by-id/usb-...-if00-port0
sudo systemctl start klipper                     # oder gleich mit --starten
```

## 4. Was `install.sh` tut

| Schritt | Inhalt | Abbruch bei |
|---|---|---|
| 1 Vorprüfungen | nicht root, `aarch64`, `SHA256SUMS`, `~/klipper` ist git-Checkout, `~/printer_data/config` da, ≥ 200 MB frei, Python (`~/klippy-env`), `sudo -n`, Dienst `klipper.service`; mit `--material-db`: Datei lesbar, gültiges JSON, Struktur (`nachbau/material_db.py --struktur`, ≥ 1 Eintrag); mit `--remain-material`: Datei lesbar, gültiges JSON, Struktur + Farbformat (§4a); Backup-Ordner `~/k2-pi-backup/<stamp>/` | jeder Fehlschlag (vor jeder Änderung) |
| 2 Klipper | HEAD ≠ `9cb9975` → nur bei **sauberem** Checkout `git checkout --detach 9cb9975` (fehlt der Commit: `git fetch origin`); eigene Dateien zählen nicht als Änderung. `.git/info/exclude` bekommt `klippy/extras/k2_*.py` + `k2_nachbau/` (Moonraker sieht den Checkout als sauber). | lokale Änderungen (Liste wird gezeigt) |
| 3 Dienst | `sudo systemctl stop klipper` (Zustand gemerkt) | stop scheitert |
| 4 Module | Backup vorhandener `extras/k2_*.py` + `k2_nachbau/`; `pi-host/k2_*.py` → `extras/` (**`k2_motor_init.py` → `k2_motor.py`**, Dateiname = Abschnitt `[k2_motor]`), `nachbau/*.py` → `extras/k2_nachbau/`; jede Kopie per `cmp` geprüft. Mitinstalliert werden auch `k2_lis2dw.py` (Pflicht, `[k2_lis2dw]` in printer.cfg) und `k2_zalign.py` (nur aktiv, wenn `[k2_zalign]` in zalign.cfg einkommentiert wird) | Kopierfehler |
| 5 Config | `~/printer_data/config` **komplett** als `config.tar.gz` gesichert; alle `config-pi/*.cfg` außer `printer.cfg` kopiert; `printer.cfg`: keine vorhanden → wird `printer.cfg`; vorhanden → nur mit `--printer-cfg-ersetzen` ersetzt, sonst `printer-k2.cfg` daneben + Hinweis; Material-DB s. §4a | – |
| 6 Prüfung | alle `test_k2_*.py` (mit `KLIPPER_SRC=~/klipper`), nachbau-Selbsttests, Batch-Lauf `klippy.py <cfg> -i /dev/null -d <dicts>` wie `lauf-modulfix.sh` (Lauf F) gegen die **installierten** Module + Config; Logs in `<backup>/tests/`, `<backup>/batch/` | ein Test rot / Batch ≠ EXIT 0 → Dienst bleibt gestoppt |
| 7 Abschluss | Dienst nur mit `--starten`; Zusammenfassung, `ls -l /dev/serial/by-id/`, verbleibende Platzhalter, nächste Schritte §4 | – |

**Erneut ausführen** ist ausdrücklich vorgesehen (z. B. nach neuem Paket): eingetragene `serial:`-Pfade (`[mcu]`, `[mcu nozzle_mcu]`, `[k2_485]`) werden aus den bisherigen Dateien übernommen; beim Ersetzen einer bisherigen K2-`printer.cfg` bleibt deren SAVE_CONFIG-Block (Kalibrierung) erhalten, außer mit `--save-config-aus-paket`. Jeder Lauf legt ein neues Backup an (zwei Läufe in derselben Sekunde: Suffix `-2`, `-3` …; vor dem 17.10.2026 überschrieb der zweite das erste).

### Optionen

| Option | Wirkung |
|---|---|
| `--klipper-nicht-anfassen` | Checkout nicht umstellen (nur Warnung, Stand dann ungeprüft) |
| `--printer-cfg-ersetzen` | vorhandene `printer.cfg` ersetzen (Original im Backup) |
| `--save-config-aus-paket` | SAVE_CONFIG-Block des Pakets statt des bisherigen |
| `--main-serial` / `--duese-serial` / `--rs485-serial PFAD` | by-id-Pfade direkt eintragen |
| `--material-db DATEI` | Creality-Materialdatenbank nach `~/printer_data/config/k2/material_database.json` kopieren (§4a) |
| `--remain-material DATEI` | Stock-`remain_material_data.json` (Material in der Düse) als Startwert nach `~/printer_data/config/k2/remain_material.json` kopieren (§4a) |
| `--dienst NAME` | anderer systemd-Dienst als `klipper` |
| `--starten` | Klipper am Ende starten |
| `--ohne-tests` / `--ohne-batch` | Prüfungen auslassen |
| `--arch-egal` | nur für Probeläufe auf x86_64 |

Umgebungsvariablen für abweichende Installationen: `KLIPPER_DIR`, `CONFIG_DIR`, `KLIPPY_ENV`, `BACKUP_ROOT`.

### Wo die by-id-Namen hinkommen

| Gerät | Datei | Abschnitt |
|---|---|---|
| USB-UART Main-MCU | `printer.cfg` | `[mcu] serial:` |
| USB-UART Düsen-MCU | `printer.cfg` | `[mcu nozzle_mcu] serial:` |
| USB-RS485-Adapter | `k2-module.cfg` | `[k2_485] serial:` |

`baud: 230400` bleibt überall. Welcher UART welche MCU ist: nacheinander anstecken und `ls -l /dev/serial/by-id/` vergleichen.

## 4a. Materialdatenbank (`material_database.json`)

`[k2_box]` liest Temperatur/Geschwindigkeit je Material aus der Creality-Datenbank (Befund `PAUSE-RESUME-MATERIAL-BEFUND-2026-10-17.md` §B, Vorschlag P4). Die Datei ist eine Creality-Datei bzw. eine Benutzer-Kopie mit Cloud-Kennungen und kommt deshalb **nie ins Paket/Repo**. Sie wird einmal vom Stock-Drucker geholt (solange das Stock-Upper-Board noch läuft) und bei der Installation mitgegeben:

```bash
# am Arbeits-PC oder Pi, Stock-Drucker im Netz:
scp root@<drucker>:/mnt/UDISK/creality/userdata/box/material_database.json .
#   (diese Benutzer-Kopie liest der Stock-Host-Code; fehlt sie, den Firmware-Default
#    /etc/sysConfig/defData/material/material_database.json holen)
./install.sh --material-db material_database.json [übrige Optionen]
```

- Ziel fest: `~/printer_data/config/k2/material_database.json` (Rechte 0644, Ordner `k2/` wird bei Bedarf angelegt). Diesen Pfad trägt `k2-module.cfg` bei `[k2_box]` ein.
- Prüfung in Schritt 1, also **vor jeder Änderung**: gültiges JSON, Struktur über `nachbau/material_db.py --struktur` (`result.list` vorhanden, ≥ 1 Eintrag); Zählwerte (Einträge, Doppel-IDs, Einträge ohne `nozzle_temperature`) werden angezeigt. Kaputte Datei → Abbruch, nichts verändert.
- Eine vorhandene DB wird nach `<backup>/material-db/` gesichert (zusätzlich zu `config.tar.gz`); identische Datei → bleibt unverändert.
- **Ohne** `--material-db` bleibt eine vorhandene DB unverändert (sie wird nur geprüft, kaputt → Warnung). Fehlt sie, meldet die Zusammenfassung den `scp`-Befehl; `k2_box` arbeitet dann mit den Festwerten.
- Ein von Hand geänderter Wert `material_database:` in `[k2_box]` (`k2-module.cfg`) bleibt bei Wiederholung erhalten (`k2cfg.py`), solange die Paket-Datei die Zeile aktiv enthält.
- Rückbau: Die DB wird auf den Stand des gewählten Backups gebracht – gesicherte Datei zurück, bzw. entfernt (+ leerer Ordner `k2/`), wenn es sie vor diesem Lauf nicht gab (auch mit `--aelteste`).

### Material in der Düse (`remain_material.json`)

Stock merkt sich nach jedem erfolgreichen Spülen Farbe und Typ des Materials in der Düse (`RemainMaterial`, Befund `REMAIN-MATERIAL-BEFUND-2026-10-17.md`). `[k2_box]` liest/schreibt dasselbe Format über die Option `remain_material_file` (Default `~/printer_data/config/k2/remain_material.json`). Fehlt die Datei, ist das Düsenmaterial nach dem Start unbekannt und der erste Wechsel spült pauschal (`Tn_extrude`). Den letzten Stock-Stand kann man einmalig als Startwert übernehmen:

```bash
scp root@<drucker>:/mnt/UDISK/creality/userdata/box/remain_material_data.json .
./install.sh --remain-material remain_material_data.json [übrige Optionen]
```

- Format (Stock, eine Zeile): `{"remain_material": {"color": "0RRGGBB"|null, "type": "<id>"|null}}`. Ziel fest: `~/printer_data/config/k2/remain_material.json` (anderer Dateiname als am Drucker, Inhalt 1:1, Rechte 0644, Ordner `k2/` bei Bedarf angelegt).
- Prüfung in Schritt 1, **vor jeder Änderung**: gültiges JSON, oberste Ebene Objekt mit `remain_material` als Objekt, darin `color` und `type` vorhanden; `color` = `null` oder `^0[0-9A-Fa-f]{6}$`, `type` = `null` oder nicht-leerer Text; Zusatzschlüssel (z. B. `tnn`) erlaubt. Sonst Abbruch, nichts verändert. `color`/`type` werden angezeigt; beide `null` (Stock-Grundzustand) ist gültig, bringt aber nichts.
- Eine vorhandene Datei wird nach `<backup>/remain-material/` gesichert; identische Datei → unverändert; abweichende → ersetzt (mit Warnung, weil sie im Betrieb von `k2_box` geschrieben sein kann).
- **Ohne** `--remain-material` wird eine vorhandene Datei **nie** angefasst – sie gehört im Betrieb `k2_box` (nur geprüft, unbrauchbar → Warnung). Fehlt sie oder ist sie unbrauchbar, nennt die Zusammenfassung den `scp`-Befehl. Die Option ist also nur für den Umstieg sinnvoll; ein späterer Lauf mit derselben alten Stock-Datei würde den inzwischen von `k2_box` gespeicherten Stand zurückdrehen.
- Kommt nie ins Paket (s. §2).
- Rückbau: wie bei der DB auf den Stand des gewählten Backups (gesicherte Datei zurück bzw. entfernt, wenn es sie vor diesem Lauf nicht gab). ACHTUNG: Das gilt auch für eine Datei, die `k2_box` erst im Betrieb angelegt hat – `--aelteste` entfernt sie.

## 5. Rückbau

```bash
./uninstall.sh              # letztes Backup (~/k2-pi-backup/letzte), mit Rückfrage
./uninstall.sh --aelteste   # Zustand vor der ERSTEN Installation
./uninstall.sh --backup ~/k2-pi-backup/<stamp> --ja
```

Stoppt Klipper, entfernt alle `extras/k2_*.py` + `k2_nachbau/` und spielt die gesicherten zurück, sichert den aktuellen Config-Ordner zusätzlich (`~/k2-pi-backup/vor-rueckbau-*.tar.gz`), entfernt neu angelegte cfg-Dateien und entpackt `config.tar.gz`, stellt den Klipper-Checkout auf Branch/Commit von vorher zurück (nur wenn er noch sauber auf `9cb9975` steht; `--klipper-lassen` unterdrückt das), entfernt die `.git/info/exclude`-Einträge. Startet Klipper nur mit `--starten`. Eine Kopie von `uninstall.sh` liegt in jedem Backup-Ordner.

## 6. Hinweise / ANNAHMEN

- `mainsail.cfg` wird von der K2-`printer.cfg` **nicht** eingebunden (eigene PAUSE/RESUME/CANCEL_PRINT, `[virtual_sdcard]`, `[pause_resume]`, `[display_status]`). Doppelte Makro-Abschnitte würden sich still vermischen.
- Detached HEAD: Moonraker meldet Klipper als „detached“; ein Update über den Update-Manager verlässt den geprüften Stand – danach `install.sh` erneut laufen lassen (Module bleiben als ungetrackte Dateien erhalten).
- `~/k2-test` (alter Testordner) wird nicht benutzt.
- Geprüft (17.10.2026) nur in einer Attrappe auf x86_64 (`--arch-egal`): Fake-HOME mit Klon des Upstream-Klippers (Fake-Commit über 9cb9975 als „Image-Stand“), Fake-`systemctl`/`sudo` über PATH, Klipper-venv des Batch-Aufbaus. Abgedeckt: Erstinstallation, Wiederholung mit `--printer-cfg-ersetzen` + serial-Optionen, Übernahme von Hand-Einträgen + SAVE_CONFIG, Batch-Fehler wird erkannt, dirty Checkout / falsche Prüfsumme / root / Architektur brechen ab, Rückbau `letzte` und `--aelteste` → Config, extras, `.git/info/exclude` und HEAD/Branch identisch zum Ausgangszustand. Material-DB (17.10.2026, synthetische Mini-DB): Neuanlage, Ersetzen mit Backup, unverändert ohne Option, identische Datei, kaputtes JSON / falsche Struktur / fehlende Datei → Abbruch ohne Änderung, Rückbau `letzte`/`--aelteste`, `pack.sh` schließt DB aus. remain_material (17.10.2026, synthetische Dateien, `remain-test.sh`): Neuanlage, Ersetzen mit Backup, unverändert ohne Option (auch wenn von „k2_box“ geändert), identische Datei, Zusatzschlüssel/`null`, Negativfälle (kaputtes JSON, leer, Liste, Schlüssel fehlt, `remain_material` kein Objekt, `color`/`type` fehlt, Farbe ohne `0`/zu kurz/kein Hex, `type` leer/Zahl, fehlende Datei) → Abbruch ohne Änderung, defekte Datei ohne Option → Warnung + `scp`-Hinweis, Rückbau `--backup`/`--aelteste`, `pack.sh` schließt Datei aus. Auf dem echten Pi noch nicht gelaufen.
