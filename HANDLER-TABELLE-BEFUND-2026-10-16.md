# HANDLER-TABELLE-BEFUND 16.10.2026 – MCU-Befehlstabelle war um einen Eintrag verschoben

**Kurz:** Die früher erstellte Zuordnung „Befehls-ID → Handler-Adresse“ der Creality-MCU-Firmware
(Main GD32F303RET6 und Düse GD32F303CBT6, Version 1.1.0.48-293-g493f9a0f) war **um einen Eintrag
verschoben**. Ursache: Die 12-Byte-Einträge wurden als `func | info | param_types` gelesen, tatsächlich
ist das Layout wie in Upstream-Klipper (`src/command.h`, alte 8-Bit-msgid-Variante)
**`info(msgid, num_args, flags, num_params) | param_types | func`**. Damit gehört der als „func“
gelesene Zeiger jeweils zum **vorigen** Befehl.

## Beleg (selbst nachgerechnet am Image, BELEGT)

- Main: Tabelle ab Datei-Offset **0x7434** (VMA 0x0800A434 bei Basis 0x08003000), Düse ab **0x7410**.
  Mit dem Upstream-Layout laufen die msgids **75/75 lückenlos 1..75**, `num_params` stimmt mit den
  Dictionaries überein.
- Code-Gegenproben (Analyse mit Ghidra, Kernpunkte nachgeprüft): `identify` 0x080058A4 liest offset/count
  und die Dictionary-Daten; `config_digital_out` 0x08006050 = `gpio_out_setup` + `oid_alloc(0x30)`;
  `config_endstop` 0x08005BF0 = `oid_alloc(0x24)` + `gpio_in_setup`; `endstop_query_state` 0x08005AF4 =
  `gpio_in_read` + `sendf`; `config_transparent` 0x080036AC = `oid_alloc`; `query_gpio_status`
  0x08003424 liest GPIOx_OCTL (Ausgangs-Latch).

| ID | Befehl | Handler (Main, Düse gleich wo vorhanden) |
|---|---|---|
| 24 | config_digital_out | 0x08006050 |
| 25 | stepper_stop_on_trigger | 0x08005CDC |
| 31 | endstop_query_state | 0x08005AF4 |
| 32 | endstop_home | 0x08005A88 |
| 33 | config_endstop | 0x08005BF0 |
| 34/35/36 | trsync_trigger / _set_timeout / _start | 0x08005920 / 0x08005A50 / 0x080065F0 |
| 41 | cont_prtouch_step | 0x08003664 |
| 42 | read_prtouch_step | 0x08003E88 |
| 43 | stop_prtouch_step | 0x0800423C (liest Swap-Pin einmal per gpio_in_read) |
| 44 | start_prtouch_step | 0x08003B68 |
| 45 | config_prtouch_step | 0x080048FC |
| 46 | read_prtouch_pres | 0x08003F60 |
| 47 | stop_prtouch_pres | 0x0800428C (setzt Swap-Pin auf sta_swap 0/1) |
| 48 | start_prtouch_pres | 0x080042C4 (10 Parameter, setzt Swap-Pin HIGH) |
| 49 | config_prtouch_pres | 0x08004A70 (Pin-Setup swp/clk/sdo) |
| 62/63 | query_lis2dw / config_lis2dw | 0x08004F58 / 0x080037A8 |
| 73/74/75 | transparent_send / config_transparent / query_gpio_status | 0x080034A0 / 0x080036AC / 0x08003424 |

Vollständige Tabelle: lokale Arbeitsdatei `HANDLER-TABELLE-KORRIGIERT.txt`.

## Folgen für frühere Dokumente

- **PRTOUCH-STOPP-BEWEIS-2026-10-16.md:** Die Handler-Adressen in §2/§3 gehören zum jeweils
  benachbarten Befehl. Falsch waren damit u. a. „`stop_prtouch_pres` treibt nicht, `sta_swap` nur Flag“
  (der echte Handler 0x0800428C schreibt den Pin – passt zum Live-Befund) und der Negativbeleg „der
  Trigger-Pfad schreibt nicht“ (es gibt Inline-BSRR/BRR-Zugriffe ohne Aufruf des Helfers). §1 war bereits
  durch PRTOUCH-LESETEST-BEFUND §3 widerlegt. Richtig bleiben die GPIO-Helfer-Adressen und die Basis
  0x08003000; auch mit korrigierten Adressen ruft kein prtouch-Handler endstop/trsync direkt auf – der
  Stopp läuft über den normalen Endstop auf der Swap-Leitung.
- **PRTOUCH-BEFUND-2026-10-16.md:** „kein MCU-Hardwarestopp“ ist überholt (s. o.); die Tabellenadresse ist
  als VMA 0x0800A434 (Main) / 0x0800A410 (Düse) zu lesen.
- **GPIO-PHASE-BEFUND / RE-RESTE-BEFUND:** nennen keine Handler-Adressen – nicht betroffen.
- **Unsere Module:** nicht betroffen (sie stützen sich auf Dictionary-Formate und Live-Belege, nicht auf
  Handler-Adressen).

Analyse: weiterer KI-Agent (Ghidra headless), Kernaussagen von uns am Image nachgerechnet.
