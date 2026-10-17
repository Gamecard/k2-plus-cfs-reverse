> **ENTWURF – am echten Gerät UNGETESTET (Stand 16.10.2026).** Klipper-Modul [k2_box] für Upstream-Klipper; offline getestet (`python3 test_k2_box.py`, 28/28 inkl. Filamentwechsel §8, Auto-Nachladen §9 und RESUME nach Wechsel-Abbruch §10). Entwurf durch einen weiteren KI-Agenten, von uns geprüft.

# BERICHT – Klipper-Modul [k2_box] + serial485-Patch (Entwurf, 16.10.2026)

> **ENTWURF – am echten Gerät UNGETESTET (Stand 16.10.2026).** Klipper-Zusatzmodul
> für Upstream-Klipper auf einem Pi-Host; nur offline mit simulierter Box
> getestet (`python3 test_k2_box.py`, 9/9) plus serial485-Patch-Selbsttest
> (`python3 serial485_patch/serial485.py`, 72/72).

Auftrag: `AUFTRAG.md` in diesem Ordner. Gearbeitet AUSSCHLIESSLICH auf Deutsch,
ohne Netzwerk, ohne ssh/curl, ohne Zugriff auf den 3D-Drucker. Neue/geänderte
Dateien NUR in diesem Arbeitsordner
(`pi-host/`), alles andere
nur gelesen. Unbelegtes ist unten als **ANNAHME** markiert, Belege mit Fundstelle.

## 1. Ergebnis-Übersicht

| Datei | Zweck | Stand |
|---|---|---|
| `k2_box.py` | Klipper-Modul `[k2_box]`: Config wie `[k2_motor]`-Stil + `box_addr`/`poll_interval`; Controller A0–A6 (Auto-Addr nach `k2_motor:ready`, 0x0a-Poll retries=0, RFID/Remain/Version/HW lesend, Preload 0x0d op 0–3, Extrude/Retrude live-identisch, Cut-Platzhalter); Guards (allow_write, nie 0x50/0x06, keine Bursts, ein Lock mit `[k2_motor]`); G-Codes `KTWO_BOX_STATUS/LOAD/UNLOAD/PRELOAD`; `get_status` für Moonraker | neu, getestet |
| `test_k2_box.py` | `FakeBoxBus` (Zustände, Hub-Maske, Odometer +50 mm/Poll, Fehler 0x0c bei ~245 mm für Slot A wie Kanal-A-Defekt, RFID/Remain/Version/HW, Auto-Addr 0xA0–0xA3) + `FakeMotorBus` (gleicher Lock) + `FakePrinter` → 9 Prüfungen (Load, Fehlerabbruch, Unload, allow_write, Live-Frames, Lock, Lesen/Poll/Status, Preload/Addr/Guards, G-Codes) | neu, **9/9 ok** (s. §5) |
| `serial485_patch/serial485.py` | Kopie von `nachbau/serial485.py` mit zwei Fixes (B.1 `_done`-Begrenzung, B.2 Bg-Race) + 7 neue Selbsttest-Checks | neu, **72/72 ok** (65 bestehend + 7 neu) |
| `serial485_patch/fix.diff` | `diff -u` Original → Kopie (Beleg der Änderung, Original unverändert) | neu |
| `BERICHT.md` | diese Datei | neu |

Testbefehle (ohne Klipper, ohne Hardware, ohne Netzwerk):

```bash
cd pi-host
python3 test_k2_box.py
python3 serial485_patch/serial485.py
```

## 2. Architektur

```
printer.cfg [k2_box] ──► PrinterK2Box (Klipper-Hülle, load_config)
│                            │ parse_klipper_config() → parse_options() (rein)
│                            │ Events: klippy:connect, k2_motor:ready
│                            │ G-Codes: STATUS/LOAD/UNLOAD/PRELOAD
│                            ▼
K2BoxController (reine Logik A0–A6, ohne Klipper importierbar)
   ├─► BoxAction (nachbau/box.py – WIEDERVERWENDET)
   │       communication_get_box_state (0x0a, retries=0) ──┐
   │       communication_get_rfid/remain/version/hw/…     │ lesend
   │       communication_set_pre_loading (0x0d, op 2/3    │ schreibend
   │         Timeout >=380 s, retries=0)                   │ (nur allow_write)
   │       communication_extrude/retrude (0x10/0x11,      ┘
   │         retries=0, lange Timeouts) + set_box_mode/feed/print
   │         + preload_inhibit (0x0f) + sensor/buffer
   ├─► BoxAutoAddrSerial ──► AutoAddr (nachbau/auto_addr.py)
   │       cmd_send_data_with_response um transact_full
   │       (expect_addr=None für 0xFE-Broadcasts)
   └─► bus (serial485-artig, /dev/ttyS5, 230400)
           DIESELBE Instanz wie [k2_motor] → EIN Lock (Halbduplex)
```

**Wiederverwendung statt Neuerfindung:** Keine Frame-Bytes hart codiert.
Alle Sequenzen/Timeouts/Retries/Parser kommen aus den geprüften Modulen:
`box.BoxAction` (+ `CfsStatusError`/`StatusBytes`, `func_retries`,
`preload_force_timeout`, `parse_box_state`, `get_rfid`, …),
`cfsseq.build_extrude_sequence`/`build_retrude_sequence` (+ `slot_to_mask`,
`EXTRUDE_LIVE_CMDS_B`, `RETRUDE_LIVE_CMDS_A`, `requests_from_log_cmds`,
`filter_funs`), `auto_addr.AutoAddr`, `serial485.build_frame` (nur für
Test-Vergleiche). Der Test beweist Byte-Gleichheit (15 + 7 Frames).

**Controller-Methoden (Umfang Teil A):**

- `describe_plan()` – Trockenlauf-Text (kein Bus).
- `ensure_addr()` / `on_motor_ready()` – A0 Adressvergabe (AutoAddr
  `startup()` + `online_once()`), erst nach `k2_motor:ready` (gleiche
  Reihenfolge wie Stock-Boot §3.1 Schritt 11). Ohne ready → `wait_motor`,
  ohne allow_write → `skipped` (0xA0 schreibend).
- `poll_status()` – A1 0x0a-Poll (retries=0 wie Original, Beleg §6.2),
  Cache für Moonraker. Lesend, auch ohne allow_write.
- `get_rfid(slot)` / `get_remain(mask)` / `get_version_sn()` /
  `get_hardware_status()` / `get_sensor(pos)` / `get_buffer()` /
  `get_wheel()` – A2 lesend (retries wie Original via `func_retries`).
- `do_preload(slot, op)` – A3 0x0d op 0–3 (op 2/3 langer Timeout + retries=0,
  keine Bursts; Beleg GHIDRA §3 + BEFUND2 §5).
- `do_extrude(slot, odo_polls=5)` / `do_load(...)` – A4 Box-Load:
  `04 IDLE → 0f an → 08 Sensor → 10/00 → 10/04 → 10/05 x5 → 10/06 →
  10/07 p=3 → 05 → 04 PRINT → 0f aus`. Abbruch bei Status != 0 mit
  `CfsStatusError`-Text (Slot-Kontext). Byte-identisch zu Live (s. §5).
- `do_retrude(slot, repeats=4)` / `do_unload(...)` – A5 Box-Unload:
  `04 IDLE → 08 Sensor → 11 p0 x4 → 11 p1` (Beleg §6.5 + cfsseq).
- `do_cut(...)` – A6 ~~Platzhalter~~ seit 16.10.2026: liefert den G-Code-Plan
  der Schneidfahrt, sendet NICHTS an die Box (Ausführung `KTWO_BOX_CUT`, §8).
- `get_status(eventtime)` – Moonraker-Objekt (nur Cache, kein Bus):
  Zustand, Hub-Maske, Temp/Feuchte, Slots, FW, ready.
- `start_background()` / `stop_background()` – Threading-Poll (offline/Test;
  in Klipper Reactor-Timer, ANNAHME).

**Sicherheitsregeln (PROTOKOLL-485 §6.8/§6.9, BEFUND2 §5–§8):**

- Schreiben nur mit `allow_write: True` (Default False → nur lesen + Plan).
  Der Guard wirft `PermissionError`, bevor irgendetwas gesendet wird
  (Test beweist: null mechanische Frames).
- Nie 0x50 (Config #6 + Reset) / nie 0x06-Feeder (FW-Bug Maske aus r7)
  – `check_not_forbidden()` in jedem Sendepfad + Test (Beleg Func-Tabelle).
- Keine Bursts: 0x10/0x11 + 0x0d op 2/3 mit retries=0 (ein Retransmit würde
  die Mechanik doppelt starten; Box hat keine Duplikaterkennung,
  Beleg BEFUND2 §5). Rest via `box.func_retries` (0x0a-Poll retries=0,
  Beleg §6.2: 10 560 Polls).
- Bus-Lock teilen: Modul öffnet den Port NICHT, sondern bekommt `bus`
  injiziert – dieselbe Instanz wie `[k2_motor]` (EIN Port ttyS5, EINE Queue,
  EIN Lock über die ganze Transaktion, vgl. `MAX_BUS_HOLD_S`). Test mit
  gemeinsamem `threading.Lock` beweist keine Überlappung (8 Transaktionen).

**G-Codes:** `KTWO_BOX_STATUS` (frischer Poll + Bericht, lesend),
`KTWO_BOX_LOAD SLOT=A..D [DRY_RUN=1]`, `KTWO_BOX_UNLOAD SLOT=… [DRY_RUN=1]`,
`KTWO_BOX_PRELOAD SLOT=… OP=0..3 [DRY_RUN=1]`. Ohne allow_write bzw. mit
DRY_RUN nur Plan (nichts gesendet). Fehler als `CfsStatusError`-Klartext
(z.B. 0x0c Odometer-Stillstand); Shutdown nur per `shutdown_on_failure`.

## 3. Einbindung in Upstream-Klipper

1. Dateien kopieren (vollständiger Weg: `config-pi/README.md` §3):
   - `k2_box.py`, `k2_transport.py`, `k2_485.py` → `klippy/extras/`
   - `nachbau/*.py` (mind. `box.py`, `cfsseq.py`, `auto_addr.py`,
     `serial485.py`) → `klippy/extras/k2_nachbau/`. k2_box fügt seinen
     Ordner NICHT in `sys.path` ein; `k2_transport.ensure_nachbau_path()`
     hängt `k2_nachbau/` nur an und prüft die Herkunft der Module
     (Fix 16.10.2026, Import-Falle `statistics`).
2. `printer.cfg`-Beispiel (zusammen mit `[k2_motor]`, Pins gekürzt):

```ini
[k2_motor]
switch: 1
motor_closed_loop: x,y,z,z1,e
retries: 4
allow_write: False
shutdown_on_failure: False
auto_init: True
main_mcu: mcu
nozzle_mcu: nozzle_mcu
motor_x_dir: PB9,0
motor_x_step: PB10,0
motor_x_stall: PB11
# ... restliche Pins/Params aus motor_control.cfg ...

[k2_box]
box_addr: 1
allow_write: False          # True für echte Loads/Unloads/Preloads
shutdown_on_failure: False  # True → SHUTDOWN bei Box-Fehler (sonst nur Meldung)
auto_init: True
poll_interval: 4.0          # s, Original-Herzschlag alle ~4 s

# Geteilter Bus: Modul k2_485.py stellt EINE Serial485-Instanz als Objekt
# 'k2_485' bereit (EIN Lock, Halbduplex) – für [k2_motor], [k2_box], [k2_belt].
[k2_485]
serial: /dev/serial/by-id/usb-…-if00-port0
baud: 230400
```

3. Ablauf prüfen:
   - `KTWO_BOX_STATUS` → Poll + `Zustand=STATE_IDLE Hub=0x00 Temp=… Slots=…`
     (geht auch mit `allow_write: False`).
   - `KTWO_BOX_LOAD SLOT=B DRY_RUN=1` → Plan, nichts gesendet.
   - `allow_write: True` + `KTWO_BOX_LOAD SLOT=B` → echter Box-Load
     (Heizen/Fahren per Makro M8200/BOX_LOAD_MATERIAL davor – hier nur Box).
   - Auto-Addr läuft nach `k2_motor:ready` von selbst (vorher `wait_motor`).

## 4. Annahmen (Unbelegtes – alles mit ANNAHME im Code markiert)

- **Upstream-API ohne Quellbaum:** `register_event_handler("klippy:connect"
/"k2_motor:ready")`, `register_command`, `get_options/get`, `reactor.pause/
register_timer`, `send_event`, `gcode.respond_info/error`, `GCodeCommand.
get_int` – gegen die bekannte API geschrieben (Muster `k2_motor_init.py`),
Kapselung in Controller/Hülle, vor Hardware-Lauf gegen echten Quellbaum prüfen.
- **Geteilter Bus:** Upstream hat keinen 485-Manager – seit 16.10.2026 liefert
  `[k2_485]` (`k2_485.py`) das `printer`-Objekt `'k2_485'` mit derselben
  `serial485.Serial485`-Instanz für `[k2_motor]` (`Rs485Transport`),
  `[k2_box]` (`BoxAction`) und `[k2_belt]`. Ohne `[k2_485]` Diagnose-Modus
  („Abschnitt [k2_485] fehlt“); Port-/Bus-Fehler bei LOAD/UNLOAD/PRELOAD →
  G-Code-Fehler (kein Shutdown). Lock-Teilung im Test mit einem `Lock` bewiesen.
- **Auto-Addr-Persistenz:** Uniids nur in-memory (`_addr_store`); Stock speichert
  in `printer.cfg` (`mb_addr_table_uniids`) – Übernahme in `configfile` fehlt
  (Entwurf). `expect_addr=None` für 0xFE-Broadcasts (Beleg §6.8).
- **Cut:** ~~Platzhalter~~ seit 16.10.2026 abends umgesetzt (§8): reine
  Kopfbewegung `KTWO_BOX_CUT` (Beleg: im Live-Cut kein Box-/Motor-Frame);
  Motor-Boards nur bei der Cut-KALIBRIERUNG (Kp 300/Stall 3, nicht auf dem Pi).
- **Toolhead-Seite von Load/Unload:** ~~nur Original-Makros~~ seit 16.10.2026
  abends als Wechsel-Schritte im Modul (§8: Heizen, Schacht X133 Y378,
  Spülen, Abstreifen, Stage 7). `KTWO_BOX_LOAD/UNLOAD` bleiben reine
  Box-Förderung mit fester Poll-Zahl (`odo_polls=5`, Beleg
  `EXTRUDE_LIVE_CMDS_B`); `KTWO_BOX_EXTRUDE` pollt bis zum Kopfsensor.
- **Neue Optionen** (`box_addr`, `allow_write`, `shutdown_on_failure`,
  `auto_init`, `poll_interval`) sind Entwurfs-Erweiterungen (Defaults sicher).
  `SLOT=A..D` (Auftrag) plus Komfort `0..3` (Original NUM numerisch, Beleg §2:
  NUM=A → key60); Masken als SLOT abgelehnt (Verwechslung).
- **0x0a-Cache:** `get_status` liefert letzten Poll (kein Bus) – vor erstem Poll
  `unknown` (Klipper ruft oft auf). Temp/Feuchte aus AHT30 (Beleg BEFUND2 §1),
  Hub-Maske = Sensor B (Beleg §6.6), `inlet_tag_mask` nicht in `get_status`
  (Sperrbit-Zweck V, Beleg BEFUND2 §2).

## 5. Testergebnisse (16.10.2026, ohne Hardware, ohne Klipper)

`python3 test_k2_box.py` → `ERGEBNIS: ok (9/9 ok)`:

1. **Kompletter Load-Ablauf (B) – ok:** Odometer 250,0 mm (5×50), Hub B belegt.
2. **Fehlerabbruch 0x0c mit Meldung (A ~245 mm) – ok:** `CfsStatusError`
   `0x0c (Odometer-Poll (mode 5) SLOT=A: Odometer-Stillstand 500 ms)`,
   danach `STATE_ERROR` + `error_code 0x0c` (Vorbild CHECKLISTE Kanal A:
   Abbruch nach ≈240–250 mm → key836).
3. **Unload (B) – ok:** Hub frei nach Retrude.
4. **allow_write=False schreibt nichts – ok:** alle 5 mechanischen Aufrufe mit
   `PermissionError`, null Frames mit 0x0d/0x04/0x0f/0x10/0x11/0xA0; 4 lesende
   Calls ok; Cut-Platzhalter wirft wie verlangt.
5. **Frame-Folge == Live-Sequenzen – ok:** Extrude 15 Frames ==
   `build_extrude_sequence(0x02, pushes=5)` == `EXTRUDE_LIVE_CMDS_B`;
   Retrude 7 Frames == `build_retrude_sequence(0x01)` ==
   `RETRUDE_LIVE_CMDS_A` gefiltert (04/08/11; 0x0e-Zwischenabfragen sind
   Diagnose, Beleg cfsseq-Docstring).
6. **Gemeinsamer Lock (keine Überlappung) – ok:** 8 Transaktionen (4 Box-0x0a
   + 4 Motor-0x0C) mit je 15 ms Arbeit über EIN `Lock` – Intervalle
   überlappungsfrei.
7. **Lesen + Poll retries=0 + Moonraker-Status – ok:** 0x0a-Call mit
   `retries=0` (Original, Beleg §6.2), Temp 22 °C/46 %, RFID `TAGB…`,
   Remain B=29, Version `153`, HW `all_ok`, Sensor/Buffer, `get_status`
   mit Zustand/Hub/Temp/Feuchte/Slots.
8. **Preload/Addr/Guards – ok:** 0x0d op 0/1 mit retries=4 (Original),
   op 2/3 mit retries=0 + Timeout ≥380 s (Beleg `preload_force_timeout`);
   0x10/0x11 überall retries=0 (keine Bursts); `ensure_addr` vor ready ohne
   einen Frame (`wait_motor`), nach ready `ready` mit 0xA1/0xA2; alle
   SLOT/OP-Guards + `check_not_forbidden(0x50/0x06)` senden nichts.
9. **G-Codes via FakePrinter – ok:** connect → `k2_box:ready`-Warten,
   `handle_motor_ready` mit Auto-Addr, STATUS/LOAD/UNLOAD/PRELOAD antworten
   `ok`, DRY_RUN sendet nichts (`Trockenlauf`), `get_status` mit Hub/Slots.

## 6. Teil B – serial485-Patch

Original **NICHT** geändert (nur gelesen + kopiert). Patch:
`serial485_patch/serial485.py` (Kopie) + `serial485_patch/fix.diff`
(`diff -u` Original → Kopie, 373 Zeilen).

**B.1 `_done` wächst unbegrenzt (CHECKLISTE Review-Offen):** `send_queue_process`
und `cmd_send_data_with_response` legten jede Antwort in `_done` ab – direkt
gedrainte Einträge (`add_send_data` + `send_queue_process`) und verwaiste
Fremd-Antworten (Thread crashed) blieben ewig. Fix: `_done_time` pro Eintrag,
`_store_done_locked()` (TTL 60 s + harte Grenze 128, älteste IDs zuerst raus),
`_take_done_locked()` (verfallene gilt als fehlend), öffentlich
`cleanup_done()` → `(entfernt, übrig)`, `stats()` meldet `done_len`.
Alte Aufrufer unverändert (gleiche Rückgaben bei kleinen Mengen).

**B.2 Bg-Thread schnappt `get_response` Antworten weg (CHECKLISTE Review-Offen):**
Lücke zwischen `raw_send` und `get_response` – bg erwarb `_lock` zuerst, las
den Antwort-Frame und dispatchte ihn, `get_response` lief ins Timeout. Fix:
`raw_send(..., expect_response=True [Default], expect_timeout, expect_fun,
expect_addr)` registriert atomar mit dem Schreiben eine Erwartung
(`_pending_expect`); bg parkt passende Frames in `_resp_queue` statt zu
dispatchen; `get_response` prüft die Queue zuerst (dann seriell wie bisher
inkl. Dispatch). `expect_response=False` = altes Verhalten (für echtes
Fire-and-forget, z.B. `cmd_485_send_data`). Alles unter `_lock`, TTLs
(2 s Erwartung / 5 s Queue, max. 32) gegen Verwaisten.

`python3 serial485_patch/serial485.py` → `SELBSTTEST: 72/72 ok`
(65 bestehende + 7 neue):

- B.1 `_done` begrenzt (MAX 128 bei 150 Einträgen), älteste verdrängt/neueste
  da, `cleanup_done` räumt künstlich gealterte ab, Einzel-Call danach ok.
- B.2 `get_response` findet geparkte Antwort trotz 120-ms-Lücke (kein Timeout,
  genau einmal dispatcht); ohne Erwartung dispatcht bg wie bisher (Negativprobe).

## 7. Quellen (alle nur gelesen)

- `nachbau/box.py`, `cfsseq.py`, `boxmini.py`, `auto_addr.py`, `serial485.py`
- `PROTOKOLL-485.md` (Func-Tabelle R153, §6 Live-Gegenprobe: §6.2 Retries/
  Timeouts, §6.4 0x0d, §6.5 0x10/0x11/0x04/0x08, §6.6 0x0a, §6.8 BEFUND2,
  §6.9 Schutzmechanismen)
- `GHIDRA-CFS-BEFUND-2026-10-15.md` (§2–§5: 0x0d/0x10/0x11/Status/0x0a)
- `GHIDRA-CFS-BEFUND2-2026-10-16.md` (§1 Temp/Feuchte, §2 Tag-Sperrbit,
  §3 0x15, §4 0x50/0x51/0x52, §5 Bursts, §6 0xEA/0xEB, §7 Auto-Addr, §8 Kanal A)
- `LIVE-ABFRAGEN-2026-10-16.md` (Motor-Transparent als Negativbeleg)
- `CHECKLISTE.md` (Kanal A: Preload/Extrude/Retrude-Ablauf, 0x0c/0x19;
  Review-Offene B.1/B.2 als Auftrag)
- `pi-host/k2_transport.py` (Rs485-Schnittstelle + nachbau-Pfadsuche),
  `pi-host/k2_motor_init.py` (Stilvorbild: testbare Logik, allow_write,
  Klipper-Hülle, G-Codes, FakePrinter-Tests)
- `config/F008_CR0CN240319C13/box.cfg` (BOX_LOAD/QUIT-Ketten, M8200-Kette,
  CR_BOX_*, BOX_CUT_MATERIAL), `config/F008_CR0CN240319C13/gcode_macro.cfg`
  (BOX_*-Makroverdrahtung), `live-1.1.7.0-configs/box-live.cfg`
  ([box]-Parameter als Doku)

## 8. Filamentwechsel T0–T15 / M8200-Kette (Ergänzung 16.10.2026 abends)

> **ENTWURF – am Gerät UNGETESTET.** Offline: `test_k2_box.py` 20/20,
> Batch gegen Upstream 9cb9975: `arbeitsdateien-lokal/upstream-test-cfs/`
> (`ERGEBNIS.txt`). Bedienung/Inbetriebnahme: `config-pi/NOTIZ-cfs.md`.

### 8.1 Belegter Stock-Ablauf (Fundstellen)

| Quelle | Beleg |
|---|---|
| `live-logs-2026-10-16/config-aktuell/box.cfg` | `[box]`-Werte (pre_cut 10/200, cut_pos_y 200, cut_velocity 30000, Tn_retrude -10 @600, Tn_extrude_temp 220, Tn_extrude 140 @360, buffer_empty_len 30, extrude_pos 133/378, clean_* 135/160/378, 160/170/374, clean_velocity 12000, box_need_clean_length 70, safe_pos 225/345, check_cut_pos_x -9.5…-5.5, switch_pin !nozzle_mcu:PB9); M8200 P/C/R/L/W/F/O → CR_BOX_PRE_OPT/CUT/RETRUDE/EXTRUDE/WASTE/FLUSH/END_OPT; TNN `T<I/4+1><ABCD[I%4]>`; kommentiertes T0-Beispiel (Z+0.4, `G0 X10 Y200 F12000`, ACCEL 5000, M8200 C, `G0 Y345 / X139 / Y378 / X133`, M8200 R, M104, M8200 L I0, `G0 E90 F600`, Blech X160/X135, Kreise `G2 I4`/`G3 I-4` bei Y374, 3× Schütteln 160↔133, `G0 X160 / Y345`, M8200 O, Z zurück) |
| `…/printer.cfg` SAVE_CONFIG | `[box] cut_pos_x = -7.80` (Kalibrierung 16.10.2026, LIVE-ABFRAGEN §Schneidposition) – UUID-Zeilen dort bewusst NICHT übernommen |
| `…/gcode_macro.cfg` | `LOAD_MATERIAL_CLOSE_FAN2`/`RESTORE_FAN2` (fan2 aus/zurück), `QUIT_MATERIAL_RETRUDE_MATERIAL` (`G91 / G0 E-10 F360 / G90`); T0…T15 sind KEINE Makros (kommen aus `box_wrapper.so`) |
| `live-logs-2026-10-15/klippy.log.2026-10-12` 00:24:49–00:26:35 | vollständiger Wechsel T1A (Unload + Load): Heizen 220 → „[box] cut sensor detected“ → „cut to return OK“ (3 s, **kein** Box-/Motor-Frame) → 0x08 [01], 0x08 [00], 0x04 [00 01] → **4× 0x11 [01 00], nach p0 1–3 je „extrude = -10.0, velocity (600,)“** → 0x11 [01 01] (20 s) → Einzug 0x04/0x0f/0x08/0x10 m0/m4/m5×5 (alle 2 s)/m6 (aus Sensor-Ereignis) → `extrude_process_stage7 tnn: T1A` → m7 p3 → 0x05 → 0x04 [01 00] → 0x0f [00] → Spülen „length: [90, 70]“, `check_and_extrude` 18 + 72 mm (240 °C, ×1,2) und 14 + 56 mm (220 °C) |
| `…/klippy.log.2026-10-13` 02:30–02:31 | zweiter Einzug: gleiche Folge, **„timeout = 90“** Countdown während m5 |
| `arbeitsdateien-lokal/cutpos-live-20261016-1601.log` 22:02 | Cut + Retrude 16.10.: Reihenfolge 0x04 [00 01] → 0x08 [00] → p0 (+E-10, hier F350 aus Material) ×3 → p0 → p1, danach „filament_sensor false“ |
| `arbeitsdateien-lokal/kanalA-live-20261016-1527.txt` | Preload/Einzug/Rückzug-Zeiten (m0 4,4 s, p1 22,4 s, op2 31 s) |
| `PROTOKOLL-485.md` „M8200-Sequenzen“ | Load P→L→F→O, Unload P→C→R→O, `CR_BOX_PRE_OPT` Pflicht vor EXTRUDE (key60), I=n → TnX; Box blockiert Einzüge, solange ein Slot im Hub steckt |
| `GHIDRA-CFS-BEFUND*.md`, `nachbau/cfsseq.py` | 0x10-Modi (m7 = Odometer > p mm in 300 ms, sonst 0x0e), 0x11 p0/p1, 0x04 [00 01] löscht ERROR |

**Aufteilung:** Box (RS485) = Rückzug 0x11, Einzug 0x10 + 0x04/0x0f/0x08/0x05,
Zustand 0x0a. Drucker = Z-Hop, Schneiden (Kopf drückt den Hebel gegen den
Anschlag), Schacht, Heizen, Extruder-Rückzug zwischen den p0, Stage 7
(Extruder greift, während die Box mit m7 prüft), Spülen, Abstreifen,
Lüfter, Rückfahrt.

### 8.2 Neue G-Codes (`k2_box.py`, Präfix `KTWO_`, Upstream-Namensregel geprüft)

| G-Code | Stock | Inhalt |
|---|---|---|
| `KTWO_BOX_PRE_OPT [TNN=] [Z_HOP=] [SOURCE=T]` | M8200 P | Kette starten (`SOURCE=T` aus `_KTWO_TOOL_CHANGE` = nachholbarer T<n>-Wechsel, §11); schon geladen (Hub-Sensor + Kopfsensor) → Rest überspringen; Kopfsensor sperren (`SET_FILAMENT_SENSOR … ENABLE=0`, sonst pausiert Upstream beim Rückzug); `SAVE_GCODE_STATE`; Z-Hop 0,4 |
| `KTWO_BOX_CUT` | M8200 C | `G0 X10 Y200 F12000`, `ACCEL=5000`, `G0 X-7.8 Y200 F30000`, `G4 P100`, `G0 X10`, Accel zurück, `M400`; optional Schneidhebel-Sensor (`cut_sensor`) |
| `KTWO_BOX_GO_EXTRUDE_POS` / `KTWO_BOX_SAFE_POS` | BOX_GO_TO_EXTRUDE_POS / BOX_MOVE_TO_SAFE_POS | T0-Beispiel Schritt 6 / 11 |
| `KTWO_BOX_RETRUDE [SLOT=] [LENGTH=]` | M8200 R [E] | Schacht, M109 (nur mit Filament am Kopf), Box bereit abwarten, Slot aus Hub-Sensor, `do_retrude(after_p0=…)`: p0 → E-10 F600 → … → p1 |
| `KTWO_BOX_EXTRUDE TNN=\|SLOT=` | M8200 L I | Hub frei/gleicher Slot prüfen, Kopf leer prüfen, M109, `do_extrude(sensor_fn, stage7_fn)`: m5 alle 2 s bis Kopfsensor (max 90 s), m6, Extruder `G1 E30 F360` (ohne M400), 2 s, m7 p3, PRINT |
| `KTWO_BOX_FLUSH [TNN=] [LENGTH=] [VELOCITY=] [TEMP=\|BASE_TEMP=]` | M8200 F | seit 17.10. abends wie Stock (§12): Menge farbabhängig (unbekannt 90 + 70 mm), je 20 % v / 80 % 1,2·v, Blasen + Blech je Abschnitt, Silikon-Kreise nur am Ende |
| `KTWO_BOX_WIPE`, `KTWO_BOX_WASTE` | T0-Bsp. 10.1–10.3, M8200 W | Abstreifen; WASTE = nur Hinweis (Stock-Kamera fehlt) |
| `KTWO_BOX_END_OPT [RETURN=0\|1]` | M8200 O | Schacht verlassen, ggf. Rückfahrt XY + Z, Zieltemperatur zurück, Zustand/Kopfsensor zurück |
| `KTWO_BOX_WAIT [TIMEOUT=]` | BOX_MODE_WAIT | 0x0a bis IDLE/PRINT, ERROR → Fehler |
| `KTWO_BOX_ERROR_CLEAR [HEAD_LOADED=1]` / `KTWO_BOX_RESUME_FIX` | BOX_ERROR_CLEAR | Fehler quittieren (+ 0x04 [00 01]); `HEAD_LOADED=1` = Kopf von Hand als geladen bestätigt (nur ohne Kopfsensor-Zustand, §11) / Z-Hop nach Pause zurücknehmen (aus RESUME) |
| `KTWO_BOX_RESUME_CHECK [MOVE=0\|1] [TEMP=]` | BOX_ERROR_RESUME_PROCESS | aus RESUME nach einem abgebrochenen Wechsel: Kopfsensor leer → RESUME gesperrt bzw. Nachholen (`resume_redo_change`, §11) |

Controller-Erweiterungen (wiederverwendet, Frame-Folgen unverändert):
`do_extrude(sensor_fn, sensor_timeout, poll_interval, stage7_fn, stage7_delay)`,
`do_retrude(after_p0)`, `do_cut()` (= G-Code-Plan, sendet nichts),
`wait_box_ready()`, `hub_mask()`, `loaded_slot`; reine Bausteine
`gcode_cut/go_extrude_pos/safe_pos/wipe/flush_segment/extruder_move`,
`flush_segments`, `retrude_extruder_steps`, `tnn_from_index`, `parse_tnn`.
Neue Optionen (Stock-Namen, Defaults = box.cfg): s. `CHANGE_DEFAULTS` und
`config-pi/k2-module.cfg`.

### 8.3 Sicherheitslogik

- Box-Frames nur mit `allow_write`, Kopf/Extruder/Heizen nur mit
  `allow_move` (beide Default False → Plan-Text). `allow_move` ohne
  `allow_write` = Trockenlauf mit Bewegung (nur außerhalb eines Drucks).
- Im Druck (print_stats printing/paused) braucht jeder Schritt BEIDE
  Freigaben, sonst Pause. T0 ohne Freigaben = Einfarbbetrieb (Makro).
- Fehler im Druck → `PAUSE`, Meldung `!! …`, Folgeschritte der Kette
  übersprungen, END_OPT räumt nur nicht-mechanisch auf; `RESUME` gesperrt
  bis `KTWO_BOX_ERROR_CLEAR`; danach senkt `KTWO_BOX_RESUME_FIX` den Z-Hop.
  Außerhalb eines Drucks → G-Code-Fehler + Aufräumen. Nie „Internal
  error“/Shutdown (alle Ausnahmen werden abgefangen).
- Seit 17.10.2026 (§10): Abbruch im laufenden Druck → `PAUSE
  CFS_RETURN_X/Y/Z=<Druckposition vor dem Wechsel>`; RESUME fährt dorthin
  statt zur Fehlerposition (Schneidhebel/Schacht).
- Seit 17.10.2026 spät (§11): RESUME nach einem Abbruch ist GESPERRT,
  solange der Kopf-Filamentsensor leer (oder unbekannt) meldet; optional
  Nachholen des T<n>-Wechsels (`resume_redo_change`, Default AUS).
  Reihenfolge von Hand: `KTWO_BOX_ERROR_CLEAR` → `T<n>` → `RESUME`.
- Box 2–4 (T4–T15) abgelehnt, solange nicht konfiguriert; Hub durch anderen
  Slot belegt → Ablehnung vor jedem Frame; Kopfsensor zeigt Filament vor
  dem Einzug → Ablehnung.
- `ReactorBusProxy`: lange Box-Antworten blockieren den Reactor nicht
  (Heizungs-PWM/MCU-Takt laufen weiter); Poll-Timer pausiert bei `busy`.

### 8.4 Tests und Batch

`test_k2_box.py` +11 Prüfungen (20/20): Bausteine == box.cfg/Live (Schnitt,
Schacht, Kreise, 18/72 + 14/56, 3× −10, TNN-Mapping, Optionsgrenzen),
Einzug bis Kopfsensor + Stage 7 (0x0e ohne Extruder, Timeout → 0x04 IDLE),
Rückzug mit Haken (Frames == `build_retrude_sequence`), Box-Wartebedingung,
volle Kette T1B in Reihenfolge (Hop → Schnitt → Schacht → p0/E-10 ×3 → p1 →
m6 → E30 → m7 → PRINT → Spülen/Abstreifen → Rückfahrt), Sperren, Fehler im
Druck (0x0c → Pause) und ohne Druck, Proxy, Makros T0–T15/M8200/RESUME per
Jinja2 gerendert, Python-3.9-Syntax.

Batch (`arbeitsdateien-lokal/upstream-test-cfs/lauf-cfs.sh`): Auslieferung
ohne Freigaben → 0 Schritte, nur Pläne, EXIT 0; `allow_move` → dekodierte
MCU-Schritte bestätigen die Bahn (89/89 Ecken, Anschlag X −7,800, Z 20,4
beim Schnitt, Extruder −30/+120/+70 mit 10/6 mm/s nur im Schacht); beide
Freigaben ohne Port → `RETRUDE fehlgeschlagen: [k2_485] Batch-Modus …`,
kein Shutdown; T5 → „Box 2 … nur Box 1“.

### 8.5 ANNAHMEN (Wechsel)

Stage-7-Extruderlänge (`buffer_empty_len` 30) + `stage7_delay` 2 s,
Rückweg-Geschwindigkeit nach dem Schnitt, `cut_dwell` 0,1 s (Input-Shaper),
Bedeutung des Schneidhebel-Sensors, ~~Material-Temperaturen (hier
`max(Ziel, 220)`), Spül-Erhöhung auf Max-Temperatur/×1,2 entfällt,
`box_wait_timeout` 200 s, Lüfter fan0/„Blasen“ beim Spülen nicht
nachgebaut~~ (seit 17.10.2026 abends umgesetzt, §12), Verhalten bei
Sensor-Timeout (0x04 IDLE), `safe_pos_x` 225 ungenutzt, Box 2–4, Z nach
RESUME mitten im Wechsel. Details: `config-pi/NOTIZ-cfs.md`.

## 9. Auto-Nachladen bei Runout (A7, Ergänzung 17.10.2026)

> **ENTWURF – am Gerät UNGETESTET.** Offline: `test_k2_box.py` 25/25 (5
> Runout-Prüfungen), Batch gegen Upstream 9cb9975:
> `arbeitsdateien-lokal/upstream-test-runout/` (`ERGEBNIS.txt`). Belege,
> Ablauf, ANNAHMEN: `config-pi/NOTIZ-runout.md`.

- Stock: `pause_on_runout: true` + `runout_gcode` (E30 F600 wenn heiß +
  `BOX_CHECK_MATERIAL_REFILL` aus `box_wrapper.so`; Strings `auto_refill`,
  `same_material_list`/`same_tnn_list`, „no tray with ingredients found“).
  Kein echter Runout in den Live-Logs → Frame-Folge unbelegt.
- Pi: `printer.cfg` `runout_gcode: KTWO_BOX_RUNOUT`, `pause_on_runout: True`
  (Upstream pausiert vor dem runout_gcode). `KTWO_BOX_RUNOUT` prüft lesend
  (0x0a PRINT/0x50, 0x08 [01] Hub leer, 0x08 [00] Einlauf leer, 0x02 [0f]
  RFID, 0x03 [0f]), wählt den Ersatz (`select_runout_slot`: Gleich-Material-
  Gruppe oder RFID gleiche mat_id + Farbe, Filament am Einlauf, zyklisch ab
  dem Nachbarslot) und fährt PRE_OPT (Z_HOP=0) → Schacht/Heizen/E30 F600 →
  EXTRUDE → WASTE → FLUSH → END_OPT RETURN=1 → RESUME.
- Neue Befehle `KTWO_BOX_RUNOUT`, `KTWO_BOX_AUTO_REFILL`,
  `KTWO_BOX_SAME_MATERIAL`; Optionen `runout_refill` (False),
  `runout_require_color` (True), `runout_resume` (True),
  `runout_tail_length`/`_velocity` (30/600); Status `auto_refill`,
  `same_material`, `runout_last`.
- Sicherheit: nur mit `runout_refill` + `allow_write` + `allow_move`; jeder
  Abbruch lässt den Druck pausiert (Meldung), Kettenfehler sperren RESUME
  bis `KTWO_BOX_ERROR_CLEAR`, nie Shutdown. `_print_active()` zählt jetzt
  auch `pause_resume.is_paused`.
- Nebenbei (A6): `cut_pos_x` steht nicht mehr in `k2-module.cfg` (SAVE_CONFIG-
  Konflikt); Default −7.80 aus `CHANGE_DEFAULTS` bzw. SAVE_CONFIG-Block.

## 10. RESUME nach abgebrochenem Wechsel im Schneidbereich (Ergänzung 17.10.2026)

> **ENTWURF – am Gerät UNGETESTET.** Offline: `test_k2_box.py` 28/28 (3 neue
> Prüfungen), Batch gegen Upstream 9cb9975:
> `arbeitsdateien-lokal/upstream-test-resumecut/` (7 Läufe, 83/83).
> Belege, Ablauf, ANNAHMEN: `config-pi/NOTIZ-reinigung-resume.md` §6a.

- Problem: Upstream-`PAUSE_STATE` hielt nach einem Wechsel-Abbruch die
  Fehlerposition (z. B. X −7,80/Y200 am Schneidhebel); `RESUME_BASE` fuhr
  gerade dorthin zurück (Hebel erneut betätigt, Druck an falscher Stelle).
- `_fail` (Pause wird durch den Fehler ausgelöst): `PAUSE CFS_RETURN_X/Y/Z`
  = `start_pos` der Kette (vor dem Z-Hop) bzw. Position vor einem
  Einzelschritt ohne Kette (`_step_start`); kein Ziel, wenn der Kopf noch
  dort steht oder die Kette in einer Pause begann. `pending_resume_z` nur
  noch, wenn die Pause durch den Fehler entsteht.
- `KTWO_BOX_RESUME_FIX CLEAR=1` (aus dem PAUSE-Makro): Z-Korrektur verwerfen
  (Z steckt im Rückfahrziel; keine Altlast einer per CANCEL beendeten Pause).
- `gcode_leave_cut_zone(c, pos)`: Kopf bei X < `pre_cut_pos_x` → `G0 X10`
  + `M400` vor jeder Schachtfahrt (`_go_extrude_pos`), Stock box.cfg:13.
- `cmd_pre_opt`: `paused_at_start`; `_keep_start_pos` nur für ein `T<n>` in
  derselben Pause (Fix: sonst Rückfahrt zur Position eines früheren
  Wechsels). `cmd_end_opt` in einer Pause: Z nicht unter die Pausenhöhe.
- Makros (`macros.cfg`): PAUSE übernimmt das Ziel (`pause_rebase`), parkt
  vom Hebel nur über X, aus dem Schacht über X160 → Y345; RESUME sperrt vor
  jeder Bewegung (Ziel am Hebel, fehlende Freigabe bei CFS-Abbruch), fährt
  vom Hebel nur in X, und `_PI_RESUME_REBASE` setzt `PAUSE_STATE` an der
  Druckposition neu (`RESTORE_GCODE_STATE` + `SAVE_GCODE_STATE`, ohne Fahrt)
  → `RESUME_BASE` = Nullfahrt, G-Code-E wie vor dem Wechsel.

### 10.1 Bewusste Abweichung vom Stock (Ergänzung 17.10.2026 spät)

`BOOTLOADER-HOST-BEFUND-2026-10-17.md` §5 belegt den Stock-Fehlerpfad
(B, Disassembly `box_wrapper.so`): `BoxError.handle_event` @`0x1c3ca4`
nimmt den Z-Hop an der Fehlerposition zurück (`G0 Z-{move_z}`), fährt
`BOX_MOVE_TO_SAFE_POS` (@`0x1184e0`: **erst `G0 Y345`, dann `G0 X225`**) und
ruft dann `PAUSE` → PAUSE_STATE = **X225/Y345 auf Druck-Z**; `RESUME_BASE`
fährt dorthin zurück (§5.3, ANNAHME: wie Upstream). Der Pi weicht bewusst ab:

| Punkt | Stock | Pi | Grund |
|---|---|---|---|
| Rückfahrziel | X225/Y345, Druck-Z (gerade Fahrt) | Druckposition vor dem Wechsel, X/Y in Pausenhöhe, Z senkrecht | Stock senkt bei X225/Y345 auf Druck-Z, danach fährt der Slicer auf Druck-Z zum Objekt (BEFUND §5.3 V) |
| Schneidbereich | kein Sonderfall, Y zuerst | X < `pre_cut_pos_x` nur in X | einziger Beleg `box.cfg:13` „垂直撞刀“; Y am Hebel unbelegt (BEFUND §5.4) |
| Schacht | Z zurück auf Druck-Z, dann Y345/X225 | Z-Hub, X160 → Y345 (T0-Beispiel 11), Park | nie auf Druck-Z quer über das Bett |
| Nachholen | automatisch (§11) | gesperrt; optional `resume_redo_change` | §11 |

Ausführlich: `config-pi/NOTIZ-reinigung-resume.md` §6a.8.

## 11. RESUME nach abgebrochenem Wechsel: Kopfsensor-Sperre und Nachholen (Ergänzung 17.10.2026 spät)

> **ENTWURF – am Gerät UNGETESTET.** Offline: `test_k2_box.py` 34/34 (6 neue
> Prüfungen), Batch `arbeitsdateien-lokal/upstream-test-resumecut/` (11
> Läufe, 151/151), a45-Batch 0 Fehler, Gesamtlauf-Kopie
> `upstream-test-resume2-gesamt/` unverändert. Belege, Ablauf, ANNAHMEN:
> `config-pi/NOTIZ-reinigung-resume.md` §6a.7–§6a.9. Alles gesperrt
> ausgeliefert (`allow_write`/`allow_move`/`resume_redo_change` False).

**Stock (BOOTLOADER-HOST-BEFUND §5.1/§5.2, B):** `BoxSave.recode_err`
@`0x19fb90` speichert `resume_tnn` = Ziel-TNN nur für Fehler im T<n>-Pfad
(nicht `macro_error_list` = M8200-Kette). `RESUME` → `BOX_ERROR_RESUME_PROCESS`
→ `error_resume_process` @`0x7cd28`: CFS an, pausiert, `resume_tnn` gesetzt;
`clear_err()`; Kopfsensor belegt → Ende ohne Wechsel (live `12:88007`);
sonst `go_to_extrude_pos` → `box_retrude_material` → `box_extrude_material`
→ `extruder_extrude` → `material_change_flush`; Fehler → `resume_err`,
`move_to_safe_pos`. Danach `RESUME_EXTERNAL` (Prime, Reinigung) → `RESUME_BASE`.

**Pi:**

- `_fail` merkt den Abbruch (`_record_abort`: Schritt, Ziel-TNN, Herkunft;
  Status `change_aborted`, `resume_tnn`, `head_filament`,
  `resume_redo_change`). Herkunft `SOURCE=T` setzt nur `_KTWO_TOOL_CHANGE`
  (T<n>-Befehl) – nur dieser Wechsel ist nachholbar (wie Stock). Merker lebt
  bis zum Ende eines RESUME (`KTWO_BOX_RESUME_FIX`) oder bis zu einer neuen
  Pause, die nicht aus `_fail` stammt (`RESUME_FIX CLEAR=1`,
  `_pause_from_fail`).
- RESUME-Makro: bei `change_aborted` zuerst `KTWO_BOX_RESUME_CHECK MOVE=<1|0>`
  (vor `_PI_RESUME_PREPARE`, vor jeder Bewegung). Kopfsensor über
  Upstream-`filament_switch_sensor` (`filament_detected`, auch bei
  ENABLE=0 aktuell):
  - **Filament** → RESUME frei (wie Stock: kein Wechsel, alter Faden).
  - **leer** → `RESUME gesperrt nach abgebrochenem CFS-Wechsel (…): …
    meldet KEIN Filament … Druck bleibt PAUSIERT. In der Pause nachholen:
    T1 (lädt T1B), dann RESUME.` (gcode-Fehler, RESUME_BASE läuft nicht).
  - **unbekannt** (`filament_sensor` leer/Abschnitt fehlt) → gesperrt
    (sicherer Default), Freigabe nur `KTWO_BOX_ERROR_CLEAR HEAD_LOADED=1`
    (abgelehnt, wenn der Sensor „leer“ meldet).
- **Nachholen** (`resume_redo_change: True` + `allow_write` + `allow_move`
  + `MOVE=1` + Herkunft T + Ziel bekannt + Kette geschlossen +
  referenziert): `PRE_OPT TNN=<Ziel> Z_HOP=0 SOURCE=T` → Schacht + `M400` +
  Heizen (max(Drucktemperatur vor der Pause, Material-DB bzw. 220); EXTRUDE/
  FLUSH mit `BASE_TEMP=`, §12) → `RETRUDE` (nur bei
  belegtem Hub) → `EXTRUDE` → `WASTE` → `FLUSH` → `END_OPT RETURN=0`; kein
  Schnitt; danach muss der Kopfsensor Filament melden; weiter mit dem
  normalen RESUME (Schacht/Vorextrusion/Rückfahrt). Fehler → `chain_error`,
  RESUME gesperrt, keine Rückfahrt, keine zweite Pause (= Stock
  `resume_err`).
- Abweichungen: Fehler quittiert der Pi nicht im RESUME (Stock
  `clear_err()`), sondern weiter per `KTWO_BOX_ERROR_CLEAR`; Nachholen
  Default AUS (Stock immer); Kopfsensor unbekannt → gesperrt.
- Korrigiert: Anleitung in `_fail`/RESUME-Sperre jetzt `KTWO_BOX_ERROR_CLEAR`
  → `T<n>` → `RESUME` (ein `T<n>` bei offenem Fehler lehnt PRE_OPT ab).
- Neue Option `resume_redo_change` (Default False, `k2-module.cfg`), neuer
  Befehl `KTWO_BOX_RESUME_CHECK`, `KTWO_BOX_ERROR_CLEAR HEAD_LOADED=1`,
  `KTWO_BOX_PRE_OPT … SOURCE=`.
- **Temperatur (Fix 17.10.2026 spät, NOTIZ §6a.10):** hatte der abgebrochene
  Wechsel schon auf die Wechsel-Temperatur geheizt (`M109 max(Ziel, 220)`),
  übergibt `_fail` der neuen Pause `CFS_TEMP=<Druckziel vor dem Aufheizen>`;
  das PAUSE-Makro merkt es als `hotend_temp` (statt `extruder.target`), RESUME
  heizt darauf. Kein `CFS_TEMP` bei Abbruch vor dem Heizen, Druckziel
  ≥ Wechsel-Temperatur oder Ziel vorher 0/unbekannt (dann wie bisher
  aktuelles Ziel + Meldung). Abbruch einer Wiederholung IN der Pause: keine
  neue PAUSE (`hotend_temp` der ersten bleibt), Düse zurück auf das Ziel vor
  diesem Wechsel (Pausentemperatur).

## 12. Spülen wie Stock + Material-Datenbank + Preload-Schutz (Ergänzung 17.10.2026 abends)

> **ENTWURF – am Gerät UNGETESTET.** Belege: `FLUSH-BEFUND-2026-10-17.md`,
> `PAUSE-RESUME-MATERIAL-BEFUND-2026-10-17.md` (§C P1–P3), `BOOTLOADER-HOST-
> NACHTRAG-2026-10-17.md` (N2/N3/N6). Ablauf/Stock-Vergleich:
> `config-pi/NOTIZ-reinigung-resume.md` §7. Rechnung über `nachbau/flush.py`
> und `nachbau/material_db.py` (in `extras/k2_nachbau/`, weich importiert:
> fehlen sie, gilt das bisherige Verhalten). Offline: `test_k2_box.py` 47/47.

### 12.1 Neue Optionen `[k2_box]` (Defaults; alle in `k2-module.cfg` kommentiert)

| Option | Default | Wirkung / Fallback |
|---|---|---|
| `material_database` | leer (aus) | Pfad zur Creality-`material_database.json` (Zielpfad von `install.sh`: `~/printer_data/config/k2/material_database.json`, nicht im Repo). Leer/fehlt/kaputt → bisheriges Verhalten + Meldung beim Connect, kein Config-Fehler |
| `flush_mode` | `color` | `color` = Stock-Spülmenge aus alter/neuer RFID-Farbe (unbekannt → `Tn_extrude` 140 → 90 + 70 mm = bisher), `LENGTH=` über `cal_flush_list`; `fixed` = immer `box_first + box_need`, `LENGTH=` in 90er-Stücken (Stand bis 17.10.); **`matrix`** (neu, §12.4) = im laufenden Druck die Slicer-Spülmatrix der G-Code-Datei, sonst wie `color` |
| `remain_material_file` | `~/printer_data/config/k2/remain_material.json` | **neu (§12.4):** Düsen-Material über Neustarts (Stock `remain_material_data.json`, gleiches Format). Leer = aus (nur im Speicher, Stand bis 17.10.). Tilde wird aufgelöst |
| `flush_clean` | `stock` | `stock` = Blech zwischen den Abschnitten, Silikon-Kreise + Schacht verlassen nur am Ende; `legacy` = volles Abstreifen (`gcode_wipe`) nach jedem Abschnitt |
| `flush_wipe_offset` | 3 | Kreisstart-Versatz `monotonic() % 3` mm (Stock `clean_offset_pos`); 0 = immer dieselbe Stelle (Tests) |
| `wipe_minimum_cruise_ratio` | 0 | Upstream-Ersatz für Stock `ACCEL_TO_DECEL=ACCEL` (9cb9975 kennt nur `MINIMUM_CRUISE_RATIO`); danach alter Wert, nur wenn lesbar |
| `flush_blow` / `flush_blow_fan` | `True` / `fan0` | Stock `M106 P0 S50` zu Beginn, je Abschnitt Lüfter `flush_blow_time` s voll, am Ende Lüfter (+ `<fan>_en`) wie vorher; ohne `[output_pin <fan>]` übersprungen |
| `flush_fan_start` / `flush_blow_time` | 50 / 5 s | Stock-Werte |
| `flush_velocity_factor` | 1,2 | 80 %-Teil jedes Abschnitts außer dem letzten; 1 = eine Geschwindigkeit (bisher) |
| `max_temp_flush_times` | 3 | Stock: Index des letzten Abschnitts (Faktor 1, Temperatur zurück) |
| `box_need_clean_length_max`, `nozzle_volume`, `flush_multiplier`, `diff_material_type_min_flush_len`, `baseline_nozzle_diameter` | 100, 183, 1, 370, 0,4 | Stock-`[box]`-Defaults (FLUSH-BEFUND §7.3) |
| `flush_clog_check` / `flush_clog_min_mm` | `False` / 10 | Stock key845 (Messrad-Summe < 10 mm) – hier nur WARNUNG |
| `box_wait_timeout` | **380** (vorher 200) | = `preload_force_timeout(1 Slot)` (N6) |
| `Tn_extrude` | 140 | jetzt genutzt: Stock-Fallback der Spülmenge |
| `ending_material_purge`, `max_tube_length`, `ending_material_timeout`, `end_print_unload` | True, 3000, 3600, False | Restmaterial / Druckende – §12.5 |

### 12.2 Verhalten

- **Temperaturen** (`_change_temp`, P1): `TEMP=` fest; sonst
  `max(Basis, Material-Temperatur)` – Basis = `BASE_TEMP=` (Nachholen/
  Runout: Drucktemperatur vor der Pause) bzw. Extruder-Ziel; Material =
  `kvParam.nozzle_temperature` der DB, Schlüssel = RFID-`material_type` des
  Slots (Rückzug: Material in der Düse, Einzug: Ziel). Ohne DB/unbekannt:
  `Tn_extrude_temp` 220 (+ `HINWEIS` mit DB). Kappung `[extruder] max_temp − 10`.
  Kommentar korrigiert: die Temperatur steht nicht im RFID-Tag.
- **Spülen** (`cmd_flush`): Menge `plan_flush_lengths`, Geschwindigkeit
  `min(360, vol/2,4·60)·Düse/0,4` (ohne DB `Tn_extrude_velocity`),
  Temperatur `max(Basis, T_alt, T_neu)`, mit DB bei `T_alt ≥ T_neu` Spitze
  `max(maxTemp)` (`M104` + `TEMPERATURE_WAIT MINIMUM=max−5`, zurück im
  letzten Abschnitt), Abschnitte `flush_segment_lines`, Blasen
  `gcode_blow`, Reinigung `gcode_nozzle_clean` (G2/G3 mit `[gcode_arcs]`,
  sonst 16-Eck). Nach ausgeführtem Spülen `remain_material` = Ziel
  (Typ/Farbe/Slot; mit `remain_material_file` auch nach Neustart bekannt,
  §12.4, ohne → unbekannt → 90 + 70 mm).
- **RESUME-Vorextrusion** (P2): Status `prime_speed` = `vol/2,4·60·0,8` des
  Materials in der Düse (unbekannt 288), 0 ohne DB → `_PI_RESUME_PRIME`
  nimmt dann `resume_prime_speed` 460.
- **Stage-7-Greifen** (P3, Pi-Erweiterung): mit DB
  `min(Tn_extrude_velocity, vol/2,4·60)` (TPU 50 mm/min).
- **Preload** (`KTWO_BOX_PRELOAD … [FORCE=1]`, N2/N3): op 2/3 nur bei
  leerem Kopfsensor, Druck/Pause nur mit `FORCE=1`, Maske 0 bei op 2/3
  abgelehnt (`preload_block_reason`) – gesperrt = nichts gesendet.
  N5 (`0x0d [0f 01]` beim Connect) bewusst NICHT eingebaut – offener Gerätetest.
- **Status** neu: `material_db`, `material_db_info`, `remain_material`,
  `prime_speed`, `flush_mode`, `flush_last`; ab §12.4 zusätzlich
  `remain_material_file`, `remain_material_load`, `remain_material_info`.

### 12.3 Offen / am Gerät

- Spülbahn am Gerät (Blech ohne Kreise zwischen den Abschnitten, Kreise am
  Ende, 5-s-Blasen), DB-Temperaturen (PETG 250, PLA→PLA Spitze 240).
- ~~`remain_material` wird nicht persistiert~~ → §12.4 (`remain_material_file`).
- ~~Slicer-Spülmatrix nicht umgesetzt~~ → §12.4 (`flush_mode: matrix`, Default aus).
- `KTWO_BOX_WIPE`/`_PI_NOZZLE_CLEAN` (G28/START_PRINT/G29/RESUME) unverändert
  (erstes X 160 statt Stock 170, ohne Versatz) – Bahn bleibt == `_PI_CLEAN_PATH`.
- N5 (Preload-Freigabe `[0f 01]` nach Box-Reset) am Gerät prüfen.

### 12.4 Düsen-Material persistent, Slicer-Spülmatrix, M600 (Ergänzung 17.10.2026)

> **ENTWURF – am Gerät UNGETESTET.** Belege: `REMAIN-MATERIAL-BEFUND-2026-10-17.md`
> (§1/§4/§6 + Nachtrag Gegenprobe), `SDCARD-GCODEMOVE-BEFUND-2026-10-17.md`
> (A, D.1, D.3), `FLUSH-BEFUND-2026-10-17.md` §2/§3. Rechnung
> `nachbau/flush.py` (`gcode_matrix_flush_len`, `finish_flush_len`).
> Offline: `test_k2_box.py` 52/52; Batch `upstream-test/flush-material` 26/26.

**`remain_material_file`** (Default `~/printer_data/config/k2/remain_material.json`, leer = aus)

- Format Stock-kompatibel: `{"remain_material": {"color": "0RRGGBB"|null,
  "type": "<id>"|null}}`; k2_box schreibt zusätzlich `"tnn": "T1A"` (Slot
  des gespülten Filaments, Stock ignoriert ihn), liest weitere Schlüssel
  tolerant. Die Stock-Datei `/mnt/UDISK/creality/userdata/box/
  remain_material_data.json` kann 1:1 als Startwert dienen (dann ohne Slot →
  `flush_mode: matrix` nutzt die Matrix erst ab dem ersten Spülen). Farben
  `-1`/`none`/`unknown` = unbekannt (Stock `ignore_ops`). Hinweis: Stock
  speichert als `type` das `material_type` von `Tn_data`, k2_box die
  RFID-`mat_id` – gleiche Rolle (Material-DB-Schlüssel), Stock-Werte werden
  unverändert übernommen.
- **Lesen** genau einmal bei `klippy:connect`. Fehlt → Hinweis (Datei wird
  nach dem nächsten Spülen angelegt). Unlesbar, kein JSON, falsche Struktur
  (`{}`, `null`, Liste, fehlende `color`/`type`), Farbe ≠ `^0[0-9A-Fa-f]{6}$`,
  ungültiger Typ → **nur WARNUNG**, Material unbekannt (90 + 70 mm wie
  bisher), **nie Config-Fehler**. Eine inhaltlich kaputte Datei wird **nicht
  überschrieben**, sondern nach `<datei>.defekt-<JJJJMMTT-hhmmss>` umbenannt
  (Inhalt bleibt zur Diagnose). Stock dagegen überschreibt sie mit `null`
  bzw. bricht den Klippy-Start ab (Befund §4).
- **Schreiben** nur nach erfolgreichem `KTWO_BOX_FLUSH` (wirklich gespült,
  `allow_move`) und nur, wenn Farbe/Typ/Slot sich ändern – **nicht** bei
  Cut, Rückzug/Entladen, Einzug, Spülfehler, Abbruch/Pause, Plan ohne
  `allow_move` (wie Stock: einziger `save`-Aufruf nach dem Spülen).
  Atomar: `<datei>.tmp` im selben Verzeichnis, `flush` + `fsync`,
  `os.replace`, `fsync` des Verzeichnisses, fehlende Verzeichnisse werden
  angelegt; andere Top-Level-Schlüssel einer gültigen Datei bleiben.
  Schreibfehler = WARNUNG, der Wert gilt bis zum Neustart, der Wechsel läuft
  weiter.
- **`KTWO_BOX_REMAIN [COLOR=0RRGGBB|#RRGGBB|none] [TYPE=<id>|none]
  [TNN=T1A] [CLEAR=1]`** – **Nicht-Stock** (Stock hat kein Reset-Kommando):
  ohne Parameter Anzeige; sonst Korrektur nach Handarbeit an der Düse
  (gilt für den nächsten Wechsel, wird gespeichert, wenn an). Bewegt und
  sendet nichts. Das in der Aufgabe skizzierte `KTWO_BOX_REMAIN SET …` ist
  unter Upstream nicht möglich (Wort ohne `=` = „Malformed command“) – daher
  nur `KEY=VALUE`.
- **Status:** `remain_material_file` (absoluter Pfad, `''` = aus),
  `remain_material_load` (`aus`/`ungelesen`/`fehlt`/`geladen`/`fehler`),
  `remain_material_info` (Meldung).
- Install-Hinweis: Den Holschritt der Stock-Datei baut `install.sh
  --remain-material` (eigener Arbeitsgang, Zielpfad = obiger Default).

**`flush_mode: matrix`** (Default bleibt `color`)

- k2_box liest die Slicer-Spülmatrix selbst aus der laufenden Druckdatei
  (Upstream `virtual_sdcard.file_path()`): die letzten **und** ersten
  256 KiB (kleine Dateien ganz) nach `; flush_volumes_matrix = …` und
  `; flush_multiplier = …` (Ende hat Vorrang = Orca-CONFIG_BLOCK; Zeilenform
  **ANNAHME**, am ersten Orca-Mehrfarbdruck prüfen). Cache nach Pfad +
  Größe + mtime, geleert bei `virtual_sdcard:reset_file`.
- Index wie Stock `[alt][neu]` = Slicer-Werkzeugnummer, aber **direkt
  durchgereicht** statt rückwärts gesucht: `KTWO_BOX_FLUSH … TOOL=<n>`
  (`_KTWO_TOOL_CHANGE T=<n>` und `M8200 L I<n>` → `M8200 F` reichen `n`
  durch); ohne `TOOL=` feste Zuordnung aus `TNN`. Altes Werkzeug = Slot des
  zuletzt gespülten Filaments (`remain_material.tnn`, auch aus der Datei).
- Nur bei `print_stats.state == "printing"` (Stock BW Z.4429; in der Pause,
  beim Nachholen/Runout und von Hand → Farbweg mit HINWEIS), ohne `LENGTH=`
  und mit bekanntem altem Werkzeug. Länge = `M[alt][neu] / 2,4 ×
  flush_multiplier` (fehlt → 1, **ohne** `nozzle_volume`), danach
  `finish_flush_len` (anderes Material ×2/min 370, sonst min 160).
- Rückfall auf `color` mit **WARNUNG** (kein Fehler, keine Pause): keine
  Druckdatei, keine Matrix, leer, nicht quadratisch, Index ≥ n (z. B.
  Creality-Print-Dateien mit `[0]`), altes Werkzeug unbekannt, `TOOL` passt
  nicht zu `TNN`. Der FLUSH-Plan nennt die Quelle („Slicer-Matrix [i][j] =
  V mm³ / 2,4 × m“).

**`M600`** (`macros.cfg`): Stock-`virtual_sdcard` macht aus `M600` im
Dateistrom `PAUSE` (VS:1228, dort nur mit Power-Loss-Schalter). Upstream
kennt kein `M600` (geprüft) → Makro `M600` → `PAUSE` (unser PAUSE-Makro:
Parken, 140 °C, RESUME wie gewohnt), nur im laufenden, nicht pausierten
Druck; sonst nur Meldung (kein Parken außerhalb eines Drucks). Kein
`rename_existing` nötig, kein Konflikt.

**Offen / am Gerät:** erste Speicherung + Neustart (Datei im Mainsail-Editor
sichtbar, Inhalt), Zeilenform der Orca-Matrix in einer echten Mehrfarbdatei
und Spülmengen gegen Orca-Anzeige, `M600` aus einer Orca-Datei (Pause,
RESUME), Stock-Datei als Startwert nach `install.sh --remain-material`.

### 12.5 Restmaterial („ending material“) + Druckende (Ergänzung 17.10.2026)

> **ENTWURF – am Gerät UNGETESTET.** Belege: `FLUSH-SONDERFAELLE-BEFUND-
> 2026-10-17.md` §2 (Flag `is_use_ending_material`, `extrusion_all_materials`
> Z.1783–1824), §3 (`box_end` Z.5104–5131), §8 Vorschläge 1–2;
> `GHIDRA-CFS-BEFUND2-2026-10-16.md` §4 (Status 0x50). Offline:
> `test_k2_box.py` 59/59; Batch `upstream-test/flush-material` 34/34
> (neuer Lauf `lauf-fm-ending`).

**Optionen `[k2_box]`** (in `k2-module.cfg` kommentiert)

| Option | Default | Wirkung |
|---|---|---|
| `ending_material_purge` | `True` (= Stock) | Restmaterial-Fall beim nächsten Wechsel / `M8200 P` / Druckende ausspülen statt Schnitt + Rückzug; `False` = bisher (Schnitt + Rückzug, Hinweis) |
| `max_tube_length` | 3000 (int, 80..10000) | Stock-`BoxCfg`-Obergrenze in mm; das letzte Stück wird gekürzt (Stock: 38 × 80 = 3040) |
| `ending_material_timeout` | 3600 s | Zeitlimit des Ausspülens (Pi-Zusatz, Stock: keins) |
| `end_print_unload` | `False` | Druckende wie Stock `box_end`: heizen, schneiden, in die Box zurück |

**Auslöser (Stock `send_data` Z.2145–2155):** Jeder 0x0a-Poll (Timer ~4 s,
`KTWO_BOX_STATUS`, Box-Warten) mit Status **0x50** im PRINT (bzw. gespeichert
im ERROR) = Einlauf oder Hub des Druck-Slots frei setzt – nur bei
`print_stats` printing/paused – den Merker (`printer.k2_box.ending_material`,
`ending_material_tnn`, Meldung einmal). `PRE_OPT` pollt vorher noch einmal
frisch (lesend, nur im Druck mit beiden Freigaben). Polls während einer
laufenden Kette setzen ihn nicht (sie sähen das 0x50 des alten Slots).
Gelöscht nach dem Ausspülen, nach erfolgreichem `EXTRUDE`, in
`KTWO_BOX_RUNOUT` (Rest ist am Kopfsensor vorbei; Stock
`check_material_refill` spült dann nicht) und per `KTWO_BOX_ENDING
CLEAR=1`. Nur im Speicher (Stock ebenso). `KTWO_BOX_RUNOUT` und das
Nachholen im RESUME (`resume_redo_change`) spülen nie aus.

**Ausspülen (Stock `extrusion_all_materials`)** – nach `KTWO_BOX_PRE_OPT`
(Kette aktiv, kein Skip) als eigener Schritt `ENDING_PURGE`:

1. nur mit `allow_write` UND `allow_move` (sonst PLAN, im Druck PAUSE wie
   jeder Wechsel); Kopfsensor ohne Zustand → **Abbruch, PAUSE + Meldung,
   nichts gefördert** (Stock förderte blind bis 3000 mm);
2. Kopfsensor aus (PRE_OPT), Schacht, Heizen auf max(Basis, Material-
   Temperatur) (Basis in der Pause `_PI_PARAM.hotend_temp`, Stock
   PRINTER_PARAM; ohne DB `Tn_extrude_temp` 220);
3. Schleife: `G1 E80 F<v>` + `M400` (statt Stock `reactor.pause(t − 0.5)`
   **ohne** `monotonic()` – Creality-Fehler, nicht übernommen), Blasen
   (`flush_blow`, fan0 5 s), Blech **ohne** Kreise (bleibt im Schacht);
   `v` = `max_vol/2,4·60` des Materials in der Düse (Hyper PLA 575, PLA14
   350) **ohne ×0,8, ohne 360-Grenze**; ohne DB bzw. ohne `max_vol`
   `Tn_extrude_velocity` 360 (= bisherige Spülgeschwindigkeit ohne DB);
4. Ende: Kopfsensor meldet nach einem Stück LEER → genau **ein** weiteres
   Stück (Stock; Kopf schon beim Start leer → 2 Stücke). Obergrenze
   erreicht und Kopf noch voll → Fehler/PAUSE (Stock: Warnung + weiter);
   erst an der Grenze leer → WARNUNG. Sensor fällt unterwegs aus oder
   Zeitlimit → Fehler/PAUSE. Merker bleibt bei jedem Fehler;
5. danach: Box `0x04 [00 01]` IDLE, `loaded_slot` vergessen (Stock
   `last_cmd = None`), Merker gelöscht. In der Kette entfallen `CUT` und
   `RETRUDE` („übersprungen (Restmaterial ausgespült)“), `EXTRUDE` lädt das
   neue Filament. **Kein Abkühlen in der Kette** (Stock `set_cool_temp`
   M104 S140 – hier nur beim Ausspülen von Hand; in der Kette heizt
   EXTRUDE gleich wieder und END_OPT stellt das Ziel zurück).
   `remain_material` bleibt (Stock: kein `save`; in der Düse ist weiter
   dasselbe Material).

`CUT`/`RETRUDE` ohne Kette bei offenem Fall: nur WARNUNG (Rückzug eines
Rests ohne Ende in der Box, ANNAHME Hub-Verstopfung).

**`KTWO_BOX_ENDING [SET=1 SLOT=A] [CLEAR=1] [PURGE=1]`** (Nicht-Stock
außer PURGE): Anzeige; Merker von Hand setzen/löschen (Stock
`use_ending_material_flag_clear` hat keinen Aufrufer); `PURGE=1` = Stock
`BOX_EXTRUSION_ALL_MATERIALS` (unabhängig vom Merker, nur außerhalb eines
Drucks oder in der Pause, nicht während einer Kette; Sensor aus/zurück,
danach M104 S140, Kopf bleibt im Schacht wie Stock).

**Druckende `KTWO_BOX_END_PRINT [UNLOAD=0|1]`** (= Stock `BOX_END`, aus
`END_PRINT` in `macros.cfg`, nur bei `change_enabled` UND
(`end_print_unload` ODER `ending_material`) UND referenzierten Achsen, nach
Rückzug/Z-Anhebung, vor `TURN_OFF_HEATERS`): Kette `PRE_OPT Z_HOP=0` →
(offener Fall: Ausspülen) → mit `UNLOAD` (Default `end_print_unload`) und
Kopfsensor „Filament“: Schacht, Heizen auf die Material-Temperatur, `CUT`,
`RETRUDE` (Box 0x11 p0×4 + E−10 + p1) → `END_OPT RETURN=0` (Schacht
verlassen, Ziel zurück). Kopf leer/unbekannt → kein Entladen (Meldung).
**Kein Spülen am Druckende** (Stock ebenso). `remain_material` bleibt.
Fehler (auch Kopfsensor unbekannt beim Ausspülen) → nur `!!`-Meldung,
Kette aufgeräumt, **keine PAUSE, kein G-Code-Fehler** – `END_PRINT` läuft
weiter und schaltet die Heizungen aus. Ohne Freigaben bzw. bei offener
Kette/offenem Fehler: übersprungen mit Meldung.

**Status** neu: `ending_material`, `ending_material_tnn`,
`ending_material_purge`, `ending_last`, `end_print_unload`.

**Offen / am Gerät:** echtes 0x50 im Druck (Zeitpunkt, Slot im Poll),
Länge Kopfsensor → Düse (< 80 mm? sonst reicht das Zusatzstück nicht),
Ausspülen einer leer gelaufenen Spule beim nächsten T<n> (Menge, Abfall im
Schacht, Box nach IDLE), Entladen am Druckende (Schnitt + Rückzug,
Rückzugslänge bis in die Box), mechanische Folge des bisherigen Rückzugs
bei leerem Einlauf (Vergleich).
