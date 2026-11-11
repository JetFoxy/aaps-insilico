#!/bin/bash
# Prepare the AAPS worktree and build the sim-bridge classpath.
#
#   AAPS_REPO=~/Projects/Dia/AndroidAPS  AAPS_PIN=74271a544b  ./tools/bootstrap.sh
#
# The worktree is disposable: nothing is committed to AAPS, `sim-bridge/` exists only there.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AAPS_REPO="${AAPS_REPO:-$HOME/Projects/Dia/AndroidAPS}"
AAPS_PIN="${AAPS_PIN:-74271a544b}"
AAPS_WT="${AAPS_WT:-$HOME/Projects/Dia/AndroidAPS-w1}"

if [ ! -d "$AAPS_REPO" ]; then
  echo "AAPS repo not found: $AAPS_REPO (set AAPS_REPO)" >&2
  exit 1
fi

if [ ! -d "$AAPS_WT" ]; then
  echo "creating worktree $AAPS_WT at $AAPS_PIN"
  git -C "$AAPS_REPO" worktree add -d "$AAPS_WT" "$AAPS_PIN"
fi

AAPS_WT="$AAPS_WT" "$ROOT/tools/sync_bridge.sh"

echo "building :sim-bridge"
(cd "$AAPS_WT" && ./gradlew :sim-bridge:simClasspath --configure-on-demand -q)

CLASSPATH="$AAPS_WT/sim-bridge/build/sim-classpath.txt"
echo "classpath: $CLASSPATH"
echo "export SIM_CLASSPATH=\"$CLASSPATH\""
