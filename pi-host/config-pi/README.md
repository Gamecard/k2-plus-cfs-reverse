> **ENTWURF – Stand 16.10.2026, am Gerät UNGETESTET.** Upstream-Klipper-Konfiguration des K2 Plus für den geplanten Raspberry-Pi-CM5-Host. MCU-Firmware bleibt die originale Creality-Firmware. Nur offline geprüft (Upstream-Klipper Commit 9cb9975, Batch-Modus, §5). Alles mit **ANNAHME** ist nicht belegt.

# Pi-Host-Konfiguration K2 Plus (`pi-host/config-pi/`)

## 1. Dateien

| Datei | Inhalt |
|---|---|
| `printer.cfg` | Hauptdatei: MCUs (by-id-Platzhalter), `[k2_prtouch]` (Probe), Kinematik, Stepper, `duplicate_pin_override`, Extruder, Bett, Kammer, Lüfter, Sensoren, `z_tilt`, `bed_mesh`, `input_shaper`, Allgemeines, SAVE_CONFIG-Block (Shaper-Werte + Stock-Mesh als Referenzprofil) |
| `k2-motor.cfg` | `[k2_motor]` mit allen Pins und aktiven Parametern aus `motor_control.cfg` |
| `k2-module.cfg` | `[k2_485]` (gemeinsamer RS485-Bus, by-id-Platzhalter), `[k2_box]`, `[k2_belt mdlx]`/`[k2_belt mdly]` (gesperrt) |
| `homing.cfg` | `[homing_override]` + Freigabe-Makro `_PI_FREIGABE` + Stall-Mode-Haken |
| `macros.cfg` | Grundbetrieb: M106/M107, M141/M191, M900, M205, START/END_PRINT (+ PRINT_START/PRINT_END), PAUSE/RESUME/CANCEL_PRINT, T0–T15 + M8200-Kette (CFS-Wechsel über `[k2_box]`, NOTIZ-cfs.md), G29, BEDPID, NOZZLE_PID |
| `zalign.cfg` | Dual-Z-Lichtschranken PA15/PA8: Variante L aktiv (rein lesend, `[gcode_button zalign_z/zalign_z1]`, `ZALIGN_STATUS`), Variante B `[k2_zalign]` auskommentiert + gesperrt, `ZALIGN_STOCKART` |
| `NOTIZ-zalign.md` | A10: Stock-z_align (Config, Mitschnitt, MCU-Dekompilat), Risiko ohne Lichtschranken, Ersatz `[k2_zalign]`, Testprotokoll T0–T8 |
| `README.md` | diese Datei |

Reihenfolge in `printer.cfg`: `[mcu]`, `[mcu nozzle_mcu]`, `[k2_prtouch]`, dann die Includes, dann der Rest. Seit dem Modul-Fix (§5.3) ist die Reihenfolge der Abschnitte **egal** (Lauf I: `[k2_prtouch]` ans Ende verschoben, fehlerfrei, `statistics` aus der Standardbibliothek); `[k2_prtouch]` muss nur wegen SAVE_CONFIG (`z_offset`) in der Hauptdatei stehen.

**Sicherheits-Defaults (alles gesperrt):** `[k2_prtouch] allow_move: False`, `[k2_motor] allow_write: False` + `auto_init: False`, `[k2_box] allow_write: False`, `[k2_belt mdlx/mdly] allow_write: False` + `allow_move: False`, `[k2_box] allow_move: False`, `[k2_zalign]` auskommentiert (aktiviert: `allow_move: False` + `_PI_FREIGABE` + Motor-Init), `_PI_FREIGABE allow_move: False` (`xy_stall_ok` ist nur noch Not-Sperre, Default True; X/Y-Homing braucht zusätzlich `[k2_motor] allow_write: True`, NOTIZ-stall.md) (G28 bricht ohne jede Bewegung ab – im Batch-Test nachgewiesen, §5).

## 2. Zuordnung Stock → Pi

Quelle: `live-logs-2026-10-16/config-aktuell/` (printer.cfg mit SAVE_CONFIG, sensorless.cfg, gcode_macro.cfg, printer_params.cfg, box.cfg, motor_control.cfg). Legende: **Ü** = übernommen (1:1, sofern nicht anders vermerkt), **M** = ersetzt durch eigenes Modul, **E** = entfällt, **O** = offen.

### 2.1 printer.cfg

| Stock-Abschnitt | Pi | Bemerkung |
|---|---|---|
| `[mcu]` ttyS2 / `[mcu nozzle_mcu]` ttyS3, 230400, `restart_method: command` | Ü | Serial → `/dev/serial/by-id/usb-ANNAHME_…` (Platzhalter), Baud bleibt 230400 (MCU-Konstante `SERIAL_BAUD`) |
| `[mcu rpi]` + `[bl24c16f]` (EEPROM i2c.1, Power-Loss) | E | EEPROM sitzt am T113-Upper-Board, das entfällt. Keine Linux-Host-MCU nötig → kein eigenes Dictionary. Power-Loss-Resume entfällt |
| `[verify_heater extruder/heater_bed/chamber_heater]` | Ü | |
| `[idle_timeout] timeout: 99999999` | Ü | Achtung: Heizungen werden nie automatisch aus (§6) |
| `[virtual_sdcard]` | Ü | Pfad `~/printer_data/gcodes` (ANNAHME Moonraker-Standard); `forced_leveling` (Creality) E |
| `[gcode_arcs]`, `[pause_resume]`, `[exclude_object]`, `[display_status]` | Ü | |
| `[temperature_sensor mcu_temp]` | E | Upstream-`temperature_mcu` kennt `gd32f303xe` nicht (`config_unknown` → „MCU temperature not supported“, statisch geprüft; im Batch-Modus übersprungen, §5.4) |
| `[stepper_x/y/z/z1]` | Ü | z1 ohne `endstop_pin` (Upstream hängt alle Z an den Endstop von stepper_z) |
| `[z_tilt]` | Ü | nutzt die Probe aus `[k2_prtouch]` |
| `[thermistor my_thermistor]`, `[extruder]` | Ü | Creality-Optionen `high_temp_value`, `pid_k*_high_temp` E (kein zweiter PID-Satz in Upstream, als Kommentar erhalten) |
| `[heater_bed]` | Ü | |
| `[filament_switch_sensor filament_sensor]` | Ü | `runout_gcode` (E30 + `BOX_CHECK_MATERIAL_REFILL`) E → nur Pause |
| `[multi_pin heater_fans]`, `[heater_fan hotend_fan]`, `[output_pin fan0_en/fan0/fan1/fan2/extruder_fan]`, `[multi_pin heater_fan2]` | Ü | Lüfter bleiben `output_pin` mit `scale: 255` (Makros M106/M107 wie Stock) |
| `[heater_fan chamber_fan]` | Ü | `is_ptc_fan: 1` (Creality) E – Upstream-heater_fan läuft bei Ziel > 0 ohnehin (ANNAHME gleiche Wirkung, §6) |
| `[temperature_sensor chamber_temp]`, `[heater_generic chamber_heater]`, `[temperature_fan chamber_fan]` | Ü | PC5 dreifach als ADC, PA0 doppelt (fan1 + temperature_fan) – wie Stock |
| `[multi_pin LED_pin]`, `[output_pin LED/power/ptc_power]` | Ü | |
| `[duplicate_pin_override]` | Ü + `nozzle_mcu:PB1` | Upstream unterscheidet Pins je MCU: hotend_fan-multi_pin und extruder_fan nutzen beide Düsen-PB1 → ohne Ergänzung „pin PB1 used multiple times“ (Batch-Lauf D). Das Stock-`PB1` (Main) bleibt drin |
| `[motor_control]` (in printer.cfg auskommentiert, aktiv in motor_control.cfg) | M `[k2_motor]` | §2.2 |
| `[lis2dw]`, `[resonance_tester]` | M | Upstream-`[lis2dw]` scheitert an der Creality-FW (altes Protokoll, „Command format mismatch“, Batch-Lauf C) → ersetzt durch `[k2_lis2dw]` (pi-host/k2_lis2dw.py, NOTIZ-accel.md); `[resonance_tester] accel_chip: k2_lis2dw` |
| `[prtouch_v3]` | M `[k2_prtouch]` | Übernommen: `z_offset 0`, `speed 5`, `samples 1`, `samples_result average`, `samples_tolerance 0.5`, `samples_tolerance_retries 5`, `step_swap_pin !PC7`, `pres_swap_pin nozzle_mcu:PA15`, `pres_cs0_pin PB13/PB14`, `pres_tri_hold 4000,10000,500`, `pres_cfg_regs 60`; ergänzt (Stock-Defaults, BELEGT per Emulation): `pres_acq_tkms 0.78125`, `pres_tri_fter 5,1,0.8`, `pres_ded_tkms 128`, `step_acq_tkms 0.390625`. **E/O:** `quick_lift_speed 100`, `prth_clr_probe_pos 150,355`, `prth_msg_show`, `enable_not_linear_comp`/`high_low_temp_leveling` (Temperaturkompensation), `prth_max_chps 8`, `prth_tri_zacc 1000`, `prth_min_fans 0.6`, `bed_mesh_fall_comp_diagonal/axis` (Mesh-Kompensation), `need_steady_regional 0.8` |
| `[bed_mesh]` | Ü | `move_check_distance` 1 → **3** (Upstream-Minimum 3.0, sonst Config-Fehler) |
| `[printer]` | Ü | `max_accel_to_decel: 30000` gibt es in Upstream nicht mehr → `minimum_cruise_ratio: 0.0` (gleichwertig, da = max_accel) |
| `[z_align]` (Lichtschranken PA15/PA8, `config_z_align`) | Ü lesend / M (O) | `zalign.cfg` Variante L `[gcode_button]` `^!PA15`/`^!PA8` (Stock `zes_untrig 1` = Pull-up, ausgelöst = 0, FW-Dekompilat); Ausrichtung `[k2_zalign]` (Upstream-Endstops + Etappen, gesperrt, auskommentiert); `START_PRINT` holt `Z_TILT_ADJUST` nach, solange `z_tilt` nicht `applied` ist (A10a) – NOTIZ-zalign.md |
| `[belt_mdl mdlx/mdly]` | M `[k2_belt mdlx/mdly]` | Stock-Werte 1:1 (`ch_max_dis 5`, `ch_best_n 140`, `ch_best_error 0.15`), dazu `allow_write: False`, `allow_move: False`, `stock_aliases: False` (README-k2_belt.md) |
| `[input_shaper]` (leer) + SAVE_CONFIG `mzv 42.0 / mzv 42.4` | Ü | im SAVE_CONFIG-Block |
| SAVE_CONFIG `[bed_mesh default]` | Ü umbenannt | als `[bed_mesh stock_referenz_2026-10-16]` – Upstream lädt es dadurch NICHT automatisch; nur Vergleich, vor dem Druck neu messen (`G29`) |
| SAVE_CONFIG `[auto_addr] mb_addr_table_uniids` | E | echte UUIDs – bewusst NICHT übernommen |
| SAVE_CONFIG `[box] cut_pos_x = -7.80` | E | kein k2_box-Option; als Referenzwert in k2-module.cfg-Kommentar |

### 2.2 motor_control.cfg, sensorless.cfg, printer_params.cfg, box.cfg

| Stock | Pi | Bemerkung |
|---|---|---|
| `[motor_control]` (switch, retries, overcurrent_switch, cut_pos_offset, motor_closed_loop, motor_*_dir/step/stall, alle aktiven `<motor>_<param>`) | M `[k2_motor]` (k2-motor.cfg) | 1:1; zusätzlich `allow_write: False`, `shutdown_on_failure: False`, `auto_init: False`, `main_mcu: mcu`, `nozzle_mcu: nozzle_mcu` (Modul sucht zuerst das Upstream-Objekt `mcu nozzle_mcu`; der volle Name geht ebenfalls) |
| sensorless.cfg `[force_move]` | Ü | nötig für `SET_KINEMATIC_POSITION` in der Inbetriebnahme |
| sensorless.cfg `[homing_override]` + `xyz_ready`, `_IF_HOME_Z`, `_IF_MOVE_XY`, `_HOME_X`, `_HOME_Y`, `_HOME_Z` | ersetzt durch homing.cfg | Reihenfolge Y→X→Z, Freifahren +10/−10, Z in Mesh-Mitte (175/175), M204-Werte wie Stock. `MOTOR_STALL_MODE`/`MOTOR_SYS_PARAM`/`MOTOR_CHECK_PROTECTION_AFTER_HOME` → Haken `_PI_VOR_HOMING`/`_PI_NACH_HOMING` (leer, O). `BOX_GO_TO_EXTRUDE_POS`/`PRES_CHECK`/`BOX_NOZZLE_CLEAN` vor Z E/O. `FORCE_MOVE stepper_y -3` E/O (§6). `BED_MESH_PROFILE LOAD="default"` nach jedem Homing E |
| sensorless.cfg `LEAVE_FLUSH_AREA` | E | Display-Hilfsmakro (FORCE_MOVE) |
| printer_params.cfg `[fan_feedback]` | E | Creality (`config_fancheck`); Drehzahlüberwachung O (optional) |
| printer_params.cfg `[custom_macro]` | ersetzt | Werte in `_PI_PARAM` (default_bed_temp 50, default_extruder_temp 220, g28_ext_temp 140) |
| printer_params.cfg `[gcode_macro product_param]` | E | nur Display-Info |
| box.cfg `[serial_485 serial485]` ttyS5 | M `[k2_485]` | Modul `k2_485.py`: EINE `nachbau/serial485.Serial485`-Instanz für Motoren X/Y/Z/Z1 + CFS + Belt; Port by-id-Platzhalter in k2-module.cfg (ANNAHME) |
| box.cfg `[auto_addr]`, `[box]` | M `[k2_box]` | Auto-Addr nach `k2_motor:ready`; `[box]`-Positionen/Temperaturen als Kommentar (gehören in künftige Wechsel-Makros) |
| box.cfg `[filament_rack]`, `[load_ai]` | E | Rack nie gesehen; AI-Kamera Creality |

### 2.3 Makros

**Übernommen/angepasst (macros.cfg):** `M106`/`M107` (Stock-Abbildung S→fanN_min…255, fan0 schaltet `fan0_en`; ohne Qmode), `M141`/`M191` (Stock-Logik ohne `SET_TEMPERATURE_FAN_SWITCH`; M191 wartet per `TEMPERATURE_WAIT`), `M900`, `M205`, `START_PRINT` (Gerüst: Bett + Düse 140 °C, G28, zweites G28 Z mit warmem Bett, Profil `default` laden falls vorhanden, Drucktemperatur) + `PRINT_START`, `END_PRINT` (Stock-Ablauf ohne BOX/Qmode, Z sicher wie `END_PRINT_POINT`) + `PRINT_END`, `wait_temp`/`WAIT_TEMP_START`/`WAIT_TEMP_END` (als `_wait_temp`/`_WAIT_TEMP_*`), `PAUSE` (Stock-z_safe-Logik, Düse 140 °C, Parken), `RESUME` (Temperatur/Lüfter zurück), `CANCEL_PRINT`, `G29` (vereinfacht: G28 → Z_TILT_ADJUST → G28 Z → BED_MESH_CALIBRATE), `BEDPID`, `NOZZLE_PID` (ohne automatisches SAVE_CONFIG), `T0` (kein Wechsel).

**Neu (Sperren/Hilfen):** `T0`–`T15` und `M8200` → CFS-Wechselkette über `KTWO_BOX_*` (nur Box 1, ohne Freigaben nur Plan; Fehler im Druck → PAUSE; NOTIZ-cfs.md), `_PI_FREIGABE`, `_PI_VOR_HOMING`, `_PI_NACH_HOMING`, `_PI_MESH_LADEN`, `_PI_PARAM`.

**Weggelassen (Grund):**

| Stock-Makro | Grund |
|---|---|
| `BOX_LOAD_MATERIAL*`, `BOX_QUIT_MATERIAL*`, `BOX_INFO_REFRESH`, `M8200`-Kette (`CR_BOX_*`) | Creality-Box-Befehle (`BOX_*`, `CR_BOX_*`); ersetzt durch die `KTWO_BOX_*`-Kette (NOTIZ-cfs.md) |
| `LOAD_MATERIAL*`, `QUIT_MATERIAL*`, `LOAD_MATERIAL_CLOSE_FAN2/RESTORE_FAN2` | `FILAMENT_RACK_*`, `BOX_*`, `SET_COOL_TEMP` (Creality) |
| `Qmode`, `Qmode_exit`, `M204`-Override | Leisemodus mit Creality-`SET_QMODE_FLAG`; M204 = Upstream |
| `IF_NEED_HOME`, `PRINT_TEMP_SET`, `PRINT_PREPARED`, `PRINT_PREPARE_CLEAR`, `MAINTENANCE_ITEM(_PARAM)`, `MOTOR_CANCEL_PRINT` | Display-/App-Hilfen bzw. `BOX_SET_CURRENT_BOX_IDLE_MODE` |
| `PRINT_CALIBRATION`, `ACCURATE_G28`, `BED_MESH_CALIBRATE_START_PRINT` | `CX_PRINT_LEVELING_CALIBRATION`, `ACCURATE_HOME_Z`, regionales Mesh mit `GCODE_FILE` (prtouch_v3) |
| `FIRST_FLOOR_PAUSE(_POSITION)`, `FIRST_FLOOR_RESUME` | Erstschicht-Prüfung über Display/Kamera |
| `ZDOWN_SWITCH_SET`, `BED_MANUAL_CAL(_START/_END)`, `Z_AXIS_CALIBRATION` | `ZDOWN_SWITCH`/`SET_G29_FLAG`/`NEXT_HOMEZ_NACCU` (`[z_align]`/prtouch_v3) | Ersatzablauf `ZALIGN_STOCKART` (zalign.cfg, braucht Variante B) |
| `CALIBRATE_CUT_POS` | `MOTOR_SYS_PARAM`/`MOTOR_CHECK_CUT_POS` – braucht k2_motor-G-Codes (O) |
| `BELT_TENSION` | `BELT_MDL_*`; Ersatz-Beispiel `KTWO_BELT_TENSION` (ruft `KTWO_BELT_*`) in README-k2_belt.md – seit dem Modul-Fix gültig, aber bewusst nicht aktiv (fährt G28 X Y + spannt; erst nach Freigabe README-k2_belt.md §6) |
| `INPUTSHAPER`, `AUTOTUNE_SHAPERS` | Creality-Makros; mit `[k2_lis2dw]` stattdessen Upstream-`SHAPER_CALIBRATE` nutzen; `CXSAVE_CONFIG` |
| `NOZZLE_PID_HIGH` | zweiter PID-Satz (Creality) |
| `TUNOFFINPUTSHAPER` | trivial (`SET_INPUT_SHAPER SHAPER_FREQ_X=0 SHAPER_FREQ_Y=0` direkt nutzbar) |
| `SET_CHAMBER_FAN`, `CANCEL_CHAMBER_FAN_SWITCH` | Watermark-Hack bzw. `SET_TEMPERATURE_FAN_SWITCH` (Creality) |
| `SET_PIN`-Override (fan0_en) | Logik steckt jetzt in M106/M107 (direktes `SET_PIN PIN=fan0` schaltet fan0_en NICHT mit) |
| `WAIT_BED_STABLE_POS`, `EXTRUDE_TEST`, `TEST_HOME`, `A_TriPress_STEP1/2`, `A_G28_test`, `START_TIMER`, `END_TIMER` | Werks-/Debug-Tests, teils `BOX_*`/`TEST_REPROBE` |
| `PAUSE_EXTERNAL`, `RESUME_EXTERNAL(_PROCESS)`, `END_PRINT_POINT*`, `END_PRINT_Z_SAFE` | in PAUSE/RESUME/END_PRINT zusammengefasst; `BOX_*`, `BOX_ERROR_RESUME_PROCESS`, `filament_rack`-Geschwindigkeit, `CLEAR_EEPROM_INFO`, `EXCLUDE_OBJECT_RESET` (Creality; Upstream setzt beim Datei-Reset selbst zurück) entfernt |

Namensregel (Upstream, `gcode.py`): Ein Befehl darf nicht mit Buchstabe + Ziffer beginnen (`register_command` lehnt `K2_*` ab: „Can't register … invalid name“) und sollte überhaupt **keine Ziffern** enthalten: der Parser (`_process_commands`, `args_r = ([A-Z_]+|[A-Z*])`) kappt den Namen an der ersten Ziffer (`_K2_X` → `_K2`, `KTWO_485_STATUS` → `KTWO_485`, beides „Unknown command“, obwohl die Registrierung klappt – Batch-Probe G). Deshalb: Hilfsmakros `_PI_*`, Modulbefehle `KTWO_*` (z. B. `KTWO_MOTOR_INIT`, `KTWO_PRTOUCH_READ`, `KTWO_BOX_STATUS`, `KTWO_BELT_TEST`, `KTWO_BUS_STATUS`). Webhook-Endpunkte (`k2_belt_test`) prüft Upstream nicht (`webhooks.register_endpoint`) – sie behalten `k2_`. Config-Abschnitte bleiben `[k2_*]`.

## 3. Installation (Pi, ANNAHME Standard-Klipper-Installation unter `~/klipper`)

Alles liegt in `~/klipper/klippy/extras/` (ein Ziel, keine Pfad-Tricks):

```bash
E=~/klipper/klippy/extras
cp pi-host/k2_transport.py pi-host/k2_485.py pi-host/k2_box.py \
   pi-host/k2_prtouch.py pi-host/k2_belt.py  $E/
cp pi-host/k2_motor_init.py $E/k2_motor.py          # Dateiname = Abschnitt [k2_motor]!
mkdir -p $E/k2_nachbau && cp nachbau/*.py $E/k2_nachbau/
# optional, nur für zalign.cfg Variante B (A10, NOTIZ-zalign.md §5 ab T4):
# cp pi-host/k2_zalign.py $E/
```

1. **Module** wie oben. `k2_transport.py` und `k2_nachbau/` sind keine Abschnitte, sondern Helfer.
2. **`nachbau/` = `klippy/extras/k2_nachbau/`** (Festlegung 16.10.2026). Pflicht für `[k2_485]` und `[k2_box]` (serial485, box, cfsseq, auto_addr; motor optional). Suchreihenfolge in `k2_transport.find_nachbau_dir()` (erster Ordner mit `serial485.py`): `$K2_NACHBAU_DIR` → `<extras>/k2_nachbau/` → `<extras>/../nachbau/` (Repo-Layout `pi-host/../nachbau`, Offline-Tests) → `<extras>/../../nachbau/` (alter Weg `~/klipper/nachbau/`, weiter gültig). Der Ordner wird nur **angehängt** (`sys.path.append`), die Module fügen ihren eigenen Ordner **nie** in `sys.path` ein (Geschwister per `from . import k2_transport`); `import_nachbau`/`check_nachbau_module` brechen mit klarer Meldung ab, falls ein gleichnamiges Fremdmodul (z. B. pip-Paket `box`) gefunden wird. Batch-Lauf F/I: `serial485`, `box`, `cfsseq`, `auto_addr`, `motor` laut `python -v` aus `extras/k2_nachbau/`, `statistics` aus der Standardbibliothek.
3. **pyserial** muss im Klipper-venv vorhanden sein (Upstream-Abhängigkeit, i. d. R. schon installiert); fehlt es, meldet `[k2_485]` das beim Connect (kein Absturz).
4. Config-Dateien nach `~/printer_data/config/`.
5. by-id-Pfade der beiden USB-UARTs (`ls -l /dev/serial/by-id/`) in `[mcu]`/`[mcu nozzle_mcu]` eintragen, den des USB-RS485-Adapters in `[k2_485] serial:` (k2-module.cfg).
6. Moonraker-Update-Manager: die kopierten Dateien machen den Klipper-Checkout „dirty“ (ANNAHME: wie bei jedem extras-Zusatzmodul) – nach `git pull` erneut kopieren.

## 4. Inbetriebnahme-Reihenfolge (nach PI-ERSATZ-PLAN §4.3, angepasst)

Jeder Schritt nur, wenn der vorige bestanden ist; nach jedem Schritt `klippy.log` prüfen (SHUTDOWN, Timeouts, Config-Fehler). Hand am Not-Aus, sobald sich etwas bewegen kann. Befehlsnamen seit dem Modul-Fix `KTWO_*` (§2.3 Namensregel).

0. **Vorbereitung:** Verkabelung nach PI-ERSATZ-PLAN §3 (3,3-V-UARTs, RS485 A/B gemessen, GND zuerst). Alle Sperren wie geliefert (§1).
1. **Nur MCU-Verbindung, ohne Bewegung** (Abweichung vom Plan: k2_prtouch/k2_box/k2_motor bleiben geladen, aber gesperrt – ohne `[k2_prtouch]` gäbe es keinen Z-Endstop). Erwartet: `Klipper ready`, im Log `Loaded MCU 'mcu' … 1.1.0.48-293-g493f9a0f` und dasselbe für `nozzle_mcu`, `FIRMWARE_RESTART` sauber. Prüfen: Temperaturen (Düse, Bett, Kammer ≈ Raumtemperatur), `QUERY_ENDSTOPS` (x/y/z „open“), `QUERY_PROBE` („open“), `M106 P0 S128` / `M107` (Lüfter hörbar), `G28` → muss mit „G28 gesperrt“ abbrechen. `ZALIGN_STATUS` → `zalign_z: RELEASED` / `zalign_z1: RELEASED` (Bett nicht ganz unten). **Abbruch** bei: kein Connect, falsche MCU-Version, Config-Fehler → UART/Baud prüfen, STOP.
2. **Motor-Init Trockenlauf:** `KTWO_MOTOR_INIT DRY_RUN=1` → Plan P0–P9 (167 Schritte), Strapping X 0x81/Y 0x82/Z 0x83/Z1 0x84 (MOTOR-INIT-BEFUND §10). Abweichung → Config korrigieren, STOP.
3. **RS485-Bus + Motor-Init lesend:** `KTWO_BUS_STATUS` → `offen=True` (sonst Port/Adapter prüfen; Meldung `[k2_485] Port … nicht zu öffnen` beim Connect). Dann `KTWO_MOTOR_STATUS` (Version/Schutz/Feedback X/Y/Z/Z1 über `k2_485`, E über transparent) und `KTWO_MOTOR_INIT` mit `allow_write: False` – nur Plan, nichts gesendet. `KTWO_BOX_STATUS` (lesend) ebenso.
4. **Motor-Init schreibend:** `allow_write: True`, danach `auto_init: True` → „All motors are normal“, Stall Z/Z1/E, `0C [0b]` 8×00, Param-Abgleich, Event `k2_motor:ready`. Abbruch bei key798–802, STATUS-Bits ≠ 0, `0C`-Fehlerbits → STOP, kein Homing.
5. **PrTouch ohne Bewegung:** `KTWO_PRTOUCH_TEST_SWAP`, `KTWO_PRTOUCH_READ`, `QUERY_PROBE` (open), `KTWO_PRTOUCH_STATUS`; `PROBE` muss mit „allow_move“-Fehler abbrechen (k2_prtouch README A.6).
6. **Homing X/Y:** `[k2_motor] allow_write: True`; vorher `KTWO_MOTOR_HOMING PHASE=BEGIN` im Stand + `QUERY_ENDSTOPS` (Polarität PB11/PB12 prüfen, NOTIZ-stall.md §5), dann `KTWO_MOTOR_HOMING PHASE=END`. Danach zur Laufzeit `SET_GCODE_VARIABLE MACRO=_PI_FREIGABE VARIABLE=allow_move VALUE=True`, `G28 Y`, dann `G28 X` (einzeln, Hand am Not-Aus). Erwartet nach G28 X Y: X 342,5 / Y 3,8 (im Batch-Test so). Abbruch bei Schrittverlust/Blockade.
7. **Z mit Probe:** `[k2_prtouch] allow_move: True`; Düse ~3 mm über dem Bett, `SET_KINEMATIC_POSITION Z=10`, `PROBE` (Stopp beobachten), `PROBE_ACCURACY SAMPLES=5`, dann `G28` (Z in Bettmitte, ≤ 5 mm/s), `Z_TILT_ADJUST`, `G28 Z`, `BED_MESH_CALIBRATE PROFILE=default` (bzw. `G29`), Ergebnis gegen `stock_referenz_2026-10-16` (−0,067 … 0,226) plausibilisieren, `SAVE_CONFIG`.
7a. **A10 Dual-Z** nach NOTIZ-zalign.md §5 (mindestens T3 vor dem ersten Druck).
8. **Heizen/Kammer:** Düse/Bett aufheizen, ggf. `NOZZLE_PID`/`BEDPID`; `M141 S45` → PTC-Lüfter (!PB14/PB2) MUSS sofort laufen, sonst sofort `M141 S0` (§6).
9. **Erstdruck einfarbig** (Slicer: CFS aus; `START_PRINT EXTRUDER_TEMP=… BED_TEMP=…`).
10. Optional je Modulreife (alle über `k2_485`): Belt lesend (`KTWO_BELT_INFO`/`KTWO_BELT_TEST`), dann `allow_write`/`allow_move` laut README-k2_belt.md §6 (CALI nur ohne Riemen); CFS lesend → schreibend (`[k2_box] allow_write: True`, Wechsel-Makros).

## 5. Offline-Validierung (Upstream-Klipper, Batch-Modus, ohne Hardware)

### 5.1 Aufbau

- Arbeitsordner `arbeitsdateien-lokal/upstream-test/` (Beschreibung `LIESMICH.txt` dort). Upstream-Kopie OHNE `.git` aus `arbeitsdateien-lokal/upstream-klipper/` (Commit 9cb9975; Referenz-Klon unverändert).
- Läufe A–E (Stand 16.10.2026 19:44): Module → `klipper/klippy/extras/`, `nachbau/*.py` → `klipper/nachbau/`; B mit Test-Patches in `klipper-testpatch/`.
- **Läufe F–I (Modul-Fix 16.10.2026 abends, OHNE Testpatches):** frische Kopie des Referenz-Klons als `upstream-test/klipper-clean/`, darin NUR unsere Module nach dem Installationsweg §3 (`extras/k2_*.py`, `k2_motor_init.py` als `extras/k2_motor.py`, `nachbau/*.py` → `extras/k2_nachbau/`).
- Lokales venv `upstream-test/venv` (`--system-site-packages`, nachinstalliert nur `greenlet 3.3.2` + `pyserial`; cffi 2.1.1/Jinja2 3.1.6 vom System). Python 3.14.8.
- Dictionaries direkt aus `arbeitsdateien-lokal/lokale MCU-Dictionaries/` (Roh-JSON, ohne Anpassung lesbar): Main `gd32f303xe`, Düse `gd32f303xb`, beide `1.1.0.48-293-g493f9a0f`.
- Befehl (`upstream-test/lauf.sh`):

```bash
cd arbeitsdateien-lokal/upstream-test
venv/bin/python klipper-clean/klippy/klippy.py ../../pi-host/config-pi/printer.cfg \
  -i /dev/null -o lauf-X/out.serial -v \
  -d ../lokale MCU-Dictionaries/mcu-main-F008-GD32F303RET6.dict.json \
  -d nozzle_mcu=../lokale MCU-Dictionaries/noz-duese-F008-GD32F303CBT6.dict.json \
  -l lauf-X/klippy.log
# Dekodieren:
venv/bin/python klipper-clean/klippy/parsedump.py ../lokale MCU-Dictionaries/mcu-main-F008-GD32F303RET6.dict.json lauf-X/out.serial
venv/bin/python klipper-clean/klippy/parsedump.py ../lokale MCU-Dictionaries/noz-duese-F008-GD32F303CBT6.dict.json lauf-X/out.serial-nozzle_mcu
```

(`KLIPPER=klipper-clean ./lauf.sh <cfg> <ordner>`; `-d <datei>` = Main-MCU, `-d <mcu-name>=<datei>` = weitere MCU, siehe `arg_dictionary` in `klippy.py`; Ausgabe der Zusatz-MCU in `<-o>-nozzle_mcu`.)

### 5.2 Ergebnisse

| Lauf | Inhalt | Ergebnis |
|---|---|---|
| **A** | Auslieferung, ORIGINAL-Module | **Abbruch:** `Can't register 'K2_PRTOUCH_PROBE_STEPS' as it is an invalid name` |
| (während der Erstellung) | dito, nacheinander | `Can't register 'K2_BOX_STATUS' …`; k2_motor: `Unknown config object 'nozzle_mcu'` (→ Config: `mcu nozzle_mcu`), danach `Unknown config object 'ppins'` |
| **B** | Auslieferung, Module mit Test-Patches (§5.3) | **EXIT 0, fehlerfrei**: Config geparst, keine unbekannten Optionen, MCU-Konfiguration für beide MCUs erzeugt (`Configured MCU 'mcu' (500 moves)`, dito `nozzle_mcu`). Beim Connect erwartungsgemäß: `[k2_box] Connect ohne Bus …`, `[k2_belt mdlx]`/`[k2_belt mdly] RS485-Bus nicht verdrahtet …` (kein `k2_485`, §6.2) |
| B-makros/-homing/-luefter/-druck | Test-G-Code (`test-*.gcode`) | Sperren greifen: `G28 gesperrt: _PI_FREIGABE allow_move=False`, `G28 X/Y gesperrt: … xy_stall_ok=False`, G28 Z → `[k2_prtouch] … allow_move: False … Keine Bewegung`; `T0` ok, `T1`/`M8200` → CFS-Fehler; Homing X/Y mit Freigabe: zuerst Y-Endstop (oid PB12, `pin_value=1`), dann X (PB11), Position danach 342,5/3,8; `PAUSE`/`RESUME` ok; `QUERY_PROBE` → `probe: open`; Lüfter dekodiert korrekt (z.B. `M106 P0 S128` → fan0_en an, PB15 invertiert 539054/1200000 Ticks; `M106 P1 S100` → PA0 613610) |
| C | + Stock-`[lis2dw]` | `Command format mismatch: query_lis2dw oid=%c rest_ticks=%u vs query_lis2dw oid=%c clock=%u rest_ticks=%u` → lis2dw entfällt |
| C2 | + Stock-`mcu_temp` | im Batch-Modus kein Fehler, weil `temperature_mcu` mit `-o` die MCU-Prüfung überspringt (`debugoutput`-Zweig) – statisch: `gd32f303xe` → `config_unknown` → Abbruch am Gerät |
| D | `duplicate_pin_override` ohne `nozzle_mcu:PB1` | `pin PB1 used multiple times in config` → Ergänzung nötig |
| E | `[k2_prtouch] allow_move: True` + G28 | Signalweg-Test schlägt fehl (`ausgelöst=False bei sta_swap=0`, keine MCU-Antworten im Batch) → **keine** Z-Fahrt; Z/Probe/Mesh offline nicht prüfbar |
| **F** | Auslieferung, **gefixte Module ohne Testpatches** (`klipper-clean/`), `[k2_485]` aktiv | **EXIT 0, fehlerfrei** (kein Config-/Internal error, kein Traceback außer den üblichen `.version`/`device-tree`-Hinweisen). Connect: `[k2_485] Batch-Modus: RS485-Port … wird nicht geöffnet`, `[k2_box] bereit (warte auf k2_motor:ready …)`; `[k2_belt]` findet den Bus (keine „nicht verdrahtet“-Meldung mehr) |
| F-makros/-homing/-luefter/-druck | Test-G-Code wie B | Fehlerbild identisch zu B (nur `K2_`→`KTWO_`); EXIT 255 bei makros/homing/druck = die gewollten Sperr-Fehler (`G28 gesperrt …`, CFS-Sperre) im Batch, luefter EXIT 0 |
| **G** | wie F + `test-485.gcode` (`KTWO_BUS_STATUS`, `KTWO_MOTOR_INIT DRY_RUN=1`, `KTWO_MOTOR_STATUS`, `KTWO_BOX_STATUS`, `KTWO_BELT_TEST`, `KTWO_PRTOUCH_STATUS`) – `[k2_485]` ohne vorhandenen Port | kein Absturz, kein Shutdown: `KTWO_BUS_STATUS` → `offen=False batch=True fehler=Batch-Modus …`; Plan P0–P9 mit Strapping 0x81–0x84 ok; `KTWO_MOTOR_STATUS` X/Y/Z/Z1 → `[k2_485] Batch-Modus …`, E → `transparent … keine Antwort`; Box `Poll-Fehler: [k2_485] …`; Belt → G-Code-Fehler `RS485-Bus nicht verfügbar …`. (Vor dem Belt-/Box-Härtungsfix führte genau das zu `Internal error` + Shutdown, und der erste Name `KTWO_485_STATUS` zu `Unknown command:"KTWO_485"` → umbenannt) |
| A10 | `upstream-test-a10/` (`lauf-alles.sh`) | 13 Läufe, 28/28: L/B EXIT 0, Z nur über trsync(PA15), Z1 nur über trsync(PA8), 370 mm/148 Etappen/10 mm/s, Sperren ohne Z-Schritt, Konflikt L+B erkannt, Erfolgsweg mit Test-Fake |
| RESUME-Schnitt | `upstream-test-resumecut/` | 7 Läufe, Bahnprüfung 83/83 aus MCU-Schritten: nach CFS-Abbruch Hebel nur in X und einmal angefahren, nie unter Pausen-Z, Endlage = Druckposition vor dem Wechsel, E unverändert; Sperr-Läufe ohne Bewegung (EXIT 255 gewollt) |
| **H** | wie G, aber **ohne** `[k2_485]` | kein Absturz: `[k2_motor] RS485-Bus fehlt: Abschnitt [k2_485] (serial: <by-id-Pfad>, baud: 230400) in printer.cfg anlegen …`, Box/Belt „Abschnitt [k2_485] fehlt in printer.cfg“, `KTWO_BUS_STATUS` unbekannt (erwartet), DRY_RUN-Plan geht |
| **I** | wie F, `[k2_prtouch]` ans Ende von printer.cfg verschoben (`cfg-I/`), `python -v` | EXIT 0, fehlerfrei; `statistics` aus `/usr/lib/python3.14/statistics.py`, `extras/statistics.py` nie geladen; nachbau-Module aus `extras/k2_nachbau/` → Reihenfolge egal |

**Stichproben aus Lauf F (parsedump, `lauf-F/main-dekodiert.txt`, `duese-dekodiert.txt`, gefixte Module):**

- `allocate_oids count=37` (Lauf B: 38 – das Query-OID ohne config-Befehl entfällt, §5.3 Punkt 6).
- Endstop `config_endstop oid=0 pin=PC7 pull_up=0` + `config_trsync oid=1`; `config_prtouch_step oid=10 oid_xstp=4 oid_ystp=7 oid_zstp=9 swp_pin=PC7`.
- Strapping-Pins `config_digital_out` oid 11–19: PB9, PB10, PB7 (value 1 = `!PB7`), PB8, PB5, PB6, PA1, PB15, PA9 (value 1 = `!PA9`) – **alle `max_duration=0`** (Lauf B: `max_duration=240000000` = 2 s); danach `config_stepper oid=4 PB8/PB7`, `oid=7 PB10/PB9`, `oid=8 PB6/PB5`, `oid=9 PB15/PA1`, alle `step_pulse_ticks=240`; stepper_enable PA9 als oid 36 (ebenfalls `max_duration=0`).
- Düse: `config_prtouch_pres oid=2 idx=0 swp_pin=PA15 clk_pin=PB13 sdo_pin=PB14`, `config_transparent oid=0`, `config_stepper oid=1 step_pin=PB5 dir_pin=PB4`.
- Kein `query_gpio_status`/`transparent_send` im Batch (Init läuft nur auf Befehl / `auto_init`).

**Stichproben aus Lauf B (parsedump, `lauf-B/main-dekodiert.txt`, `duese-dekodiert.txt`):**

- Stepper Main: `config_stepper oid=5 step_pin=PB8 dir_pin=PB7` (X, `!` Host-seitig), `oid=8 PB10/PB9` (Y), `oid=9 PB6/PB5` (Z), `oid=10 PB15/PA1` (Z1); alle `step_pulse_ticks=240` (2 µs @ 120 MHz, Upstream-Default).
- Endstops: `config_endstop oid=0 pin=PC7 pull_up=0` (Probe, Invertierung `!PC7` Host-seitig) + `config_trsync oid=1`; `PB11` (oid 3), `PB12` (oid 6).
- `config_prtouch_step oid=11 oid_xstp=5 oid_ystp=8 oid_zstp=10 swp_pin=PC7` (zstp = stepper_z1, wie k2_prtouch-Befund).
- Düse: `config_transparent oid=0` (k2_motor), `config_prtouch_pres oid=2 idx=0 swp_pin=PA15 clk_pin=PB13 sdo_pin=PB14`, `config_stepper oid=1 step_pin=PB5 dir_pin=PB4`, Heizung PB8 (PWM 12 000 000 Ticks = 0,1 s), ADC PA0, Filamentsensor `buttons_add pin=PA11 pull_up=1` + `invert=1`.
- k2_motor-Strapping-Pins als `config_digital_out` PB9, PB10, PB7 (value 1 = `!PB7` logisch 0), PB8, PB5, PB6, PA1, PB15, PA9 (value 1 = `!PA9` aus) – vor den `config_stepper` derselben Pins (Zeitmultiplex wie Stock).
- PC5 dreifach `config_analog_in`, PA0 zweifach PWM (temperature_fan + fan1), PB2 zweifach (heater_fan-enable + ptc_power), Düsen-PB1 zweifach – alles wie Stock.

### 5.3 Gefundene Modul-Probleme – BEHOBEN 16.10.2026 abends (Lauf F–I ohne Testpatches)

Ursprünglich nur per Test-Patch umgangen (`upstream-test/klipper-testpatch/`, Diffs `upstream-test/testpatch/*.diff`, bleiben als Beleg). Backups vor dem Fix: `arbeitsdateien-lokal/modulfix-backup/` (pi-host + nachbau).

1. **G-Code-Namen `K2_*` ungültig** (`gcode.py register_command`: 2. Zeichen Ziffer) → alle registrierten Befehle in k2_motor, k2_box, k2_prtouch, k2_belt auf `KTWO_*` umbenannt (Mux-Befehle von k2_belt ebenso; Stock-Aliase `BELT_MDL_*` waren schon gültig). Webhook-Endpunkte `k2_belt_test`/`belt_mdl_test` bleiben (Upstream prüft Endpunkt-Namen nicht). Neu `KTWO_BUS_STATUS` ([k2_485]). Tests prüfen jetzt jeden registrierten Namen gegen die Upstream-Regel inkl. Parser-Zerlegung (test_k2_prtouch mit der echten `GCodeDispatch.register_command` + `args_r`).
2. **k2_motor `lookup_object("ppins")`** → `"pins"` (`KlippyGpio`); die Pin-Nummer für `query_gpio_status` kommt aus der MCU-Enumeration (Upstream-`pins.lookup_pin` liefert nur Namen und markiert den Pin als belegt – wird nicht mehr aufgerufen). k2_prtouch-Altcode ebenfalls auf `pins`.
3. **k2_motor `_build_transparent_config(self)`** ohne Argument (Upstream ruft `cb()`).
4. **k2_motor `nozzle_mcu`** wird wie in k2_prtouch aufgelöst (`_lookup_mcu`: erst `"mcu " + name`, dann wörtlich) → Config wieder `nozzle_mcu: nozzle_mcu`.
5. **Import-Falle `sys.path.insert(0, <klippy/extras>)`** beseitigt: kein Modul fügt seinen Ordner in `sys.path` ein, Geschwister per `from . import k2_transport` (Fallback `import k2_transport` nur offline), `nachbau/` per `k2_transport.ensure_nachbau_path()` nur angehängt (§3). Die Workaround-Reihenfolge „[k2_prtouch] vor den Includes“ ist damit unnötig (Lauf I).
6. **Strapping-Pins `max_duration`:** jetzt `setup_max_duration(0.0)` für alle Dir/Step-Pins + `!PA9` – BELEGT als Stock-Verfahren: das Stock-`.so` ruft in `motor_x/y/z/z1_init` und für `enable_pin_pa9` `setup_max_duration` mit Konstante `0.0` direkt nach `setup_pin('digital_out', …)` auf (`arbeitsdateien-lokal/lokale Analyse-Dateien`, z. B. 0x73ba8/0x73c54, 0xce8bc). Entspricht dem Upstream-Muster für statische Pins (`output_pin`, `stepper_enable`).
7. **Abfrage-OID `query_gpio_status`:** Firmware-Handler (Main-Image `mcu0_140`, 0x08003424) liest nur `gpio_pin`, Port-Tabelle GPIOA–E, Register-Offset 0x0C (GD32 `OCTL` = Ausgangs-Latch) und schickt `gpio_status` mit der OID **unverändert** zurück – kein `oid_lookup`, kein `config_*` nötig. Deshalb keine `create_oid` mehr (Konstante 0, `allocate_oids` 38 → 37). Nebenbefund: `HANDLER-TABELLE.txt` (lokale Analyse-Dateien*) ordnet die Handler um einen Eintrag verschoben zu (Handler zum Info-Wort steht 12 Byte weiter; gegengeprüft an `config_digital_out` 0x08006050 und `config_transparent` 0x080036AC) – Aussagen, die sich auf diese Zuordnung stützen, nachprüfen.
8. **Neu [k2_485]** (Punkt 4 des Auftrags): `pi-host/k2_485.py`, s. §6.2. Zusätzlich gehärtet (Batch-Probe G): k2_belt (`_xfer`) und k2_box (`KTWO_BOX_LOAD/UNLOAD/PRELOAD`), k2_motor (`KTWO_MOTOR_INIT`/Auto-Init) melden Bus-/Port-Fehler (`RuntimeError`/`OSError`) als G-Code-Fehler statt „Internal error“ + Klipper-Shutdown.

### 5.4 Offline prinzipiell nicht prüfbar

RS485-Port und alles darüber (`[k2_485]` öffnet im Batch-Modus bewusst nie; Motor-Init, Box, Belt nur über Fake-Seriell in den Offline-Tests), Antworten der MCUs (`query_gpio_status`, `transparent_*`, `*_prtouch`, Endstop-Zustände, ADC-Werte → Heizregelung, Watermark, heater_fan-Reaktion), Stopp unter Bewegung (trsync PC7), `temperature_mcu`-Prüfung (im `-o`-Modus übersprungen), tatsächliche Pin-Pegel/Polaritäten, Zeitverhalten.

## 6. Offene Punkte und ANNAHMEN

1. **Modul-Fixes §5.3** – erledigt 16.10.2026 abends (Lauf F–I). Am Gerät ungetestet.
2. **RS485-Bus `[k2_485]`** (`pi-host/k2_485.py`): öffnet GENAU EINE `nachbau/serial485.Serial485`-Instanz (`serial:` Pflicht, `baud:` Default 230400, `timeout:` 1,0 s). Klipper legt das Objekt beim Laden unter `k2_485` ab (`load_object` = Wirkung von `add_object`); `[k2_motor]` lädt es bei Bedarf selbst vor (`has_section` + `load_object`) und nutzt es für X/Y/Z/Z1 (`Rs485Transport`), `[k2_box]`/`[k2_belt]` holen es per `lookup_object('k2_485')` → Reihenfolge egal. Port: Öffnen erst bei `klippy:connect`, faul erneut bei jedem Zugriff, Schließen bei `klippy:disconnect`/Ausfall (`OSError`, nicht `TimeoutError`). **Ohne Port:** Batch-Modus → nie geöffnet, Meldung `Batch-Modus: RS485-Port … wird nicht geöffnet`; Normalbetrieb → Meldung `[k2_485] Port … nicht zu öffnen (…)` beim Connect, kein Absturz/Shutdown, `get_status`/`KTWO_BUS_STATUS` zeigen `offen=False` + Fehler, Zugriffe der Module → G-Code-Fehler. **Fehlt `[k2_485]`:** `[k2_motor] RS485-Bus fehlt: Abschnitt [k2_485] … anlegen` (nur `DRY_RUN=1` geht), Box/Belt analog. ANNAHME: USB-RS485-Adapter mit Auto-DE (Halbduplex ohne RTS-Steuerung, wie `serial485.py`). OFFEN: Bus-Zugriffe sind blockierend (Serial485-Lock + pyserial) und laufen im Klipper-Reaktor-Thread (bei Box-Poll/Init kurzzeitige Blockade, Stock-Wrapper ähnlich) – am Gerät auf „Timer too close“ achten.
3. **ERLEDIGT 16.10.2026 (NOTIZ-stall.md):** Alter Stand – Stall-Mode für X/Y-Homing: Stock setzt vor dem Homing `MOTOR_STALL_MODE DATA=1` (Endschalter-Signal auf PB11/PB12) und danach `DATA=2` + Schutzprüfung + Kp ID 70 300→100. `[k2_motor]` hat dafür noch keine G-Codes → `_PI_VOR_HOMING`/`_PI_NACH_HOMING` leer, `xy_stall_ok: False` sperrt X/Y-Homing.
4. **Stock-`FORCE_MOVE STEPPER=stepper_y DISTANCE=-3` nach dem XY-Homing** nicht übernommen (CoreXY: Versatz je 1,5 mm in X/Y ohne Positionsupdate, Creality-force_move ggf. anders). Koordinaten am Gerät gegen Stock prüfen (Purge/Wischposition Y 374–378!), ggf. `position_endstop` anpassen.
5. **Düsenreinigung vor dem Antasten** (Stock `BOX_NOZZLE_CLEAN` am Silikonstreifen X160–170/Y374, `NOZZLE_CLEAR`) fehlt – wichtig für die Kraftsensor-Genauigkeit; als reines G-Code-Makro nachbaubar (Koordinaten in k2-module.cfg-Kommentar).
6. **prtouch_v3-Kompensationen** (Temperatur nichtlinear, `bed_mesh_fall_comp_*`, `need_steady_regional`, `prth_min_fans`) fehlen in k2_prtouch.
7. **PTC-Lüfter:** ANNAHME, dass `is_ptc_fan` nur „läuft, solange die Kammerheizung ein Ziel hat“ bedeutet – am Gerät prüfen (Schritt 8), sonst Überhitzungsgefahr.
8. **ERLEDIGT 16.10.2026 (`[k2_lis2dw]`, NOTIZ-accel.md):** Alter Stand – Beschleunigungssensor: eigenes Modul für das alte Creality-lis2dw-Protokoll nötig (oder externer ADXL); bis dahin nur gespeicherte Shaper-Werte.
9. **MCU-Temperatur** (gd32f303xe/xb) nicht in Upstream; optional eigenes Mini-Modul.
10. **Hochtemperatur-PID** (Stock ab 280 °C eigener Satz) fehlt.
11. **`[idle_timeout]` 99999999** wie Stock: Heizungen gehen nie automatisch aus – bewusst entscheiden.
12. **ERLEDIGT 16.10.2026 (NOTIZ-cfs.md, nur Box 1):** Alter Stand – CFS-Wechsel (`T1–T15`, `M8200`) gesperrt, bis k2_box-Wechselablauf + Cutter (`CALIBRATE_CUT_POS`, braucht k2_motor-G-Codes) stehen.
13. **`[k2_belt]`** eingetragen, aber gesperrt; Rüttel-Zyklus (Default-`cycle_gcode`) fährt X/Y per FORCE_MOVE ±200 mm ohne Endschalterschutz – erst nach X/Y-Homing-Freigabe.
14. **`[z_align]`-Ersatz:** lesend aktiv, `[k2_zalign]` als Entwurf gesperrt (NOTIZ-zalign.md), Gerätetest offen; `START_PRINT` holt `Z_TILT_ADJUST` nach Stromlos nach (A10a, NOTIZ §3.2).
15. **ANNAHMEN:** by-id-Namen; RS485-Adapterpfad; `~/printer_data/gcodes`; Bedeutung `power` PC9; PA0 doppelt angesteuert (fan1 + temperature_fan, wie Stock) unkritisch; Düsen-PB1 doppelt (hotend_fan + extruder_fan, wie Stock) unkritisch; Stock-`PB1` im Override meinte die Düsen-MCU; `minimum_cruise_ratio 0.0` ≙ `max_accel_to_decel = max_accel`; `move_check_distance 3` statt 1 unkritisch; Upstream-Default `step_pulse_duration` 2 µs passt zu den Closed-loop-Treibern; kein `M84` am Druckende (wie Stock).

## 7. Ergänzungen 16.10.2026 (Details in den Notizen)

| Thema | Datei | Kurz |
|---|---|---|
| Sensorless X/Y-Homing | `NOTIZ-stall.md`, `homing.cfg`, `k2-motor.cfg` | Stock-Frame-Folge (Stall-Mode 1/2 an X/Y, Kp ID 70 300↔100, Schutzabfrage) über `KTWO_MOTOR_HOMING PHASE=BEGIN/END`; `FORCE_MOVE stepper_y -3` wie Stock |
| Beschleunigungssensor | `NOTIZ-accel.md`, `../README-k2_lis2dw.md` | `[k2_lis2dw]` spricht das alte Creality-Protokoll, liefert Upstream-Datenstruktur; Erstlauf im Stand (`ACCELEROMETER_DEBUG_READ REG=0x0f` → 0x44) |
| CFS-Farbwechsel | `NOTIZ-cfs.md`, `macros.cfg`, `k2-module.cfg`, `../README-k2_box.md` §8 | T0–T15 + M8200 P/C/R/L/W/F/O als `KTWO_BOX_*`-Kette, nur Box 1, ohne Freigaben nur Plan, Fehler im Druck → PAUSE mit Rückfahrziel = Druckposition vor dem Wechsel (NOTIZ-reinigung-resume §6a) |
| Düsenreinigung + RESUME | `NOTIZ-reinigung-resume.md`, `macros.cfg`, `homing.cfg` | `_PI_NOZZLE_CLEAN` (Silikonstreifen wie Stock `BOX_NOZZLE_CLEAN`) vor dem ersten Z-Antasten, in START_PRINT und G29; RESUME: Schacht → Aufheizen → Vorextrusion → Reinigung → Rückfahrt erst X/Y, dann Z (nie unter Pausen-Z); nach CFS-Abbruch: PAUSE verlässt Hebel (nur X)/Schacht (X160→Y345), RESUME fährt zur Druckposition vor dem Wechsel und setzt PAUSE_STATE neu – nie in den Schneidhebel (§6a) |
| Schneidposition kalibrieren | `NOTIZ-cutcal.md`, `cutcal.cfg`, `../README.md` §10 | `CALIBRATE_CUT_POS [SAVE=1]` → `KTWO_MOTOR_CUT_CAL` (Stock-Folge Kp 300, Stall-Mode 3, Anstöße an PB11, +0,4, Mode 2, Kp 100); Wert über SAVE_CONFIG in `[k2_box] cut_pos_x` |
| CFS-Nachladen bei leerer Spule | `NOTIZ-runout.md`, `k2-module.cfg`, `printer.cfg` (Filamentsensor), `../README-k2_box.md` §9 | `runout_gcode: KTWO_BOX_RUNOUT`, Ersatz-Slot gleiches Material + Farbe (RFID) oder eigene Gruppe; Default aus (`runout_refill: False`), sonst PAUSE |
| Dual-Z-Lichtschranken (17.10.2026) | `NOTIZ-zalign.md`, `zalign.cfg`, `../k2_zalign.py` | lesend `[gcode_button]`; Ausrichtung `KTWO_ZALIGN` / `ZALIGN_STOCKART` gesperrt; Testprotokoll A10; `START_PRINT` → `_PI_ZTILT_WENN_NOETIG` |

Gemeinsame Batch-Prüfung aller Module mit dieser Config (Upstream 9cb9975, Live-Dictionaries): **EXIT 0**,
MCU-Ausgabe u. a. `config_endstop pin=PC7`, `config_prtouch_pres … clk_pin=PB13 sdo_pin=PB14`,
`config_lis2dw oid=… spi_oid=…`, Strapping-Pins `max_duration=0`. Am Gerät ist alles UNGETESTET.
