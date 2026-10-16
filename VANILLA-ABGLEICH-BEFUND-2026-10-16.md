# VANILLA-ABGLEICH-BEFUND 16.10.2026 – Pi-Config/Module gegen k2-vanilla-public

Gegenprüfung unserer `pi-host/config-pi/` und Module gegen den Fork **k2-vanilla-public** (offene Treiber für
den normalen K2, Stand 834642d). Maßstab bei Abweichungen: aktuelle Stock-Plus-Config > unsere Config > Vanilla.
Analyse durch einen weiteren KI-Agenten; **jeder Punkt der Kategorie „ändern“ wurde von uns gegen Stock bzw.
Upstream-Quellen nachgeprüft** – zwei davon erwiesen sich als falsch.

## Ergebnis der Nachprüfung

| Nr. | Behauptung | Nachprüfung | Folge |
|---|---|---|---|
| A1 | M106/M107 brauchen `SYNC=0`, sonst stocken Bewegungen | **widerlegt**: Upstream-`SET_PIN` reiht per Lookahead-Callback ein (`output_pin.py` GCodeRequestQueue), hält nichts an; `SYNC` gibt es nur im Creality-Fork | keine Änderung |
| A2 | `M191` wartet bei uns im Kühlzweig nicht, Stock schon | **gleichwertig**: Stock wartet dort mit `SET_HEATER_TEMPERATURE … TARGET=0 WAIT=1` – Ziel 0 kehrt sofort zurück | keine Änderung |
| A3 | Kein `BED_MESH_PROFILE LOAD=default` nach Homing | **bestätigt** (Stock `sensorless.cfg`) | **umgesetzt**: `homing.cfg` ruft nach `G28 Z` `_PI_MESH_LADEN` |
| A8 | Kein `on_error_gcode` | **teilweise**: Upstream-Default schaltet Heizungen ab; Lüfter/Kammer/Pause bleiben | **umgesetzt**: `on_error_gcode: _PI_RESET_STATE` (TURN_OFF_HEATERS, M107, M141 S0, CLEAR_PAUSE) |
| A9 | `sweeping_period`-Kommentar widerspricht Config | **bestätigt** (missverständlich) | **umgesetzt**: Kommentar eindeutig (Upstream-Sweep bewusst, Stock-Methode optional) |
| A4 | Düsenreinigung vor dem Z-Antasten fehlt | **bestätigt** (eigene Doku: OFFEN; Stock reinigt am Silikonstreifen vor `_HOME_Z`) | offen – braucht Bewegungs-Makro |
| A5 | RESUME ohne Prime/Reinigung | **plausibel** (Stock RESUME_EXTERNAL: Prime `PAUSE.extrude+80`, Reinigung) | offen |
| A6 | Kein Ersatz für `CALIBRATE_CUT_POS` | **bestätigt** (`cut_pos_x` nur als Handwert) | offen – braucht Stall-Mode 3 + Messfahrt |
| A7 | Kein CFS-Nachladen bei Runout | **bestätigt** (NOTIZ-cfs: Auto-Nachladen nicht umgesetzt) | offen |
| A10 | `z_align` (Dual-Z-Lichtschranken) ungenutzt | bekannt offen | erst Vergleich am Gerät |

Kategorie „K2-Base-spezifisch, nicht übernehmen“ (Auszug): Geometrie 262/296,5/270, Y-Enable `!PA8`,
Single-Z mit Getriebe/TMC2208, Thermistor-Tabellen und PIDs der Base, `max_temp 315`, Bett-`max_power 0.5`,
Lüfter-Mapping der Base, Kammer ohne Heizung, Box-Koordinaten Y≈294, Mesh 11×11, Shaper 52,4/45,4,
Motor-Kp X 300 (Plus: X 100 / Y 300).

## Motor-Kalibrierfunktionen aus Vanilla (nur dokumentiert, NICHT umgesetzt)

Laut Vanilla-`motor_control.py` (von uns nicht am Gerät geprüft):
- **0x03 Encoder-Kalibrierung** `[00]`, ~60 s, **dreht den Motor**, danach Power-Cycle nötig.
- **0x04 Elektrik-Offset-Kalibrierung** `[00]`, ~60 s je Achse, **dreht den Motor**, Antwort 1+4+4 Byte.
- **0x07 Flash-Parameter** lesen `[06,id]`, schreiben `[05,id]+Bytes`, speichern `[03]` – **schreibt den Motor-Flash**.

Zurückgestellt bis zu echtem Bedarf (Motortausch); dann nur mit Einzelfreigabe und Live-Gegenprobe je Funktion.
