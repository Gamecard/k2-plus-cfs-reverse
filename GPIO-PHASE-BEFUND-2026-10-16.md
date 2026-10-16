# GPIO-Phase (Motor-Adress-Strapping) und MCU-Schnittstelle – Befund 16.10.2026

Statische Analyse (zwei Zweitanalysen mit einem weiteren KI-Agenten, von uns gegen Dictionary, Config
und Boot-Log nachgeprüft). Arbeitsdateien und extrahierte Dictionaries lokal, nicht im Repo.
Ergänzt `MOTOR-INIT-BEFUND-2026-10-16.md` (Ablauf der Init) um die MCU-Seite.

## 1. MCU-Firmware (K2 Plus, F008)

| MCU | Image | MCU-Konstante | Version |
|---|---|---|---|
| Main (GD32F303RET6, ttyS2) | `mcu0_140_G32-mcu0_022_000` | `gd32f303xe` | 1.1.0.48-293-g493f9a0f-dirty-20241220_140327 |
| Düse (GD32F303CBT6, ttyS3) | `noz0_130_G30-noz0_021_000` | `gd32f303xb` | 1.1.0.48-293-g493f9a0f-dirty-20241220_143931 |

Beide: 75 Befehle / 32 Antworten, gleiche Codebasis und IDs (Unterschiede nur MCU, INITIAL_PINS, Build-Zeit).
Data-Dictionary per zlib aus dem Image extrahiert (BELEGT). Alle Strapping-Pins (PB9, PB10, PB7, PB8, PB5, PB6,
PA1, PB15, PA9; Düse PB12) liegen in der Pin-Enumeration.

## 2. Für die Motoren relevante Befehle

| Zweck | Befehl / Antwort | Herkunft |
|---|---|---|
| Pin direkt setzen | `set_digital_out pin=%u value=%c` | Upstream-Klipper |
| Pin konfigurieren/schalten | `config_digital_out oid pin value default_value max_duration`, `update_digital_out`, `queue_digital_out` | Upstream |
| Pin zurücklesen | `query_gpio_status oid=%c gpio_pin=%c` → `gpio_status oid=%c status=%c` | **Creality** |
| Extruder-Motor (Düsen-MCU) | `config_transparent oid=%c`, `transparent_send oid=%c write=%*s timeout_ms=%u` → `transparent_response oid=%c read=%*s` | **Creality** |
| Strain-Probe | `config/start/stop/read/cont_prtouch_*` → `ack_prtouch`, `resault_prtouch_*` | Creality |
| Lüfterüberwachung | `config_fancheck`, `query_fancheck` → `fan_status` | Creality |

## 3. Ablauf der GPIO-Phase (Boot-Log, Zeitstempel)

1. 8× Pin-Abfrage (`query_gpio_status`) → „initial set_pin_io done“
2. 0,2 s → Broadcast `FF 0E 01` über RS485 (≈70 ms Timeout) und über transparent (≈2 s Timeout)
3. Runde 1 `0E 02` (live antworteten nur Z + E) → 1,0 s → Runde 2 (alle) → „All motors are normal“
4. Stall-Mode `11 02`, Schutzabfrage `0C 0b`, `0C 05`
5. 8× Restore-Abfrage (alle Pegel 0) → „restore set_pin_io done“ → ≈1,0 s → Parameter-Abgleich

Kein Spezialbefehl für das Übersteuern der Stepper-Pins: `[duplicate_pin_override]` in der printer.cfg erlaubt
`config_stepper` und `config_digital_out` auf denselben Pins (Zeitmultiplex, Synchronisation per `M400`).
Adressformel (Motor-FW): Adresse = 0x81 + 2·STEP + DIR (siehe MOTOR-INIT-BEFUND).

## 4. Folgen für einen Pi-Host

| Variante | Strapping | Zusätzlich nötig |
|---|---|---|
| Creality-MCU-FW + Creality-Host | wie Stock | Creality-`.so` sind armhf/cp39 (T113); Teile von `klippy.py`/`mcu.py` fehlen im Image |
| **Creality-MCU-FW + Upstream-Klipper** (empfohlen) | mit Upstream-Mitteln (`digital_out`/`set_digital_out` + `duplicate_pin_override`) vor dem Broadcast | eigenes Modul: Motor-Protokoll (0E/06/0B/0C/11/12), Param-Abgleich (227er-Tabelle, isclose 1e-6), optional `query_gpio_status`-Rücklesung, Extruder über `transparent_*` – `nachbau/motor_init.py` als Vorlage, Stock-Bug 0x85 nicht nachbauen |
| Upstream-MCU-FW | – | zusätzlich `transparent_*` und `query_gpio_status` in die Firmware portieren – deutlich mehr Aufwand |

Offen: Extraktion des Dictionaries von `mcu0_130` zeigte kein `transparent_*` – vermutlich Extraktionslücke,
nicht geprüft.

## KORREKTUR 16.10.2026 abends – Basisadresse der Klipper-MCUs (Main + Düse)

Die Main-MCU- und Düsen-MCU-Images (`mcu0_140…`, `noz0_130…`) liegen ab **0x08003000** (davor Bootloader), NICHT ab 0x08000000.
Belege (eigene Prüfung): Reset-Handler bei 0x080033F8 ist nur mit Basis 0x08003000 ein gültiger Prolog (`cpsid i`, SP setzen, Sprung);
158 von 233 Thumb-Zeigern im Image treffen mit 0x08003000 einen Funktionsprolog, mit 0x08000000 nur 6. Klipper-Befehlstabelle
(Main) bei 0x0800743C, 12-Byte-Einträge [Handler | Info (Byte0 = msgid) | param_types].
Folge: **Adressangaben** der früheren MCU-Analysen (PrTouch-MCU, Bit-Spezifikation, Stopp-Analyse) sind ungültig; Data-Dictionaries
(zlib) sind nicht betroffen. Die Kernaussage „prtouch-Code stoppt die Stepper nicht selbst“ wurde mit korrekter Basis bestätigt:
kein Aufruf des Stopp-Helfers von `stepper_stop_on_trigger`; die einzige gemeinsame Pin-Funktion setzt im prtouch-Lesepfad den
Swap-Pin (Synchronisation). Plausibler Stopp-Weg (ANNAHME): Host hängt die Z-Stepper per `stepper_stop_on_trigger` an ein trsync
und löst bei erkanntem Kontakt `trsync_trigger` aus.
