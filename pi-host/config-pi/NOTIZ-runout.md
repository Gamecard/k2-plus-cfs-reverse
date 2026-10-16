# NOTIZ – Auto-Nachladen aus der CFS bei leerer Spule (A7)

> **ENTWURF 17.10.2026 – am Gerät UNGETESTET.** Offline geprüft:
> `pi-host/test_k2_box.py` (25/25, davon 5 Runout-Prüfungen) und Batch gegen
> Upstream-Klipper 9cb9975 (`arbeitsdateien-lokal/upstream-test-runout/`,
> `ERGEBNIS.txt`). Technik: `pi-host/README-k2_box.md` §9.

## 1. Was Stock tut (Belege)

| Quelle | Beleg |
|---|---|
| `live-logs-2026-10-16/config-aktuell/printer.cfg` `[filament_switch_sensor filament_sensor]` | `pause_on_runout: true`, `runout_gcode`: wenn `can_extrude` → `G91 / G0 E30 F600 / G90`, dann `BOX_CHECK_MATERIAL_REFILL` |
| `…/gcode_macro.cfg` `PAUSE`/`PAUSE_EXTERNAL` | PAUSE fährt vor dem runout_gcode: E −1/−2, Z-Hub, `BOX_GO_TO_EXTRUDE_POS` → `BOX_NOZZLE_CLEAN` → `BOX_MOVE_TO_SAFE_POS`, M104 S140, Lüfter aus |
| `rootfs-live/…/box_wrapper.cpython-39.so` (Strings) | `BOX_CHECK_MATERIAL_REFILL` → `check_material_refill` / `material_auto_refill` / `is_material_available`; `BOX_ENABLE_AUTO_REFILL`, `BOX_UPDATE_SAME_MATERIAL_LIST`, `BOX_GET_GCODE_USED_TNN`; Logtexte „same_material_list: %s, same_tnn_list: %s“, „same_tnn_list len smaller than 2“, „no auto refill“, „disable material automatic refill“, „no tray with ingredients found“, „[error_resume_process] filament sensor is detected“ |
| Live-Logs 05.–16.10. (Stock-Statusobjekt `box`) | `'auto_refill': 1, 'filament_useup': 1, 'same_material': []`; je Slot `color_value` (`0FFFFFF` …), `material_type` (`101001` …), `remain_len` – **kein einziger echter Runout geloggt** (nur Config-Echos) |
| `GHIDRA-CFS-BEFUND2-2026-10-16.md` §4 | Box-FW: Status **0x50** nur im 0x0a-Poll in STATE_PRINT = Einlauf ODER Hub des Druck-Slots frei (Runout); 0x51/0x52 aus dem print_check-Thread („außer es lag schon ein Runout vor“) |
| `PROTOKOLL-485.md` „RFID-Frame“, `nachbau/box.py` `split_rfid_vendor` | RFID 0x02 = 40-Zeichen-vender, `mat_id` (Pos. 11–16, z. B. 101001 Hyper PLA) + `col` (17–23) |
| Orca-Profile „Creality K2 Plus“ | G-Code enthält nur M8200, **keine** Gleich-Material-Liste → die Liste kommt bei Stock über Creality Print/Oberfläche, nicht aus der Datei |
| Vanilla (`lokale Analyse-Dateien`, nur Vorbild) | `pause_on_runout: false` + eigenes Aufschieben; Ersatz = belegter Slot mit gleichem Material UND gleicher Farbe, erster in Indexfolge; vorher 30 mm Rest durch die Zahnräder |

Unbelegt: Stock-Reihenfolge bei mehreren Kandidaten, genaue Box-Frame-Folge
beim Nachladen, ob Stock nach dem Nachladen selbst `RESUME` ausführt.

## 2. Was der Pi tut

`printer.cfg`: `pause_on_runout: True` + `runout_gcode: KTWO_BOX_RUNOUT`.
Upstream (`klippy/extras/filament_switch_sensor.py` `_runout_event_handler`)
schickt bei `pause_on_runout` ZUERST `send_pause_command()` (virtual_sdcard
hält an), wartet `pause_delay` und führt dann `"PAUSE\n" + runout_gcode`
aus – der Druck steht also sicher, bevor `KTWO_BOX_RUNOUT` läuft. Darum
bleibt es wie Stock bei True; `False` + eigenes Pausieren wäre nur nötig,
wenn ohne Pause getauscht werden sollte. (Wird `pause_on_runout` doch auf
False gestellt, pausiert `KTWO_BOX_RUNOUT` in jedem Abbruchfall selbst.)

`KTWO_BOX_RUNOUT [SLOT=A..D] [TEMP=] [DRY_RUN=1]`:

1. Lesend prüfen (Reihenfolge, jede Störung → Pause + Klartext):
   `runout_refill`/`KTWO_BOX_AUTO_REFILL` an → `allow_write` UND `allow_move`
   → kein Wechsel aktiv/offen → Kopfsensor leer (sonst „Fehlauslösung“) →
   Bus da → 0x0a: Box im **PRINT** (CFS druckt; ERROR 0x50/0x51/0x52 wird
   später per 0x04 [00 01] quittiert, andere Fehler → Pause) → aktueller
   Slot (`SLOT=` > zuletzt geladen > Hub-Slot aus dem PRINT-Poll) → 0x08 [01]
   **Hub leer** → 0x08 [00] **Einlauf des Slots leer** (Spule wirklich
   leer, kein Bruch) → 0x02 [0f] RFID aller Slots (+ gemerktes Etikett des
   leeren Slots vom Laden) → 0x03 [0f] Rest (nur Meldung).
2. Ersatz wählen (`select_runout_slot`):
   - mit Gleich-Material-Gruppen (`KTWO_BOX_SAME_MATERIAL`): nächstes
     Gruppenmitglied mit Filament am Einlauf (Listenfolge ab dem leeren
     Slot, zyklisch);
   - sonst RFID: gleiche `mat_id` UND gleiche Farbe (`runout_require_color:
     False` = Farbe egal), Filament am Einlauf, Reihenfolge ab dem Slot nach
     dem leeren (A→B→C→D→A). Slots ohne lesbares Etikett nie.
   - kein Kandidat → Pause („no tray with ingredients found“).
3. Nachladen über die vorhandene Kette: `KTWO_BOX_PRE_OPT TNN=… Z_HOP=0`
   (PAUSE hat schon gehoben) → Schacht, Heizen auf max(Drucktemperatur vor
   der Pause, 220), **E30 F600** (Stock-Rest durch die Zahnräder, hier im
   Schacht statt am Parkpunkt über dem Bett) → `KTWO_BOX_EXTRUDE` (Box-
   Einzug bis Kopfsensor, Stage 7, mode 7, PRINT) → `WASTE` → `FLUSH` (90 +
   70) → `END_OPT RETURN=1` (zurück zum Parkpunkt) → **RESUME**
   (`runout_resume: False` = pausiert lassen). Kein Schnitt, kein Rückzug:
   Hub und Kopf sind leer.
4. Fehler in der Kette → wie jeder Wechsel-Fehler: Pause bleibt, Meldung
   `!! … fehlgeschlagen`, `RESUME` gesperrt bis `KTWO_BOX_ERROR_CLEAR`.
   Vorprüfungs-Abbrüche sperren RESUME NICHT (Filament von Hand nachlegen,
   dann RESUME = Upstream-Standard). Nie Shutdown; ohne Druck (Handaufruf)
   G-Code-Fehler.

Weitere Befehle: `KTWO_BOX_AUTO_REFILL [ENABLE=0|1]` (Laufzeit, Start =
`runout_refill`), `KTWO_BOX_SAME_MATERIAL LIST=T1A,T1C/T1B,T1D` bzw.
`CLEAR=1` (nur Box 1, Gruppen ≥ 2 Slots). Status `printer.k2_box`:
`auto_refill`, `same_material`, `runout_last`.

## 3. Config (`k2-module.cfg` `[k2_box]`)

| Option | Default | Bedeutung |
|---|---|---|
| `runout_refill` | False | Auto-Nachladen; wirkt nur mit `allow_write` + `allow_move` |
| `runout_require_color` | True | RFID-Regel: Farbe muss passen |
| `runout_resume` | True | nach Erfolg automatisch RESUME |
| `runout_tail_length` / `_velocity` | 30 / 600 | Stock `G0 E30 F600`; 0 = aus |

## 4. Inbetriebnahme

1. Wechsel (`NOTIZ-cfs.md` §2) muss sicher laufen.
2. `KTWO_BOX_RUNOUT SLOT=A DRY_RUN=1` mit leerem Slot A: zeigt Ersatz und
   Begründung, bewegt nichts.
3. `runout_refill: True`, Testdruck mit fast leerer Spule, Hand am Not-Aus.

## 5. ANNAHMEN / offen

- Ersatz-Regel ohne Slicer-Liste (RFID mat_id + Farbe) und zyklische
  Reihenfolge; Stock-Reihenfolge und -Format der Liste unbelegt.
- „CFS druckt“ = Box im PRINT (bzw. ERROR 0x50/0x51/0x52); Quittieren dieser
  Fehler mit 0x04 [00 01] vor dem Einzug.
- Einlauf des leeren Slots muss frei sein; bei Bruch (Einlauf belegt, Hub
  leer) wird nicht nachgeladen.
- Etikett der leeren Spule: falls die Box nur noch „unknown“ liefert, gilt
  das beim Laden gemerkte (nur im Speicher, nach Neustart weg).
- Ladetemperatur aus `_PI_PARAM.hotend_temp` (macros.cfg PAUSE), min. 220;
  Stock nimmt RFID/Materialdatenbank.
- RESUME aus dem runout_gcode heraus (Upstream: virtual_sdcard läuft weiter,
  sobald das Skript endet) – am Gerät prüfen.
- Mehrere Boxen, Externe Spule (filament_rack) → immer Pause.
