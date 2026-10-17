# PRTOUCH-MCU-BEFUND 17.10.2026 – PrTouch auf MCU-Bit-Ebene (statisch)

**Kurz:** Die Düsen-MCU liest einen **CS1237-ADC (Chipsea, 24 Bit) per Bitbang** (CLK = PB13, DOUT = PB14),
konfiguriert ihn mit `cfg_regs` (60 = 1280 Hz / PGA 128), filtert die Werte (Hochpass gegen einen gleitenden,
37–49 ms alten Mittelwert → 5er-Ausreißerfilter → IIR-Tiefpass) und löst aus, wenn **drei aufeinanderfolgende
gefilterte Samples steigend** über `min_hold`, `min_hold+add_hold`, `min_hold+2·add_hold` liegen. Bei Auslösung
zieht sie **PA15 auf 0 und friert ihren Puffer ein**. Die Main-MCU friert bei PC7 = 0 ihren Z-Schritt-Puffer ein;
gestoppt wird Z über den normalen Klipper-Endstop/trsync. `ned_tftr`/`ned_hftr` sind nur Schalter (≠ 0),
`ned_lftr` ist der Tiefpass-Koeffizient ×1000, `lmt_dead` ist eine **Sample-Anzahl** (min. 64), `max_hold` hat
im Bitbang-Modus **keine Schwellenfunktion** (nur Umschaltung Antast-/Stream-Modus), `acq_tick` ist im
Bitbang-Modus nur ein Ein-Schalter. Datenformat: gepackte Deltas; Druck = s32 (24-Bit-Zweierkomplement),
Ticks = u32 DWT-Zyklenzähler, Schritt-Daten = s32 Stepper-Position.

Neue sicherheitsrelevante Punkte für `k2_prtouch` (Details §7): Der Trigger ist ein **Anstiegsdetektor**
(langsamer Kraftaufbau löst nicht aus), `ned_hftr = 0` oder `min_hold == max_hold` machen das Antasten
unbrauchbar bzw. gefährlich, `arm_settle_s` muss die Totzeit `lmt_dead` abdecken, und die Pull-Belegung von PC7
hängt bei der Creality-Firmware von der Config-Reihenfolge ab (`pull_up=0` heißt dort **Pull-Down**).

Methode: nur statisch, kein Zugriff auf Drucker/Pi. Eigenes Ghidra-Projekt + llvm-objdump, eigenes Modell der
Filterkette; frühere Vorarbeiten eines weiteren KI-Agenten (lokal, nicht im Repo) nur als Hinweis genutzt und nicht übernommen.
Kennzeichnung: **B** = Beobachtung (Adresse/Dekompilat/Datei:Zeile), **F** = Folgerung, **ANNAHME** = nicht belegt.

---

## 0. Grundlagen

| | Main (GD32F303RET6) | Düse (GD32F303CBT6) |
|---|---|---|
| Image | `fw/F008/mcu0_140_G32-mcu0_022_000.bin` (SHA256 `846867c1…6b181`) | `fw/F008/noz0_130_G30-noz0_021_000.bin` (SHA256 `6915e65b…be3208`) |
| Basis | 0x08003000 | 0x08003000 |
| Befehlstabelle | Datei-Off **0x7434** = VMA **0x0800A434** | Datei-Off 0x7410 = VMA 0x0800A410 |
| prtouch-IDs | 38–49 (wie `prtouch_mcu_formats.json`) | dito |
| prtouch-Version (expar1) | 0x47 = 71 | 0x47 = 71 |

- **B** Tabelle eigenständig gefunden (`arbeitsdateien-lokal/re-prtouch-1017/scripts/tabelle.py`, Suche nach msgid 1..75
  im Layout `info|param_types|func`): genau ein Treffer je Image, param_types aller prtouch-Befehle stimmen mit dem
  Dictionary überein. Die im Auftrag genannte „0x0800743C“ ist der alte **Datei-Offset** 0x743C (func-Feld im
  verschobenen Layout), keine VMA.
- **B** Main und Düse enthalten **denselben** prtouch-Code an denselben Adressen; Byte-Unterschiede im Bereich
  0x08007180–0x080083A0 und in den Handlern nur in Literal-Pools und `bl`-Zielen der libgcc-Gleitkommaroutinen
  (Vergleich im Arbeitsordner). Die Düse nutzt den pres-Teil, die Main-MCU den step-Teil.
- **B** Die Antwort-Encoder stimmen mit `prtouch_mcu_formats.json` überein: `ack_prtouch` id 95 (Düse 0x080097A8,
  Main 0x080097B8), `resault_prtouch_pres` id −30 (0x080097C0 Düse), `resault_prtouch_step` −31 (0x080097C8 Main),
  `resault_prtouch_step_cnt` −32 (0x080097C0 Main), `resault_prtouch_apax` 94 (0x080097A0 Düse).
- **B** Laufzeitkontext: Creality hat in `run_tasks` das `irq_wait` der Leerlaufschleife durch **Polling der
  prtouch-Tasks** ersetzt: 0x08007BA4–0x08007BA6 `tasks_status = 0xFF (TS_IDLE)`, 0x08007BA8 `cpsie i`, Schleife bis
  0x08007D94 (`while tasks_status != 0`). Darin nacheinander: pres-Task (0x08007BAA ff.), step-Task (0x08007BD0 ff.),
  apax-Task (0x08007C52 ff.). **F** Abtastung läuft mit freigegebenen Interrupts, immer wenn die MCU nichts
  anderes zu tun hat.
- **B** Zeitbasis aller prtouch-Ticks = **DWT->CYCCNT** (Literal 0xE0001000 bei 0x08007470/0x080043C0/0x08003B9C,
  gelesen an +4) – das ist in Klipper (`src/generic/armcm_timer.c:44-46`) auch die MCU-Uhr. Takt 120 MHz
  (`CLOCK_FREQ` beider Dictionaries).

### Zustandsstrukturen (B, aus Handlern + Task)

Düse, pres-Zustand **S = 0x200007DC** (Wortindex):

| Index | Inhalt | Beleg |
|---|---|---|
| 0 | oid | 0x08004A84 |
| 1 | ADC-Modus (1 wenn clk_pin == sdo_pin) | 0x08004AA2–0x08004AAA |
| 2 | Kanalzahl = idx+1 | 0x08004A78–0x08004A7A |
| 3 | cfg_regs | 0x080042CA |
| 4 | tri_chxs (Bitmaske Kanal) | 0x08007E36, 0x080081DC |
| 5..8 | letzte Lesezeit je Kanal (Timeout-Bezug) | 0x08004384–0x08004398, 0x0800518A |
| 9..12 | Hochpass-Summe je Kanal | 0x08007E78, 0x08007EA6 |
| 13..16 | Zähler Config-Handshake (gerade = schreiben, ungerade = zurücklesen) | 0x08007E00–0x08007E1A |
| 17..20 | zurückgelesenes CS1237-Konfigregister | 0x08007DF8, 0x08007E08 |
| 21..24 | Sample-Zähler seit Start | 0x08007E64–0x08007E6A |
| 0x19.. | ticks[4][64] (Schieberegister, Index 63 = neuestes) | 0x08007E3A–0x08007E60 |
| 0x119.. | Rohwerte[4][64] (`is_src=1`) | dito, `read` 0x08004058 |
| 0x219.. | gefilterte Werte[4][64] (`is_src=0`) | dito, `read` 0x08003F92 |
| 0x319.. | Hochpass-Fenster M[4][21] | 0x08007E7A–0x08007EAE |
| 0x4AE..0x4B5 | acq_tick, ned_tftr, ned_hftr, a = ned_lftr/1000 (float), min_hold, max_hold, add_hold, lmt_dead (≥ 64) | 0x080042CC–0x0800431E |
| 0x4BA | Swap-Ausgang (PA15) | 0x08004A98, 0x0800837C → 0x20001AC4 |
| 0x4C0+i / Byte 0x1310+i | CLK-Ausgang / DOUT-Eingang Kanal i | 0x08004ADA–0x08004AFE |

Main, step-Zustand **T = 0x20001B00**: [0] oid, [1..3] oid_xstp/ystp/zstp, [4] aqc_tick, [5] letzte Abtastzeit,
[6..0x45] ticks[64], [0x46..0x85] Z-Position[64], Byte 0x218 Swap-Eingang PC7
(0x080048FC–0x0800491C, 0x08003B68–0x08003B86, 0x08007BD0–0x08007C50).

---

## 1. ADC/Sensor-Auslesung

**Antwort (beantwortet):**

- **Pins:** clk = Ausgang, sdo = Eingang mit Pull-Up; Werte vom Host (`clk_pin=PB13 sdo_pin=PB14`), nicht in der
  Firmware fest. **B** `config_prtouch_pres` 0x08004AD0–0x08004AFE: `gpio_out_setup(clk, 1)` (CLK startet HIGH),
  `gpio_in_setup(sdo, pull_up=1)`; Sonderfall `clk == sdo` → `gpio_adc_setup` (0x08004AAE–0x08004ABA) = alternativer
  Analog-Modus (interner ADC, 12 Bit) – beim K2 nicht benutzt. PB13/PB14 selbst: Stock-Emulation
  (PRTOUCH-LESETEST-BEFUND §1) + `printer.cfg:339`.
- **Chip/Protokoll: CS1237-Familie (Chipsea), 2-Draht (SCLK + DRDY/DOUT).**
  - Daten lesen 0x080050D0/0x08005134–0x0800520E: CLK low, Wartezeit, DOUT prüfen (low = Daten bereit);
    dann **27 Takte** (0x080051A2 `movs r5,#0x1b`), Bit jeweils in der High-Phase gelesen, MSB zuerst;
    Ergebnis `>> 3` = **24 Datenbits**, die 3 Zusatztakte sind die CS1237-Status-/Endetakte.
  - Konfigregister lesen 0x08005228: 29 Takte, Befehl **0x56** (7 Bit, MSB zuerst), 1 Umschalttakt, 8 Bit lesen,
    1 Endtakt = 46 Takte. Schreiben 0x080054B0: Befehl **0x65**, 8 Bit `cfg_regs` MSB zuerst. 0x56/0x65 und
    27/46 Takte sind die CS1237-Befehle/-Rahmen (Datenblattwissen, nicht im Image).
  - **B** Bedeutung von `cfg_regs` steht in der Stock-Config selbst: `printer.cfg:343
    pres_cfg_regs:60 #60（1280/128）44（640/128）56（1280/64）` = Datenrate/Verstärkung. Das passt bitgenau auf das
    CS1237-Register (Bit 5:4 Rate 11 = 1280 Hz, Bit 3:2 PGA 11 = 128, Bit 1:0 Kanal A). Live 0,7907 ms ≈ 1/1280 Hz.
  - **ANNAHME** exakter Typ CS1237 vs. pinkompatibler CS1238 – statisch nicht unterscheidbar (Kanalbits = 00).
- **Bitbang-Timing (F, Schätzung aus Befehlszählung, nicht gemessen):** zwischen den Flanken je eine
  Zählschleife über 10 Durchläufe auf einer Stack-Variable (z. B. 0x080051B2–0x080051C4) ≈ 90–100 Takte
  + GPIO-Aufruf → Halbperiode ≈ 1 µs, SCLK ≈ 0,5 MHz, ein Lesevorgang ≈ 55 µs. Läuft mit freigegebenen IRQs.
- **Wartelogik (B):** DOUT high und seit letztem Lesen ≤ 599 999 Takte (0x08005224 → **5 ms**) → Rückgabe
  0x80000000 = „nicht bereit“, Sample wird verworfen (0x08005208, 0x08007E1E). Nach > 5 ms wird **trotzdem gelesen**
  (F: getrennter/hängender Sensor liefert dann alle 5 ms den Wert −1 statt nichts). Konfig-Zugriffe: 120 ms
  (0xDBBA00 bei 0x080054AC).
- **Konfig-Handshake (B):** Ab jedem `start_prtouch_pres` wird abwechselnd das Register geschrieben (Zähler gerade)
  und zurückgelesen (ungerade), bis Rücklesewert == `cfg_regs` (0x08007DF4–0x08007E1A, 0x080081A4–0x080081B4); erst
  dann reine Datenlesungen. Die bei diesen Zugriffen mitgelesenen 24-Bit-Daten werden als Samples verwendet.
- **Datenformat (B):** Druck = **s32** aus 24-Bit-Zweierkomplement: `>> 3`, bei gesetztem Bit 23 `| 0xFF000000`
  (0x080051F6–0x080051FE; Konfigpfade 0x08005228/0x080054B0 analog mit `>> 5`). Wertebereich ±8 388 607 –
  passt zu den negativen Live-Ruhewerten (≈ −1 427 700). Ticks = **u32** (CYCCNT). Kein u32-Druckwert.

**Sicherheit:** hoch (Protokoll, Bitbreite, Vorzeichen, Register), mittel (Chiptyp-Name), niedrig (Taktzeiten).

**Empfehlung k2_prtouch:** PB13/PB14 bleiben; Ruhetest um Plausibilität erweitern (Rohwert ≠ −1 und im
24-Bit-Bereich, Tickabstand ≈ 0,78 ms ± 5 % statt ~5 ms), weil ein abgezogener Sensor mit `dif = 0` den
heutigen `dif ≤ rest_max_dif`-Test besteht (`k2_prtouch.py:814-823`).

---

## 2. Frame-Formate

**Antwort (beantwortet):** `prtouch_mcu_formats.json` ist **korrekt** (Namen, Typen, IDs, Feldreihenfolge).
Bedeutung und Skalierung der Felder:

| Nachricht | Felder → Herkunft (B) |
|---|---|
| `ack_prtouch` (config_pres) | err 0, expar0 0, expar1 0 (0x08004ABE–0x08004AC8) |
| `ack_prtouch` (start_pres) | err 0, **expar0 64**, expar1 0 (0x080043A6–0x080043B0; live identisch) |
| `ack_prtouch` (stop_pres) | err 0, expar0 0, **expar1 71** = Version (0x08004294–0x080042A6) |
| `ack_prtouch` (config_step / start_step) | err 0, expar0 0, expar1 0 (0x08004920–0x08004928 / 0x08003B88–0x08003B90) |
| `ack_prtouch` (stop_step) | err 0, **expar0 = PC7-Rohpegel**, expar1 71 (0x08004244–0x0800425A) |
| `resault_prtouch_pres` (Antwort auf read) | oid, tri_chxs = S[4], **buf_len = 64 fest**, ch, idx, len, ticks, datas (0x08004034–0x0800404E) |
| `resault_prtouch_pres` (Stream-Modus, asynchron) | buf_len = **63**, idx 0, len 0 (0x08008100–0x08008112; live genau so) |
| `resault_prtouch_step` | oid, buf_len 64, idx, len, ticks, datas (0x08003E88) |
| `resault_prtouch_step_cnt` | cnt_x/y/z = Feld `count` (Offset 0x14) der Stepper-Struktur (0x08003664–0x08003696) |

- **err ist immer 0** in allen prtouch-Handlern (B, alle `sendf`-Aufrufe oben) → eine err-Prüfung schützt nicht.
- **oid wird nicht geprüft:** start/stop/read lesen das Argument oid gar nicht, Antworten tragen die gespeicherte oid.
- **Packung (B):** Encoder 0x08006524 / Abschluss 0x080064D8: Wert 0 gegen 0, danach Deltas zum Vorgänger; je
  Wert 1/2/3/4 Byte vorzeichenbehaftet LE (Grenzen ±2⁷/±2¹⁵/±2²³); 2-Bit-Größenmaske, Maskenbytes rückwärts
  vor den Daten abgelegt; vorn die Anzahl n. Ergebnis `[n][Maske ⌈n/4⌉ B, Big-Endian][Werte]` – bestätigt den
  Live-Befund und `decode_packed` (`k2_prtouch.py:655`). Ticks laufen modulo 2³² (u32-Maske nötig, vorhanden).
  Werte 0x80000000 werden übersprungen (0x0800652A–0x08006536).
- **Blockgröße (B):** Abbruch, sobald ticks+datas > 41 Byte (0x0800400E `cmp r1,#0x29`), das Überlauf-Sample ist
  **mit drin**. Darum 7–9 Samples je Antwort, nicht fest 8.
- **`len`-Fehler (B):** `len = Endindex − idx + 1` (0x08004014–0x0800401A). Passt der angefragte Bereich komplett,
  ist `len` **um 1 zu groß** (live D3: len 9 bei 8 Samples). Maßgeblich ist n aus dem Paket.
- **idx (B):** Index im **Schieberegister** (0 = ältestes, 63 = neuestes), kein Ring: jedes Sample schiebt alle
  64 Einträge um 1 (0x08007E3A–0x08007E60). `idx+len` wird auf 64 begrenzt (0x08003F7E–0x08003F88).
- **is_src (B):** 1 = Rohwerte, 0 = gefilterte Werte, **≥ 2 = nur Status** (Antwort mit tri_chxs, ohne Daten,
  0x08004060–0x0800407A) – so fragt Stock `read_prtouch_pres [1, 2, 0, 0, 0]`.
- **ch (B):** ohne Bereichsprüfung als Offset benutzt (0x08003F78 `lsls r6,r1,#8`); gültig 0..Kanalzahl−1, max. 4.
- **Skalierung:** pres-datas = CS1237-Counts (keine physikalische Einheit im Image); ticks = MCU-Takte 120 MHz;
  step-datas = Z-Stepper-Position in Mikroschritten, berechnet wie Upstream `stepper_get_position`
  (`|position − count′|`, count′ = count/2 ohne SF_SINGLE_SCHED, 0x08007C22–0x08007C36) **minus 0x40000000**
  (0x08007C38) – identisch zu Upstream `src/stepper.c:321-344` (POSITION_BIAS). Hinweis: das Lesen geschieht mit
  freigegebenen IRQs (Upstream verlangt gesperrte), seltene Ein-Schritt-Ausreißer möglich (F).
- **Zeitbezug (B):** pres-Ticks = Düsen-Uhr, step-Ticks = Main-Uhr – **verschiedene Uhren**. Beide Puffer enden
  aber am Auslösezeitpunkt (§4), daher funktioniert „Zeit relativ zum letzten Sample“.

**Sicherheit:** hoch.

**Empfehlung k2_prtouch:** `len` ignorieren (wird schon so gemacht); beim Blocklesen den nächsten Index aus n
bilden (`idx += n`) statt fester 8er-Schritte – live lieferte idx 0 nur 7 Samples, Sample 7 fehlte dann
(`READ_CHUNK` `k2_prtouch.py:221`, `read_pres` 1040-1050, `read_step` 1137-1145, `read_frozen` 1021/1123).
Einträge mit Tick 0 und Wert 0 (Puffer noch nicht voll) verwerfen. Für Schritt-Daten das Vorzeichen bei
invertierter Z-Richtung umdrehen (Upstream `klippy/stepper.py:216-218`) oder nur Beträge relativ zum Ende nutzen.

---

## 3. Schwellen und Filter in der Düsen-MCU

### 3.1 Betriebsarten (B)

| Bedingung | Verhalten |
|---|---|
| `acq_tick == 0` | keine Abtastung (0x08007BAA–0x08007BAE); `stop_*` und Auslösung setzen 0 |
| Bitbang (clk ≠ sdo) | Abtastung bei jedem DRDY des CS1237; `acq_tick` wird **nicht** als Periode benutzt (Sprung 0x08007BB6 → 0x08007DC4) |
| ADC-Modus (clk == sdo) | Periode = `acq_tick` Takte (0x08007BB0–0x08007BCC) – beim K2 nicht aktiv |
| `min_hold == max_hold` | **Stream-Modus:** Rohwerte gepackt, asynchron gesendet, kein Puffer, kein Filter, keine Auslösung (0x08007E26–0x08007E30 → 0x080080A0–0x0800816E) – Stock READ_PRES `[…,128,128,…]` |
| `min_hold != max_hold` | **Antast-Modus:** Puffer, Filter, Auslösung (unten) |

### 3.2 Filterkette je Sample im Antast-Modus (B)

1. `tri_chxs = 0`; Schieberegister ticks/roh/gefiltert um 1; neues Sample → roh[63] = gefiltert[63],
   tick[63] = CYCCNT; Sample-Zähler +1 (0x08007E34–0x08007E6A).
2. **Hochpass, nur wenn `ned_hftr ≠ 0`** (0x08007E6C–0x08007EC2): Fenster M[21] wird geschoben, neu hinein kommt
   **roh[20]** (= Sample von vor 43 Takten); M[16] := der Wert aus M[16..20], der M[15] am nächsten liegt
   (Ausreißerfilter 0x08005070); Summe über M[0..15] gleitend; **gefiltert[63] −= Summe/16** (Rundung Richtung 0).
   **F:** Bezugswert = Mittel der (ausreißergefilterten) Samples von vor 48–63 Abtastungen ≈ **37–49 ms**.
3. **Ausreißerfilter, nur wenn `ned_tftr ≠ 0`** (0x08007EC6, 0x08008198–0x080081A2): gefiltert[59] := Wert aus
   gefiltert[59..63], der gefiltert[58] am nächsten liegt. Fenstergröße fest 5 – der Wert von `ned_tftr` zählt nicht.
4. **IIR-Tiefpass, wenn |a| > 0,001 und Zähler > 5** (0x08007ED0–0x08007F2E, Konstante 1e−3 als double bei
   0x08008078, `__aeabi_dcmpgt` 0x08009044): **y[59] = (1−a)·y[58] + a·x[59]**, `a = ned_lftr / 1000.0f`
   (`__aeabi_ui2f` + `__aeabi_fdiv` mit 1000.0f bei 0x080042EE–0x080042F4, Literal 0x447A0000 bei 0x080043BC).
   Stock a = 0,8 (leichte Glättung); a = 1 → aus; a = 0 → aus.
5. **Totzeit:** Auswertung erst ab Zähler ≥ `lmt_dead` (0x08007F34–0x08007F3A); `lmt_dead` wird beim Start auf
   **mindestens 64** angehoben (0x08004310–0x0800431E). Stock 128 ≈ 100 ms.

**Folge (F):** In gefiltert[0..58] stehen voll gefilterte Werte, gefiltert[59] ist eben fertig, gefiltert[60..63]
sind nur hochpassgefiltert. `is_src=0` liefert genau dieses Feld.

### 3.3 Auslösung (Bitbang-Modus, B, 0x08007FD2–0x08008074, 0x080081DC–0x080081E6)

- L[0..4] := gefiltert[59..63]; L[1] := Wert aus L[1..3] am nächsten an L[0]; L[2] := Wert aus L[2..4] am nächsten
  an L[1] (Ausreißerfilter 0x08008000/0x0800800A).
- Richtung: ist gefiltert[0] > gefiltert[63], werden alle L negiert (Vorzeichen egal).
- Auslösung, wenn **L[2] ≤ L[3] ≤ L[4]** und **L[2] ≥ min_hold**, **L[3] ≥ min_hold + add_hold**,
  **L[4] ≥ min_hold + 2·add_hold** und **alle gefiltert[0..60] ≤ L[2]** (unnegiert verglichen).
- Dann `tri_chxs |= 1 << Kanal`. Nach dem Kanaldurchlauf (0x08008172–0x08008196): `acq_tick := 0` (Abtaststopp,
  Puffer eingefroren) und **`gpio_out_write(PA15, 0)`** per `bl 0x0800426C` bei **0x08008192**.
- `max_hold` hat im Bitbang-Modus **keine** Schwellenfunktion; nur im ADC-Modus zählt er (alle letzten `min_hold`
  Samples ≥ `max_hold`, 0x08007F46–0x08007F86).
- Kein MCU-Notstopp bei Überkraft (keine weitere Schwelle im Code; `pres_chk_hold` wird nicht übertragen).

### 3.4 Parameter-Semantik (B, Umrechnung Host→MCU aus PRTOUCH-LESETEST-BEFUND §7)

| MCU-Parameter | Stock | Bedeutung in der Firmware | Beleg |
|---|---|---|---|
| `cfg_regs` | 60 | CS1237-Konfigregister (Rate/PGA/Kanal); bestimmt die **echte** Abtastrate | 0x080054B0, `printer.cfg:343` |
| `acq_tick` | 93 750 | Bitbang: nur Ein/Aus (≠ 0); ADC-Modus: Periode in Takten | 0x08007BAA–0x08007BCC |
| `ned_tftr` | 5 | Schalter Ausreißerfilter (Stufe 59), Wert sonst ohne Wirkung | 0x08007EC6 |
| `ned_hftr` | 1000 | Schalter Hochpass, Wert sonst ohne Wirkung | 0x08007E6C |
| `ned_lftr` | 800 | Tiefpass a = ned_lftr/1000 (u32 → float), aktiv bei a > 0,001 | 0x080042EE, 0x08007ED6 |
| `min_hold` | 4000 | Schwelle L[2] (Counts nach Hochpass) | 0x0800803E |
| `add_hold` | 500 | Zuschlag: L[3] ≥ min+add, L[4] ≥ min+2·add | 0x08008046–0x0800804E |
| `max_hold` | 10 000 | nur ≠ min_hold → Antast-Modus (ADC-Modus: Schwelle) | 0x08007E26–0x08007E30 |
| `lmt_dead` | 128 | Totzeit in **Samples** (nicht ms), min. 64 | 0x08004318, 0x08007F38 |
| `aqc_tick` | 46 875 | Main: Takte zwischen zwei Z-Positions-Samples | 0x08007BD8–0x08007BEA |
| `idx` (config) | 0 | Kanalnummer, Kanalzahl = idx+1 (max. 4) | 0x08004A78 |
| `oid_xstp/ystp` | – | nur für `cont_prtouch_step` (Zählerstände) | 0x08003664 |
| `oid_zstp` | z1 | Stepper, dessen Position der step-Puffer aufzeichnet | 0x08007C14–0x08007C1E |
| `swp_pin` (step) | PC7 | Eingang mit **Pull-Up**, Lesen bei stop/Task | 0x08004910–0x0800491C |
| `swp_pin` (pres) | PA15 | Push-Pull-Ausgang, HIGH ab config/start | 0x08004A8E–0x08004A9C |

`ned_tftr`/`ned_hftr` sind im Dictionary `%c`; die Firmware speichert das volle Wort (0x080042D0–0x080042E2) und
nutzt es nur als ≠ 0 – Stocks 1000 ist daher unkritisch.

### 3.5 Modellrechnung (F, `scripts/mcu_pres_modell.py`, Ergebnis `modell_ergebnis.txt`)

Ein 1:1-Python-Nachbau von §3.2/3.3 (float32 wie libgcc) mit den **2 523 live gelesenen Ruhe-Rohwerten** (Test A):

- Stock-Parameter, reine Ruhe: **keine Auslösung**, |gefiltert| ≤ 1 002 (Live: 0 Fehlauslöser – passt).
- Sprung: löst erst ab **5 000 Counts** (= min + 2·add) aus, **2 Samples** nach dem ersten hohen Sample (≈ 1,6 ms);
  1- und 2-Sample-Spitzen lösen nie aus.
- Rampe: unter **≈ 70–80 Counts/Sample** (≈ 90–100 Counts/ms) keine Auslösung, egal wie groß die Kraft wird;
  80 → nach 73 Samples, 300 → nach 17, 3 000 → nach 4.
- `ned_hftr = 0`: **Fehlauslösung nach 133 Samples** (Absolutwerte gegen Schwellen) – Hochpass ist Pflicht.

**Sicherheit:** hoch (Code), mittel (Modell ist Nachbau, nicht gegen eingefrorene Live-Puffer geprüft).

**Empfehlung k2_prtouch:** siehe §7 (Parameterprüfungen, Mindestgeschwindigkeit, Ruhetest am gefilterten Kanal).

---

## 4. Stopp-Auslösung – nur Lücken gegenüber PRTOUCH-STOPP-BEWEIS

1. **Wo die Düse PA15 zieht (neu, B):** einzige Trigger-Schreibstelle 0x08008188–0x08008192 im Leerlauf-Task:
   `acq_tick := 0`, dann `bl 0x0800426C` (gpio_out_write) mit r1 = 0. Weitere PA15-Schreiber: config (HIGH,
   0x08004A94 via gpio_out_setup), start (HIGH, 0x080043A2), stop (= `sta_swap`, 0x080042B8). STOPP-BEWEIS §3
   („genau 2 Aufrufer, Trigger-Pfad schreibt nicht“) und HANDLER-TABELLE-BEFUND („Inline-BSRR/BRR ohne Helfer“)
   sind damit präzisiert: Es ist ein **normaler Helferaufruf** aus dem Leerlauf-Task (0x0800426C hat 55 Aufrufer).
2. **PA15 ist Push-Pull** (gpio_out_setup → Modus 3, 0x08004938/0x0800469A) – treibt PC7 aktiv in beide Richtungen.
3. **Main friert ihren Puffer beim Trigger ein (neu, B):** step-Task 0x08007BD0–0x08007C50: zwischen den
   regulären Abtastungen wird PC7 gelesen; ist PC7 = 0, wird **sofort ein letztes Sample** genommen und danach
   `aqc_tick := 0` (0x08007C44–0x08007C50). **F:** Beide Puffer enden damit am selben Ereignis (Düse: das
   auslösende Sample; Main: Erkennung PC7 = 0). Versatz geschätzt ≤ 0,1–0,2 ms (Lesen + Filter + Polling) ≈ ≤ 1 µm
   bei 5 mm/s. Das erklärt `tri_time = 0`, `tri_pose = STEP_DATA[-1]` und den „Swap-Nullpunkt“ ohne Uhrabgleich.
4. **Der eigentliche Z-Stopp ist Upstream-Standard (B):** `endstop_home` 0x08005A88, Überabtastung 0x08005B30
   (liest PC7 bei 0x08005B36, Vergleich mit `pin_value`), Auslösung über trsync 0x08005784 →
   `stepper_stop_on_trigger`. Keine prtouch-Sonderlogik.
5. **Pull-Semantik der Creality-Firmware weicht ab (neu, B):** GPIO-Kern 0x08004734–0x08004766:
   `pull_up = 0` → **Pull-Down**, `1` → Pull-Up, sonst (z. B. −1/`~`) → schwebend. Upstream: 0 = schwebend
   (`src/stm32/stm32f1.c:219`). `config_endstop` reicht `pull_up` durch (0x08005BFE), `config_prtouch_step` setzt
   immer Pull-Up (0x08004910). Wer zuletzt konfiguriert, bestimmt den Pull: Stock = endstop, dann prtouch_step →
   **Pull-Up** (Emulation `prtouch-emu/emu_probe.txt:144,179`).
6. **Mindestreaktion (F):** Düse ≥ 3 aufeinanderfolgende Samples über Schwelle (≈ 1,6 ms ab erstem), Main-Endstop
   nach Upstream-Abtastung (Stock 4 × 15 µs, Rast 100 µs). Wie schnell die Kraft beim Kontakt steigt, ist statisch
   nicht bestimmbar.

**Sicherheit:** hoch (1–5), niedrig (Zeitwerte in 3 und 6).

---

## 5. ANNAHMEN in `pi-host/README-k2_prtouch.md`

### Hauptweg (A.5, README:217-230)

| Nr. | Status | Beleg / Kernaussage |
|---|---|---|
| P1 Stopp unter Bewegung schnell genug | **teilweise** | MCU-seitig: Auslösung ≥ 2 Samples nach erstem Überschreiten (§3.5), PA15 im selben Durchlauf (0x08008192), Main Standard-Endstop (§4.4). **Neu:** Anstiegsdetektor – langsamer Kraftaufbau (< ~80 Counts/Sample) löst nie aus. Physik offen. |
| P2 `arm_settle_s` 0,2 s genügt zur „Ruhewertübernahme“ | **bestätigt mit Korrektur** | Es gibt keine Ruhewertübernahme: Bezugswert gleitet (§3.2/2). Die Düse wertet erst nach `lmt_dead` Samples aus (0x08007F34; Stock 128 ≈ 100 ms). 0,2 s reicht für Stock; Kontakt innerhalb der Totzeit wird **nicht** erkannt. |
| P3 Ruhetest mit gestartetem start_prtouch_step | **bestätigt** | getrennte Zustände auf getrennten MCUs (S 0x200007DC Düse, T 0x20001B00 Main), kein Querbezug. |
| P4 Signalweg-Test vor jeder Fahrt stört nicht | **bestätigt** | stop_prtouch_pres setzt nur `acq_tick = 0` + PA15 (0x0800428C–0x080042B8); start initialisiert danach alles neu (0x080042C4 ff.). **Nur vor** start erlaubt – danach würde er entschärfen. |
| P5 start/stop_prtouch_step antworten mit ack_prtouch | **bestätigt** | 0x08003B90 bzw. 0x0800425A; stop liefert expar0 = PC7-Pegel, expar1 = 71. |
| P6 PC7 geteilt ohne Konflikt | **bestätigt, mit Hinweis** | Beide richten nur einen Eingang ein, kein Besitzkonzept in der MCU. Aber: Pull hängt von der Reihenfolge ab (§4.5); k2_prtouch sendet config_prtouch_step **vor** config_endstop (Callback-Reihenfolge `k2_prtouch.py:2157-2164`, Upstream `mcu.py:1015`) → **Pull-Down** statt Stock-Pull-Up. Bei aktiv getriebenem PA15 ohne Wirkung. |

### Altbericht §4 (README:437-499)

| Nr. | Status | Beleg / Kernaussage |
|---|---|---|
| 1 Datenformat u32/s32 4 Byte | **widerlegt** (live) → statisch erklärt | gepackte Deltas (0x08006524/0x080064D8); Druck s32 aus 24 Bit, Ticks u32, step s32 Position (§1, §2) |
| 2 clk/sdo PB13/PB14 | **bestätigt** | Rollen statisch (clk Ausgang, sdo Eingang, 0x08004AD0–0x08004AFE), Pins per Emulation |
| 3 ned-Mapping/Skalierung | **bestätigt** (Zuordnung), alte ×10 **widerlegt** | ×1000 = `/1000.0f` in der MCU (0x080042F4); tftr/hftr nur Schalter |
| 4 holds-Mapping | **bestätigt** (Reihenfolge) | Semantik §3.3; `max_hold` ohne Schwellenwirkung im Bitbang |
| 5 lmt_dead = 200 | **widerlegt** (Wert 128) | Bedeutung: Totzeit-Samples, min. 64 (0x08004318) |
| 6 aqc_tick 45 700 | **widerlegt** (46 875) | Bedeutung: Main-Takte je step-Sample (0x08007BE6) |
| 7 read-Args | **bestätigt/ergänzt** | is_src 1 roh / 0 gefiltert / ≥ 2 Status; idx = Schieberegister-Index; Block 7–9 Samples; `len` ggf. +1 (§2) |
| 8 Kontaktkriterium 3000 + 2 Samples | **ersetzt** | MCU: 3 steigende Samples ≥ 4000/4500/5000 nach Hochpass (§3.3); Host-Kriterium nur noch für `mode: steps` |
| 9 Notgrenze 15 000 (`pres_chk_hold`) | **offen (Host)** | wird nicht an die MCU übertragen; MCU hat keine Notgrenze (§3.3) |
| 10 Ruhetest dif ≤ 3000 | **offen (Host)**, Ergänzung nötig | MCU hat keinen Ruhetest; Plausibilitätslücke §1 |
| 11 can_brk | **offen (Host)** | keine MCU-Entsprechung |
| 12 zoft-Semantik | **teilweise bestätigt** | Pufferende = Auslösung auf beiden MCUs (§4.3) |
| 13 Swap nur Zeitbasis | **widerlegt/präzisiert** | Swap ist Trigger-Leitung **und** Pufferende-Marke; Ruhe HIGH, Kontakt LOW (§4) |
| 14 Stepper-OIDs | **bestätigt** | nur oid_zstp für Positionsdaten; x/y nur für `cont_prtouch_step` |
| 15 Klipper-APIs | nicht MCU (bereits README A.4) | – |
| 16 Upstream-Probe-API | überholt | – |
| 17 Kompensationen | nicht MCU | keine MCU-Funktion dafür |

Code-ANNAHMEN zusätzlich: „step gleiche Packung wie pres“ (`k2_prtouch.py:712-716`) **bestätigt** (derselbe
Encoder in read_prtouch_step); „read_step ≤ 8 Samples“ (`k2_prtouch.py:1137-1145`) **bestätigt mit Korrektur**
(7–9); „Ticks/Swap-Nullpunkt ohne Versatz“ (`ticks_to_times`, `k2_prtouch.py:737`) **bestätigt** (§4.3).

---

## 6. Korrekturen an früheren Dokumenten

- PRTOUCH-STOPP-BEWEIS §3: „Trigger-Pfad schreibt nicht“ / „genau 2 Aufrufer“ falsch – Schreibstelle 0x08008192.
  `0x08006D30` ist `sendf`, nicht der Scheduler.
- HANDLER-TABELLE-BEFUND: „Inline-BSRR/BRR-Zugriffe ohne Aufruf des Helfers“ → es ist ein normaler Helferaufruf.
- PRTOUCH-LESETEST-BEFUND §6: „Schwelle relativ zum Ruhewert beim Scharfschalten“ → relativ zu einem gleitenden
  Mittel von vor 37–49 ms; der Restversatz-Effekt erklärt sich daraus genauso.
- PRTOUCH-LESETEST-BEFUND §2.2: „Ringposition, ≤ 8 Samples“ → Schieberegister-Index, 7–9 Samples je Antwort.
- README A.3 Spalte `acq_tick`: im Bitbang-Modus ohne Wirkung als Periode.

---

## 7. Empfehlungen für `pi-host/k2_prtouch.py` (Code hier NICHT geändert)

Priorität hoch (Sicherheit):
1. **Parameter prüfen:** `ned_hftr ≠ 0` (sonst Fehlauslösung, §3.5), `min_hold ≠ max_hold` (sonst Stream-Modus,
   **nie** Auslösung → Crash), `0 < ned_lftr ≤ 1000` oder 0, `lmt_dead ≥ 64`, `cfg_regs` nur bekannte Werte
   (60/44/56). Ort: `parse_options` nach `k2_prtouch.py:491`.
2. **`arm_settle_s` an die Totzeit koppeln:** ≥ `lmt_dead` × Abtastperiode (aus `cfg_regs`: 1280 Hz → 0,78 ms)
   + Reserve; z. B. Fehler, wenn `arm_settle_s < 1,5 · lmt_dead / Rate`. Ort: `k2_prtouch.py:425-428`, `arm` 1818-1820.
3. **Mindestgeschwindigkeit:** Die MCU erkennt nur schnellen Kraftanstieg. Stock tastet immer mit 5 mm/s.
   Bis zur Messung am Gerät Antast-/Homing-Geschwindigkeit nicht weit unter 5 mm/s zulassen (Vorschlag:
   Warnung < 3 mm/s, Fehler < 1 mm/s; Grenzen sind **ANNAHME**, aus der Modellrampe + Live-zoft 0,010–0,040 mm
   grob abgeschätzt). Ort: Geschwindigkeitsprüfungen `k2_prtouch.py:388/413/1437/2013`.
4. **Ruhetest erweitern:** Tickabstand ≈ 0,78 ms (Sensor läuft), Rohwert ≠ −1 und im 24-Bit-Bereich; zusätzlich
   `is_src=0` lesen und max |gefiltert| ≪ `min_hold` prüfen (live ≈ 1 000 bei 4 000). Ort: `check_rest_ok` 814, `arm` 1821.
5. **PC7-Pull festlegen:** entweder bewusst wie Stock (config_endstop vor config_prtouch_step → Pull-Up) oder
   bewusst Pull-Down lassen (fehlersicher: ungetriebene Leitung = „ausgelöst“ → Abbruch vor der Fahrt). In jedem
   Fall dokumentieren; `^!PC7` ergäbe Pull-Up, `~!PC7` schwebend. Ort: `_build_step_config` 2206, `setup_pin` 2087.

Priorität mittel (Daten):
6. Blocklesen mit `idx += n` statt `READ_CHUNK`, Abbruch bei n = 0; Tick-0-Einträge verwerfen (§2).
7. Optional nach `stop` den Status `read_prtouch_pres [oid, 2, 0, 0, 0]` auswerten: `tri_chxs = 1` bestätigt, dass
   die Düse (und nicht Rauschen auf PC7) ausgelöst hat.
8. Schritt-Daten mit Richtungsinvertierung umrechnen (§2).

---

## 8. Offen (statisch nicht klärbar) – mit Suchumfang

- **Kraftanstieg beim echten Kontakt** (Counts pro Sample bei 5 mm/s) und damit Überfahrweg und Mindest-
  geschwindigkeit: nur Gerät (eingefrorenen `is_src=0/1`-Puffer nach einer Antastung lesen). Statisch durchsucht:
  alle Zugriffe auf die Schwellen (Literale 0x20001A94–0x20001AB0 vollständig, §3.4) – keine weitere Logik.
- **CS1237 vs. CS1238, Taktzeiten am Pin, Aufwachzeit nach Power-Down** (CLK ist nach `config` HIGH): Datenblatt
  bzw. Logikanalysator. Im Image keine weiteren Hinweise (Strings/Dictionary ohne Chipnamen).
- **apax** (`start_prtouch_apax` löscht den pres-Zustand per memset S+0x14/0xDA0, 0x08003B24–0x08003B2C) nur gestreift:
  Druck + Extruder-Schrittintervall (`espds`) für Pressure-Advance; für die Z-Probe nicht nötig.
- Ältere Main-Firmware `mcu0_130_G32-mcu0_020_000.bin`: gleiche Logik, andere IDs (37–48), Version **68** statt 71
  (0x08003F2C `movs r5,#0x44`). Läuft laut Dictionary-Version nicht auf dem Gerät.

---

## 9. Arbeitsdateien (`arbeitsdateien-lokal/re-prtouch-1017/`, lokal)

`main.bin`, `noz.bin`, `main130.bin` (Kopien), `*.dis` (llvm-objdump, VMA ab 0x08003000), `decomp_main.c`,
`decomp_noz.c` (Ghidra 12.1.4 headless, eigenes Projekt `ghidra_proj/re1017`), `noz_main_loop.c` (Leerlauf-Task),
`ghidra_scripts/K2Prep.java|K2Seed.java|K2Decomp.java`, `scripts/tabelle.py` (Befehlstabelle),
`scripts/rd.py` (Literale), `scripts/fn.py` (Dekompilat-Auszug), `scripts/mcu_pres_modell.py` +
`modell_ergebnis.txt` (Filter-/Trigger-Modell gegen Live-Ruhedaten).
