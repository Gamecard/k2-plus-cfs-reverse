# Live-Abfragen Motor X (0x81), 16.10.2026 ~13:10 (Log-Uhr UTC+8)

Gesendet über Moonraker mit Creality-eigenen G-Codes (lokales Whitelist-Wrapper-Skript
für lesende Motor-G-Codes über Moonraker; nicht im Repo). Drucker im Standby, nicht referenziert. Belege: klippy.log (Drucker)
und gcode_store, Zeitraum 13:10:13–13:10:56.

| G-Code | Request (data_send) | Antwort | Bedeutung |
|---|---|---|---|
| MOTOR_VERSION NUM=1 DATA=0 | `81 04 00 0f 00` | `f7 81 0f 00 0f 01 09 07 51 01 03 01 03 02 4b 01 00 02` | **0x0F = Version**: 12 B = fw 1.9.7.81 (01.09.07.0x51), cmd_system 1.3, protection 1.3, flash_param 2.75 (02.0x4b), logger 1.0 – Host-Parser „获取版本号数据“ |
| MOTOR_GET NUM=1 DATA=1 | `81 04 00 08 01` | `f7 81 07 00 08 00 00 00 00 0a` | **0x08 = „aktueller Wert einer vom System unterstützten Variable“** [index] → 4 B Float (Index 1 = 0.0). Welche Variable welcher Index ist: offen (nur Index 1 getestet) |
| MOTOR_READ_ADDR NUM=1 | `81 04 00 0e 02` | `f7 81 04 00 0e 81 00` | 0x0E [02] → eigene Adresse 0x81 (bestätigt Live-Log-Befund) |
| MOTOR_AUTO_CHECK_PROTECTION NUM=1 DATA=11 | `81 04 00 0c 0b` | `f7 81 0b 00 0c 00×8 fb` | 0x0C [0b] → 8 B Fehler-/Warncodes, alle 0 = keine Fehler |
| MOTOR_READ_ALL_PIN_IO | (kein RS485 – MCU-GPIO-Abfrage `gpio_status`, oid 11) | PB9, PB10, !PB7, PB8, PB5, PB6, PA1, PB15 = 0 | dir/step-Pins der 4 Motoren laufen über die **Main-MCU** (Klipper-GPIO), nicht über den Bus → für Pi-Ersatz: GPIO-Phase braucht weiter die Main-MCU |

Nebenbefund: 0x0a-Antwort zeigt weiterhin Hub-Maske 0x01 (Slot A) im Zustand IDLE, 22 °C / 53 %.
Host-Beschreibungen der Motor-G-Codes (chinesisch) aus `/printer/gcode/help` – u.a. MOTOR_GET „打印系统所支持变量“,
MOTOR_BOOT „使系统进入boot模式“ (NICHT senden), MOTOR_REBOOT, MOTOR_SET_ADDR, Kalibrierungen (NICHT senden).

## MOTOR_GET-Scan (0x08), Motor X, 16.10.2026 ~13:15 (Rohdaten: arbeitsdateien-lokal/motor_get_scan.txt – lokal, nicht im Repo)

Request `81 04 00 08 <idx>` → Antwort 4 Byte (LE). Host dekodiert je Index als float oder int
(„系统所支持变量当前值“ = aktueller Wert der vom System unterstützten Variable), Namen gibt der Host NICHT aus.

| idx | Rohbytes | Host-Wert | Typ |
|---|---|---|---|
| 1–8 | 00 00 00 00 | 0.0 | float |
| 9 | 33 33 d3 3a | 0.0016113 | float |
| 10 | 33 33 53 3a | 0.0008057 | float |
| 11 | 52 04 00 00 | 1106 | int |
| 12 | 00 00 00 00 | 0 | int |

Drucker im Standby, nicht referenziert (Motoren stromlos/inaktiv) → viele Werte 0. Für Bedeutung:
gleiche Abfrage nach Homing/während Bewegung wiederholen bzw. Namen aus motor_control_wrapper.so ziehen.

### WARNUNG – Creality-Host-Bug (zweimal reproduziert)
`MOTOR_GET … DATA=0` und `MOTOR_GET … DATA=13` lösen in `motor_control_wrapper.so` einen internen Fehler aus
(`key60: Internal error on command:MOTOR_GET`) → **Klipper geht in SHUTDOWN**. DATA=0 wird schon vom G-Code-Parser
abgelehnt („minimum of 1“) und crasht trotzdem. Gültig: DATA=1..12. Wiederherstellung: `FIRMWARE_RESTART`
(POST /printer/firmware_restart), danach ~60 s bis Motoren/Box bereit (Motor-Init + Auto-Addr laufen neu).
Ob der Motor selbst Index ≥13 verträgt, ist offen (Crash liegt host-seitig vor dem Senden bei DATA=0;
bei 13 nicht geprüft, ob ein Frame rausging). Das Whitelist-Wrapper-Skript lässt seitdem nur DATA=1..12 und NUM=1..4 zu und sendet nur bei
Druckerstatus standby/complete/cancelled/error **und** Klipper-Zustand `ready`.

**Empfehlung für andere Nutzer:** Motor-G-Codes nicht frei über Moonraker absetzen, sondern
über eine Whitelist, die nur lesende Befehle und belegte Index-Bereiche durchlässt – ein
einziger falscher Index (`MOTOR_GET DATA=0` oder `DATA=13`) reicht für einen Klipper-SHUTDOWN.

### Ursache belegt (16.10.2026, Traceback + REA)
Drucker-`klippy.log` 16.10. 13:12:27 (Log-Uhr UTC+8), Traceback nach `Internal error on command:MOTOR_GET`:

```
File ".../motor_control_wrapper.py", line 1982, in Motor_Control.motor_get
File ".../box_wrapper.py", line 384, in ParseData.parse_num_to_byte
TypeError: 'NoneType' object cannot be interpreted as an integer
```

Mechanismus (statische Analyse, `REA-MOTOR-BEFUND-2026-10-16.md` §1d): `motor_get` liest
`DATA` per `gcmd.get_int('DATA', 0, minval=1, maxval=12)`. Creality's `gcode.py` wirft bei
Bereichsverletzung keinen CommandError, sondern gibt nur eine Warnung aus („minimum of 1“) und
liefert `None`; `motor_get` rechnet weiter, `parse_num_to_byte(None)` → TypeError → key60 →
SHUTDOWN. **`motor_send_data` wird nie erreicht – bei DATA=0 und DATA=13 ging KEIN Frame auf den
Bus.** Ob der Motor idx ≥ 13 verträgt, bleibt damit ungeprüft (Host kennt 13–18 in seiner
Typkette). Zusätzlich aus REA: idx 5 = `pos_fdb`, 6 = `spd_fdb`, 7 = `id_fdb`, 8 = `iq_fdb`
(Standby-Scan oben: alle 0.0, passt zu stromlosen Motoren); Typ 11/12 = `<I` (unsigned).

## Skalierung pos_fdb (idx 5) / idx 11 – live gemessen 16.10.2026 nach G28 (Bett leer, User-Freigabe)

Motor X (0x81), Y=175, Z=10, F3000–6000, je Position M400 + Abfrage. printer.cfg [stepper_x]:
rotation_distance 40, microsteps 16.

| X [mm] | idx 5 (float) | idx 11 (uint) |
|---|---|---|
| 50 | 1.35489 | 690 |
| 100 | 2.92568 | 1490 |
| 150 | 4.49648 | 2290 |
| 160 | 6.06728 | 3090 |
| 170 | 1.35489 | 690 |
| 180 | 2.92568 | 1490 |
| 185 | 3.71108 | 1890 |
| 200 | 6.06728 | 3090 |
| 250 | 1.35470 | 690 |

Ergebnis (BELEGT): **idx 11 = Single-Turn-Position 0..3199 Counts/Umdrehung** (80 Counts/mm, 3200 = 40 mm =
1 Umdrehung = 200 Vollschritte × 16), **idx 5 pos_fdb = idx11 · 2π/3200 [rad]** (auf 5 Stellen). Beide laufen
alle 40 mm über (50-mm-Schritte zeigen daher nur +π/2 = Aliasing). idx 6 spd_fdb im Stillstand ≈ 0 (±0,006).
Vor G28 (Motor stromlos): idx 5 = 0.0, idx 11 = 1106. Vorgang: G28 X Y → „Must home axis first“ bei G1
(Creality verlangt auch Z) → G28 komplett → Messfahrt → Park X175 → M84. Klipper durchgehend ready.

## MOTOR_GET idx 1–12 während konstanter X-Fahrt (16.10.2026, nach G28, Bett leer, User-Freigabe)

Motor X (0x81), G1 X40→X300 bei F600 (10 mm/s) bzw. F1200 (20 mm/s); Abfragen nacheinander während der Fahrt
(Abstand je Abfrage ~0,1–0,3 s → Positionswerte zeitversetzt). Erwartet bei rotation_distance 40:
10 mm/s = 0,25 U/s = 1,5708 rad/s; 20 mm/s = 3,1416 rad/s.

| idx | F600 | F1200 | Stillstand | Deutung |
|---|---|---|---|---|
| 1 | 5.188 | 1.089 | 0 / folgt pos | **pos_ref** (Soll-Winkel rad, single-turn) – vermutet, sehr plausibel |
| 2 | 1.633 | 3.201 | 0 | **spd_ref** (Soll-Drehzahl rad/s) – vermutet, sehr plausibel |
| 3 | 0.0 | 0.0 | 0 | **id_ref** (d-Strom-Soll, bei FOC = 0) – vermutet |
| 4 | 0.0794 | 0.0592 | 0 | **iq_ref** (q-Strom-Soll; ≈ iq_fdb 0.088/0.070) – vermutet |
| 5 | 5.769 / 2.561 | 2.211 / 2.106 | – | pos_fdb (rad) – belegt |
| 6 | 1.529 / 1.572 | 3.454 / 3.110 | ≈0 | **spd_fdb = rad/s** (1,57 bzw. 3,14 erwartet) – BELEGT |
| 7 | -0.0028 | 0.0041 | ≈0 | id_fdb – belegt (Name) |
| 8 | 0.0880 | 0.0705 | ≈0 | iq_fdb – belegt (Name) |
| 9 | -0.0725 | -0.0330 | 0.00161 | offen (Regelabweichung? Spannung?) |
| 10 | -0.0363 | 0.0008 | 0.00081 | offen |
| 11 | 222 | 2096 | – | pos_counts (0..3199) – belegt |
| 12 | 1 | 1 | 0 | **Bewegungs-/Regler-aktiv-Flag** (uint) – vermutet |

Schema-Hypothese: idx 1–4 = Sollwerte (pos/spd/id/iq _ref), idx 5–8 = Istwerte (_fdb, Host-Namen belegt) –
typische FOC-Kaskade (Positions-→Drehzahl-→Stromregler). Klipper durchgehend ready, am Ende M84.

## Gegenprobe idx 12 = DIR-Eingang (16.10.2026, Motor X 0x81, Fahrten ±X)

Anlass: Der Motor-FW-Befund (`MOTOR-FW-BEFUND-2026-10-16.md` §3) deutet idx 12 als Pegel eines
GPIO-Eingangs, der bei Flanken die Zählrichtung umschaltet (DIR-Eingang) – und **nicht** als
Bewegungs-/Regler-aktiv-Flag (Hypothese im Abschnitt davor). Vorhersage: Fahrt in Gegenrichtung → 0.

| Zustand | idx 12 | Begleitwert |
|---|---|---|
| nach Einschalten, Stillstand (noch keine Fahrt) | 0 | – |
| Fahrt −X | **0** | spd_fdb (idx 6) −1,81 rad/s |
| Fahrt +X | **1** | – |
| Stillstand nach +X | 1 (hält letzte Richtung) | – |

Ergebnis: **idx 12 = DIR-Eingangspegel – BESTÄTIGT.** Der Wert folgt der Fahrtrichtung und bleibt
im Stillstand auf der zuletzt gefahrenen Richtung stehen; „1 in Fahrt / 0 im Stillstand“ aus dem
Abschnitt davor war nur die Richtung +X bzw. der Einschaltzustand. Damit ist die Hypothese
„Bewegungs-/Regler-aktiv-Flag“ widerlegt. Nachbau: `motor.read_dir_input()`, Name `dir_input`.
