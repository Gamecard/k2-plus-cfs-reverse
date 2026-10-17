# CFS-RESTE-BEFUND 17.10.2026 – statische Klärung der offenen CFS-App-Punkte (Ghidra)

> Eigene Ghidra-Neuanalyse 17.10.2026 (Projekt `arbeitsdateien-lokal/re-cfs-app-1017/`, nur dort;
> keine bestehenden Projekte geöffnet). Ziele: `fw/cfs/cfs0_050_G32-cfs0_000_153.bin` (Basis
> **0x08003000**, GD32F303VET6, RT-Thread 5.0.2) und `fw/cfs/cfs6_100_G31-cfs6_222_000.bin`
> (Basis **0x08004000**). Werkzeuge: `analyzeHeadless` (Ghidra 12.1.4) mit `SetCfsBase.java`/
> `CfsDispatch.java`, Hilfsskripte `scripts/{Q.java,q.sh,fn.sh,lit.py,decomp.py,DumpAsm.java,
> callctx.py,s3writers.py}`, Voll-Disassembly `out/asm_cfs0_153.txt`/`out/asm_cfs6_222.txt`,
> Voll-Dekompilat `out/all_cfs0.c`/`out/all_cfs6.c`, Dispatcher `out/disp_cfs0.c`/`out/disp_cfs6.c`,
> entpackte `.data` `out/data_init.bin`.
>
> Belegstärke: **B** = Dekompilat/Disassembly mit Adresse, **B+L** = zusätzlich live bestätigt
> (zitiert aus vorhandenen Logs), **V** = Deutung aus Code/Strings/Kontext, **ANNAHME** = unbelegt,
> **OFFEN** = statisch nicht klärbar (Suchumfang angegeben).
>
> Keine SN/UUID/Vendor-Daten. Ergänzt/korrigiert `GHIDRA-CFS-BEFUND-2026-10-15.md`,
> `GHIDRA-CFS-BEFUND2-2026-10-16.md`, `RE-RESTE-BEFUND-2026-10-16.md` §2b/§3/§4. Nachbau-Hinweise
> sind **nur Empfehlungen**, es wurde **kein** Code geändert.

Takt: 1 RT-Tick = 1 ms (`rt_tick_from_millisecond` @0x0801d536, BEFUND2). Adressen ohne Zusatz =
cfs0_000_153 (Basis 0x08003000); cfs6-Adressen sind als solche markiert (Basis 0x08004000).

---

## 1. Config #6 (0x0c lesen / 0x50 schreiben + Reset) = persistenter Geräte-/Arbeitsmodus – **B**

**Antwort:** Config #6 ist der **gespeicherte Arbeitsmodus der Box** (1 Byte, Flash-DB). `0` =
STANDARD (Normalbetrieb mit Auto-Preload), ≠0 = Werks-/Alterungs-/Testmodus. Der Wert wird beim
Boot gelesen und bestimmt, welcher Betriebs-Thread läuft; `0x50` schreibt ihn persistent und löst
einen Reset aus, damit der neue Modus beim nächsten Start greift.

**Belege:**
- Leser beim Boot: Haupt-Init-Thread `FUN_080032e8` (`out/all_cfs0.c:247`): `FUN_08011d82(6)` →
  `"current mode = %d\n"` → `FUN_080096ca(mode)` (Modus-Dispatcher) und danach `FUN_0800536e()`
  (Auto-Preload enable). (B)
- Lesewert = Datenbank-Key 6: `FUN_08011d82(key)` = `database_get_value` (@0x08011d82), Key-Index
  über `FUN_08011e48` (gültige Keys 5..0xC7). Schreiben: `FUN_08011db4(6,&wert,1)` =
  `database_set_value`, Persistieren: `FUN_08011df0` (schreibt FAL-Page). (B)
- 0x0c-Handler `FUN_0800f124`: liest `FUN_08011d82(6)`, 1 Byte, synchronisiert Box-Struct+7
  (`[0x200037d0+7]` = aktueller Modus, dieselbe Variable, die `FUN_080096ca` @0x080096d0 setzt),
  antwortet `0x0c` status 0 + 1 Byte (`out/all_cfs0.c:11353`). (B+L – live `[0]`, immer Modus 0)
- 0x50-Handler (`out/disp_cfs0.c:378–402`): `FUN_08011d82(6)` lesen; **gleicher Wert ⇒ status 3,
  kein Schreiben/Reset**; sonst `FUN_08011db4(6,…,1)` + `FUN_08011df0` + Antwort + `LAB_0800f6f8`:
  `rt_thread_delay(100)` + `FUN_08016388`. Adress-Sonderfall: 0xEA keine Antwort, 0xEB Absender 0xEB
  (BEFUND2 §6). (B)
- **Reset bestätigt (war V → jetzt B):** `FUN_08016388` schreibt `0x05FA0004` nach `0xE000ED0C`
  (`SCB->AIRCR`, VECTKEY 0x05FA + Bit 2 SYSRESETREQ) → Software-Reset. Literale `0x080164bc→
  0xE000ED29` (−0x1D = 0xED0C), `0x08016674→0x05FA0004`. (B)
- Modus-Dispatcher `FUN_080096ca` (@0x080096ca, TBB @0x080096ec, 0x27 Fälle): **0 = STANDARD**
  (`"switch standard mode!"`), 1 = `FUN_08012bd8` (box_aging), 2 = `FUN_08009ec4`, 6 =
  `RFID_authentication`-Thread, 7/0xb/0xc/0xd/0xe = Feed-/Buffer-/Print-Aging, 0x16–0x1b/0x1e/0x1f =
  Motor-/Sensor-Tests, 0x20 = `host_hum/slave_hum`-Sensortest, 0x24 = `FUN_08012c42(99,0)`, 0x26 =
  `main_pcb_test_mode`; `>0x26` ⇒ `"illegal mode!"`. (B)
- Default: `FUN_08011eba` (DB-Checksummenfehler) setzt Key5 = `0x10503` (= FW/DB-Version „1.5.3“,
  V) und **Key6 = 0** (Modus STANDARD). (B)
- Selbst-Rückstellung: `FUN_0800a026` schreibt nach ≥0x3B Alterungszyklen `"switch to
  STANDARD_MODE"`, Key6 = 0, `delay(10)`, `FUN_08016388` (Reset). `FUN_08012b16` schreibt bei
  Buffer-Dauerfehler (`>2999`) Key6 = **2** + Reset (Alterungsmodus übersteht den Reset). (B)
- Flash-DB-Partition: Deskriptor `@0x08029d2c` nennt **`0x0807F800`** (letzte 2-KiB-Flash-Page),
  Gerätename `"gd32_flash"` (`@0x0802c598`). (B)

**Sicherheit:** Mechanik und Bedeutung **B**. Welche konkrete „Option“ der Modus außer STANDARD/
Test bei Endkunden je gesetzt ist, ist unbekannt – Default/Normalbetrieb ist **0**, live immer `[0]`.

**Folge für Nachbau (`box.py`):** `0x0c` wird bereits nur gelesen – Interpretation ergänzen:
`config6 = 0` = Normalbetrieb, ≠0 = Werks-/Testmodus. **`0x50` niemals senden** (persistenter
Moduswechsel in den Flash + Reset; ein versehentlicher ≠0-Wert bringt die Box beim nächsten Boot in
einen Test-/Alterungsmodus statt Normalbetrieb). Falls je eine Diagnose „Box steckt im Testmodus“
nötig wird: `0x0c`-Byte prüfen (≠0) – zurücksetzen nur über offiziellen Weg/`0x50 [00]` mit Freigabe.

---

## 2. cfs6-Handler 0x16–0x19 / 0x57 (Trockner) – differenziert, nur 0x18/0x19 sind Trockner – **B**

**Antwort:** Die vier Codes sind **nicht alle** Trockner. Belegt:
- **0x16 = LCD/Display + Backlight an/aus** (kein Trockner).
- **0x17 = Odometer-(Messrad-)Zählrichtung setzen** (kein Trockner).
- **0x18 = Trockner/Heizung STARTEN** (der eigentliche Dry-/Heat-Befehl).
- **0x19 = Trockner-STATUS abfragen** (Antwort 6 Byte).
- **0x57 = Werks-/Alterungs-Parameter setzen** (Kanal + Zyklenzahl, kein Trockner).

Alle Antworten über `FUN_0800e8d0(fun,status,data,n)` (Antwort-Builder cfs6), Längenprüfung
`FUN_0800e0ec(fun,len,soll)` (status 1 bei zu kurz). cfs6 hat **2 Kammern** (Index 0/1).

**Belege (cfs6, Basis 0x08004000):**

| fun | len/ndata | Handler | Wirkung | Beleg |
|---|---|---|---|---|
| 0x16 | 4 / 1 | `FUN_0800daa0` → `FUN_080142d4(b0)` | `b0==0` → Display aus (DCS `0x28`) + Backlight-PWM 0; `b0≠0` → Display an (`0x29`) + Backlight 100 %. LCD-SPI (`FUN_08015488`/`FUN_08015128`), identisch zum finsh `lcd dispon/dispoff`. Antwort 0x16 st 0, 0 Byte | B |
| 0x17 | 5 / 2 | `FUN_0800e62c` → je Bit in `b0`: `FUN_080089a8(ch, b1==0)` | setzt Odometer-Feld `+5` (Zählrichtung; ISR `FUN_0801678c`: `+5==1` → `+Δ`, sonst `−Δ`). `b1==0` → Richtung 1. `b0` = Maske (unterste 4 Bit, `ch` im Handler durchlaufen). Antwort 0x17 st 0, 0 Byte | B |
| 0x18 | 9 / 6 | `FUN_0800e5cc` → `FUN_08006328`=`api_heat_msg_send` → `heat_mq` → `FUN_080063d8`=`api_heat_set` | **Trockner/Heizung starten.** Daten: `b0`=Kammer-Maske (Bit0=Kammer0, Bit1=Kammer1), `b1`=Modus, `b2`=Zieltemp °C (begrenzt 0x1E..0x55 = 30..85), `b3`=Lüfter-/Minuten-Param (begrenzt 20..60), `b4b5`=u16 **BE** Trockenzeit in Minuten (≤0x167F=5759). Modus: **0=aus, 1=Heizen, 2=Trocknen (mit Zeit), 3=Trocknen-Halten/Rollback**. Antwort 0x18 st 0, 0 Byte | B |
| 0x19 | 4 / 1 | `FUN_0800de38` → je Bit in `b0`: `FUN_08006130(ch, &buf)` | **Trockner-Status abfragen.** Antwort 0x19 st 0, **6 Byte**: `[b0=Maske-Echo, state, temp, ?, zeit_u16]` (`state` aus `DAT_200033A8`, `temp` aus Kammer-Messwert) | B |
| 0x57 | 8 / 5 | `FUN_0800e7e8` → `FUN_0800c5dc(data)` | **Werks-/Alterungsparameter.** `b0`=Modus: `1` → Kanalwahl `u32∈{1..4, 0xFF}` (`DAT_20006DA8`), `2` → Zyklenzahl `u32∈{1..99999}` (`DAT_20006DAC`); `b1..b4`=u32 **BE**. Setzt `DAT_20004781=1` (Trigger). Gelesen von den Aging-Schleifen (`FUN_080097e8` Feeder-Aging nutzt DA8=Kanal/DAC=Zyklen; `FUN_0800b1fc` Preload-Aging nutzt DAC=„times“). Antwort 0x57 st 0, 0 Byte | B |

- Trockner-Regelthread: `heat_thread_entry` `FUN_08012956`, Zustandsmaschine pro Kammer
  `FUN_08012078` (PID, Fehler „ptc fault / temp rise abnormal / fan fault / sensor fault /
  outlet temp too high“, Strings `[D/HT]`/`[W/HT]`/`[I/HT]`), `api_heat_set` `FUN_080063d8`. (B)
- Kammer-Heizdaten-Tabelle @`0x200033A8` (0x28/Eintrag): `+0`=Modus/State, `+1`=Soll-Flag,
  Temp-/Zeitfelder; Lüfterschwelle `<0x3C` (60 s). (B)

**Sicherheit:** Handler-Zuordnung und Parameter-Layout **B**. Die genaue Semantik von `b3` in 0x18
(als „Minuten“ an `api_heat_set` übergeben, dort nur für Lüfter-Timing 0x2000339C = b3×10 genutzt)
ist **V**. Kein cfs6-Gerät am Bus ⇒ keine Live-Bestätigung (**OFFEN** nur die Live-Prüfung).

**Folge für Nachbau:** Betrifft nur eine cfs6-Trockner-Box (nicht die vorliegende cfs0-CFS). Für
einen späteren `drybox.py`: 0x18 = `[kammer_maske, modus(0/1/2/3), temp°C, b3(20-60), minuten_u16_BE]`,
0x19 = `[kammer_maske]` → 6-Byte-Status. 0x16/0x17/0x57 sind **kein** Trockner (Display bzw. Odometer
bzw. Werkstest). Vorher am echten Gerät mitschneiden, nicht blind senden (Heizung!).

---

## 3. Fehlercodes 0x20 und 0x22 – kein erreichbarer Erzeuger in der App – **B**

**Antwort:** Weder 0x20 noch 0x22 werden in cfs0_000_153 auf einem erreichbaren Pfad als
Box-/Slot-Fehlercode gesetzt. Beide existieren ausschließlich im LED-Mapping `FUN_08010c8a`
(@0x08010c8a) und sind dort **tote Einträge** ohne Quelle:
- **0x22** wird nur von `FUN_08015898` zurückgegeben (RFID-Tag-Lebenszähler-Prüfung: APDU-Dekrement
  `FUN_0801522c(4,…)` liefert SW ≠ 0x9000). **`FUN_08015898` hat 0 XRefs/0 Caller** (Ghidra-XRef und
  Literal-Pool-Scan leer) → **toter Code**; ebenso die Schwesterfunktion `FUN_0801597a`
  (`get_tag_life_count`). (B)
- **0x20** kommt im ganzen Image nur als GPIO-Pin-Nummer (`FUN_0800a164`/`0800a2d6`: Pin 0x20–0x23),
  BASEPRI-Maske (`movs r0,#0x20; msr basepri`), Puffergröße und als Rückgabe RT-Thread-/libc-interner
  Funktionen (`FUN_080258c2`, `FUN_08026610`) vor – **nie** als Box-Status/Fehler. (B)
- Gegenbeleg über die Fehler-Senke: Der einzige Schreiber von `S[3]` (gespeicherter Fehlercode,
  `0x200037D3`) ist `FUN_08003e3a` (@0x08003e48), gespeist aus den Prozess-Rückgaben
  (`out/all_cfs0.c`: 0x05/0x06/0x08/0x09/0x0a…0x19), sowie `FUN_08003e2c` (löscht) und `FUN_08004d4a`
  (schreibt 0x16). **Keiner** dieser Pfade liefert je 0x20 oder 0x22 (Disassembly-Scan aller
  `bl 0x08003e3a`-Aufrufstellen via `callctx.py`: Argumente sind 1/3/4/5/0x0b/0x0d/0x16/0x51/0x52,
  nie 0x20/0x22). (B)

**Sicherheit:** **B** (geschlossen: kein lebender Erzeuger). Deutung der LED-Einträge (0x20 →
LED-Code wie 0x14-Sensorpfad, 0x22 → LED-Code 6) ist **V**; sie sind Reste aus einer älteren/
anderen FW-Variante.

**Folge für Nachbau:** 0x20/0x22 brauchen **keinen** eigenen Status-Namen; in `box.STATUS_NAMES`
höchstens als „reserviert/unbenutzt (nur LED-Tabelle)“ kommentieren. Live sind sie bei R153 nicht zu
erwarten (RE-RESTE §2b: einziger Beleg bisher LED-Map – bestätigt). Falls eine neuere Box-FW sie je
sendet, Quelle erneut prüfen (dann vmtl. über RFID-Tag-Life-Pfad reaktiviert).

---

## 4. Zweck des Typ-7-Tag-Sperrbits (0x0a Byte 5) und 0x5c-Bezug – Prädikat B, Zweck V

**Antwort:** Byte 5 Bit i = „Slot i hat Filament am Einlauf **und** einen geladenen **Typ-7-Tag,
dessen On-Tag-Verbrauchsfeld noch 0 ist**“. Dasselbe Prädikat **sperrt** zugleich die RFID-Daten-
ausgabe über 0x02/0x03. Zweck (V): Ein Typ-7-Tag ist ein **gesicherter FM15L013-Tag mit On-Tag-
Lebens-/Verbrauchszähler**; solange dessen Verbrauchsfeld noch nicht beschrieben ist (Feld +0x10 ==
0), gibt die FW die Spulendaten nicht frei und markiert das Bit. `0x5c` lädt/liest den zur
Authentifizierung des Typ-7-Tags nötigen Schlüssel – kausal getrennt vom Bit, aber derselbe
Typ-7-Spulen-Mechanismus.

**Belege:**
- Prädikat `FUN_080053f2(i)` (@0x080053f2, Disassembly): `FUN_080053c6` = Einlauf-Präsenzflag
  `[0x2000391E+i]`; ist es ≠0, Tail-Call `FUN_0800dba0(i)` mit `rec = 0x20004590 + i·0x64`
  (RFID-Manager-Datensatz). `FUN_0800dba0` (@0x0800dba0, Disassembly): Ergebnis 1 nur wenn
  `rec[0x17]≠0` (Tag-Daten geladen) **und** `rec[0x63]==7` (Tag-Typ, Log „NUM“) **und**
  `u32 rec[0x28]==0` (Tag-Feld +0x10). (B)
- Datenausgabe-Sperre durch dasselbe Prädikat: `FUN_0800545c` (0x02-Datenzeiger) und `FUN_0800549c`
  (0x03-%-Zeiger) geben nur dann einen gültigen Zeiger zurück, wenn `FUN_080053f2(i)==0`
  (`out/all_cfs0.c:9480…`/`9500…`). ⇒ Bit≠0 und 0x02/0x03-Daten schließen sich gegenseitig aus. (B)
- Tag-Typ: `FUN_08014a5e` (Reader, @0x08014a5e) schreibt `rec[0x4b]`/Typ-Feld: **Typ 4** = Pfad mit
  reiner 8-Byte-UID (`pcVar=='\x04'`), **Typ 7** = erweiterter/gesicherter Pfad (APDU, mehr Felder).
  (B)
- On-Tag-Verbrauch: Der Typ-7-Zweig `FUN_08015a42` („write used/unused sw“, „get/dec life sw“)
  schreibt Verbrauch/Prozent per APDU 0x11/0x12 in den Tag; Dekrement `FUN_0801522c`. `rec[0x28]`
  (= Feld +0x10) ist der verbuchte Used-/Life-Wert. (B)
- 0x5c-Bezug: Handler `FUN_0800f1e8` → `FUN_0800d7e4` schreibt 16 Byte an **I²C-Gerät 0x60**
  (`FUN_0800af7e(0x60,…)`) und `FUN_0800d7f8` liest den Schlüssel zurück („rfid key read ok! 0x%02x
  0x%02x“); derselbe Schlüssel wird vom Reader-Auth (`FUN_080149a2` Mutual-Auth vor `FUN_08015532`-
  Lesung) genutzt. Das Byte-5-Prädikat ruft 0x5c **nicht** direkt auf – gemeinsamer Zweck (Typ-7-
  Spulen), aber keine direkte Kausalität im Code (kein gemeinsamer Caller). (B für Mechanik, V für
  „Autorisierung“-Deutung)

**Sicherheit:** Prädikat/Datensperre/0x5c-Mechanik **B**; „gesicherter Tag noch nicht freigegeben“
als Zweck **V**. Live immer 0 (BEFUND2 §2) = kein solcher Typ-7-Tag steckte je.

**Folge für Nachbau:** `inlet_tag_mask` (Byte 5) weiter als „Typ-7-Tag ohne Datenfreigabe“ führen
und dokumentieren, dass bei gesetztem Bit `get_rfid` (0x02) „unknown“ und `get_remain_len` (0x03)
0xFF liefern – also kein Lesefehler, sondern gesperrter Tag. **0x5c niemals blind senden**
(Schlüssel-Schreibpfad ins Secure-Element, Tag-/Reader-Risiko).

---

## 5. RX-Puffer des Box-Treibers = 1024 Byte; App hat keine echte Längen-Obergrenze (nur len ≥ 3) – **B**

**Antwort:**
- **Treiber-RX-Ringpuffer = 1024 Byte** (RT-Thread-Serial-DMA, `bufsz`-Feld der `serial_configure`).
- Die **App** prüft in der Empfangs-Statemachine nur `len > 2` (also len ≥ 3); `len` ist 1 Byte
  (max 255) und hat **keine Obergrenze**. Der Frame-Kontext ist nur **0x70 = 112 Byte** groß, Daten
  liegen ab Offset 6, die CRC fest auf Offset 0x6A (106). Damit passen **≤ 100 Datenbytes** sauber
  (len ≤ 103); größere `len` überschreiben erst CRC/Timeout-Felder und (len ≳ 110) laufen über den
  Puffer hinaus. Die `0xFC`-Grenze ist **Bootloader-spezifisch**, in der App nicht vorhanden
  (deckt RE-RESTE §1).

**Belege:**
- RX-Ringpuffer: USART-Init `FUN_0801bafa` setzt `serial_configure` an `dev+0x48` aus der Konstante
  für „uart1“ = `0x00100008`. Dekodiert (RT-Thread-Bitfeld): data_bits 8, stop 1, parity none,
  **`bufsz = (0x00100008 >> 10) & 0xFFFF = 0x400 = 1024`**; baud `0x38400 = 230400`. Device geöffnet
  mit Flag **0x600** (DMA-RX | INT-TX) in `FUN_0800ea1a` (@0x0800ea1a, `FUN_080168a6(dev,0x600)`).
  Der RX-FIFO-Wrap nutzt dieses `bufsz` (`FUN_0801a7a8`: `(cfg>>10)&…`). (B)
- App-Statemachine `FUN_0800f880` (@0x0800f880): Zustand 2 nimmt `len` nur bei `2 < len` an, speichert
  `frame[3]=len`, `remaining=len-3`; Datenbytes landen in Zustand 5 bei `frame[(len-remaining)+3]`
  (erstes Byte Offset 6), CRC in Zustand 6 bei festem `frame[0x6a]`. Kein weiterer Längenvergleich,
  keine `0xFC`-Konstante. (B)
- Kontextgröße: `FUN_0800f81a` nullt `0x70` Byte. Layout: `[0]`State `[1]`remaining `[2]`addr
  `[3]`len `[4]`status `[5]`fun `[6..]`data `[0x6a]`crc `[0x6c..0x6f]`Timeout-Timestamp. ⇒ sauberer
  Datenbereich 6..0x69 = 100 Byte. (B)
- CRC über `len..data` (`FUN_0800eaa4`→`FUN_0800e9ec`, BEFUND §1, hier bestätigt). Längster
  definierter Befehl: 0x5c mit len 0x13 (19) bzw. 0x12 mit len 8 – weit unter der Grenze. (B)

**Sicherheit:** **B**. (Hinweis: der fehlende Obergrenzen-Check ist ein potenzieller Pufferüberlauf
bei len ≳ 110 – praktisch nie, da kein definierter Befehl so lang ist und der Host nie so lange
Frames sendet.)

**Folge für Nachbau (`serial485.py`/`box.py`):** Die Host-seitige Prüfung „len bis 255“ ist
unkritisch; die reale Box akzeptiert len 3..255, verarbeitet aber nur die definierten kurzen
Frames korrekt. Empfehlung: ausgehende Frames **auf die bekannten Längen begrenzen** (max len 0x13
für CFS-App-Befehle), keine überlangen/zusammengesetzten Frames senden (Überlauf-Risiko in der Box).
Der 1024-Byte-Treiberpuffer bedeutet: während ein blockierender Handler läuft (BEFUND2 §5), gehen
nachrückende Bytes bis 1 KiB nicht verloren – aber **keine** Duplikaterkennung, daher weiter
`retries=0` bei Mechanik-Kommandos (so umgesetzt).

---

## 6. 0x0d op 3 (live nie gesehen) = Forced-Preload OHNE RFID – **B**

**Antwort:** `0x0d` mit op = 3 löst ein **erzwungenes Vorladen (Pretension) ohne RFID-Lesung** aus –
identisch zu op 2 (Forced-Preload mit RFID), nur bleibt das Preload-RFID-Flag `PRE[1]=0`, sodass die
Preload-Statemachine nur die Pretension fährt und ohne RFID-Lesung/Margin-Schätzung in den Leerlauf
zurückgeht.

**Belege:**
- `FUN_0800f0da` (@0x0800f0da): `op==2` → `FUN_08004ec4(mask,1)`, **`op==3` → `FUN_08004ec4(mask,0)`**,
  op 0/1 = global disable/enable, sonst status 1. (B)
- `FUN_08004ec4(mask, flag)` (@0x08004ec4): `"forced_preloading!"`, `FUN_08004d4a(4)` (Zustand muss
  IDLE/PRELOAD sein, sonst 3; kein Filament → 0x16+ERROR); in IDLE vorab Pull-back aller Hub-Slots
  `FUN_08004cd0(4,1)`, in PRELOAD bis 179 s warten; dann je gesetztem Masken-Bit mit Filament (Sensor
  `FUN_0800e890`) `FUN_08004e80(slot, flag)`. (B)
- `FUN_08004e80(slot, flag)` → `FUN_0800533c(slot, flag)`: setzt das Preload-RFID-Flag `PRE[1]`
  (`[0x2000572c+1]`, dieselbe Variable wie `FUN_0800536e`=enable/`FUN_08005376`=disable) auf `flag`.
  op 2 ⇒ `PRE[1]=1` (Statemachine `FUN_080064b8` läuft pretension → CHECK_HUB → „read rfid + margin
  estimation“), **op 3 ⇒ `PRE[1]=0`** (nur pretension, dann zurück in den Leerlauf, keine
  RFID-Lesung). Danach 5 s warten + bis 180 s pollen, Antwort erst am Ende. (B)
- Status/Antwort wie op 2: 0 OK, 1 op>3, 3 Box nicht IDLE/PRELOAD, 0x16 kein Slot mit Filament,
  sonst Fehler des vorgeschalteten Pull-backs; immer `FUN_0800eaf2(0x0d,status,–,0)`. (B)

**Sicherheit:** **B** (statisch vollständig). Live nie gesendet (**OFFEN** nur die Live-Prüfung –
Suchumfang: `klippy.log` 05.–16.10., kein `0x0d [m 03]`).

**Folge für Nachbau (`box.py`):** op 3 als „Forced-Preload ohne RFID“ dokumentieren; Laufzeit/Timeout
wie op 2 (bis ~3 min pro Slot, `retries=0`, langer Timeout). Nutzen: Spule ohne (lesbaren) Tag laden.
Verhalten sonst identisch zu op 2 (gleiche Pull-back-/ERROR-Fallen bei blockiertem Hub, BEFUND2 §8).

---

## 7. MT6826S-Encoder speist 0x0e MEASURING_WHEEL / das gemeinsame Odometer, NICHT die BLDC-Regelung – B/B+L

**Antwort:** Der MT6826S (SPI PB12–15) ist der **Weg-/Messrad-Geber**: sein Wert fließt in den
0x0e-MEASURING_WHEEL-Wert und in das **eine gemeinsame Odometer** hinter dem Hub, das alle Feed-/
Retract-Prozesse für Weg-/Stillstands-/>3000-mm-Prüfungen nutzen. Die BLDC-Regelung (MS3791/MS8828)
läuft **gesteuert (Open-Loop-PWM)**; ihre einzige Rückführung ist ein **separater FG-Tacho (PE11)**,
der nur zur Blockier-/Überlast-Erkennung dient. Der MT6826 speist die BLDC-Regelung also **nicht**.

**Belege:**
- SPI-Lesung: `FUN_0800b618` → `FUN_0800cdbe(spi)` liest signed int (Rohcount) → float → Odometer-
  Objekt `0x2000019b + 0x11`. Timer-Callback `FUN_0800b63e` (@0x0800b63e, Periode 20 ms:
  `FUN_0801d536(0x14)`), Init `FUN_0800b722` („mt6826_timer“, SPI). (B)
- mm-Wert: `FUN_0800b606` = `count × 7.886e-4` mm (`DAT_0800b8b8 = 0x3A4EBCD3`). 0x0e-Handler
  `FUN_0800efc6`: op 0 → `FUN_0800b794` (Position nullen), op 1 → `FUN_0800b606` → 4-Byte float **BE**.
  (B)
- Verbraucher des Odometers (= MT6826-Wert): `FUN_0800b606` wird von allen Prozess-Funktionen
  gerufen – `FUN_08003e74`/`08003efa`/`08004190`/`0800472c`/`0800494e`/`08025c4e`/`08025dea` (Weg/
  >3000 mm/Stillstand) sowie `FUN_0800da14`/`0800dc94` (RFID-Used-%-Schätzung). (B)
- **BLDC = Open-Loop:** PWM-Set `FUN_08011164` = Richtung + Duty (= Speed-Parameter), **keine**
  Positions-/Geschwindigkeits-Rückführung aus dem Odometer. Rückführung ist der FG-Tacho:
  `MS3791_is_blocked` `FUN_080111aa` (Flag aus FG-Pulszähler) und `motor_blocked_detect_timer`
  `FUN_080038aa` (@0x080038aa, nur Block/Überlast, Strings „MS3791 is overload“, „reverse detooth
  timeout“). Das Odometer-Objekt `0x2000019b` wird **ausschließlich** von `FUN_0800b144/b606/b618/
  b63e/b722/b794/b7aa` referenziert (Odometer-Modul), **nie** vom BLDC-/PWM-/Block-Pfad. (B)
- **Live (B+L):** Normale Einzüge aller Slots fördern ~1300 mm @ ~155 mm/s, vom 0x0e-Odometer
  verfolgt; bei mechanisch blockiertem Kanal A **läuft der Motor, das Odometer steht** → Abbruch
  0x0c genau an der Blockier-mm-Marke (`arbeitsdateien-lokal/livelog-cfs/BEFUND-LIVE.md:116–120`;
  PROTOKOLL-485 §6.5, CHECKLISTE „Kanal-A-Diagnose“). Stünde der Geber auf der BLDC-Welle, würde er
  bei blockiertem Filament/weiterdrehendem Motor weiterzählen. Dass er stehen bleibt, belegt: der
  Geber folgt der **tatsächlichen Filamentbewegung** (Messrad), nicht der Motorwelle. (B+L)

**Sicherheit:** Datenfluss (MT6826 → 0x0e/Odometer, nicht BLDC) **B**; Verhalten „Messrad“ durch den
Live-Block-Fall **B+L**. Der **physische Anbau** (eigenes Messrad vs. Förderrolle hinter dem Hub) ist
aus dem Binär nicht ableitbar – bleibt **ANNAHME**; funktional ist es ein Filament-Weggeber, kein
BLDC-Wellen-Encoder.

**Folge für Nachbau (`box.py`):** 0x0e = Filament-Weg (Messrad), float32 **BE** mm, op 0 = Nullen
(weiterhin nur mit `reset=True`). Für eine Weg-Simulation/Emulation (`k2_box.py`): der Odometer-Wert
ist vom Motor-Zustand **entkoppelt** – bei blockiertem Pfad stagniert er (das triggert 0x09/0x0c/
0x52). Die BLDC-Drehzahl/-Blockade separat (FG) modellieren, nicht aus dem Odometer ableiten.

---

## Kurzliste Korrekturen/Ergänzungen gegenüber den Vorbefunden

- **RE-RESTE §2a (Config #6):** Bedeutung geklärt – persistenter **Arbeitsmodus** (0 = STANDARD),
  Boot-Leser `FUN_080032e8`→`FUN_080096ca`, Reset `FUN_08016388` = SCB-SYSRESETREQ (war V → B),
  Default 0, DB-Page `0x0807F800`.
- **RE-RESTE §2b (0x20/0x22):** geschlossen – **kein lebender Erzeuger**; 0x22 nur in totem
  `FUN_08015898` (RFID-Life-Dec), 0x20 nur GPIO/BASEPRI/libc. Reine LED-Tabellen-Reste.
- **RE-RESTE §3 (cfs6 0x16–0x19/0x57):** differenziert – **0x16 = LCD-Display**, **0x17 = Odometer-
  Richtung**, **0x18 = Trockner/Heizung START** (6-Byte-Layout belegt), **0x19 = Trockner-Status**,
  **0x57 = Werks-Aging-Parameter**. Nur 0x18/0x19 sind Trockner.
- **GHIDRA-CFS-BEFUND §9 / Offen:** cfs6-Trockner-Handler erledigt; Config #6 erledigt; 0x20/0x22
  erledigt.
- **BEFUND2 §2 (Typ-7-Sperrbit):** Zweck präzisiert – gesicherter FM15L013-Typ-7-Tag mit noch nicht
  beschriebenem On-Tag-Verbrauchsfeld; 0x5c = Schlüssel-Lade-/Lesepfad (I²C 0x60) für dessen Auth,
  kausal getrennt.
- **MT6826S / RE-RESTE §4:** Messrad-Tendenz jetzt belegt (Datenfluss B, Live-Block B+L); BLDC
  Open-Loop + separater FG-Tacho.

## Arbeitsdateien (lokal, nicht fürs Repo)

`arbeitsdateien-lokal/re-cfs-app-1017/`:
- `proj/` – eigenes Ghidra-Projekt (cfs0_153, cfs6_222), frisch importiert, nur hier gesperrt.
- `scripts/` – `Q.java`, `q.sh`, `fn.sh`, `lit.py`, `decomp.py`, `DumpAsm.java`, `callctx.py`,
  `s3writers.py`.
- `out/` – `all_cfs0.c`/`all_cfs6.c` (Voll-Dekompilat), `disp_cfs0.c`/`disp_cfs6.c` (Dispatcher),
  `asm_cfs0_153.txt`/`asm_cfs6_222.txt` (Voll-Disassembly), `data_init.bin` (entpackte `.data`),
  `q_*.txt` (Einzelabfragen).
