# box_wrapper API-Fläche – Live-Introspektion auf dem Drucker (13.10.2026)

Methode: Artikel-Technik "Live introspection" – direkt auf dem K2 Plus (FW 1.1.7.0)
per SSH ausgeführt, KEIN Disassembly nötig. Alle Signaturen sind echt
(`inspect.signature`), inklusive der korrekten Default-Werte.

Reproduktion (ändert nichts am Drucker, nur import + introspection):
```
ssh root@192.168.1.100 '/usr/share/klippy-env/bin/python -c "
import sys
sys.path.insert(0, \"/usr/share/klipper/klippy/extras\")
sys.path.insert(0, \"/usr/share/klipper/klippy\")
sys.path.insert(0, \"/usr/share/klipper\")
import box_wrapper, inspect
print(inspect.signature(box_wrapper.BoxAction.motor_send_data))"'
```

## Klassen (9, wie im Artikel für 1.88 MB beschrieben; hier 2,3 MB)

- BoxAction (110 Methoden), BoxState (22), BoxCfg (0 = nur Daten),
  ParseData (9), BoxSave (11), BoxProtol (3: ExtrudeStage/RetrudeStage/START_ADDR),
  CutSensor (1), DryRunInfo (1), MultiColorMeterialBoxWrapper (122)

## Kern-Signaturen (Korrekturen gegenüber Static-Decode!)

```
motor_send_data(self, addr, cmd, timeout, state=b'\x00', data=b'', retries_en=True)
send_data(self, addr, cmd, timeout, state=b'\xff', data=b'', report_err=True)
```

WICHTIG: `send_data` nutzt Default `state=0xff`, `motor_send_data` `state=0x00` –
das BESTÄTIGT die STATUS-Semantik (Hi: 0xFF = Host-Operations-Kommandos).
Static-Decode lag bei beiden Defaults daneben (Artikel-Hinweis bestätigt).

### Live-Probe der Request-Payloads (K2 Plus)

Per SmartSelf-Proxy auf dem Drucker wurden die echten Wrapper-Aufrufe ohne
RS485-Schreibzugriff ermittelt:

```text
communication_get_rfid(1, 1)       -> send_data(b'\x01', b'\x02', 2, data=b'\x01')
communication_get_rfid(1, 15)      -> send_data(b'\x01', b'\x02', 2, data=b'\x0f')
communication_get_remain_len(1, 1) -> send_data(b'\x01', b'\x03', 2, data=b'\x01')
communication_get_remain_len(1,15) -> send_data(b'\x01', b'\x03', 2, data=b'\x0f')
```

`num` ist eine 1-hot-/Mehrfach-Slotmaske (`0x01/0x02/0x04/0x08`, `0x0f` = alle),
nicht die Slotnummer. Operational-Requests werden mit STATUS `0xff` gebaut;
die geloggten `status=0x00`-Frames waren häufig Antworten. `boxmini.py` wurde
deshalb korrigiert: Payload `[mask]`, STATUS `0xff`, Timeout 2 Sekunden.

```
communication_get_rfid(self, addr, num=15)
communication_get_remain_len(self, addr, num=15)
communication_extrude_process(self, addr, num, trigger='', stage=2, report_err=True, extrude=None)
communication_extrude2_process(self, addr, num, trigger)
communication_retrude_process(self, addr, num, trigger, report_err=True)
communication_set_pre_loading(self, addr, action, num=15, force=False)
communication_set_box_mode(self, addr, m, num='0')
communication_measuring_wheel(self, addr, action)
communication_get_box_state / _box_mode / _buffer_state / _hardware_status / _version_sn (self, addr)
communication_get_filament_sensor_state(self, addr, pos)
communication_get_filament_and_buffer_state(self, addr, report_err=True)
communication_create_connect(self, addr)
communication_ctrl_connection_motor_action(self, addr, act)
communication_set_motor_speed (...)  # vgl. cmd_timeout
communication_get_dry_mode / set_dry_mode / pause_dry / continue_dry / auto_dry (self, addr[, ...])
communication_tighten_up_enable(self, addr, enable)
communication_test(self, count, timeout, interval, addr, num)
process_msg(self, data_send, msg)
ret_parse_process(self, data_send, msg)
generate_auto_get_rfid_func(self, addr, num)
extrude_process_stage7(self)   # keine Args!
box_extrude_material_stage8(self, tnn)
```

`num=15` Default = 0x0F = alle 4 Slots (Bitmaske). Die Antwort ist positional
A..D; `0xff` darin bedeutet „nicht ausgewählt“, `0x00` „ausgewählt und leer“.

## Timeouts (Modul-Konstanten box_wrapper, Sekunden!)

```
TIMEOUT_SHORT_TIME=2, LONG=150, LONGER=300, MAX=3600
GET_BOX_STATE=3600, PAUSE_DRY=3600, CONTINUE_DRY=3600, SET_PRE_LOADING=300,
EXTRUDE_PROCESS=15, RETRUDE_PROCESS=150, EXTRUDE_PROCESS_MODEL2=150,
Rest (RFID/REMAIN/MODE/WHEEL/VERSION/DRY/CONNECT/...) = 2
```

Hinweis zur Namensgebung: `TIMEOUT_SHORT_TIME` existiert in zwei Bedeutungen –
`auto_addr_wrapper.py` (0,05 s Discovery) vs. `box_wrapper` (2 s Operational).
In `nachbau/` heißen sie `TIMEOUT_SHORT` (Discovery) bzw. `TIMEOUT_OPERATIONAL`
(Operational, 2,0 s). Nicht verwechseln.

## Frame-Positionen / Typen

```
HEAD=0, ADDR=1, LEN=2, STATE=3, CMD=4, DATA=5, BOX_NUM=1, MATERIAL_NUM=2
DEV_TYPE_MB=1, DEV_TYPE_CFS_PRO=10
```

## Fehler-Schlüssel (Auswahl, vollständig im Modul)

key_buffer_err, key_cut_err, key_cut_pos_err, key_cutter_err, key_dry_* (Heater,
Fan, NTC, Timeout, Over-Temp, AC), key_eeprom_err, key_enwind_err,
key_extrude_err1-5, key_filament_err, key_joint_err, key_left/right_rfid_card_err,
key_load_err, key_material_err, key_measuring_wheel_err, key_nozzle_blocked_err,
key_params_err, key_retrude_err1-4/6, key_rfid_err, key_sensor_err, key_slot_err,
key_speed_err, key_state_err, key_timeout (+ Trockner-Fehler = CFS hat Dryer!)

## ParseData (alle 9)

```
get_box_status(self, msg)
get_cmd_num(self, data_send, return_method)
get_key_from_value(self, dict, v)
get_measuring_wheel(self, data_send, msg)
get_remain_len(self, data_send, msg)
get_rfid(self, msg)
parse_num_string_to_byte(self, data)
parse_num_to_byte(self, data)
parse_uint16_num_to_byte(self, data)
```

`get_rfid(self, msg)` nimmt NUR msg – parst die Antwort allein (passt zu
`A:<hex>;`-Format). `get_remain_len/get_measuring_wheel` brauchen zusätzlich
`data_send` (Kontext: welcher Slot gefragt war).

## BoxState (alle 22)

Tn-Datenmodell: get/modify_Tn_data + get/modify_Tn_inner_data
(addr, part, num, subpart, uppart), generate_Tn_data/inner_data/map,
generate_Tnn_content/map, get_Tnn_content/map, BoxMode get_box_mode,
Save-Layer get/modify/update_tn_save_data, sync_tn_data, update_same_material_list,
state_init, clear_color_count, clear_e_err/e_err_set.

## BoxSave (alle 11)

Fehler-Ablage (get_err/clear_err/recode_err/save_err_tnn), Resume-Tnn,
Fan-Sicherung (save/restore_fan), Drucker-Accel-Sicherung (save/restore_printer_accel).

## Vollständige BoxAction-Liste (110, Abkürzungen aufgelöst siehe oben)

Tn_Extrude, blow, box_extrude_material[_part|_stage8], box_retrude_material[_filament_err_part],
break_box_extruder_retrude, break_move_to_cut, cal_flush_list, check_and_extrude,
check_connect, check_flush_temp_and_extrude, check_material_refill,
check_preloading_scrap_slot, check_printing_used_material, check_rfid_valid,
check_same_box, check_speed_and_extrude, communication_* (siehe Kern-Signaturen),
convert_scv/tcv, cut_hall_find_test/zero, cut_hall_test/zero, cut_material,
disable/enable_filament_sensor, disable/enable_heart_process, extrude_process_auto_retry_process,
extrude_process_stage7, extruder_extrude, extrusion_all_materials, filament_conflict_check,
filament_err_tighten_up_event, find_objs, generate_auto_get_rfid_func,
generate_check_preloading_scrap_slot_func, get_box_mode_and_wait,
get_filament_sensor_detect, get_five_way_sensor_detect, get_flush_len/max_temp/temp/velocity,
get_gcode_used_tnn, get_material_max_extrusion_speed/target_max_temp/target_temp/type,
get_slot_mask, go_to_extrude_pos, has_flushing_sign, is_material_available,
is_use_ending_material_flush, make_material_loose, material_auto_refill, material_flush,
material_volume_to_length, motor_send_data, move_to_cut/safe_pos, nozzle_clean,
power_loss_clean/restore, process_msg, quickly_wait_heating, reset_flushing_sign,
ret_parse_process, retrude_process_clear_flag, rfid_check, send_data, set_cool_temp,
set_flushing_sign, set_temp, timeout_process, update_Tnn_map, update_filament_pos,
update_same_material_list, update_state_process, update_tn_save_data,
use_ending_material_flag_clear, z_down/move/restore.

## Offene Vertiefungen

- CutSensor/DryRunInfo je 1 Methode (Signatur noch holen)
- MultiColorMeterialBoxWrapper (122 Methoden – Top-Level-Klasse)
- MockConfig-Injection (Artikel-Technik 1) für Config-Fläche – braucht Instanziierung
  (VORSICHT: kann Serial-Ports öffnen; nur mit Vorsicht auf dem Drucker)
- SmartSelf-Proxy (Technik 2) für Call-Graphen reiner Python-Methoden (z.B. cal_flush_list)

## Artikel-Techniken angewendet (13.10.2026, vgl. "Decoding Creality's locked Klipper fork")

- Live-Introspektion: alle Signaturen live auf dem Drucker verifiziert
  (send_data state=0xff vs motor_send_data state=0x00 – Static-Decode lag daneben)
- MockConfig-Injection: MultiColorMeterialBoxWrapper instanziiert gegen Mocks
  (Skript `/home/pro/k2-fw-live-1.1.7.0/mockconfig-box.py`, Fake-Serial /dev/ttyFAKE0,
  keine Seiteneffekte) – ERGEBNIS: 53 Config-Reads mit Defaults (u.a. box_first_clean_length=90,
  box_need_clean_length=80, Tn_retrude=-60, Tn_extrude=140, nozzle_volume=183,
  flush_multiplier=1, max_tube_length=3000) + Event-Handler
  (klippy:shutdown/ready, box:extrude_process_stage7/handle_motor_extruder_error/
  clear_box_color_count/command_error)
- SmartSelf-Proxy: BoxAction-Subklasse mit __getattribute__-Logging, cal_flush_list
  aufgerufen (Skripte /tmp/smartself[2].py, Drucker-seitig /tmp/) – ERGEBNIS:
  Zugriffskette `cal_flush_list > boxcfg`, dann Abbruch (Mock-Wert als float statt
  Liste) – Technik funktioniert, Voll-Durchlauf braucht realistische Wert-Fixtures
- Slot-Load-Trick: motor_control_wrapper.so gescannt – Top-Slot-Offsets -3992 (72×),
  -3808 (60×), -3872 (38×); KEIN isolierter ~24er-Cluster wie im Artikel (-3020),
  Namens-Mapping via MethodDef-Tabelle noch offen
- verify_sys_param dekompiliert (REA/Ghidra, FUN_000303c0 =
  `Motor_Control.verify_sys_param.parsing_response`): Response-Format-Kaskade
  (mehrere Parse-Versuche nacheinander), Längenprüfung `== 4`, Wertevergleich
  gegen geschriebenen Parameter – bestätigt das Artikel-Muster
  Write-then-Read-Verify (key803 bei Mismatch nach Retries)
- Belt-Schwellen aus Source bestätigt: ch_best_n=140, ch_best_error=0.05,
  mistake=0.02, adjustnum1/2=140/160 (live Config nutzt ch_best_error=0.15)
