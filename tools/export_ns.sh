#!/usr/bin/env bash
# Export Nightscout collections for the in-silico stand, in weekly chunks.
#
#   NS_URL=https://your.site ./tools/export_ns.sh 2026-10-01 2026-10-29
#
# Auth: NS_TOKEN / NS_SECRET from the environment are used without prompting;
# otherwise you are asked, and nothing is written to disk or passed in argv.
#   NS_TOKEN  — Nightscout access token (Admin Tools -> Subjects, readable role)
#   NS_SECRET — API_SECRET in plain form, hashed locally
#   neither   — site must be publicly readable
#
# Collections: devicestatus, treatments, entries (CGM), profile (no date filter).
set -euo pipefail

FROM="${1:?usage: NS_URL=https://site $0 FROM(YYYY-MM-DD) TO(YYYY-MM-DD, exclusive)}"
TO="${2:?missing TO date}"
: "${NS_URL:?set NS_URL, e.g. NS_URL=https://your.site}"
NS_URL="${NS_URL%/}"
OUT="${OUT:-$(cd "$(dirname "$0")/.." && pwd)/data/raw}"
COUNT=20000
mkdir -p "$OUT"
umask 077

AUTH_HEADER=""
if [ -n "${NS_TOKEN:-}" ]; then
  JWT=$(curl -sS --config <(printf 'url = "%s/api/v2/authorization/request/%s"\n' "$NS_URL" "$NS_TOKEN") \
        | python3 -I -c 'import sys,json; print(json.load(sys.stdin)["token"])') \
    || { echo "Could not exchange NS_TOKEN (wrong token or URL?)" >&2; exit 1; }
  AUTH_HEADER="Authorization: Bearer $JWT"
elif [ -n "${NS_SECRET:-}" ]; then
  HASH=$(printf %s "$NS_SECRET" | shasum -a 1 | cut -d' ' -f1)
  AUTH_HEADER="api-secret: $HASH"
else
  echo "Auth: 1) access token  2) API_SECRET  3) none"
  read -r -p "Choice [1/2/3]: " CHOICE
  case "$CHOICE" in
    1)
      read -r -s -p "Access token: " T; echo
      JWT=$(curl -sS --config <(printf 'url = "%s/api/v2/authorization/request/%s"\n' "$NS_URL" "$T") \
            | python3 -I -c 'import sys,json; print(json.load(sys.stdin)["token"])') \
        || { echo "Could not exchange the token (wrong token or URL?)" >&2; exit 1; }
      unset T
      AUTH_HEADER="Authorization: Bearer $JWT"
      ;;
    2)
      read -r -s -p "API_SECRET: " S; echo
      HASH=$(printf %s "$S" | shasum -a 1 | cut -d' ' -f1)
      unset S
      AUTH_HEADER="api-secret: $HASH"
      ;;
    3) ;;
    *) echo "bad choice" >&2; exit 1 ;;
  esac
fi

fetch() { # endpoint url-suffix outfile
  local ep="$1" suffix="$2" file="$3"
  local url="$NS_URL/api/v1/$ep.json?count=$COUNT$suffix"
  local cfg
  cfg=$(printf 'url = "%s"\n' "$url")
  if [ -n "$AUTH_HEADER" ]; then
    cfg+=$(printf '\nheader = "%s"' "$AUTH_HEADER")
  fi
  curl -gsS --fail --config <(printf '%s\n' "$cfg") -o "$file"
  local n
  n=$(python3 -I -c 'import sys,json; d=json.load(open(sys.argv[1])); print(len(d) if isinstance(d,list) else -1)' "$file") \
    || { echo "  $file: not valid JSON (auth failure?)" >&2; return 1; }
  [ "$n" -ge 0 ] || { echo "  $file: not a JSON array" >&2; return 1; }
  [ "$n" -lt "$COUNT" ] || echo "  WARNING: $file hit the $COUNT cap; shorten the range" >&2
  printf '  %-40s %6s records\n' "$(basename "$file")" "$n"
}

addweek() { date -j -v+7d -f %Y-%m-%d "$1" +%Y-%m-%d; }
epoch_ms() { echo $(( $(date -j -u -f "%Y-%m-%d %H:%M:%S" "$1 00:00:00" +%s) * 1000 )); }

echo "Exporting $FROM .. $TO from $NS_URL into $OUT"
cur="$FROM"
while [[ "$cur" < "$TO" ]]; do
  nxt=$(addweek "$cur")
  [[ "$nxt" > "$TO" ]] && nxt="$TO"
  RANGE="&find[created_at][\$gte]=$cur&find[created_at][\$lt]=$nxt"
  # entries carry no created_at, only date (epoch ms)
  ERANGE="&find[date][\$gte]=$(epoch_ms "$cur")&find[date][\$lt]=$(epoch_ms "$nxt")"
  fetch devicestatus "$RANGE" "$OUT/devicestatus_$cur.json"
  fetch treatments    "$RANGE" "$OUT/treatments_$cur.json"
  fetch entries       "$ERANGE" "$OUT/entries_$cur.json"
  cur="$nxt"
done

fetch profile "" "$OUT/profile.json"
echo "Done. Raw data in $OUT — gitignored, never commit it."
