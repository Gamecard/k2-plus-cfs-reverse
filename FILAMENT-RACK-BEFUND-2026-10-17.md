# FILAMENT-RACK-BEFUND 2026-10-17 – `filament_rack_wrapper` (K2 Plus, FW 1.1.7.0)

Rein **statische** Analyse (kein Drucker-/Pi-Zugriff). Nachbau: `<projekt>/nachbau/filament_rack.py`
(Selbsttest **ok 83/83**). Arbeitsdateien (lokal, nicht fürs Repo): `<projekt>/arbeitsdateien-lokal/re-rack-1017/`.

Belegform: **B** = belegt (Code), **L** = zusätzlich Live-Log, **A** = Annahme, **TODO** = offen.
`Z.<n>` = Cython-`__pyx_lineno` der Originalquelle `klippy/extras/filament_rack_wrapper.py`
(aus den Fehlerpfaden; einzelne Operationen ±1 Zeile). `@0x…` = vaddr im `.so` (llvm-objdump, Basis 0).

## 0. Quelle und Methode

- Binary: `rootfs-live/usr/share/klipper/klippy/extras/filament_rack_wrapper.cpython-39.so`
  (206 768 B, ELF32 armhf, stripped, Cython 0.29.21, md5 `50713b85…`, laut livelog-motor-Abgleich
  in allen geprüften Ständen identisch). Loader `filament_rack.py`: 3 Zeilen
  (`load_config → FilamentRackWrapper(config)`).
- Rezept wie frühere lokale Analysen: `llvm-objdump -d --no-show-raw-insn`,
  Cython-Stringtabelle (324 Einträge, `.data` 0x40970–0x422ac, Slots in `.bss`),
  Konstanten (23, inkl. Float-Fix), Tupel (49, u. a. **Varnames je Funktion**),
  Funktionsadressen über Qualnamen-Xrefs (28 Methoden). Eigener Annotator ergänzt
  GOT-Symbole (`_Py_True/False/NoneStruct`, Typen) und ordnet Operationen die
  `__pyx_lineno` des nächsten Fehlersprungs zu. Ghidra 12.1.4 (headless, Imagebase 0) als
  Gegenlesung für den Kontrollfluss.
- Builtin-Cache (B, Init @0x40dc–0x4120): `[0x42300]=open`, `[0x42308]=range`,
  `[0x42310]=hex`, `[0x42318]=chr`.

## 1. Was ist das „Filament-Rack“? (Hardware/Bus/Adresse)

Das Modul vereint **zwei** Dinge:

1. **RS485-RFID-Leser an Adresse `0x11`** (Config `addr`, Default 17; B Z.70, L Status).
   Auslöser: `not_pin` (live `!PA5`, **Haupt-MCU**) über `register_buttons([not_pin],
   _button_handler)` (B Z.104-105) → bei `state` truthy `reactor.register_callback(send_data)`
   (B @0xd924, Z.124-126). `send_data` fragt **einmal** die RFID ab (Abschnitt 4).
   Antwortformat = 40-Zeichen-Vendor wie beim CFS (gleiches Feldlayout,
   `box.split_rfid_vendor`). Physisch also ein RFID-Leser + Sensor am externen
   Spulenhalter; Auto-Addr-Startadresse der Klasse „RFID/Filament-Rack“ = `0x11`
   (PROTOKOLL-GESAMT.md §1.6). Der Bus ist derselbe 485-Bus (`serial_485 serial485`,
   B `find_objs` @0x1c8ac Z.116).
2. **Host-Logik für die externe Spule** (Makros `LOAD_MATERIAL*`/`QUIT_MATERIAL*`):
   Material-Temperatur/-Geschwindigkeit aus `material_database.json`, Schnellheizen,
   Lüfter sichern/blasen, Abkühlen, Spülen. Diese Teile **laufen beim User live**
   (Makros rufen `FILAMENT_RACK_*`, UI ruft beim Start `FILAMENT_RACK_MODIFY`, Status
   `filament_rack` wird abgefragt; L klippy.log 12./16.10.).

**Hardware beim User: nicht nachweisbar.** Live (klippy.log 05.–16.10.2026):
`[filament rack] not_pin: !PA5` wird geladen, aber **keine einzige**
`[filament rack] data_send`/`do not get respond`/`rfid`-Zeile → `send_data` lief nie,
d. h. `not_pin` hat nie ausgelöst (A: kein Sensor an PA5 oder nie betätigt). Status
`vender/color_value/material_type = '-1'` (= Startwert bzw. MODIFY ohne Parameter).
Auto-Addr-Tabellen im Log zeigen nur die eine CFS-Box (`mb_addr_table`), keinen Rack-Eintrag.
Fazit: Der RS485-Teil betrifft Hardware, die der K2 Plus des Users **vermutlich nicht hat**
(oder nie ausgelöst hat) – er ist für den Nachbau optional; die Material-/Heizlogik (2.)
ist dagegen live relevant.

## 2. Config `[filament_rack]` (B `__init__` @0x1cff8 Z.66-113; L Live-Status)

| Option | Methode | Default | min | max | Ziel | Beleg |
|---|---|---|---|---|---|---|
| `not_pin` | `get` | (A: None) | | | `not_pin` | Z.69, Tupel `('not_pin', ?)` |
| `addr` | `getint` | 17 | | | `addr` | Z.70 @0x1d2a0 |
| `extrude` | `getint` | 140 | 0 | 300 | `extrude` | Z.71 @0x1d324 |
| `temp` | `getint` | 220 | 140 | 350 | `temp` | Z.72 @0x1d3d0 |
| `velocity` | `getint` | 360 | 0 | 900 | `velocity` | Z.73 @0x1d484 |
| **`max_temp_flush_times`** | **`getfloat`** | **370** | **300** | **1000** | **`diff_material_type_min_flush_len`** | Z.74 @0x1d51c |
| `max_temp_flush_times` | `getint` | 3 | 0 | 10 | `max_temp_flush_times` | Z.75 @0x1d5e4 |
| `didi` | `getfloatlist` | () (L) | | | `beep_didi_param` | Z.76 |
| `need_clean_length` | `getint` | 70 | 0 | 200 | `need_clean_length` | Z.77 @0x1d71c |

**Creality-Fehler (B):** Z.74 liest den **falschen Schlüssel** `max_temp_flush_times` als
Float mit `minval=300`. Wird `max_temp_flush_times: 3` in der Config gesetzt, bricht Klippy
mit Config-Fehler ab; ohne Eintrag gilt 370 (= Box-Default, L). Live-Werte alle = Default (L).

Weitere `__init__`-Teile (B): `material_database`/`system_config` = `os.path.join(base_dir, …)`
(Z.79-80); Flags `can_get_filament_rack`, `can_get_filament_rack_tmp`; `filament_rack_data =
{'vender':'-1','color_value':'-1','material_type':'-1'}` (Z.107-109); `remain_material =
RemainMaterial(config)`; Events: falls `[motor_control]` mit `switch == 1` →
`'auto_addr:start' → enable_get_filament`, sonst `'klippy:ready' → enable_get_filament`
(A: genaue Verzweigung @0x1e24c-0x1e800), `'klippy:ready' → find_objs`.
G-Codes (Z.92-101): `FILAMENT_RACK`, `FILAMENT_RACK_FLUSH`, `FILAMENT_RACK_SAVE_FAN`,
`FILAMENT_RACK_RESTORE_FAN`, `FILAMENT_RACK_SET_TEMP`, `FILAMENT_RACK_PRE_FLUSH`,
`FILAMENT_RACK_MODIFY`, `SET_COOL_TEMP`.
`find_objs` (Z.116-121): `_serial = lookup_object('serial_485 ' + 'serial485')`, `toolhead`,
`print_stats`, `output_pin_fan0 = lookup_object('output_pin fan0')`, `box`,
`remain_material.get_velocity()`.

## 3. Methodenübersicht

| Methode | Adresse (Rumpf / Wrapper) | Zeilen | Nachbau |
|---|---|---|---|
| `RemainMaterial.__init__/init/save/get_velocity/get_status` | 0x25018 / 0x234b0 / 0x21c68 / 0x2190c / 0xaf3c | 8-61 | `RemainMaterial` |
| `__init__` | 0x1cff8 / 0x21760 | 66-113 | `CONFIG_SPEC`, `GCODES` |
| `find_objs` | 0x1c8ac | 116-121 | Stub `klipper_glue` |
| `_button_handler(self, eventtime, state)` | 0xd924 / 0xdca0 | 124-126 | `FilamentRackRfid.button_handler` |
| `enable_get_filament` | 0xaea4 | 129 | `enable_get_filament` |
| `check_rfid_valid(self, rfid)` | 0x1af00 / 0x1c6fc | 131-145 | `check_rfid_valid` |
| `send_data(self, eventtime=None)` | 0x25cfc / 0x27458 | 147-178 | `FilamentRackRfid.send_data` |
| `beep(self)` | 0x18a10 | 182-212 | `beep_plan`, `read_sound_switch` |
| `cmd_filament_rack(self, gcmd)` | 0x18738 / 0x18868 | 215 | = `self.send_data()` |
| `cmd_set_cool_temp` | 0x17a34 / 0x1858c | 217-226 | `set_cool_temp_gcode` |
| `get_status` | 0x174c0 / 0x17890 | ~227-230 | `FilamentRackRfid.get_status` |
| `get_material_target_temp(self, type=None, max=False)` | 0x14bf0 / 0x172a8 | 232-262 | `get_material_target_temp` |
| `get_material_target_speed(self, type=None)` | 0x12644 / 0x14a38 | 264-293 | `get_material_target_speed` |
| `get_material_type(self, type=None)` | 0x101f4 / 0x1248c | 295-325 | `get_material_type` |
| `is_different_type_material(self)` | 0xfb0c | 327-339 | `is_different_type_material` |
| `quickly_wait_heating(self, set_temp)` | 0x275fc / 0x28520 | 341-354 | `quickly_wait_heating` |
| `cmd_filament_rack_set_temp` | 0xf284 / 0xf964 | 355-360 | (Kette s. §5) |
| `blow(self)` | 0xceb4 | 380-385 | `blow_gcode` |
| `cmd_save_fan` / `cmd_restore_fan` | 0xca34 / 0xc5e8 | 387-390 / 392-396 | `save_fan_gcode` / `restore_fan_gcode` |
| `cmd_filament_rack_flush` | 0x286d0 / 0x2ce60 | 398-455 | **Stub** (§6) |
| `cmd_filament_rack_modify` | 0xdfb8 / 0xf0d4 | 461-481 | `modify_rack_data` |
| `cmd_filament_rack_pre_flush` | nur Wrapper 0xb960 | – | No-op (kein Rumpf, B) |

## 4. RS485-Protokoll des Racks (B, `send_data`)

**Request** (Bytes-Literale `is_unicode=0` @Stringtabelle 0x40de4–0x40e20; Variablen laut
Varnames-Tupel `cmd, addr, timeout, state, length`):

```
data_send = addr + length + state + cmd = b'\x11' + b'\x03' + b'\xff' + b'\x02'   (Z.162, @0x26244-0x26270)
Wire (an Serial_485_Wrapper, ohne Head/CRC):  11 03 ff 02
Vollframe:                                    F7 11 03 FF 02 64
                                              ^  ^  ^  ^  ^  CRC-8/ATM(03 ff 02) = 0x64
                                              |  addr len status=0xFF fun=0x02, keine Daten
ret = self._serial.cmd_send_data_with_response(data_send, 0.5)    (Z.167, 0.5 = vmov.f64 @0x263e4)
Log: '[filament rack] data_send: 0x11 0x3 0xff 0x2'   (' '.join(hex(i) for i in data_send), Z.163)
```

**Antwort:** `rfid = ''.join(chr(i) for i in ret[5:-1])` (Genexpr @0xd638, Z.170) – also
Vollframe `F7 11 <len> <status> <fun> <40 ASCII> <crc>`, erwartet `len(rfid) == 40`
(`cmp r0,#40` @0x267c8) → Frame 46 B, len-Byte 0x2B. Das Modul selbst prüft weder CRC noch
Absender/Status (macht `serial_485_wrapper`). Status-/fun-Byte der Antwort: **unbelegt** (TODO;
im Nachbau-Test synthetisch status 0x00, fun 0x02).

**Auswertung** (Z.171-178): `len == 40 and check_rfid_valid(rfid)` →
`filament_rack_data['vender'] = rfid` (ganz), `['material_type'] = rfid[11:17]`,
`['color_value'] = rfid[17:24]`; sonst Warnung `[filament rack] rfid[%s] is error`.
`ret is None` → `[filament rack] do not get respond`, Rückkehr.
Felder passen 1:1 zu `box.RFID_VENDOR_FIELDS` (mat_id 11..16, col 17..23).

**Ablauf/Flags** (Z.148-156, B; Vergleiche per Identität mit `Py_False/Py_True`):
```
if self.can_get_filament_rack is False:
    if self.can_get_filament_rack_tmp is True: return
    self.can_get_filament_rack_tmp = True
    while self.can_get_filament_rack is False:
        self.reactor.pause(self.reactor.monotonic() + 1)
threading.Thread(target=self.beep).start()
… Request/Antwort wie oben …
```
`enable_get_filament` (Z.129) setzt `can_get_filament_rack = True`. `_tmp` wird **nie**
zurückgesetzt (B: einzige Schreibstelle @0x25eb4).

## 5. Material-, Heiz-, Lüfter- und Ton-Logik (B)

- **check_rfid_valid** (Z.131-145): `material_type = rfid[12:17]` (5 Zeichen, `mov r1,#12;
  mov r2,#17` @0x1af3c) gegen `material_database.json → result.list[i].base.id`; Treffer →
  `respond_info('material_type: %s')`, True; sonst False. Except-Text ist ein Creality-
  Copy-Paste (`material database get extrusion speed: %s`).
- **DB-ID aus Typ:** `type[1:6]` (@0x14d74; 6-stelliger Typ `101001` → ID `01001`).
  `type is None` → `filament_rack_data['material_type']`; `type == 'None'` oder `'-1'` →
  Warnung `failed to obtain consumable type` + Fallback.
- **get_material_target_temp** (Z.232-262): Treffer → `int(kvParam.nozzle_temperature)`
  bzw. bei `max=True` `int(base.maxTemp)` (A: Zuordnung über Varnames `nozzle_temp`/
  `nozzle_max_temp`); Fallback `self.temp`. Log `get material temp: %d`.
- **get_material_target_speed** (Z.264-293): `v = float(kvParam.filament_max_volumetric_speed)
  / 2.4` (Z.282, Divisor 2.4 aus .rodata @0x13b80), Rückgabe `v * 60` (Z.292); Fallback
  `self.velocity` **ohne** ×60. **L:** Status `remain_material_velocity` live
  `350.00000000000006` (= 14/2.4·60) und `575.0` (= 23/2.4·60); Logs
  `max_volumetric_speed: 23` → `get material extrusion speed: 9`.
- **get_material_type** (Z.295-325): `base.meterialType` (sic), Fallback **None**
  (GOT 0x3ffb0 = `_Py_NoneStruct` @0x10a94).
- **is_different_type_material** (Z.327-339): Typen für `remain_material.type` vs. aktuellen
  Rack-Typ; None → False; `last not in current and current not in last` → True
  (`__Pyx_PySequence_ContainsTF(…, Py_NE)` @0xfcd4/0xfcf0 → **Teilstring**-Vergleich);
  sonst `abs(last_temp - current_temp) > 10`.
- **quickly_wait_heating(set_temp)** (Z.341-354): `M104 S<set_temp>`; `target =
  toolhead.extruder.get_heater().target_temp`; `while not printer.is_shutdown():` `cur =
  …smoothed_temp`; wenn `target-10 <= cur <= target+10` (zwei `Py_LE` @0x27a84/0x27acc) **und**
  `heater.can_extrude` → `G91`, `G0 E0.01 F60`, `G90`, Ende; sonst
  `reactor.pause(monotonic()+0.5)`.
- **cmd_filament_rack_set_temp** (Z.355-360): `temp = get_material_target_temp(
  type=remain_material.type)`, ein Vergleich `Py_GT` (@0xf3b4; A: gegen aktuelle Temperatur),
  Log `temp = %s`, `quickly_wait_heating(temp)`, `M400`.
- **cmd_set_cool_temp** (Z.217-226): `print_stats.state == 'paused'` → falls `target_temp >=
  min_extrude_temp` (`Py_GE`) `SET_GCODE_VARIABLE MACRO=PRINTER_PARAM VARIABLE=hotend_temp
  VALUE=<target>`, dann `M104 S140`; `'printing'` → nur Warnung `unknown action`; sonst `M104 S0`.
- **blow** (Z.380-385): `SET_PIN PIN=fan0 VALUE=255`, `G4 P3000`, `M400`,
  `SET_PIN PIN=fan0 VALUE=%.2f` (= `last_value*scale` von `output_pin fan0`).
  **save_fan**: `fan0_last_value = last_value*scale`, `SET_PIN PIN=fan0 VALUE=0.0`;
  **restore_fan**: `SET_PIN PIN=fan0 VALUE=%.2f % fan0_last_value`.
- **beep** (Z.182-212): `system_config.json → user_info.get('sound_sw', 1)`; aus → kein Ton.
  `didi` leer → `subprocess.check_output('/usr/bin/beep.sh 0.2', shell=True)`; sonst je
  Index: gerade → `beep.sh <didi[i]>` (+ `time.sleep(didi[i])`, A Reihenfolge), ungerade →
  `time.sleep(didi[i])` (`tst #1` @0x19aac).
- **cmd_filament_rack_modify** (Z.461-481): `COLOR_VALUE`/`MATERIAL_TYPE`/`VENDER` aus
  `gcmd.get_command_parameters()`, fehlend → Warnung + `'-1'` → `filament_rack_data` (L).
- **RemainMaterial** (Z.8-61): Datei `creality/userdata/box/remain_material_data.json`,
  Schlüssel `remain_material: {color, type}`; `save()` schreibt, loggt
  `save: color: %s, type: %s`, ruft `get_velocity()` = `get_material_target_speed(type=self.type)`.
  `get_status` = `remain_material_color/type/velocity` (L).

## 6. `cmd_filament_rack_flush` (Z.398-455) – nur Struktur, nicht nachgebaut

Belegte Reihenfolge der Zugriffe (B, Annotator `out/L_flush.txt`): `M106 P0 S50` (Z.399);
`remain_max_temp/remain_temp` = `get_material_target_temp(type=remain_material.type,
max=True/False)` (Z.401-402); `current_*` für `filament_rack_data['material_type']`
(Z.403-404); Vergleich (Z.405); `speed = get_material_target_speed(…)` (Z.408);
`is_different_type_material()` → `flush_len = diff_material_type_min_flush_len` sonst
`extrude` (Z.411-412); Farbe: `remain_material.color`/`filament_rack_data['color_value']` mit
Sonderwerten `'-1'`, `'none'`, `'unknown'` (Z.414-417), Hex-Paare `int(…, 16)` (Z.421-424);
`box.box_action.get_flush_len(…)` (Z.425), `cal_flush_list` (Z.431); Logs `hys: …`;
Schleife `box.box_action.check_flush_temp_and_extrude(…, max_temp_flush=True,
max_temp_flush_times)` (Z.439-446), `box.box_state.clear_e_err` (Z.448), `blow()` (Z.450),
`box.box_action.nozzle_clean('circle'?)` (Z.452-454), `remain_material.save(color_value,
material_type)` (Z.455). **TODO:** Parameter/Reihenfolge der `box_action`-Aufrufe und
Schleifenbedingungen; erfordert box_wrapper-`BoxAction`-Nachbau.

## 7. Weitere Auffälligkeiten (B)

- `send_data` blockiert als Reactor-Callback in `reactor.pause`-Schleifen (1 s) und ruft
  `cmd_send_data_with_response` synchron (0,5 s Timeout, Retry-Verhalten des 485-Wrappers A).
- Fehlertexte teils kopiert (`… get extrusion speed …` in check_rfid_valid/get_material_type).
- `FILAMENT_RACK_PRE_FLUSH` ist ein No-op (nur Argument-Wrapper @0xb960, keine Rumpffunktion).

## 8. Nachbau `nachbau/filament_rack.py`

- Frames nur bauen/parsen; Senden ausschließlich über `serial485.Serial_485_Wrapper.
  cmd_send_data_with_response` (Sendeschutz `check_tx_frame`), **Default gesperrt**
  (`allow_write=False` → `RackWriteLocked`, nichts gesendet).
- Material-/Heiz-/Lüfter-/Ton-Logik als reine Funktionen (Callbacks statt Klipper-Objekten,
  `beep_plan` statt `subprocess`), `RemainMaterial` mit frei wählbarem Pfad.
- Stubs (`NotImplementedError`): `cmd_filament_rack_flush`, Klipper-Glue (`load_config`,
  `register_buttons`, `register_command`, `find_objs`).
- **Selbsttest `python3 nachbau/filament_rack.py` → `ok 83/83`**. CRC: Es existiert **kein
  echter Rack-Frame** (Rack live nie gesehen); geprüft wird die gemeinsame serial485-CRC an
  echten Frames gleichen Aufbaus (Live `f7 01 07 00 03 1c ff 00 00 11`, ref-hi
  `f7 01 05 ff 0d 0f 01 69` mit status 0xFF) und daraus der Rack-Request `f7 11 03 ff 02 64`.
  RFID-Antworten im Test sind **synthetisch** (Vendor-Layout wie box.py). Live-gestützt geprüft:
  Config-Defaults, `14/2.4·60 = 350.00000000000006`, `23/2.4·60 = 575.0`, Log-Werte 9/5,
  MODIFY ohne Parameter → `'-1'`.

## 9. Offene Punkte

1. **Hardware-Existenz** beim User (Sensor an `PA5`, RFID-Leser an 0x11) – nur mit
   Sichtprüfung/Live-Test klärbar; bisher kein einziger Trigger im Log.
2. Antwort-Status/-fun des Racks und Retry-Verhalten (`retransmit`-Default des Stock-
   `serial_485_wrapper`) – nur per Mitschnitt mit Rack-Hardware.
3. Format/Inhalt `material_database.json` (lokal nicht vorhanden): Struktur
   `result.list[].base.{id,maxTemp,meterialType}` + `kvParam.{nozzle_temperature,
   filament_max_volumetric_speed}` aus dem Code; ID = 5 Zeichen (`type[1:6]`).
4. `cmd_filament_rack_flush` vollständig (§6), abhängig vom `BoxAction`-Nachbau.
5. Kleinere A-Punkte: Startwerte der Flags/`velocity`, Vergleich in `cmd_filament_rack_set_temp`
   (`Py_GT` gegen was), exakte Event-Verzweigung `auto_addr:start` vs. `klippy:ready`,
   `max`-Zuordnung in `get_material_target_temp`.
