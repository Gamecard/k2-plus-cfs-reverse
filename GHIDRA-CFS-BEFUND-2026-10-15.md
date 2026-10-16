# GHIDRA-CFS-BEFUND 15.10.2026 – CFS-Firmware cfs0_000_153: RS485-Dispatcher und Payload-Semantik (Ghidra, 15.10.2026)

> Kopie des Ghidra-Befunds (Original: Arbeitsdatei lokal `/tmp/claude-0/ghidra-cfs/BEFUND.md`,
> flüchtig). Alle Pfade unter `/tmp/claude-0/ghidra-cfs/` sind **Arbeitsdateien lokal**
> (Ghidra-Projekte, Dekompilate) und NICHT Teil des Projekts/Repos. Längere
> Dekompilat-Auszüge wurden bewusst auf Belegzeilen mit Adresse gekürzt – im Repo sollen
> keine größeren Rekonstruktionen von Creality-Code landen.
> Eingearbeitet in: PROTOKOLL-485.md, PROTOKOLL-GESAMT.md, CHECKLISTE.md,
> AUDIT-UND-AENDERUNGEN.md, `nachbau/box.py`, `nachbau/cfsseq.py`, `nachbau/boxmini.py`.
>
> **KORRIGIERT/ERGÄNZT durch `GHIDRA-CFS-BEFUND2-2026-10-16.md`** (zweite Ghidra-Analyse 16.10.2026):
> 0x0a Byte 0/1/2/5, 0x15, Status 0x50/0x51/0x52/0x53, Adressen 0xEA/0xEB, Auto-Addr. Betroffene Stellen sind unten mit
> „→ BEFUND2“ markiert; der ursprüngliche Text bleibt zur Nachvollziehbarkeit stehen.


Ziel: `fw/cfs/cfs0_050_G32-cfs0_000_153.bin` (GD32F303VET6, Cortex-M4/Thumb, RT-Thread 5.0.2).
Nebenziele: `cfs0_050_G30-cfs0_000_150.bin` (Gegenprobe) und `cfs6_100_G31-cfs6_222_000.bin`.
Werkzeug: Ghidra 12.1.4 headless, Projekte unter `/tmp/claude-0/ghidra-cfs/{proj,verify}` (Arbeitsdateien lokal).

Belegstärke in diesem Dokument:
- **B** = im Dekompilat oder in der Disassembly belegt (Adresse angegeben)
- **B+L** = belegt und zusätzlich durch Live-Frames aus PROTOKOLL-485.md bestätigt
- **V** = vermutet (Deutung aus Namen, Strings oder Kontext, nicht beweisbar aus dem Code allein)

---

## 0. Basis-Korrektur und Import

| Datei | Basis alt | Basis neu | SP / Reset | Funktionen alt → neu | Strings mit XRef (alt → neu) | XRefs auf Strings |
|---|---|---|---|---|---|---|
| cfs0_000_153 | 0x08000000 | **0x08003000** | 0x20006ee8 / 0x0801a969 | 927 (Doku: 942) → **1063** | 538 → **930** (von ~1180) | 812 → **1464** |
| cfs6_222_000 | 0x08000000 | **0x08004000** | 0x20008030 / 0x08004145 | Doku: 798 → **936** | – → 601 (von 806) | – → 1019 |
| cfs0_000_150 | – | 0x08003000 | – | – (Dispatcher-Gegenprobe) | – | – |

Der Wert „alt“ für cfs0 stammt aus einem Vergleichsimport mit derselben Pipeline, nur mit Argument `0x08000000` (Projekt `proj/cfs0_153_ALT08000000`).

Rezept, getestet als Frischimport:
```
export GHIDRA_HEADLESS_MAXMEM=4G      # 30 GB RAM, 25 GB frei; pro Lauf reichen 4G (~15 s Analyse)
S=<projekt>/ghidra-scripts
/opt/ghidra/ghidra_12.1.4_PUBLIC/support/analyzeHeadless /tmp/claude-0/ghidra-cfs/verify v0 \
  -import fw/cfs/cfs0_050_G32-cfs0_000_153.bin -loader BinaryLoader -processor "ARM:LE:32:Cortex" \
  -preScript SetCfsBase.java \
  -postScript CfsDispatch.java out=/tmp/claude-0/ghidra-cfs/verify/disp_cfs0.c \
  -postScript CfsDetail.java -scriptPath $S -overwrite
```
Speicherblöcke nach `SetCfsBase`: `bootloader` (0x08000000, uninit.), `flash` (Image, r-x), `flash_rest` (bis 0x08080000), `sram` 0x20000000/64 KiB, `periph` 0x40000000/256 MiB (volatile), `periph_ahb3` 0x60000000, `exmc_regs` 0xA0000000, `ppb` 0xE0000000/1 MiB (volatile). Die Vektortabelle (84 Einträge) wird als Pointer definiert, Handler werden als Funktionen angelegt (`reset_handler`, `vec_NN`).

---

## 1. Rahmenformat, CRC, Empfang (cfs0_000_153)

| Element | Adresse | Befund | Beleg |
|---|---|---|---|
| Byte-Empfangs-Statemachine | `FUN_0800f880` | Zustände 0..6: wartet auf `0xF7`, prüft `addr ∈ {eigene, 0xEA, 0xEB}`, nimmt `len` nur bei `len > 2` an, dann status, fun, `len-3` Datenbytes, CRC. Timeout `rs485 timeout` nach 0x78 Ticks | **B** |
| CRC-Prüfung | `FUN_0800eaa4` → `FUN_0800e9ec` | CRC-8, Polynom 0x07, Init 0x00, **ohne Tabelle (Bitschleife)**, über **`len,status,fun,data`** (Kopf 0xF7 und `addr` sind NICHT enthalten) | **B+L** (6 Live-Frames nachgerechnet, alle passen) |
| CRC-Fehler | `FUN_0800f880` (Zustand 6) | `if addr != 0xEA: reply(fun, status=2, keine Daten)` | **B** |
| Weiterleitung an Auto-Addr | `FUN_0800a8fc`/`FUN_0800a9cc` | Steht `fun` in der 4er-Tabelle @0x200000c4 (Schrittweite 0x34, Modul `RS485_AUTO_ADDR`), wird der Frame NICHT dispatcht, sondern als Antwort auf eine eigene Anfrage der Box einsortiert | **B** |
| Dispatcher | **`FUN_0800f27c`** | `switch(fun)`, zwei TBB-Tabellen @0x0800f2ae (fun 0..15) und @0x0800f36e (fun 0x52..0x5d) | **B** |
| Antwort-Builder | `FUN_0800eaf2(fun, status, data*, n)` | `F7, own_addr, n+3, status, fun, data[n], crc8(len..data)` | **B** |
| Längenprüfung | `FUN_0800ebc2(fun, len, soll)` | `len != soll` ⇒ reply(fun, **status 1**) und Abbruch | **B** |
| Maskenprüfung | `FUN_0800eb88(mask, dst*, strict)` | `0x01→0, 0x02→1, 0x04→2, 0x08→3`; `0x00→4` („alle Slots“) nur wenn `strict==0`; alles andere ⇒ ungültig (status 1, Vergleich als 32-Bit-Wert `cmp r3,#9`) | **B** |

Request-Status-Byte: Der Dispatcher liest `frame[2]` (status) **nie** aus (**B**). Der Host sendet live 0xFF (`f7 01 03 ff 14 06`), der Wert ist für die FW egal.

Broadcasts (**B**): `addr 0xEA` heißt keine Antwort (bei unbekanntem fun, bei CRC-Fehler, bei 0x50). **→ BEFUND2 §6: Bei normalen Kommandos antwortet die Box auf 0xEA trotzdem (mit eigener Adresse); 0xFE/0xFF nimmt die App nur ohne eigene Adresse an.** `addr 0xEB` heißt Antwort mit Absenderadresse 0xEB (bei 0x50; 0x51 wirkt nur bei 0xEB).

TBB-Problem: Ghidra disassembliert die TBB-Tabellenbytes als Code. Das ergab vorher `halt_baddata` und Phantom-`case`s. `CfsDispatch.java` legt die Tabellen als Daten an, setzt COMPUTED_JUMP-Referenzen und einen `JumpTable`-Override. Danach ist das Dekompilat sauber (0 × `halt_baddata`; Datei `dispatcher_cfs0_153.c`).

---

## 2. Tabelle fun → Handler → Semantik (cfs0_000_153)

`len` = erwartetes Längenbyte (= 3 + Anzahl Datenbytes). `mask` = Slot-Bitmaske (siehe `eb88`). Antworten haben dieselbe fun.

| fun | len (Daten) | Handler | Semantik (Request → Antwort) | Beleg |
|---|---|---|---|---|
| 0x01 | 3 (0) | inline + `FUN_08010c60` | Antwort status 0 ohne Daten, danach LED-Status aus den Slot-Sensoren neu setzen | B |
| 0x02 | 4 (1: mask) | `FUN_0800ef00` / `FUN_0800e362` | je gesetztem Bit `"A:<info>;"`: 0x28 Byte RFID-Rohdaten, sonst `"busy"` (Slot wird gerade preloaded) bzw. `"unknown"` (Filament da, kein Tag) | B+L (`C:busy;`, `B:unknown;`) |
| 0x03 | 4 (1: mask) | `FUN_0800ef66` | 4 Byte, je Slot Füllstand in % (`FUN_080053ce`), 0xFF = kein Wert, 0 = kein Filament | B+L (`1c ff 00 00`) |
| 0x04 | 5 (mask, mode) | `FUN_0800f014` | Box-Mode: mode 0 = Slot in **STATE_PRINT** (2) setzen (mask≠0 nötig) + `rfid_mgr_fila_print`; mode 1 = Print verlassen, Zustand IDLE, `rfid_mgr_fila_idle`. Status 3, wenn Zustand PRELOAD(1)/RELOAD(3); sonst 1 | B |
| 0x05 | 3 (0) | `FUN_0800f0c2` | 1 Byte: 2 Bit Buffer-Sensoren (Pins 0x38/0x39, invertiert) | B (Deutung „Buffer“: V) |
| 0x06 | 5 (mask, op) | `FUN_0800ebda` | Feeder des Slots: op 0 = alle stoppen, 1 = „out“ mit Speed-Param[1], 2 = „back“ mit Param[6]; Status 3 bei Zustand ≠ IDLE. **FW-BUG:** Maske kommt aus `r7` (Tick-Wert), nicht aus dem Datenbyte ⇒ praktisch immer status 1 | B (Disassembly 0x0800f470 `mov r0,r7`; r7 = `rt_tick_get()` aus 0x0800f8a6). Auch in R150 |
| 0x07 | 4 (1: op) | `FUN_0800ec26` | Hauptmotor (MS3791?): 0 = Stopp, 1 = vorwärts Param[2], 2 = rückwärts Param[7]; Status 3 bei Zustand ≠ IDLE | B (Motor-Zuordnung V) |
| 0x08 | 4 (1: sel) | `FUN_0800ec70` | 1 Byte Bitmaske: sel 0 = Sensor A (Pin-Tab +0x00), sel 1 = Sensor B (+0x10), jeweils „1 = aktiv (Pin low)“ | B (A = Einlauf, B = Hub: V) |
| 0x09 | 5 (a, b, wert) | `FUN_0800ecd2` | Speed-Parameter setzen: (0,0)→P[1], (0,1)→P[6], (1,0)→P[2], (1,1)→P[7]; a = Feeder/Haupt, b = out/back | B (Feld-Bedeutung V) |
| 0x0a | 3 (0) | `FUN_0800ed26` | **Box-State-Abfrage**, Details in §5 | B+L |
| 0x0b | 5 (mask, x) | `FUN_0800eee8` → `FUN_08010684` | LED-Code x für Slot setzen, status 0 | B (LED: V, aus String `led set err`) |
| 0x0c | 3 (0) | `FUN_0800f124` | 1 Byte Config-Wert #6 (`FUN_08011d82(6)`) | B (Bedeutung V) |
| 0x0d | 5 (mask, op) | `FUN_0800f0da` | **Preload/RFID-Steuerung**, Details in §3 | B |
| 0x0e | 4 (1: op) | `FUN_0800efc6` | op 0 = Odometer nullen; op 1 = 4 Byte **Odometer als float32 Big-Endian** (mm) | B+L (`c5 00 91 49` = −2057,07) |
| 0x0f | 4 (1: x) | inline | x∈{0,1}: `S[8]=x` (0x200037d8), status 0; sonst status 1. `S[8]≠0` sperrt das automatische Preload (`FUN_080053b0`). **Kein Motor-Kommando** | B |
| 0x10 | 6 (mask, mode, p) | `FUN_080045c6` | **Extrude/Feed-Prozess**, Details in §4 | B |
| 0x11 | 5 (mask, p) | `FUN_08004d90` | **Retract**, Details in §4 | B |
| 0x12 | 8 (mask, mode, s, dist_hi, dist_lo) | `FUN_080047f6` | `cmd_feed_process_3`: mode 0 = `FUN_080047c6` (feed_top_to_hub), Antwort status + 1 Byte Zustand; mode 1 = `FUN_0800472c`: Hauptmotor Param[3] bis Odometer > dist (u16 BE, max. 3000 mm) oder Stillstand 2 s, status 0. Das Byte s wird nur geloggt („speed“) | B (nur R153; in R150 fehlt 0x12) |
| 0x13 | 4 (1: mask) | `FUN_08004dc2` | Pretension-Check (`FUN_08004460`) → feed_top_to_hub (`FUN_08004326`) → `FUN_08003e74` (fördern bis Stillstand); status = Fehlercode | B |
| 0x14 | 3 (0) | inline | 22 Byte: `"153"` + 19 Byte aus 0x200038f0 (Version/Seriennummer) | B (Inhalt V) |
| 0x15 | 3 (0) | `FUN_0800f16e` | 16 Byte: 6 Einträge `((i+1)<<4) | X[i]` aus 0x2000390d, Rest 0 | B (~~Bedeutung offen~~ → BEFUND2 §3: Init-Flags EEPROM/AHT30/Odometer/Buffer/RFID0/RFID1, 0 = OK) |
| 0x30 | 4 (1) | `FUN_0800f19c` | GPIO 0x10 setzen (Pin = (x==1)), status 0 | B |
| 0x50 | 4 (1: wert) | inline | Config #6 schreiben + `FUN_08011df0` (speichern), status 0, dann `rt_thread_delay(100)` + `FUN_08016388` (vermutlich Reset). Gleicher Wert ⇒ status 3, kein Reset | B (Reset: V) |
| 0x51 | 4 (1) | inline | nur bei `addr==0xEB`: `G[3]=x`, keine Antwort | B |
| 0x52 | 3 (0) | inline (0x0800f764) | Antwort mit **fun 0x53**, status 0, 2 Byte `[S[1], S[0]]` | B |
| 0x53 | 5 (a, b) | inline (0x0800f78e) | schreibt 0x20000034=a, 0x20000035=b, keine Antwort (Längenfehler meldet sich fälschlich als fun 0x52) | B |
| 0x55 | 4 (1: x) | `FUN_0800f14a` | Echo/Test: Antwort `x+1` (0xFF wird 0) | B |
| 0x56 | 3 (0) | inline | status 0, danach Reset (`FUN_08016388`) | B (Reset: V) |
| 0x5b | 3 (0) | `FUN_0800f1ba` | 8 Byte aus 0x1FFFF7E8 (GD32-Unique-ID: W0, W1&W2) | B |
| 0x5c | 0x13 (16) | `FUN_0800f1e8` | 16-Byte-Block an `FUN_0800af7e(0x60,…)` (I2C-Gerät 0x60), Antwort 16 Byte Schlüssel/Ergebnis (`rfid key read ok!`) | B (Auth-/Krypto-Zweck: V) |
| 0x5d | 3 (0) | `FUN_08007c0e` | `main_pcb_test_mode` (RFID TEST usw.) | B |
| 0xA0–0xA3 | – | NICHT im Dispatcher | laufen über das Modul `RS485_AUTO_ADDR` (0x0800a8fc–0x0800ac90; `FUN_0800a92c` nimmt addr 0xFF/0xFE/eigene an; `FUN_0800aac8` beantwortet 0xFE-Discovery). Die Tabellen-Initialisierung ist nicht aufgelöst | B (teilweise) |
| sonst (0x00, 0x16–0x2f, 0x31–0x4f, 0x54, 0x57–0x5a, >0x5d) | – | `LAB_0800f7f8` | status 1 ohne Daten (bei addr 0xEA keine Antwort) | B |

Gegenprobe R150 (`dispatcher_cfs0_150.c`): identischer Aufbau (Dispatcher `FUN_0800f0a4`, lencheck `FUN_0800e9ea`), dieselben Codes **außer 0x12**. Der 0x06-r7-Bug ist dort ebenfalls vorhanden.

---

## 3. 0x0d – Preload/RFID-Control (Frage 1)

Request `[mask, op]`, len 5. Handler `FUN_0800f0da` (**B**; Belegzeilen statt Dekompilat):
- `op = data[1]`; `op==0` → `FUN_08005376` (disable), `op==1` → `FUN_0800536e` (enable)
- `op==2` → `FUN_08004ec4(data[0]=mask, 1)`, `op==3` → `FUN_08004ec4(mask, 0)`, sonst Status 1
- Status ≠ 0 → `FUN_08003e3a(status)` (Fehler → STATE_ERROR, außer 1/3)
- danach immer `FUN_0800eaf2(0x0d, status, –, 0)` = Antwort ohne Daten

- **op 0x00 = disable** (**B**). `FUN_08005376` setzt `PRE[1]=0` (0x20000025). Das ist dieselbe Funktion wie das finsh-Kommando `preloading disable` (`FUN_08006692`).
- **op 0x01 = enable** (**B**). `PRE[1]=1`, entspricht `preloading enable`.
- **op 0x02 = forced preloading mit RFID** (**B**). `FUN_08004ec4(mask,1)` → je Slot in mask, der Filament hat (Sensor A meldet innerhalb 20 ms **nicht** „1“), und der nicht gerade preloadet: `FUN_08004e80` (`forced_preloading %d`). Ablauf: `PRE[3]=slot`, `PRE[2]=PRE[1]`, `PRE[1]=1`, Preload-State auf 0. Danach 5 s warten und bis zu 180 s auf Ende pollen, dann `PRE[1]` zurücksetzen.
  Die Preload-Statemachine `FUN_080064b8` läuft mit `PRE[1]==1` durch **pretension → STATE_CHECK_HUB → „read rfid and margin estimation“**. Ein Force-Read ist also enthalten.
- **op 0x03 = forced preloading OHNE RFID** (**B**). Wie op 2, aber `PRE[1]=0`. Die Statemachine macht dann nur **pretension** und geht zurück in den Leerlauf (Zustand 3 → `break`).
  Diese Variante fehlt in der Nachbau-Annahme.
- `mask` (1. Byte) wirkt **nur bei op 2/3** (**B**). Bei op 0/1 wird es ignoriert, d.h. enable/disable gilt global.
- Status (**B**):
  - 0 = OK
  - 1 = op > 3
  - 3 = Box nicht IDLE/PRELOAD
  - 0x16 = kein Slot mit Filament (`FUN_08004d4a(4)` → `FUN_0800e81a`; setzt auch STATE_ERROR)
  - sonst der Fehler eines vorgeschalteten Rückzugs
- Befindet sich die Box in IDLE, wird vor dem Force zuerst `FUN_08004cd0(4,1)` ausgeführt, also ein Pull-back aller Slots mit Filament am Hub (**B**).
- Bei Zustand PRELOAD wartet die FW bis 179 s auf das laufende Preload (**B**).
- Die Antwort kommt bei op 2/3 erst nach dem kompletten Vorgang, also bis zu ~3 min (**B**; Auswirkung auf Host-Timeouts: V).

Abweichung cfs6_222 (**B**, `FUN_0800e674`):
- op <2 wirkt **pro Maske** (`mask&0xF`, 0 bedeutet alle).
- op 2 braucht mask≠0.
- op 3 nur für einen Slot.
- Antwort nur bei op 0/1 sofort.

---

## 4. 0x10 Extrude / 0x11 Retrude (Frage 2)

### 0x10 `[mask, mode, p]` – `FUN_080045c6` (`cmd_feed_process_2`; das Log zeigt `mode+1`)

`mask` → Slot über `eb88(strict=1)`, d.h. mask 0 ist ungültig. Speed-Parameter `P[i]` = Tabelle hinter `*(0x20000014)`.

| mode | Funktion | Ablauf | Antwort | Beleg |
|---|---|---|---|---|
| 0 | `FUN_080044ce` (`cmd_parse_out_stage1`) | Pretension-Check → feed_top_to_hub → `FUN_08004104` (Hauptmotor P[3] bis Bedingung `FUN_08003fec`) | status = Fehlercode, 1 Byte = Box-Zustand S[2] | B |
| 1 | `FUN_08003e74` | Zustand RELOAD(3), Hauptmotor P[3] fördern bis Odometer-Stillstand (<0,01 mm je 20 ms, 25×) oder >3000 mm ⇒ 0x0b | status | B |
| 2 | `FUN_08003efa(0)` | RELOAD, Hauptmotor P[2], bis 3 s prüfen, ob sich der Odometer um ≥5 mm bewegt (sonst 0x0a), dann wie mode 1 | status | B |
| 3 | `FUN_08003e60(0)` → `FUN_08003e2c` | Fehler löschen: S[2]=IDLE, S[3]=0, LED neu | status 0 | B |
| 4 | `FUN_08025c4e` | RELOAD, **sofort Antwort status 0**, dann Hauptmotor P[3]. Die FW liest den Bus **selbst** (`FUN_08025eb2`/`FUN_08025c18`): eingehende 0x10 mit mode 5 → Antwort status 0 + 4 Byte Odometer (float BE); mode 6 → Fördern beenden und **„stage 7: enter buffer_check“**; CRC falsch → status 2. Timeouts: 25 s ⇒ 0x0a, >3000 mm ⇒ 0x0b, Stillstand 500 ms ⇒ 0x0c. Nach buffer_check: Antwort 0x10 mit status 0 (buffer full) bzw. 0x0d. Fehlercodes landen in `DEF[4]` (0x20000008) | B |
| 5 | inline | Abfrage: status = gespeicherter Fehler aus mode 4 (`DEF[4]`, danach gelöscht), Daten = 4 Byte Odometer float BE; Log `stage6: x.xx mm` | B |
| 6 | `FUN_08025d96` | RELOAD; wenn der Buffer-Sensor nicht schon „voll“ meldet: Hauptmotor P[2] bis voll (≤3 s), sonst 0x0d | status | B |
| 7 | `FUN_08025dea(p)` | RELOAD; Hauptmotor P[4], 15×20 ms (300 ms) warten, bis der Odometer um **mehr als p mm** gelaufen ist. Danach Vergleich gegen `p*0,5` (double): darunter ⇒ **0x0e** „feed extruder timeout“, sonst 0 | status | B (Schwelle ½·p aus Konstante 0x08026010 = 0.5; Vergleichsrichtung aus `bcs` nach dcmp: V) |
| >7 | – | `illegal data`, **keine Antwort** | – | B |

Live `[mask 07 03]` heißt: **mode 7 mit p = 3 mm**, also ein Extruder-Nachschub-Check („stage8“), ob 3 mm Filament in 300 ms abgenommen werden (**B** für die Mechanik; Interpretation als Extruder-Greif-Check: **V**).

### 0x11 `[mask, p]` – `FUN_08004d90` (mask 0 erlaubt ⇒ alle Slots)

1. `p==1` ⇒ zuerst `rfid_mgr_fila_back(slot)` (`FUN_0800d9b8`).
2. `FUN_08004d4a`: Zustand muss IDLE/PRELOAD sein, sonst **3**. Ein Slot ohne Filament (Sensor A) ⇒ **0x16** + STATE_ERROR. Bei mask 0 ohne jedes Filament: ebenfalls 0x16.
3. S[2]=RELOAD, dann `FUN_08004cd0(slot,p)`:
   - `p=0` → `FUN_08004c9a`: Feeder-Rückwärts (`FUN_0800d226(slot,1)`), Hauptmotor rückwärts P[7], warten bis Buffer-Sensor = Zustand[0] (≤5 s), sonst **0x13**.
   - `p=1` → `FUN_08004c5a` (**pull_back_hub**): Sensor A muss 0 sein, sonst **0x17**. Danach `FUN_0800494e` (Rückzug mit Odometer, Motorblock ⇒ **0x19**) und `FUN_08004884` („pull back hub PE“, Timeout ⇒ **0x14**).
   - sonst ⇒ status 1 („illegal data“).
   - mask 0: über alle Slots, bei denen Sensor B aktiv ist.
4. `FUN_08003e60(ret)`: ret≠0 ⇒ STATE_ERROR mit S[3]=ret (außer 1/3), sonst IDLE.
5. Antwort 0x11 status=ret, ohne Daten.

Alles **B**. Die Namen „Sensor A = Einlauf, Sensor B = Hub-PE“ sind **V**.

cfs6_222 (`FUN_0800dac8`, `FUN_0800e324`; **B**): andere Implementierung über eine Message-Queue.
- 0x10: Modi 0,4,5,6,7 aktiv, 1–3 ohne Antwort; mode 5 liefert status 0x0c bei gesetztem Flag. Busy ⇒ status 3 + 1 Byte Zustand.
- 0x11: p 0/1, Zustand 2/4 ⇒ status 3 + Zustand.

---

## 5. Status-Byte (Frage 3) und 0x0a Box-State (Frage 4)

### Status-Werte in Antworten (cfs0_000_153)

| Wert | Bedeutung | Quelle | Beleg |
|---|---|---|---|
| 0x00 | OK | überall | B |
| 0x01 | ungültig: Längenbyte falsch (`FUN_0800ebc2`), Maske ungültig (`FUN_0800eb88`), Subcode ungültig, **fun unbekannt** | Dispatcher | B |
| 0x02 | CRC-Fehler im Request | `FUN_0800f880`, auch inline in `FUN_08025c4e` | B |
| 0x03 | busy / falscher Zustand (z.B. 0x04 in PRELOAD/RELOAD, 0x06/0x07 nicht IDLE, `err_status=…`, „pretension now“); bei 0x50 auch „Wert unverändert“ | diverse | B |
| 0x05 | `cmd_parse_out`: Feeder-Out-Fehler | `FUN_08004df2` | B |
| 0x06 | `buffer_fill_timeout` | `FUN_08004df2` | B |
| 0x08 | Pretension-Warten (30×) erfolglos | `FUN_08004460` | B |
| 0x09 | feed_top_to_hub: Odometer bewegt sich nicht | `FUN_08004190` | B |
| 0x0a | 25-s-Timeout bzw. <5 mm in 3 s | `FUN_08025c4e`, `FUN_08003efa` | B |
| 0x0b | Fördern > 3000 mm | `FUN_08003e74`, `FUN_08025c4e` | B |
| 0x0c | Odometer-Stillstand 500 ms | `FUN_08025c4e` | B |
| 0x0d | Buffer nicht voll | `FUN_08025c4e`, `FUN_08025d96` | B |
| 0x0e | feed extruder timeout | `FUN_08025dea` | B |
| 0x13 | Rückzug: Buffer-Sensor-Timeout 5 s | `FUN_08004c9a` | B |
| 0x14 | pull back hub PE timeout | `FUN_08004884` | B |
| 0x16 | Slot ohne Filament | `FUN_08004d4a`/`FUN_0800e81a` | B |
| 0x17 | pull_back_hub: Sensor A nicht aktiv (→ BEFUND2 §8: Einlauf-Sensor meldet kein Filament) | `FUN_08004c5a` | B |
| 0x18 | (intern) kein Slot belegt; wird in `d4a` auf 0x16 abgebildet | `FUN_0800e81a` | B |
| 0x19 | pull_back_hub motor block | `FUN_0800494e` | B |
| 0x30 | nur 0x0a: Preload-Ereignis anstehend (`preload_check_flag`), 4 Byte Ereignisdaten | `FUN_0800ed26` | B+L (`f7 01 07 30 0a 03 00 00 00`) |
| 0x50 | nur 0x0a in STATE_PRINT: Sensorprüfung `FUN_0800e8e0` ≠ 0 (setzt R[8]=1, LED-Fehler 0x50) | `FUN_0800ed26` | B (Bedeutung: V, z.B. Filament-Runout; → BEFUND2 §4: Einlauf oder Hub des Druck-Slots frei = Runout) |
| S[3] | nur 0x0a in STATE_ERROR: gespeicherter Fehlercode (einer der obigen) | `FUN_0800ed26` | B |

Weitere Fehlercodes im LED-Mapping `FUN_08010c8a`: 0x1a, 0x20, 0x22, 0x51, 0x52, 0x53. Ihre Quellen sind nicht verfolgt (**V**: weitere Fehler). **→ BEFUND2 §4/§8 KORRIGIERT: 0x51/0x52 sind echte Fehler aus dem Thread print_check (0x51 ≥ 40 mm bzw. ≥ 3× Buffer leer ohne „voll“, 0x52 < 5 mm in ~10 s), 0x1a = Pull-back-Timeout 40 s; 0x53 wird nie erzeugt.**

Fehler-Mechanik `FUN_08003e3a(err)` (**B**): Für err ∉ {1,3} gilt `S[3]=err`, `S[2]=4` (STATE_ERROR) und die LED zeigt den Fehler. err 1/3 stellt nur den Slot-Index wieder her. Gelöscht wird der Fehler mit 0x10 mode 3 (`FUN_08003e2c`).

### 0x0a Box-State – `FUN_0800ed26`

Box-Zustand `S[2]` (0x200037d2), Mapping aus `FUN_0800fa0c` nach TBB-Reparatur (**B**):

| Wert | String |
|---|---|
| 0 | STATE_IDLE |
| 1 | STATE_PRELOAD |
| 2 | STATE_PRINT |
| 3 | STATE_RELOAD |
| 4 | STATE_ERROR |
| 5 | STATE_TEST |
| ≥6 | „unknown“ |

**STATE_CHECK_HUB ist KEIN Box-Zustand** (**B**). Er gehört zur Preload-Statemachine `FUN_080064b8` (Zustandsbyte 0x2000391c):

| Wert | Bedeutung |
|---|---|
| 0 | forced-Start |
| 1 | idle/Queue |
| 2 | **STATE_CHECK_HUB** |
| 3 | pretension |
| 4 | read rfid + margin estimation |

Er taucht in 0x0a nicht direkt auf.

Antwort 0x0a, normal (**B+L**): 6 Datenbytes

| Byte | Inhalt |
|---|---|
| 0 | S[1] (→ BEFUND2 §1: Temperatur °C, int8, AHT30) |
| 1 | S[0] (→ BEFUND2 §1: rel. Feuchte %, uint8) |
| 2 | Flags: `R[3] | 0x80` einmalig, wenn R[3]≠0 (R[3]: Motorblock-Status, **V**), sonst R[5] (ebenfalls zurückgesetzt). **→ BEFUND2 §8 KORRIGIERT (B): R[3] = Motorblock > 10 s (Timer motor_blocked_detect_timer), Bit 4 Hauptmotor, Bit 0–3 Feeder A–D; R[5] = Blockmaske sofort** |
| 3 | **S[2] = Box-Zustand** |
| 4 | Bitmaske Sensor B (aktiv = 1) |
| 5 | Bitmaske `FUN_080053f2` (~~Slot hat gültige RFID-Daten mit Status 7, **V**~~ **→ BEFUND2 §2 KORRIGIERT: Einlauf-Flag && Tag geladen && Tag-Typ 7 && Tag-Feld+0x10 == 0 – Sperrbit, schließt 0x02-Daten aus**) |

Live `09 00 0a 13 34 00 00 00 00` ⇒ S[1..0]=0x13/0x34, Zustand IDLE. Woher S[0]/S[1] kommen, ist nicht gefunden (**offen** → BEFUND2 §1 geklärt: AHT30). Denselben Wert liefert 0x52 (Antwort-fun 0x53).

Status der 0x0a-Antwort, nach Zustand (**B**):

| Zustand | Status-Byte |
|---|---|
| ERROR | S[3] (gespeicherter Fehler) |
| PRINT | 0x50 oder 0 |
| sonst | 0 |
| Preload-Ereignis anstehend | 0x30 mit 4 Byte Ereignis-Bitmaske (Werte 1/2/3 je Slot aus `FUN_080065f4`) |

---

## 6. Weitere Auffälligkeiten

- **0x04 Box-Mode**: §2 (mode 0 = PRINT, 1 = IDLE).
- **0x0f** ist das Preload-Inhibit-Flag `S[8]` (**B**), nicht „Motor-Action“. Motor-Kommandos sind **0x06** (Feeder, mit r7-Bug), **0x07** (Hauptmotor) und **0x09** (Speeds).
- **Force-Flag**: siehe 0x0d op 2/3 (§3). Im Dispatcher existiert kein separates force-Byte.
- **Dryer-Kommando**: In cfs0_000_153 gibt es **keins** (**B**: keine Heiz-/Trockner-fun, keine HT-Strings).
  cfs6_222 ist eine andere Gerätevariante mit Heizung/Lüfter (`[D/HT] ptc_power`, `Chamber backside fan`, `dry aging wait ac connect`) und Bus „RS485“ oder „CAN“ (`api_comm_cmd_parse` = `FUN_08005acc`).
  fun-Liste cfs6 (switch auf fun−2): 0x02 `0800dff8`, 0x03 `0800df7c`, 0x04 `0800e500`, 0x05 `0800de14`, 0x08 `0800de7c`, 0x0a `0800dd3c`, 0x0b `0800e4b0` (nur src 0xFF), 0x0c `0800dd18`, 0x0d `0800e674`, 0x0e `0800def8`, 0x0f `0800da5c`, 0x10 `0800dac8`, 0x11 `0800e324`, 0x14 `0800e074`, 0x15 `0800debc`, 0x16 `0800daa0`, 0x17 `0800e62c`, 0x18 `0800e5cc` (6 Byte), 0x19 `0800de38`, 0x50 `0800e7c0`, 0x51 `0800e7a0`, 0x55 `0800e4d0`, 0x56 `0800da84`, 0x57 `0800e7e8` (5 Byte), 0x5b `0800e0c0`, 0x5c `0800e818`, 0x5d `0800e108`, 0xA0 `0800e290`, 0xA1 `0800e110`, 0xA2 `0800e250`, 0xA3 `0800e210`.
  Kandidaten für Trockner/Heizung: 0x16–0x19 und 0x57 (neu gegenüber cfs0; **V**, nicht dekompiliert).
- 0x53-Längenfehler wird als fun 0x52 gemeldet (Copy-Paste in der FW, **B**).

---

## 7. String-XRefs alt (0x08000000) vs. neu (0x08003000), Stichprobe (Frage 5)

Format: Anzahl Strings / XRefs / referenzierende Funktionen.

| String | alt | neu |
|---|---|---|
| `preloading enable` / `disable` / `read` | 1/0/0 | 1/1/1 (`FUN_08006692`) |
| `rfid_mgr_fila_print` | 1/0/0 | 1/2/1 |
| `rfid_mgr_fila_idle` / `_back` | 1/0/0 | 1/1/1 |
| `rfid_mgr_fila_save` / `_task` | 1/0/0 | 1/3/1 |
| `rfid_auth_check`, `rfid_detect_tag` | 1/0/0 | 1/1/1 |
| `rfid init <card id>` | 1/2/1 | 1/2/1 |
| `different rfid uid detected` | 1/1/1 | 1/1/1 |
| `rfid continuous detect timeout, spool…` | 1/0/0 | 1/1/1 |
| `feed filament exceed max length` | 1/0/0 | 1/1/1 |
| `STATE_CHECK_HUB` | 1/0/0 | 1/1/1 |
| `box_aging_mode_test[…]`, `task_test[…]`, `switch_mode_test[…]` (finsh-Hilfetexte) | 1/0/0 | 1/1/1 |
| `read_motor_info realtime` | 1/0/0 | 1/1/1 |
| `cmd_parse_out_stage1_mode3` | 1/0/0 | 1/1/1 |
| `feeder_pid_output`, `feeding 500ms timeout`, `STATE_IDLE`, `rs485 timeout`, `stage 7: enter buffer_check`, `MF003` | je 1/1/1 | je 1/1/1 |

Erklärung (**B**): Was auch bei der alten Basis Referenzen hatte, wird PC-relativ geladen (`adr`), und das ist basisunabhängig. Alle absoluten Literal-Pool-Zeiger (finsh-Tabellen, `__FUNCTION__`-Namen, Hilfetexte) lösen erst mit 0x08003000 auf.

Gesamt: Strings mit XRef 538 → 930, XRefs 812 → 1464, Funktionen 927 → 1063.

---

## 8. Skript-Änderungen (`<projekt>/ghidra-scripts/`, chown pro:pro)

- **SetCfsBase.java** (neu geschrieben, Diff: `/tmp/claude-0/ghidra-cfs/skripte.diff`):
  - Basis per Argument (`0x…`) oder Dateiname (cfs0→0x08003000, cfs6→0x08004000).
  - Alte Konstante 0x08000000 und der Reset-Hack `base.add(0x4144)` sind als „KORRIGIERT 15.10.2026“ kommentiert.
  - `moveBlock` statt eines zweiten `createInitializedBlock`.
  - Uninitialisierte Blöcke bootloader/flash_rest/sram/periph/ppb/exmc.
  - Vektortabelle als Pointer, Reset und ISRs als Funktionen.
  - Läuft als preScript (dann macht die Headless-Autoanalyse den Rest) oder als postScript (dann `analyzeAll`).
- **CfsDetail.java** (neu geschrieben):
  - Gleiche Basislogik (Argument/Dateiname), Korrektur-Kommentar.
  - Ist der Block schon korrekt, wird weder verschoben noch neu analysiert.
  - Heartbeat-Suche nur im Flash-Block, Stringanfang wird zurückgesucht.
- **CfsDispatch.java** (neu):
  - Findet lencheck heuristisch (`movs r0,#fun / movs r2,#len / bl`), daraus den Dispatcher (bei cfs6 über den gemeinsamen Aufrufer).
  - Repariert TBB/TBH-Sprungtabellen (Daten, COMPUTED_JUMP-Referenzen, `JumpTable`-Override) und scannt danach erneut.
  - Gibt die Tabelle fun/len/Aufrufstelle/Folgeaufrufe aus und schreibt das Dekompilat (`out=`).
  - Verifiziert auf R153, R150 und cfs6_222.

Arbeitsdateien lokal (`/tmp/claude-0/ghidra-cfs/`, flüchtig, nicht im Repo):
- Dispatcher-Dekompilate: `dispatcher_cfs0_153.c`, `dispatcher_cfs0_150.c`, `dispatcher_cfs6_222.c`
- Gesamt-Dekompilate: `all_cfs0.c` (38k Zeilen), `all_cfs6.c`
- Abfrage-Helfer: `scripts/Q.java` + `q.sh`
- Logs: `import_*.log`, `verify/*.log`

## 9. Offen

- ~~Woher S[0]/S[1] kommen (0x0a Bytes 0–1, live 0x13 0x34).~~ → BEFUND2 §1
- ~~R[3]-Quelle.~~ → BEFUND2 §8
- Bedeutung von Config #6 (0x0c/0x50).
- ~~0x15-Inhalt.~~ → BEFUND2 §3
- ~~Initialisierung der Auto-Addr-Tabelle (0xA0–0xA3).~~ → BEFUND2 §7
- ~~Physische Zuordnung von Sensor A/B (Pin-Tabelle @0x20000218).~~ → BEFUND2 §8 (PD7–PD4 / PE10–PE7)
- cfs6-Handler 0x16–0x19/0x57 (Trockner?) im Detail.

## 10. Nachprüfung bei der Einarbeitung (15.10.2026, gegen `all_cfs0.c`)

- **0x0d** `FUN_0800f0da` vom Auftraggeber selbst geprüft (op 0/1 global, op 2/3 →
  `FUN_08004ec4(mask, 1/0)`, sonst Status 1) – bestätigt.
- **0x0d Laufzeit präzisiert (B):** `FUN_08004ec4` ruft `FUN_08004e80` **je Slot der Maske
  nacheinander** auf (Slot mit Filament, nicht der gerade laufende). Pro Slot: 5 s Wartezeit
  + bis 180 s Polling (`0xb4` × 1 s). Vorab ggf. bis 179 s (`0xb3` × 1 s) Warten auf ein
  laufendes Preload (Zustand PRELOAD) bzw. Pull-back aller Slots (Zustand IDLE,
  `FUN_08004cd0(4,1)`). „~3 min“ gilt also **pro Slot**; Maske 0x0F mit 4 belegten Slots
  kann theoretisch > 12 min dauern.
- **0x03 Restmenge (B, `FUN_0800ef66`):** je Slot `0xFF`, wenn nicht in der Maske, Slot
  gerade preloadet, ODER Filament da (Sensor A liest 0 innerhalb 20 Ticks, `FUN_0800e890`)
  aber keine gültigen RFID-Daten (`FUN_0800545c` = 0). `0x00`, wenn Sensor A **kein
  Filament** meldet. Sonst gespeicherter Prozentwert (`FUN_080053ce`, kann auch 0 sein).
  Sensor-Polarität aus `FUN_0800e81a`: Pegel 0 = Filament vorhanden.
  → Live `1c ff 00 00` (Umstecken, B = `unknown`) passt: B = Filament ohne Tag-Daten.
  Die Host-Angabe „remain B = 0“ (T1B, Klipper-Zustand) ist ein Host-Wert, kein Wire-Byte.
- **0x10 mode 5** (`FUN_080045c6`): Odometer-Float wird byteweise umgedreht gesendet (BE) –
  passt zu den Live-0x10-Antworten `f7 01 07 00 10 c4 …` (= negativer Float, Status 0).

## 11. Live-Gegenprobe 15.10.2026 (klippy.log 05.–16.10., Drucker-Zugriff nur lesend)

Details und Belegzeilen: PROTOKOLL-485.md §6. Achtung: Logs vor dem 12.10.2026 00:23
(Log-Uhr, UTC+8) stammen von Box-FW **113**, danach R153 (= dieser Befund).

**Bestätigt (LIVE BELEGT 15.10.2026):**
- CRC-Scope `len..data` (alle 36 626 RX-Frames, auch ein defekter Frame mit gleicher Host-CRC).
- 0x0d op 0/1 sofort (Host sendet Maske 0x0F), op 2 antwortet erst nach dem Vorgang
  (31–37 s je Slot, eine Sendung); op 2 in IDLE mit belegtem Hub: Pull-back-Blockade (~11 s,
  Flags 0x90).
- 0x10 Modi 0/4/5/6/7 wie Tabelle (mode 5 = Odometer-Float BE, st=0c einmal je mode-4-Lauf);
  0x11 p0/p1 inkl. Fehler 0x14 und **0x19** (Motorblock, Flags 0x91).
- 0x0a: Byte 3 Zustand (0,1,2,4 gesehen), Byte 4 Sensor-B-/Hub-Maske (= 0x08 [01]), Byte 2
  Flags (Slotmaske einmalig, 0x90/0x91), in ERROR Status = S[3].
- 0x04 mode 0/1, 0x08 sel 0/1, 0x03 0xFF/0x00-Semantik, 0x15-Layout, 0x14 „153“+SN.
- 0x30-Ereignis mit 4 Byte; Werte 1/2/3 passen zu Einstecken / Herausziehen / Preload fertig
  (Deutung, gestützt).
- An die Box gehen nur 02/03/04/05/08/0a/0d/0e/0f/10/11/14/15/a2/a3 – alle in §2.

**Widersprochen / neu:**
- **0x0a Byte 5** („Slot hat gültige RFID-Daten“, §6 Tabelle Zeile 5, V): live **immer 0**
  (8 268 Frames, auch mit RFID-Spule) → Deutung **unbelegt/widersprochen**. **→ BEFUND2 §2: Typ-7-Tag-Sperrbit.**
- **0x51** (§5: „nur LED-Mapping“): live **echter Fehlercode** in STATE_ERROR, Host-Name
  SPEED_ERR, key846 „box speed is smaller than extruder“ (452× am 12.10.). Quelle im Code
  weiter nicht verfolgt. **→ BEFUND2 §4: Quelle = Thread print_check.**
- **0x0a Byte 0/1** (§9 offen): langsam gegenläufig variierend → Vermutung Temp °C / Feuchte %
  (AHT30); nicht gegengeprüft. **→ BEFUND2 §1: belegt (B+L).**
- **FW 113** liefert 0x0a mit nur 4 Datenbytes (Status 0) – nicht mit dem 0x30-Ereignis verwechseln.
- Fehler löschen: Host nutzt 0x04 [00 01] (ERROR → IDLE), nie 0x10 mode 3.
- Host-Namen: 0x08 GET_FILAMENT_SENSOR_STATE, 0x0f TIGHTEN_UP_ENABLE (Semantik „Auto-
  Preload-Sperre“ aus §2 bleibt unsere Deutung), 0x15 GET_HARDWARE_STATUS, Status 0x30
  UPDATE_STATE.

**Nicht prüfbar (nie live):** 0x0d op 3, Status 0x50, Zustände RELOAD/TEST, 0x06-FW-Bug
(0x06/0x07/0x09 nie an die Box), Adressen 0xEA/0xEB.
