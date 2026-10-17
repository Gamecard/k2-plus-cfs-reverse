# Motor-Kalibrierung-Befund 17.10.2026: systemid, encoder_calibrate (0x03), elec_offset_calibrate (0x04), flash_param (0x07)

Nur statische Analyse. Kein Drucker-, Pi- oder Moonraker-Zugriff, **nichts gesendet, nichts ausgeführt**.
Ergänzt `MOTOR-FW-BEFUND-2026-10-16.md`, `MOTOR-RESTE-BEFUND-2026-10-17.md` und `REA-MOTOR-BEFUND-2026-10-16.md`;
dort Belegtes wird nur zitiert, wenn es für den Ablauf nötig ist.

**Binaries:**
- Host: `rootfs-live/usr/share/klipper/klippy/extras/motor_control_wrapper.cpython-39.so`
  (SHA256 `d8191b77…b4b6e0f`, armhf, Cython). Adressen = **ELF-vaddr** (`llvm-objdump`), Ghidra/REA +0x10000.
- MCU: `fw/F008/mot0_022_C30-mot2_002_081.bin` (`mot2_002_081`, WCH QingKe RV32 + Xwchc), Adressen = **FW-VMA,
  Basis 0x3000** (wie MOTOR-FW-/MOTOR-RESTE-BEFUND). Objektbezeichner wie dort: `obj` = Hauptobjekt
  (RAM 0x20001e98), `ctrl` = obj->[4], `drv` = obj->[8], `enc` = obj->[0xc], `hal` = obj->[0x10],
  `par` = obj->[0x14] (Parametertabelle).

**Methode:** Ghidra 12.1.4 headless (isoliertes Projekt mit WCH-XW-Erweiterung aus der Vorarbeit, Dekompilat
aller 583 FW-Funktionen) + `llvm-objdump`-Gegenprobe der Kernaussagen. Host: eigene Cython-Annotation
(String-, Tupel-, Konstanten- und GOT-Auflösung) über `llvm-objdump`. **Float-Konstanten:** In der FW stammen alle
hier zitierten Fließkommawerte aus `flw`-Ladevorgängen, gegengeprüft per Byte-Lesen aus dem Image
(0x1b3c4 = 6,2831855; 0x1c21c = 63661,97; 0x19b44 = 0,5; 0x19b48 = 0,1; 0x1a32c = 2,0), dazu die Ganzzahl
25 000 000 (`lui 0x17d8; addi -0x7c0` @0xc6b0). Host-Float-Konstanten werden hier **nicht** verwendet
(nur Ganzzahlen 0/1/3/4/5/6/8/12/60, Strings, Tupel) – die `vmov.f64`-Falle betrifft diesen Befund nicht.

**Einstufung:** **B** = belegt (Code), **V** = Folgerung/vermutet, **ANNAHME** = nicht belegt, **offen** = statisch
nicht klärbar (mit Suchumfang). Alle Zahlenbeispiele für Messwerte sind **synthetisch**.

---

## Kurzfassung

| Funktion | Host (G-Code) | Cmd | FW-Handler | Bewegung / Strom | Flash | Antwort | Stock-Aufruf |
|---|---|---|---|---|---|---|---|
| **systemid** | kein G-Code, nur Dict-Eintrag | 0x0D | **keiner** (nicht registriert) | – | – | keine | nie |
| **encoder_calibrate_official** | `MOTOR_ENCODER_CALIBRATE_OFFICIAL NUM=1..10 [EXTRUDER=1]` | 0x03 | 0xf79c | Phasenprüfung (≤ ¾ el. Periode) + **Dauerdrehung bis ≈ 63 Umdrehungen, ≈ 2,5 U/s, ≤ ≈ 27 s**, Schutz AUS | `phase_order_invert` (MCU-Flash) + **2× Encoder-IC-EEPROM** | 1 B Status (0/4/8/0xC) | nie (nur manuell) |
| **elec_offset_calibrate** | `MOTOR_ELEC_OFFSET_CALIBRATE NUM=1..10 [EXTRUDER=1]`, `…_ALL` (NUM 1–4) | 0x04 | 0xf800 | Phasenprüfung + **1 Umdrehung vor, 1 zurück** (open-loop), Schutz AUS | `phase_order_invert` immer; bei „pass“ **alle Parameter** + Kalibrier-Record | 9 B: pass, Fehler °, Mittelwert | nie (nur manuell) |
| **flash_param** | `MOTOR_FLASH_PARAM NUM DATA ID PARAMS PARAMS_TYPE [EXTRUDER=1]` | 0x07 | 0xfcf4 | keine Bewegung, aber **Regelung kurz AUS/Neu-Init** (Sub 03/05), **Neustart** (Sub 01) | Sub 01, 03 | je Sub | nie (Boot nutzt 0x06) |
| encoder_calibrate (FW-intern) | – (nicht in Host-Tabelle) | 0x02 | 0xf76c | keine | **Encoder-IC-EEPROM**, falls Register abweichen | keine | FW selbst, 1× pro Hochlauf |

**Hauptbefunde:**
1. **0x0D systemid existiert in `mot2_002_081` nicht** (B): Die FW-Kommandotabelle hat keinen Eintrag 0x0D, der
   Host nutzt den Dict-Eintrag nirgends. Die eigentliche „Systemidentifikation“ (R/L-Messung,
   `system_id_RL_param_*`) läuft als **Motorcheck automatisch beim Hochlauf**, nicht per Bus-Kommando.
2. **0x03 und 0x04 bewegen den Motor open-loop mit abgeschalteten Schutzfunktionen** und blockieren die FW
   (keine Regelung, kein EN-Eingang, kein Bus) für ≈ 10 s (0x04) bzw. bis ≈ 40 s (0x03). Der Host prüft
   **keine** Vorbedingung (kein Homing, keine Druckprüfung, keine Entkopplung).
3. **Falsche Kalibrierung bleibt dauerhaft:** `phase_order_invert` wird bei 0x03 **und** 0x04 **unbedingt** in den
   MCU-Flash geschrieben – auch wenn die Bewegung blockiert war und die Elektrowinkel-Kalibrierung danach
   „failed“ meldet.
4. **Host-FW-Inkonsistenzen bei 0x07** (B, beide Seiten): uint32/int32 packt der Host **Big-Endian** (`'>I'`),
   die FW liest **Little-Endian**; bei DATA=5 wertet der Host das erste Antwortbyte als Erfolg/Fehlschlag, die FW
   schickt dort aber das **Wert-Echo**. Außerdem schreibt 0x07 Sub 05 nur **RAM** – dauerhaft erst mit Sub 03.

---

## 1. Rahmen und gemeinsame Bausteine

**Request** (B, `serial485.build_frame`, live für 0x06/0x08/0x0C/0x13 bestätigt):
`F7 | addr | len = n+3 | 00 | cmd | payload[n] | CRC` – Antwort gleich, mit STATUS-Bitfeld statt `00`
(MOTOR-FW-BEFUND §0). Die FW ruft genau den Handler mit passendem Cmd; eine generische Quittung gibt es nicht
(MOTOR-RESTE §1). Im Folgenden steht `<CRC>` als Platzhalter – **bewusst keine sendefertigen Frames**.

**FW-Registrierung** (B, @0x6100–0x63d8, `reg(slot, cmd, broadcast_ok, name, handler)`): 0x00–0x08, 0x0B, 0x0C,
0x0E, 0x0F, 0x11–0x16. **0x0D fehlt** (Slot 0x0D trägt Cmd 0x0F `version` – nicht verwechseln). Broadcast
(0xFF) nur für 0x01/0x0B/0x0E → 0x03/0x04/0x07 per 0xFF werden ignoriert (B).

**Host-Gemeinsamkeiten** (B): Alle drei Kalibrier-/Flash-Handler lesen `NUM` per `get_int('NUM', 1, minval=1,
maxval=10)`, verzweigen bei `EXTRUDER` in die Extruder-Variante (transparent über die Düsen-MCU,
`cmd_transparent_send`, Timeout `TIMEOUT_LONGER_TIME_MS`) und senden sonst über
`box_action.motor_send_data(addr, cmd, timeout, data=…, retries_en=False)` (GOT+0x250 = `_Py_FalseStruct`).
Ausgewertet wird `data = result[5 : result[2]−3+5]`.

**FW-Hilfsfunktionen** (B, Zuordnung aus Hauptkonstruktor @all.c 13151–13179 bzw. HAL-Init):

| Zeiger | Funktion | Bedeutung |
|---|---|---|
| `obj+0xa18` | FUN_c46e | **Phasenreihenfolge prüfen** („CheckMotorPhaseOrder“) |
| `obj+0xa1c` | FUN_c64a | **Encoder-Kalibrierung „official“** |
| `obj+0xa20` | FUN_e318 | Encoder-Register prüfen/setzen (Cmd 0x02) |
| `obj+0xa58` | FUN_ca48 | **Elektrowinkel-Offset-Kalibrierung** („ElecZeroOffset“) |
| `obj+0xa10` / `0xa14` / `0xa08` | FUN_d788 / d8ec / ba9e | Regelung ein/aus, alle Module neu init., Neustart (Watchdog) |
| `hal+0xcc` / `hal+0xd0` | FUN_4226 / 425e | Wartezeit **ms** / **µs** (SysTick, Ticks/ms bzw. /µs aus `hal+0x32`/`+0x43`) |
| `hal+0xf0` / `hal+0xec` | FUN_4464 / 444e | PFIC: IRQ 26 und IRQ 41 **sperren** / wieder freigeben |
| `par+0x10ec` | FUN_9cb4 | **einen** benannten Parameter in den Flash schreiben (Seite löschen + neu schreiben) |
| `par+0x10f4` / `0x10f8` | FUN_9d48 / 9d7a | **alle** Parameter in den Flash schreiben / alle aus dem Flash laden |
| `par+0x112c` | FUN_aade | Parameter im **RAM** setzen (aus Text, je Typ) |
| `par+0x1130` / `0x1138` / `0x1134` | FUN_ae9e / aeb4 / a0f2 | Kalibrier-Record lesen / schreiben / prüfen |

**V:** IRQ 41 = TIM1_UP (PWM-Update → FOC-Regel-Interrupt), IRQ 26 = EXTI4 – nach der CH32V30x-Vektortabelle
(ANNAHME, dass der Chip dieser Familie entspricht; Peripherie-Belegung nicht weiter verfolgt).

**Default-Werte der Kalibrierfelder** (B, Hauptkonstruktor FUN_b2c6, werden beim Laden aus dem Flash
überschrieben – Laufzeitwerte **nicht** im Image):

| Feld | Param (ID) | FW-Default | Verwendung |
|---|---|---|---|
| `obj+0xc4` | `ud_cal_set` (0x14) | 0,06 | Spannungsvektor Phasenprüfung + 0x04 |
| `obj+0xc8` | `encoder_calibtate_official_ud_cal_set` (0x17) | 0,06 | Spannungsvektor 0x03 (erst ×0,5) |
| `obj+0xcc` | `elec_angle_calibration_standar_deg` (8) | 15,0 | Pass-Grenze 0x04 (el. Grad) |
| `obj+0x12c` | `elec_offset` (9) | – | Ergebnis 0x04 (Encoder-Counts) |
| `obj+0xec` | `elec_offset_err_deg` (0x19) | – | Ergebnis 0x04 |
| `obj+0x5a` | `phase_order_invert` (10) | 1 | Ergebnis Phasenprüfung |
| `obj+0x7c` | – (fest) | 200 | Schritte pro Umdrehung bei 0x04 |
| `obj+0x48`/`0x4a`/`0x4c` | – (fest) | 10 / 10 / 1 | Messwerte je Schritt / ms je Schritt / ms je Messung |
| `obj+0x5c` | – (fest) | −1 | Drehsinn-Vorzeichen |
| `obj+0x60` | – (fest) | 6400 | Schritte pro Umdrehung bei 0x03 |
| Motorobjekt `+0` | – | **50** | Polpaare (FUN_7666; 1,8°-Hybridschrittmotor, V) |

Einheit von `ud_cal_set` (ANNAHME): normierte Spannung bzw. Tastgrad. Bei 24 V wären 0,06 ≈ 1,4 V und bei
`motor_R`-Default 2,0 Ω ≈ 0,7 A Phasenstrom – nur Größenordnung, nicht belegt.

---

## 2. systemid (Host-Tabelle 0x0D)

**Host (B):** `Motor_cmd['systemid'] = 0x0D` wird im Modul-Init aufgebaut (einzige Ladestelle des
Strings @0x126628: 0x109dc, Dict-Aufbau). Es gibt **keine** Funktion `motor_systemid`, keinen G-Code
`MOTOR_SYSTEMID` (vollständige Liste der `MOTOR_*`-Namen in der Stringtabelle geprüft) und keinen Webhook.

**FW (B):** In `mot2_002_081` ist 0x0D nicht registriert (§1) → der Dispatcher findet keinen Eintrag,
**keine Wirkung, keine Antwort**. Strings „systemid“/„system_id“ kommen im FW-Image nicht vor (beide
FW-Varianten `_021/_022` geprüft).

**Was „Systemidentifikation“ in dieser FW tatsächlich ist (B):** der **Motorcheck** `obj+0xa34` = FUN_e51c
(MOTOR-RESTE §4): R/L-Messung je Phase („[Aphase]/[Bphase]“, „R(ohm) | L(mH)“), Ergebnis 1–6 → Fehlerbits 9–13.
Er startet im Haupttask beim Hochlauf, wenn `obj+0x1d8` ≠ 0 (FUN_bde6). `motor_R`/`motor_L` schreibt er **nur**
in den Flash, wenn `obj+0x1da` ≠ 0; der Haupttask setzt `obj+0x1da = 0` (all.c 9732) → im Normalbetrieb **nur
Messung, kein Flash-Schreiben** (V, Setzer von `obj+0x1da ≠ 0` nicht gefunden). Die 10 Host-Parameter
`system_id_RL_param_*` gehören zur 227er-Host-Tabelle; die FW-Parametertabelle (FUN_68ac, ≈ 70 IDs)
registriert sie **nicht** (B) → per 0x06/0x07 nicht erreichbar (Antwort 0 Byte, V wie bei ID 226).

**Folge für den Nachbau:** Stub bleibt; 0x0D wäre wirkungslos, aber es gibt keinen Grund, es zu senden.
Offen: ob ältere/neuere Motor-FW 0x0D implementiert (nur `mot2_002_081`/`_021`/`_023` vorhanden).

---

## 3. encoder_calibrate_official (Cmd 0x03)

### 3.1 Host `motor_encoder_calibrate_official` (@0x64a80, B)
- G-Code `MOTOR_ENCODER_CALIBRATE_OFFICIAL NUM=1` – Hilfetext „编码器磁场一键校准“ (Encoder-Magnetfeld-Ein-Klick-
  Kalibrierung). `NUM` 1..10, sonst Extruder-Variante `motor_extruder_encoder_calibrate_official` (@0x90e58).
- `motor_send_data(Motor_Addr[NUM], Motor_cmd['encoder_calibrate_official'], 60, data=b'\x00',
  retries_en=False)` – Payload-Objekt @0x125594 ist **Bytes `00`** (Roh-Stringtabelle: Inhalt `00`, kein
  Unicode). Timeout-Argument **60** (Einheit V: Sekunden – andere Aufrufe übergeben Bruchteile).
- **Request:** `F7 <addr> 04 00 03 00 <CRC>`.
- Auswertung `status = data[0]`: **0x0C** → „校准成功“ (Erfolg), **0x00** → „没有运行校准“ (nicht gelaufen),
  **0x04** → „正在进行自校准“ (läuft noch), **0x08** → „运行校准失败“ (fehlgeschlagen), sonst „未知的状态返回值“.
  Ausgabe `respond_info("编码器磁场一键校准 status:%s")`. **Keine** weiteren Frames, kein Fehler-Raise, kein
  Neustart der Regelung.
- **Keine Vorbedingungsprüfung** (kein Homing-/Druckstatus, kein Motor-Aus).

### 3.2 FW-Handler 0xf79c → FUN_c46e + FUN_c64a (B)
Binärmodus (`obj+0x4ba == 1`), Payload wird **nicht** ausgewertet:
1. `hal+0xf0()` – IRQ 26/41 gesperrt (Regel-Interrupt aus, V).
2. **Phasenprüfung** FUN_c46e(obj, 0) (§5).
3. **Encoder-Kalibrierung** FUN_c64a(obj, 0, 2):
   - `protect_en` (`obj+0x3f4`) gesichert und **auf 0** (Schutzprüfung aus), Pin `drv+0x138` per `hal+200`
     geschaltet (Bedeutung offen), PWM-Freigabe `drv+4 = 1`.
   - Spannungsvektor `ud = encoder_calibtate_official_ud_cal_set × 0,5` bei el. Winkel 0, **500 ms** halten;
     dann volles `ud`, 10 ms.
   - **Encoder-IC-Register 0x0E ← 0x58** (`enc+0x48`, @0xc73c/0xc75c), Rücklesen, **EEPROM-Brennbefehl**
     (`enc+0x50`), **6000 ms** warten.
   - **Dauerdrehung open-loop:** pro Schritt +2π/6400 **mechanisch** (el. Winkel × 50 Polpaare,
     Polpaarzahl kürzt sich heraus), Schrittzeit `uVar10 = (int)(63661,97·2π/6400) = 62 µs`, Anzahl
     `N = 25 000 000 / 62 = 403 225` Schritte (B). Rampe: erste 10 % Schrittzeit von 2× auf 1×, letzte 10 %
     von 1× auf 2×. Im Mittelteil wird je Schritt der Encoder-Status gelesen (`enc+0x44` → `enc+0xe`); bei
     `(status & 0xF0) == 0xC0` **Abbruch** (fertig, ohne Bremsrampe).
   - Ende: `ud = 0`, **6500 ms** warten, `protect_en` zurück.
   - Antwort (Modus 2): **1 Byte** = `(enc+0xc >> 20) & 0xF` (Statusnibble), dann Reply-Builder.
4. Handler: `drv+4 = 0` (PWM aus), `ctrl+4 = 0` (Regler aus), `hal+0xec()` (IRQs frei).

**Antwort:** `F7 <addr> 04 <ST> 03 <status> <CRC>` mit status ∈ {0x0, 0x4, 0x8, 0xC} – deckt sich exakt mit der
Host-Auswertung (B, beide Seiten).

**Dauer/Bewegung (V aus den Konstanten):** ≈ 0,7 s Phasenprüfung + 0,5 s + 6 s EEPROM + Drehung
(nominal N·62 µs ≈ 25 s, mit Rampen ≈ 27,5 s; früher, sobald der Encoder „fertig“ meldet) + 6,5 s → **≈ 14–41 s**.
Drehzahl ≈ 1/(6400·62 µs) ≈ **2,5 U/s**, maximal ≈ **63 Umdrehungen** in eine Richtung.
Umgerechnet (ANNAHME Riemenscheibe 20 Z GT2 = 40 mm/U): **≈ 100 mm/s, bis ≈ 2,5 m Weg** – weit mehr als jeder
Achsweg. Am Extruder ≈ 63 Umdrehungen Fördergetriebe (Filamentweg nicht bestimmt).

**Was im Speicher landet:**
- MCU-Flash: `phase_order_invert` (aus Schritt 2, §5) – B.
- Encoder-IC: Register 0x0E = 0x58 per EEPROM-Brand (B) und das Ergebnis der IC-internen Selbstkalibrierung
  (V: die 6,5 s nach der Drehung entsprechen der Speicherzeit des ICs). **ANNAHME:** Encoder ist ein
  MagnTek-MT68xx-Typ mit Selbstkalibrierung (15-Bit-Wert, `AUTOCAL`-Register 0x0E, Status in Bits 7:6,
  „11“ = Erfolg, EEPROM-Programmierung mit ≈ 6 s Wartezeit) – passt zu allen Konstanten, Typenschild
  nicht belegt.
- `elec_offset` wird **nicht** neu bestimmt (V: nach einer Encoder-Linearisierung ist der alte Offset nicht mehr
  sicher gültig → Werksablauf vermutlich 0x03, danach 0x04).

**Textkonsole:** gleiche Routine mit Ausgaben („-------set calibration speed...“, „EEPROM write finished!“,
„encoder calibrate finished!“, `result: %#x`).

---

## 4. elec_offset_calibrate (Cmd 0x04)

### 4.1 Host `motor_elec_offset_calibrate` (@0x62a98) und `…_all` (@0x64494) (B)
- `MOTOR_ELEC_OFFSET_CALIBRATE NUM=1` – „电零位一键校准“ (Elektro-Nullpunkt-Ein-Klick-Kalibrierung).
  `NUM` 1..10, `EXTRUDER` → `motor_extruder_elec_offset_calibrate` (@0x97b9c, transparent).
- Senden wie 0x03: `motor_send_data(addr, 0x04, 60, data=b'\x00', retries_en=False)` →
  **Request** `F7 <addr> 04 00 04 00 <CRC>`.
- Auswertung: `data[0]` 0 → „校准失败“, 1 → „校准成功“, sonst „未知的状态返回值“; `struct.unpack('<f',
  data[1:5])` = maximale Abweichung (°), `unpack('<f', data[5:9])` = Mittelwert (pulse). Ausgabe
  „电零位一键校准 status:%s, 校准结果的最大偏差:%s°, 校准结果均值:%spulse“.
- `MOTOR_ELEC_OFFSET_CALIBRATE_ALL` („4个电机电零位一键校准“) = nur
  `gcode.run_script_from_command("MOTOR_ELEC_OFFSET_CALIBRATE NUM=1")` … `NUM=4` nacheinander (X, Y, Z, Z1;
  **ohne** Extruder, **ohne** Zwischenprüfung).
- Keine Vorbedingungsprüfung, kein Wiedereinschalten der Regelung.

### 4.2 FW-Handler 0xf800 → FUN_c46e + FUN_ca48 (B, Listing 0xf800–0xf87e)
1. **Nur Payload-Byte 0 = 0x00** wird ausgeführt; jedes andere erste Byte → sofortiger Rücksprung, keine
   Antwort (`0xf856–0xf85c`).
2. `hal+0xf0()`, **Phasenprüfung** FUN_c46e(obj, 0) (§5).
3. **ElecZeroOffset** FUN_ca48(obj, 0, 2):
   - `protect_en` gesichert und **auf 0**; `drv+4 = 1`; `ud = ud_cal_set` bei Winkel 0, 500 ms; dann
     `(obj+0x4a & 0x7fff)·2` = 20 ms.
   - **Vorwärts:** 200 Schritte, el. Winkel = i · 50·2π/200 → **genau 1 mechanische Umdrehung**. Je Schritt
     10 ms warten, dann 10 Encoder-Messungen à 1 ms, Mittelwert (mit Überlauf-Korrektur), Offset je Schritt =
     Mittelwert mod (32768/50 Counts). Bewegungsprüfung: Spannweite der Mittelwerte ≥ 10 Counts →
     `obj+0x224` („hat sich bewegt“) = 1.
   - **Rückwärts:** dieselben 200 Positionen absteigend; Ergebnis je Position mit dem Vorwärtswert gemittelt
     (mit ±Periode-Korrektur).
   - Auswertung (jede 4. Position): Max/Min/Mittelwert → `err` = (Max − Min)·360·50/32768 = **el. Grad**,
     `avg` in Counts.
   - **Pass**, wenn bewegt **und** `err < elec_angle_calibration_standar_deg` (Default 15°); sonst „failed“.
   - **Nur bei Pass:** `elec_offset = avg`, `elec_offset_err_deg = err`, **alle Parameter in den Flash**
     (`par+0x10f4`, bis 3 Versuche à 500 ms), danach **Kalibrier-Record** schreiben (§6).
   - Antwort (Modus 2): **9 Byte** `[pass u8, err f32 LE, avg f32 LE]` → `F7 <addr> 0C <ST> 04 … <CRC>`.
4. Handler danach: `drv+4 = 0`, `ctrl+4 = 0`, **`par+0x10f8` = alle Parameter aus dem Flash neu laden**,
   `hal+0xec()`.

**Folge von Schritt 4 (V, stark):** Bei „failed“ verwirft das Neuladen die RAM-Ergebnisse – **außer**
`phase_order_invert`, das Schritt 2 bereits unbedingt in den Flash geschrieben hat.

**Dauer/Bewegung (V):** ≈ 0,7 s Phasenprüfung + 0,5 s + 2 × 200 × ≈ 20 ms ≈ 8 s + 0,5 s + Flash
(≥ 1 s) → **≈ 10–13 s**. Bewegung **+1 Umdrehung, dann −1 Umdrehung**, langsam (≈ 0,125 U/s), open-loop
mit festem Spannungsvektor. ANNAHME 40 mm/U: ±40 mm Achsweg; Z: eine Spindelsteigung.

Die Host-Feldnamen in 0x13 (`elec_offset`, `phase_order_invert`, `elec_offset_err_deg`) sind genau diese
drei Ergebnisse; Stock liest IDs 9/10/25 bei jedem Boot nur (MOTOR-INIT §4), schreibt sie nie.

---

## 5. Gemeinsamer Schritt: Phasenreihenfolge (FUN_c46e, B, Listing bis 0xc5b0)

- `protect_en` gesichert/0, `drv+4 = 1`, `ud = ud_cal_set` bei el. Winkel 0, 500 ms; 20 ms.
- 4 Schritte: el. Winkel = `obj+0x5c`·k·π/2 (k = 0..3), je 10 ms, Encoder lesen.
- Steigen die 4 Werte (mit Vorzeichen `obj+0x5c`) **streng monoton** → `phase_order_invert = 0`, **sonst 1**.
- `ud = 0`, `drv+4 = 0`, 100 ms, dann **`par+0x10ec(par, "phase_order_invert")` – immer** (Listing
  `0xc59e–0xc5b0`, String @0x19e10 geprüft).
- Bewegung ≤ ¾ el. Periode = ≤ 5,4° mechanisch (bei 50 Polpaaren).

**Fehlerbild (V, stark):** Ist der Rotor blockiert (Endanschlag, Riemen klemmt, Last) oder springt der Encoder,
sind die Werte nicht monoton → **`phase_order_invert = 1` landet im Flash**. Die FOC regelt danach mit
vertauschter Phasenfolge (Mitkopplung → Durchgehen/Schleppfehler/Überstrom), bis jemand unter freier
Mechanik neu kalibriert. Plausibilitätsprüfung beim Boot (FUN_bda2) akzeptiert 0 **und** 1 – kein Schutz.

---

## 6. Kalibrier-Record (Sicherung) – B

- Adresse = `par+0x2c` = **Param-Block − 0x100 = 0x08022B00** (FUN_b07e), Länge **0x90 B**; Param-Block selbst
  0x08022C00, 0x400 B (MOTOR-RESTE §5). **Beide nicht im Image.**
- Schreiben FUN_aeb4: Kopf `5A 5A`, Inhalt ab +2: `phase_order_invert` (i16), `elec_offset` (f32),
  `elec_offset_err_deg` (f32), 8 Byte aus `obj+0x1e…0x25` (V: Geräte-UUID), Summen-Prüfsumme (u16, Byte-Summe
  über 0x8E B) bei +0x8E; Seite löschen, halbwortweise schreiben, vergleichen, **bis 3 Versuche**, Interrupts
  währenddessen gesperrt (`csr 0x800`).
- Prüfen FUN_a0f2 (Kopf + Summe); Lesen FUN_ae9e (memcpy).
- **Boot (FUN_d966):** Ist der Haupt-Block ungültig (Flash-Key/Version) oder fallen `elec_offset`/
  `phase_order_invert`/`err` aus dem Plausibilitätsbereich (`|elec_offset|` < 2·32768/50, `err` < 360°,
  `phase_order_invert` < 2), werden die Werte aus dem Record übernommen und alles neu gespeichert. Der Record
  ist damit die **Werkskalibrierungs-Sicherung**; nur 0x04-Pass überschreibt ihn.

---

## 7. flash_param (Cmd 0x07) / MOTOR_FLASH_PARAM

### 7.1 Host (B, `motor_flash_param` @0xa4e30, Kern `_motor_flash_param` @0x4b878)
- Parameter: `NUM` 1..10, `DATA` 1..10 (`validate_data_params`), `ID` (Default 0, Obergrenze-Konstante
  @0x126498 nicht aufgelöst), `PARAMS` (''), `PARAMS_TYPE` ('float'); `EXTRUDER` → `motor_extruder_flash_param`.
- Payload = `parse_num_to_byte(DATA)`; bei DATA ∈ {3, 4} `+= b'\x00'`; bei DATA ∈ {5, 6} `+= ID-Byte`; bei DATA = 5
  zusätzlich der Wert, gepackt nach `PARAMS_TYPE`: float `'f'` (nativ = LE), uint16_t `'<H'`, int16_t `'<h'`,
  **uint32_t/int32_t `'>I'`** (Big-Endian; Format-Objekt @0x126514 = Bytes `>I`, B).
- `motor_send_data(…, retries_en=False)`, Ausgabe „读取到电机返回值:%s“.
- DATA = 5: **`data[0] == 0` → „flash参数操作-写入:%s 失败“, `== 1` → „… 成功“** (B: Vergleiche @0x4c6b8/0x4dcdc).
- DATA = 6: Wert mit demselben Typ entpackt (`'f'`, `'<H'`, `'<h'`, `'>I'`), „flash参数操作-读取:%s value:%s“.

### 7.2 FW-Handler 0xfcf4 (B; Sub = Payload-Byte 0)

| Sub | Wirkung | Flash | Antwort |
|---|---|---|---|
| 01 | `use_default_param = 1` → **Flash** (`0x10ec`), alle Module neu init., **Neustart** (Watchdog) | ja | keine |
| 02 | Flash-Prüfung (Summe neu berechnen, mit `flash_checksum` vergleichen) | – | 5 B `[ok, sum_calc u16 LE, sum_flash u16 LE]` |
| 03 | Regelung aus → **alle Parameter in den Flash** → Regelung wie vorher an (nur ohne latched Fehler) | ja | `[01]` – **immer**, auch wenn das Speichern scheitert |
| 04 | alle Parameter aus dem Flash laden | – | keine |
| 05 | Wert ab Payload-Byte 2 (**LE**, Typ aus FW-Tabelle) → Text (`%d` bzw. Float mit **6 Nachkommastellen**) → RAM-Setter; Regelung aus, Neu-Init, wieder an (ohne latched Fehler) | **nein (nur RAM)** | Wert-Echo 2 B (16 Bit) bzw. 4 B (32 Bit/float) LE |
| 06 | Flash-Wert der ID lesen | – | 2 bzw. 4 B LE |
| 00, ≥ 07 | nichts | – | keine |

Unregistrierte IDs (Tabellen-Eintrag leer, FW-Tabelle 0x1140 B genullt): Setter tut nichts, Antwort ohne
Wert; Sub 05 schaltet die Regelung trotzdem aus/an (B). Kein Speicherzugriff außerhalb der Tabelle (ID ≤ 255
bleibt innerhalb, B). Der Neustart-Zweig in Sub 05 (`par+5 == 1`) ist im Dekompilat ohne Setzer (V: tot).

**Factory-Reset Sub 01 im Detail (B, FUN_d966):** Beim nächsten Hochlauf werden bei `use_default_param ≠ 0`
die **FW-Defaults** aktiv und wieder gespeichert; `elec_offset`, `elec_offset_err_deg`, `phase_order_invert`
werden aus dem Flash übernommen (bleiben erhalten). **Alles andere fällt auf FW-Defaults** – u. a.
`driver_board_amp_gain`/`R_of_current_sample_ohm` (20 × 0,02 Ω = 0,4 Ω statt live 1 Ω, MOTOR-RESTE §5),
PID-/LESO-Werte, Schutzgrenzen, Stall-Parameter. Stock schreibt beim Boot nur die in der Config gesetzten
IDs zurück (MOTOR-INIT §4) – **amp_gain/R gehören nicht dazu** → Strommessung/Überstromschutz danach um
Faktor ≈ 2,5 verschoben (V, stark).

### 7.3 Host ↔ FW-Inkonsistenzen (B, beide Seiten belegt)
1. **uint32/int32 Big- vs. Little-Endian:** Host packt/entpackt `'>I'`, FW liest/sendet LE → Werte
   byte-verdreht (Schreiben **und** Lesen). Betroffen in der FW-Tabelle nur `protect_err_code_backup` (0xbf)
   und `protect_warning_code_backup` (0xc0).
2. **DATA=5-Erfolgsmeldung:** Host erwartet Status 0/1 in `data[0]`, FW liefert das niederwertigste Wertbyte
   → „成功/失败“ sagt nichts über den Erfolg (bei vielen Werten gar keine Meldung).
3. **„flash_param“ schreibt bei DATA=5 nur RAM.** Dauerhaft erst mit DATA=3; ein Neustart vorher verwirft den Wert.
4. **Float-Genauigkeit:** Die FW geht über Text mit 6 Nachkommastellen → Werte < 5e-7 werden 0, kleine Werte
   verlieren Stellen (z. B. 1,2345678e-5 → 0,000012) (B: FUN_77d4(…, 6) → Setter).

### 7.4 Stock-Nutzung
`MOTOR_FLASH_PARAM` kommt in keiner Config, keinem Makro und keinem Live-Log vor; der Boot nutzt 0x06
(MOTOR-INIT §4, B). Die Live-Bezeichnung „update_flash_param“ ist 0x06.

---

## 8. Cmd 0x02 encoder_calibrate (FW-intern, nicht in der Host-Tabelle) – B

FUN_e318: nur wenn `obj+0x1d == 0` (Handler 0xf76c setzt 0 → erzwingt Lauf): `protect_en` 0, liest 7
Encoder-Register (Adressliste RAM 0x20002b20), vergleicht mit Sollwerten (0x20002b28; Register 2: Bit 3),
schreibt Abweichungen, **brennt bei Änderung das Encoder-EEPROM** (6000 ms), liest zurück, setzt
`obj+0x1d = 1`. **Keine Bewegung, keine Antwort.** Der Haupttask (FUN_bde6, all.c 9743) ruft das bei jedem
Hochlauf einmal auf. Host kennt 0x02 nicht; Stubs nicht nötig.

---

## 9. Wann ruft Stock das auf?

- **Nie automatisch** (B, bounded): `MOTOR_ENCODER_CALIBRATE_OFFICIAL`, `MOTOR_ELEC_OFFSET_CALIBRATE(_ALL)` und
  `MOTOR_FLASH_PARAM` stehen nur in der Wrapper-.so (rootfs vollständig per Binär-Grep durchsucht), in keiner
  Config, keinem Makro, keinem Live-Log 15./16.10.
- Sie sind **Nutzer-/Service-G-Codes** (Konsole/Fluidd, Hilfetexte vorhanden). **V:** Die Werkskalibrierung
  (Encoder-Magnetfeld + Elektro-Nullpunkt) erfolgt am Band; der Kalibrier-Record @0x08022B00 trägt sie.
- Die FW selbst führt beim Hochlauf nur 0x02-Registerprüfung und den Motorcheck (R/L) aus, **nie** 0x03/0x04.

---

## 10. Vorbedingungen (aus dem Code abgeleitet, V)

Für einen fachgerechten Lauf (nur zum Verständnis – **nicht** als Anleitung):
- Motor **mechanisch frei** und unbelastet (0x03 dreht bis ≈ 63 U; 0x04 ±1 U; Phasenprüfung braucht freie
  Drehung), d. h. bei X/Y Riemen/Achse entkoppelt, bei Z Spindel frei, beim Extruder kein Filament.
- Stabile Versorgung (Flash- und EEPROM-Schreibvorgänge mitten im Ablauf).
- Reihenfolge 0x03 vor 0x04 (V, s. §3.2).
- Kein Druck, Klipper-Bewegungen ruhend (Motor ist danach stromlos, s. §11).

---

## 11. Fehlerpfade

| Situation | Verhalten | Beleg |
|---|---|---|
| 0x04 mit Payload ≠ `00` | ignoriert, keine Antwort → Host-Timeout | B |
| 0x03/0x04/0x07 an 0xFF | ignoriert (kein Broadcast-Flag) | B |
| 0x0D | ignoriert | B |
| Rotor blockiert bei 0x03/0x04 | Phasenprüfung → `phase_order_invert = 1` **im Flash**; 0x04 „failed“ (keine Bewegung) | B (Code) / V (Folge) |
| 0x04 err ≥ Grenze | „failed“, RAM durch Flash-Neuladen verworfen, `phase_order_invert` bleibt | B |
| 0x04 Pass, Flash-Speichern scheitert 3× | Record wird trotzdem geschrieben; Text „Save All Variable To Flash Failed“ nur im Textmodus | B |
| 0x03 Encoder meldet nie „fertig“ | volle Laufzeit (≈ 27,5 s Drehung), Status ≠ 0xC → Host „失败/未知“ | B |
| Host-Timeout (60) kürzer als Lauf | nicht zu erwarten (Lauf ≤ ≈ 41 s) | V |
| Nach jedem 0x03/0x04 | `ctrl+4 = 0`, `drv+4 = 0` → **Motor stromlos**, bis Regelung per EN-Flanke 1→0 oder 0x05 `[02]` wieder an | B (Code) / V (Wieder-Ein) |
| Stromausfall während Flash-Schreiben | Boot-Pfad: Flash-Key/Prüfsumme → Defaults bzw. Record-Wiederherstellung | B (Pfad) / V (Wirkung) |
| 0x07 Sub 01 | Neustart; RS485-Adresse danach offen (Adresse kommt per Strapping-Broadcast 0x0E beim Klipper-Init) | offen |

---

## 12. Risiken / warum gesperrt

1. **Unkontrollierte Bewegung:** 0x03 dreht open-loop bis ≈ 63 Umdrehungen mit ≈ 2,5 U/s; an einer montierten
   Achse ist das ein **Crash in den Rahmen** (Riemenriss, Motor-/Riemenscheibenschaden), am Extruder Filament-
   förderung bzw. Zermahlen. 0x04 fährt ±1 Umdrehung ohne Rücksicht auf die Position.
2. **Ohne Schutz und ohne Not-Aus:** Während 0x03/0x04 sind `protect_en` = 0, der Regel-Interrupt gesperrt,
   der Haupttask blockiert → **EN-Eingang (M84/Notstopp) und Bus wirken nicht**, Überstrom-/Schleppfehler-
   Erkennung aus. Abbruch nur durch Spannung weg.
3. **Dauerhafte Fehlkalibrierung:** blockierter Rotor → falsches `phase_order_invert` im Flash (§5); erfolgreicher
   0x04 an belasteter Achse → schlechter `elec_offset` **inklusive** Überschreiben der Werks-Sicherung (§6).
   Folge: Drehmomentverlust, Schwingen, Durchgehen, Fehler 2/3/8 – Reparatur nur durch erneute Kalibrierung unter
   Werksbedingungen.
4. **Nicht-flüchtige Verschleiß-/Ausfallpunkte:** jede Ausführung brennt das Encoder-IC-EEPROM (0x03: 2×,
   0x02: bei Abweichung) und mehrere MCU-Flash-Seiten; Stromausfall währenddessen kann den Encoder-IC falsch
   konfiguriert hinterlassen (ANNAHME: begrenzte EEPROM-Zyklen des ICs).
5. **Positionsverlust nach dem Lauf:** Motor bleibt stromlos, Klipper weiß davon nichts (keine Rückmeldung im
   Host-Code) → Schritte gehen verloren, Homing-Bezug ungültig (V).
6. **0x07:** Sub 01 = Werksreset aller Nicht-Kalibrier-Parameter inkl. Stromskala (§7.2) + Neustart; Sub 05 mit
   Host-Typ uint32/int32 schreibt byte-verdrehte Werte; jede Sub 03/05 schaltet die Regelung kurz ab
   (bei Fahrt: Ruck/Schrittverlust) und bei latched Fehler **nicht** wieder ein.
7. **Klipper-Blockade (ANNAHME):** Falls `motor_send_data` synchron wartet, steht Klippy bis ≈ 40 s.

**Konsequenz für den Nachbau:** `systemid`, `encoder_calibrate`, `elec_offset_calibrate`, `set_flash_param`
bleiben `NotImplementedError` (keine Frame-Erzeugung). Empfehlung (nicht umgesetzt, anderer Dateibereich):
`serial485.check_tx_frame` sollte Cmd 0x03, 0x04 sowie 0x07 mit Sub 01/03/05 hart sperren (analog 0x0B).
Lesend wäre nur 0x07 Sub 02/06 vertretbar (`get_flash_param`, separater Schritt, live nie geprüft).

---

## 13. Offen (mit Suchumfang)

| Punkt | Warum offen | nächster Schritt |
|---|---|---|
| Encoder-IC-Typ, Bedeutung Reg. 0x0E = 0x58, Pin `drv+0x138` | kein Schaltplan/Datenblatt-Abgleich | Bauteilaufdruck am Ersatzboard; Datenblatt-Abgleich |
| Einheit/Laufzeitwert `ud_cal_set`, `encoder_calibtate_official_ud_cal_set`, `elec_angle_calibration_standar_deg` | Flash-Block nicht im Image | lesend 0x06 `[02, 0x14/0x17/0x08]` (nie gesendet) |
| Obergrenze `ID` in MOTOR_FLASH_PARAM | Konstante @0x126498 nicht aufgelöst | Modul-Init weiter auflösen |
| Webhook `motor_param_modifi_data` | nicht Teil dieser Analyse, vermutlich schreibend | eigener Befund |
| Verhalten anderer Motor-FW-Versionen bei 0x0D | nur `mot2_002_081` vorhanden | weitere Images |
| Timeout-Einheit `motor_send_data` (60) | Implementierung in `box_wrapper` | box_wrapper-Analyse |

---

## 14. Arbeitsdateien (lokal, nicht im Repo)

Annotierte Host-Disassembly je Funktion (`enc_cal_off.asm`, `elec_cal.asm`, `elec_cal_all.asm`,
`ext_enc_cal.asm`, `ext_elec_cal.asm`, `flash_param.asm`, `flash_param_gc.asm`), vollständige Host-Disassembly,
angepasste Annotations-Skripte. FW-Dekompilate/Listing aus der Vorarbeit (MOTOR-RESTE §9) wiederverwendet.
