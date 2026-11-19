from pathlib import Path

from fastapi.testclient import TestClient

from pc_manager_server.config import Settings
from pc_manager_server.main import create_app


def make_settings(tmp_path: Path) -> Settings:
    root = Path(__file__).resolve().parents[1]
    return Settings(
        project_root=root,
        bind_host="127.0.0.1",
        bind_port=8080,
        admin_password="correct-horse-battery",
        database_path=tmp_path / "manager.sqlite3",
        ros_setup=Path("/opt/ros/jazzy/setup.bash"),
        ros_overlays=(),
        rosbag_root=tmp_path / "rosbags",
        rosbag_state_path=tmp_path / "state" / "rosbag.json",
        process_log_root=tmp_path / "logs",
        min_free_gb=0,
        warning_free_gb=0,
        agent_timeout_seconds=0.05,
        configured_domains=(32,),
    )


def test_api_requires_login_and_registers_pc_once(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))

    assert client.get("/api/hosts").status_code == 401
    login = client.post("/api/login", json={"password": "wrong"})
    assert login.status_code == 401

    login = client.post("/api/login", json={"password": "correct-horse-battery"})
    assert login.status_code == 200
    token = login.json()["session_token"]
    headers = {"X-Manager-Session": token}

    registered = client.post("/api/hosts", headers=headers, json={
        "name": "robot-test",
        "address": "192.0.2.10",
        "port": 8765,
        "domain_id": 32,
    })
    assert registered.status_code == 201
    body = registered.json()
    assert len(body["agent_token"]) >= 32
    assert "token:" in body["agent_config"]
    assert "\n" in body["agent_config"]
    assert "agent_token" not in client.get("/api/hosts", headers=headers).json()[0]

    duplicate = client.post("/api/hosts", headers=headers, json={
        "name": "robot-test-duplicate",
        "address": "192.0.2.10",
        "port": 8765,
        "domain_id": 32,
    })
    assert duplicate.status_code == 409

    assert client.get("/api/session", headers=headers).status_code == 200
