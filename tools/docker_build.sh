#!/bin/bash
# Build the runtime image: flatten the host sim-bridge classpath, then docker build.
# Requires ./tools/bootstrap.sh to have produced the classpath.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLASSPATH="${SIM_CLASSPATH:-$HOME/Projects/Dia/AndroidAPS-w1/sim-bridge/build/sim-classpath.txt}"

if [ ! -f "$CLASSPATH" ]; then
  echo "classpath not found: $CLASSPATH" >&2
  echo "run ./tools/bootstrap.sh first, or set SIM_CLASSPATH" >&2
  exit 1
fi

rm -rf "$ROOT/build/docker-lib"
"$ROOT/tools/fat_classpath.sh" "$CLASSPATH" "$ROOT/build/docker-lib" /opt/sim/lib

docker build -t aaps-insilico:local "$ROOT"
echo "image: aaps-insilico:local"
echo "run:   docker compose up -d   ->  http://127.0.0.1:8766/?live=1"
