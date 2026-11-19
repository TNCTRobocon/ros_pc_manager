from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AgentSettings:
    name: str
    bind_host: str
    bind_port: int
    token: str
    ros_setup: Path
    default_domain_id: int
    launches_file: Path
    state_file: Path
    log_root: Path


def load_agent_settings(config_path: str | Path | None = None) -> AgentSettings:
    candidate = Path(config_path or os.environ.get(
        "ROS_PC_MANAGER_AGENT_CONFIG",
        "/etc/ros-pc-manager/agent.yaml",
    )).expanduser()
    if not candidate.is_file():
        raise FileNotFoundError(
            f"Agent config not found: {candidate}. Copy agent.yaml.example and set its token."
        )
    raw: dict[str, Any] = yaml.safe_load(candidate.read_text()) or {}
    agent = raw.get("agent", {})
    paths = raw.get("paths", {})
    base = candidate.parent
    def resolve(value: str) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else (base / path).resolve()
    domain_id = int(agent.get("default_domain_id", 0))
    if not 0 <= domain_id <= 232:
        raise ValueError("default_domain_id must be between 0 and 232")
    token = str(agent.get("token", ""))
    if len(token) < 24:
        raise ValueError("agent.token must contain at least 24 characters")
    return AgentSettings(
        name=str(agent.get("name") or os.uname().nodename),
        bind_host=str(agent.get("host", "0.0.0.0")),
        bind_port=int(agent.get("port", 8765)),
        token=token,
        ros_setup=resolve(str(agent.get("ros_setup", "/opt/ros/jazzy/setup.bash"))),
        default_domain_id=domain_id,
        launches_file=resolve(str(paths.get("launches_file", "launches.yaml"))),
        state_file=resolve(str(paths.get("state_file", "/var/lib/ros-pc-manager/launches.json"))),
        log_root=resolve(str(paths.get("log_root", "/var/log/ros-pc-manager"))),
    )
