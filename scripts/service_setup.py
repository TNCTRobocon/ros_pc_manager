#!/usr/bin/env python3
"""Prepare and validate ROS PC Manager configs and systemd unit files.

This helper deliberately never overwrites an existing YAML configuration.
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import shutil
import socket
import subprocess
import secrets
import uuid
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANAGED_MARKER = "# Managed by ROS PC Manager setup scripts. Do not edit manually."
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{24,}$")


class SetupError(RuntimeError):
    """A setup precondition or configuration validation failed."""


def load_yaml_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SetupError(f"{label}を読み込めません: {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise SetupError(f"{label}のYAML形式が不正です: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SetupError(f"{label}はYAMLマッピングである必要があります: {path}")
    return raw


def atomic_write_yaml(path: Path, data: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp.{os.getpid()}")
    try:
        temp.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        temp.chmod(mode)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def resolve_config_path(value: str, config_path: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = config_path.parent / path
    return path.resolve()


def valid_agent_token(value: Any) -> bool:
    token = str(value or "")
    return token == token.strip() and bool(TOKEN_PATTERN.fullmatch(token)) and token.lower() not in {
        "replace_with_manager_token",
        "replace_with_agent_token",
        "replace_with_auto_generated_token",
    }


def prompt_agent_token() -> str:
    if not sys.stdin.isatty():
        raise SetupError(
            "Agent Tokenが設定されていません。通常はsetup_agent_service.shが"
            "ランダムTokenを自動生成します。既存Agentに合わせる場合だけ"
            "ROS_PC_MANAGER_AGENT_TOKENを設定してください。"
        )
    first = getpass.getpass("Agent Tokenを入力してください: ").strip()
    second = getpass.getpass("Agent Tokenをもう一度入力してください: ").strip()
    if first != second:
        raise SetupError("Agent Tokenが一致しません。")
    return first


def ensure_agent_files(
    root: Path,
    token_provider: Callable[[], str] | None = None,
) -> tuple[Path, Path]:
    """Create only missing Agent files and fill a missing/placeholder token."""
    config_dir = root / "agent" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "agent.yaml"

    if not config_path.exists():
        template_path = config_dir / "agent.yaml.example"
        raw = load_yaml_mapping(template_path, "Agent設定テンプレート")
        agent = raw.setdefault("agent", {})
        if not isinstance(agent, dict):
            raise SetupError("agent.yaml.example の agent はマッピングである必要があります。")
        agent["name"] = socket.gethostname()
        agent["token"] = ""
        atomic_write_yaml(config_path, raw)
        print(f"Agent設定を作成しました: {config_path}")

    raw = load_yaml_mapping(config_path, "Agent設定")
    agent = raw.setdefault("agent", {})
    paths = raw.setdefault("paths", {})
    if not isinstance(agent, dict) or not isinstance(paths, dict):
        raise SetupError("agent.yaml の agent / paths はマッピングである必要があります。")

    launches_raw = paths.get("launches_file", "launches.yaml")
    if not isinstance(launches_raw, str) or not launches_raw.strip():
        raise SetupError("agent.yaml の paths.launches_file は空でないパスを指定してください。")
    launches_path = resolve_config_path(launches_raw, config_path)
    if not launches_path.exists():
        atomic_write_yaml(launches_path, {"launches": []})
        print(f"空のlaunch設定を作成しました: {launches_path}")

    changed = False
    agent_id = str(agent.get("id", "")).strip()
    if not agent_id:
        agent["id"] = str(uuid.uuid4())
        changed = True
        print("Agent IDを生成しました。")
    else:
        try:
            uuid.UUID(agent_id)
        except ValueError as exc:
            raise SetupError("agent.idはUUIDである必要があります。") from exc

    existing_token_raw = str(agent.get("token", ""))
    existing_token = existing_token_raw.strip()
    placeholders = {
        "",
        "replace_with_manager_token",
        "replace_with_agent_token",
        "replace_with_auto_generated_token",
    }
    if existing_token.lower() in placeholders:
        env_token = os.environ.get("ROS_PC_MANAGER_AGENT_TOKEN", "").strip()
        if env_token:
            token = env_token
        elif token_provider is not None:
            token = token_provider()
        else:
            token = secrets.token_urlsafe(32)
        if not valid_agent_token(token):
            raise SetupError(
                "Agent Tokenが不正です。24文字以上のURL-safe Tokenを指定してください。"
            )
        agent["token"] = token
        changed = True
        print("Agent Tokenを自動生成しました。値は表示していません。")
    elif not valid_agent_token(existing_token_raw):
        raise SetupError(
            "既存のAgent Tokenが不正です。Agent側のTokenを確認し、正しい値を設定してください。"
        )
    else:
        print("既存のAgent Tokenを保持します。")

    discovery_missing = "discovery" not in raw
    discovery = raw.setdefault("discovery", {})
    if not isinstance(discovery, dict):
        raise SetupError("discoveryはYAMLマッピングである必要があります。")
    if "enabled" not in discovery:
        discovery["enabled"] = True
        changed = True
    if changed or discovery_missing:
        atomic_write_yaml(config_path, raw)

    config_path.chmod(0o600)
    if launches_path.exists():
        try:
            launches_path.chmod(0o600)
        except OSError:
            pass
    return config_path, launches_path


def run_ros_setup(setup_files: list[Path], label: str) -> None:
    for setup_file in setup_files:
        if not setup_file.is_file():
            raise SetupError(f"{label}が見つかりません: {setup_file}")
    script = (
        "set -e; "
        "for setup_file in \"$@\"; do source \"$setup_file\"; done; "
        "command -v ros2 >/dev/null"
    )
    try:
        result = subprocess.run(
            ["/bin/bash", "-c", script, "ros-pc-manager-setup", *map(str, setup_files)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupError(f"{label}の確認に失敗しました: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "ros2コマンドが見つかりません。"
        raise SetupError(f"{label}をsourceできません: {detail}")


def validate_agent_config(root: Path) -> None:
    # Import through the installed editable package so validation matches runtime.
    try:
        from pc_manager_agent.config import load_agent_settings
        from pc_manager_agent.launch_manager import LaunchManager
    except ImportError as exc:
        raise SetupError(
            "Agent Pythonパッケージが見つかりません。先に ./scripts/setup.sh を実行してください。"
        ) from exc

    config_path = root / "agent" / "config" / "agent.yaml"
    try:
        settings = load_agent_settings(config_path)
    except (OSError, TypeError, ValueError) as exc:
        raise SetupError(f"Agent設定を読み込めません: {exc}") from exc
    if not settings.ros_setup.is_file():
        raise SetupError(f"ROS 2 setup.bashが見つかりません: {settings.ros_setup}")

    launches_path = settings.launches_file
    if not launches_path.is_file():
        raise SetupError(f"launches.yamlが見つかりません: {launches_path}")
    raw = load_yaml_mapping(launches_path, "launches.yaml")
    entries = raw.get("launches", [])
    if not isinstance(entries, list):
        raise SetupError("launches.yaml の launches はリストである必要があります。")

    try:
        manager = LaunchManager(settings)
    except OSError as exc:
        raise SetupError(
            "Agentのstate_file / log_rootを現在の実行ユーザーが書き込めません。"
            "agent.yamlのpathsでユーザーが書き込める場所を指定してください。"
        ) from exc
    seen: set[str] = set()
    workspace_setups: list[Path] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise SetupError("launches.yaml の各launch設定はマッピングである必要があります。")
        launch_id = str(entry.get("id", ""))
        if launch_id in seen:
            raise SetupError(f"launch IDが重複しています: {launch_id}")
        seen.add(launch_id)
        try:
            normalized = manager.validate_launch_entry(entry)
        except (KeyError, TypeError, ValueError) as exc:
            raise SetupError(f"launch設定が不正です ({launch_id or 'ID未設定'}): {exc}") from exc
        workspace_setup = Path(normalized["workspace"]) / "install" / "setup.bash"
        if workspace_setup not in workspace_setups:
            workspace_setups.append(workspace_setup)

    run_ros_setup([settings.ros_setup], "AgentのROS 2環境")
    for workspace_setup in workspace_setups:
        run_ros_setup(
            [settings.ros_setup, workspace_setup],
            f"WorkspaceのROS 2環境 ({workspace_setup.parent.parent})",
        )
    print(f"Agent設定を検証しました。登録launch数: {len(entries)}")


def ensure_server_configs(root: Path) -> Path:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    server_config = config_dir / "server.yaml"
    domains_config = config_dir / "domains.yaml"

    if not server_config.exists():
        shutil.copy2(config_dir / "server.yaml.example", server_config)
        print(f"Server設定を作成しました: {server_config}")
    if not domains_config.exists():
        shutil.copy2(config_dir / "domains.yaml.example", domains_config)
        print(f"Domain設定を作成しました: {domains_config}")

    # server.yaml may contain an optional plaintext admin password.
    try:
        server_config.chmod(0o600)
    except OSError as exc:
        raise SetupError(f"Server設定の権限を設定できません: {server_config}: {exc}") from exc

    raw = load_yaml_mapping(server_config, "Server設定")
    server = raw.get("server", {})
    ros = raw.get("ros", {})
    paths = raw.get("paths", {})
    if not all(isinstance(section, dict) for section in (server, ros, paths)):
        raise SetupError("server.yaml の server / ros / paths はマッピングである必要があります。")

    domains_value = str(raw.get("domains_file", "config/domains.yaml"))
    domains_path = Path(domains_value).expanduser()
    if not domains_path.is_absolute():
        domains_path = (root / domains_path).resolve()
    if not domains_path.exists():
        shutil.copy2(config_dir / "domains.yaml.example", domains_path)
        print(f"Domain設定を作成しました: {domains_path}")
    domains_raw = load_yaml_mapping(domains_path, "Domain設定")
    domains = domains_raw.get("domains", [])
    if not isinstance(domains, list):
        raise SetupError("domains.yaml の domains はリストである必要があります。")
    for item in domains:
        if not isinstance(item, dict) or "id" not in item:
            raise SetupError("domains.yaml の各項目にはidが必要です。")
        try:
            domain_id = int(item["id"])
        except (TypeError, ValueError) as exc:
            raise SetupError("Domain IDは整数で指定してください。") from exc
        if not 0 <= domain_id <= 232:
            raise SetupError("Domain IDは0〜232で指定してください。")
    try:
        port = int(server.get("port", 8080))
    except (TypeError, ValueError) as exc:
        raise SetupError("server.portは整数で指定してください。") from exc
    if not 1 <= port <= 65535:
        raise SetupError("server.portは1〜65535で指定してください。")

    ros_setup = Path(str(ros.get("setup", "/opt/ros/jazzy/setup.bash"))).expanduser()
    if not ros_setup.is_absolute():
        ros_setup = (root / ros_setup).resolve()
    overlays_value = ros.get("overlays", [])
    if not isinstance(overlays_value, list):
        raise SetupError("ros.overlaysはWorkspaceパスのリストで指定してください。")
    setup_files = [ros_setup]
    for value in overlays_value:
        overlay = Path(str(value)).expanduser()
        if not overlay.is_absolute():
            overlay = (root / overlay).resolve()
        setup_files.append(overlay / "install" / "setup.bash")

    run_ros_setup(setup_files, "ServerのROS 2環境")
    print(f"Server設定を確認しました。HTTP port: {port}")
    return server_config


def systemd_quote(value: str | Path) -> str:
    text = str(value)
    if "\n" in text or "\r" in text:
        raise SetupError("systemd設定に改行を含むパスは使用できません。")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def systemd_escape_path(value: str | Path) -> str:
    """Escape a path for directives such as WorkingDirectory= (not ExecStart=)."""
    text = str(value)
    if "\n" in text or "\r" in text:
        raise SetupError("systemd設定に改行を含むパスは使用できません。")
    escaped: list[str] = []
    for char in text:
        if char == "\\":
            escaped.append("\\\\")
        elif char.isspace() or char in {'"', "#"}:
            escaped.append(f"\\x{ord(char):02x}")
        else:
            escaped.append(char)
    return "".join(escaped)


def render_shutdown_sudoers(username: str) -> str:
    """Render the narrowly scoped sudoers rule used by the Agent shutdown API."""
    if not re.fullmatch(r"[a-z_][a-z0-9_.-]*\$?", username):
        raise SetupError(f"sudoersに使用できないユーザー名です: {username!r}")
    return (
        f"{MANAGED_MARKER}\n"
        f"{username} ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff\n"
    )


def render_unit(kind: str, root: Path) -> str:
    if kind not in {"agent", "server"}:
        raise SetupError(f"不明なサービス種別です: {kind}")
    template_path = root / "deploy" / "systemd" / f"pc-manager-{kind}.service.example"
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SetupError(f"systemdテンプレートを読み込めません: {template_path}") from exc

    user = getpass.getuser()
    group = os.getgid()
    import grp
    group_name = grp.getgrgid(group).gr_name
    replacements = {
        "__RUN_USER__": user,
        "__RUN_GROUP__": group_name,
        "__PROJECT_ROOT__": systemd_escape_path(root.resolve()),
    }
    if kind == "agent":
        replacements.update({
            "__AGENT_EXEC__": systemd_quote(root.resolve() / ".venv" / "bin" / "ros-pc-manager-agent"),
            "__AGENT_CONFIG__": systemd_quote(root.resolve() / "agent" / "config" / "agent.yaml"),
        })
    else:
        server_config = root.resolve() / "config" / "server.yaml"
        replacements.update({
            "__SERVER_EXEC__": systemd_quote(root.resolve() / ".venv" / "bin" / "ros-pc-manager-server"),
            "__SERVER_CONFIG_ENV__": systemd_quote(f"ROS_PC_MANAGER_CONFIG={server_config}"),
        })

    rendered = template
    for old, new in replacements.items():
        rendered = rendered.replace(old, new)
    unresolved = re.findall(r"__[A-Z0-9_]+__", rendered)
    if unresolved:
        raise SetupError(f"systemdテンプレートに未置換項目があります: {', '.join(unresolved)}")
    return MANAGED_MARKER + "\n" + rendered


def main() -> int:
    parser = argparse.ArgumentParser(description="ROS PC Manager service setup helper")
    parser.add_argument(
        "action",
        choices=(
            "prepare-agent",
            "validate-agent",
            "prepare-server",
            "render-agent",
            "render-server",
            "render-shutdown-sudoers",
        ),
    )
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--user", help="Linux account for the shutdown sudoers rule.")
    args = parser.parse_args()
    root = args.root.resolve()

    try:
        if args.action == "prepare-agent":
            ensure_agent_files(root)
            validate_agent_config(root)
        elif args.action == "validate-agent":
            validate_agent_config(root)
        elif args.action == "prepare-server":
            ensure_server_configs(root)
        elif args.action in {"render-agent", "render-server"}:
            if args.output is None:
                raise SetupError("--outputを指定してください。")
            kind = "agent" if args.action == "render-agent" else "server"
            args.output.write_text(render_unit(kind, root), encoding="utf-8")
            args.output.chmod(0o600)
        elif args.action == "render-shutdown-sudoers":
            if args.output is None or not args.user:
                raise SetupError("--outputと--userを指定してください。")
            args.output.write_text(render_shutdown_sudoers(args.user), encoding="utf-8")
            args.output.chmod(0o600)
        return 0
    except SetupError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError) as exc:
        print(f"エラー: 設定処理に失敗しました: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
