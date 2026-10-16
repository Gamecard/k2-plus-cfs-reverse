> **ENTWURF – am echten Gerät UNGETESTET (Stand 16.10.2026).** Klipper-Zusatzmodul für Upstream-Klipper auf einem Pi-Host; nur offline mit simulierter MCU/Motoren getestet (`python3 test_k2_motor.py`, 9/9). Entwurf durch einen weiteren KI-Agenten, Review und drei Korrekturen (Pins vor Connect, gültige print_time, query_gpio_status) durch uns.

# BERICHT – Klipper-Zusatzmodul [k2_motor] (Entwurf, 16.10.2026)

Auftrag: `AUFTRAG.md` in diesem Ordner. Gearbeitet AUSSCHLIESSLICH auf Deutsch,
ohne Netzwerk, ohne ssh/curl, ohne Zugriff auf den 3D-Drucker. Neue Dateien NUR
in ``pi-host/``, alles andere
nur gelesen.

## 1. Ergebnis-Übersicht

| Datei | Zweck | Stand |
|---|---|---|
| `k2_transport.py` | Zwei Transporte mit gleicher Schnittstelle `transact(addr, fun, data, timeout, retries) → (status, fun, data)`: `Rs485Transport` (X/Y/Z/Z1) + `TransparentTransport` (E via Düsen-MCU, volle Frames mit `serial485.build_frame`/`parse_frame`, CRC + Absenderprüfung, retries=0 wie Stock) | neu, getestet |
| `k2_motor_init.py` | Klipper-Modul `[k2_motor]`: Config wie Stock-`[motor_control]` + `allow_write`/`shutdown_on_failure`/`auto_init`/`nozzle_mcu`/`main_mcu`; Ablauf P0–P9 nach Befund; E-Param via transparent 0x81 (Stock-Bug 0x85 NICHT nachgebaut, Guard im Code); Fehler analog key798–803 (Shutdown nur per Option); Event `k2_motor:ready`; G-Codes `K2_MOTOR_INIT`/`K2_MOTOR_STATUS` | neu, getestet |
| `test_k2_motor.py` | `FakeMcu` (transparent + digital_out) + `FakeRs485Bus` (0x81–0x84 inkl. Broadcast/Strapping-Guard, Param mit Echo) → kompletter Init-Lauf; Plan-Vergleich gegen `motor_init.build_init_plan`; E-nur-transparent; allow_write=False; Live-Frame-CRC; dazu `FakePrinter`-Lebenszyklus (Review 1–3: Config-vor-connect, print_time>0, query+Fallback) | neu, **9/9 ok** (s. §6) |
| `BERICHT.md` | diese Datei (inkl. §6 Review-Korrekturen) | neu |

Testbefehl (ohne Klipper, ohne Hardware):

```bash
cd pi-host
python3 test_k2_motor.py
```

Ergebnis am 16.10.2026 (Review-Stand): `ERGEBNIS: ok (9/9 ok)` – Details in §5 (Erststand 6/6) + §6 (Review).

## 2. Architektur

```
printer.cfg [k2_motor] ──► PrinterK2Motor (Klipper-Hülle, load_config)
                               │ parse_klipper_config() → parse_options() (rein, testbar)
                               ▼
K2MotorController (reine Logik P0–P9, ohne Klipper importierbar)
   ├─► Rs485Transport ──► bus (serial485-artig, /dev/ttyS5, 230400)
   │       transact() → (status, fun, data), bevorzugt transact_full()
   ├─► TransparentTransport ──► TransparentMcuAdapter.exchange()
   │       build_frame() → transparent_send(timeout_ms) → parse_frame()
   └─► GpioOps (set_pin/query_pin) ──► KlippyGpio (ppins/output_pin + optional
           query_gpio_status, wenn Creality-MCU-FW vorhanden)
```

**Wiederverwendung statt Neuerfindung:** Phasen, Reihenfolge, Payloads, Pausen
(0,2/0,1/0,05/1,0/0,1/1,0/5 s), Timeouts (0E/0C 0,05 s, sonst 1,0 s,
transparent Broadcast ≈2 s), Versuche (Stall ≤3, Param lesen/schreiben ≤3,
GPIO-Resend 3, Runden ≤retries=4), Toleranz (`isclose(rel_tol=1e-9,
abs_tol=1e-6)`), Filter (`configurable ∩ Config` + immer 9/10/25, nie 226,
nicht-Float nur lesen) und Strapping-Formel (`0x81 + 2·STEP + DIR`) sind ein
direkter Port von `nachbau/motor_init.py` / `motor.py` (dort belegt). Der
Test beweist die Gleichheit (39 ttyS5- + 9 transparent-Frames plan-identisch).

**Transporte (§1 des Auftrags):**

- `Rs485Transport(bus)`: reiner Wrapper, öffnet den Port NICHT selbst
  (Upstream-Hinweis: EIN Port, EINE Queue, Halbduplex-Lock über die ganze
  Transaktion – CFS/Belt teilen sich ttyS5). Nutzt `transact_full()` wenn
  vorhanden (STATUS-Byte bleibt erhalten), sonst `transact()` (Status 0x00
  angenommen – als ANNAHME markiert).
- `TransparentTransport(mcu_adapter)`: baut IMMER den vollen Frame
  (`F7 addr len status fun data crc`, Status 0x00 wie live), `timeout_ms =
  int(timeout*1000)`, `retries+1` Versuche (Aufrufer gibt 0 wie Stock).
  `b''` (leer) → TimeoutError (Stock: Broadcast + `0C [05]` bleiben
  leer/unbeantwortet – Aufrufer wertet das als „erwartet“). Danach
  `parse_frame()` (CRC), Absender- (`read[1] == addr`, Broadcast beliebig)
  und fun-Prüfung, Rückgabe `(status=read[3], fun, data)`.

**Controller (§2):** `K2MotorController(cfg, rs485, transparent, gpio, pause,
logger, send_event)` implementiert P0–P9 exakt nach Befund §2/§4/§5 (s.
Modul-Docstring). `describe_plan()` gibt den Trockenlauf-Text aus (kein Bus).
`run_init(dry_run=False)` führt aus; bei `allow_write=False` (Default) nur
lesende Schritte + Plan (kein GPIO-Setzen, kein Broadcast/Stall/Clear/
Write/Update). `motor_status()` liest Version/Schutz/Feedback (nur lesend).
Guard: E NIE als 0x85 (Exception statt Senden).

**G-Codes:** `K2_MOTOR_INIT [DRY_RUN=1] [ALLOW_WRITE=1]` (Default: Plan +
Diagnose; Schreiben nur mit `allow_write: True` UND ohne DRY_RUN),
`K2_MOTOR_STATUS` (0x0F-Version dekodiert, 0x0C-[0b] via
`decode_protection_local`, 0x08 idx 5/6 als Float – nur lesend).

**Events:** Nach P9 `send_event("k2_motor:ready")` (statt Stock-
`auto_addr:start`; CFS-Auto-Addr wartet darauf – gleiche Reihenfolge wie
Stock wegen gemeinsamer ttyS5-Queue).

## 3. Einbindung in Upstream-Klipper

1. Dateien kopieren (kein `nachbau/` nötig – beide Module sind standalone,
   Fallback-Frame-Helfer eingebaut):
   - `k2_transport.py` → `klippy/extras/k2_transport.py`
   - `k2_motor_init.py` → `klippy/extras/k2_motor.py`
2. `printer.cfg`-Beispiel (Pins wie Werks-`motor_control.cfg` F008, Werte
   gekürzt – volle Param-Liste aus `motor_control.cfg` übernehmen):

```ini
[k2_motor]
switch: 1
motor_closed_loop: x,y,z,z1,e
retries: 4
allow_write: False          # Default: nur lesen + Plan (für echten Init True)
shutdown_on_failure: False  # True → SHUTDOWN wie Stock-key798 (sonst nur Meldung)
auto_init: True
main_mcu: mcu
nozzle_mcu: nozzle_mcu
motor_x_dir: PB9,0
motor_x_step: PB10,0
motor_x_stall: PB11
motor_y_dir: !PB7,0
motor_y_step: PB8,0
motor_y_stall: PB12
motor_z_dir: PB5,0
motor_z_step: PB6,1
motor_z_stall: PB13
motor_z1_dir: PA1,1
motor_z1_step: PB15,1
motor_z1_stall: PA10
motor_e_stall: nozzle_mcu:PB12
x_controller_pos_loop_pid_param_kp: 100
x_controller_spd_loop_pid_param_kp: 0.028
x_controller_spd_loop_pid_param_ki: 6.0
# ... restliche <motor>_<param> aus motor_control.cfg ...
# ID 226 (stall_pos_err_rad_for_slicer) DARF in der Config stehen, wird aber
# NIE gesendet (reiner Host-Parameter, Motor antwortet 0 B – wie Stock-Plan,
# der ihn bewusst auslässt).

[duplicate_pin_override]
# NÖTIG für das Zeitmultiplex: [k2_motor] nutzt dieselben DIR/STEP-Pins wie
# [stepper_*] (Strapping vor dem Broadcast, danach wieder Stepper-Betrieb).
# Synchronisation per M400 (wie Stock L3096/3164).

[mcu]
# ... Main-MCU (Empfehlung: Creality-FW mit query_gpio_status + transparent_*,
# s. §4) ...

[nozzle_mcu]
# ... Düsen-MCU mit config_transparent (Creality-FW) ...
```

3. Ablauf prüfen:
   - `K2_MOTOR_INIT DRY_RUN=1` → Plan ausgeben, nichts senden.
   - `allow_write: True` + `K2_MOTOR_INIT` → echter Init (Pins → Broadcast →
     Runden → Stall → Schutz → Clear → Restore → Param → `k2_motor:ready`).
   - `K2_MOTOR_STATUS` → Version/Schutz/Feedback je Motor.
4. CFS-Auto-Addr auf `k2_motor:ready` warten lassen (statt `auto_addr:start`).

## 4. Offene Punkte / Annahmen (alles Unbelegte ist als ANNAHME markiert)

**Aus dem Befund übernommene V-Punkte (V = vermutet, dort belegt als V):**

- Aufrufer `klippy._connect` (Frame-Tag) vs. `klippy:ready` – hier ANNAHME:
  Auto-Init auf `klippy:connect` (wie Stock nach Connect, transparent-Lookup
  L3089 erst dort möglich). Per `auto_init: False` abschaltbar.
- `switch=0`-Zweig endet nach Normalisierung (V) – so implementiert.
- Adresswert in `0E [02]`-Runde ungeprüft (V) – so implementiert (truthy zählt).
- `06 [03]`-Ergebnis ungeprüft (V) – gesendet, Fehler nur geloggt.
- Schutz-Seitenzweig (`FORCE_MOVE`/`M84` bei Fehlerbits, V) – NICHT
  implementiert (nur geloggt; Druckbetrieb gehört nicht in die Init).
- `motor_params_config`-Import wirkungslos (V) – weggelassen.
- Shutdown-Argumente key798 (V) – eigene Meldung + Option statt Shutdown.
- `pin108 = STEP` (V, aber alle 4 Adressen gehen auf) – Formel wie Befund.
- Restore-Zweck „wieder normale STEP/DIR-Eingänge“ (V) – so dokumentiert.

**Neue ANNAHMEN (Upstream ohne vorliegenden Quellbaum):**

- Klipper-API-Signaturen (`register_config_callback`, `create_oid`,
  `add_config_cmd("config_transparent oid=%d")`,
  `lookup_query_command("transparent_send oid=%c write=%*s timeout_ms=%u",
  "transparent_response oid=%c read=%*s", oid=...)`,
  `register_event_handler("klippy:connect"/"klippy:ready")`,
  `register_command`, `reactor.pause`, `config_digital_out`/
  `update_digital_out`, `ppins`/`output_pin`, `[duplicate_pin_override]`) –
  gegen die bekannte Upstream-API geschrieben, Kapselung in
  `KlippyTransparentAdapter`/`KlippyGpio`, im Code je Stelle mit ANNAHME
  kommentiert. Vor dem ersten echten Lauf gegen den echten Quellbaum prüfen.
- Empfohlene Variante Creality-MCU-FW + Upstream-Klipper (Befund §4):
  `query_gpio_status` + `transparent_*` vorhanden → Rücklesung aktiv. Mit
  reiner Upstream-MCU-FW entfällt die Rücklesung (`query_pin → None`,
  Prüfung übersprungen) UND `transparent_*` muss erst in die Firmware
  portiert werden (deutlich mehr Aufwand, s. GPIO-Befund §4).
- RS485-Port teilt sich mit CFS/Belt (eine Queue, ein Lock) – hier nur als
  injizierter `bus` + Hinweis umgesetzt; Port-Manager fehlt noch.
- Timeouts 0x11/0x0F/0x08 je 1,0 s UNBELEGT (live nie Timeout beobachtet,
  `TIMEOUT_LONG`) – übernommen, im Code markiert.
- `allow_write`-Semantik (Default False → nur lesen + Plan) ist eine
  SICHERHEITS-ANNAHME dieses Entwurfs (Stock schreibt immer). Diagnose ohne
  Broadcast liefert erwartbar key798 (RS485 braucht Broadcast) – kein Fehler
  des Moduls, sondern der Diagnose-Modus.
- Neue Config-Optionen (`allow_write`, `shutdown_on_failure`, `auto_init`,
  `nozzle_mcu`, `main_mcu`) sind Erweiterungen (Defaults stock-kompatibel
  bzw. sicher). `<motor>_<param>`-Sammlung via `get_options()`/Fallback ist
  als ANNAHME implementiert (mehrere Wrapper-Formen versucht).
- `12 02`-UUID gehört NICHT zur Init (belegt) – nicht implementiert (nur
  Hinweis: Webhook `motor_dev_uuid` bei Bedarf separat).
- `MOTOR_FLASH_PARAM` (0x07) beim Boot ungenutzt – nicht implementiert.
- X/Y-Stall erst beim Homing (belegt) – P4 nur Z/Z1/E.
- E hat keine Dir/Step-Pins (nur Stall) – so modelliert.
- Float32-Rundung < abs_tol (belegt) – `_float_to_bytes` ohne Stock-Toleranz-
  Sonderfälle; non-Float-Writes bewusst inaktiv (Schreibformat unbelegt).

**Noch zu tun vor Hardware-Lauf (kein Druckerzugriff in diesem Auftrag):**

- Gegen echten Upstream-Quellbaum kompilieren (`ppins.setup_pin`-Signatur,
  `ConfigWrapper`-Methoden, `GCodeCommand.get_int` verifizieren).
- `duplicate_pin_override`-Syntax für die konkrete Klipper-Version prüfen.
- Live: Pegel vor Broadcast gegen `MOTOR_READ_ALL_PIN_IO` prüfen (Stock tut
  es per `query_gpio_status` – hier implementiert, aber live noch offen).
- Transparent-Pfad für E-Param live verifizieren (hier korrekt auf 0x81,
  Stock-Bug 0x85 entfernt – Gegenprobe am Log: Stock-0x85-Frames kommen im
  Test nie vor).
- `auto_addr`-Reihenfolge (`k2_motor:ready` → CFS) am laufenden System testen.

## 5. Testergebnis (16.10.2026, ohne Hardware, ohne Klipper)

`python3 test_k2_motor.py` → `ERGEBNIS: ok (6/6 ok)`:

1. **Transparent-Format vs. Live (CRC) – ok:** 3 Live-Requests byte-identisch
   (`f7 ff 04 00 0e 01 89` Broadcast, `f7 81 04 00 0e 02 80` E-Addr-Read,
   `f7 81 04 00 11 02 14` E-Stall) + 2 Antworten (`f7 81 04 00 0e 81 00`,
   `f7 81 0b 00 0c 00×8 fb` Schutz-8x00); `build_frame`/`parse_frame` CRC ok,
   Roundtrip via `TransparentTransport`+FakeMcu mit live-identischen
   Request-Bytes. (Gegenprobe gegen `serial485.build_frame` wenn importierbar.)
2. **Kompletter Init-Lauf – ok:** Fake-Werte mit 2 gezielten Abweichungen
   (X ID 70 300→100 wie live) → Status `ready`, `is_ready=True`, Event
   `k2_motor:ready`, 16 GPIO-Sets (8 Strapping + 8 Restore), 0 Fehler.
3. **Plan-Vergleich – ok:** Gesendete Folge == `motor_init.build_init_plan`
   (gleiche Config): **ttyS5 39 + transparent 9 Frames plan-identisch**
   (Reihenfolge + Payloads, bedingte Writes via isclose gefiltert).
4. **Extruder via transparent – ok:** E 8 Frames via transparent an 0x81
   (0E/11/0C/06 dabei); RS485-Addrs `{0xFF,0x81,0x82,0x83,0x84}` – **nie 0x85**.
5. **allow_write=False – ok:** Keine schreibenden Frames (kein Broadcast/
   Stall/Clear/Write/Update, kein GPIO-Setzen); nur Reads (16 RS485- + 4
   transparent-Versuche) + Plan-Text mit `allow_write=False`-Hinweis.
   (Erwarteter Nebeneffekt im Diagnose-Modus: key798, da RS485 ohne
   Broadcast nicht antwortet – geloggt, kein Shutdown bei Default.)
6. **K2_MOTOR_STATUS – ok:** 5 Motoren je Version `1.9.7.81` (live-Werte),
   Schutz ok (8x00 dekodiert), pos_fdb/spd_fdb 0.0.

Hinweis zur Ausgabe: Der Diagnose-Lauf loggt erwartbar
`FEHLER: [key798] ...` (Diagnose ohne Broadcast) – das ist die korrekte
Fehlermeldung analog Stock, kein Testfehler (Endzeile `6/6 ok` zählt).

## 6. Review-Korrekturen (AUFTRAG2, 16.10.2026)

Auftrag: `AUFTRAG2.md` in diesem Ordner (gleiche Regeln: Deutsch, kein
Netzwerk/ssh/curl/Drucker, nur in diesem Ordner geschrieben). Die drei
verbindlichen REVIEW-Befunde sind vollständig umgesetzt; `python3
test_k2_motor.py` meldet jetzt `ERGEBNIS: ok (9/9 ok)` (6 alte + 3 neue
Review-Tests).

### 1. Pins zu spät angelegt – FIX (Review 1)

- Fehler: `GpioOps._output_pin` legte `ppins.setup_pin("digital_out", pin)`
  lazy zur Laufzeit an (nach `klippy:connect`). Upstream-Klipper verlangt
  MCU-Objekte/Pins im Konstruktor bzw. während des Config-Ladens
  (Konfigurationsbefehle gehen vor dem Connect an die MCU).
- Fix: `KlippyGpio.__init__(printer, main_mcu_name, pin_specs)` legt ALLE
  Strapping-Pins (aus `motor_<m>_dir/_step` für x/y/z/z1 plus Enable `!PA9`,
  belegt GPIO-Befund (lokal) §0) SOFORT per `ppins.setup_pin` an; MCU-Fremdpins
  (z.B. `nozzle_mcu:PB12` E-Stall) werden übersprungen. Das alte Lazy-
  `_output_pin` ist entfernt – `set_pin` wirft `KeyError`, wenn der Pin nicht
  in `load_config` angelegt wurde. Zur Laufzeit nur noch setzen.
- Transparent analog (gleicher Lebenszyklus-Fehler): `PrinterK2Motor.__init__`
  macht `nozzle_mcu.create_oid()` + `register_config_callback(
  _build_transparent_config)` VOR connect (wie Stock-`_build_config` @0xa04fc);
  `add_config_cmd("config_transparent oid=%d")` läuft im Config-Callback vor
  finalize. `handle_connect` macht nur noch Lookups (`_tadapter.setup()` +
  `_gpio.handle_connect()`), danach optional Auto-Init.

### 2. `set_digital(0, level)` – FIX (Review 2, Entscheidung begründet)

- Fehler: `pin.set_digital(0, level)` mit `print_time=0` ist falsch –
  `MCU_digital_out.set_digital(print_time, value)` braucht eine gültige Zeit.
- Entscheidung: Variante (a) gequeue'tes `digital_out` mit gültiger
  `print_time` + M400 – NICHT Variante (b) direkter MCU-Befehl
  `set_digital_out pin=%u value=%c` (obwohl in der Creality-MCU-FW vorhanden,
  Upstream-Befehl, sofort wirksam, ohne oid). Begründung: (a) entspricht dem
  Stock (GPIO-Befund (lokal) §1, B: `update_digital_out` via `set_digital` + Lookahead +
  `M400`, `set_digital_out` im `.so` negativ belegt ohne einzige Referenz),
  respektiert das `duplicate_pin_override`-Zeitmultiplex mit `config_stepper`
  (M400 synchronisiert Stepper-Queue und Strapping – sofortiges Setzen würde
  die Move-Queue umgehen). Im Code als Kommentar bei `KlippyGpio` dokumentiert.
- Implementierung (gekapselt hinter `GpioOps`): `set_pin` holt
  `toolhead.get_last_move_time()` (Fallback
  `mcu.estimated_print_time(reactor.monotonic()) + 0,100 s`), prüft `> 0` und
  ruft `pin.set_digital(print_time, level)`; neu `sync()` macht
  `toolhead.wait_moves()` (Fallback `gcode.run_script("M400")`). Der
  Controller ruft `sync()` nach jedem Setzen (P1, Resend, Restore, switch=0),
  bevor Broadcast/Rücklesung folgt. Pin-Nummer für (b) wäre über
  `ppins.lookup_pin` / MCU-Enumeration `pin` auflösbar (s. §3) – nicht
  implementiert, da (a) gewählt.

### 3. `query_pin`-Stub – FIX (Review 3)

- Fehler: `query_pin` gab immer None zurück.
- Dictionary-Prüfung (`lokale MCU-Dictionaries/mcu-main-F008-GD32F303RET6.dict.json`,
  nur gelesen): Befehle `query_gpio_status oid=%c gpio_pin=%c` (75) und Antwort
  `gpio_status oid=%c status=%c` (-20) vorhanden; `config_digital_out oid=%c
  pin=%u …` (24), `update_digital_out` (21), `set_digital_out pin=%u value=%c`
  (20), `config_transparent` (74), `transparent_send` (73) ebenfalls da. Aber
  KEIN passender `config_*`-Befehl für das Query-oid – daher ANNAHME: kein
  config-Befehl nötig, eigenes Query-OID via `create_oid()` FRÜH (Konstruktor),
  für alle Pins geteilt. Stock-Log (Agenten-Log (lokal) :228ff, :2492ff) zeigt `oid:
  11` für alle 8 Pins in beiden Phasen – ebenfalls geteilt; ob das das oid
  eines `config_digital_out` oder eines eigenen Objekts ist, bleibt als ANNAHME
  markiert (hier eigenes oid, ebenfalls geteilt – Antwort-`oid` zählt nur als
  Echo).
- Implementierung: `handle_connect` löst `lookup_query_command(
  "query_gpio_status oid=%c gpio_pin=%c", "gpio_status oid=%c status=%c",
  oid=query_oid)` auf (bevorzugt `try_lookup_query_command`); `query_pin`
  sendet `[query_oid, pin_num]` und gibt `status` (phys-Pegel, `!`-Pins bereits
  hardwareseitig berücksichtigt: `!PB7` logisch 0 → status 1, belegt Log :2261
  vs. :2494) zurück. `pin_num` via `ppins.lookup_pin` (mehrere Rückgabeformen)
  oder `mcu.get_enumeration('pin', …)` / `get_enumerations()['pin']` mit
  Basis+Offset-Logik (`PA0 [0,16]` → `PB9` = 16+9 = 25, Helfer
  `pin_number_from_enum`, ANNAHME Upstream-Muster). Falls der Befehl im
  MCU-Dictionary fehlt (reine Upstream-FW ohne Creality-Query), bleibt
  `_query=None` und `query_pin` fällt auf None zurück (Prüfung übersprungen,
  optional – wie bisher dokumentiert).

### Tests (Review-Vorgabe)

`FakePrinter`/`FakeConfigMcu`/`FakePpins`/`FakeOutputPin`/`FakeToolhead` neu in
`test_k2_motor.py` (Klipper-Lebenszyklus nachgebaut, ohne Klipper/Hardware):

- (a) Config VOR connect: `FakePpins.setup_pin`, `create_oid`,
  `add_config_cmd`, `register_config_callback` werfen NACH `fire_connect()`
  `RuntimeError` wie Klipper; `lookup_query_command` wirft VOR connect.
  Test `REVIEW 1` legt 9 Pins + Query-OID 1 + transparent-OID/Callback vor
  connect an, feuert connect (Callback liefert `config_transparent oid=1`),
  löst Query auf und prüft alle 4 Nach-Connect-Verbote.
- (b) `print_time<=0` ablehnen: `FakeOutputPin.set_digital` wirft bei
  `print_time<=0` (wie Klipper „Timer too close“); Test `REVIEW 2` prüft
  `last_print_time=10,0 (>0)` aus `toolhead.get_last_move_time()`,
  `sync()→wait_moves (1×)`, direkter `set_digital(0, …)`-Bug wird abgelehnt,
  `!PB7` logisch 0 → phys 1.
- (c) `query_gpio_status` simuliert: `FakeGpioQuery.send([oid, pin_num])`
  antwortet `{"oid","status"}` aus den `FakeOutputPin`-Pegeln (nie gesetzte
  Pins: 0); Test `REVIEW 3` prüft `PB9=0`, `!PB7=1`, `PB6=1`, Nummern 25/23
  und Fallback `None` bei `supports_query_gpio=False`.
- Integration gegengeprüft (nicht als eigener Test, manuell): Controller mit
  `KlippyGpio`+`FakePrinter` läuft `ready`, `wait_moves 2×` (P1+Restore),
  Restore-Query überall 0.

Ergebnis 16.10.2026: `ERGEBNIS: ok (9/9 ok)` – 1.–6. wie bisher (§5), 7.
Config-vor-connect, 8. print_time, 9. query+Fallback. Geändert:
`k2_motor_init.py` (`KlippyGpio` neu, `GpioOps.sync`, Controller-`sync()`-
Aufrufe, `PrinterK2Motor`-Frühconfig + `ENABLE_PIN_RAW`/`pin_number_from_enum`),
`k2_transport.py` (nur `KlippyTransparentAdapter`-Docstring: Lebenszyklus
config-vor-connect/lookup-danach), `test_k2_motor.py` (Fakes + 3 Tests).

## 7. Quellen (alle nur gelesen)

- `MOTOR-INIT-BEFUND-2026-10-16.md`, `GPIO-PHASE-BEFUND-2026-10-16.md`
- `nachbau/motor_init.py`, `nachbau/motor.py`, `nachbau/serial485.py`
- `live-logs-2026-10-15/klippy.log` (Boot ab Zeile 2259)
- `config/F008_CR0CN240319C13/motor_control.cfg` (Pins + F008-Params)
