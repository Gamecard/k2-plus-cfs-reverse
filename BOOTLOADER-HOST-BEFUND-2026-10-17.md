# BOOTLOADER-HOST-BEFUND – CFS-Bootloader `cfs0_050_G32` und Host-`.so` (statisch, 17.10.2026)

> Nur statische Analyse. Kein Zugriff auf Drucker, Pi oder Hardware. Code in `nachbau/` und `pi-host/` ist **nicht** geändert.
> Arbeitsdateien: `arbeitsdateien-lokal/re-bl-host-1017/` (lokal, nicht fürs Repo; Inhalt siehe §7).
> Keine SN, UID oder Vendor-Daten im Text. Live-Zeilen werden nur mit Datei:Zeile zitiert.

**Belegstärken**
- **B** = im Disassembly oder Dekompilat mit Adresse belegt
- **B+L** = zusätzlich durch eine Live-Logzeile gestützt
- **V** = Folgerung oder Deutung
- **ANNAHME** = nicht belegt

**Adressen**
- Bootloader: Flash-Adresse ab `0x08000000` (Datei-Offset = Adresse − `0x08000000`).
- Host-`.so`: ELF-vaddr. Gleich in `llvm-objdump` und im eigenen Ghidra-Projekt `host2` (Imagebase 0).

**Log-Kürzel**
- `05:`/`12:`/`13:` = `live-logs-2026-10-15/klippy.log.2026-10-<TT>`
- Log-Uhr = UTC+8

## Kurzfassung

| Nr. | Frage | Stand | Kernaussage |
|---|---|---|---|
| 1 | Bootloader-Adresse 0xF0 | **beantwortet** | 0xF0 ist eine adressunabhängige Sonderadresse. Sie wird nur mit fun **0xF2** bedient: ein **EEPROM-Dienst** (24C16 per Bit-Bang-I²C an PE5/PE6). op 0 schreibt mit einem 8-Byte-Schlüssel, op 1 liest. Die Antwort trägt Absender 0xF0. Der Host nutzt den Dienst nie. |
| 2 | Längengrenze len ≤ 0xFC | **beantwortet** | Prüfung bei `0x080011ec`–`0x080011f2`. Ursache ist der Empfangspuffer: 0xFF Byte je Slot, 4 Byte Kopf. Ein zu langer Frame wird **stumm** verworfen, es kommt keine Status-1-Antwort. |
| 3 | Burst-Timing `[00 02]` | **beantwortet** | Das ist **kein 2–3-ms-Timeout**. Der Host rechnet `Timeout = Slotanzahl(Maske) × 300 s`. Bei Maske 0 ergibt das **0 s**. Dann folgen 5 Sendungen direkt hintereinander. Jede Wartezeit endet nach einer Reactor-Runde (live 1–8 ms). |
| 4 | Mehrfachmaske, Host-Timeouts | **beantwortet** (Host); Box-Laufzeit live offen | Je Versuch 300 s pro Slot (bei 4 Slots 1200 s), bis 5 Versuche, keine Gesamtfrist. 4 Slots reichen rechnerisch (≤ ~966 s). **1 Slot** kann bei laufendem Preload knapp werden (≤ 364 s > 300 s). |
| 5 | Stock-RESUME nach CFS-Fehler | **beantwortet** (statisch); Rückfahrbewegung von `RESUME_BASE` = **ANNAHME** | Stock **holt** den Wechsel beim RESUME **automatisch nach**. Bedingung: Fehler im T<n>-Pfad und Kopfsensor leer. Stock fährt zur **Safe-Pos X225/Y345 auf Druck-Z** zurück (PAUSE_STATE), nicht zur Druckposition vor dem Wechsel. Die Pi-Lösung **weicht in beiden Punkten ab**. |

---

## 0. Quellen und Methode

**Bootloader-Image**
- In `fw/cfs/` liegen nur App-Images: cfs0 ab `0x08003000`, cfs6 ab `0x08004000`. Das bestätigt `RE-RESTE-BEFUND-2026-10-16.md` §1.
- Quelle ist deshalb der Flash-Dump `reversing/live_dumps/0x0800_0000.bin` aus fake-name/cfs-reverse-engineering. Commit `d9453317…`, 2025-04-18. Auszug und Herkunft: `arbeitsdateien-lokal/re-bl-host-1017/extern/fake-name-auszug/HERKUNFT.txt`.
- Verwendet: Bereich `0x08000000`–`0x08002FFF`, SHA256 `99cd9145…dd8`. Datei: `arbeitsdateien-lokal/re-bl-host-1017/bl/bootloader_cfs0_050_G32_0x08000000.bin`.
- Versionsstring `cfs0_050_G32` @`0x08002240`. Das ist genau die `boot_ver`, die `fw/cfs/version.json` zur App 153 der Box des Users nennt. Hinter dem Bootloader liegt im Dump App 108 (`cfs0_000_108` @`0x08003200`).
- **Übertragbarkeit:**
  - Versionsname gleich (**B**).
  - Verhalten live gestützt (**B+L**, `12:6922`–`6940`):
    - A1-Antwort `f7 fe 11 00 a1 01 01 …` kommt von Adresse 0xFE mit mode 1.
    - Danach folgen A0 und A2 an 0x01, beide mit mode 1.
    - Dann sendet der Host `0xff 0x4 0x0 0xb 0x1` (Loader→App) ohne Antwort.
    - Das ist genau der Ablauf aus §1.
  - Byte-Gleichheit mit dem Bootloader in der eigenen Box ist nicht prüfbar (**ANNAHME**, nur per SWD-Dump).

**Werkzeuge**
- Ghidra 12.1.4 headless in einem **eigenen** Projekt (`…/re-bl-host-1017/ghidra/proj`, Programme `bl_g32` und `host2`).
- Eigene Skripte:
  - `BlSetup.java`, `DumpAll.java`, `FixJT.java` (Sprungtabelle @`0x08001a38`)
  - `LabelDec.java`
- `llvm-objcopy` und `llvm-objdump` (Thumb-v7em bzw. ARM).
- Python-Skripte (`skripte/`):
  - `pyx.py`: Cython-Stringtabelle, `__pyx_int/float`-Konstanten, Funktionsgrenzen über Qualnames
  - `annot2.py`: annotierte Disassembly mit Register-Basen und Cache-Slots von `__Pyx_GetModuleGlobalName`
  - `wo.py`
  - `retry_timing.py`: Live-Wartezeiten
- REA-MCP war nicht nötig.
- Der Ghidra-Decompiler faltet bei den Cython-`.so` die PC-relativen Literale nicht. Deshalb ist §3–§5 aus annotierter ARM-Disassembly rekonstruiert.

---

## 1. Bootloader-Parser: Bedeutung der Adresse 0xF0

### 1.1 Empfang (USART1-IRQ `vec_54` @`0x08001038`, **B**)

**Zustandsautomat**
- Pro Byte wird USART1 `DATA` (`0x40004404`) gelesen.
- Stufe 0 erwartet `0xF7` (@`0x08001098`).
- Stufe 1 ist das Adressbyte. Es wird einer von vier „Adressklassen“ zugeordnet und in Frame+2 abgelegt.

| Zieladresse | Klasse | Prüfstelle | Was der Bootloader damit macht | Antwort |
|---|---|---|---|---|
| `0xFF` | 2 | `0x080010c8` | Nur fun **0x0B** mit data[0]=**01** → Sprung in die App, wenn sie gültig ist (CRC-Prüfung `FUN_08000a38`). Geprüft in `FUN_0800136c` @`0x080013ac`–`0x080013c4` und im 30-s-Fenster `FUN_080013f8` @`0x08001454`–`0x08001470`. | keine |
| `0xFE` | 1 | `0x080010d2` | Nur fun **0xA0/0xA1** (Auto-Addr), direkt im IRQ: @`0x08001274`–`0x08001282` → `FUN_08000b28`. A1 antwortet nur bei `data[0] ≤ eigene ≤ data[1]`, nach 1–999 ms Zufallswartezeit (LCG `0x41C64E6D`; Seed = CRC über die 12-Byte-UID, Poly `0xEDB88320`, in `FUN_08001480`). A0 vergleicht 12 Byte UID; bei data[0] ≠ 0 wird das die neue Adresse. | **1×**, Absender = eigene Adresse. Daten `[01, 01, UID12]` = dev_type 1, **mode 1** (0x20000b76 ← 1) |
| eigene (Byte @`0x20000010`) | 0 | `0x080011bc` | fun **0xA2/0xA3** im IRQ (@`0x080012a8`). fun **0xF0** (Update-Subs, Hauptschleife, @`0x080013d2`–`0x080013d6`). | ja |
| `0xF0` | 3 | `0x080011c8` | Nur fun **0xF2** (@`0x080012b4`–`0x080012c6`) → `FUN_08000e1c` (EEPROM-Dienst, §1.2). Andere fun werden in der Hauptschleife verworfen. | ja, **Absender 0xF0** |
| sonst | – | `0x080011d0`–`0x080011e8` | Frame verworfen. RBNEIE (CTL0 Bit 5) bleibt aus, bis der Receiver-Timeout auslöst (USART_RT `0x40004484` = 10 Bitzeiten). | keine |

**Weitere Fakten (B)**
- **Startadresse 0xFE:**
  - C-Startup kopiert `0x08002260` → `0x20000000` (0x14 Byte, `memcpy` @`0x08001f90`).
  - Das Byte bei `0x08002270` ist `0xFE` und landet in der eigenen Adresse `0x20000010`.
  - Eine Box im Bootloader ist also unadressiert, bis A0 kommt.
- **30-s-Fenster:**
  - Aufruf `FUN_080013f8(30000)` @`0x08001988`.
  - Einheit ms: SysTick-Zähler / 15000; V: 120 MHz/8.
  - Der Bootloader wartet, solange die eigene Adresse 0xFE ist. Läuft die Frist ab und ist die App gültig, springt er in die App (`FUN_08001304`). Sonst wartet er endlos.
- **Update-Hauptschleife:**
  - Nur fun 0xF0 an die **eigene** Adresse; data[0] = Sub (Sprungtabelle @`0x08001a38`).

    | Sub | Wirkung |
    |---|---|
    | 0 | Version, 25 Byte: `cfs0_050_G32` + `-` + App-String @`0x08003200` |
    | 1 | Request (Ack 0x75), dann 4-Byte-Länge, dann Datenblöcke à **0x80** Byte (`FUN_0800136c(…,0x80,…)`) |
    | 2 | App starten (Ack 0x75, 3 s, Sprung) |
    | 3 | Antwort **0xE0** |
    | 4/5 | ignoriert |
    | 6 | Page `0x0807F800` löschen |
    | 7 | UID (12 Byte) |

  - Fehlercodes 0x1F, 0x20, 0x21.
- **Unterschiede zur App** (`GHIDRA-CFS-BEFUND2-2026-10-16.md` §7):
  - A0 wird nur **1×** beantwortet (App 3×).
  - Ziel 0xFE wird **unabhängig** von der eigenen Adresse angenommen. A0 mit passender UID ändert also auch eine schon adressierte Box im Bootloader. A1 bleibt bei Host-Daten `[FE FE]` adresslosen Boxen vorbehalten.
  - mode = 1 statt 0.

### 1.2 Was 0xF0 bedeutet: EEPROM-Dienst `FUN_08000e1c` (**B**, Zweck **V**)

**Hardware-Zugriff**
- Bit-Bang-I²C an **PE5 (SCL) und PE6 (SDA)**.
- Die Pin-Strings `PE5`/`PE6` @`0x080021a8`/`0x080021ac` werden in `FUN_08001480` in die Pinvariablen `0x20000004`/`0x20000005` übernommen.
- Das deckt sich mit den App-Strings `EEPROM_SCL:PE5`/`EEPROM_SDA:PE6` (fake-name `firmware.md:393-394`).
- 24C16-Adressierung: Gerät `0xA0 | ((addr>>7)&0xE)`, Grenze `addr ≤ 0x800` = 2 KiB (`FUN_08000ce4` @`0x08000cf6`).

**Request-Daten (nach fun 0xF2)**
- **op 0 – Schreiben:**
  - `data[1..8]` = 8-Byte-Schlüssel. Er wird gegen eine fest einkompilierte Konstante @`0x080021e8` (8 Wörter) verglichen.
  - `data[9..]` (= len−12 Byte) wird byteweise ab **EEPROM-Adresse 0** geschrieben, mit je 3 ms Pause.
  - Danach liest der Bootloader zurück und vergleicht, bis zu 3 Versuche.
  - Status 0 = OK, 1 = Fehler oder Schlüssel falsch.
- **op 1 – Lesen:**
  - `data[1]` = n. Der Bootloader liest n Byte ab EEPROM-Adresse 0 zweimal und vergleicht (bis zu 3 Versuche).
  - Die Antwort enthält die n Byte.
- **Antwort:**
  - Der Frame-Builder `FUN_08000ad8` schreibt die eigene Adresse in den Frame. Deshalb wird die eigene Adresse vorher auf **0xF0** gesetzt (@`0x08000f7e`–`0x08000f80` bzw. `0x08000ff8`–`0x08000ffa`) und danach zurückgeschrieben, aber nur wenn der alte Wert ≠ 0 war.
  - Die Antwort hat also Absender 0xF0, fun 0xF2.
- Alles läuft **im USART-IRQ**, blockierend mit ms-Delays.

**Deutung (V)**
- 0xF0 ist eine **adressunabhängige Werkstatt-/Service-Adresse**. Damit kann man das Board-EEPROM einer Box im Bootloader lesen oder (mit Schlüssel) beschreiben, ohne ihre Busadresse zu kennen.
- Bei mehreren Boxen im Bootloader würden alle antworten und kollidieren. Der Dienst ist also für ein Einzelgerät gedacht.
- Was ab EEPROM-Adresse 0 steht (Werksdaten, SN?), ist hier **ANNAHME**. Das EEPROM-Layout gehört zur App-Seite (anderer Agent).

**Nicht verwechseln**
- **Func-Code 0xF0** = Update an die eigene Adresse (`mcu_util_485`).
- **Adresse 0xF0** = EEPROM-Dienst mit fun 0xF2.

**Host nutzt 0xF0/0xF2 nicht (B)**
- `mcu_util_485`: die Disassembly enthält `mov r3,#240` (Func 0xF0) an 7 Stellen (`0x15214`…`0x15a80`), aber kein `#242`.
- Die Konstantentabellen von `box_wrapper`/`serial_485_wrapper` enthalten weder 240 noch 242.
- Live nie gesehen (`PROTOKOLL-485.md:721-723`).

**Sicherheit:** hoch für Parser, Dispatch und Dienst (B, teils B+L). Der Zweck ist V.

**Empfehlung (nur Text, kein Code geändert)**
- `nachbau/serial485.py`: 0xF0 **nicht** in `BROADCAST_ADDRS` aufnehmen. Nie an Adresse 0xF0 senden, weil das der EEPROM-Schreibpfad ist.
- `nachbau/auto_addr.py`: ein Bootloader beantwortet A0 nur 1×. Das ist kompatibel.
- `cfswatch.py` könnte Frames an oder von 0xF0 mit fun 0xF2 als „Bootloader-EEPROM-Dienst“ markieren.

---

## 2. Längengrenze len ≤ 0xFC

**Beleg (B)**
- `vec_54` @`0x080011ec`–`0x080011f2`: `subs r2,r7,#3; uxtb r2,r2; cmp r2,#0xF9; bhi →Verwerfen`.
- Angenommen wird nur **3 ≤ len ≤ 0xFC**. Ein zu kleines len läuft über 0xFD hinaus und fällt ebenfalls heraus.
- Bei einem Verstoß schaltet der Bootloader den RX-Interrupt aus (@`0x080011d8`–`0x080011e8`) und setzt erst nach dem Receiver-Timeout zurück (@`0x080010e0` ff.). Es gibt **keine Antwort**. Die App liefert dagegen Status 1.

**Ursache (B)**
- Der Empfangspuffer hat zwei Slots à **0xFF Byte** ab `0x20000918` (Ping-Pong, Slot i = Basis + i·0xFF). Der Zeiger auf den fertigen Frame liegt bei Basis+0x200.
- Jeder Slot hat 4 Byte Kopf: Index/Status, Stufe → später Nutzlänge, Adressklasse.
- Die Nutzbytes (Status, fun, Daten = len−1 Byte) beginnen bei +4. Das letzte Byte liegt damit bei Index len+2. Aus len+2 ≤ 0xFE folgt **len ≤ 0xFC**.
- Senden ist symmetrisch: `FUN_08000ad8` legt die CRC fest an Index 0xFE eines 0xFF-Puffers.

**Host (B)**
- In `box_wrapper`/`serial_485_wrapper` gibt es keine Längenprüfung.
- Die serial-Konstanten sind nur 0–5, 160, 252, 254, 2400, 3600, −20, 0.01, 5.0 und 50.0.
- 252 (0xFC) und 254 sind **Adress-Schwellen** für den Antwortschlüssel in `cmd_send_data_with_response` (@`0x1c354`–`0x1c3e0`), keine Längengrenze.
- Den C-Teil (`serial_485_queue_*`, chelper) gibt es im Projekt nicht → **offen**.

**Live**
- Längstes len = 46 (0x2E, RFID-Antwort; Auswertung `'#msgid'` über alle Logs).
- Ein Update-Datenblock hat len 0x83.

**Folge für `nachbau/serial485.py`** (`FRAME_LEN_MAX = 255`, Z. 97/146/175) und `pi-host/k2_transport.py:179` – **nur Empfehlung**
- Für den **Empfang** 255 belassen. Die App-Grenze ist nicht Teil dieses Auftrags.
- Beim **Senden** `len ≤ 0xFC` erzwingen oder warnen. Kein Stock-Frame ist größer, und ein Bootloader verwirft größere Frames stumm (der Host sieht nur Timeouts).
- Für einen künftigen CFS-Update-Pfad: Datenblöcke **genau 0x80 Byte**. Der Bootloader kopiert je Frame fest 0x80 Byte, schaltet die Flash-Adresse aber um die gemeldete Länge weiter (`FUN_08001480` Update-Schleife). Größere Blöcke würden das Image zerstören.
- **Nebenbefund (offen):** Sub 3 antwortet `0xE0`. Laut `PROTOKOLL-GESAMT.md` §1.7 liest `mcu_util_485` die Sector-Size als signed Byte (≤ 0 → Fehlerpfad). Das widerspricht sich. Nachzuprüfen bei `mcu_util_485` @`0x15540`–`0x15560`; für die Fragen hier nicht nötig.

---

## 3. Host-Burst-Timing `0x0d [00 02]` (BEFUND2 §5 „Ursache Host-seitig vermutet“)

**Antwort:** Die Bursts entstehen, weil der Host bei Maske 0 einen **Timeout von 0 s** errechnet. Einen Host-Takt von 2–3 ms gibt es nicht. **B**, live gestützt.

### Kette (alle Adressen B)

1. **G-Code `BOX_SET_PRE_LOADING`** (`cmd_set_pre_loading` @`0x7204c`)
   - ACTION `RUN` → Aktion 2, `TIGHT` → 3 (@`0x0729d4`–`0x0729f0`).
   - Der zweite `get_int` hat **default 0**, min 0, max 15 (@`0x072238`–`0x0722cc`; vermutlich `NUM`, der Name ist nicht direkt aufgelöst).
   - Aufruf von `communication_set_pre_loading` über eine der beiden Aufrufstellen @`0x072738`/`0x072bc4`. Wie die Argumente zugeordnet werden, ist nicht vollständig verfolgt. Der Wire-Frame `[00 02]` zeigt aber Maske 0 und Aktion 2.
   - Live steht davor jedes Mal die Warnung „the param of 'POWER_ON' is error“ aus genau diesem Handler (`13:48750`, `13:69363`, `13:71503`).
2. **`BoxAction.communication_set_pre_loading(self, addr, action, num=15, force=False)`** @`0x1581e4`
   - Druckt der Drucker oder ist er pausiert und `force is False`, wird **nichts gesendet** (@`0x158248`–`0x158284`, GOT `_Py_FalseStruct`).
   - `count = Σ_i [(1<<i) & num ≠ 0]` für i = 0..3 (@`0x158580`–`0x158630`).
   - op 0/1: `send_data(addr, cmd, TIMEOUT_SHORT_TIME, data=num+op)`, also 2 s (@`0x1589a4`).
   - op 2/3: Meldet `get_filament_sensor_detect()` Filament im Kopf (Log „filament_sensor true/false“, `0x117fdc`), wird **nicht gesendet** (@`0x158744`–`0x1587dc`).
   - Sonst **`timeout = count × cmd_timeout['SET_PRE_LOADING']`** (`PyNumber_Multiply` @`0x158b3c`).
   - `cmd_timeout['SET_PRE_LOADING']` = `TIMEOUT_LONGER_TIME` = **300** (Modul-Init @`0x1cd00`, @`0x1d1dc`–`0x1d208`).
   - Weitere Konstanten: `TIMEOUT_SHORT_TIME` = 2 (@`0x1ccd0`), `TIMEOUT_MAX` = 3600 (@`0x1cd18`).
   - **Maske 0 → count 0 → timeout 0.**
3. **`send_data`** → `self._serial.cmd_send_data_with_response(data, timeout)` (@`0x161d04`–`0x161dc4`, 2 Argumente).
   - `retries_en` hat den Default **True** (Wrapper @`0x1d744`, GOT → `_Py_TrueStruct`).
4. **`Serial_485_Wrapper.cmd_send_data_with_response`** @`0x1c134`
   - Antwortschlüssel `'#addr_<a>' + '#cmd_<f>'`. Für 0xFC–0xFE mit fun ≥ 0xA0 entfällt der Adressteil.
   - `add_send_data(…)` legt den Auftrag in die Warteschlange.
   - Danach wartet die Funktion mit `completion.wait()` **ohne Frist** (@`0x1cc90`–`0x1ccf0`, `0x1d038`).
5. **`send_queue_process`** @`0x19ca0` (Reactor-Timer)
   - Arbeitet `send_list` **sequentiell** ab: `get_response(*item)`, dann `remove_send_data`.
   - Leerlauf-Takt **10 ms** (`return monotonic()+0.01`, K0.01 @`0x19f94`).
6. **`Serialhdl_485.get_response`** @`0x1a548`
   - Anzahl Versuche:
     - `retries_en` falsch → 1 (@`0x1ab28`)
     - `timeout ≥ TIMEOUT_MAX` (3600, serial-Modul @`0x5d14`) → 1
     - sonst → **5** (@`0x1a6a8`–`0x1a750`, `0x1ab18`)
   - Schleife:
     - `retries -= 1` (@`0x1a7a8`), Log `retries = %d, cmd = %s` (@`0x1a884`)
     - `raw_send_wait_ack(cmd, key, timeout if timeout < TIMEOUT_MAX else 1)` (@`0x1a9d0` Py_LT, `0x1aba0` K1, Aufruf @`0x1aacc`)
     - Log `cmd[…] get response or timeout` (@`0x1ad88`)
     - Keine Antwort und `retries > 0` (Py_LE @`0x1ae74`) → Log `do not get response…` (@`0x1b9ac`) und **sofort** der nächste Versuch, ohne Pause.
7. **`Serialhdl_485.raw_send_wait_ack`** @`0x10fd4`
   - `completion = reactor.completion()`; `send_notifications[key] = completion` (@`0x11114`).
   - `ffi_lib.serial_485_queue_send(serialqueue, cmd, len(cmd))` (@`0x11148`).
   - **`completion.wait(reactor.monotonic() + timeout)`** (@`0x112b4`–`0x11384`).
   - Bei `None`: `send_notifications.pop(key)` (@`0x11460`–`0x114f0`). Eine spätere Antwort hat damit keinen Empfänger mehr.

### Live-Abgleich (B+L, `arbeitsdateien-lokal/re-bl-host-1017/out/retry_timing.txt`)

| Kommando | Sendungen (n) | Wartezeit Sendung → „get response or timeout“ | Bedeutung |
|---|---|---|---|
| `0x0d [00 02]` | 26 | **1–8 ms**, Median 2 ms (z. B. `13:69366`–`69385`) | timeout 0 → Rückkehr nach einer Reactor-Runde, 5 Sendungen in ≈ 20–30 ms |
| `0x0d [01 02]` | 4 | 31,0–37,0 s, je 1 Sendung (`13:90197`) | count 1 → 300 s, Antwort rechtzeitig |
| `0x14` ohne Antwort | – | 2,00–2,07 s | `TIMEOUT_SHORT_TIME` |
| `0x0a` | – | max 1,07 s, immer „retries = 0“ | `GET_BOX_STATE` = 3600 ≥ TIMEOUT_MAX → 1 Versuch à 1 s (bestätigt die Formel) |

- In den Bursts loggt die C-Ebene 4× `buf_len = 0x0` ohne vollständigen Frame (`13:69372`–`69384`). Das ist RX-Aktivität ohne gültige Antwort: Kollision oder Fragment (**V**).

### Neubewertung von BEFUND2 §5

| Punkt | Neue Bewertung |
|---|---|
| (1) „Burst-Timeout 2–3 ms“ | Präzisiert: **konfiguriert 0 s** (B). Die 2 ms sind Reactor-Latenz. |
| (2) Kollision auf dem Halbduplex-Bus | Plausibel (**V**, gestützt durch `buf_len = 0x0`). |
| (3) Späte Antworten verworfen | **B** (`pop` des Schlüssels). |

- Die Gefahr bleibt: Die Box führt jeden vollständig empfangenen Frame aus, also bis zu 5× Pull-back aller Hub-Slots (`13:130620` ff.: Box ≈ 11 s stumm).

**Sicherheit:** hoch (B + Live-Zeitmessung).

**Empfehlung (nur Text)**
- `nachbau/box.py`:
  - G2 (Maske 0 → 980 s, `retries=0`) ist sicherer als Stock. Im Kommentar die Stock-Formel ergänzen: `count×300 s`, Maske 0 → 0 s → 5 Sendungen.
  - Erwägen, op 2/3 mit **Maske 0 abzulehnen** oder nur mit ausdrücklichem Flag zu senden. Stock-Wirkung von Maske 0 ist nur der Pull-back aller Hub-Slots ohne Preload (BEFUND2 §5).
- Stock-Schutz übernehmen:
  - op 2/3 nur senden, wenn der **Kopf-Filamentsensor leer** ist.
  - Im Druck oder in der Pause nur mit `force`.
- `cfswatch.py`: `[00 02]`-Bursts samt key831 als „Stock-Host-Artefakt (Timeout 0)“ kennzeichnen, nicht als Box-Ausfall.
- `pi-host` (`KTWO_BOX_PRELOAD` nur Einzelslot, `retries=0`): kein Burst-Risiko.

---

## 4. `0x0d` op 2 mit Mehrfachmaske: Host-Timeouts

**Antwort (B, Host-Seite)**
- **Je Versuch:** `Slotanzahl × 300 s`, also 300 / 600 / 900 / **1200 s** für 1 / 2 / 3 / 4 Slots (§3, @`0x158b3c`).
- **Versuche:** bis 5, weil `retries_en=True` und Timeout < 3600 (§3 Schritt 6).
  - Bleibt die Antwort aus, sendet der Host bis zu 4× erneut.
  - Folge (**V**): Die Box hat keine Duplikaterkennung, und ihr Empfang ist während des Handlers blockiert (BEFUND2 §5). Der wiederholte Frame wird deshalb nach Ende des laufenden Vorgangs abgearbeitet. Der Force-Preload läuft dann **noch einmal**.
- **Keine Gesamtfrist:** `cmd_send_data_with_response` wartet mit `completion.wait()` ohne Argument.
  - Worst case 5 × 1200 s.
  - Die Warteschlange ist so lange blockiert: `send_queue_process` arbeitet sequentiell. Live passt dazu „Host pollt solange nicht“ (`PROTOKOLL-485.md` §6.4).
- **Stock sendet Mehrfachmasken z. B. bei** `cmd_first_power_on_preload` → `communication_set_pre_loading(…, RUN, num=…)` (@`0x0833cc`). Wie die Maske dort berechnet wird, ist nicht verfolgt.
- Live gab es nur `[01 02]` und `[00 02]`. Eine Mehrfachmaske wurde nie live gesehen.

**Reicht das? (V)**
- Box-Zeiten aus dem App-Befund (anderer Agent; `PROTOKOLL-485.md:136-137`, BEFUND2 §5/§8):
  - je Slot 5 s + ≤ 180 s
  - im Zustand PRELOAD vorher ≤ 179 s Warten
  - in IDLE vorher Pull-back hub-belegter Slots (p1 ≤ 40 s + Stufe 2 ≤ 7 s)

| Slots | Box worst case (V) | Stock-Host je Versuch | reicht? |
|---|---|---|---|
| 4 | 740 s nominal; mit 179 s Vorwarten 919 s; mit Pull-back eines Hub-Slots ≈ 966 s | 1200 s | **ja** (≥ ~230 s Reserve) |
| 3 | 555 s / 734 s | 900 s | ja |
| 2 | 370 s / 549 s | 600 s | ja |
| 1 | 185 s; mit Pull-back 232 s; mit **179 s Vorwarten 364 s** | 300 s | **nicht immer.** Läuft schon ein Preload, ist ein Retransmit und doppelter Force-Preload möglich. |

**Empfehlung (nur Text)**
- `nachbau/box.py` `preload_force_timeout` = `180 + 200·n` ergibt 380 / 580 / 780 / **980 s**. Das deckt den 1-Slot-Fall (364 s), liegt bei 4 Slots aber **knapp** über ≈ 966 s.
- Vorschlag: **`max(180 + 200·n, 300·n)`**, also 380 / 600 / 900 / 1200 s. `retries=0` beibehalten.
  - Mit `MAX_BUS_HOLD_S = 1200` (`serial485`) bleibt das erlaubt (1200 × 1 Versuch).
- `pi-host/k2_box.py` `box_wait_timeout` (200 s, als ANNAHME markiert) gegen den 1-Slot-Worst-Case (364 s) prüfen.
- Die echte Box-Laufzeit einer Mehrfachmaske bleibt **live offen** (Test aus `RE-RESTE-BEFUND` §2d).

---

## 5. Stock-Verhalten nach CFS-Fehler beim RESUME

### 5.1 Was beim Fehler passiert (B)

**`BoxSave.recode_err(err, last_tnn, tnn, addr, vtnn)`** @`0x19fb90`
- `save_resume_tnn(tnn)` wird nur aufgerufen, wenn `err` **nicht** in `macro_error_list` steht und `tnn` gesetzt ist (@`0x19ffe0`–`0x1a008c`). Der Contains-Helfer `0x30278` wird dabei mit Py_NE aufgerufen, also als „not in“.
- `macro_error_list` = `macro_cut_err`, `macro_retrude_err`, `macro_box_extrude_err`, `macro_extruder_extrude_err`, `macro_flush_err` (`BoxSave.__init__` @`0x1a2754` ff.). Das sind Fehler der **M8200-Kette**.
- Der T<n>-Pfad (`Tn_action`) meldet `cut_err`, `retrude_err`, `box_extrude_err`, `extruder_extrude_err`, `flush_err`, `filament_err`, `empty_print` … (`error_list` @`0x1a2604` ff.). Für ihn wird **`resume_tnn` = Ziel-TNN des abgebrochenen Wechsels** gespeichert.
- Für Fehler der M8200-Kette wird **kein** `resume_tnn` gesetzt.

**Fehler-Park** (`BoxError.handle_event` @`0x1c3ca4`; identische Folge in `error.__init__` @`0x1c1330`):
1. `G91`, `G0 Z-{move_z} F600`, `G90`. Damit wird der Z-Hub des Wechsels zurückgenommen (`z_down` @`0x144ff0`: `G0 Z0.5`, `move_z=0.5`).
2. **`BOX_MOVE_TO_SAFE_POS`**. Das ist `move_to_safe_pos` @`0x1184e0`: `G90`, **zuerst `G0 Y{safe_pos_y}`, dann `G0 X{safe_pos_x}`** (@`0x118888` vor `0x1189d4`). Die Werte sind Y345 und X225 (`box.cfg:33-34`).
3. `M400`, `move_z=0`.
4. Geschwindigkeit, Beschleunigung und speed_factor zurücksetzen (`G0 F…`, `M204 S…`, `M220 S…`).
5. Ist der Drucker pausiert: `M104 S140` und erneut `BOX_MOVE_TO_SAFE_POS`.
6. Druckt er: **`PAUSE`** (@`0x1c53b8`–`0x1c54d8`), dann `M400`.

- Daraus folgt (B, Makrofolge): `PAUSE_BASE` speichert PAUSE_STATE an **X225/Y345 und Druck-Z** (ohne Hop). Das ist weder die Fehlerposition noch die Druckposition vor dem Wechsel.
- Im Fehlerpfad gibt es **kein** `get_position` und keine gespeicherte Startposition (Stringlisten von `handle_event` und `error_resume_process`).

### 5.2 RESUME (B; Makros `live-logs-2026-10-16/config-aktuell/gcode_macro.cfg:870-881`)

`RESUME` → `RESUME_EXTERNAL_PROCESS` → **`BOX_ERROR_RESUME_PROCESS`** → `RESUME_EXTERNAL` → `RESUME_BASE`.

**`error_resume_process`** @`0x7cd28` (aufgerufen von `cmd_error_resume_process` @`0x6d9e4`; der wirft selbst keinen Fehler)

| Schritt | Bedingung | Wirkung | Adresse | Live |
|---|---|---|---|---|
| 1 | CFS `enable == 0` | Log, `clear_resume_tnn()`, Ende | @`0x7cdb8` ff. | `12:5550/6214/6683` |
| 2 | Zustand ≠ `paused` | Log, `clear_resume_tnn()`, Ende | @`0x7d040` ff. | – |
| 3 | `resume_tnn is None` | Log, Ende | @`0x7d380`–`0x7d4bc` | `05:66249` |
| 4 | Kopfsensor belegt **und** `error == 'filament_err'` | Sonderlog | @`0x7d4c8`–`0x7d738` | – |
| 5 | – | `tnn = resume_tnn`; `clear_resume_tnn()`; `clear_err()` | @`0x7d744`–`0x7d8ec` | – |
| 6 | **Kopfsensor meldet Filament** | Log „filament sensor is detected“, **Ende ohne Wechsel** | @`0x7d938`–`0x7dabc` | `12:88007` |
| 7 | sonst | **Wechsel nachholen** (Folge unten) | @`0x7dcd0` ff. | – |

**Schritt 7 im Einzelnen:**
- `go_to_extrude_pos(tnn)` (@`0x7dcd0`)
- `box_retrude_material(tnn)` (@`0x7dd78`)
- `box_extrude_material(…)` (@`0x7e388`)
- `extruder_extrude(…)` (@`0x7e6cc`)
- `material_change_flush(…)` (@`0x7e9d4`)
- Bei einem Fehler in einer dieser Stufen:
  - `recode_err(…, '<stufe>_err')`
  - **`pause_resume.resume_err = True`** (@`0x7e1d8`, GOT `_Py_TrueStruct`)
  - `move_to_safe_pos()`, `M400`, Ende

**Danach**
- `RESUME_EXTERNAL` läuft nur ohne `resume_err`: Temperatur, `BOX_GO_TO_EXTRUDE_POS`, Vorextrusion E+80, `BOX_NOZZLE_CLEAN`.
- Dann folgt **`RESUME_BASE`**. Ob Creality-`RESUME_BASE` bei `resume_err` abbricht, ist unbekannt (**ANNAHME**: nein). Die Quelle `pause_resume` liegt nicht vor.

**Zweiter Stock-Weg**
- Die Display-Taste „Wiederholen“ löst `BOX_TNN_RETRY_PROCESS` aus (`Tnn_retry_process` @`0x8f5c4`).
- Dort läuft je Fehlerart ein `*_retry_process`, danach `RESUME`, wenn der Drucker pausiert ist.
- Live: `12:51987` („error: empty_print, tnn: T1A …“, „set resume_flag“).

### 5.3 Rückfahrziel

**ANNAHME** zur Bewegung: Creality-`RESUME_BASE` arbeitet wie Upstream, also `RESTORE_GCODE_STATE NAME=PAUSE_STATE MOVE=1`. Das ist eine gerade Fahrt zu **X225/Y345 auf Druck-Z**. Danach führt der nächste Slicer-Befehl zum Druck.

**V:** Auf X225/Y345 senkt sich die Düse auf Druck-Z. Steht dort ein Objekt, droht eine Kollision. Stock nimmt außerdem den Z-Hub schon an der Fehlerposition zurück, bevor es Y/X zur Safe-Pos fährt.

### 5.4 Vergleich mit der Pi-Lösung (`pi-host/config-pi/NOTIZ-reinigung-resume.md` §6a.3, `NOTIZ-cfs.md` §3) – **weicht ab**

| Punkt | Stock (dieser Befund) | Pi (Entwurf 17.10.) | Abweichung |
|---|---|---|---|
| Abgebrochenen Wechsel nachholen | **Automatisch** im RESUME (T<n>-Pfad-Fehler, CFS an, pausiert, `resume_tnn` gesetzt, **Kopfsensor leer**). Bei Kopfsensor belegt kein Nachholen. M8200-Kettenfehler setzen kein `resume_tnn`. | **Kein** Nachholen. Von Hand `T<n>` in der Pause, dann `KTWO_BOX_ERROR_CLEAR` + `RESUME`. | **ja** |
| Rückfahrziel | PAUSE_STATE = **Safe-Pos X225/Y345, Druck-Z ohne Hop** (nach Fehler-Park), gerade Fahrt (ANNAHME) | **Druckposition vor dem Wechsel**. X/Y in Pausenhöhe, Z senkrecht, PAUSE_STATE dort neu gesetzt. | **ja** (Pi konservativer) |
| Ausstieg aus Schneidbereich/Schacht | Z zuerst auf Druck-Z, dann **Y zuerst**, dann X (`move_to_safe_pos`). Kein Sonderfall Schneidhebel. | Schneidbereich **nur in X**, Schacht nach Z-Hub über X160 → Y345 | **ja** (Pi vorsichtiger). Für „am Hebel nur X“ gibt es im Stock-Fehlerpfad keinen Beleg. |

**Sicherheit:** hoch für die Stock-Logik (B, Stringfolgen und Disassembly mit Adressen, teils B+L). Die Bewegung von `RESUME_BASE` ist ANNAHME.

**Empfehlung (nur Text)**
1. In NOTIZ §6a.2/§6a.6 die ANNAHME „Stock holt evtl. nach“ durch diesen Beleg ersetzen.
2. Das abweichende Rückfahrziel als **bewusste Abweichung** dokumentieren. Die Pi-Lösung ist sicherer und sollte bleiben.
3. Optional Stock-analog ergänzen: In `_fail` die Ziel-TNN merken. Beim RESUME nachholen, aber **nur wenn der Kopfsensor leer ist**. Bei Fehler RESUME sperren statt weiterlaufen.
4. Minimal: RESUME nach einem abgebrochenen Wechsel sperren, solange der Kopfsensor kein Filament meldet. Das verhindert einen Weiterdruck ohne Filament, wenn `T<n>` von Hand vergessen wurde.

---

## 6. Offen (mit Suchumfang)

**Offen in diesem Auftrag (Bootloader/Host)**
- **Creality-`pause_resume`** (Semantik von `PAUSE_BASE`/`RESUME_BASE`, Umgang mit `resume_err`): Quelle liegt im Projekt nicht vor. Liegt auf dem Drucker unter `klippy/extras/`; Zugriff war in diesem Auftrag verboten.
- **Byte-Gleichheit des Bootloaders** in der Box des Users: nur per SWD-Dump `0x08000000`–`0x08002FFF`.
- **Sub 3 → 0xE0** gegen `mcu_util_485` „signed ≤ 0 → Fehler“: `mcu_util_485` @`0x15540`–`0x15560`.
- **C-Teil `serialqueue`** (Längenprüfung, `buf_len`-Logs): chelper-Binary nicht im Projekt.
- **G-Code-Parametername des 2. `get_int`** in `BOX_SET_PRE_LOADING` (vermutlich `NUM`): Das Objekt @`0x2435c4` liegt außerhalb der Stringtabelle (@`0x0722a0`).
- **Maskenberechnung in `cmd_first_power_on_preload`** (@`0x0833cc`): nicht verfolgt.

**Gehört zur App-Seite (anderer Agent)**
- **EEPROM-Inhalt ab Adresse 0** (Zweck des 0xF0-Dienstes): EEPROM-Layout der App (`FUN_0800b088`, `FUN_0800af7e`).
- **Echte Box-Laufzeit bei Mehrfachmaske** und op 3: nur live.

## 7. Arbeitsdateien (`arbeitsdateien-lokal/re-bl-host-1017/`)

**Bootloader**
- `bl/bootloader_cfs0_050_G32_0x08000000.bin`, `bl/bl.elf`, `bl/bl_disasm.txt`
- `out/bl_all.c`, `out/bl_main_fixed.c` (Hauptschleife mit Sprungtabelle)

**Host-`.so`**
- `so/*.so` (Kopien), `so/*_disasm.txt`
- `out/*_strtab.json`, `*_consts.json`, `*_funcs.tsv`, `*_pcrel.json`
- `out/s485_*.asm`, `out/box_*.asm` (annotierte Ausschnitte), `out/box_all.asm`, `out/s485_all2.asm`
- `out/s485_dec2.c` (Ghidra-Dekompilat mit korrekter Basis)

**Sonstiges**
- `out/retry_timing.txt` (Live-Wartezeiten je Kommando)
- `ghidra/proj/` (`bl_g32`, `host2`), `ghidra/scripts/*.java`
- `skripte/*.py`
- `extern/fake-name-auszug/` (Dump und Texte; nur Fakten zitiert, keine Lizenz)

## 8. Textvorschläge für `CHECKLISTE.md` (nicht eingetragen)

- **Z. 302 ff.:** `- [x] Bootloader-Parser: Adresse 0xF0 – GEKLÄRT 17.10.2026 (BOOTLOADER-HOST-BEFUND §1): adressunabhängige Service-Adresse, nur fun 0xF2 = EEPROM-Dienst (24C16, PE5/PE6; op 0 Schreiben mit 8-Byte-Schlüssel, op 1 Lesen; Antwort-Absender 0xF0). Host nutzt ihn nie. Außerdem: 0xFF nur 0x0B [01] (Loader→App), 0xFE nur A0/A1, eigene Adresse A2/A3 + Update-fun 0xF0. Nie an 0xF0 senden.`
- **Z. 308 f.:** `- [x] Längengrenze Bootloader len ≤ 0xFC – BELEGT 17.10.2026 (§2, vec_54 @0x080011ec): RX-Puffer 0xFF/Slot, 4 Byte Kopf. Zu lange Frames werden stumm verworfen (keine Antwort). Empfehlung: serial485/k2_transport beim Senden len ≤ 0xFC prüfen, Empfang 255 lassen. Update-Datenblöcke genau 0x80 Byte.`
- **Z. 342 f.:** `- [x] 0x0d op 2 Mehrfachmaske, Host-Seite – GEKLÄRT 17.10.2026 (§4): Stock-Timeout je Versuch = Slotanzahl × 300 s (4 Slots 1200 s), bis 5 Versuche, keine Gesamtfrist. 4 Slots reichen rechnerisch (≤ ~966 s). 1 Slot kann bei laufendem Preload 300 s überschreiten (bis 364 s). Nachbau-Vorschlag: max(180+200·n, 300·n), retries=0. Box-Laufzeit live weiter offen.`
- **Z. 483 („Offen aus BEFUND2“):** `Host-Burst-Timing im .so` → `~~Host-Burst-Timing im .so~~ GEKLÄRT 17.10.2026 (§3): [00 02] = Maske 0 → Timeout 0×300 s = 0 → 5 Sendungen direkt hintereinander (live 1–8 ms je Versuch). Stock sendet op 2/3 nur bei leerem Kopfsensor und im Druck nur mit force.`
- **Z. 144 (RESUME-Eintrag) ergänzen:** `Stock (BOOTLOADER-HOST-BEFUND §5): holt den Wechsel im RESUME automatisch nach (T<n>-Pfad-Fehler, Kopfsensor leer) und fährt zur Safe-Pos X225/Y345 auf Druck-Z zurück. Pi weicht bewusst ab (Druckposition vor dem Wechsel, kein Auto-Nachholen). Offen: RESUME sperren, solange der Kopfsensor leer ist?`
- **`PROTOKOLL-485.md` §6.2 „Sonderfall“ / §6.7 (Vorschlag):** „Ursache offen“ ersetzen durch „Host-Timeout = Slotanzahl × 300 s, bei Maske 0 also 0 s (BOOTLOADER-HOST-BEFUND §3)“. In §2 „Bootloader-Protokoll“: „Bedeutung von 0xF0 unklar“ → „EEPROM-Dienst fun 0xF2 (§1)“.
