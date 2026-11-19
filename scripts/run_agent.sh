#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ ! -f agent/config/agent.yaml ] || [ ! -f agent/config/launches.yaml ]; then
  echo "Run scripts/prepare_agent_config.sh and edit the Agent YAML files first." >&2
  exit 1
fi
exec .venv/bin/ros-pc-manager-agent --config "$ROOT/agent/config/agent.yaml"
