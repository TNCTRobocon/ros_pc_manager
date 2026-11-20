#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$ROOT/agent/config/agent.yaml"
[[ -f "$CONFIG" ]] || { echo "agent.yaml がありません。先に ./scripts/setup_agent_service.sh を実行してください。" >&2; exit 1; }
"$ROOT/.venv/bin/python" - "$CONFIG" <<'PY'
import sys, yaml
from pathlib import Path
config = Path(sys.argv[1])
raw = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
agent = raw.get("agent", {})
print("Agent ID:", agent.get("id", ""))
print("Agent name:", agent.get("name", ""))
print("Agent Token:", agent.get("token", ""))
PY
