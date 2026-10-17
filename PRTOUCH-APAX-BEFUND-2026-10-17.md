# PRTOUCH-APAX-BEFUND 17.10.2026 – Was `*_prtouch_apax` ist und ob der Pi es braucht (statisch)

**Kurz:** „apax“ ist der **Messkanal für die automatische Pressure-Advance-Kalibrierung** (Host-Hilfetext:
„The dynamic pa detection starts …“) und gleichzeitig der **Rohdaten-Strom für `PRES_CHECK`**, den
Dehnungsstreifen-Selbsttest beim Düsenabstreifen. Die Düsen-MCU streamt dabei ohne Filter und ohne Auslösung
jedes CS1237-Kraftsample (≈ 1280/s) zusammen mit dem **momentanen Schrittintervall des Extruder-Steppers**
(`espds`, mit Richtungsvorzeichen, 0 = steht). Es gibt **keine Achse und keinen Düsen-„apex“**, keinen
Beschleunigungssensor, keinen PA15-Trigger. Für Antasten, Bed-Mesh, Z-Offset und Z-Homing ist apax **nicht nötig**.

**Korrektur an Vorbefunden:** „apax nicht im Normalpfad / 0 Treffer“ (README-k2_prtouch §1, PRTOUCH-BEFUND §1,
frühere lokale Analyse) stimmt nur für die G-Codes `AUTO_APAX`/`WAVE_APAX`. **`PRES_CHECK` benutzt
`start_prtouch_apax`/`stop_prtouch_apax`** und läuft bei Stock in **jedem G28 mit Z** (Homing-Override,
`sensorless.cfg`). Live belegt über die Busrate der Düsen-MCU (§4).

Methode: nur statisch, kein Zugriff auf Drucker/Pi. Firmware: llvm-objdump-Listing der Düsen-FW (Kopie aus
lokale Arbeitsdateien), Befehlstabelle mit dem dortigen `tabelle.py` neu ausgelesen. Host-Wrapper (Cython, ARM):
eigene Namensspur-Skripte über `llvm-objdump` + die String-/Konstantentabellen aus frühere lokale Analyse
(Tabellen stichprobenartig gegen das Listing geprüft). Ghidra wurde nicht zusätzlich gebraucht, weil alle
apax-Handler kurz sind und vollständig im Listing gelesen wurden.
Kennzeichnung: **B** = Beobachtung (Adresse/Datei:Zeile), **F** = Folgerung, **ANNAHME** = nicht belegt.

---

## 1. MCU-Befehle (Düse `noz0_130_G30-noz0_021_000.bin`, Basis 0x08003000)

Befehlstabelle (B, `scripts/tabelle.py` aus lokale Arbeitsdateien gegen das Düsen-Dictionary, Main identisch):

| ID | Befehl (Dictionary) | Handler | Antwort |
|---|---|---|---|
| 40 | `config_prtouch_apax oid=%c oid_estp=%c` | **0x0800340C** | keine (Config-Befehl) |
| 39 | `start_prtouch_apax oid=%c cfg_regs=%c acq_tick=%u` | **0x08003ABC** | `ack_prtouch err=0 expar0=0 expar1=acq_tick` |
| 38 | `stop_prtouch_apax oid=%c` | **0x0800347C** | `ack_prtouch err=0 expar0=0 expar1=0` |
| 94 | `resault_prtouch_apax oid=%c ch=%c len=%c ticks=%.*s datas=%.*s espds=%.*s` | Sender im Leerlauf-Task 0x08007D42–0x08007D48 (Format 0x080097A0) | asynchron |

Es gibt **kein `read_prtouch_apax`** – die Daten kommen nur als Strom. `pi-host/prtouch_mcu_formats.json`
stimmt (Namen, Typen, IDs).

### 1.1 Zustand A = 0x20000044 (B)

| Wort | Inhalt | Beleg |
|---|---|---|
| A[0] | oid (Argument) | 0x0800340C–0x08003412 (`ldrd`/`strd` args[0..1]) |
| A[1] | oid_estp (Argument, **ungeprüft**) | dito |
| A[2] | acq_tick (0 = aus) | start 0x08003B46, stop 0x0800348C |
| A[3] | cfg_regs (CS1237-Konfig) | start 0x08003B3C |
| A+0x790 (0x200007D4) | Zeitpunkt der letzten Abtastung (nur ADC-Modus) | start 0x08003B36, Task 0x08007D7A/0x080081CE |
| 0x20000054 + ch·0xA0 | Packer ticks / +0x280 datas / +0x500 espds, je Kanal 0..3 | start 0x08003ACA–0x08003B22, Task 0x08007CB0–0x08007CF6 |

A liegt in `.bss` (Reset-Handler 0x08003C12–0x08003C1A nullt 0x20000034–0x20002468) → nach jedem MCU-Start
ist apax **aus**, solange niemand `start_prtouch_apax` schickt (B).

### 1.2 Handler (B)

- **config** 0x0800340C: speichert nur oid und oid_estp, **keine** oid-Allokation, keine Typprüfung.
- **start** 0x08003ABC: setzt die drei Packer je Kanal zurück, dann
  **`memset(0x200007F0 = S+0x14, 0, 0xDA0)`** bei 0x08003B24–0x08003B2C. Das ist exakt der Datenbereich des
  pres-Zustands S = 0x200007DC (Wort 5 … 0x36C: letzte Lesezeiten, Hochpass-Summen, Konfig-Handshake-Zähler,
  zurückgelesenes CS1237-Register, Sample-Zähler, ticks/roh/gefiltert[4][64], Hochpassfenster) – dieselbe
  `memset` macht auch `start_prtouch_pres` (0x08004378–0x08004380). **Nicht** gelöscht werden oid, ADC-Modus,
  Kanalzahl, `tri_chxs` und die pres-Parameter ab S+0x12B8 (inkl. pres-`acq_tick`). Danach A+0x790 := CYCCNT,
  A[3] := cfg_regs, A[2] := acq_tick, `ack_prtouch(oid, 0, 0, acq_tick)` (0x08003B3E–0x08003B4C).
- **stop** 0x0800347C: nur A[2] := 0, `ack_prtouch(oid, 0, 0, 0)`. Kein Leeren angefangener Pakete, kein PA15.
- `start_prtouch_pres`/`stop_prtouch_pres`/Auslösung schreiben A **nicht** (Literal-Suche nach 0x20000044 im
  Image: nur 0x08003418, 0x08003498, 0x08003B60, 0x08007DB0, 0x08008380 = config/stop/start/Task).

### 1.3 Abtast-Task (B, Leerlauf-Schleife, 0x08007C52–0x08007D8C, nach pres- und step-Task)

```
wenn A.acq_tick == 0 oder S.kanalzahl == 0: nichts
ADC-Modus (S[1]≠0): nur wenn seit A+0x790 ≥ acq_tick Takte vergangen (0x08007D74–0x08007D88)
Bitbang (K2):       bei jedem Durchlauf, acq_tick ist nur Ein/Aus
je Kanal ch < S.kanalzahl:
  t = CYCCNT
  wenn S.cfg_zurueck[ch] ≠ A.cfg_regs: Konfig-Handshake (schreiben 0x080081B8 / lesen 0x08007C98, wie pres)
  sonst: v = CS1237 lesen (0x080050D0)               # 0x80000000 = noch kein DRDY → überspringen
  ticks.pack(t); datas.pack(v)                         # Packer 0x08006524 (wie pres)
  st = oid_lookup(A.oid_estp, Stepper)                 # 0x08007CD0, Typmarke 0x08005FD1 = config_stepper
  espd = (st.count ≠ 0) ? st.interval · (st.flags & 2 ? −1 : +1) : 0     # 0x08007CD6–0x08007CF0
  espds.pack(espd)
  wenn Bytes(ticks+datas+espds) > 41: alle drei abschließen (0x080064D8) und senden:
      resault_prtouch_apax(oid = S[0] (pres-oid!), ch, len = Anzahl, ticks, datas, espds)
```

- **espd-Bedeutung (B/F):** Stepper-Struktur passt auf Upstream `struct stepper` (config_stepper 0x08005FD0:
  Flags +0x3C mit 4 = SF_INVERT_STEP, 0x10 = SF_SINGLE_SCHED; Upstream `src/stepper.c:40-58`): +0x0C
  `interval` = aktuelles Schrittintervall in MCU-Takten, +0x14 `count` = verbleibende (Halb-)Schritte, Flag-Bit 1
  = `SF_NEXT_DIR` (**ANNAHME** gleiche Bedeutung wie Upstream). **F:** espd = ±Takte pro Extruderschritt
  (Geschwindigkeit ∝ 1/|espd|), 0 = Extruder steht. Keine Position, keine Beschleunigung.
- **datas** = CS1237-Rohwert (s32 aus 24 Bit), **ohne** Hochpass/Tiefpass/Ausreißerfilter und **ohne**
  Schwellen/PA15 – reine Messung.
- **ticks** = DWT-CYCCNT (120 MHz) vor dem Lesen.
- **Paketgröße (F):** > 41 Byte Nutzlast → bei ticks-Delta ≈ 93 750 (3 Byte) + Druck-Delta 1–2 Byte + espd
  meist 1 Byte etwa 5–6 Samples je Paket.
- **Gefahr (B/F):** oid_lookup schlägt bei falschem `oid_estp` fehl → `sched_shutdown(0x0B)` (0x08006C46–
  0x08006C48, 0x08006C18 = shutdown). Da `config_prtouch_apax` nicht prüft, kommt der Shutdown erst **beim
  ersten Sample nach `start_prtouch_apax`** (ohne `config_prtouch_apax` ist oid_estp = 0).
- **Zusammenspiel mit pres (F):** Beide Tasks lesen dasselbe CS1237 über dieselbe Lesefunktion; der pres-Task
  läuft in jeder Schleife zuerst und bekommt das DRDY-Sample, apax sieht danach meist 0x80000000. Ein
  `start_prtouch_apax` bei **scharfem** pres löscht dessen Puffer und Sample-Zähler (Totzeit `lmt_dead` beginnt
  neu, Hochpassbezug leer) – Stock macht das nie (PRES_CHECK ist vor `_HOME_Z` beendet).

**Sicherheit:** hoch (Handler, Task, Formate), mittel (Vorzeichenbit von espd).

---

## 2. Host-Wrapper `prtouch_v3_wrapper.cpython-39.so`, Klasse `PrtouchApax`

Funktionsadressen (ELF-vaddr) aus frühere lokale Analyse; Namensspuren in `<arbeitsdateien>/spur_*.txt`.

### 2.1 Einrichtung (B)

- `__init__` 0xF776C: liest `[prtouch_v3]`-Werte (Defaults = Live-Werte, `liveconfig_prtouch_resolved.txt`):
  `apax_run_limt [5000,5000,500,5]`, `apax_apa_limt [20,180]`, `apax_run_cont 51` (3…100),
  `apax_pos_star [10.8,20,0.28,180]`, `apax_run_frdo 1.0`, `apax_max_pavl [0.1,0.16]`, `apax_ned_hold 50000`,
  `apax_oft_rest 0.0`, `apax_stp_base`, `apax_spx_cont 3`, `apax_sys_pavl` (Default 0.04), `apax_cal_fter (1,0)`,
  `pres_cfg_regs 60`, `apax_acq_tkms 0.78125`, `bed_mesh.speed` → `rdy_sped`. Übernimmt **oid und mcu von
  `prtouch.pres`** (gemeinsame oid). `activate` nur, wenn der Extruder-`step_pin` auf demselben MCU wie pres liegt
  (Spur `parse_pin … step_pin … chip … chip … activate`; beim K2 `nozzle_mcu:PB5`, `config-pi/printer.cfg:224`).
  Registriert `resault_prtouch_apax` mit `_handle_resault` und die G-Codes `WAVE_APAX`, `AUTO_APAX`,
  `TEST_APAX`, `PRES_CHECK`.
- `_build_config` 0x99BE4: `oid_estp = extruder.extruder_stepper.stepper.get_oid()`,
  `per_step_mm = …get_step_dist()`, `add_config_cmd("config_prtouch_apax oid=%d oid_estp=%d")`,
  `cmd_start`/`cmd_stop` = `lookup_query_command(start/stop_prtouch_apax, ack_prtouch)` (Tupel bei 0x9A0E0/0x9A1A4),
  `mcu_freq = CLOCK_FREQ`.
- `PRTouchEndstopWrapper._handle_home_rails_end` 0x81534–0x81550: `self.apax.pas_fila = {}` (PyDict_New +
  setattr) → **jede Z-Referenzfahrt verwirft die gemerkten PA-Werte je Filament**.

### 2.2 G-Codes (B = Namensspur, Details der Rechnung ANNAHME)

| G-Code | Ablauf |
|---|---|
| `WAVE_APAX RUN=1` (0xABEAC) | Listen `apax_time/data/espd` leeren, `start_prtouch_apax(oid, cfg_regs, seconds_to_clock(acq_tkms/1000))` = `acq_tick 93750`. |
| `WAVE_APAX RUN=0` | `stop_prtouch_apax` (0xAD71C), optional Debugdatei `…_CH%d.txt` mit Spalten `Time Data Epsd` (Zeitstempel-Name). |
| `PRES_CHECK RUN=1` (0xA8DD0) | wie WAVE_APAX RUN=1 + `chk_info` je Kanal = [min 2²⁸−1, max −(2²⁸−1), Summe 0, Anzahl 0]. |
| `PRES_CHECK RUN=0` | **`stop_prtouch_apax`** (0xA9798–0xA97D4), dann je Kanal `PRES_CHECK_INFO_CH%d min/max/avg/dif/len`; Prüfung gegen **`prtouch.pres.chk_hold`** (Spur 0xAA37C), Fehler über `check_reise` mit `key769` / `PR_ERR_CODE_PRES_NOT_BE_SENSED` (0xAA454–0xAA52C), sonst `SUCCESS: Strain gauge dynamic detection passed.` **ANNAHME** Kriterium `dif ≥ pres_chk_hold` (= 15000 live); alle Live-Werte 37 535…324 487 bestanden. |
| `AUTO_APAX` (0x10A248, 137 kB) | PA-Messung, s. 2.3. Parameter `RUN_TYPE` (0 Stop, 1 nur E vor Druck, 2 XYZE vor Druck, 3 „im Druck“), `NOZ_DLEN 0.4`, `FIL_DLEN 1.75`, `IDX_FILA`, `CUT_CONT`, `NOZ_TEMP`, `IS_SAVE`. |
| `TEST_APAX` (0x29B68) | Wiederholtest: ruft `AUTO_APAX RUN_TYPE=%d CUT_CONT=%d` (0x2A0A8) `RUN_CONT`-mal und gibt `pas=%s` (gesammelte `extruder_stepper.pressure_advance`) aus. |

`_handle_resault` (0x2AC24): entpackt ticks/datas/espds (`prtouch.unzip_data`), führt `chk_info` (min/max/
Summe/Anzahl) nach, hängt an `apax_data`/`apax_espd` an, begrenzt die Listen (Konstanten 60.0 und 1.2 neben
`acq_tkms`; **ANNAHME** ≈ 60 s × 1,2 Fenster) und zeichnet bei `msg_show` `APAX_CH%d_CHART`.

### 2.3 AUTO_APAX-Ablauf (Spurreihenfolge 0x10A248 ff.; Lokalnamen aus dem Code-Objekt-Tupel bei 0x2D0DC)

1. Wenn für `IDX_FILA` schon ein Wert in `pas_fila` steht: `SET_PRESSURE_ADVANCE ADVANCE=<wert>` (**ANNAHME**:
   danach Ende). `RUN_TYPE=0`: `stop_prtouch_apax`.
2. Düsentemperatur prüfen (`target_temp`/`smoothed_temp` gegen `min_extrude_temp`, Fehlertext „… lower than the
   temperature required …“), ggf. `M104 S%d` + `TEMPERATURE_WAIT SENSOR=extruder MINIMUM/MAXIMUM ±5`.
3. Alten PA merken (`sys_pavl`), `SET_PRESSURE_ADVANCE ADVANCE=0`, alte Grenzen merken (`sys_limt`),
   `SET_VELOCITY_LIMIT ACCEL=5000 ACCEL_TO_DECEL=5000 VELOCITY=500 SQUARE_CORNER_VELOCITY=5` (`run_limt`),
   `G92 E0`, `M83`. E-Geschwindigkeiten `max_e_spd/min_e_spd` aus `apa_limt [20,180]`, `run_pose`, π/4·d²
   (**ANNAHME**: 20/180 mm/s Linien­geschwindigkeit × 0,4 mm × 0,28 mm Schicht ÷ Filamentquerschnitt ≈ 0,9 bzw.
   8,4 mm/s E).
4. **Typ 1:** `WAVE_APAX RUN=1`, 0,1 s, `run_cont`× (51) `G1 E%.3f F%.3f` im Wechsel schnell/langsam, `M400`,
   0,2 s, `M82`, `WAVE_APAX RUN=0` – nur Extrusion, keine Achsbewegung.
   **Typ 2:** Fahrt nach `pos_star` (X 10,8 / Y 20 / Z 0,28) mit `bed_mesh.speed`, dann
   `G92 E0 / M83 / G91 / WAVE_APAX RUN=1`, `G1 X%.2f` und Linien `G1 Y%.3f E%.3f F%.3f` mit wechselnder
   Geschwindigkeit (Länge bis 180 mm) – **druckt eine Testlinie auf das Bett**; danach `G90 / M82 / WAVE_APAX RUN=0`.
   Typ 3 hat im Code keinen eigenen Fahrweg in der Spur (**ANNAHME**: Messung während eines laufenden Drucks).
5. Zurücksetzen: `SET_VELOCITY_LIMIT` alt, `SET_PRESSURE_ADVANCE` alt, `G92 E%f`.
6. Auswertung: zu wenig Daten → „Run Error!! No data was received … data_len=%d, need_len=%d“; Kraftamplitude
   unter `ned_hold` 50 000 → „No filament extrusion was detected …“. Je Geschwindigkeitswechsel wird aus Kraft-
   und espd-Verlauf (Lokale `espd_min_indx`, `data_min/max_indx`, `angle/sin/cos` = Drift-Drehung,
   `fifo_*`) ein PA geschätzt (`pa_up_vals`, `pa_down_vals`), Mediane `mid_up_pa`/`mid_down_pa`, + `oft_rest`,
   begrenzt mit `max_pavl` → Meldungen `AUTO_APAX_PA pa=%.3f, mid_up_pa=%.3f, mid_down_pa=%.3f`,
   `AUTO_APAX_UPAS/DPAS`. Ergebnis → `pas_fila[IDX_FILA]` + `SET_PRESSURE_ADVANCE`; mit `IS_SAVE=1`
   `configfile.set('prtouch default','sys_pavl',…)` + `SAVE_CONFIG`. Die genaue Formel ist statisch nicht
   rekonstruiert (**offen**, Funktion 137 kB).

### 2.4 Wer ruft es auf (B)

- `PRES_CHECK RUN=1/0`: Stock-`[homing_override]` um `BOX_NOZZLE_CLEAN`, wenn Z noch nicht referenziert
  (`config/F008_CR0CN240319C13_1/sensorless.cfg:221-227`, live `live-logs-2026-10-16/config-aktuell/sensorless.cfg`).
- `AUTO_APAX`/`WAVE_APAX`/`TEST_APAX`: in keiner Stock-Config/keinem Makro und in keinem Live-Log
  (`grep -a AUTO_APAX` über `rootfs-live`, `config`, `live-1.1.7.0-configs`: nur die `.so`). **ANNAHME:** nur
  für Werk/Service oder vom Slicer/der App per Start-G-Code aufrufbar. Live läuft ein fester
  `pressure_advance: 0.038` (`live-1.1.7.0-configs/printer-live.cfg:140`).

---

## 3. Bezug zu Antasten / Bed-Mesh / Z-Offset / Wipe

| Bereich | Bezug |
|---|---|
| Antasten, Z-Homing, Bed-Mesh, Z-Offset | **keiner** (B): apax hat keine Schwellen, schreibt PA15 nicht; kein apax-Name in den Probe-/Mesh-Funktionen der Namensspur (`spur_alle.txt`); einzige Kopplung: `pas_fila`-Reset bei Z-Homing (§2.1). |
| Wipe (`BOX_NOZZLE_CLEAN`) | **nur Diagnose:** PRES_CHECK misst während des Abstreifens, ob der Kraftsensor überhaupt reagiert (dynamischer Selbsttest). Das Abstreifen selbst nutzt apax nicht. |
| MCU-Zustand pres | `start_prtouch_apax` löscht den pres-Datenbereich (§1.2) – dasselbe macht `start_prtouch_pres`; bei Stock-Reihenfolge (PRES_CHECK fertig → `_HOME_Z` → `start_prtouch_pres`) ohne Wirkung. |
| Druck | Nur über AUTO_APAX → `SET_PRESSURE_ADVANCE` (Klipper-Standard). |

---

## 4. Live-Beleg: PRES_CHECK erzeugt den apax-Strom (B, `scripts/busrate.py` → `busrate_1012.txt`)

Klipper-`Stats` (`bytes_read` von `nozzle_mcu`, `live-logs-2026-10-15/klippy.log.2026-10-12`):

| Zeit | nozzle_mcu gelesen |
|---|---|
| 00:03:02–00:04:05 | 72–92 B/s (Ruhe) |
| 00:04:08–00:04:14 | **11 217–12 488 B/s** |
| 00:04:16,9 | `[PRES_CHECK_INFO_CH0] … dif=37535, len=13901`, `SUCCESS` (Zeilen 2992–2995) |
| 00:04:17 | 9 643 B/s (Teilintervall) |
| ab 00:04:20 | 77–88 B/s |

**F:** 13 901 Samples ≈ 10,9 s bei 1280/s; ≈ 9,7 Byte je Sample auf dem Bus. Der Strom endet genau mit
`PRES_CHECK RUN=0` (bestätigt `stop_prtouch_apax` im RUN=0-Zweig). Belegt ≈ 12,4 kB/s ≈ **54 %** der 230 400-Bd-
Düsenleitung (10 Bit/Byte).

---

## 5. Braucht der Pi das? – Bewertung

- **Für die Kernfunktionen nein** (Antasten, Z-Homing, Mesh, Z-Offset): bestätigt. `k2_prtouch.py` enthält nur
  die Formatkonstanten (`k2_prtouch.py:124-163`) und sendet nichts davon; die MCU-Seite bleibt dann aus (.bss = 0,
  §1.1). Kein Risiko durch Nichtbenutzung.
- **PRES_CHECK-Ersatz: sinnvoll, optional.** `NOTIZ-reinigung-resume.md` §2.3 begründet das Weglassen damit, dass
  `k2_prtouch` keine lange Aufzeichnung parallel zur Fahrt hat – genau das liefert apax mit drei kleinen
  Befehlen. Nutzen: erkennt einen toten/abgezogenen Sensor, den der Ruhetest nicht sieht (PRTOUCH-MCU-BEFUND §1:
  abgezogener Sensor liefert −1 mit `dif = 0`).
- **AUTO_APAX-Nachbau: nicht empfohlen** (Aufwand hoch, Formel offen, druckt Testlinien bzw. extrudiert ins
  Leere, ändert `SAVE_CONFIG`). Ein fester PA (0,038) oder Upstream-PA-Kalibrierdruck reicht.

---

## 6. Vorschläge für `pi-host/k2_prtouch.py` (Code hier NICHT geändert)

1. **Optionaler G-Code `KTWO_PRTOUCH_CHECK RUN=1|0`** (PRES_CHECK-Analogon) um `_PI_NOZZLE_CLEAN` vor dem ersten
   Z-Antasten:
   - Config: `config_prtouch_apax oid=<pres-oid> oid_estp=<extruder.extruder_stepper.stepper.get_oid()>` per
     `add_config_cmd` **nach** `config_prtouch_pres` (Ort `_build_pres_config` 2893). oid_estp **muss** der
     Extruder-Stepper auf `nozzle_mcu` sein, sonst MCU-Shutdown beim ersten Sample (§1.3) – vor dem Senden prüfen,
     dass `stepper.get_mcu()` das pres-MCU ist; sonst Funktion deaktivieren (Stock: `activate`).
   - RUN=1: nur wenn pres **nicht** scharf ist (kein laufendes `start_prtouch_pres`), dann
     `start_prtouch_apax oid cfg_regs=60 acq_tick=93750`; ack prüfen (`expar1 == acq_tick`).
   - Antwort `resault_prtouch_apax` mit der **pres-oid** registrieren; nur min/max/Summe/Anzahl führen
     (`decode_packed` vorhanden, `k2_prtouch.py:655`), keine Listen aufheben.
   - RUN=0: `stop_prtouch_apax`, dann `dif = max − min` gegen `pres_chk_hold` (Stock 15 000) **und** Plausibilität
     (Anzahl ≈ Dauer × 1280 ± 20 %, Rohwerte ≠ −1, im 24-Bit-Bereich). Bei Fehler: Homing/Druck abbrechen.
   - Immer `stop_prtouch_apax` in `finally`/bei Abbruch und vor jedem `arm()`; zusätzlich in `arm()` hart
     ablehnen, solange der apax-Strom läuft.
2. **Buslast beachten:** ≈ 12 kB/s auf der Düsenleitung während des Stroms; nicht parallel zu Antasten oder
   Druck laufen lassen (Stock tut es nur beim Abstreifen, ≈ 3–11 s).
3. **Doku korrigieren** (README-k2_prtouch §1, Zeilen 687-688; PRTOUCH-BEFUND §1 Zeile 20): „apax = Hilfskanal
   für PA“ ist richtig, „nicht im Normalpfad“ gilt nur ohne PRES_CHECK; PRES_CHECK ist apax-basiert.
4. **Kein AUTO_APAX/WAVE_APAX nachbauen.** Falls doch gewünscht: zuerst die Auswertung (§2.3 Schritt 6) am Gerät
   mit aufgezeichneten Strömen nachvollziehen.

---

## 7. Offen / ANNAHMEN

- Genaue PA-Formel in `cmd_AUTO_APAX` und Bedeutung von `cal_fter (1,0)`, `spx_cont 3`, `stp_base`, `run_frdo`
  (nur Namen/Konstanten gesehen). Suchumfang: Namensspur der ganzen Funktion (`spur_gross.txt`), nicht dekompiliert.
- Prüfkriterium PRES_CHECK exakt (`chk_hold`-Vergleich nur aus der Spur; Konstanten 3000/78/50.0 im selben
  Bereich können Rauschen der Grob-Spur sein).
- `RUN_TYPE=3` (im Druck) – Aufrufer unbekannt; kein Beleg für Aufruf durch Slicer/App.
- Vorzeichenbit espd = `SF_NEXT_DIR` (Upstream-Namen, nicht im Image benannt).
- Bedeutung der Konstanten 60.0/1.2 in `_handle_resault` (Listenbegrenzung angenommen).

---

## 8. Arbeitsdateien (`arbeitsdateien-lokal/`<arbeitsdateien>/`, lokal)

`noz.bin`, `noz.dis` (Kopien aus lokale Arbeitsdateien), `scripts/spur2.py` (Namensspur mit Basisregister-
Verfolgung), `scripts/anker.py` (Grob-Gegenprobe ohne Registerverfolgung, mehr Rauschen),
`scripts/spur.py` (erste, zu grobe Version), `scripts/busrate.py` (Busrate aus Klipper-Stats),
`spur_klein.txt`, `spur_gross.txt`, `spur_alle.txt`, `anker_alles.txt`, `busrate_1012.txt`.
