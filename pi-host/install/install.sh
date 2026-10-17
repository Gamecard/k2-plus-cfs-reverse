#!/bin/bash
# =====================================================================
# K2 Plus – Pi-Host-Installation (Upstream-Klipper + K2-Module + Config)
# Läuft auf dem Pi als Benutzer "pi" aus dem entpackten Paketordner:
#   ./install.sh [Optionen]        (Hilfe: ./install.sh --hilfe)
# Bewegt NICHTS, startet Klipper nur mit --starten. Idempotent: mehrfach
# aufrufbar, jeder Lauf legt ein eigenes Backup unter ~/k2-pi-backup/ an.
# =====================================================================
set -eu
set -o pipefail

ZIEL_COMMIT=9cb99754c035b90206ef0a02660f674fc9517e9b   # geprüfter Upstream-Stand
MIN_FREI_KB=204800                                     # 200 MB

PKG=$(cd "$(dirname "$0")" && pwd)
KLIPPER_DIR=${KLIPPER_DIR:-$HOME/klipper}
CONFIG_DIR=${CONFIG_DIR:-$HOME/printer_data/config}
KLIPPY_ENV=${KLIPPY_ENV:-$HOME/klippy-env}
BACKUP_ROOT=${BACKUP_ROOT:-$HOME/k2-pi-backup}

KLIPPER_ANFASSEN=1
PRINTER_ERSETZEN=0
SAVE_AUS_PAKET=0
STARTEN=0
TESTS=1
BATCH=1
ARCH_EGAL=0
DIENST=""
SET_ARGS=()

hilfe() {
  cat <<'EOF'
Aufruf: ./install.sh [Optionen]

  --klipper-nicht-anfassen   Klipper-Checkout NICHT auf 9cb9975 umstellen (nur prüfen/warnen)
  --printer-cfg-ersetzen     vorhandene printer.cfg durch die K2-printer.cfg ersetzen
                             (sonst landet sie als printer-k2.cfg daneben)
  --save-config-aus-paket    beim Ersetzen den SAVE_CONFIG-Block des Pakets nehmen
                             (Standard: Kalibrierblock einer bisherigen K2-printer.cfg behalten)
  --main-serial PFAD         /dev/serial/by-id/... der Main-MCU   -> [mcu] serial
  --duese-serial PFAD        /dev/serial/by-id/... der Düsen-MCU  -> [mcu nozzle_mcu] serial
  --rs485-serial PFAD        /dev/serial/by-id/... des USB-RS485   -> [k2_485] serial
  --dienst NAME              systemd-Dienst (Standard: klipper, automatisch erkannt)
  --starten                  Klipper-Dienst am Ende starten (Standard: bleibt gestoppt)
  --ohne-tests               Offline-Tests überspringen
  --ohne-batch               Batch-Prüfung der Config überspringen
  --arch-egal                nur für Probeläufe auf Nicht-aarch64-Rechnern
  --hilfe                    diese Hilfe

Umgebung (nur bei abweichender Installation): KLIPPER_DIR, CONFIG_DIR, KLIPPY_ENV, BACKUP_ROOT
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --klipper-nicht-anfassen) KLIPPER_ANFASSEN=0 ;;
    --printer-cfg-ersetzen)   PRINTER_ERSETZEN=1 ;;
    --save-config-aus-paket)  SAVE_AUS_PAKET=1 ;;
    --main-serial)  [ $# -ge 2 ] || { echo "FEHLER: $1 braucht einen Wert" >&2; exit 2; }; SET_ARGS+=(--set "mcu=$2"); shift ;;
    --duese-serial) [ $# -ge 2 ] || { echo "FEHLER: $1 braucht einen Wert" >&2; exit 2; }; SET_ARGS+=(--set "mcu nozzle_mcu=$2"); shift ;;
    --rs485-serial) [ $# -ge 2 ] || { echo "FEHLER: $1 braucht einen Wert" >&2; exit 2; }; SET_ARGS+=(--set "k2_485=$2"); shift ;;
    --dienst)       [ $# -ge 2 ] || { echo "FEHLER: $1 braucht einen Wert" >&2; exit 2; }; DIENST=$2; shift ;;
    --starten)      STARTEN=1 ;;
    --ohne-tests)   TESTS=0 ;;
    --ohne-batch)   BATCH=0 ;;
    --arch-egal)    ARCH_EGAL=1 ;;
    --hilfe|-h|--help) hilfe; exit 0 ;;
    *) echo "FEHLER: unbekannte Option: $1 (--hilfe)" >&2; exit 2 ;;
  esac
  shift
done

SCHRITT=""
schritt() { SCHRITT=$1; echo; echo "=== [$1] $2 ==="; }
ok()      { echo "  ok    $*"; }
info()    { echo "  info  $*"; }
warn()    { echo "  WARN  $*"; }
fehler()  { echo; echo "FEHLER in Schritt [$SCHRITT]: $*" >&2; exit 1; }
trap 'rc=$?; if [ $rc -ne 0 ]; then echo "ABBRUCH (Exit $rc) in Schritt [${SCHRITT:-?}] – nichts weiter verändert. Backup (falls angelegt): ${BK:-keins}" >&2; fi' EXIT

# Pfade in Klipper, die NUR von diesem Paket stammen (für Sauberkeitsprüfung + .git/info/exclude)
EIGENE_MUSTER='^klippy/extras/(k2_[A-Za-z0-9_]+\.py|k2_nachbau/)'

klipper_fremdaenderungen() {
  # git status ohne unsere eigenen, installierten Dateien
  git -C "$KLIPPER_DIR" status --porcelain --untracked-files=all \
    | awk '{ p=substr($0,4); print p }' | grep -vE "$EIGENE_MUSTER" | grep -v '__pycache__/' || true
}

# ---------------------------------------------------------------------
schritt 1 "Vorprüfungen"
[ "$(id -u)" -ne 0 ] || fehler "nicht als root/mit sudo starten – als Benutzer pi ausführen"
ARCH=$(uname -m)
if [ "$ARCH" = aarch64 ]; then ok "Architektur aarch64"
elif [ $ARCH_EGAL -eq 1 ]; then warn "Architektur $ARCH (--arch-egal, Probelauf)"
else fehler "Architektur $ARCH statt aarch64 – falsches Gerät?"; fi

[ -f "$PKG/SHA256SUMS" ] || fehler "SHA256SUMS fehlt im Paket ($PKG)"
( cd "$PKG" && sha256sum --quiet -c SHA256SUMS ) || fehler "Prüfsummen stimmen nicht – Paket beschädigt/verändert"
ok "Prüfsummen ($(wc -l < "$PKG/SHA256SUMS") Dateien)"
[ -f "$PKG/PAKET-INFO.txt" ] && info "Paket: $(sed -n 's/^Paket: //p' "$PKG/PAKET-INFO.txt")"

[ -f "$KLIPPER_DIR/klippy/klippy.py" ] || fehler "Klipper nicht gefunden: $KLIPPER_DIR/klippy/klippy.py"
git -C "$KLIPPER_DIR" rev-parse --git-dir >/dev/null 2>&1 || fehler "$KLIPPER_DIR ist kein git-Checkout"
ok "Klipper unter $KLIPPER_DIR"
[ -d "$CONFIG_DIR" ] || fehler "Config-Ordner fehlt: $CONFIG_DIR"
ok "Config-Ordner $CONFIG_DIR"

FREI=$(df -Pk "$HOME" | awk 'NR==2 {print $4}')
[ "$FREI" -ge "$MIN_FREI_KB" ] || fehler "zu wenig Platz in $HOME: $((FREI/1024)) MB frei, nötig $((MIN_FREI_KB/1024)) MB"
ok "freier Platz $((FREI/1024)) MB"

if [ -x "$KLIPPY_ENV/bin/python" ]; then PY=$KLIPPY_ENV/bin/python
else PY=$(command -v python3) || fehler "kein Python gefunden"; warn "kein $KLIPPY_ENV – nutze $PY (Batch braucht Klipper-Abhängigkeiten)"; fi
ok "Python $("$PY" -c 'import sys; print(sys.version.split()[0])') ($PY)"
command -v python3 >/dev/null || fehler "python3 fehlt (für k2cfg.py)"

sudo -n true 2>/dev/null || fehler "sudo ohne Passwort nicht möglich (nötig für systemctl stop/start)"
if [ -z "$DIENST" ]; then
  if systemctl cat klipper.service >/dev/null 2>&1; then DIENST=klipper
  else fehler "Dienst klipper.service nicht gefunden – mit --dienst NAME angeben"; fi
fi
systemctl cat "$DIENST.service" >/dev/null 2>&1 || fehler "Dienst $DIENST.service nicht gefunden"
ok "Dienst $DIENST.service"

STAMP=$(date +%Y%m%d-%H%M%S)
BK=$BACKUP_ROOT/$STAMP
mkdir -p "$BK"
cp "$PKG/uninstall.sh" "$BK/uninstall.sh"
{
  echo "STAMP=$STAMP"
  echo "PAKET=$PKG"
  echo "KLIPPER_DIR=$KLIPPER_DIR"
  echo "CONFIG_DIR=$CONFIG_DIR"
  echo "DIENST=$DIENST"
} > "$BK/state.env"
ok "Backup-Ordner $BK"

# ---------------------------------------------------------------------
schritt 2 "Klipper-Checkout auf ${ZIEL_COMMIT:0:7}"
HEAD_VORHER=$(git -C "$KLIPPER_DIR" rev-parse HEAD)
BRANCH_VORHER=$(git -C "$KLIPPER_DIR" symbolic-ref -q --short HEAD || true)
{ echo "KLIPPER_HEAD_VORHER=$HEAD_VORHER"; echo "KLIPPER_BRANCH_VORHER=$BRANCH_VORHER"; } >> "$BK/state.env"
info "aktuell ${HEAD_VORHER:0:9} (${BRANCH_VORHER:-detached})"
FREMD=$(klipper_fremdaenderungen)
GEAENDERT=0
if [ "$HEAD_VORHER" = "$ZIEL_COMMIT" ]; then
  ok "bereits auf dem geprüften Stand"
  [ -z "$FREMD" ] || warn "lokale Änderungen im Klipper-Checkout (Stand damit NICHT der geprüfte):"$'\n'"$FREMD"
elif [ $KLIPPER_ANFASSEN -eq 0 ]; then
  warn "NICHT auf ${ZIEL_COMMIT:0:7} – wegen --klipper-nicht-anfassen unverändert gelassen (ungeprüfter Stand!)"
else
  if [ -n "$FREMD" ]; then
    echo "$FREMD" | sed 's/^/        /'
    fehler "Klipper-Checkout hat lokale Änderungen (oben) – nichts umgestellt. Aufräumen oder --klipper-nicht-anfassen"
  fi
  if ! git -C "$KLIPPER_DIR" cat-file -e "$ZIEL_COMMIT^{commit}" 2>/dev/null; then
    info "Commit fehlt lokal – git fetch origin"
    git -C "$KLIPPER_DIR" fetch --quiet origin || fehler "git fetch fehlgeschlagen (Netz?)"
    git -C "$KLIPPER_DIR" cat-file -e "$ZIEL_COMMIT^{commit}" 2>/dev/null || fehler "Commit $ZIEL_COMMIT auch nach fetch nicht vorhanden"
  fi
  git -C "$KLIPPER_DIR" checkout --quiet --detach "$ZIEL_COMMIT" || fehler "git checkout fehlgeschlagen"
  GEAENDERT=1
  ok "umgestellt auf ${ZIEL_COMMIT:0:9} (detached HEAD; Moonraker zeigt das als 'detached')"
fi
echo "KLIPPER_GEAENDERT=$GEAENDERT" >> "$BK/state.env"
ln -sfn "$STAMP" "$BACKUP_ROOT/letzte"   # ab hier wird verändert -> Rückbau-Ziel
# Eigene Dateien für git/Moonraker unsichtbar machen (nur lokal, .git/info/exclude)
EXCL="$(git -C "$KLIPPER_DIR" rev-parse --git-path info/exclude)"
case "$EXCL" in /*) ;; *) EXCL="$KLIPPER_DIR/$EXCL" ;; esac
mkdir -p "$(dirname "$EXCL")"; touch "$EXCL"
if grep -q '^# k2-pi-paket BEGIN' "$EXCL"; then ok ".git/info/exclude enthält die K2-Einträge bereits"
else
  printf '%s\n' '# k2-pi-paket BEGIN' '/klippy/extras/k2_*.py' '/klippy/extras/k2_nachbau/' '# k2-pi-paket END' >> "$EXCL"
  echo "EXCLUDE_ANGELEGT=1" >> "$BK/state.env"
  ok "K2-Dateien in .git/info/exclude eingetragen (Checkout bleibt für Moonraker 'sauber')"
fi

# ---------------------------------------------------------------------
schritt 3 "Klipper-Dienst stoppen"
if systemctl is-active --quiet "$DIENST"; then
  sudo -n systemctl stop "$DIENST" || fehler "systemctl stop $DIENST fehlgeschlagen"
  echo "DIENST_WAR_AKTIV=1" >> "$BK/state.env"
  ok "$DIENST gestoppt"
else
  echo "DIENST_WAR_AKTIV=0" >> "$BK/state.env"
  ok "$DIENST lief nicht"
fi

# ---------------------------------------------------------------------
schritt 4 "Module nach klippy/extras/"
E=$KLIPPER_DIR/klippy/extras
mkdir -p "$BK/extras"
VORH=0
for f in "$E"/k2_*.py; do [ -e "$f" ] || continue; cp -a "$f" "$BK/extras/"; VORH=$((VORH+1)); done
[ -d "$E/k2_nachbau" ] && { cp -a "$E/k2_nachbau" "$BK/extras/"; VORH=$((VORH+1)); }
( cd "$BK/extras" && find . -mindepth 1 -maxdepth 1 | sed 's#^\./##' | sort ) > "$BK/extras-vorher.txt"
ok "Backup vorhandener K2-Dateien: $VORH Einträge -> $BK/extras/"
: > "$BK/extras-installiert.txt"
for src in "$PKG"/pi-host/k2_*.py; do
  b=$(basename "$src")
  [ "$b" = k2_motor_init.py ] && dst=k2_motor.py || dst=$b   # Dateiname = Abschnitt [k2_motor]
  install -m 0644 "$src" "$E/$dst"
  cmp -s "$src" "$E/$dst" || fehler "Kopie $dst fehlerhaft"
  echo "$dst" >> "$BK/extras-installiert.txt"
  info "$b -> extras/$dst"
done
rm -rf "$E/k2_nachbau"
mkdir -p "$E/k2_nachbau"
for src in "$PKG"/nachbau/*.py; do
  install -m 0644 "$src" "$E/k2_nachbau/"
  cmp -s "$src" "$E/k2_nachbau/$(basename "$src")" || fehler "Kopie nachbau/$(basename "$src") fehlerhaft"
done
echo "k2_nachbau" >> "$BK/extras-installiert.txt"
ok "$(($(wc -l < "$BK/extras-installiert.txt")-1)) Module + k2_nachbau/ ($(ls "$E/k2_nachbau" | wc -l) Dateien) installiert und verglichen"

# ---------------------------------------------------------------------
schritt 5 "Config nach $CONFIG_DIR"
tar -czf "$BK/config.tar.gz" -C "$(dirname "$CONFIG_DIR")" "$(basename "$CONFIG_DIR")"
( cd "$CONFIG_DIR" && find . -mindepth 1 | sed 's#^\./##' | sort ) > "$BK/config-vorher.txt"
ok "kompletter Config-Ordner gesichert: $BK/config.tar.gz ($(wc -l < "$BK/config-vorher.txt") Einträge)"
: > "$BK/config-installiert.txt"
for src in "$PKG"/pi-host/config-pi/*.cfg; do
  b=$(basename "$src")
  [ "$b" = printer.cfg ] && continue
  python3 "$PKG/k2cfg.py" merge "$src" "$CONFIG_DIR/$b" --alt "$CONFIG_DIR/$b" "${SET_ARGS[@]}"
  chmod 0644 "$CONFIG_DIR/$b"
  echo "$b" >> "$BK/config-installiert.txt"
  info "$b"
done
if [ ! -e "$CONFIG_DIR/printer.cfg" ]; then
  PZIEL=printer.cfg; info "keine printer.cfg vorhanden – K2-printer.cfg wird printer.cfg"
elif [ $PRINTER_ERSETZEN -eq 1 ]; then
  PZIEL=printer.cfg; info "printer.cfg wird ersetzt (--printer-cfg-ersetzen, Original im Backup)"
else
  PZIEL=printer-k2.cfg
fi
SAVE_OPT=()
[ $SAVE_AUS_PAKET -eq 1 ] && SAVE_OPT=(--save-config-aus-paket)
python3 "$PKG/k2cfg.py" merge "$PKG/pi-host/config-pi/printer.cfg" "$CONFIG_DIR/$PZIEL" \
  --alt "$CONFIG_DIR/$PZIEL" "${SET_ARGS[@]}" "${SAVE_OPT[@]}"
chmod 0644 "$CONFIG_DIR/$PZIEL"
echo "$PZIEL" >> "$BK/config-installiert.txt"
echo "PRINTER_ZIEL=$PZIEL" >> "$BK/state.env"
ok "$(wc -l < "$BK/config-installiert.txt") cfg-Dateien abgelegt, Hauptdatei: $PZIEL"
if [ "$PZIEL" = printer-k2.cfg ]; then
  warn "vorhandene printer.cfg NICHT ersetzt. Klipper lädt weiter printer.cfg!"
  warn "Zum Umstellen: erneut mit --printer-cfg-ersetzen, oder von Hand: mv printer-k2.cfg printer.cfg"
fi
if [ "$PZIEL" = printer.cfg ] && [ -e "$CONFIG_DIR/printer-k2.cfg" ]; then
  info "printer-k2.cfg aus einem früheren Lauf liegt noch daneben (wird nicht geladen, kann gelöscht werden)"
fi
if [ -e "$CONFIG_DIR/mainsail.cfg" ]; then
  info "mainsail.cfg vorhanden, wird von der K2-printer.cfg absichtlich NICHT eingebunden"
  info "(eigene PAUSE/RESUME/CANCEL_PRINT + [virtual_sdcard]/[pause_resume]/[display_status] in der K2-Config)"
fi

# ---------------------------------------------------------------------
schritt 6 "Offline-Tests und Batch-Prüfung (ohne Hardware)"
FEHL=0
if [ $TESTS -eq 1 ]; then
  mkdir -p "$BK/tests"
  for t in "$PKG"/pi-host/test_k2_*.py; do
    n=$(basename "$t" .py)
    if ( cd "$PKG/pi-host" && KLIPPER_SRC="$KLIPPER_DIR" K2_MCU_DICT_DIR="$PKG/mcu-dicts" \
         timeout 600 "$PY" -B "$t" ) > "$BK/tests/$n.log" 2>&1; then
      ok "$n: $(grep -E 'ERGEBNIS|BESTANDEN|PASS' "$BK/tests/$n.log" | tail -1)"
    else
      warn "$n FEHLGESCHLAGEN – $BK/tests/$n.log"; FEHL=$((FEHL+1))
    fi
  done
  for m in serial485 box boxmini cfsseq motor motor_mini auto_addr; do
    if ( cd "$PKG/nachbau" && timeout 600 "$PY" -B "$m.py" ) > "$BK/tests/nachbau-$m.log" 2>&1; then
      ok "nachbau/$m Selbsttest: $(grep -viE '^\s*$' "$BK/tests/nachbau-$m.log" | tail -1 | cut -c1-70)"
    else
      warn "nachbau/$m Selbsttest FEHLGESCHLAGEN – $BK/tests/nachbau-$m.log"; FEHL=$((FEHL+1))
    fi
  done
else
  info "Tests übersprungen (--ohne-tests)"
fi
if [ $BATCH -eq 1 ]; then
  BO=$BK/batch; mkdir -p "$BO"
  set +e
  ( cd "$BO" && timeout 300 "$PY" "$KLIPPER_DIR/klippy/klippy.py" "$CONFIG_DIR/$PZIEL" \
      -i /dev/null -o "$BO/out.serial" -v \
      -d "$PKG/mcu-dicts/mcu-main-F008-GD32F303RET6.dict.json" \
      -d "nozzle_mcu=$PKG/mcu-dicts/noz-duese-F008-GD32F303CBT6.dict.json" \
      -l "$BO/klippy.log" ) > "$BO/ausgabe.txt" 2>&1
  BRC=$?
  set -e
  if [ $BRC -eq 0 ] && ! grep -qE '^(Config error|Internal error)|Unhandled exception' "$BO/klippy.log" "$BO/ausgabe.txt"; then
    ok "Batch-Prüfung $PZIEL: EXIT 0, keine Config-/Internal-Fehler ($BO/klippy.log)"
  else
    warn "Batch-Prüfung FEHLGESCHLAGEN (EXIT $BRC) – $BO/klippy.log, $BO/ausgabe.txt:"
    grep -hE '^(Config error|Internal error)|Unhandled exception|[a-z]+\.[Ee]rror: ' "$BO/klippy.log" "$BO/ausgabe.txt" 2>/dev/null | head -5 | sed 's/^/        /'
    FEHL=$((FEHL+1))
  fi
else
  info "Batch-Prüfung übersprungen (--ohne-batch)"
fi
[ $FEHL -eq 0 ] || fehler "$FEHL Prüfung(en) fehlgeschlagen – Dienst bleibt gestoppt. Logs: $BK/tests, $BK/batch. Rückbau: $BK/uninstall.sh"

# ---------------------------------------------------------------------
schritt 7 "Abschluss"
if [ $STARTEN -eq 1 ]; then
  sudo -n systemctl start "$DIENST" || fehler "systemctl start $DIENST fehlgeschlagen"
  ok "$DIENST gestartet (--starten) – klippy.log beobachten"
else
  ok "$DIENST bleibt GESTOPPT (Start: sudo systemctl start $DIENST oder erneut mit --starten)"
fi
echo "FERTIG=1" >> "$BK/state.env"

cat <<EOF

--------------------------------------------------------------------
ZUSAMMENFASSUNG
  Klipper:   $(git -C "$KLIPPER_DIR" rev-parse --short=9 HEAD) (Ziel ${ZIEL_COMMIT:0:9})$([ $GEAENDERT -eq 1 ] && echo ", umgestellt von ${HEAD_VORHER:0:9}")
  Module:    $E/k2_*.py + k2_nachbau/
  Config:    $CONFIG_DIR (Hauptdatei $PZIEL)
  Backup:    $BK   (Rückbau: $BK/uninstall.sh  bzw.  ./uninstall.sh)
  Prüfungen: Tests $([ $TESTS -eq 1 ] && echo grün || echo übersprungen), Batch $([ $BATCH -eq 1 ] && echo 'EXIT 0' || echo übersprungen)
  Dienst:    $([ $STARTEN -eq 1 ] && echo gestartet || echo gestoppt)
--------------------------------------------------------------------
EOF

echo "SERIELLE PORTS (by-id):"
if [ -d /dev/serial/by-id ]; then ls -l /dev/serial/by-id/ | sed '1d; s/^/  /'
else echo "  /dev/serial/by-id/ fehlt – noch kein USB-UART/RS485-Adapter erkannt (Lower-Board/Adapter anstecken)"; fi
echo
echo "Noch Platzhalter (ANNAHME) eintragen:"
if python3 "$PKG/k2cfg.py" platzhalter "$CONFIG_DIR/$PZIEL" "$CONFIG_DIR/k2-module.cfg"; then
  echo "  keine – alle drei serial-Pfade gesetzt"
else
  cat <<EOF
  Zuordnung: Main-MCU-UART  -> $PZIEL      [mcu]             serial:
             Düsen-MCU-UART -> $PZIEL      [mcu nozzle_mcu]  serial:
             USB-RS485      -> k2-module.cfg [k2_485]        serial:
  Bequem:    ./install.sh --main-serial /dev/serial/by-id/... --duese-serial ... --rs485-serial ...
             (Werte bleiben bei späteren Läufen erhalten), baud bleibt 230400.
EOF
fi
cat <<'EOF'

NÄCHSTE SCHRITTE (pi-host/config-pi/README.md §4 – zuerst OHNE Bewegung):
  1. Verkabelung nach PI-ERSATZ-PLAN §3 prüfen; alle Sperren bleiben wie geliefert
     (allow_move/allow_write False, _PI_FREIGABE allow_move False).
  2. Klipper starten, klippy.log: "Klipper ready", beide MCUs 1.1.0.48-293-g493f9a0f.
     Prüfen: Temperaturen ≈ Raum, QUERY_ENDSTOPS (open), QUERY_PROBE (open),
     M106 P0 S128 / M107, G28 MUSS mit "G28 gesperrt" abbrechen, ZALIGN_STATUS.
  3. KTWO_MOTOR_INIT DRY_RUN=1 (Plan P0–P9, 167 Schritte, Strapping 0x81–0x84).
  4. KTWO_BUS_STATUS (offen=True), KTWO_MOTOR_STATUS, KTWO_BOX_STATUS (lesend).
  5. KTWO_PRTOUCH_TEST_SWAP, KTWO_PRTOUCH_READ, KTWO_PRTOUCH_STATUS; PROBE muss abbrechen.
  Alles ab §4 Schritt 4 (schreibend) und 6 (Homing) nur mit Einzelfreigabe,
  Bett leer, Hand am Not-Aus.
  Hinweis: ~/k2-test ist veraltet und wird nicht benutzt (bei Bedarf selbst löschen).
EOF
