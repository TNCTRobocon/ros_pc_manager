from __future__ import annotations

import os
import socket
import uuid
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
    agent_id: str = ""
    discovery_enabled: bool = False
    ros_overlays: tuple[Path, ...] = ()
    rosbag_root: Path | None = None
    rosbag_state_path: Path | None = None
    min_free_gb: float = 5.0
    warning_free_gb: float = 10.0

    @property
    def process_log_root(self) -> Path:
        # Compatibility with the existing rosbag manager's settings interface.
        return self.log_root


def _stable_agent_id() -> str:
    machine_id = ""
    for candidate in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        try:
            machine_id = candidate.read_text(encoding="utf-8").strip()
            if machine_id:
                break
        except OSError:
            continue
    identity = machine_id or socket.gethostname()
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, identity))


def load_agent_settings(config_path: str | Path | None = None) -> AgentSettings:
    candidate = Path(config_path or os.environ.get(
        "ROS_PC_MANAGER_AGENT_CONFIG",
        "/etc/ros-pc-manager/agent.yaml",
    )).expanduser()
    if not candidate.is_file():
        raise FileNotFoundError(
            f"Agent config not found: {candidate}. Copy agent.yaml.example and run setup_agent_service.sh."
        )
    raw: dict[str, Any] = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
    agent = raw.get("agent", {})
    paths = raw.get("paths", {})
    ros = raw.get("ros", {})
    discovery = raw.get("discovery", {})
    limits = raw.get("limits", {})
    if not all(isinstance(section, dict) for section in (agent, paths, ros, discovery, limits)):
        raise ValueError("agent.yaml sections agent/paths/ros/discovery/limits must be mappings")

    base = candidate.parent

    def resolve(value: str | Path) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else (base / path).resolve()

    domain_id = int(agent.get("default_domain_id", 0))
    if not 0 <= domain_id <= 232:
        raise ValueError("default_domain_id must be between 0 and 232")
    token = str(agent.get("token", ""))
    if len(token) < 24:
        raise ValueError("agent.token must contain at least 24 characters")

    agent_id = str(agent.get("id") or _stable_agent_id()).strip()
    try:
        uuid.UUID(agent_id)
    except ValueError as exc:
        raise ValueError("agent.id must be a UUID; run scripts/setup_agent_service.sh") from exc

    overlays_raw = ros.get("overlays", [])
    if not isinstance(overlays_raw, list):
        raise ValueError("ros.overlays must be a list of workspace paths")
    overlays = tuple(resolve(str(value)) for value in overlays_raw)

    rosbag_root = resolve(str(paths.get(
        "rosbag_root", "~/.local/state/ros-pc-manager/rosbags"
    )))
    rosbag_state = resolve(str(paths.get(
        "rosbag_state", "~/.local/state/ros-pc-manager/rosbag_state.json"
    )))
    minimum = max(0.0, float(limits.get("minimum_free_gb", 5.0)))
    warning = max(minimum, float(limits.get("warning_free_gb", 10.0)))

    return AgentSettings(
        name=str(agent.get("name") or os.uname().nodename),
        bind_host=str(agent.get("host", "0.0.0.0")),
        bind_port=int(agent.get("port", 8765)),
        token=token,
        ros_setup=resolve(str(agent.get("ros_setup", "/opt/ros/jazzy/setup.bash"))),
        default_domain_id=domain_id,
        launches_file=resolve(str(paths.get("launches_file", "launches.yaml"))),
        state_file=resolve(str(paths.get("state_file", "~/.local/state/ros-pc-manager/launches.json"))),
        log_root=resolve(str(paths.get("log_root", "~/.local/state/ros-pc-manager/logs"))),
        agent_id=agent_id,
        discovery_enabled=bool(discovery.get("enabled", True)),
        ros_overlays=overlays,
        rosbag_root=rosbag_root,
        rosbag_state_path=rosbag_state,
        min_free_gb=minimum,
        warning_free_gb=warning,
    )
