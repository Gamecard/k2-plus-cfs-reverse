# CHELPER-BEFUND – chelper-Objekte 1.1.7.0 gegen OTA 1.1.3.13 (statisch, 17.10.2026)

> Nur statische Analyse. Kein Zugriff auf Drucker oder Pi.
> Neu im Projekt: `rootfs-live/usr/share/klipper/klippy/chelper/` (FW **1.1.7.0**, heute lesend vom Drucker geholt, md5 geprüft).
> Vergleichsbasis: chelper aus dem OTA-Image **V1.1.3.13** (siehe FLUSH-BEFUND §2.2).
> Werkzeuge: `readelf`/`llvm-objdump`/`llvm-dwarfdump`, pyelftools-Skripte, REA (Ghidra 12.1.4) auf `c_helper.so`.
> Adressen: `.o` = Sektions-Offset in `.text`. `c_helper.so` = ELF-vaddr (llvm-objdump). Im REA-Import gilt **+0x10000**.
> Arbeitsdateien liegen lokal (nicht im Repo).

**Belegstärken:** **B** = im Binary belegt (Symbol + Offset), **V** = Folgerung, **ANNAHME** = nicht belegt.

---

## Kurzfassung

| Frage | Ergebnis | Beleg |
|---|---|---|
| `c_helper.so` 1.1.7.0 = 1.1.3.13? | **ja, byte-identisch** (SHA256 `13127750…9221`, 46668 Byte) | B |
| `filament_change.o` gleich? | Datei verschieden, **Maschinencode und Konstanten byte-gleich**. Unterschiede nur in Build-Pfaden und im LTO-Zufallssuffix. | B |
| `get_flushing_volume` + Hilfsfunktionen identisch? | **ja** (`.text`, `.text.startup`, `.rodata`, `.rel.text`, `.symtab` gleich; LTO-GIMPLE nach Normalisierung gleich) | B |
| Live-Code im `.so` = Code im `.o`? | **ja.** Gleicher Befehlsfluss und gleicher Literal-Pool (29 Wörter). Nur eine Registerzuordnung weicht ab (LTO-Neulink). | B |
| `nachbau/flush.py` ändern? | **nein.** Kein Byte-Unterschied. Selbsttest weiterhin **ok 44/44** (60/165/193/367). | B |
| `serial_485_queue.o`, `msgblock_485.o` | ebenfalls Code-gleich zu 1.1.3.13 | B |
| `.h`-Dateien | alle drei byte-identisch zu 1.1.3.13 | B |

Die ANNAHME im FLUSH-BEFUND („1.1.7.0 byte-gleich“) ist damit **belegt**.

---

## 1. Dateivergleich 1.1.3.13 ↔ 1.1.7.0

| Datei | SHA256 1.1.3.13 | SHA256 1.1.7.0 | Größe alt/neu |
|---|---|---|---|
| `c_helper.so` | `13127750585144db…` | `13127750585144db…` (**identisch**) | 46668 / 46668 |
| `filament_change.o` | `f6e8cbc3d8c2095e…7e12` | `2c52cb9261619acd…4849` | 22652 / 22616 |
| `serial_485_queue.o` | `8c3770714fb536f6…1b80` | `ab9fcaef99622533…9b19` | 56236 / 56184 |
| `msgblock_485.o` | `17ebb9429bbfff6a…6213` | `8108d3febf485faa…8821` | 15020 / 15008 |
| `filament_change.h`, `serial_485_queue.h`, `msgblock_485.h` | – | identisch | – |

### 1.1 Warum die `.o` trotzdem verschieden sind

Die Objekte sind **„fat LTO“**: echter ARM-Code in `.text` plus GCC-LTO-Zwischencode (`.gnu.lto_*`, zlib) plus frühe Debug-Info (`.gnu.debuglto_*`). Compiler laut DWARF-Producer: `GNU C17 8.3.0 -mcpu=cortex-a7 -mfloat-abi=hard -mfpu=neon … -O2 -flto -fwhole-program`.

Sektionsweiser Vergleich `filament_change.o` (Skript `seccmp.py`, LTO-Hash im Sektionsnamen normalisiert):

| Sektion | Ergebnis |
|---|---|
| `.text` (0x468), `.text.startup` (0x48), `.rodata` (4), `.rodata.str1.4` (13), `.rel.text`, `.rel.text.startup`, `.symtab` | **gleich** |
| `.gnu.lto_.{icf,inline,jmpfuncs,opts,profile,pureconst,refs,symbol_nodes,symtab}` | gleich |
| `.gnu.lto_{RGB2HSV,DeltaHS_BBS,calc_triangle_3rd_edge,calc_flushing_volume,get_flushing_volume,main}` | verschieden, **je genau 8 Byte** (entpackt) |
| `.gnu.lto_.decls`, `.gnu.debuglto_.debug_{info,line,str}`, `.strtab`, `.shstrtab` | verschieden (Längen −4…−24 Byte) |

**Ursache der Unterschiede (B):**
- Die 8 Byte in jeder Funktions-LTO-Sektion sind das Zufallssuffix des anonymen LTO-Symbols: `filament_change.c.4e43c814` (alt) bzw. `filament_change.c.d668637c` (neu). Das ist auch der einzige Unterschied in `.symtab`/`.strtab`, Symbol Nr. 14.
- `.decls` und `.debug_*` enthalten die absoluten Build-Pfade des Herstellers. Alt ist ein „Pre-release“-Baum (`…/klipper-9aa9fc81/…`), neu ein Jenkins-Baum (`…/klipper-c427947/…`). Die Pfade sind verschieden lang, dadurch verschieben sich String-Offsets und Zeilentabellen.
- Nach Normalisierung (Suffix → `XXXXXXXX`, Pfadpräfix → `<BUILD>`) sind **alle Funktionskörper-Sektionen gleich**. Verbleibende Unterschiede in `.decls`/`.debug_info` sind nur verschobene String-/Zeilen-Offsets (Stichprobe: `debug_info`-Offsets `0x220`→`0x29e` usw.) (`ltonorm.py`).

**Folgerung (V, gut gestützt):** Gleicher Quelltext, neu gebaut auf einem anderen Build-Server. Das gilt identisch für `serial_485_queue.o` (alle 18 Funktions-LTO-Sektionen je 7 Byte Suffix-Unterschied, `.text` 0x7c8 gleich) und `msgblock_485.o` (`.text` 0x32c gleich).

Die `.o` sind auf dem Drucker ohnehin nur Build-Reste. Geladen wird `c_helper.so` (CFFI `get_ffi()`). Diese Datei ist **unverändert seit 1.1.3.13**.

---

## 2. `get_flushing_volume` in `c_helper.so`

| Symbol | `.o` (Offset) | `c_helper.so` (vaddr) | Größe |
|---|---|---|---|
| `RGB2HSV` (lokal, ohne Namen im `.so`) | `.text+0x000`, Pool `0x110–0x127` | `0x30f0`, Pool `0x3200–0x3217` | 0x128 |
| `calc_flushing_volume.constprop.0` (lokal) | `0x128`, Pool `0x3e8–0x45b` | `0x3218`, Pool `0x34e0–0x354f` | 0x334 |
| `get_flushing_volume` (global, dynsym #80) | `0x45c` | `0x37d8` | 12 |
| `m_max_flush_volume` (const float 800.0 = `0x44480000`) | `.rodata+0` | `.rodata 0x9b98` (dynsym #4) | 4 |
| `main` (Testprogramm) | `.text.startup+0` | **nicht** im `.so` (dort ist `main` @0x2460 ein anderes Symbol) | 0x48 |

**Belege (B):**
- `get_flushing_volume` ist in beiden Fassungen ein reiner Tail-Call (`sub sp,#8; add sp,#8; b calc…`). Im `.o` springt er nach `0x128`, im `.so` nach `0x3218`. REA zeigt dasselbe: `get_flushing_volume @0x137d8 → FUN_00013218`.
- **Literal-Pools wortweise gleich** (`pools.py`): RGB2HSV-Pool 6/6, calc-Pool 29/29 Wörter.
  - 0.001, 60.0 (f64), 60.0f
  - π, 1.2f→f64, 0.59, 0.3, 0.11, 0.33, 0.67, cos-Literal −0.50000005, 60.0, 800.0, 0.7f→f64, 560.0
  - 255.0f, 180.0f, 80.0f, 230.0f, 0.0f
- **Befehlsfluss gleich** (`o_norm.txt` ↔ `so_norm.txt`, 282 bzw. 280 Zeilen). Die einzige Abweichung liegt im `.so` bei `0x32a0`/`0x32b0`: Die beiden Adressen `sp+8`/`sp+16` für die `sincos`-Ergebnisse werden einmalig in r5/r4 gehalten (`add r5,sp,#8` / `add r4,sp,#16`). An `0x32d4`/`0x32d8` und `0x32f8`/`0x3300` stehen dann `mov r0,r4; mov r1,r5`. Das `.o` rechnet `add r0,sp,#16; add r1,sp,#8` jedes Mal neu (`0x1dc`/`0x1e0`, `0x200`/`0x208`).
  - Netto +2 Befehle, deshalb sind alle `vldr [pc,#…]` im calc-Teil um +8 versetzt (`0x164` `#732` ↔ `0x3254` `#740`).
  - Die adressierten Literale sind dieselben.
  - Das ist eine reine Registerzuteilung beim LTO-Linken (`-fwhole-program`). Rechnung und Rundungspfade (f32↔f64-Konvertierungen) sind unverändert.
- PLT-Ziele im `.so` passen zu den `.o`-Relokationen: `fmax`, `fmin`, `fmod`, `sincos`, `pow`, `sqrtf`, `sqrt`.

**Neues Detail (B, aus DWARF):** `calc_flushing_volume(const rgb_t from, const rgb_t to, float extra_flush_volume)`, Quellzeile 74.
- `.constprop.0` hat `extra_flush_volume` als Konstante eingefaltet: `vldr s15` → Literal `0.0f` (`.o 0x458` / `.so 0x3550`, Addition bei `0x388` bzw. `0x3480`).
- Die Reihenfolge `max(V,60) + 0.0 → int → min(·,800)` entspricht genau `nachbau/flush.py`.

---

## 3. Funktionskatalog `filament_change.o`

Quelle: `filament_change.c` (Header: Autor-Kommentar 2024-04-23). Funktionen laut DWARF-Subprogrammen (Name, Quellzeile). `static` sind alle außer `main`/`get_flushing_volume`.

| Zeile | Funktion | Signatur (DWARF) | Zweck | im Binary |
|---|---|---|---|---|
| 17 | `to_radians` | `float (float degree)` | Grad → rad (`h/180·π`) | eingebettet in calc |
| 22 | `DeltaHS_BBS` | `float (h1,s1,v1,h2,s2,v2)` mit Locals `h1_rad`,`h2_rad`,`hs_dist`… | Farbabstand im HSV-Kreis, max 1.2 | eingebettet |
| 35 | `RGB2HSV` | `void (float r,g,b, float *h,*s,*v)` (Parameter aus Registernutzung s0–s2/r0–r2, V) (Locals `Cmax`,`Cmin`,`delta`) | RGB→HSV | eigener Block `.text+0` / `.so 0x30f0` |
| 64 | `get_luminance` | `float (r,g,b)` | `0.3R+0.59G+0.11B` | eingebettet |
| 69 | `calc_triangle_3rd_edge` | `float (edge_a, edge_b, degree_ab)` | Kosinussatz, 120° | eingebettet |
| 74 | `calc_flushing_volume` | `int (const rgb_t from, const rgb_t to, float extra_flush_volume)` | Kernformel (Locals `from_lumi`,`to_lumi`,`lumi_flush`,`hs_flush`,`inter_hsv_v`,`flush_volume`) | `.constprop.0` |
| 114 | `get_flushing_volume` | `int (const rgb_t source, const rgb_t target)` | **einziger Export**, ruft calc mit extra = 0 | `0x45c` / `0x37d8` |
| 119 | `main` | `int (void)` | Test: `from=(0,0,0)`, `to=(0xE1,0x37,0xB4)`, `printf("volume = %d\n")` | nur `.o` `.text.startup` |

**Globale Konstanten (DWARF `DW_AT_const_value`, Zeilen 9–15):**

| Name | Wert | Verwendung |
|---|---|---|
| `g_min_flush_volume_from_support` | 420 | **ungenutzt** (kein Literal, kein Code) – BambuStudio-Erbe, V |
| `g_flush_volume_to_support` | 230 | ungenutzt als Variable. 230 als Literal ist der `hs_flush`-Faktor, V |
| `g_min_flush_multiplier` | 0.0f | ungenutzt |
| `g_max_flush_multiplier` | 3.0f | ungenutzt |
| `m_max_flush_volume` | 800.0f (extern sichtbar) | Obergrenze |

**Aufrufer in den Python-Erweiterungen (B, `strings`):**
- `box_wrapper.cpython-39.so`: nur `get_flushing_volume` und `rgb_t *` (über `ffi_main.new`), `ffi_lib`, `get_ffi`.
  - Einziger Aufrufpfad ist `BoxAction.get_flush_len` Z.3009 (`self.ffi_lib.get_flushing_volume(self.scv[0], self.tcv[0])`). `convert_scv`/`convert_tcv` füllen die `rgb_t`. Details im FLUSH-BEFUND §2.1.
- `motor_control_wrapper.cpython-39.so`: nur `msgblock_485_crc8`.
- `serial_485_wrapper.cpython-39.so`: `serial_485_queue_{alloc,send,pull,get_stats,exit,free}`, `struct pull_message *`.
- `serial_485_queue_extract_old` hat **keine** cdef (1.1.3.13-`__init__.py`) und keinen Python-Aufrufer.

**Fehlende Dateien:** Die 1.1.7.0-`chelper/__init__.py` (cdef-Deklarationen, `SOURCE_FILES`) liegt nicht lokal vor. Verwendet wurde die 1.1.3.13-Fassung aus dem OTA. Ob sie in 1.1.7.0 gleich ist: **ANNAHME**. Die `.h`-Dateien sind gleich, und die cdef-Texte stimmen wortgleich mit den Headern überein.

---

## 4. `msgblock_485` und `serial_485_queue` – Kurzüberblick

Dekompilat: REA/Ghidra auf `c_helper.so`, Evidence `ev_cb4fc8b4…`, `ev_20955997…`. REA-Adressen = vaddr + 0x10000.

### 4.1 Rahmen und CRC

**`msgblock_485_crc8(data, len)`** @`0x37e4`
- CRC-8, Polynom 0x07, Init 0, MSB-first, ohne Final-XOR.

**`msgblock_485_check(need_sync, buf, buf_len)`** @`0x5928`. Rückgabe: `>0` = Länge eines gültigen Frames, `0` = mehr Bytes nötig, `<0` = Anzahl zu verwerfender Bytes.
- `buf_len < 6` → 0 (warten). 6 = `F7 addr 03 status fun crc` = kleinster Frame.
- Wenn `need_sync == 0`:
  - `buf[0] != 0xF7` → verwerfen.
  - `len = buf[2] < 3` → verwerfen.
  - `buf_len <= len+2` → 0 (warten).
  - CRC über `buf[2 .. 1+len]` (len-Byte + Status + Fun + Daten) mit `buf[2+len]` vergleichen. Gleich → `len+3`.
- Verwerfen: `memchr(…, 0xF7)` ab `buf+1` (bei Head-Fehler ab `buf[0]`, der dort nicht F7 ist).
  - Treffer → `-(Offset)`, `need_sync = 0`.
  - Kein Treffer → `-buf_len`, `need_sync = 1`.
- Wenn `need_sync == 1`: Suche ab `buf[0]`. Head an Position 0 → Rückgabe 0, Sync aufgehoben, weiter beim nächsten `read`.
- Jeder Fehler wird mit Hexdump geloggt (`errorf` „buf[%d] = 0x%x“, „crc = 0x%x, msgcrc8 = 0x%x …“, „Discard bytes until next HEAD found“).

**Senden** (Timer-Callback `command_event` @`0x7ae4`):
- Python übergibt `[addr, len, status, fun, daten…]`, also `n = len+1` Byte ohne Head und ohne CRC.
- C baut daraus `buf[0] = 0xF7` + memcpy und setzt `crc8(buf+2, buf[2])` an `buf[1+n]`.
- `write(fd, buf, n+2)` in **einem** Aufruf, `bytes_write += n+2`.

### 4.2 Queue, Thread, Timeouts

**`serial_485_queue_alloc(fd, type)`** @`0x7cb0`
- `calloc(0x1090)`, Pipe, Pollreactor mit 2 fds + 1 Timer.
- Wenn `type == 'f'` (0x66): Debug-Datei, nur Schreiben – wie das Klipper-`SQT_DEBUGFILE`.
- Hintergrund-Thread `background_thread` @`0x8f34` mit `nice(-20)`.

**`serial_485_queue_send`** @`0x7140`
- Legt **genau eine** Nachricht in den Sende-Slot `+0x106c`. Eine noch nicht gesendete wird **überschrieben** (alter Zeiger geht verloren, kein Free).
- Danach Pipe-Kick (`write(pipe,1)`) → `kick_event` @`0x7a94` setzt den Timer auf „jetzt“.
- Der Timer meldet danach `PR_NEVER` (Literal `0x7bd0` = 1e16). **Kein periodischer Sendetakt.**

**`input_event`** @`0x7940`
- `read` in den 4096-Byte-Eingangspuffer (`+0x14`), dann in einer Schleife `msgblock_485_check`.
- Gültiger Frame → `bytes_read += n`, neue Nachricht in den **einen** Empfangs-Slot `+0x1070`. Ein dort noch liegender Frame wird **überschrieben** (der letzte gewinnt). Danach `pthread_cond_signal`, falls `receive_waiting`.
- Verworfene Bytes → `bytes_invalid += n`.
- EOF oder Lesefehler → `pollreactor_do_exit`.

**`serial_485_queue_pull`** @`0x77d0`
- Wartet **ohne Zeitlimit** (`pthread_cond_wait`), bis der Slot gefüllt ist oder der Reactor beendet wird (dann `len = -1`).

**`serial_485_queue_get_stats`** @`0x3980`
- `"bytes_write=%u bytes_read=%u bytes_invalid=%u"`.

**`serial_485_queue_extract_old`** @`0x3838`
- Liest die Listen `old_sent`/`old_receive` (max. 100).
- In `input_event`/`command_event` wird in diese Listen nichts eingehängt (Dekompilat) → liefert praktisch immer 0. V

**Timeouts und Retry: im C-Teil keine.**
- Kein Zeitlimit beim Empfang, keine Wiederholung, kein Adress- oder fun-Filter.
- Das alles liegt in `serial_485_wrapper.cpython-39.so`: `raw_send_wait_ack`, `_bg_thread`, Strings `retries = %d, cmd = %s`, `do not get response, retries = %d …`, `TIMEOUT_MAX`, `retries_en`.

### 4.3 Abgleich mit dem Nachbau (nur Abweichungen; kein Code geändert)

**`nachbau/serial485.py`**

| Punkt | Stock (C) | Nachbau | Bewertung |
|---|---|---|---|
| Frame, CRC-Scope, Poly | `F7 addr len status fun daten crc`, CRC 0x07/0 über `len..daten` | gleich (`build_frame`, `parse_frame`) | keine Abweichung |
| min. Länge / `len`-Grenzen | `buf_len ≥ 6`, `len ≥ 3`, `len` ≤ 255 (Byte) | `FRAME_LEN_MIN = 3`, `FRAME_LEN_MAX = 255`, `< 6` → Fehler | keine |
| Resync | ab `buf+1` zum nächsten `0xF7` | `_resync` ab Index 1 | keine |
| **Mehrere Frames in einem Lesepuffer** | **ein** Empfangs-Slot, der **letzte** gültige Frame überschreibt frühere (z. B. Herzschlag + Antwort im selben `read` → nur der spätere kommt an) | arbeitet alle Frames der Reihe nach ab und nimmt den ersten passenden (addr/fun) | **Abweichung im Verhalten.** Der Nachbau ist toleranter. Stock kann Antworten verlieren, wenn ein Folgeframe schneller eintrifft, als Python `pull` aufruft. |
| Adress-/fun-Filter | nicht im C-Teil | `expect_addr`/`expect_fun` im Bus | kommt aus dem Python-Wrapper, nicht aus C (Beleg dafür: Wrapper-Strings) |
| Timeout/Retry (`MAX_RETRIES = 4`, Timeouts je Kommando) | **nicht im C-Teil** | im Bus | durch C weder bestätigt noch widerlegt. Quelle bleibt `serial_485_wrapper` plus Live-Logs. |
| Eingangspuffer leeren vor dem Senden | C leert nichts. Ein alter Frame im Slot bleibt, bis der nächste ihn überschreibt. | `reset_input_buffer()` + `_rest_buf = b""` | ob der Python-Wrapper den Slot vorher abräumt: **offen** |
| Sende-Slot | ein Slot, eine nicht gesendete Nachricht wird überschrieben | synchrones `write` | ohne praktische Folge (Halbduplex, eine Anfrage zur Zeit) |
| Statistik | `bytes_write`/`bytes_read`/`bytes_invalid` | `sent`/`received`/`timeouts`/`retries`/`crc_errors` | nur andere Zählweise |
| Lesegröße | `read` bis 4096 | `ser.read(64)` in einer Schleife | ohne Folge |

**`pi-host/k2_transport.py`** (Weg über die MCU mit `transparent_send`, nicht über den chelper)
- Frame-Bau und -Prüfung (`_build_frame_local`, `_parse_frame_local`: `< 6`, Head, `3 ≤ len ≤ 255`, CRC über `len..daten`) stimmen mit `msgblock_485_check` überein. **Keine Abweichung.**
- `BOOTLOADER_LEN_MAX = 0xFC` für ausgehende Frames ist eine zusätzliche Schutzgrenze ohne Gegenstück im C-Teil (C begrenzt nur über `BUFFER_MAX` 512).

---

## 5. Offen

- 1.1.7.0-`chelper/__init__.py` (cdef) ist nicht lokal vorhanden; hier wurde die 1.1.3.13-Fassung verwendet. ANNAHME: unverändert.
- Ob `serial_485_wrapper` vor jedem `send` den Empfangs-Slot leert und wie er das Pull-ohne-Timeout begrenzt (eigener Thread `_bg_thread` + Python-Timeout?): nicht Gegenstand dieses Befunds.
