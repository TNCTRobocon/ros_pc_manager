import subprocess
from pathlib import Path

import yaml
import pytest

from pc_manager_agent.config import AgentSettings
from pc_manager_agent.launch_manager import LaunchManager


def make_manager(tmp_path: Path) -> LaunchManager:
    ros_setup = tmp_path / "ros_setup.bash"
    ros_setup.write_text("# test ROS setup\n")
    workspace = tmp_path / "ws"
    install = workspace / "install"
    install.mkdir(parents=True)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ros2 = fake_bin / "ros2"
    fake_ros2.write_text("#!/bin/bash\nif [ \"$1\" = launch ]; then exec sleep 60; fi\nexit 0\n")
    fake_ros2.chmod(0o755)
    (install / "setup.bash").write_text(f'export PATH="{fake_bin}:$PATH"\n')
    launches_file = tmp_path / "launches.yaml"
    launches_file.write_text(yaml.safe_dump({
        "launches": [{
            "id": "demo",
            "display_name": "Demo Launch",
            "workspace": str(workspace),
            "package": "demo_pkg",
            "launch_file": "demo.launch.py",
            "ros_domain_id": 32,
            "arguments": {},
        }]
    }))
    settings = AgentSettings(
        name="test-agent",
        bind_host="127.0.0.1",
        bind_port=8765,
        token="x" * 32,
        ros_setup=ros_setup,
        default_domain_id=32,
        launches_file=launches_file,
        state_file=tmp_path / "state" / "launches.json",
        log_root=tmp_path / "logs",
    )
    return LaunchManager(settings)


def test_managed_launch_is_started_and_stopped(tmp_path):
    manager = make_manager(tmp_path)
    result = manager.start("demo")
    assert result["ok"] is True
    assert manager.list_launches()["launches"][0]["status"] == "RUNNING"

    with pytest.raises(RuntimeError, match="already running"):
        manager.start("demo")

    stopped = manager.stop("demo")
    assert stopped["ok"] is True
    assert manager.list_launches()["launches"][0]["status"] == "STOPPED"


def test_external_terminal_process_is_not_listed_or_stopped(tmp_path):
    manager = make_manager(tmp_path)
    external = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        listed = manager.list_launches()["launches"]
        assert len(listed) == 1
        assert listed[0]["status"] == "STOPPED"

        manager.stop_running()
        assert external.poll() is None
    finally:
        external.terminate()
        external.wait(timeout=2)


def test_bulk_stop_only_stops_manager_owned_launches(tmp_path):
    manager = make_manager(tmp_path)
    manager.start("demo")
    result = manager.stop_running()
    assert result["stopped"] == ["demo"]
    assert manager.list_launches()["running_count"] == 0


def test_agent_restart_recovers_and_can_stop_managed_launch(tmp_path):
    manager = make_manager(tmp_path)
    started = manager.start("demo")
    assert started["ok"] is True

    # A new Agent instance must recover the process from persisted PID identity,
    # without relying on the original subprocess.Popen object.
    restarted_manager = LaunchManager(manager.settings)
    recovered = restarted_manager.list_launches()["launches"][0]
    assert recovered["status"] == "RUNNING"
    assert recovered["pid"] == started["pid"]

    stopped = restarted_manager.stop("demo")
    assert stopped["ok"] is True
    assert restarted_manager.list_launches()["launches"][0]["status"] == "STOPPED"
