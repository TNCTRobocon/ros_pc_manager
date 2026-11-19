from pathlib import Path

import pytest

from pc_manager_server.config import Settings
from pc_manager_server.rosbag_manager import RosbagManager


def make_manager(tmp_path: Path) -> RosbagManager:
    ros_setup = tmp_path / "ros_setup.bash"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ros2 = fake_bin / "ros2"
    fake_ros2.write_text(
        "#!/bin/bash\n"
        "if [ \"$1\" = topic ]; then echo /topic_a; echo /topic_b; exit 0; fi\n"
        "if [ \"$1\" = bag ]; then exec sleep 60; fi\n"
        "exit 0\n"
    )
    fake_ros2.chmod(0o755)
    ros_setup.write_text(f'export PATH="{fake_bin}:$PATH"\n')
    return RosbagManager(Settings(
        project_root=tmp_path,
        bind_host="127.0.0.1",
        bind_port=8080,
        admin_password="test-password",
        database_path=tmp_path / "db.sqlite3",
        ros_setup=ros_setup,
        ros_overlays=(),
        rosbag_root=tmp_path / "bags",
        rosbag_state_path=tmp_path / "state" / "rosbag.json",
        process_log_root=tmp_path / "logs",
        min_free_gb=0,
        warning_free_gb=0,
        agent_timeout_seconds=1,
        configured_domains=(32,),
    ))


def test_topic_list_and_recording_lifecycle(tmp_path):
    manager = make_manager(tmp_path)
    assert manager.list_topics(32) == ["/topic_a", "/topic_b"]

    started = manager.start(32, ["/topic_a"])
    assert started["recording"] is True
    assert started["recording_info"]["domain_id"] == 32
    assert started["recording_info"]["topics"] == ["/topic_a"]

    stopped = manager.stop()
    assert stopped["recording"] is False
    assert stopped["recording_info"]["status"] == "STOPPED"
    assert Path(stopped["recording_info"]["output_path"]).parent.exists()


def test_reject_invalid_domain_and_topic(tmp_path):
    manager = make_manager(tmp_path)
    with pytest.raises(ValueError):
        manager.list_topics(300)
    with pytest.raises(ValueError):
        manager.start(32, ["not-a-topic"])


def test_only_one_domain_can_be_recorded_at_a_time(tmp_path):
    manager = make_manager(tmp_path)
    manager.start(32, ["/topic_a"])

    with pytest.raises(RuntimeError, match="already active"):
        manager.start(33, ["/topic_b"])

    stopped = manager.stop()
    assert stopped["recording"] is False
    assert stopped["recording_info"]["domain_id"] == 32
