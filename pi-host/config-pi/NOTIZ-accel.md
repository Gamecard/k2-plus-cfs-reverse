> **ENTWURF – Stand 16.10.2026, am Gerät UNGETESTET.** Nur offline geprüft (Tests `pi-host/test_k2_lis2dw.py`, Batch `arbeitsdateien-lokal/upstream-test-accel/`, Lauf A EXIT 0).

# NOTIZ: Beschleunigungssensor in `printer.cfg` einbauen

Modul: `pi-host/k2_lis2dw.py` (Doku `pi-host/README-k2_lis2dw.md`). Es spricht das alte Creality-lis2dw-Protokoll der Düsen-MCU und meldet sich bei Upstream als normaler Beschleunigungssensor (`accel_chip`).

## 1. Block für `printer.cfg`

Ersetzt den Kommentarblock „Beschleunigungssensor (Stock) … ENTFÄLLT vorerst …“ (derzeit Zeilen 382–393, vor „Nivellierung“). Muss **vor** dem SAVE_CONFIG-Block stehen. Genau dieser Block lief im Batch-Lauf A (`upstream-test-accel/accel-block.cfg`).

```ini
# Beschleunigungssensor: [k2_lis2dw] = Stock-[lis2dw] fuer Upstream-Klipper
# (altes Creality-MCU-Protokoll, Modul pi-host/k2_lis2dw.py, README-k2_lis2dw.md).
# Pins/Takt/Achsen 1:1 Stock.
[k2_lis2dw]
cs_pin: nozzle_mcu:PA4
spi_speed: 5000000
spi_software_sclk_pin: nozzle_mcu:PA5
spi_software_mosi_pin: nozzle_mcu:PA7
spi_software_miso_pin: nozzle_mcu:PA6
axes_map: x,z,y
#start_fifo_bypass: True   # False = Startablauf wie Creality-Host

# Stock-Werte; Upstream misst per Sweep (sweeping_period 1.2 s Default),
# Creality-Stock per Einzelfrequenzen. Gleiche Methode wie Stock: sweeping_period: 0
[resonance_tester]
accel_chip: k2_lis2dw
probe_points:
   175,175,175
min_freq: 20
max_freq: 120
accel_per_hz: 100
```

Kein `[lis2dw]` zusätzlich eintragen. Das Upstream-Modul würde wieder mit „Command format mismatch: query_lis2dw“ abbrechen (Kontroll-Lauf K).

## 2. Installation (README §3 ergänzen)

```bash
cp pi-host/k2_lis2dw.py ~/klipper/klippy/extras/     # Dateiname = Abschnitt [k2_lis2dw]
```

Das Modul braucht keine `k2_nachbau`-Helfer. Es nutzt nur die Upstream-Geschwister `bus`, `adxl345`, `bulk_sensor` und `lis2dw` (Commit 9cb9975).

## 3. Vorschläge für `config-pi/README.md` (nicht von mir geändert)

- §2.1, Zeile `[lis2dw]`, `[resonance_tester]`: „E/O“ → „M `[k2_lis2dw]` + Ü `[resonance_tester]` (accel_chip k2_lis2dw)“.
- §3: zusätzlich `cp pi-host/k2_lis2dw.py $E/`.
- §5.2: Lauf „accel-A/B/K“ (siehe `upstream-test-accel/LIESMICH.txt`).
- §6, Punkt 8: „eigenes Modul `[k2_lis2dw]` vorhanden, am Gerät ungetestet. Erstlauf siehe README-k2_lis2dw.md §7.“
- §4: neuer Schritt nach Schritt 1, noch ohne Bewegung: `ACCELEROMETER_DEBUG_READ REG=0x0f` (= 0x44), `KTWO_ACCEL_STATUS`, `ACCELEROMETER_QUERY` im Stand. `SHAPER_CALIBRATE` erst nach freigegebenem Homing (Schritt 6/7).

## 4. Batch-Beleg (Lauf A, `upstream-test-accel/lauf-A/duese-dekodiert.txt`)

```
config_spi oid=1 pin=PA4 cs_active_high=0
config_lis2dw oid=2 spi_oid=1
spi_set_software_bus oid=1 miso_pin=PA6 mosi_pin=PA7 sclk_pin=PA5 mode=3 rate=5000000
```

In Lauf B (G-Code `ACCELEROMETER_QUERY`) folgen die Registerschreibungen `21 06`, `2e 00`, `25 34`, `20 94` und `query_lis2dw oid=2 clock=12000000 rest_ticks=300000`.
