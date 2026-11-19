#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p agent/config
[ -f agent/config/agent.yaml ] || cp agent/config/agent.yaml.example agent/config/agent.yaml
[ -f agent/config/launches.yaml ] || cp agent/config/launches.yaml.example agent/config/launches.yaml
chmod 600 agent/config/agent.yaml
echo "Edit agent/config/agent.yaml and launches.yaml before starting the Agent."
