# Nachtrag zum Bootloader-/Host-Befund 17.10.2026: Preload-Parameter, Erst-Preload-Maske, Sector-Size

Bezug: `BOOTLOADER-HOST-BEFUND-2026-10-17.md`, Abschnitt „Offen in diesem Auftrag“, drei Punkte:
2. `get_int` in `BOX_SET_PRE_LOADING`, Maske in `cmd_first_power_on_preload`, Sub 3 → 0xE0 gegen `mcu_util_485`.
Die Befund-Datei selbst wurde nicht geändert.

Nur statische Analyse. Es wurde nichts gesendet und weder auf Drucker noch Pi zugegriffen.

**Binaries**
- `rootfs-live/usr/share/klipper/klippy/extras/box_wrapper.cpython-39.so` (Cython 0.29, armhf)
- `rootfs-live/usr/bin/mcu_util_485`, SHA256 `1b85c732a594729b68cfd569d990661979a29d4d84820de0e9622ca92d6bb9ef`

**Adressen und Zeilen**
- Adressen sind ELF-vaddr (wie `llvm-objdump`). Für `box_wrapper` gilt in Ghidra/REA +0x10000.
- `mcu_util_485` ist ET_EXEC; dort sind Ghidra- und ELF-Adresse gleich.
- „Py N“ ist die Python-Zeile aus `__pyx_lineno` (`movw r11/r10, #…`) im zugehörigen Fehlerpfad.

**Werkzeuge**
- REA/Ghidra 12.1.4: Dekompilat `mcu_util_485` `FUN_00014a08`, Evidence `ev_71374925…`; Funktionszuordnung `ev_5ef18a52…`.
- `llvm-objdump` mit eigenem Cython-Annotator (Stringtabelle, Modul-Basis `0x243128`, GOT-Auflösung über `readelf -r`).
- Float-Konstanten werden hier nicht verwendet, der REA-Float-Fehler (`vmov.f64 #imm`) spielt also keine Rolle. Alle zitierten Konstanten sind `__pyx_int` (K0/K1/K4/K15) oder Strings.

**Kennzeichnung:** **B** = am Binary belegt, **L** = live im Log gestützt, **V** = Vermutung oder Deutung, **ANNAHME** = unbelegt.

---

## 1. `BOX_SET_PRE_LOADING`: Parameternamen, Grenzen, Defaults

### 1a. Auflösung des Objekts @`0x2435c4` (B)
`0x2435c4` ist **kein String**, sondern das gecachte **1-Tupel `('NUM',)`**.
- Es entsteht in `__Pyx_InitCachedConstants`: `ldr r1,[r4,#0x498]` = `S"NUM"` (`0x2435c0`), `PyTuple_Pack(1, …)` @`0xf5e0`, `str r0,[r4,#0x49c]` → `0x2435c4` (@`0xf5e4`), mit r4 = `0x243128`.
- In `cmd_set_pre_loading` ist es das Positionsargument von `__Pyx_PyObject_Call(get_int, args=T, kwargs=dict)` (@`0x722a0`–`0x722ac`).
- Ebenso ist der erste `get_int` (@`0x720d8`, `0x2435bc`) das Tupel `('ADDR',)`, gebaut @`0xf5c0`–`0xf5cc`.
- Deshalb fand die Stringtabelle das Objekt nicht: Sie enthält nur `__Pyx_StringTabEntry`.

### 1b. Rekonstruktion `cmd_set_pre_loading` @`0x7204c` (B; Pseudocode, nicht die Quelle)

| Py | Inhalt | Beleg |
|---|---|---|
| 5296 | `addr = gcmd.get_int('ADDR', default=0, minval=1, maxval=4)` | K0/K1/K4 @`0x72098`/`0x720a8`/`0x720c0` |
| 5297 | `num = gcmd.get_int('NUM', default=0, minval=0, maxval=15)` | K0/K0/K15 @`0x72260`/`0x72270`/`0x72288` |
| 5298 | `action = gcmd.get_command_parameters().get('ACTION')` | @`0x721fc` |
| 5299 | `power_on = ….get('POWER_ON')` | @`0x723a4` |
| 5300–5303 | `ENABLE` → `box_save.preloading_poweron = True`, `DISABLE` → `False` | `PyObject_SetAttr` @`0x72444`/`0x72550`; GOT `_Py_TrueStruct`/`_Py_FalseStruct` |
| 5305 | sonst `logging.warning("[warning] the param of 'POWER_ON' is error …")`, **danach weiter** | @`0x728d8`, Rücksprung `0x72904 → 0x7255c` |
| 5306–5308 | `CLOSE` → `preloading_en = False`, op 0 | @`0x72574`, `0x725d4` |
| 5309–5311 | `OPEN` → `preloading_en = True`, op 1 | @`0x72950`, `0x729b8` |
| 5312/5314 | `RUN` → op 2, `TIGHT` → op 3 (ohne `preloading_en`) | @`0x729d4`/`0x729f4` |
| 5318 | sonst Warnung „ACTION only with …“, **return** (nichts gesendet) | @`0x72ab0`, Rückgabe @`0x72aec` |
| 5320–5323 | Bei `CLOSE`/`OPEN`: `for i in range(1,5): if box_action.check_connect(i): communication_set_pre_loading(i, op, num)` | Schleife @`0x72640`–`0x72de8` (`cmp #5`); `check_connect` = `0x243ca4` |
| 5325 | Bei `RUN`/`TIGHT`: `communication_set_pre_loading(addr, op, num)` | @`0x72bc4`–`0x72c90`; Argumente `[sp+0xc]`=addr, op, `[sp+4]`=num |

**Antwort zu Punkt 1**
- Der 2. Parameter heißt **`NUM`**: Default 0, min 0, max 15. Er ist eine **Slotmaske** (Bit i = Slot A–D) und keine Slotnummer.
  - `communication_set_pre_loading` bildet daraus `count` über die Bits (BEFUND §3), und die Maske geht unverändert als 1. Datenbyte auf den Draht.
- `ADDR`: Default 0, min 1, max 4, nur für `RUN`/`TIGHT` relevant.
  - **ANNAHME:** Wie im Upstream-Klipper (`_get_wrapper`) gilt die Bereichsprüfung nur für gesetzte Parameter. Fehlt `ADDR`, kommt also ungeprüft 0 an. Die Creality-`gcode.py` liegt nicht im Projekt.
  - Was `communication_set_pre_loading(0, …)` dann sendet, ist nicht verfolgt (ANNAHME).
- `ACTION` und `POWER_ON` sind String-Vergleiche, also case-sensitiv (`__Pyx_PyString_Equals`, op 2).
  - Fehlt `POWER_ON`, kommt bei **jedem** Aufruf die POWER_ON-Warnung. Das deckt sich mit den Live-Warnungen vor jedem `[00 02]` (BEFUND §3).
- `OPEN`/`CLOSE` ignorieren `ADDR` und senden an **alle** verbundenen Boxen, mit Maske `NUM` (Default 0).
  - Laut App-Befund wirkt die Maske bei op 0/1 nicht (Kommentar `nachbau/box.py:996-998`). Das ist also unkritisch.

### 1c. Ursache der `[00 02]`-Bursts geschlossen (B+L)
Live kommt der Aufruf aus Moonraker:
- `"BOX_SET_PRE_LOADING ADDR=1 NUM=0 ACTION=RUN"` (`13:72823`, `13:72826`)
- `"BOX_INFO_REFRESH ADDR=1 NUM=0"` (`13:72819`)

Das Makro `BOX_INFO_REFRESH` reicht `NUM={params.NUM}` an `BOX_SET_PRE_LOADING … ACTION=RUN` und `BOX_GET_RFID` weiter (`box.cfg:133`, Log `12:1577`).

Die Kette:
- Der Aufrufer schickt `NUM=0`, vermutlich als Slotindex „A“ gemeint (**V**).
- Der Host wertet `NUM` als Maske 0.
- Daraus folgen `count` 0, Timeout 0 und 5 Sendungen (BEFUND §3).

Die Burst-Ursache ist damit vollständig belegt:
- **Wire `[00 02]` stammt aus `NUM=0`** (B: Py 5297/5325).
- Der Aufrufer liefert `NUM=0` (L).

`BOX_GET_RFID` prüft `NUM` genauso als Maske 0..15. Dort sind `ADDR` und `NUM` aber **Pflicht** (kein `default` im kwargs-Dict, @`0x42274`–`0x42334`). Live scheiterte `BOX_GET_RFID ADDR=1 NUM=A` deshalb am `get_int`, als Fehler der UI-Seite (**V**).

---

## 2. Maske in `cmd_first_power_on_preload` @`0x82da8` (Aufrufstelle @`0x833cc`)

### 2a. Wer löst aus: `get_connect_state` @`0x83d6c` (B)
Es gibt nur eine Quelle von `BOX_FIRST_POWER_ON_PRELOAD`: `gcode.run_script('BOX_FIRST_POWER_ON_PRELOAD ADDRS=%d' % mask)` (Py 5063, String-Objekt @`0x245c20`, Laden @`0x86848`).

**Schleife über 4 Boxen** (`i = 0..3`, Zähler `[sp+0x3c]`, `cmp #4` @`0x86698`)
- Py 5047: `if state == 'None' or last_uuid != new_uuid`.
  - `state` = `box_state.get_Tn_data(i+1, 'state')`, der Wert **vor** dem Überschreiben (`get_Tn_data` @`0x83f7c`, `[sp+0x30]`).
  - Vergleiche: `== 'None'` @`0x856b4` (`__Pyx_PyString_Equals`); `!=` @`0x85864` (`PyObject_RichCompare` op 3, `[sp+8]` last vs. `[sp+4]` new).
- Py 5049: `mask |= 1 << i` (`PyNumber_Lshift` @`0x856d4`, `PyNumber_InPlaceOr` @`0x85a30`).
  - Danach für die neue Box: `communication_get_hardware_status`, `communication_get_filament_sensor_state(addr,'MATERIAL')`, `get_Tn_inner_data(…)`, `sync_tn_data` u. a. (@`0x85a68`–`0x8628x`).
- Py 5058–5061 (für **jede** Box, beide Zweige treffen sich bei `0x86564 → 0x8588c`):
  - `if box_save.preloading_en`: `communication_set_pre_loading(i+1, action['OPEN'])`
  - sonst: `communication_set_pre_loading(i+1, action['CLOSE'])`
  - Ohne `num` gilt der Default 15, auf dem Draht also **`[0f 01]` bzw. `[0f 00]`**. Das erklärt die 22× `[0f 01]` und 3× `[0f 00]` aus PROTOKOLL-485 §6.4 (**V**, Zuordnung je Sendung nicht geprüft).
- Py 5062, ein Ausdruck mit drei Bedingungen:
  - `mask != 0`: `__Pyx_PyInt_NeObjC` @`0x866b4`; der Helfer `0x34444` liefert bei Identität `_Py_FalseStruct`.
  - `and box_action.box_save.preloading_poweron` (@`0x86710`)
  - `and box_action.get_filament_sensor_detect() is False`: Identitätsvergleich mit GOT `_Py_FalseStruct` @`0x867f0`–`0x86800`.

**Bedeutung der Maske `ADDRS`:**
- **Bit i = Box-Adresse i+1** (nicht Slot).
- Gesetzt für Boxen, deren Tn-`state` vorher `'None'` war (erstmals seit Klipper-Start gesehen, **V**) oder deren UUID sich geändert hat (Box getauscht).
- Ausgelöst wird nur, wenn `POWER_ON=ENABLE` gesetzt wurde (`preloading_poweron`, persistiert in `box_save`, **V**) und der Kopf-Filamentsensor leer meldet.

### 2b. `cmd_first_power_on_preload` @`0x82da8` (B)

| Py | Inhalt | Beleg |
|---|---|---|
| 5066 | `action = {'CLOSE':0,'OPEN':1,'RUN':2,'TIGHT':3}` | @`0x82dc0`–`0x82e30` |
| 5067 | `addrs = gcmd.get_int('ADDRS', 0)` (positional, **ohne** min/max) | Tupel `('ADDRS', 0)` @`0x243560`, gebaut @`0xf470`–`0xf480`; Aufruf @`0x82e50`–`0x82e58` |
| 5068–5070 | `for i in range(4): addr = i+1; if addrs & (1 << i):` | `__Pyx_PyInt_AddObjC` @`0x82ed4`, `PyNumber_Lshift` @`0x82ef4`, `PyNumber_And` @`0x82f08`, Ende `cmp #4` @`0x835e0` |
| 5071 | `box_action.communication_get_filament_sensor_state(addr, 'MATERIAL')`; das Ergebnis wird verworfen | @`0x82f5c` (`0x243d08`), Aufruf @`0x8302c`; Wire `0x08 [00]` (L) |
| 5072 | `material_status = box_state.get_Tn_inner_data(addr, 'filament_sensor', subpart='material')` | @`0x8317c`–`0x831f4` |
| 5073/5074 | `if material_status == 0: continue` | `__Pyx_PyInt_EqObjC` @`0x8322c` (Helfer `0x34324`; Identität → `_Py_TrueStruct`), true → `0x835c0` |
| 5075 | `logging.info('get_connect_state: get_filament_sensor_state material_status:%s' % material_status)` | @`0x832c4`, `PyUnicode_Format` @`0x8331c` |
| 5076 | `box_action.communication_set_pre_loading(addr, action['RUN'], num=material_status)` | **@`0x833cc`**, `__Pyx_PyDict_GetItem('RUN')` @`0x833ec`, kwarg `num` @`0x83424` |
| 5077 | `box_action.communication_get_rfid(addr, num=material_status)` | @`0x83484`–`0x834ec` |
| 5078 | `box_action.communication_get_remain_len(addr, num=material_status)` | @`0x83534`–`0x83594` |

**Antwort zu Punkt 2**
- **Boxen:** `ADDRS` ist eine Box-Bitmaske (Bit 0..3 → Adresse 1..4). Bits ab 4 werden ignoriert (Schleife nur bis 3). Es gibt keine Grenzprüfung (B).
- **Kanäle und Bit-Layout:** Die Preload-Maske je Box ist **`material_status`**. Das ist der gespeicherte Wert aus `0x08 GET_FILAMENT_SENSOR_STATE [00]`, also die Bitmaske der Slots **mit Filament** (Bit 0 = A … Bit 3 = D).
  - B: Lesepfad `get_Tn_inner_data(…, 'filament_sensor', subpart='material')`.
  - L: `0x08`-Antwortdaten `07` → Log „Tn_inner_data[T1][filament_sensor][material]: 7“ (`13:80110`–`80113`) und „material_status: 7“ (`13:118932`, `12:13246`).
  - Es gibt **keine** Einschränkung auf „Slot ohne RFID“ oder „Slot nicht preloadet“. Alle Slots mit Filament werden force-preloadet.
- **Bedingungen:**
  - Box im `ADDRS`-Bit gesetzt.
  - `material_status != 0`. Eine nicht-int-Maske wäre `!= 0` und liefe weiter (ANNAHME: `None` gäbe in `communication_set_pre_loading` einen TypeError).
  - Die Schutzregeln von `communication_set_pre_loading` gelten zusätzlich (BEFUND §3: kein Senden bei Druck/Pause ohne `force`, kein op 2 bei Filament im Kopf). Den Kopfsensor prüft auch schon der Auslöser (Py 5062).
- **Gesendete Frames je Box mit Bitsatz** (Wire **V** aus den bekannten Encodern, Reihenfolge **B**):
  1. `0x08 [00]` → Maske m
  2. wenn m ≠ 0: **`0x0d [m 02]`** (Force-Preload mit RFID). Host-Timeout = popcount(m) × 300 s, bis 5 Versuche (BEFUND §3/§4).
  3. `0x02 [m]` GET_RFID
  4. `0x03 [m]` GET_REMAIN_LEN
- **Mehrfachmasken entstehen genau hier:** Bei 3 bestückten Slots wird z. B. `[07 02]` mit 900 s je Versuch gesendet.
- **Live:** Nie ausgelöst. Kein „BOX_FIRST_POWER_ON_PRELOAD“ und kein „material_status:%s“ aus Py 5075 in den Logs 12./13.10., obwohl `last_uuid:None` mehrfach vorkommt (z. B. `13:80088`, `13:116880`).
  - Passt zu `preloading_poweron` = False: Live kam nie `POWER_ON=ENABLE`, nur die Warnung (**V**).
- **Sicherheit:** hoch für Kontrollfluss und Bedingungen (B); mittel für die Wire-Bytes von 0x02/0x03 (aus bestehenden Encodern, nicht neu verfolgt).

---

## 3. Sub 3 → 0xE0 gegen `mcu_util_485` „signed ≤ 0 → Fehler“

### 3a. Befund (B, REA-Dekompilat `FUN_00014a08` + Disassembly)

**Empfang** (Thread-Handler `0x11eac`, Jump-Table @`0x11ef4` nach Zustand−3)
- Zustand 3 (Version) kopiert ab `msg+9` 25 Byte nach `entry+41` (@`0x11f1c`–`0x11f48`).
- Zustand 4 (Sector) speichert **`entry+0x28 = msg[9]`** (@`0x11fb0`/`0x11fb4`), also das erste Datenbyte der Sub-3-Antwort.

**Auswertung nach „send fw len“** (@`0x15560`–`0x15584`):
```
s = (int8) entry[+0x28]                    ldrsb r1,[r4,#40]   @0x15560
s == 0 -> "invalid sector size!", Slave übersprungen   @0x15868 ff.
s <  0 -> chunk = (s * 0xFC) & 0xFF        rsbne/lslne/uxtbne @0x15858-0x15860
s >  0 -> chunk = 0                        mov r5,#0           @0x1556c
while (n = read(fw_fd, buf, chunk)) != 0:  __read_chk           @0x15584
    send fun 0xF0, data = buf[0:n], 500 ms, 3 Versuche
```
- Das `ble` @`0x15568` führt also **nicht** pauschal in einen Fehler. Es führt in die **Chunk-Berechnung für negative Werte**. Fehler gibt es nur bei s == 0.
- Die Aussage in `PROTOKOLL-GESAMT.md` §1.7 („≤ 0 → Fehlerpfad → 1..127“) ist damit **falsch**.

### 3b. Folge für 0xE0 (B, Rechnung)
- 0xE0 → s = −32 → (−32 × 252) & 0xFF = (−8064) mod 256 = **0x80 = 128 Byte**.
- Das entspricht genau der festen Blockgröße des Bootloaders (`FUN_0800136c(…,0x80,…)`, BEFUND §1.1 / §2).
- **Kein Widerspruch und kein Bug** im Normalfall. Der Bootloader meldet 0xE0, der Host leitet daraus Datenblöcke à 128 Byte ab.
- Gleichwertige Formel: chunk = (4 × (256 − u)) mod 256 mit u = unsigned Byte. 0xE0 ist also „−32 Vier-Byte-Worte“ (**V**, Deutung der Kodierung).

### 3c. Randwerte (B, Rechnung aus dem Code; praktisch nicht gesendet)

| Sub-3-Byte | Host-Verhalten |
|---|---|
| 0x00 | „invalid sector size!“, kein Update für diesen Slave |
| 0x01–0x7F | chunk 0 → `read(…,0)` = 0 → **kein einziger Datenblock**, Schleife endet sofort („read done“) |
| 0x80, 0xC0 | chunk 0 (512 bzw. 256 mod 256), dasselbe |
| sonst 0x81–0xFF | chunk = 4·(256−u) mod 256, also 4…252 Byte |

- Wird kein Datenblock gesendet, steht der Zustand danach nicht auf 9. Folge: „update fw bin fail!“ (@`0x15634`ff., `entry[-0x12] = 5`), ohne Start-Sub 2 (**V**, Zustandswechsel im Empfangsthread nicht vollständig verfolgt).
- Ein positiver Sector-Wert wäre ein **latenter Host-Bug** (stilles Nichtstun statt Fehlermeldung). Die bekannte Bootloader-Version `cfs0_050_G32` sendet aber fest 0xE0 (BEFUND §1.1), praktisch tritt der Fall also nicht auf.
- `chunk ≤ 252` hält zusammen mit 3 Byte Kopf (Adresse, Länge, Status, fun) die Bootloader-Grenze `len ≤ 0xFC` ein; bei 0xE0 ist len = 0x83 (BEFUND §2).

**Sicherheit:** hoch (Dekompilat und Disassembly stimmen überein; die Rechnung ist deterministisch).

---

## 4. Abgleich mit dem Nachbau (nur Vorschläge, keine Code-Änderung)

| # | Stelle | Stock (diese Analyse) | Nachbau | Bewertung / Vorschlag |
|---|---|---|---|---|
| N1 | `nachbau/box.py` `communication_set_pre_loading(mask, action)` (Z. 1286 ff.) | Stock `(addr, action, num=15, force=False)`; `num` = Maske, unverändert auf dem Draht | `mask` direkt, `check_mask` 0..15 | **stimmt** (Wire-identisch). Docstring-TODO Z. 1292 („num/force-Abbild unbelegt“) kann auf „num = Slotmaske 1:1 (Py 5297/5325, 5076)“ präzisiert werden. |
| N2 | `box.py` Maske 0 bei op 2/3 (`preload_force_timeout`, Z. 362 ff.) | Maske 0 entsteht nur durch `NUM=0` aus der UI bzw. dem Makro `BOX_INFO_REFRESH` (§1c); Stock-Erstpreload sendet nie Maske 0 (`continue` bei m == 0, Py 5073) | Maske 0 wird mit 1200 s gesendet | Begründung im Docstring („belegter Request“) ergänzen: Der Request ist ein **Fehlgebrauch** (NUM als Index). Vorschlag: op 2/3 mit Maske 0 ablehnen oder nur mit ausdrücklichem Flag senden (BEFUND §3). |
| N3 | `box.py` / `pi-host` Schutzregeln | op 2/3 nur bei leerem Kopfsensor; im Druck/Pause nur mit `force` (BEFUND §3) | `box.py`: keine Prüfung (Low-Level); `pi-host do_preload` (Z. 1221 ff.): **keine** Kopfsensor- und Druckzustands-Prüfung | Vorschlag `pi-host`: In `do_preload` für op 2/3 `self._head_filament() is not False` → ablehnen; bei `print_stats` printing/paused nur mit `FORCE=1`. |
| N4 | Erst-Preload (fehlt in `box.py` und `pi-host`) | Box neu oder getauscht + `preloading_poweron` + Kopf leer → je Box `0x08 [00]` → m; m ≠ 0 → `0x0d [m 02]`, `0x02 [m]`, `0x03 [m]` | nicht vorhanden | Bewusst offen lassen oder als optionales Makro nachbilden (dann mit `preload_force_timeout(m)`, `retries=0`, Mehrfachmaske m). Live nie ausgelöst, daher **nicht dringend**. |
| N5 | Preload-Freigabe nach (Re-)Connect | Stock sendet bei **jeder** erkannten Box `0x0d [0f 01]` (preloading_en) bzw. `[0f 00]` (Py 5058–5061) | `pi-host` sendet das nicht (nur `KTWO_BOX_PRELOAD` manuell) | Vorschlag: Beim Start/Reconnect optional `0x0d [0f 01]` senden (Config-Schalter, nur mit `allow_write`). **ANNAHME:** Nach einem Box-Reset ist die Freigabe aus, ohne diese Sendung bliebe Auto-Preload also womöglich aus. Live prüfen (0x0a nach Box-Neustart ohne Host-[0f 01]). |
| N6 | `pi-host` `box_wait_timeout` 200 s (Z. 318) | Erstpreload- und Mehrslot-Läufe bis count × 300 s | Pi sendet nur Einzelslots | Für `KTWO_BOX_PRELOAD` unkritisch (Timeout kommt aus `preload_force_timeout`). Für das Warten auf IDLE nach einem Box-seitigen Auto-Preload gilt weiter BEFUND §4 (1-Slot-Worst-Case 364 s > 200 s) → Vorschlag ≥ 380 s. |
| N7 | `nachbau/motor_mini.py:109` `SUB_GET_SECTOR = 0x03` | chunk = (int8(b) × 252) & 0xFF, 0xE0 → 128 | nur die Konstante, kein Update-Pfad | Für einen künftigen Update-Pfad: Sector-Byte als int8 auswerten; **chunk == 0 (b ∈ 0x00–0x7F, 0x80, 0xC0) → Abbruch mit Fehler** statt still nichts zu senden; Blöcke fest 0x80 (BEFUND §2). |

**Textvorschlag `PROTOKOLL-GESAMT.md` §1.7 (nicht eingetragen):**
> „Sector-Size = signed Byte (`ldrsb [r4,#40]` @0x15560): 0 → ‚invalid sector size!‘; < 0 → Chunk = (s·252)&0xFF (0xE0 → 128 = Bootloader-Blockgröße); > 0 → Chunk 0 → keine Daten (latenter Host-Bug). NACHTRAG 17.10.2026.“

**Textvorschlag `PROTOKOLL-485.md` §6.4, Zeile `[00 02]` (nicht eingetragen):**
> „Ursache: G-Code `BOX_SET_PRE_LOADING … NUM=0 ACTION=RUN` (UI/Makro `BOX_INFO_REFRESH`; `NUM` ist Slotmaske, Default 0) → Maske 0 → Timeout 0 (BOOTLOADER-HOST-NACHTRAG §1c).“

---

## 5. Belegqualität und Grenzen

**Belegt (B), hoch**
- Tupel `('NUM',)` und `('ADDR',)`; alle Defaults und Grenzen.
- Kontrollfluss von `cmd_set_pre_loading`, `cmd_first_power_on_preload` und dem Auslöser in `get_connect_state`.
- Sector-Chunk-Formel in `mcu_util_485`.

**Live gestützt (L)**
- `NUM=0` vom Aufrufer.
- `material`-Wert 7 = Maske aus `0x08`.
- Erstpreload live nie ausgelöst.

**Vermutet (V)**
- `state == 'None'` bedeutet „seit Start nicht gesehen“.
- `preloading_poweron` war live False.
- Zustandsfolge nach einem Chunk-0-Lauf.
- Wire-Bytes von 0x02/0x03 im Erstpreload (aus bestehenden Encodern).

**ANNAHME**
- `get_int` prüft beim Default keinen Bereich (Creality-`gcode.py` nicht im Projekt).
- Verhalten von `communication_set_pre_loading(addr=0)`.
- `None`-Maske führt zum TypeError.
- Preload-Freigabe nach Box-Reset (N5).

**Nicht verfolgt**
- Der genaue Schreibpfad von `filament_sensor/material` in `process_msg` (@`0x1db370` ff.). Der Annotator benennt die Offsets dort falsch. Live ist der Wert über das Log belegt.

**Methodik-Hinweis**
- Annotator-Labels bei kleinen Offsets (`[rX,#0x4]`, `#0x8`, `#0xc`, z. B. „range“, „chr“, „BoxAction.rfid_check“) sind Fehlzuordnungen auf Objekt-Felder (`ob_type`, `im_self`, `im_func`) und wurden ignoriert.
- Namen nur aus Basis `0x243128` + Offset und Stringtabelle zitiert.

**Arbeitsdateien** (lokal, nicht im Repo):
- annotierte ASM-Ausschnitte der genannten Funktionen
- `nachtrag_rekonstruktion.txt` (Pseudocode mit Adressen und Zeilen)
- Kopie und Disassembly von `mcu_util_485`
