# BUSTEST-BEFUND 16.10.2026 – erster Test unseres Protokollcodes am echten RS485-Bus

**Ergebnis: 78/78 Frames erfolgreich, 0 Timeouts, 0 Retries, 0 CRC-Fehler.**
Erstmals hat **unser eigener Code** (nachbau/`serial485.py` + pi-host/`k2_box.py`
`K2BoxController` + pi-host/`k2_motor_init.py` `K2MotorController` über
`k2_transport.Rs485Transport`) als **alleiniger Besitzer von `/dev/ttyS5`** mit
der CFS-Box und den vier Closed-loop-Motoren gesprochen – so wie später auf dem Pi.

## Aufbau

- Drucker im Standby, Bett leer. Creality-Klipper per `/etc/init.d/klipper stop`
  gestoppt (einziger ttyS5-Nutzer war klippy.py; Prüfung über `/proc/*/fd`).
- Testskript `pi-host/bustest_lesend.py` lief **auf dem Drucker** (Python 3.9,
  pyserial 3.4), danach `/etc/init.d/klipper start`. Ablauf entkoppelt per
  `start-stop-daemon -S -b` (ein `nohup … &` stirbt beim SSH-Ende mit – beobachtet).
- **GuardBus**: feste Whitelist NUR lesender Frames, alles andere →
  `PermissionError` vor dem Senden (Selbsttest: `0x81 0x0E [01]` blockiert).
  `allow_write` blieb False. Dauer am Bus: ~13 s.

| Ziel | Funktion | Daten |
|---|---|---|
| Box 0x01 | 0x14 Version/SN, 0x0A Poll (3×), 0x15 Hardware, 0x08 Sensor [0]/[1], 0x05 Buffer, 0x0E Messrad [01] (lesen), 0x03 Restlänge [0F], 0x02 RFID je Slot | nur lesend |
| Motoren 0x81–0x84 | 0x0F [00] Version, 0x0C [0B] Schutz, 0x08 idx 1–12 | nur lesend |

## Ergebnisse (Auszug)

- **Box:** FW `153`, SN `<SN>`; Poll `STATE_IDLE`, 23 °C / 54 % rF, keine Blockaden;
  Hardware-Status alle 6 Komponenten ok (EEPROM, AHT30, MT6826, Buffer, RFID 0/1);
  Sensor A = 7, Hub = 0, Buffer = 2, Messrad 1804,57 mm; RFID A–C `unknown`, D `none`.
- **Motoren X/Y/Z/Z1:** FW `1.9.7.81` (cmd 1.3, prot 1.3, flash 2.75, logger 1.0),
  Schutz 0/0 (keine Fehler/Warnungen).
- **Kreuzprobe Dekodierung (stark):** Bei allen vier Motoren ist idx 11 (Counts)
  **exakt** `pos_fdb(idx 5) / 2π · 3200` (X 1095/1095, Y 2144/2144, Z 2005/2005,
  Z1 2367/2367); idx 1 (pos_ref) = idx 5 auf 4 Stellen. Z/Z1 mit Haltestrom
  iq ≈ −0,10 (Bett gegen Schwerkraft), X ≈ 0 – plausibel. idx 12 DIR X/Z/Z1 = 1, Y = 0.
- Motoradressen 0x81–0x84 bleiben bei gestopptem Host erhalten (solange Strom an).

## Wichtige Nebenbefunde (Rückweg in den Stock-Betrieb)

1. **Klipper-Dienst-Neustart ohne MCU-Reset → Extruder-Motor stumm.** Nach
   `/etc/init.d/klipper start` beantwortete der Extruder-Motor (transparent über
   die Düsen-MCU, 0x81) weder den Broadcast `ff 0e 01` noch `81 0e 02`
   (`transparent_response read=b''`, 4 Runden) → `key798 "Motor connection failed,
   exceeding maximum retry count" ['e']` → Shutdown. **Nicht** durch den Test
   verursacht (Extruder hängt nicht an ttyS5; Whitelist hätte ihn blockiert).
   **Abhilfe belegt:** `FIRMWARE_RESTART` (Moonraker `/printer/firmware_restart`)
   → Extruder antwortet sofort (`f7 81 04 00 0e 81 00`, 0x11, 0x0C ok), `ready`.
   Für künftige Tests: Dienst starten **und gleich danach FIRMWARE_RESTART**.
   Relevanz Pi: `[k2_motor]` muss bei leerer transparent-Antwort nach Host-Neustart
   mit einem MCU-Reset rechnen (Ursache – Düsen-MCU-/Motorzustand – offen).
2. **Stock-Klippy-Absturz im Shutdown:** ~80 s nach dem key798-Shutdown endete
   klippy.py mit `RecursionError` (webhooks `_do_query` → `json.dumps`, parallel
   1-Hz-Polling `85 04 00 13 02` – erneut der Stock-Bug 0x85 auf ttyS5).
   procd startet Klipper **nicht** neu (kein respawn) → Moonraker „Klippy Host not
   connected“, FIRMWARE_RESTART dann unmöglich; nur Dienststart hilft.

## Bedeutung

- Transport (Framing, CRC, Absender-/fun-Prüfung, Status-Byte, Timeouts) und die
  Lese-Parser von `[k2_box]`/`[k2_motor]` sind **am echten Gerät bestätigt**.
- Noch NICHT getestet: Motor-Init mit Strapping (braucht MCU/Klipper), Extruder
  über transparent, alle schreibenden Box-Abläufe (Preload/Extrude/Retrude),
  Belt-Module 0x21/0x22, `[k2_prtouch]`.
