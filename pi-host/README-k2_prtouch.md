> **ENTWURF – am echten Gerät UNGETESTET (Stand 16.10.2026).** Klipper-Modul [k2_prtouch] für den eingebauten Kraftsensor. Offline 43/43 + Review-Test. Entwurf durch einen weiteren KI-Agenten, Review durch uns (Fix: max_travel_mm hart ≤ 10 mm). Vor dem ersten echten Test unbedingt die ANNAHMEN-Liste unten am Gerät prüfen, erst mit allow_move: False.

# BERICHT – Klipper-Modul [k2_prtouch] (PrTouch v3, K2 Plus)

Stand: 16.10.2026. Ablage: `lokale Arbeitsdateien `
Ergebnis: `k2_prtouch.py`, `test_k2_prtouch.py`, `BERICHT.md` (dieser).
Regeln: KEIN Netzwerk, KEIN ssh/curl, KEIN Druckerzugriff. Nur analysiert +
entworfen + getestet mit Fakes. Alle Dateipfade unten sind Lese-Belege.

## 0. Einordnung

Der eingebaute Kraftsensor (Düse als Taster, PrTouch v3) wird mit der
UNVERÄNDERTEN Creality-MCU-Firmware genutzt und ersetzt
`prtouch_v3_wrapper.so`. Kernbefund (BELEGT):

- Die MCU erkennt den Kontakt (Schwellen `min/max/add_hold`, `ned_*`,
  `tri_chxs`), stoppt aber NICHTS (STOPP-BEWEIS §§2-3: kein
  `stepper_stop_on_trigger`/`trsync`/`endstop_home` in den prtouch-Pfaden;
  Swap Düse→Main nur HIGH bei config/start, Main liest einmal).
- Der Host fährt zeitgesteuert (live 5 mm/s), zeichnet `pres` 64×0,78 ms
  (49,8 ms) + `step` 64×0,38 ms (24,4 ms) auf und rechnet den Kontakt
  nachträglich (`best_indx → step_bst_indx → zoft`, live `zoft`
  0,010–0,040 mm, `tri_time` immer 0,000, `CAL_TURN == best_indx` an allen
  99 Punkten).
- `trig_pos:None` überall, `TEST_SWAP` (PY 78/PRES 71/STEP 71) vor jeder
  Phase, `PRES_CHECK SUCCESS`, Temperaturen 140/50 °C (180 °C Reinigung),
  Lüfter 0,6, Hub 5 mm, Mesh 9×9 Spirale 2,9 s/Punkt, 1× `can_brk False`
  mit versetzter Wiederholung (−0,25/+0,25 mm).

Folge: Ein sicheres Ersatzmodul darf NIEMALS eine lange, blinde Tauchfahrt
machen. Es muss in kurzen Schritten fahren, nach jedem Schritt per M400
synchronisieren, lesen/auswerten und hostseitig abbrechen.

## 1. Architektur

```
printer.cfg [k2_prtouch]
  → PrinterK2Prtouch (Klipper-Hülle, ANNAHMEN Upstream-API)
    → K2PrtouchController (reine Logik, ohne Klipper testbar)
      → pres-MCU (KlippyPresMcu / FakePres)   Düse nozzle_mcu
      → step-MCU (KlippyStepMcu / FakeStep)   Main mcu
      → Toolhead   (KlippyToolhead / FakeToolhead)
```

- `k2_prtouch.py` ist ohne Klipper importierbar (reine Helfer +
  Controller). Alle MCU-/Bewegungs-Zugriffe sind hinter `PrtouchMcuOps` /
  `ToolheadOps` gekapselt (Stil wie `k2_motor_init.py`/`k2_box.py`).
- Einmalig (register_config_callback, vor connect):
  `config_prtouch_pres` auf `nozzle_mcu` (idx, swp PA15, clk/sdo –
  s. ANNAHMEN), `config_prtouch_step` auf `mcu` (X/Y/Z-OIDs, swp !PC7).
- Pro Antastung: `TEST_SWAP` + Ruhetest → `start_prtouch_step` (`aqc_tick`)
  + `start_prtouch_pres` (`cfg_regs=60`, `acq_tick=93750`, `ned_*`,
  `holds 4000/10000/500`, `lmt_dead`) → Schleife aus
  `move_down(step_mm) + wait_moves + read_pres/step + decode + detect` →
  bei Kontakt `stop_*` + Lift + `Z = A + zoft` + `can_brk`, ggf. genau
  einmal versetzt wiederholen.
- Dekodierung: `ticks` u32-LE → Sekunden `(t−t_last)/freq`,
  `datas` s32-LE (pres roh, step Zähler → mm via `step_dist_mm`
  0,0025, relativ zum Ende). `tri_pose ≈ 0`, `bst_pose` interpoliert,
  `zoft = bst−tri`.
- G-Codes: `K2_PRTOUCH_PROBE [X Y DRY_RUN]`, `K2_PRTOUCH_HOME_Z`,
  `K2_PRTOUCH_MESH [COUNT]`, `K2_PRTOUCH_TEST_SWAP`, `K2_PRTOUCH_READ`,
  `K2_PRTOUCH_STATUS`. `get_status()` für Moonraker (allow_move, step,
  letztes Ergebnis – kein Bus-Poll).
- `apax` ist bewusst NICHT enthalten (BELEGT 0 Treffer in 11532 Log-Zeilen,
  Host-Befund: separates Aux-Modul, für Z-Probe entbehrlich).

## 2. Sicherheitskonzept (S1–S5, im Code + Tests)

- **S1 Kurze Schritte:** `step_mm` Default 0,02, Maximum 0,05 (hart in
  `parse_options` + doppelt in `probe_point`); `probe_speed` Default 5,0,
  Maximum 5,0; nach JEDEM Schritt `wait_moves()` (M400). Test (b)+(g).
- **S2 Harte Grenzen:** `max_travel_mm` Default 5,0 (Stock-Hub);
  `emergency_thresh` Default 15000 (|roh−Null| → Abbruch + Fehler);
  `probe_timeout_s` Default 30 s. Bei JEDEM Fehler: `stop_*`, Lift
  `z_lift_mm` (Default 2,0), klare Meldung, kein Weiterprobe (außer
  genau einer `can_brk`-Wiederholung). Tests (b)+(c).
- **S3 Freigabe:** `allow_move` Default False → nur Swap/Ruhe/Lesen/Plan,
  keine Z-Bewegung. `K2_PRTOUCH_PROBE DRY_RUN=1` ebenfalls bewegungslos.
  Test (d).
- **S4 Vorabtests:** `TEST_SWAP`-Äquivalent (beide MCU-Seiten müssen
  antworten; Fehlertext wie Stock `PR_ERR_CODE_SWAP_PIN_DETECTI`) +
  Ruhetest (`dif ≤ rest_max_dif` Default 3000, analog PRES_CHECK).
  Abbruch VOR Bewegung. Test (e).
- **S5 Temperaturen:** Nur Warnung bei Abweichung von 140/50 °C (±15),
  niemals Heizen. `get_temps()`-Fehler → Hinweis, kein Abbruch.
- **Überfahrweg:** Fake-Spike erscheint im ersten Fenster nach Kontakt;
  Schleife bricht im selben Durchlauf ab → maximal 1× `step_mm`
  Überfahrt (live Fenster 49,8/24,4 ms ≈ 0,25/0,12 mm bei 5 mm/s, hier
  per Schritte auf 0,02 mm begrenzt). Test (g): `min_z ≥ wahr−step_mm`,
  `Z-Fehler < 0,02 mm` (a).

## 3. printer.cfg-Beispiel

```ini
# Dateien kopieren: k2_prtouch.py → klippy/extras/k2_prtouch.py
# (Upstream-Klipper auf dem Pi-Host, MCU-FW unverändert lassen).

[k2_prtouch]
# Sicherheit: erst DRY_RUN testen, dann True (S3):
allow_move: False
step_mm: 0.02            # Maximum 0.05 (S1)
probe_speed: 5.0         # Maximum 5.0 (S1)
lift_speed: 5.0
max_travel_mm: 5.0       # Stock-Hub (S2)
z_lift_mm: 2.0           # Anhebung nach Kontakt/Fehler (S2)
probe_timeout_s: 30.0
contact_thresh: 3000     # ANNAHME s. §4
emergency_thresh: 15000  # ANNAHME s. §4
rest_max_dif: 3000       # ANNAHME PRES_CHECK-Analogon

# MCUs/Pins (Stock aus printer-live.cfg):
main_mcu: mcu
nozzle_mcu: nozzle_mcu
pres_idx: 0
pres_swap_pin: nozzle_mcu:PA15
step_swap_pin: !PC7
pres_cs0_pin: nozzle_mcu:PB13, nozzle_mcu:PB14
# ANNAHME clk/sdo aus cs0 (Reihenfolge tauschbar):
# pres_clk_pin: nozzle_mcu:PB13
# pres_sdo_pin: nozzle_mcu:PB14
# pres_clk_sdo_swap: False

# Stock-Sampling (BELEGT Werte, ANNAHME Mapping s. §4):
cfg_regs: 60
acq_tick: 93750
aqc_tick: 45700
ned_tftr: 5
ned_hftr: 1
ned_lftr: 0.8
ned_lftr_scale: 10
min_hold: 4000
max_hold: 10000
add_hold: 500
lmt_dead: 200

# Datenformat (ANNAHME, zentral):
# tick_format: <I
# data_format: <i
# step_dist_mm: 0.0025
# retry_dx: -0.25
# retry_dy: 0.25

# homing_override-Vorschlag (Z-Homing über eigenen G-Code):
# [homing_override]
# gcode:
#   {% if params.Z is defined %}
#     K2_PRTOUCH_HOME_Z
#   {% else %}
#     G28 X Y
#   {% endif %}
# Danach: BED_MESH_PROFILE LOAD="default" wie Stock (sensorless.cfg).
```

Erstlauf-Reihenfolge: `K2_PRTOUCH_TEST_SWAP` → `K2_PRTOUCH_READ` →
`K2_PRTOUCH_PROBE DRY_RUN=1` → `allow_move: True` + `K2_PRTOUCH_PROBE` →
`K2_PRTOUCH_HOME_Z` → `K2_PRTOUCH_MESH COUNT=3`.

## 4. ANNAHMEN (vollständig, vor Gerätetest zu klären)

1. **Datenformat ticks/datas:** u32-LE / s32-LE, 4 B, little (Optionen
   `tick_format <I`, `data_format <i`). BELEGT: `%.*s`-Arrays, 64 Samples,
   `from_bytes/little/signed`-Strings in `unzip_data`. Byte-Breite
   statisch nicht entscheidbar (SPEZIFIKATION §3, MCU-Befund §6).
2. **clk/sdo-Belegung:** Ableitung aus `pres_cs0_pin` PB13/PB14, Reihenfolge
   per `pres_clk_sdo_swap` (Default PB13=clk, PB14=sdo). BELEGT Negativ:
   stehen NICHT in `printer-live.cfg` (SPEZIFIKATION §1.3); Herkunft aus
   Wrapper-Build (`adc_par/clk_par/sdo_par`). Nur per Ghidra-Cross-Ref
   oder Live-MCU-Mitschnitt beweisbar.
3. **ned-Mapping:** `pres_tri_fter (5,1,0.8)` → `ned_tftr/hftr/lftr` in
   Format-Reihenfolge. BELEGT Werte/Stellenzahl, ANNAHME Zuordnung.
   Zusatz: `ned_lftr` 0,8 (float) vs `%u` → Skalierung `ned_lftr_scale`
   (Default 10 → MCU-Wert 8). Exakter Faktor offen.
4. **holds-Mapping:** `pres_tri_hold (4000,10000,500)` →
   `min/max/add_hold` in Format-Reihenfolge. BELEGT Werte, ANNAHME
   Zuordnung (Host-Befund als V markiert).
5. **`lmt_dead`:** Default 200 (ANNAHME: `prth_err_dead` 0,2 × 1000).
   Kandidaten `prth_err_dead 0,2` vs `%u`, `pres_ded_tkms` 128,0,
   `pres_chk_hold` 15000 (SPEZIFIKATION §2.1). Nur per Roh-Frame klärbar.
6. **`aqc_tick`:** Default 45700 aus STEP-Fenster 0,0244 s (Spanne
   45600–46000 je Punkt). `step_acq_tkms`-Key existiert als Tupel,
   fehlt in `liveconfig_*` (SPEZIFIKATION §2.2).
7. **`read_*`-Args:** `ch=0` (BELEGT alle Logs nur CH0), `len=64`
   (ANNAHME Fenstergröße), `is_src=1` FTR / `idx=0` Ringoffset
   (ANNAHME; `is_src` 0/1 = SRC vs FTR/CAL aus Wellen-Namen).
8. **Kontaktkriterium:** Schwellwert `contact_thresh` 3000 + 2
   aufeinanderfolgende Samples (ANNAHME; exakte `ned_*`+`holds`/`dead`+
   CAL-Formel nur per Roh-Mitschnitt). Live-Plausibilität: FTR ±600
   Ruhe vs −9489 Spitze, TEST_PRES_AMPLITUDE `threshld` 3000.
9. **Notgrenze:** 15000 (ANNAHME Rolle von `pres_chk_hold`; BELEGT
   Existenz). Größer als normale Spitze (~9500), kleiner als defekt
   (50000+).
10. **Ruhetest:** `dif ≤ 3000` über 64 Samples (ANNAHME Analogon zu
    PRES_CHECK `dif=55285` über 7668 Samples + `threshld` 3000).
11. **can_brk:** UND-Regel (best≤53 UND step≤42 UND zoft≥0,040 → False)
    aus genau 1 Beobachtung (T58). Nachbarschafts-Vergleich
    (`final deutlich über Umgebung`) nur in `K2_PRTOUCH_MESH`
    (ANNAHME Schranke 0,05 mm).
12. **zoft-Semantik:** `Z = A + zoft` mit `A` = Z bei Trigger (tiefster
    Punkt). BELEGT `C=A+B` an 93 Punkten, `zoft` 0,010–0,040.
13. **Swap:** Nur Zeitbasis (BELEGT Fehlertext + TEST_SWAP + R/W-Richtung);
    `ticks`-Fusion als `(t−t_last)/freq` ohne extra Offset (BELEGT
    `step_bst_time == pres_bst_time` an allen 99). Pegel/Flanke offen.
14. **Stepper-OIDs:** Auto-Lookup via `get_oid/_oid/oid` (ANNAHME je nach
    Upstream-Version); Fallback manuelle `stepper_*_oid` in Config.
15. **Klipper-APIs (alle ANNAHME, Version beachten):**
    `register_config_callback`, `create_oid`, `add_config_cmd`,
    `lookup_query_command(cmd, resp, oid=)`, `ppins.lookup_pin`,
    `mcu.get_enumeration('pin')`, `toolhead.manual_move/wait_moves/
    get_position`, `heater.get_temp()`, `bed_mesh.get_mesh_limits()`,
    `SET_KINEMATIC_POSITION`, Reactor-`pause`. Vor Gerätetest gegen
    Upstream-Quellen prüfen (s. §5).
16. **Upstream-Probe-API:** Bewusst NICHT voll implementiert (zu
    versionsabhängig). Stattdessen eigener `K2_PRTOUCH_MESH` (ANNAHME
    `bed_mesh`-Übergabe) + `K2_PRTOUCH_HOME_Z` via
    `SET_KINEMATIC_POSITION` (homing_override-Vorschlag oben).
17. **Temperaturkomp/Fallkomp/Outlier/Gauß/Regional:** Weggelassen
    (`samples=1`, `z_offset=0`, Komp rein per `zoft`; im Nivellierungs-Log
    0 Zeilen → still oder inaktiv). Bei Bedarf nachrüsten
    (`prth_tmp_comp`, `bed_mesh_fall_comp_*`, `probe_outlier_check`).

## 5. Vor dem ersten echten Test am Gerät (Pflicht)

1. **Config gegen Upstream prüfen:** Klipper-Version notieren;
   `register_config_callback`-, `lookup_query_command`-,
   `manual_move`/`wait_moves`-, `SET_KINEMATIC_POSITION`-Signaturen in
   den Upstream-Quellen (`klippy/mcu.py`, `toolhead.py`, `homing.py`,
   `probe.py`, `gcode.py`) gegenlesen. Fakes ersetzen keine API-Prüfung.
2. **Pins per Ghidra/Live-Mitschnitt belegen:** `clk/sdo`-Belegung
   (PB13/PB14-Annahme!), `pres_idx` (0/1 → PB13/PB14), Stepper-OIDs,
   `lmt_dead`/`ned_lftr`-Skalierung, `is_src/sta_swap`-Kodierung. Erst
   mit gezieltem MCU-Debug-Mitschnitt (`start/read/resault/stop/ack`
   mit `ticks/tri_chxs/buf_len/acq_tick`) sind die Roh-Frames beweisbar
   (SPEZIFIKATION Offene Punkte). Bis dahin: NUR DRY_RUN + READ +
   TEST_SWAP.
3. **Trockenlauf ohne Bewegung:** `allow_move: False` lassen;
   `K2_PRTOUCH_TEST_SWAP`, `K2_PRTOUCH_READ`, `K2_PRTOUCH_PROBE DRY_RUN=1`
   müssen Plan + Ruhewerte liefern, ohne dass sich Z bewegt.
4. **Erste echte Antastung vorbereitet:** Bett frei, Düse sauber,
   Hand am Notausschalter; `step_mm` 0,02, `max_travel` klein (z.B.
   1,0 für den allerersten Versuch über dem Bett), `z_lift` 2,0;
   Temperaturen 140/50 einhalten (Warnung ernst nehmen, selbst heizen
   per Stock-Makro G29-Fluss, nicht per Modul).
5. **Logs sichern:** `klippy.log` + `K2_PRTOUCH_STATUS` nach jedem
   Versuch; bei `emergency/max_travel/timeout/can_brk False` sofort
   stoppen, Ursache (ANNAHMEN §4) prüfen, keine Wiederholung mit
   `allow_move` erzwingen.
6. **Mesh erst nach Einzelpunkten:** Erst mehrere `K2_PRTOUCH_PROBE`
   an Bettmitte (Streuung < 0,02?), dann `K2_PRTOUCH_MESH COUNT=3`,
   Matrix gegen Stock-Profil (`default` −0,061…0,224) plausibilisieren.
   `can_brk False` → versetzten Wiederholungswert verwenden (wie T59),
   schlechten Wert NICHT ins Mesh übernehmen.

## 6. Tests

`python3 test_k2_prtouch.py` (ohne Klipper, deterministische Fakes):

- (a) Kontakt bei bekannter Höhe → `|z−wahr| < 0,02`, `zoft` plausibel.
- (b) kein `down`-Schritt > `step_mm`, Summe ≤ `max_travel`; Config-Härten
  (`step_mm>0,05`, `speed>5` → ValueError); `max_travel`-Abbruch begrenzt.
- (c) `emergency_always` → Status `emergency` nach ≤2 Schritten +
  angehoben (`z > min_z`).
- (d) `allow_move=False` / `DRY_RUN=1` → 0 Moves, Z unverändert.
- (e) `swap_ok=False` → `swap_error` (Stock-Wortlaut
  `PR_ERR_CODE_SWAP_PIN_DETECTI`); `rest_noisy` → `rest_error`; beide vor
  Bewegung (0 Moves).
- (f) alle 17 Formatstrings byte-exakt in beiden JSON-Dictionaries
  (inkl. `aqc_tick`/`resault_*`).
- (g) Stopp spätestens 1 Schritt nach Kontakt (`wahr−min_z ≤ step_mm`),
  `waits ≥ downs` (M400 nach jedem Schritt).
- (x) `can_brk`-Heuristik: T58-Tripel → False, Normal → True.

Stand bei Abgabe: 43/43 bestanden.
