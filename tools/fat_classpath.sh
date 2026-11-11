#!/bin/bash
# Flatten a Gradle runtime classpath into one directory and rewrite the classpath file.
#   ./tools/fat_classpath.sh /aaps/sim-bridge/build/sim-classpath.txt /out/lib
set -euo pipefail

CP_FILE="${1:?usage: fat_classpath.sh <sim-classpath.txt> <out-dir> [target-prefix]}"
OUT="${2:?usage: fat_classpath.sh <sim-classpath.txt> <out-dir> [target-prefix]}"
PREFIX="${3:-$OUT}"

mkdir -p "$OUT"
LIST="$OUT/.entries"
: > "$LIST"

index=0
while IFS= read -r entry; do
  [ -z "$entry" ] && continue
  [ -e "$entry" ] || continue
  index=$((index + 1))
  base=$(basename "$entry")
  dest="$OUT/$(printf '%04d' "$index")-$base"
  if [ -d "$entry" ]; then
    cp -R "$entry" "$dest"
  else
    cp "$entry" "$dest"
  fi
  printf '%s/%s\n' "$PREFIX" "$(basename "$dest")" >> "$LIST"
done < <(tr ':' '\n' < "$CP_FILE")

paste -sd: "$LIST" > "$OUT/sim-classpath.txt"
rm -f "$LIST"
echo "wrote $OUT/sim-classpath.txt ($index entries)"
