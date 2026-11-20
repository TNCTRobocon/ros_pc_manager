#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$ROOT/agent/config/agent.yaml"
[[ -x "$ROOT/.venv/bin/python" ]] || { echo "./scripts/setup.sh を先に実行してください。" >&2; exit 1; }
[[ -f "$CONFIG" ]] || { echo "agent.yaml がありません。先に ./scripts/setup_agent_service.sh を実行してください。" >&2; exit 1; }
exec "$ROOT/.venv/bin/python" - "$CONFIG" "$@" <<'PY'
from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import socket
import sys
from pathlib import Path

import httpx
import psutil
import segno
import yaml


def private_ipv4_addresses() -> list[str]:
    found: set[str] = set()
    for interface, entries in psutil.net_if_addrs().items():
        if interface == "lo" or interface.startswith(("docker", "br-", "veth")):
            continue
        for entry in entries:
            if entry.family != socket.AF_INET:
                continue
            try:
                address = ipaddress.ip_address(entry.address)
            except ValueError:
                continue
            if address.version == 4 and address.is_private and not (
                address.is_loopback or address.is_link_local or address.is_unspecified
            ):
                found.add(str(address))
    return sorted(found)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print a one-time QR code to pair this Agent with the smartphone browser."
    )
    parser.add_argument(
        "--address",
        help="LAN IPv4 address to put in the QR code (required when multiple addresses exist).",
    )
    args = parser.parse_args()

    config_path = Path(sys.argv[1])
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    agent = config.get("agent", {})
    token = str(agent.get("token", ""))
    if len(token) < 24:
        raise SystemExit("Agent Tokenが設定されていません。先にsetup_agent_service.shを実行してください。")

    port = int(agent.get("port", 8765))
    domain_id = int(agent.get("default_domain_id", 0))
    addresses = private_ipv4_addresses()
    if args.address:
        try:
            selected = ipaddress.ip_address(args.address)
        except ValueError as exc:
            raise SystemExit("--addressにはIPv4アドレスを指定してください。") from exc
        if selected.version != 4 or not selected.is_private or str(selected) not in addresses:
            raise SystemExit("指定アドレスはこのPCのプライベートIPv4アドレスではありません。")
        address = str(selected)
    elif len(addresses) == 1:
        address = addresses[0]
    elif not addresses:
        raise SystemExit("LAN側のプライベートIPv4アドレスが見つかりません。")
    else:
        print("QRコードに使用するLAN IPv4アドレスを選択してください:")
        for index, value in enumerate(addresses, start=1):
            print(f"  {index}. {value}")
        try:
            choice = int(input("番号: ").strip())
            address = addresses[choice - 1]
        except (ValueError, IndexError) as exc:
            raise SystemExit("有効な番号を選択してください。") from exc

    base_url = f"http://127.0.0.1:{port}"
    try:
        response = httpx.post(
            base_url + "/api/pairing/create",
            headers={"Authorization": "Bearer " + token},
            timeout=5.0,
        )
        response.raise_for_status()
        pairing = response.json()
    except httpx.HTTPError as exc:
        raise SystemExit(
            f"Agent APIに接続できません: {exc}\n"
            "Agentが起動中か、設定されたポートを確認してください。"
        ) from exc

    payload = {
        "type": "ros-pc-manager-agent-pair",
        "version": 1,
        "agent_id": pairing["agent_id"],
        "name": pairing["name"],
        "address": address,
        "port": port,
        "domain_id": domain_id,
        "code": pairing["code"],
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).decode("ascii").rstrip("=")
    pairing_url = f"https://tnctrobocon.github.io/ros_pc_manager/#pair={encoded}"
    qr = segno.make(pairing_url, error="m")

    print()
    print(f"Agent: {pairing['name']} ({address}:{port})")
    print(f"有効期限: {int(pairing.get('expires_in', 300))}秒")
    print("以下のQRコードをスマートフォンのカメラで読み取ってください。")
    print("QRには5分で失効する一時コードだけが含まれ、Agent Tokenは含まれません。")
    print()
    qr.terminal(compact=True)
    print()
    print("QRコードを読み取った後、ブラウザで「ペアリングして登録」を押してください。")
    print("コードが期限切れになった場合は、このコマンドを再実行してください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
