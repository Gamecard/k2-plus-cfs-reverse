# Motor-Init-Befund 16.10.2026: Start-Motorsequenz des Stock-Hosts (K2 Plus, FW 1.1.7.0)

Nur statische Analyse plus Gegenprobe gegen vorhandene Live-Logs. Kein Druckerzugriff, nichts gesendet.
Binary: `rootfs-live/usr/share/klipper/klippy/extras/motor_control_wrapper.cpython-39.so`
(armhf, Cython 0.29.21, SHA256 `d8191b77…b4b6e0f`).

**Quellen:**
- **Hauptanalyse (maßgeblich):** eigener Agent, Ghidra 12.1.4 headless (großer Decompiler-Puffer, damit auch
  `set_motor_pin` mit 25 KB dekompilierbar), eigene Cython-Auflöser für Strings/Konstanten/Tupel und
  Python-Zeilennummern-Annotator. Arbeitsdateien `arbeitsdateien-lokal/cc-init/` (lokal, nicht im Repo):
  `BEFUND-CC.md`, `ptable_fix.json` (Parametertabelle 227 Einträge), `pyx_consts_fix.json` (korrigierte
  Konstanten), `smp.lines`/`vsp.lines` (annotierte Abläufe `set_motor_pin`/`verify_sys_param`).
- **Zweitanalyse:** mit einem weiteren KI-Agenten (llvm-objdump + String-Referenz-Reihenfolge);
  Arbeitsdateien lokal, nicht im Repo.
  Abgleich in §8.
- **Live-Gegenprobe:** `live-logs-2026-10-15/klippy.log`, Boot 16.10. 01:15 Log-Uhr. **Alle Zeilenangaben
  `klippy.log:NNNN` bzw. `:NNNN` in dieser Datei beziehen sich auf dieses Log – lokal, nicht im Repo.**

**Adressen** = ELF-vaddr wie bei `llvm-objdump` (Ghidra/REA: +0x10000). **`Lnnnn`** = Python-Zeile aus den
`__pyx_lineno`-Konstanten. Einstufung: **B** = belegt (Code bzw. Code + Log), **V** = vermutet.

---

## 1. Einstieg

| Funktion | Adresse | Rolle | |
|---|---|---|---|
| `Motor_Control.__init__` | `0xcaea4` | liest `motor_closed_loop`, `switch` (Pflicht), `overcurrent_switch` (0), `retries` (4), `cut_pos_offset` (0.4); `motor_params_init = False`, `is_ready = True`. Bei `switch == 1`: `motor_pin_init()`, `motor_init(config)`, `is_ready = False`. Registriert G-Codes und Webhook-Endpunkte (u. a. `motor_dev_uuid`) | B |
| `_build_config` | `0xa04fc` | `nozzle_mcu.add_config_cmd("config_transparent oid=%d")` | B |
| `motor_pin_init`, `motor_x/y/z/z1/e_init` | `0x1dbe8`, `0x73648` … `0x707ec` | lesen `motor_<m>_dir/_step/_stall`, richten Dir/Step als `digital_out` an der Main-MCU ein | B |
| `motor_init` | `0xacef0` (83 KB) | baut je Motor die Tabelle mit **227** `MotorFlashParam`-Einträgen; Closure `init_system_params` (`0xabb9c`) liest die Config-Overrides | B |
| **`set_motor_pin`** | **`0xf87d8`** (25 KB, L3085–3198) | **die gesamte Start-Motorsequenz** | B |
| `verify_sys_param` | `0xc163c` | Sys-Param-Abgleich (Cmd 0x06), von `set_motor_pin` aufgerufen; auch G-Code `MOTOR_VERIFY_SYS_PARAM` | B |

**Aufrufer von `set_motor_pin`:** nicht im Modul registriert (kein `klippy:connect`-String, keine Referenz aus
anderen `.so`). Alle Log-Zeilen der Sequenz tragen den Frame-Tag `[klippy:_connect:258]`; Cython erzeugt keine
eigenen Frames. Daraus: Creality's `klippy.py` ruft `set_motor_pin()` direkt in `_connect()` nach dem MCU-Connect
auf (**V, stark**; `klippy.py` liegt nicht im Image). Passend: `set_motor_pin` richtet als Erstes
`cmd_transparent_send` ein, was erst nach dem Connect geht (**B**).

Ein zweiter Param-Pfad (`motor_config_params_init`/`check_and_retry` mit key803,
`motor_(extruder_)sys_param_init`) hat keine Xrefs und keinen G-Code; seine Log-Strings (`use_time`,
`update:%s success`) fehlen im Live-Log → läuft beim Boot **nicht** (**B**, Code + Log).

## 2. Ablauf `set_motor_pin` (Pseudocode)

```python
def set_motor_pin(self):                                     # 0xf87d8
    # L3089: transparent-Kanal zur Düsen-MCU (Extruder-Motor)
    self.cmd_transparent_send = self.nozzle_mcu.lookup_query_command(
        'transparent_send oid=%c write=%*s timeout_ms=%u',
        'transparent_response oid=%c read=%*s', oid=self.oid, cq=None, extruder_transparent=True)
    cnt = 1                                                   # L3095
    self.toolhead = self.printer.lookup_object('toolhead')
    self.set_motor_pin_dir()        # Dir/Step = Config-Pegel (Strapping, §5), per Lookahead-Callback
    run_script('M400')                                        # L3096
    self.check_motor_pin_io()       # L3098: GPIO zurücklesen, Resend, sonst key799
    respond_info('initial set_pin_io done')                   # L3099
    num_by_name = {'x':1, 'y':2, 'z':3, 'z1':4, 'e':1}        # L3102 (e=1: Adresse 0x81 auf dem Düsenbus)
    if self.switch == 0:                                      # L3109
        self.set_motor_pin_normal(); pause(0.1)
        respond_info('switch=0 set_pin_io 0'); run_script('M400')   # L3112-3115 (danach Ende: V)
    pause(0.2)                                                # L3118
    while cnt <= self.retries:                                # L3119 (retries=4 → max. 4 Runden)
        try:
            self.motor_set485_addr(MOTOR_SET_ADDR NUM=9)            # ttyS5: FF 0E [01], keine Antwort (~50 ms)
            self.motor_set485_addr(MOTOR_SET_ADDR NUM=9 EXTRUDER=…) # transparent: FF 0E [01], leer (~2 s)
            respond_info('broadcast retries:%s' % cnt)              # L3126
            pause(0.1)                                              # L3127
            ok = 0
            for m in self.motor_closed_loop:                        # L3130
                pause(0.05)                                         # L3140
                if self.motor_read485_addr(MOTOR_READ_ADDR NUM=num_by_name[m] [EXTRUDER bei 'e']):
                    ok += 1                                         # 0E [02] → [addr]; 'e' transparent 81 0E 02
                # sonst: Motor in Fehlliste (V)
            if ok == len(self.motor_closed_loop):                   # L3146
                self.motor_params_init = True                       # L3148 (gibt stats() frei)
                respond_info('All motors are normal')               # L3149
                self.set_z_stall_mode()                             # L3150: 0x11 [02] an z (0x83), z1 (0x84)
                if 'e' in self.motor_closed_loop:
                    self.set_e_stall_mode()                         # L3152: 0x11 [02] transparent an 0x81
                run_script('MOTOR_CHECK_PROTECTION_AFTER_HOME DATA=11')   # L3154: 0C [0b]
                run_script('MOTOR_CLEAR_ERR_WARN_CODE NUM=0 DATA=5')      # L3155: 0C [05], unbedingt
                break                                               # (V, Fluss/Log)
            pause(1.0)                                              # L3157
            cnt += 1                                                # L3158
        except Exception as e:
            logging.error(e)                                        # L3159-3160
    self.set_motor_pin_normal()                                     # L3163: alle Pins physisch 0
    pause(0.1); run_script('M400')                                  # L3164-3165
    self.check_motor_pin_io_restore()                               # L3166 (key800)
    respond_info('restore set_pin_io done')                         # L3167
    pause(1.0)                                                      # L3168
    try:                                                            # L3170-3175: wirkungslos (V)
        from extras.motor_params_config import objs                 #   Modul fehlt im Image → ImportError
        for m in self.motor_closed_loop: objs.get(m, None)          #   Ergebnis verworfen
    except Exception: pass
    if cnt != self.retries + 1:                                     # L3178: Erfolg
        self.verify_sys_param()                                     # L3189 → §4
        self.is_ready = True                                        # L3190
        self.printer.send_event('auto_addr:start')                  # L3191 (CFS-Auto-Addr startet erst jetzt)
    if cnt == self.retries + 1:                                     # L3192: alle Runden erfolglos
        self.is_ready = False
        self.gcode._respond_error('{"code":"key798", … "values":%s}' % …)   # L3195
        pause(5.0)                                                  # L3196
        self.printer.invoke_shutdown(…); raise self.printer.command_error(…)  # L3197-3198 (Argumente: V)
```

`auto_addr_wrapper` hängt `reg_auto_addr_set` bei `switch == 1` genau an das Event `auto_addr:start`
(Klartextquelle `auto_addr_wrapper.py`:178, lokal, nicht im Repo) → die CFS-Auto-Adressierung läuft erst nach
dem Param-Abgleich (**B**).

**`12 02` (UUID) gehört NICHT zur Init (B, Code + Log).** `set_motor_pin` enthält keinen Bezug auf `dev_uuid`;
die Init sendet je Motor nur `0E [02]`. Die `12 02`-Frames im Boot-Log stammen aus dem Webhook-Pfad:
`klippy.log:2316/2339/2349` (lokal, nicht im Repo) tragen den Tag `[webhooks:_process_request]`, liegen **vor**
„All motors are normal“ (`:2422`) und enden in `get dev_uuid: {…}` (`:2370`). Das ist ein Client-Poll über den
Endpunkt `motor_dev_uuid` (vermutlich das Display) – er teilt sich nur die ttyS5-Queue mit der Init und
wiederholt sich später (`:4315–4373`, verschränkt mit Auto-Addr).

## 3. Bedingungen und Fehlerzweige

| Fall | Verhalten | |
|---|---|---|
| „Normal“ | In **derselben** Broadcast-Runde liefert `motor_read485_addr` für **jeden** Eintrag aus `motor_closed_loop` eine Antwort (truthy, L3134/3142/3146). Die gemeldete Adresse wird nicht mit der erwarteten verglichen (V: kein Vergleich im Pfad) | B |
| Runden | höchstens `retries` Runden (Zähler ab 1, `cnt <= retries`); live 2 Runden (Runde 1: nur Z antwortet, Runde 2: alle; `:2314` / `:2386` / `:2422`) | B |
| Einzelanfrage | `retries=0`; Timeout 0,05 s für `0E 02` (Konstante in `motor_read485_addr` `0x615a0`), transparent ≈ 2 s (`TIMEOUT_LONG_TIME_MS`) | B |
| **key798** | „Motor connection failed, exceeding maximum retry count“: `is_ready=False`, 5 s Pause, `invoke_shutdown` → Klipper-SHUTDOWN. Stall- und Param-Phase entfallen (`verify_sys_param` hängt an `cnt != retries+1`). Die GPIO-Wiederherstellung (L3163–3167) läuft **auch im Fehlerfall** | B (Shutdown-Argumente V) |
| **key799** | `check_motor_pin_io` (`0xef308`): jeder Dir/Step-Pin per `query_gpio_status oid=%c gpio_pin=%c` an der Main-MCU zurückgelesen (bei `!`-Pins invertiert verglichen); bei Abweichung `resend_set_io` 3 Versuche (je Lookahead-Set, M400, 0,2 s), dann key799 + 5 s (`0xee804`, `0xee9f4`) | B |
| **key800** | dasselbe beim Restore (`check_motor_pin_io_restore`) | B |
| **key801 / key802** | `set_z_stall_mode` (`0xf3f70`) / `set_e_stall_mode` (`0x2fcf4`): je bis 3 Versuche, 0,05 s Pause; danach key801 (z/z1, + 5 s) bzw. key802 (e) | B |
| **key803** | nur im nicht genutzten zweiten Param-Pfad (§1) | B |
| X/Y Stall-Mode | beim Boot **nicht** gesetzt (`set_z_stall_mode` fragt nur z/z1 ab, L2889/2891), erst beim Homing | B |
| Schutzabfrage | `motor_check_protection_after_home DATA=11` (`0x59c70`): `0C [0b]` an jeden Motor aus `motor_closed_loop` (E transparent), Bits → `motor_error_code`/`motor_warning_code`. Nur bei Fehlern (V): `FORCE_MOVE STEPPER=stepper_y DISTANCE=0.01 VELOCITY=5`, `M84`, `MOTOR_CLEAR_ERR_WARN_CODE NUM=0 DATA=5` (L1847–1855). Danach sendet `set_motor_pin` `0C [05]` **unbedingt** (L3155) | B |

## 4. Config-Steuerung und Param-Abgleich (Cmd 0x06)

| Option `[motor_control]` | Wirkung | |
|---|---|---|
| `switch` (Pflicht) | 1: Pins + Tabellen, volle Sequenz, `auto_addr` wartet auf `auto_addr:start`. 0: nur Pins normal (V: danach Ende) | B |
| `motor_closed_loop` | Motorliste für Tabellenbau, Adress-Runden, Schutzabfrage, Stall z/z1/e | B |
| `retries` (4) | maximale Zahl der Broadcast-Runden (nicht die Einzel-Retries, die sind 0) | B |
| `motor_<m>_dir` / `_step` = `PIN,Pegel` | Pegel für die Strapping-Phase (§5) | B |
| `motor_<m>_stall` | Stall-Pin; steuert außerdem, ob `verify_sys_param` den Motor bearbeitet (`getattr(self,'motor_%s_stall')` truthy, L851) | B |
| `overcurrent_switch` (0) | `stats()`-Stromlogging, nur zusammen mit `motor_params_init` | B |
| `cut_pos_offset` | nur Cut-Positions-Korrektur, kein Boot-Einfluss | B |
| **`<motor>_<param>`** (z. B. `x_controller_pos_loop_pid_param_kp`) | Override für den Tabelleneintrag `param` des Motors | B |

**Override-Logik** (`init_system_params` `0xabb9c`, Schlüssel `stepper + '_' + key`):

```python
cv = config.get(f"{stepper}_{key}", None)
if cv is None and p.value_id not in (9, 10, 25):
    cv = p.value                       # override bleibt False
else:
    p.override = True                  # in der Config ODER ID 9/10/25
p.value = eval(str(cv), …)             # bei 9/10/25 ohne Config: None
```

**`verify_sys_param`** (`0xc163c`), je Motor `num` aus `MOTOR_NAME_TO_NUM = {'x':1,'y':2,'z':3,'z1':4,'e':5}`
(Modul-Init `0x11760–0x117e8`), sofern `motor_<m>_stall` gesetzt (K2 Plus: alle fünf):

```python
for key, p in motor_params_table[MOTOR_NUM_TO_NAME[num]].items():   # 227 IDs in ID-Reihenfolge
    if not p.configurable: log(" is not configurable"); continue     # 0xc2524
    if not p.override:     log(" is not need override"); continue    # 0xc2a60
    for _ in range(3):                                                # lesen, ≤ 3 Versuche
        r = _motor_sys_param(num, data=2, id_byte=id, id_params_type=p.value_type)   # 06 [02,id]
        if r: break
    if r is None / leere Bytes: warning(" result is None" …); continue
    if p.value_id in (9, 10, 25): log(" read success"); continue     # nur lesen, nie schreiben
    if not math.isclose(read, p.value, abs_tol=p.tolerance):          # 0xc4398 / 0xc4410
        _param_modifi_data ← {stepper, key, precision, value_id, original_value=read, modifi_value=p.value}
        log(" is not close to configured value:")
        for _ in range(3):                                            # schreiben, ≤ 3 Versuche
            r = _motor_sys_param(num, data=1, id_byte=id, …, params=p.value)          # 06 [01,id]+<wert>
            if r: break
        Echo prüfen → " write success" / " write failed"
log("Updata param"); _motor_sys_param(num, data=3)                    # 06 [03] einmal je Motor (Ergebnis ungeprüft: V)
```

- **Toleranz (B):** Kein Tabelleneintrag übergibt `tolerance` → für alle gilt der `__init__`-Default
  **`abs_tol = 1e-6`** (Double `0x1b380` = 1e-06), dazu Python-Default **`rel_tol = 1e-9`**. Live steht es direkt
  im Log: `precision:1e-06` (`klippy.log:2519`). Float32-Rundung (0.028 → 0.02800000086) bleibt darunter.
- **Welche IDs angefasst werden (B):** nur `configurable=True` **∩ in der Config gesetzt**, dazu **immer 9/10/25**
  (`param_elec_offset`, `param_phase_order_invert`, `param_elec_offset_err_deg`, nur gelesen). 61 der 227 IDs sind
  `configurable`: 9, 10, 12–14, 20, 21, 23, 25, 41–43, 66, 67, 70, 86, 87, 89, 94, 95, 97, 102, 103, 105, 110, 111,
  113, 116, 118–123, 127, 128, 130, 141–145, 173, 179–183, 187–190, 200, 204, 217, 218, 220–223, 226.
  Konfigurierte, aber nicht `configurable` Schlüssel werden still ignoriert.
- **K2-Plus-Config (F008):** je Achse x/y/z/z1 die IDs 70, 86, 87, 102, 103, 116, 118–121 (Pos-/Spd-/Cur-Loop,
  LESO) und 187/188 (`protection_param_prt_track_max_err/_err_time`), bei x/y zusätzlich 226
  (`stall_pos_err_rad_for_slicer`). **ID 226** ist ein reiner Host-Parameter, der Motor antwortet mit 0 Byte
  (live). Für `e` steht nichts in der Config → nur 9/10/25.
- **Live (Boot 16.10.):** 11 Schreibvorgänge, alle „write success“, z. B. ID 70 300.0 → 100 (X, Z, Z1), ID 188
  0,01 → 0,1 (alle), ID 86/87 0,028/6,0 → 0,035/2,0 (Z, Z1). Werte sind **flüchtig** (live 15.10. bestätigt) –
  deshalb schreibt Stock bei jedem Boot erneut.
- `MOTOR_FLASH_PARAM` (Cmd 0x07) wird beim Boot **nicht** verwendet; die Log-Bezeichnung „update_flash_param“
  meint Cmd 0x06 (**B**). Log-Falle: `value:N` in `update_flash_param key:… value:N` ist die ID.

## 5. GPIO vor/nach der Init: Adress-Strapping — BELEGT

**Pins** (Config `config/F008_CR0CN240319C13/motor_control.cfg`, abgeglichen mit der Zweitanalyse – identisch):

| Motor | DIR (Config) | STEP (Config) | physisch DIR/STEP | Stall | Adresse |
|---|---|---|---|---|---|
| X | `PB9,0` | `PB10,0` | 0 / 0 | PB11 | **0x81** |
| Y | `!PB7,0` | `PB8,0` | **1** / 0 (invertiert, live `status 1`) | PB12 | **0x82** |
| Z | `PB5,0` | `PB6,1` | 0 / 1 | PB13 | **0x83** |
| Z1 | `PA1,1` | `PB15,1` | 1 / 1 | PA10 | **0x84** |
| E | – | – | – | `nozzle_mcu:PB12` | 0x81 auf dem Düsenbus (transparent) |

Dazu **Enable `!PA9`** (fest im Modul, `enable_pin_pa9` @`0xce888`, nicht in der Config; Fundstelle aus der
Zweitanalyse, `force_stop` setzt es @`0xd3418`). Alle Dir/Step/Stall-Pins sind GPIOs der **Main-MCU**.

- **Vorher (B):** `set_motor_pin_dir` setzt per Lambda `set_digital(pt, int(info[1]))` die Config-Pegel
  (`0x23b64…`), `check_motor_pin_io` prüft sie.
- **Motor-FW 0x0E Sub 01** (Broadcast `FF 0E 01`; Handler `0x107b4` → `0x1087c/0x10802–0x10828` in
  `mot2_002_081`, s. `MOTOR-FW-BEFUND-2026-10-16.md`): liest die GPIO-Eingänge `pins+0x108` und `pins+0x110`
  (= DIR, vgl. MOTOR_GET idx 12, live bestätigt), rechnet `addr = ((in108 << 1) | in110) − 0x7f` und speichert das
  Byte in `obj+0x1c`:

  **Adresse = 0x81 + 2·pin108 + pin110** (pin110 = DIR, pin108 = STEP; −0x7f als Byte = +0x81).
  Mit den physischen Pegeln als (DIR, STEP): X(0,0) = 0x81, Y(1,0) = 0x82, Z(0,1) = 0x83, Z1(1,1) = 0x84.
  Das entspricht exakt der beobachteten Zuordnung (**B**). Dass `pin108` STEP ist, ist **V**, aber dadurch
  gestützt, dass alle vier Adressen aufgehen. Sub 02 liefert die Adresse zurück (`0x10860`).
- **Nachher (B):** `set_motor_pin_normal` setzt alle Dir/Step-Pins **physisch auf 0** (logisch 0, bei `!`-Pins
  mit Config-Pegel 0 logisch 1; `startswith('!') and int(info[1]) == 0`, `0x316a8`), `check_motor_pin_io_restore`
  prüft nach. Zweck (V): Leitungen sind danach wieder normale STEP/DIR-Eingänge für den Druckbetrieb.
- Der Extruder hat keine Dir/Step-Pins, nur `motor_e_stall`; er meldet 0x81 auf seinem eigenen Düsenbus (**B**, Log).

## 6. Zeiten

| Stelle | Wert | Beleg |
|---|---|---|
| nach `switch=0`-Normalisierung, nach Restore-`set_motor_pin_normal` | **0,1 s** | `0xf9230`, `0xfb0a8` |
| zwischen „initial … done“ und erstem Broadcast | **0,2 s** | `0xf9724`; live 01:15:40,0 → Broadcast |
| nach Broadcast vor den Adress-Abfragen | **0,1 s** | `0xfa4a8` |
| vor jeder `0E 02`-Abfrage; Stall-Mode-Retry-Pause | **0,05 s** | `0xfbd0c` |
| zwischen erfolglosen Runden | **1,0 s** | `0xfc3b8`; live ≈ 1,0 s |
| nach „restore … done“ bis `update_flash_param stepper:1` | **1,0 s** | `0xfb5cc`; live `:2500` 01:15:50,608 → `:2501` 01:15:51,613 |
| vor Shutdown bei key798 / nach key799/800/801 | **5 s** | `0xfde94`, `0xee9f4` |
| Timeout `0E 02` / transparent | 0,05 s / ≈ 2 s | `0x615a0`, `TIMEOUT_LONG_TIME_MS` |
| GPIO-Resend | 0,2 s je Versuch, 3 Versuche | `resend_set_io` |

Die Zeitkonstanten stammen aus der **korrigierten** Konstantentabelle (§7). Die alte Auflösung hätte bei `0xfb5cc`
0,8 s und bei `0xfde94` 3,8 s ergeben – beides falsch und vom Log widerlegt (Restore → stepper:1 = 1,005 s).

## 7. Nebenbefunde

- **Stock-Bug Extruder-Param-Abgleich (B, Code + Log):** Adresse, Stall und Schutz laufen für E korrekt über
  transparent (`EXTRUDER`-Parameter, 0x81 auf dem Düsenbus). `verify_sys_param` dagegen nutzt `_motor_sys_param`
  **ohne** transparent-Zweig und adressiert E als `num=5` → **0x85 auf ttyS5**, wo niemand antwortet. Folge: jede
  E-ID (9/10/25) läuft in Timeouts, live ≈ 9 s ohne Wirkung (`klippy.log:4025` stepper:5 01:15:54,3 → `:4301`
  „Updata param“ 01:16:03,7). Der korrekte transparent-Pfad `motor_extruder_sys_param_init` existiert, wird aber
  nicht aufgerufen. Kein bewusstes Überspringen, sondern ein Fehlgriff.
- **Falsch aufgelöste Konstanten der Vorarbeit:** Der Auflöser hinter `arbeitsdateien-lokal/rea-motor/pyx_consts.json`
  (lokal, nicht im Repo) übersah `vmov.f64 d0,#imm`; 13 Float-Konstanten waren falsch. Korrigiert
  (`pyx_consts_fix.json`):

  | Adresse | vorher | richtig |
  |---|---|---|
  | `0x126024` | 0.4 | 0.5 |
  | `0x126034` | 0.8 | **1.0** |
  | `0x12603c` | 1.3 | 1.5 |
  | `0x126044` | 1.8 | 2.0 |
  | `0x126048` | 1.8 | 2.5 |
  | `0x126054` | 3.8 | 4.0 |
  | `0x126058` | 3.8 | **5.0** |
  | `0x12605c` | 3.8 | 5.5 |
  | `0x126060` | 3.8 | 6.0 |
  | `0x126074` | 0.15 | 0.25 |
  | `0x12607c` | 0.7 | 15.0 |
  | `0x126088` | 1e-06 | 20.0 |
  | `0x1260e0` | 0.00203718 | 1.0 |

  Gegenprüfung: Weder `REA-MOTOR-BEFUND-2026-10-16.md` noch `MOTOR-FW-BEFUND-2026-10-16.md` stützen eine Aussage auf
  diese 13 Werte (dort verwendet: nur int-Konstanten, z. B. 1/12 in `motor_get`, int 8 @`0x1260f0` – unverändert).
  Beide Dateien tragen dazu einen Prüfvermerk. Betroffen war nur die Zweitanalyse (0,8 s / 3,8 s, §8).
- `"All motors are normal and init params done."` (`0x1288d4`) ist ein toter String ohne Code-Referenz.

## 8. Vergleich der zwei Analysen

| Punkt | Hauptanalyse (Ghidra) | Zweitanalyse (llvm-objdump) | Bewertung |
|---|---|---|---|
| Kernfunktion `set_motor_pin` @`0xf87d8`, Reihenfolge GPIO → Broadcast → `0E 02` → Stall → `0C 0b/05` → Restore → `verify_sys_param` → `auto_addr:start` | ja | ja | **übereinstimmend** |
| key798–802, `switch=0`-Zweig, X/Y ohne Boot-Stall, E über transparent, `verify_sys_param`-Filter (configurable/override, IDs 9/10/25, `isclose`) | ja | ja | **übereinstimmend** |
| Aufrufer | `klippy._connect` (Frame-Tag im Log) | „vermutlich `klippy:ready`“ | Hauptanalyse durch Log-Tag gestützt |
| **`12 02` UUID** | nicht Teil der Init | „läuft mit in der `MOTOR_READ_ADDR`-Runde“, Schritt 8 „UUID-Nachlese“ | **Zweitanalyse falsch** – Log-Tag `[webhooks:_process_request]` (`klippy.log:2316/2339/2349`, vor `:2422`); kein `dev_uuid`-Bezug in `set_motor_pin`. Der vermeintliche zweite `MOTOR_READ_ADDR`-Block (`0xfb99c/0xfbee0`) ist der Schleifenkörper derselben Runde (Python-Zeilen 3133/3141 zeigen es; Cython legt ihn hinter den Restore-Block) |
| Schritt 8 `motor_params_config` | – | Nachlese-Runde | tatsächlich wirkungsloser `import` (Modul fehlt im Image) |
| **Toleranz** | Default `abs_tol = 1e-6` + `rel_tol = 1e-9`, kein Eintrag setzt eigene; live `precision:1e-06` | „`isclose` mit `tolerance`/`precision`“, ohne Wert | Hauptanalyse konkret und live bestätigt |
| **Strapping** | **belegt** über Motor-FW 0x0E Sub 01 (Formel, Adresse in `obj+0x1c`) | „vermutet, stark gestützt, kein FW-Beleg“ (nur Host-Seite) | Hauptanalyse tiefer (FW-Handler) |
| Pausen | 0,1/0,2/0,05/1,0/5,0 s | 0,8 s (`0xfb5cc`), 3,8 s (`0xfde94`) | Zweitanalyse nutzte die falsch aufgelösten Konstanten (§7) |
| Motornummern in `set_motor_pin` | `{x:1,y:2,z:3,z1:4,e:1}` (L3102, genutzt) | `x=1/y=2/z1=3/z2=4/e=5` | beide Dicts stehen im Code (L3101 Strings, L3102 Ints); genutzt wird L3102 |
| Extruder-Param-Abgleich | Stock-Bug 0x85 auf ttyS5, transparent-Pfad ungenutzt | nur „0x85 antwortet nie“ | Hauptanalyse benennt Ursache im Code |
| Pin-Liste inkl. Stall + `!PA9` | Dir/Step, E-Stall | Dir/Step/Stall vollständig, `!PA9` | **übereinstimmend**; `!PA9` nur in der Zweitanalyse |

Fazit: Die Zweitanalyse bestätigt Grundablauf, Fehlerkeys und Filterlogik unabhängig; bei UUID, Pausen und
Strapping-Tiefe gilt die Hauptanalyse.

## 9. Folgen für den Pi-Ersatz (Vorgaben für ein späteres `motor_init.py`)

1. **Pins vor dem Broadcast setzen:** Dir/Step-Pegel laut Tabelle §5 über die Main-MCU (Klipper-GPIO) setzen und
   zurücklesen, **dann** `FF 0E 01` (ttyS5) + transparent `FF 0E 01`, 0,1 s warten, je Motor `0E 02` (0,05 s Abstand).
   Erst wenn alle aus `motor_closed_loop` in einer Runde antworten: Stall-Mode `0x11 [02]` an Z/Z1/E,
   `0C [0b]`, `0C [05]`. Danach alle Pins physisch 0 und prüfen. Bis zu 4 Runden mit 1,0 s Pause, sonst Abbruch
   (Stock: key798 + SHUTDOWN).
2. **Param-Abgleich nach jedem Kaltstart:** Werte sind flüchtig. Je Motor nur `configurable ∩ konfiguriert` plus
   9/10/25 lesend; lesen `06 [02,id]` (≤ 3 Versuche), vergleichen mit `isclose(abs_tol=1e-6)`, bei Abweichung
   `06 [01,id]+<wert>` (≤ 3 Versuche, Echo prüfen), am Ende `06 [03]` je Motor. ID 226 nicht senden (Host-Parameter).
3. **Extruder über transparent:** E nie als 0x85 auf ttyS5 ansprechen, sondern über die Düsen-MCU
   (`transparent_send`, Adresse 0x81) – auch beim Param-Abgleich (Stock-Bug nicht nachbauen; Vorbild ist der
   ungenutzte `motor_extruder_sys_param_init`).
4. **`auto_addr` erst nach `is_ready`:** Die CFS-Auto-Adressierung startet beim Stock erst mit `auto_addr:start`
   nach erfolgreichem Param-Abgleich – ein Ersatz sollte dieselbe Reihenfolge einhalten (gemeinsame ttyS5-Queue).
5. **UUID-Poll ist optional:** `12 02` gehört nicht zur Init; nur nachbauen, wenn ein Client (Display) den
   Endpunkt `motor_dev_uuid` braucht.
6. **Offen vor dem ersten Live-Lauf:** Trockenlauf von `motor_init.py` gegen FakeBus und die Live-Log-Sequenz;
   Pegel vor dem Broadcast gegen `MOTOR_READ_ALL_PIN_IO` prüfen (Stock tut das per `query_gpio_status`);
   transparent-Pfad für den Extruder-Param-Abgleich nachbauen.
