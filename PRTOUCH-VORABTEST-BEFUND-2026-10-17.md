# PrTouch-Vorabtest am Gerät – 17.10.2026

Skript `pi-host/prtouch_vorabtest.py` (README-k2_prtouch A.9) auf dem Stock-Drucker, Klipper gestoppt,
nur Düsen-MCU (ttyS3, 230400), **keine Bewegung, keine Heizung, Main-MCU nicht angesprochen**.
3 Durchläufe je `start_prtouch_pres` (Default-Antast-Parameter) → 0,2 s → Ruhetest → `stop_prtouch_pres sta_swap=1`.
Rohdaten lokal: `arbeitsdateien-lokal/prtouch-vorabtest-1017/` (nicht im Repo).

| Durchlauf | Ergebnis | tick_median_ms | filt_max_abs (Grenze 2000) | Roh-Spannweite (n=64) |
|---|---|---|---|---|
| 1 | OK (armed) | 0,7914 | 364 | 1 002 |
| 2 | OK (armed) | 0,7912 | 497 | 892 |
| 3 | OK (armed) | 0,7912 | 443 | 614 |

Bewertung:
- **Tickabstand** 0,791 ms ≈ `acq_tick` 93 750 / 120 MHz = 0,781 ms (+1,3 %, MCU-Raster) – passt, sehr stabil.
- **Gefilterter Kanal** in Ruhe ≤ 497 → Faktor ≥ 4 Abstand zur Ruhe-Grenze 2000 und ≥ 8 zu min_hold 4000.
  `rest_filt_max_frac` muss nicht verschärft werden.
- **Rohkanal** Ruhe-Spannweite 614–1 002 Counts (Spitze-Spitze) bei Nullpunkt ≈ −1,433 M.
  Für das Host-Kriterium in `mode: steps` (Schwelle 3000) Abstand ≈ Faktor 3; für `contact_criterion: cumulative`
  (Stufe ≥ 250 auf 16er-Median) plausibel, da das Median-Rauschen deutlich unter der Spannweite liegt (ANNAHME, nicht
  gemessen: Median-Streuung je Stufe).
- `tri_chxs` = 0 in Ruhe (kein Fehlkontakt), Entschärfen 3/3, Abschluss-Reset ok.
- Danach Klipper start + `POST /printer/firmware_restart` (vom PC; auf dem Drucker gibt es kein `curl`) →
  `ready`, Extruder liest Temperatur, nach > 80 s weiterhin stabil.
- Beim Reset der Düsen-MCU erscheinen 2× `Wait for identify_response … Serial connection closed`-Tracebacks:
  erwartet (Verbindung schließt durch den Reset), Neuverbindung danach sauber.

Offen: Signalweg PA15 → PC7 (braucht Main-MCU) und Antasten mit Bewegung → erst am Pi.
