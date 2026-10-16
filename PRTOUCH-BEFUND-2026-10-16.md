# PrTouch v3 (Düse als Kraftsensor) – Befund 16.10.2026

Grundlage: zwei statische Analysen (Host-Modul `prtouch_v3_wrapper.so`, MCU-Firmware Main/Düse) und eine
Gegenprüfung gegen einen echten Mitschnitt der „Auto-Nivellierung“ am Display (klippy.log, 16.10.2026).
Analysen durch einen weiteren KI-Agenten, Kernaussagen von uns am Mitschnitt nachgeprüft (Mesh-Werte, Zeiten,
Raster). Arbeitsdateien und Mitschnitt lokal, nicht im Repo. Unbelegtes = ANNAHME.

## 1. Architektur (BELEGT)

- **Kein MCU-Hardwarestopp:** Die MCU erkennt den Kontakt nicht selbst und stoppt die Stepper nicht (kein
  trsync/Endstop-Pfad in den prtouch-Befehlen). Sie zeichnet nur auf:
  - **Düsen-MCU:** Kraft-Samples `pres` (Bitbang-ADC über clk/sdo; Sensortyp CS1237-Familie, 24 Bit = ANNAHME),
    64 Samples à 0,78 ms (≈ 49,8 ms Fenster).
  - **Main-MCU:** Schrittzähler X/Y/Z `step` mit Zeitstempeln, 64 Samples (≈ 24,4 ms Fenster).
- **Der Host berechnet den Kontakt nachträglich** aus Kraftverlauf (Knick/„best_indx“) und Schrittdaten
  (Zuordnung über Zeitstempel) → Korrektur `zoft` (0,010–0,040 mm), Ergebnis = Position + zoft.
- `apax`: Hilfskanal (vermutl. Pressure-Advance-Erfassung über oid_estp) – für die Bett-Abtastung NICHT nötig.
- MCU-Befehle (Dictionary): `config/start/stop/read/cont_prtouch_step|pres|apax` → `ack_prtouch`,
  `resault_prtouch_pres|step|apax|step_cnt` (Formatstrings in GPIO-PHASE-BEFUND-2026-10-16.md §2).

## 2. Live-Mitschnitt „Auto-Nivellierung“ (BELEGT)

- 99 Antastungen: 2× G28, 3× Bürste (Düsenreinigung), 2× G28, 2× Z-Tilt, 9 Feinmessungen Nullpunkt, 81 Mesh-Punkte.
- Mesh 9×9, Raster 42,5 mm, Spirale (Zentrum ausgespart), 2,9 s/Punkt, gesamt 237 s; Tastfahrt 5 mm/s, Hub 5 mm,
  Verfahren 100 mm/s, Lüfter 0,6; Temperaturen Düse 140 °C / Bett 50 °C, Reinigung bei 180 °C.
- Vorab Sensor-Selbsttest: „Strain gauge dynamic detection passed“ (PRES_CHECK).
- Ergebnis dieses Geräts: Spanne −0,067 … +0,226 mm (0,29 mm); 1 Ausreißer (`can_brk False`) automatisch
  verworfen und versetzt (±0,25 mm) neu gemessen. Profil `default` gespeichert.

## 3. Spezifikation des Abtastvorgangs (für ein eigenes Probe-Modul)


```text
Konstanten (belegt): PRES_N=64, PRES_DT=0,78125 ms, FENSTER=49,8 ms;
  STEP_N=64, STEP_DT~0,38 ms, FENSTER=24,4 ms;
  SPEED=5 mm/s, HUB=5 mm, HALT=0,0025 mm, SAMPLES=1;
  Lüfter während Tastung 0,6; TEST_SWAP vorher (PY/PRES/STEP-Versionen).
pro Punkt (x, y):
  fahre (x, y, Hub=5) mit 100 mm/s; lüfter(0,6)
  taste abwärts mit 5 mm/s; zeichne 49,8 ms pres + 24,4 ms step auf
  bestimme i_p = argmax(Kontaktkriterium auf CAL)   # CAL_TURN == best_indx
  t_tri = PRES_T[i_p] - PRES_T[-1]                  # == 0: Kontakt = Fensterende
  t_bst = t_tri - (PRES_T[-1] - PRES_T[i_p])        # best_time, negativ
  i_s   = index von t_bst auf STEP_T                # step_bst_indx
  tri_pose = interp(STEP_DATA, t_tri)               # ≈ STEP_DATA[-1]
  bst_pose = interp(STEP_DATA, t_bst)               # ≈ STEP_DATA[i_s]
  zoft   = bst_pose - tri_pose                      # 0,010–0,040
  A = aktuelle Z-Position bei Kontakt                # POS Z
  final = A + zoft                                   # == "probe at z" ohne comp
  can_brk = plausibel(final, zoft, i_p, i_s)         # genau 1× False beobachtet:
      # i_p<=53, i_s<=42, zoft>=0,040, final deutlich über Nachbarschaft
  wenn not can_brk: hebe 1 mm, weiche (+0,25/-0,25 mm), wiederhole einmal
  gib final + zoft zurück
mesh: Spirale ab (175/217,5) über 42,5-mm-Raster, Zentrum aussparen
  (kommt aus NACCU-Phase), 2,9 s/Punkt, Matrix 9×9, Ebenheit ~0,29 mm;
  z_offset=0, Kompensation rein per zoft; danach SAVE default.
```

## 4. Offen (nur mit gezieltem MCU-Debug-Mitschnitt klärbar)

Rohe prtouch-Frames (Klipper loggt sie nicht): ticks, tri_chxs, Puffer-Indizes, Schwellen (min/max/add_hold,
ned_*, lmt_dead), acq_tick, swap-Kodierung, Sensor-Registerbits; Temperatur-/Regional-/Gauß-Pfade.
Der Host-Rechenweg ist belegt, die MCU-Bit-Ebene nicht.

## 5. Folgen für den Pi-Ersatz – zwei Wege

| | A) PrTouch-Ersatz nachbauen | B) Eddy-Sensor nachrüsten (z. B. Cartographer, BTT Eddy) |
|---|---|---|
| Hardware | keine neue (Original-Kraftsensor) | Sensor + Halter am Druckkopf, Kabel |
| Software | eigenes Modul: Messfahrt, pres/step einsammeln, Kontakt nachträglich berechnen, an bed_mesh/Homing übergeben; Upstream-`load_cell_probe` passt nicht direkt (erwartet Live-Stopp via trsync) | Upstream-Klipper bzw. Herstellermodul |
| Aufwand/Risiko | groß, Genauigkeit erst zu beweisen | klein–mittel, erprobt |
| Mesh | langsam (2,9 s/Punkt) | sehr schnell, hochauflösend |
| Z-Offset | direkt über Düsenkontakt | Kalibrierung nötig (teils eigener Touch-Modus) |

Empfehlung für einen funktionierenden Pi-Umbau: B; A wenn Original-Hardware Pflicht ist oder für die Community.

## KORREKTUR 16.10.2026 abends – Basisadresse der Klipper-MCUs (Main + Düse)

Die Main-MCU- und Düsen-MCU-Images (`mcu0_140…`, `noz0_130…`) liegen ab **0x08003000** (davor Bootloader), NICHT ab 0x08000000.
Belege (eigene Prüfung): Reset-Handler bei 0x080033F8 ist nur mit Basis 0x08003000 ein gültiger Prolog (`cpsid i`, SP setzen, Sprung);
158 von 233 Thumb-Zeigern im Image treffen mit 0x08003000 einen Funktionsprolog, mit 0x08000000 nur 6. Klipper-Befehlstabelle
(Main) bei 0x0800743C, 12-Byte-Einträge [Handler | Info (Byte0 = msgid) | param_types].
Folge: **Adressangaben** der früheren MCU-Analysen (PrTouch-MCU, Bit-Spezifikation, Stopp-Analyse) sind ungültig; Data-Dictionaries
(zlib) sind nicht betroffen. Die Kernaussage „prtouch-Code stoppt die Stepper nicht selbst“ wurde mit korrekter Basis bestätigt:
kein Aufruf des Stopp-Helfers von `stepper_stop_on_trigger`; die einzige gemeinsame Pin-Funktion setzt im prtouch-Lesepfad den
Swap-Pin (Synchronisation). Plausibler Stopp-Weg (ANNAHME): Host hängt die Z-Stepper per `stepper_stop_on_trigger` an ein trsync
und löst bei erkanntem Kontakt `trsync_trigger` aus.

## NACHTRAG 16.10.2026 spät – Stopp-Weg BEWIESEN (PRTOUCH-STOPP-BEWEIS-2026-10-16.md)

- Hypothese „Düse schaltet Swap-Leitung bei Kontakt → Main-Endstop/trsync stoppt Z“ ist **WIDERLEGT**.
- Swap-Leitung (Düse PA15 → Main !PC7) = **nur Zeitsynchronisierung**: Düse setzt sie bei config/start HIGH, Main liest sie einmal bei start_prtouch_step.
- Düse erkennt den Kontakt (Schwellen, tri_chxs), stoppt aber nichts; Host nutzt kein endstop_home/trsync/stepper_stop.
- **Der Host bricht die Fahrt ab** und rechnet den Kontakt nachträglich (zoft). Folge für ein eigenes Modul: nur kurze, langsame
  Abwärtsschritte mit Prüfung nach jedem Schritt und sofortigem Abbruch; kein Endstop auf !PC7 nötig.
