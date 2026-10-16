# GHIDRA-CFS-BEFUND2 – CFS-Firmware R153 (cfs0_000_153), offene Punkte 1–8 (Ghidra, 16.10.2026)

> Projektkopie vom 16.10.2026. Ergänzt und korrigiert `GHIDRA-CFS-BEFUND-2026-10-15.md` (Korrekturliste am Ende).
> Umgesetzt in `nachbau/box.py` (parse_box_state: temp_c/rh, inlet_tag_mask, block_*; parse_get_hardware_status: HW_COMPONENTS; STATUS_NAMES),
> `nachbau/cfswatch.py` (Klartext), `nachbau/cfsseq.py` (Kommentare), `nachbau/auto_addr.py` (Selbsttest 13 gegen FW-Verhalten).
> Keine SN/UUID/Vendor-Daten enthalten.

Ziel: `fw/cfs/cfs0_050_G32-cfs0_000_153.bin`, Basis 0x08003000, Ghidra-Projekt `cfs0_153` (Arbeitsdateien lokal, nicht im Projekt; nur lesend geöffnet).
Werkzeuge (Arbeitsdateien lokal, nicht im Projekt): `q2.sh` (Ghidra headless, `-readOnly`, Q.java), `fn.sh` (Funktion aus `all_cfs0.c` ziehen),
`rd.py`/`lit.py`/`cont.py`/`bl.py` (Literal-Pool, Zeigersuche, BL-Scanner), `decomp.py` (Keil-RW-Dekompression → `data_init.bin`).
Belegstärke: **B** = Dekompilat/Disassembly mit Adresse, **B+L** = zusätzlich live bestätigt, **V** = Deutung.

Neu gegenüber dem ersten Befund. Die `.data`-Sektion ist Keil-komprimiert (Scatter-Tabelle @0x0802da08: Quelle 0x0802db6c → 0x20000000, 0xbc4 Byte, Entpacker `0x0800316c`). Mit `decomp.py` ist sie jetzt entpackt. Damit sind die Auto-Addr-Tabelle und die Sensor-Pin-Tabelle lesbar (siehe 7 und 8).
Systemtakt: `rt_tick_from_millisecond` @0x0801d536 rechnet 1:1 um, also gilt RT_TICK_PER_SECOND = 1000 und 1 Tick = 1 ms (**B**).

---

## 1. 0x0a Byte 0/1 = Temperatur °C / rel. Feuchte % aus dem AHT30 – **B+L**

Datenfluss:

| Schritt | Adresse | Beleg |
|---|---|---|
| Init | `FUN_080032e8` @0x080032fa | `bl 0x0800cfe4` (AHT-Init, I2C-Bus „i2c0“). Bei Rückgabe ≠0: `strb #1,[0x2000390e]` (AHTxx_init_flag = Fehler) |
| Messung | `FUN_0800ced0` | I2C-Adresse `0x38`, schreibt `AC 33 00`, wartet 200 ms, liest 6 Byte. Feuchte = raw20(b1..b3)·100·2⁻²⁰, Temperatur = raw20(b3..b5)·200·2⁻²⁰ − 50. Konstanten @0x0800d10c = 100.0, @0x0800d114 = 9.5367e-7 (2⁻²⁰), @0x0800d11c = 200.0, @0x0800d124 = 50.0. Statusbyte und AHT-CRC werden **nicht** geprüft |
| Thread (nicht als Funktion angelegt) | @0x0800a5a0 | Schleife mit `rt_thread_delay(1000)`; läuft nur bei `S[6]==0` (kein Testmodus) und `[0x2000390e]==0` (AHT ok) |
| Aufruf | @0x0800a5fe | `bl 0x0800ced0(&temp,&hum)` |
| Temperatur → S[1] | @0x0800a606 / 0x0800a60e | `vcvt.s32.f32` → `strb [0x200037d1]` = S[1] |
| Feuchte → S[0] | @0x0800a614 / 0x0800a61c | `vcvt.u32.f32` → `strb [0x200037d0]` = S[0] |
| LCD-Anzeige | danach | `FUN_0800a3fe(temp)` / `FUN_0800a3be(hum)` |
| 0x0a-Antwort | `FUN_0800ed26` @0x0800ed56/0x0800ed5c | `data[0]=S[1]` (Temp), `data[1]=S[0]` (Feuchte) |

- **Skalierung/Rundung (B):** ganze Zahl, abgeschnitten Richtung 0 (VCVT ohne R). Temperatur ist ein int8 im Zweierkomplement (unter 0 °C erscheint ≥0x80); Feuchte ist uint8 von 0 bis 100. Es gibt keine Nachkommastelle und keinen Offset.
- **Takt:** etwa 1,2 s (1000 ms Delay + 200 ms Messung).
- **Fehlerfall (B):** Die Rückgabe von `ced0` wird ignoriert. Bei einem I2C-Fehler bleiben die letzten Werte stehen. Schlug die Init fehl, bleiben S[0]/S[1] dauerhaft 0 (BSS; `FUN_08003d62` initialisiert sie nicht).
- **Debug-Override (B):** Das finsh-Kommando `temp_humi [temp] [humi]` (`FUN_0800d0da`) setzt `[0x200001d8]=1`. Danach liefert `ced0` feste Werte.
- **Live:** b0 0x11–0x1c = 17–28 °C, b1 0x2d–0x39 = 45–57 % rF. Beides ist plausibel und gegenläufig, wie es bei rel. Feuchte zu erwarten ist. Dasselbe Paar liefert 0x52 (Antwort-fun 0x53) als `[S[1],S[0]]`.
- 0x53 schreibt 0x20000034/35 („slave_temp/hum“). Das ist nur der Vergleichswert des Werkstatt-Tests `FUN_080083a6` (Toleranz ±2 °C / ±5 %) und keine Kalibrierung (B).

## 2. 0x0a Byte 5 – kein Flag „RFID gültig“, sondern „Typ-7-Tag ohne Datenfreigabe“ – **B** (Deutung V)

`FUN_0800ed26` setzt Bit i auf `FUN_080053f2(i)`. Die echte Logik ist per Disassembly geprüft; das Ghidra-Dekompilat hat zwei Funktionen vermischt:

- @0x080053f6: `bl FUN_080053c6` liest `[0x2000391e+i]`. Das ist das entprellte Einlauf-Präsenzflag; es wird in `FUN_08006414` beim Log „slot%d insert filament“ auf 1 gesetzt und bei „pullout“ auf 0.
- Ist das Flag ≠0, folgt ein Tail-Call auf `0x0800dba0(i)` mit `rec = 0x20004590 + i·100` (RFID-Manager-Datensatz). Ergebnis 1 genau dann, wenn alle drei gelten:
  - `rec[0x17] ≠ 0` (Tag-Daten geladen),
  - `rec[0x63] == 7` (Tag-Byte 0x4b, im Log „NUM“, Tag-Typ),
  - `u32 rec[0x28] == 0` (Tag-Feld +0x10).

Gegenprobe (**B**): Dasselbe Prädikat sperrt die Datenausgabe. `FUN_0800545c` und `FUN_0800549c` geben nur dann einen Zeiger auf die RFID-Daten zurück, wenn `053f2 == 0`. Daraus folgt:
- Ein Slot, für den 0x02 die 0x28-Byte-Rohdaten liefert (bzw. 0x03 einen %-Wert), hat in Byte 5 zwingend Bit = 0.
- Typ-7-Tags (Pfad `FUN_08015a42`, APDU mit SW 0x9000, Verbrauch/Prozent werden zurückgeschrieben, String `MFrfid_fm15l013_check`) bekommen ein Bit = 1, solange Feld +0x10 == 0. In diesem Fall meldet 0x02 „unknown“ und 0x03 0xFF.

**Live immer 0** heißt also: In keinem Slot steckte ein Typ-7-Tag mit diesem Zustand. Die Spule mit Tag liefert Daten über 0x02 und ist daher kein Typ 7 (bzw. nicht gesperrt). Gesichert ist: Bit≠0 nur bei Filament im Einlauf + geladenem Typ-7-Tag + Tag-Feld+0x10 == 0. Zweck „verschlüsselter Tag noch nicht autorisiert“ ist **V**; vermutlich hängt er mit 0x5c (Key/Auth über I2C 0x60) zusammen.

## 3. 0x15 GET_HARDWARE_STATUS – **B+L**

`FUN_0800f16e`: 16 Byte, `data[i] = ((i+1)<<4) | X[i]` für i=0..5, Rest 0. `X` = Init-Flags @0x2000390d (Namen aus `FUN_080050fe`). Oberes Nibble = Index 1..6, unteres Nibble = Fehler (0 = OK).

| Byte | Nibble | Komponente | Schreiber | Wert ≠0 bedeutet |
|---|---|---|---|---|
| 0 | 0x1_ | eeprom_init_flag | `FUN_0800b0d6` = Rückgabe `FUN_0800b088` | 1 = EEPROM fehlt, 2 = Schreib/Lese-Verify @0x7f0 fehlgeschlagen |
| 1 | 0x2_ | AHTxx_init_flag | @0x08003304 | 1 = AHT30 nicht gefunden oder 3× Lesefehler |
| 2 | 0x3_ | odometer_magnetic_init_flag | `FUN_0800b722` | 1 = MT6826-Timer-Create- oder Kommunikationsfehler |
| 3 | 0x4_ | Buffer_init_flag | `FUN_08011948` | 1 = Buffer-Zustand 3 (PD8 **und** PD9 aktiv = unplausibel) |
| 4/5 | 0x5_/0x6_ | rfid_init_flag[0/1] | `FUN_080145be` (Leser-Erkennung) / `FUN_0800bf74` | 1 = RFID-Leser 0/1 Fehler. Bei `bf74` wird das Flag schon beim ersten Fehlversuch gesetzt, auch wenn ein späterer Retry gelingt |

Live `10 20 30 40 50 60 00…`: alle sechs Komponenten OK.

## 4. Status 0x50 / 0x51 (/0x52 /0x53) – Korrektur – **B**

- **0x50** (`FUN_0800ed26` → `FUN_0800eb7a` → `FUN_0800e8e0`): nur in STATE_PRINT. Geprüft wird der aktuelle Druck-Slot `[0x20000240]`:
  - Formel: `(SensorB_frei) | (SensorA_frei<<1)`, jeweils 20-ms-Abtastung über `FUN_0800e890`/`e8b8`, bei 1 nochmals gelesen.
  - Ist ein Wert ≠0, liefert die FW Status 0x50 und setzt `R[8]` (0x2000390c) = 1. LED = Fehler 0x50.
  - Bedeutung: Filament-Ende/-Verlust im druckenden Slot, also Einlauf oder Hub leer (Deutung V, Mechanik B).
  - Der Zweig `== 0x53` in `ed26` ist **toter Code**: `eb7a` liefert nur 0 oder 0x50.
- **0x51 / 0x52 sind echte Fehler aus dem Thread „print_check“** (Eintrag 0x0801173b, Name @0x08003540; nicht als Funktion angelegt). Die frühere Aussage „0x51 nur LED-Tabelle“ ist falsch. Belege:
  - Buffer-IRQs (`FUN_080118f6`, „init buffer irq“):
    - Pin 0x39 = PD9 → ISR 0x080116da „[%d]buffer free!“: Hauptmotor an (`FUN_08011164(P)`), `[0x20000554]=1` (aktiv), `[0x20000555]=1` (Ereignis), `[0x20000556]++` (Zähler).
    - Pin 0x38 = PD8 → ISR 0x08011702 „buffer full!“: aktiv=0, Zähler=0, Motor aus (`FUN_08011198` = PWM 0).
    - Gesetzt wird das Ganze auch durch 0x04 mode 0 (@0x0800f080) und gelöscht durch mode 1 (@0x0800f0a0).
  - Thread-Schleife (100 ms):
    - Fenster `r5 = 100`; jedes neue free-Ereignis setzt die Odometer-Referenz neu und gibt +2.
    - Ab `r5 < 50` wird der Feeder 1 s zugeschaltet („printf feeder start/stop“).
    - Bei `r5 == 0` (≈10 s ohne buffer full) wertet die FW `diff = odom_ref − odom_jetzt` aus (@0x080117bc):
      - `diff < 5.0` mm (@0x08011a00) und `R[8]==0` → **0x52** („err 0x52!--1“, @0x08011848)
      - `diff ≥ 40.0` mm (@0x08011a18) **oder** Zähler ≥ 3 → **0x51** („err 0x51“, @0x0801186e)
      - dazwischen: kein Fehler
    - Danach: `FUN_08003e3a(code)` (STATE_ERROR, S[3]=code), Motor aus, Reset `FUN_0801171c`.
  - Deutung (V, passt zu key846 „box speed is smaller than extruder“): 0x51 heißt, die Box hat ≥40 mm nachgeschoben bzw. der Buffer ist ≥3× leergelaufen, ohne je „voll“ zu melden. Der Extruder zieht also mindestens so schnell, wie die Box liefert (Schlupf/Reibung im Pfad). 0x52 heißt, die Box soll fördern, das Odometer bewegt sich aber < 5 mm (Blockade), außer es lag schon ein Runout (0x50) vor.
- **0x53** wird nirgends erzeugt (nur LED-Mapping `FUN_08010c8a`). 0x50/0x51 in `FUN_0800bdd4/be3a` sind I2C-Adressen (RFID-Bitbang), keine Status.

## 5. Unbeantwortete `0x0d [00 02]`-Bursts – Empfang blockiert, keine Busy-Antwort, keine Duplikat-Erkennung

FW-Fakten (**B**):
- **RX-Thread** (Eintrag 0x0800fa63, Schleife @0x0800fa6c–0x0800fa80): `FUN_0800fa0c` → `FUN_0800f880(ctx, dev)` → `rt_thread_delay(1)`.
  - `f880` liest Byte für Byte nicht-blockierend (`rt_device_read(…,1)` @0x0800f892, Device offen mit Flag 0x600 = DMA-RX|INT-TX) und ruft bei gültiger CRC **synchron** `FUN_0800f27c` auf (@0x0800f9de).
- **Ein blockierender Handler hält den gesamten Empfang an.** Ein Beispiel ist 0x0d op 2/3 mit `rt_thread_delay(1000)`-Schleifen bzw. Pull-back.
  - Weitere Bytes landen nur im Treiberpuffer (Größe nicht ermittelt) und werden danach der Reihe nach abgearbeitet.
  - Es gibt keine Busy-Antwort, keine Queue mit Verwerfen und keine Sequenznummer: **identische Wiederholungen werden erneut ausgeführt.**
  - Ein Zwischenbyte-Timeout gibt es nur bei angefangenem Frame (0x78 = 120 ms, `f880`). Gepufferte Bytes setzen den Timer immer neu.
- Jeder vollständig empfangene 0x0d-Frame wird beantwortet; alle Pfade in `FUN_0800f0da` enden in `eaf2(0x0d,…)`. Ausnahme ist nur die Antwort-Unterdrückung `R[2]` in `eaf2`. Diese ist praktisch tot: R[2] wird nur gelöscht (@0x0800f9fe, 0x0800eb06, Init 0x080035d0) und nirgends gesetzt.
- `op 2, mask 0` führt keinen Force-Preload aus. Es bleibt: `FUN_08004d4a(4)` (Zustand IDLE/PRELOAD, sonst Status 3; kein Filament → 0x16 + ERROR), in IDLE `FUN_08004cd0(4,1)` = Pull-back **aller** Slots mit aktivem Hub-Sensor, in PRELOAD bis zu 179 s Warten.

Live-Abgleich:
- 13.10. 03:39:41 (Hub A belegt): Die Box war danach ≈11 s stumm (5× 0x04 ohne Antwort, `13:130637ff`). Erst 0x14 um 03:39:52,28 wird beantwortet, und zwar nach 330 ms statt ~5 ms. Danach meldet 0x0a fl=0x90 und IDLE. Damit ist **belegt, dass mindestens ein Burst-Frame ankam und ausgeführt wurde**.
  - Die 330 ms passen zum Abarbeiten des Rückstands; die 5 gepufferten 0x04 `[00 01]` ergeben IDLE + Fehler gelöscht über `FUN_08003e2c` @0x0800f0aa.
  - Die eigentliche 0x0d-Antwort (und die 5 verspäteten 0x04-Antworten) tauchen im Host-Log **nie** auf.
- 13.10. 02:16:22 (Hub leer): Die Box ist nicht busy (0x04 bei 02:16:23,15 nach 6 ms beantwortet). Trotzdem gibt es keine 0x0d-Antwort.

Bewertung:
- Die Box „verweigert“ den Burst **nicht** (B).
- Ursache ist der Host-Pfad (V, gestützt):
  1. Das Burst-Timeout liegt bei 2–3 ms und damit unter der Box-Latenz. Die Box druckt `forced_preloading!` über die Konsole und arbeitet `d4a`/`cd0` ab, was ≳3–6 ms dauert.
  2. Der Host sendet die Wiederholung, während die Box antwortet. Auf dem Halbduplex-Bus kollidiert das.
  3. Späte Antworten werden vom Host offenbar verworfen bzw. nicht geloggt (kein „got“, kein „Discard bytes“). Vermutlich leert der Host den Eingang vor der nächsten Transaktion.
- Gefahr (B): Kommen mehrere Burst-Frames intakt an, startet die Box den Pull-back mehrfach hintereinander.

## 6. Adressen 0xEA/0xEB – existieren, sind aber nur halbe Broadcasts – **B**

- Der RX-Zustand 1 (`f880`, case 1) nimmt `addr ∈ {eigene, 0xEA, 0xEB}` an. **0xFF und 0xFE werden in der App nur angenommen, wenn die eigene Adresse 0xFE ist** (Box ohne Adresse).
  - `FUN_0800a92c` (prüft 0xFF/0xFE/eigene) hat keine Referenz und ist toter Code.
  - 0xFF ist damit Bootloader-only (0x0b Loader→App an 0xFF wird von der App verworfen).
- 0xEA:
  - Bei normalen Kommandos (0x0a, 0x10 …) antwortet die Box trotzdem, mit **eigener** Adresse.
  - Unterdrückt werden nur: CRC-Fehler-Antwort (`f880` case 6), unbekannte fun (`LAB_0800f7f8`), Antwort auf 0x50.
- 0xEB:
  - 0x50 antwortet mit Absender 0xEB (Adresse wird temporär umgeschrieben und wiederhergestellt).
  - 0x51 schreibt nur bei 0xEB `G[3]` = 0x20000243. Das ist nur ein Debug-Schalter für Ausgaben beim RFID-Schreiben (Leser `FUN_0800f814` vor „rfid write used=…“). Keine Antwort.
- Fazit: 0xEA/0xEB sind von der FW vorgesehene Sammeladressen für Konfig #6 (0x50 schreiben + Reset) bzw. Debug (0x51). Als echte Broadcasts für Abfragen taugen sie nicht, weil bei mehreren Boxen alle antworten und kollidieren würden. Dass sie live nie vorkommen, passt dazu. Die Absicht ist **V**, die Mechanik **B**.

## 7. Auto-Addr (0xA0–0xA3) – Tabelle aus entpackter `.data` vollständig – **B**

Tabelle @0x200000c4, 4 × 0x34 Byte. Felder: +0 Name, +4 fun, +8 Handler, +0xc Datenpuffer, +0x10 Pufferlänge, +0x14 armed, +0x18 Sendewiederholungen, +0x1c Zufallsverzögerung an, +0x20/+0x24 Min/Max (ms).

| fun | Name | Handler | Puffer | Wdh. | Zufallsverzögerung | Verhalten |
|---|---|---|---|---|---|---|
| 0xA1 | get_uniid | 0x0800aac8 | 32 B | 1 | **ja, 0–1000 ms** | nur wenn eigene Adr = 0xFE und `data[0] ≤ 0xFE`, `data[1] ≥ 0xFE` (Host `[FE FE]`) → Antwort an Adr 0xFE |
| 0xA0 | set_addr | 0x0800ab34 | 32 B | **3** | nein | `data = [neue_addr, uid12]`; uid == GD32-UID (memcmp 12 @0x0800ab54) → eigene Adr = neue_addr, dann **3×** Antwort von der neuen Adr |
| 0xA2 | online_check | 0x0800aba0 | – | 1 | nein | immer Antwort |
| 0xA3 | get_addr_table | 0x0800aba0 | – | 1 | nein | identisch zu 0xA2 |

- Antwortdaten aller vier: `[0x01, 0x00, uid12]` = dev_type 1 (MB/CFS), mode 0 (APP) + 12 Byte UID von 0x1FFFF7E8 (`FUN_0800aa28`). Status-Byte 0, fun = Request-fun.
- Gesendet wird direkt über `aa44`/`aa70` (eigener Frame-Bau, nicht `eaf2`). A1/A2/A3 setzen zusätzlich `R[1]=1` (0x20003905; Host-Kontakt, V).
- Ablauf (`FUN_0800abf6`, nach jedem RX-Byte):
  - Ein passender Frame wird in den Puffer kopiert (`FUN_0800a9cc`, armed→0).
  - Ohne Verzögerung läuft der Handler sofort.
  - Bei A1 wird zuerst eine Zufallszeit gezogen (`rand` `FUN_08003274`, `srand(tick)`), danach wird gewartet. Das ist Kollisionsvermeidung für mehrere adresslose Boxen.
- Diese Frames gehen **nicht** in den Dispatcher (`FUN_0800a8fc`). 0xA0–0xA3 tauchen deshalb in der fun-Tabelle nicht auf.
- Abgleich mit `nachbau/auto_addr.py`:
  - Übereinstimmend: Broadcast 0xFE, A1-Daten `[FE FE]`, A0-Daten `[addr]+uid`, Antwortformat `[dev_type, mode]+uid`, A1 nur ohne Adresse, A2/A3 nur unter eigener Adresse, `VALID_DEV_TYPES` enthält 1, `MODE_APP=0`. Live-Frame `f7 01 11 00 a2 01 00 …` passt.
  - Abweichend 1: A0 wird **3×** beantwortet (das FakeSerial liefert 1×). Der Host muss Duplikate tolerieren.
  - Abweichend 2: Eine Box **mit** Adresse ignoriert A0/A1 an 0xFE schon im RX-Zustand 1. Das Busmodell „antwortet auf 0xA0 bei passender UUID“ gilt also nur für adresslose Boxen.
  - Abweichend 3: Die A1-Antwort kann bis 1000 ms nach der Anfrage kommen. Live hat der Host ein A1-Timeout von ≈1 s; Antworten mit großer Zufallszeit können knapp verloren gehen (V).
  - Die App verwendet keinen Loader-Modus (mode immer 0). mode=1 kommt nur vom Bootloader.

## 8. Kanal A: 0x0c, 0x19, Sensoren, Folgefehler

Pins aus der entpackten `.data` @0x20000218 (`FUN_0800e78c` = +0x00, `FUN_0800e79c` = +0x10). Die Portzuordnung setzt die übliche RT-Thread-GD32-Nummerierung pin = Port·16 + n voraus (**V**); die Indizes selbst sind **B**.

| Funktion | Pins Slot A/B/C/D |
|---|---|
| Sensor A = Einlauf (0 = Filament da) | 0x37/0x36/0x35/0x34 = PD7/PD6/PD5/PD4 |
| Sensor B = Hub (0 = Filament am Hub) | 0x4a/0x49/0x48/0x47 = PE10/PE9/PE8/PE7 |
| Buffer | 0x38 = PD8 („full“), 0x39 = PD9 („free“) |
| Odometer | MT6826, **eines für alle Slots** (`FUN_0800b606` ohne Slot-Index; 0x0a-Odometer = gemeinsamer Pfad hinter dem Hub) |

- **0x0c bei Extrude mode 4** (`FUN_08025c4e`, **B**):
  - Hauptmotor P[3], Takt 20 ms (`rt_thread_delay(0x14)`), max. 1250 Runden = 25 s (→ 0x0a).
  - Abbruch 0x0b, sobald `odom_start − odom > 3000.0` mm (@0x08025f24).
  - **0x0c**, sobald 25 Runden in Folge `odom_vorher − odom < 0.01` mm (@0x08025f28) gilt, d.h. 500 ms ohne Vorwärtsbewegung.
  - Rückwärtsbewegung (Rückfedern) zählt ebenfalls als „keine Bewegung“. Live: −240 mm, dann +4,4 mm Rückfederung, dann 0x0c.
  - Mode 4 prüft **keinen** Hub- oder Einlaufsensor, nur das gemeinsame Odometer.
- **0x05 bei mode 0** (Live st=05 nach 7,24 s): `feed_top_to_hub` `FUN_080042a8` wartet 700×10 ms = 7 s auf Hub-Sensor = 0 (10× in Folge), sonst 5 (**B+L**, präzisiert den Eintrag 0x05 „Feeder-Out“).
- **0x19 beim Retrude p=1** (`FUN_0800494e`, Pull-back-Stufe 1, **B+L**):
  - Feeder + Hauptmotor rückwärts, Takt 20 ms, max. 2000 Runden = 40 s (→ **0x1a**).
  - Ziel: Hub-Sensor 5× in Folge frei (`FUN_080043cc(slot,0,5)`).
  - Bei Odometer-Bewegung < 0.01 mm/20 ms (@0x08004a34) und gesetztem `R[3]` → **0x19** „pull_back_hub motor block“.
    - `R[3]` kommt aus dem Timer „motor_blocked_detect_timer“ (100 ms, `FUN_080038aa`). Bit 4 = Hauptmotor MS3791 blockiert, Bit 0–3 = Feeder A–D blockiert (`FUN_080102d2(i)`).
    - `R[3]` wird erst gesetzt, wenn der Block **> 10 s** anhält (@0x08003c60). `R[5]` bekommt jeden Block sofort.
    - Das erklärt die Live-0x19 nach 11,6/12,2 s.
  - Stillstand ≥150 Runden (3 s) bei gesetztem Hauptmotor-Block-Bit (`R[4]` Bit 4): bis zu 2 Retries mit Vorwärts-Ruck (Motor P[2], 50 ms).
  - Stillstand 3 s **ohne** Block-Bit gilt als „already pull back odometer“, also Erfolg.
  - Stufe 2 `FUN_08004884`: Hub weiter belegt → bis 7 s warten, sonst **0x14**.
  - Vor dem Pull-back prüft `FUN_08004c5a`, ob Sensor A Filament meldet; meldet er keins → **0x17**.
- **0x0a Byte 2 neu gedeutet (B):** `R[3]|0x80` (Block > 10 s) bzw. `R[5]` (Block-Maske), jeweils einmalig gemeldet.
  - Live 0x90 = Hauptmotor > 10 s blockiert, 0x91 = Hauptmotor + Feeder A.
  - Live 01/02/04/08 nach Preload/Retract = Feeder-Block des Slots. Vermutlich ist das der normale Endanschlag der Pretension (V).
  - Nebenwirkung bei Block > 10 s: Ereignis-Eintrag ins EEPROM (`FUN_0800af7e(0x80,…)`) und Motor-Stopp (`FUN_080035ea`).
- **Folgefehler bei mechanisch blockiertem Kanal mit dauerhaft aktivem Hub-Sensor A (B, Auswirkung V):**
  1. **0x0d op 2/3 in IDLE** zieht vor jedem Force-Preload alle Slots mit aktivem Hub-Sensor zurück (`cd0(4,1)`). A blockiert jeden Force-Preload, auch für B/C/D, mit ≥10 s Motorblock. Bei Fehler gilt: STATE_ERROR + Abbruch, kein Preload der anderen Slots.
  2. **0x11 mask 0** wählt alle Slots mit aktivem Hub-Sensor; A wird jedes Mal mit zurückgezogen.
  3. **0x10 mode 0 für Slot A** sieht den Hub sofort „erreicht“ (Sensor schon aktiv). Die 7-s-Prüfung greift dann nicht; Fehler kommen erst in der Odometer-Stufe (0x09/0x0c).
  4. **Im Druck (PRINT)** bewertet print_check nur Buffer und Odometer, nicht den Hub-Sensor. Ein schwergängiger Kanal A gibt **0x51** (≥40 mm ohne „buffer full“), ein blockierter **0x52**. Der Hub-Sensor erzeugt im Druck nur dann 0x50, wenn er **frei** wird.
  5. Hub-Sensoren sind pro Slot getrennt (PE7–PE10). Ein in A steckendes Filament verfälscht die Hub-Abfrage für B/C/D nicht. Es blockiert aber mechanisch den gemeinsamen Pfad und damit das gemeinsame Odometer; B/C/D enden dann in 0x09/0x0c/0x52 (V).

## Kurzliste Korrekturen gegenüber GHIDRA-CFS-BEFUND-2026-10-15

- 0x0a b0 = Temperatur °C (int8), b1 = rel. Feuchte % (uint8), AHT30 @I2C 0x38, ~1,2 s, abgeschnitten.
- 0x0a b5 = Typ-7-Tag-Sperrbit (nicht „RFID gültig“). Es schließt sich mit 0x02-Daten gegenseitig aus.
- 0x0a b2: R[3] = Motorblock > 10 s (Bit 4 Hauptmotor, Bit 0–3 Feeder), R[5] = Blockmaske.
- 0x15: Init-Flags eeprom/AHT/odometer/buffer/rfid0/rfid1, 0 = OK.
- 0x51/0x52 = print_check-Fehler (Buffer-Nachschub-Überwachung); 0x53 nie erzeugt; 0x50 = Runout im Druck-Slot.
- 0x05 auch = Hub nicht innerhalb 7 s erreicht (`FUN_080042a8`); 0x1a = Pull-back-Odometer-Timeout 40 s.
- Auto-Addr: Tabelle vollständig, A0 wird 3× beantwortet, A1 mit 0–1000 ms Zufallsverzögerung, A2 = A3.
- 0xFE/0xFF nimmt die App nur ohne eigene Adresse an; 0xEA antwortet bei normalen Kommandos trotzdem.
- RX läuft synchron im Dispatcher-Thread: ein blockierender Handler stoppt den Empfang, Wiederholungen werden erneut ausgeführt.
