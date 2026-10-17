#!/bin/bash
# =====================================================================
# K2 Plus – Rückbau einer install.sh-Installation aus ~/k2-pi-backup/
#   ./uninstall.sh [--backup DIR | --aelteste] [--klipper-lassen] [--starten] [--ja]
# Standard: letztes Backup (~/k2-pi-backup/letzte). Nach mehreren
# install.sh-Läufen führt --aelteste in einem Schritt zum Zustand VOR der
# ersten Installation zurück.
# =====================================================================
set -eu
set -o pipefail

BACKUP_ROOT=${BACKUP_ROOT:-$HOME/k2-pi-backup}
BK=""
AELTESTE=0
KLIPPER_LASSEN=0
STARTEN=0
JA=0
ZIEL_COMMIT=9cb99754c035b90206ef0a02660f674fc9517e9b
MATDB_REL=k2/material_database.json   # wie install.sh

hilfe() {
  cat <<'EOF'
Aufruf: ./uninstall.sh [Optionen]
  --backup DIR      bestimmtes Backup (~/k2-pi-backup/<stamp>) zurückspielen
  --aelteste        ältestes Backup = Zustand vor der ersten Installation
  --klipper-lassen  Klipper-Checkout NICHT auf den vorherigen Stand zurückstellen
  --starten         Klipper-Dienst danach starten (Standard: bleibt gestoppt)
  --ja              ohne Rückfrage
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --backup) [ $# -ge 2 ] || { echo "FEHLER: --backup braucht einen Wert" >&2; exit 2; }; BK=$2; shift ;;
    --aelteste) AELTESTE=1 ;;
    --klipper-lassen) KLIPPER_LASSEN=1 ;;
    --starten) STARTEN=1 ;;
    --ja) JA=1 ;;
    --hilfe|-h|--help) hilfe; exit 0 ;;
    *) echo "FEHLER: unbekannte Option: $1 (--hilfe)" >&2; exit 2 ;;
  esac
  shift
done

SCHRITT=""
schritt() { SCHRITT=$1; echo; echo "=== [$1] $2 ==="; }
ok()     { echo "  ok    $*"; }
info()   { echo "  info  $*"; }
warn()   { echo "  WARN  $*"; }
fehler() { echo; echo "FEHLER in Schritt [$SCHRITT]: $*" >&2; exit 1; }

schritt 1 "Backup wählen"
[ "$(id -u)" -ne 0 ] || fehler "nicht als root/mit sudo starten – als Benutzer pi ausführen"
if [ -z "$BK" ]; then
  if [ $AELTESTE -eq 1 ]; then
    for d in $(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '2*' 2>/dev/null | sort); do
      grep -q '^KLIPPER_GEAENDERT=' "$d/state.env" 2>/dev/null && { BK=$d; break; }
    done
  else
    BK=$(readlink -f "$BACKUP_ROOT/letzte" 2>/dev/null || true)
  fi
fi
[ -n "$BK" ] && [ -f "$BK/state.env" ] || fehler "kein gültiges Backup gefunden (${BK:-$BACKUP_ROOT}) – ggf. --backup DIR oder --aelteste"
[ -f "$BK/config.tar.gz" ] || warn "$BK/config.tar.gz fehlt (Installation brach vor Schritt 5 ab) – Config bleibt unverändert"
# state.env nur als Daten lesen (keine Ausführung)
st() { sed -n "s/^$1=//p" "$BK/state.env" | tail -1; }
KLIPPER_DIR=$(st KLIPPER_DIR); CONFIG_DIR=$(st CONFIG_DIR); DIENST=$(st DIENST)
HEAD_VORHER=$(st KLIPPER_HEAD_VORHER); BRANCH_VORHER=$(st KLIPPER_BRANCH_VORHER)
GEAENDERT=$(st KLIPPER_GEAENDERT); EXCL_ANGELEGT=$(st EXCLUDE_ANGELEGT)
[ -n "$KLIPPER_DIR" ] && [ -n "$CONFIG_DIR" ] && [ -n "$DIENST" ] || fehler "state.env unvollständig"
ok "Backup $BK"
info "Klipper $KLIPPER_DIR, Config $CONFIG_DIR, Dienst $DIENST"
if [ $JA -eq 0 ]; then
  printf '  Zurückspielen? Aktueller Config-Ordner wird vorher zusätzlich gesichert. [j/N] '
  read -r a; case "$a" in j|J|ja|Ja) ;; *) echo "abgebrochen"; exit 1 ;; esac
fi

schritt 2 "Klipper-Dienst stoppen"
if systemctl is-active --quiet "$DIENST"; then
  sudo -n systemctl stop "$DIENST" || fehler "systemctl stop $DIENST fehlgeschlagen"; ok "$DIENST gestoppt"
else ok "$DIENST lief nicht"; fi

schritt 3 "Module zurück"
E=$KLIPPER_DIR/klippy/extras
if [ ! -f "$BK/extras-vorher.txt" ]; then
  info "Installation brach vor Schritt 4 ab – Module unverändert gelassen"
else
N=0
for f in "$E"/k2_*.py; do [ -e "$f" ] || continue; rm -f "$f"; N=$((N+1)); done
[ -d "$E/k2_nachbau" ] && { rm -rf "$E/k2_nachbau"; N=$((N+1)); }
find "$E/__pycache__" -maxdepth 1 -name 'k2_*.pyc' -delete 2>/dev/null || true
ok "$N K2-Einträge entfernt"
R=0
if [ -d "$BK/extras" ]; then
  for f in "$BK/extras"/* ; do [ -e "$f" ] || continue; cp -a "$f" "$E/"; R=$((R+1)); done
fi
ok "$R Einträge aus dem Backup wiederhergestellt"
fi

schritt 4 "Config zurück"
if [ -f "$BK/config.tar.gz" ]; then
SICH="$(dirname "$BK")/vor-rueckbau-$(date +%Y%m%d-%H%M%S).tar.gz"
tar -czf "$SICH" -C "$(dirname "$CONFIG_DIR")" "$(basename "$CONFIG_DIR")"
ok "aktueller Stand zusätzlich gesichert: $SICH"
if [ -f "$BK/config-installiert.txt" ]; then
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    if ! grep -qxF "$f" "$BK/config-vorher.txt"; then rm -f "$CONFIG_DIR/$f"; info "entfernt: $f"; fi
  done < "$BK/config-installiert.txt"
fi
tar -xzf "$BK/config.tar.gz" -C "$(dirname "$CONFIG_DIR")"
ok "Config-Ordner aus $BK/config.tar.gz wiederhergestellt"
# Material-DB: Zustand dieses Backups herstellen (auch bei --aelteste, wenn sie
# erst ein späterer Lauf angelegt hat)
MZ=$CONFIG_DIR/$MATDB_REL
if [ -f "$BK/material-db/material_database.json" ]; then
  if ! cmp -s "$BK/material-db/material_database.json" "$MZ"; then
    mkdir -p "$(dirname "$MZ")"; cp -a "$BK/material-db/material_database.json" "$MZ"
  fi
  ok "Material-DB $MATDB_REL = Backup-Stand ($BK/material-db/)"
elif [ -f "$BK/config-vorher.txt" ] && ! grep -qxF "$MATDB_REL" "$BK/config-vorher.txt"; then
  if [ -e "$MZ" ]; then rm -f "$MZ"; info "entfernt: $MATDB_REL (gab es vor diesem Lauf nicht)"; fi
  if ! grep -qxF "$(dirname "$MATDB_REL")" "$BK/config-vorher.txt"; then
    rmdir "$CONFIG_DIR/$(dirname "$MATDB_REL")" 2>/dev/null && info "entfernt: leerer Ordner $(dirname "$MATDB_REL")/" || true
  fi
fi
else
  info "kein Config-Backup in diesem Lauf – übersprungen"
fi

schritt 5 "Klipper-Checkout"
if [ "$GEAENDERT" != 1 ]; then
  ok "Installation hatte den Checkout nicht verändert"
elif [ $KLIPPER_LASSEN -eq 1 ]; then
  info "--klipper-lassen: bleibt auf $(git -C "$KLIPPER_DIR" rev-parse --short=9 HEAD)"
else
  CUR=$(git -C "$KLIPPER_DIR" rev-parse HEAD)
  DIRTY=$(git -C "$KLIPPER_DIR" status --porcelain --untracked-files=no)
  if [ "$CUR" != "$ZIEL_COMMIT" ]; then
    warn "Checkout steht inzwischen auf ${CUR:0:9} (nicht ${ZIEL_COMMIT:0:9}) – nicht angefasst"
  elif [ -n "$DIRTY" ]; then
    warn "lokale Änderungen im Klipper-Checkout – nicht angefasst:"; echo "$DIRTY" | sed 's/^/        /'
  elif [ -n "$BRANCH_VORHER" ]; then
    git -C "$KLIPPER_DIR" checkout --quiet "$BRANCH_VORHER"
    [ "$(git -C "$KLIPPER_DIR" rev-parse HEAD)" = "$HEAD_VORHER" ] \
      || warn "Branch $BRANCH_VORHER zeigt jetzt auf $(git -C "$KLIPPER_DIR" rev-parse --short=9 HEAD), vorher ${HEAD_VORHER:0:9}"
    ok "zurück auf Branch $BRANCH_VORHER"
  else
    git -C "$KLIPPER_DIR" checkout --quiet --detach "$HEAD_VORHER"; ok "zurück auf ${HEAD_VORHER:0:9}"
  fi
fi
EXCL="$(git -C "$KLIPPER_DIR" rev-parse --git-path info/exclude)"
case "$EXCL" in /*) ;; *) EXCL="$KLIPPER_DIR/$EXCL" ;; esac
if [ "$EXCL_ANGELEGT" = 1 ] && [ -f "$EXCL" ] && grep -q '^# k2-pi-paket BEGIN' "$EXCL"; then
  sed -i '/^# k2-pi-paket BEGIN$/,/^# k2-pi-paket END$/d' "$EXCL"; ok "K2-Einträge aus .git/info/exclude entfernt"
fi

schritt 6 "Abschluss"
if [ $STARTEN -eq 1 ]; then sudo -n systemctl start "$DIENST"; ok "$DIENST gestartet"
else ok "$DIENST bleibt gestoppt (sudo systemctl start $DIENST)"; fi
echo "ZURUECKGESPIELT=$(date +%Y%m%d-%H%M%S)" >> "$BK/state.env"
# 'letzte' entfernen, damit ein weiterer Aufruf nicht versehentlich einen älteren Installationsstand zurückholt
[ "$(readlink -f "$BACKUP_ROOT/letzte" 2>/dev/null)" = "$(readlink -f "$BK")" ] && rm -f "$BACKUP_ROOT/letzte"
[ -L "$BACKUP_ROOT/letzte" ] && [ $AELTESTE -eq 1 ] && rm -f "$BACKUP_ROOT/letzte"
echo
echo "Rückbau aus $BK fertig. Das Backup selbst bleibt liegen (manuell löschen: rm -rf $BACKUP_ROOT)."
