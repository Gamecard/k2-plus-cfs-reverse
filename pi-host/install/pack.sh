#!/bin/bash
# =====================================================================
# K2 Plus – Installationspaket für den Pi bauen (läuft auf dem Arbeits-PC)
#   pi-host/install/pack.sh [-o AUSGABEORDNER] [--tests]
# Sammelt den AKTUELLEN Stand von pi-host/ (Module, Tests, config-pi),
# nachbau/, MCU-Dictionaries und die Installationsskripte in
#   k2-pi-paket-<datum>.tar.gz  (+ .sha256)
# Standard-Ausgabe: arbeitsdateien-lokal/pi-paket/
# Keine Verbindung zum Pi/Drucker.
# =====================================================================
set -eu
set -o pipefail

HIER=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HIER/../.." && pwd)
OUT=$REPO/arbeitsdateien-lokal/pi-paket
TESTS=0
DICTS=$REPO/arbeitsdateien-lokal/lokale MCU-Dictionaries
DICT_DATEIEN="mcu-main-F008-GD32F303RET6.dict.json noz-duese-F008-GD32F303CBT6.dict.json"
# Nur fürs Stock-Gerät / Werkzeuge am Arbeits-PC – gehören NICHT aufs Pi-Paket:
AUSGESCHLOSSEN="bustest_lesend.py emu_prtouch_stock.py prtouch_fingertest.py prtouch_lesetest.py prtouch_vorabtest.py"

while [ $# -gt 0 ]; do
  case "$1" in
    -o) [ $# -ge 2 ] || { echo "FEHLER: -o braucht einen Ordner" >&2; exit 2; }; OUT=$2; shift ;;
    --tests) TESTS=1 ;;
    -h|--hilfe|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "FEHLER: unbekannte Option $1" >&2; exit 2 ;;
  esac
  shift
done

fehler() { echo "FEHLER: $*" >&2; exit 1; }
[ -d "$REPO/pi-host" ] && [ -d "$REPO/nachbau" ] || fehler "Repo-Struktur nicht gefunden unter $REPO"
for d in $DICT_DATEIEN; do [ -f "$DICTS/$d" ] || fehler "MCU-Dictionary fehlt: $DICTS/$d"; done

STAMP=$(date +%Y%m%d-%H%M)
NAME=k2-pi-paket-$STAMP
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
P=$TMP/$NAME
mkdir -p "$P/pi-host/config-pi" "$P/nachbau/ref-hi" "$P/mcu-dicts"

# --- Inhalte ---------------------------------------------------------
cp "$HIER/install.sh" "$HIER/uninstall.sh" "$HIER/k2cfg.py" "$HIER/README.md" "$P/"
chmod 0755 "$P/install.sh" "$P/uninstall.sh"
for f in "$REPO"/pi-host/k2_*.py "$REPO"/pi-host/test_k2_*.py; do cp "$f" "$P/pi-host/"; done
cp "$REPO/pi-host/prtouch_mcu_formats.json" "$P/pi-host/"          # von test_k2_prtouch gebraucht
cp "$REPO"/pi-host/README*.md "$P/pi-host/"                          # Doku zu den Modulen
cp "$REPO"/pi-host/config-pi/*.cfg "$REPO"/pi-host/config-pi/*.md "$P/pi-host/config-pi/"
cp "$REPO"/nachbau/*.py "$P/nachbau/"
cp "$REPO"/nachbau/ref-hi/* "$P/nachbau/ref-hi/"                      # Testvektoren der Selbsttests
cp "$REPO/belt_mdl-live.py" "$P/"                                     # Original (GPLv3) für test_k2_belt-Vergleich
for d in $DICT_DATEIEN; do cp "$DICTS/$d" "$P/mcu-dicts/"; done
for x in $AUSGESCHLOSSEN; do rm -f "$P/pi-host/$x"; done
find "$P" -name '__pycache__' -prune -exec rm -rf {} +
[ -f "$P/pi-host/k2_motor_init.py" ] || fehler "pi-host/k2_motor_init.py fehlt"

# --- Prüfungen -------------------------------------------------------
# Geheimnisse/Zugangsdaten/Netz (Paket bleibt privat, trotzdem nichts davon hinein)
# Persönliche Muster (Passwörter, Mail …) stehen NICHT hier, sondern lokal in
# arbeitsdateien-lokal/pi-paket/leak-muster.txt (eine ERE pro Zeile, nicht im Repo).
MUSTER='passw(or)?d *[:=]|PRIVATE KEY|192\.168\.[0-9]+\.[0-9]+|[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z.]{2,}|PI-ZUGANG'
LM="$(dirname "$0")/../../arbeitsdateien-lokal/pi-paket/leak-muster.txt"
if [ -f "$LM" ]; then
  while IFS= read -r z; do [ -n "$z" ] && MUSTER="$MUSTER|$z"; done < "$LM"
else
  echo "Hinweis: $LM fehlt – nur allgemeine Leak-Muster geprüft" >&2
fi
if grep -rnIiE "$MUSTER" "$P"; then
  fehler "Leak-Muster im Paketinhalt (oben) – Paket NICHT gebaut"
fi
for f in "$P"/*.md "$P"/pi-host/*.md "$P"/pi-host/config-pi/*.md; do
  case "$(basename "$f")" in [U]EBERGABE*|CHECKLISTE*|PI-ZUGANG*|PROTOKOLL*) fehler "unerwünschte Datei $f";; esac
done
bash -n "$P/install.sh"; bash -n "$P/uninstall.sh"
python3 - "$P" <<'PY'
import ast, os, sys
n = 0
for root, _d, files in os.walk(sys.argv[1]):
    for f in files:
        if f.endswith(".py"):
            p = os.path.join(root, f)
            ast.parse(open(p, encoding="utf-8").read(), p)
            n += 1
print("  Python-Syntax ok: %d Dateien" % n)
PY
if [ $TESTS -eq 1 ]; then
  echo "  Tests im Paket (KLIPPER_SRC=arbeitsdateien-lokal/upstream-klipper):"
  for t in "$P"/pi-host/test_k2_*.py; do
    ( cd "$P/pi-host" && KLIPPER_SRC="$REPO/arbeitsdateien-lokal/upstream-klipper" K2_MCU_DICT_DIR="$P/mcu-dicts" \
      python3 -B "$t" ) > "$TMP/t.log" 2>&1 || { tail -20 "$TMP/t.log"; fehler "$(basename "$t") rot"; }
    echo "    $(basename "$t"): $(grep -E 'ERGEBNIS|BESTANDEN|PASS' "$TMP/t.log" | tail -1)"
  done
fi

# --- Info + Prüfsummen + Tarball ------------------------------------
{
  echo "Paket: $NAME"
  echo "Erstellt: $(date '+%Y-%m-%d %H:%M:%S %z')"
  echo "Klipper-Ziel: 9cb99754c035b90206ef0a02660f674fc9517e9b (Upstream, geprüfter Stand)"
  echo "Inhalt: pi-host/k2_*.py, test_k2_*.py, config-pi/, nachbau/, mcu-dicts/, install.sh, uninstall.sh, k2cfg.py"
  echo "Quelldateien (sha256, Kurzform):"
  ( cd "$P" && find pi-host nachbau -name '*.py' -o -name '*.cfg' | sort | xargs sha256sum | awk '{printf "  %s  %s\n", substr($1,1,12), $2}' )
} > "$P/PAKET-INFO.txt"
( cd "$P" && find . -type f ! -name SHA256SUMS | sed 's#^\./##' | LC_ALL=C sort | xargs sha256sum > SHA256SUMS )
mkdir -p "$OUT"
tar --sort=name --owner=0 --group=0 --numeric-owner -czf "$OUT/$NAME.tar.gz" -C "$TMP" "$NAME"
( cd "$OUT" && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256" )

echo
echo "Paket:    $OUT/$NAME.tar.gz ($(du -h "$OUT/$NAME.tar.gz" | cut -f1), $(wc -l < "$P/SHA256SUMS") Dateien)"
echo "Prüfsumme: $(cut -d' ' -f1 "$OUT/$NAME.tar.gz.sha256")"
echo
echo "Auf den Pi (siehe pi-host/install/README.md):"
echo "  scp $OUT/$NAME.tar.gz pi@<pi-host>:~"
echo "  ssh pi@<pi-host> 'tar xzf $NAME.tar.gz && cd $NAME && ./install.sh'"
