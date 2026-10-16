# REA-Motor-Befund 16.10.2026: motor_control_wrapper.cpython-39.so (K2 FW 1.1.7.0, armhf, Cython 0.29.21)

> **Nachtrag 16.10.2026 (Traceback):** Der letzte offene Schritt in §1d ist belegt. Drucker-`klippy.log`
> 16.10. 13:12:27: `motor_control_wrapper.py` line 1982 in `Motor_Control.motor_get` →
> `box_wrapper.py` line 384 in `ParseData.parse_num_to_byte` → `TypeError: 'NoneType' object cannot be
> interpreted as an integer`. Damit gilt **Variante A**: `get_int` liefert `None`, Absturz vor dem Senden,
> **kein Frame auf dem Bus**. Variante B ist widerlegt. Umsetzung im Nachbau: PROTOKOLL-485.md §3.1
> „NACHTRAG 16.10.2026 (2)“, AUDIT-UND-AENDERUNGEN.md.

Nur statische Analyse, nichts gesendet, kein Druckerzugriff.
Binary: `rootfs-live/usr/share/klipper/klippy/extras/motor_control_wrapper.cpython-39.so`,
SHA256 `d8191b77e1fda9f48afe93a064518635cc7d697475ad086ef1245d4f7b4b6e0f`.

**Adressen** = ELF-vaddr (wie bei `llvm-objdump`). In Ghidra/REA gilt **+0x10000** (Ghidra-Basis).
Python-Zeilennummern stammen aus den `__pyx_lineno`-Konstanten im Fehlerpfad.

Werkzeuge: REA/Ghidra 12.1.4 (Import, String-Suche; das Binary ist gestrippt, deshalb wenig
Funktionen und Xrefs, und große Funktionen überschreiten den Decompiler-Puffer), dazu eigene
Auflöser auf Basis von `llvm-objdump`:
Cython-Stringtabelle (1314 Einträge) → `pyx_strtab*.json`; `__pyx_int/float` → `pyx_consts.json`;
gecachte Tupel → `pyx_tuples.json`; PC-relative Lade-Paare; annotierte Disassembly `*.asm`.
Skripte und JSON-Auflösungen liegen nur in den lokalen Arbeitsdateien (nicht im Repo); Ghidra-Headless-Dekompilate und annotierte Disassembly ebenfalls nur lokal.

---

## 1. MOTOR_GET (Cmd 0x08, Request `[idx]`)

### 1a. Kommando-Byte und Adresse — BELEGT
Das Modul-Init (`0x10168`, Dict-Aufbau `0x10a9c`) baut zwei Dicts:
- `Motor_cmd`: reboot 0x01, encoder_calibrate_official 0x03, elec_offset_calibrate 0x04, control 0x05,
  sys_param 0x06, flash_param 0x07, **get 0x08**, boot 0x0b, protection 0x0c, systemid 0x0d,
  read485_addr/set485_addr 0x0e, version 0x0f, stall_mode 0x11, dev_uuid 0x12, err_detail_data 0x13,
  cut_detail_data 0x14, pos_data_sample 0x15, spd_data_sample 0x16.
- `Motor_Addr` (Schlüssel ist der NUM-String): '0'→0x80 … '8'→0x88, '9'→0xff (Broadcast).

Die Live-Werte 0x08, 0x0f, 0x0e und 0x0c stimmen damit überein.

### 1b. Namen der Indizes — TEILWEISE BELEGT
| idx | Name | Beleg |
|---|---|---|
| 5 | `pos_fdb` | Modul-Dict `NO_ERR_CODE_TO_GET` = {'pos_fdb':5,'spd_fdb':6,'id_fdb':7,'iq_fdb':8}, aufgebaut bei `0x10cf8–0x10d60` (r1=Schlüssel, r2=Wert) |
| 6 | `spd_fdb` | wie oben |
| 7 | `id_fdb` (d-Achsen-Strom) | wie oben, dazu Tupel `('motor_e_id_fdb','MOTOR_GET NUM=1 DATA=7 EXTRUDER=1')` @ `0x9f28` |
| 8 | `iq_fdb` (q-Achsen-Strom) | wie oben, dazu Tupel `('motor_e_iq_fdb','MOTOR_GET NUM=1 DATA=8 EXTRUDER=1')` @ `0x9f44` |
| 1–4, 9–12 | **kein Name im Host** | Es gibt keine weitere Tabelle. `MOTOR_DATAS` ist nur eine lokale Liste `[0..15]` in `validate_data_params` (`0x68298`, Schleife bis 16) |

Verwendung von `NO_ERR_CODE_TO_GET`: `_motor_no_cut_detail_data` (`0xa1bb4`, Modul-Global-Lookup bei `0xa20c4`)
iteriert `.items()` und liest jeden Index einzeln über Cmd 0x08 (BELEGT).
Ergebnis: Index 5–8 ist die Positions-, Drehzahl- und Stromrückmeldung der FOC-Regelung.

Hypothesen zu den übrigen Indizes (VERMUTET, keine Host-Belege):
- idx 9 = 0.0016113 = **3,3 V/2048** und idx 10 = 0.0008057 = **3,3 V/4096**, also genau 2 bzw. 1 LSB
  eines 12-Bit-ADC bei 3,3 V. Wahrscheinlich Roh-/Offsetspannungen der Strommessung (z. B. ia/ib).
- idx 11 = 1106 (int) passt zu einem Roh-Encoderwert (`encoder_fdb` ist in der Err-/Cut-Struktur ebenfalls `i`).
- Zur Gegenprobe kann man die Feldliste aus `err_detail_data` (Cmd 0x13, `'<I8BfHfiiffffffffi'`, `0x3a3f0`) heranziehen:
  err_code, dev_uuid[8], elec_offset, phase_order_invert, elec_offset_err_deg, encoder_fdb, encoder_fdb_last,
  pos_fdb_encoder, spd_fdb_encoder, spd_fdb_ctrl, id, iq, board_detect_power_supply_V,
  board_detect_temperature_C, pos_loop_pid_err, step_increment. `cut_detail_data` (0x14) hat dieselbe Liste plus ein `f`.

### 1c. Woher kommt float oder int? — BELEGT
Der Typ ist im Code fest verdrahtet, als Cython-Kette von `==`-Vergleichen (`x in (...)`).
Der Motor liefert keine Typinformation, eine Tabelle gibt es nicht.
- `motor_get` (`0x51524`, Py-Zeile 1987): `data in (1..10, 17, 18)` → `struct.unpack('<f', …)`
  (Vergleiche `0x51e04–0x520e8`). Zeile 1989: `data in (11..16)` → `struct.unpack('<I', …)` (`0x526bc–0x527c8`).
- Dieselbe Kette steht in `motor_extruder_get` (`0x88df0–0x89100`) und in `_motor_no_cut_detail_data`.
- Wertbytes = `result[5 : result[2] - 3 + 5]`, also `[5:9]` bei Länge 7
  (Helper Sub `0x1cf74`, Add `0x1cd38`, Slice `0x1d4bc`; `0x51d30–0x51dcc`).
- Ausgabe: `respond_info("系统所支持变量当前值%s: result:%s value:%s")`.
- Bemerkenswert: Der Host kennt die Indizes 13–18. Wegen `maxval=12` sind sie per G-Code aber nicht erreichbar.

### 1d. Absturz bei DATA=0 und DATA=13 — Mechanismus BELEGT (letzter Schritt per Traceback belegt, s. Nachtrag)
Ablauf in `motor_get` (Py-Zeilen 1971–1991):
1. `validate_num_params(gcmd)`, dann `validate_data_params(gcmd)` (`0x68268`):
   `get_int('DATA', 0, minval=0, maxval=15)`, Test `data in [0..15]`, alles in try/except.
   **DATA=0 und 13 bestehen diese Prüfung.**
2. `motor_extruder_get(gcmd)` gibt False zurück, wenn `EXTRUDER` fehlt (`0x879c0`, Tupel `('EXTRUDER','')`).
3. Zeile 1978: `num = gcmd.get_int('NUM', 1, minval=1, maxval=10)`.
   Zeile 1979: `data = gcmd.get_int('DATA', 0, minval=1, maxval=12)` (`0x51880–0x518ec`; Tupel `('DATA',0)` @ `0x1255ac`, Konstanten 1/12).
4. Danach `addr = Motor_Addr.get(...)`, `cmd = Motor_cmd.get('get')`, `self.parse.parse_num_to_byte(data)` (`0x51b38`).
   Erst anschließend folgt `self.box_action.motor_send_data(...)` (`0x51bec`).

Es gibt **keinen IndexError oder KeyError über eine Namenstabelle**, und `motor_get` selbst enthält kein try/except.
Die Ursache liegt in Creality's `gcode.py`: Ungültige Parameter werfen dort **keinen CommandError**.
Das Programm gibt nur eine Warnung aus und läuft weiter. Beleg im Live-Log `klippy.log.2026-10-12` Z. 119767:
`key171 "Unable to parse … NUM=A"` kommt per `_respond_warning`, danach **TypeError (NoneType)** im Handler,
dann `key60` und SHUTDOWN.
Für minval/maxval wird dasselbe Verhalten angenommen. Das passt zur Beobachtung „minimum of 1“ und trotzdem Absturz.
Daraus ergeben sich zwei Varianten:
- (A, wahrscheinlicher) `get_int` liefert `None`. Dann scheitert `parse_num_to_byte(None)` mit TypeError/AttributeError
  in Zeile ≈1980, **bevor ein Frame rausgeht**.
- (B) `get_int` liefert den Default 0. Dann geht der Frame `[0x08, 0x00]` raus, 0 liegt in keiner der Typlisten,
  und `value` bleibt ungebunden: UnboundLocalError in Zeile 1991.
  B ist für DATA=13 ausgeschlossen, falls 13 durchkäme, denn 13 läge in der int-Liste und würde gar nicht abstürzen.

Prüfen lässt sich das nur lesend über die klippy.log auf dem Drucker: Traceback nach
`Internal error on command:MOTOR_GET` → Zeile 1980 bzw. `parse_num_to_byte` spricht für A, `UnboundLocalError 'value'` für B.
**→ Ergebnis: Variante A belegt** (Traceback: Zeile 1982 → `parse_num_to_byte`, `TypeError` NoneType).
**Folgerung (VERMUTET):** Dieselbe Absturzklasse droht bei NUM=0 oder NUM≥11 ohne EXTRUDER.
Bei EXTRUDER=1 gelten die Grenzen NUM 0..10 und DATA 1..12 (`0x87ab4–0x87bac`).

## 2. Interne Aufrufe DATA=7/8 EXTRUDER=1 und „DATA=1 / _stats_motor_get_value“ — BELEGT
- `MOTOR_GET NUM=1 DATA=1 ` ist **kein interner Aufruf**. Es ist der Anfang des Hilfetexts
  `"MOTOR_GET NUM=1 DATA=1 打印系统所支持变量"` (Strtab-Slot `0x12692c`).
  Die Nähe zu `_stats_motor_get_value` in der strings-Ausgabe ist zufällig.
- `_stats_motor_get_value` ist ein Attribut. `motor_extruder_get` setzt es nach dem Dekodieren
  (`self._stats_motor_get_value = value`, SetAttr-Helper `0x18120` bei `0x88d78` (float) und `0x892d0` (int)).
  Gelesen wird es vom Decorator-Wrapper.
- Kontext ist die **Klipper-Statistik**. `Motor_Control.stats(eventtime)` (`0xa0c04`) läuft mit dem periodischen
  Stats-Timer in die klippy.log und ist aktiv nur bei `self.overcurrent_switch` und `self.motor_params_init` (`0xa0c20`, `0xa0c4c`).
  Ablauf: `_run_script_stats('motor_e_id_fdb', 'MOTOR_GET NUM=1 DATA=7 EXTRUDER=1')` und dasselbe für iq/DATA=8
  (`0xa0c90`, `0xa0d48`). Die inneren Funktionen sind `_run_script_stats.run` (`0x27e7c`: `self.gcode.run_script(script)`)
  und `stats_query_string(name)` (Wrapper `0x1b7f4`: liefert `"name=<_stats_motor_get_value> "`).
  Danach `split('=')`, `float()`, `math.hypot(id, iq)` (`0xa0e2c`).
  Rückgabe: `"motor_control: <id> <iq> overcurrent=<|I|>"`.
- Zweck (VERMUTET, gestützt durch `hypot` und den Schalter `overcurrent_switch`): Logging bzw. Überwachung des
  Extruder-Phasenstrombetrags |I| = √(id²+iq²). **Kein Zusammenhang mit der Cut-Erkennung.**
- Transport bei EXTRUDER=1 (BELEGT): Es läuft **nicht** über den Box-RS485. `data_pack`/`cmd_transparent_send`
  gehen über die Düsen-MCU mit `transparent_send oid=%c write=%*s timeout_ms=%u`
  und `transparent_response …` (`motor_extruder_get` `0x884b0`).

## 3. Weitere Motor-G-Codes (Frame = `Motor_Addr[NUM]` + Länge + `00` + Cmd + Payload + CRC)
| G-Code (Handler) | Cmd | Payload und Parameter | Bewertung / Beleg |
|---|---|---|---|
| MOTOR_FLASH_PARAM (`motor_flash_param` `0xa4e30`, Kern `_motor_flash_param` `0x4b878`) | 0x07 | `[DATA]`, dazu `00` bei DATA∈{3,4}, dazu ID bei DATA∈{5,6}. Bei **DATA=5 (Schreiben)** folgt der Wert, gepackt nach `PARAMS_TYPE`: float→`'f'`, uint16_t→`'<H'`, uint32_t→`'>I'`, int16_t→`'<h'`, sonst `'>I'` (**Big-Endian bei 32 Bit!**). Antwort bei 5: Statusbyte 0 = Erfolg („写入成功/失败“). **DATA=6 = Lesen**: Wert wird nach Typ entpackt („读取:%s value:%s“). Parameter: NUM 1..10, DATA, ID (Default 0), PARAMS (''), PARAMS_TYPE ('float') | Cmd belegt. Payload-Logik vermutet (Vergleiche `0x4bbbc–0x4bd08`, Packs `0x4bd98`/`0x4c954`/`0x4ce10`/`0x4d2c4`/`0x4d7c0`) |
| MOTOR_SYS_PARAM | 0x06 | analog mit ID/PARAMS/PARAMS_TYPE (Hilfetext: `ID=14 PARAMS=1.4 PARAMS_TYPE=float`) | Cmd belegt, Rest nicht im Detail geprüft |
| MOTOR_CONTROL (`motor_control`; EXTRUDER: `motor_extruder_control` `0x8c544`) | 0x05 | `[DATA]`, NUM 1..10, DATA bis 12. Antwort: 3 Statusbytes → „电机控制使能状态 / EN脚电平 / 控制器 / pwm输出“ (高/低, 开启/关闭, 未知) | Cmd belegt. DATA-Bedeutung offen (Hilfe-Beispiel DATA=4, Text „电机控制使能相关指令“) |
| MOTOR_STALL_MODE (`motort_stall_mode` `0xe0d94`) | 0x11 | `[DATA]`, DATA Default 2, Bereich 1..10. **1 = Endschalter-/Blockiersignal, 2 = Notfall-Schutzausgang** (Hilfetext `0x126964`). Wird an mehrere Motoren gesendet (`MOTOR_NUM`). Z/Z1: `set_z_stall_mode` mit Retry (key801). E: `set_e_stall_mode` über transparent (key802) | Cmd und Werte 1/2 belegt (Hilfetext) |
| MOTOR_CLEAR_ERR_WARN_CODE (`motor_clear_err_warn_code` `0xdee3c`) | 0x0c | `[DATA]`: **5 = Fehler- und Warncodes löschen** (Hilfetext), **11 = abfragen** (live `0c 0b` → 8 B). NUM=0 bedeutet alle Motoren: x/y/z/z1 einzeln, Extruder über `motor_extruder_clear_err_warn_code` (`0x53510`) mit festem Payload `b'\x05'` (Tupel `0x1256b0`, Ref `0x5392c`) | belegt |
| MOTOR_CHECK_CUT_POS (`motor_check_cut_pos` `0xe270c`) | 0x11 (stall_mode) | Ablauf: G28 X Y (falls nötig), BED_MESH_CLEAR, `SET_VELOCITY_LIMIT ACCEL=25000`, X-Motor auf stall_mode **4** (`parse_num_to_byte(4)` `0xe31b0`), Fahrt zwischen `check_cut_pos_x_min/max`, Auswertung, Rücksetzen auf **2** (`0xe7ad0`). `cut_pos_x` wird per CXSAVE_CONFIG gespeichert. Fehler: key854 (Filament beim Schneiden), key855 (Cut-Position), key866 (kein Cutter). Hilfe: „通过x电机 stall引脚电流定位切刀位置“ | Cmd belegt. Bedeutung von Modus 4 vermutet (Messmodus für Stallpin/Strom) |
| MOTOR_EXTRUDER_RETRY_PROCESS (`cmd_…` `0xa5be4`) | – | Sendet keine eigenen Frames, reine G-Code-Folge: `SET_STEPPER_ENABLE STEPPER=extruder ENABLE=0`, `MOTOR_CLEAR_ERR_WARN_CODE NUM=0 DATA=5`, M400, Z anheben, BOX_GO_TO_EXTRUDE_POS, `M104 S<max>`, `G0 E0.01 F60`, `G4 P30000`, `G0 E-1`, `G0 E70`, BOX_NOZZLE_CLEAN, Rückfahrt, Beschleunigung wiederherstellen. Auslöser: `extruder_motor_retry` (`0x6a9f8`) bei `auto_retry` | belegt (String-Refs) |

Weitere Tabellen aus dem Modul-Init (belegt):
- `motor_error_dict`: Bit 0..13 → key781..794 (Encoder-Sprung, Encoder-Lesefehler, SW-Spitzen-/Dauerüberstrom,
  HW-Überstrom, Drehzahl, Positionsgrenze, Positionssprung, Schleppfehler, R/L, instabil, Phase fehlt, Phasen-R-Differenz, Sonstige).
- `motor_warning_dict`: Bit 0..2 → key795..797 (Kommunikation, Unterspannung, MCU-Übertemperatur).
- `ERR_CODE_TO_UNION`: Fehlerbit → (Format, Felder) für die Fehlerdetails, z. B. 1→`<ii` lower/upper_encoder_pulse,
  4→`<f` peak_overcurrent_A, 8→`<ff` continuous_overcurrent_A/time_s, 32→speed_over_limit, 64→pos_over_limit_min/max,
  128→pos_cmd_mutation_turns, letztes Element tracking_error (Schlüssel vermutlich 256).
- `MOTOR_NUM_TO_NAME` 1..5 → x, y, z, z1, e.

## 4. Status der Fragen
| Frage | Status |
|---|---|
| 1 Indexnamen | 5–8 belegt. 1–4 und 9–12: keine Namen im Host vorhanden (bounded negativ); Hypothesen siehe oben |
| 1 Typquelle | belegt: hartkodiert {1–10,17,18} float, {11–16} int |
| 1 Absturz 0/13 | **belegt** (kein Lookup-Fehler; get_int-Warnpfad → None → `parse_num_to_byte` TypeError, Traceback 16.10. 13:12:27, kein Frame gesendet) |
| 2 | belegt: Stats-Hook für Extruder-Stromüberwachung. „DATA=1“ ist nur ein Hilfetext |
| 3 | Cmd-Bytes belegt. Payload-Semantik teils vermutet (siehe Tabelle) |

Artefakte: Arbeitsdateien lokal, nicht im Repo (`pyx_strtab.json`, `pyx_strtab_raw.json`,
`pyx_consts.json`, `pyx_tuples.json`, `funcs.json` (Qualname → Adresse), `pcrel.json`, `scripts/`); annotierte
Disassembly (`*.asm`) und Ghidra-Dekompilate nur in der lokalen Arbeitskopie, bewusst nicht in dieser Datei.
