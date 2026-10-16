# BEWEIS: Z-Stopp beim Düsenkontakt (Creality K2 Plus, PrTouch v3) — statischer Nachweis

> **KORREKTUR 2 (16.10.2026, HANDLER-TABELLE-BEFUND-2026-10-16.md): Die Handler-Adressen in §2/§3 sind um einen Eintrag verschoben** (Tabellen-Layout `info|param_types|func`). Insbesondere ist „`stop_prtouch_pres` treibt nicht“ falsch – der echte Handler setzt den Swap-Pin auf 0/1.


> **KORREKTUR 16.10.2026 (PRTOUCH-LESETEST-BEFUND-2026-10-16.md §3): §1 ist WIDERLEGT.** Die Emulation des Stock-Moduls zeigt `config_endstop oid=1 pin=PC7` + `config_trsync` + `stepper_stop_on_trigger` und Homing-Event-Handler – der Z-Stopp läuft über einen normalen Klipper-Endstop auf der Swap-Leitung (Düse PA15 → Main PC7). Die Aussagen zu den prtouch-MCU-Befehlen selbst (§2/§3: sie stoppen nichts) bleiben gültig.


> Statischer Beweis (weiterer KI-Agent, Ghidra mit korrekter Basis 0x08003000); Kernstellen von uns nachgeprüft: 0x0800426C = gpio_out_write (BSRR/BRR), 0x0800411C = gpio_in_read (IDR), Host-Modul ohne home_start/home_wait/add_stepper/trsync/setup_pin. Arbeitsdateien lokal, nicht im Repo.


Arbeitsordner: `<projekt>/arbeitsdateien-lokal/lokale Arbeitsdateien ` — Ergebnis diese Datei.
Stand: 16.10.2026. **NUR STATISCHE ANALYSE** (kein Netzwerk, kein ssh/curl, kein Druckerzugriff).
Regel: Jede Aussage trägt Belegstelle (Adresse/Dekompilat-Zeile oder Python-Zeile/String-Tabelle) und Einstufung `BELEGT` oder `ANNAHME`.
`BELEGT` = Code/String oder Code+Datei. `ANNAHME` = statisch nicht entscheidbar, Kandidat mit Begründung.
Schreibweisen `resault_*`, `aqc_tick` sind Firmware-Realität und werden exakt übernommen.

Quellen (nur gelesen, außer diesem Ordner):
- `main.bin` (Main GD32F303RET6, 30948 B), `noz.bin` (Düse GD32F303CBT6, 30872 B) in diesem Ordner [BELEGT: `ls -l`].
- `HANDLER-TABELLE.txt` (74+74 Einträge, Adressen absolut) in diesem Ordner.
- Host-Modul `rootfs-live/usr/share/klipper/klippy/extras/prtouch_v3_wrapper.cpython-39.so` (ELF32 armhf, Cython) [BELEGT: `readelf -h`, `strings`].
- Vorarbeit `../lokale Arbeitsdateien ` (`disasm.txt`, `pyx_strtab.json`, `pyx_tuples.json`, `funcs.json`, `liveconfig_*`) und `../lokale Arbeitsdateien SPEZIFIKATION.md`, `../lokale Arbeitsdateien Agenten-Befund (lokal)` — darauf aufgebaut, nicht wiederholt.
- Config `live-1.1.7.0-configs/printer-live.cfg`, Ghidra headless `/opt/ghidra/ghidra_12.1.4_PUBLIC/support/analyzeHeadless` [BELEGT: Version/Help].
- Alle Ghidra-Artefakte in diesem Ordner: `ghidra_proj/prtouch_main|noz`, `scripts/Q.java|ListAsm.java|ListAsm2.java`, `asm_*.txt`, `decomp_*.txt`, `xref_*.txt`.

## 0. Korrekte Basis (beide MCU-Images ab 0x08003000)

- Basis `0x08003000` [BELEGT: `asm_reset.txt:0x080033f8` zeigt `cpsid i; ldr r3,[0x08003404]; ldr r2,[0x08003408]; mov sp,r3; bx r2` — exakt das im Auftrag genannte Reset-Muster `cpsid i; SP setzen; bx`; File-Off `0x080033f8-0x08003000=0x3f8`, Bytes dort `72b6024b…` (`0xb672`=`cpsid i`) — bei Basis `0x08000000` läge Reset bei File-Off `0x33f8`, dort liegt kein Prolog; Vektor File-Off `0x04`=`0x080033f9` (Thumb-Bit, Ziel `0x080033f8`) passt nur zu `0x08003000`].
- Handler-Adressen aus `HANDLER-TABELLE.txt` sind gültiger Thumb-Code [BELEGT: `asm_main.txt:0x08005a88 push {r3,r4,r5,lr}`, `asm_main.txt:0x08003b68 push {r4,lr}`, `asm_main.txt:0x080042c4 push {r4,r5,r6,r7,lr}`, `asm_callees.txt:0x08004660 push…`, Stichprobe `python3 struct`-Scan: `0x08005a88:0xb538`, `0x08005ef0:0xb510`, `0x08003b68:0xb510`, `0x080042c4:0xb5f0` = `push`-Prologe; Ghidra-Loader `BinaryLoader -loader-baseAddr 0x08003000 -processor ARM:LE:32:Cortex` importiert fehlerfrei (`ghidra_proj/prtouch_main|noz`)].
- Befehlstabelle: `HANDLER-TABELLE.txt:1` (`Main @0x0800743c`) / `:76` (`Düse @0x08007418`), 12-B-Einträge, Formate z. B. `:45 config_prtouch_step oid=%c oid_xstp=%u oid_ystp=%u oid_zstp=%u swp_pin=%u`, `:48 start_prtouch_pres … ned_tftr/ned_hftr/ned_lftr min_hold/max_hold/add_hold lmt_dead`, `:49 config_prtouch_pres … swp_pin=%u …`, `:32 endstop_home … trsync_oid …`, `:25 stepper_stop_on_trigger … trsync_oid …`, `:34-37 trsync_*` [BELEGT: Datei-Zeilen]. Hinweis: Flash bei `0x0800743c` disassembliert als Code (`asm_reset.txt:0x0800743c bl 0x080043f4…`, nicht als Tabelle) [BELEGT: Datei-Zeile] — ob die Tabelle zur Laufzeit im RAM aufgebaut wird oder die Adresse einen anderen Revisionsstand meint, ist statisch nicht entscheidbar [ANNAHME/offen]; die Handler-Adressen selbst sind trotzdem gültig (s. Prologe oben).
- Werkzeugkette [BELEGT: `scripts/ListAsm2.java`, `asm_*.txt`, `xref_*.txt` in diesem Ordner; `llvm-objdump 23.1.1` kennt `arm`-Target, aber kein `-b binary`, daher Ghidra wie beauftragt].

## 1. Host: kein Standard-Klipper-MCU_endstop auf step_swap_pin, kein normales Homing

### 1.1 Negativbeleg: keine Homing-Endstop-API im Wrapper

- `strings -a prtouch_v3_wrapper.cpython-39.so | grep -c` ergibt `0` für jeden der Standard-Homing-Begriffe [BELEGT: Shell-Zählung, reproduzierbar]:
  `setup_pin:0`, `add_stepper:0`, `home_start:0`, `home_wait:0`, `endstop_home:0`, `trsync:0`, `stepper_stop:0`, `z_virtual:0`, `query_endstop:0`, `config_endstop:0`, `probe:z:0`.
- Einziger `HomingMove:1` [BELEGT: `strings`-Treffer `HomingMove` bei File-Off `0x1305d5`, `pyx_strtab.json:0x14e51c HomingMove`] — Klassenname/Typ, kein `home_start/wait`-Aufruf (diese Strings fehlen).
- Das heißt: Der Wrapper ruft **weder** `ppins.setup_pin('endstop',…)` **noch** `MCU_endstop(mcu,pin_params)` mit `parse_pin(step_swap_pin)` **noch** `add_stepper/get_steppers→home_start/home_wait` **noch** `endstop_home/trsync/stepper_stop_on_trigger` auf [BELEGT: Negativbeleg Strings; `pyx_strtab.json` enthält `ppins 0x14e1dc`, `parse_pin 0x14e1e0`, `MCU_endstop 0x14de8c`, `get_steppers 0x14eb0c` als vorhandene, aber `setup_pin/add_stepper/home_*` fehlen vollständig].

### 1.2 Was vorhanden ist: Probe-API, kein Endstop

- Vorhandene Strings [BELEGT: `strings -a` + `pyx_strtab.json`]: `MCU_endstop 0x14de8c`, `ppins 0x14e1dc`, `parse_pin 0x14e1e0`, `swap_pin 0x14e1e4`, `step_swap_pin 0x14e954`, `pres_swap_pin 0x14ed44`, `get_steppers 0x14eb0c`, `homing_move 0x14e524`, `mcu_probe 0x14e520`, `get_position_endstop 0x14ea38`, `HomingMove 0x14e51c`, `add_config_cmd 0x14e1ec`, `lookup_query_command 0x14e1fc`, `config_prtouch_pres 0x14e1f4`, `config_prtouch_step 0x14eb14`, `homing:homing_move_begin 0x14e9e8`, `homing:home_rails_begin 0x14e9f0` u. a.
- `config_prtouch_step oid=%d oid_xstp=%d oid_ystp=%d oid_zstp=%d swp_pin=%s` [BELEGT: `pyx_strtab.json:0x14eb14`] und `config_prtouch_pres oid=%d idx=%d swp_pin=%s clk_pin=%s sdo_pin=%s` [BELEGT: `0x14e1f4`] werden per `add_config_cmd`/`lookup_query_command` registriert [BELEGT: Strings `0x14e1ec/0x14e1fc`]; Tupel-Slots `0x14cd14/0x14cd7c/0x14cd28/0x14cd90/0x14cd9c/0x14cd1c` für `start/read/cont/stop_prtouch_*` [BELEGT: `pyx_tuples.json`].
- `PRTouchEndstopWrapper` (`funcs.json: PRTouchEndstopWrapper.__init__ 0x88364 (71376 B)`, `_build_config 0x86cfc (5736 B)`) + `PrtouchStep.build_config 0xa6ba0` + `PrtouchPres._build_config 0x38ae8` [BELEGT: `funcs.json`, `disasm.txt:129554 0x86cfc: push…`, `:130988 0x88364: push…`] implementieren eine **Probe** (`mcu_probe`, `homing_move`, `get_position_endstop`, `get_steppers`, `homing:homing_move_begin/end`, `home_rails_begin/end` als Event-Listener) [BELEGT: Stringliste oben], keinen `Endstop` im Klipper-Homing-Sinn.
- Pins aus Config [BELEGT: `printer-live.cfg:333 step_swap_pin: !PC7`, `:334 pres_swap_pin: nozzle_mcu:PA15`, `:339 pres_cs0_pin: …`; `liveconfig_prtouch_resolved.txt: step_swap_pin !PC7 / pres_swap_pin nozzle_mcu:PA15 / pres_tri_hold (4000,10000,500) / pres_tri_fter (5,1,0.8)`]: `!PC7` ohne `nozzle_mcu:`-Präfix = Main-MCU, `PA15` mit Präfix = Düsen-MCU. Diese Strings werden als `%s`-Pins an `config_prtouch_step/pres` (`swp_pin=%s`) übergeben und hostseitig per `parse_pin` in `%u`-Pin-IDs aufgelöst [BELEGT: Formatpaar MCU `%u` vs Host `%s`, `SPEZIFIKATION.md §1.1`], **nicht** an `MCU_endstop`/`setup_pin`.
- `PR_ERR_CODE_SWAP_PIN_DETECTI: The synchronization pin test failed, pres_swap_pin={0}, step_swap_pin={1}.` [BELEGT: `pyx_strtab.json:0x14dd1c`] + `TEST_SWAP` (`cmd_TEST_SWAP 0x21f50`, Help `Test The Swap Pin.`) mit `PY_VER/PRES_VER/STEP_VER` [BELEGT: `funcs.json`, `pyx_strtab.json:0x14e064`] belegen: Swap = **Synchronisation** (Zeitbasis), nicht Stopp. Kein `trsync_oid` in PrTouch-Formaten [BELEGT: Negativbeleg `HANDLER-TABELLE.txt:45,48,49` vs `:25,32,34-37`], also kein Klipper-`trsync`.
- Wie `get_steppers/homing_move/get_position_endstop` konkret verdrahtet sind (Probe-Helper vs HomingMove), ist ohne `.py`-Quelle nur über ARM-Disassembly (`disasm.txt`, Cython-Modul-State per `[r6,#off]`-Ladevorgänge, z. B. `disasm.txt 0x88b64ff: ldr r1,[r6,…]`-Muster) auflösbar und wurde hier nicht vollständig auf Offsets gemappt [ANNAHME: Probe-Pfad wie in `SPEZIFIKATION.md §§4-6` (timed `homing_move` mit `halt_step_dist`, danach `read_*`, kein `trsync`); der Negativbeleg (kein Endstop-Homing) steht davon unabhängig].

## 2. Main-FW: config_prtouch_step richtet swp_pin NICHT als Endstop ein; Step-Seite LIEST

GPIO-Primitive (Upstream-Referenz `config_endstop→gpio_in_setup`, `config_digital_out→gpio_out_setup`, `endstop_query_state→gpio_in_read`, `update_digital_out→gpio_out_write`) identifiziert über Thumb-Listing in diesem Ordner:

- `0x0800411c = gpio_in_read` [BELEGT: `asm_prtouch.txt:0x0800411c ubfx r2,r0…; ldr r3,[0x0800413c]; … ldr r2,[r3,#0x08] (IDR); lsl/tst; return 0/1` — liest Eingangs-Datenregister].
- `0x0800426c = gpio_out_write` [BELEGT: `asm_prtouch.txt:0x0800426c and r2,r0,#0xf; lsrs r0,r0,#0x4; … ldr r2,[0x08004288]=0x0800987c (GPIO-Basistabelle); ldr.w r2,[r2,r0,lsl#2]; cbnz r1→ str r3,[r2,#0x10] (BSRR/set) sonst [r2,#0x14] (BRR/clear)`].
- `0x08004878 = gpio_in_setup` (Eingang, ruft Kern `0x08004660` mit `r1=1`) [BELEGT: `asm_callees.txt:0x08004878 cmp r0,#0x6f; … movs r1,#0x1; sxtb r2,r2; bl 0x08004660`]; `0x08004660` = GPIO-Kern (RCC `0x40021000`-Ableitung `add.w r5,#0x40000000/#0x21000`, CRL/CRH `[r4,#0x0/#0x04]`, BSRR/BRR `[r4,#0x10/#0x14]`) [BELEGT: `asm_gpio2.txt:0x08004660`].
- `0x08004970/0x08004938 = gpio_out_write`-Hülle/Kern (Bereichsprüfung `cmp r0,#0x6f`, dann `bl 0x08004938`, dort `bl 0x08004660` + `str [r3,#0x10]/[r3,#0x14]`) [BELEGT: `asm_callees.txt:0x08004970`, `asm_gpio2.txt:0x08004938`]; `update_digital_out 0x08005c14` ist nur `ldrb; ldrb; b.w 0x08004970` [BELEGT: `asm_gpio2.txt:0x08005c14`] — bestätigt `0x08004970=gpio_out_write`.
- `0x08005bf0` (Typ-Literal `0x08005bf1` aus `config_endstop`) alloziert (`bl 0x08006cec size 0x24`) und ruft `bl 0x08004878` (=Eingang) [BELEGT: `asm_lits2.txt:0x08005bf0 … bl 0x08006cec … bl 0x08004878 … strb r0,[r4,#0xc]`] — bestätigt `0x08004878=Eingang` und Upstream-Regel `config_endstop→gpio_in_setup`.

Dann PrTouch/Step auf Main:

- `config_prtouch_step 0x08003b68` (12 B laut `HANDLER-TABELLE.txt:45`) ist winzig (`push; ldr r4,[0x08003b98]=0x20001b00; … bl 0x080096a0 (memset 0x200); … bl 0x08006d30 (sched); pop`, Ende `0x08003b96`) und enthält **keinen** `bl` zu `0x08004878/0x08004988/0x08004970/0x0800411c/0x0800426c` [BELEGT: `asm_main.txt:0x08003b68:0x100` vollständig]. Also: `swp_pin` wird hier **weder per `gpio_in_setup` noch per `gpio_out_setup` eingerichtet** und weder gelesen noch geschrieben.
- `start_prtouch_step 0x0800423c` liest EINMAL: `ldr r3,[0x08004264]=0x20001b00; ldrb.w r0,[r3,#0x218] (swp_pin-Ablage hinter `0x18+0x200`-Puffer); ldr r1,[r3,#0x0]; bl 0x0800411c (=gpio_in_read)` [BELEGT: `asm_prtouch.txt:0x0800423c … 0x0800424c bl 0x0800411c`], danach `bl 0x08006d30` (Timer `0x47`) — kein Schreiben (`0x0800426c/0x08004970` kommt nicht vor).
- Keinerlei `bl` zu `endstop_home 0x08005af4`, `stepper_stop_on_trigger 0x08006050`, `trsync_* 0x08005a50ff` im Step/Pres-Pfad [BELEGT: `xref_trsync.txt` leer für diese Ziele im Main-Projekt nach Disassembly; `asm_stop_long.txt`/`asm_prtouch.txt` enthalten nur `bl 0x080096a0/0x08006524/0x080064d8/0x08006d30`, keine Homing-Bls; `endstop_home 0x08005af4` selbst ruft `bl 0x0800411c` (`asm_homing.txt:0x08005b0e`) — dieser Homing-Pfad wird von PrTouch nicht benutzt].
- Fazit Main [BELEGT]: Step-Seite **liest** (einmalig beim Start), **schreibt nicht**, kein Endstop-Setup, kein `trsync`/`stepper_stop_on_trigger`.

## 3. Düsen-FW: config/start_pres SCHREIBEN swap_pin (HIGH), Trigger-Pfad schreibt NICHT

- `config_prtouch_pres 0x080042c4` (`HANDLER-TABELLE.txt:49`) treibt `swp_pin` HIGH: `movw r3,#0x12e8; movs r1,#0x1; ldr r0,[r5,r3] (=swp_pin aus State `0x200007dc`); bl 0x0800426c (=gpio_out_write)` [BELEGT: `asm_main.txt:0x080042c4-Ausschnitt … 0x0800439a-0x080043a2 bl 0x0800426c`; `asm_prtouch.txt:0x0800428c:0x120`-Wiederholung derselben Sequenz]. Kein `bl 0x08004878` (=Eingang) in der gesamten `0x080042c4`-Sequenz (nur `bl 0x0800920c/0x08009424` (Float), `bl 0x080096a0` (memset), `bl 0x0800426c`, `bl 0x08006d30`) [BELEGT: `asm_main.txt`/`asm_prtouch.txt`]. Ob zuvor ein explizites `gpio_out_setup` (`0x08004988`-Typ) oder Initial-Pin-Setup erfolgte, ist statisch nicht sichtbar (nur `write`, kein `setup`-Bl) [ANNAHME: Pin ist zu diesem Zeitpunkt bereits Ausgang oder wird als solcher vorausgesetzt; jedenfalls Ausgangs-Richtung durch Schreiben belegt].
- `start_prtouch_pres 0x0800428c` treibt erneut (Tail-Call): `… ldr r0,[r4,#0x12e8] (=swp_pin); ldrb r1,[r5,#0x4] (=cfg_regs, live `60`); b.w 0x0800426c` [BELEGT: `asm_prtouch.txt:0x0800428c … 0x080042aa-0x080042b8 b.w 0x0800426c`]. Da `cfg_regs=60≠0`, wertet `cbnz r1` in `0x0800426c` als SET (HIGH) — konsistent mit Config-HIGH.
- **Trigger-Pfad schreibt nicht**: XRefs auf `gpio_out_write 0x0800426c` zeigen im Main-Projekt **genau 2** Aufrufer — `0x080043a2 (in FUN_080042c4)` und `0x080042b8 (Tail aus 0x0800428c)` [BELEGT: `xref_write.txt: xref:0x0800426c → 080043a2 UNCONDITIONAL_CALL FUN_080042c4 / 080042b8 UNCONDITIONAL_JUMP`]. Kein Aufrufer in Abtast-/Filter-/Trigger-Task, in `stop_prtouch_pres 0x08003f60`, `stop_prtouch_step 0x08003e88`, `read_prtouch_pres 0x080048fc`, `read_prtouch_step 0x08003664`, `cont_prtouch_step 0x0800340c` oder Timer-Callbacks. `stop_*` enthalten nur `memset/Filter/sched` (`asm_stop_long.txt:0x08003f60/0x08003e88` ohne `0x0800426c/0x0800411c/0x08004878/0x08004970`-Bls außer einem `0x0800411c`-Bl im angehängten `0x08004140`-Loop, der zu Endstop gehört, nicht zu Stop) [BELEGT: Datei-Zeilen]. Düse-Codebasis = Main-Codebasis (gleiche Handler-Adressen in `HANDLER-TABELLE.txt:76ff`, gleiche Prologe) — getrennte Noz-XRefs waren vor Disassembly leer [BELEGT: `xref_noz.txt` leer vor `ListAsm2`-Disassembly; nach Disassembly identische Opcodes erwartet, daher als ANNAHME mit starker Beleglage: gleiche Binärbasis].
- `stop_prtouch_pres sta_swap` treibt den Pin NICHT: `0x08003f60` lädt `r2=[r0,#0x04] (=sta_swap)`, prüft `cmp r2,#0x1; bhi error` (nur 0/1 zulässig), danach nur Puffer-/Filter-/Sched-Logik (`bl 0x080096a0/0x08006524/0x080064d8/0x08006d30`), kein GPIO-Bl [BELEGT: `asm_stop_long.txt:0x08003f60 … 0x08003f64-0x08004054`]. `sta_swap` ist Puffer-/Zustandsflag, kein Pin-Pegel [ANNAHME zur Semantik, BELEGT zum Nicht-Schreiben].
- Dass die Düse den Kontakt **erkennt** (Schwellen/Filter/meldung), steht außer Frage [BELEGT: `HANDLER-TABELLE.txt:48 start_prtouch_pres … ned_tftr/ned_hftr/ned_lftr min_hold/max_hold/add_hold lmt_dead`, `:46 resault_prtouch_pres … tri_chxs …`, `liveconfig … pres_tri_hold (4000,10000,500) / pres_tri_fter (5,1,0.8)`], aber die Erkennung mündet **nicht** in `gpio_out_write(swap_pin)` [BELEGT: XRef-Negativbeleg oben].

## 4. Fazit: Hypothese WIDERLEGT — stattdessen Host-seitiger Timed-Stopp + Nachberechnung

Hypothese (Prüfer): Düsen-MCU erkennt Kontakt und treibt `pres_swap_pin (PA15)` → Leitung zu `step_swap_pin (!PC7)` → Host-`MCU_endstop` → `endstop_home+trsync+stepper_stop_on_trigger` stoppt Z.

- Teil „Düse erkennt Kontakt (min/max/add_hold, ned_*, tri_chxs)": **BELEGT** (s. §3, Formate/Antwortfeld `tri_chxs`).
- Teil „Düse treibt swap_pin beim Kontakt": **WIDERLEGT** [BELEGT: nur Config/Start-HIGH, kein Trigger-Write, §3-XRefs].
- Teil „Leitung = synchronization pin": als Sync-Zeitbasis **BELEGT** (Fehlertext, `TEST_SWAP`, `ticks`-Fusion in `SPEZIFIKATION.md §4`), als Stopp-Leitung **WIDERLEGT** (s. oben).
- Teil „Host legt Standard-`MCU_endstop` auf `step_swap_pin` an → normales Homing": **WIDERLEGT** [BELEGT: §1-Negativbeleg (kein `setup_pin/add_stepper/home_start/home_wait/endstop_home/trsync/stepper_stop`), keine PrTouch→Homing-Bls in §2].
- Gesamturteil: **WIDERLEGT**. Der Z-Stopp erfolgt **nicht** per Hardware-Endstop über Swap-Pin.

Stattdessen (statisch belegter Mechanismus):

- Host fährt **zeitgesteuerte** Abwärtsfahrt (`homing_move`/`syn_probe`, `speed 5.0`, `halt_step_dist`-Fenster; kein `trig_pos`) und zeichnet parallel `pres` (49,8 ms, `pres_acq_tkms 0.78125`) + `step` (24,4 ms) auf [BELEGT: `SPEZIFIKATION.md §§2,6`, `liveconfig speed/samples`, `HANDLER-TABELLE.txt:44,46,48` ohne `trsync_oid`].
- Danach `read_prtouch_pres/step (+cont)` → `resault_*` mit `tri_chxs/buf_len/ticks/datas` [BELEGT: `:42,46` + Tupel-Slots], Host fusioniert (`unzip_data 0xfc190`, `get_bst_time 0xfd360`, `get_bst_zoft 0x32134`, `calc_z 0x243dc`, `PROBE_PRES/STEP_INFO`-Strings) zu `best_indx/bst_indx/tri_pose/bst_pose/bst_zoft` und bricht die Fahrt hostseitig ab (`probe_finish 0x7f660`/`multi_probe_end 0x7e3a0`, `stop_prtouch_pres/step`) [BELEGT: `funcs.json`, `pyx_strtab.json:0x14e8a0/0x14e8a8/0x14ee08`, `SPEZIFIKATION.md §5`; exakte Filterformel ANNAHME].
- Swap-Pins (`!PC7`/`PA15`) dienen nur der **gemeinsamen Zeitbasis** (`ticks`-Nullpunkt, `TEST_SWAP`-Prüfung vor jeder Tastung) [BELEGT: §1-Fehlertext, §2-Lese/§3-Schreib-Richtung (Main liest einmal, Düse schreibt HIGH), keine Trigger-Flanke].

## 5. Folgerung für ein Upstream-Klipper-Modul (minimal, unveränderte MCU-FW)

- **Kein** `MCU_endstop` auf `!PC7`, **kein** `endstop_home/trsync/stepper_stop_on_trigger`-Umbau nötig oder sinnvoll [BELEGT: §§1-3] — ein `Endstop auf !PC7 + prtouch-Konfiguration/Start auf beiden MCUs` im Sinne der Hypothese würde ins Leere laufen (Pin wird nur einmal gelesen bzw. HIGH getrieben, kein Trigger).
- Minimal stattdessen [BELEGT: Formate/IDs/Pins §§1-3; Verfahren ANNAHME nach `SPEZIFIKATION.md §§5-6`]:
  1. `config_prtouch_pres` (Düse, `swp_pin=nozzle_mcu:PA15`, `clk/sdo` aus Wrapper-Build/`adc_par…` — Pins nicht in `printer-live.cfg`, daher Ghidra/Live-Mitschnitt nötig) + `config_prtouch_step` (Main, X/Y/Z-OIDs, `swp_pin=!PC7`) → `finalize_config`.
  2. Vor jeder Tastung `TEST_SWAP`-Äquivalent (Versionen + Sync prüfen, bei Fehler `PR_ERR_CODE_SWAP_PIN_DETECTI`-Abbruch).
  3. `start_prtouch_step (aqc_tick≈45700)` + `start_prtouch_pres (cfg_regs=60, acq_tick=93750, ned=(5,1,0.8), holds=(4000,10000,500))` mit exakten Formatstrings inkl. `aqc`/`resault`.
  4. Langsame, **kurze** Z-Fahrt mit normalen `queue_step`-Moves (5 mm/s, Lüfter 0,6), **kein** `endstop_home/trsync` — Stopp hostseitig nach `read_*`-Auswertung; Fenster deckt nur ~0,25/0,12 mm ab (Sicherheitshinweis).
  5. `read_prtouch_pres/step` (je 64, `ch=0`), `ticks` über Swap-Nullpunkt zusammenführen, Kontakt nach §4 (`CAL→best→step_bst→zoft`), `stop_*` senden, `zoft` addieren (`z_offset=0`).
- Offene (nicht widerlegende) ANNAHMEN für Nachbau: `clk/sdo`-Belegung, `lmt_dead`-Skalierung (`prth_err_dead 0.2` vs `%u`), `is_src/sta_swap/tri_chxs/expar`-Kodierung, Sample-Byte-Layout — nur per Live-MCU-Mitschnitt klärbar, bewusst nicht durchgeführt.

## Anhang: Dateibelege (Auswahl, alle in diesem Ordner)

- `HANDLER-TABELLE.txt:45,48,49` (PrTouch-Formate), `:25,32,34-37` (Homing/trsync separat), `:24,21,31,33` (GPIO-Upstream-Handler).
- `asm_reset.txt:0x080033f8` (Reset), `:0x0800743c` (Code statt Tabelle).
- `asm_main.txt:0x08005a88/0x08003b68/0x080042c4` (Handler-Anfänge), `asm_callees.txt:0x08004970/0x08004878/0x08006cec/0x08006d30`, `asm_gpio2.txt:0x08004660/0x08004938/0x08005c14/0x08005a88`, `asm_lits2.txt:0x08005bf0/0x080059f8`, `asm_prtouch.txt:0x0800423c/0x0800428c/0x0800411c/0x0800426c`, `asm_stop_long.txt:0x08003f60/0x08003e88`, `asm_homing.txt:0x08005af4/0x08006050`, `asm_sched.txt`, `asm_deferred.txt`, `xref_write.txt`, `xref_trsync.txt`, `xref_noz.txt`, `decomp_main_gpio.txt`, `decomp_test2.txt`.
- Host: `../lokale Arbeitsdateien pyx_strtab.json:0x14de8c/0x14e1dc/0x14e1e0/0x14e1e4/0x14e954/0x14ed44/0x14eb0c/0x14e524/0x14e520/0x14ea38/0x14e51c/0x14e1ec/0x14e1fc/0x14eb14/0x14e1f4/0x14dd1c`, `pyx_tuples.json:0x14cd14ff`, `funcs.json:0x88364/0x86cfc/0xa6ba0/0x38ae8`, `disasm.txt:129554/130988/162235/49549`, `liveconfig_prtouch_resolved.txt/raw.txt`, `printer-live.cfg:333/334/339`.
