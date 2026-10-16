# Motor-FW-Befund: Closed-Loop-Motor-FW + Host-Parser (16.10.2026)

Nur statische Analyse, kein Druckerzugriff (REA/llvm-objdump). Übernommen aus dem lokalen
Arbeitsbefund (REA-Motor-Befund 2), Pfade neutralisiert. Arbeitsdateien lokal, nicht im Repo:
Disassembly mit XW-Erweiterung, annotierte Handler (GET, protection, 0x13–0x16), String-Liste (VMA),
lui/addi-Referenzen, Hilfsskripte. Host-Adressen = ELF-vaddr von `motor_control_wrapper.cpython-39.so`
(wie in `REA-MOTOR-BEFUND-2026-10-16.md`). Einarbeitung: PROTOKOLL-485.md §3.1 „NACHTRAG (4)“,
`nachbau/motor.py` (`VARIABLE_TABLE`, `decode_protection`, `decode_motor_status`,
`read_err_detail`/`read_stall_detail`/`read_pos_samples`/`read_spd_samples`).

**Live-Nachtrag 16.10.2026:** Die in §3 geforderte Gegenprobe ist gemacht – idx 12 zeigt bei Fahrt −X
**0** (spd_fdb −1,81 rad/s), bei +X **1** und hält im Stillstand die letzte Richtung (0 nach dem
Einschalten) → **DIR-Eingang bestätigt** (`LIVE-ABFRAGEN-2026-10-16.md`, letzter Abschnitt).

**Prüfvermerk 16.10.2026 (Host-Konstanten):** Die frühere Host-Konstantenauflösung hatte 13 Float-Konstanten
falsch (Liste in `MOTOR-INIT-BEFUND-2026-10-16.md` §7). Dieser Befund nutzt aus ihr nur **int 8 @`0x1260f0`**
(`zfill(8)`, §1) – nicht betroffen. Die übrigen Konstanten hier (z. B. −1.0 @`0x19d14`) stammen aus der
**Motor-FW**, nicht aus der Host-Tabelle. Keine Korrektur nötig. Ergänzend belegt der Motor-Init-Befund §5 mit
dem Handler 0x0E Sub 01 (@`0x107b4`) das **Adress-Strapping**: Adresse = 0x81 + 2·`pins+0x108` + `pins+0x110`
(STEP/DIR) → X 0x81, Y 0x82, Z 0x83, Z1 0x84.

## 0. Motor-Firmware gefunden
- Dateien `fw/F008|F012/mot{0,1,2}_022_C30-mot2_002_081.bin` (116412 B, alle SHA `67b364a6…`, untersucht)
  und `fw/F008|F012/motor/mot*_02{1,3}_C30-mot2_002_081.bin` (116396 B, SHA `92ff3130…`). Bis auf Binärrauschen
  haben beide denselben String-Satz, die Logik ist vermutlich gleich. Kennung im Image bei Datei-Offset 0x200:
  `mot2_002_081`. Einen String „1.9.7“ gibt es nicht. Die Version kommt als Zahlen über `fireware ver: %d.%d.%d.%d`.
- **CPU: RISC-V WCH QingKe (CH32V30x-Klasse), rv32imafc + Xwchc** (belegt).
  - Startup `j reset; 12×nop; ebreak` ist WCH-typisch.
  - Ohne `+xwchc` disassembliert llvm-objdump die Opcodes 0x239c/0x383a als „unknown“, mit `+xwchc` als `lbu`/`lhu`.
  - `0x1FFFF720` dient als Kalibrierwert für den Temperatursensor.
- **Basisadresse 0x00003000** (belegt). Der Zeigertest über Wort-Tabellen ergibt 0 Treffer, weil RISC-V Adressen per lui/addi bildet.
  Die lui/addi-Referenzen auf String-Anfänge zählen so: 0x0 → 7, **0x3000 → 303**, 0x4000 → 7, 0x08000000 → 0, 0x08003000 → 0.
  Die Default-Handler `j .` in der Vektortabelle (0x36a0…) liegen bei Datei-Offset 0x6a0.
  Vor der App liegt also ein Bootloader von 12 KB (Flash-Alias 0).
- Die FW hat eine Text-Konsole (`help`, `Invalid datapack.` …) und einen Binärmodus. Der Binärmodus gilt, wenn `obj+0x4ba == 1`.
  Jeder Handler verzweigt danach. Das Payload-Byte 0 steht in `*(obj+0x6cc)`.

### Befehlsregistrierung @0x6100–0x63d8 (belegt): `reg(id, cmd, flag, name, handler)`
| Cmd | Name (FW) | Handler | | Cmd | Name | Handler |
|---|---|---|---|---|---|---|
| 00 | help | 0xf6bc | | 0b | boot | 0x104e6 |
| 01 | reboot | 0xf724 | | 0c | protection | 0x1053c |
| 02 | encoder_calibrate | 0xf76c | | 0e | rs485_addr | 0x107b4 |
| 03 | encoder_calibrate_official | 0xf79c | | 0f | version | 0x109ba |
| 04 | elec_offset_calibrate | 0xf800 | | 11 | emergency_pin_mode (Host: stall_mode) | 0x10c96 |
| 05 | control | 0xf880 | | 12 | dev_uuid | 0x108b8 |
| 06 | sys_param | 0xf9be | | 13 | err_detail_data | 0x10a7e |
| 07 | flash_param | 0xfcf4 | | 14 | cut_detail_data | 0x10b22 |
| 08 | get | 0x102d0 | | 15 | pos_data | 0x10bca |
| | | | | 16 | spd_data | 0x10c30 |

### Antwortrahmen (Builder @0xdd6e, belegt)
`F7 | addr(obj+0x1c) | len = n+3 | STATUS | cmd | payload[n] | CRC`.
Der Host schneidet passend dazu `result[5 : result[2]-3+5]` aus.
**Das STATUS-Byte (Byte 3) ist ein Bitfeld:**
- bit0 = Stall-Flag (`obj+0x40`, = MOTOR_GET idx14)
- bit1 = latched Fehlercode ≠ 0
- bit2 = latched Warncode ≠ 0

Im Request steht an dieser Stelle `00`.

## 1. Cmd 0x0C protection: Byte-Layout — BELEGT (FW und Host)
FW-Felder (Objekt `s0`):
- `0x428` = Fehler roh, `0x42c` = **Fehler latched = 0x428 & Maske(0x400)** (`0xdec0`, `0xdff0`)
- `0x438` = Warnung roh, `0x43c` = **Warnung latched = 0x438 & Maske(0x404)** (`0xe0e6`)
- `0x430`/`0x440` = Vorzyklus
- `0x434`/`0x444` = Backup, bei Änderung kopiert (`0xba46`, `0xba56`). Wahrscheinlich die Flash-Parameter
  `protect_err_code_backup` und `protect_warning_code_backup`, Typ uint32_t (vermutet).

Sub-Kommando = Payload-Byte 0 (Sprungtabelle @0x1bd30, 12 Einträge):
| Sub | Wirkung | Antwort |
|---|---|---|
| 00 | nichts | – |
| 01 / 02 | protect_en = 0 / 1 (`obj+0x3f4`, ruft Param-Update „protect_en“) | – |
| 03 / 04 | protect_report = 0 / 1 (`obj+0x3f6`) | – |
| **05** | **löscht** 0x428, 0x42c, 0x430, 0x438, 0x43c, 0x440 (Backups 0x434/0x444 bleiben) | – |
| 06 | Fehler-Backup | 4 B |
| 07 | Fehler latched | 4 B (*) |
| 08 | Warn-Backup | 4 B |
| 09 | Warn latched | 4 B (*) |
| 0A | Fehler-Backup + Warn-Backup | 8 B |
| **0B** | **Fehler latched (B0–3) + Warn latched (B4–7)** | 8 B (*) |

Die Bytes werden byteweise ab der niedrigsten Adresse gesendet: **u32 Little-Endian**.
Byte 0–3 = Fehlercode, Byte 4–7 = Warncode.
(*) Seiteneffekt bei 07/09/0B, belegt @0x10616: Steht `stall_mode` (`obj+0x32`) auf **2** (Notfall-Ausgang),
wird nach der Antwort der Notfall-/Stall-Pin deaktiviert (HAL+0xc8, Pin `pins+0x120`).
Gesetzt wird er im Schutz-Zyklus @0xba7a bei jeder Änderung der latched Codes.
Abfragen mit 0B quittiert damit die Notfall-Leitung, die Codes selbst bleiben unverändert.

Bit-Belegung in der FW (belegt, `0xde18–0xe0ae`), passend zu motor_error_dict Bit n → key781+n:
| Bit | Beleg im Code |
|---|---|
| 0 | 0x1 Encoder-Sprung |
| 1 | 0x2 Encoder-Lesefehler |
| 2 | 0x4 Spitzenstrom (`0x454` vs. Limit) |
| 3 | 0x8 Dauerstrom |
| 4 | HW-Überstrom (Setzer nicht lokalisiert) |
| 5 | 0x20 Drehzahl |
| 6 | 0x40 Positionsgrenze |
| 7 | 0x80 Positions-Cmd-Sprung |
| 8 | 0x100 Schleppfehler |
| 9 | 0x200 ValueAbnormal |
| 10 | 0x400 instabil |
| 12 | 0x1000 |
| 13 | 0x2000 |

Die FW-Namenstabelle @0x1b3d4 (Stride 0x1e) läuft in derselben Reihenfolge:
EncoderMutation … MotorCheckOtherErr.
Warnungen (Stride 0x19, @0x1b794): bit0 CommunicationErr (Setzer nicht lokalisiert),
**bit1 Unterspannung** (`0x480` < min, `0xe0fc`), **bit2 MCU-Übertemperatur** (`0x484` > max, `0xe0da`).

Host-Parser (belegt; motor_auto_check_protection `0x56788–0x56d90`; identisches Muster in
check_protection_after_home `0x5babc`, extruder_motor_err_state_query `0x6d440`, check_protection_code `0xd6cbc`):
```
data = result[5:result[2]-3+5]
s = ''.join(bin(b)[2:].zfill(8)[::-1] for b in data)   # zfill(8): CONST int 8 @0x1260f0; [::-1]: Slice 0x125654
err_bits, warn_bits = s[:32], s[32:]                     # 0x56b28 / 0x56b58
# Index i, an dem '1' steht → motor_error_dict[i] bzw. motor_warning_dict[i]
```
Daraus folgt: Index i = 8·Byte + Bit, also genau die LE-u32-Bitnummer. Fehler = u32 aus Byte 0–3 (Bits 0..13 genutzt),
Warnung = u32 aus Byte 4–7 (Bits 0..2).
Hinweis: `bytes_to_int` (`0x1e658`) arbeitet mit `byteorder='big'`, betrifft aber nur andere Felder, nicht diesen Pfad.

## 2. Cmd 0x13–0x16 — alle LESEND/SICHER
Die FW liest bei allen vier **keine Payload** (kein Zugriff auf `0x6cc`; belegt). Was der Host anhängt, ist egal.
Ein G-Code existiert nicht. Der Host stellt sie als **Webhook-Endpoints** bereit (`register_endpoint` in `__init__` `0xceb40…0xcf8d4`;
Parameter `num`(2), `code`(0), `dev_uuid`(0)).

| Cmd | FW-Verhalten | Antwort | Host |
|---|---|---|---|
| 0x13 err_detail | Liefert den **Snapshot zum letzten Fehlerereignis**. Erfasst @0xc172 bei jedem gesetzten, in Maske 0x400 freigegebenen Fehlerbit: err_code = 1<<n, Encoder roh 0x84/0x88, obj+0xf0, obj+0x100, ctrl+0xc0, ctrl+0x60c/0x610 (id/iq), drv+0x48/0x4c (V/°C), ctrl+0x124 (pos-PID-Fehler), dazu 8-B-Union je nach Fehlertyp. Der Handler kopiert nur statische Felder (uuid, elec_offset 0x12c, phase_order 0x5a, elec_offset_err 0xec) in den Puffer; das ist idempotent. | 74 B = `<I8BfHfiiffffffffi` (66 B) + 8 B Union (`ERR_CODE_TO_UNION`) | `motor_err_detail_data` `0x3a3f0`; ruft danach `_motor_pos_spd_sample_data` (0x15 + 0x16) |
| 0x14 cut_detail | **Snapshot beim Stall-Ereignis** (Stall-Callback @0xbf44, aufgerufen von der Stall-Erkennung @0xb87e/0xb8f0). Das ist der Messpunkt von MOTOR_CHECK_CUT_POS. | 74 B; Host `<I8BfHfiiffffffffif` (70 B) wertet die ersten 70 aus | `motor_cut_detail_data` `0x35c0c`; dazu `_motor_no_cut_detail_data` (MOTOR_GET idx5–8) |
| 0x15 pos_data | Ringpuffer mit **10 Proben à 16 B**, älteste zuerst. Kopf `obj+0x88c` (gemeinsam mit 0x16). Geschrieben von @0xb91c (registriert @0xf22e) nur bei `ctrl+4 == 1`. **Kein Start, kein Löschen.** | `0A` + 10 × `<ffff` = 161 B | Felder (Host): **fdb_pos, leso_pos, target_pos, leso_err** = FW obj+0xf0, ctrl+0xbc, ctrl+0x50, ctrl+0x4a0 |
| 0x16 spd_data | wie 0x15, eigener Ring 0x92e | `0A` + 10 × `<ffff` | **fdb_spd, leso_spd, target_spd, leso_in_u** = obj+0x100, ctrl+0xc0, ctrl+0x54, ctrl+0x4bc |

~~Die Zuordnung „Hostname-Reihenfolge = FW-Byte-Reihenfolge“ ist vermutet; sie setzt eine zip/unpack-Reihenfolge voraus.~~
(live bestätigt, s. Nachtrag unten.)
Die Namen selbst sind belegt (Strings @0x38290-Block).
Snapshot-Inhalte bleiben bis zum nächsten Ereignis erhalten. `protection [05]` setzt sie nicht zurück.

### Live-Nachtrag 16.10.2026 (17:26 Log-Uhr): 0x13–0x16 LIVE BESTÄTIGT
Motor X `0x81`, Stillstand nach `M84`, Klipper durchgehend `ready`; aufgerufen über die Webhook-Endpunkte
`motor_err_detail_data` (Bus: 0x13 → 0x15 → 0x16) und `motor_cut_detail_data` (0x14 → MOTOR_GET idx 5–8) mit
`{"num": 1}` – genau die oben statisch gefundene Host-Kette. `motor_param_modifi_data` (vermutlich schreibend) wurde
nicht angefasst. Details/Rohdaten ohne UUID: `LIVE-ABFRAGEN-2026-10-16.md` „0x13–0x16 live“, PROTOKOLL-485.md §3.1
NACHTRAG (5).
- **Payload:** Der Host sendet `81 04 00 1x 02` (Payload `[02]`); die Antworten passen zur Aussage „FW liest keine Payload“.
- **Längen:** 0x13/0x14 len `0x4d` → 74 B, 0x15/0x16 len `0xa4` → 161 B (Zählbyte `0x0A`), STATUS 00 – wie statisch.
- **Format/Reihenfolge:** Host-JSON = Dekodierung mit `<I8BfHfiiffffffffi` (+`f` für 0x14) in der Host-Feldreihenfolge.
  0x14 live z. B. `encoder_fdb` 4421, `board_detect_power_supply_V` 24,365, `board_detect_temperature_C` 50,32,
  `pos_loop_pid_err` 0,01211; das 70-B-Zusatzfloat heißt im Host **`param_peak_overcurrent_A`** (live 0,012).
- **0x13 ohne Fehler seit Boot:** err_code 0, Messfelder 0, nur uuid/elec_offset (123,58)/phase_order (0)/
  elec_offset_err (3,867°) gefüllt → bestätigt „Handler kopiert nur statische Felder nach“.
- **0x15:** Feld 3 `target_pos` (2,159847) und Feld 4 `leso_err` (0) eindeutig; Feld 1/2 im Stillstand identisch.
  **0x16:** alle ≈ 0, Reihenfolge nur konsistent.
- Offen bleibt: Union-Inhalte bei err_code ≠ 0; Bedeutung von `param_peak_overcurrent_A` (0,012 bei err_code 0).

## 3. MOTOR_GET (Cmd 0x08): FW-Tabelle — BELEGT (Sprungtabelle @0x1bce4, 19 Einträge, idx 0..18)
`ctrl = obj->[4]` (Regler), `drv = obj->[8]` (Treiberplatine). Die Regler-Update-Routine @0x82b6–0x83fe schreibt
vier PI-Strukturen im Abstand 0xcc, jeweils **+0 = ref, +4 = fdb**:
| idx | Feld | Deutung |
|---|---|---|
| 0 | konstant 0 | – |
| **1** | ctrl+0x118 | **pos_ref** (= ctrl+0x50 target_pos + 0x84, begrenzt durch 0x10c) — belegt |
| **2** | ctrl+0x1e4 | **spd_ref** (= Pos-PI-Ausgang 0x120 + FF 0x88 + 0xa8, begrenzt durch 0x1d8) — belegt |
| **3** | ctrl+0x2b0 | **id_ref** (= 0x664 + 0xac + 0x8c, begrenzt) — belegt |
| **4** | ctrl+0x37c | **iq_ref** (= 0x668 + 0x90, begrenzt durch 0x370) — belegt |
| 5 | ctrl+0x11c | pos_fdb = ctrl+0xbc = **leso_pos**, Single-Turn-Unwrap mit ±π/2π (Konstanten @0x1b7e0/0x1b3c4) |
| 6 | ctrl+0x1e8 | spd_fdb = ctrl+0xc0 = **leso_spd** = `spd_fdb_ctrl` |
| 7 / 8 | ctrl+0x2b4 / 0x380 | id_fdb / iq_fdb (= ctrl+0x60c / 0x610) |
| **9** | ctrl+0x7c | **Ia [A]** = drv-Funktion @0x5f16: ADC-Kanal 0 − Offset(drv+0xc), ×Vorzeichen(drv+6), Umrechnung @0x5ef2: raw·Vref·(1/4096)/(Gain·R) — belegt |
| **10** | ctrl+0x80 | **−Ib [A]** = drv-Funktion @0x5f52 (ADC-Kanal 1), × −1.0 (Konstante @0x19d14, @0xbb4a) — belegt |
| 11 | obj+0x7bc (u32) | Schrittpuls-Zählerstand. ×Skala(0x7d0) ergibt den Soll-Winkel `0xd0`, bei spd < 0 gespiegelt auf 2π − x (@0xb7dc/0xbb7a). Live 0..3199 = 3200 Pulse/U |
| **12** | obj+0x7a2 (u16) | **Pegel eines GPIO-Eingangs** (`pins+0x110`, @0xe26a). Bei einer Flanke wird der Zähler gelatcht und die Zählrichtung umgeschaltet (HAL 0x148/0x14c). Daraus folgt: **DIR-Eingang** (FW: vermutet, stark; **live bestätigt** 16.10.) |
| 13 | obj+0x7a6 (u16) | GPIO `pins+0x118` = **EN-Eingang** (vermutet): Pegel 1 → Regelung aus, sonst ein, wenn kein Fehler latched (@0xb7a8–0xb80e) |
| 14 | obj+0x40 (u16) | Stall-Flag: \|iq_ref\| > Schwelle bzw. \|pos-PID-Fehler ctrl+0x124\| > Schwelle (@0xb842); = STATUS bit0 |
| 15 / 16 | obj->[0xc]+0x24 / +0x2c (u32) | offen |
| 17 | drv+0x4c | `board_detect_temperature_C` (MCU-Temperatursensor, Kalibrierung 0x1FFFF720) |
| 18 | drv+0x48 | `board_detect_power_supply_V` (ADC × Vref × `power_voltage_sample_gain`) |
| > 18 | – | kein Fehler: es wird der veraltete Wert aus dem Scratch `gp-0x7b4` gesendet |

Die Typwahl des Hosts passt: float für {1–10, 17, 18}, int für {11–16}.
Abgleich mit den Live-Werten:
- idx1 läuft mit der Position, idx2 ≈ Sollgeschwindigkeit, idx3 = 0 (id_ref = 0 bei FOC ohne Feldschwächung), idx4 ≈ iq_fdb: **alle Deutungen bestätigt**.
- idx9/idx10 sind **Phasenströme, keine Spannungen und keine Regelabweichung.** Im Stillstand liegen 0,00161/0,00081 A
  ≈ 2/1 LSB, wenn Gain·R ≈ 1 Ω ist (3,3 V/4096 = 0,806 mA/LSB). Gain·R = 1 Ω ist vermutet und lässt sich über die
  Flash-Parameter `driver_board_amp_gain` und `driver_board_R_of_current_sample_ohm` lesend prüfen.
- Eine ud/uq-Spannung gibt es im GET nicht. Die Regelabweichung des Lagereglers steckt nur in err_detail (`pos_loop_pid_err`) und als `leso_err` in 0x15.
- idx12 ist **kein** Run-Flag. Es ist der Pegel des Richtungseingangs; dass er in Fahrt 1 und im Stillstand 0 zeigt, ist richtungsabhängig.
  Gegenprobe: Bei Fahrt in Gegenrichtung muss idx12 = 0 zeigen. → **Gemacht 16.10.: −X = 0, +X = 1, bestätigt.**

## 4. Einheit spd_fdb (idx6) — rad/s, BELEGT
- FW-Parameternamen `protect_speed_over_limit_rad_s`, `stall_pos_err_rad`, `protect_pos_tracking_error_maximum_rad`.
- Die Position ist in rad mit Unwrap über π/2π gerechnet.
- idx6 ist der LESO-Drehzahlschätzer (`spd_fdb_ctrl`, `leso_spd`), keine Skalierung im Host.
- Live: 1,53–1,57 bei 1,571 Soll.

## 5. Einstufung lesend vs. zustandsändernd
- **Lesend/sicher:** 0x08 (alle idx), 0x13, 0x14, 0x15, 0x16, 0x0C mit 06/08/0A.
  0x0C 07/09/0B sind lesend, haben aber den Seiteneffekt „Notfall-Pin freigeben“, nur bei stall_mode 2.
- **Zustandsändernd:** 0x0C 01–05 (protect_en, protect_report, Codes löschen) und 0x11 (stall/emergency_pin_mode setzen).
