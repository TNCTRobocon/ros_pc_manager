#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ ! -x .venv/bin/ros-pc-manager-server ]; then echo "Run scripts/setup.sh first." >&2; exit 1; fi
exec .venv/bin/ros-pc-manager-server
