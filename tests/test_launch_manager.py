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
    package_share = install / "share" / "demo_pkg"
    launch_dir = package_share / "launch"
    launch_dir.mkdir(parents=True)
    (package_share / "package.xml").write_text(
        "<package format='3'><name>demo_pkg</name><version>0.1.0</version>"
        "<description>test</description><maintainer email='a@b'>test</maintainer>"
        "<license>MIT</license></package>"
    )
    (launch_dir / "demo.launch.py").write_text("# test launch\n")
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


def test_workspace_discovery_and_launch_config_update(tmp_path):
    manager = make_manager(tmp_path)
    workspace = tmp_path / "ws"

    assert manager.workspace_packages(workspace) == ["demo_pkg"]
    assert manager.workspace_launch_files(workspace, "demo_pkg") == ["demo.launch.py"]

    current = manager.get_launch_config()
    assert current["workspaces"] == [str(workspace)]

    changed = dict(current["launches"][0])
    changed["display_name"] = "GUIで変更した起動名"
    changed["arguments"] = {"use_sim": "false"}
    added = {
        **changed,
        "id": "camera_test",
        "display_name": "2つ目のlaunch",
    }
    updated = manager.replace_launch_config([changed, added])

    assert [item["display_name"] for item in updated["launches"]] == [
        "GUIで変更した起動名", "2つ目のlaunch"
    ]
    assert updated["launches"][0]["arguments"] == {"use_sim": "false"}
    stored = yaml.safe_load(manager.settings.launches_file.read_text())
    assert stored["launches"][0]["display_name"] == "GUIで変更した起動名"
    assert stored["launches"][1]["id"] == "camera_test"


def test_launch_config_rejects_invalid_workspace_package_and_file(tmp_path):
    manager = make_manager(tmp_path)
    current = manager.get_launch_config()["launches"][0]

    invalid_workspace = {**current, "workspace": str(tmp_path / "missing")}
    with pytest.raises(ValueError, match="Workspace"):
        manager.replace_launch_config([invalid_workspace])

    invalid_package = {**current, "package": "not_installed"}
    with pytest.raises(ValueError, match="Package"):
        manager.replace_launch_config([invalid_package])

    invalid_file = {**current, "launch_file": "missing.launch.py"}
    with pytest.raises(ValueError, match="Launchファイル"):
        manager.replace_launch_config([invalid_file])


def test_active_launch_cannot_be_edited_or_deleted_in_config(tmp_path):
    manager = make_manager(tmp_path)
    manager.start("demo")
    original = manager.get_launch_config()["launches"][0]

    modified = {**original, "display_name": "changed"}
    with pytest.raises(RuntimeError, match="実行中"):
        manager.replace_launch_config([modified])
    with pytest.raises(RuntimeError, match="実行中"):
        manager.replace_launch_config([])

    manager.stop("demo")
    result = manager.replace_launch_config([])
    assert result["launches"] == []
