#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if ! command -v python3 >/dev/null 2>&1; then echo "python3 is required" >&2; exit 1; fi
if [ ! -d .venv ]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
mkdir -p var config agent/config
[ -f config/server.yaml ] || cp config/server.yaml.example config/server.yaml
[ -f config/domains.yaml ] || cp config/domains.yaml.example config/domains.yaml
echo "Setup complete. Server: .venv/bin/ros-pc-manager-server"
