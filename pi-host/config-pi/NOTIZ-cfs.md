# NOTIZ – CFS-Filamentwechsel auf dem Pi (T0–T15, M8200)

> **ENTWURF 16.10.2026 – am Gerät UNGETESTET.** Offline geprüft:
> `pi-host/test_k2_box.py` (20/20) und Batch gegen Upstream-Klipper 9cb9975
> (`arbeitsdateien-lokal/upstream-test-cfs/ERGEBNIS.txt`). Technik und
> Belege: `pi-host/README-k2_box.md` §8. `config-pi/README.md` ist hier
> bewusst nicht geändert (dort steht noch „T1–T15/M8200 → Fehler“; gilt
> seit diesem Stand nicht mehr).

## 1. Was wo passiert

| Datei | Inhalt |
|---|---|
| `macros.cfg` | `T0`–`T15` → `_KTWO_TOOL_CHANGE T=n`; `M8200` (P/C/R/L/W/F/O wie Stock-box.cfg); `_KTWO_FAN_SAVE/_RESTORE` (fan2); `RESUME` gesperrt bei offenem Wechsel-Fehler + `KTWO_BOX_RESUME_FIX` |
| `k2-module.cfg` `[k2_box]` | Freigaben `allow_write` / `allow_move` (beide False), `pause_on_failure: True`, alle Stock-`[box]`-Werte (Positionen, Längen, Temperaturen, Geschwindigkeiten), `cut_pos_x: -7.80` (SAVE_CONFIG-Wert der Stock-Kalibrierung) |
| `pi-host/k2_box.py` | Schritte `KTWO_BOX_PRE_OPT/CUT/GO_EXTRUDE_POS/RETRUDE/EXTRUDE/WASTE/FLUSH/WIPE/SAFE_POS/END_OPT/WAIT/ERROR_CLEAR/RESUME_FIX` |

Zuordnung (Stock box.cfg `M8200 L`): Werkzeug `n` → Box `n/4+1`, Slot
`"ABCD"[n%4]` – `T0..T3 = T1A..T1D`. **Nur Box 1 ist unterstützt**
(`box_addr: 1`); T4–T15 werden mit Meldung abgelehnt (ANNAHME: weitere
Boxen hätten eigene Adressen 2–4 und bräuchten je einen Controller).

Ablauf eines T-Befehls (= Stock-T0-Beispiel aus box.cfg):

1. `PRE_OPT`: schon geladen? (Hub-Sensor 0x08 [01] + Kopfsensor) → fertig.
   Sonst Kopfsensor sperren, G-Code-Zustand sichern, Z +0,4.
2. `CUT`: X10 Y200 → ACCEL 5000 → X −7,80 (F30000) → 0,1 s halten → X10.
3. `RETRUDE`: Schacht (Y345 → X139 → Y378 → X133), 220 °C, Box bereit,
   0x11 p0 → E −10 F600 (3×) → p0 → p1 (Hub frei; live bis 23 s).
4. `EXTRUDE`: Box fördert, bis der Kopfsensor anspricht (m5 alle 2 s, max.
   90 s), Box-Buffer (m6), Extruder greift `E+30 F360`, Box prüft (m7),
   Box PRINT.
5. `WASTE`: nur Hinweis (Stock: Kamera prüft den Abfallschacht).
6. `FLUSH`: 90 mm + 70 mm (je 20/80 %), nach jedem Abschnitt Abstreifen
   (Blech X160→X135, Kreise am Silikon, 3× Schütteln).
7. `END_OPT`: Schacht verlassen (X160 → Y345), zurück auf Start-XY, Z
   runter, Zieltemperatur zurück, Kopfsensor + Zustand zurück.

Creality Print: schickt entweder `T<n>` (→ volle Kette oben) oder die
Kette `M8200 P / C / R / L I<n> / F / O` mit eigenem Z-Hub/Rückweg
(Stock-Kommentar „T0 kann durch diese Makros ersetzt werden“). Bei der
M8200-Kette macht `PRE_OPT` keinen Z-Hop (`Z_HOP=0`) und `END_OPT` keine
Rückfahrt (`RETURN=0`) – das erledigt der Slicer wie im Stock-Beispiel.
Abweichung vom Stock: `M8200 F … L…` löst KEINEN zusätzlichen Einzug aus.

## 2. Inbetriebnahme (Reihenfolge, Hand am Not-Aus)

Voraussetzung: Homing + Bewegung freigegeben (README.md §4), `[k2_485]`
mit echtem Port, `KTWO_BOX_STATUS` liefert Zustand/Hub/Temp.

1. Lesend: `KTWO_BOX_STATUS`, `KTWO_BOX_WAIT`, `QUERY_FILAMENT_SENSOR
   SENSOR=filament_sensor`. `T1` → nur Plan (alles gesperrt), Plan lesen.
2. `allow_move: True` (Box noch gesperrt), ohne Filament im Kopf:
   `KTWO_BOX_CUT` einzeln – prüfen, dass der Kopf den Schneidhebel trifft
   (X −7,80 aus der Stock-Kalibrierung; Y200). Dann `KTWO_BOX_GO_EXTRUDE_POS`,
   `KTWO_BOX_WIPE`, `KTWO_BOX_SAFE_POS` (Kollisionen mit Schacht/Silikon?).
3. `allow_write: True`: `KTWO_BOX_RETRUDE` (Slot aus Hub-Sensor),
   `KTWO_BOX_EXTRUDE TNN=T1B`, `KTWO_BOX_FLUSH` einzeln, Düse auf 220 °C.
4. Erst dann ganze Wechsel `T1`, `T0`, … außerhalb eines Drucks, zuletzt im
   Druck (Creality Print, CFS an).

## 3. Fehler im Wechsel

- Im Druck: `!! [k2_box] <Schritt> fehlgeschlagen: …` → Druck PAUSIERT
  (kein Abbruch/Shutdown), restliche Schritte werden übersprungen,
  Kopfsensor und G-Code-Zustand werden zurückgesetzt, der Kopf bleibt stehen.
  Weiter: Ursache beheben (z. B. Filament im Hub, Box-Fehler 0x0c =
  Odometer-Stillstand), Schritt bzw. ganzen Wechsel `T<n>` von Hand
  wiederholen, `KTWO_BOX_ERROR_CLEAR` (setzt auch die Box per 0x04 [00 01]
  auf IDLE), dann `RESUME` (senkt danach den Z-Hop des Wechsels per
  `KTWO_BOX_RESUME_FIX`). Ein Wiederholungs-`T<n>` fährt zur Startposition
  des abgebrochenen Wechsels zurück.
- Ohne Druck: normaler G-Code-Fehler, Zustand aufgeräumt, keine Bewegung
  danach (der Kopf bleibt z. B. im Schacht).
- Ohne RS485-Port: `… fehlgeschlagen: [k2_485] …` (Batch: „Batch-Modus“).

## 4. ANNAHMEN / offen

- **Stage 7:** Extruder-Länge `buffer_empty_len` 30 mm @ `Tn_extrude_velocity`
  360 und `stage7_delay` 2 s bis 0x10 m7 (live 3,7–3,8 s zwischen
  `stage7`-Ereignis und m7; was der Stock-Handler genau fährt, ist nicht
  geloggt). m7 verlangt > 3 mm in 300 ms – mit 6 mm/s knapp (0x0e-Grenze
  liegt laut Ghidra bei p·0,5).
- **Schneiden:** Rückweg mit `travel_velocity`; `cut_dwell` 0,1 s ist eigene
  Zugabe (Batch: ohne Halt kehrt der Kopf wegen Input-Shaper schon bei
  X −7,69 um). Schneidhebel-Sensor (`switch_pin` !nozzle_mcu:PB9) nur
  optional über einen eigenen `[filament_switch_sensor]` (`cut_sensor:`),
  Pegelbedeutung unbelegt.
- **Temperaturen:** Stock nimmt Material-Temperaturen aus dem RFID-Tag
  (live 220, Spülen bis 240 °C, Geschwindigkeit ×1,2) – hier
  `max(Ziel, Tn_extrude_temp 220)` bzw. `TEMP=`; keine Erhöhung beim Spülen.
- **Lüfter:** nur fan2 aus/zurück (Stock LOAD_MATERIAL_CLOSE_FAN2); das
  Blasen beim Spülen (live 2× SET_PIN) ist nicht nachgebaut.
- **Wartezeiten:** `box_wait_timeout` 200 s (Preload ≤ ~3 min/Slot),
  Sensor-Timeout 90 s (live) → Box per 0x04 [00 01] auf IDLE.
- **Positionen:** `safe_pos_x` 225 ungenutzt (Stock-Beispiel verlässt den
  Schacht nur über X160/Y345); `extrude_entry_pos_x` 139 aus dem Beispiel.
- **Z-Hop 0,4 mm** wie Stock-Beispiel – bei hohen Objekten nahe Y200/X10
  bzw. auf dem Weg zum Schacht Kollisionsgefahr wie im Stock.
- **Reactor:** lange Box-Antworten laufen über `ReactorBusProxy` in einem
  Hilfs-Thread; am Gerät prüfen, dass Heizung/MCU dabei stabil bleiben.
- **Mehrere Boxen**, WASTE-Prüfung, Auto-Nachladen (Stock
  `BOX_CHECK_MATERIAL_REFILL`), RFID-basierte Spülmengen: nicht umgesetzt.
