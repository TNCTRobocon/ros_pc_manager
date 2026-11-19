#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[[ -x "$ROOT/.venv/bin/python" ]] || {
  echo "./scripts/setup.sh を先に実行してください。" >&2
  exit 1
}

# Prepare and validate Agent YAML without registering or starting a systemd service.
exec "$ROOT/.venv/bin/python" "$ROOT/scripts/service_setup.py" prepare-agent
