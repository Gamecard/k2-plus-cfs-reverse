# REMAIN-MATERIAL-Befund 17.10.2026: Persistenz des Düsen-Materials (K2 FW 1.1.7.0)

> Nur statische Analyse plus vorhandene Live-Logs. Kein Drucker- oder Pi-Zugriff, keine Code-Änderung.
> Arbeitsdateien liegen lokal (nicht im Repo).

**Binaries**
- `filament_rack_wrapper.cpython-39.so`: Hier ist die Klasse `RemainMaterial` definiert (Z.8–61).
- `box_wrapper.cpython-39.so`: Hier wird sie benutzt (`box_action.remain_material`).

**Adressen** sind ELF-vaddr wie bei `llvm-objdump`. In Ghidra/REA gilt **+0x10000**.
Die Zeilen stammen aus den `__pyx_lineno`-Konstanten in den Fehlerpfaden (`mov r7/r6, #Zeile` vor dem gemeinsamen Traceback-Block) und aus `__Pyx_AddTraceback` (`r2`).

**Werkzeuge**
- `llvm-objdump` mit den vorhandenen Cython-Auflösern (Stringtabelle, Tupel, Cache-Slots).
- REA/Ghidra 12.1.4 als Gegenprobe: `search_strings` findet den Pfadstring in `filament_rack_wrapper` bei Ghidra `0x3d2d0` (= vaddr `0x2d2d0`).
- Es gibt keine Float-Konstanten, die hier relevant wären. Der vmov.f64-Fehler aus dem REA-Motor-Befund spielt also keine Rolle.

Kennzeichnung: **B** = belegt (Code), **L** = Live-Log, **A** = ANNAHME.

---

## Kurzfassung

| Frage | Stock-Verhalten | Beleg |
|---|---|---|
| Datei | `os.path.join(base_dir, 'creality/userdata/box/remain_material_data.json')`, auf dem Drucker **`/mnt/UDISK/creality/userdata/box/remain_material_data.json`** | B (Z.10) + L (base_dir) |
| Format | JSON, `json.dump(data, f)` ohne indent: `{"remain_material": {"color": "0RRGGBB", "type": "<material_type>"}}`. Fehlt ein Wert: `null` | B |
| Felder | **nur `color` und `type`**. Nicht gespeichert werden Temperatur, Slot/TNN und Geschwindigkeit. `velocity` wird nur im Speicher aus der Material-DB berechnet | B |
| Lesen | **genau einmal**, beim Konfig-Laden (`FilamentRackWrapper.__init__` → `RemainMaterial(config)` → `init()`). Danach nur noch die Attribute im Speicher | B |
| Schreiben | (1) beim Start, wenn die Datei fehlt, kaputt ist oder keinen Schlüssel `remain_material` hat (Defaults `null`). (2) `save()` **nach einem erfolgreichen Spülen**: CFS-Wechsel `material_change_flush` und Rack-`FILAMENT_RACK_FLUSH` | B + L |
| Nicht geschrieben | bei Cut, Rückzug/Entladen, manuellem Laden (`box_extrude_material`), Cancel, Druckende, Power-Loss-Fortsetzung mit gleichem Slot und Spülfehler (`ret is False`) | B (einziger save-Aufruf je Modul) |
| Atomar? | **nein**: `open(path, 'w')` kürzt die Datei sofort, ohne tmp/rename/fsync. Lesen und Schreiben stehen je in `try/except Exception` → `logging.error(e)` | B |
| Kaputte Datei | JSON-Fehler: Log-Fehler und **Überschreiben mit `null`-Defaults**. Danach wird beim nächsten Spülen die Fallback-Länge `Tn_extrude` (140 → 90+70) benutzt | B |
| Strukturfehler | `{"remain_material": {}}` oder `null` lösen `KeyError`/`TypeError` **außerhalb** jedes try aus. Das bricht den Klippy-Start ab (Config-Objekt `filament_rack` scheitert) | B (Folge A) |
| Power-Loss | Der Wert überlebt Neustarts (live mehrfach gesehen). Ein Stromausfall genau beim Schreiben hinterlässt eine leere Datei; dann wird beim nächsten Start auf `null` zurückgesetzt | L + B/A |

---

## 1. Klasse `RemainMaterial` (filament_rack_wrapper, Z.8–61)

Rekonstruktion (B, Adressen = Rümpfe):

```python
class RemainMaterial:                                              # Z.8
    def __init__(self, config):                                    # 0x25018
        self.path = os.path.join(base_dir,
            'creality/userdata/box/remain_material_data.json')     # Z.10
        self.config = config                                       # Z.11
        self.color = None; self.type = None; self.velocity = None  # Z.12-14
        self.init()                                                # Z.15

    def init(self):                                                # 0x234b0
        data = {}                                                  # Z.18  @0x234f0
        if os.path.exists(self.path):                              # Z.19  @0x23558-0x23684
            try:
                with open(self.path, 'r') as f:                    # Z.21  @0x236e4 'r'
                    data = json.load(f)                            # Z.22  @0x23854
            except Exception as e:                                 # Z.23  @0x23b24
                logging.error(e)                                   # Z.24  @0x23bcc
        if data and 'remain_material' in data:                     # Z.25  @0x23d24/@0x23d48
            self.color = data.get('remain_material', {})['color']  # Z.26  @0x23d58/@0x23e8c
            self.type  = data.get('remain_material', {})['type']   # Z.27  @0x23ed8/@0x23ff0
        else:
            data['remain_material'] = {'color': None, 'type': None}  # Z.29 @0x24560-0x245c4
            try:
                with open(self.path, 'w') as f:                    # Z.31  @0x24628 'w'
                    json.dump(data, f)                             # Z.32  @0x247a0
            except Exception as e:                                 # Z.33
                logging.error(e)                                   # Z.34

    def save(self, color, type):                                   # 0x21c68 (Wrapper 0x232a0)
        self.color = color; self.type = type                       # Z.37/38
        data = {}                                                  # Z.39
        try:
            with open(self.path, 'r') as f:                        # Z.41  (kein exists-Check)
                data = json.load(f)                                # Z.42
        except Exception as e:                                     # Z.43
            logging.error(e)                                       # Z.44
        data['remain_material'] = {'color': self.color, 'type': self.type}  # Z.45
        logging.info('save: color: %s, type: %s' % (self.color, self.type))  # Z.46 @0x22580
        try:
            with open(self.path, 'w') as f:                        # Z.48
                json.dump(data, f)                                 # Z.49  @0x22820
        except Exception as e:                                     # Z.50
            logging.error(e)                                       # Z.51
        self.get_velocity()                                        # Z.52  @0x23010

    def get_velocity(self):                                        # 0x2190c, Z.55
        self.velocity = self.config.get_printer().lookup_object('filament_rack') \
                            .get_material_target_speed(type=self.type)

    def get_status(self, eventtime=None):                          # 0xaf3c, Z.59-61
        return {'color': self.color, 'type': self.type, 'velocity': self.velocity}
```

### Details (B)

**Zugriffsart beim Lesen**
- `data.get(...)` wird mit Index `['color']` gelesen: `__Pyx_PyDict_GetItem` @0x9c34, der Fallback `PyObject_GetItem` liegt @0x24330.
- Das ist **kein** `.get('color')`. Ein fehlender Schlüssel löst `KeyError` aus.
- Z.25–29 liegen außerhalb jedes try. Auch `FilamentRackWrapper.__init__` hat um `RemainMaterial(config)` kein `PyExc_Exception`-Matching (0 Treffer im Dekompilat).

**Read-modify-write**
- Andere Top-Level-Schlüssel bleiben erhalten.
- Das innere Dict wird bei jedem `save` komplett ersetzt.

**Schreibweise und Dateisystem**
- `json.dump` bekommt 2 Positionsargumente und keine kwargs (Fastcall @0x24838 bzw. @0x228b8). Die Ausgabe ist daher kompakt in einer Zeile.
- Es gibt kein `os.makedirs`. Fehlt das Verzeichnis, scheitert das Schreiben nur mit einem Log-Eintrag; im Speicher steht der neue Wert trotzdem.

**`base_dir`**
- `base_dir` wird aus `extras.base_info` importiert (String im Modul; wie `virtual_sdcard.py` Z.7).
- Das Live-Log zeigt dieselbe Konstruktion bei `box_wrapper` als `/mnt/UDISK/creality/userdata/box/box_heater.json` („Dry state file not found, skipping: …“). Daraus folgt `base_dir = /mnt/UDISK/` (**B+L**).
- Das Modul `base_info` selbst liegt nicht im lokalen rootfs-Auszug.

**Status-Namen und Nutzung**
- Statusnamen nach außen: `printer['filament_rack'].remain_material_color/_type/_velocity` (L, Rack-Befund).
- Die Stock-Makros nutzen `remain_material_velocity * 0.8` (Resume-Vorextrusion).

## 2. Anbindung in box_wrapper

| Stelle | Zeile / Adresse | Zugriff |
|---|---|---|
| `BoxAction.__init__` | Z.1231 @0x19c220 | `self.remain_material = …` (Startwert; A: `None`) |
| `BoxAction.find_objs` | Z.1293 @0x198480–0x1984d4 | `self.remain_material = self.printer.lookup_object('filament_rack').remain_material`. Ohne Default, das heißt `[filament_rack]` ist Pflicht (in allen Stock-`box.cfg` vorhanden) |
| `MCMBW.material_change_flush` | **Z.4513/4514** @0x0ad110–0x0ad1ec | **`remain_material.save(color=get_Tn_data(addr,'color_value',num), type=get_Tn_data(addr,'material_type',num))`**. Läuft nur, wenn `material_flush` ≠ False ist (FLUSH-BEFUND §1) |
| `MCMBW.get_flush_len` | Z.4425/4429, Z.4456 | liest `.color` als Quellfarbe (None → `Tn_extrude`) |
| `MCMBW.is_different_type_material` | Z.4410/4417 | liest `.type` |
| `MCMBW.flush_material` (Power-Loss, gleicher Slot) | Z.4523/4524 @0x0aa4b4–0x0aa588 | `if box_action.remain_material:` → `get_material_max_extrusion_speed(type=remain.type)` (·60·0.8). **Kein save** |
| `BoxAction.get_flush_temp` / `get_flush_max_temp` | Z.2887 / Z.2894 | `type=remain.type` |
| `BoxAction.move_to_cut` | Z.2539 | Schnitt-Geschwindigkeit `type=remain.type` |
| `BoxAction.set_temp` / `cut_material` | Z.3217 / Z.3223 | Temperatur `type=remain.type` |
| `BoxAction.box_extrude_material` (manuelles Laden) | Z.3702, Z.3734 | Heizen `type=remain.type`. **Kein save** |
| `MCMBW.box_end` | Z.5117 | liest `.type` |
| Rack `cmd_filament_rack_flush` | Z.455 @0x2a2a8 | `save(color=filament_rack_data['color_value'], type=filament_rack_data['material_type'])` |

**Vollständigkeit (B)**
- In ganz `box_wrapper` (Annotation des gesamten `.text`, alle Lasten des Strings `remain_material`) gibt es genau **einen** `save`-Aufruf (Z.4514).
- Alle anderen Zugriffe sind GetAttr auf `.color` oder `.type`.
- Kein Code setzt `remain_material.color/type` direkt. Es gibt auch kein G-Code-Kommando zum Zurücksetzen (`FILAMENT_RACK_MODIFY` ändert nur `filament_rack_data`).

**Aufrufer von `material_change_flush` (alle mit save bei Erfolg)**
- Wechsel und G-Code: `Tn_action` (Z.4646), `cmd_material_change_flush` (Z.4348, `BOX_MATERIAL_CHANGE_FLUSH`), `cmd_CR_BOX_FLUSH` (Z.5847).
- Fehler-Retry-Prozesse: Z.4693, Z.4722, Z.4747, Z.4764, Z.4795.
- `error_resume_process` (Z.5169).

**Power-Loss-Zweig**
- `Tn_action` Z.4593–4596: Bei `last_tnn == tnn` und `box_state.is_continue_print` läuft `power_loss_same_material_process` → `flush_material` (ohne save).
- Bei gleichem Slot ohne Fortsetzung wird nur geheizt (Z.4601–4604).

**Abgrenzung**
- Der zuletzt geladene Slot (`last_cmd`, `Tnn_map`) wird **getrennt** über `box_save` bzw. `update_tn_save_data(..., save=…)` persistiert (Varnamen `data`, `file`; Datei vermutlich `creality/userdata/box/tn_data.json`, **A**).
- Das ist nicht `remain_material`.

## 3. Live-Belege (klippy.log 05.–16.10.)

- Jede `save`-Zeile folgt direkt auf `Tn_data[T1][color_value][n]` / `[material_type][n]`. Danach kommen `max_volumetric_speed: …` / `get material extrusion speed: …` (= `get_velocity`) und `M104` auf die Zieltemperatur. Das ist genau die Reihenfolge von Z.4513–4516 (L).
  Beispiel 12.10. 23:22:00: `save: color: 0ce58f8, type: 000001`.
- 9 `save`-Zeilen. Es gibt keine einzige außerhalb dieses Musters (L).
- **Persistenz über Neustart bestätigt (L):**
  - Das letzte `save` am 12.10. 02:34 war `026eeee`. Nach dem Kaltstart (RTC 2020) zeigt der Status sofort `remain_material_color: '026eeee'`.
  - Ebenso `0ce58f8` am 13.10. → Starts am 14., 15. und 16.10.
- Stand der letzten Logs (16.10.): `color 0ce58f8`, `type 000001`, `velocity 350.00000000000006`.
- **Erwarteter Dateiinhalt (A, nur Struktur):**
  `{"remain_material": {"color": "0ce58f8", "type": "000001"}}`, sofern danach nicht mehr gespült wurde.
  Zum lesenden Holen: **`/mnt/UDISK/creality/userdata/box/remain_material_data.json`**.

## 4. Fehlerfälle und Stromausfall

| Fall | Stock-Ablauf | Ergebnis |
|---|---|---|
| Datei fehlt (Erststart) | `exists` False → `data={}` → Defaults schreiben | Datei mit `null`, Farbe unbekannt → `Tn_extrude` (B) |
| leer/abgeschnitten (Stromausfall beim Schreiben) | `json.load` wirft → `logging.error` → `data={}` → Defaults **überschreiben** die Datei | Farbe/Typ verloren, Fallback-Spülung 160 mm, Temperatur `Tn_extrude_temp` (B) |
| gültiges JSON ohne `remain_material` | Defaults ergänzen, andere Schlüssel bleiben | B |
| `{"remain_material": {}}` / `null` / JSON-Liste | KeyError/TypeError außerhalb try | Klippy-Start scheitert (B; Fehlermeldung A) |
| Verzeichnis fehlt / read-only | Schreiben scheitert, nur `logging.error` | Wert nur im Speicher, nach Neustart alt (B) |
| Stromausfall während des Spülens | save läuft erst nach dem Spülen | Datei enthält das **alte** Material. Beim Fortsetzen mit gleichem Slot läuft `flush_material` mit der alten Typ-Geschwindigkeit (B/A) |

**Schreibfenster (A)**
- Stock schreibt ohne `fsync`. Ein Stromausfall kurz nach `save` kann auf `/mnt/UDISK` einen leeren oder alten Inhalt hinterlassen. Das hängt vom Dateisystem und dessen Commit-Intervall ab, beides ist lokal nicht geprüft.
- Die NVMe-/Journal-Erfahrung auf dem Pi zeigt dasselbe Muster bei harten Abschaltungen.

## 5. Abgleich mit `pi-host/k2_box.py`

| Punkt | Stock | k2_box heute |
|---|---|---|
| Speicherort | Datei, überlebt Neustart | **nur Speicher**: `self.remain_material = {"type": None, "color": None, "tnn": ""}` (`__init__`, Kommentar „Stock persistiert“) |
| Setzen | `save` nach erfolgreichem `material_change_flush` / Rack-Flush | in `cmd_flush` nur `if ran:` (wirklich gefahren) → `{"type": new_key, "color": new_col, "tnn": tnn}` |
| Bei Spülfehler (Stock key845 → `False`) | kein save | k2_box prüft die Verstopfung nur als Warnung und **setzt trotzdem**. Das ist konsistent, weil wirklich gespült wurde |
| Nutzer | Quellfarbe, alter Typ (Temp/Speed/×2), Cut-/Rückzug-/Lade-Temperatur, Resume-Geschwindigkeit | `_flush_plan` (alt-Farbe/-Typ), `_nozzle_key` (Rückzug-/Wechseltemperatur, `prime_speed`), Status `remain_material` |
| Unbekannt | `color None` → `Tn_extrude` | `plan_flush_lengths(src_color=None)` → `Tn_extrude` → [90, 70]. Gleiches Ergebnis |
| Zusatz | – | `tnn` (Stock speichert keinen Slot) |

**Folge heute**
- Nach jedem Klipper-Restart oder Neustart des Pi ist die alte Farbe unbekannt.
- Der erste Wechsel spült deshalb immer pauschal 160 mm (Stock: farbabhängig 160–410 mm, bei anderem Material bis 819 mm).
- `_nozzle_key` fällt auf das Etikett des geladenen Slots zurück.

## 6. Umsetzungsvorschlag (keine Code-Änderung)

1. **Neue Option** `[k2_box] remain_material_file`.
   - Vorschlag: Default `~/printer_data/config/k2/remain_material.json`. Das ist neben der bereits vorgesehenen `material_database.json`, im Mainsail-Editor sichtbar und von Hand korrigierbar.
   - Leer = aus, also das heutige Verhalten.
   - Alternative, wenn veränderliche Zustände nicht ins Config-Backup sollen: `~/printer_data/k2/remain_material.json` (eigenes Verzeichnis außerhalb `config/`).
2. **Format Stock-kompatibel:** `{"remain_material": {"color": "0RRGGBB"|null, "type": "<6-stellig>"|null, "tnn": "T1A"}}`.
   - Unbekannte Zusatzschlüssel (`tnn`, optional `saved_at`) werden beim Lesen ignoriert.
   - Damit lässt sich die Stock-Datei vom Drucker 1:1 als Startwert übernehmen (Migration), zum Beispiel per `install.sh --material-db`-ähnlichem Holschritt.
3. **Lesen:** einmal in `__init__`, wie Stock beim Config-Laden, oder spätestens bei `klippy:connect`.
   - **Robuster als Stock:** Jeder Fehler wird zu einer Warnung mit Rückfall auf `None`. Fehler sind hier: Datei fehlt, JSON kaputt, Schlüssel fehlt, falscher Typ, Farbe passt nicht zu `^0[0-9A-Fa-f]{6}$`.
   - **Nie ein Config-Fehler**, und die kaputte Datei beim Lesen **nicht überschreiben** (höchstens als `*.bad` umbenennen).
   - Die Meldung erscheint beim Connect wie bei `material_db_info`.
4. **Schreiben, nur bei geänderter Farbe/Typ:**
   - (a) **nach erfolgreichem FLUSH** (heute `if ran:` in `cmd_flush`). Das ist der Stock-Zeitpunkt.
   - Bewusst **nicht** schreiben bei Cut, Rückzug/Entladen, manuellem Laden, Cancel, Druckende und Resume-Prime. Stock tut das auch nicht, und physikalisch steckt der Rest des zuletzt gespülten Materials weiter in der Düse.
   - Optional (Design-Entscheidung, **nicht** Stock): Ein Kommando `K2_BOX_REMAIN [COLOR=] [TYPE=] [CLEAR=1]` für manuelle Korrektur, als Ersatz für das fehlende Stock-Reset.
5. **Atomar schreiben:**
   - `tmp = path + ".tmp"` im selben Verzeichnis.
   - `json.dump(..., separators=(",", ":"))`, dann `f.flush()` und `os.fsync(f.fileno())`.
   - Danach `os.replace(tmp, path)` und `os.fsync` auf den Verzeichnis-Deskriptor.
   - `os.makedirs(dirname, exist_ok=True)` vorher.
   - Ausnahmen nur loggen und `respond_info`. Der Wert im Speicher bleibt gültig.
   - Die Datei ist < 100 Byte groß; der kurze blockierende Aufruf im Reactor ist unkritisch (A).
6. **Status:** `remain_material` behalten und `remain_material_file`/`remain_material_info` (geladen/Fehler) ergänzen.
7. **Tests** (`test_k2_box.py`):
   - Laden: gültig, leer, kaputt, `{}`, falscher Typ.
   - Speichern: atomar, `tmp` aufgeräumt, keine Änderung → kein Schreiben.
   - Ablauf: Restart-Simulation mit nachfolgendem `plan_flush_lengths` mit Quellfarbe.

**Offen (A)**
- Dateisystem und Mount-Optionen von `/mnt/UDISK`, also das tatsächliche Verlustfenster bei Stock.
- Der Startwert von `BoxAction.remain_material` vor `find_objs`.
- Die Datei `tn_data.json` als Persistenz von `last_cmd`.
- Der Inhalt der Stock-Datei; wird lesend geholt.

## Nachtrag: Gegenprobe am Gerät (17.10.2026, lesend)
- Datei existiert genau unter `/mnt/UDISK/creality/userdata/box/remain_material_data.json` (59 Byte, zuletzt geschrieben 16.10. 21:41).
- Format exakt wie rekonstruiert: `{"remain_material": {"color": "0RRGGBB", "type": "000001"}}` (kompakt, eine Zeile).
- Inhalt ≠ vorhergesagter Wert aus den Logs vom 16.10. (andere Farbe) – erwartbar: nach dem Log-Ende wurde offenbar noch einmal gespült. Typ `000001` wie vorhergesagt.
- Offener Punkt „Dateisystem“: `/mnt/UDISK` ist **ext4, gemountet mit `sync`** → Stock-Schreiben ist trotz fehlendem fsync/rename weitgehend sofort auf dem Medium; das Verlustfenster beschränkt sich auf einen Abbruch mitten im `json.dump` (leere/halbe Datei).
