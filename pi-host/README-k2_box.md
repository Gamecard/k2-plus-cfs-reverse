> **ENTWURF – am echten Gerät UNGETESTET (Stand 16.10.2026).** Klipper-Modul [k2_box] für Upstream-Klipper; offline getestet (`python3 test_k2_box.py`, 9/9). Entwurf durch einen weiteren KI-Agenten, von uns geprüft.

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
| `k2_box.py` | Klipper-Modul `[k2_box]`: Config wie `[k2_motor]`-Stil + `box_addr`/`poll_interval`; Controller A0–A6 (Auto-Addr nach `k2_motor:ready`, 0x0a-Poll retries=0, RFID/Remain/Version/HW lesend, Preload 0x0d op 0–3, Extrude/Retrude live-identisch, Cut-Platzhalter); Guards (allow_write, nie 0x50/0x06, keine Bursts, ein Lock mit `[k2_motor]`); G-Codes `K2_BOX_STATUS/LOAD/UNLOAD/PRELOAD`; `get_status` für Moonraker | neu, getestet |
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
- `do_cut(...)` – A6 Platzhalter: wirft `NotImplementedError`, sendet NICHTS
  (braucht Motor/Toolhead – ANNAHME, s. §4).
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

**G-Codes:** `K2_BOX_STATUS` (frischer Poll + Bericht, lesend),
`K2_BOX_LOAD SLOT=A..D [DRY_RUN=1]`, `K2_BOX_UNLOAD SLOT=… [DRY_RUN=1]`,
`K2_BOX_PRELOAD SLOT=… OP=0..3 [DRY_RUN=1]`. Ohne allow_write bzw. mit
DRY_RUN nur Plan (nichts gesendet). Fehler als `CfsStatusError`-Klartext
(z.B. 0x0c Odometer-Stillstand); Shutdown nur per `shutdown_on_failure`.

## 3. Einbindung in Upstream-Klipper

1. Dateien kopieren (nachbau/ wird zur Laufzeit per sys.path gefunden –
   Pfadsuche wie `k2_transport.py`; für Klipper ohne nachbau/ die vier
   Dateien mit kopieren – ANNAHME, s. §4):
   - `k2_box.py` → `klippy/extras/k2_box.py`
   - `nachbau/box.py`, `cfsseq.py`, `auto_addr.py`, `serial485.py` →
     daneben (oder Pfad anpassen)
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

# ANNAHME geteilter Bus (s. §4): dieselbe ttyS5-Instanz als Objekt 'k2_485'
# für BEIDE Module registrieren (EIN Lock, Halbduplex). Upstream hat keinen
# zentralen 485-Manager – Port-Verwaltung bleibt beim Betreiber.
```

3. Ablauf prüfen:
   - `K2_BOX_STATUS` → Poll + `Zustand=STATE_IDLE Hub=0x00 Temp=… Slots=…`
     (geht auch mit `allow_write: False`).
   - `K2_BOX_LOAD SLOT=B DRY_RUN=1` → Plan, nichts gesendet.
   - `allow_write: True` + `K2_BOX_LOAD SLOT=B` → echter Box-Load
     (Heizen/Fahren per Makro M8200/BOX_LOAD_MATERIAL davor – hier nur Box).
   - Auto-Addr läuft nach `k2_motor:ready` von selbst (vorher `wait_motor`).

## 4. Annahmen (Unbelegtes – alles mit ANNAHME im Code markiert)

- **Upstream-API ohne Quellbaum:** `register_event_handler("klippy:connect"
/"k2_motor:ready")`, `register_command`, `get_options/get`, `reactor.pause/
register_timer`, `send_event`, `gcode.respond_info/error`, `GCodeCommand.
get_int` – gegen die bekannte API geschrieben (Muster `k2_motor_init.py`),
Kapselung in Controller/Hülle, vor Hardware-Lauf gegen echten Quellbaum prüfen.
- **Geteilter Bus:** Upstream hat keinen 485-Manager – ANNAHME `printer`-Objekt
  `'k2_485'` mit derselben `serial485.Serial485`-Instanz für `[k2_motor]`
  (`Rs485Transport`) und `[k2_box]` (`BoxAction`). Ohne Bus Diagnose-Modus
  (wie `[k2_motor]` ohne Bus). Lock-Teilung im Test mit einem `Lock` bewiesen.
- **Auto-Addr-Persistenz:** Uniids nur in-memory (`_addr_store`); Stock speichert
  in `printer.cfg` (`mb_addr_table_uniids`) – Übernahme in `configfile` fehlt
  (Entwurf). `expect_addr=None` für 0xFE-Broadcasts (Beleg §6.8).
- **Cut:** `BOX_CUT_MATERIAL`/`M8200 C`/`CR_BOX_CUT` fahren Kopf an Cutter-Pos
  und nutzen Motor-Boards (Beleg box.cfg-Kette + LIVE-ABFRAGEN Cut-Kalibrierung
  Kp 300/Stall 3) – Box-Modul sendet bewusst nichts (Platzhalter).
- **Toolhead-Seite von Load/Unload:** Heizen (M104 220 °C), Fahren an
  Extrude-Pos (X133 Y378), Purge/Flush (G1 E…), Wischen – machen Original-Makros
  (`BOX_LOAD_MATERIAL_*`, M8200 P→L→F→O); hier nur Box-Förderung. `odo_polls=5`
  Default aus Live B (Beleg `EXTRUDE_LIVE_CMDS_B`); echte Stufen-Steuerung ist
  sensor-/messrad-gesteuert im `.so` (TODO dort), hier feste Poll-Zahl.
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
