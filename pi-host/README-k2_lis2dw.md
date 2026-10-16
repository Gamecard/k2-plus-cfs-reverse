> **ENTWURF – am Gerät UNGETESTET (Stand 16.10.2026).** Zusatzmodul `[k2_lis2dw]` für Upstream-Klipper (Commit 9cb9975) auf dem Pi-Host. Die MCU-Firmware bleibt die von Creality. Bisher nur offline geprüft: `python3 test_k2_lis2dw.py` mit 64/64 Prüfungen und Batch-Lauf `arbeitsdateien-lokal/upstream-test-accel/` (Lauf A EXIT 0). Entwurf durch einen KI-Agenten. Was mit **ANNAHME** markiert ist, ist nicht belegt.

# [k2_lis2dw] – LIS2DW-Beschleunigungssensor an der Creality-Düsen-MCU

| Datei | Zweck |
|---|---|
| `k2_lis2dw.py` | Modul `[k2_lis2dw]` / `[k2_lis2dw <name>]` |
| `test_k2_lis2dw.py` | Offline-Tests: Firmware-Emulation, echte Upstream-Helfer, echtes `msgproto` + Creality-Dictionary |
| `config-pi/NOTIZ-accel.md` | fertiger Block für `printer.cfg`, Einbauhinweise |
| `README-k2_lis2dw.md` | diese Datei |

## 1. Problem und Lösung in Kürze

Upstream-`[lis2dw]` bricht an der Creality-Firmware ab, mit `Command format mismatch: query_lis2dw oid=%c rest_ticks=%u vs query_lis2dw oid=%c clock=%u rest_ticks=%u` (Batch-Lauf C in `upstream-test/` und Kontroll-Lauf K in `upstream-test-accel/`). Die Firmware stammt aus Build `1.1.0.48-293-g493f9a0f` von Main und Düse. Sie enthält noch den lis2dw-Code aus der Zeit **vor** dem Upstream-Umbau auf `sensor_bulk`.

`[k2_lis2dw]` spricht genau dieses alte Protokoll. Nach oben liefert es Upstream die erwartete Struktur: Batches `{'data': [(t, x, y, z), …], 'errors', 'overflows'}` über `BatchBulkHelper` und `adxl345.AccelQueryHelper`. Es lässt sich deshalb als `accel_chip` für `[resonance_tester]` nutzen, also für `SHAPER_CALIBRATE`, `TEST_RESONANCES` und `MEASURE_AXES_NOISE`. Dazu kommen `ACCELEROMETER_QUERY/MEASURE/DEBUG_READ/DEBUG_WRITE` und der Webhook `lis2dw/dump_lis2dw`.

## 2. Protokoll-Unterschiede (mit Fundstellen)

Quellen:
- **D** = Dictionaries `arbeitsdateien-lokal/lokale MCU-Dictionaries/{mcu-main-F008-GD32F303RET6,noz-duese-F008-GD32F303CBT6}.dict.json`. Für lis2dw sind Main und Düse identisch.
- **FW** = Düsen-Image `fw/F008/noz0_130_G30-noz0_021_000.bin`, gleicher Build-String wie das Live-Dictionary. Dekompiliert mit Ghidra 12.1.4 headless und `llvm-objdump`.
- **U** = Upstream `arbeitsdateien-lokal/upstream-klipper/`.

Die Creality-Host-Quelle `lis2dw.py` gibt es in `rootfs-live/` **nicht**. Dort liegen nur `belt_mdl.py` und `.so`-Wrapper. Die Live-Logs zeigen aber die Meldungen und Funktionsnamen des alten Upstream-Moduls (`[lis2dw:_start_measurements:188] lis2dw_dev_id: 44`, `LIS2DW starting 'lis2dw' measurements`, `_finish_measurements:232`, `live-logs-2026-10-15/klippy.log.2026-10-12` Z. 32207–34850). Das Host-Verhalten wurde deshalb aus der Firmware abgeleitet und nicht aus der Creality-Host-Quelle.

| Punkt | Creality-Firmware (alt) | Upstream 9cb9975 (neu) |
|---|---|---|
| Konfiguration | `config_lis2dw oid=%c spi_oid=%c` (D id 63; FW @0x080037a8: `oid_alloc` 0x4c Byte, `timer.func`=0x080039b9, `spi` @+0x10) | `config_lis2dw oid bus_oid bus_oid_type lis_chip_type` (U `src/sensor_lis2dw.c:110`, `klippy/extras/lis2dw.py:77`), SPI **oder** I2C |
| Start | `query_lis2dw oid clock rest_ticks`: Start **zum Zeitpunkt `clock`** (FW @0x08004f58: `waketime=args[1]`, `flags=HAVE_START`, `sequence=limit_count=0`). Der Task (@0x0800769c) schreibt dann `FIFO_CTRL=0xC0` (`movw r3,#0xc02e`) | `query_lis2dw oid rest_ticks`, Start sofort (U `sensor_lis2dw.c:221–237`) |
| Stopp | gleicher Befehl mit `rest_ticks=0`. FW liest ein Sample nach (`fifo_disable=1`), schreibt `FIFO_CTRL=0x00`, liest `FIFO_SAMPLES`, schickt den Restblock und **antwortet mit `lis2dw_status`** | `rest_ticks=0`, ohne Antwort (`send_wait_ack`, U `lis2dw.py:193`) |
| Lesen | je Timer-Ereignis (alle `rest_ticks`) **ein Sample pro Durchlauf**: 7-Byte-SPI `0xA8…` (Register 0x28–0x2D) und danach 2-Byte `0xAF` (FIFO_SAMPLES). Weiter, solange `FSS & 0x3f ≠ 0` (FW `lis2dw_query` @0x08004e78) | Block mit 8 Samples per 49-Byte-SPI, wenn `fifo_bytes_pending ≥ 48` (U `sensor_lis2dw.c:160–218`) |
| Datenmeldung | `lis2dw_data oid=%c sequence=%hu data=%*s` (D −25). Puffer 48 Byte @+0x1b, Meldung bei `data_count > 42` (also 8 Samples). Sample = **XL,XH,YL,YH,ZL,ZH**, also int16 LE je Achse, 14 bit linksbündig (FW @0x08004e78 Byte-Zuordnung `d[i]=msg[i+1]`, `lis2dw_report` @0x08003454: `sendf(seq, data_count, data)`, dann `seq++`) | `sensor_bulk_data oid sequence data` (U `bulk_sensor.py:115`), gleiche 6-Byte-Samples (`"<hhh"`, U `lis2dw.py:85`), 8 Samples je Block (`MAX_BULK_MSG_SIZE 51 // 6`, U `bulk_sensor.py:203/214`) |
| Status | `lis2dw_status oid clock query_ticks next_sequence buffered=%c fifo=%c limit_count=%hu` (D −26; FW @0x08004e1c: `time1=DWT_CYCCNT` (0xe0001004), SPI `0xAF`, `time2−time1`, `buffered=data_count`, **`fifo = FSS & 0x1f`**, `limit_count`) | `sensor_bulk_status … buffered=%u possible_overflows=%hu`. `buffered` = Puffer-Bytes **plus FIFO-Bytes** (`fifo & 0x3F`·6), U `sensor_lis2dw.c:137/263`, `sensor_bulk.c:34` |
| Überlauf | `limit_count++`, wenn FIFO_SAMPLES Bit 6 (OVR) gesetzt ist (FW @0x08004e78) | `possible_overflows++` bei Bit 6 (U `sensor_lis2dw.c:127`) |
| Clock-Synchronisation (Host) | alter Host: `chip_clock = seq·8 + buffered//6 + fifo + 1` | `chip_clock = seq·8 + buffered//6 + 1` (U `bulk_sensor.py:258–263`) |

**Übersetzung:** `buffered_neu = buffered_alt + (fifo & 0x1f)·6`, `possible_overflows = limit_count`. Damit sind beide chip_clock-Formeln identisch; Test 2 prüft das in 500 Zufallsfällen.

**Firmware-Belege:** Die Befehlstabelle im Düsen-Image liegt bei File-Offset 0x7418. Ein Eintrag hat 12 Byte: `info` (msg_id, num_args, flags, num_params), `param_types*`, `func*`. Der Funktionszeiger steht also **am Ende** des Eintrags. Danach gehört zu `query_lis2dw_status` (id 61) 0x08004e1d, zu `query_lis2dw` (62) 0x08004f59 und zu `config_lis2dw` (63) 0x080037a9. Das bestätigt die Verschiebung um einen Eintrag, die `config-pi/README.md` §5.3 Punkt 7 für `HANDLER-TABELLE.txt` beschreibt.

**Nebenbefund:** Das Düsen-Image ist für **0x08003000** gelinkt, nicht für 0x08000000. Die `param_types`-Zeiger 0x0800a7xx lägen bei Basis 0x08000000 hinter dem Dateiende (0x7898 Byte). Bei 0x08003000 beginnen 10 von 13 geprüften Handlern mit `push`, bei 0x08000000 nur 1 von 13. Die Angabe „Basis 0x08000000“ in `arbeitsdateien-lokal/lokale Analyse-Dateien (lokal).md` für das Düsen-Image sollte nachgeprüft werden.

## 3. Modul-Design (Upstream-Wiederverwendung)

```
K2Lis2dw(extras.lis2dw.LIS2DW)              <- Upstream-Klasse als Basis
  geerbt unverändert: read_reg, set_reg, start_internal_client,
                      _convert_samples (axes_map/Skalierung), _process_batch
  neu: __init__ (alte config-/query-Befehle), _build_config,
       _start_measurements, _finish_measurements, KTWO_ACCEL_STATUS
  ffreader = CrealityFixedFreqReader(extras.bulk_sensor.FixedFreqReader)
      geerbt unverändert: note_end, _update_clock, pull_samples (Zeitachse,
                          ClockSyncRegression, "<hhh"-Dekodierung)
      neu: setup_query_command (lis2dw_status / lis2dw_data),
           note_start(start_clock) (erste Statusabfrage mit minclock)
      query_status_cmd = _StatusQuery -> translate_status() (alt -> neu)
  batch_bulk = extras.bulk_sensor.BatchBulkHelper (Upstream)
  adxl345.AccelCommandHelper / AccelQueryHelper / read_axes_map (Upstream)
```

Register, Skalierung (`LIS2DW_SCALE = 9.80665·1.952/4` mm/s² je LSB) und `BATCH_UPDATES` übernimmt das Modul aus Upstream-`lis2dw.py`. Dort gelten dieselben Werte wie im alten Modul.

Geprüfte Upstream-Schnittstellen:
- `resonance_tester.py:268–276` liest `accel_chip`. `:300–308` (`connect`) verlangt `lookup_object(<name>)` mit dem Attribut `start_internal_client`. `:338–343` nutzt `chip.start_internal_client()` und `chip.name`. `:382–391` (`CHIPS=`) prüft dasselbe. `:508–512` (`MEASURE_AXES_NOISE`) ebenso.
- `adxl345.py:34–104` (`AccelQueryHelper`: `handle_batch` erwartet `msg['data']` als Liste von `(t, x, y, z)`; `get_samples`, `has_valid_samples`), `:107–188` (`AccelCommandHelper`, Mux-Schlüssel `CHIP`, Default ohne Namen), `:191–197` (`read_axes_map`).
- `shaper_calibrate.py:168–193` (`calc_freq_response`): nutzt `get_samples()`, Abtastrate `N/T` aus den Host-Zeitstempeln. Die Zeitachse geht also direkt in die Frequenzachse ein.
- `bulk_sensor.py:23–100` (`BatchBulkHelper`), `:118–134` (`BulkDataQueue`, `msg_fmt` frei wählbar), `:156–201` (`ClockSyncRegression`), `:207–299` (`FixedFreqReader`: `setup_query_command` ist fest auf `sensor_bulk_status` verdrahtet (`:218–226`) und wird deshalb überschrieben).
- `mcu.py:76–108` (`CommandQueryWrapper.send(minclock, reqclock)`), `:26–37` (`DummyResponse` im Batch-Modus), `:1202` (`lookup_query_command`), `:1213` (`register_serial_response`).
- `gcode.py:145–149` (Namensregel: 2. Zeichen keine Ziffer) und `:201/210–215` (Parser `args_r`: `KTWO_LIS2DW_STATUS` würde an der `2` zerlegt). Deshalb heißt der eigene Befehl **`KTWO_ACCEL_STATUS`** (ohne Ziffer).
- `msgproto.py:310–318` (`lookup_command` → „Command format mismatch“).

## 4. Konfiguration und Befehle

Block: `config-pi/NOTIZ-accel.md` §1. Optionen:

| Option | Default | Bedeutung |
|---|---|---|
| `cs_pin` | Pflicht | Stock `nozzle_mcu:PA4`. Ohne `cs_pin` gibt es einen Config-Fehler, denn die Firmware kann nur SPI |
| `spi_speed`, `spi_software_sclk/mosi/miso_pin`, `spi_bus` | 5 MHz | wie Upstream-`bus.MCU_SPI_from_config`, Modus 3 |
| `axes_map` | `x,y,z` | Stock `x,z,y` |
| `start_fifo_bypass` | `True` | siehe §5.2; `False` = Startablauf wie Creality-/alter Host |

Befehle:
- Upstream: `ACCELEROMETER_QUERY [CHIP=k2_lis2dw]`, `ACCELEROMETER_MEASURE`, `ACCELEROMETER_DEBUG_READ REG=`, `ACCELEROMETER_DEBUG_WRITE REG= VAL=`. Ist der Sensor im Abschnitt `[k2_lis2dw]` ohne eigenen Namen angelegt, gelten diese Befehle auch ohne `CHIP=`.
- Eigener Befehl: `KTWO_ACCEL_STATUS [CHIP=]` zeigt den Rohstatus (`query_lis2dw_status`, misst dabei nicht) und die übersetzten Upstream-Werte.
- Webhook: `lis2dw/dump_lis2dw` mit `sensor=k2_lis2dw`, wie Upstream-`[lis2dw]`.

## 5. Abweichungen vom Creality-Host / ANNAHMEN

1. **I2C aus (`CTRL2=0x06`)** wie Upstream bei SPI. Der alte Host hat das vermutlich nicht gemacht. **ANNAHME:** unkritisch, weil Adress-Autoinkrement (Bit 2) gesetzt bleibt. Die Firmware braucht es für ihren 7-Byte-Lesezugriff.
2. **FIFO-Start im Bypass:** Der Host schreibt `FIFO_CTRL=0x00`, die Firmware stellt zum Startzeitpunkt `0xC0` ein. Der FIFO beginnt also leer. Beim alten Ablauf (vorab `0xC0`) läuft der FIFO in den ≈ 100 ms bis zum Start voll. Dann ist FSS = 32 und die Firmware meldet `fifo = 32 & 0x1f = 0`. Das kostet am Anfang bis zu 32 Samples Versatz, und die Regression baut ihn nur langsam ab. In der Emulation lag der maximale Fehler bei 26,8 statt 1,7 Sample-Perioden. **ANNAHME** (Datenblatt LIS2DW12): Der Wechsel Bypass → Continuous startet mit leerem FIFO. Notfalls `start_fifo_bypass: False`.
3. **Schwache Überlauf-Erkennung:** `limit_count` zählt nur, wenn OVR beim Lesen gesetzt ist. Das gilt bei Upstream genauso. Den FSS-Wert 32 meldet die Firmware als 0 (Maske `0x1f`). Ein MCU-Stau von ≥ 20 ms (32 Samples) verfälscht deshalb eine Statusabfrage. Upstream verwirft Abfragen mit zu langer `query_ticks` ohnehin.
4. **Batch-Modus (`klippy -o`):** Ohne MCU-Antworten werden Chip-ID und Rücklesen übersprungen und es gehen nur Schreibbefehle raus. `ACCELEROMETER_DEBUG_*` bleibt Upstream-Code und erzeugt im Batch einen Fehler (`IndexError`), wie bei jedem Upstream-Beschleunigungssensor.
5. **Messmethode:** Upstream-`resonance_tester` misst standardmäßig per Sweep (`sweeping_period 1.2`). Der Creality-Stock lief laut Log mit Einzelfrequenzen („Testing frequency 20 Hz …“). Die Stock-Optionen `min_freq`/`max_freq`/`accel_per_hz` gibt es in Upstream unverändert. Zum direkten Vergleich mit Stock: `sweeping_period: 0`.
6. **Zeitachse:** Upstream setzt Sample-Zeiten ≈ 1,5 Perioden vor die Verfügbarkeit (`+1`-Konvention, `bulk_sensor.py:260–263`). Das ist so beabsichtigt und war beim alten Host gleich.
7. **Firmware-Last:** Pro Sample laufen zwei Soft-SPI-Transfers, wie bei Stock. Im Stock-Lauf vom 12.10. meldeten die Statistiken der Düsen-MCU `mcu_awake ≈ 0,18`. **ANNAHME:** Unter Upstream ist die Last gleich, denn der Firmware-Pfad ist identisch.
8. **`on_restart`-Stopp** (`query_lis2dw … rest_ticks=0` beim Klipper-Neustart ohne MCU-Reset): Die Firmware antwortet mit einem unaufgeforderten `lis2dw_status`. Upstream protokolliert das als `got {...}`-Warnung. Harmlos, das Verhalten war beim alten Host gleich.
9. Gebunden an Upstream **9cb9975**: `lis2dw`-Konstanten/Methoden und `FixedFreqReader`-Interna (`_update_clock`, `query_status_cmd`, `bulk_queue`). Nach einem Klipper-Update die Tests neu laufen lassen (`KLIPPER_SRC=~/klipper python3 test_k2_lis2dw.py`).

## 6. Tests

`cd pi-host && python3 test_k2_lis2dw.py` ergibt 64/64 Prüfungen in ca. 2 s, Python 3.14. Die Syntax ist auf 3.9 geprüft, numpy ist optional (nur für `MEASURE_AXES_NOISE`). Geladen wird das Modul als `extras.k2_lis2dw` mit den **echten** Upstream-Modulen `bulk_sensor`, `adxl345`, `lis2dw`, `resonance_tester`, `shaper_calibrate` sowie `msgproto`, `gcode`. Nur `extras.bus` ist eine SPI-Attrappe. Die Düsen-Firmware wird nach dem Dekompilat emuliert (`FwSim`, `ChipSim`).

Die Prüfungen decken ab:
- Formate gegen beide Dictionaries; Upstream-Formate scheitern mit Mismatch.
- Status-Übersetzung, chip_clock = alter Host, Überlauf 0xffff.
- Dekodierung synthetischer `lis2dw_data`-Pakete (Wire-Rundreise) mit negativen Werten, Teilblock und Sequenz-Überlauf.
- `axes_map` `x,z,y` / `-x,-z,y` / `z,x,-y`; 1 g ≈ 9807 mm/s².
- Start/Stopp mit Registerfolge, `clock`/`rest_ticks=300000`, `minclock` der ersten Statusabfrage, Stopp als Query, falsche Chip-ID, Batch-Modus.
- Zeitachse gegen die Emulation (Chip +400 ppm, 30 s):
  - lückenlos
  - `f_est` 1600,634 statt wahr 1600,640 Hz (< 20 ppm)
  - Versatz −1,50 Perioden, Jitter 0,04
  - Creality-Ablauf zum Vergleich
  - Langlauf 335 s über den Sequenz-Überlauf (FW-Sequenz 0x105b7)
- Integration: `ACCELEROMETER_QUERY` (z ≈ 1 g), automatischer Stopp, `KTWO_ACCEL_STATUS`, `ACCELEROMETER_DEBUG_READ`, Webhook, `resonance_tester.connect`, `MEASURE_AXES_NOISE` mit dem echten Upstream-Rechenweg.
- G-Code-Namen mit echter Upstream-Prüfung und Parser-Gegenprobe.
- Python-3.9-Syntax.

Mutationsprobe: Ohne FIFO-Anteil in `translate_status`, ohne `axes_map`-Umsetzung oder ohne `minclock` schlagen jeweils 3–8 Prüfungen fehl.

Batch (`arbeitsdateien-lokal/upstream-test-accel/`, `LIESMICH.txt` dort):

| Lauf | Ergebnis |
|---|---|
| A (nur Config) | EXIT 0. Düse: `config_spi oid=1 pin=PA4`, `config_lis2dw oid=2 spi_oid=1`, `spi_set_software_bus … PA6/PA7/PA5 mode=3 rate=5000000`, kein Mismatch |
| B (G-Code) | `KTWO_ACCEL_STATUS` ok; `ACCELEROMETER_QUERY` sendet `21 06`, `2e 00`, `25 34`, `20 94`, `query_lis2dw oid=2 clock=12000000 rest_ticks=300000`. Danach kommt erwartungsgemäß „No accelerometer measurements found“, weil der Batch-Modus keine Daten hat |
| K (Kontrolle Stock-`[lis2dw]`) | `Command format mismatch: query_lis2dw …` |

## 7. Erstlauf am Gerät

Jeder Schritt nur, wenn der vorige bestanden ist. Nach jedem Schritt `klippy.log` prüfen.

1. **Ohne Bewegung, nach `FIRMWARE_RESTART`:**
   - Im Log darf kein „format mismatch“ stehen, und `Configured MCU 'nozzle_mcu'` muss erscheinen.
   - `ACCELEROMETER_DEBUG_READ REG=0x0f` → `0x44`. Ein anderer Wert bedeutet Verdrahtung, Pins oder SPI-Modus prüfen, STOP.
   - `KTWO_ACCEL_STATUS` → Antwort mit `messung=False`. Bei Timeout `Timeout on wait for 'lis2dw_status'` ist die Firmware oder die OID falsch, STOP.
2. **`ACCELEROMETER_QUERY` im Stand**, Motoren stromlos oder still:
   - Erwartet: Drucker-z ≈ ±9800 (wegen `axes_map x,z,y` liegt Chip-y auf Drucker-z), x und y nahe 0 (|·| ≲ 1000).
   - Log: `k2_lis2dw 'k2_lis2dw' dev_id: 44`, `starting …`, `finished …`.
   - 3× wiederholen, um den Start/Stopp-Zyklus zu prüfen. Danach `KTWO_ACCEL_STATUS` → `messung=False`.
   - Abbruch bei: „No accelerometer measurements found“, konstanten Nullwerten oder `Invalid lis2dw id`.
3. **`ACCELEROMETER_MEASURE`, 2 s warten, `ACCELEROMETER_MEASURE NAME=stand`:**
   - Die CSV liegt unter `/tmp/k2_lis2dw-stand.csv`.
   - Die Abtastrate ist Zeilen/Δt und muss ≈ 1600 Hz betragen. Die Zeitstempel müssen monoton und ohne Lücken sein.
   - Optional mit der Achse leicht gegen das Gehäuse tippen: Die Spitzen müssen in der richtigen Achse erscheinen. Das prüft `axes_map`, Vorzeichen sind dabei egal.
4. **`MEASURE_AXES_NOISE`** im Stand. Die Rauschwerte notieren und mit laufenden Lüftern vergleichen.
5. **Erst nach freigegebenem Homing** (README §4 Schritte 6/7, Hand am Not-Aus):
   - `TEST_RESONANCES AXIS=X` (Kopf fährt zu 175,175,175), danach `SHAPER_CALIBRATE`.
   - Ergebnis mit den Stock-Werten vergleichen (SAVE_CONFIG: mzv 42,0 / 42,4 Hz), gegebenenfalls auch mit `sweeping_period: 0`.
   - Erst danach `SAVE_CONFIG`.

## 8. Offene Punkte

- Am Gerät ungetestet: alle Schritte aus §7.
- ANNAHMEN aus §5 (Punkte 1, 2, 7).
- Die Creality-Host-Quelle für lis2dw ist nicht vorhanden. Die Registerwerte `CTRL6=0x34`, `CTRL1=0x94` und `FIFO_CTRL` sind aus dem alten und neuen Upstream übernommen, nicht aus Creality-Code. Die Firmware selbst setzt nur `FIFO_CTRL`.
- Ob Creality nach dem Stopp `CTRL1=0x00` (Power-down) schreibt, ist unbekannt. Das Modul lässt den Chip wie Upstream weiterlaufen und setzt nur `FIFO_CTRL=0x00`.
