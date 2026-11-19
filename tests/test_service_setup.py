from __future__ import annotations

import shutil
import sys
from pathlib import Path

import yaml
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from service_setup import (  # noqa: E402
    SetupError,
    ensure_agent_files,
    ensure_server_configs,
    render_unit,
    validate_agent_config,
)


def copy_agent_template(root: Path) -> Path:
    config_dir = root / "agent" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "agent" / "config" / "agent.yaml.example", config_dir / "agent.yaml.example")
    return config_dir


def make_ros_setup(root: Path) -> Path:
    fake_bin = root / "fake-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    ros2 = fake_bin / "ros2"
    ros2.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    ros2.chmod(0o755)
    setup = root / "ros" / "setup.bash"
    setup.parent.mkdir(parents=True, exist_ok=True)
    setup.write_text(f'export PATH="{fake_bin}:$PATH"\n', encoding="utf-8")
    return setup


def test_ensure_agent_files_creates_empty_launch_config_and_secure_token(tmp_path: Path):
    config_dir = copy_agent_template(tmp_path)

    agent_config, launches_config = ensure_agent_files(
        tmp_path,
        token_provider=lambda: "manager-generated-token-0123456789",
    )

    raw_agent = yaml.safe_load(agent_config.read_text(encoding="utf-8"))
    raw_launches = yaml.safe_load(launches_config.read_text(encoding="utf-8"))
    assert raw_agent["agent"]["token"] == "manager-generated-token-0123456789"
    assert raw_launches == {"launches": []}
    assert agent_config.stat().st_mode & 0o777 == 0o600
    assert launches_config.stat().st_mode & 0o777 == 0o600
    assert agent_config.parent == config_dir


def test_ensure_agent_files_preserves_existing_config_and_launches(tmp_path: Path):
    config_dir = copy_agent_template(tmp_path)
    agent_path = config_dir / "agent.yaml"
    launches_path = config_dir / "launches.yaml"
    agent_path.write_text(
        yaml.safe_dump({
            "agent": {
                "name": "existing-host",
                "host": "0.0.0.0",
                "port": 8765,
                "token": "x" * 32,
                "ros_setup": "/opt/ros/jazzy/setup.bash",
                "default_domain_id": 42,
            },
            "paths": {
                "launches_file": "launches.yaml",
                "state_file": "~/.local/state/test/state.json",
                "log_root": "~/.local/state/test/logs",
            },
        }),
        encoding="utf-8",
    )
    launches_path.write_text("launches: []\n", encoding="utf-8")
    before_agent = agent_path.read_bytes()
    before_launches = launches_path.read_bytes()

    ensure_agent_files(tmp_path, token_provider=lambda: pytest.fail("token must not be requested"))

    assert agent_path.read_bytes() == before_agent
    assert launches_path.read_bytes() == before_launches
    assert agent_path.stat().st_mode & 0o777 == 0o600


def test_ensure_agent_files_rejects_invalid_token(tmp_path: Path):
    copy_agent_template(tmp_path)

    with pytest.raises(SetupError, match="Tokenが不正"):
        ensure_agent_files(tmp_path, token_provider=lambda: "too-short")


def test_validate_agent_config_checks_ros_and_launch_files(tmp_path: Path):
    config_dir = copy_agent_template(tmp_path)
    ros_setup = make_ros_setup(tmp_path)
    workspace = tmp_path / "workspace"
    install = workspace / "install"
    launch_dir = install / "share" / "demo_pkg" / "launch"
    launch_dir.mkdir(parents=True)
    (install / "setup.bash").write_text("# workspace setup\n", encoding="utf-8")
    (install / "share" / "demo_pkg" / "package.xml").write_text(
        "<package><name>demo_pkg</name></package>\n",
        encoding="utf-8",
    )
    (launch_dir / "demo.launch.py").write_text("# test\n", encoding="utf-8")

    agent = {
        "agent": {
            "name": "test-host",
            "host": "127.0.0.1",
            "port": 8765,
            "token": "valid-manager-token-0123456789",
            "ros_setup": str(ros_setup),
            "default_domain_id": 32,
        },
        "paths": {
            "launches_file": "launches.yaml",
            "state_file": str(tmp_path / "state" / "launches.json"),
            "log_root": str(tmp_path / "logs"),
        },
    }
    (config_dir / "agent.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    (config_dir / "launches.yaml").write_text(
        yaml.safe_dump({
            "launches": [{
                "id": "demo",
                "display_name": "Demo",
                "workspace": str(workspace),
                "package": "demo_pkg",
                "launch_file": "demo.launch.py",
                "ros_domain_id": 32,
                "arguments": {},
            }]
        }),
        encoding="utf-8",
    )

    validate_agent_config(tmp_path)


def test_validate_agent_config_rejects_missing_workspace(tmp_path: Path):
    config_dir = copy_agent_template(tmp_path)
    ros_setup = make_ros_setup(tmp_path)
    agent = {
        "agent": {
            "name": "test-host",
            "host": "127.0.0.1",
            "port": 8765,
            "token": "valid-manager-token-0123456789",
            "ros_setup": str(ros_setup),
            "default_domain_id": 32,
        },
        "paths": {
            "launches_file": "launches.yaml",
            "state_file": str(tmp_path / "state" / "launches.json"),
            "log_root": str(tmp_path / "logs"),
        },
    }
    (config_dir / "agent.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    (config_dir / "launches.yaml").write_text(
        yaml.safe_dump({"launches": [{
            "id": "broken",
            "display_name": "Broken",
            "workspace": str(tmp_path / "missing"),
            "package": "demo_pkg",
            "launch_file": "demo.launch.py",
            "ros_domain_id": 32,
            "arguments": {},
        }]}),
        encoding="utf-8",
    )

    with pytest.raises(SetupError, match="Workspace"):
        validate_agent_config(tmp_path)


def test_ensure_server_configs_creates_only_missing_files(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy2(ROOT / "config" / "server.yaml.example", config_dir / "server.yaml.example")
    shutil.copy2(ROOT / "config" / "domains.yaml.example", config_dir / "domains.yaml.example")
    ros_setup = make_ros_setup(tmp_path)
    (config_dir / "server.yaml.example").write_text(
        yaml.safe_dump({
            "server": {"host": "127.0.0.1", "port": 8099},
            "security": {"admin_password": ""},
            "paths": {"data_root": "var"},
            "ros": {"setup": str(ros_setup), "overlays": []},
            "domains_file": "config/domains.yaml",
        }),
        encoding="utf-8",
    )

    server_config = ensure_server_configs(tmp_path)
    existing_text = server_config.read_text(encoding="utf-8")
    ensure_server_configs(tmp_path)
    assert server_config.read_text(encoding="utf-8") == existing_text
    assert server_config.stat().st_mode & 0o777 == 0o600
    assert (config_dir / "domains.yaml").is_file()


def test_rendered_units_are_quoted_and_have_no_unresolved_placeholders(tmp_path: Path):
    project = tmp_path / "project with spaces"
    (project / "deploy").mkdir(parents=True)
    shutil.copytree(ROOT / "deploy" / "systemd", project / "deploy" / "systemd")

    for kind in ("agent", "server"):
        unit = render_unit(kind, project)
        assert unit.startswith("# Managed by ROS PC Manager setup scripts.")
        assert "__" not in unit
        escaped_project = str(project).replace(" ", r"\x20")
        assert f"WorkingDirectory={escaped_project}" in unit
        if kind == "agent":
            assert f'--config "{project}/agent/config/agent.yaml"' in unit
            assert "KillMode=process" in unit
        else:
            assert f'ROS_PC_MANAGER_CONFIG={project}/config/server.yaml' in unit
