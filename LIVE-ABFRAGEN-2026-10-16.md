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
