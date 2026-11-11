#!/bin/bash
# Copy bridge/ into the pinned AAPS worktree as an extra Gradle module.
# Nothing is committed to AAPS; the worktree is disposable.
set -euo pipefail

WT="${AAPS_WT:-$HOME/Projects/Dia/AndroidAPS-w1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ ! -d "$WT" ]; then
  echo "worktree not found: $WT" >&2
  echo "create it with: git -C ~/Projects/Dia/AndroidAPS worktree add -d $WT 74271a544b" >&2
  exit 1
fi

rm -rf "$WT/sim-bridge"
mkdir -p "$WT/sim-bridge"
cp -R "$ROOT/bridge/src" "$WT/sim-bridge/src"
cp "$ROOT/bridge/build.gradle.kts" "$WT/sim-bridge/build.gradle.kts"

if ! grep -q ":sim-bridge" "$WT/settings.gradle"; then
  printf "\ninclude ':sim-bridge'\n" >> "$WT/settings.gradle"
fi

echo "synced bridge -> $WT/sim-bridge"
