# FLUSH-SONDERFÄLLE-BEFUND – Restmaterial, Stromausfall, Spülschacht-Grenzen, Heizwarten (statisch, 17.10.2026)

> Nur statische Analyse. Kein Zugriff auf Drucker oder Pi, kein Code in `pi-host/` geändert.
> Ergänzt `FLUSH-BEFUND-2026-10-17.md` (Normalfall Farbwechsel) und schließt dessen offene Punkte aus §9.
> Arbeitsdateien liegen lokal (nicht im Repo).

**Ziel-Binary:** `rootfs-live/usr/share/klipper/klippy/extras/box_wrapper.cpython-39.so` (FW 1.1.7.0, Cython 0.29.21, armhf).
Dazu im Klartext: `rootfs-live/usr/share/klipper/klippy/extras/gcode_move.py` (GM), `config/F008/gcode_macro.cfg`, `config/F008_CR0CN240319C13/box.cfg`.

**Belegstärken** (wie FLUSH-BEFUND)
- **B** = im Disassembly/Dekompilat belegt (Adresse und/oder Python-Zeile)
- **B+L** = zusätzlich Live-Log
- **V** = Folgerung/Deutung
- **ANNAHME** = nicht belegt

**Konventionen**
- `Z.<n>` = Python-Zeile aus `__pyx_lineno` (Fehlerpfade) bzw. `co_firstlineno` (PyCode_New im Modul-Init, für `def`-Zeilen). Einzelne Anweisungen ±1–2.
- `@0x…` = ELF-vaddr (`llvm-objdump`, eigenes Ghidra-Projekt mit Imagebase 0). Im REA-Standardimport **+0x10000**.
- `True/False/None` als Argument oder Vergleichswert: aus dem GOT-Eintrag gelesen (`_Py_TrueStruct` 0x234f9c, `_Py_FalseStruct` 0x234f84, `_Py_NoneStruct` 0x234f98).
- Floats nur aus der Disassembly (`vmov.f64 #imm` bzw. Literal), nicht aus der alten Konstantentabelle (REA-MOTOR-Lehre).

---

## Kurzfassung

| Funktion | Auslöser | Was sie wirklich tut | Beleg |
|---|---|---|---|
| `set_flushing_sign` / `reset_flushing_sign` | Schachtfahrten, Schneiden, `M8200 P`/`M8200 O` | **nur** `M400` + `SET_LIMITS` + `M400` bzw. `M400` + `RESTORE_LIMITS` + `M400`. **Keine Datei.** | B |
| `has_flushing_sign` | – (kein Aufrufer im Modul) | Datei `creality/userdata/config/flushing_sign` vorhanden → löschen, `True`; sonst `False`. Wer die Datei anlegt: nicht im Modul | B / ANNAHME |
| `is_use_ending_material_flush` | nächster Wechsel (`Tn_action`), `M8200 P` (`CR_BOX_PRE_OPT`), Druckende (`BOX_END`) – nur wenn das Flag `is_use_ending_material` gesetzt ist | **Kein Druckende-Spülen.** „ending material“ = Spule im CFS leer gelaufen, Rest steckt im Schlauch. Kopfsensor aus, dann `extrusion_all_materials`: Rest in **80-mm-Stücken** durch die Düse, je Stück Blasen + Blech ohne Kreise, bis der Kopfsensor leer meldet **plus ein Stück**, höchstens `max_tube_length` (3000 mm). Danach `set_cool_temp`, Box IDLE, `last_cmd = None` | B |
| `flush_material` (MCMBW) | Stromausfall-Fortsetzung: CFS aus (GM:637-639) oder gleicher Slot (`power_loss_same_material_process`) | **Fest 160 mm** (`Tn_extrude` 140 → `[90, 70]`), Geschwindigkeit `max_vol/2.4·60·0.8` aus dem gespeicherten Düsenmaterial (Default 300 mm/min), keine Temperaturlogik, keine Messradprüfung, kein `remain_material.save` | B |
| `power_loss_clean` | `BOX_END_PRINT` (aus `END_PRINT`) | **Kein Spülen.** Löscht `enable`/`tnn_map`/`last_cmd` aus der Box-Speicherdatei `tn_save_data_path` | B |
| `power_loss_restore` | `BOX_POWER_LOSS_RESTORE` (GM:636) | **Kein Spülen.** Liest dieselbe Datei zurück: `BOX_ENABLE_CFS_PRINT ENABLE=<0/1>`, `Tnn_map`, `box_save.last_cmd` | B |
| `quickly_wait_heating(temp)` | Spülstart, Max-Temp-Stufe, Restmaterial, Druckende | `M104`, dann alle 0,5 s: Fenster **±10 K** um das Ziel **und** `can_extrude` → `G91 / G0 E0.01 F60 / G90` und fertig. **Kein Timeout**, Abbruch nur bei Shutdown | B |
| `Tn_Extrude` Mehrfach-Percent | nur `BOX_MATERIAL_FLUSH PERCENT=a,b,…` bzw. `BOX_TN_EXTRUDE` mit `Tn_extrude_percent` > 1 Eintrag | Teilstücke `extrude·percent[i]` mit `velocity[i]`; mit TNN zusätzlich Messradprüfung, sobald ≥ 2·`buffer_empty_len` gefördert; am Ende einmal Blasen + volles Wischen + Schacht. Im Stock-Ablauf **nicht** genutzt | B |

**Wichtigste Folgerung für den Pi (V):** Spülen am Druckende gibt es bei Stock **nicht**. Stock **entlädt** am Druckende (Schneiden + Rückzug in die Box, `box_end`). Der Restmaterial-Weg ist ein Runout-Sonderfall: Ist die Spule leer, aber der Rest noch im Schlauch, wird er beim nächsten Wechsel oder am Druckende **ausgespült statt zurückgezogen**. k2_box kennt beides nicht (§8).

---

## 1. Spülschacht-Kennung `flushing_sign`

### 1.1 Rümpfe (B)

```
BoxAction.set_flushing_sign(self)            # def Z.1276 (co_firstlineno 0x4fc), @0x198bdc
1277  self.gcode.run_script_from_command("M400")
1278  self.gcode.run_script_from_command("SET_LIMITS")
1279  self.gcode.run_script_from_command("M400")

BoxAction.reset_flushing_sign(self)          # def Z.1281 (0x501), @0x198850
1282  M400 ; 1283  RESTORE_LIMITS ; 1284  M400

BoxAction.has_flushing_sign(self)            # def Z.1269 (0x4f5), @0x198f68
1270  if os.path.exists(self.flushing_sign):
1271      os.remove(self.flushing_sign)
          return True                        # GOT True @0x199344
      return False                           # GOT False @0x199348
```

- Die Zeilen 1277/1278/1279 stammen aus den Fehlerpfaden: `movw r7,#0x4fd/#0x4fe/#0x4ff` (@0x198e90, @0x198ee8, @0x198f08), je hinter `getattr(gcode)`, `run_script_from_command` und dem Aufruf.
- **Korrektur SDCARD-BEFUND C.1:** Dort steht „Z.1278 `M400`, Z.1279 `SET_LIMITS`“. Richtig ist 1277 `M400`, 1278 `SET_LIMITS`, **dazu ein drittes `M400` in Z.1279**. Für `reset` analog (1282–1284, `movw r7,#0x502–0x504`).
- `self.flushing_sign` ist der Pfad aus `BoxAction.__init__` (String `creality/userdata/config/flushing_sign`, Z.1217 @0x19b22c). `has_flushing_sign` liest das **Attribut** (`getattr(self,'flushing_sign')` @0x199004, r0 = self).
- `MultiColorMeterialBoxWrapper.has_flushing_sign` (def Z.4184, @0xc3e18) reicht nur `box_action.has_flushing_sign()` durch.

### 1.2 Wer setzt und wer löscht (B, Zeilen ±1)

| Aufrufer | set | reset |
|---|---|---|
| `BoxAction.move_to_cut` | Z.2582 | Z.2620 |
| `BoxAction.go_to_extrude_pos` | Z.2714 | – |
| `BoxAction.nozzle_clean(circle=True)` | Z.3154 (nur mit Kreisen) | Z.3182 (nur mit Kreisen) |
| `BoxAction.move_to_safe_pos` | – | Z.3193 |
| `cmd_CR_BOX_PRE_OPT` (`M8200 P`) | Z.5761 | – |
| `cmd_CR_BOX_END_OPT` (`M8200 O`) | – | Z.5856 |

### 1.3 Folgen

- **Die FLUSH-BEFUND-Annahme in §7.2 ist widerlegt:** „Markerdatei zeigt an, dass der Kopf im Schacht ist, und dient der Stromausfall-Reinigung“. `set_flushing_sign` schreibt **keine** Datei. Im ganzen Modul gibt es kein `open(…,'w')` auf diesen Pfad. Der String kommt nur in `__init__` und `has_flushing_sign` vor (B, Volltext-Annotation des `.text`).
- `has_flushing_sign` hat **keinen Aufrufer** im Modul (B). Im Projekt-rootfs-Auszug enthält keine andere Datei den String (B, Suche über `rootfs-live` und den OTA-1.1.3.13-Auszug). Der Auszug ist aber unvollständig.
  - **ANNAHME:** Ein anderer Creality-Prozess (Oberfläche/Server) legt die Datei an oder fragt sie ab, oder es ist Altcode.
- „flushing_sign“ heißt also praktisch: **„X/Y-Grenzen für Schacht und Schneider auf den Config-Bereich weiten“** (`SET_LIMITS` = `position_min/max` aus `[stepper_x/y]`, GM:96-101, SDCARD-BEFUND C.1).
- `power_loss_clean` hat mit `flushing_sign` nichts zu tun (§4.2).

---

## 2. Restmaterial („ending material“)

### 2.1 Flag `is_use_ending_material` (B, Bedingungen teils V)

| Stelle | Zeile | Wirkung |
|---|---|---|
| `BoxAction.__init__` | Z.1207 | Startwert (V: `False`) |
| `BoxAction.send_data` (Box-Ereignis Filamentfehler) | Z.2145–2155 | Bei `print_stats.state` `printing`/`paused` und noch nicht gesetztem Flag: **`is_use_ending_material = True`** (GOT True @0x168574, SetAttr @0x168598), dann `recode_err('filament_err', …)`. Daneben stehen `get_Tn_data(…,'auto_refill')` (Z.2150) und `box_filament_err` (Z.2146/2147, V: Box-Einlauf leer). |
| `BoxAction.check_material_refill` (`BOX_CHECK_MATERIAL_REFILL`) | Z.1765–1780 | Bei `box_save.error == 'filament_err'` und `box_filament_err`: Ist das Flag gesetzt, folgen `SET_FILAMENT_SENSOR … ENABLE=0` und `register_callback(material_auto_refill)` (Z.1776). Sonst „no auto refill“ (Z.1780). |
| `is_use_ending_material_flush` | Z.3901 | Flag = `False` nach Erfolg |
| `use_ending_material_flag_clear` | Z.3336–3341 | Flag = `False`. Bei `box_save.error == 'filament_err'` zusätzlich `clear_err()`. **Kein Aufrufer im Modul** (B) |

**Deutung (V):** Die Spule im CFS ist leer (Box-Einlaufsensor). Der Druck läuft mit dem Rest weiter, der zwischen Box und Kopf im Schlauch liegt. Dieser Rest hat kein Ende mehr in der Box und lässt sich deshalb nicht sinnvoll zurückziehen.

### 2.2 `BoxAction.is_use_ending_material_flush(self, **kw)` @0xe3790, Z.3880–3905

Der Wrapper @0xe56bc nennt nur `self`. Das zweite C-Argument (r1 → r7) ist das kwargs-Dict, das unverändert an `recode_err` geht (@0xe3c1c, `PyObject_Call(recode_err, (), kw)`).

```
3882  logging.info("is use ending material")
3883  gcode: "SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=0"
3884  gcode: "M400"
3885  ret = self.extrusion_all_materials()                       → §2.3
3886  if ret is False:                                           (GOT False @0xe3a6c, cmp r11,r8)
3887      self.box_save.clear_err();  3888 self.box_state.clear_e_err()
3890      self.box_save.recode_err(**kw);  return False          (r8 = False)
3892  Tnn_content = box_state.get_Tnn_content(box_save.last_cmd)
3893  material = communication_get_filament_sensor_state(addr,'MATERIAL') →
           get_Tn_inner_data(addr,'filament_sensor', subpart='material')
3894  num = {'A':0,'B':1,'C':2,'D':3}[…]
3895  if material & (1 << num) == 0:                             (Slot im CFS wirklich leer)
3898      box_state.modify_Tn_data(… 'vender' … 'none' …)        (Argumentfolge V)
3899      box_state.update_same_material_list(…); self.update_Tnn_map(…)
3900      self.update_filament_pos(…)
3901  self.is_use_ending_material = False                        (GOT False @0xe4b40)
3902  self.box_save.box_filament_err = False
3904  self.box_save.clear_err();  box_save.last_cmd_tmp = box_save.last_cmd
3905  self.box_save.last_cmd = None                              (GOT None @0xe4cd4)
      return True                                                (GOT True @0xe4d04)
```

**kwargs der Aufrufer (B):**

| Aufrufer | Zeile | kwargs (an `recode_err` im Fehlerfall) | Reaktion auf `False` |
|---|---|---|---|
| `Tn_action` (T-Wechsel) | Z.4614/4615 | `err='z_err_extrusion_all_materials_err', vtnn=<vtnn>` | Abbruch des Wechsels (Vergleich mit False @0xa1b64) |
| `cmd_CR_BOX_PRE_OPT` (`M8200 P`) | Z.5761/5762 | `err='cr_break_ending_flush_err'` (vorher `M400`) | Vergleich mit False @0x536d0 (V: Abbruch) |
| `box_end` (`BOX_END` aus `END_PRINT`) | Z.5111/5112 | `err='print_end_err', last_tnn=box_save.last_cmd` | Ende (V) |

Danach (V): `last_cmd` ist `None` und der Kopfsensor leer. Der normale Weg mit Schneiden und Rückzug (`Tn_action` Z.4619 ff., `box_end` Z.5115 ff.) entfällt damit. Er hängt an `get_filament_sensor_detect()`.

### 2.3 `BoxAction.extrusion_all_materials(self)` @0x1c6a90, Z.1783–1824

```
1784  temp = self.get_material_target_temp(self.box_save.last_cmd)
1785  if print_stats.state == "paused" and config.has_section("gcode_macro PRINTER_PARAM")
1786/87     and PRINTER_PARAM.variables['hotend_temp']: temp = …['hotend_temp']   (gemerkte Drucktemperatur)
1788  log "hotend_temp: %s"
1789  speed = self.get_material_max_extrusion_speed(self.box_save.last_cmd)      # mm/s = max_vol/2.4
1790  log "extrude all material, last_cmd: %s, temp: %s"
1792  if speed is None: velocity = boxcfg.Tn_extrude_velocity[0]                 # 360 mm/min
1794  else:             velocity = speed * 60                                    # OHNE 0.8, OHNE 360-Deckel
1795  if paused / Extruder nicht can_extrude (Verknüpfung V): 1796 self.quickly_wait_heating(temp)
1800  length = 0; stop_next = False
      loop:
         self.go_to_extrude_pos()                                                # Schacht, set_flushing_sign
1801     ret = self.check_and_extrude(80, velocity, wait=False)                  # G91 / G0 E80 F<v> / G90, ohne M400
1802     t = 80 / velocity * 60                                                  # Sekunden
1803     if t > 0.5:  1804 self.reactor.pause(t - 0.5);  1805 M400
         else:        1807 M400
1809     if ret is False: warning "extrude err" (V: return False)
1811     self.blow()                                                             # fan0 5 s voll
1812     self.nozzle_clean(circle=False)                                         # Blech + 3× Schütteln, bleibt im Schacht
1813     length += 80
1814     if length >= boxcfg.max_tube_length: 1815 warning "extrude more than %d"; break
         if stop_next: break
1819     stop_next = (self.get_filament_sensor_detect() is False)
1821  self.set_cool_temp()                                                       # M104 S140 u. a. (FLUSH-BEFUND §7.2)
1822  if box_save.last_cmd: 1824 communication_set_box_mode(addr, 'IDLE')
```

**Belege**
- 80 = `K80` (@0x1c781c).
- `wait=False` = GOT False @0x1c7848. Derselbe Wert wird als `circle=` und als Vergleichswert für den Sensor genutzt (`[sp,#0x24]`, @0x1c8548 `clz`).
- Schleifenende @0x1c7f24–0x1c8570: zuerst `length >= max_tube_length` (RichCompare op 5 @0x1c7f7c), dann das Flag (`cmp [sp,#0x2c],#1` @0x1c84b8 → @0x1c8140 `set_cool_temp`), dann der neue Sensorwert.
- `t − 0.5`: Helfer @0x1c6948, `vmov.f64 d0,#0.5` + `vsub.f64` @0x1c69f4. Im ganzen Funktionsrumpf steht **kein** `monotonic` (0 Treffer).
  - **V:** Klipper-`reactor.pause()` erwartet eine absolute Weckzeit. `t − 0.5` liegt immer in der Vergangenheit, die Pause kehrt also sofort zurück. Das eigentliche Warten übernimmt das folgende `M400`. Das sieht nach einem Creality-Fehler aus, der aber ohne Wirkung bleibt.
- `max_tube_length` int Default **3000**, `buffer_empty_len` 30 (`BoxCfg.__init__` @0x1b8f14 / @0x1b8b18).

**Ergebnis (V):** Der Rest wird in 80-mm-Stücken ausgespült, bis der Kopfsensor leer meldet. Danach folgt noch genau ein Stück (das Ende vom Sensor bis zur Düse), höchstens 3000 mm. Die Geschwindigkeit ist die volle Material-Fördergrenze, also ohne den Faktor 0.8 und ohne die 360-Grenze des Normal-Spülens:

| Material | Geschwindigkeit |
|---|---|
| Hyper PLA (23 mm³/s) | ≈ 575 mm/min |
| Generic PLA (14 mm³/s) | 350 mm/min |

Es wird kein `remain_material.save` geschrieben. Danach `set_cool_temp` (140 °C).

**G-Code `BOX_EXTRUSION_ALL_MATERIALS`** (`cmd_extrusion_all_materials` @0x644f0, Z.5510–5517) läuft unabhängig vom Flag:
- ruft `extrusion_all_materials()`;
- bei `False`: `clear_err`, `clear_e_err`, `recode_err('macro_extrusion_all_materials_err')`;
- sonst `last_cmd_tmp = last_cmd`, `last_cmd = None`, `SET_FILAMENT_SENSOR … ENABLE=0`.

Retry über `macro_extrusion_all_materials_err_retry_process` (@0x939a8).

---

## 3. Druckende – `BOX_END` / `BOX_END_PRINT`

Stock-`END_PRINT` (`config/F008/gcode_macro.cfg`): `RESTORE_LIMITS`, `M400`, `G92 E0`, **`BOX_END`**, **`BOX_END_PRINT`**, …

### 3.1 `MCMBW.box_end(self)` @0x7fe0c, Z.5104–5131 (B, Rumpf über Fehlerpfade und GOT-Vergleiche)

```
5106  if box_state.Tn_data['enable'] == 0:      # CFS-Druck aus
5107/09   box_action.move_to_safe_pos(); return
5111  if box_save.last_cmd is not None and box_action.is_use_ending_material:
5112      ret = box_action.is_use_ending_material_flush(err='print_end_err', last_tnn=box_save.last_cmd)
          if ret is False: return               (GOT False @0x801b8)
5115  if box_action.get_filament_sensor_detect():            # Kopf hat Filament
5117      go_to_extrude_pos(); temp = get_material_target_temp(tnn=last_cmd, type=remain_material.type)
5119      quickly_wait_heating(temp)
5121      ret = move_to_cut()   → False: clear_e_err; recode_err('print_end_move_to_cut_err')   (5122/5124)
5125      go_to_extrude_pos()
5129      ret = box_retrude_material(…)  → False: recode_err('print_end_err', last_cmd)
5130      move_to_safe_pos(); box_save.last_cmd_tmp = last_cmd
5131      box_save.last_cmd = None                                        (GOT None @0x81000)
```

**Live-Beleg (B+L, `klippy.log.2026-10-12` ab Zeile ≈55000, 01:15:34–01:16:21):**
1. `END_PRINT_Z_SAFE`
2. `filament_sensor true`, Heizen auf 220
3. `[box] cut sensor detected` / `cut to return OK`
4. 3× `extrude = -10.0 … tnn: None` mit Box `0x11` (RETRUDE_PROCESS)
5. `filament_sensor false`
6. `SET_PRE_LOADING`, `TIGHTEN_UP_ENABLE`
7. `clean the data of power loss` + `Tnn_map: …` (= §3.2)

`clean the data of power loss` steht live 6× am 12.10.

**Stock spült am Druckende also nicht, sondern entlädt:**
1. Heizen auf die Materialtemperatur.
2. Schneiden.
3. Rückzug in die Box (`box_retrude_material` → `Tn_Extrude(Tn_retrude …)`, Z.3421/3496).

Die Düse bleibt mit dem alten Material gefüllt. `remain_material` wird nicht geändert, der nächste Wechsel spült also farbabhängig (REMAIN-BEFUND).

### 3.2 `cmd_box_end_print` @0x65afc, Z.5476–5491 (B)

Bausteine:
- Pre-Loading `OPEN` (`force`), `communication_tighten_up_enable(ENABLE)`
- `Tnn_map`-Log, `box_save.clear_resume_tnn()`
- `if need_update_power_loss_info == False: need_update_power_loss_info = True` (Z.5486/5487, GOT False/True @0x662fc/@0x66360)
- **`power_loss_clean()`** (Z.5489)
- `modify_Tn_data(…'enable'…)` (Z.5489/5490, Fehlerweg `'state_err'`), `SET_FILAMENT_SENSOR … ENABLE=0`

**Kein Spülen.**

---

## 4. Stromausfall

Ablauf GM:626-662 (SDCARD-BEFUND B.2):
1. `BOX_POWER_LOSS_RESTORE`.
2. Liegt in der Box-Speicherdatei `enable == 0` (CFS-Druck aus): **`box.flush_material()`**, dann Kopfsensor prüfen.
3. Sonst `T<n>` (bzw. `M8200 L I<n>`). Bei gleichem Slot läuft der in §4.4.

### 4.1 `BoxAction.power_loss_restore(self)` @0xea6e4, Z.3775–3799 (= `BOX_POWER_LOSS_RESTORE`, Wrapper `cmd_power_loss_restore` @0x641c8)

```
3775  data = {}
3776  if os.path.exists(box_state.tn_save_data_path):
3778–3783  try: data = json.load(open(path,'r'))  except: logging.error(e);  if data is None: warning "data is None"
          enable = data.get('enable', None); tnn_map = data.get('tnn_map', {}); last_cmd = data.get('last_cmd', None)
          logging.info("enable = %s, map = %s, last_cmd = %s")
3790      if enable == 1 or enable == 0:                     (K1 @0xeb938, K0 @0xeb9d4)
3791          gcode: "BOX_ENABLE_CFS_PRINT ENABLE=%s" % enable
3793      if len(tnn_map): for k in tnn_map: box_state.Tnn_map[k] = tnn_map[k];  3795 log "Tnn_map: %s"
3797      if last_cmd is not None: box_save.last_cmd = last_cmd
3799  else: logging.warning("file not exist")
```

### 4.2 `BoxAction.power_loss_clean(self)` @0xe8664, Z.3802–3828 (aus `BOX_END_PRINT`)

```
3803  if os.path.exists(box_state.tn_save_data_path):
3806–3810  data = json.load(…) (Fehler → logging.error; None → warning "data is None")
3814–3816  enable = data.get('enable', None); tnn_map = data.get('tnn_map', {}); last_cmd = data.get('last_cmd', None)
3817  if enable is not None: del data['enable']
      if len(tnn_map): del data['tnn_map']
      if last_cmd is not None: del data['last_cmd']
3825  try: json.dump(data, open(path,'w')); 3828 logging.info("clean the data of power loss")
      except: logging.error(e)
  else: logging.warning("file not exist")
```

**Folge (V):** Nach einem normalen Druckende fehlt `enable` in der Datei. `GM:633 data.get("enable", -1)` liefert dann −1, und eine spätere Fortsetzung nimmt den CFS-Zweig. Wie bei REMAIN-BEFUND §4 wird nicht atomar geschrieben (`open('w')`).

### 4.3 `MCMBW.flush_material(self, velocity=300)` @0xaa3cc, Z.4519–4539

Default 300: Wrapper @0xab594 lädt `K300` (Slot 0x243484, `PyLong_FromLong(300)` @0x1b1e4). Das zweite C-Argument liegt in `[sp]` (@0xaa3dc).

```
4521  self.box_action.go_to_extrude_pos()
4523  if self.box_action.remain_material:              # RemainMaterial-Objekt → immer wahr (V)
4524      v = box_action.get_material_max_extrusion_speed(type=remain_material.type)   # mm/s
4525/26   if v: velocity = v * 60 * 0.8                  (K60, K0.8)
4527  length = box_action.boxcfg.Tn_extrude;  flush_list = []           # 140
4529  box_action.cal_flush_list(length, flush_list)                     # → [90, 70]
4530  for i in range(len(flush_list)):
4530/31   box_action.check_and_extrude(flush_list[i], velocity, wait=True)          (GOT True @0xaa9ec)
4532      box_action.blow()
4533      if box_action.box_save.error is not None:
4534          logging.warning("error: %s" % error); 4535 return False                (GOT False @0xaad10)
4536      if i != len(flush_list) - 1: box_action.nozzle_clean(circle=False)        (GOT False @0xaae0c)
4538  box_action.nozzle_clean()                         # mit Kreisen
4539  box_action.move_to_safe_pos();  return None
```

**Unterschiede zum Normal-Spülen (FLUSH-BEFUND §1/§6), alle B:**
- **feste Menge 160 mm** (keine Farbe, kein ×2, kein Minimum 370)
- **ein** Geschwindigkeitswert ohne 20/80-Teilung und ohne ×1.2, ohne 360-Deckel, dafür ×0.8
  - Hyper PLA ≈ 460 mm/min, Generic PLA 280 mm/min (= `remain_material_velocity·0.8`, REMAIN-BEFUND §1)
- **keine Temperaturlogik**: kein `quickly_wait_heating`, kein `M104`; es gilt die Temperatur, die GM vorher mit `M109` gesetzt hat
- **keine Messrad-/Verstopfungsprüfung**
- **kein `remain_material.save`**
- Fehler nur über `box_save.error`

Der Kopfsensor wird nicht verändert.

### 4.4 `MCMBW.power_loss_same_material_process(self, tnn, vtnn)` @0xa61cc, Z.4541–4580

Auslöser: `Tn_action` Z.4590–4596.
- Bedingung: `last_tnn == tnn` **und** `box_state.is_continue_print`; Log `is_continue_print, last_tnn[%s] is the same with tnn[%s]`.
- Z.4595: `get_filament_sensor_detect()` steuert den Aufruf (Polarität V).
- Ohne Fortsetzung bei gleichem Slot wird nur geheizt (Z.4601–4604).

```
4542  Tnn_content = box_state.get_Tnn_content(tnn);  4543 num = {'A':0,…}[…]
4545/46  material    = 0x08-Sensor 'MATERIAL'    → Tn_inner_data filament_sensor.material
4547/48  connections = 0x08-Sensor 'CONNECTIONS' → …connections
4549  if (material & connections) & (1 << num):           # Einlauf UND Verbindung belegt
4551–4554  (filament_useup-Kennung zurücksetzen, update_filament_pos, Tn_data['filament'] = 1 – Details V)
4555      communication_set_box_mode(addr, 'PRINT', num=num)
4556      ret = self.flush_material()                          → §4.3
4558      if ret is False:                                     (GOT False @0xa71e0, cmp r7,r8)
              box_state.clear_e_err(); 4559 recode_err('e_err_tn_extrude_err', tnn=tnn, vtnn=vtnn)
4561      box_state.is_continue_print = False;  return True
4563  elif connections & (1 << num):                        # verbunden, aber Einlauf leer
4565      set_box_mode(addr,'PRINT', num=num)
4570–4573 material_type/color_value aus get_tn_save_data → modify_Tn_data
4575      box_action.extrusion_all_materials()                 # Rest ausspülen, §2.3
4576/77   reactor.register_callback(box_action.material_auto_refill).wait()
4579/80   if not get_filament_sensor_detect(): recode_err('filament_err', addr=…, filament_sensor_enable=…)
```

---

## 5. `BoxAction.quickly_wait_heating(self, temp)` @0x134ea0, Z.2761–2772

Das schließt den offenen Punkt aus FLUSH-BEFUND §9.

```
2762  self.gcode.run_script_from_command("M104 S%.2f" % temp)
2763  target = self.toolhead.extruder.get_heater().target_temp      # nach M104 = temp
2764  while not self.is_shutdown:
2766/67   smoothed = …get_heater().smoothed_temp
          if target - 10 <= smoothed <= target + 10:              (K10, Sub @0x34728 / Add @0x34964, RichCompare op 1 = <=)
              if …get_heater().can_extrude:
2770–72           G91 ; G0 E0.01 F60 ; G90
                  return
2769      self.reactor.pause(self.reactor.monotonic() + 0.5)     (K0.5, Add @0x35068)
```

**Folgen (B/V)**
- **Kein Timeout.** Die Schleife endet nur über Fenster + `can_extrude` oder über Shutdown.
- **Das Fenster ist beidseitig.** Beim Abkühlen (z. B. 280 → 220) wartet die Funktion, bis ≤ 230 °C erreicht sind.
- `can_extrude` ist Upstream `min_extrude_temp` (170). Darunter wartet sie trotz Fenster weiter.
- Die Max-Temperatur-Stufe (`check_flush_temp_and_extrude` Z.1340) heizt mit `quickly_wait_heating(max_temp − 5)`. Sie kehrt also schon bei **≥ max_temp − 15** zurück, erst danach kommt `M104 S<max_temp>`. Live 12.10.: `target_temp:235` für maxTemp 240 → Freigabe ab 225 °C (V).
- Der Mini-Vorschub `E0.01 F60` ist ohne Funktion für die Menge (V: hält den Extruder „aktiv“).

**Pi (`k2_box.flush_segment_lines`):** `M104 S<max>` + `TEMPERATURE_WAIT MINIMUM=<max−5>` wartet länger (10 K mehr) und ist nur einseitig.
- Für das Spülen ist das unkritisch, eher sicherer.
- Den Start-Heizschritt macht `k2_box._heat` mit `M109 S<temp>`. Upstream-M109 wartet bis auf ±1 K mit Ableitung, das ist langsamer als Stock (V).

---

## 6. Mehrfach-Percent-Pfad in `BoxAction.Tn_Extrude` (Z.3107–3145)

Das schließt den offenen Punkt aus FLUSH-BEFUND §9. Quelle: Dekompilat `Tn_Extrude` @0x11c060.

Er gilt bei `len(percent) > 1` (Z.3057/3058: `None` → 1, genau 1 Eintrag → `percent[0]`).

```
3107  if Tnn_content != None:                                   (RichCompare op 3)
3108      communication_measuring_wheel(addr, 'GET');  3111 last = get_Tn_data(addr,'measuring_wheel')
3114      total = 0
          for i in range(len(percent)):
              ret = self.check_and_extrude(extrude * percent[i], velocity[i])
3116          if ret is False: warning "extrude err" (V: return False)
3118          total += extrude * percent[i]
3119          if total >= 2 * boxcfg.buffer_empty_len:          (K2, op 5; Default 30 → 60 mm)
3120–3125         Messrad GET; diff = current - last; log "last_measuring_wheel = …"
3128              if abs(diff) < abs(boxcfg.diff_length):        (op 0; Default |−10|)
3130                  self.error(key_nozzle_blocked_err …)      # key845 wie im Spülpfad
3132      self.blow();  3134 self.nozzle_clean();  3135 self.go_to_extrude_pos()
      else:
3138      for i in range(len(percent)): ret = check_and_extrude(extrude * percent[i], velocity[i])
3140          if ret is False: warning "extrude err" (V: return False)
3142      self.blow();  3144 self.nozzle_clean();  3145 self.go_to_extrude_pos()
```

- **Ohne Temperaturlogik und ohne `cal_flush_list`.** `velocity` muss dieselbe Länge wie `percent` haben, sonst IndexError (V).
- **Aufrufer (B):**
  - `BOX_MATERIAL_FLUSH` (`cmd_material_flush`, Z.4314–4332): `LEN` 0…200 (Default `Tn_extrude`), `VELOCITY=a,b,…`, `TEMP` `min_extrude_temp`…`max_temp` (Default `Tn_extrude_temp`), `PERCENT=a,b,…` (Default `Tn_extrude_percent`) → `material_flush(…)`. Am Ende steht `check_and_extrude(-5, 360)` (Tupel 0x24353c, Werte über Init-Code geprüft).
  - `BOX_TN_EXTRUDE` (`cmd_Tn_Extrude`, Z.5354): `Tn_extrude`/`Tn_extrude_velocity`/`Tn_extrude_temp`/`Tn_extrude_percent`, ohne TNN.
- **Im Stock-Ablauf nicht erreicht.** `Tn_extrude_percent` ist per Default `(1,)` und in der Live-`box.cfg` nicht gesetzt. Die Spülkette nutzt `percent=[1]`, die Rückzüge `Tn_retrude` < 0 (Zweig `extrude < 0`).
- **Pi:** nicht nachbauen.

---

## 7. Nebenbefunde und Korrekturen

1. **Tupel-Auflösung (Methodenfehler, B).**
   - Das Tupel-Skript der FLUSH-Arbeitsdateien übernimmt bei `mov r2, r6` einen veralteten Registerwert. r6 ist aber GOT `_Py_NoneStruct` (Init @0xed0c/@0xed14, Literal @0xe764 → 0x234f98).
   - **Betroffen:** `('nozzle_temperature', 300)`, `('maxTemp', 300)`, `('filament_max_volumetric_speed', 300)`, `('enable', 300)`, `('last_cmd', 300)`. Richtig ist jeweils `None` als zweites Element.
   - **Echt sind:** `(-7,300)`, `(3,300)`, `(-3,300)`, `(-5,360)` und `flush_material(velocity=300)` (Slots 0x243484 bzw. 0x24327c im Init geprüft).
   - **FLUSH-BEFUND §5 ist zu korrigieren:** `kvParam.get('nozzle_temperature', None)` bzw. `base.get('maxTemp', None)`. Fehlt das Feld, folgt `int(None)` → Ausnahme → V: Fallback `Tn_extrude_temp`.
   - Auswirkung auf `nachbau/flush.py`/k2_box: keine. Beide nutzen bei fehlendem Feld schon den Fallback.
2. **SDCARD-BEFUND C.1:** Zeilen und das dritte `M400` wie in §1.1.
3. **FLUSH-BEFUND §7.2 „Markerdatei … Stromausfall-Reinigung“** ist widerlegt (§1.3). §9 „`set_flushing_sign`-Rumpf“ ist vollständig geklärt.
4. **REMAIN-BEFUND §2** („`flush_material` … (·60·0.8) **kein save**“): bestätigt und um den Default 300 ergänzt.

---

## 8. Abgleich mit `pi-host/k2_box.py` und Vorschläge (keine Code-Änderung)

| Stock-Sonderfall | k2_box heute | Relevanz Pi |
|---|---|---|
| `set/reset_flushing_sign` = `SET_LIMITS`/`RESTORE_LIMITS` | keins; Pi-`printer.cfg` hat dauerhaft die weiten Grenzen (SDCARD-BEFUND C.1) | **keine** |
| `has_flushing_sign`, Datei `flushing_sign` | – | keine (im Stock ohne Aufrufer) |
| Druckende `box_end`: Schneiden + Rückzug in die Box | Pi-`END_PRINT` zieht nur E −1/−pause_extrude zurück, **kein Entladen** | **mittel** – Design-Entscheidung |
| Restmaterial (`is_use_ending_material` → `extrusion_all_materials`) | fehlt; Runout nur über den Kopfsensor (`KTWO_BOX_RUNOUT`: E30 + Nachladen) | **mittel/hoch**, s. Vorschlag 1 |
| `power_loss_*`, `flush_material` | Upstream-Klipper hat keine Stromausfall-Fortsetzung | **niedrig** (erst mit eigenem Power-Loss-Resume) |
| `quickly_wait_heating` | `M109` / `TEMPERATURE_WAIT MINIMUM=max−5` | gering; Pi wartet gründlicher (§5) |
| Mehrfach-Percent | – | keine |

**Vorschläge (Priorität absteigend)**

1. **Restmaterial-Fall absichern.** Stock zieht einen Rest **ohne Ende in der Box** nie zurück, sondern spült ihn aus.
   - Ein Pi-Wechsel (`KTWO_BOX_CUT` → `KTWO_BOX_RETRUDE`) nach leer gelaufenem Einlauf würde ein freies Stück in den Schlauch/Hub zurückziehen. **ANNAHME:** Das kann den Hub verstopfen oder den nächsten Slot blockieren; am Gerät nicht geprüft.
   - Vorschlag:
     - (a) Im Druck aus dem 0x0a-Poll den Status 0x50 (Einlauf/Hub frei im PRINT, GHIDRA-CFS-BEFUND2 §4) als Flag `ending_material` merken. Stock-Pendant: `send_data` Z.2153.
     - (b) In `KTWO_BOX_PRE_OPT` bzw. vor `CUT`/`RETRUDE` gilt dann, wenn das Flag gesetzt ist: kein Schnitt, kein Rückzug, sondern ein neuer Schritt „Rest ausspülen“ nach §2.3:
       - Kopfsensor aus, Schacht, Heizen;
       - Schleife `G1 E80 F<max_vol/2.4·60>`, je Stück Blasen + Blech (`gcode_wipe` ohne Kreise);
       - Ende bei Kopfsensor leer + 1 Stück, Obergrenze `max_tube_length` 3000;
       - danach Flag löschen.
     - (c) Den Rest wie Stock **nicht** als `remain_material` speichern.
   - Default: als Option `ending_material_purge` (Vorschlag Default AN, wenn `allow_move`/`allow_write` an sind, sonst Pause mit Klartext).
2. **Druckende entladen (optional).** Stock schneidet am Druckende und zieht in die Box zurück (§3.1), damit das Filament nicht im Kopf liegt.
   - Vorschlag: neues `KTWO_BOX_END_PRINT [UNLOAD=0|1]` für `END_PRINT`:
     - Option `end_print_unload` (Default AUS, weil die Pi-Kette heute bewusst geladen lässt);
     - Ablauf: `_heat(Materialtemperatur)` → `CUT` → `GO_EXTRUDE_POS` → `RETRUDE` → `SAFE_POS` → `last_loaded = None`;
     - `remain_material` bleibt (Düse ist noch voll), wie Stock.
   - Vorher Restmaterial-Fall wie in Vorschlag 1 prüfen.
3. **Kein Spülen am Druckende einbauen.** Stock hat das nicht. Der Begriff „ending material flush“ meint den Runout-Rest (§2), nicht das Druckende.
4. **Heizwarten:** Das Pi-Verhalten beibehalten.
   - Optional nur für die Max-Temp-Stufe `MINIMUM=max−15` statt `max−5`. Das ist Stock-näher und spart Zeit, ist aber eine Design-Entscheidung.
   - Ein Timeout ist beim Pi über `TEMPERATURE_WAIT` ebenfalls nicht vorhanden. Ein eigenes Zeitlimit mit Pause wäre robuster als Stock (Vorschlag, nicht Stock).
5. **Stromausfall:** erst relevant, wenn der Pi ein Power-Loss-Resume bekommt. Dann gilt Stock-nah:
   - bei gleichem Slot bzw. ohne CFS feste 160 mm (`[90,70]`) mit `max_vol/2.4·60·0.8`;
   - Blech zwischen den Stücken, Kreise am Ende;
   - **ohne** `remain_material`-Update.
   - Die Box-Zustandsdatei (`enable`/`tnn_map`/`last_cmd`) am Druckende löschen wie `power_loss_clean`.
6. **Doku-Korrekturen** wie in §7 (FLUSH-BEFUND §5/§7.2/§9, SDCARD-BEFUND C.1).

---

## 9. Offen / ANNAHME

- Wer die Datei `flushing_sign` anlegt bzw. `has_flushing_sign` abfragt (außerhalb von `box_wrapper`; rootfs-Auszug unvollständig).
- `is_use_ending_material`-Setzbedingung in `send_data` Z.2145–2153 im Detail (`auto_refill`-Abfrage, Reihenfolge `printing`/`paused`).
- Argumentfolge von `modify_Tn_data(… 'vender' … 'none')` in Z.3898/3900.
- Rückgabe bei `extrude err` in `extrusion_all_materials` und im Mehrfach-Percent-Pfad (`return False` = V).
- Genaue Verknüpfung in `extrusion_all_materials` Z.1795 (`paused` / `can_extrude`) vor `quickly_wait_heating`.
- Mechanische Folgen eines Rückzugs bei leerem Einlauf am Pi (Vorschlag 1) – nur am Gerät klärbar.
- Live (Logs 12.–16.10.): `clean the data of power loss` 6× (Druckende, §3); **keine** Zeile `is use ending material`, `extrude all material` oder `start box.flush_material` → Restmaterial- und Stromausfall-Pfade sind live unbelegt (nur statisch B).

## 10. Methode

**Ghidra**
- Ghidra 12.1.4 headless, Kopie des FLUSH-Projekts (Imagebase 0), `-readOnly`.
- Dekompilate von 17 Funktionen, Cython-Namen mit dem vorhandenen PIC-Auflöser nachbenannt.

**Zeilen und Variablennamen**
- Zeilen aus `__pyx_lineno` (Fehlerpfade) und `co_firstlineno` (PyCode_New im Init).
- Kontrollfluss über GOT-Lasten (`True/False/None`) und Sprungziele in der `llvm-objdump`-Disassembly.

**Gegenproben**
- Tupel-Slots gegen den Init-Code geprüft. Das hat den Fehler in §7.1 aufgedeckt.
- Floats nur aus `vmov.f64` / Literal (0.5 @0x1c69f4).
- REA-MCP (Ghidra 12.1.4, Standardimport): `read_bytes 0x1d69f4` (= vaddr 0x1c69f4 + 0x10000) liefert `000bb6ee 400b38ee` = `vmov.f64 d0,#0.5` / `vsub.f64 d0,d8,d0`. Das bestätigt den 0.5-Helfer aus §2.3. Ghidras Auto-Analyse hat diesen Bereich nicht als Funktion angelegt (`procedure_assembly` scheitert); Bytegleichheit mit `llvm-objdump` ist geprüft.
