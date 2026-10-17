# SDCARD-GCODEMOVE-BEFUND – Slicer-Spülmatrix, Stromausfall-Fortsetzung, gcode_move (statisch, 17.10.2026)

> Nur statische Analyse. Abgeglichen wurde mit vorhandenen Live-Logs. Es gab keinen Zugriff auf Drucker oder Pi.
> Code in `pi-host/` ist **nicht** geändert. Arbeitsdateien liegen lokal (nicht im Repo).

## Grundlagen

**Quellen (Klartext-Python, FW 1.1.7.0, md5 geprüft)**

| Kürzel | Datei |
|---|---|
| VS | `rootfs-live/usr/share/klipper/klippy/extras/virtual_sdcard.py` (1510 Zeilen) |
| GM | `rootfs-live/usr/share/klipper/klippy/extras/gcode_move.py` (826 Zeilen) |
| PR | `rootfs-live/usr/share/klipper/klippy/extras/pause_resume.py` (Creality) |
| UP-VS / UP-GM | Upstream-Klipper 9cb9975: `klippy/extras/virtual_sdcard.py` / `gcode_move.py` |
| BW | `box_wrapper.cpython-39.so` (Disassembly bzw. Dekompilat wie im FLUSH-BEFUND; `Z.<n>` = `__pyx_lineno`) |
| ST | `config/F008_CR0CN240319C13/gcode_macro.cfg`; `box.cfg` wird ausgeschrieben |

**Belegstärken**

- **B** = im Code belegt (Zeile bzw. Adresse)
- **B+L** = zusätzlich durch ein Live-Log bestätigt
- **V** = Folgerung
- **ANNAHME** = nicht belegt

**Zeilenbezug zum Live-Log:** Die Zeilennummern in VS passen zu den Live-Logs von FW 1.1.7.0. Beispiel: `virtual_sdcard:record_power_loss_info:1169` in `klippy.log.2026-10-12:66530` entspricht VS:1169. Das Log vom 05.10. stammt aus einer älteren FW, dort steht `work_handler:1250` statt VS:1306.

**Anonymisierung:** Die Live-Metadaten enthalten Dateinamen und UUIDs von Druckaufträgen. Beides wird hier **nicht** zitiert, angegeben sind nur Log-Datei und Zeile.

---

## Kurzfassung

| Thema | Ergebnis | Beleg |
|---|---|---|
| Woher kommt die Matrix? | `virtual_sdcard.get_gcode_flush_para()` gibt nur `gcode_metadata["metadata"]["flush_para"]` zurück (VS:984-990). Das Dict stammt aus dem externen Creality-Skript `klippy/extras/metadata.py`. VS ruft es per Subprozess auf (VS:949-961), und zwar nur bei `SDCARD_PRINT_FILE` (VS:565). | B |
| Format | `{'flush_multiplier': <float>, 'flush_volumes_matrix': [<int>, …]}`. Die Matrix ist flach, zeilenweise und hat n² Einträge. Bei Orca 2.4.2 sind es 16×16 = 256 Einträge mit Diagonale 0 und Multiplikator 1.3. Creality Print 7.x liefert `[0]` (n = 1). | B+L |
| Einheit | Werte in **mm³**. Spüllänge = `Wert / 2.4 × flush_multiplier`, **ohne** `nozzle_volume`. Danach folgt die Stock-Nachbearbeitung: gleiches Material min 160, anderes Material ×2, mindestens 370. | B (BW Z.4404, FLUSH-BEFUND §2/§3) |
| Index | `flush_array[i_alt][i_neu]`, Zeile = alt (von), Spalte = neu (nach). Der Index ist die **Slicer-Werkzeugnummer** n aus `T<n>`. Gefunden wird er rückwärts über das feste `Tn_map` `{"T0":"T1A", …, "T15":"T4D"}` und das vom Display gesetzte `Tnn_map` (Slicer-Slot → physischer Slot). Der erste Treffer gilt. | B (BW Z.4387-4403, generate_Tn_map) + L |
| Wann genutzt | Nur bei `print_stats.state == "printing"` und `last_tnn` ≠ None (BW Z.4429). Fehlt die Matrix, ist sie nicht quadratisch, liegt der Index ≥ n oder wird er nicht gefunden, folgt eine Warnung, `None` und dann der **Farbweg** (FLUSH-BEFUND §2). | B |
| Stromausfall | EEPROM 24C16 am Host-I²C (`[bl24c16f]`, i2c.1 am `rpi`-MCU): 8-Byte-Ringslots mit `file_position` (int) und E-Basis (float). Dazu kommt die JSON-Datei `print_file_name.json`. Beim Fortsetzen sucht die FW in der G-Code-Datei **rückwärts** nach X/Y/Z/E und dem letzten `T<n>`. Danach folgen `G28 X Y`, `ZDOWN`, CFS-Wechsel per `T<n>` (mit Spülen), eine Schrägfahrt zum Punkt und der Weiterdruck. | B |
| gcode_move | `RESTORE_GCODE_STATE` (auch `MOVE=1`) ist **inhaltsgleich mit Upstream**: eine gerade Fahrt X/Y/Z zugleich. Damit ist die ANNAHME aus NOTIZ §4.2/§6a.8 **bestätigt**. Neu bei Creality: `SET_LIMITS`/`RESTORE_LIMITS`/`SET_Z_LIMIT`/`RESTORE_Z_LIMIT`/`RESET_HOME_AXES_XY`/`SET_POSITION`/`SAVE_ENDPRINT`, eine Z-Klemmung auf −2 außerhalb des Drucks und `CX_RESTORE_GCODE_STATE` (Stromausfall). | B |
| `SET_LIMITS` | **Keine Beschleunigung.** Der Befehl setzt die X/Y-Kinematikgrenzen auf `position_min/max` aus der Config (GM:96-101). Registriert wird er in GM:61, **nicht** in `box_wrapper.so`. `box_wrapper` ruft ihn in `set_flushing_sign` auf, `RESTORE_LIMITS` in `reset_flushing_sign`. | B |

---

## A. Slicer-Spülmatrix

### A.1 Laden der Metadaten (VS)

1. **Druckstart** `SDCARD_PRINT_FILE` (VS:539-567):
   - `_load_file` (VS:564)
   - `load_gcode_metadata(current_file.name)` (VS:565)
   - `record_print_history` (VS:566)
   - `do_resume` (VS:567)
2. **Laden** `load_gcode_metadata` → `get_print_file_metadata(file_path)` (VS:599-601, 949-961):
   - `check_output("<klippy-env-python> …/klippy/extras/metadata.py -f '<datei>' -p <verzeichnis>")`, danach `json.loads`.
   - Bei einem Fehler bleibt `{}` stehen und es wird nur geloggt.
   - Der Aufruf **blockiert** im Klippy-Thread (V).
3. **Auslesen** `get_gcode_flush_para()` (VS:984-990): `self.gcode_metadata.get("metadata").get("flush_para", None)`.
   - Bei einer Exception folgt ein Log und `None`.
4. **Diagnose** `SHOW_GCODE_FLUSH FILENAME=<datei>` (VS:568-583):
   - Lädt die Metadaten einer beliebigen Datei.
   - Antwort `"shwo gcode flush para: …"` (Tippfehler im Original).
   - Nur erlaubt, wenn nicht gedruckt wird (`SD busy`).
   - Nebenwirkung: Der Befehl **überschreibt** `gcode_metadata` (V).
5. **Lebensdauer (V):** `gcode_metadata` wird **nie zurückgesetzt** (nicht in `_reset_file`/`do_cancel`).
   - Wer über `M23`/`M24` statt `SDCARD_PRINT_FILE` startet, behält die Matrix des **vorigen** Auftrags.
   - Im Stock-Ablauf (Display/App → `SDCARD_PRINT_FILE`) spielt das keine Rolle.
6. **`metadata.py` liegt im Projekt nicht vor.**
   - **ANNAHME:** eine Ableitung von Moonrakers `metadata.py` mit Creality-Zusätzen (`flush_para`, `model_info`).
   - Die Zuordnung Header-Zeile → Feld ist daher abgeleitet, nicht gelesen.
   - **ANNAHME** zu den Quellzeilen: Orca schreibt `; flush_volumes_matrix = 0,569,…` und `; flush_multiplier = 1.3` in den CONFIG_BLOCK am Dateiende.

### A.2 Felder aus echten Druckaufträgen (B+L)

Grundlage sind die `gcode_metadata:`-Zeilen der Live-Logs. Für die Matrix-Einträge gibt es eine Rechenprobe; sie liegt lokal.

| Log:Zeile | Slicer | `multicolor_method` | `flush_multiplier` | Matrix |
|---|---|---|---|---|
| `klippy.log.2026-10-05:20008` | OrcaSlicer 2.4.2 | 0 | 1.3 | 256 Werte = 16×16, Diagonale 0, außerhalb 332…1070 |
| `klippy.log.2026-10-12:5277` | OrcaSlicer 2.4.2 | 0 | 1.3 | 16×16, außerhalb 316…1054 |
| `klippy.log.2026-10-12:65795` | Creality Print 7.2.2 | 1 | 1.0 | `[0]` |
| `klippy.log.2026-10-12:79939` | Creality Print 7.3.0 | 1 | 1.3 | `[0]` |

**Weitere Metadaten-Schlüssel**
- **Orca:** `filament_name`, `filament_type` (16 Einträge, `;`-getrennt), `filament_total`, `filament_weight_total`, `first_layer_*`, `layer_count`, `model_info{MINX…MAXY, MaterialType, MaterialName, multicolor_method}`
- **Creality Print:** `default_filament_colour` (Liste), `filament_used_g`, `model_info{…, MINZ/MAXZ, multicolor_method: 1}`
- **Farbe pro Filament:** liefert die Orca-Datei in den Metadaten **nicht**. `filament_colour` fehlt also (B+L).

**Matrix**
- Die Matrix ist **nicht symmetrisch**: 116 von 120 Paaren unterscheiden sich, z. B. [0][1] = 569 und [1][0] = 419. Die Richtung ist also wichtig.
- Beispiel mit Multiplikator 1.3:

| Wechsel | Volumen | Länge = Volumen / 2.4 × 1.3 |
|---|---|---|
| T0→T1 | 569 mm³ | 308.2 mm |
| T1→T0 | 419 mm³ | 227.0 mm |

- Zum Vergleich der Farbweg mit live gemessenen 101…229 mm (FLUSH-BEFUND §2). Orca spült damit merklich mehr (V).

**`multicolor_method`**
- 0 = Orca-Stil: Die Datei enthält `T<n>` (die Wechsel macht `box_wrapper`).
- 1 = Creality-Print-Stil: `T<n>`-Zeilen werden **übersprungen** (VS:1276-1282, 1449), die Datei steuert die Wechsel per `M8200 …`-Kette.
- Ist die CFS deaktiviert (`Tn_data["enable"] == 0`) und `multicolor_method` ≠ 0, überspringt VS die Blöcke von `M8200 P` bis `M8200 O` (VS:1284-1302, externes Filament-Rack).

### A.3 Verwendung in `box_wrapper` (B, Querverweis FLUSH-BEFUND §2/§3)

`MCMBW.get_flush_len(last_tnn, current_tnn)` (BW Z.4423-4491):
- Nur wenn `print_stats.state == "printing"` **und** `last_tnn is not None` (Z.4429): `flush_len = get_flush_length_from_gcode(last_tnn, current_tnn)`.
- Ist das Ergebnis nicht `None`, folgt dieselbe Nachbearbeitung wie im Farbweg (×2 bzw. min 370 bei anderem Material, sonst min 160).
- Bei `None` gilt der Farbweg.
- **Folge (V):** Im Zustand **paused** wird die Matrix nicht benutzt. Das betrifft Wechsel im Fehler- oder Resume-Pfad und manuelle Wechsel während der Pause.

`get_flush_length_from_gcode` (BW @0xb47d0, Z.4357-4406):
- Die bisher offene Index-Zuordnung ist jetzt aus dem Dekompilat aufgelöst.
- Herangezogen wurden LINENO 0x1123-0x1125, `GetSlice(key, 1)`, `PyNumber_Long` und `RichCompare` mit `Py_EQ`/`Py_GE`.

```
4360 flush_para = printer.lookup_object('virtual_sdcard').get_gcode_flush_para()
4362   Fehler -> warning "Error in getting flushing parameters" -> None
4367 flush_multiplier = flush_para['flush_multiplier'];  m = flush_para['flush_volumes_matrix']
4370   m leer -> warning "gcode has no flush parameters" -> None
4371   sqrt(len(m)) nicht ganzzahlig -> warning "flushing parameter format is abnormal" -> None
4374 material_quantity = n;  4375/76 flush_array = np.array(m).reshape(n, n)
4387 for key, value in box_state.Tn_map.items():            # "T0".."T15" -> "T1A".."T4D"
4388     if last_tnn == box_state.Tnn_map[value]:            # Tnn_map: Slicer-Slot -> physischer Slot
4389         last_tnn = int(key[1:]); break                   # "T5" -> 5
4391 if not isinstance(last_tnn, int) or last_tnn >= n:
4392     warning "The box address does not comply with the rules, last_tnn: %s" -> None
4395-4400  dasselbe für current_tnn
4403 flush_volume = flush_array[last_tnn][current_tnn]        # [alt][neu]
4404 flush_len = box_action.material_volume_to_length(flush_volume) * flush_multiplier   # /2.4
4405 gcode.respond_info("flush_volume: %f, flush_len: %f")
```

**`Tn_map` (B)** ist fest. `BoxState.generate_Tn_map` (BW @0x1b4f70, Quellzeilen 605-612) bildet:
- die Liste `"T" + a + b` für a in `('1','2','3','4')` (Tupel @0x2433b0) und b in `('A','B','C','D')` (Tupel @0x24339c),
- dann für i in `range(16)` (`cmp r9,#16` @0x1b519c): `Tn_map["T"+str(i)] = liste[i]`.

Ergebnis: T0→T1A, T1→T1B, …, T4→T2A, …, T15→T4D. Das stimmt mit `tnn_from_index` in `k2_box.py:394-406` und `box.cfg` `M8200 L` überein. `PyObject_SetItem` auf `Tn_map` gibt es nur dort; die Liste der Leser stammt aus den String-Xrefs.

**`Tnn_map` (B+L)** wird vom Display-Client unmittelbar vor dem Druck gesetzt (`part: tnn_map`):
- Belege: `klippy.log.2026-10-05:16509`, `2026-10-12:54353/62701/76915`.
- Beispiel: `'T1B': 'T1A', 'T1C': 'T1A', 'T1D': 'T1A'` bedeutet, dass die Slicer-Filamente 1-3 aus dem physischen Slot T1A gedruckt werden.
- Eine weitere Live-Variante: `'T1A': 'T1B'`.

**Folgen der Rückwärtssuche (V)**

| Fall | Wirkung |
|---|---|
| Mehrere Slicer-Filamente auf demselben physischen Slot | Es gilt der **kleinste** Index. Zeile/Spalte gehören dann evtl. zu einem anderen Slicer-Filament als dem gerade gedruckten. |
| Slicer-Projekt mit n < 16 Filamenten | Jeder Slot, dessen Index ≥ n ist, fällt auf den Farbweg zurück. |
| Creality-Print-Dateien (`[0]`, n = 1) | Wechsel von/nach Index > 0 → Farbweg. Nur Index 0 → 0 ergäbe Volumen 0 → `flush_len` 0 → Nachbearbeitung min 160. |
| `flush_multiplier` | Nach FLUSH-BEFUND §3 (Z.4362/4363) wird `None` als 1 behandelt. Fehlt der Schlüssel ganz, wäre der Indexzugriff eine Exception (V). |

**Stock-`M8200 F S[speed] L[length]`** (`box.cfg`:191-196) ignoriert S und L und ruft nur `CR_BOX_FLUSH TNN=<M8200.tnn>` auf. Eine Spüllänge aus der Datei wird im Stock **nicht** über `M8200 F` übernommen (B).

### A.4 Verhalten ohne Matrix

| Fall | Meldung (Log-Warnung) | Weiter mit |
|---|---|---|
| `gcode_metadata` leer/kaputt, `metadata.py` fehlgeschlagen | `Error in getting flushing parameters` | Farbweg |
| Matrix leer | `gcode has no flush parameters` | Farbweg |
| Länge keine Quadratzahl | `flushing parameter format is abnormal` | Farbweg |
| Slot nicht in `Tnn_map`-Werten oder Index ≥ n | `The box address does not comply with the rules, …` | Farbweg |
| nicht `printing` oder `last_tnn` None | (`Try to get the resize length…` erscheint nicht) | Farbweg bzw. `Tn_extrude` 140 roh |

In den vorhandenen Live-Logs kommt keine dieser Zeilen vor. Auch `flush; last_tnn` oder `flush_para:` fehlen. Der Matrix-Pfad ist **live nicht belegt**; die vorhandenen Orca-Drucke waren einfarbig bzw. ohne Wechsel (V).

---

## B. Stromausfall-Fortsetzung (Power-Loss) in VS/GM/PR

### B.1 Was gespeichert wird (B)

| Ablage | Inhalt | Schreiber |
|---|---|---|
| **EEPROM** `[bl24c16f]` (`printer.cfg`:37-40: `i2c_mcu: rpi`, `i2c.1`), 2 KB | Byte 0 = aktueller Slot `pos` (1…255); Byte 1 = Gültig-Flag (1 = Datensatz aktiv, 255 = gelöscht); Slot `pos*8`: `INT file_position`, `pos*8+4`: `FLOAT base_position_e` (gerundet auf 2 Stellen) | `record_power_loss_info` VS:1153-1205 |
| `creality/userdata/config/print_file_name.json` | `file_path`, `absolute_coord`, `absolute_extrude`, `fan_state` (letzte `M106 S`/`M106 P0/P1/P2`-Zeilen), `M204`, `filament_used`, `last_print_duration`, `SET_GCODE_OFFSET` (−5 = keiner), `pressure_advance`, `max_accel`, `requested_accel_to_decel`, `square_corner_velocity`, `variable_z_safe_pause` | `gcode_move.recordPrintFileName` GM:422-479 (aufgerufen VS:1200/1204/1327), PR:206-213 (setzt `variable_z_safe_pause` = 0 nach RESUME) |
| `…/gcode_layer.json` | `{"layer": n}` je Schichtwechsel (Schlüssel `LAYER_KEYS` VS:10) | VS:905-911, 1232-1248 |
| `gcode.last_temperature_info` / `gcode.exclude_object_info` | Bett/Düse/Kammer; `EXCLUDE_OBJECT_DEFINE`/`EXCLUDE_OBJECT`-Zeilen | Creality-`gcode.py` (**liegt nicht vor**, ANNAHME) |
| `user_print_refer.json` | Schalter `power_loss.switch`, `delay_image.*` | Display/App (V) |

**Herkunft weiterer Felder**
- `M204`, `SET_GCODE_OFFSET` und `pressure_advance` werden in GM:435-439 nur **übernommen**.
- Wer sie schreibt, ist offen (ANNAHME: Creality-`gcode.py`).

**Schreibtakt (VS:1153-1205)**
- **Freigabe:** Nur mit `power_loss_switch` und `bl24c16f`, nur wenn `layer > 6` oder (`count_G1 > 18` und Z > 1.0) gilt, und nur bei `file_position > 0`.
- **Wann geschrieben wird:**
  - je **neuer Schicht** ab Schicht 3 bei Z > 1.0,
  - ohne Schichtmarken alle > 5 s bei Z > 1.0.
- **Verschleiß:** Gleicher Slot bis 255 Schreibvorgänge, dann `pos+1` (Ring 1…255).
- **Kopplung mit der CFS (B):**
  - Gilt `box.box_state.need_update_power_loss_info == False`, wird **nicht** geschrieben (VS:1163-1165).
  - Damit landet keine Position mitten im Wechsel im EEPROM; das Attribut steht im BW-Stringpool.
- **JSON:** einmal bei `count_G1 == 19`, danach alle > 15 s ab Schicht 6 oder Z > 3 (VS:1199-1204).
- **Löschen** (Flag 255 + JSON weg):
  - `END_PRINT` im Dateistrom (VS:1220-1227)
  - Dateiende (VS:1394-1399)
  - neuer Druck (`rm_power_loss_info` VS:665-680)
  - `CLEAR_EEPROM_INFO` (aus `CANCEL_PRINT`, ST; VS:498-517)
  - Webhook `pause_resume/cancel_continue_print` (PR)

### B.2 Ablauf beim Fortsetzen (B)

1. **Abfrage:** Display/App fragt `pause_resume/check_continue_print_state` (PR:77-117). Sind JSON und EEPROM gültig und ist `print_stats` = standby, gilt `print_stats.power_loss = 1`.
2. **Start:** `SDCARD_PRINT_FILE FILENAME=… ISCONTINUEPRINT=1` (VS:553-556) setzt `box.box_state.is_continue_print`. Kein `forced_leveling` (VS:544), `rm_power_loss_info` bleibt aus.
3. **`work_handler` → `get_restore_info`** (VS:1096-1151). Bedingungen: Schalter, `is_continue_print`, **nicht** `do_resume_status`, gleicher Dateipfad (VS:1056-1069) und gültiges EEPROM.
   - Dann folgen `note_start(info_path)`, das EEPROM lesen, `file_position` setzen und die Schicht aus `gcode_layer.json` übernehmen.
   - Geheizt wird mit **`M140`, `M109`, ggf. `M141`** auf die letzten Temperaturen.
   - `getXYZET` sucht ab `file_position` **rückwärts** zeilenweise (VS:748-774, 800-886):
     - letztes `E`, letztes `X`+`Y`, letztes `Z` aus G0/G1;
     - bei Mehrfarbdateien das letzte `T<n>` (Blöcke à 512 KB rückwärts);
     - bei `multicolor_method` 1 zusätzlich das letzte `M8200 L I<n>` → `"T<n>"` (VS:850-868).
   - Bei einfarbigen Dateien ist `T` = `"T0"` (VS:875).
   - Ergibt sich Z == 0, wird **abgebrochen** und das EEPROM gelöscht (VS:1122-1143).
4. **Timer `restore_print`** (VS:1071-1094) → `gcode_move.cmd_CX_RESTORE_GCODE_STATE` (GM:489-727, unter dem gcode-Mutex):
   1. **Zustand setzen:**
      - Basis `[0,0,0,base_e]`, `homing_position = 0` (der Z-Offset ist damit zunächst weg), Ziel `[X,Y,Z,E+base_e]`.
      - E-Basis `+= last_E − Ziel_E − 0.7 + 5.0`: Der nächste Extrusionsbefehl fördert **4,3 mm mehr** (GM:554-555).
   2. **Fahren:**
      - Lüfterzeilen ausführen.
      - `G28 X Y`.
      - Mit `[z_align]` (K2: ja, `printer.cfg`:386): `BED_MESH_CLEAR`, **`ZDOWN`**, `BED_MESH_PROFILE LOAD="default"`, dann `set_position(Z = gemessen + offset_value)` mit Z als homed (GM:583-618).
      - Ohne `[z_align]`: Z = gespeichertes Z + `variable_z_safe_g28` (3.0) + `variable_z_safe_pause` + `offset_value` (GM:576-578, 619-623).
   3. **CFS (B):**
      - zuerst immer `BOX_POWER_LOSS_RESTORE` (GM:636).
      - CFS **aus** (`enable` 0): `box.flush_material()` (Endspülen mit Restmaterial) und Kopfsensor prüfen. Bei leerem Sensor kommt der Runout-Handler und es geht nicht weiter (GM:637-644, 480-487).
      - CFS **an** + Orca-Datei: `M400`, **`T<n>`** = vollständiger CFS-Wechsel inkl. Spülen, `M400` (GM:646-652). BW-String `is_continue_print, last_tnn[%s] is the same with tnn[%s]` → **V:** Bei gleichem Slot gibt es eine Sonderbehandlung.
      - CFS **an** + Creality-Print-Datei: `M8200 L I<n>` wird als `T<n>` ausgeführt, dann `G1 X Y F3000` (GM:653-662).
   4. **Zurück zum Druckpunkt:**
      - `move_with_transform(Ziel X/Y/Z)` als **eine Schrägfahrt** mit `speed` 25 mm/s.
      - dann `G1 X Y F3000`, `M400`, `SET_Z_LIMIT`, `M204`, `absolute_extrude`, `EXCLUDE_OBJECT*`-Zeilen.
      - `SET_GCODE_OFFSET Z_ADJUST=<−gespeichert> MOVE=0` + `Z_OFFSET_APPLY_PROBE`, Pressure-Advance-Zeile, Beschleunigungswerte (GM:666-725).
   5. **Abschluss:**
      - danach `extruder_fan` an.
      - `M220 S20` „langsam bis zur nächsten Schicht“ **nur**, wenn `layer > 1` und `T == ""` (VS:1085-1090).
      - Weil einfarbige Dateien `"T0"` liefern, greift das praktisch nie. Das sieht nach einem **Creality-Fehler oder vertauschter Bedingung** aus (V).

### B.3 Zusammenspiel mit pause_resume und Box (B)

- **RESUME:** `PR.send_resume_command` setzt `v_sd.do_resume_status = True` (PR:184-187). `get_restore_info` überspringt damit den Wiederherstellungsweg (VS:1099), und `work_handler` setzt das Flag am Ende zurück (VS:1491).
- **Jeder Start von `work_handler`** (Druckstart **und jedes RESUME**):
  - `CLEAR_PAUSE` (VS:1307),
  - **`G90`** über den `else`-Zweig von `get_restore_info` (VS:1147-1150).
  - Upstream erzwingt nach RESUME **kein** G90 (UP-VS:240-249). Stock hebt damit nach RESUME einen bei der Pause aktiven G91-Modus auf (V: relevant nur, wenn mitten in einer relativen Sequenz pausiert wurde).
- **Weitere Pausenquellen im Dateistrom (VS):**
  - `M600` → `PAUSE` (VS:1228-1229), **nur** bei eingeschaltetem Power-Loss-Schalter (VS:1208).
  - Z-Ziel > `max_z + 5` im schnellen G1-Pfad → Fehler `587` + Flag → `PAUSE` (GM:262-272, VS:1450-1453; nur wenn `pause_resume.pause_start` False).
- **`pause_start`:** VS liest es nur an dieser Stelle (VS:1450). Ergänzt PAUSE-RESUME-BEFUND D.2.
- **PAUSE-Höhe:** `variable_z_safe_pause` in der JSON wird nach RESUME auf 0 gesetzt (PR:206-213). Wer es bei PAUSE setzt, ist offen (ANNAHME: Creality-`gcode.py`/Display).

---

## C. gcode_move: Creality gegen Upstream 9cb9975

| Punkt | Creality (GM) | Upstream (UP-GM) | Bedeutung für Pi |
|---|---|---|---|
| `RESTORE_GCODE_STATE` | GM:742-762: Modus, Basis, Homing-Position, Geschwindigkeiten; `e_diff` auf E-Basis; `MOVE=1` → `last_position[:3] = state` → **ein** `move_with_transform` | UP-GM:240-261 inhaltsgleich (nur `allow_absolute_extrude`, Basis `[:4]`, Fehlertext ohne JSON `key274`) | **Bestätigt** NOTIZ §4.2/§6a.8 und PAUSE-RESUME-BEFUND D.1: Stock-`RESUME_BASE` fährt **gerade und schräg** (X/Y/Z zugleich) mit `MOVE_SPEED` (Stock immer 50). Die ANNAHME „wie Upstream“ kann weg. |
| `SAVE_GCODE_STATE` | GM:730-740 | UP-GM:228-238 | gleich (Schlüssel `absolute_extrude` statt `allow_absolute_extrude`) |
| `SET_GCODE_OFFSET`, `G92`, `M221`, Homing-Ende | GM:403-421, 346-355, 381-387, 127-130 | UP-GM:208-226, 181-190, 200-206, 79-82 | Z-Offset-Logik gleich |
| `M220`/`M221` | `SAVE=1` schreibt `speed_mode.json` / `flow_rate.json` (GM:365-401) | – | entfällt |
| `G1` | nur XYZ/E (keine Zusatzachsen). **Ausnahme:** Bei Z und `print_stats.state != "printing"` wird mit gehomtem Z ein Ziel < −2 **auf −2 geklemmt**. Das gilt auch im Zustand **paused** (GM:301-308). Fehlertexte als JSON | allgemeine `axis_map`, ohne Klemmung (UP-GM:134-161) | Pi ohne Klemmung; `homing.cfg`/Makros fahren nicht unter −2 (V) |
| `simple_cmd_G1` | schneller Parser, Z-Grenze `limits[2][1] + 5` → Fehler 587 und Pause (GM:234-286); Aufrufer = Creality-`gcode.py` (ANNAHME) | – | entfällt |
| `SET_LIMITS` | `kin.set_limits(x_min, x_max, y_min, y_max)` aus `[stepper_x/y] position_min/max` (Stock: X −10…352.5, Y −6.2…400) (GM:96-101) | – (Upstream setzt die Grenzen beim Homing auf genau diese Werte) | s. C.1 |
| `RESTORE_LIMITS` | `kin.restore_limits()` (GM:103-104), Creality-`corexy` **liegt nicht vor** | – | Wirkung ANNAHME |
| `RESET_HOME_AXES_XY` | `set_limits(1,-1,1,-1)` (GM:66-71) → nach Upstream-Konvention X/Y „nicht gehomt“ (V) | – | – |
| `SET_Z_LIMIT` / `RESTORE_Z_LIMIT` | Z-max = `position_max − z_align.zmax_safe_pox_diff + |fade_target| (≤ 5) + 0.2` bzw. wieder `position_max` (GM:72-94), nur im Stromausfall-Pfad genutzt (GM:676) | – | entfällt |
| `SET_POSITION X= Y= Z= E=` | `toolhead.set_position(..., homing_axes=(2,))` → Z gilt als gehomt (GM:797-813) | – | entfällt |
| `SAVE_ENDPRINT` | `M400`, `auto_protect_coordinate_system` (bei G-Code-Basis ≠ 0: 5 mm hoch, **relativ zu physisch X0/Y0**, `G92`, Z zurück), `TURN_OFF_HEATERS`, `END_PRINT` (GM:166-232, 815-823); in der Stock-Config nicht referenziert | – | entfällt |
| Stromausfall | `recordPrintFileName`, `CX_RESTORE_GCODE_STATE`, `power_loss_sensor_detected` (GM:422-727) | – | §B |
| Upstream-Neuerungen | – | Zusatzachsen (`_update_extra_axes`), `klippy:analyze_shutdown` | – |

### C.1 `SET_LIMITS` und `RESTORE_LIMITS` – Korrektur bisheriger Annahmen

**B**
- `SET_LIMITS` wird in GM:61 registriert und setzt **Positionsgrenzen** X/Y (GM:96-101).
- Mit Beschleunigung hat der Befehl nichts zu tun.
- Aufrufer im Stock:

| Aufrufer | Stelle |
|---|---|
| `RESUME_EXTERNAL` am Ende | ST:939 |
| `CALIBRATE_CUT_POS` | ST:795/800 |
| `END_PRINT` (`RESTORE_LIMITS`) | ST:632 |
| BW `BoxAction.set_flushing_sign` | ~~Z.1278 `M400`, Z.1279 `SET_LIMITS`~~ – **korr. 17.10.2026:** Z.1277 `M400`, Z.1278 `SET_LIMITS`, Z.1279 `M400` |
| BW `BoxAction.reset_flushing_sign` | ~~Z.1283 `M400`, Z.1284 `RESTORE_LIMITS`~~ – **korr. 17.10.2026:** Z.1282 `M400`, Z.1283 `RESTORE_LIMITS`, Z.1284 `M400` |

Die BW-Stellen sind String-Xrefs im Disassembly. Weitere Strings finden sich in `prtouch_v3_wrapper.so`.

~~Damit ist der offene Punkt „`set_flushing_sign`-Rumpf“ aus FLUSH-BEFUND §9 teilweise geklärt. Der Rumpf besteht aus `M400` + `SET_LIMITS`, dazu kommt die Datei `flushing_sign` (`has_flushing_sign` Z.1270/1271: `os.path.exists`/`os.remove`).~~

> **Korrektur 17.10.2026 (B, FLUSH-SONDERFAELLE-BEFUND-2026-10-17.md §1.1/§1.3/§7 Nr. 2):** Der Rumpf ist damit
> **vollständig** geklärt. `set_flushing_sign` = genau drei Befehle `M400` (Z.1277) + `SET_LIMITS` (Z.1278) +
> `M400` (Z.1279); `reset_flushing_sign` analog `M400` (Z.1282) + `RESTORE_LIMITS` (Z.1283) + `M400` (Z.1284)
> (Fehlerpfad-Zeilen `movw r7,#0x4fd–0x4ff` bzw. `#0x502–0x504`). **Keine Datei:** Beide schreiben/löschen nichts;
> die Datei `flushing_sign` taucht nur in `__init__` (Pfad-Attribut) und in `has_flushing_sign` (Z.1270/1271:
> `os.path.exists`/`os.remove`) auf, und `has_flushing_sign` hat im Modul keinen Aufrufer.

**V:** Stock hält im Druck engere X/Y-Grenzen und weitet sie für Schacht/Schneiden auf den Config-Bereich (Y bis 400).

**ANNAHME:** Die engeren Werte setzt die Creality-Kinematik beim Homing bzw. mit `restore_limits`. Die Datei liegt nicht vor.

**Pi:** `pi-host/config-pi/printer.cfg`:158-173 hat dieselben Bereiche (X −10…352.5, Y −6.2…400). Upstream setzt sie beim Homing. Der Pi steht damit **dauerhaft im „SET_LIMITS-Zustand“** des Stock.
- Für RESUME/Spülen fehlt nichts.
- Eine Schutzwirkung engerer Grenzen im Druck hat der Pi nicht (ANNAHME, solange die Creality-Kinematik unbekannt ist).

**Zu korrigieren (nur Text, nicht geändert)**
- `pi-host/config-pi/NOTIZ-cutcal.md`:13: „`SET_LIMITS`/`RESTORE_LIMITS` stecken in `box_wrapper.so`“ → richtig ist `gcode_move.py` GM:61-62.
- `pi-host/config-pi/NOTIZ-cutcal.md`:56: „Beschleunigung … (≙ SET_LIMITS)“ → `SET_LIMITS` sind Positionsgrenzen.
- `pi-host/config-pi/NOTIZ-cutcal.md`:153: „Wirkung von `SET_LIMITS` unbelegt“ → belegt.
- PAUSE-RESUME-BEFUND Tabelle A.4 („`SET_LIMITS` … Befehl aus `box_wrapper.so`, Wirkung unbelegt“) und D.3 → wie oben.

### C.2 Bedeutung für die RESUME-Rückfahrt (NOTIZ §6a.8)

- Das Stock-Ziel X225/Y345 auf Druck-Z per **gerader Schrägfahrt** ist jetzt **vollständig belegt**: PR:200-203 → GM:759-762.
- Die Pi-Lösung bleibt die konservativere (X/Y in Pausenhöhe, Z senkrecht):
  - `_PI_RESUME_RETURN` → `RESUME_BASE` mit Nullfahrt,
  - `RESTORE_GCODE_STATE` stellt E-Basis und Modus wie Stock her.
- Zwei Stock-Eigenheiten übernimmt der Pi bewusst **nicht**: das erzwungene `G90` nach jedem RESUME (§B.3) und die Z-Klemmung auf −2 in der Pause. Beides ist kein Nachteil (V).

---

## D. Folgen und Vorschläge für pi-host (keine Code-Änderung)

### D.1 Slicer-Matrix in `k2_box` nutzen – ohne Fork

**Quelle: eigenes Parsen in `k2_box` (empfohlen)**
- Upstream-`virtual_sdcard.get_status()` liefert `file_path` (UP-VS:104-111), Upstream sendet `virtual_sdcard:reset_file` (UP-VS:151).
- **Laden:** beim ersten Spülen im Zustand `printing`
  - Pfad aus `virtual_sdcard.file_path()` holen, Datei-**Ende** lesen (z. B. letzte 512 KB) und Kopf (erste 64 KB).
  - Gesucht werden `; flush_volumes_matrix = …` und `; flush_multiplier = …`. Die Zeilenform ist **ANNAHME** (Orca-CONFIG_BLOCK) und am ersten echten Auftrag zu prüfen.
- **Cache:** Schlüssel (Pfad, Größe, mtime); leeren bei `virtual_sdcard:reset_file`.
- **Blockieren:** Lesen im Klippy-Thread blockiert kurz (V: wenige ms bei 512 KB). Alternative: beim Druckstart in einem Thread vorparsen.
- **Prüfung wie Stock:**
  - Länge muss eine Quadratzahl sein, sonst Farbweg mit Meldung.
  - Multiplikator fehlt → 1.

**Moonraker (ANNAHME, nicht geprüft)**
- Upstream-Moonraker-Metadaten enthalten nach unserem Kenntnisstand **keine** `flush_volumes_matrix`. Die Creality-Felder kommen aus deren eigener `metadata.py`.
- Ein Klipper-Modul kann Moonraker zudem nicht sauber abfragen.
- Deshalb nicht empfohlen.

**Slicer-seitig (Alternative, ANNAHME)**
- Orca-Wechsel-G-Code-Platzhalter (`{flush_length}`, `{previous_extruder}`, `{next_extruder}`) könnten die Länge direkt übergeben, z. B. `_KTWO_TOOL_CHANGE T={next_extruder} FLUSH_MM={flush_length}`.
- Dafür braucht es ein eigenes Druckerprofil. Die Platzhalter sind im Projekt nicht belegt.

**Index-Zuordnung auf dem Pi**
- Es gibt kein `Tnn_map` und keine App-Zuordnung; `T<n>` → TNN ist fest (`tnn_from_index`). Der Index ist also direkt `n = (box−1)·4 + "ABCD".index(slot)`.
- **Empfehlung:** Die **Werkzeugnummer** durchreichen, statt sie wie Stock rückwärts zu suchen. `_KTWO_TOOL_CHANGE` kennt `i`, und `M8200 L I<n>` liefert `I`. Beispiel: `KTWO_BOX_FLUSH TNN=… TOOL=<n>`.
- Merken: `remain_material["tool"]` neben `tnn`.
- Da nur `box_addr: 1` freigegeben ist, braucht eine Matrix nur n ≥ 4.

**Regeln wie Stock**
- Nur im Zustand `printing` und nur mit bekanntem alten Werkzeug.
- Länge = `M[alt][neu] / 2.4 × flush_multiplier`, **ohne** `nozzle_volume`.
- Danach `finish_flush_len` (anders ×2/min 370, sonst min 160).
- Rechenkern vorhanden: `nachbau/flush.py` `gcode_matrix_flush_len`. Er entspricht Z.4371-4404 einschließlich Rand-/Indexprüfung.

**Optionen und Meldungen**
- Neuer `flush_mode`-Wert z. B. `matrix` (Matrix, sonst Farbe) neben `color`/`fixed`.
- Default `color` lassen, bis der erste Orca-Mehrfarbdruck am Gerät geprüft ist.
- Im `FLUSH-Plan` die Quelle nennen („Slicer-Matrix [i][j] = V mm³ × m“), damit der Unterschied zum Farbweg (Orca spült ~1,5-2× mehr) sichtbar ist.

**Pausen-Fall bewusst festlegen**
- Stock nutzt die Matrix in der Pause **nicht** (Z.4429). Für Pi-Nachholen (`resume_redo_change`) und Runout reicht der Farbweg; das entspricht Stock.

### D.2 Stromausfall

- **Unverändert entfallen** lassen (`pi-host/config-pi/README.md`:31). Der EEPROM hängt am Creality-Host-I²C, und der Ablauf setzt `ZDOWN`, Creality-`gcode.py`-Dateien und die Display-App voraus.
- **Falls später gewünscht (V, niedrige Priorität):**
  - Upstream-Bausteine `[save_variables]` (Dateiposition, E-Basis, Werkzeugnummer, Temperaturen je Schicht) und `M23`/`M26 S<pos>`/`M24` (UP-VS:180-224).
  - Dazu eine eigene Rückkehrsequenz (Heizen, `G28 X Y`, Z ohne Homing aus gespeicherter Höhe, CFS-Wechsel auf das gespeicherte Werkzeug, Spülen, Fahrt X/Y in Höhe, Z senkrecht).
- **Lehren aus Stock:**
  - **Rückwärtssuche** nach X/Y/Z/E/T in der Datei statt Positionsspeicher.
  - **Kein Speichern während des CFS-Wechsels** (`need_update_power_loss_info`).
  - **+4,3 mm** Vorextrusion über die E-Basis.
  - Die Schrägfahrt zum Punkt würde der Pi wie beim RESUME senkrecht aufteilen.

### D.3 Kleinere Punkte

- **Doku-Korrekturen** wie in C.1 (NOTIZ-cutcal, PAUSE-RESUME-BEFUND A.4/D.3) und FLUSH-BEFUND §9:
  - „Slicer-Index-Zuordnung“ → aufgelöst (A.3).
  - „`set_flushing_sign`-Rumpf“ → `M400` + `SET_LIMITS` (+ Datei `flushing_sign`).
  - NOTIZ §7.1 „Nicht übernommen: Slicer-Matrix“ → Verweis auf D.1.
- **`M600` im Dateistrom:** Stock macht daraus `PAUSE` (nur mit Power-Loss-Schalter). Upstream-`M600` gibt es nicht. Falls Orca-Dateien `M600` enthalten (Farbwechsel-Pause), braucht der Pi ein Makro `M600` → `PAUSE`. `pi-host/config-pi/*.cfg` enthält derzeit **kein** `M600` (geprüft per grep). Ohne Makro meldet Upstream „Unknown command“, und die Zeile wird übersprungen (V).
- **Erzwungenes `G90` nach RESUME (Stock):** nicht nachbauen. Upstream stellt den Modus aus PAUSE_STATE korrekt her.

---

## E. Belegqualität und Offenes

| Punkt | Stand |
|---|---|
| Matrix-Format, Multiplikator, n² | **B+L** (4 Live-Metadatensätze) |
| Index = Slicer-`T<n>`, Rückwärtssuche über `Tn_map`/`Tnn_map`, `[alt][neu]` | **B** (Dekompilat BW Z.4387-4403, generate_Tn_map @0x1b4f70 + Tupel) **+L** (`Tnn_map` vom Display) |
| Zeilenform der Header-Felder in der G-Code-Datei | **ANNAHME** (`metadata.py` fehlt; keine G-Code-Datei im Projekt) |
| Matrix-Pfad live | nicht belegt (keine `flush_para:`/`flush; last_tnn`-Zeile in den Logs) |
| Power-Loss-Ablauf | **B** (VS/GM/PR Klartext); live nur Schreibseite (`record_power_loss_info to eeprom`, 50× am 12.10.), keine Wiederaufnahme im Log |
| Wirkung `RESTORE_LIMITS` / Creality-`corexy` | **ANNAHME** (Datei fehlt) |
| Creality-`gcode.py` (`exclude_object_info`, `last_temperature_info`, Schreiber von `M204`/`SET_GCODE_OFFSET`/`pressure_advance`/`variable_z_safe_pause`) | **ANNAHME** (fehlt) |
| `BOX_POWER_LOSS_RESTORE`, `power_loss_clean`, `flush_material` in BW | nur Namen gesichtet |
