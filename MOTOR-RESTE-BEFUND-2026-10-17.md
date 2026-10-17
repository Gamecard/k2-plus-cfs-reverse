# Motor-Reste-Befund 17.10.2026: offene Punkte der Closed-Loop-Motor-FW (mot2_002_081)

Nur statische Analyse. Kein Drucker-, Pi- oder Moonraker-Zugriff, nichts gesendet, kein Code im Nachbau geändert.
Ergänzt `MOTOR-FW-BEFUND-2026-10-16.md` (dort Belegtes wird nicht wiederholt, nur Lücken und Korrekturen).

**Firmware:** `fw/F008/mot0_022_C30-mot2_002_081.bin` (SHA256 `67b364a6…`, identisch mot1/mot2). Basis 0x3000,
WCH QingKe RISC-V (rv32imafc + Xwchc) wie im MOTOR-FW-BEFUND. **Host:** `motor_control_wrapper.cpython-39.so`
(ELF-vaddr), `belt_mdl-live.py` (Klartext), `rootfs-live/usr/bin/mcu_util_485` (ARM, ELF-vaddr).

**Werkzeuge / Methode (neu gegenüber der Vorarbeit):** Ghidra 12.1.4 headless in einer **eigenen, isolierten
Kopie** mit zwei lokalen Ergänzungen: (1) SLEIGH-Konstruktoren für die WCH-„XW“-Kompaktbefehle
(`qk.c.lbu/lhu/sb/sh` und die SP-Varianten; Bitlayout per Brute-Force gegen `llvm-objdump --mattr=+xwchc`
ermittelt), (2) Call-Fixups für die gcc-`-msave-restore`-Millicode (0x3220/0x3228/0x3236/0x3250 bzw.
0x325c/0x3260/0x326a/0x3274). Damit dekompiliert die FW sauber (583 Funktionen, Handler mit echten
Parametern `obj`, `args`). Eigenes Projekt `arbeitsdateien-lokal/re-motor-1017/proj/motfw` (+ `mcuutil`),
bestehende Projekte nicht geöffnet. Gegenprobe jeder Kernaussage im `llvm-objdump`-Listing (`mot.asm`).

**Adressen** = Motor-FW-VMA (Basis 0x3000, wie im MOTOR-FW-BEFUND). **Objekte** (Konstruktor/Init belegt,
FUN_b2c6 / FUN_f174): `obj` = Hauptobjekt RAM 0x20001e98, `ctrl` = obj->[4] (0x20000000), `drv` = obj->[8]
(Treiberplatine, 0x20000968), `enc` = obj->[0xc] (Encoder, 0x20000af8), `hal` = obj->[0x10] (0x20001ccc),
`par` = obj->[0x14] (Parametertabelle, 0x20000b60). Funktionszeiger im Objekt: `obj+0xa10` = Regelung
ein/aus (FUN_d788), `obj+0xa44` = Antwort senden (FUN_dd6e), `obj+0xa00` = Frame-Dispatcher (FUN_d5c2).

**Einstufung:** **B** = belegt (Code, ggf. + Log), **V** = Folgerung/vermutet, **ANNAHME** = nicht belegt,
**offen** = statisch nicht klärbar (mit Suchumfang).

---

## Kurzfassung

| Nr | Frage | Status | Kernaussage |
|---|---|---|---|
| 1 | 0x05 `[01]`/`[02]` | **beantwortet** (B) | Sub 01 = Regelung AUS, 02 = Regelung EIN (an aktueller Position), 03 = Umschalten, **04 = Status lesen (3 B)**. Nur Sub 04 antwortet – deshalb nie Antwort auf 01/02. |
| 2 | Erase 0xF0/0x06 Motoren | **teilweise** | App kennt 0xF0 nicht (ignoriert, keine Antwort, B). Stock-Updater sendet 0x06 **nur an CFS-Typ 1/10, nie an Motoren** (B). Wirkung im Motor-Bootloader: offen (nicht im Image). |
| 3 | idx 15/16 | **beantwortet** (B, Betriebswert V) | idx 15 = Encoder Single-Turn 0..32767 (32768/U), idx 16 = `encoder_fdb` (= idx 15 bei `singleturn_control_mode` 1 = Default, sonst Multiturn). |
| 4 | Setzer Fehlerbit 4/11, Warnbit 0 | **beantwortet** (B) | Bit 11 = Motorcheck „PhaseNotConnected“ (@0xe082). Bit 4 und Warnbit 0 haben **keinen Setzer**. Zusatz: Bit 1 und 6 werden ebenfalls nie gesetzt. |
| 5 | Gain·R | **teilweise** | FW-Default **20 × 0,02 Ω = 0,4 Ω** (B). Laufzeitwert kommt aus dem Flash (nicht im Image). Live-Rohbytes schließen 0,4 Ω aus und passen exakt zu **1 Ω** (= Host-Tabelle 20 × 0,05) → V, stark. **Korrektur:** idx 10 ist bei `phase_order_invert` = 0 (live) **+Ib**, nicht −Ib. |
| 6 | idx 13–18, > 18 | **beantwortet** (B) | idx 13 = EN-Pin-Pegel (Host-Label „EN脚电平“), flankengesteuert; > 18 → veralteter Scratch-Wert, kein Seiteneffekt. Sicherer Lesepfad für idx 13: 0x05 `[04]`. |

---

## 1. Motor-Funktion 0x05 `control` – Semantik und fehlende Antwort

**Beobachtung (B):** Handler `0xf880` (Dekompilat `dec/0f880_h05_control.c`, Listing `0xf90a–0xf9bc`),
Sub-Kommando = Payload-Byte 0 (`*(obj+0x6cc)`):

| Sub | Wirkung | Antwort | Beleg |
|---|---|---|---|
| 00, ≥ 05 | nichts | keine | `0xf922` → Rücksprung `0xf8d8` |
| **01** | `obj+0xa10(obj, 0)` = **Regelung AUS** | **keine** | `0xf95c–0xf96c` |
| **02** | `obj+0xa10(obj, 1)` = **Regelung EIN** | **keine** | `0xf9a8–0xf9b4` |
| 03 | Umschalten über statische Variable `gp−0x7ac` (0x20002b4c, Start 0 → erster Aufruf = EIN), unabhängig vom echten Zustand | keine | `0xf98a–0xf9ba` |
| **04** | **lesend:** 3 Byte `[obj+0x7a6 EN-Pin-Pegel, ctrl+4 Regler aktiv, drv+4 PWM-Freigabe]`, dann `obj+0xa44` | **3 B** | `0xf926–0xf958` |

Textkonsole (nicht Binärmodus): `control en 0|1`, `control toggle` (Strings @0x1a050 „en“, @0x1bd68/0x1bd6c).

**Was „Regelung AUS/EIN“ bedeutet (B, FUN_d788 = `obj+0xa10`, `dec/0d788_control_enable_a10.c`):**
- **AUS (0):** `drv+4 = 0` (PWM-Freigabe-Flag, Host-Name `driver_board_param_pwm_output_en`), `ctrl+4 = 0`
  und `ctrl+0x47c = 0` (Regler aus), `hal+0x134` = Timer @0x40000400 stoppen (CTLR1.CEN = 0, FUN_12218),
  `hal+0x128(0)` = Zähler 0 (CNT, FUN_12462), `obj+0x7b0/0x7b4 = 0`, Soll-Winkel `obj+0xd0` aus dem
  Schrittzähler neu berechnet.
- **EIN (1):** 0x15/0x16-Ringpuffer **geleert** (Kopf `obj+0x88c = 0`, memset `obj+0x88e…0x92e`),
  Schrittzähler-Objekt `obj+0x790` aus `encoder_fdb`·Skala neu gesetzt (`obj+0x7f0`), `drv+4 = 1`,
  Regler-Reset (`ctrl+0x960`), Beobachter-Init mit der aktuellen Position (`ctrl+0x5d8(obj+0xf0, 0, …)`),
  `ctrl+4 = 1`, `ctrl+0x47c = 1`, Timer-Zähler 0 und Start (`hal+0x128(0)`, `hal+0x130`).
- **V:** Timer @0x40000400 ist der STEP-Pulszähler (dort mit externer Taktquelle konfiguriert, FUN um
  `all.c` Z. 2237–2258); „AUS“ heißt damit Motor stromlos/frei (PWM-Flag 0, Regler aus).

**Warum keine Antwort (B):** Der Dispatcher FUN_d5c2 (`0xd6a6–0xd780`) prüft `F7`, Adresse (eigene oder 0xFF),
Länge und CRC, ruft den Tabelleneintrag mit passendem Cmd auf (`0xd77a jalr`) und löscht danach nur das
Binärmodus-Flag (`0xd77c`). Eine generische Quittung gibt es nicht; Antworten entstehen nur, wenn ein Handler
selbst `obj+0xa44` (FUN_dd6e, Antwort-Builder) aufruft. Im 0x05-Handler tut das ausschließlich Sub 04.
Live passend: jede 0x05-`[01]`/`[02]`-Anfrage endet nach ≈ 52–58 ms in „timeout … recv_result:None“
(`live-logs-2026-10-15/klippy.log.2026-10-13:2210–2249`, `klippy.log.2026-10-12:28620–28629`).

**Host-Gegenprobe (B):** Formatstring @0x107f01 der Host-.so: „电机控制使能状态:%s EN脚电平当前:%s,
控制器当前:%s, pwm输出当前:%s“ (Steuerfreigabe / EN-Pin-Pegel / Regler / PWM-Ausgang), Hilfetext
„MOTOR_CONTROL NUM=1 DATA=4 电机控制使能相关指令“ @0x106599 → genau die drei Bytes von Sub 04.

**Kontext BELT_TENSION (B, Klartext `belt_mdl-live.py:231–249`):** In `set_tension()` je Runde (2×):
`RESET_HOME_AXES_XY` → `MOTOR_CONTROL NUM=1/2 DATA=2` (Regelung EIN an X/Y) → 0,3 s → `FORCE_MOVE`
X −200/Y +200, M400, X +200/Y −200, M400 → `MOTOR_CONTROL NUM=1/2 DATA=1` (AUS) → `M84`. Live-Reihenfolge
passt (`[02]` direkt vor `FORCE_MOVE stepper_x distance=-200`, `klippy.log.2026-10-13:2240–2250`).
**V:** Die Verteilfahrt läuft bewusst im Closed-Loop; danach Motoren frei für die Spannungsmessung.

**Nebenbefund (B):** Auch Parameter-Schreiben schaltet die Regelung um: 0x06 Sub 01 (FUN_f9be) und 0x07 Sub 05
(FUN_fcf4) merken `ctrl+4`, rufen `obj+0xa10(obj,0)`, schreiben den RAM-Wert (FUN_aade), re-initialisieren alle
Module (`obj+0xa14` = FUN_d8ec: Encoder, Treiber, Schrittmotor, Regler) und schalten den alten Zustand **nur
wieder ein, wenn kein Fehler latched ist** (`obj+0x42c == 0`). Bei latched Fehler bleibt die Regelung nach
einem Param-Write aus.

**Sicherheit:** hoch (Code + Host-String + Log).

---

## 2. Erase 0xF0/0x06 für Motoren

**Beobachtung App (B):**
- Die Kommandotabelle (Registrierung FUN_60fc, 19 Einträge `reg(obj, i, cmd, broadcast_ok, name, …, handler)`)
  enthält nur Cmd 0x00–0x16; **0xF0 fehlt**, ebenso 0xA0/0xA1 (Auto-Adressierung von `mcu_util_485`).
  Der Dispatcher ruft nur passende Einträge (`0xd750–0xd77a`) → 0xF0-Frames (jedes Sub) werden von der
  laufenden App **ignoriert, keine Antwort, keine Wirkung**.
- Broadcast-Flag (`0xd758–0xd762`): Per 0xFF erreichbar sind nur `reboot` (0x01), `boot` (0x0B) und
  `rs485_addr` (0x0E); alle anderen Cmds ignorieren 0xFF.
- Bootloader-Einstieg `boot` 0x0B `[00]` (FUN_104e6): `obj+0x28` (Param `boot_key`, RAM 0x20001ec0) = 0x8567,
  dann `obj+0xa08` (FUN_ba9e): PWM-Tastgrad 0, `drv+4 = 0`, `hal+0x150` → FUN_11b8c schreibt 0xCCCC nach
  0x40003000 (IWDG-Start) → Watchdog-Reset. `reboot` 0x01 `[00]` macht dasselbe ohne Boot-Key.
  **ANNAHME:** Der Bootloader prüft `boot_key` = 0x8567 im RAM und bleibt dann im Update-Modus.

**Beobachtung Host-Updater `mcu_util_485` (B):** Update-Automat FUN_00014a08
(`dec/mcu_util_485_14a08_update_state_machine.c`), Erase-Zustand 5 @**0x15378–0x153d4**
(`dec/mcu_util_485_15378_erase_nur_typ1_10.asm`): `ldr r5,[node,#0x10]; cmp r5,#1; cmpne r5,#10` → F0 `[06]`
(Timeout 500 ms, 3 Versuche) **nur für Gerätetyp 1 oder 10** (CFS). Motoren sind Typ 2 (V, stark:
„found all motors“ bei 4 Knoten Typ 2; Adress-Zuweisung Typ 2 nur für Knoten an 0xFD = ADDR_MOTOR,
`0x14fcc–0x14fdc`). Die übrigen Subs (00 Version, 03 Sektorgröße, 01 Request, Länge, Daten, 02 Ende) prüfen
den Typ nicht. **Folgerung (B):** Stock sendet an Motoren nie 0x06.

**Offen:** Ob/wie der Motor-**Bootloader** 0xF0 `[06]` ausführt (welcher Sektor, Rückgabe). Der Bootloader
(Flash 0x08000000–0x08002FFF, 12 KB) ist in keiner vorhandenen Datei (`fw/F008|F012/mot*`, `motor/mot*`,
rootfs, Arbeitsordner durchsucht; dort nur ein CFS-Bootloader). Suchumfang: Motor-Bootloader per WCH-Link/SWD
von einem Ersatzboard lesen oder Fremd-Full-Flash-Dump; dabei auch den Param-Block 0x08022C00–0x08022FFF
sichern (s. §5).

**Nebenbefund 0x07 `flash_param` (B, FUN_fcf4; Accessor-Modi in FUN_a42a):** Sub 01 = Werkseinstellungen
(`use_default_param` = 1 in den Flash, Re-Init, Neustart; V für „Flash“), Sub 02 = Flash-Prüfung (lesend,
5 B), Sub 03 = **alle Parameter in den Flash schreiben** (Antwort `[01]`), Sub 04 = alle aus dem Flash laden
(keine Antwort), Sub 05 = RAM-Wert schreiben (+ Neustart, falls `par+5` = 1), **Sub 06 = Flash-Wert lesen**
(par+0x1104 → Modus 4). Zum Vergleich 0x06 Sub 02 = RAM-Wert lesen (par+0x1128 → Modus 7).

**Sicherheit:** App- und Host-Seite hoch (B); Bootloader-Verhalten offen.

---

## 3. idx 15/16 = obj->[0xc]+0x24 / +0x2c

**Beobachtung (B):** `obj->[0xc]` ist das Encoder-Objekt (FUN_f174: `obj[3]+0x40` = „encoder->Init“).
Konstruktor FUN_6870: `enc+0x10` = 15 (Bit), **`enc+0x14` = 0x8000 = 32768 Counts/Umdrehung**,
`enc+0x18` = 1 (Richtung). Methoden (FUN_5e40, `dec/05e40_enc_init.c` ff.):
- `enc+0x5c` FUN_5d30: SPI-Rohwert lesen (u16 >> 1 = 15 Bit).
- `enc+0x60` FUN_5c38: `enc+0x1c` = roh, `enc+0x20` = roh mod 32768.
- `enc+0x64` FUN_5c50: `enc+0x28` = alter Wert, **`enc+0x24` = `enc+0x20`, bei `enc+0x18` < 0 gespiegelt
  (32768 − x)** → idx 15.
- `enc+0x54` FUN_5bcc: Umdrehungszähler `enc+0x30` ±1 bei Sprung > ±16384;
  **`enc+0x2c` = (`enc+8` == 0 ? `enc+0x30`·32768 : 0) + `enc+0x24`** → idx 16.
- `enc+0x58` FUN_5c1c (Lesepfad) ruft 0x60/0x64/0x54 und liefert `enc+0x2c`; FUN_bae2 schreibt das nach
  **`obj+0x84`** = Feld `encoder_fdb` in 0x13/0x14 und Textbefehl `get encoder_fdb` (String @0x1be10,
  `printf "%ld"` von `obj+0x84` in FUN_102d0).
- `enc+8` = `singleturn_control_mode` (`obj+0x2e`, Param-ID 7): FUN_b218 `0xb258 lhu 0x2e(obj)` →
  `0xb27c sh …, 0x8(enc)`. FW-Default **1** (Hauptkonstruktor `0xb3d2`: `obj+0x2c` = 0x00010000 →
  `use_default_param` 0, `singleturn_control_mode` 1); Host-Tabelle Default ebenfalls 1.

**Antwort:** idx 15 = **Encoder-Single-Turn-Wert** (Counts 0..32767, richtungskorrigiert), idx 16 =
**`encoder_fdb`** (Counts). Mit `singleturn_control_mode` = 1 ist idx 16 == idx 15; nur bei 0 Multiturn
(Umdrehungen·32768 + Single-Turn). Umrechnung: rad = Counts·2π/32768. Nicht verwechseln mit idx 11
(STEP-Pulse, 3200/U).

**Live-Stütze (B, Log):** 0x14 live `encoder_fdb` 4421 ↔ `pos_fdb_encoder` 0,8477 rad; 4421·2π/32768 =
0,84772 rad → 32768 Counts/U bestätigt, Wert im Single-Turn-Bereich (`LIVE-ABFRAGEN-2026-10-16.md` Z. 159).

**Sicherheit:** Felder/Formel hoch (B). „idx 16 == idx 15 im Betrieb“ V (hängt am Flash-Wert von ID 7;
lesend prüfbar mit 0x06 `[02, 07]`).

---

## 4. Setzer von Fehlerbit 4, Fehlerbit 11, Warnbit 0

**Beobachtung (B):** Fehlerprüfung `obj+0x9e8` = FUN_ddf2 (`dec/0ddf2_protect_err_check_9e8.c`),
Sprungtabelle @0x1bb08 (14 Einträge), wirkt nur bei `protect_en` (`obj+0x3f4`) ≠ 0 (Default 1, `0xb4f8`).
**Alle Aufrufe** (Assembly, alle Ladestellen von `obj+0x9e8`): nur Schutzzyklus FUN_b9c8 `0xb9d8–0xba24`
mit n = **0, 2, 3, 5, 7, 8, 9**. Warnprüfung `obj+0x9ec` = FUN_e0b6 nur für n = 1 (Unterspannung) und 2
(Übertemperatur); Aufrufe `0xba26`, `0xba30`, `0xe5ae`, `0xe982`. Alle Schreibstellen auf `obj+0x428`
(Fehler roh): `0xdeae, 0xdee0, 0xdfd8, 0xe002, 0xe07c, 0xe08e, 0xe09c, 0xe0aa` (Prüffunktion), `0xe5a0`
(Motorcheck löscht Bits 9–13), `0x1064a` (0x0C `[05]`). Auf `obj+0x438` (Warnung roh): `0xe0de` (|2),
`0xe100` (|4), `0x10656` (löschen).

| Bit | FW-Name (Tabelle @0x1b3d4) | Setzer | Ergebnis |
|---|---|---|---|
| **4** | HardwareCurrent_OverLimit | Tabelleneintrag 4 → `0xdfe8` = nur Latch neu rechnen | **kein Setzer** (B) |
| **11** | MotorCheckNotConnected | Fall 9 → `obj+0x1d6 == 4` → `\|= 0x800` @**0xe082–0xe08e** (Untertabelle @0x1bb40) | **lokalisiert** (B) |
| Warn **0** | CommunicationErr (@0x1b794) | keiner | **kein Setzer** (B) |
| 1 | EncoderReadErr | Code in Fall 1 vorhanden, aber nie mit n = 1 aufgerufen | nie gesetzt (B, Lücke im Vorbefund) |
| 6 | PosFdbOverLimit | Code in Fall 6 vorhanden, nie aufgerufen | nie gesetzt (B) |

**Herkunft Bit 9–13 (B):** `obj+0x1d6` ist das Ergebnis des **Motorchecks** (R/L-/Phasenmessung, FUN_e51c =
`obj+0xa34`, `dec/0e51c_motorcheck_a34.c`): 1 LowPower (kein Fehlerbit, nur bei gesetzter
Unterspannungswarnung), 2 UnStable → Bit 10, 3 ValueAbnormal → Bit 9, **4 MotorPhaseNotConnected → Bit 11**
(gesetzt `0xe922/0xe924`), 5 ResistanceDifferent → Bit 12 (`0xe5c8`), 6 OtherErr → Bit 13. Der Schutzzyklus
übernimmt das Ergebnis und löscht `obj+0x1d6` (`0xe016`). Der Motorcheck startet beim Hochlauf, wenn
`obj+0x1d8` ≠ 0 (Haupttask FUN_bde6 `0xbe2a`; Default 1, Konstruktor `0xb66a`).

**Folgerung (V):** Effektiv setzbar sind in mot2_002_081 nur Bits 0, 2, 3, 5, 7, 8 (zyklisch) und 9–13
(Motorcheck beim Hochlauf) sowie Warnbits 1 und 2. Für HW-Überstrom fehlt zusätzlich eine
Timer-Break-Konfiguration bei der TIM1-PWM-Init (FUN um `all.c` Z. 2095–2147: Basis, OC1–4, NVIC, kein
BDTR-Setup) – V.

**Grenze:** Statisch vollständig aufgezählt über Dekompilat (≈ 86 % des Bereichs 0x3000–0x1cb8c in
Funktionen; Rest überwiegend Konstanten/Strings) und Assembly-Suche nach den Literal-Offsets 0x428/0x438.
**Sicherheit:** hoch.

---

## 5. Strommessung Gain·R und Umrechnung der Rohwerte

**Beobachtung (B):**
- Umrechnung FUN_5ef2 (= `drv+0x17c`, gesetzt in Treiber-Init FUN_6070):
  **I = Δ · Vref · (1/4096) / (Gain · R)**, `Vref` = `drv+0x24`, `Gain` = `drv+0x2c`, `R` = `drv+0x20`;
  Konstante 1/4096 @0x19b4c.
- idx 9 (`ctrl+0x7c`) = Ia = FUN_5f16 (`drv+0x180`): Δ = ADC-Kanal 0 − Offset `drv+0xc` als **int16**
  (`0x5f34–0x5f40`), mal `current_dir` = int16 `drv+6`.
- idx 10 (`ctrl+0x80`) = Ib (FUN_5f52, `drv+0x184`, Kanal 1, Offset `drv+0xe`) × **(+1,0 bei
  `phase_order_invert` = 0, −1,0 bei ≠ 0)** (`0xbb30 lh 0x5a(obj)`, `beqz` → `0xbc5c` lädt +1,0 @0x19c98,
  sonst −1,0 @0x19d14). `obj+0x5a` = Param-ID 10 `phase_order_invert` (0x20001ef2). **Korrektur zum
  MOTOR-FW-BEFUND §3:** idx 10 ist nur bei `phase_order_invert` = 1 „−Ib“; live ist er 0 (0x13 live), also
  **idx 10 = +Ib**.
- **FW-Defaults** (Treiber-Konstruktor FUN_676e, `dec/0676e_drv_ctor_defaults.c`): `0x678a`
  power_supply_V 24,0 · `0x6798` **Vref 3,3** · `0x67a6` **amp_gain 20,0** · `0x67b4`
  power_voltage_sample_gain 11,234 · `0x67c2` **R_of_current_sample_ohm 0,02** → **Gain·R = 0,4 Ω**;
  `drv+6` (current_dir) = −1, `drv+4` (PWM-Freigabe) = 0.
- FW-Param-Tabelle (FUN_68ac): nur **ID 26** (0x1a, V), **ID 28** (0x1c, amp_gain → `drv+0x2c`) und
  **ID 30** (0x1e, R → `drv+0x20`) registriert. Vref (`drv+0x24`) und power_voltage_sample_gain (`drv+0x28`)
  sind nicht registriert → bleiben 3,3 / 11,234 (IDs 27/29 antworten vermutlich mit 0 B, V).
- Laden (FUN_d966 = `obj+0xa54`): `flash_key` gültig, `flash_param_version` gleich (0x24b = 2.75, passt zu
  live 0x0F) und `use_default_param` = 0 → **alle Parameter aus dem Flash** (par+0x10f8 → FUN_9d7a,
  Modus 1); sonst Defaults. Flash-Block **0x08022C00**, 0x400 B (`par+0x28`/`par+0x24` in FUN_68ac) –
  **nicht im Image**.
- Host-Tabelle (Vorarbeit `arbeitsdateien-lokal/cc-init/ptable_fix.json`, nicht erneut nachgerechnet):
  amp_gain 20, R 0,05, adc_ref 3,3 → 1,0 Ω.

**Folgerung aus den Live-Rohbytes (V, stark):** Stillstand idx 9 = `33 33 d3 3a`, idx 10 = `33 33 53 3a`
(`arbeitsdateien-lokal/motor_get_scan.txt:9–10`) sind **bitgenau** 2·(3,3f/4096) bzw. 3,3f/4096
(Float32 0x3ad33333 / 0x3a533333). Da Δ und `current_dir` ganzzahlig sind (B), muss Gain·R zur Laufzeit eine
**positive ganze Zahl** sein. 0,4 Ω (FW-Default) ist ausgeschlossen (1 bzw. 2 LSB wären 0,4/0,8 Δ; auch der
F1200-Wert −0,0330 A wäre 16,4 LSB). Mit der Host-Tabelle konsistent: **Gain·R = 1 Ω → 0,80566 mA/LSB**.
**ANNAHME:** Der Flash enthält R = 0,05 Ω; Werte 2 Ω, 3 Ω … sind statisch nicht ausgeschlossen.

**Umrechnung:** I [A] = current_dir · Δ · 3,3 / 4096 / (Gain·R); bei 1 Ω: Δ = I / 0,00080566
(Live F600: Ia −0,0725 A ≈ 90 LSB, F1200: −0,0330 A ≈ 41 LSB).

**Lesend prüfbar (nur Empfehlung, nicht gesendet):** 0x06 `[02, 0x1c]` / `[02, 0x1e]` = wirksame RAM-Werte
(4 B float LE), 0x07 `[06, 0x1c]` / `[06, 0x1e]` = Flash-Werte.

**Sicherheit:** Formel, Vorzeichen, Defaults hoch (B); Laufzeitwert 1 Ω V (stark), endgültig nur live.

---

## 6. idx 13–18 und > 18 (nur Lücken)

**Beobachtung (B):** GET-Handler FUN_102d0: Grenze `li a4,0x12; bltu` @**0x1039a–0x1039e**, Sprungtabelle
@0x1bce4. idx 13 `obj+0x7a6`, 14 `obj+0x40`, 15/16 Encoder (§3), 17 `drv+0x4c`, 18 `drv+0x48` – wie im
MOTOR-FW-BEFUND. **idx > 18:** Sprung nach `0x103b8` → 4 B aus dem Scratch `gp−0x7b4` (0x20002b44) =
letzter Wert, den irgendein Handler dort abgelegt hat (GET, 0x06/0x07 lesen/schreiben, Kalibrier- und
Motorcheck-Antworten). Kein Fehler, kein Absturz, kein Seiteneffekt.

**idx 13 präzisiert (B):** FUN_b784 (= `obj+0x9e0`, zyklisch im Haupttask FUN_bde6) liest GPIO `drv+0x118`
(`0xb794`), speichert den Vorwert in `obj+0x7a8` und reagiert **auf Flanken**:
0→1 → Regelung AUS (`obj+0xa10(obj,0)`); 1→0 → Regelung EIN, **nur wenn kein Fehler latched**
(`obj+0x42c == 0`); ohne Flanke und mit `drv+4 == 0` werden Schrittzähler und Soll-Winkel laufend auf den
Encoder nachgeführt. Pegel 1 = gesperrt. Der Name „EN“ ist jetzt belegt: 0x05 Sub 04 sendet dasselbe Feld
`obj+0x7a6` als „EN脚电平“ (§1). **ANNAHME:** Der Eingang hängt am Host-Pin `!PA9` (Verdrahtung unbelegt).

**Sicherer Lesepfad für idx 13:** 0x05 `[04]` (eigener Sender) bzw. Host-G-Code `MOTOR_CONTROL NUM=1 DATA=4`
(DATA ≤ 12, also nicht der Crash-Pfad von `MOTOR_GET DATA=13`; live nicht geprüft).

**Nebenbefund Benennung (B):** `obj+0x32` heißt in der FW `emergency_pin_mode` (Param-ID 11), gesetzt von
Cmd 0x11 (FUN_10c96); `obj+0x34` heißt `stall_mode` (Param-ID 12, Kriterium: 1 = |iq_ref| > `stall_cur_A`
`obj+0x130`, 2 = |Lagefehler `ctrl+0x124`| > `stall_pos_err_rad` `obj+0x134`, bei Modus 3 `obj+0x138`).
Stall-Bewertung (FUN_b842) nur bei `emergency_pin_mode` ∈ {1, 3}. **0x11 `[04]` antwortet
`[Modus, Pegel Stall-Pin drv+0x120]`** – das zweite Byte (live `00`) ist der Pin-Pegel, kein Füllbyte.

**Sicherheit:** hoch.

---

## 7. Folgen für den Nachbau (nur Empfehlungen – kein Code geändert)

**`nachbau/motor.py` / `nachbau/motor_mini.py`:**
1. **0x05:** Konstanten `CONTROL_OFF = 0x01`, `CONTROL_ON = 0x02`, `CONTROL_TOGGLE = 0x03`,
   `CONTROL_STATUS = 0x04`. Lesend `read_control_status()` = 0x05 `[04]` → `{en_pin, controller_on, pwm_on}`
   (3 B). Schreibend `set_control(on, confirm=True)` ohne Antwort-Erwartung (kurzer Timeout, `retries=0`,
   keine Fehlermeldung bei Timeout). `[03]` (Toggle) nie senden. `[02]` leert die 0x15/0x16-Ringe.
   `cfswatch.py`: 0x05 `[01]`/`[02]` ohne Antwort als „erwartet“ markieren.
2. **idx 10:** `minus_ib` → Bedeutung „Ib × (−1 falls phase_order_invert)“. `read_phase_currents()` darf
   nicht pauschal negieren: bei `phase_order_invert` = 0 (live) ist `ib` = idx 10. Selbsttests mit
   `ph["ib"]` entsprechend anpassen.
3. **idx 13:** Status „belegt (FW + Host-Label)“, Flankenlogik im Docstring; Lesen bevorzugt über
   `read_control_status()`.
4. **idx 15/16:** Namen z. B. `encoder_single_turn` und `encoder_fdb`, Einheit Counts (32768/U),
   Status „fw“; Hilfsfunktion Counts → rad (2π/32768). `read_variable_raw_fw` kann 15/16 als statisch belegt
   führen (Live-Gegenprobe weiter offen).
5. **Fehler-/Warnbits:** Kommentar in `MOTOR_ERROR_BITS`/`MOTOR_WARNING_BITS` ergänzen: Bit 1, 4, 6 und
   Warnbit 0 werden in mot2_002_081 nie gesetzt; Bits 9–13 kommen aus dem Motorcheck beim Hochlauf
   (FW-Namen ValueAbnormal / UnStable / NotConnected / ResistanceDifferent / OtherErr als Zweitbeschriftung).
6. **Stromskala:** Konstanten `ADC_VREF = 3.3`, `ADC_COUNTS = 4096`, `GAIN_R_FW_DEFAULT = 0.4`,
   `GAIN_R_ASSUMED = 1.0` (V); optional `read_current_scale()` über 0x06 `[02,28]`/`[02,30]` (lesend, live
   noch nie gesendet → hinter `confirm=True`).
7. **Update/Erase:** Docstring „nur CFS-Typ 1/10“ durch den Beleg ersetzen (mcu_util_485 @0x15378);
   für Motorziele `erase=True` mit `ValueError` ablehnen. Die App ignoriert 0xF0 komplett – ein Update geht
   nur über den Bootloader (0x0B). **Nie `FF 0B …` oder `FF 01 …` broadcasten** (setzt alle Motoren in
   Bootloader/Neustart).

**`pi-host/k2_motor_init.py`:**
1. Fehler-/Warntexte (Z. ≈ 504–515) wie Punkt 5 oben annotieren.
2. Param-Writes (0x06 `[01]`, z. B. ID 70 beim Sensorless-Homing) nur im Stillstand: Die FW schaltet dabei
   die Regelung kurz aus, initialisiert alle Module neu und schaltet bei latched Fehler **nicht** wieder ein.
   Nach Writes optional 0x05 `[04]` und 0x0C `[0b]` lesen.
3. `KTWO_MOTOR_STATUS` kann 0x05 `[04]` (EN/Regler/PWM) lesend mit aufnehmen.
4. `SUB_STALL_GET` (0x11 `[04]`): zweites Antwortbyte als Stall-Pin-Pegel dekodieren.

---

## 8. Offen (mit Suchumfang)

| Punkt | Warum offen | Suchumfang / nächster Schritt |
|---|---|---|
| Motor-Bootloader: Verhalten von F0 `[06]`, Boot-Key-Prüfung | Bootloader 0x08000000–0x08002FFF nicht in den Images | Ersatzboard per WCH-Link lesen oder Fremd-Dump; nie am Produktivmotor testen |
| Laufzeitwerte Gain/R (und `singleturn_control_mode`) | Flash-Block 0x08022C00 (0x400 B) nicht im Image | lesend live: 0x06 `[02,0x1c]`, `[02,0x1e]`, `[02,0x07]`; 0x07 `[06,…]` für Flash-Werte |
| Wirkung „Timer @0x40000400 = STEP-Zähler“, „AUS = stromlos“ | Peripherie-Semantik V | live: 0x05 `[04]` vor/nach M84 bzw. Enable vergleichen |
| Verdrahtung EN-Eingang ↔ `!PA9` | Schaltplan fehlt | live: 0x05 `[04]` bei Stepper enable/disable |
| idx 15/16 live | nie gesendet (Host-Crash-Grenze) | eigener Sender, `read_variable_raw_fw(15/16, confirm=True)` |

---

## 9. Arbeitsdateien (lokal, `arbeitsdateien-lokal/re-motor-1017/`)

- `proj/` – eigenes Ghidra-Projekt (`motfw` = Motor-FW, `mcuutil` = mcu_util_485).
- `ghidra-wch-patch/` – geänderte `riscv.rvc.sinc` (XW-Befehle) und `riscv32-fp.cspec` (Call-Fixups) +
  Diffs; `mk-ghidra-wch.sh` stellt die isolierte Ghidra-Kopie (Reflink von /opt, /opt unverändert) wieder
  her; `gh.sh` (Aufruf mit eigenen XDG-Verzeichnissen), `rebuild.sh` (Projekt neu aufbauen).
- `gscripts/` – Ghidra-Skripte (SetupMot, FixSave, MakeFuncs, Decomp, DecompAll, ListFuncs);
  `scripts/` – Kandidaten-Sammler und `rng.py` (Adressbereich aus dem Listing).
- `all.c` – alle 583 Dekompilate; `dec/` – benannte Schlüsselfunktionen (Handler, Dispatcher,
  Schutzprüfung, Encoder, Treiber, Parameter, mcu_util_485-Updater); `mot.asm` (+xwchc), `funcs.txt`.
