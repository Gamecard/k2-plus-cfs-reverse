# NOTIZ – Düsenreinigung vor dem Z-Antasten (A4) und RESUME mit Vorextrusion (A5)

> **ENTWURF 16.10.2026 spät – am Gerät UNGETESTET.** Offline geprüft:
> Batch gegen Upstream-Klipper 9cb9975 (`arbeitsdateien-lokal/upstream-test-a45/`,
> `ERGEBNIS.txt`: 9 Läufe, Bahnprüfung aus den dekodierten MCU-Schritten
> 0 Fehler, Jinja2-Render aller Zweige 0 Fehler) und alle `pi-host/test_k2_*.py`
> grün. `config-pi/README.md` ist hier bewusst nicht geändert (dort steht in
> §2.2/§6 Punkt 5 noch „Düsenreinigung fehlt“ – gilt seit diesem Stand nicht
> mehr). Alles mit **ANNAHME** ist nicht belegt.
>
> **Ergänzung 17.10.2026 (§6a):** RESUME nach einem im Druck abgebrochenen
> CFS-Wechsel (Schneidhebel/Schacht) – Rückfahrziel = Druckposition vor dem
> Wechsel, Schneidbereich nur in X, PAUSE_STATE-Neusetzung. Offline geprüft:
> `arbeitsdateien-lokal/upstream-test-resumecut/` (7 Läufe, 83 Prüfungen,
> 0 Fehler), a45-Batch weiter 0 Fehler, `test_k2_box.py` 28/28.
>
> **Ergänzung 17.10.2026 spät (§6a.7–§6a.9):** RESUME nach abgebrochenem
> CFS-Wechsel ist GESPERRT, solange der Kopf-Filamentsensor leer meldet
> (sonst druckte RESUME „Luft“); optional (`[k2_box] resume_redo_change`,
> Default AUS) holt RESUME den abgebrochenen T<n>-Wechsel nach wie Stock
> `error_resume_process`. Bewusste Abweichungen vom Stock mit Belegen aus
> `BOOTLOADER-HOST-BEFUND-2026-10-17.md` §5: §6a.8. Offline: resumecut-Batch
> 11 Läufe / 151 Prüfungen, a45-Batch 0 Fehler, `test_k2_box.py` 34/34.

## 1. Was wo passiert

| Datei | Änderung |
|---|---|
| `macros.cfg` `_PI_PARAM` | neue Variablen: `nozzle_clean_enable/_z_min/_z_speed/_accel`, `resume_prime_extra/_speed/_min_temp`, `resume_cool_fan/_dwell`, `resume_travel_speed/_z_speed`, `pause_x/_y/_z/_pos_valid` |
| `macros.cfg` `_PI_NOZZLE_CLEAN` | **neu**: Düse am Silikonstreifen reinigen (Ersatz Stock `BOX_NOZZLE_CLEAN`) – Schacht → Abstreifen → Schacht verlassen; `ONLY=GO`, `LEAVE=0`, `VIA=AUTO\|BOX\|GCODE`, `WHY=` |
| `macros.cfg` `_PI_CLEAN_PATH` | **neu**: dieselbe Bahn als reiner G-Code (`PART=GO\|WIPE\|LEAVE`), Werte aus `[k2_box]` |
| `macros.cfg` `START_PRINT` | Reinigung vor dem zweiten `G28 Z`; am Ende über dem Schacht auf Drucktemperatur, dann abstreifen |
| `macros.cfg` `G29` | Reinigung zwischen `Z_TILT_ADJUST` und dem letzten `G28 Z` |
| `macros.cfg` `PAUSE` | merkt zusätzlich die Pausenposition (`pause_x/y/z`); seit 17.10. `CFS_RETURN_X/Y/Z` (Rückfahrziel aus k2_box, `pause_rebase`), verlässt Schneidbereich (nur X) bzw. Schacht (X160 → Y345) vor dem Parken (§6a) |
| `macros.cfg` `RESUME` | ruft `_PI_RESUME_PREPARE` (nur wenn pausiert), Lüfter zurück, dann `RESUME_BASE` + `KTWO_BOX_RESUME_FIX`; seit 17.10. spät nach einem CFS-Abbruch (`k2_box.change_aborted`) zuerst `KTWO_BOX_RESUME_CHECK MOVE=0\|1` (§6a.7) |
| `macros.cfg` `_PI_RESUME_PREPARE/_PRIME/_RETURN` | **neu**: Schacht → `M109` → Vorextrusion → Abkühlen → Abstreifen → Schacht verlassen → Rückfahrt (X/Y in Pausenhöhe, dann Z senkrecht) |
| `macros.cfg` `_PI_RESUME_REBASE` | **neu 17.10.**: nach CFS-Abbruch PAUSE_STATE an der erreichten Druckposition neu speichern (§6a) |
| `homing.cfg` `[homing_override]` | `_PI_NOZZLE_CLEAN WHY=G28` vor dem ersten Z-Antasten (nur wenn Z noch nicht referenziert) |

## 2. A4 – Reinigung vor dem Z-Antasten

### 2.1 Stock-Belege

| Fundstelle | Inhalt |
|---|---|
| `live-logs-2026-10-16/config-aktuell/sensorless.cfg:198-204` | in `[homing_override]`, nur wenn Z noch nicht referenziert (`x_axes[2] is not defined`): `BOX_GO_TO_EXTRUDE_POS`, `PRES_CHECK RUN=1`, `BOX_NOZZLE_CLEAN`, `PRES_CHECK RUN=0`, dann `SET_PIN extruder_fan 0` + `_HOME_Z` |
| `…/gcode_macro.cfg:516-523` (START_PRINT) | `G28` → `NOZZLE_CLEAR` → `M104/M190/M109 140` → `BOX_NOZZLE_CLEAN` → `NEXT_HOMEZ_NACCU` → `G28 Z` |
| `…/gcode_macro.cfg:529-538` (START_PRINT-Ende) | `BOX_GO_TO_EXTRUDE_POS` → `M190/M109` Drucktemperatur → … → `BOX_NOZZLE_CLEAN` |
| `…/gcode_macro.cfg:1005-1015` (G29) | `G28 Z` → `NOZZLE_CLEAR` → Heizen → `G28 Z` (optisch) → `Z_TILT_ADJUST` → `NEXT_HOMEZ_NACCU` → `G28 Z` |
| `…/box.cfg:23-37` + T0-Beispiel (`box.cfg:220-275`) | Schacht `extrude_pos 133/378`, Einfahrt `G0 Y345 / X139 / Y378 / X133` (6–6.3), Abstreifen 10.1–10.3: Blech `X160 → X135` (Y378), Kreise `G0 X160 Y374 / G2 I4 J0 P1 F10000`, `G0 X170 Y374 / G3 I-4 J0 P1 F10000`, 3× `X160 ↔ X133` (Y378), raus `G0 X160 / G0 Y345` (11) |
| `rootfs-live/…/box_wrapper.cpython-39.so` (strings) | `BoxAction.nozzle_clean`, `G2 I4 J0 P1 F10000`, `G3 I-4 J0 P1 F10000`, `G0 X%.2f Y%.2f F12000`, `clean_pos_left/right/middle`, `clean_offset_pos`, `temp_pos:%s` → `BOX_NOZZLE_CLEAN` fährt die Silikon-Kreise des T0-Beispiels |
| `live-logs-2026-10-15/klippy.log.2026-10-12:3364` ff. | `BOX_NOZZLE_CLEAN` mit `temp_pos:1.04…`, Dauer 3,0 s (00:05:02,9 → 00:05:05,9) bzw. 4,7 s; `temp_pos` in allen Logs 0,01…2,88 |
| `klippy.log.2026-10-12:2992-2995` | `[PRES_CHECK_INFO_CH0] min=-1442966 max=-1405431 avg=-1413520 dif=37535 len=13901`, `SUCCESS: Strain gauge dynamic detection passed` – Messung läuft WÄHREND des Abstreifens (RUN=1 vorher, RUN=0 nachher) |
| Vanilla `lokale Analyse-Dateien:23-35`, `start_print.cfg:51-57` | `NOZZLE_CLEAN` hebt vorher 5 mm („Box-Modul wischt auf aktueller Höhe“), Start: `G28 Z` → `NOZZLE_CLEAN` → `G28 Z` („Cleaning preloads the strain gauge“) |

### 2.2 Umsetzung `_PI_NOZZLE_CLEAN`

- **Bahn = Stock-T0-Beispiel**, alle Werte aus `[k2_box]` (k2-module.cfg, eine Quelle):
  Schacht `Y345 → X139 → Y378 → X133` (`KTWO_BOX_GO_EXTRUDE_POS`), Abstreifen
  (`KTWO_BOX_WIPE`: Blech, 2 Kreise r 4 als 16-Eck, 3× Schütteln, endet bei
  133/378), Schacht verlassen `X160 → Y345` (`KTWO_BOX_SAFE_POS`). Das
  Verlassen über X160/Y345 macht auch Stock-PAUSE (`BOX_MOVE_TO_SAFE_POS`,
  `gcode_macro.cfg:775`) – danach diagonal zur Bettmitte ist kollisionsfrei.
- **Wiederverwendung `KTWO_BOX_*` (VIA=BOX)** außerhalb eines Drucks bzw. mit
  voller CFS-Freigabe. **Im Druck** verlangt k2_box für JEDEN Schritt
  `allow_write` UND `allow_move` und pausiert sonst (setzt `chain_error`,
  RESUME wäre gesperrt) – `_print_active()` zählt dabei `print_stats`
  printing/paused **oder** `pause_resume.is_paused` (k2_box-Stand 16.10. spät).
  Die Reinigung braucht aber nur `allow_move`. Deshalb dann **VIA=GCODE**:
  `_PI_CLEAN_PATH` erzeugt dieselbe Bahn aus denselben `[k2_box]`-Werten
  (Defaults = k2_box `CHANGE_DEFAULTS`). Ebenso bei offener Wechselkette
  (`chain_active`). Nachweis Gleichheit: render-a45.py (Zeile für Zeile ==
  `k2_box.gcode_go_extrude_pos/gcode_wipe/gcode_safe_pos`) und Batch
  (BOX ↔ GCODE an 23 Ecken max. 0,044 mm Unterschied).
- **Freigaben:** `_PI_FREIGABE allow_move` UND `[k2_box] allow_move`, X/Y
  referenziert; sonst Meldung `… übersprungen: <Grund> – keine Bewegung.` und
  weiter (G28 tastet dann OHNE Reinigung an). Nie Fehler, nie Shutdown.
  Global abschaltbar: `_PI_PARAM nozzle_clean_enable: False`.
- **Z:** ist Z referenziert und `< nozzle_clean_z_min` (5), zuerst `G1 Z5`
  (Bett runter; Stock wischt bei Z5: NOZZLE_CLEAR-Log 12.10. 00:05:28
  `record_z_pos:5.0` vor `BOX_GO_TO_EXTRUDE_POS`; Vanilla +5 mm). Beim ersten
  G28 ist Z nicht referenziert → nur X/Y wie Stock (beim K2 fährt das Bett,
  Schacht/Silikon sitzen am Rahmen).
- **Kein Heizen, kein Extrudieren.** Beim Homing ist die Düse so warm, wie sie
  gerade ist (Stock G28 heizt ebenfalls nicht; START_PRINT/G29 reinigen bei
  140 °C nach `M109`).
- Beschleunigung für die Fahrt `M204 S2000` (Stock `_IF_MOVE_XY`/`_HOME_Z`),
  das Abstreifen setzt `wipe_accel` 5000 und zurück; am Ende wieder die
  Beschleunigung von vorher. `SAVE/RESTORE_GCODE_STATE` (ohne Fahrt) um alles.

### 2.3 PRES_CHECK – weggelassen (begründet)

Stock-`PRES_CHECK RUN=1/0` ist ein **dynamischer** Dehnungsstreifen-Test über
das ganze Abstreifen (≈ 13 900 Samples, `dif` muss groß sein: 37 535 bzw.
55 285 → „dynamic detection passed“). Dafür bräuchte es eine lange
MCU-Aufzeichnung parallel zur Fahrt, die `k2_prtouch` nicht hat
(`KTWO_PRTOUCH_READ` liest nur ein 64er-Ruhefenster und meldet nur). Ersatz,
schon vorhanden und strenger fürs Antasten: `k2_prtouch` prüft vor JEDER
Antastung den Signalweg (`sta_swap` 0/1 → Endstop PC7) und nach dem
Scharfschalten die Ruhe (`dif <= rest_max_dif` 3000) und fährt sonst nicht
(`k2_prtouch.py` `arm()`). Ein zusätzlicher `KTWO_PRTOUCH_READ` im Homing
brächte nur eine Logzeile (und im Batch eine MCU-Abfrage ohne Antwort).

## 3. NOZZLE_CLEAR (Bett-Reiben mit Kraftsensor) – weggelassen (begründet)

Belegter Ablauf (klippy.log.2026-10-12:3313-3734, 00:04:52–00:06:03, identisch
klippy.log.2026-10-13:13869-14689):

1. `BED_MESH_CLEAR`, Lüfter aus, `BOX_GO_TO_EXTRUDE_POS`, `M104 S180`,
   `TEMPERATURE_WAIT 175..185` (`prth_clr_tnoz [140,180]`), `BOX_NOZZLE_CLEAN`.
2. Drei Antastungen am Bettrand Y355 bei X 155,83/158,83/161,83 bzw.
   144,27/147,27/150,27 (`prth_clr_probe_pos 150,355` + wechselnder Versatz,
   3 mm Raster) → Kontakt Z 0,22–0,28.
3. `G1 X185 Y355.5` (`prth_clr_pose [185,355.5,10]`), dann **Düse 0,6 mm unter
   die Kontakthöhe drücken** (`G1 … Z-0.35` bzw. `Z-0.31`; `prth_clr_zpre -0.6`),
   Lüfter an (`M106 S127`, `M106 P2 S255`), `M109 S170`, Z5, Schacht, wieder
   auf Z-0,31/-0,35, `M109 S140` (≈ 27 s unter Last abkühlen).
4. `G1 X205 Y355.5 Z5 F300` – Düse langsam schräg abziehen (Rückstand bleibt
   am Bett), Schacht, `BOX_NOZZLE_CLEAN`, `BED_MESH_PROFILE LOAD="default"`.

Nicht übernommen, weil nicht **sicher** nachbaubar: (a) der Schritt drückt die
Düse aktiv **unter** die gemessene Bettoberfläche – das setzt eine am Gerät
validierte Probe voraus (`[k2_prtouch] allow_move: False`, im Batch nicht
prüfbar, README §5.2 Lauf E); (b) die genaue Formel der Drücktiefe ist nicht
eindeutig: 12.10. Kontakt 0,22/0,23/0,25 → geloggt −0,35 (= Max − 0,6),
13.10. Kontakt 0,2725/0,2775/0,27 → geloggt −0,31 (Max − 0,6 wäre −0,3225,
Mittel − 0,6 −0,327); unklar sind auch der zweite Absenkvorgang nach dem
Schachtbesuch sowie `prth_clr_rhld 2`, `prtouch_clr_sped 200`,
`prtouch_clr_cnt 14`; (c) Fehlbedienung beschädigt Bett/Düse. Stattdessen
reinigt der Pi überall am Silikonstreifen (bei 140 °C). Später nachrüstbar,
sobald `PROBE`/`PROBE_ACCURACY` am Gerät stabil sind (README §4 Schritt 7).
`NEXT_HOMEZ_NACCU` (prtouch-Umschaltung Grob/Fein-Antasten) hat in Upstream
kein Gegenstück und entfällt.

## 4. A5 – RESUME mit Vorextrusion und Reinigung

### 4.1 Stock-Belege

| Fundstelle | Inhalt |
|---|---|
| `…/gcode_macro.cfg:882-942` `RESUME_EXTERNAL` | Temperatur zurück (`M109`, wenn kälter), Lüfter, `BOX_GO_TO_EXTRUDE_POS` (903), Z-Rücknahme `z_safe-2` (Zeile auskommentiert → wirkungslos), `E = PAUSE.extrude + 80.0` (913), `E_VELOCITY = filament_rack.remain_material_velocity * 0.8` (914), `M106 S0`, `G1 E{E} F{E_VELOCITY}` (932), `M106 S127` (934), `G4 P3000` (935), `BOX_NOZZLE_CLEAN` (936), Lüfter zurück, `SET_LIMITS`, `extruder_fan` an; danach `RESUME_BASE` |
| `…/gcode_macro.cfg:737-783` `PAUSE_EXTERNAL` | Rückzug `E-1 F180` + `E-{extrude 2} F360`, `M104 S140`, Z-Hub (`z_safe`), Schacht + `BOX_NOZZLE_CLEAN` + `BOX_MOVE_TO_SAFE_POS` |
| `klippy.log.2026-10-05:66250` ff. | RESUME live: `velocity: 460.0`, `Extruder not hot enough, SET M109 S220` (02:17:01,9), Heizen bis ≈02:17:45, dann Vorextrusion + 3 s, `BOX_NOZZLE_CLEAN` 02:17:55,7 |
| `klippy.log.2026-10-12` 00:10/00:14/00:16, 02:35 | `velocity: 460.0` (3×), `velocity: 280.00…` (1×, anderes Material) |
| Upstream `klippy/extras/pause_resume.py` | `PAUSE`: `SAVE_GCODE_STATE NAME=PAUSE_STATE`; `RESUME`: `RESTORE_GCODE_STATE NAME=PAUSE_STATE MOVE=1 MOVE_SPEED=<VELOCITY oder recover_velocity 50>` |
| Upstream `klippy/extras/gcode_move.py` `cmd_RESTORE_GCODE_STATE` | stellt Modus/Basis wieder her, korrigiert die E-Basis um die seitdem geförderte Länge (`e_diff`), MOVE=1 = **eine gerade Fahrt X/Y/Z gleichzeitig** zur gespeicherten Position |
| Vanilla `macros.cfg:180-205` `_RESUME_FINISH` | Temperatur abwarten, `G1 E30 F300`, Lüfter, `RESUME_BASE` |

### 4.2 Upstream-Semantik und Rückfahrt

`RESUME_BASE` fährt **schräg** (X/Y/Z zugleich) von der aktuellen Position zur
Pausenposition. Steht der Kopf nach dem Reinigen bei X160/Y345 und hoch, sinkt
Z während der ganzen Fahrt über das Objekt – das Objekt ist zwar nie höher als
die Pausenhöhe, aber der Weg ist unnötig knapp. Deshalb fährt
`_PI_RESUME_RETURN` vorher selbst: (1) falls nötig erst auf mindestens
Pausen-Z, (2) X/Y in dieser Höhe (Pausenhöhe = Pausen-Z + `z_safe`, z. B.
50 mm), (3) Z senkrecht auf Pausen-Z. `RESUME_BASE` macht danach nur noch eine
Nullfahrt und stellt Modus/E-Basis her (Batch: G-Code-E nach RESUME = vor
PAUSE, E-Basis +79 = 82 − 3). Die Pausenposition merkt sich `PAUSE`
(`gcode_move.gcode_position` beim Rendern = Position von `PAUSE_BASE`).
Sonderfälle: Pausenposition im Schacht (Y > `safe_pos_y`) → Einfahrt wie
`KTWO_BOX_GO_EXTRUDE_POS` (Y345 → X139 → Y → X). Pause aus einem im Druck
abgebrochenen CFS-Wechsel → seit 17.10. ist die „Pausenposition“ die
Druckposition VOR dem Wechsel, Schneidbereich nur in X, PAUSE_STATE wird
danach neu gesetzt (§6a); Ziel am Schneidhebel → RESUME verweigert.

### 4.3 Ablauf `RESUME` (nur wenn pausiert)

1. `chain_error` offen → `RESUME gesperrt …` (unverändert).
1a. (17.10. spät) Pause aus einem abgebrochenen CFS-Wechsel
   (`k2_box.change_aborted`): `KTWO_BOX_RESUME_CHECK MOVE=1|0` VOR jeder
   Bewegung – Kopfsensor leer → RESUME gesperrt bzw. Nachholen (§6a.7).
2. `_PI_RESUME_PREPARE` – Voraussetzungen: X/Y/Z referenziert,
   `_PI_FREIGABE allow_move`, `[k2_box]` mit `allow_move`, Pausenposition
   bekannt. Sonst nur `M109 S<Pausentemperatur>` + Meldung, Rückweg über
   `RESUME_BASE` wie bisher – **außer** (seit 17.10., §6a) die Pause stammt
   aus einem abgebrochenen CFS-Wechsel (`pause_rebase`) oder der Kopf steht
   im Schneidbereich: dann `RESUME gesperrt …` ohne jede Bewegung. Ziel am
   Schneidhebel (X ≤ `check_cut_pos_x_max`) → immer gesperrt.
   0. (17.10.) Kopf im Schneidbereich → zuerst `G1 X<pre_cut_pos_x>` + `M400`.
   1. `_PI_NOZZLE_CLEAN ONLY=GO` – in den Abfallschacht (im Druck/Pause
      VIA=GCODE, s. §2.2), Tropfen beim Aufheizen fallen dort hinein.
   2. `M109 S<hotend_temp>` (aus PAUSE).
   3. `_PI_RESUME_PRIME` – nur wenn die Düse **im Schacht** steht (±0,5 mm,
      sonst nie: Meldung) und `can_extrude` UND Temperatur ≥
      max(`resume_prime_min_temp` 180, `min_extrude_temp`); dann `M107 P0`,
      `G1 E{pause_extrude + resume_prime_extra} F{resume_prime_speed}`
      (= 82 mm mit 460 mm/min), `M106 P0 S127`, `G4 P3000`.
   4. `_PI_NOZZLE_CLEAN` – Abstreifen + Schacht verlassen.
   5. `_PI_RESUME_RETURN` – §4.2 (bricht ab, wenn ein CFS-Schritt der
      Vorbereitung `chain_error` gesetzt hat).
   6. (17.10.) nur bei `pause_rebase`: `_PI_RESUME_REBASE` (§6a).
3. Lüfter wie vor der Pause (fan0 sonst `M107 P0`, damit der Abkühl-Lüfter
   aus ist), `extruder_fan` an.
4. `RESUME_BASE {rawparams}`, `KTWO_BOX_RESUME_FIX` (Z-Hop nach CFS-Abbruch).

Ist der Druck nicht pausiert, macht RESUME nur `RESUME_BASE` (meldet „not
paused“) + `KTWO_BOX_RESUME_FIX` – keine Lüfter-/Extruderaktion.

## 5. Nachweis (offline)

- Batch `arbeitsdateien-lokal/upstream-test-a45/` (`lauf-a45.sh`,
  `LIESMICH.txt`, `ERGEBNIS.txt`): `lauf-basis` EXIT 0; `lauf-gesperrt`
  (Auslieferung) 0 Schritte, Meldungen „übersprungen: _PI_FREIGABE
  allow_move=False“, „G28 gesperrt“, kein Shutdown; `lauf-ohnebox`
  (`[k2_box] allow_move: False`) G28 ohne Reinigung, nie im Schacht, keine
  Vorextrusion; `lauf-g28` Reinigungsbahn (24 Stock-Ecken, max. 0,13 mm
  Input-Shaper-Verrundung) **vor** der ersten Z-Bewegung (Bettmitte 175/175),
  während der Reinigung keine Z-Fahrt; `lauf-gcodepfad` BOX == GCODE;
  `lauf-start`/`lauf-g29` Reinigung zwischen 1. Z-Antasten (+5 mm danach) und
  2. `G28 Z`, START_PRINT-Ende Schacht + Abstreifen; `lauf-resume` Park
  342,5/200 → Schacht → **Vorextrusion 82,00 mm mit 7,65 mm/s im Schacht
  (X133/Y378 ±0,009 mm)** → 3 s → Abstreifen → raus → X110/Y120 in Z50 → Z
  senkrecht auf 10, **nie unter Z10**, Endlage = PAUSE_STATE; `lauf-sd`
  START_PRINT im laufenden Druck (`virtual_sdcard`, print_stats „printing“):
  4× VIA=GCODE, keine k2_box-Pause. Alle Freigabe-Läufe ohne
  k2_box-Fehler/Shutdown.
- TEST-Anpassungen nur in den Batch-Kopien (Probe → Pin-Endstop PC10,
  `min_extrude_temp: 0`, Stubs `Z_TILT_ADJUST`/`BED_MESH_CALIBRATE`, Fake-Bus
  für den Stall-Mode) – s. `LIESMICH.txt`.
- `render-a45.py`: alle Zweige (Wegwahl je Druck-/Pausenzustand, Sperren,
  Z-Anhebung, kalte Düse, Pause im Schacht/Schneidbereich, PAUSE merkt
  Position, Reihenfolgen START_PRINT/G29/homing_override) 0 Fehler.
- `pi-host/test_k2_*.py` grün (u. a. `test_k2_box` rendert RESUME).

## 6. ANNAHMEN / offen

- **Prime-Geschwindigkeit** fest 460 mm/min (Stock materialabhängig aus
  `filament_rack.remain_material_velocity · 0,8`, live 460 bzw. 280).
- **`resume_prime_min_temp` 180 °C** – eigene Schranke, kein Stock-Wert.
- **Abstreif-Versatz:** Stock verschiebt die Wischstelle offenbar um
  `temp_pos` 0…3 mm (`clean_offset_pos`; Werte sehen nach `monotonic % 3` aus);
  hier immer dieselbe Stelle (k2_box-Bahn). Silikon nutzt sich ggf. punktuell ab.
- **Reinigung beim ersten G28 ohne Heizen** wie Stock; ob Rückstände an einer
  kalten Düse das Antasten stören, ist am Gerät zu beobachten (START_PRINT/G29
  reinigen ohnehin noch einmal bei 140 °C vor dem maßgeblichen `G28 Z`).
- **PAUSE parkt weiter bei X max−10 / Y Mitte** (unverändert, Stock parkt am
  Schacht/X160 Y345). RESUME heizt deshalb erst im Schacht wieder auf.
- ~~**Pause aus einem abgebrochenen CFS-Wechsel:** Upstream-`PAUSE_STATE`
  hält die Fehlerposition … `RESUME_BASE` fährt dorthin zurück~~ –
  **gelöst 17.10.2026 (offline), §6a.** Am Gerät bleibt zu prüfen: §6a.6.
- **Wegwahl BOX/GCODE** spiegelt `k2_box._print_active()` (Stand 16.10. spät:
  print_stats printing/paused ODER `pause_resume.is_paused`). Ändert k2_box
  das erneut, muss `_PI_NOZZLE_CLEAN` mitgezogen werden (sonst pausiert k2_box
  und setzt `chain_error`); `render-a45.py`/Batch fangen das ab.
- **Positionen** (Schacht, Silikon) sind Stock-Werte aus `[k2_box]`; wegen
  `FORCE_MOVE stepper_y -3` (NOTIZ-stall.md §4) am Gerät erst langsam mit
  `KTWO_BOX_GO_EXTRUDE_POS`/`KTWO_BOX_WIPE`/`KTWO_BOX_SAFE_POS` einzeln prüfen
  (NOTIZ-cfs.md §2 Schritt 2), dann `_PI_NOZZLE_CLEAN`, dann G28.

## 6a. RESUME nach abgebrochenem CFS-Wechsel im Schneidbereich (17.10.2026)

> **ENTWURF – am Gerät UNGETESTET.** Offline: Batch
> `arbeitsdateien-lokal/upstream-test-resumecut/` gegen Upstream 9cb9975
> (`ERGEBNIS.txt`: 7 Läufe, Bahnprüfung aus den dekodierten MCU-Schritten
> 83/83), `pi-host/test_k2_box.py` 28/28 (3 neue Prüfungen), a45-Batch
> weiter 0 Fehler (eine Render-Erwartung bewusst geändert, s. 6a.5).
> Alles weiter gesperrt ausgeliefert.

### 6a.1 Problem

Bricht ein Wechsel im laufenden Druck ab, pausiert k2_box (`_fail` →
`PAUSE`). Upstream-`PAUSE_BASE` speichert dabei `PAUSE_STATE` an der
**Fehlerposition** (z. B. am Schneidhebel X −7,80 / Y200, oder im Schacht
X133/Y378, jeweils Druck-Z + Hop 0,4). `RESUME_BASE` = `RESTORE_GCODE_STATE
NAME=PAUSE_STATE MOVE=1` fährt **gerade** (X/Y/Z zugleich) dorthin zurück
(Upstream `pause_resume.py` `cmd_RESUME`, `gcode_move.py`
`cmd_RESTORE_GCODE_STATE`) → schräg in den Hebel / gegen den Rahmen, und der
Druck ginge danach an der Fehlerposition weiter (der nächste Slicer-Befehl
erwartet aber die Druckposition vor `T<n>`). Zusätzlich parkte PAUSE schräg
aus Hebel/Schacht heraus (`G1 X342,5 Y200`).

### 6a.2 Stock-Belege (Maßstab)

| Fundstelle | Inhalt |
|---|---|
| `live-logs-2026-10-16/config-aktuell/box.cfg:13` | `pre_cut_pos_x: 10 #预撞刀的位置，保证垂直撞刀` – Vor-Schneidposition, „Hebel senkrecht anfahren“ → am Hebel nur X-Bewegung |
| `…/box.cfg:226-237` (T0-Beispiel 3.–6.1) | `G0 X10 Y200` → `M8200 C` (Schnitt) → „6. zuerst Y“ `G0 Y345` (bei X10) → `G0 X139` („此位置直行无碰撞风险“, gerade ohne Kollisionsrisiko) |
| `live-logs-2026-10-15/klippy.log.2026-10-12:13144/13156` | `[box] cut sensor detected` → 3 s → `[box] cut to return OK` (Schnitt endet wieder an der Vor-Schneidposition, k2_box `gcode_cut`: `G0 X10` + `M400`) |
| `…/box.cfg:39-40` | `check_cut_pos_x_max: -5.5`, `check_cut_pos_x_min: -9.5` – Bereich des Schneidanschlags (Kalibrierung zählt erst dort Treffer, NOTIZ-cutcal §1) |
| `…/box.cfg:268-270` (T0-Beispiel 11.) | Schacht verlassen `G0 X160` / `G0 Y345` |
| `…/box.cfg:273-274` (T0-Beispiel 13.) | „Z wiederherstellen (oder zurück zum Modell, **dann** Z)“ |
| `…/gcode_macro.cfg:759-775` | Stock-PAUSE: `M400`, Rückzug, Z-Hub, dann `BOX_GO_TO_EXTRUDE_POS` / `BOX_NOZZLE_CLEAN` / `BOX_MOVE_TO_SAFE_POS` (nicht schräg zum Parkpunkt) |
| `…/gcode_macro.cfg:870-881` | Stock-RESUME: `RESUME_EXTERNAL_PROCESS` → `BOX_ERROR_RESUME_PROCESS` → `RESUME_EXTERNAL` → `RESUME_BASE` |
| `box_wrapper.cpython-39.so` (strings) | `save_resume_tnn`, `clear_resume_tnn`, `resume_flag`, `[error_resume_process] state is not paused` / `resume_tnn is None` / `filament sensor is detected` → Stock merkt sich die TNN des abgebrochenen Wechsels und holt ihn beim RESUME nach – seit 17.10. spät **belegt** (Disassembly, `BOOTLOADER-HOST-BEFUND-2026-10-17.md` §5.1/§5.2, s. §6a.8) |
| `klippy.log.2026-10-12:51987-51988`, `klippy.log.2026-10-05:66249`, `klippy.log.2026-10-12:88007` | `error: empty_print, tnn: T1A` → `set resume_flag`; RESUME: `[error_resume_process] resume_tnn is None` bzw. `filament sensor is detected` – **kein** Log eines Fehler-Resume mit Rückfahrt aus dem Schneidbereich → Stock-Rückfahrziel nach Box-Fehler **unbelegt** |

### 6a.3 Lösung (gewählt: Startposition als Rückfahrziel + Schneidbereich nur in X)

1. **k2_box `_fail`** (Fehler im laufenden Druck, Pause wird DADURCH
   ausgelöst): `PAUSE CFS_RETURN_X/Y/Z=<Druckposition vor dem Wechsel>` –
   `start_pos` aus `PRE_OPT` (vor dem Z-Hop) bzw. die Position vor einem
   Einzelschritt ohne Kette (z. B. `M8200 C` ohne `P`, `_step_start`).
   Kein Ziel, wenn der Kopf noch dort steht (±0,01) oder die Kette in einer
   Pause begann (Wiederholung von Hand, Runout-Kette: dort ist PAUSE_STATE
   schon die Druckposition) – dann auch kein `pending_resume_z` mehr (Fix:
   früher Z-Korrektur auf Park-Z).
2. **PAUSE-Makro**: übernimmt `CFS_RETURN_*` als `pause_x/y/z`,
   `pause_rebase: True`, Meldung „Fehlerposition … Rückfahrziel …“, ruft
   `KTWO_BOX_RESUME_FIX CLEAR=1` (Z steckt im Ziel; zugleich keine Z-Altlast
   aus einer per CANCEL beendeten Pause). Parken: Kopf im Schneidbereich
   (X < `pre_cut_pos_x`) → zuerst `G90` + `G1 X10` (nur X, vor Rückzug und
   Z-Hub); Kopf im Schacht (Y > `safe_pos_y`) → nach dem Z-Hub `G1 X160`,
   `G1 Y345`; dann wie bisher `X max−10 / Y Mitte`.
3. **RESUME** (`_PI_RESUME_PREPARE`) – Sperren VOR jeder Bewegung (auch vor
   `M109`): Ziel X ≤ `check_cut_pos_x_max` → `RESUME gesperrt` (RESUME_BASE
   führe in den Hebel); `pause_rebase` oder Kopf im Schneidbereich ohne
   Bewegungsfreigabe/Referenz → `RESUME gesperrt` (Druck bleibt pausiert).
   Kopf im Schneidbereich → zuerst `G1 X10` + `M400`. Dann Schacht → Heizen →
   Vorextrusion → Abstreifen → raus → `_PI_RESUME_RETURN` (X/Y in Pausenhöhe,
   Z senkrecht; Ziel im Schneidbereich an der Bettkante → X/Y bis X10, hinein
   nur X; offener `chain_error` → Abbruch vor der Rückfahrt) →
   `_PI_RESUME_REBASE`: Kopf an `pause_x/y/z` (±0,01, sonst Abbruch ohne
   RESUME_BASE) → `RESTORE_GCODE_STATE NAME=PAUSE_STATE` (Modus, Basis,
   E-Basis + e_diff) + `SAVE_GCODE_STATE NAME=PAUSE_STATE` an der
   Druckposition → `RESUME_BASE` = Nullfahrt, G-Code-E = E vor dem Wechsel.
4. **Fahrten in den Schacht** (`KTWO_BOX_GO_EXTRUDE_POS`, `_PI_CLEAN_PATH
   PART=GO`): vom Schneidhebel aus erst `G0 X10` + `M400`, dann `Y345`
   (k2_box `gcode_leave_cut_zone`) – deckt den von Hand wiederholten
   Einzelschritt ab.
5. **END_OPT in einer Pause** (Wiederholungs-`T<n>`): Rückfahrt X/Y zur
   Startposition, Z aber NICHT unter die Pausenhöhe (sonst stünde die Düse
   auf Druckhöhe über dem Objekt und RESUME führe von dort quer zum Schacht);
   RESUME senkt. **Fix:** eine alte Startposition (`_keep_start_pos`) gilt
   nur noch für ein `T<n>` in derselben Pause – nach CANCEL/RESUME fuhr
   `END_OPT` sonst zur Position eines früheren Wechsels.

Unverändert: `chain_error`-Sperre (RESUME gesperrt bis
`KTWO_BOX_ERROR_CLEAR`), Wegwahl `_print_active()`/`VIA=GCODE` (§2.2),
`KTWO_BOX_RESUME_FIX` nach `RESUME_BASE` (mit unserem PAUSE-Makro jetzt
Nullfahrt bzw. nichts; Fallback für fremde PAUSE-Makros: Z-Korrektur wie
bisher). Verworfen: „RESUME fährt erst aus dem Schneidbereich, dann zur
Fehlerposition“ – die Fehlerposition ist kein sinnvolles Ziel (Druck ginge
dort weiter); sie bleibt nur in PAUSE_STATE bis zum Rebase.

### 6a.4 Nachweis (offline)

Batch `upstream-test-resumecut` (`LIESMICH.txt`, `ERGEBNIS.txt`), Fake-Bus
(Motoren für das Homing, CFS antwortet nicht) + TEST-Hook (Host-Druck,
Abbruch AM Anschlag): `lauf-schnitt` – Anschlag X −7,800 bei Z10,4, X < 9,4
nur bei Y200 (±1 Schritt), genau ein Besuch im Schneidbereich, hinaus nur in
X (Y/Z konstant), dann Z-Hub, Park, RESUME Schacht → Vorextrusion 82 mm →
Abstreifen → X/Y in Z50 → Z senkrecht auf 10, nie unter Z10, Endlage
X110/Y120/Z10 = Druckposition vor `T1`, G-Code-E = 1 wie vor `T1`, danach
nur der Druckweg X110 → X120 (RESUME_BASE ohne Fahrt); `lauf-schacht` –
Abbruch bei X133/Y378: Z-Hub vor dem Ausstieg, X160 → Y345 → Park, sonst wie
oben; `lauf-normal` – unverändert (kein Rebase, Endlage = PAUSE_STATE);
`lauf-wiederholung` – Abbruch eines `T1` IN der Pause: keine zweite Pause,
RESUME fährt erst nur X vom Hebel, Endlage = Druckposition; `lauf-sperre` /
`lauf-gesperrt` – RESUME verweigert, keine Bewegung, Druck bleibt pausiert.
Kein Internal error/Shutdown. `test_k2_box.py`: `_fail`-Ziel, Wiederholung,
Altlast `_keep_start_pos`, Einzelschritt, Schacht vom Hebel, `RESUME_FIX
CLEAR`, Render aller neuen Makrozweige.

### 6a.5 Geänderte Erwartung im a45-Batch (begründet)

`render-a45.py` „RETURN Pause im Schneidbereich: keine Fahrt, Meldung“ →
jetzt „Fehler statt Fahrt“: das alte Verhalten ließ RESUME_BASE danach
gerade in den Hebel fahren (genau der behobene Fehler). Alle übrigen
a45-Prüfungen unverändert grün (Bahn + Render 0 Fehler).

### 6a.6 ANNAHMEN / am Gerät prüfen

- **Hebelzone:** Der Hebel wird erst links von X ≈ −5 betätigt (Kalibrierstart
  `pre_cut_cal_pos_x` −5, Treffer ab −5,5); „Schneidbereich“ = X < 10 ist
  bewusst weiter gefasst. Verweigert wird nur ein Ziel X ≤ −5,5.
- **Druckpositionen** liegen nie links von X 0 (Bett) – ein Ziel in 0…10
  (Bettkante) wird nur in X angefahren.
- **Y-Ausdehnung des Hebels:** nur bei Y200 (`cut_pos_y`) belegt; ob eine
  Y-Bewegung bei X < 10 abseits Y200 gefahrlos ist, ist unbelegt – deshalb
  dort generell keine Y-Bewegung.
- **Abbruch mitten in der Schneidfahrt** ist selten (der Schnitt läuft als
  ein Script inkl. Rückweg); realistisch sind Abbrüche danach (Schacht).
  Der Batch erzwingt den Hebelfall per TEST-Hook.
- **SET_GCODE_OFFSET während einer CFS-Pause:** PAUSE_STATE-Basis gilt wie
  bei Upstream-RESUME; die Korrektur erfolgt mit dem nächsten Druckbefehl
  statt in RESUME_BASE (Rebase ohne Fahrt).
- ~~**Stock-Gleichwertigkeit:** Stock holt den Wechsel beim RESUME evtl.
  selbst nach (`resume_tnn`); hier weiter von Hand …~~ – **geklärt 17.10.
  spät:** Stock holt nach (BOOTLOADER-HOST-BEFUND §5.2, B); Pi sperrt RESUME
  bei leerem Kopf und holt optional nach (§6a.7, Abweichungen §6a.8).
  Reihenfolge von Hand: `KTWO_BOX_ERROR_CLEAR` → `T<n>` → `RESUME` (ein
  `T<n>` bei offenem Fehler lehnt PRE_OPT ab).
- **Am Gerät (Hand am Not-Aus):** Abbruch im Schacht provozieren (z. B. Box
  stromlos während `T1`), Parkweg und RESUME-Bahn beobachten; Hebelfall nur
  mit `KTWO_BOX_CUT` außerhalb eines Drucks + manuellem `G1 X10` prüfen.

### 6a.7 RESUME-Sperre bei leerem Kopf + optionales Nachholen (17.10.2026 spät)

> **ENTWURF – am Gerät UNGETESTET. Alles weiter gesperrt ausgeliefert**
> (`[k2_box] allow_write/allow_move: False`, `resume_redo_change: False`).

**Problem.** Nach einem abgebrochenen Wechsel kann der Kopf leer sein
(Filament geschnitten/zurückgezogen, das neue noch nicht geladen). Nach
`KTWO_BOX_ERROR_CLEAR` lief RESUME bisher normal weiter – ohne Filament
(„Luft“), falls niemand `T<n>` in der Pause wiederholt hatte. Stock holt den
Wechsel in diesem Fall selbst nach (BOOTLOADER-HOST-BEFUND §5.2).

**Umsetzung (`k2_box.py`, `macros.cfg`):**

1. `_fail` (Pausenpfad im Druck) merkt den Abbruch: Schritt, Ziel-TNN,
   Herkunft der Kette (`_record_abort`, Stock `recode_err` →
   `save_resume_tnn`). Status: `change_aborted`, `resume_tnn`,
   `head_filament` (True/False/None), `resume_redo_change`. Ein
   Einzelschritt ohne eigene TNN in derselben Pause behält Ziel und
   Herkunft; die Ablehnung eines `T<n>` bei offenem Fehler (PRE_OPT tut
   nichts) überschreibt sie nicht. Lebensdauer: bis zum Ende eines
   erfolgreichen RESUME (`KTWO_BOX_RESUME_FIX` ohne CLEAR) bzw. bis zu einer
   NEUEN Pause, die nicht aus `_fail` stammt (`RESUME_FIX CLEAR=1` aus dem
   PAUSE-Makro, Marker `_pause_from_fail`) – kein veralteter Merker nach
   CANCEL/neuem Druck.
2. Herkunft: `_KTWO_TOOL_CHANGE` ruft `KTWO_BOX_PRE_OPT … SOURCE=T`. Nur
   diese Kette (T<n>-Befehl) ist nachholbar – Stock setzt `resume_tnn` nur
   für Fehler im T<n>-Pfad, nicht für die M8200-Kette (`macro_error_list`,
   BEFUND §5.1). M8200-Kette/Runout/von Hand: Herkunft leer.
3. RESUME-Makro: ist `printer.k2_box.change_aborted` gesetzt (und der Druck
   pausiert), zuerst `KTWO_BOX_RESUME_CHECK MOVE=<1|0>` – VOR
   `_PI_RESUME_PREPARE` (das rendert erst danach) und vor jeder Bewegung.
   `MOVE=1` nur mit `_PI_FREIGABE allow_move`, X/Y/Z referenziert und
   bekannter Pausenposition (= PREPARE darf danach fahren). Ohne Abbruch
   bzw. nicht pausiert: kein Prüfbefehl, Ablauf unverändert.
4. `KTWO_BOX_RESUME_CHECK` liest den Kopfsensor wie k2_box überall:
   Upstream-`[filament_switch_sensor <filament_sensor>]` → `get_status()
   ["filament_detected"]` (`_sensor_state`). Upstream setzt diesen Zustand
   auch bei `SET_FILAMENT_SENSOR ENABLE=0` (`filament_switch_sensor.py`
   `note_filament_present`: `filament_present` wird VOR der
   `sensor_enabled`-Prüfung gesetzt) – B (Quelltext 9cb9975).
   - **Filament erkannt** → RESUME frei (Meldung; ob es das Ziel-Filament
     ist, prüft der Pi nicht – Stock ebenso, §5.2 Schritt 6).
   - **leer** → `RESUME gesperrt nach abgebrochenem CFS-Wechsel (<Schritt>
     fehlgeschlagen; Ziel T1B = T1): Kopf-Filamentsensor … meldet KEIN
     Filament … Druck bleibt PAUSIERT. In der Pause nachholen: T1 (lädt
     T1B), dann RESUME. Kein automatisches Nachholen: <Grund>.` Bei offener
     M8200-Kette zusätzlich: `KTWO_BOX_EXTRUDE TNN=…` als Kettenschritt.
   - **unbekannt** (`filament_sensor` leer bzw. Abschnitt fehlt) →
     **gesperrt** (sicherer Default: nach einem Abbruch ist offen, ob
     Schnitt/Rückzug den Kopf geleert haben; ein gesperrtes RESUME ist
     harmlos, „Luft“ ruiniert den Druck unbemerkt). Freigabe nur von Hand
     nach Sichtprüfung: `KTWO_BOX_ERROR_CLEAR HEAD_LOADED=1` (wirkt NUR ohne
     Sensorzustand; meldet der Sensor „leer“, wird die Bestätigung
     abgelehnt). Nachholen ohne Kopfsensor nie (Stock prüft ihn ebenso).
5. **Nachholen** (nur `resume_redo_change: True` UND `allow_write` UND
   `allow_move` UND `MOVE=1` UND Herkunft T UND Ziel-TNN bekannt UND keine
   offene Kette UND referenziert – sonst Sperre wie 4. mit Grund):
   `KTWO_BOX_PRE_OPT TNN=<Ziel> Z_HOP=0 SOURCE=T` → Schacht + `M400` +
   Heizen (TEMP = max(Drucktemperatur vor der Pause, `Tn_extrude_temp` 220),
   wie Runout) → `KTWO_BOX_RETRUDE` (nur wenn der Hub belegt ist; heiße Düse
   für die E−10-Schritte) → `KTWO_BOX_EXTRUDE` → `KTWO_BOX_WASTE` →
   `KTWO_BOX_FLUSH` → `KTWO_BOX_END_OPT RETURN=0`. Kein Schnitt (Kopf ist
   leer – Stock ebenso). Danach muss der Kopfsensor Filament melden; dann
   weiter wie jedes RESUME (`_PI_RESUME_PREPARE`: Schacht, M109,
   Vorextrusion, Abstreifen, Rückfahrt, Rebase = Stock `RESUME_EXTERNAL`).
   Jeder Fehler → `chain_error` (keine zweite Pause), `RESUME gesperrt:
   Nachholen des Wechsels … fehlgeschlagen … keine Rückfahrt` (= Stock
   `resume_err`); Merker bleibt, das nächste RESUME holt nach
   `KTWO_BOX_ERROR_CLEAR` erneut nach.
6. Nebenbei korrigiert: Anleitung in der `_fail`-Meldung und in der
   `chain_error`-Sperre des RESUME-Makros – richtig ist **erst
   `KTWO_BOX_ERROR_CLEAR`, dann `T<n>`**, dann RESUME (PRE_OPT lehnt ein
   `T<n>` bei offenem Fehler ab; vorher stand die umgekehrte Reihenfolge).

**Entscheidung zu „Nachholen“:** umgesetzt, Default AUS. Begründung: die
Bausteine sind dieselben wie beim Runout-Nachladen (PRE_OPT Z_HOP=0 in der
Pause → EXTRUDE → FLUSH → END_OPT, `test_k2_box`/Batch `upstream-test-runout`
geprüft); neu sind nur Gate, Heizschritt und Ergebnisprüfung (~150 Zeilen).
Jeder Fehler lässt den Druck pausiert und sperrt RESUME; Bewegung nur
Schacht/Abstreifen in Pausenhöhe (wie RESUME_PREPARE ohnehin). Nicht
nachholbar (bewusst): M8200-Kette (die Datei schickt nach RESUME selbst
`M8200 L/F/O`), Runout-Kette, Einzelschritte – dort nur die Sperre.

### 6a.8 Bewusste Abweichungen vom Stock (Belege `BOOTLOADER-HOST-BEFUND-2026-10-17.md` §5)

| Punkt | Stock (Beleg) | Pi | Begründung |
|---|---|---|---|
| Rückfahrziel | `BoxError.handle_event` @`0x1c3ca4`: `G91 / G0 Z-{move_z} / G90` (Hop zurück) → `BOX_MOVE_TO_SAFE_POS` (@`0x1184e0`: **erst `G0 Y345`, dann `G0 X225`**) → `PAUSE` → PAUSE_STATE = **X225/Y345 auf Druck-Z** (§5.1, B); `RESUME_BASE` fährt gerade dorthin (§5.3, **ANNAHME**: wie Upstream) | **Druckposition vor dem Wechsel** (`PAUSE CFS_RETURN_X/Y/Z`), X/Y in Pausenhöhe, Z senkrecht, PAUSE_STATE dort neu (§6a.3) | Stock senkt die Düse bei X225/Y345 auf Druck-Z und der nächste Slicer-Befehl fährt von dort auf Druck-Z zum Objekt (BEFUND §5.3 V: Kollisionsgefahr). Pi kehrt dorthin zurück, wo `T<n>` begann – konservativer. |
| Ausstieg Schneidbereich | kein Sonderfall: Z zuerst zurück auf Druck-Z, dann **Y zuerst**, dann X (§5.1/§5.4) | Schneidbereich (X < `pre_cut_pos_x` 10) **nur in X**, vor Rückzug und Z-Hub | Einziger Beleg zum Hebel ist `box.cfg:13` `pre_cut_pos_x` „预撞刀的位置，保证垂直撞刀“ (Hebel senkrecht anfahren) + Schnitt-Rückweg `G0 X10`; eine Y-Fahrt am Hebel ist unbelegt (§5.4: „im Stock-Fehlerpfad kein Beleg“) → vorsichtiger. |
| Ausstieg Schacht | Z-Hop zurück auf Druck-Z, dann Y345, X225 (§5.1) | erst Z-Hub (Pausenhöhe), dann X160 → Y345 (T0-Beispiel 11), dann Park | Kopf nie auf Druck-Z quer über das Bett. |
| Nachholen | **automatisch** in `RESUME` → `BOX_ERROR_RESUME_PROCESS` (`error_resume_process` @`0x7cd28`): CFS an, pausiert, `resume_tnn` gesetzt, Kopfsensor **leer** → `go_to_extrude_pos`, `box_retrude_material`, `box_extrude_material`, `extruder_extrude`, `material_change_flush`; Kopfsensor belegt → nichts (live `12:88007`); Fehler → `resume_err` (§5.2, B) | Kopf leer → **RESUME gesperrt** (Default); Nachholen nur mit `resume_redo_change: True` + Freigaben (§6a.7) | Default aus wie jede neue Bewegungsfunktion bis zur Gerätefreigabe; die Sperre allein verhindert schon den Druck ohne Filament (BEFUND §5.4 Empfehlung 4). |
| Fehler quittieren | `error_resume_process` Schritt 5 `clear_err()` – RESUME quittiert selbst (§5.2) | `KTWO_BOX_ERROR_CLEAR` bleibt Pflicht vor RESUME | bewusste Quittung nach Ursachenbehebung (Box-Fehler 0x04 [00 01]). |
| Nachholen-Umfang | nur T<n>-Pfad (`resume_tnn` nur, wenn der Fehler nicht in `macro_error_list` steht = M8200-Kette) (§5.1, B) | gleich: nur Herkunft `SOURCE=T`; M8200-Kette/Runout/Einzelschritt nur Sperre | M8200: die Datei schickt nach RESUME die restlichen Kettenbefehle selbst. |
| Kopfsensor unbekannt | unbelegt (Stock hat ihn immer) | gesperrt bis `KTWO_BOX_ERROR_CLEAR HEAD_LOADED=1` | sicherer Default (§6a.7 Punkt 4). |

### 6a.9 Nachweis (offline) und ANNAHMEN

- `pi-host/test_k2_box.py` 34/34 (6 neue Prüfungen): Kopf leer → gesperrt
  (keine Bewegung, kein Box-Frame, Meldung „T0 (lädt T1A)“), `HEAD_LOADED`
  gegen „leer“ abgelehnt, nach `T0` in der Pause frei; Kopf voll → frei;
  Sensor fehlt/leer konfiguriert → gesperrt, `HEAD_LOADED=1` gibt frei;
  Merker-Lebensdauer (PAUSE aus `_fail`, abgelehnte PRE_OPT, Einzelschritt,
  neue Pause, RESUME-Ende, ohne Druck); Nachholen (PRE_OPT Z_HOP=0 → Schacht
  → M109 235 → Rückzug A mit heißer Düse → Einzug A → Stage 7/m7/PRINT →
  Spülen → raus, kein Schnitt/Z/Rückfahrt) inkl. Fehlerfall; alle
  Nachhol-Sperrgründe; RESUME-Makro rendert `KTWO_BOX_RESUME_CHECK MOVE=1/0`
  nur nach Abbruch. Nebenbei: Test-Helfer `_render_env` las `[k2_box]` aus
  einem Kommentar (kcfg leer) – korrigiert, Render jetzt mit echten Werten.
- Batch `arbeitsdateien-lokal/upstream-test-resumecut/` (11 Läufe, 151/151):
  `lauf-kopfleer` (Kopf leer → RESUME verweigert, nach dem Park keine
  Bewegung, Extruder nur +1/−3, EXIT 255 gewollt), `lauf-redo` (Nachholen:
  Park → Schacht in Z50, Box antwortet nicht → verweigert, danach keine
  Bewegung, keine zweite Pause), `lauf-ohnesensor` (verweigert, „UNBEKANNT“),
  `lauf-ohnesensor-ok` (`HEAD_LOADED=1` → RESUME normal bis X110/Y120/Z10);
  schnitt/schacht/wiederholung/sperre mit Kopf voll unverändert grün
  („RESUME frei“). Kopfsensor im Batch per TEST-Hook `KTWO_TEST_HEAD`.
  a45-Batch unverändert 0 Fehler; Gesamtlauf-Kopie
  `upstream-test-resume2-gesamt/` Fehlerbild wie bisher.
- **ANNAHME:** Der Kopfsensor PA11 meldet „leer“, sobald das Filament nach
  dem Abbruch aus dem Kopf ist; nach einem Schnitt OHNE Rückzug steckt das
  obere Stück noch im Sensor → „voll“ → RESUME frei (wie Stock §5.2
  Schritt 6). Lage des Sensors relativ zu Schneider/Zahnrädern unbelegt.
- **ANNAHME:** Nachhol-Temperatur max(Drucktemperatur vor der Pause, 220)
  statt Stock-Materialtemperatur aus RFID; Rückzug nur bei belegtem Hub
  (Stock ruft `box_retrude_material` immer – bei leerem Hub gleichwertig).
- **ANNAHME:** Kopf voll ≠ Ziel-Filament geladen (z. B. Schnitt gescheitert):
  RESUME druckt mit dem alten Filament weiter (Stock ebenso) – vorher von
  Hand `T<n>`.
- **Beobachtung (nicht geändert):** `PAUSE` merkt `hotend_temp` =
  Extruder-Ziel beim Abbruch; hatte die Kette schon geheizt, ist das
  max(Drucktemperatur, 220) – RESUME heizt dann darauf statt auf die
  Drucktemperatur (END_OPT nach Fehler stellt das Ziel nicht zurück).
  Vorschlag: `_fail` übergibt `orig_target` an PAUSE.
- **Am Gerät (Hand am Not-Aus):** Abbruch im Schacht provozieren (Box
  stromlos während `T1`), dann Kopf leeren (Filament von Hand ziehen) →
  RESUME muss verweigern; `T1` in der Pause → RESUME läuft. Nachholen erst
  nach erfolgreichen Einzelschritten (NOTIZ-cfs.md §2) testen.
