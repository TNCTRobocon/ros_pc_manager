#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -x "$ROOT/.venv/bin/ros-pc-manager-agent" ]] || {
  echo "./scripts/setup.sh を先に実行してください。" >&2
  exit 1
}
if [[ ! -f "$ROOT/agent/config/agent.yaml" || ! -f "$ROOT/agent/config/launches.yaml" ]]; then
  "$ROOT/scripts/prepare_agent_config.sh"
fi
exec "$ROOT/.venv/bin/ros-pc-manager-agent" --config "$ROOT/agent/config/agent.yaml"
