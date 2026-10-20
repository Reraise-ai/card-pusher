#!/usr/bin/env bash
# Prueft, dass die versionierten Dateien keine Spuren einer privaten Installation enthalten
# (Servernamen, interne Adressen, feste Laufwerkspfade, Personen- und Kontennamen, Zugangsdaten).
#
# Aufruf aus dem Repo: bash tools/spuren-pruefen.sh        (0 Treffer = Exit 0)
#
# Zwei Muster, bewusst getrennt:
#  1. Allgemeines Muster (unten, oeffentlich): feste Benutzer-/Projektpfade unter Windows,
#     Adressen aus dem Bereich 100.64.0.0/10 (Tailscale/CGNAT), Schluessel und Tokens.
#  2. Privates Muster mit den konkreten Namen der eigenen Installation. Es steht NICHT im Repo
#     (sonst wuerde die Pruefliste selbst die Namen veroeffentlichen), sondern in einer Datei mit
#     einem Regex je Zeile: $SPUREN_MUSTER_DATEI oder ~/.config/reraise/spuren-muster.txt.
#     Fehlt die Datei, laeuft nur das allgemeine Muster und das Skript sagt es.
# Bewusste Ausnahmen stehen als Zeilen-Regex in tools/spuren-ausnahmen.txt.
set -u
cd "$(dirname "$0")/.." || exit 2
ALLGEMEIN='[A-Za-z]:\\+(Users|Projects)\\+|\b100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}\b|BEGIN [A-Z ]*PRIVATE KEY|\bghp_[A-Za-z0-9]{20,}|\bsk-[A-Za-z0-9]{20,}'
MUSTERDATEI="${SPUREN_MUSTER_DATEI:-$HOME/.config/reraise/spuren-muster.txt}"
MUSTER="$ALLGEMEIN"
if [ -f "$MUSTERDATEI" ]; then
  privat=$(grep -v -e '^#' -e '^$' "$MUSTERDATEI" | paste -sd'|' -)
  [ -n "$privat" ] && MUSTER="$MUSTER|$privat"
else
  echo "Hinweis: kein privates Muster ($MUSTERDATEI); es laeuft nur das allgemeine Muster." >&2
fi
AUSNAHMEN=tools/spuren-ausnahmen.txt
treffer=$(git ls-files -z | grep -zv -e '^tools/spuren-pruefen\.sh$' -e '^tools/spuren-ausnahmen\.txt$' \
  | xargs -0 grep -I -n -i -E "$MUSTER" 2>/dev/null || true)
if [ -f "$AUSNAHMEN" ] && [ -n "$treffer" ]; then
  treffer=$(printf '%s\n' "$treffer" | grep -v -E -f <(grep -v -e '^#' -e '^$' "$AUSNAHMEN") || true)
fi
if [ -n "$treffer" ]; then
  printf '%s\n' "$treffer"
  echo "Spuren gefunden: $(printf '%s\n' "$treffer" | wc -l) Treffer" >&2
  exit 1
fi
echo "Keine Spuren gefunden (0 Treffer)."
