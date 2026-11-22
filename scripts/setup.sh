#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 1
fi
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
mkdir -p var agent/config

cat <<'EOF'
ROS PC Manager Agent setup complete.

Next:
  ./scripts/setup_agent_service.sh  # create config, grant poweroff permission, install systemd, enable auto-start and start Agent
  ./scripts/show_agent_pairing.sh   # print a one-time QR code for smartphone pairing
  ./scripts/show_agent_token.sh     # display the Agent Token for manual pairing

For manual foreground operation:
  ./scripts/prepare_agent_config.sh
  ./scripts/run_agent.sh

There is no required central Manager Server.
EOF
