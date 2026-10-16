> **ENTWURF – am echten Gerät UNGETESTET (Stand 16.10.2026).** Klipper-Zusatzmodul `[k2_belt]` für Upstream-Klipper auf einem Pi-Host; nur offline getestet (`python3 test_k2_belt.py`, 12/12 Tests, 266/266 Prüfungen). Entwurf durch einen KI-Agenten.

# [k2_belt] – Riemenspann-Module mdlx/mdly (K2 Plus)

Offener Port von Crealitys `klippy/extras/belt_mdl.py` (Klartext-Python,
GPLv3, live identisch zu Image 1.1.3.13; lokal `belt_mdl-live.py`) für
Upstream-Klipper. Gleicher Aufbau wie `[k2_box]`/`[k2_motor]`: reine Logik
ohne Klipper testbar (`K2BeltController`), dünne Klipper-Hülle
(`PrinterK2Belt`), Buszugriff nur über den **geteilten** RS485-Bus.
Kein numpy, Python 3.9 kompatibel.

| Datei | Zweck |
|---|---|
| `k2_belt.py` | Modul `[k2_belt mdlx]` / `[k2_belt mdly]` |
| `test_k2_belt.py` | Offline-Tests (Fake-Bus, Belt-Simulator, Original-Vergleich, FakePrinter) |
| `README-k2_belt.md` | diese Datei |

Abhängigkeiten zur Laufzeit: `k2_transport.py` (Frame-/CRC-Helfer, sonst
lokaler Fallback) und ein serial485-artiger Bus (`nachbau/serial485.py`,
gleiche Instanz wie für `[k2_motor]`/`[k2_box]`).

## 1. Funktionsumfang

Busprotokoll wie Original (PROTOKOLL-485.md §1, Live-Log 12.10.2026):
Frame `[F7][addr][len][00][fun][daten][crc]`, CRC-8/ATM über `len..daten`,
Adresse **0x21 = mdlx**, **0x22 = mdly**, 32-bit-Werte big-endian,
**Timeout 1 s, retries = 4** (= 5 Sendungen), wie live bei jedem Belt-Request.

| fun | Bedeutung | Art |
|---|---|---|
| 0 | Firmware-Version → `[hal 2 B][soft 2 B]` (live `01 02` / `03 04`) | lesend |
| 2 `[03]` | Flash lesen → `[03][place][idl_adc][full_adc]` | lesend |
| 6 | ADC des Dehnmessstreifens (unsigned 32 bit) | lesend |
| 4 `[03]+12 B` | Flash schreiben, Antwort = Echo | **schreibend** (`allow_write`) |
| 8 `[dir][dist]` | Spann-Schieber bewegen, danach `dist · 0,0132 s` warten | **mechanisch** (`allow_move`) |
| Motor 0x05 | `0x81/0x82 fun 05 [02]`/`[01]` (Original `MOTOR_CONTROL`) im Rüttel-Zyklus, retries 0, nie beantwortet | **mechanisch** (`allow_move`) |

Rechenweg (identisch zum Original, ohne numpy): Gerade durch
`(idl_adc, cali_n1=140 N)` und `(full_adc, cali_n2=160 N)` → Kraft in N.
Plausibilität −300…600 N. Regelung wie Original `run_tension`: nur spannen
(Richtung 1), Schrittweite `int(min(|Ziel−Ist|,10))+2` mit Abklingen und
Feinschritten ab Schritt 20/40/50/70/110, Toleranz ±2 %, Abbruch nach
200 Schritten; `cycles` (2) × [Regelung + Rüttel-Zyklus], dann finale
Regelung und Flash schreiben.

### G-Codes (Mux-Key `MDL_NAME` wie Original)

| Neu | Stock-Alias (`stock_aliases: True`) | Wirkung | Freigabe |
|---|---|---|---|
| `KTWO_BELT_INFO MDL_NAME=mdlx` | `BELT_MDL_INFO` | Version, Flash, Fit, ADC, Kraft | – |
| `KTWO_BELT_TEST MDL_NAME=…` | `BELT_MDL_TEST` | wie INFO + Bereich `ch_best_n·(1±ch_best_error)`; Ausgabe `get_mdl_data: {'current_tension': N, 'target_error': 0/1}` | – |
| `KTWO_BELT_MOVE MDL_NAME=… POS=<abs> [DRY_RUN=1]` | `BELT_MDL_MOVE` | Schieber auf absolute Position, Position in den Flash | write + move |
| `KTWO_BELT_SET MDL_NAME=… [MDL_N=<N>] [DRY_RUN=1]` | `BELT_MDL_SET` | auf `MDL_N` (Default `ch_best_n`) spannen | write + move |
| `KTWO_BELT_CALI MDL_NAME=… DIR=0\|1\|2 [TEN=<N>] [DRY_RUN=1]` | `BELT_MDL_CALI` | DIR 0: Position := 0; DIR 1/2: ADC bei Referenzkraft als idl/full | write |

Webhook: `k2_belt_test` (Mux `MDL_NAME`, Antwort wie Original), mit
`stock_aliases` zusätzlich `belt_mdl_test`. `get_status` (Objekt
`k2_belt mdlx`): `tension` (wie Original) + `current_place`, `current_adc`,
`calibrated`, `in_range`, Versionen, Freigaben, `last_error`.

Ohne Freigabe (oder mit `DRY_RUN=1`) wird **nichts geschrieben/bewegt**:
lesende Schritte laufen (Ist-Kraft, ADC), dann kommt der Plan inkl. der
exakten Frames, z. B.:

```
[k2_belt mdlx] NICHTS gesendet – Freigabe fehlt (allow_write: True, allow_move: True). Plan:
[k2_belt mdlx]   Ist 129.70 N, Ziel 140.0 N ±2 %, Position 0
[k2_belt mdlx]   Regelung (max. 200 Schritte), erster Schritt: Schieber bewegen (fun 8): Richtung 1, Weg 11, danach 0.145 s warten → f7 21 08 00 08 01 00 00 00 0b 84
[k2_belt mdlx]   2 Rüttel-Zyklen: Motor-0x05 f7 81 04 00 05 02 17; …; M84
```

Fehler sind `command_error` (kein Klipper-Shutdown), deutsch, mit dem
Stock-Code in Klammern: 712/713 Kommunikation, 714/715 nicht kalibriert
(Flash `0xFFFFFFFF`), 716/717 Kraft unplausibel, 718/719 Regelung > 200
Schritte, 720/721 Kalibrierwert 0 bzw. unbrauchbar (gerade Codes mdlx,
ungerade mdly).

## 2. Geteilter RS485-Bus

Wie `[k2_box]` (README-k2_box.md §2/§4): Das Modul öffnet **keinen** Port.
Es holt beim ersten Befehl bzw. bei `klippy:connect` das printer-Objekt
`k2_485` aus `[k2_485]` (Modul `k2_485.py`; Fallback `k2_rs485`, `serial485`;
abweichend per `bus_object`). Ist der Port nicht nutzbar (fehlt, Batch-Modus,
Adapter gezogen), melden die Befehle einen G-Code-Fehler „RS485-Bus nicht
verfügbar …“ statt Klipper-Shutdown (Fix 16.10.2026)
– **dieselbe** `serial485.Serial485`-Instanz wie `[k2_motor]` und `[k2_box]`
(ein Port ttyS5 bzw. USB-RS485, 230400). Jede Transaktion läuft komplett
unter dem Bus-Lock (Senden bis Antwort/Timeout inkl. Retries). Die
Wartezeit nach einer Schieberbewegung (`dist·0,0132 s`) läuft **nach** der
Transaktion über `reactor.pause` – der Bus ist dann frei, Box-Polls dürfen
dazwischen (wie live: 0x0a-/0xa2-Frames zwischen den Belt-Frames). Test 11
belegt 80 verschränkte Belt-/Box-Transaktionen über eine Instanz ohne
Überlappung.

## 3. printer.cfg-Beispiel

```ini
# Voraussetzung: geteilter Bus [k2_485] (Objekt 'k2_485', wie [k2_box]),
# [k2_motor] für die Motorinitialisierung.

[force_move]
enable_force_move: True        # FORCE_MOVE im Rüttel-Zyklus (nur KTWO_BELT_SET)

[k2_belt mdlx]
ch_max_dis: 5                  # Stock-Option (im Original ungenutzt)
ch_best_n: 140                 # Soll-Riemenkraft in N
ch_best_error: 0.15            # KTWO_BELT_TEST: ±15 % (Stock-printer.cfg)
allow_write: False             # Flash schreiben (CALI/MOVE/SET)
allow_move: False              # Schieber/Motor-0x05/FORCE_MOVE (MOVE/SET)
stock_aliases: False           # True → BELT_MDL_* + Webhook belt_mdl_test
#verbose: False                # True → jeden Regelschritt/Frame melden
#addr: 0x21                    # Default aus Namen (mdly → 0x22)
#control_error: 0.02           # Regeltoleranz (Original „mistake“)
#cali_n1: 140                  # Referenzkräfte der Kalibrierung
#cali_n2: 160
#cycles: 2                     # Rüttel-Zyklen vor der finalen Regelung
#bus_object: k2_485
#cycle_gcode:                  # Default s. unten
#  @MOTOR_CONTROL NUM=1 DATA=2
#  ...

[k2_belt mdly]
ch_max_dis: 5
ch_best_n: 140
ch_best_error: 0.15
allow_write: False
allow_move: False

# Ersatz für das Stock-Makro BELT_TENSION (gcode_macro.cfg); Stock-Ablauf:
# Homen, Mesh löschen, Kopf in sichere Position, Motoren aus, INFO + SET.
[gcode_macro KTWO_BELT_TENSION]
gcode:
  G90
  {% if "xy" not in printer.toolhead.homed_axes %}
    G28 X Y
  {% endif %}
  BED_MESH_CLEAR
  # Stock: BOX_MOVE_TO_SAFE_POS (CFS-Makro) – hier eigene sichere Position
  M400
  M84
  {% set axes = params.AXES|default("XY")|upper %}
  {% if 'X' in axes %}
    KTWO_BELT_INFO MDL_NAME=mdlx
    KTWO_BELT_SET MDL_NAME=mdlx MDL_N=140
  {% endif %}
  {% if 'Y' in axes %}
    KTWO_BELT_INFO MDL_NAME=mdly
    KTWO_BELT_SET MDL_NAME=mdly MDL_N=140
  {% endif %}
```

**Rüttel-Zyklus (`cycle_gcode`).** Mehrzeilige Option; normale Zeilen gehen
per `run_script_from_command` an Klipper, `@`-Zeilen führt das Modul selbst
aus: `@WAIT <s>` (reactor.pause) und `@MOTOR_CONTROL NUM=1..4 DATA=1..12`
(Motor-0x05 an `0x80+NUM` über den geteilten Bus, retries 0, 50 ms).
Default (Upstream):

```
@MOTOR_CONTROL NUM=1 DATA=2
@MOTOR_CONTROL NUM=2 DATA=2
@WAIT 0.3
FORCE_MOVE STEPPER=stepper_x DISTANCE=-200 VELOCITY=60
FORCE_MOVE STEPPER=stepper_y DISTANCE=200 VELOCITY=60
M400
FORCE_MOVE STEPPER=stepper_x DISTANCE=200 VELOCITY=60
FORCE_MOVE STEPPER=stepper_y DISTANCE=-200 VELOCITY=60
M400
@MOTOR_CONTROL NUM=1 DATA=1
@MOTOR_CONTROL NUM=2 DATA=1
M84
```

Stock-identisch wäre (`k2_belt.STOCK_CYCLE_GCODE`, braucht die Creality-
Befehle `RESET_HOME_AXES_XY` und `MOTOR_CONTROL` als G-Code):
`RESET_HOME_AXES_XY`, `MOTOR_CONTROL NUM=1 DATA=2`, `MOTOR_CONTROL NUM=2
DATA=2`, `@WAIT 0.3`, dann dieselben FORCE_MOVE/M400, `MOTOR_CONTROL …
DATA=1` ×2, `M84`. Achtung: Die FORCE_MOVEs fahren X/Y je ±200 mm ohne
Endschalterschutz – nur mit freier Achse (deshalb `allow_move`).

## 4. Abweichungen vom Original (bewusst)

1. **Antwortprüfung strenger:** Original gab bei falschem Kopf/Adresse/
   Funktionscode nur „reset:… error“ aus und lief mit alten Werten weiter;
   hier prüft der Bus CRC/Adresse/fun (ungültige Frames → Retry), danach
   Fehler `key712`. Zu kurze Antworten → Fehler statt `AssertionError`.
2. **Flash-Echo:** Original verglich mit ODER (ein gleicher Wert genügte)
   und ignorierte das Ergebnis; hier müssen alle drei Werte stimmen.
   Bewegungs-Echo (fun 8) wird ebenfalls geprüft.
3. **Kein Schreiben ungelesener Werte:** MOVE/CALI/SET lesen den Flash
   selbst (fun 2), wenn noch kein INFO lief. Original schrieb dann die
   RAM-Startwerte `0xFFFFFFFF` über die Kalibrierung (Test 6 belegt das am
   Original). Nach INFO sind die Frames identisch zum Original (Test 5).
4. **MOVE ohne POS / CALI ohne DIR** → Fehler (Original-Default 0 hätte
   den Schieber auf 0 gefahren bzw. die Position genullt). MOVE mit
   Weg ≥ 0xFFFF → Fehler **vor** jeder Änderung (Original: NameError und
   trotzdem neue Position im Flash).
5. **CALI DIR 1/2 ohne TEN** nutzt `cali_n1`/`cali_n2` (Original: `None`
   → späterer Absturz im Fit). `cali_n1/n2` sind neu konfigurierbar, weil
   TEN wie im Original nur im RAM gilt (nicht im Flash).
6. **`idl_adc == full_adc`** → Fehler `key720` (Original: numpy-RankWarning
   und unbrauchbare Gerade).
7. **MDL_N-Default** = `ch_best_n` (Original fest 140 = Stock-Wert),
   Bereich 0 < N ≤ 300 (wie Config-Grenze).
8. **Rüttel-Zyklus konfigurierbar**, Default ohne `RESET_HOME_AXES_XY` und
   mit internem Motor-0x05 statt Creality-`MOTOR_CONTROL` (s. §3).
9. **Rechnung ohne numpy:** Zweipunktgerade geschlossen gerechnet, Kraft
   als `n1 + (adc−idl)·slope`. Abweichung zu `np.polyfit` < 1e-9 N (Live:
   129,69073523645056 N exakt bis 1e-10).
10. Ausgaben kompakt und deutsch; Original-Debugzeilen (`times:`, `adc_buf:`
    …) nur mit `verbose: True` sinngemäß. Fehler immer `command_error`,
    nie Shutdown; Fehler auch für Abschnittsnamen ≠ mdlx/mdly (Original
    warf dort gar keinen Fehler).
11. Status zusätzlich mit Position/ADC/Kalibrierzustand; Webhook-Name neu
    (`k2_belt_test`), Stock-Name als Alias.

Unverändert übernommen (auch wenn fragwürdig): Die Regelung **lockert
nie** (zu straff = nach einer Messung „fertig“), und nach dem Rüttel-
Zyklus prüft die nächste Regelung zuerst den **vor** dem Zyklus gemessenen
Wert (live 12.10.: `times:1` direkt gefolgt vom Flash-Schreiben). Die
Ergebnisse der ersten `cycles` Regelungen werden ignoriert, nur die finale
löst `key718` aus.

## 5. ANNAHMEN (unbelegt, vor Hardware-Lauf prüfen)

- **Bus-Objekt `k2_485`:** Upstream hat keinen 485-Manager; seit 16.10.2026
  stellt `[k2_485]` (`k2_485.py`) die gemeinsame `Serial485`-Instanz bereit
  (config-pi/k2-module.cfg).
- **Blockierender Bus im Reactor:** `transact_full` blockiert bis 5 s
  (5 × 1 s) im Klipper-Hauptthread – wie die übrigen Pi-Module; für den
  Belt-Ablauf unkritisch (Druck läuft nicht), aber kein reactor-freundliches
  Warten.
- **Motor-0x05-Semantik:** `DATA=2` vor und `DATA=1` nach den FORCE_MOVEs
  (live ohne Antwort). Vermutlich Regelung aus/an der Closed-Loop-Motoren
  für das freie Durchschieben – nicht belegt. Timeout 50 ms aus dem
  Log-Abstand (≈ 55 ms bis „timeout“).
- **`RESET_HOME_AXES_XY`** existiert in keinem lesbaren Quelltext des
  Images; ANNAHME: setzt nur den XY-Homing-Zustand zurück → upstream durch
  das M84 vor dem Ablauf und am Zyklusende abgedeckt.
- **Einheiten:** `current_place`/Schieberweg laut Original-Kommentar 1 µm
  (andernorts 0,01 mm); live nach dem Spannen 147. `ch_max_dis` (5 mm) wird
  wie im Original nicht ausgewertet.
- **Kalibrierung DIR 1/2** setzt eine definierte Referenzkraft (140/160 N)
  am Dehnmessstreifen voraus (Original-Kommentar: „vor allem ohne
  montierten Riemen“) – wie die Kraft aufgebracht wird, ist unbelegt.
- **Version:** Hardware-/Softwareversion als 2 Hex-Bytes (`0102`/`0304`);
  Zerlegung in Major/Minor unbelegt.
- **Upstream-API** gegen den lokalen Quellbaum geprüft
  (`register_mux_command`, `register_mux_endpoint`, `run_script_from_command`,
  `reactor.pause(waketime)`, `gcmd.get_float(above=, maxval=)`,
  `config.get`/`config.error`), aber nicht in einem laufenden Klipper.

## 6. Erstlauf am Gerät (Reihenfolge)

1. `[k2_belt mdlx]`/`[k2_belt mdly]` mit `allow_write: False`,
   `allow_move: False`, `verbose: True`. Bus-Objekt muss vorhanden sein
   (Meldung bei `klippy:connect`, sonst „RS485-Bus nicht verdrahtet“).
2. **Nur lesen:** `KTWO_BELT_INFO MDL_NAME=mdlx`, dann `mdly`. Erwartung
   (live 12.10.): Version `0102`/`0304`, plausible idl/full (nicht
   `0xFFFFFFFF`), Kraft ≈ 130–140 N. `KTWO_BELT_TEST` → `target_error 0`.
3. Gleichzeitig Box/Motoren beobachten (geteilter Bus): keine
   Timeouts/CRC-Fehler in `k2_485`-Statistik, CFS-Poll läuft weiter.
4. **Plan prüfen:** `KTWO_BELT_SET MDL_NAME=mdlx DRY_RUN=1` – erster Schritt
   und Zyklus-Frames ansehen. `cycle_gcode` an die eigene Maschine anpassen
   (Wege/Stepper-Namen, `[force_move] enable_force_move: True`).
5. Erst dann `allow_write: True` + `allow_move: True` für **eine** Achse,
   Riemen frei, Kopf in sicherer Position, `KTWO_BELT_SET MDL_NAME=mdlx`.
   Danach `KTWO_BELT_INFO` (Position im Flash) und `KTWO_BELT_TEST`.
6. `KTWO_BELT_MOVE`/`KTWO_BELT_CALI` nur bei Bedarf (CALI nur mit
   Referenzkraft, s. ANNAHMEN). Danach Freigaben wieder auf False.

## 7. Tests

```bash
cd pi-host
python3 test_k2_belt.py     # ERGEBNIS: ok (12/12 Tests, 266/266 Prüfungen ok)
```

1. Frame-Bau: 9 Requests == Live-Wire (`21 03 00 00`, `21 04 00 02 03`,
   `21 08 00 08 01 00 00 00 0b`, Flash-Write, `81 04 00 05 02` …), CRC ==
   serial485 und == 10 Live-Antworten (u. a. `…3d`, `…f0`, `…4c`).
2. Parsing der Live-Antworten (Version, Flash place 147, ADC, Echos),
   Fehlerframes (Adresse/CRC/Kopf/Länge).
3. Rechenwege gegen Live-Werte mdlx/mdly (Steigung, Kraft bis 1e-6 N) und
   12 Live-Regelschritte (Schrittweite je `times:N`).
4. Fit/Kraft gegen das **Original** (direkt importiert mit Attrappen für
   `BoxAction`/Klipper, numpy falls vorhanden) über 5 Kalibriersätze.
5. Komplettabläufe Port == Original am gleichen Simulator: INFO, TEST, SET
   (≈ 20 Regelschritte + 2 Zyklen), MOVE, CALI 1/2/0 – identische Wire-
   Frames, G-Codes, Pausen, Kraft, Position, Flash; mdly; „zu straff“;
   SET-Timeout 3×200 Schritte (alle Schrittweiten-Zweige) mit key718.
6. Fehlerfälle (714/715/716/712/720, Echo-Abweichung, Config-Fehler) +
   Beleg der Original-Schwäche (CALI ohne INFO zerstört Kalibrierung).
7. Freigabe-Sperren (keine / nur write / nur move / DRY_RUN): kein fun 8,
   kein Motor-0x05, kein G-Code, fun 4 nur bei erlaubtem CALI; Plan mit
   Frames.
8. Retries über echtes `serial485.Serial485`: 4 verschluckte Antworten →
   Erfolg mit 5. Sendung; 5 → Fehler nach genau 5 Sendungen; Parameter
   Belt 1 s/4, Motor-0x05 50 ms/0.
9. Default-Zyklus: Motor-0x05-Frames == Live, keine Creality-G-Codes.
10. Klipper-Hülle mit FakePrinter: Mux-G-Codes mdlx+mdly, Aliase, Webhook,
    `command_error` statt Shutdown, relative Pausen, ohne Bus, Config-Fehler.
11. Geteilter Bus: Belt-Messungen + Box-Polls parallel, keine Überlappung.
12. Python-3.9-Syntax (`ast.parse(feature_version=(3,9))`), kein numpy.
