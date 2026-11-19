from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve(value: str | Path, base: Path = PROJECT_ROOT) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


@dataclass(frozen=True)
class Settings:
    project_root: Path
    bind_host: str
    bind_port: int
    admin_password: str
    database_path: Path
    ros_setup: Path
    ros_overlays: tuple[Path, ...]
    rosbag_root: Path
    rosbag_state_path: Path
    process_log_root: Path
    min_free_gb: float
    warning_free_gb: float
    agent_timeout_seconds: float
    configured_domains: tuple[int, ...]


def load_settings() -> Settings:
    config_path = Path(os.environ.get(
        "ROS_PC_MANAGER_CONFIG",
        PROJECT_ROOT / "config" / "server.yaml",
    )).expanduser()
    if not config_path.is_file():
        config_path = PROJECT_ROOT / "config" / "server.yaml.example"

    raw: dict[str, Any] = yaml.safe_load(config_path.read_text()) or {}
    server = raw.get("server", {})
    paths = raw.get("paths", {})
    ros = raw.get("ros", {})
    limits = raw.get("limits", {})
    security = raw.get("security", {})

    data_root = _resolve(paths.get("data_root", "var"))
    data_root.mkdir(parents=True, exist_ok=True)

    admin_password = os.environ.get(
        "ROS_PC_MANAGER_ADMIN_PASSWORD",
        str(security.get("admin_password", "")).strip(),
    )
    if not admin_password:
        password_file = data_root / "admin_password.txt"
        if password_file.is_file():
            admin_password = password_file.read_text().strip()
        else:
            admin_password = secrets.token_urlsafe(24)
            password_file.write_text(admin_password + "\n")
            password_file.chmod(0o600)
            print(
                "\n[ROS PC Manager] First-run admin password generated. "
                f"Read it from: {password_file}\n",
                flush=True,
            )

    domains_path = _resolve(raw.get("domains_file", "config/domains.yaml"))
    if not domains_path.is_file():
        domains_path = domains_path.with_name("domains.yaml.example")
    configured_domains: list[int] = []
    if domains_path.is_file():
        domain_raw = yaml.safe_load(domains_path.read_text()) or {}
        for item in domain_raw.get("domains", []):
            if isinstance(item, dict) and "id" in item:
                try:
                    value = int(item["id"])
                except (TypeError, ValueError):
                    continue
                if 0 <= value <= 232:
                    configured_domains.append(value)
    configured_domains = sorted(set(configured_domains))

    setup = _resolve(ros.get("setup", "/opt/ros/jazzy/setup.bash"))
    overlays = tuple(_resolve(path) for path in ros.get("overlays", []))
    minimum_free_gb = max(0.0, float(limits.get("minimum_free_gb", 5.0)))
    warning_free_gb = max(
        minimum_free_gb,
        float(limits.get("warning_free_gb", 10.0)),
    )

    return Settings(
        project_root=PROJECT_ROOT,
        bind_host=os.environ.get("ROS_PC_MANAGER_HOST", server.get("host", "0.0.0.0")),
        bind_port=int(os.environ.get("ROS_PC_MANAGER_PORT", server.get("port", 8080))),
        admin_password=admin_password,
        database_path=_resolve(paths.get("database", str(data_root / "manager.sqlite3"))),
        ros_setup=setup,
        ros_overlays=overlays,
        rosbag_root=_resolve(paths.get("rosbag_root", str(data_root / "rosbags"))),
        rosbag_state_path=_resolve(
            paths.get("rosbag_state", str(data_root / "rosbag_state.json"))
        ),
        process_log_root=_resolve(paths.get("log_root", str(data_root / "logs"))),
        min_free_gb=minimum_free_gb,
        warning_free_gb=warning_free_gb,
        agent_timeout_seconds=float(server.get("agent_timeout_seconds", 3.0)),
        configured_domains=tuple(configured_domains),
    )
