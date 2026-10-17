# FLUSH-BEFUND – Spülen und Düsenreinigung beim CFS-Farbwechsel (statisch, 17.10.2026)

> Nur statische Analyse plus Abgleich mit vorhandenen Live-Logs. Kein Zugriff auf Drucker oder Pi.
> Code in `pi-host/` ist **nicht** geändert. Rechen-Nachbau: `nachbau/flush.py` (Selbsttest **ok 44/44**).
> Arbeitsdateien liegen lokal (nicht im Repo).

### Nachtrag: 1.1.7.0-Abgleich (17.10.2026)

Der chelper liegt jetzt auch vom Drucker vor (FW 1.1.7.0, `rootfs-live/usr/share/klipper/klippy/chelper/`). Details: `CHELPER-BEFUND-2026-10-17.md`.

- **`c_helper.so` ist byte-identisch** mit der Fassung aus OTA 1.1.3.13 (SHA256 `13127750…9221`). Der live geladene Code ist also derselbe.
- **`filament_change.o`**: Die Datei unterscheidet sich (anderer Build-Pfad, anderes LTO-Zufallssuffix). Diese Teile sind aber **byte-gleich**:
  - `.text`, `.text.startup`, `.rodata`, `.rel.text` und `.symtab`
  - alle Funktions-LTO-Sektionen (nach Normalisierung)
- `get_flushing_volume` im `.so` (@`0x37d8` → calc @`0x3218`) hat denselben Befehlsfluss und denselben Literal-Pool (29/29 Wörter) wie das `.o`. Einzige Abweichung: eine LTO-Registerzuteilung (+2 Befehle).
- Die bisherige **ANNAHME „1.1.7.0 byte-gleich“ ist damit belegt (B).** `nachbau/flush.py` bleibt unverändert, Selbsttest weiter **ok 44/44** (60/165/193/367).
- Neu aus DWARF: `calc_flushing_volume(from, to, float extra_flush_volume)`. Der Export ruft sie mit `extra = 0.0f` auf (Literal @`0x458`).

**Ziel-Binary:** `rootfs-live/usr/share/klipper/klippy/extras/box_wrapper.cpython-39.so`
(Cython 0.29.21, armhf, Quellname `klippy/extras/box_wrapper.py`).

**Belegstärken**
- **B** = im Disassembly oder Dekompilat belegt (Adresse und/oder Python-Zeile)
- **B+L** = zusätzlich durch Live-Log bestätigt
- **V** = Folgerung oder Deutung
- **ANNAHME** = nicht belegt

**Konventionen**
- `Z.<n>` = `__pyx_lineno` der Originalquelle. Er stammt aus den Fehlerpfaden; eine einzelne Operation kann um ±1–2 Zeilen verschoben sein.
- `@0x…` = ELF-vaddr. Das gilt für `llvm-objdump` und für das eigene Ghidra-Projekt mit Imagebase 0. Im REA-Standardimport gilt **+0x10000**.
- **Float-Konstanten** sind gegen die Init-Disassembly geprüft: `vmov.f64 #imm` bzw. `vldr` + Literal, z. B. 2.4 @`0x1af20`, 0.4 @`0x126f90`, 2.4 @`0x12a0d0`. Die verwendete Tabelle löst `vmov.f64` auf.
- **Live-Logs:** `live-logs-2026-10-15/klippy.log.2026-10-1x`. Die Flush-Zeilen stammen aus dem FW-Stand **1.1.7.0**: Versionswechsel im Log vom 12.10. ab Logzeile 10369, alle `flush_velocity`-Zeilen liegen danach.

---

## Kurzfassung

| Thema | Stock-Verhalten (1.1.7.0) | Beleg |
|---|---|---|
| Spülmenge | **farbabhängig.** C-Funktion `get_flushing_volume(src_rgb, dst_rgb)` → 60…800 mm³. Spüllänge = `(Volumen + nozzle_volume 183) / 2.4 × flush_multiplier 1`. Mindestens `box_first + box_need` (90+70 = 160 mm). Bei anderem Material ×2, mindestens 370 mm. | B+L |
| Quellfarbe | gespeicherte Farbe des Materials in der Düse: `box_action.remain_material.color`. Nach jedem erfolgreichen Spülen wird sie per `remain_material.save` neu gesetzt. | B |
| Abschnitte | `cal_flush_list`: erster Abschnitt 90, dann 70er-Abschnitte, Rest verteilt (max. 100) | B+L (4 Live-Listen) |
| Geschwindigkeit | `max_volumetric_speed / 2.4 × 60`, gedeckelt auf **360 mm/min**, skaliert mit Düse/0.4. Je Abschnitt 20 % mit v und 80 % mit v × 1.2 (`flush_velocity_factor`). Ausnahme: letzter Abschnitt, dort ×1. | B+L |
| Temperatur | `flush_temp = max(T_alt, T_neu)` aus `material_database.json` (`kvParam.nozzle_temperature`). Wenn `T_alt ≥ T_neu`: Spülung auf `max(maxTemp_alt, maxTemp_neu)` (erst warten bis max−5, dann `M104 max`). Letzter Abschnitt `M104 flush_temp`, am Ende `M104 S<T_neu>`. | B+L |
| Je Abschnitt | Extrusion → `blow` (fan0 5 s voll) → `nozzle_clean(circle=False)`. Nach dem letzten Abschnitt `nozzle_clean()` mit Silikon-Kreisen. | B+L |
| Düse verstopft | Summe der CFS-Messrad-Differenzen über alle Abschnitte. Ist `|Summe| < |diff_length|` (10 mm), folgt **key845** und `flush_err`/`macro_flush_err`. | B+L (2× live am 12.10.) |
| Im Druck | zuerst Slicer-Matrix `flush_volumes_matrix` aus der G-Code-Datei (`virtual_sdcard.get_gcode_flush_para`), ohne nozzle_volume. Danach dieselbe Nachbearbeitung. | B |
| k2_box heute | feste `[90, 70]`, eine Geschwindigkeit, `max(Ziel, 220)`, volles Abstreifen nach **jedem** Abschnitt, kein Blasen, keine Verstopfungsprüfung | Vergleich §8 |

---

## 1. Aufrufkette

```
M8200 F → CR_BOX_FLUSH TNN=<tnn>        cmd_CR_BOX_FLUSH @0x4df84
BOX_MATERIAL_CHANGE_FLUSH [LAST_TNN] [TNN]   cmd_material_change_flush @0xb83e4 (Z.4333–4355)
  └ MCMBW.material_change_flush(last_tnn, tnn)            @0xab728  Z.4494–4517
      ├ M106 P0 S50 ; M400                                  Z.4495/4496
      ├ flush_len = self.get_flush_len(last_tnn, tnn)       Z.4498  → §2
      ├ velocity, percent = box_action.get_flush_velocity(last_tnn, tnn)   Z.4500 → §4
      ├ last_temp, next_temp, flush_temp = box_action.get_flush_temp(…)    Z.4502 → §5
      ├ last_max, next_max, flush_max = box_action.get_flush_max_temp(…)   Z.4504
      ├ box_action.quickly_wait_heating(flush_temp)         Z.4506
      ├ if last_temp >= next_temp:  ret = material_flush(flush_len, velocity, flush_temp, flush_max, percent, tnn)  Z.4507/4508
      │ else:                       ret = material_flush(flush_len, velocity, flush_temp, flush_temp, percent, tnn)  Z.4510
      ├ if ret is False: return False                       (Vergleich mit _Py_FalseStruct, GOT @0xac7ac)
      ├ Tnn_content = box_state.get_Tnn_content(tnn)
      ├ box_action.remain_material.save({'color': get_Tn_data(addr,'color_value',num),
      │                                  'type':  get_Tn_data(addr,'material_type',num)})   Z.4513/4514
      └ M104 S%d % next_temp ; return True                  Z.4516
BoxAction.material_flush(length, velocity, temp, max_temp, percent, tnn)  @0xec74c Z.3759–3771
  ├ ret = self.Tn_Extrude(...)                               Z.3759 → §6
  ├ ret is False → box_state.clear_e_err(); return False     Z.3760/3761
  └ self.move_to_safe_pos(); self.box_save.restore_fan(); return True   Z.3768/3770
```

**Fehlerweg (B).** Bei `False` ruft `cmd_material_change_flush` die Funktionen `set_cool_temp` und `recode_err(…, 'macro_flush_err')` auf (Z.4352). Der T<n>-Weg geht über `cmd_CR_BOX_FLUSH` und endet in `recode_err(…, 'cr_break_flush_err')`.

**Live-Beleg (B+L):** `klippy.log.2026-10-12` 00:26:52 `key845` → `error: macro_flush_err`.

---

## 2. Spülmenge `MultiColorMeterialBoxWrapper.get_flush_len(last_tnn, current_tnn)` @0xae89c, Z.4423–4491

Variablennamen (Tupel @0x2441e8): `ignore_ops, coefficient, flush_len, current_tnn_content, last_tnn_content, tmp, hex_pairs, scv, tcv, j, pair`.

### Ablauf

```
4423  ignore_ops = ['-1', 'none', 'unknown'];  coefficient = 1
4425  if last_tnn is None and box_action.remain_material.color is None:
4426      logging.info("last_tnn is none and save color is none")
4427      return box_action.boxcfg.Tn_extrude                  # 140 (roh)
4429  if box_action.print_stats.state == "printing" and last_tnn is not None:
4430      logging.info("Try to get the resize length from the file")
4431      flush_len = self.get_flush_length_from_gcode(last_tnn, current_tnn)      → §3
4432/33 current/last_tnn_content = box_state.get_Tnn_content(...)
          if flush_len is not None: → Nachbearbeitung (Z.4438–4450, wie unten)
4453  current_tnn_content = box_state.get_Tnn_content(current_tnn)
4456  color = box_action.remain_material.color
4457  if color is None or color in ignore_ops:
4458      logging.info("scv(last_tnn: %s) is ignore" % last_tnn); 4459 return Tn_extrude
4461  hex_pairs = [color[j:j+2] for j in range(1, len(color), 2)]    # '0RRGGBB' → RR,GG,BB
4462  scv = [int(pair, 16) for pair in hex_pairs]
4464  color = box_state.get_Tn_data(addr, 'color_value', num)  (des Ziels)
4466  ignoriert → "tcv(last_tnn: %s) is ignore" % current_tnn; 4467 return Tn_extrude
4468/69 tcv = … wie oben
4470  flush_len = box_action.get_flush_len(scv, tcv)                → §2.1
4471  if flush_len is None: 4472 warning "[warning] do not get flush_len …"; 4473 return Tn_extrude
4477  if self.is_different_type_material(last_tnn, current_tnn):
4479      coefficient = 2                                           (K 2 @L4480)
4480      gcode.respond_info("different material type")
4481      if flush_len * coefficient > boxcfg.diff_material_type_min_flush_len:   (RichCompare op GT)
4482/83      respond "flush_len: %f"; return flush_len * coefficient
4485      logging.info("different material type min flush len")
4486      return boxcfg.diff_material_type_min_flush_len           # 370
4487  gcode.respond_info("flush_len: %f" % (flush_len * coefficient))
4489  if flush_len * coefficient < box_first_clean_length + box_need_clean_length:   (op LT)
4490      return box_first_clean_length + box_need_clean_length     # 90+70 = 160
4491  return flush_len * coefficient
```

**Belegt (B):**
- Der Startwert `coefficient = 1` liegt @L4425 (K1).
- `coefficient = 2` liegt im Zweig „anders“ (`uStack_74 = K2`).
- Die Vergleichsoperatoren sind aus `PyObject_RichCompare(…, op)` gelesen.

**Zeile 4429:** `PyUnicode_Equals(state, "printing")` und `param_2 is None` → Farbzweig.

**Fallback (V):** In vier Fällen gibt die Funktion `Tn_extrude` **roh** zurück (140), ohne Mindestlänge. `cal_flush_list(140)` macht daraus `[90, 70]` (§6.1).

### 2.1 `BoxAction.get_flush_len(scv, tcv)` @0x12528c, Z.3006–3013

```
3006  if self.convert_scv(scv) is False: return None      # convert_*: self.scv.r/g/b = cv[0..2] (Z.2742–2746, try/except → False)
3008  if self.convert_tcv(tcv) is False: return None
3009  flush_volume = self.ffi_lib.get_flushing_volume(self.scv[0], self.tcv[0])
3010  gcode.respond_info("flush_volume: %d, nozzle_volume: %d" % (flush_volume, boxcfg.nozzle_volume))
3011  flush_len = self.material_volume_to_length(flush_volume + boxcfg.nozzle_volume) * boxcfg.flush_multiplier
3012  gcode.respond_info("flush_len: %f" % flush_len)
3013  return flush_len
```

- `material_volume_to_length(volume) = volume / 2.4` (Z.3003, @0x1c6748: Konstante 2.4, Hilfsfunktion `vdiv.f64` @0x1c668c) – **B**.
- 2.4 mm² entspricht etwa dem Querschnitt von 1,75-mm-Filament (π·0,875² = 2,405) – V.
- `ffi_lib` ist die chelper-CFFI-Bibliothek (`get_ffi`/`ffi_main`). Die cdef aus 1.1.3.13 lautet `int get_flushing_volume(const rgb_t source, const rgb_t target)`, `rgb_t = {uint8 r,g,b}`.

**Live (B+L)** – alle Werte mit `nozzle_volume 183` und `flush_multiplier 1`:

| Logzeile | flush_volume | flush_len |
|---|---|---|
| 12:13649 | 60 | 101.25 |
| 12:144029 / 13:84040 | 165 | 145.0 |
| 12:86970 | 193 | 156.666667 |
| 12:138724 | 367 | 229.166667 |

Jeder Wert ist exakt `(V + 183) / 2.4`.

### 2.2 Farbvolumen `get_flushing_volume` (C, chelper `filament_change.o`)

**Quelle**
- Das 1.1.7.0-Rootfs im Projekt enthält kein `chelper/`.
- Die Funktion stammt deshalb aus dem offiziellen OTA-Image **V1.1.3.13** desselben Druckertyps (T113, swupdate-cpio → squashfs → `usr/share/klipper/klippy/chelper/`).
- Dort liegt sie als Objekt mit Symbolen: `filament_change.o`, SHA256 `f6e8cbc3…7e12`, plus `filament_change.h`.
- LTO-Abschnitte heißen `RGB2HSV`, `DeltaHS_BBS`, `calc_triangle_3rd_edge`, `calc_flushing_volume`. „BBS“ deutet auf die Spülvolumen-Rechnung aus BambuStudio/OrcaSlicer – V.

**Gegenprobe**
- REA (Ghidra 12.1.4, `procedure_pseudo_code calc_flushing_volume.constprop.0`) und `llvm-objdump` stimmen überein.
- Alle Konstanten stammen aus den Literal-Pools des `.o`.

**Algorithmus** (B, Adressen im `.o`):
1. `r,g,b /= 255` (f32, 255.0 @0x448) → `RGB2HSV` (@0x0).
   - `max`/`min` über `fmax`/`fmin`, `Δ = max − min`.
   - Bei `|Δ| < 0.001` gilt `h = 0`.
   - `r == max` → `h = fmod((g−b)/Δ, 6)·60`.
   - `g == max` → `h = ((b−r)/Δ + 2)·60`.
   - sonst `h = ((r−g)/Δ + 4)·60`.
   - `s = Δ/max` (bei `max < 0.001` gilt `s = 0`), `v = max`.
2. Farbabstand (DeltaHS) im HSV-Kreis:
   - `dx = cos(h1)·s1·v1 − cos(h2)·s2·v2`, `dy` analog mit `sin`.
   - `hs = min(1.2f, √(dx²+dy²))`.
   - Grad→rad über `h/180·π` (π @0x3e8).
3. Helligkeit `L = 0.3R + 0.59G + 0.11B` (@0x400/0x3f8/0x408).
   - `L_src ≤ L_dst` (dunkel → hell): `lumi = (L_dst − L_src)^0.7f · 560` (0.7 @0x438, 560 @0x440).
   - sonst: `lumi = (L_src − L_dst)·80` und `hs = min(0.33·v_src + 0.67·v_dst, hs)` (@0x410/0x418).
4. `a = 230·hs` (@0x454), `b = lumi`.
   - Dreieck mit 120°: `V = √(a² + b² − 2ab·cos120°)` (cos-Literal @0x420 = −0.50000005).
5. `V = max(V, 60)` (@0x428), `int()` (Abschneiden), `min(·, 800)` (@0x430; `m_max_flush_volume` = 800.0f).

**Übertragbarkeit auf 1.1.7.0 (B+L):**
- Der Nachbau trifft die vier Live-Paare exakt: 60, 165, 193, 367 (Selbsttest).
- Die Farbpaare stammen aus `Tn_data[…][color_value]` und den zuvor geladenen Farben.
- Byte-Gleichheit mit dem 1.1.7.0-chelper ist **nicht geprüft** (ANNAHME).
- `main()` im `.o` rechnet einen Testvektor: Schwarz → (0xE1, 0x37, 0xB4). Nachbau-Ergebnis: 427, live nicht geprüft.

---

## 3. Spülmenge im Druck: `get_flush_length_from_gcode(last_tnn, current_tnn)` @0xb47d0, Z.4357–4406

1. Log `"flush; last_tnn: %s, current_tnn: %s"`.
2. `flush_para = printer.lookup_object('virtual_sdcard').get_gcode_flush_para()`.
   - Fehler → `"Error in getting flushing parameters"` → `None`.
3. `flush_multiplier = flush_para['flush_multiplier']`; ist er `None`, gilt 1 (Z.4362/4363) – **B**.
4. `flush_volumes_matrix` leer → `"gcode has no flush parameters"`.
5. Ist `√len` keine ganze Zahl → `"flushing parameter format is abnormal"` → `None`.
6. `np.array(...).reshape(n, n)`.
7. Slicer-Index aus `box_state.Tn_map`/`Tnn_map` – V, Zuordnung nicht im Detail aufgelöst.
   - Ist der Index ≥ n → `"The box address does not comply with the rules …"` → `None`.
8. `flush_volume = flush_array[i_last][i_cur]` (Z.4403).
9. `flush_len = box_action.material_volume_to_length(flush_volume) * flush_multiplier` (Z.4404).
   - **Ohne** `nozzle_volume`, mit dem **Slicer**-Multiplikator – **B**.
10. Danach folgt in `get_flush_len` dieselbe Nachbearbeitung wie in §2: ×2 bzw. Minimum 370, sonst Minimum 160.

**Grenzen**
- Upstream-Klipper hat **kein** `virtual_sdcard.get_gcode_flush_para()` – Creality-Erweiterung, V.
- Live gibt es nur Farbzweig-Belege. Die Zeile `flush; last_tnn` kommt in den Logs nicht vor.

---

## 4. Geschwindigkeit `BoxAction.get_flush_velocity(last_tnn, current_tnn)` @0x126e9c, Z.2942–2981

```
2943 baseline_nozzle = getattr(boxcfg, 'baseline_nozzle_diameter', 0.4) or 0.4       (0.4 @0x126f90)
2944 nozzle_diameter = self._get_nozzle_diameter(baseline_nozzle)     # extruder.__nozzle_diameter, sonst Fallback (Z.2987–2997)
2952 next_speed = self.get_material_max_extrusion_speed(current_tnn)  # = filament_max_volumetric_speed / 2.4  (vdiv @0x129fcc, 2.4 @0x12a0d0)
2954   → log "next material max extrusion speed: %.2f mm/s"
2958 sonst try: next_speed = boxcfg.Tn_extrude_velocity[0] / 60;  2960 except: "set extrusion speed to 360 as default", 6
2963 base_speed = 360 if 360 < next_speed*60 else next_speed*60    (mov r0,#360 @0x1273fc; RichCompare op LT @0x127420)
2967 scale = 1.0; baseline_nozzle <= 0 → warning "baseline_nozzle invalid, fallback scale=1"
2970 sonst scale = nozzle_diameter / baseline_nozzle
2978 velocity = [base_speed * scale]; percent = [1]
2979 log "flush_velocity: next=… base=… nozzle=…(base=…) scale=… feed=…(mm/min)"
2981 return velocity, percent
```

**Hinweise**
- Der Parameter `last_tnn` ist ungenutzt (vom Compiler entfernt, `r1` = `current_tnn`).
- Die Obergrenze 360 ist ein **festes Literal**, nicht `Tn_extrude_velocity`.

**Live (B+L):**

| Log | max_volumetric_speed | next (mm/s) | base = feed (mm/min) |
|---|---|---|---|
| Hyper PLA | 23 | 9.58 | 360 |
| Generic PLA | 14 | 5.83 | 350 (Liste `[350.00000000000006]`) |

- Im Log 05.10. (FW 1.1.4.12) steht `velocity: [575.0]`, also noch ohne Deckel. Ältere FW, V.

**Material-ID (B+L)**
- Aus `Tn_data material_type` wird die DB-ID mit `type[1:]`: `101001` → `01001` (Hyper PLA: 23 mm³/s, 220 °C, maxTemp 240); `000001` → `00001` (Generic PLA: 14 mm³/s).
- DB-Datei: `material_database.json`, Pfad aus dem Modul-Global `material_database`, `result.list[i].base.id`.
- Stock-Default im Projekt: `rootfs-live/etc/sysConfig/defData/material/material_database.json`. Felder: `base.id`, `base.maxTemp`, `kvParam.nozzle_temperature`, `kvParam.filament_max_volumetric_speed`.

---

## 5. Temperaturen

**`get_flush_temp`** (@0x12bc74, Z.2886–2892) – **B**:
- `last_temp = get_material_target_temp(last_tnn, type=remain_material.type)`
- `next_temp = get_material_target_temp(current_tnn)`
- `flush_temp = max(last_temp, next_temp)` (inline: RichCompare GT @0x12bdcc)
- Ausgabe `respond_info "flush_temp: %d"`

**`get_flush_max_temp`** (@0x12b48c, Z.2893–2899): analog mit `get_material_target_max_temp`.

**`get_material_target_temp`** (@0x131fb0, Z.2777–2809) – B, Zeilen ±2:
- `int(list[i].kvParam.get('nozzle_temperature', 300))`
- Fallback jeweils `boxcfg.Tn_extrude_temp` (220): bei `tnn is None`, bei `material_type == "None"` und bei Datei-/JSON-Fehler.

**`get_material_target_max_temp`** (@0x12f0c0, Z.2814–2846): `int(base.get('maxTemp', 300))`, gleiche Fallbacks.

**Max-Temperatur-Spülung**
- Nur wenn `last_temp >= next_temp`, also beim Wechsel heiß → kalt oder bei gleicher Temperatur (Z.4507).
- Sonst bekommt `material_flush` als `max_temp` nur `flush_temp`.

**`quickly_wait_heating(temp)`** (@0x134ea0, Z.2761–2775) – V im Detail:
- `M104 S%.2f`.
- Schleife über `smoothed_temp` gegen das Ziel mit ±10-K-Fenster (K10) und `can_extrude`.
- `reactor.pause(+0.5 s)`, dazwischen `G91 / G0 E0.01 F60 / G90`.

**Live (B+L, 12.10. 00:26)**
- `target_temp:235` (= 240 − 5), `set target max temp` (240).
- Nach dem ersten Abschnitt: `restore target temp` (220).

---

## 6. Spülablauf `BoxAction.Tn_Extrude(extrude, velocity, temp, max_temp, percent, tnn)` @0x11c060, Z.3051–3146

```
3052 self.quickly_wait_heating(temp);  3053 G91
3054 if tnn is not None: Tnn_content = box_state.get_Tnn_content(tnn)
3056 log "extrude = %s, velocity: %s, temp: %s, percent: %s, tnn: %s"
3057 percent None → coefficient = 1;  3058 len(percent)==1 → coefficient = percent[0];  sonst Mehrfach-Pfad (3107–3145, s. u.)
3059 if tnn is None or extrude < 0:  3061 ret = check_and_extrude(extrude*coefficient, velocity[0]); 3063 Fehler → "extrude err", False
--- Spülpfad ---
3067 communication_measuring_wheel(Tnn_content['addr'], 'GET');   3070 last = get_Tn_data(addr, 'measuring_wheel')
3072 length = [];  3073 self.cal_flush_list(extrude*coefficient, length);  3074 log "length: %s"
3075 for i in range(len(length)):
3079/80   if i != 0 and i == len(length)-1:
              ret = check_flush_temp_and_extrude(length[i], boxcfg.max_temp_flush_times, temp, max_temp, velocity, max_temp_flush=True)
3082      else: ret = check_flush_temp_and_extrude(length[i], i, temp, max_temp, velocity, max_temp_flush=True)
          ret is False → return False
3087/90   communication_measuring_wheel(...,'GET'); current = get_Tn_data(addr,'measuring_wheel')
3091      diff += current - last;   last = current
3092      log "last_measuring_wheel = %s, current_measuring_wheel = %s, diff = %s"
3094      self.blow()
3095/96   if box_save.error is not None: warning "error: %s" → return False
3098–3101 if i == len(length)-1: self.nozzle_clean()   else: self.nozzle_clean(circle=False)
3102 if abs(diff) < abs(boxcfg.diff_length):           # nach der Schleife (RichCompare op LT)
3104     error(key_nozzle_blocked_err …) → False      ("!! {"code":"key845", "msg":"the nozzle is blocked"}")
     return True
```

**Belege**
- `max_temp_flush=True` = `_Py_TrueStruct` (GOT @0x11d134).
- `circle=False` = `_Py_FalseStruct` (@0x11f1bc).
- Die Messrad-Prüfung liegt **nach** der `for`-Schleife (B, Dekompilat).

**Live (B+L, 12.10. 00:26:02–00:26:52):**
1. `length: [90, 70]`
2. `18@360`, `72@432`, Messrad-Diff −0.0016, `temp_pos:2.83` (Wischen ohne Kreis)
3. `restore target temp`, `14@360`, `56@360`, `temp_pos:2.42`
4. `key845`, `error: macro_flush_err`

Das Messrad meldete kaum Bewegung. Ursache am Gerät offen (V: Puffer liefert, Messrad-Auflösung oder Rad hängt).

**Mehrfach-Percent-Pfad (Z.3107–3145, nur Struktur, V)**
- `for i in range(len(percent)): check_and_extrude(extrude*percent[i], velocity[i])`.
- Fehler → `blow`, `nozzle_clean`, `go_to_extrude_pos`.
- Mit Messrad-Prüfung gegen `buffer_empty_len × 2` (Z.3119) und `diff_length` (Z.3128).
- Die Spülkette nutzt diesen Pfad nicht (`percent = [1]`).

### 6.1 `BoxAction.cal_flush_list(src_length, cal_length)` @0x123bf0, Z.3015–3049

Variablen (Tupel @0x243fa4): `src_length, cal_length, remain_length, count, remain_everage, i`.

Die Funktion füllt die übergebene Liste und gibt `None` zurück (GOT @0x124bfc).

```
3016 if src <= need_max:   3017–3022 append(src if src >= first else first); return
3025 append(first); 3026 remain = src - first; count = 1
3028 while remain >= need: 3030 append(need); 3031 remain -= need; 3032 count += 1
3033 if count == 1 and remain > 0: 3035 append(need)
3036 elif count >= 2 and remain > 0:
3039     avg = (remain - (need_max - first)) / (count - 1)        (TrueDivide)
         if remain <= need_max - need: 3041 cal[count-1] += remain
3042     elif avg > need_max - need:   3044 append(need)
         else: 3047 cal[0] = need_max; 3048/49 for i in range(count-1): cal[i+1] += avg
```

**Live (B+L, alle Fälle aus den Logs exakt):**

| Eingabe | Ergebnis |
|---|---|
| 160 | `[90, 70]` |
| 229.17 | `[90, 70, 70]` |
| 247.08 | `[90, 70, 87.08]` |
| 309.17 | `[90, 70, 70, 79.17]` |

### 6.2 `check_flush_temp_and_extrude(length, index, temp, max_temp, velocity, max_temp_flush, …)` @0x193d50, Z.1336–1359

```
1337 velocity_factor = 1
1338 if index == 0 and max_temp_flush:
1340     self.quickly_wait_heating(max_temp - 5);  1341 M104 S%.2f % max_temp;  1342 log "set target max temp"
1343 if max_temp_flush:
1344     if index < boxcfg.max_temp_flush_times:   1345 velocity_factor = boxcfg.flush_velocity_factor   # 1.2
1346     elif index == boxcfg.max_temp_flush_times: velocity_factor = 1; 1348 M104 S%.2f % temp; 1349 "restore target temp"
1352     log "restore velocity_factor=" + str(velocity_factor)
1353     for portion, factor in ((0.2, 1), (0.8, velocity_factor)):         # Tupel-Konstante (0.2, 1), K 0.8
1354/55      log "restore velocity=" + str(velocity[0]*factor)
             ret = self.check_speed_and_extrude(length*portion, velocity[0]*factor)   → False → return False
     return True
1359 else: return self.check_and_extrude(length, velocity[0])
```

**Folgen (B+L)**
- Bei `max_temp_flush_times = 3` laufen höchstens die Abschnitte 0–2 mit ×1.2 und Max-Temperatur.
- Der **letzte** Abschnitt bekommt immer `index = 3` (Z.3080), also `M104 temp` und ×1.
- Die Live-Folge `18@360` / `72@432` / `14@360` / `56@360` passt exakt.

**Extrusion (B)** – `check_and_extrude` / `check_speed_and_extrude` (Z.1311–1332):
- `G91`, `G0 E%.2f F%.2f`, `G90`, `M400`.
- Danach Prüfung `box_state.extrude_err`.
- Log `"check_and_extrude extrude: %s, velocity: %s"`.

---

## 7. Reinigung

### 7.1 `BoxAction.nozzle_clean(circle=True)` @0x118eec, Z.3149–3182

Default `circle=True`: Py_True im Wrapper @0x11bec4 – B. G-Code-Folge – B, Strings und Reihenfolge aus Disassembly @0x119244–0x11a8e4:

```
3150 clean_offset_pos = reactor.monotonic() % 3          → respond "temp_pos:%s"   (live 0.01…2.94)
3152 if circle: self.set_flushing_sign()
3154 G90;  3155/56 max_accel/_to_decel = toolhead.get_max_accel()/get_max_accel_to_decel()
3157 SET_VELOCITY_LIMIT ACCEL=5000 ACCEL_TO_DECEL=5000
3158 y_pos = toolhead.get_position()[1]
3159 if y_pos < safe_pos_y:  3160 G0 X<clean_right_pos_x> F<clean_velocity>;  3161 G0 Y<safe_pos_y> F<clean_velocity>
3162 G0 X<clean_pos_right_x> F<clean_velocity>          # live 170 (Attribut clean_pos_right)
3163 G0 X<clean_left_pos_x>  F<clean_velocity>          # live 135  → Blech-Schnitt
3164 if circle:
3165     G0 X<clean_pos_left_x + offset> Y<clean_pos_middle_y> F12000
3166     G2 I4 J0 P1 F10000
3167     G0 X<clean_pos_right_x - offset> Y<clean_pos_middle_y> F10000
3168     G3 I-4 J0 P1 F10000
3169–73 3× { G0 X<clean_right_pos_x> Y<clean_right_pos_y> F<v>;  G0 X<extrude_pos_x> Y<clean_left_pos_y> F<v> }
3174 if circle: 3175 G0 X<clean_right_pos_x> F<v>;  3177 G0 Y<safe_pos_y> F<v>    # Schacht verlassen
3179 M400;  3180 SET_VELOCITY_LIMIT ACCEL=%.3f ACCEL_TO_DECEL=%.3f (alte Werte)
3181 if circle: self.reset_flushing_sign()
```

- Mit den Live-Werten ergibt das: X170 → X135, Kreise um (160+o | 374) und (170−o | 374), 3× X160/Y378 ↔ X133/Y378, raus X160 → Y345.
- `BOX_NOZZLE_CLEAN [CIRCLE=0|1]` (Default 1, @0x36304) ruft genau diese Funktion.
- Die T0-Beispielkommentare in `box.cfg` (G0 X160 → X135) weichen beim ersten X ab. Der Code nimmt `clean_pos_right_x` (170), V: Kommentar veraltet.

### 7.2 Weitere Bausteine

**`blow()`** (@0x136f18, Z.2733–2740) – B:
- `fan0_last = box_save.output_pin_fan0.last_value × scale`
- `SET_PIN PIN=fan0 VALUE=255`, `G4 P5000`, `M400`, `SET_PIN PIN=fan0 VALUE=%.2f` (alter Wert)

**`go_to_extrude_pos()`** (@0x1375e8, Z.2710–2731) – B, Verzweigungsdetails V:
1. Ohne `x` in `homed_axes`: `G28 X Y`, `BED_MESH_CLEAR`.
2. `set_flushing_sign()`, `G90`.
3. Wenn `y ≤ safe_pos_y`: `G0 Y<safe_pos_y> F24000`, dann `G0 X<clean_left_pos_x + 4> F24000` (live X139).
4. Danach `G0 X<clean_left_pos_x> F<v>`, `G0 Y<clean_left_pos_y> F<v>`, `G0 X<clean_left_pos_x> Y<extrude_pos_y> F<v>`, `G0 X<extrude_pos_x> Y<extrude_pos_y> F<v>`.

**`move_to_safe_pos()`** (@0x1184e0, Z.3185–3194) – B:
- Bei referenziertem `xy`: `G90`, `G0 Y<safe_pos_y> F<clean_velocity>`, `G0 X<safe_pos_x> F<clean_velocity>`.
- Danach `reset_flushing_sign()`.

**`flushing_sign`**
- Markerdatei `creality/userdata/config/flushing_sign`, Prüfung per `os.path.exists`/`remove` (`has_flushing_sign` Z.1270).
- Die Markerdatei zeigt an, dass der Kopf im Spülschacht ist, und dient der Stromausfall-Reinigung (`power_loss_clean`).
- `set_flushing_sign` enthält `M400` und `SET_LIMITS` (Z.1278/1279). Der Rest ist ANNAHME.

**`is_different_type_material(last, cur)`** (@0xb3c04, Z.4409–4421) – B, Zusammensetzung V:
- **wahr**, wenn `get_material_type` verschieden ist. Für `last` gilt `type=remain_material.type`.
- **wahr**, wenn `|T_alt − T_neu| > 10`.

**`set_cool_temp()`** (@0x1970d4, Z.1301–1310)
- Läuft nach `BOX_MATERIAL_CHANGE_FLUSH` (Erfolg und Fehler).
- Merkt die Zieltemperatur in `PRINTER_PARAM hotend_temp`, dann `M104 S140`. Abhängig vom Zustand auch `G4 P2000` / `M104 S0`.

### 7.3 Config-Optionen `[box]` mit Stock-Defaults

Quelle: `BoxCfg.__init__` @0x1b6808, extrahiert `getX(name, default, minval, maxval)` – B.

| Option | Typ | Default | min | max | live box.cfg |
|---|---|---|---|---|---|
| `nozzle_volume` | float | 183 | 0 | 300 | – |
| `flush_multiplier` | float | 1 | 0 | 10 | – |
| `flush_velocity_factor` | float | 1.2 | 1 | 5 | – |
| `max_temp_flush_times` | int | 3 | 0 | 10 | – |
| `diff_material_type_min_flush_len` | float | 370 | 300 | 1000 | – |
| `box_first_clean_length` | int | 90 | 0 | 600 | – |
| `box_need_clean_length` | int | 80 | 0 | = first | 70 |
| `box_need_clean_length_max` | int | 100 | 0 | 600 | – |
| `diff_length` | float | −10 | −30 | 30 | – |
| `Tn_extrude` | float | 140 | 0 | 200 | 140 |
| `Tn_extrude_velocity` | intlist | (360,) | | | 360 |
| `Tn_extrude_percent` | intlist | (1,) | | | – |
| `Tn_extrude_temp` | float | 220 | 180 | | 220 |
| `clean_velocity` | float | 6000 | 1200 | 60·max_velocity (V) | 12000 |
| `clean_left_pos_x/_y` | float | 150 / 368 | | | 135 / 378 |
| `clean_right_pos_x/_y` | float | 170 / 368 | | | 160 / 378 |
| `clean_pos_left_x` / `_right_x` / `_middle_y` | float | 157 / 172 / 374 | | | 160 / 170 / 374 |
| `extrude_pos_x/_y` | float | 127 / 368 | | | 133 / 378 |
| `safe_pos_x/_y` | float | 205 / 350 | | | 225 / 345 |
| `baseline_nozzle_diameter` | getattr | 0.4 | | | – |

`clean_pos_*` gibt es nur im Zweig `has_extrude_pos=1`. `clean_pos_min/max_x/y` = 152/362/156/368 werden im Flush-Pfad nicht benutzt (V).

---

## 8. Folgen für k2_box (Vergleich mit `pi-host/k2_box.py`, `cmd_flush` / `flush_segments` / `gcode_wipe`, und `config-pi/NOTIZ-reinigung-resume.md`)

Keine Code-Änderung in diesem Auftrag. Die Vorschläge in Prioritätsfolge:

1. **Spülmenge farbabhängig statt fest `[90, 70]`.**
   - Heute: `flush_segments(c, None)` liefert immer 160 mm.
   - Stock spült bei gleichem Material farbabhängig 160 bis 409,6 mm ((800+183)/2.4), Fallback 160.
   - Bei anderem Material verdoppelt Stock den Wert, also bis 819 mm.
   - Bei anderem Material oder >10 K Temperaturunterschied: Minimum **370 mm**.
   - Vorschlag: `flush.get_flush_len()` + `flush.cal_flush_list()` übernehmen.
   - Die Farbe des Materials in der Düse nach jedem Wechsel persistent speichern, entsprechend Stock `remain_material.save`.
   - Die Zielfarbe kommt aus den RFID-/Slotdaten, die k2_box schon für `require_color` liest.
   - `LENGTH=` bleibt als manueller Override. Die Aufteilung sollte aber `cal_flush_list` folgen. Heute schneidet k2_box in 90er-Stücke (ANNAHME in k2_box, jetzt widerlegt).
2. **Geschwindigkeit:** Stock nutzt 20 % mit v und 80 % mit v × 1.2 (Abschnitte außer dem letzten).
   - v = `min(360, max_vol_speed/2.4·60)`, skaliert mit Düse/0.4.
   - k2_box: eine Geschwindigkeit (`tn_extrude_velocity`) für beide Teile; die 20/80-Teilung selbst stimmt.
   - Der Faktor ×1.2 fehlt. Bei Generic PLA (14 mm³/s) fährt Stock 350 statt 360.
3. **Temperatur:** k2_box nutzt `max(Ziel, 220)`. Stock nutzt:
   - `max(T_alt, T_neu)` aus `material_database.json` über die ID `material_type[1:]`.
   - bei `T_alt ≥ T_neu` Spülung auf `max(maxTemp)`: warten bis max−5, `M104 max`.
   - letzter Abschnitt `M104 flush_temp`, am Ende `M104 S<T_neu>`.
   - Der Kommentar in `_change_temp` („aus dem RFID-Tag“) ist zu korrigieren: Quelle ist die Material-DB, der Schlüssel kommt vom RFID-`material_type`.
4. **Wischen:** k2_box wischt nach **jedem** Abschnitt voll mit Kreisen. Stock arbeitet so:
   - Zwischenabschnitte `circle=False`: Blech + 3× Schütteln, bleibt im Schacht.
   - Nur nach dem letzten Abschnitt Kreise plus Ausfahrt.
   - Kreisstart zufällig um 0–3 mm versetzt (`monotonic % 3`).
   - Erstes X ist `clean_pos_right_x` (170), nicht `clean_right_pos_x` (160).
   - `SET_VELOCITY_LIMIT … ACCEL_TO_DECEL=5000`, danach alte Werte. k2_box setzt nur `ACCEL`.
   - Kreise als `G2/G3 … P1` (Ersatz-Polygon in k2_box ist begründet).
5. **Blasen fehlt:** Nach jedem Abschnitt läuft fan0 5 s auf 255 (`SET_PIN PIN=fan0`), danach der alte Wert. Vorher `M106 P0 S50` beim Spülstart.
   - Vorschlag: als optionaler Schritt mit dem Pi-Lüfternamen.
6. **Verstopfungsprüfung fehlt:** Stock summiert die Messrad-Differenz (0x10 Odometer) über alle Abschnitte. Bei `< |diff_length|` (10 mm) folgt Fehler key845.
   - Live hat das 2× ausgelöst, obwohl gespült wurde. Ob das ein echter Stau war oder ein Messrad-Artefakt, ist offen.
   - Vorschlag: nur als **Warnung** übernehmen (Option, Default aus), bis das am Gerät geklärt ist.
7. **Ende:** Stock fährt nach dem Spülen `move_to_safe_pos` (Y345 → X225) und `restore_fan`, dann `M104 S<T_neu>`.
   - `BOX_MATERIAL_CHANGE_FLUSH` (Makro-Weg) kühlt danach mit `set_cool_temp` auf 140.
   - Ob k2_box dasselbe Endverhalten will, ist eine Design-Entscheidung. Die Abweichung ist in NOTIZ §6a.8 schon dokumentiert.
8. **Im Druck:** Stock nimmt zuerst die Slicer-Matrix aus dem G-Code-Header (`flush_volumes_matrix`, `flush_multiplier`).
   - Für Upstream-Klipper bräuchte es einen eigenen Header-Parser in `virtual_sdcard`. Niedrige Priorität: Der Farbweg allein entspricht Stock außerhalb des Drucks und beim Fallback.
9. **Kleinigkeiten:**
   - Stock-Extrusion nutzt `G0 E… F…` statt `G1`, gleichwertig.
   - `go_to_extrude_pos` fährt mit F24000 bis X139 und hat Zwischenschritte X135 → Y378. Für die Sicherheit ohne Bedeutung, V.

---

## 9. Belegqualität und offene Punkte

| Punkt | Stand |
|---|---|
| Mengenformel, Abschnitte, Geschwindigkeit, 20/80-Teilung, Temperaturfolge, Verstopfungsprüfung | **B+L** |
| Farbalgorithmus | **B** (1.1.3.13-Objekt, REA + objdump) **+L** (4 Live-Werte exakt). Byte-Gleich mit 1.1.7.0 = ANNAHME. |
| Zeilennummern | ±1–2 (Fehlerpfad-Zuordnung) |

**Offen / ANNAHME**
- Slicer-Index-Zuordnung in `get_flush_length_from_gcode` (`Tn_map`/`Tnn_map`).
- Details von `quickly_wait_heating`.
- `set_flushing_sign`-Rumpf.
- Mehrfach-Percent-Pfad in `Tn_Extrude`.
- `is_use_ending_material_flush`, `power_loss_clean`, `flush_material` (Endspülen mit Restmaterial, Z.4522–4540: `max_extrusion_speed × 60 × 0.8`, `nozzle_clean`, `move_to_safe_pos`): nur gesichtet.
- Ursache der live ausgelösten key845 (Messrad bewegte sich während des Spülens um < 0,01 mm).
- Ob `maxval` von `clean_velocity` = 60 × `printer.max_velocity` ist.

---

## 10. Nachbau `nachbau/flush.py`

Reiner Rechen- und Listen-Nachbau, ohne Senden.

**Funktionen**
- `flushing_volume` – f32/f64 wie im Original
- `parse_color_value`
- `color_flush_len`
- `finish_flush_len`
- `get_flush_len`
- `gcode_matrix_flush_len`
- `cal_flush_list`
- `flush_velocity`
- `flush_temps`
- `segment_plan`
- `flush_plan`
- `nozzle_blocked`
- `gcode_extrude`
- `gcode_blow`
- `gcode_nozzle_clean`

**Weitere Inhalte:** `BOX_DEFAULTS` und `LIVE_BOX_CFG`.

**Selbsttest `python3 flush.py` → `SELBSTTEST flush: ok 44/44`**, Live-Vektoren daraus:
- Volumen 60/165/193/367
- `flush_len` 101.25/145/156.67/229.17
- vier `length`-Listen
- Geschwindigkeit 360/350
- Abschnittsfolge 18@360, 72@432, 14@360, 56@360 mit 235/240/220 °C
- Messrad-Diff → key845

## 11. Methode

**Ghidra-Projekt**
- Ghidra 12.1.4 headless, eigenes Projekt (Kopie, Imagebase 0) mit Label-Import: Cython-Strings, Konstanten, Tupel.
- Eigene Nachauflösung der PIC-Ausdrücke (`&UNK_x + lit`, GOT-Einträge → `_Py_None/True/FalseStruct`).

**Zeilen und Varnamen**
- Python-Zeilen aus der `__pyx_lineno`-Variablen des Traceback-Aufrufs.
- Varnamen aus den Code-Objekt-Tupeln.

**Disassembly-Grundlage**
- Annotierte `llvm-objdump`-Disassembly mit Stringtabelle, Konstanten (inkl. `vmov.f64 #imm`), Tupeln und Cache-Slots.
- Tabellen aus den vorhandenen box_wrapper-Arbeitsdateien des Bootloader-Host-Befunds wiederverwendet.

**Gegenprüfungen**
- Gegenprobe der C-Funktion per REA-MCP (`procedure_pseudo_code`).
- Floats gegen Literal-Pools geprüft.
- Live-Abgleich mit `klippy.log` 12./13.10. (FW 1.1.7.0).
