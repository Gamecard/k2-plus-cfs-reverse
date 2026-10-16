# PRTOUCH-LESETEST-BEFUND 16.10.2026 – Sensor live gelesen, Stock-Modul emuliert

**Kurz:** Der eingebaute Kraftsensor ist **am echten Gerät gelesen** – mit unserem
`pi-host/k2_prtouch.py`, ohne jede Bewegung. Dabei sind zwei Annahmen bestätigt,
eine widerlegt und korrigiert worden (Datenformat), und ein früherer Befund ist
**widerlegt** (Z-Stopp, s. §3).

## 1. Emulation des Stock-Moduls (ohne Hardware)

`prtouch_v3_wrapper.cpython-39.so` wurde auf dem Drucker in einem **eigenen
Prozess** geladen (klippy-env-Python 3.9), alle Klipper-Objekte als protokollierende
Attrappen, echtes `pins.PrinterPins`. Werkzeug: `pi-host/emu_prtouch_stock.py`.
Das laufende Klipper blieb unberührt, kein Seriell-Zugriff.

Mitgelesene MCU-Konfiguration (wörtlich):

```
mcu        config_endstop oid=1 pin=PC7 pull_up=0
mcu        config_trsync oid=2   (+ trsync_start, stepper_stop_on_trigger nachgeschlagen)
mcu        config_prtouch_step oid=3 oid_xstp=<x> oid_ystp=<y> oid_zstp=<letzter Z-Stepper> swp_pin=PC7
nozzle_mcu config_prtouch_pres oid=1 idx=0 swp_pin=PA15 clk_pin=PB13 sdo_pin=PB14
nozzle_mcu config_prtouch_apax oid=1 oid_estp=<extruder>
```

- **clk = PB13, sdo = PB14** – BELEGT (war ANNAHME 2). `is_init` = False.
- `oid_zstp` = **letzter** Z-Stepper (K2: `stepper_z1`).
- **TEST_SWAP** (BELEGT, Erfolgstabelle per variierter Attrappen-Antwort):
  `stop_prtouch_pres sta_swap=0` → `stop_prtouch_step` → `sta_swap=1` →
  `stop_prtouch_step`. Erfolg genau dann, wenn die Main-MCU erst `expar0=0`,
  dann `expar0=1` meldet. `PRES_VER`/`STEP_VER` = `expar1` der ersten Antworten
  (live 71/71).
- **READ_PRES** (Lesemodus): `start_prtouch_pres [60, 93750, 0, 0, 0, 128, 128, 0, 128]`,
  danach **asynchrone** `resault_prtouch_pres`-Pakete.
- Weitere Stock-G-Codes: TEST_PRES, READ_PRES, TEST_PRES_AMPLITUDE, WAVE_APAX,
  AUTO_APAX, TEST_APAX, PRES_CHECK, TEST_SWAP, NOZZLE_CLEAR, SAFE_MOVE_Z,
  PRTOUCH_PLATFORM_DET, BED_MESH_CALIBRATE/BED_MESH_PROFILE (überschrieben).

## 2. Live-Lesetest an der Düsen-MCU (ohne Bewegung)

Werkzeug `pi-host/prtouch_lesetest.py`: Klipper-Dienst gestoppt, Düsen-MCU über
Klippys `serialhdl` direkt (wie `console.py`): `reset` → `allocate_oids` →
**nur** `config_prtouch_pres` (wie Stock) → Messungen → `reset`. Keine Heizung,
keine Stepper, Main-MCU nicht angesprochen. Danach Klipper-Start
(ohne FIRMWARE_RESTART **ready**; Extruder antwortete sofort, weil die Düsen-MCU
frisch zurückgesetzt war).

Vorher, über Stock-G-Codes im laufenden Klipper: `TEST_SWAP` → „Swap Test Success“
(PY 78 / PRES 71 / STEP 71); `READ_PRES` → Ruhe ≈ −1 427 550, DIF 1003, 0,78 ms.

| Test | Ergebnis |
|---|---|
| A: Stock-Lesemodus, asynchron 2 s | ~320 Pakete, ~2 520 Samples, **0,7907 ms** Abstand, Werte −1 428 6xx … −1 427 2xx (DIF 1 167–1 292), Ruhetest ok |
| B: `read_prtouch_pres` im Lesemodus | nur Nullen – im Lesemodus streamt die MCU asynchron, der Ring bleibt leer |
| C/D: **unsere Antast-Parameter** `(60, 93750, 5, 1, 8, 4000, 10000, 500, 200)` | keine asynchronen Pakete, kein Fehlauslöser (`tri_chxs 0`), `read_prtouch_pres` liefert echte Rohwerte (DIF 756–803, 0,791 ms) |

### 2.1 Paketformat (BELEGT, 315+3 von 318 Paketen exakt)

`ticks` und `datas` von `resault_prtouch_pres` sind **gepackt** (nicht feste
4-Byte-Werte, ANNAHME 1 widerlegt):

```
[n][Größenmaske: ceil(n/4) Byte, Big-Endian][Wert 0 absolut][Wert 1..n-1 als Delta]
```

Je Sample i 2 Bit der Maske (LSB zuerst): Länge = Feld+1 Byte, jeder Wert
little-endian mit Vorzeichen. Beispiel ticks `08 aaab | 4 B Startzeit | 7× 3 B Delta`
(Delta ≈ 94 900 Ticks = 0,79 ms bei 120 MHz). Datenbeispiel `08 1012 | f0 34 ea`
= −1 428 240, danach 1–2-Byte-Deltas. Bei n = 9 ist die Maske 3 Byte lang.

### 2.2 Ringpuffer und `read_prtouch_pres`

- `buf_len` = 64; je Abfrage höchstens **8 Samples**; `idx` = Ringposition
  (wird zurückgemeldet), Ring wird laufend überschrieben (Wiederholung von
  `idx=0` liefert neuere Werte). Für ein 64er-Fenster: 8 Blöcke lesen und nach
  Tick sortieren (`merge_ring`).
- `is_src=1` = **Rohwerte** (Skala wie Stock-READ_PRES); `is_src=0` = anderer,
  kleiner (gefilterter) Kanal. ANNAHME 7 war vertauscht.

## 3. Korrektur: Z-Stopp läuft über einen normalen Klipper-Endstop

Das Stock-Modul ist ein **`PRTouchEndstopWrapper`** mit `config_endstop` auf
**PC7** (= `step_swap_pin`) plus `config_trsync`, `stepper_stop_on_trigger`,
Homing-Event-Handlern (`homing:homing_move_begin/end`, `home_rails_begin/end`) und
Attributen `_home_cmd/_oid/_pin/_invert/_mcu`. `prtouch_v3.py` legt daraus das
`probe`-Objekt an (`probe:z_virtual_endstop` für `stepper_z/z1`).

Damit ist **PRTOUCH-STOPP-BEWEIS §1 („kein Standard-MCU_endstop auf
step_swap_pin“) widerlegt.** Die prtouch-MCU-Befehle selbst rufen kein endstop/trsync auf (Handler-Adressen
korrigiert in HANDLER-TABELLE-BEFUND-2026-10-16.md) – der Stopp entsteht **zusätzlich** über die
Swap-Leitung: Düsen-MCU (PA15) → Main-MCU (PC7) → Klipper-Endstop → trsync stoppt
die Z-Stepper in der MCU.

**Bedeutung für den Pi:** Mit Upstream-Klipper reicht vermutlich ein normaler
Endstop/Probe auf `!PC7` an der Main-MCU, wenn vorher die Düsen-MCU per
`start_prtouch_pres` (Antast-Parameter) scharf geschaltet wird. Das ist einfacher
und sicherer als die zeitgesteuerte Schrittfahrt im Entwurf. Die PA15-Umschaltung bei Kontakt ist inzwischen **live belegt** (§6).

## 4. Änderungen an `pi-host/k2_prtouch.py`

- `decode_packed` + `packing: packed` (Default; `struct` = alte Annahme, nur Tests).
- `KlippyPresMcu.read_pres` liest 8 Blöcke (`READ_CHUNK = 8`) und führt sie per
  `merge_ring` zusammen; `is_src=1`. `read_step` analog (ANNAHME, Main-MCU live
  noch nicht gelesen).
- Echter TEST_SWAP-Ablauf (`set_swap`/`read_swap`) im Controller.
- `add_config_cmd` ohne `is_init` (wie Stock); `oid_zstp` = `stepper_z1`
  (Fallback `stepper_z`).
- Tests 73/73 (neu: echte Pakete, Ringlesen mit Live-Blöcken, Swap-Tabelle).

## 5. Weiter offen

- Abbildung der Antast-Parameter (`ned_*`, `*_hold`, `lmt_dead`) auf die
  Stock-Werte beim echten Antasten – per Emulation eines Stock-Antastlaufs klärbar.
- Main-MCU-Seite (`read_prtouch_step`, Packung, Ring) live.
- Stopp unter Bewegung (trsync + `stepper_stop_on_trigger`) – erst auf dem Pi
  bzw. mit Einzelfreigabe.

## 6. Fingerdruck-Test: Düse schaltet die Swap-Leitung (BELEGT)

Werkzeug `pi-host/prtouch_fingertest.py`. Klipper gestoppt, Düse kalt (24 °C).
Main-MCU: reset, **nur** `config_endstop oid=0 pin=PC7 pull_up=0`, Pegel per
`endstop_query_state` alle ~50 ms. Düsen-MCU: reset, nur `config_prtouch_pres`
(wie Stock), Antast-Modus mit unseren Parametern
`(60, 93750, 5, 1, 8, 4000, 10000, 500, 200)`. Nach jeder Auslösung 2 s später
`stop_prtouch_pres sta_swap=1` + erneut scharf. 100 s Messfenster, Benutzer drückt
die Düse von Hand leicht von unten. Keine Bewegung, keine Heizung.

| Beobachtung | Wert |
|---|---|
| PC7 in Ruhe (scharf) | 1 |
| Auslösungen | **19**, jede: PC7 → 0 **und** `tri_chxs = 1` |
| Kraftausschlag bei Auslösung (Rohwert − Ruhe) | 4 462 … 220 704 (kleinster ≈ `min_hold` 4000) |
| Fehlauslöser in Ruhe (24 s + 19 s, Drift bis 2 397) | **0** |
| nach Druckserie Restversatz ≈ 6 000, neu scharf | keine Auslösung → Schwelle relativ zum Ruhewert beim Scharfschalten |
| `stop … sta_swap=1` | setzt PC7 wieder auf 1 (Re-Arm funktioniert) |
| unaufgeforderte Pakete im Antast-Modus | 0 |

**Folge für den Pi-Ersatz:** Der Mechanismus ist vollständig Standard-Klipper-
tauglich: Düsen-MCU per `start_prtouch_pres` scharf schalten → Kontakt zieht
PA15/PC7 auf 0 → normaler Endstop/Probe auf **`!PC7`** an der Main-MCU (trsync stoppt
Z in der MCU, wie Stock). Nach dem Antasten `stop_prtouch_pres sta_swap=1`.
Damit wird die zeitgesteuerte Schrittfahrt des Entwurfs zum Rückfall; Hauptweg
für `[k2_prtouch]` sollte ein Upstream-`[probe]` mit Arm-/Disarm-Hook werden.


## 7. Stock-Antastlauf emuliert: Parameter-Abbildung (BELEGT)

`pi-host/emu_prtouch_stock.py` ruft jetzt zusätzlich die Endstop-API des Stock-Objekts
auf (`multi_probe_begin`, `probe_prepare`, `home_start`, `home_wait`, `probe_finish`,
`multi_probe_end`, `query_endstop`; trsync als Attrappe). Das Stock-Objekt ist ein
vollwertiger Klipper-Endstop (`home_start(print_time, sample_time, sample_count,
rest_time, triggered)`, `home_wait`, `query_endstop`, `probe_prepare/finish(hmove)`).

Ablauf beim Antasten (wörtlich mitgelesen, Live-Konfig, beide MCUs 120 MHz):

```
probe_prepare:  mcu        cont_prtouch_step [3]            -> resault_prtouch_step_cnt
                nozzle_mcu start_prtouch_pres [1, 60, 93750, 5, 1000, 800, 4000, 10000, 500, 128]
                mcu        start_prtouch_step [3, 46875]
home_start:     mcu        endstop_home [1, clock, sample_ticks, 4, rest_ticks, pin_value=0, trsync=2, reason=1]
home_wait:      mcu        endstop_home [1, 0, …] (Ende), trsync_trigger
probe_finish:   mcu        stop_prtouch_step [3]
                nozzle_mcu stop_prtouch_pres [1, sta_swap=1]
                nozzle_mcu read_prtouch_pres [1, 2, 0, 0, 0]          (Status)
                nozzle_mcu read_prtouch_pres [1, is_src 0|1, 0, idx, 64-idx]  idx = 0, 8, …, 56
                mcu        read_prtouch_step [3, 0, 0], dann [3, idx, 64-idx]
```

Abbildung Konfig → MCU-Parameter (eindeutig durch gezielte Variation:
`pres_tri_fter 7,3,0.25`, `pres_tri_hold 4100,10200,530`, `pres_ded_tkms 96.5`,
`pres_acq_tkms 0.5`, `step_acq_tkms 0.5`, `pres_cfg_regs 44` →
`[44, 60000, 7, 3000, 250, 4100, 10200, 530, 96]`, `aqc 60000`):

| MCU-Parameter | Quelle `[prtouch_v3]` | Stock |
|---|---|---|
| `cfg_regs` | `pres_cfg_regs` | 60 |
| `acq_tick` | `pres_acq_tkms` ms × Düsen-Takt | 0,78125 → 93 750 |
| `ned_tftr` | `pres_tri_fter[0]` | 5 |
| `ned_hftr` | `pres_tri_fter[1]` × 1000 | 1 000 |
| `ned_lftr` | `pres_tri_fter[2]` × 1000 | 800 |
| `min/max/add_hold` | `pres_tri_hold` | 4000 / 10000 / 500 |
| `lmt_dead` | `int(pres_ded_tkms)` (abgeschnitten) | 128 |
| `aqc_tick` | `step_acq_tkms` ms × Main-Takt | 0,390625 → 46 875 |

Damit waren im Entwurf falsch: `ned_hftr` 1 (→ 1000), `ned_lftr` 8 (Skala 10 statt 1000),
`lmt_dead` 200 (→ 128), `aqc_tick` 45 700 (→ 46 875). Der Fingerdruck-Test (§6) lief noch
mit den alten Werten und löste trotzdem zuverlässig aus. `is_src`: 0 = FTR, 1 = SRC
(Stock-Beschriftung bestätigt §2.2). Nach `stop` liest Stock den eingefrorenen Ring
vollständig aus.
