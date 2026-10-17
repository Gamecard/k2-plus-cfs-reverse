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
**Nicht** enthalten: Geräteskripte fürs Stock-Gerät (`bustest_lesend.py`, `emu_prtouch_stock.py`, `prtouch_lesetest.py`, `prtouch_fingertest.py`, `prtouch_vorabtest.py`), Logs, Befunde, Übergabe-/Zugangsnotizen. `pack.sh` bricht bei Zugangsdaten-/IP-Mustern im Inhalt ab.

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
| 1 Vorprüfungen | nicht root, `aarch64`, `SHA256SUMS`, `~/klipper` ist git-Checkout, `~/printer_data/config` da, ≥ 200 MB frei, Python (`~/klippy-env`), `sudo -n`, Dienst `klipper.service`; Backup-Ordner `~/k2-pi-backup/<stamp>/` | jeder Fehlschlag |
| 2 Klipper | HEAD ≠ `9cb9975` → nur bei **sauberem** Checkout `git checkout --detach 9cb9975` (fehlt der Commit: `git fetch origin`); eigene Dateien zählen nicht als Änderung. `.git/info/exclude` bekommt `klippy/extras/k2_*.py` + `k2_nachbau/` (Moonraker sieht den Checkout als sauber). | lokale Änderungen (Liste wird gezeigt) |
| 3 Dienst | `sudo systemctl stop klipper` (Zustand gemerkt) | stop scheitert |
| 4 Module | Backup vorhandener `extras/k2_*.py` + `k2_nachbau/`; `pi-host/k2_*.py` → `extras/` (**`k2_motor_init.py` → `k2_motor.py`**, Dateiname = Abschnitt `[k2_motor]`), `nachbau/*.py` → `extras/k2_nachbau/`; jede Kopie per `cmp` geprüft. Mitinstalliert werden auch `k2_lis2dw.py` (Pflicht, `[k2_lis2dw]` in printer.cfg) und `k2_zalign.py` (nur aktiv, wenn `[k2_zalign]` in zalign.cfg einkommentiert wird) | Kopierfehler |
| 5 Config | `~/printer_data/config` **komplett** als `config.tar.gz` gesichert; alle `config-pi/*.cfg` außer `printer.cfg` kopiert; `printer.cfg`: keine vorhanden → wird `printer.cfg`; vorhanden → nur mit `--printer-cfg-ersetzen` ersetzt, sonst `printer-k2.cfg` daneben + Hinweis | – |
| 6 Prüfung | alle `test_k2_*.py` (mit `KLIPPER_SRC=~/klipper`), nachbau-Selbsttests, Batch-Lauf `klippy.py <cfg> -i /dev/null -d <dicts>` wie `lauf-modulfix.sh` (Lauf F) gegen die **installierten** Module + Config; Logs in `<backup>/tests/`, `<backup>/batch/` | ein Test rot / Batch ≠ EXIT 0 → Dienst bleibt gestoppt |
| 7 Abschluss | Dienst nur mit `--starten`; Zusammenfassung, `ls -l /dev/serial/by-id/`, verbleibende Platzhalter, nächste Schritte §4 | – |

**Erneut ausführen** ist ausdrücklich vorgesehen (z. B. nach neuem Paket): eingetragene `serial:`-Pfade (`[mcu]`, `[mcu nozzle_mcu]`, `[k2_485]`) werden aus den bisherigen Dateien übernommen; beim Ersetzen einer bisherigen K2-`printer.cfg` bleibt deren SAVE_CONFIG-Block (Kalibrierung) erhalten, außer mit `--save-config-aus-paket`. Jeder Lauf legt ein neues Backup an.

### Optionen

| Option | Wirkung |
|---|---|
| `--klipper-nicht-anfassen` | Checkout nicht umstellen (nur Warnung, Stand dann ungeprüft) |
| `--printer-cfg-ersetzen` | vorhandene `printer.cfg` ersetzen (Original im Backup) |
| `--save-config-aus-paket` | SAVE_CONFIG-Block des Pakets statt des bisherigen |
| `--main-serial` / `--duese-serial` / `--rs485-serial PFAD` | by-id-Pfade direkt eintragen |
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
- Geprüft (17.10.2026) nur in einer Attrappe auf x86_64 (`--arch-egal`): Fake-HOME mit Klon des Upstream-Klippers (Fake-Commit über 9cb9975 als „Image-Stand“), Fake-`systemctl`/`sudo` über PATH, Klipper-venv des Batch-Aufbaus. Abgedeckt: Erstinstallation, Wiederholung mit `--printer-cfg-ersetzen` + serial-Optionen, Übernahme von Hand-Einträgen + SAVE_CONFIG, Batch-Fehler wird erkannt, dirty Checkout / falsche Prüfsumme / root / Architektur brechen ab, Rückbau `letzte` und `--aelteste` → Config, extras, `.git/info/exclude` und HEAD/Branch identisch zum Ausgangszustand. Auf dem echten Pi noch nicht gelaufen.
