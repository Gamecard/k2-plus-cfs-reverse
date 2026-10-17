# PAUSE-RESUME-MATERIAL-BEFUND 2026-10-17 – Creality-`pause_resume.py` und Materialdatenbank (K2 Plus, FW 1.1.7.0)

Rein **statisch** (kein Drucker-/Pi-Zugriff). Belegform: **B** = belegt (Quelltext/Disassembly),
**L** = zusätzlich Live-Log, **A** = Annahme, **V** = Vermutung, **W** = widerlegt.
Zeilennummern: `CR:<n>` = Creality-`rootfs-live/usr/share/klipper/klippy/extras/pause_resume.py`
(Klartext-Python, 227 Zeilen), `UP:<n>` = Upstream-Klipper 9cb9975 `klippy/extras/pause_resume.py`
(100 Zeilen), `ST:<n>` = Stock-Makros **live** `config/F008_CR0CN240319C13/gcode_macro.cfg`
(md5-gleich mit der am 16.10. vom Drucker geholten `gcode_macro.cfg`), `ALT:<n>` = älterer
Stand `config/F008/gcode_macro.cfg`, `PI:<n>` = `pi-host/config-pi/macros.cfg`,
`KB:<n>` = `pi-host/k2_box.py`. Arbeitsdateien lokal (nicht im Repo).

Nachbau: `<projekt>/nachbau/material_db.py` (Lader + Abfragen, Selbsttest **ok 40/40**,
inkl. Kreuzprobe gegen `nachbau/filament_rack.py`).

## Kurzfassung

| # | Frage | Ergebnis |
|---|---|---|
| 1 | Was sind `PAUSE_BASE`/`RESUME_BASE`? | Die Python-Befehle `PAUSE`/`RESUME` aus `pause_resume.py`, per `rename_existing` umbenannt (Upstream `gcode_macro.py:135-170`). `PAUSE_BASE` = Upstream-gleich (SD-Pause + `SAVE_GCODE_STATE NAME=PAUSE_STATE`, CR:175-181 = UP:61-67). `RESUME_BASE` = Upstream (`RESTORE_GCODE_STATE NAME=PAUSE_STATE MOVE=1 MOVE_SPEED=<VELOCITY / recover_velocity 50>`, CR:200-204 = UP:81-85) **plus** `resume_err`-Abbruch und Power-Loss-Datei. **B** |
| 2 | `resume_err` | Einmal-Sperrflag: von `box_wrapper.so` gesetzt, wenn das automatische Nachholen im RESUME scheitert. `RESUME_EXTERNAL` überspringt dann alles (ST:884/942); `RESUME_BASE` wartet 0,5 s, **setzt das Flag zurück und kehrt zurück, ohne fortzusetzen** (CR:195-199) → Druck bleibt pausiert, nächstes RESUME versucht neu. **B**. Die ANNAHME „RESUME_BASE bricht bei `resume_err` nicht ab“ (BOOTLOADER-HOST-BEFUND §5.2) ist **W**. |
| 3 | Rückfahrt von `RESUME_BASE` | Wie Upstream: eine gerade Fahrt zu PAUSE_STATE (CR:200-203). Stock-Live-`RESUME` gibt `VELOCITY` **nicht** weiter (`{get_params}` ist in ST:875 undefiniert) → immer `recover_velocity` 50 mm/s. **B** (Creality-`gcode_move.py` liegt nicht vor → MOVE=1-Bahn selbst **A**: wie Upstream). |
| 4 | Was macht Stock zusätzlich? | Power-Loss-Endpunkte/-Datei, `do_resume_status`, Display-JSON-Meldungen, Schnell-Abbruch (`invoke_action`/`fast_stop_complete`) beim Webhook-Cancel, `getBootLoaderVersion`; Koordinationsflags `pause_start`/`motor_cancel_print_start` (nur Ablage, gelesen von box/motor-Wrapper). §A.2 |
| 5 | Unser RESUME dagegen | Pi ist in allen Sicherheitspunkten **gleich oder strenger** (Sperre statt Einmalflag, `is_paused`-Prüfung, Heizen im Schacht, Rückfahrt X/Y in Pausenhöhe). **Lücke:** Vorextrusions-, Wechsel- und Spül-Parameter sind fest (460 mm/min, 220 °C, 360 mm/min) statt materialabhängig aus der DB. §A.4, §C |
| 6 | Materialdatenbank | Cloud-Antwortformat `result.list[]` mit `base` (Katalog) + `kvParam` (Slicer-Profil, alles Strings). Stock-Code liest nachweislich nur 5 Felder: `base.id`, `base.meterialType`, `base.maxTemp`, `kvParam.nozzle_temperature`, `kvParam.filament_max_volumetric_speed`. Default-DB: 56 Einträge, 32 davon mit `nozzle_temperature` > 220 °C. §B |

---

## A. `pause_resume.py` – Creality gegen Upstream 9cb9975

### A.1 Gemeinsamer Kern (B)

| Teil | Upstream | Creality | Gleich? |
|---|---|---|---|
| Config | `recover_velocity` 50 (UP:11) | dasselbe (CR:13); live `[pause_resume]` ohne Optionen (`config/F008_CR0CN240319C13/printer.cfg:382`, Pi `pi-host/config-pi/printer.cfg:504`) → 50 mm/s | ja |
| `send_pause_command` | UP:47-59 | CR:161-173 | ja |
| `cmd_PAUSE` | `is_paused` → Meldung; sonst Pause senden, `SAVE_GCODE_STATE NAME=PAUSE_STATE`, `is_paused=True` (UP:61-67) | identisch, nur Meldetext als Display-JSON `key211` (CR:175-181) | ja (Semantik) |
| `cmd_RESUME` | nicht pausiert → Meldung; `RESTORE_GCODE_STATE … MOVE=1 MOVE_SPEED=%.4f`; Resume senden; `is_paused=False` (UP:77-86) | + `resume_err`-Zweig (CR:195-199), + Power-Loss-Datei (CR:206-213), nicht-pausiert als `gcmd.warning` `key16` (CR:193-194) | Kern ja |
| `send_resume_command` | `v_sd.do_resume()` (UP:68-75) | + `v_sd.do_resume_status = True` davor (CR:185) | fast |
| `CLEAR_PAUSE` / `CANCEL_PRINT` | UP:87-97 | CR:214-224 | ja |
| Webhook pause/resume | UP:37-40 | CR:146-149 | ja |

**Semantik `PAUSE_BASE`/`RESUME_BASE`:** Beide Stock-Makros (`[gcode_macro PAUSE] rename_existing:
PAUSE_BASE`, ST:726-728; `[gcode_macro RESUME] rename_existing: RESUME_BASE`, ST:870-872) benennen
die Python-Befehle oben um (Upstream `gcode_macro.py:135-170`: der alte Handler wird unter
`rename_existing` neu registriert). Pi macht dasselbe (PI:667-669, PI:757-759, PI:992-994).
Folgen (B):

- `PAUSE_BASE` speichert PAUSE_STATE an der Position **zum Zeitpunkt des Aufrufs**. Stock ruft es
  als ERSTES (ST:732-735, danach `PAUSE_EXTERNAL`), Pi ebenso (PI:694). Nach einem CFS-Fehler fährt
  `box_wrapper` vor dem `PAUSE` zur Safe-Pos (BOOTLOADER-HOST-BEFUND §5.1) → PAUSE_STATE =
  X225/Y345 auf Druck-Z: damit **bestätigt** (Makrofolge + CR:180).
- `RESUME_BASE` fährt mit `MOVE=1` von der aktuellen Position zu PAUSE_STATE; Upstream
  `gcode_move.py:240-260`: Modus/Basis/Faktoren zurück, E-Basis + `e_diff`, dann **eine**
  `move_with_transform` zu X/Y/Z der Pause (= gerade Linie). Creality-`gcode_move.py` liegt nicht
  vor → Bahn = **A** (wie Upstream); der Aufruf selbst (CR:201-203) ist B.

### A.2 Creality-Zusätze (B, sofern nicht anders markiert)

| Zusatz | Zeilen | Wirkung | Pi-Relevanz |
|---|---|---|---|
| `resume_err` | Init CR:51, Status CR:157, Prüfung CR:195-199 | s. A.3 | Gegenstück `chain_error`/Sperren (A.4) |
| `pause_start`, `motor_cancel_print_start` | CR:49-50 | nur Ablage, in `pause_resume.py` sonst unbenutzt. Gelesen/geschrieben von anderen Modulen: `box_wrapper` `BoxError.handle_event` liest `pause_resume.pause_start` direkt vor dem `PAUSE`-Aufruf (Disassembly @`0x1c53f0`, Vergleich danach), `motor_control_wrapper` nutzt beide (Strings „motor_control start PAUSE…“). **V:** Sperre gegen Doppel-Pause bei Motor-/Box-Fehler gleichzeitig. Genaue Semantik offen. | Pi hat keine zweite Pausen-Quelle aus `.so`-Modulen; `PAUSE` prüft `is_paused` (PI:672) |
| Power-Loss | Endpunkte CR:30-35, Handler CR:70-141; `RESUME` schreibt `variable_z_safe_pause = 0` in die Power-Loss-Datei (CR:206-213) | Fortsetzen nach Stromausfall (EEPROM `bl24c16f`, Datei `print_file_name_path`) | entfällt bewusst (`pi-host/config-pi/README.md`, PI-ERSATZ-PLAN „Power-Loss … weglassen“) |
| `do_resume_status = True` | CR:185 | Attribut des Creality-`virtual_sdcard` (Quelle fehlt) – **V:** Statusmeldung „wird fortgesetzt“ für Display/Power-Loss | entfällt |
| Webhook `pause_resume/cancel` | CR:142-145 | vor `CANCEL_PRINT`: `gcode.invoke_action()` + `gcode.fast_stop_complete()` (Creality-`gcode.py`, Quelle fehlt) – **V:** bricht einen laufenden langen Befehl ab, damit Cancel sofort greift | Upstream wartet auf das G-Code-Mutex: ein Cancel aus Mainsail wartet, bis ein laufender `KTWO_BOX_*`-Schritt/`M109` fertig ist (§C P5) |
| `fast/response`, `fast/stop` | CR:44-47, CR:150-153 | dieselben Creality-Hooks einzeln | entfällt |
| `getBootLoaderVersion` | CR:42-43, CR:55-59 | gibt `software_version` der Haupt-MCU zurück | Moonraker/Mainsail zeigen MCU-Versionen selbst |
| `_setBootLoaderState` | CR:61-68 | `config_usrboot` / `jump_to_usrboot_query` – **nirgends registriert** (toter Code) | – |
| Display-Meldungen | CR:177 `key211`, CR:194 `key16` | JSON für die Stock-Oberfläche | – |

Der offene Punkt aus BOOTLOADER-HOST-BEFUND §6 („Creality-`pause_resume` … Semantik
`PAUSE_BASE`/`RESUME_BASE`, Umgang mit `resume_err`“) ist damit **geschlossen**.

### A.3 `resume_err` im Stock-Ablauf (B)

Stock-`RESUME` (ST:870-875): `RESUME_EXTERNAL_PROCESS` → (`BOX_ERROR_RESUME_PROCESS` →
`RESUME_EXTERNAL`, ST:877-880) → `RESUME_BASE`. Klipper rendert jedes Makro erst beim Aufruf,
`RESUME_EXTERNAL` liest `resume_err` also **nach** dem Nachhol-Versuch.

1. `BOX_ERROR_RESUME_PROCESS` (`error_resume_process`, BOOTLOADER-HOST-BEFUND §5.2): holt bei
   leerem Kopfsensor den Wechsel nach; scheitert eine Stufe → `recode_err`, **`resume_err = True`**,
   `move_to_safe_pos`.
2. `RESUME_EXTERNAL` läuft komplett nur bei `resume_err` None/False (ST:884 … ST:942): kein Heizen,
   keine Schachtfahrt, keine Vorextrusion, keine Reinigung.
3. `RESUME_BASE`: `is_paused` noch True → `resume_err` True → `reactor.pause(+0,5 s)`,
   **`resume_err = False`**, `return` (CR:195-199). **Kein** `RESTORE_GCODE_STATE`, **kein**
   `do_resume`, `is_paused` bleibt True → der Druck bleibt pausiert, ohne Meldung an den G-Code-
   Aufrufer (nur `logging.info`).
4. Nächstes RESUME: Flag ist wieder False → neuer Durchlauf. Ob `resume_tnn` dann noch gesetzt ist:
   `error_resume_process` löscht es vor dem Nachholen (`clear_resume_tnn`, §5.2 Schritt 5), der
   Fehlerpfad ruft aber `recode_err(…, '<stufe>_err')`, und das speichert für T<n>-Pfad-Fehler
   `resume_tnn` neu (§5.1) → **V:** zweites RESUME versucht erneut nachzuholen.

### A.4 Unser RESUME gegen Stock

| Punkt | Stock (Beleg) | Pi (Beleg) | Bewertung / Risiko |
|---|---|---|---|
| Fehler beim Nachholen | `resume_err` = **Einmal**-Sperre, RESUME_BASE schweigt und bleibt pausiert (A.3) | `chain_error` → `action_raise_error` in RESUME (PI:762-764) bzw. `KTWO_BOX_RESUME_CHECK` / `_PI_RESUME_RETURN` (PI:926-928); bleibt bis `KTWO_BOX_ERROR_CLEAR` | Wirkung gleich (pausiert, keine Rückfahrt); Pi **strenger** (dauerhaft + Meldung). Risiko: keins |
| RESUME ohne Pause | **keine** `is_paused`-Prüfung im Makro (ST:870-875): `RESUME_EXTERNAL` heizt/fährt in den Schacht/extrudiert 82 mm/reinigt auch im laufenden Druck; `hotend_temp` ist nach dem letzten RESUME 0 (ST:891) → ggf. `M109 S0` + Kalt-Extrusionsfehler (ST:923-932); erst danach warnt `RESUME_BASE` | alles nur bei `is_paused` (PI:765); sonst nur `RESUME_BASE` (Meldung) + `KTWO_BOX_RESUME_FIX` | Pi **besser**. Stock-Risiko gering (Display zeigt RESUME nur in der Pause) |
| Reihenfolge Heizen | heizt ZUERST (am Parkpunkt Safe-Pos, ST:885-892), dann Schacht (ST:903) | Schacht zuerst, dann `M109` (PI:852-855) | Pi besser (Tropfen in den Schacht), dokumentiert NOTIZ §4.3 |
| Temperatur-Variable | `hotend_temp` nach Gebrauch auf 0 (ST:891) | wird nicht zurückgesetzt, aber jede neue PAUSE setzt sie (PI:724), RESUME nutzt sie nur in der Pause | gleichwertig |
| Vorextrusion | `E = PAUSE.extrude + 80` (ST:913), **F = `filament_rack.remain_material_velocity · 0,8`** (ST:914, ST:932) – materialabhängig (B.3) | 82 mm mit **fest 460 mm/min** (PI:62-65, PI:892), nur im Schacht und heiß (PI:882-885) | **Lücke** (§C P2): für das Standard-Material des Users richtig (Hyper PLA 23 mm³/s → 460, L), für langsamere Materialien zu schnell: Generic PLA 280, Silk 200, TPU 40–60 mm/min. Risiko **mittel** bei TPU/Silk (Zahnrad frisst, Düse verstopft) |
| Rückfahrt | gerade Linie zu PAUSE_STATE mit 50 mm/s (CR:200-203; ST:875 ohne `VELOCITY`) | eigene Fahrt X/Y in Pausenhöhe, Z senkrecht (PI:908-959), danach `RESUME_BASE` = Nullfahrt; `{rawparams}` wird weitergereicht (PI:793) | Pi besser; `VELOCITY` wirkt nur im Fallback ohne Freigaben (PI:837-841) |
| Z-Rücknahme in `RESUME_EXTERNAL` | auskommentiert (ST:908) → wirkungslos | – | bestätigt NOTIZ §4.1 |
| `SET_LIMITS` am Ende | ST:939 (Befehl aus `box_wrapper.so`, Wirkung unbelegt) | Pi stellt die Beschleunigung in `_PI_NOZZLE_CLEAN` selbst zurück (PI:550, PI:575); Modus/Geschwindigkeit per `SAVE/RESTORE_GCODE_STATE` (PI:843, PI:860) | **A:** gleichwertig; nach einem Abbruch mitten im Reinigen bliebe `M204 S2000` stehen (Pi und vermutlich Stock) – Risiko gering |
| Lüfter | Werte vor dem Heizen gesetzt (ST:893-902), `M106 S0` vor dem Extrudieren, `S127` + 3 s, dann `M106 P2/P0 S{s_value}` (ST:937-938) – **Stock-Fehler:** `s_value`/`s_value_0` nur definiert, wenn der Lüfter vorher lief → sonst `M106 P2 S F1` | Lüfter nach `_PI_RESUME_PREPARE` aus den gemerkten Werten (PI:780-789) | Pi robuster; Stock-Fehler nicht übernehmen |
| Nachholen | automatisch (BOOTLOADER-HOST-BEFUND §5.2) | Sperre bei leerem Kopf, Nachholen nur mit `resume_redo_change` (NOTIZ §6a.7) | bewusste Abweichung (NOTIZ §6a.8) |
| Nachhol-/Wechsel-Temperatur | Material-Temperatur des Ziel-Slots aus der DB (`get next material temp`, live 188× „220“; `get next material max temp: 240`, L) | `max(Ziel, tn_extrude_temp 220)` (KB:2004-2015, KB:287) | **Lücke** (§C P1): bei PETG/ABS/ASA/PA/PC-Slots zu kalt. Risiko **mittel-hoch** sobald Nicht-PLA im CFS liegt |
| CANCEL | `END_PRINT`, `CANCEL_PRINT_BASE`, `CLEAR_EEPROM_INFO` (ST:944-953) | `END_PRINT`, `CANCEL_PRINT_BASE` (PI:992-997) | EEPROM entfällt; ok |

### A.5 Abgleich mit NOTIZ §6a.8 (und Nachbarstellen)

| Stelle | Aussage dort | Ergebnis |
|---|---|---|
| §6a.8 „Rückfahrziel“ | Stock-`RESUME_BASE` fährt gerade zu X225/Y345, Druck-Z (**ANNAHME**: wie Upstream) | **bestätigt** für `pause_resume` (CR:200-203 = UP:81-85) und für PAUSE_STATE (CR:180 + Makrofolge ST:732-735). Präzisierung: Stock fährt immer mit 50 mm/s (ST:875 verwirft `VELOCITY`). Rest-**A:** Creality-`gcode_move` |
| §6a.8 „Nachholen … Fehler → `resume_err`“ | Fehler → `resume_err` (§5.2) | **bestätigt + präzisiert:** Druck bleibt pausiert, Flag ist einmalig (A.3) |
| §6a.7 Punkt 5 | Fehler → `chain_error`, RESUME gesperrt „(= Stock `resume_err`)“ | Wirkung gleich, **Lebensdauer verschieden** (Stock einmalig, Pi bis `ERROR_CLEAR`) – Text ergänzen (§C P6) |
| BOOTLOADER-HOST-BEFUND §5.2 letzter Absatz | „Ob Creality-`RESUME_BASE` bei `resume_err` abbricht … **ANNAHME**: nein“ | **W** (CR:195-199) |
| BOOTLOADER-HOST-BEFUND §5.3 | Bewegung von `RESUME_BASE` = ANNAHME wie Upstream | **bestätigt** auf `pause_resume`-Ebene |
| §6a.10 + Kommentar PI:688 | Stock-Belege `config/F008/gcode_macro.cfg:738` (hotend_temp) und `:836-842` (RESUME-Temperatur) | Semantik **bestätigt**, aber **älterer Stand**: live ist `config/F008_CR0CN240319C13/gcode_macro.cfg` → ST:761 bzw. ST:885-892 (ALT:738/836-842 haben noch kein `RESUME_EXTERNAL`/`resume_err`; ALT:888 reicht `VELOCITY` noch durch). Nur Verweise korrigieren (§C P6) |
| §6a.10 „Stock setzt das Ziel vor `PAUSE` nicht zurück (ANNAHME)“ | – | `pause_resume.py` enthält nichts zur Temperatur; `PAUSE_EXTERNAL` merkt `extruder.target` (ST:761). Damit heizt Stock nach einem Abbruch nach dem Aufheizen ebenfalls auf die Wechsel-Temperatur zurück, sofern `box_wrapper` das Ziel nicht vorher zurücksetzt (weiter **A**). Pi-Fix `CFS_TEMP` geht über Stock hinaus |
| §4.1 / §6 „Prime-Geschwindigkeit fest 460 (Stock materialabhängig)“ | ANNAHME 460 | Formel jetzt vollständig belegt (B.3): `vol/2,4·60·0,8`; 460 = Hyper PLA, 280 = Generic PLA (beide L) |

---

## B. Materialdatenbank `material_database.json`

### B.1 Dateien und Struktur (B)

- **Stock-Default** (Firmware): `rootfs-live/etc/sysConfig/defData/material/material_database.json`.
- **Gelesen** wird vom Host-Code die Arbeitskopie `creality/userdata/box/material_database.json`
  (Strings in `filament_rack_wrapper.so` und `box_wrapper.so`; `nachbau/filament_rack.py:106`).
  Wer sie anlegt/aktualisiert (Kopie der Default-DB, Cloud-Abruf durch die Display-App), ist
  **offen** (V: Display-App). Eine Benutzer-Kopie liegt lokal vor (nicht im Repo).

```
{ "code": 0 (int), "msg": "ok", "reqId": <str>,
  "result": { "count": <int = Länge der Liste>, "version": <str, Zeitstempel>,
              "list": [ {
                 "engineVersion": "3.0.0", "printerIntName": "F008", "nozzleDiameter": ["0.4"],
                 "base":    { id (str, 5 Zeichen), name, brand, meterialType (sic),
                              minTemp, maxTemp (int, °C), density (float, g/cm³),
                              diameter (str, mm), colors, costPerMeter, weightPerMeter,
                              dryingTemp, dryingTime, softeningTemp, shrinkageRate,
                              isSoluble, isSupport, rank },
                 "kvParam": { ~90 Orca-Slicer-Schlüssel, ALLE Werte als String, "nil" = leer:
                              nozzle_temperature, nozzle_temperature_initial_layer,
                              nozzle_temperature_range_low/high (°C),
                              filament_max_volumetric_speed (mm³/s), filament_type,
                              filament_diameter, filament_density, filament_flow_ratio,
                              pressure_advance, default_filament_colour,
                              filament_minimal_purge_on_wipe_tower, chamber_temperature,
                              cool_plate_temp, … } } ] } }
```

Default-DB: 56 Einträge, alle `printerIntName` F008, Düse 0.4, `engineVersion` 3.0.0, IDs
eindeutig und 5-stellig, `kvParam` 90–93 Schlüssel (`material_db.py --struktur`).

### B.2 Welche Felder der Stock-Code wirklich nutzt

Strings-Abgleich aller 124 DB-Schlüssel gegen die fünf `.so` in `rootfs-live/…/extras/` (exakte
Treffer) plus Logik aus FILAMENT-RACK-BEFUND §5:

| Feld | Leser | Verwendung (B, sonst markiert) |
|---|---|---|
| `result.list` | rack, box | Iteration (`data.get('result',{}).get('list',[])`) |
| `base.id` | rack, box | Schlüssel: `== type[1:6]` (6-stelliger RFID-Materialcode, RFID-vender `[11:17]`) bzw. `rfid[12:17]`; erster Treffer gewinnt (`break`), `printerIntName`/`nozzleDiameter` werden nicht geprüft (A) |
| `kvParam.nozzle_temperature` | rack, box | `int()` → Material-/Wechsel-Temperatur (`get_material_target_temp`, Log „get next material temp“) |
| `base.maxTemp` | rack, box | `int()` → Höchsttemperatur (`max=True`, Box: `get_material_target_max_temp`, Log „get next material max temp“; V: Spültemperatur `get_flush_max_temp`) |
| `kvParam.filament_max_volumetric_speed` | rack, box | `/2.4` → mm/s, `·60` → mm/min (`remain_material_velocity`, RESUME-Prime ·0,8; Box: „next material max extrusion speed: 9.58 mm/s“ → `flush_velocity … feed=360.0(mm/min)`, L) |
| `base.meterialType` | rack, box | Materialfamilie, Teilstring-Vergleich in `is_different_type_material` (→ Spüllänge `diff_material_type_min_flush_len` 370) |
| `base.name` | box (V) | Log „material database get material name: %s“ – Zuordnung zu `base.name` nicht verifiziert |
| `default_filament_colour` | box (V) | String „default_filament_colour: %s“ im `.so`; live steht der Wert aber in den Slicer-Metadaten des Druckauftrags – Quelle **offen**. In der Default-DB unbrauchbar (`""`, `"\"\""` oder `#FFFFFF`) |
| `pressure_advance` | prtouch | nur gleichnamiger String in `prtouch_v3_wrapper` – kein DB-Leser belegt |

Alle übrigen Felder (Temperaturbereich, `minTemp`, Trocknung, Dichte, Kammer, Bett …) liest der
Host-Code **nicht** – sie dienen Slicer/Display. Fehlerpfade (B, FILAMENT-RACK §5): unbekannte ID,
Typ `'None'`/`'-1'` oder `int()`/`float()`-Fehler → Temperatur-Fallback `[filament_rack] temp`
220, Geschwindigkeit `velocity` 360 **ohne** ·60 (= 360 mm/min), Typ `None`.

Flush-relevant sind damit genau `meterialType`, `nozzle_temperature`, `maxTemp` und
`filament_max_volumetric_speed`; die Spülmenge selbst kommt aus dem Druckauftrag
(`flush_volumes_matrix`, L) und `material_volume_to_length`. Die genaue Spül-Formel
(`get_flush_velocity`/`get_flush_temp`/`cal_flush_list`) gehört zur parallel laufenden Flush-Analyse
von `box_wrapper` und ist hier **nicht** nachgebaut.

### B.3 Werte der Stock-Default-DB (B; Speed = `vol/2,4·60` mm/min, Prime = Speed · 0,8 = Stock-RESUME-Vorextrusion)

Erzeugt mit `python3 nachbau/material_db.py --tabelle <Default-DB>`. „Bereich“ = `kvParam.nozzle_temperature_range_low/high` (Slicer, vom Host nicht gelesen).

| id | Name | meterialType | nozzle_temperature | Bereich low–high | minTemp | maxTemp | vol. mm³/s | Speed mm/min (vol/2,4·60) | RESUME-Prime ·0,8 |
|---|---|---|---|---|---|---|---|---|---|
| 01001 | Hyper PLA | PLA | 220 | 190–240 | 190 | 240 | 23 | 575.0 | 460.0 |
| 02001 | Hyper PLA-CF | PLA-CF | 220 | 190–240 | 190 | 240 | 23 | 575.0 | 460.0 |
| 06002 | Hyper PETG | PETG | 250 | 220–270 | 220 | 270 | 23 | 575.0 | 460.0 |
| 03001 | Hyper ABS | ABS | 260 | 240–280 | 240 | 280 | 23 | 575.0 | 460.0 |
| 09002 | ENDER FAST PLA | PLA | 220 | 190–240 | 190 | 240 | 23 | 575.0 | 460.0 |
| 04001 | CR-PLA | PLA | 220 | 190–240 | 190 | 240 | 18 | 450.0 | 360.0 |
| 05001 | CR-Silk | PLA | 220 | 190–240 | 190 | 240 | 10 | 250.0 | 200.0 |
| 06001 | CR-PETG | PETG | 240 | 220–270 | 220 | 270 | 18 | 450.0 | 360.0 |
| 07001 | CR-ABS | ABS | 260 | 240–280 | 240 | 280 | 18 | 450.0 | 360.0 |
| 00001 | Generic PLA | PLA | 220 | 190–240 | 190 | 240 | 14 | 350.0 | 280.0 |
| 00002 | Generic PLA-Silk | PLA | 220 | 190–240 | 190 | 240 | 10 | 250.0 | 200.0 |
| 00003 | Generic PETG | PETG | 250 | 220–270 | 220 | 270 | 16 | 400.0 | 320.0 |
| 00004 | Generic ABS | ABS | 260 | 240–280 | 240 | 280 | 16 | 400.0 | 320.0 |
| 00005 | Generic TPU | TPU | 220 | 200–250 | 210 | 240 | 2 | 50.0 | 40.0 |
| 00006 | Generic PLA-CF | PLA-CF | 220 | 190–240 | 190 | 240 | 18 | 450.0 | 360.0 |
| 00007 | Generic ASA | ASA | 260 | 240–280 | 240 | 280 | 10 | 250.0 | 200.0 |
| 08001 | Ender-PLA | PLA | 220 | 190–240 | 190 | 240 | 18 | 450.0 | 360.0 |
| 09001 | EN-PLA+ | PLA | 220 | 190–240 | 190 | 240 | 18 | 450.0 | 360.0 |
| 10001 | HP-TPU | TPU | 220 | 190–240 | 190 | 240 | 3 | 75.0 | 60.0 |
| 11001 | CR-Nylon | PA | 260 | 250–270 | 250 | 270 | 12 | 300.0 | 240.0 |
| 13001 | CR-PLA Carbon | PLA | 220 | 190–240 | 190 | 240 | 18 | 450.0 | 360.0 |
| 14001 | CR-PLA Matte | PLA | 220 | 190–240 | 190 | 240 | 18 | 450.0 | 360.0 |
| 15001 | CR-PLA Fluo | PLA | 220 | 190–240 | 190 | 240 | 18 | 450.0 | 360.0 |
| 16001 | CR-TPU | TPU | 220 | 210–240 | 210 | 240 | 2 | 50.0 | 40.0 |
| 17001 | CR-Wood | PLA | 220 | 190–240 | 190 | 240 | 18 | 450.0 | 360.0 |
| 18001 | HP Ultra PLA | PLA | 220 | 190–240 | 190 | 240 | 18 | 450.0 | 360.0 |
| 19001 | HP-ASA | ASA | 260 | 240–280 | 240 | 280 | 14 | 350.0 | 280.0 |
| 00008 | Generic PA | PA | 260 | 240–280 | 240 | 260 | 10 | 250.0 | 200.0 |
| 00009 | Generic PA-CF | PA-CF | 280 | 260–300 | 260 | 300 | 6 | 150.0 | 120.0 |
| 00010 | Generic BVOH | BVOH | 220 | 200–220 | 200 | 220 | 6 | 150.0 | 120.0 |
| 00011 | Generic PVA | PVA | 220 | 215–225 | 215 | 225 | 6 | 150.0 | 120.0 |
| 00012 | Generic HIPS | HIPS | 240 | 220–250 | 220 | 250 | 10 | 250.0 | 200.0 |
| 00013 | Generic PET-CF | PET-CF | 290 | 280–320 | 280 | 320 | 8 | 200.0 | 160.0 |
| 00014 | Generic PETG-CF | PETG-CF | 250 | 240–260 | 240 | 260 | 10 | 250.0 | 200.0 |
| 00015 | Generic PA6-CF | PA6-CF | 300 | 280–300 | 280 | 300 | 8 | 200.0 | 160.0 |
| 00016 | Generic PAHT-CF | PAHT-CF | 300 | 300–320 | 300 | 320 | 8 | 200.0 | 160.0 |
| 00017 | Generic PPS | PPS | 310 | 300–350 | 320 | 350 | 2 | 50.0 | 40.0 |
| 00018 | Generic PPS-CF | PPS-CF | 310 | 300–350 | 300 | 350 | 6 | 150.0 | 120.0 |
| 00019 | Generic PP | PP | 240 | 220–250 | 220 | 250 | 12 | 300.0 | 240.0 |
| 00020 | Generic PET | PET | 260 | 250–270 | 250 | 270 | 8 | 200.0 | 160.0 |
| 00021 | Generic PC | PC | 260 | 250–270 | 250 | 270 | 16 | 400.0 | 320.0 |
| 00025 | Generic PA12-CF | PA-CF | 290 | 260–300 | 270 | 310 | 8 | 200.0 | 160.0 |
| 00022 | Generic PA612-CF | PA-CF | 290 | 260–300 | 270 | 310 | 8 | 200.0 | 160.0 |
| 12003 | Hyper PAHT-CF | PA-CF | 290 | 280–320 | 280 | 320 | 10 | 250.0 | 200.0 |
| 12002 | Hyper PPA-CF | PA-CF | 300 | 280–320 | 280 | 320 | 8 | 200.0 | 160.0 |
| 00023 | Generic Support for PA | PA | 290 | 260–300 | 260 | 300 | 6 | 150.0 | 120.0 |
| 00024 | Generic Support for PLA | PLA | 220 | 190–240 | 190 | 240 | 6 | 150.0 | 120.0 |
| 00026 | Generic TPU 64D | TPU | 220 | 215–240 | 210 | 240 | 2 | 50.0 | 40.0 |
| 07002 | Hyper PC | PC | 260 | 250–270 | 250 | 270 | 18 | 450.0 | 360.0 |
| 01601 | Soleyin Ultra PLA | PLA | 220 | 190–240 | 190 | 240 | 18 | 450.0 | 360.0 |
| 00033 | Generic ASA-CF | ASA-CF | 270 | 250–280 | 250 | 280 | 16 | 400.0 | 320.0 |
| 00034 | Generic PA6-GF | PA-GF | 270 | 250–290 | 250 | 290 | 6 | 150.0 | 120.0 |
| 00035 | PLA-LW | PLA | 220 | 190–270 | 190 | 270 | 6 | 150.0 | 120.0 |
| 00027 | Generic PETG-GF | PETG-GF | 270 | 240–270 | 240 | 280 | 8 | 200.0 | 160.0 |
| 00031 | Generic PP-CF | PP-CF | 260 | 220–270 | 220 | 270 | 6 | 150.0 | 120.0 |
| 00032 | Generic PCTG | PCTG | 250 | 250–270 | 250 | 270 | 8 | 200.0 | 160.0 |

Auffällig (B): `nozzle_temperature` ist nie < 220 (24× = 220, 32× > 220). `maxTemp` = `range_high`
bis auf 5 Einträge, `minTemp` weicht bei einigen ebenfalls vom Bereich ab. `meterialType` ≠
`kvParam.filament_type` bei 1 Eintrag (CR-PLA Carbon: PLA vs. PLA-CF – Stock rechnet mit `PLA`).
Live passt: `max_volumetric_speed: 23` → „get material extrusion speed: 9“ (= `int(9,58)`) und
`14` → `5`; `remain_material_velocity` 575.0 / 350.00000000000006; RESUME „velocity: 460.0“ /
„280.00000000000006“ (L, Hyper PLA bzw. Generic PLA).

### B.4 Benutzer-Kopie (lokal) gegen Default – nur Struktur und Zählwerte

- Gleiches Format und gleiche Feldnamen; zusätzlich `base.alias`, `base.dryingTempHigh/Low`,
  `base.mixColorInfoList`, `kvParam.dont_slow_down_outer_wall`.
- Knapp doppelt so viele Einträge (101 statt 56); alle 56 Default-IDs sind enthalten, die übrigen
  sind weitere Marken/Varianten. Weiter eindeutige 5-stellige IDs, nur F008/0.4.
- **Unvollständige Einträge:** 19 der zusätzlichen Einträge haben stark gekürzte `kvParam`
  (9–52 statt ~90 Schlüssel), 7 davon **ohne** `nozzle_temperature` (JSON `null`) – dort greift im
  Stock der Fallback 220 °C, unabhängig vom Material. `filament_max_volumetric_speed` ist überall
  lesbar.
- Bei den 56 gemeinsamen Einträgen sind die genutzten Felder `nozzle_temperature` und
  `filament_max_volumetric_speed` **identisch**; abweichend sind `meterialType` (3 Einträge),
  `maxTemp` (1), `name` (1) sowie nicht genutzte Felder. `meterialType` ≠ `filament_type` bei 10
  Einträgen.
- Folge: Für Temperatur/Geschwindigkeit der Standard-Materialien ist die Default-DB gleichwertig;
  für Typvergleich (Spüllänge) und Höchsttemperatur kann die Benutzer-Kopie andere Ergebnisse
  liefern.

### B.5 Abgleich mit unseren Annahmen

| Annahme (Fundstelle) | DB/Stock | Abweichung |
|---|---|---|
| `tn_extrude_temp` 220, Wechsel-Temperatur `max(Ziel, 220)` (KB:287, KB:2004-2015) | Stock nimmt `nozzle_temperature` des Ziel-Materials (live 220 = PLA) | gleich für PLA-Familie (24 Einträge); **zu kalt** für 32 Einträge (PETG 240–250, ABS/ASA 260, PA/PC 260–300, PPS 310), wenn der laufende Druck kälter ist (z. B. PLA-Druck, Wechsel auf PETG-Slot → 220 statt 250; ABS → 220 < Bereich ab 240) |
| Nachhol-Temperatur `max(Drucktemp, 220)` (NOTIZ §6a.7 Punkt 5, §6a.9) | dito (Ziel-TNN-Material) | dito |
| `resume_prime_speed` 460 mm/min (PI:62-65) | `vol/2,4·60·0,8` des zuletzt geladenen Materials; Fallback 360·0,8 = 288 | richtig für vol 23 (5 Einträge); zu schnell für die übrigen 51 Einträge (bis 11,5× bei TPU vol 2 → 40) |
| `tn_extrude_velocity` 360 mm/min für EXTRUDE/FLUSH (KB:289, KB:2579) | Stock live `flush_velocity … feed=360.0` (vol 23) bzw. `350.0` (vol 14) – V: min(Material-Speed, Basis) | für PLA ok (L), für TPU (50–75), Silk/ASA/PA (≤ 250) zu schnell. Formel → Flush-Analyse |
| Rack-Nachbau Fallback 220 / 360 (`nachbau/filament_rack.py` `get_material_target_temp(fallback_temp=220)`, `get_material_target_speed(fallback_velocity=360)`) | gleich (B) | keine; Kreuzprobe `material_db.py` grün, auch für `nozzle_temperature = null` |
| `nachbau/box.py` „mat_id 101001 = Hyper PLA“ | `type[1:6]` = 01001 = Hyper PLA (Default-DB) | bestätigt |
| `check_rfid_valid` vergleicht `rfid[12:17]` | = `mat_id[1:6]` (`RFID_VENDOR_FIELDS`: mat_id ab Index 11) | konsistent |
| Pi-Gleich-Material über RFID `mat_id` + Farbe (KB `rfid_material`) | Stock-Familienvergleich über `meterialType` (Teilstring) | Pi strenger (gleiche ID), unkritisch |

---

## C. Folgen für pi-host (nur Vorschläge, keine Code-Änderung)

| # | Vorschlag | Priorität | Begründung |
|---|---|---|---|
| P1 | **Materialabhängige Wechsel-/Nachhol-Temperatur.** `k2_box._change_temp` (KB:2004): ohne `TEMP=` → `max(Ziel, nozzle_temperature(Ziel-Slot))`, Material über RFID-`mat_id` des Slots (`rfid_material`, `type[1:6]`), Fallback wie bisher `tn_extrude_temp` 220 + Meldung. Lookup-Logik: `nachbau/material_db.py` `MaterialDB.change_temp_proposal`. Für das Spülen ggf. `maxTemp` (Ergebnis der Flush-Analyse abwarten). | mittel-hoch (sobald Nicht-PLA im CFS) | B.5 Zeile 1; Stock-Log „get next material temp“ |
| P2 | **Materialabhängige RESUME-Vorextrusion.** `resume_prime_speed` aus dem Material im Kopf (zuletzt erfolgreich geladene TNN → RFID → DB): `vol/2,4·60·0,8`; unbekannt → 288 (Stock-Fallback) statt 460, oder 460 als Obergrenze behalten. Umsetzung z. B. als `k2_box`-Status `prime_speed`, den `_PI_RESUME_PRIME` (PI:892) liest. | mittel | A.4 „Vorextrusion“, B.5 |
| P3 | **Spül-/Einzugsgeschwindigkeit begrenzen:** `tn_extrude_velocity` 360 mit `min(360, vol/2,4·60)` deckeln (TPU 50, Silk 250). Abgleich mit dem Flush-Befund. | mittel (TPU/Silk) | B.5 Zeile 4 |
| P4 | **DB-Bereitstellung:** Option `[k2_box] material_database: <pfad>`; das Installationsskript kopiert die DB vom Drucker (Default oder Benutzer-Kopie). **Nicht ins Repo** (Creality-Datei, Benutzer-Kopie mit Cloud-Kennungen). Fehlt die Datei oder fehlen Felder (`null` wie in B.4) → heutige Festwerte, Meldung im Log. | Voraussetzung für P1–P3 | B.1, B.4 |
| P5 | **Cancel während langer Box-Schritte** prüfen: Upstream-Webhook-Cancel wartet auf das G-Code-Mutex (Stock bricht per `fast_stop_complete` ab, CR:142-145). Am Gerät messen, wie lange ein Mainsail-Cancel während `KTWO_BOX_EXTRUDE` (bis 90 s) bzw. Preload-Warten (200 s) braucht; ggf. Abbruch-Prüfung in den `k2_box`-Warteschleifen. `M112` bleibt der Not-Aus. | niedrig | A.2 |
| P6 | **Doku-Korrekturen** (Text): BOOTLOADER-HOST-BEFUND §5.2 letzter Absatz „ANNAHME: nein“ → widerlegt (CR:195-199), §5.3 → auf `pause_resume`-Ebene bestätigt, §6 erster Punkt → erledigt; NOTIZ §6a.7 Punkt 5 / §6a.8: „= Stock `resume_err`“ um „Stock einmalig, Pi bis `ERROR_CLEAR`“ ergänzen; NOTIZ §6a.10 und Kommentar PI:688: Stock-Verweise auf `config/F008_CR0CN240319C13/gcode_macro.cfg:761` bzw. `:885-892` umstellen; NOTIZ §6 Prime-Geschwindigkeit: Formel `vol/2,4·60·0,8` eintragen. | niedrig | A.5 |
| P7 | **Beibehalten** (bestätigt sinnvoller als Stock): `is_paused`-Prüfung in RESUME (PI:765), dauerhafte Sperre statt Einmalflag, Heizen im Schacht, Rückfahrt X/Y in Pausenhöhe, `{rawparams}` an `RESUME_BASE`, robuste Lüfter-Rückstellung. Stock-Fehler `s_value` (ST:937-938) und fehlende `is_paused`-Prüfung nicht übernehmen. | – | A.4 |

## D. Offen

1. Creality-`gcode_move.py`/`virtual_sdcard.py`/`gcode.py` liegen nicht vor: MOVE=1-Bahn
   (A: Upstream-gleich), `do_resume_status`, `invoke_action`/`fast_stop_complete`.
2. `pause_start`/`motor_cancel_print_start`: genaue Semantik (Disassembly `box_wrapper`
   @`0x1c53f0` ff., `motor_control_wrapper`).
3. Wirkung von `SET_LIMITS`/`RESTORE_LIMITS` (`box_wrapper.so`).
4. Wer `creality/userdata/box/material_database.json` erzeugt/aktualisiert und wann.
5. Box-Seite der DB-Nutzung im Detail (`BoxAction.get_material_*`, `get_flush_velocity`,
   `get_flush_temp`, Herkunft `default_filament_colour`) – Flush-Analyse.
6. Ob `resume_tnn` nach gescheitertem Nachholen erneut gesetzt wird (A.3 Punkt 4, V).
